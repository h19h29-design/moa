"""Read-only guidance over immutable analysis evidence; never rewrites exports."""
import hashlib
import json
from urllib.parse import urlencode

from .web import esc


# These explain the analyzer's observations, not a new validation or approval.
GUIDANCE = {
    'unreadable_file': (
        '파일 내용을 충분히 읽지 못했습니다',
        '이 파일의 내용이 구조 분석에 모두 반영됐는지 확인할 수 없습니다.',
        '원본 파일이 열리는지 확인하고, 빠진 본문·표·첨부 내용을 직접 대조해 주세요.'),
    'native_zones_unavailable': (
        '머리말·각주 구분을 확인해 주세요',
        '추출한 글만으로는 머리말·본문·각주를 원본과 같은 구역으로 구분했는지 확인할 수 없습니다.',
        '원본의 머리말·바닥글·각주가 빠지거나 본문에 섞이지 않았는지 확인해 주세요.'),
    'unsupported_structure': (
        '이 파일 형식의 구조를 자동으로 확인하지 못했습니다',
        '현재 분석기가 이 형식의 구역과 관계를 읽지 못했습니다.',
        '원본 파일을 열어 내용과 다른 첨부와의 관계를 직접 확인해 주세요.'),
    'structure_parser_failed': (
        '파일의 문서 구조를 분석하지 못했습니다',
        '구조 분석 과정이 완료되지 않아 파일의 구역과 관계를 확인할 수 없습니다.',
        '원본 파일이 정상적으로 열리는지 확인하고 담당 관리자에게 분석 실패를 알려 주세요.'),
    'notice_structure_limit': (
        '문서 묶음의 구조 분석 한도를 넘었습니다',
        '처리할 수 있는 크기나 구역·관계 수를 넘어 이 묶음의 구조를 충분히 분석하지 못했습니다.',
        '원본의 파일 목록과 내용을 직접 확인하고 담당 관리자에게 분석 범위를 문의해 주세요.'),
    'invalid_table_header_flag': (
        '표의 제목 행 표시를 읽지 못했습니다',
        '문서 안에 저장된 표 제목 행 표시가 올바른 형식이 아니어서 적용하지 못했습니다.',
        '원본 표에서 각 열의 제목이 어느 행인지 확인해 주세요.'),
    'noncontiguous_table_header_ignored': (
        '표 중간의 제목 행 표시를 확인해 주세요',
        '첫 행부터 이어지지 않는 제목 행 표시가 있어 자동 제목 행으로 적용하지 않았습니다.',
        '원본 표의 제목 행과 본문 행을 대조해 구분이 맞는지 확인해 주세요.'),
    'table_geometry_unconfirmed': (
        '표의 행·열과 합쳐진 셀을 확인해 주세요',
        '표의 셀 배치나 병합 범위를 확실하게 해석하지 못했습니다.',
        '원본과 미리보기에서 셀의 행·열 위치, 합쳐진 셀, 빠진 값이 없는지 대조해 주세요.'),
    'missing_header_part': (
        '참조된 머리말·바닥글을 찾지 못했습니다',
        '문서가 가리키는 머리말 또는 바닥글의 내부 자료를 찾지 못했습니다.',
        '원본에서 머리말·바닥글을 확인하고 학교명·발신 정보 등이 빠지지 않았는지 대조해 주세요.'),
    'external_or_unsafe_part_ignored': (
        '불러오지 않은 머리말·바닥글 참조가 있습니다',
        '문서 밖의 주소 또는 허용되지 않은 내부 위치를 가리키는 참조는 읽지 않았습니다.',
        '원본에 보이는 머리말·바닥글을 직접 확인해 주세요. 경고에 적힌 외부 주소를 열 필요는 없습니다.'),
    'unresolved_footnote': (
        '각주 표시와 설명을 연결하지 못했습니다',
        '각주 표시가 가리키는 설명을 하나로 특정하지 못했습니다.',
        '원본에서 각주 표시와 해당 설명을 찾고, 검수 화면에서 두 구역의 연결을 확인해 주세요.'),
    'unlinked_footnote': (
        '각주가 어느 본문에 해당하는지 확인해 주세요',
        '각주 내용은 찾았지만 이를 가리키는 본문과 연결하지 못했습니다.',
        '원본에서 이 각주를 사용하는 본문을 찾아 검수 화면의 구역 간 관계를 확인해 주세요.'),
    'unresolved_attachment': (
        '본문에서 언급한 첨부를 찾지 못했습니다',
        '본문의 첨부 이름과 일치하는 파일을 이 수신 묶음에서 찾지 못했습니다.',
        '아래 언급된 이름과 실제 첨부 목록을 대조해 누락·파일명 차이 여부를 확인해 주세요.'),
    'ambiguous_attachment': (
        '연결할 첨부 파일을 하나로 정하지 못했습니다',
        '본문에 적힌 첨부 이름과 일치하는 후보가 여러 개입니다.',
        '파일 번호와 내용을 확인해 어떤 첨부를 가리키는지 검수 화면에서 구역 간 관계를 확인해 주세요.'),
}
FALLBACK = ('추가 확인이 필요한 분석 경고입니다',
    '이 경고에 대한 쉬운 설명이 아직 등록되지 않았습니다.',
    '원본과 분석 내용을 대조하고, 아래 분석 근거의 경고 코드를 담당 관리자에게 알려 주세요.')

STYLE = '''<style>
.school-warnings{overflow-wrap:anywhere}.school-warnings .warning-card{border:1px solid #c8d2dd;border-left:4px solid #a96d10;border-radius:8px;padding:16px;margin:12px 0;background:#fff}
.school-warnings h3{font-size:17px;margin:0 0 10px}.school-warnings p{line-height:1.65;margin:8px 0}
.school-warnings .warning-location{background:#f3f6f9;padding:8px 10px;border-radius:4px}
.school-warnings pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}
.school-warnings summary{cursor:pointer}.school-warnings details{margin-top:12px}
[id^="analysis-file-"],[id^="analysis-block-"]{scroll-margin-top:24px}
[id^="analysis-file-"]:target,[id^="analysis-block-"]:target{outline:3px solid #547aaf;outline-offset:4px}
</style>'''


def anchor(kind, ident):
    """Opaque fragment IDs keep document-supplied IDs out of HTML attributes."""
    if kind not in ('file', 'block'): raise ValueError('Invalid analysis anchor')
    return 'analysis-'+kind+'-'+hashlib.sha256(ident.encode()).hexdigest()


def _key(value):
    return value if isinstance(value, str) else ''


def describe(analysis, ident, *, warnings=None):
    """Resolve locations only within this authorized, immutable conversion."""
    files = {f['id']:(i, f) for i, f in enumerate(analysis['files'], 1)}
    blocks, ordinals = {}, {}
    for b in analysis['blocks']:
        fid = b['file_id']
        ordinals[fid] = ordinals.get(fid, 0)+1
        blocks[b['id']] = (ordinals[fid], b)
    url = '/structure?'+urlencode({'id':ident, 'revision':analysis['conversion_revision']})
    items = []
    for warning in analysis['warnings'] if warnings is None else warnings:
        code = _key(warning.get('code'))
        title, explanation, action = GUIDANCE.get(code, FALLBACK)
        found = blocks.get(_key(warning.get('block_id')))
        fid = warning.get('file_id')
        if fid is None and found: fid = found[1]['file_id']
        file = files.get(_key(fid))
        # Never let a stale or mismatched block redirect to a different file.
        block = found if file and found and found[1]['file_id'] == fid else None
        location, href, link_text, locator = '문서 묶음 · 파일 위치 미확인', '', '', {}
        if file:
            location = '파일 '+str(file[0])+' · '+file[1]['filename']
            href = url+'#'+anchor('file', fid)
            link_text = '자동 분석에서 해당 파일 보기'
            if block:
                locator = block[1].get('locator', {})
                page, line = locator.get('page'), locator.get('line')
                if locator.get('format') == 'pdf' and type(page) is int and page > 0:
                    location += ' · PDF '+str(page)+'쪽'
                    if type(line) is int and line >= 0:
                        location += ' · 추출 텍스트 '+str(line+1)+'번째 줄'
                location += ' · 자동 분석 구역 '+str(block[0])
                href = url+'#'+anchor('block', block[1]['id'])
                link_text = '자동 분석에서 해당 구역 보기'
            else:
                location += ' · 파일 전체 · 세부 위치 미확인'
        # Keep raw analysis unchanged; display only known, non-executable evidence.
        evidence = {k:warning[k] for k in ('code','file_id','block_id','reference') if k in warning}
        if locator: evidence['locator'] = locator
        items.append(dict(title=title, explanation=explanation, action=action,
            location=location, href=href, link_text=link_text,
            # Note references are often internal IDs, not visible footnote text.
            reference=_key(warning.get('reference')) if code in ('unresolved_attachment','ambiguous_attachment') else '',
            evidence=evidence))
    return items


def render(analysis, ident, *, warnings=None):
    items = describe(analysis, ident, warnings=warnings)
    parts = [STYLE, '<section class="school-warnings" aria-label="분석 경고 안내">',
        '<p>자동 분석 당시의 확인 항목입니다. 수정·확인 후에도 원래 경고는 보존됩니다. '
        '아래 위치 링크는 같은 변환 버전의 자동 분석 근거를 엽니다.</p>',
        '<p>자동 분석 구역 번호는 이 화면의 추출 순서이며 원본의 쪽·문단 번호가 아닙니다. '
        'PDF 쪽수는 확인된 경우에만 표시하고, 추출 텍스트 줄은 원본의 인쇄 줄과 다를 수 있습니다.</p>']
    if not items: parts.append('<p>기록된 자동 분석 경고가 없습니다. 원본과의 대조는 필요합니다.</p>')
    for item in items:
        parts.append('<article class="warning-card"><h3>'+esc(item['title'])+'</h3>'
            '<p>'+esc(item['explanation'])+'</p><p class="warning-location"><strong>확인 위치</strong>: '+esc(item['location'])+'</p>')
        if item['reference']: parts.append('<p>문서에서 언급된 이름·표시: '+esc(item['reference'])+'</p>')
        parts.append('<p><strong>확인할 내용</strong>: '+esc(item['action'])+'</p>')
        if item['href']: parts.append('<p><a data-warning-location href="'+esc(item['href'])+'">'+item['link_text']+'</a></p>')
        parts.append('<details><summary>분석 근거 · 원래 경고 코드 보기</summary><pre>'+
            esc(json.dumps(item['evidence'], ensure_ascii=False, indent=2))+'</pre></details></article>')
    return ''.join(parts)+'</section>'
