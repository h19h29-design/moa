"""Private per-form save/recovery; autosave ACKs never contain document input."""
import json
import time
from urllib.parse import urlencode

from . import school_review as review, school_review_drafts as drafts, school_queue_web
from .school_saved_queue_web import query, number
from .web import esc


def link(ident, queue=None):
    values={'id':ident}
    if queue and queue.get('saved'): values.update(saved_id=queue['key'],saved_item=queue['item'])
    return '/structure-draft?'+urlencode(values)


def directory(handler, raw):
    from .school_draft_compare_web import link as compare_link
    from .school_draft_history_web import link as history_link
    q=query(raw,{'page'})
    result=drafts.listing(handler.store,handler._principal(),page=number(q.get('page',['1'])[0],1_000_000))
    content=('<section id="input-drafts" style="overflow-wrap:anywhere"><h1>내 임시 저장 입력</h1>'
        '<p>학교·담당자별 비공개입니다. 구역/관계별 최근 임시 입력을 표시합니다. '
        '로그아웃·서버 재시작 후에도 복구할 수 있습니다.</p>'
        '<p>임시 저장은 정식 검수·확인·HTML 반영이 아닙니다. 확인 체크는 저장하지 않습니다. '
        '변경된 기준의 입력은 읽기 전용이며 자동 병합하지 않습니다.</p>')
    if result['migration_required']: content+='<p>임시 저장의 명시적 마이그레이션이 필요합니다. 이 화면은 DB를 변경하지 않습니다.</p>'
    elif not result['rows']: content+='<p>임시 저장한 입력이 없습니다.</p>'
    labels={'block':'구역 입력','relation_add':'관계 추가 입력','relation_remove':'관계 제거 사유'}
    for row in result['rows']:
        content+=('<article class="card"><h2>'+esc(row['title'])+'</h2><p>'+labels[row['action_kind']]+
            ' · 임시 #'+str(row['sequence'])+' · '+esc(row['created_at'])+'</p><a data-draft-resume href="'+
            esc(link(row['id']))+'">임시 입력 복구·확인</a> · <a data-draft-compare href="'+
            esc(compare_link(row['id']))+'">현재 검수본과 비교</a> · <a data-draft-history href="'+
            esc(history_link(row['id']))+'">임시 입력 버전 이력</a></article>')
    content+='<nav aria-label="임시 입력 목록">'+str(result['page'])+' / '+str(result['pages'])+'페이지 '
    for page,label in ((result['page']-1,'이전'),(result['page']+1,'다음')):
        if 1<=page<=result['pages']: content+='<a href="/structure-drafts?page='+str(page)+'">'+label+'</a> '
    return handler._send(handler._shell(content+'</nav></section>'))


def render(handler, raw):
    q=query(raw,{'id','saved_id','saved_item'})
    row=drafts.get(handler.store,handler._principal(),q.get('id',[''])[0])
    queue=school_queue_web.submission(handler,{**{k:q[k] for k in ('saved_id','saved_item') if k in q},
        'id':[row['notice_version']],'revision':[row['conversion_revision']]})
    from . import school_review_web
    return school_review_web.render(handler,row['notice_version'],{'revision':[row['conversion_revision']]},queue=queue,draft=row)


def read_only(handler, row):
    from .school_draft_compare_web import link as compare_link
    from .school_draft_history_web import link as history_link
    from .school_draft_restore_web import link as restore_link, origin
    from .school_draft_handoff_web import private_status
    content=('<section id="draft-readonly" style="overflow-wrap:anywhere"><h1>임시 입력 · 읽기 전용</h1>'
        '<p>기준 검수·수신본·변환·임시 입력이 변경됐거나 현재 수정 권한이 없습니다. '
        '저장했던 입력은 보존되며 최신 문서로 자동 적용·병합하지 않습니다.</p>'
        '<p><a data-draft-compare href="'+esc(compare_link(row['id']))+'">현재 검수본과 비교·변경된 기준 확인</a></p>'
        '<p><a data-draft-history href="'+esc(history_link(row['id']))+'">임시 입력 버전 이력</a></p>'
        '<details><summary>저장한 입력 데이터</summary><pre style="white-space:pre-wrap">'+
        esc(json.dumps(row['payload'],ensure_ascii=False,indent=2))+'</pre></details>'
        '<p><a data-draft-restore href="'+esc(restore_link(row['id']))+'">이 입력 가져오기 전 확인</a></p>'+origin(row)+
        private_status(handler.store,handler._principal(),row['id'])+
        '<a href="/structure-drafts">내 임시 저장 입력</a></section>')
    return handler._send(handler._shell(content))


def submit(handler, form, *, autosave=False):
    common={'csrf','id','revision','base_hash','expected_review','expected_draft','action','comment'}
    context={'queue_id','queue_item','saved_id','saved_item'}
    kind=form.get('action',[''])[0]
    fields={'block':{'block_id','block_kind','text'},
            'relation_add':{'relation_kind','relation_source','relation_target'},
            'relation_remove':{'relation_kind','relation_source','relation_target'}}
    if (kind not in fields or set(form)-(common|context|fields[kind]) or not (common|fields[kind])<=set(form)
            or any(len(v)!=1 for v in form.values())): raise ValueError('Invalid input draft form')
    if autosave:
        session=handler._session(); now=time.monotonic()
        attempts=[t for t in session.get('draft_attempts',[]) if now-t<60]
        if len(attempts)>=120: return handler._send('잠시 후 임시 저장을 다시 시도하세요.',429)
        session['draft_attempts']=[*attempts,now]
    queue=school_queue_web.submission(handler,form); value=lambda k:form[k][0]
    action={'kind':kind}
    if kind=='block': action.update({k:value(k) for k in ('block_id','block_kind','text')})
    else: action['relation']={k:value('relation_'+k) for k in ('kind','source','target')}
    ident=drafts.save(handler.store,handler._principal(),value('id'),revision_id=value('revision'),
        base_hash=value('base_hash'),expected_review=value('expected_review'),expected_draft=value('expected_draft'),
        action=action,comment=value('comment'),acknowledge_same=autosave)
    if autosave:
        row=drafts.get(handler.store,handler._principal(),ident)
        return handler._send(json.dumps({'id':ident,'created_at':row['created_at'],'resume':link(ident,queue)}),
                             ctype='application/json; charset=utf-8')
    return handler._redirect(link(ident,queue))
