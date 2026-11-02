"""Explicit owner-private display-name changes and immutable history."""
import re
from urllib.parse import urlencode
from . import school_warning_search_names as names, school_warning_search_management as management
from . import school_service as svc, school_review as review
from .school_warning_searches_web import PATH, directory_link
from .school_saved_queue_web import query, number
from .school_review_web import _hidden
from .school_compare_web import STYLE
from .web import esc


def link(ident,*,page=1,q='',state='active',list_page=1,hq='',hf='',ht=''):
    fields={'id':ident,**({'page':page} if page!=1 else {}),**({'q':q} if q else {}),
        **({'state':state} if state!='active' else {}),**({'list_page':list_page} if list_page!=1 else {}),
        **({'hq':hq} if hq else {}),**({'hf':hf} if hf else {}),**({'ht':ht} if ht else {})}
    return PATH+'/name?'+urlencode(fields)


def context(values):
    selected=management.filters(q=values.get('q',[''])[0],state=values.get('state',['active'])[0])
    history=names.filters(q=values.get('hq',[''])[0],start=values.get('hf',[''])[0],end=values.get('ht',[''])[0])
    return dict(selected,list_page=number(values.get('list_page',['1'])[0],1_000_000),
        hq=history['q'],hf=history['start'],ht=history['end'])


def render(handler,raw,*,status=200,warning=''):
    values=query(raw,{'id','page','q','state','list_page','hq','hf','ht'})
    if 'id' not in values:raise ValueError('Saved warning condition required')
    back=context(values);p=handler._principal()
    history=names.filters(q=back['hq'],start=back['hf'],end=back['ht'])
    row=names.get(handler.store,p,values['id'][0],page=number(values.get('page',['1'])[0],1_000_000),**history)
    writable=not (row['name_required'] or row['management_required']) and svc.require_member(handler.store,p)['role'] in svc.WRITE_ROLES
    from .school_warning_name_restore_web import link as restore_link
    content=STYLE+'<section class="comparison" id="warning-search-name"><h1>경고 검색 조건 이름 관리</h1>'
    if warning:content+='<p role="alert" data-warning-name-conflict>'+esc(warning)+'</p>'
    content+='<h2 data-warning-current-name>'+esc(row['name'])+'</h2><p data-warning-original-name>처음 저장한 이름: '+esc(row['original_name'])+'</p>'
    if writable and row['name']!=row['original_name']:
        content+='<p><a data-warning-name-original-restore href="'+esc(restore_link(row['id'],source_version=0,history_page=row['page'],**back))+'">처음 저장한 이름으로 복원</a></p>'
    content+='<p>표시 이름만 변경합니다. 처음 이름과 이전 이름은 이력에 보존하며 검색 조건·문서·변환본·보관 상태·원본·검수 이력·영수증을 변경하지 않습니다.</p>'
    content+='<p>문서: '+esc(row['title'])+' · '+('보관함' if row['state']=='archived' else '사용 중')+' · 이름 기록 #'+str(row['name_version'])+'</p>'
    if row['name_required'] or row['management_required']:
        content+='<p data-warning-name-required>이름 변경에는 명시적 마이그레이션이 필요합니다. 이 화면은 DB를 변경하지 않습니다.</p>'
    elif writable:
        content+='<form method="post" action="'+PATH+'/rename" data-warning-name-form>'+handler._csrf_field()
        for k,v in dict(id=row['id'],expected=row['name_version'],expected_state=row['version'],**back).items():content+=_hidden(k,str(v))
        content+='<label>새 표시 이름<input name="name" maxlength="120" required value="'+esc(row['name'])+'"></label>'
        content+='<label><input type="checkbox" name="acknowledge" value="yes" required> 검색 조건은 유지하고 표시 이름만 변경합니다.</label>'
        content+='<button>표시 이름 변경</button></form><p>현재와 다른 이름을 입력하세요. 이전 이름으로 돌아갈 때도 새 기록으로 남습니다.</p>'
    else:content+='<p>현재 계정은 읽기 전용입니다. 이름 변경은 변경 권한이 있는 담당자만 할 수 있습니다.</p>'
    filtered=bool(row['history']['q'] or row['history']['start'] or row['history']['end'])
    if not row['name_required']:
        content+='<h2>이름 이력 검색</h2><form method="get" action="'+PATH+'/name" data-warning-name-filter>'
        for k,v in dict(id=row['id'],q=back['q'],state=back['state'],list_page=back['list_page']).items():content+=_hidden(k,str(v))
        content+='<label>이름<input name="hq" maxlength="120" value="'+esc(row['history']['q'])+'"></label>'
        content+='<label>시작일<input type="date" name="hf" value="'+esc(row['history']['start'])+'"></label>'
        content+='<label>종료일<input type="date" name="ht" value="'+esc(row['history']['end'])+'"></label>'
        content+='<button>이력 검색</button></form>'
        content+='<p>이름·기간 조건은 이 화면의 이력만 골라 봅니다. 저장 조건·문서·변환본·보관 상태·원본·검수 이력·영수증은 그대로 유지합니다.</p>'
    content+='<h2>이름 변경 이력</h2><p data-warning-name-counts>이름 기록 '+str(row['total'])+'개'+((' · 전체 '+str(row['all_total'])+'개') if filtered else '')+'</p>'
    if filtered and not row['events']:
        content+='<p data-warning-name-empty>조건에 맞는 이름 기록이 없습니다. <a data-warning-name-filter-clear href="'+esc(link(row['id'],q=back['q'],state=back['state'],list_page=back['list_page']))+'">이력 검색 지우기</a></p>'
    content+='<ol data-warning-name-history>'
    for e in row['events']:
        content+='<li data-warning-name-event="'+str(e['version'])+'">#'+str(e['version'])+' · '+esc(e['name'])+' · '+esc(e['created_at'])
        if writable and e['name']!=row['name']:
            content+=' · <a data-warning-name-restore-open href="'+esc(restore_link(row['id'],source_version=e['version'],history_page=row['page'],**back))+'">이 이름으로 복원</a>'
        content+='</li>'
    content+='</ol><nav aria-label="경고 검색 조건 이름 이력 페이지">'+str(row['page'])+' / '+str(row['pages'])+'페이지'
    for n,label in ((row['page']-1,'이전 이름 이력'),(row['page']+1,'다음 이름 이력')):
        if 1<=n<=row['pages']:content+=' · <a href="'+esc(link(row['id'],page=n,**back))+'">'+label+'</a>'
    content+='</nav><p><a data-warning-name-back href="'+esc(directory_link(q=back['q'],state=back['state'],page=back['list_page']))+'">검색한 조건 목록으로</a>'
    content+=' · <a data-warning-search-open href="'+PATH+'/open?'+esc(urlencode({'id':row['id']}))+'">경고 검색 조건 불러오기</a></p></section>'
    return handler._send(handler._shell(content),status)


def dispatch(handler,*,raw=None,form=None):
    if form is None:return render(handler,raw)
    fields={'csrf','id','expected','expected_state','name','acknowledge','q','state','list_page','hq','hf','ht'}
    if (set(form)!=fields or any(len(v)!=1 for v in form.values()) or form['acknowledge'][0]!='yes'
            or any(not re.fullmatch(r'0|[1-9][0-9]{0,9}',form[k][0]) for k in ('expected','expected_state'))):
        raise ValueError('Explicit warning name change required')
    back=context(form);ident=form['id'][0]
    try:names.rename(handler.store,handler._principal(),ident,name=form['name'][0],
        expected=int(form['expected'][0]),expected_state=int(form['expected_state'][0]))
    except review.Conflict:
        return render(handler,urlencode(dict(id=ident,**back)),status=409,
            warning='이름·보관 상태가 바뀌었거나 같은 이름을 제출했습니다. 이름 변경 기능의 설치 여부와 최신 이력을 확인하고 다시 선택하세요.')
    return handler._redirect(link(ident,**back))
