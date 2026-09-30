"""Whole-notice conversion using local parsers and versioned human review.

Original objects are immutable. Only approved, non-evaluation structures are
consulted; document facts are always read from the current files.
"""
from __future__ import annotations

import copy
import html
import io
import json
import re
import uuid
import zipfile
from pathlib import Path
from urllib.parse import quote

from .core import Store, digest, encode, kst_now, write_jsonl
from .extract import LAYOUTS, PARSER_VERSION, detect_kind, html_document, table_pattern
from .render import CSS, render_mobile, render_page

CONVERTER_VERSION = 'moa-notice-v1'
ROLES = ('convert', 'attachment')
REASONS = {'unconfirmed': '아직 미확인', 'missing': '내용 누락',
           'table': '표 관계 오류', 'readability': '읽기 불편',
           'role': '파일 역할 오류', 'incomplete': '원본 일부 페이지만 제공됨',
           'other': '기타'}
FORM_NAME = re.compile(r'신청서|동의서|양식|서식|참고자료|신청양식')
FORM_TITLE = re.compile(r'^(?:[\w가-힣 ·ㆍ·:()\-]{0,70})?(?:희망 신청서|희망신청서|참가 신청서|참가신청서|동의서|신청서)\s*$')
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_FILES = 10


def default_role(filename: str) -> str:
    return 'attachment' if FORM_NAME.search(filename) else 'convert'


def validate_file(data: bytes, filename: str) -> dict:
    filename = Path(filename.replace('\\', '/')).name
    if not filename or len(filename) > 220 or any(ord(c) < 32 for c in filename):
        raise ValueError('파일명이 올바르지 않습니다.')
    if not data or len(data) > MAX_FILE_BYTES:
        raise ValueError('파일은 1바이트~25MB여야 합니다.')
    kind = detect_kind(data, filename)
    extension = Path(filename).suffix.lower()
    if extension not in ('.hwp','.hwpx','.docx','.pdf','.jpg','.jpeg','.png','.gif',
                          '.webp','.tif','.tiff','.html','.htm','.txt','.xlsx','.xls'):
        raise ValueError('지원하는 문서·이미지 파일을 선택하세요.')
    expected = {'.hwp':'hwp','.hwpx':'hwpx','.docx':'docx','.pdf':'pdf',
                '.jpg':'image','.jpeg':'image','.png':'image','.gif':'image',
                '.webp':'image','.tif':'image','.tiff':'image','.html':'html','.htm':'html'}
    if extension in expected and kind != expected[extension]:
        raise ValueError('파일 내용과 확장자가 일치하지 않습니다.')
    return {'filename': filename, 'kind': kind}


def create_upload(store: Store, uploads: list[tuple[str, bytes]], title: str = '', attachment_indices=(), evaluation=False) -> str:
    if not 1 <= len(uploads) <= MAX_FILES or sum(len(d) for _,d in uploads) > MAX_UPLOAD_BYTES:
        raise ValueError('최대 10개 파일, 전체 50MB까지 업로드할 수 있습니다.')
    checked = [(validate_file(data, name), data) for name,data in uploads]
    files = []
    for i,(meta, data) in enumerate(checked):
        obj = store.object(data, meta['filename'])
        files.append({**obj, **meta, 'id': str(i),
                      'role': 'attachment' if i in attachment_indices else default_role(meta['filename']),
                      'scope': 'auto', 'form_start': None, 'boundary_confirmed': False,
                      'incomplete': False})
    ident = uuid.uuid4().hex
    title = title.strip()[:300] or Path(files[0]['filename']).stem
    now = kst_now().isoformat()
    store.db.execute('INSERT INTO mobile_notices(id,title,files,split,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                     (ident,title,encode(files),'eval' if evaluation else 'train',now,now))
    store.db.commit()
    return ident


def add_attachments(store: Store, ident: str, rid: str, uploads: list[tuple[str,bytes]]):
    row = notice_row(store,ident)
    if (row['latest_revision'] or '') != rid or row['state'] == 'processing':
        raise ValueError('최신 버전을 열어 다시 첨부하세요.')
    files = json.loads(row['files'])
    if not uploads or len(files)+len(uploads) > MAX_FILES or sum(len(d) for _,d in uploads) > MAX_UPLOAD_BYTES:
        raise ValueError('첨부파일 개수·용량 제한 초과')
    checked = [(validate_file(data,name),data) for name,data in uploads]
    for meta,data in checked:
        obj = store.object(data,meta['filename'])
        files.append({**obj,**meta,'id':uuid.uuid4().hex[:12],'role':'attachment',
                      'scope':'auto','form_start':None,'boundary_confirmed':False,'incomplete':False})
    previous = revision(store,ident)
    if previous:
        data = previous['data']
        data['files'] = files
        save_revision(store,ident,data)
    else:
        store.db.execute('UPDATE mobile_notices SET files=?,updated_at=? WHERE id=?',
                          (encode(files),kst_now().isoformat(),ident))
        store.db.commit()


def register_collected(store: Store, doc: dict) -> str:
    """Collector and uploads share the same roles; collection quotas stay unchanged."""
    row = store.db.execute('SELECT id FROM mobile_notices WHERE origin_notice_id=?', (doc['id'],)).fetchone()
    evaluation = store.db.execute("SELECT 1 FROM cases WHERE notice_id=? AND split='eval' LIMIT 1",
                                   (doc['id'],)).fetchone()
    if row:
        if evaluation:
            store.db.execute("UPDATE mobile_notices SET split='eval' WHERE id=?",(row['id'],))
            store.db.commit()
            export_mobile(store)
        return row['id']
    files = []
    if doc.get('body_html', '').strip():
        obj = store.object(doc['body_html'].encode(), '게시글 본문.html')
        files.append({**obj, 'kind':'html', 'id':'body', 'role':'convert', 'scope':'auto',
                      'form_start':None, 'boundary_confirmed':False, 'incomplete':False})
    for i,asset in enumerate(doc.get('assets', [])):
        files.append({**asset, 'kind':asset.get('kind','unsupported'), 'id':str(i), 'role':default_role(asset.get('filename','')),
                      'scope':'auto', 'form_start':None, 'boundary_confirmed':False,
                      'incomplete':False})
    ident = digest(('collected:' + doc['id']).encode())[:32]
    now = kst_now().isoformat()
    # A whole document inherits the most restrictive split of its table cases.
    store.db.execute('INSERT OR IGNORE INTO mobile_notices'
                     '(id,origin_notice_id,title,files,split,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                     (ident,doc['id'],doc.get('title','안내'),encode(files),
                      'eval' if evaluation else 'train',now,now))
    store.db.commit()
    return ident


def notice_row(store: Store, ident: str):
    row = store.db.execute('SELECT * FROM mobile_notices WHERE id=?', (ident,)).fetchone()
    if not row:
        raise ValueError('안내문을 찾을 수 없습니다.')
    return row


def revision(store: Store, ident: str, revision_id: str = '') -> dict | None:
    row = notice_row(store,ident)
    rid = revision_id or row['latest_revision']
    found = store.db.execute('SELECT * FROM mobile_revisions WHERE id=? AND notice_id=?', (rid,ident)).fetchone()
    return {**dict(found), 'data':json.loads(found['payload'])} if found else None


def file_roles(files: list, edits: dict) -> list:
    out = copy.deepcopy(files)
    for file in out:
        edit = edits.get(file['id'], {})
        role = edit.get('role', file['role'])
        if role not in ROLES:
            raise ValueError('파일 역할은 모바일 변환 또는 원본 첨부 유지입니다.')
        file['role'] = role
        file['scope'] = edit.get('scope', file.get('scope','auto'))
        if file['scope'] not in ('auto','notice','mixed'):
            raise ValueError('문서 범위를 확인하세요.')
        boundary = edit.get('form_start', file.get('form_start'))
        file['form_start'] = int(boundary) if boundary not in ('',None) else None
        if file['form_start'] is not None and not 0 <= file['form_start'] <= 2000:
            raise ValueError('서식 시작 구역 번호를 확인하세요.')
        file['boundary_confirmed'] = bool(edit.get('boundary_confirmed', False))
        file['incomplete'] = bool(edit.get('incomplete', False))
    return out


def _decorative(text: str) -> bool:
    compact = re.sub(r'\s+', '', text)
    return not re.search(r'[\w가-힣]', compact) or \
        bool(re.fullmatch(r'([가-힣]{2,30}학교)(?:\1|[·ㆍ])*', compact) and compact.count('학교') >= 5)


def ordered_blocks(result: dict) -> tuple[list, list]:
    """Reflow layout cells but keep real data tables and source pointers."""
    tables = result.get('tables', [])
    omitted = []
    def flatten(items, ancestors=()):
        out = []
        for item in items:
            if item['type'] == 'paragraph':
                text = item.get('text','')
                if not text.strip(): continue
                if _decorative(text):
                    omitted.append({'reason':'decorative','text':text})
                else:
                    out.append({'type':'paragraph','text':text})
            elif item['type'] == 'table':
                i = item['table_index']
                if not 0 <= i < len(tables) or i in ancestors:
                    raise ValueError('표 참조가 올바르지 않습니다.')
                table = tables[i]
                cells = table.get('cells', [])
                empty = sum(not c.get('text','').strip() for c in cells)
                layout = table.get('nested') or table.get('cols') == 1 or \
                    (cells and empty > len(cells)/3) or table.get('rows') == 1
                if layout:
                    for cell in sorted(cells,key=lambda c:(c['row'],c['col'])):
                        blocks = cell.get('blocks') or [{'type':'paragraph','text':cell.get('text','')}]
                        out.extend(flatten(blocks, (*ancestors,i)))
                else:
                    out.append({'type':'table','table':copy.deepcopy(table),'source_table_index':i})
        return out
    if 'blocks' in result:
        return flatten(result['blocks']), omitted
    # Older/text-only parsers retain every line and every table. Uncertain order is
    # disclosed for comparison rather than guessed from a page image.
    items = [{'type':'paragraph','text':t} for t in result.get('text','').splitlines() if t.strip()]
    items.extend({'type':'table','table':copy.deepcopy(t),'source_table_index':i}
                 for i,t in enumerate(tables))
    return items, omitted


def repeated_columns(table: dict) -> tuple[dict, str]:
    """Recognise two identical semantic 3-column headers, not merely six columns."""
    if table.get('cols') != 6 or table.get('nested') or table.get('geometry_uncertain'):
        return table, ''
    by_row = {}
    for c in table['cells']:
        by_row.setdefault(c['row'], {})[c['col']] = c
    header_row = None
    for r,cells in by_row.items():
        labels = [re.sub(r'\s+','',cells.get(c,{}).get('text','')) for c in range(6)]
        if labels[:3] == labels[3:] and labels[0] in ('연번','번호') and \
                labels[1] in ('학과','학과명') and labels[2] in ('수업교실','교실','장소'):
            header_row = r
            break
    if header_row is None:
        return table, ''
    data = []
    for r,cells in sorted(by_row.items()):
        if r <= header_row: continue
        for offset in (0,3):
            group = [cells.get(offset + c) for c in range(3)]
            texts = [c.get('text','').strip() if c else '' for c in group]
            if not any(texts): continue
            if not all(group) or not texts[0].isdigit() or not all(texts) or \
                    any(c.get('colspan',1) != 1 or c.get('rowspan',1) != 1 for c in group):
                return table, ''
            data.append(texts)
    numbers = sorted(int(row[0]) for row in data)
    if not numbers or numbers != list(range(1,max(numbers) + 1)):
        return table, ''
    data.sort(key=lambda row:int(row[0]))
    labels = [by_row[header_row][c]['text'] for c in range(3)]
    cells = [{'row':r,'col':c,'rowspan':1,'colspan':1,'text':text,'header':r==0}
             for r,row in enumerate([labels,*data]) for c,text in enumerate(row)]
    caption = '\n'.join(c['text'] for c in table['cells'] if c['row'] < header_row and c['text'])
    return {'rows':len(data)+1,'cols':3,'cells':cells,'source':table.get('source'),
            'caption':caption,'geometry_uncertain':False}, 'repeated-columns-v1'


def structure_key(table: dict) -> str:
    """Headers, merges and roles only: never include a past document's fact values."""
    cells = table.get('cells', [])
    header_rows = {c['row'] for c in cells if c.get('header')}
    if not header_rows and cells:
        header_rows = {min(c['row'] for c in cells)}
    labels = [re.sub(r'\d|\s','',c.get('text','')) for c in cells if c['row'] in header_rows]
    if not any(re.search(r'[A-Za-z가-힣]',label) for label in labels):
        return ''  # No semantic evidence: dimensions alone must not select a case.
    return digest(encode({'headers':labels,'cols':table.get('cols'),
                          'body_merges':[(c['col'],c.get('rowspan',1),c.get('colspan',1))
                                         for c in cells if c['row'] not in header_rows and
                                         (c.get('rowspan',1)>1 or c.get('colspan',1)>1)],
                          'header_spans':[(c['col'],c.get('rowspan',1),c.get('colspan',1))
                                          for c in cells if c['row'] in header_rows]}).encode())[:24]


def approved_rules(store: Store) -> dict:
    from .corpus import mobile_allowed
    rows = store.db.execute("SELECT n.id,n.origin_notice_id,n.approved_revision,r.payload FROM mobile_notices n"
                            " JOIN mobile_revisions r ON r.id=n.approved_revision"
                            " WHERE n.state='approved' AND n.latest_revision=n.approved_revision AND n.split!='eval'")
    rules = {}
    for row in rows:
        if not mobile_allowed(store,row):continue
        data = json.loads(row['payload'])
        if data.get('parser_version') != PARSER_VERSION or data.get('converter_version') != CONVERTER_VERSION:
            continue
        for b in data.get('blocks', []):
            if b['type'] == 'table':
                key = structure_key(b['table'])
                if not key: continue
                rules[key] = {'notice_id':row['id'],
                    'revision_id':row['approved_revision'], 'block_id':b['id'],
                    'layout':b.get('layout','scroll_table'), 'rule_id':'approved-structure-layout-v1'}
    return rules


def build_conversion(store: Store, ident: str, edits: dict | None = None, title: str = '') -> dict:
    from .learn import extract_asset
    row = notice_row(store,ident)
    files = file_roles(json.loads(row['files']), edits) if edits is not None else json.loads(row['files'])
    blocks, warnings, excluded, audit, source_results = [], [], [], [], []
    rules = approved_rules(store)
    for file in files:
        if file['role'] == 'attachment': continue
        result = extract_asset(store,file)
        source_results.append({'file_id':file['id'],'sha256':file['sha256'],
                               'parser_version':result.get('parser_version',PARSER_VERSION),
                               'status':result['status'], 'text':result.get('text','')})
        if result['status'] != 'extracted':
            warnings.append({'code':result['status'],'file_id':file['id'],
                             'message':file['filename']+' — '+result['status']+'; 원문 확인이 필요합니다.'})
        if file.get('incomplete'):
            warnings.append({'code':'incomplete','file_id':file['id'],'message':'일부 페이지 자료입니다. 나머지 내용을 추정하지 않았습니다.'})
        content, decorative = ordered_blocks(result)
        # School mastheads are source metadata, not the opening of the 안내 body.
        # Preserve them after the notice; identify only an explicit title preceded
        # by a short 가정통신문 masthead, never arbitrary introductory conditions.
        file_title = re.sub(r'\s+','',Path(file['filename']).stem)
        for index,item in enumerate(content):
            if item['type']=='paragraph' and re.sub(r'\s+','',item['text']) == file_title:
                prefix = content[:index]
                if prefix and all(b['type']=='paragraph' and len(b['text'])<150 for b in prefix) and \
                        any('가정통신문' in re.sub(r'\s+','',b['text']) for b in prefix):
                    for b in prefix: b['placement']='source_info'
                    item['placement']='title'
                break
        excluded.extend({**x,'file_id':file['id']} for x in decorative)
        form_at = file.get('form_start')
        if form_at is None and file.get('scope') != 'notice':
            for i,b in enumerate(content):
                text = b.get('text','')
                if b['type'] == 'table':
                    text = ' '.join(c['text'] for c in b['table'].get('cells',[]))
                    form = sum(word in text for word in ('지원자','성명','보호자','서명','신청자')) >= 3
                else:
                    form = bool(FORM_TITLE.fullmatch(text.strip()))
                if form:
                    form_at = i
                    break
        if file.get('scope') == 'mixed' and form_at is None:
            warnings.append({'code':'boundary','file_id':file['id'],'message':'혼합 문서의 서식 시작 구역을 지정하세요.'})
        if form_at is not None and not file.get('boundary_confirmed'):
            warnings.append({'code':'boundary','file_id':file['id'],
                             'message':f'안내/서식 경계 후보는 구역 {form_at}입니다. 원문 대조 후 경계를 확인하세요.'})
        for i,b in enumerate(content):
            b.update(id=f"{file['id']}:{i}",file_id=file['id'],source_block=i)
            if form_at is not None and i >= form_at:
                excluded.append({'reason':'form','file_id':file['id'],'source_block':i,'block':b})
                continue
            if b['type'] == 'table':
                original = copy.deepcopy(b['table'])
                table, local_rule = repeated_columns(original)
                b['table'] = table
                b['original_table'] = original
                b['layout'] = 'grade_cards' if local_rule else table_pattern(table)['layout']
                if local_rule:
                    audit.append({'block_id':b['id'],'rule_id':local_rule,'method':'fixed-component'})
                rule = rules.get(structure_key(table))
                if rule and rule['notice_id'] != ident:
                    b['layout'] = rule['layout']
                    audit.append({'block_id':b['id'],**rule,'method':'approved-structure-only'})
                if table.get('geometry_uncertain') or table.get('nested'):
                    warnings.append({'code':'geometry','file_id':file['id'],'message':'표 병합·관계를 원문과 대조하세요.'})
            blocks.append(b)
    if not any(f['role'] == 'convert' for f in files):
        warnings.append({'code':'no_guide','message':'모바일 변환할 안내 파일을 선택하세요.'})
    if not blocks:
        warnings.append({'code':'empty','message':'읽을 수 있는 안내 내용이 없습니다.'})
    return {'title':title.strip()[:300] or row['title'], 'files':files, 'blocks':blocks,
            'warnings':warnings, 'excluded':excluded, 'sources':source_results,
            'recommendations':audit, 'parser_version':PARSER_VERSION,
            'converter_version':CONVERTER_VERSION, 'human_corrected':False}


def validate_blocks(blocks: list) -> None:
    if not isinstance(blocks,list) or not 1 <= len(blocks) <= 2000:
        raise ValueError('안내 구역은 1~2,000개여야 합니다.')
    if len(encode(blocks)) > 2 * 1024 * 1024:
        raise ValueError('수정 내용 용량 제한 초과')
    for b in blocks:
        if 'note' in b and not isinstance(b['note'],str): raise ValueError('단위·각주는 문자열이어야 합니다.')
        if b.get('type') == 'paragraph':
            if not isinstance(b.get('text'),str): raise ValueError('본문은 문자열이어야 합니다.')
        elif b.get('type') == 'table':
            t = b.get('table',{})
            if 'caption' in t and not isinstance(t['caption'],str): raise ValueError('표 제목은 문자열이어야 합니다.')
            if b.get('layout') not in LAYOUTS:
                raise ValueError('지원하는 표 표현을 선택하세요.')
            rows,cols = t.get('rows'),t.get('cols')
            if type(rows) is not int or type(cols) is not int or not 1 <= rows <= 1000 or not 1 <= cols <= 200:
                raise ValueError('표 행·열 범위 오류')
            cells = t.get('cells',[])
            if not isinstance(cells,list) or len(cells) > 10000: raise ValueError('표 셀 개수 제한')
            occupied = set()
            for c in cells:
                values = [c.get('row'),c.get('col'),c.get('rowspan',1),c.get('colspan',1)]
                if any(type(v) is not int for v in values) or not isinstance(c.get('text'),str):
                    raise ValueError('표 셀 좌표·내용 오류')
                r,col,rs,cs = values
                if r < 0 or col < 0 or rs < 1 or cs < 1 or r+rs > rows or col+cs > cols:
                    raise ValueError('병합 셀 범위 오류')
                for rr in range(r,r+rs):
                    for cc in range(col,col+cs):
                        if (rr,cc) in occupied: raise ValueError('표 셀이 겹칩니다.')
                        occupied.add((rr,cc))
        else:
            raise ValueError('지원하지 않는 안내 구역')


def render_notice(data: dict, file_url=None, candidate=True) -> str:
    file_url = file_url or (lambda f: '/mobile/file?id=%s&file=%s' % (data['notice_id'],quote(f['id'])))
    parts = []
    if candidate:
        parts.append('<p class="moa-note">미검수 변환 결과 — 원문 대조 후 사용하세요.</p>')
    for w in data.get('warnings',[]):
        parts.append('<p class="moa-warning">'+html.escape(w['message'])+'</p>')
    parts.append('<article class="moa-notice">')
    metadata = []
    for b in data.get('blocks',[]):
        if b['type'] == 'paragraph':
            text = b['text']
            if b.get('placement')=='source_info':
                metadata.append(text)
                continue
            if b.get('placement')=='title' and text==data['title']: continue
            heading = text.startswith('[') and text.rstrip().endswith(']') or \
                bool(re.fullmatch(r'[가-하][.．]\s*.{1,25}',text))
            tag = 'h2' if heading else 'p'
            parts.append(f'<{tag} class="moa-paragraph">{html.escape(text)}</{tag}>')
        elif b['type'] == 'table':
            if b['table'].get('caption'):
                parts.append('<h2>'+html.escape(b['table']['caption'])+'</h2>')
            parts.append(render_mobile(b['table'],b['layout']))
            if b.get('note'):
                parts.append('<p class="moa-paragraph">'+html.escape(b['note'])+'</p>')
    if metadata:
        parts.append('<section><h2>발신 정보·문의처 (원문 표기)</h2>'+''.join(
            '<p class="moa-paragraph">'+html.escape(t)+'</p>' for t in metadata)+'</section>')
    parts.append('</article><section class="moa-attachments"><h2>원본 및 함께 첨부한 파일</h2><ul>')
    for file in data['files']:
        role = '원본 첨부 유지' if file['role'] == 'attachment' else '안내 원본 / 작성용 서식 포함 시 원본 사용'
        parts.append('<li><a href="%s">%s</a><span> · %s</span></li>' %
                     (html.escape(file_url(file),quote=True),html.escape(file['filename']),role))
    parts.append('</ul></section>')
    return render_page(data['title'], ''.join(parts)).replace('</style>',
        '.moa-notice,.moa-attachments{max-width:720px;margin:auto;background:white;padding:18px;border-radius:12px}'
        '.moa-paragraph{white-space:pre-wrap;line-height:1.8;overflow-wrap:anywhere}'
        'h2{font-size:18px;line-height:1.6}h3{max-width:720px;margin:16px auto;font-size:22px}'
        '.moa-warning{max-width:720px;margin:8px auto;color:#873a16;background:#fff0df;padding:12px}'
        '.moa-attachments{margin-top:12px}.moa-attachments li{margin:12px 0;overflow-wrap:anywhere}'
        '.moa-attachments span{font-size:12px;color:#667}</style>')


def save_revision(store: Store, ident: str, data: dict) -> dict:
    row = notice_row(store,ident)
    data = copy.deepcopy(data)
    data['notice_id'] = ident
    fingerprint = digest(encode(data).encode())
    sequence = store.db.execute('SELECT COALESCE(max(sequence),0)+1 FROM mobile_revisions WHERE notice_id=?', (ident,)).fetchone()[0]
    # Include sequence so returning to earlier content is still a new review version.
    rid = digest((fingerprint + ':' + str(sequence)).encode())
    now = kst_now().isoformat()
    obj = store.object(render_notice(data).encode(), 'candidate.html')
    store.db.execute('INSERT INTO mobile_revisions VALUES(?,?,?,?,?,?)',
                     (rid,ident,sequence,encode(data),obj['sha256'],now))
    store.db.execute("UPDATE mobile_notices SET title=?,files=?,latest_revision=?,approved_revision=NULL,"
                     " state='candidate',updated_at=? WHERE id=?",
                     (data['title'],encode(data['files']),rid,now,ident))
    store.db.commit()
    export_mobile(store)
    return revision(store,ident)


def generate(store: Store, ident: str, edits=None, title='') -> dict:
    result=save_revision(store,ident,build_conversion(store,ident,edits,title))
    row=notice_row(store,ident)
    if row['origin_notice_id']:
        from .corpus import classify_notice
        classify_notice(store,row['origin_notice_id'])
    return result


def correct(store: Store, ident: str, rid: str, blocks: list, title: str = '') -> dict:
    row = notice_row(store,ident)
    if rid != row['latest_revision'] or row['state']=='processing': raise ValueError('다른 변경이 저장됐습니다. 새 버전을 열어 다시 수정하세요.')
    validate_blocks(blocks)
    data = revision(store,ident)['data']
    # Only current content/structure is editable. Source files and audit cannot be
    # rewritten by a submitted JSON object.
    data['blocks'] = blocks
    data['title'] = title.strip()[:300] or data['title']
    data['human_corrected'] = True
    data['correction_base'] = rid
    return save_revision(store,ident,data)


def decide(store: Store, ident: str, rid: str, action: str, reviewer: str,
           reason='unconfirmed', note='', compared=False, rights=False, privacy=False) -> None:
    row = notice_row(store,ident)
    if rid != row['latest_revision'] or row['state']=='processing': raise ValueError('검수 화면이 이전 버전입니다. 최신 결과를 확인하세요.')
    if action not in ('approved','held','rejected','candidate') or reason not in REASONS:
        raise ValueError('검수 상태·사유를 확인하세요.')
    if not reviewer.strip(): raise ValueError('검수자 이름을 입력하세요.')
    data = revision(store,ident)['data']
    if action == 'approved':
        if not all((compared,rights,privacy)): raise ValueError('원문 비교·이용 권한·개인정보 확인 후 승인하세요.')
        if any(w['code'] in ('needs_vision','needs_parser','parse_error','incomplete','boundary','empty','no_guide')
               for w in data.get('warnings',[])):
            raise ValueError('미지원·누락·서식 경계를 해결한 뒤 승인할 수 있습니다.')
    now = kst_now().isoformat()
    store.db.execute('INSERT INTO mobile_reviews(notice_id,revision_id,action,reason,note,reviewer,created_at)'
                     ' VALUES(?,?,?,?,?,?,?)', (ident,rid,action,reason,note[:2000],reviewer.strip()[:80],now))
    store.db.execute('UPDATE mobile_notices SET state=?,approved_revision=?,updated_at=? WHERE id=?',
                     (action,rid if action=='approved' else None,now,ident))
    store.db.commit()
    export_mobile(store)


def export_mobile(store: Store):
    rules = approved_rules(store)
    write_jsonl(store.root/'learning/mobile-approved-rules.jsonl',
                ({'structure_key':key,**value} for key,value in rules.items()))


def queue_conversion(store: Store, ident: str, rid: str, edits: dict, title: str):
    row = notice_row(store,ident)
    if (row['latest_revision'] or '') != rid or row['state']=='processing':
        raise ValueError('변환 중이거나 이전 화면입니다. 잠시 후 최신 버전을 확인하세요.')
    file_roles(json.loads(row['files']),edits)  # Validate before changing queue/state.
    payload = {'edits':edits,'title':title,'base_revision':rid}
    store.db.execute("INSERT INTO jobs(kind,ref_id,status,updated,payload) VALUES('mobile',?,'pending',?,?)"
                     " ON CONFLICT(kind,ref_id) DO UPDATE SET status='pending',attempts=0,owner=NULL,"
                     " next_attempt=NULL,updated=excluded.updated,payload=excluded.payload",
                     (ident,kst_now().isoformat(),encode(payload)))
    store.db.execute("UPDATE mobile_notices SET state='processing',approved_revision=NULL WHERE id=?",(ident,))
    store.db.commit()
    export_mobile(store)


def queue_outdated(store: Store) -> int:
    """An upgraded parser produces a fresh candidate; human versions stay archived."""
    rows = store.db.execute("SELECT n.id,n.title,n.files,n.latest_revision,r.payload FROM mobile_notices n"
                            " JOIN mobile_revisions r ON r.id=n.latest_revision WHERE n.state!='processing'").fetchall()
    count = 0
    for row in rows:
        data = json.loads(row['payload'])
        if data.get('parser_version') == PARSER_VERSION and data.get('converter_version') == CONVERTER_VERSION:
            continue
        files = json.loads(row['files'])
        edits = {f['id']:{key:f.get(key) for key in ('role','scope','form_start','boundary_confirmed','incomplete')}
                 for f in files}
        queue_conversion(store,row['id'],row['latest_revision'],edits,row['title'])
        count += 1
    export_mobile(store)
    return count


def drain_mobile(store: Store, limit=1):
    outcomes = []
    for job in store.claim_jobs('mobile',limit,'review-web'):
        row = None
        try:
            args = json.loads(job['payload'])
            row = notice_row(store,job['ref_id'])
            if (row['latest_revision'] or '') != args['base_revision']:
                raise ValueError('stale revision')
            generate(store,job['ref_id'],args['edits'],args['title'])
            store.finish_job(job['id'])
            outcomes.append('done')
        except Exception as exc:
            # Failure is observable, no exception text or document content in logs.
            store.fail_job(job['id'],type(exc).__name__,max_attempts=1)
            store.db.execute("UPDATE mobile_notices SET state=? WHERE id=?",
                              ('candidate' if row and row['latest_revision'] else 'files_ready',job['ref_id']))
            store.db.commit()
            outcomes.append('error')
    return outcomes


def bundle(store: Store, ident: str) -> bytes:
    row = notice_row(store,ident)
    rev = revision(store,ident)
    if not rev: raise ValueError('먼저 모바일 HTML을 생성하세요.')
    data = rev['data']
    paths, written = {}, set()
    out = io.BytesIO()
    with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for f in data['files']:
            filename = re.sub(r'[\\/\x00-\x1f]','_',f['filename'])
            path = 'attachments/'+f['sha256'][:12]+'-'+filename
            paths[f['id']] = path
            if path not in written:
                z.writestr(path,store.object_path(f['sha256']).read_bytes())
                written.add(path)
        z.writestr('index.html',render_notice(data,lambda f:paths[f['id']],row['state']!='approved'))
    return out.getvalue()
