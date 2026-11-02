"""Explicit private archive/restore forms; no JavaScript or document writes."""
import re
from urllib.parse import urlencode

from . import school_confirmed_export_downloads as downloads
from . import school_confirmed_export_management as management, school_service as svc
from . import school_confirmed_export_web as web, school_review as review
from .school_saved_queue_web import query, number
from .school_review_web import _hidden
from .web import esc


def link(ident, *, page=1):
    return '/confirmed-export/saved/manage?'+urlencode({'id':ident,**({'page':page} if page!=1 else {})})


def render(handler, raw, *, status=200, warning=''):
    from .school_confirmed_export_saved_web import link as preview_link
    q=query(raw,{'id','page'})
    if 'id' not in q: raise ValueError('Saved export list required')
    principal=handler._principal()
    row=management.get(handler.store,principal,q['id'][0],page=number(q.get('page',['1'])[0],1_000_000))
    content=web.STYLE+'<section class="comparison" id="saved-export-management"><h1>저장 묶음 보관·복원</h1>'
    content+='<h2 data-export-management-name>'+esc(row['name'])+'</h2>'
    if warning: content+='<p role="alert" data-export-management-conflict>'+esc(warning)+'</p>'
    content+='<p data-export-management-state>'+('보관함' if row['state']=='archived' else '사용 중')+' · 상태 기록 #'+str(row['version'])+' · '+str(row['item_count'])+'건</p>'
    content+='<p>보관은 기본 목록에서만 숨깁니다. 목록 이름·문서 순서·고정 버전·원본·검수 이력은 그대로 보존합니다. 보관함에서도 고정본 확인·다운로드가 가능하며, 버전 변경·원본 무결성 검사를 동일하게 적용합니다.</p>'
    if row['management_required']:
        content+='<p>보관·복원에는 명시적 마이그레이션이 필요합니다. 이 화면은 DB를 변경하지 않습니다.</p>'
    elif svc.require_member(handler.store,principal)['role'] in svc.WRITE_ROLES:
        action='restore' if row['state']=='archived' else 'archive'
        label='기본 목록으로 복원' if action=='restore' else '보관함으로 옮기기'
        content+='<form method="post" action="/confirmed-export/saved/'+action+'" data-export-management>'+handler._csrf_field()
        content+=_hidden('id',row['id'])+_hidden('expected',str(row['version']))
        content+='<label><input type="checkbox" name="acknowledge" value="yes" required> 원본·고정 목록을 유지한 채 표시 상태를 변경합니다.</label><button>'+label+'</button></form>'
    else: content+='<p>현재 계정은 읽기 전용입니다. 보관·복원은 변경 권한이 있는 담당자만 할 수 있습니다.</p>'
    from .school_confirmed_export_downloads_web import link as receipt_link
    receipts=downloads.listing(handler.store,principal,row['id'])
    content+='<h2>다운로드 영수증</h2>'
    if receipts['receipt_required']:
        content+='<p>다운로드 영수증 기능의 명시적 마이그레이션이 필요합니다. 기존 다운로드는 계속 동작합니다.</p>'
    else:
        last=receipts['rows'][0] if receipts['rows'] else None
        content+=('<p data-export-receipts-summary>'+('아직 내려받지 않음' if not receipts['total'] else
            '다운로드 '+str(receipts['total'])+'회 · 마지막 '+esc(last['created_at']))+'</p>')
        content+='<p><a data-export-receipts href="'+esc(receipt_link(row['id']))+'">다운로드 영수증 전체 보기</a></p>'
    content+='<h2>보관·복원 이력</h2><p>처음 저장 '+esc(row['created_at'])+' · 보관·복원 기록 '+str(row['total'])+'개</p><ol data-export-management-history>'
    for event in row['events']:
        content+='<li>#'+str(event['version'])+' · '+('보관함으로 이동' if event['action']=='archive' else '기본 목록으로 복원')+' · '+esc(event['created_at'])+'</li>'
    content+='</ol><nav aria-label="보관·복원 이력 페이지">'+str(row['page'])+' / '+str(row['pages'])+'페이지'
    for page,label in ((row['page']-1,'이전 이력'),(row['page']+1,'다음 이력')):
        if 1<=page<=row['pages']: content+=' · <a href="'+esc(link(row['id'],page=page))+'">'+label+'</a>'
    content+='</nav><p><a data-export-saved-resume href="'+esc(preview_link(row['id']))+'">고정 묶음 확인·다운로드</a>'
    content+=' · <a href="/confirmed-export/saved">사용 중인 목록</a> · <a href="/confirmed-export/saved?state=archived">보관함</a></p></section>'
    return handler._send(handler._shell(content),status)


def dispatch(handler, path, *, raw=None, form=None):
    if form is None: return render(handler,raw)
    if (set(form)!={'csrf','id','expected','acknowledge'} or any(len(v)!=1 for v in form.values())
            or form['acknowledge'][0]!='yes' or not re.fullmatch(r'0|[1-9][0-9]{0,9}',form['expected'][0])):
        raise ValueError('Explicit saved export transition required')
    expected=int(form['expected'][0])
    if expected>management.MAX_VERSION: raise ValueError('Invalid saved export version')
    ident=form['id'][0]
    try:
        management.change(handler.store,handler._principal(),ident,
            action='archive' if path.endswith('/archive') else 'restore',expected=expected)
    except review.Conflict:
        return render(handler,urlencode({'id':ident}),status=409,
            warning='화면을 연 뒤 보관 상태가 바뀌었거나 보관 기능이 설치되지 않았습니다. 최신 상태·이력을 확인한 뒤 다시 선택하세요.')
    return handler._redirect(link(ident))
