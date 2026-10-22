"""Server-rendered, escaped staff review forms. No executable document content."""
import json
from urllib.parse import quote

from . import school_review as review
from .school_export_web import link as export_link
from .web import esc

STATUS = {'unreviewed':'미검수', 'draft':'수정 중 · 확인 필요', 'confirmed':'담당자 확인 완료'}


def _hidden(name, value):
    return '<input type="hidden" name="'+esc(name)+'" value="'+esc(str(value))+'">'


def _link(path, ident, revision):
    return path+'?id='+quote(ident,safe='')+'&amp;revision='+quote(revision,safe='')


def _form(handler, ident, v, action):
    fields = {'id':ident, 'revision':v['original']['conversion_revision'], 'base_hash':v['base_hash'],
              'expected_review':v['latest']['id'] if v['latest'] else '', 'action':action}
    return '<form action="/structure-review" method="post">'+handler._csrf_field()+''.join(_hidden(k,val) for k,val in fields.items())


def _reason(button):
    return ('<label>수정·확인 사유 <textarea name="comment" maxlength="2000" required></textarea></label>'
            '<button>'+esc(button)+'</button></form>')


def _page(query, key):
    value = int(query.get(key,['1'])[0])
    if not 1 <= value <= 10000: raise ValueError('Invalid page')
    return value


def _pages(url, key, page, total, size):
    count = max(1,(total+size-1)//size)
    content = '<p>'+str(page)+' / '+str(count)+'쪽 (총 '+str(total)+'개) '
    if page>1: content += '<a href="'+url+'&amp;'+key+'='+str(page-1)+'">이전</a> '
    if page<count: content += '<a href="'+url+'&amp;'+key+'='+str(page+1)+'">다음</a>'
    return content+'</p>'


def render(handler, ident, query):
    v = review.view(handler.store,handler._principal(),ident,
        revision_id=query.get('revision',[''])[0],event_id=query.get('event',[''])[0])
    if query.get('format',[''])[0]=='json':
        return handler._send(json.dumps(v,ensure_ascii=False),ctype='application/json; charset=utf-8')
    a, original = v['analysis'], v['original']
    revision = original['conversion_revision']; url = _link('/structure-review',ident,revision)
    if v['historical_event']: url += '&amp;event='+quote(v['latest']['id'])
    content = '<h1>문서 구조 검수</h1><p><strong>'+esc(STATUS[v['status']])+'</strong> · 비공개</p>'
    content += '<p>자동 분석 원본과 담당자 수정본은 별도입니다. 확인은 이 버전의 구조에만 적용되며 공개·원본 변경·기존 HTML 덮어쓰기·학습·운영 승인이 아닙니다.</p>'
    content += '<p>변환 버전 '+esc(revision)+' · <a href="'+_link('/structure',ident,revision)+'">자동 분석 원본</a> · <a href="'+_link('/structure-history',ident,revision)+'">검수 이력</a> · <a href="'+url+'&amp;format=json">수정본·근거 JSON</a> · <a href="/notice?id='+quote(ident)+'">수신본</a></p>'
    if v['latest']:
        content += '<p>검수 #'+str(v['latest']['sequence'])+' · '+esc(v['latest']['actor'])+' · '+esc(v['latest']['created_at'])+'</p>'
        content += '<p><a href="'+esc(export_link('/review-preview',ident,revision,v['latest']['id']))+'">이 검수 버전의 HTML 미리보기·다운로드</a></p>'
    if not v['editable']:
        content += '<p role="status">읽기 전용: 조회 권한, 이전 수신본/변환/검수 버전, 변환 진행 또는 명시적 마이그레이션 필요 상태입니다.</p>'
    content += '<h2>파일과 미확정 범위</h2><ul>'
    for f in a['files']:
        content += '<li>'+esc('file:'+f['id'])+' · '+esc(f['filename'])+' · '+esc(f['role'])+' · '+esc(f['status'])+'</li>'
    content += '</ul><p>자동 분석 완전성: '+('완전' if a['complete'] else '일부 미확정·누락 있음')+' — 확인 후에도 원래 경고를 보존합니다.</p>'
    for warning in a['warnings']:
        content += '<p>'+esc(json.dumps(warning,ensure_ascii=False))+'</p>'
    if v['editable']:
        if v['status']!='confirmed':
            content += _form(handler,ident,v,'confirm')+'<label><input type="checkbox" name="acknowledge_warnings" value="yes" required> 파일·구역·관계와 미확정/누락 범위를 확인했습니다. 누락 없는 문서임을 보증하는 확인은 아닙니다.</label>'+_reason('이 구조 버전 확인 완료')
        else:
            content += _form(handler,ident,v,'reopen')+_reason('확인 취소 · 다시 검수')
    content += '<h2>구역 확인·수정</h2><p>종류·추출 텍스트만 수정합니다. 원본 위치·SHA·표 셀·파일 역할은 그대로이며 수정하면 다시 확인이 필요합니다. 원본에 없는 표 구조를 만들지는 않습니다.</p>'
    block_page = _page(query,'block_page'); size = review.PAGE_SIZE
    content += _pages(url,'block_page',block_page,len(a['blocks']),size)
    originals = {b['id']:b for b in original['blocks']}
    for b in a['blocks'][(block_page-1)*size:block_page*size]:
        old = originals[b['id']]
        content += '<section class="card" id="'+esc(b['id'])+'"><h3>'+esc(b['id'])+' · '+esc(review.BLOCK_LABELS[b['kind']])+'</h3>'
        content += '<details><summary>자동 분석 원본 · '+esc(review.BLOCK_LABELS[old['kind']])+'</summary><pre style="white-space:pre-wrap">'+esc(old['text'])+'</pre></details>'
        content += '<p>원본 위치 '+esc(json.dumps(old['locator'],ensure_ascii=False))+' · 자동 근거 '+esc(old['basis'])+'</p>'
        if v['editable']:
            content += _form(handler,ident,v,'block')+_hidden('block_id',b['id'])+'<label>구역 종류 <select name="block_kind">'
            for key,label in review.BLOCK_LABELS.items():
                content += '<option value="'+key+'"'+(' selected' if b['kind']==key else '')+'>'+label+'</option>'
            content += '</select></label><label>수정본 텍스트 (최대 20,000자)<textarea name="text" rows="5" maxlength="20000" required>'+esc(b['text'])+'</textarea></label>'+_reason('구역 수정 저장')
        else: content += '<pre style="white-space:pre-wrap">'+esc(b['text'])+'</pre>'
        content += '</section>'
    content += '<h2>헤더·각주·첨부 관계 수정</h2><p>기존 관계를 제거하거나 같은 수신본의 구역/파일 ID로 관계를 추가하세요. 파일의 안내문·첨부 역할은 이 화면에서 바꾸지 않습니다.</p>'
    relation_page = _page(query,'relation_page')
    content += _pages(url,'relation_page',relation_page,len(a['relations']),size)
    for edge in a['relations'][(relation_page-1)*size:relation_page*size]:
        content += '<section class="card"><p>'+esc(review.RELATION_LABELS.get(edge['kind'],edge['kind']))+' · '+esc(edge['source'])+' → '+esc(edge['target'])+' · '+esc(edge['basis'])+' · '+esc(edge['certainty'])+'</p>'
        if v['editable'] and edge['kind'] in review.RELATION_LABELS:
            content += _form(handler,ident,v,'relation_remove')+''.join(_hidden('relation_'+k,edge[k]) for k in ('kind','source','target'))+_reason('이 관계 제거')
        content += '</section>'
    if v['editable']:
        content += _form(handler,ident,v,'relation_add')+'<label>관계 <select name="relation_kind">'
        for key,label in review.RELATION_LABELS.items(): content += '<option value="'+key+'">'+esc(label)+'</option>'
        content += '</select></label><label>출발 ID <input name="relation_source" maxlength="160" required></label><label>대상 ID <input name="relation_target" maxlength="160" required></label>'+_reason('관계 추가')
    return handler._send(handler._shell(content))


def render_history(handler, ident, query):
    revision = query.get('revision',[''])[0]
    v = review.view(handler.store,handler._principal(),ident,revision_id=revision)
    revision = v['original']['conversion_revision']
    result = review.history(handler.store,handler._principal(),ident,revision_id=revision,before=int(query.get('before',['0'])[0]))
    if query.get('format',[''])[0]=='json':
        return handler._send(json.dumps(result,ensure_ascii=False),ctype='application/json; charset=utf-8')
    url = _link('/structure-history',ident,revision)
    content = '<h1>문서 구조 검수 이력</h1><p>수정·확인·확인 취소는 삭제나 덮어쓰기 없이 추가됩니다.</p><a href="'+_link('/structure-review',ident,revision)+'">이 버전의 최신 검수</a>'
    versions = review.conversions(handler.store,handler._principal(),ident,before=int(query.get('versions_before',['0'])[0]))
    content += '<h2>변환 버전별 검수 기록</h2><p>재변환 전 기록도 아래에서 열 수 있습니다. 구조 분석이 없던 구버전은 조회할 수 없습니다.</p><ul>'
    for row in versions['revisions']:
        content += '<li><a href="'+_link('/structure-history',ident,row['id'])+'">변환 #'+str(row['sequence'])+' · '+esc(row['created_at'])+'</a></li>'
    content += '</ul>'
    if versions['next_before']: content += '<a href="'+url+'&amp;versions_before='+str(versions['next_before'])+'">더 이전 변환</a>'
    if not result['events']: content += '<p>아직 검수 이력이 없습니다.</p>'
    for e in result['events']:
        content += '<section class="card"><h2>#'+str(e['sequence'])+' · '+esc(STATUS[e['status']])+'</h2><p>'+esc(e['actor'])+' · '+esc(e['created_at'])+' · '+esc(e['action'])+'</p><p>사유: '+esc(e['comment'])+'</p>'
        content += '<p><a href="'+_link('/structure-review',ident,revision)+'&amp;event='+quote(e['id'])+'">당시 수정본 보기</a></p>'
        content += '<p><a href="'+esc(export_link('/review-preview',ident,revision,e['id']))+'">당시 수정본 HTML·다운로드</a></p>'
        for change in e['changes']:
            content += '<p>'+esc(change['target'])+' '+esc(change.get('id',''))+'</p><details open><summary>수정 전</summary><pre style="white-space:pre-wrap">'+esc(json.dumps(change['before'],ensure_ascii=False,indent=2))+'</pre></details><details open><summary>수정 후</summary><pre style="white-space:pre-wrap">'+esc(json.dumps(change['after'],ensure_ascii=False,indent=2))+'</pre></details>'
        content += '</section>'
    if result['next_before']: content += '<a href="'+url+'&amp;before='+str(result['next_before'])+'">이전 이력</a>'
    return handler._send(handler._shell(content))


def submit(handler, form):
    # Reject ambiguous duplicate fields and authority/provenance injection.
    allowed = {'csrf','id','revision','base_hash','expected_review','action','comment',
        'block_id','block_kind','text','relation_kind','relation_source','relation_target','acknowledge_warnings'}
    if set(form)-allowed or any(len(values)!=1 for values in form.values()): raise ValueError('Invalid form fields')
    field = lambda k: form.get(k,[''])[0]
    kind = field('action'); action = {'kind':kind}
    if kind=='block': action.update(block_id=field('block_id'),block_kind=field('block_kind'),text=field('text'))
    elif kind in ('relation_add','relation_remove'):
        action['relation'] = {k:field('relation_'+k) for k in ('kind','source','target')}
    elif kind=='confirm': action['acknowledge_warnings'] = field('acknowledge_warnings')=='yes'
    review.save(handler.store,handler._principal(),field('id'),revision_id=field('revision'),
        base_hash=field('base_hash'),expected_review=field('expected_review'),action=action,comment=field('comment'))
    return handler._redirect('/structure-review?id='+quote(field('id'),safe='')+'&revision='+quote(field('revision'),safe=''))
