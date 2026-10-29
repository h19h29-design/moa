"""Authenticated GET-only warning directory; no scripts, writes or new exports."""
from urllib.parse import urlencode

from . import school_warning_search as search, school_warning_web as guidance
from .school_saved_queue_web import query, number
from .school_compare_web import STYLE
from .web import esc

PATH = '/structure-warnings'
FILTER_STYLE = '''<style>
#warning-search .warning-filters{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr) minmax(0,1fr);gap:12px}
#warning-search .warning-filters label{min-width:0}#warning-search input,#warning-search select{width:100%;min-width:0;max-width:100%}
#warning-search .filter-actions{grid-column:1/-1}#warning-search .warning-pages{margin:18px 0}
@media(max-width:800px){#warning-search .warning-filters{grid-template-columns:minmax(0,1fr)}}
</style>'''


def link(ident, revision, *, q='', file='', code='', page=1):
    fields = {'id':ident, 'revision':revision}
    fields.update({k:v for k,v in (('q',q),('file',file),('code',code),('page',page if page!=1 else '')) if v})
    return PATH+'?'+urlencode(fields)


def render(handler, raw):
    values = query(raw, {'id','revision','q','file','code','page'})
    data = search.listing(handler.store, handler._principal(), values.get('id',[''])[0],
        values.get('revision',[''])[0], q=values.get('q',[''])[0], file=values.get('file',[''])[0],
        code=values.get('code',[''])[0], page=number(values.get('page',['1'])[0], 1_000_000))
    f, ident, revision = data['filters'], data['id'], data['revision']
    content = [STYLE, FILTER_STYLE, '<section class="comparison" id="warning-search"><h1>분석 경고 모아보기</h1>',
        '<p>이 수신 문서의 같은 변환본에 기록된 경고를 파일·유형·검색어로 찾습니다. '
        '수정·확인 후에도 원래 경고는 보존되며, 검색은 해결 처리나 담당자 확인을 대신하지 않습니다.</p>',
        '<p class="compare-meta">고정 변환 버전: <code data-warning-revision>'+esc(revision)+'</code></p>',
        '<form method="get" action="'+PATH+'" class="warning-filters" data-warning-filters>',
        '<input type="hidden" name="id" value="'+esc(ident)+'"><input type="hidden" name="revision" value="'+esc(revision)+'">',
        '<label>경고 검색<input type="search" name="q" maxlength="120" value="'+esc(f['q'])+'" placeholder="예: 각주, 신청서, 파일명"></label>']
    for name, label, options in (('file','파일',data['files']), ('code','경고 유형',data['codes'])):
        content.append('<label>'+label+'<select aria-label="'+label+'" name="'+name+'"><option value="">전체 · '+str(data['all_total'])+'건</option>')
        for option in options:
            content.append('<option value="'+esc(option['value'])+'"'+(' selected' if f[name]==option['value'] else '')+'>'+
                esc(option['label'])+' · '+str(option['count'])+'건</option>')
        content.append('</select></label>')
    content.extend(['<div class="filter-actions"><button type="submit">경고 검색</button> · <a data-warning-reset href="'+
        esc(link(ident,revision))+'">검색 초기화</a></div></form>',
        '<p>제목·설명·확인할 내용·파일 위치·언급된 첨부 이름·경고 코드를 글자 그대로 검색합니다. '
        '대소문자는 구분하지 않으며, 파일·유형 옆 건수는 검색 전 전체 경고 기준입니다.</p>',
        '<p data-warning-summary>전체 경고 '+str(data['all_total'])+'건 · 검색 결과 '+str(data['total'])+'건</p>'])
    if data['rows']:
        content.append('<p data-warning-range>원래 분석 순서를 유지합니다. 현재 페이지는 검색 결과 중 '+
            str((data['page']-1)*search.PAGE_SIZE+1)+'–'+str(min(data['page']*search.PAGE_SIZE,data['total']))+'번째 항목입니다.</p>')
        content.append(guidance.render(data['analysis'],ident,warnings=[r['warning'] for r in data['rows']],directory=False))
    elif data['all_total']:
        content.append('<p data-warning-empty>검색 조건에 맞는 경고가 없습니다. 원래 기록된 경고는 그대로 남아 있습니다. 조건을 바꾸거나 검색을 초기화하세요.</p>')
    else:
        content.append('<p data-warning-empty>이 변환본에 기록된 자동 분석 경고가 없습니다. 원본과의 대조는 필요합니다.</p>')
    content.append('<nav class="warning-pages" aria-label="분석 경고 페이지">'+str(data['page'])+' / '+str(data['pages'])+'페이지')
    for page, label, rel in ((data['page']-1,'이전 경고','prev'),(data['page']+1,'다음 경고','next')):
        if 1<=page<=data['pages']:
            content.append(' · <a rel="'+rel+'" href="'+esc(link(ident,revision,**f,page=page))+'">'+label+'</a>')
    content.append('</nav><p><a data-warning-structure href="'+esc('/structure?'+urlencode({'id':ident,'revision':revision}))+
        '">이 변환본의 전체 자동 분석 보기</a></p></section>')
    return handler._send(handler._shell(''.join(content)))
