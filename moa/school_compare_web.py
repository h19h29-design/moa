"""Escaped, bounded, authenticated comparison UI. Every link pins both sides."""
from urllib.parse import urlencode

from . import school_review as review, school_review_compare as compare
from . import school_service as svc, school_warning_web as warnings_ui
from .core import encode
from .web import esc

STATUS = {'unreviewed':'미검수','draft':'수정 중 · 확인 필요','confirmed':'담당자 확인 완료'}
RELATIONS = {**review.RELATION_LABELS,'guide_of':'안내 원본 → 통신문','attachment_of':'첨부 → 통신문'}
MAX_HTML_BYTES = 32 * 1024 * 1024
STYLE = '''<style>
.comparison{overflow-wrap:anywhere}.comparison *{box-sizing:border-box}
.compare-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px}
.comparison .card{padding:18px}.comparison h2{font-size:19px}.comparison h3{font-size:16px}
.comparison select{width:100%;min-width:0}.comparison label{display:block;margin-bottom:8px}
.comparison pre{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.7;font-size:14px;margin:8px 0}
.comparison del{background:#ffe3e0;color:#8a241b;text-decoration:line-through}
.comparison ins{background:#dff3e7;color:#185c38;text-decoration:underline}
.compare-note{border-left:4px solid #55758b;padding:10px 14px;background:#edf3f7}
.compare-meta{font-size:13px;color:#47596a}.compare-pages{margin:14px 0}
.comparison a{overflow-wrap:anywhere}.comparison summary{cursor:pointer}
@media(max-width:650px){.compare-grid{grid-template-columns:minmax(0,1fr)}.comparison .card{padding:12px}}
</style>'''


class _Page:
    def __init__(self): self.parts, self.size = [], 0

    def add(self, html):
        self.size += len(html.encode())
        if self.size>MAX_HTML_BYTES: raise ValueError('Comparison HTML output limit')
        self.parts.append(html)

    def text(self): return ''.join(self.parts)


def text_pair(before, after):
    """Linear prefix/suffix comparison, not quadratic edit-distance alignment.

    Enclose the changed middle span (including unchanged islands) on each side;
    do not claim it is a minimal word-level diff or normalize away whitespace.
    """
    if before==after: return esc(before),esc(after)
    start=0; limit=min(len(before),len(after))
    while start<limit and before[start]==after[start]: start+=1
    end=0
    while end<limit-start and before[len(before)-1-end]==after[len(after)-1-end]: end+=1
    def marked(text,tag):
        stop=len(text)-end
        return esc(text[:start])+'<'+tag+'>'+esc(text[start:stop])+'</'+tag+'>'+esc(text[stop:])
    return marked(before,'del'),marked(after,'ins')


def _label(event):
    if event['id']=='original': return '자동 분석 원본 · 미검수'
    return '검수 #'+str(event['sequence'])+' · '+STATUS[event['status']]+' · '+event['created_at']


def _url(data, **query):
    return esc(compare.link(data['notice_version'],data['conversion_revision'],data['left']['id'],data['right']['id'],**query))


def _version_link(data, event):
    params={'id':data['notice_version'],'revision':data['conversion_revision']}
    route='/structure'
    if event['id']!='original': route='/review-preview'; params['event']=event['id']
    return esc(route+'?'+urlencode(params))


def _endpoint(value, names):
    file_id=value[5:] if value.startswith('file:') else value.split('/',1)[0]
    return esc(value+(' · '+names[file_id] if file_id in names else ''))


def _pages(page, data, group, params):
    result=data[group]; key={'blocks':'block_page','relations':'relation_page','warnings':'warning_page'}[group]
    page.add('<nav class="compare-pages" aria-label="'+{'blocks':'구역','relations':'관계','warnings':'경고'}[group]+' 페이지">'
             +str(result['page'])+' / '+str(result['pages'])+'쪽 · 총 '+str(result['total'])+'개 ')
    for step,label in ((-1,'이전'),(1,'다음')):
        number=result['page']+step
        if 1<=number<=result['pages']:
            page.add('<a href="'+_url(data,**(params | {key:number}))+'">'+label+'</a> ')
    page.add('</nav>')


def render(handler, query):
    allowed={'id','revision','left','right','format','before','block_page','relation_page','warning_page'}
    if set(query)-allowed or any(len(v)!=1 for v in query.values()): raise ValueError('Invalid comparison query')
    field=lambda k,default='':query.get(k,[default])[0]
    if field('format','html') not in ('html','json'): raise ValueError('Invalid format')
    before=int(field('before','0'))
    if not 0<=before<2**63: raise ValueError('Invalid history cursor')
    params={k:int(field(k,'1')) for k in ('block_page','relation_page','warning_page')}
    data=compare.compare(handler.store,handler._principal(),field('id'),revision_id=field('revision'),
                         left=field('left'),right=field('right'),**params)
    if field('format')=='json': return handler._send(encode(data),ctype='application/json; charset=utf-8')
    history=review.history(handler.store,handler._principal(),field('id'),revision_id=field('revision'),before=before)
    params['before']=before
    page=_Page(); page.add(STYLE+'<div class="comparison"><h1>문서 구조 변경점 비교</h1>'
        '<p>비공개 · 읽기 전용 · 같은 수신본·변환 버전의 두 시점을 비교합니다.</p>'
        '<p class="compare-note">원본·자동 분석·이전 검수 이력은 그대로 보존합니다. '
        '차이 없음이나 담당자 확인은 문서의 완전성·정확성 보증, 공개·학습·운영 승인이 아닙니다.</p>')
    page.add('<details><summary>고정 버전·원본 근거</summary><p>수신본 '+esc(data['notice_version'])+
        '</p><p>변환 '+esc(data['conversion_revision'])+'</p><p>자동 분석 SHA-256 '+esc(data['base_hash'])+'</p></details>')
    page.add('<p><a href="'+esc('/structure-history?'+urlencode({'id':field('id'),'revision':field('revision')}))+'">이 변환 버전의 검수 이력</a></p>')
    page.add('<form method="get" action="/structure-compare"><input type="hidden" name="id" value="'+esc(field('id'))+
        '"><input type="hidden" name="revision" value="'+esc(field('revision'))+'"><input type="hidden" name="before" value="'+str(before)+'"><div class="compare-grid">')
    choices={e['id']:e for e in history['events']}
    for key in ('left','right'): choices[data[key]['id']]=data[key]
    choices['original']={'id':'original','sequence':0}
    for key,label in (('left','비교 기준 (왼쪽)'),('right','비교 대상 (오른쪽)')):
        page.add('<label>'+label+'<select name="'+key+'">')
        for event in sorted(choices.values(),key=lambda e:e['sequence']):
            page.add('<option value="'+esc(event['id'])+'"'+(' selected' if event['id']==data[key]['id'] else '')+'>'+esc(_label(event))+'</option>')
        page.add('</select></label>')
    page.add('</div><button>선택한 두 버전 비교</button></form><p><a href="'+
        esc(compare.link(field('id'),field('revision'),field('right'),field('left')))+'">기준·대상 바꾸기</a> · '
        '<a href="'+_url(data,**(params | {'format':'json'}))+'">이 페이지의 비교 JSON</a></p>')
    page.add('<p>선택 목록은 검수 이력 최대 20개씩 표시합니다. 현재 선택한 버전은 목록을 넘겨도 유지됩니다. ')
    if history['next_before']: page.add('<a data-history="older" href="'+_url(data,**(params | {'before':history['next_before']}))+'">더 이전 검수 선택</a> ')
    if before: page.add('<a href="'+_url(data,**(params | {'before':0}))+'">최근 검수 목록</a>')
    page.add('</p><div class="compare-grid">')
    for key,label in (('left','비교 기준'),('right','비교 대상')):
        event=data[key]
        page.add('<section class="card"><h2>'+label+' · '+esc(_label(event))+'</h2><p class="compare-meta">'+esc(event['actor'])+' '+esc(event['created_at'])+'</p>')
        if event['comment']: page.add('<p>검수 사유: '+esc(event['comment'])+'</p>')
        page.add('<p><a href="'+_version_link(data,event)+'">이 시점 원본 분석 / 수정본 HTML 보기</a></p></section>')
    page.add('</div>')
    summary=data['summary']
    page.add('<h2>변경 요약</h2><p>구역 변경 '+str(summary['changed_blocks'])+'개 · 동일 구역 '+str(summary['unchanged_blocks'])+'개</p>'
        '<p>관계 추가 '+str(summary['added_relations'])+'개 · 제거 '+str(summary['removed_relations'])+'개 · 근거 변경 '+str(summary['modified_relations'])+'개 · 동일 '+str(summary['unchanged_relations'])+'개</p>')
    page.add('<p>검수 상태: '+esc(STATUS[data['left']['status']])+' → '+esc(STATUS[data['right']['status']])+
        (' (상태 변경은 내용 수정 수에 합산하지 않습니다.)' if summary['status_changed'] else ' (같음)')+'</p>')
    page.add('<h2>바뀐 구역</h2><p>종류와 추출 텍스트를 비교합니다. 취소선은 기준 쪽, 밑줄은 대상 쪽의 변경 범위입니다. '
        '공통 앞·뒤를 제외한 중간 범위를 표시하므로 중간의 같은 문구도 포함될 수 있습니다. 표 셀·파일 역할·원본 위치는 편집하지 않습니다.</p>')
    _pages(page,data,'blocks',params)
    if not data['blocks']['total']: page.add('<p>두 시점 사이의 구역 내용 변경이 없습니다.</p>')
    names={f['id']:f['filename'] for f in data['files']}
    for block in data['blocks']['items']:
        page.add('<section class="card" data-change="block"><h3>'+esc(names.get(block['file_id'],block['file_id']))+' · '+esc(block['id'])+'</h3>'
            '<p class="compare-meta">원본 위치 '+esc(encode(block['locator']))+' · '+esc(block['basis'])+' · '+esc(block['certainty'])+'</p><div class="compare-grid">')
        texts=text_pair(block['before']['text'],block['after']['text'])
        for i,(key,label,tag) in enumerate((('before','기준','del'),('after','대상','ins'))):
            kind=esc(review.BLOCK_LABELS[block[key]['kind']])
            if 'kind' in block['fields']: kind='<'+tag+'>'+kind+'</'+tag+'>'
            page.add('<div><h4>'+label+' · '+kind+'</h4><pre>'+texts[i]+'</pre></div>')
        page.add('</div></section>')
    page.add('<h2>바뀐 헤더·각주·첨부 관계</h2><p>관계 종류·출발·대상이 같은 근거를 묶어 비교합니다. 자동 후보와 담당자 지정 근거를 구분합니다.</p>')
    _pages(page,data,'relations',params)
    if not data['relations']['total']: page.add('<p>관계 변경이 없습니다.</p>')
    for edge in data['relations']['items']:
        change={'added':'추가','removed':'제거','modified':'근거 변경'}[edge['change']]
        page.add('<section class="card" data-change="relation"><h3>'+change+' · '+esc(RELATIONS.get(edge['kind'],edge['kind']))+'</h3><p>'+
            _endpoint(edge['source'],names)+' → '+_endpoint(edge['target'],names)+'</p><div class="compare-grid">')
        for key,label in (('before','기준'),('after','대상')):
            page.add('<div><h4>'+label+'</h4>')
            for evidence in edge[key]: page.add('<p>'+esc(evidence['basis'])+' · '+esc(evidence['certainty'])+'</p>')
            if not edge[key]: page.add('<p>관계 없음</p>')
            page.add('</div>')
        page.add('</div></section>')
    page.add('<h2>보존된 파일·미확정 범위</h2><ul>')
    for f in data['files']: page.add('<li>'+esc('file:'+f['id'])+' · '+esc(f['filename'])+' · '+esc(f['role'])+' · '+esc(f['status'])+'</li>')
    page.add('</ul><p>자동 분석 완전성: '+('완전' if data['complete'] else '일부 미확정·누락 있음')+' (검수 후에도 원래 경고를 보존)</p>')
    _pages(page,data,'warnings',params)
    original=svc.structure_for(handler.store,handler._principal(),field('id'),revision_id=field('revision'))
    page.add(warnings_ui.render(original,field('id'),warnings=data['warnings']['items']))
    page.add('</div>')
    return handler._send(handler._shell(page.text()))
