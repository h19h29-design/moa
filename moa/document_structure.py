"""School-only, local structural evidence; never approval, learning or fetching.

Native markup and text heuristics have different certainty. Unknown page/note
inventories stay unknown; original roles and bytes are never changed here.
The research parser version and its cache are deliberately not modified.
"""
from __future__ import annotations

import copy
import io
import json
import os
import re
import subprocess
import sys
import unicodedata
import zipfile
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup, Comment, Declaration, Doctype, ProcessingInstruction
from defusedxml import ElementTree

from . import extract
from .core import digest, encode, write_json

VERSION = 'moa-school-structure-v1'
MAX_BLOCKS = 2000
MAX_RELATIONS = 4000
MAX_WARNINGS = 2000
MAX_RESULT_BYTES = 6 * 1024 * 1024
MAX_INPUT_BYTES = 25 * 1024 * 1024
NOTE = re.compile(r'^(?:※|\[\d{1,3}\]|\*{1,2}\s|주\s*\d{0,3}\s*[:：)])')
ZONES = {'header':'header', 'footer':'footer', 'footnote':'footnote', 'endnote':'footnote'}
HEADER_LABEL = re.compile(r'(?:대상|학년|학급|비용|금액|항목|내용|시간|시각|장소|일시|기한|준비물|문의|성명|보호자|서명|'
                          r'audience|grade|class|fee|cost|amount|item|details|description|date|time|name|signature)', re.I)


def _name(el):
    return el.tag.rsplit('}', 1)[-1]


def _attr(el, key):
    return next((v for k, v in el.attrib.items() if k.rsplit('}', 1)[-1] == key), '')


def _guess(text, index):
    if NOTE.match(text): return 'footnote', 'note_marker_candidate', 'candidate'
    if index < 3 and len(text) < 120 and re.fullmatch(r'.*(?:학교|School|가정통신문)', text, re.I):
        return 'header', 'masthead_candidate', 'candidate'
    return 'body', 'document_order', 'observed'


class _Inventory:
    def __init__(self, kind):
        self.kind, self.blocks = kind, []

    def add(self, text, locator, *, kind='body', basis='document_order', certainty='observed', **extra):
        if not text.strip() and kind not in ('table','footnote') and not extra.get('references') and not extra.get('anchor'): return
        if len(self.blocks) >= MAX_BLOCKS: raise ValueError('Structure block limit')
        if kind == 'body': kind, basis, certainty = _guess(text.strip(), len(self.blocks))
        block = dict(id='b'+str(len(self.blocks)), kind=kind, text=text.strip(),
                     locator={'format':self.kind, **locator}, basis=basis, certainty=certainty, **extra)
        self.blocks.append(block)
        return block

    def table(self, table, locator, *, references=(), zone='', container_id=''):
        t = {k:copy.deepcopy(table[k]) for k in ('rows','cols','geometry_uncertain','nested') if k in table}
        explicit = table.get('source') == 'html'
        t['cells'] = [{k:c[k] for k in ('row','col','rowspan','colspan','text','header','header_basis') if k in c}
                      for c in table.get('cells', [])]
        first = [c for c in t['cells'] if c['row'] == 0 and c['text'].strip()]
        label_row = len(first) >= 2 and all(HEADER_LABEL.fullmatch(c['text'].strip()) for c in first)
        for cell in t['cells']:
            marked = bool(explicit and cell.get('header'))
            native = cell.get('header') and cell.get('header_basis') == 'native_row_header'
            cell['header'] = bool(marked or native or label_row and cell['row'] == 0)
            cell['header_basis'] = ('explicit_markup' if marked else 'native_row_header' if native else
                                    'semantic_first_row_candidate' if cell['header'] else 'not_header')
        return self.add('\n'.join(c['text'] for c in t['cells']), locator, kind='table',
                 basis='source_table', table=t, references=list(references), zone=zone, container_id=container_id)


def _html_zone(node):
    return ('footnote' if node.get('role') in ('doc-footnote','doc-endnote') else
            node.name if node.name in ('header','footer') else '')


def _inside_html_control(node, boundary):
    parent = node.parent
    while parent is not None and parent is not boundary:
        if getattr(parent,'name',None) and (parent.name == 'table' or _html_zone(parent)): return True
        parent = parent.parent
    return False


def _html_text(node):
    return ' '.join(str(s).strip() for s in node.strings if str(s).strip() and not _inside_html_control(s,node))


def _html_refs(node):
    refs = []
    for a in node.find_all('a', href=True):
        if _inside_html_control(a,node): continue
        href = a['href']
        if href.startswith('#'):
            refs.append({'target':unquote(href[1:]), 'kind':'note' if a.get('role') == 'doc-noteref' or a.find_parent('sup') else 'internal'})
        elif not urlsplit(href).scheme and not href.startswith('//'):
            refs.append({'target':href, 'kind':'file'})
    return refs


def _html(data, inv):
    source = data.decode('utf-8', 'replace')
    parsed = extract.html_document(source)  # Reuse table/size validation.
    soup = BeautifulSoup(source, 'html.parser')
    for c in soup.find_all(string=lambda n:isinstance(n, (Comment,Declaration,Doctype,ProcessingInstruction))): c.extract()
    for bad in soup.select('script,style,nav'): bad.decompose()
    table_index = {id(t):i for i,t in enumerate(soup.find_all('table'))}
    for bad in soup.select('head'): bad.decompose()

    def visit(nodes, path='', inherited_zone='', container_id=''):
        for i, node in enumerate(nodes):
            tag = getattr(node, 'name', None)
            loc = {'path':path+'/'+(tag or 'text')+'['+str(i)+']'}
            if not tag:
                if str(node).strip(): inv.add(str(node), loc)
                continue
            zone = _html_zone(node)
            if tag == 'table':
                table = copy.deepcopy(parsed['tables'][table_index[id(node)]])
                cells = [td for tr in node.find_all('tr') if tr.find_parent('table') is node
                         for td in tr.find_all(['td','th'],recursive=False)]
                for cell,td in zip(table['cells'],cells): cell['text'] = _html_text(td)
                owner = node.find_parent(['header','footer'])
                block = inv.table(table, loc, references=_html_refs(node), zone=inherited_zone or (owner.name if owner else ''),
                          container_id=container_id)
            elif zone or tag in ('p','h1','h2','h3','h4','h5','h6','li','pre'):
                if node.find('table') and not zone:
                    visit(node.children, loc['path'],inherited_zone,container_id)
                    continue
                else:
                    block = inv.add(_html_text(node), loc,
                        kind=zone or ('heading' if tag.startswith('h') else 'body'),
                        basis='explicit_html_'+zone if zone else 'html_heading' if tag.startswith('h') else 'document_order',
                        certainty='explicit' if zone or tag.startswith('h') else 'observed',
                        anchor=node.get('id',''), references=_html_refs(node))
            else:
                visit(node.children, loc['path'],inherited_zone,container_id)
                continue
            # Visit each nearest explicit zone/table once, including notes in
            # cells/headers. Their text was excluded from the parent inventory.
            for j, child in enumerate(node.find_all(True)):
                if (child.name == 'table' or _html_zone(child)) and not _inside_html_control(child,node):
                    visit([child],loc['path']+'/control['+str(j)+']',zone or inherited_zone,
                          block['id'] if block else container_id)
    visit(soup.children)
    return 'extracted' if inv.blocks else 'needs_parser', {'native_zones':True, 'pages_observed':False}, []


def _xml_text(el, *, skip_zones=True):
    chunks = []
    def walk(node):
        tag = _name(node).lower()
        if tag == 'tbl' or skip_zones and node is not el and tag in ZONES: return
        if tag == 't': chunks.append(''.join(node.itertext())); return
        if tag in ('br','linebreak'): chunks.append('\n')
        if tag == 'tab': chunks.append('\t')
        for child in node: walk(child)
        if tag == 'p': chunks.append('\n')
    walk(el)
    return ''.join(chunks).strip()


def _hwpx(data, inv):
    base = extract.hwpx_document(data)
    roots = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = sorted((n for n in z.namelist() if re.fullmatch(r'Contents/section\d+\.xml',n)),
                       key=lambda n:int(re.search(r'\d+', n).group()))
        roots = [(n, ElementTree.fromstring(z.read(n))) for n in names]
    table_nodes = [el for _,root in roots for el in root.iter() if _name(el).lower() == 'tbl']
    if len(table_nodes) != len(base['tables']): raise ValueError('Native table inventory mismatch')
    tables = dict(zip(map(id,table_nodes),base['tables']))

    def controls(el, path):
        found = []
        for i,child in enumerate(el):
            child_path = path+'/'+_name(child)+'['+str(i)+']'
            if _name(child).lower() in ZONES or _name(child).lower() == 'tbl': found.append((child,child_path))
            else: found.extend(controls(child,child_path))
        return found

    def visit(el, part, path, zone='', container_id=''):
        tag = _name(el).lower(); loc = {'part':part, 'path':path}
        if tag in ZONES:
            block = inv.add(_xml_text(el), loc, kind=ZONES[tag], basis='native_control',
                    certainty='explicit', anchor=part+'#'+path)
            for child,child_path in controls(el,path):
                visit(child,part,child_path,ZONES[tag],block['id'] if block else '')
            return
        if tag == 'tbl':
            table = copy.deepcopy(tables[id(el)])
            cells = [tc for tr in el if _name(tr).lower() == 'tr' for tc in tr if _name(tc).lower() == 'tc']
            for cell,tc in zip(table['cells'],cells): cell['text'] = _xml_text(tc)
            nested = controls(el,path)
            refs = [{'target':part+'#'+child_path,'kind':'note'} for child,child_path in nested
                    if ZONES.get(_name(child).lower()) == 'footnote']
            inv.table(table,loc,references=refs,zone=zone,container_id=container_id)
            for child,child_path in nested: visit(child,part,child_path,zone,container_id)
            return
        if tag == 'p':
            pending, refs, notes = [], [], []
            segment = 0
            def flush():
                nonlocal segment
                inv.add(''.join(pending), {**loc,'segment':segment}, references=list(refs), zone=zone,
                        kind=zone or 'body', certainty='explicit' if zone else 'observed',
                        basis='native_control' if zone else 'document_order', container_id=container_id)
                pending.clear(); refs.clear(); segment += 1
            def inline(node, current):
                for i, child in enumerate(node):
                    child_path = current+'/'+_name(child)+'['+str(i)+']'
                    name = _name(child).lower()
                    if name in ZONES:
                        if ZONES[name] == 'footnote':
                            refs.append({'target':part+'#'+child_path,'kind':'note'}); notes.append((child,child_path))
                        else:
                            flush(); visit(child,part,child_path)
                    elif name == 'tbl':
                        flush(); visit(child,part,child_path,zone,container_id)
                    elif name == 't': pending.append(''.join(child.itertext()))
                    elif name in ('br','linebreak'): pending.append('\n')
                    elif name == 'tab': pending.append('\t')
                    else: inline(child,child_path)
            inline(el,path); flush()
            for child,child_path in notes: visit(child,part,child_path,zone)
            return
        for i, child in enumerate(el): visit(child,part,path+'/'+_name(child)+'['+str(i)+']',zone,container_id)
    for part, root in roots: visit(root,part,'/'+_name(root))
    return base['status'] if inv.blocks else 'needs_parser', {'native_zones':True, 'pages_observed':False}, []


def _docx(data, inv):
    warnings = []
    ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    table_count = 0

    def owned_children(el, name, depth=0):
        if depth > 32: raise ValueError('DOCX depth limit')
        for child in el:
            if child.tag == ns+name: yield child
            elif _name(child) not in ('tbl','tr','tc') and not _name(child).endswith('Pr'):
                yield from owned_children(child,name,depth+1)

    def table_data(el):
        # Traverse content-control wrappers but stop at owned row/cell/table
        # boundaries. A nested table never supplies rows to its parent table.
        rows = list(owned_children(el,'tr'))
        if len(rows) > 1000: raise ValueError('DOCX row limit')
        cells, previous, cols, uncertain, header_prefix = [], {}, 0, False, True
        for r, tr in enumerate(rows):
            marker = tr.find(ns+'trPr/'+ns+'tblHeader')
            active = marker is not None and _attr(marker,'val').lower() in ('','1','true','on')
            if marker is not None and _attr(marker,'val').lower() not in ('','1','true','on','0','false','off'):
                warnings.append('invalid_table_header_flag')
            if active and not header_prefix: warnings.append('noncontiguous_table_header_ignored')
            header = active and header_prefix
            header_prefix = header_prefix and active
            before = tr.find(ns+'trPr/'+ns+'gridBefore')
            col = int(_attr(before,'val') or 0) if before is not None else 0
            if not 0 <= col <= 200: raise ValueError('DOCX grid limit')
            current = {}
            for tc in owned_children(tr,'tc'):
                if len(cells) >= 10000: raise ValueError('DOCX cell limit')
                span_el = tc.find(ns+'tcPr/'+ns+'gridSpan')
                span = extract._span(_attr(span_el,'val')) if span_el is not None else 1
                if col+span > 200: raise ValueError('DOCX column limit')
                merge = tc.find(ns+'tcPr/'+ns+'vMerge')
                value = (_attr(merge,'val') or 'continue') if merge is not None else ''
                text = _xml_text(tc)
                key = (col,span)
                if value == 'continue' and key in previous:
                    cell = previous[key]; cell['rowspan'] += 1
                    if text: cell['text'] += ('\n' if cell['text'] else '')+text
                    current[key] = cell
                else:
                    cell = dict(row=r,col=col,rowspan=1,colspan=span,text=text,
                                header=header,header_basis='native_row_header' if header else 'not_header')
                    cells.append(cell)
                    if value == 'restart': current[key] = cell
                    elif value: uncertain = True
                if tc.find(ns+'tcPr/'+ns+'hMerge') is not None: uncertain = True
                col += span
            previous = current
            cols = max(cols,col)
        if uncertain: warnings.append('table_geometry_unconfirmed')
        return dict(rows=len(rows),cols=cols,cells=cells,source='docx',geometry_uncertain=uncertain,
                    nested=any(child is not el and _name(child) == 'tbl' for child in el.iter()))

    with zipfile.ZipFile(io.BytesIO(data)) as z:
        info = z.infolist()
        if len(info) > 1000 or len({i.filename for i in info}) != len(info) or sum(i.file_size for i in info) > 50*1024*1024 or \
                any(i.file_size > 20*1024*1024 or i.file_size/max(i.compress_size,1) > 500 for i in info):
            raise ValueError('DOCX archive limit')
        root = ElementTree.fromstring(z.read('word/document.xml'))
        referenced_notes = set()
        def references(el):
            refs = []
            def walk(node):
                if node is not el and _name(node) == 'tbl': return
                if _name(node) in ('footnoteReference','endnoteReference'):
                    group = 'footnotes' if _name(node) == 'footnoteReference' else 'endnotes'
                    key = group+':'+_attr(node,'id'); referenced_notes.add((group,_attr(node,'id')))
                    refs.append({'target':key,'kind':'note'})
                for child in node: walk(child)
            walk(el)
            return refs

        def embedded_tables(el, part, path, zone, container_id, depth):
            for i, child in enumerate(el):
                child_path = path+'/'+_name(child)+'['+str(i)+']'
                if _name(child) == 'tbl': visit(child,part,child_path,zone,container_id,depth+1)
                else:
                    if depth >= 32: raise ValueError('DOCX depth limit')
                    embedded_tables(child,part,child_path,zone,container_id,depth+1)

        def visit(el, part, path, zone='', container_id='', depth=0):
            nonlocal table_count
            if depth > 32: raise ValueError('DOCX depth limit')
            tag = _name(el)
            loc = {'part':part,'path':path}
            if tag == 'tbl':
                table_count += 1
                if table_count > 100: raise ValueError('DOCX table limit')
                table = table_data(el)
                if table['cells']:
                    inv.table(table,loc,references=references(el),zone=zone,container_id=container_id)
                embedded_tables(el,part,path,zone,container_id,depth)
            elif tag == 'p':
                inv.add(_xml_text(el),loc,kind=zone or 'body',references=references(el),container_id=container_id,
                        basis='referenced_native_part' if zone else 'document_order',
                        certainty='explicit' if zone else 'observed')
                embedded_tables(el,part,path,zone,container_id,depth)
            else:
                for i, child in enumerate(el):
                    visit(child,part,path+'/'+_name(child)+'['+str(i)+']',zone,container_id,depth+1)
        visit(root,'word/document.xml','/document')
        rels = {}
        if 'word/_rels/document.xml.rels' in z.namelist():
            rels = {_attr(r,'Id'):r for r in ElementTree.fromstring(z.read('word/_rels/document.xml.rels'))}
        seen = set()
        for el in root.iter():
            kind = {'headerReference':'header','footerReference':'footer'}.get(_name(el))
            if not kind: continue
            rel = rels.get(_attr(el,'id'))
            if rel is None: warnings.append('missing_header_part'); continue
            target = _attr(rel,'Target')
            if _attr(rel,'TargetMode') == 'External' or not re.fullmatch(kind+r'\d+\.xml', target):
                warnings.append('external_or_unsafe_part_ignored'); continue
            part = 'word/'+target
            if part in seen: continue
            seen.add(part)
            if part not in z.namelist(): warnings.append('missing_header_part'); continue
            visit(ElementTree.fromstring(z.read(part)),part,'/',kind)
        for group in ('footnotes','endnotes'):
            part = 'word/'+group+'.xml'
            if part not in z.namelist(): continue
            for el in ElementTree.fromstring(z.read(part)):
                ident = _attr(el,'id')
                if (group,ident) in referenced_notes:
                    path = '/note['+ident+']'
                    block = inv.add(_xml_text(el), {'part':part,'path':path}, kind='footnote',references=references(el),
                            basis='referenced_native_note', certainty='explicit', anchor=group+':'+ident)
                    embedded_tables(el,part,path,'footnote',block['id'],0)
    return 'extracted' if inv.blocks else 'needs_parser', {'native_zones':True, 'pages_observed':False,
                            'header_scope':'referenced_parts_not_rendered_pages'}, warnings


def _pdf(data, inv):
    import pdfplumber
    base = extract.pdf_document(data)
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = [(p.extract_text() or '').splitlines() for p in pdf.pages]
    tops = Counter(lines[0] for lines in pages if lines)
    for number, lines in enumerate(pages,1):
        for i, line in enumerate(lines):
            repeated = i == 0 and tops[line] > 1
            inv.add(line, {'page':number,'line':i}, kind='header' if repeated else 'body',
                    basis='repeated_page_top' if repeated else 'document_order', certainty='candidate' if repeated else 'observed')
        for i, table in enumerate(base['tables']):
            if table['page'] == number: inv.table(table, {'page':number,'table_index':i,'bbox':table['bbox']})
    return base['status'], {'native_zones':False, 'pages_observed':True, 'text_may_overlap_tables':bool(base['tables'])}, ['native_zones_unavailable']


def parse_document(data: bytes, kind: str) -> dict:
    """Bounded pure parsing for the school subprocess; never executes document code."""
    if not isinstance(data, bytes) or len(data) > MAX_INPUT_BYTES: raise ValueError('Structure input limit')
    inv = _Inventory(kind)
    try:
        if kind == 'html': status, coverage, warnings = _html(data, inv)
        elif kind == 'hwpx': status, coverage, warnings = _hwpx(data, inv)
        elif kind == 'docx': status, coverage, warnings = _docx(data, inv)
        elif kind == 'pdf': status, coverage, warnings = _pdf(data, inv)
        elif kind == 'hwp':
            base = extract.hwp_document(data)
            for i, block in enumerate(base.get('blocks', [])):
                if block['type'] == 'table': inv.table(base['tables'][block['table_index']], {'source_block':i,'table_index':block['table_index']})
                else: inv.add(block['text'], {'source_block':i})
            status, coverage, warnings = base['status'], {'native_zones':False, 'pages_observed':False}, ['native_zones_unavailable']
        else:
            status, coverage, warnings = ('needs_vision' if kind == 'image' else 'needs_parser'), {'native_zones':False}, ['unsupported_structure']
    except Exception as exc:
        raise ValueError('Structure extraction failed') from exc
    result = {'version':VERSION, 'status':status, 'blocks':inv.blocks, 'coverage':coverage, 'warnings':warnings}
    if len(encode(result).encode()) > MAX_RESULT_BYTES: raise ValueError('Structure output limit')
    return result


def _filename_key(value):
    value = unicodedata.normalize('NFKC', value).strip()
    value = re.sub(r'\s*\d+\s*부[.。]?\s*$', '', value)
    value = re.sub(r'^(?:붙임|첨부)\s*\d+\s*[.)：:_-]?\s*', '', value)
    value = re.sub(r'\.(?:hwp|hwpx|docx|pdf|html?|txt|xlsx?|png|jpg|jpeg)$', '', value, flags=re.I)
    return re.sub(r'[^\w가-힣]', '', value).casefold()


def analyze_bundle(files: list, documents: dict) -> dict:
    """Connect only evidence in this one notice version, never another school."""
    blocks, file_rows, relations, warnings = [], [], [], []
    relation_keys = set()
    used_bytes = 256  # Envelope, commas and conservative per-item accounting.
    def charge(item):
        nonlocal used_bytes
        used_bytes += len(encode(item).encode())+1
        if used_bytes > MAX_RESULT_BYTES: raise ValueError('Notice structure output limit')
    def warn(code, file_id, **extra):
        if len(warnings) >= MAX_WARNINGS: raise ValueError('Notice structure warning limit')
        item = dict(code=code, file_id=file_id, **extra)
        charge(item); warnings.append(item)
    def relate(kind, source, target, basis, certainty):
        key = (kind,source,target,basis,certainty)
        if key in relation_keys: return
        if len(relations) >= MAX_RELATIONS: raise ValueError('Notice structure relation limit')
        item = dict(zip(('kind','source','target','basis','certainty'),key))
        charge(item)
        relation_keys.add(key)
        relations.append(item)
    for f in files:
        fid = f['id']; doc = documents.get(fid, {'status':'not_analyzed','blocks':[],'coverage':{}})
        file_row = {k:f[k] for k in ('id','filename','sha256','role')} | {
            'status':doc['status'], 'coverage':doc.get('coverage',{}), 'block_count':len(doc.get('blocks',[]))}
        charge(file_row); file_rows.append(file_row)
        relate('attachment_of' if f['role'] == 'attachment' else 'guide_of', 'file:'+fid, 'notice', 'received_role', 'explicit')
        if doc['status'] != 'extracted': warn('unreadable_file', fid)
        for code in doc.get('warnings', []): warn(code, fid)
        local = []
        for block in doc.get('blocks', []):
            if len(blocks) >= MAX_BLOCKS: raise ValueError('Notice structure block limit')
            b = copy.deepcopy(block); b.update(id=fid+'/'+block['id'], file_id=fid, sha256=f['sha256'])
            if b.get('container_id'): b['container_id'] = fid+'/'+b['container_id']
            charge(b)
            local.append(b); blocks.append(b)
        local_ids = {b['id'] for b in local}
        anchors = {}
        for b in local:
            if b.get('anchor'): anchors.setdefault(b['anchor'], []).append(b)
        linked_notes = set()
        for b in local:
            if b['kind'] == 'header' or b.get('zone') == 'header':
                relate('header_for', b['id'], 'file:'+fid,
                       'explicit_zone' if b.get('zone') == 'header' else b['basis'],
                       'explicit' if b.get('zone') == 'header' else b['certainty'])
            if b.get('container_id') in local_ids:
                relate('part_of', b['id'], b['container_id'], 'native_containment', 'explicit')
            for cell in b.get('table',{}).get('cells', []):
                if cell.get('header'):
                    relate('table_header_for', b['id']+f"/r{cell['row']}c{cell['col']}", b['id'], cell['header_basis'],
                           'explicit' if cell['header_basis'] in ('explicit_markup','native_row_header') else 'candidate')
            for ref in b.get('references', []):
                if ref['kind'] not in ('note','internal'): continue
                targets = [n for n in anchors.get(ref['target'], []) if n['kind'] == 'footnote']
                if len(targets) == 1:
                    relate('footnote_for', b['id'], targets[0]['id'], 'explicit_reference', 'explicit')
                    linked_notes.add(targets[0]['id'])
                elif ref['kind'] == 'note': warn('unresolved_footnote', fid, block_id=b['id'], reference=ref['target'])
            labels = []
            for line in b['text'].splitlines():
                match = re.match(r'^\s*(?:붙임|첨부)\s*(?:\d+\s*[.)：:_-]?\s*)?(.+)$', line)
                if match: labels.append((match[1], 'attachment_label'))
            labels += [(Path(unquote(urlsplit(r['target']).path)).name, 'relative_file_link')
                       for r in b.get('references', []) if r['kind'] == 'file']
            for label, basis in labels:
                key = _filename_key(label)
                targets = [other for other in files if other['id'] != fid and key and _filename_key(other['filename']) == key]
                if len(targets) == 1: relate('attachment_reference', b['id'], 'file:'+targets[0]['id'], basis, 'candidate')
                else: warn('ambiguous_attachment' if targets else 'unresolved_attachment', fid, block_id=b['id'], reference=label)
        for b in local:
            if b['kind'] == 'footnote' and b['id'] not in linked_notes: warn('unlinked_footnote', fid, block_id=b['id'])
    result = dict(version=VERSION, method='local_rules_and_native_inventory', human_approved=False,
                  complete=bool(file_rows) and not warnings, files=file_rows, blocks=blocks, relations=relations, warnings=warnings)
    if len(blocks) > MAX_BLOCKS or len(encode(result).encode()) > MAX_RESULT_BYTES: raise ValueError('Notice structure output limit')
    return result


def _valid_result(result):
    """Reject stale/partial/corrupt cache shapes before exposing a revision."""
    if not isinstance(result,dict) or result.get('version') != VERSION: return False
    if result.get('status') not in ('extracted','needs_parser','needs_vision','parse_error'): return False
    if not isinstance(result.get('coverage'),dict) or not isinstance(result.get('warnings'),list): return False
    if not all(isinstance(w,str) for w in result['warnings']): return False
    blocks = result.get('blocks')
    if not isinstance(blocks,list) or len(blocks) > MAX_BLOCKS: return False
    ids = set()
    for b in blocks:
        if not isinstance(b,dict) or not isinstance(b.get('id'),str) or not re.fullmatch(r'b\d+',b['id']) or b['id'] in ids: return False
        ids.add(b['id'])
        if b.get('kind') not in ('header','heading','body','footer','footnote','table'): return False
        if not isinstance(b.get('text'),str) or not isinstance(b.get('locator'),dict) or not isinstance(b.get('basis'),str): return False
        if b.get('certainty') not in ('observed','explicit','candidate'): return False
        if not isinstance(b.get('anchor',''),str) or b.get('zone','') not in ('','header','footer','footnote'): return False
        container = b.get('container_id','')
        if not isinstance(container,str) or container and not re.fullmatch(r'b\d+',container): return False
        refs = b.get('references',[])
        if not isinstance(refs,list) or not all(isinstance(r,dict) and isinstance(r.get('target'),str)
                and r.get('kind') in ('note','internal','file') for r in refs): return False
        if b['kind'] == 'table':
            table = b.get('table')
            if not isinstance(table,dict) or not isinstance(table.get('cells'),list): return False
            for cell in table['cells']:
                if not isinstance(cell,dict) or not isinstance(cell.get('text'),str): return False
                if not all(isinstance(cell.get(k),int) and cell[k] >= 0 for k in ('row','col')): return False
                if not isinstance(cell.get('header'),bool) or not isinstance(cell.get('header_basis'),str): return False
    if any(b.get('container_id') and (b['container_id'] not in ids or b['container_id'] == b['id']) for b in blocks): return False
    return True


def extract_asset(store, asset):
    """Independent versioned cache and resource-bounded parser, no DB writes."""
    from .learn import _limits
    sha = asset['sha256']
    original = store.object_path(sha)  # The existing store validates the SHA path.
    kind = asset.get('kind','unsupported')
    if kind not in ('html','hwpx','hwp','docx','pdf','image','unsupported'): kind = 'unsupported'
    path = store.root/'extracted'/f'{sha}-{VERSION}-{kind}.json'
    try:
        if original.is_symlink() or original.stat().st_size > MAX_INPUT_BYTES: raise ValueError('Structure input limit')
        with original.open('rb') as source: raw = source.read(MAX_INPUT_BYTES+1)
        if len(raw) > MAX_INPUT_BYTES or digest(raw) != sha: raise ValueError('Structure source integrity failure')
        if path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_RESULT_BYTES:
            try:
                cached = json.loads(path.read_text())
                if _valid_result(cached) and cached.get('source_sha256') == sha and cached.get('kind') == kind and cached['status'] != 'parse_error':
                    return cached
            except (ValueError, OSError, RecursionError): pass
        if kind in ('image','unsupported'):
            result = parse_document(b'',kind)
        else:
            parser = subprocess.run([sys.executable,'-m','moa.document_structure',str(original),kind],
                capture_output=True, timeout=55, preexec_fn=_limits,
                env={k:v for k,v in os.environ.items() if k in ('PATH','PYTHONPATH','LANG','LC_ALL','HOME','TZ',
                    'TMPDIR','TMP','TEMP','XDG_CACHE_HOME','PYTHONPYCACHEPREFIX')})
            if parser.returncode or len(parser.stdout) > MAX_RESULT_BYTES: raise ValueError('Structure worker failed')
            result = json.loads(parser.stdout)
            if not _valid_result(result) or result.get('source_sha256') != sha: raise ValueError('Structure worker integrity failure')
    except (ValueError,OSError,RecursionError,subprocess.SubprocessError):
        result = {'version':VERSION,'status':'parse_error','blocks':[],'coverage':{},'warnings':['structure_parser_failed']}
    result.update(source_sha256=sha,kind=kind)
    write_json(path,result)
    return result


def build_analysis(store, files):
    documents = {f['id']:extract_asset(store,f) for f in files}
    try:
        return analyze_bundle(files,documents)
    except ValueError:
        # Preserve originals and their inventory caches. Do not silently retain a
        # partial graph, auto-approve it, or endlessly retry a size-limited result.
        return {'version':VERSION,'method':'local_rules_and_native_inventory','human_approved':False,
                'complete':False,'files':[{k:f[k] for k in ('id','filename','sha256','role')} |
                    {'status':documents[f['id']]['status'],'coverage':documents[f['id']].get('coverage',{}),
                     'block_count':len(documents[f['id']].get('blocks',[]))} for f in files],
                'blocks':[],'relations':[],'warnings':[{'code':'notice_structure_limit'}]}


if __name__ == '__main__':
    original, kind = sys.argv[1:3]
    with Path(original).open('rb') as source: data = source.read(MAX_INPUT_BYTES+1)
    result = parse_document(data,kind)
    result['source_sha256'] = digest(data)
    print(encode(result))
