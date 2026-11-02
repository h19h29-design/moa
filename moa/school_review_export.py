"""Read-only, private HTML exports of immutable staff review events.

No parsing, model calls, object writes, jobs, approval or migration. Only the
saved structure overlay is rendered. Original conversion HTML stays separate.
"""
from __future__ import annotations

from collections import defaultdict
import io
import os
from pathlib import Path
import re
import stat
import zipfile

from . import mobile, school_review as review, school_service as svc
from .core import digest, encode
from .web import esc

RENDERER = 'moa-school-reviewed-html-v1'
MAX_HTML_BYTES = 32 * 1024 * 1024
MAX_TABLE_SLOTS = 20_000
STATUS = {'draft':'수정 중 · 확인 필요', 'confirmed':'담당자 확인 완료'}
RELATIONS = {**review.RELATION_LABELS, 'guide_of':'안내 원본 → 통신문', 'attachment_of':'첨부 → 통신문'}
CSP = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; object-src 'none'"
STYLE = '''
*{box-sizing:border-box}body{margin:0;background:#f4f6f8;color:#18242d;font:16px/1.7 system-ui,sans-serif}
main{max-width:960px;margin:auto;padding:24px}section.file,.summary{background:white;border:1px solid #dce2e5;border-radius:12px;padding:24px;margin:0 0 20px}
h1{font-size:26px}h2{font-size:21px}h3{font-size:18px}a{color:#165e87}p,li,dd,h1,h2,h3,summary{overflow-wrap:anywhere}
.block-text{white-space:pre-wrap;margin:8px 0}.source-block{border-top:1px solid #e5eaed;padding:16px 0;scroll-margin-top:16px}
.meta{font-size:13px;color:#52616c}.warning{background:#fff4df;border-left:4px solid #a96314;padding:12px}
.relations{font-size:14px;padding-left:22px}.relations li{margin:6px 0}.table-scroll{overflow:auto;max-width:100%}
table{border-collapse:collapse;width:100%}td,th{border:1px solid #c8d1d8;padding:8px;white-space:pre-wrap;overflow-wrap:anywhere}th{background:#edf3f6}
header.block-text,footer.block-text{padding:12px;background:#edf3f6}aside.block-text{padding:12px;border-left:3px solid #8199aa}
details{margin:10px 0}.cell-ref{display:inline-block;margin-right:8px}code{overflow-wrap:anywhere}
@media(max-width:600px){main{padding:12px}section.file,.summary{padding:16px}h1{font-size:22px}}
@media print{body{background:white}main{max-width:none;padding:0}.table-scroll{overflow:visible}}
'''


def snapshot(store, principal, ident, *, revision_id, event_id):
    """Pin all output to an existing event; never fall back to latest."""
    if not all(isinstance(v,str) and 0 < len(v) <= 160 for v in (ident,revision_id,event_id)):
        raise ValueError('Pinned receiving, conversion and review versions required')
    version = svc.get_version(store,principal,ident)
    v = review.view(store,principal,ident,revision_id=revision_id,event_id=event_id)
    event = v['latest']
    return {'renderer':RENDERER, 'notice_version':ident, 'receiving_sequence':version['sequence'],
        'title':version['title'], 'conversion_revision':revision_id,
        'review':{k:event[k] for k in ('id','sequence','status','actor','created_at','base_hash')},
        'analysis':v['analysis'], 'private':True, 'publication_approved':False}


def _anchor(node):
    # Opaque ASCII anchors, not source URLs or user-supplied DOM identifiers.
    return 'n-'+digest(node.encode())[:24]


class _HTML:
    """Bound retained UTF-8 output and sparse-grid expansion for the whole document."""
    def __init__(self, chunks):
        self.chunks, self.size, self.table_slots = [], 0, MAX_TABLE_SLOTS
        for chunk in chunks: self.append(chunk)

    def append(self, text):
        raw = text.encode()
        if self.size + len(raw) > MAX_HTML_BYTES:
            raise ValueError('Reviewed HTML output limit')
        self.chunks.append(raw)
        self.size += len(raw)

    def finish(self):
        return b''.join(self.chunks)


def _table(block, edges, budget):
    table = block.get('table',{})
    cells = table.get('cells',[])
    rows, cols = table.get('rows',0), table.get('cols',0)
    fallback = ('text' in block.get('reviewed_fields',[]) or table.get('geometry_uncertain') or
                type(rows) is not int or type(cols) is not int or not 0 < rows <= 500 or not 0 < cols <= 200 or
                rows*cols > MAX_TABLE_SLOTS or not cells or len(cells) > MAX_TABLE_SLOTS)
    limited = not fallback and rows*cols > budget.table_slots
    fallback = fallback or limited
    occupied, origins = set(), {}
    if not fallback:
        for c in cells:
            r, col, rs, cs = (c.get(k,1 if k.endswith('span') else -1) for k in ('row','col','rowspan','colspan'))
            if (not all(type(x) is int for x in (r,col,rs,cs)) or r<0 or col<0 or rs<1 or cs<1 or r+rs>rows or col+cs>cols):
                fallback = True; break
            area = {(y,x) for y in range(r,r+rs) for x in range(col,col+cs)}
            if occupied & area:
                fallback = True; break
            occupied.update(area); origins[r,col] = c
    if fallback:
        reason = '문서 전체의 표 표시량 제한' if limited else '표 텍스트 수정 또는 셀 배치 미확정'
        return ('<p class="warning">'+reason+': 수정본 텍스트로 표시합니다. '
                '원본 셀 배치는 변경하지 않았으며, 원본 표와 대조하세요.</p>'
                '<p class="block-text">'+esc(block['text'])+'</p>')
    budget.table_slots -= rows*cols
    headers = {e['source'] for e in edges if e['kind']=='table_header_for' and e['target']==block['id']}
    parts = ['<div class="table-scroll" tabindex="0" role="region" aria-label="검수본 표"><table>']
    for r in range(rows):
        parts.append('<tr>')
        for col in range(cols):
            c = origins.get((r,col))
            if c is None:
                if (r,col) not in occupied: parts.append('<td></td>')
                continue
            cell_id = block['id']+f'/r{r}c{col}'
            tag = 'th' if cell_id in headers else 'td'
            parts.append(f'<{tag} rowspan="{c.get("rowspan",1)}" colspan="{c.get("colspan",1)}">'+esc(c['text'])+f'</{tag}>')
        parts.append('</tr>')
    return ''.join(parts)+'</table></div>'


def render_html(saved, *, original_paths=None):
    """Escaped semantic blocks and explicit graph links; no document markup."""
    analysis, event = saved['analysis'], saved['review']
    blocks_by_file, outgoing = defaultdict(list), defaultdict(list)
    names = {'notice':'통신문 묶음'}
    for f in analysis['files']: names['file:'+f['id']] = f['filename']
    for b in analysis['blocks']:
        names[b['id']] = review.BLOCK_LABELS[b['kind']]+' '+b['id']
        blocks_by_file[b['file_id']].append(b)
        for c in b.get('table',{}).get('cells',[]):
            cell_id = b['id']+f'/r{c["row"]}c{c["col"]}'
            names[cell_id] = '원본 셀 '+cell_id
    for edge in analysis['relations']: outgoing[edge['source']].append(edge)

    def relationships(source):
        parts = []
        for edge in outgoing[source]:
            label = esc(RELATIONS.get(edge['kind'],edge['kind']))
            target = edge['target']
            link = ('<a href="#'+_anchor(target)+'">'+esc(names[target])+'</a>' if target in names else
                    '<span>대상 미확정: '+esc(target)+'</span>')
            parts.append('<li data-relation="'+esc(edge['kind'])+'">'+label+' → '+link+
                         ' <span class="meta">('+esc(edge['basis'])+' · '+esc(edge['certainty'])+')</span></li>')
        return '<ul class="relations">'+''.join(parts)+'</ul>' if parts else ''

    parts = _HTML(['<!doctype html><html lang="ko"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<meta http-equiv="Content-Security-Policy" content="'+esc(CSP)+'">',
        '<meta name="referrer" content="no-referrer"><title>검수 수정본 · '+esc(saved['title'])+'</title>',
        '<style>'+STYLE+'</style></head><body><main id="'+_anchor('notice')+'">',
        '<section class="summary"><h1>검수 수정본 HTML</h1><p>수신 제목: '+esc(saved['title'])+'</p>',
        '<p><strong>'+esc(STATUS[event['status']])+'</strong> · 비공개 · 검수 #'+str(event['sequence'])+'</p>',
        '<p>선택한 검수 시점의 스냅샷입니다. 최신 확인 상태·공개 승인·누락 없는 원문 재현을 뜻하지 않습니다. '
        '원본과 자동 변환 HTML은 별도로 보존합니다.</p>',
        '<p class="meta">검수자 '+esc(event['actor'])+' · '+esc(event['created_at'])+'</p>',
        '<details><summary>출력 버전·근거</summary><p class="meta">수신 '+esc(saved['notice_version'])+
        ' · 변환 '+esc(saved['conversion_revision'])+' · 검수 '+esc(event['id'])+' · 렌더러 '+RENDERER+
        ' · 스냅샷 SHA256 '+digest(encode(saved).encode())+'</p></details>',
        '<p class="warning">분석 범위: '+('분석기가 보고한 완전성 충족' if analysis['complete'] else '일부 미확정·누락 있음')+
        '. 담당자 확인 후에도 원래 분석 경고를 유지합니다.</p>'])
    for warning in analysis['warnings']:
        parts.append('<p class="warning">'+esc(encode(warning))+'</p>')
    parts.append('<nav aria-label="문서·첨부 목록"><ul>')
    for f in analysis['files']:
        parts.append('<li><a href="#'+_anchor('file:'+f['id'])+'">'+esc(f['filename'])+'</a></li>')
    parts.append('</ul></nav></section>')
    for f in analysis['files']:
        parts.append('<section class="file" data-file-id="'+esc(f['id'])+'" id="'+_anchor('file:'+f['id'])+'">'
            '<h2>'+esc(f['filename'])+'</h2><p class="meta">'+('안내 원본' if f['role']=='convert' else '원본 첨부')+
            ' · '+esc(f['status'])+'</p>'+relationships('file:'+f['id']))
        if not blocks_by_file[f['id']]: parts.append('<p class="warning">분석된 구역이 없습니다. 원본을 확인하세요.</p>')
        for b in blocks_by_file[f['id']]:
            parts.append('<section class="source-block" data-block-id="'+esc(b['id'])+'" data-kind="'+esc(b['kind'])+
                '" id="'+_anchor(b['id'])+'"><p class="meta">'+esc(review.BLOCK_LABELS[b['kind']])+
                (' · 담당자 수정' if b.get('reviewed_fields') else ' · 자동 분석 유지')+'</p>')
            if b['kind']=='table': parts.append(_table(b,analysis['relations'],parts))
            else:
                tag = {'heading':'h3','header':'header','footer':'footer','footnote':'aside'}.get(b['kind'],'p')
                attrs = ' role="doc-footnote"' if b['kind']=='footnote' else ''
                parts.append('<'+tag+' class="block-text"'+attrs+'>'+esc(b['text'])+'</'+tag+'>')
            parts.append(relationships(b['id']))
            parts.append('<details><summary>원본 위치·관계 근거</summary><p class="meta">'+esc(encode(b['locator']))+
                ' · '+esc(b['basis'])+' · '+esc(b['certainty'])+'</p>')
            # Cell anchors also survive text-only fallback; no stale cell text is printed.
            for c in b.get('table',{}).get('cells',[]):
                cell_id = b['id']+f'/r{c["row"]}c{c["col"]}'
                parts.append('<span class="cell-ref meta" id="'+_anchor(cell_id)+'">'+esc(names[cell_id])+'</span>'+relationships(cell_id))
            parts.append('</details></section>')
        if original_paths:
            parts.append('<p><a data-original-file="'+esc(f['id'])+'" href="'+esc(original_paths[f['id']])+'" download>원본 파일: '+esc(f['filename'])+'</a></p>')
        parts.append('</section>')
    parts.append('<p class="warning">원본 파일은 수정하지 않았습니다. 원본에는 실행 가능한 콘텐츠가 있을 수 있으므로 신뢰할 수 있는 앱에서 확인하세요. '
                 '단독 HTML에는 원본 파일이 포함되지 않습니다. 원본이 필요하면 검수 화면에서 ZIP을 내려받으세요.</p></main></body></html>')
    return parts.finish()


def bundle(store, saved):
    """All-or-nothing original integrity checks; stable ZIP names and metadata."""
    originals, total, paths, contents = [], 0, {}, []
    files = saved['analysis']['files']
    if not 1 <= len(files) <= mobile.MAX_FILES: raise review.Conflict('Original file inventory changed')
    for i, f in enumerate(files):
        try:
            path = store.object_path(f['sha256'])
            if path.resolve() != path: raise OSError('Original path is a symlink')
            fd = os.open(path,os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd,'rb') as src:
                st = os.fstat(src.fileno())
                if not stat.S_ISREG(st.st_mode) or not 0 < st.st_size <= mobile.MAX_FILE_BYTES:
                    raise OSError('Original size/type changed')
                raw = src.read(mobile.MAX_FILE_BYTES+1)
            total += len(raw)
            if len(raw)>mobile.MAX_FILE_BYTES or total>mobile.MAX_UPLOAD_BYTES or digest(raw)!=f['sha256']:
                raise OSError('Original integrity changed')
        except (OSError,ValueError):
            raise review.Conflict('Original integrity check failed') from None
        # User filenames stay metadata only. No traversal, collisions, URL fragments,
        # platform-reserved basenames or control characters in archive paths.
        suffix = Path(f['filename']).suffix.lower()
        if not re.fullmatch(r'\.[a-z0-9]{1,8}',suffix): suffix = '.bin'
        archive_path = f'originals/{i+1:02d}-{f["sha256"]}{suffix}'
        paths[f['id']] = archive_path
        originals.append({'id':f['id'],'filename':f['filename'],'role':f['role'],'sha256':f['sha256'],
                          'bytes':len(raw),'path':archive_path})
        contents.append((archive_path,raw))
    html = render_html(saved,original_paths=paths)
    manifest = {k:v for k,v in saved.items() if k!='analysis'}
    manifest.update(snapshot_sha256=digest(encode(saved).encode()), html_sha256=digest(html), originals=originals,
                    analysis_complete=saved['analysis']['complete'], warnings=saved['analysis']['warnings'])
    out = io.BytesIO()
    with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for name, data in [('index.html',html),('manifest.json',encode(manifest).encode()),*contents]:
            entry = zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0))
            entry.create_system = 3; entry.external_attr = 0o100600 << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(entry,data)
    return out.getvalue()
