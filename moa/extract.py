"""Local extraction; uncertain geometry and non-readable documents stay unreviewed."""
from __future__ import annotations

import io
import json
import re
import sys
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup
from defusedxml import ElementTree

from .core import digest, encode

LAYOUTS = ('key_value_cards','timeline','grade_cards','comparison','read_only_form','scroll_table')
PARSER_VERSION = 'moa-local-v3'


def detect_kind(data: bytes, filename: str = '') -> str:
    start = data.lstrip()[:512].lower()
    if data.startswith(b'%PDF-'):
        return 'pdf'
    if data.startswith(b'PK\x03\x04'):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if any(re.fullmatch(r'Contents/section\d+\.xml',n) for n in z.namelist()):
                    return 'hwpx'
                if 'word/document.xml' in z.namelist():
                    return 'docx'
        except zipfile.BadZipFile:
            pass
        return 'unsupported'
    if data.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'):
        return 'hwp' if '.hwp' in filename.lower() else 'legacy_ole'
    if data.startswith((b'\x89PNG\r\n\x1a\n',b'\xff\xd8\xff',b'GIF8',b'II*\x00',b'MM\x00*')) or data[:4]==b'RIFF' and data[8:12]==b'WEBP':
        return 'image'
    if b'<html' in start or b'<!doctype html' in start or b'<body' in start:
        return 'html'
    return 'unsupported'


def _span(value) -> int:
    n=int(value or 1)
    if not 1 <= n <= 200:
        raise ValueError('비정상적인 병합 셀 범위')
    return n


def html_document(html: str) -> dict:
    soup=BeautifulSoup(html,'html.parser')
    for bad in soup.select('script,style,nav,form'):
        bad.decompose()
    tables=[]
    all_tables=soup.select('table')
    if len(all_tables)>100: raise ValueError('표 개수 제한 초과')
    for tbl in all_tables:
        rows=[tr for tr in tbl.select('tr') if tr.find_parent('table') is tbl]
        cells,occupied=[],set()
        for r,tr in enumerate(rows):
            col=0
            for td in tr.find_all(['td','th'],recursive=False):
                while (r,col) in occupied: col+=1
                rs,cs=_span(td.get('rowspan',1)),_span(td.get('colspan',1))
                if r+rs>1000 or col+cs>200 or len(cells)>=10000:
                    raise ValueError('표 크기 제한 초과')
                cells.append({'row':r,'col':col,'rowspan':rs,'colspan':cs,
                              'text':td.get_text(' ',strip=True),'header':td.name=='th'})
                occupied.update((rr,cc) for rr in range(r,r+rs) for cc in range(col,col+cs))
                col+=cs
        tables.append({'rows':max((c['row']+c['rowspan'] for c in cells),default=0),
                       'cols':max((c['col']+c['colspan'] for c in cells),default=0),
                       'cells':cells,'source':'html','nested':bool(tbl.select('table'))})
    return {'status':'extracted','text':soup.get_text('\n',strip=True),'tables':tables}


def hwpx_document(data: bytes) -> dict:
    def name(el): return el.tag.split('}')[-1]
    def child(el,key): return next((e for e in el.iter() if name(e)==key),None)
    texts,tables=[],[]
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        info=z.infolist()
        if len(info)>1000 or sum(i.file_size for i in info)>50*1024*1024:
            raise ValueError('HWPX 압축해제 용량/파일 수 제한 초과')
        if any(i.file_size>20*1024*1024 or i.file_size/max(i.compress_size,1)>500 for i in info):
            raise ValueError('HWPX 압축비/파일 크기 제한 초과')
        names=sorted((i.filename for i in info if re.fullmatch(r'Contents/section\d+\.xml',i.filename)),
                     key=lambda n:int(re.search(r'section(\d+)',n).group(1)))
        if not names: raise ValueError('HWPX 본문 섹션 없음')
        for section in names:
            root=ElementTree.fromstring(z.read(section))
            texts.extend(e.text or '' for e in root.iter() if name(e)=='t')
            for tbl in (e for e in root.iter() if name(e)=='tbl'):
                cells=[]
                trs=[e for e in tbl if name(e)=='tr']
                for r,tr in enumerate(trs):
                    for c,tc in enumerate(e for e in tr if name(e)=='tc'):
                        addr,span=child(tc,'cellAddr'),child(tc,'cellSpan')
                        row=int(addr.get('rowAddr',r)) if addr is not None else r
                        col=int(addr.get('colAddr',c)) if addr is not None else c
                        rs=_span(span.get('rowSpan',1)) if span is not None else 1
                        cs=_span(span.get('colSpan',1)) if span is not None else 1
                        if not 0<=row<1000 or not 0<=col<200: raise ValueError('비정상 표 좌표')
                        cells.append({'row':row,'col':col,'rowspan':rs,'colspan':cs,
                            'text':' '.join((e.text or '') for e in tc.iter() if name(e)=='t'), 'header':False})
                if len(cells)>10000 or len(tables)>=100: raise ValueError('표 크기/개수 제한 초과')
                tables.append({'rows':max((c['row']+c['rowspan'] for c in cells),default=0),
                    'cols':max((c['col']+c['colspan'] for c in cells),default=0), 'cells':cells,
                    'source':'hwpx','section':section, 'header_inferred':True,
                    'nested':sum(1 for e in tbl.iter() if name(e)=='tbl')>1})
    return {'status':'extracted','text':'\n'.join(texts),'tables':tables}


def pdf_document(data: bytes) -> dict:
    import pdfplumber
    texts,tables,warnings=[],[],[]
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        if len(pdf.pages)>60: raise ValueError('PDF 60페이지 초과')
        for number,page in enumerate(pdf.pages,1):
            text=page.extract_text() or ''
            texts.append(text)
            if len(text.strip())<20: warnings.append(f'page_{number}:needs_vision')
            page_tables=page.find_tables()
            if len(page_tables)>50: raise ValueError('페이지당 표 개수 제한 초과')
            for table in page_tables:
                grid=table.extract()
                cells=[]
                for r,row in enumerate(grid):
                    for c,value in enumerate(row):
                        if value is not None:
                            cells.append({'row':r,'col':c,'rowspan':1,'colspan':1,
                                          'text':value,'header':False})
                tables.append({'rows':len(grid),'cols':max(map(len,grid),default=0),'cells':cells,
                    'source':'pdf','page':number,'bbox':list(table.bbox),'raw_grid':grid,
                    'geometry_uncertain':True})  # PDF merged/header geometry is not a gold label.
    return {'status':'needs_vision' if warnings else 'extracted', 'text':'\n\n'.join(texts),
            'tables':tables,'warnings':warnings}


def _hwp_records(data: bytes):
    import struct
    off = 0
    while off + 4 <= len(data):
        head = struct.unpack('<I', data[off:off+4])[0]
        tag, level, size = head & 0x3ff, (head >> 10) & 0x3ff, (head >> 20) & 0xfff
        hdr = 4
        if size == 0xfff:
            if off + 8 > len(data):
                break
            size = struct.unpack('<I', data[off+4:off+8])[0]
            hdr = 8
        body = data[off+hdr:off+hdr+size]
        yield tag, level, body
        off += hdr + size


def _hwp_text(raw: bytes) -> str:
    units = raw.decode('utf-16-le', 'replace')
    out, i = [], 0
    while i < len(units):
        c = ord(units[i])
        if c == 0x0d:
            out.append('\n')
            i += 1
        elif c in (0x09, 0x0a):
            out.append(' ')
            i += 1
        elif c == 0:
            i += 1
        elif c < 0x20:
            # Extended inline controls (objects, fields, hidden chars) occupy 8
            # utf-16 units only when followed by a printable control code pair;
            # lone control bytes and padding consume a single unit.
            nxt = ord(units[i + 1]) if i + 1 < len(units) else 0
            i += 8 if nxt >= 0x20 else 1
        else:
            out.append(units[i])
            i += 1
    return ''.join(out).strip()


def _hwp_cells(section: bytes):
    import struct
    recs = list(_hwp_records(section))
    tables = []
    i = 0
    while i < len(recs):
        tag, level, body = recs[i]
        i += 1
        if tag != 71 or len(body) < 4 or struct.unpack('<I', body[:4])[0] != 0x74626c20:
            continue
        cells, nested = [], 0
        while i < len(recs) and recs[i][1] > level:
            ctag, clevel, cbody = recs[i]
            if ctag == 72 and clevel == level + 1 and len(cbody) >= 26:
                col, row, colspan, rowspan = struct.unpack('<HHHH', cbody[8:16])
                texts, skip = [], None
                for j in range(i + 1, len(recs)):
                    t2, l2, b2 = recs[j]
                    if l2 < clevel or (l2 == clevel and t2 == 72):
                        break
                    if skip is not None:
                        if l2 > skip:
                            continue
                        skip = None
                    if t2 == 71 and len(b2) >= 4 \
                            and struct.unpack('<I', b2[:4])[0] == 0x74626c20:
                        nested += 1
                        skip = l2
                    elif t2 == 67:
                        t = _hwp_text(b2)
                        if t:
                            texts.append(t)
                cells.append({'row': row, 'col': col, 'rowspan': max(1, rowspan),
                              'colspan': max(1, colspan), 'text': '\n'.join(texts),
                              'header': row == 0})
            i += 1
        if cells:
            addrs = [(c['row'], c['col']) for c in cells]
            end_r = max(c['row'] + c['rowspan'] for c in cells)
            end_c = max(c['col'] + c['colspan'] for c in cells)
            covered = set()
            for c in cells:
                for rr in range(c['row'], c['row'] + c['rowspan']):
                    for cc in range(c['col'], c['col'] + c['colspan']):
                        covered.add((rr, cc))
            uncertain = len(addrs) != len(set(addrs)) or len(covered) != end_r * end_c
            tables.append({'rows': end_r, 'cols': end_c, 'cells': cells,
                           'source': 'hwp', 'nested': nested > 0,
                           'geometry_uncertain': bool(nested or uncertain)})
    return tables


def hwp_document(data: bytes) -> dict:
    import struct
    import olefile
    with olefile.OleFileIO(io.BytesIO(data)) as ole:
        header = ole.openstream('FileHeader').read()
        flags = struct.unpack('<I', header[36:40])[0]
        if flags & 2:
            return {'status': 'needs_parser', 'text': '', 'tables': [],
                    'kind': 'hwp', 'note': 'encrypted'}
        texts, tables = [], []
        for name in ole.listdir():
            if name[0] == 'BodyText' and len(name) > 1:
                raw = ole.openstream(name).read()
                if flags & 1:
                    import zlib
                    raw = zlib.decompress(raw, -15)
                paras, stack = [], []
                for tag, level, body in _hwp_records(raw):
                    while stack and level <= stack[-1]:
                        stack.pop()
                    if tag == 71 and len(body) >= 4 \
                            and struct.unpack('<I', body[:4])[0] == 0x74626c20:
                        stack.append(level)
                    elif tag == 67 and not stack:
                        t = _hwp_text(body)
                        if t:
                            paras.append(t)
                texts.extend(paras)
                tables.extend(_hwp_cells(raw))
        status = 'extracted' if texts or tables else 'needs_parser'
        return {'status': status, 'text': '\n'.join(texts), 'tables': tables,
                'kind': 'hwp'}


def docx_document(data: bytes) -> dict:
    ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ElementTree.fromstring(z.read('word/document.xml'))
    texts, tables = [], []
    for tbl in root.iter(ns + 'tbl'):
        rows, grid_cols = list(tbl.iter(ns + 'tr')), 0
        cells = []
        vm = {}
        for r, tr in enumerate(rows):
            col = 0
            for tc in tr.findall(ns + 'tc'):
                tcpr = tc.find(ns + 'tcPr')
                span, merge = 1, None
                if tcpr is not None:
                    gs = tcpr.find(ns + 'gridSpan')
                    if gs is not None:
                        span = int(gs.get(ns + 'val') or 1)
                    vm_el = tcpr.find(ns + 'vMerge')
                    if vm_el is not None:
                        merge = vm_el.get(ns + 'val') or 'cont'
                while (r, col) in vm:
                    col += 1
                if merge == 'cont':
                    vm[(r, col)] = vm.get((r - 1, col), col)
                    col += span
                    continue
                rowspan = 1
                if merge == 'restart':
                    vm[(r, col)] = col
                    for nr in range(r + 1, len(rows)):
                        nxt = rows[nr].findall(ns + 'tc')
                        # count continuation cells in this column on later rows
                        found = False
                        scan = 0
                        for ntc in nxt:
                            npr = ntc.find(ns + 'tcPr')
                            nspan = int(npr.find(ns + 'gridSpan').get(ns + 'val') or 1) \
                                if npr is not None and npr.find(ns + 'gridSpan') is not None else 1
                            nv = npr.find(ns + 'vMerge') if npr is not None else None
                            if scan == col and nv is not None \
                                    and (nv.get(ns + 'val') or 'cont') == 'cont':
                                found = True
                                break
                            scan += nspan
                        if not found:
                            break
                        rowspan += 1
                txt = '\n'.join(p.text or '' for p in tc.iter(ns + 't'))
                cells.append({'row': r, 'col': col, 'rowspan': rowspan, 'colspan': span,
                              'text': txt.strip(), 'header': r == 0})
                col += span
            grid_cols = max(grid_cols, col)
        if cells:
            tables.append({'rows': len(rows), 'cols': grid_cols, 'cells': cells,
                           'source': 'docx', 'nested': len(list(tbl.iter(ns + 'tbl'))) > 1})
    for p in root.iter(ns + 'p'):
        t = ''.join(e.text or '' for e in p.iter(ns + 't'))
        if t.strip():
            texts.append(t.strip())
    return {'status': 'extracted' if texts or tables else 'needs_parser',
            'text': '\n'.join(texts), 'tables': tables}


def table_pattern(table: dict) -> dict:
    cells=table['cells']
    merged=any(c['rowspan']>1 or c['colspan']>1 for c in cells)
    labels=' '.join(c['text'] for c in cells if c['row']==0 or c['col']==0)
    if merged or table.get('geometry_uncertain') or table.get('nested'):
        layout='scroll_table'
    elif re.search(r'동의|서명|성명|보호자',labels):
        layout='read_only_form'
    elif re.search(r'학년|학급',labels):
        layout='grade_cards'
    elif re.search(r'시간|시각',labels) and table['cols']<=4:
        layout='timeline'
    elif table['cols']==2 and re.search(r'일시|장소|대상|비용|준비물|기한|문의',labels):
        layout='key_value_cards'
    else:
        layout='scroll_table'
    anchors=sorted(set(re.findall(r'학년|학급|시간|시각|일시|장소|대상|비용|준비물|기한|동의|서명|성명|보호자',labels)))
    signature={'rows':table['rows'],'cols':table['cols'],'anchors':anchors,
               'spans':[(c['row'],c['col'],c['rowspan'],c['colspan']) for c in cells],
               'uncertain':bool(table.get('geometry_uncertain'))}
    return {'pattern':digest(encode(signature).encode()),'layout':layout,
            'features':signature,'method':'heuristic-v1','verified':False}


if __name__=='__main__':
    path,kind=sys.argv[1:3]
    data=Path(path).read_bytes()
    if kind=='pdf': result=pdf_document(data)
    elif kind=='hwpx': result=hwpx_document(data)
    elif kind in ('hwp','legacy_ole'): result=hwp_document(data)
    elif kind=='docx': result=docx_document(data)
    elif kind=='html': result=html_document(data.decode('utf-8','replace'))
    else: result={'status':'needs_vision' if kind=='image' else 'needs_parser','text':'','tables':[]}
    print(json.dumps(result,ensure_ascii=False))
