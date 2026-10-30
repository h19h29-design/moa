"""Restore a selected immutable name through a fresh explicit confirmation."""
import re
from urllib.parse import urlencode
from . import school_warning_search_names as names, school_service as svc, school_review as review
from .school_warning_search_names_web import PATH, link as history_link
from .school_warning_search_management_web import context
from .school_saved_queue_web import query, number
from .school_review_web import _hidden
from .school_compare_web import STYLE
from .web import esc

ROUTE=PATH+'/name-restore'


def link(ident,*,source_version,history_page=1,q='',state='active',list_page=1):
    return ROUTE+'?'+urlencode(dict(id=ident,source_version=source_version,history_page=history_page,
        q=q,state=state,list_page=list_page))


def render(handler,raw,*,status=200,warning=''):
    values=query(raw,{'id','source_version','history_page','q','state','list_page'})
    if not {'id','source_version'}<=set(values):raise ValueError('Select a saved warning name')
    if not re.fullmatch(r'0|[1-9][0-9]{0,9}',values['source_version'][0]):raise ValueError('Invalid name source')
    source=int(values['source_version'][0]);back=context(values)
    page=number(values.get('history_page',['1'])[0],1_000_000);p=handler._principal()
    row=names.selection(handler.store,p,values['id'][0],source_version=source)
    content=STYLE+'<section class="comparison" id="warning-name-restore"><h1>이전 이름 복원 확인</h1>'
    if warning:content+='<p role="alert" data-warning-name-restore-conflict>'+esc(warning)+'</p>'
    content+='<h2>현재 표시 이름</h2><p data-warning-name-restore-current>'+esc(row['name'])+'</p>'
    content+='<h2>복원할 이름</h2><p data-warning-name-restore-selected>'+esc(row['source_name'])+'</p>'
    content+='<p>'+('처음 저장한 이름' if source==0 else '이름 기록 #'+str(source))+' · '+esc(row['source_created_at'])+'</p>'
    content+='<p>문서: '+esc(row['title'])+' · '+('보관함' if row['state']=='archived' else '사용 중')+'</p>'
    content+='<p>검색어: '+esc(row['filters']['q'] or '전체')+' · 파일: '+esc(row['file_label'])+' · 유형: '+esc(row['code_label'])+'</p>'
    content+='<p>선택한 이름을 새 이름 기록으로 추가합니다. 기존 이름 이력·검색 조건·문서·변환본·보관 상태·원본·검수 이력·영수증은 보존합니다.</p>'
    if row['source_name']==row['name']:
        content+='<p data-warning-name-restore-same>이미 현재 이름입니다. 새 기록을 추가하지 않습니다.</p>'
    elif svc.require_member(handler.store,p)['role'] in svc.WRITE_ROLES:
        content+='<form method="post" action="'+ROUTE+'" data-warning-name-restore-form>'+handler._csrf_field()
        for k,v in dict(id=row['id'],source_version=source,expected=row['name_version'],expected_state=row['version'],history_page=page,**back).items():
            content+=_hidden(k,str(v))
        content+='<label><input type="checkbox" name="acknowledge" value="yes" required> 선택한 이름만 복원하고 기존 기록과 검색 조건은 보존합니다.</label>'
        content+='<button>선택한 이름으로 복원</button></form>'
    else:content+='<p>현재 계정은 읽기 전용입니다. 이름 복원은 변경 권한이 있는 담당자만 할 수 있습니다.</p>'
    content+='<p><a data-warning-name-restore-cancel href="'+esc(history_link(row['id'],page=page,**back))+'">이름 이력으로 돌아가기</a></p></section>'
    return handler._send(handler._shell(content),status)


def dispatch(handler,*,raw=None,form=None):
    if form is None:return render(handler,raw)
    fields={'csrf','id','source_version','expected','expected_state','history_page','acknowledge','q','state','list_page'}
    if (set(form)!=fields or any(len(v)!=1 for v in form.values()) or form['acknowledge'][0]!='yes'
            or any(not re.fullmatch(r'0|[1-9][0-9]{0,9}',form[k][0]) for k in ('source_version','expected','expected_state'))):
        raise ValueError('Explicit warning name restore required')
    back=context(form);page=number(form['history_page'][0],1_000_000);ident=form['id'][0];source=int(form['source_version'][0])
    try:names.restore(handler.store,handler._principal(),ident,source_version=source,
        expected=int(form['expected'][0]),expected_state=int(form['expected_state'][0]))
    except review.Conflict:
        return render(handler,urlencode(dict(id=ident,source_version=source,history_page=page,**back)),status=409,
            warning='이름·보관 상태가 바뀌었거나 이미 같은 이름입니다. 최신 이름과 선택한 기록을 확인한 뒤 다시 선택하세요.')
    return handler._redirect(history_link(ident,**back))
