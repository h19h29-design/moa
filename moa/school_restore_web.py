"""Read-only restoration confirmation; POST uses the existing guarded writer."""
from urllib.parse import urlencode

from . import school_review as review, school_review_compare as compare
from .school_export_web import link as export_link
from .web import esc


def link(ident, revision, source):
    return '/structure-restore?'+urlencode({'id':ident,'revision':revision,'source':source})


def render(handler, query):
    from .school_review_web import STATUS, _form, _hidden, _reason
    if set(query) != {'id','revision','source'} or any(len(v)!=1 for v in query.values()):
        raise ValueError('Explicit restore pins required')
    ident, revision, source = (review._text(query[k][0],160,required=True) for k in ('id','revision','source'))
    principal = handler._principal()
    current = review.view(handler.store,principal,ident,revision_id=revision)
    target = review.view(handler.store,principal,ident,revision_id=revision,event_id=source)
    latest, selected = current['latest'], target['latest']
    if not latest or selected['sequence'] >= latest['sequence']:
        raise ValueError('A previous review event is required')
    comparison = compare.compare(handler.store,principal,ident,revision_id=revision,left=latest['id'],right=source)
    counts = comparison['summary']
    has_change = any(counts[k] for k in ('changed_blocks','added_relations','removed_relations','modified_relations'))
    can_restore = current['editable'] and (has_change or current['status']=='confirmed')
    content = ('<h1>이전 검수본 복원 확인</h1><p><strong>비공개 · 새 수정 이력 추가</strong></p>'
        '<p>같은 수신본·변환 버전의 구역과 관계를 선택한 검수 시점으로 되돌립니다. 원본·자동 분석·기존 검수 이력·과거 HTML은 그대로 보존합니다.</p>'
        '<p>과거의 확인 완료는 복원하지 않습니다. 저장 후 <strong>수정 중 · 확인 필요</strong> 상태에서 다시 검수하세요. 공개·배포·학습은 실행하지 않습니다.</p>'
        '<p style="overflow-wrap:anywhere">변환 버전 '+esc(revision)+'</p>')
    for title,event in (('현재 검수',latest),('복원할 검수',selected)):
        content += ('<section class="card"><h2>'+title+' #'+str(event['sequence'])+'</h2><p>'+esc(STATUS[event['status']])+': '
            +esc(event['actor'])+' · '+esc(event['created_at'])+'</p><p style="white-space:pre-wrap;overflow-wrap:anywhere">사유: '+esc(event['comment'])+'</p></section>')
    content += ('<p>변경될 구역 '+str(counts['changed_blocks'])+'개 · 관계 추가 '+str(counts['added_relations'])
        +'개 / 제거 '+str(counts['removed_relations'])+'개 / 근거 변경 '+str(counts['modified_relations'])+'개</p>'
        '<p><a href="'+esc(compare.link(ident,revision,latest['id'],source))+'">현재 검수와 복원 대상의 변경점 비교</a> · '
        '<a href="'+esc(export_link('/review-preview',ident,revision,source))+'">복원 대상의 당시 HTML 보기</a></p>')
    if can_restore:
        content += (_form(handler,ident,current,'restore')+_hidden('source_event',source)
            +'<p><label><input type="checkbox" name="acknowledge_restore" value="yes" required> 선택한 과거 내용으로 새 수정본을 만들며, 다시 검수가 필요함을 확인했습니다.</label></p>'
            +_reason('새 수정본으로 복원'))
    else:
        content += '<p role="status">복원할 수 없습니다: 읽기 전용·이전 수신/변환·변환 진행 중이거나, 이미 같은 내용의 미확인 수정본입니다.</p>'
    content += '<p><a href="'+esc('/structure-history?'+urlencode({'id':ident,'revision':revision}))+'">취소 · 검수 이력으로</a></p>'
    return handler._send(handler._shell(content))
