"""Explicit archive/restore confirmation and immutable private history."""
import re
from urllib.parse import urlencode

from . import school_warning_search_management as management, school_service as svc, school_review as review
from .school_warning_searches_web import PATH, directory_link
from .school_saved_queue_web import query, number
from .school_review_web import _hidden
from .school_compare_web import STYLE
from .web import esc


def link(ident,*,page=1,q='',state='active',list_page=1):
    fields={'id':ident,**({'page':page} if page!=1 else {}),**({'q':q} if q else {}),
        **({'state':state} if state!='active' else {}),**({'list_page':list_page} if list_page!=1 else {})}
    return PATH+'/manage?'+urlencode(fields)


def context(values):
    selected=management.filters(q=values.get('q',[''])[0],state=values.get('state',['active'])[0])
    return dict(selected,list_page=number(values.get('list_page',['1'])[0],1_000_000))


def render(handler,raw,*,status=200,warning=''):
    values=query(raw,{'id','page','q','state','list_page'})
    if 'id' not in values: raise ValueError('Saved warning condition required')
    back=context(values);p=handler._principal()
    row=management.get(handler.store,p,values['id'][0],page=number(values.get('page',['1'])[0],1_000_000))
    content=STYLE+'<section class="comparison" id="warning-search-management"><h1>경고 검색 조건 보관·복원</h1><h2>'+esc(row['name'])+'</h2>'
    if warning: content+='<p role="alert" data-warning-management-conflict>'+esc(warning)+'</p>'
    content+='<p data-warning-management-state>'+('보관함' if row['state']=='archived' else '사용 중')+' · 상태 기록 #'+str(row['version'])+'</p>'
    content+='<p>보관은 사용 중인 목록에서만 숨깁니다. 이름·조건·문서·변환 버전은 그대로 유지하며 보관함에서도 불러올 수 있습니다. 원본·검수 이력·영수증을 변경하지 않습니다.</p>'
    content+='<p>문서: '+esc(row['title'])+' · 처음 저장: '+esc(row['created_at'])+'</p>'
    content+='<p>검색어: '+esc(row['filters']['q'] or '전체')+' · 파일: '+esc(row['file_label'])+' · 유형: '+esc(row['code_label'])+'</p>'
    content+='<p class="compare-meta">고정 변환 버전: <code>'+esc(row['conversion_revision'])+'</code></p>'
    if row['management_required']:
        content+='<p data-warning-management-required>보관·복원에는 명시적 마이그레이션이 필요합니다. 이 화면은 DB를 변경하지 않습니다.</p>'
    elif svc.require_member(handler.store,p)['role'] in svc.WRITE_ROLES:
        action='restore' if row['state']=='archived' else 'archive'
        content+='<form method="post" action="'+PATH+'/'+action+'" data-warning-management>'+handler._csrf_field()
        for name,value in dict(id=row['id'],expected=row['version'],**back).items(): content+=_hidden(name,str(value))
        content+='<label><input type="checkbox" name="acknowledge" value="yes" required> 검색 조건을 삭제하지 않고 표시 상태만 변경합니다.</label>'
        content+='<button>'+('사용 중인 목록으로 복원' if action=='restore' else '보관함으로 옮기기')+'</button></form>'
    else: content+='<p>현재 계정은 읽기 전용입니다. 보관·복원은 변경 권한이 있는 담당자만 할 수 있습니다.</p>'
    content+='<h2>보관·복원 이력</h2><p>상태 기록 '+str(row['total'])+'개</p><ol data-warning-management-history>'
    for event in row['events']:
        content+='<li>#'+str(event['version'])+' · '+('보관함으로 이동' if event['action']=='archive' else '사용 중인 목록으로 복원')+' · '+esc(event['created_at'])+'</li>'
    content+='</ol><nav aria-label="경고 검색 조건 이력 페이지">'+str(row['page'])+' / '+str(row['pages'])+'페이지'
    for page,label in ((row['page']-1,'이전 상태 이력'),(row['page']+1,'다음 상태 이력')):
        if 1<=page<=row['pages']: content+=' · <a href="'+esc(link(row['id'],page=page,**back))+'">'+label+'</a>'
    content+='</nav><p><a data-warning-search-open href="'+PATH+'/open?'+esc(urlencode({'id':row['id']}))+'">경고 검색 조건 불러오기</a>'
    content+=' · <a data-warning-management-back href="'+esc(directory_link(q=back['q'],state=back['state'],page=back['list_page']))+'">검색한 조건 목록으로</a></p></section>'
    return handler._send(handler._shell(content),status)


def dispatch(handler,path,*,raw=None,form=None):
    if form is None: return render(handler,raw)
    if (set(form)!={'csrf','id','expected','acknowledge','q','state','list_page'} or any(len(v)!=1 for v in form.values())
            or form['acknowledge'][0]!='yes' or not re.fullmatch(r'0|[1-9][0-9]{0,9}',form['expected'][0])):
        raise ValueError('Explicit warning search transition required')
    expected=int(form['expected'][0]);back=context(form);ident=form['id'][0]
    try:
        management.change(handler.store,handler._principal(),ident,action='archive' if path.endswith('/archive') else 'restore',expected=expected)
    except review.Conflict:
        return render(handler,urlencode(dict(id=ident,**back)),status=409,
            warning='화면을 연 뒤 보관 상태가 바뀌었거나 보관 기능이 설치되지 않았습니다. 최신 상태·이력을 확인한 뒤 다시 선택하세요.')
    return handler._redirect(link(ident,**back))
