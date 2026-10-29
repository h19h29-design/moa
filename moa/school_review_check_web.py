"""Pinned HTML, changes and remaining warnings before staff confirmation."""
from urllib.parse import urlencode

from . import school_review_check as check
from . import school_warning_web as warnings_ui
from .school_saved_queue_web import query
from .school_export_web import link as export_link
from .school_review_compare import link as compare_link
from .school_compare_web import STYLE
from .school_review_web import STATUS, _hidden
from .web import esc

REASONS={
    'read_only':'조회 권한으로는 확인 완료를 저장할 수 없습니다.',
    'migration_required':'학교 검수 기능의 명시적 마이그레이션이 필요합니다.',
    'version_changed':'다른 수신본·변환본이 있거나 변환이 진행 중/실패 상태입니다. 최신 검수 화면을 다시 여세요.',
    'review_changed':'이후 다른 정식 검수가 저장됐습니다. 최신 검수본을 다시 확인하세요.',
    'already_confirmed':'이 검수본은 이미 담당자 확인을 마쳤습니다.',
    'no_blocks':'확인할 문서 구역이 없습니다.',
    'invalid_relations':'수정한 구역과 맞지 않는 관계가 있습니다. 검수 화면에서 관계를 수정·제거한 뒤 확인하세요.',
}


def link(ident, revision, event):
    return '/structure-confirm?'+urlencode({'id':ident,'revision':revision,'event':event})


def render(handler, raw):
    q=query(raw,{'id','revision','event'})
    if set(q)!={'id','revision','event'}: raise ValueError('Explicit review pins required')
    ident,revision,event=(q[k][0] for k in ('id','revision','event'))
    data=check.preview(handler.store,handler._principal(),ident,revision_id=revision,event_id=event)
    v=data['view']; a=v['analysis']; saved=data['saved']; s=data['summary']; e=v['latest']
    html_url=export_link('/review-html',ident,revision,event)
    content=STYLE+'<section class="comparison" id="review-final-check"><h1>'+('담당자 확인 기록' if v['status']=='confirmed' else '확인 완료 전 최종 검토')+'</h1>'
    content+='<p>'+esc(saved['title'])+' · 정식 검수 #'+str(e['sequence'])+' · <strong>'+esc(STATUS[v['status']])+'</strong> · 비공개</p>'
    content+='<p>작성자 '+esc(e['actor'])+' · '+esc(e['created_at'])+'</p><p class="compare-note">아래 요약과 HTML은 선택한 정식 검수본에 고정됩니다. 개인 임시 입력과 다른 탭의 미저장 입력은 포함되지 않습니다. 확인 후에도 원래의 누락·미확정 경고를 보존합니다.</p>'
    content+='<div class="compare-grid"><section class="card" data-check-summary><h2>자동 분석본 대비 변경</h2>'
    content+='<p>구역 수정 '+str(s['changed_blocks'])+'개 · 관계 추가 '+str(s['added_relations'])+'개 / 제거 '+str(s['removed_relations'])+'개 / 근거 변경 '+str(s['modified_relations'])+'개</p>'
    content+='<a href="'+esc(compare_link(ident,revision,'original',event))+'">변경 항목 자세히 비교</a><h3>이 검수본 저장 사유</h3><pre>'+esc(e['comment'])+'</pre></section>'
    content+='<section class="card" data-check-scope><h2>확인 범위</h2><p>파일 '+str(len(a['files']))+'개 · 구역 '+str(len(a['blocks']))+'개 · 관계 '+str(len(a['relations']))+'개</p>'
    content+='<p>자동 분석 완전성: '+('완전' if a['complete'] else '일부 미확정·누락 있음')+' · 경고 '+str(len(a['warnings']))+'개</p><ul>'
    for f in a['files']: content+='<li>'+esc(f['filename'])+' · '+esc(f['role'])+' · '+esc(f['status'])+'</li>'
    content+='</ul></section></div><section class="card" data-check-warnings><h2>보존되는 경고·미확정 범위</h2>'
    content+=warnings_ui.render(v['original'],ident)
    content+='</section><section class="card"><h2>이 정식 검수본의 HTML</h2><p><a href="'+esc(html_url)+'">HTML 크게 보기</a> · <a href="'+esc(html_url)+'&amp;download=1">HTML 다운로드</a> · <a href="'+esc(export_link('/review-bundle',ident,revision,event))+'">HTML·원본 ZIP 다운로드</a></p>'
    content+='<iframe title="최종 검토할 정식 수정본 HTML" sandbox src="'+esc(html_url)+'" style="height:60vh"></iframe></section>'
    if data['can_confirm']:
        fields={'id':ident,'revision':revision,'event':event,'base_hash':v['base_hash']}
        content+='<p id="queue-unsaved" role="status" hidden>저장하지 않은 확인 사유가 있습니다.</p><script src="/static/school-queue.js" defer></script>'
        content+='<form method="post" action="/structure-confirm" data-final-confirm data-queue-review>'+handler._csrf_field()+''.join(_hidden(k,x) for k,x in fields.items())
        content+='<label><input type="checkbox" name="acknowledge" value="yes" required> 이 정식 검수본의 HTML·파일·구역·관계와 미확정 범위를 확인했습니다.</label>'
        content+='<label>확인 사유 <textarea name="comment" maxlength="2000" required></textarea></label><p>이 확인은 문서 구조 검수 기록입니다. 문서 전체의 완전성 보증이나 공개 승인은 아닙니다.</p><button>이 정식 검수본 확인 완료</button></form>'
    else:
        content+='<section class="card" role="status"><h2>확인 상태 안내</h2><ul>'
        for reason in data['reasons']: content+='<li data-final-reason="'+reason+'">'+REASONS[reason]+'</li>'
        content+='</ul></section>'
    content+='<p><a href="'+esc('/structure-review?'+urlencode({'id':ident,'revision':revision}))+'">최신 검수 화면으로</a> · <a href="'+esc('/structure-history?'+urlencode({'id':ident,'revision':revision}))+'">검수 이력</a> · <a href="/structure-drafts">내 임시 저장 입력</a></p></section>'
    if v['status']=='confirmed': content+='<p><a data-confirmed-export href="/confirmed-export">확인 완료본 선택·묶음 다운로드</a></p>'
    return handler._send(handler._shell(content))


def submit(handler, form):
    fields={'csrf','id','revision','event','base_hash','acknowledge','comment'}
    if set(form)!=fields or any(len(v)!=1 for v in form.values()) or form['acknowledge'][0]!='yes':
        raise ValueError('Explicit final confirmation fields required')
    field=lambda k:form[k][0]
    confirmed=check.confirm(handler.store,handler._principal(),field('id'),revision_id=field('revision'),
        event_id=field('event'),base_hash=field('base_hash'),comment=field('comment'),acknowledge=True)
    return handler._redirect(link(field('id'),field('revision'),confirmed))
