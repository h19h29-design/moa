"""Server-rendered, escaped staff review forms. No executable document content."""
import json
from urllib.parse import quote

from . import school_review as review
from . import school_queue_web, school_review_drafts as drafts, school_draft_web
from .school_review_compare import link as compare_link
from .school_export_web import link as export_link
from .school_restore_web import link as restore_link
from .web import esc

STATUS = {'unreviewed':'미검수', 'draft':'수정 중 · 확인 필요', 'confirmed':'담당자 확인 완료'}


def _hidden(name, value):
    return '<input type="hidden" name="'+esc(name)+'" value="'+esc(str(value))+'">'


def _link(path, ident, revision):
    return path+'?id='+quote(ident,safe='')+'&amp;revision='+quote(revision,safe='')


def _form(handler, ident, v, action, queue=None, input_draft=None):
    fields = {'id':ident, 'revision':v['original']['conversion_revision'], 'base_hash':v['base_hash'],
              'expected_review':v['latest']['id'] if v['latest'] else '', 'action':action}
    if input_draft is not None: fields['expected_draft']=input_draft['expected']
    return ('<form action="/structure-review" method="post" data-queue-review'+(' data-draft-autosave' if input_draft is not None else '')+'>'+
            handler._csrf_field()+''.join(_hidden(k,val) for k,val in fields.items())+school_queue_web.hidden(queue))


def _reason(button, input_draft=None):
    content='<label>수정·확인 사유 <textarea name="comment" maxlength="2000" required>'+esc(input_draft['comment'] if input_draft else '')+'</textarea></label>'
    content+='<button>'+esc(button)+'</button>'
    if input_draft is not None:
        content+=' <button formaction="/structure-draft/save" formnovalidate data-draft-save>이 항목 입력 임시 저장</button>'
        if input_draft['resume']: content+=' <a data-draft-resume href="'+esc(input_draft['resume'])+'">저장된 임시 입력 복구</a>'
        content+='<p data-draft-status role="status" aria-live="polite">자동 임시저장 준비 중 · JavaScript가 꺼져 있으면 수동 저장하세요.</p><button type="button" data-draft-retry hidden>임시 저장 다시 시도</button>'
    return content+'</form>'


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


def render(handler, ident, query, *, queue=None, draft=None):
    try:
        v = review.view(handler.store,handler._principal(),ident,
            revision_id=query.get('revision',[''])[0],event_id=query.get('event',[''])[0])
    except (review.Conflict,LookupError,ValueError):
        if draft: return school_draft_web.read_only(handler,draft)
        raise
    if draft:
        # Validate against the SAME view used to build the form, never reload a
        # newer base after checking compatibility and silently rebase old input.
        head=drafts.metadata(handler.store,handler._principal(),ident,draft['conversion_revision'],[draft['target_key']])
        if (not v['editable'] or v['base_hash']!=draft['base_hash']
                or (v['latest']['id'] if v['latest'] else '')!=draft['base_review']
                or head.get(draft['target_key'],{}).get('id')!=draft['id']):
            return school_draft_web.read_only(handler,draft)
        query=dict(query); action=draft['payload']['action']
        if action['kind']=='block':
            index=next(i for i,b in enumerate(v['analysis']['blocks']) if b['id']==action['block_id'])
            query['block_page']=[str(index//review.PAGE_SIZE+1)]
        elif action['kind']=='relation_remove':
            index=next(i for i,r in enumerate(v['analysis']['relations']) if all(r[k]==val for k,val in action['relation'].items()))
            query['relation_page']=[str(index//review.PAGE_SIZE+1)]
    if query.get('format',[''])[0]=='json':
        return handler._send(json.dumps(v,ensure_ascii=False),ctype='application/json; charset=utf-8')
    a, original = v['analysis'], v['original']
    block_page=_page(query,'block_page'); relation_page=_page(query,'relation_page'); size=review.PAGE_SIZE
    visible_blocks=a['blocks'][(block_page-1)*size:block_page*size]
    visible_edges=a['relations'][(relation_page-1)*size:relation_page*size]
    actions=[{'kind':'block','block_id':b['id']} for b in visible_blocks]
    actions += [{'kind':'relation_remove','relation':{k:r[k] for k in ('kind','source','target')}}
                for r in visible_edges if r['kind'] in review.RELATION_LABELS]
    actions += [{'kind':'relation_add'}]
    installed=drafts.available(handler.store)
    heads=drafts.metadata(handler.store,handler._principal(),ident,original['conversion_revision'],[drafts.target(x) for x in actions]) if installed and v['editable'] else {}
    def input_draft(action):
        if not installed or not v['editable']: return None
        topic=drafts.target(action); head=heads.get(topic)
        if draft and draft['target_key']==topic: head=draft
        return {'expected':head['id'] if head else '', 'resume':school_draft_web.link(head['id'],queue) if head else '',
                'comment':draft['payload']['comment'] if draft and draft['target_key']==topic else ''}
    revision = original['conversion_revision']; url = _link('/structure-review',ident,revision)
    if v['historical_event']: url += '&amp;event='+quote(v['latest']['id'])
    page_url = esc(school_queue_web.link(queue)) if queue else url
    content = ('<div id="school-structure-review"><style>'
        '#school-structure-review{min-width:0;overflow-wrap:anywhere}'
        '#school-structure-review input,#school-structure-review select,#school-structure-review textarea'
        '{max-width:100%;box-sizing:border-box}'
        '</style><h1>문서 구조 검수</h1>')+(school_queue_web.banner(queue,handler) if queue else '')
    if not queue:
        content+='<p id="queue-unsaved" role="status" hidden>저장하지 않은 입력이 있습니다.</p><script src="/static/school-queue.js" defer></script>'
    if draft:
        content+='<section id="draft-restored" class="card"><h2>임시 입력 복구됨</h2><p>임시 #'+str(draft['sequence'])+' · '+esc(draft['created_at'])+' · 아직 정식 검수에 반영되지 않았습니다. 아래 항목을 확인하고 정식 저장하세요. 확인 체크는 복구하지 않습니다.</p></section>'
        from .school_draft_compare_web import link as draft_compare_link
        content+='<p><a data-draft-compare href="'+esc(draft_compare_link(draft['id']))+'">복구한 입력과 현재 검수본 비교</a></p>'
        from .school_draft_history_web import link as draft_history_link
        content+='<p><a data-draft-history href="'+esc(draft_history_link(draft['id']))+'">임시 입력 버전 이력</a></p>'
        from .school_draft_restore_web import origin
        content+=origin(draft)
        from .school_draft_handoff_web import private_status
        content+=private_status(handler.store,handler._principal(),draft['id'])
    if v['editable']:
        content+='<p>각 구역·관계는 입력이 1.5초 멈추면 개인 초안으로 자동 임시저장합니다. 항목별 완료 표시를 확인하세요. 정식 검수 반영은 별도 저장이 필요합니다. 확인/확인 취소·과거 검수본 복원은 자동저장하지 않습니다. JavaScript가 꺼지면 수동 임시저장을 사용하세요.</p>'
        if not installed: content+='<p>입력 임시 저장의 명시적 마이그레이션이 필요합니다. 이 조회는 DB를 변경하지 않습니다.</p>'
        else: content+='<script src="/static/school-draft.js" defer></script>'
    content += '<p><strong>'+esc(STATUS[v['status']])+'</strong> · 비공개</p>'
    content += '<p>자동 분석 원본과 담당자 수정본은 별도입니다. 확인은 이 버전의 구조에만 적용되며 공개·원본 변경·기존 HTML 덮어쓰기·학습·운영 승인이 아닙니다.</p>'
    content += '<p>변환 버전 '+esc(revision)+' · <a href="'+_link('/structure',ident,revision)+'">자동 분석 원본</a> · <a href="'+_link('/structure-history',ident,revision)+'">검수 이력</a> · <a href="'+url+'&amp;format=json">수정본·근거 JSON</a> · <a href="/notice?id='+quote(ident)+'">수신본</a></p>'
    if v['latest']:
        content += '<p>검수 #'+str(v['latest']['sequence'])+' · '+esc(v['latest']['actor'])+' · '+esc(v['latest']['created_at'])+'</p>'
        content += '<p><a href="'+esc(export_link('/review-preview',ident,revision,v['latest']['id']))+'">이 검수 버전의 HTML 미리보기·다운로드</a></p>'
        content += _compare_links(ident,revision,v['latest'])
        from .school_draft_handoff import for_review
        from .school_draft_handoff_web import receipt_html
        content += receipt_html(for_review(handler.store,handler._principal(),v['latest']['id']),formal=True)
    if not v['editable']:
        content += '<p role="status">읽기 전용: 조회 권한, 이전 수신본/변환/검수 버전, 변환 진행·실패 또는 명시적 마이그레이션 필요 상태입니다.</p>'
    content += '<h2>파일과 미확정 범위</h2><ul>'
    for f in a['files']:
        content += '<li>'+esc('file:'+f['id'])+' · '+esc(f['filename'])+' · '+esc(f['role'])+' · '+esc(f['status'])+'</li>'
    content += '</ul><p>자동 분석 완전성: '+('완전' if a['complete'] else '일부 미확정·누락 있음')+' — 확인 후에도 원래 경고를 보존합니다.</p>'
    for warning in a['warnings']:
        content += '<p>'+esc(json.dumps(warning,ensure_ascii=False))+'</p>'
    if v['editable']:
        if v['status']!='confirmed':
            content += _form(handler,ident,v,'confirm',queue)+'<label><input type="checkbox" name="acknowledge_warnings" value="yes" required> 파일·구역·관계와 미확정/누락 범위를 확인했습니다. 누락 없는 문서임을 보증하는 확인은 아닙니다.</label>'+_reason('이 구조 버전 확인 완료')
        else:
            content += _form(handler,ident,v,'reopen',queue)+_reason('확인 취소 · 다시 검수')
    content += '<h2>구역 확인·수정</h2><p>종류·추출 텍스트만 수정합니다. 원본 위치·SHA·표 셀·파일 역할은 그대로이며 수정하면 다시 확인이 필요합니다. 원본에 없는 표 구조를 만들지는 않습니다.</p>'
    content += _pages(page_url,'block_page',block_page,len(a['blocks']),size)
    originals = {b['id']:b for b in original['blocks']}
    for b in visible_blocks:
        info=input_draft({'kind':'block','block_id':b['id']})
        if draft and draft['target_key']==drafts.target({'kind':'block','block_id':b['id']}):
            b={**b,**{k:draft['payload']['action'][source] for k,source in (('kind','block_kind'),('text','text'))}}
        old = originals[b['id']]
        content += '<section class="card" id="'+esc(b['id'])+'"><h3>'+esc(b['id'])+' · '+esc(review.BLOCK_LABELS[b['kind']])+'</h3>'
        content += '<details><summary>자동 분석 원본 · '+esc(review.BLOCK_LABELS[old['kind']])+'</summary><pre style="white-space:pre-wrap">'+esc(old['text'])+'</pre></details>'
        content += '<p>원본 위치 '+esc(json.dumps(old['locator'],ensure_ascii=False))+' · 자동 근거 '+esc(old['basis'])+'</p>'
        if v['editable']:
            content += _form(handler,ident,v,'block',queue,info)+_hidden('block_id',b['id'])+'<label>구역 종류 <select name="block_kind">'
            for key,label in review.BLOCK_LABELS.items():
                content += '<option value="'+key+'"'+(' selected' if b['kind']==key else '')+'>'+label+'</option>'
            content += '</select></label><label>수정본 텍스트 (최대 20,000자)<textarea name="text" rows="5" maxlength="20000" required>'+esc(b['text'])+'</textarea></label>'+_reason('구역 수정 저장',info)
        else: content += '<pre style="white-space:pre-wrap">'+esc(b['text'])+'</pre>'
        content += '</section>'
    content += '<h2>헤더·각주·첨부 관계 수정</h2><p>기존 관계를 제거하거나 같은 수신본의 구역/파일 ID로 관계를 추가하세요. 파일의 안내문·첨부 역할은 이 화면에서 바꾸지 않습니다.</p>'
    content += _pages(page_url,'relation_page',relation_page,len(a['relations']),size)
    for edge in visible_edges:
        content += '<section class="card"><p>'+esc(review.RELATION_LABELS.get(edge['kind'],edge['kind']))+' · '+esc(edge['source'])+' → '+esc(edge['target'])+' · '+esc(edge['basis'])+' · '+esc(edge['certainty'])+'</p>'
        if v['editable'] and edge['kind'] in review.RELATION_LABELS:
            info=input_draft({'kind':'relation_remove','relation':{k:edge[k] for k in ('kind','source','target')}})
            content += _form(handler,ident,v,'relation_remove',queue,info)+''.join(_hidden('relation_'+k,edge[k]) for k in ('kind','source','target'))+_reason('이 관계 제거',info)
        content += '</section>'
    if v['editable']:
        info=input_draft({'kind':'relation_add'})
        relation=draft['payload']['action']['relation'] if draft and draft['action_kind']=='relation_add' else {}
        content += _form(handler,ident,v,'relation_add',queue,info)+'<label>관계 <select name="relation_kind">'
        for key,label in review.RELATION_LABELS.items(): content += '<option value="'+key+'"'+(' selected' if relation.get('kind')==key else '')+'>'+esc(label)+'</option>'
        content += '</select></label><label>출발 ID <input name="relation_source" maxlength="160" value="'+esc(relation.get('source',''))+'" required></label><label>대상 ID <input name="relation_target" maxlength="160" value="'+esc(relation.get('target',''))+'" required></label>'+_reason('관계 추가',info)
    return handler._send(handler._shell(content+'</div>'))


def _compare_links(ident, revision, event):
    content = '<p><a href="'+esc(compare_link(ident,revision,'original',event['id']))+'">자동 분석본과 변경점 비교</a>'
    if event['previous_id']:
        content += ' · <a href="'+esc(compare_link(ident,revision,event['previous_id'],event['id']))+'">직전 검수 버전과 비교</a>'
    return content+'</p>'


def render_history(handler, ident, query):
    revision = query.get('revision',[''])[0]
    v = review.view(handler.store,handler._principal(),ident,revision_id=revision)
    revision = v['original']['conversion_revision']
    result = review.history(handler.store,handler._principal(),ident,revision_id=revision,before=int(query.get('before',['0'])[0]))
    if query.get('format',[''])[0]=='json':
        return handler._send(json.dumps(result,ensure_ascii=False),ctype='application/json; charset=utf-8')
    url = _link('/structure-history',ident,revision)
    content = '<h1>문서 구조 검수 이력</h1><p>수정·확인·확인 취소·이전 검수본 복원은 삭제나 덮어쓰기 없이 추가됩니다. 복원 후에는 다시 검수가 필요합니다.</p><a href="'+_link('/structure-review',ident,revision)+'">이 버전의 최신 검수</a>'
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
        content += _compare_links(ident,revision,e)
        from .school_draft_handoff import for_review
        from .school_draft_handoff_web import receipt_html
        content += receipt_html(for_review(handler.store,handler._principal(),e['id']),formal=True)
        if v['editable'] and e['id'] != v['latest']['id']:
            content += '<p><a href="'+esc(restore_link(ident,revision,e['id']))+'">이 검수본을 새 수정본으로 복원</a></p>'
        if e['action']=='restore':
            source = next(c['after'] for c in e['changes'] if c['target']=='restore_source')
            content += '<p>복원 출처: <a href="'+_link('/structure-review',ident,revision)+'&amp;event='+quote(source['event_id'],safe='')+'">검수 #'+str(source['sequence'])+'</a> · 다시 검수 필요로 저장</p>'
            blocks = sum(c['target']=='block' for c in e['changes'])
            relations = sum(c['target']=='relation' for c in e['changes'])
            content += '<p>복원 변경: 구역 '+str(blocks)+'개 · 관계 '+str(relations)+'개. 수정 전·후 전체 내용은 위 직전 검수 버전과 비교에서 쪽별로 확인하세요.</p>'
        # A restoration can span the whole document; do not multiply its full
        # before/after text by 20 history entries on one HTML page.
        for change in ([] if e['action']=='restore' else e['changes']):
            content += '<p>'+esc(change['target'])+' '+esc(change.get('id',''))+'</p><details open><summary>수정 전</summary><pre style="white-space:pre-wrap">'+esc(json.dumps(change['before'],ensure_ascii=False,indent=2))+'</pre></details><details open><summary>수정 후</summary><pre style="white-space:pre-wrap">'+esc(json.dumps(change['after'],ensure_ascii=False,indent=2))+'</pre></details>'
        content += '</section>'
    if result['next_before']: content += '<a href="'+url+'&amp;before='+str(result['next_before'])+'">이전 이력</a>'
    return handler._send(handler._shell(content))


def submit(handler, form):
    # Reject ambiguous duplicate fields and authority/provenance injection.
    allowed = {'csrf','id','revision','base_hash','expected_review','action','comment',
        'block_id','block_kind','text','relation_kind','relation_source','relation_target','acknowledge_warnings',
        'source_event','acknowledge_restore','queue_id','queue_item','saved_id','saved_item','expected_draft'}
    if set(form)-allowed or any(len(values)!=1 for values in form.values()): raise ValueError('Invalid form fields')
    field = lambda k: form.get(k,[''])[0]
    queue = school_queue_web.submission(handler,form)
    kind = field('action'); action = {'kind':kind}
    if kind=='block': action.update(block_id=field('block_id'),block_kind=field('block_kind'),text=field('text'))
    elif kind in ('relation_add','relation_remove'):
        action['relation'] = {k:field('relation_'+k) for k in ('kind','source','target')}
    elif kind=='confirm': action['acknowledge_warnings'] = field('acknowledge_warnings')=='yes'
    elif kind=='restore':
        if set(form)-{'csrf','id','revision','base_hash','expected_review','action','comment','source_event','acknowledge_restore','queue_id','queue_item','saved_id','saved_item'}:
            raise ValueError('Invalid restore form fields')
        action.update(source_event=field('source_event'),acknowledge_restore=field('acknowledge_restore')=='yes')
    review.save(handler.store,handler._principal(),field('id'),revision_id=field('revision'),
        base_hash=field('base_hash'),expected_review=field('expected_review'),action=action,comment=field('comment'))
    if queue: return handler._redirect(school_queue_web.link(queue))
    return handler._redirect('/structure-review?id='+quote(field('id'),safe='')+'&revision='+quote(field('revision'),safe=''))
