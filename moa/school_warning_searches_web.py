"""CSRF-protected save; owner-only directory and pinned read-only reload."""
import secrets
from urllib.parse import urlencode

from . import school_warning_searches as saved, school_warning_search_web as live
from . import school_service as svc, school_review as review
from .school_saved_queue_web import query, number
from .school_review_web import _hidden
from .school_compare_web import STYLE
from .web import esc

PATH = '/structure-warning-searches'


def save_form(handler, data):
    if not saved.available(handler.store):
        return '<p data-warning-search-required>검색 조건 저장에는 명시적 마이그레이션이 필요합니다. 경고 검색은 계속 사용할 수 있습니다.</p>'
    if svc.require_member(handler.store,handler._principal())['role'] not in svc.WRITE_ROLES: return ''
    content='<form method="post" action="'+PATH+'/save" data-warning-search-save>'+handler._csrf_field()
    content+=_hidden('id',secrets.token_urlsafe(18))+_hidden('notice',data['id'])+_hidden('revision',data['revision'])
    for name,value in data['filters'].items(): content+=_hidden(name,value)
    content+='<label>경고 검색 조건 이름<input name="name" maxlength="120" required></label><button>현재 경고 검색 조건 저장</button></form>'
    return content+'<p>검색 버튼으로 적용한 조건과 문서·변환 버전을 내 계정에 저장합니다. 입력 중인 조건은 먼저 검색하세요. 페이지 위치와 검색 결과 목록은 저장하지 않습니다.</p>'


def directory_link(*,q='',state='active',page=1):
    fields={**({'q':q} if q else {}),**({'state':state} if state!='active' else {}),**({'page':page} if page!=1 else {})}
    return PATH+('?' + urlencode(fields) if fields else '')


def directory(handler, raw):
    from . import school_warning_search_management_web as management_web
    values=query(raw,{'page','q','state'})
    data=saved.listing(handler.store,handler._principal(),q=values.get('q',[''])[0],state=values.get('state',['active'])[0],
        page=number(values.get('page',['1'])[0],1_000_000))
    selected=data['filters']
    content=STYLE+'<section class="comparison" id="warning-searches"><h1>저장한 경고 검색 조건</h1>'
    content+='<p>내 학교·내 계정에만 보이며 재로그인 후에도 불러올 수 있습니다. 저장한 문서·변환 버전에서 같은 조건으로 다시 검색하고 첫 페이지를 엽니다. 최신 변환본으로 바꾸지 않습니다.</p>'
    content+='<form method="get" action="'+PATH+'" data-warning-saved-filters><label>저장 조건 검색<input name="q" type="search" maxlength="120" value="'+esc(selected['q'])+'"></label>'
    content+='<label>표시 상태<select aria-label="표시 상태" name="state">'
    for value,label in (('active','사용 중'),('archived','보관함'),('all','전체')):
        content+='<option value="'+value+'"'+(' selected' if value==selected['state'] else '')+'>'+label+'</option>'
    content+='</select></label><button>저장 조건 검색</button> · <a data-warning-saved-reset href="'+PATH+'">검색 초기화</a></form>'
    content+='<p>이름·문서 제목·저장한 검색어·파일·경고 유형을 글자 그대로 검색합니다. 대소문자는 구분하지 않습니다. 보관은 기본 목록에서만 숨기며 원래 조건과 기록은 보존합니다.</p>'
    content+='<nav aria-label="저장 조건 표시 상태">'
    for value,label in (('active','사용 중인 조건'),('archived','보관한 조건'),('all','모든 조건')):
        content+='<a href="'+esc(directory_link(q=selected['q'],state=value))+'">'+label+'</a> · '
    content+='</nav>'
    if data['migration_required']:
        content+='<p data-warning-search-required>조건 저장 기능의 명시적 마이그레이션이 필요합니다. 이 화면은 DB를 변경하지 않습니다.</p>'
    else:
        if data['management_required']:
            content+='<p data-warning-management-required>보관·복원에는 명시적 마이그레이션이 필요합니다. 기존 조건 검색·불러오기는 계속 사용할 수 있습니다.</p>'
        if not data['rows']:
            content+='<p data-warning-search-empty>표시 조건에 맞는 저장한 경고 검색 조건이 없습니다. 검색어·보관함을 확인하거나 통신문에서 새 조건을 저장하세요.</p>'
    for row in data['rows']:
        content+='<article class="card" data-warning-search-card><h2>'+esc(row['name'])+'</h2>'
        content+='<p>문서: '+esc(row['title'])+' · 조건 저장: '+esc(row['created_at'])+'</p>'
        content+='<p data-warning-saved-state>'+('보관함' if row['state']=='archived' else '사용 중')+' · 상태 기록 #'+str(row['version'])+'</p>'
        content+='<p>검색어: '+esc(row['filters']['q'] or '전체')+' · 파일: '+esc(row['file_label'])+' · 유형: '+esc(row['code_label'])+'</p>'
        content+='<p class="compare-meta">저장 당시 변환 버전: <code>'+esc(row['conversion_revision'])+'</code></p>'
        content+='<a data-warning-search-open href="'+PATH+'/open?'+esc(urlencode({'id':row['id']}))+'">경고 검색 조건 불러오기</a>'
        content+=' · <a data-warning-management-open href="'+esc(management_web.link(row['id'],**selected,list_page=data['page']))+'">보관·복원과 이력</a></article>'
    content+='<p>파일·유형 이름은 저장 당시 표시입니다. 원래 경고·검수·영수증을 수정하거나 경고를 해결 처리하지 않습니다. 저장한 이름과 조건은 덮어쓰지 않으며 다른 조건은 새 이름으로 저장할 수 있습니다.</p>'
    content+='<nav aria-label="저장한 경고 검색 조건 페이지">'+str(data['page'])+' / '+str(data['pages'])+'페이지 · 총 '+str(data['total'])+'개'
    for page,label in ((data['page']-1,'이전 저장 조건'),(data['page']+1,'다음 저장 조건')):
        if 1<=page<=data['pages']: content+=' · <a href="'+esc(directory_link(**selected,page=page))+'">'+label+'</a>'
    content+='</nav><p><a href="/">학교 통신문 목록</a></p></section>'
    return handler._send(handler._shell(content))


def dispatch(handler, path, *, raw=None, form=None):
    try:
        if form is not None:
            if set(form)!={'csrf','id','name','notice','revision','q','file','code'} or any(len(v)!=1 for v in form.values()):
                raise ValueError('Invalid warning search fields')
            saved.create(handler.store,handler._principal(),form['id'][0],
                **{k:form[k][0] for k in ('name','notice','revision','q','file','code')})
            return handler._redirect(PATH)
        if path==PATH: return directory(handler,raw)
        values=query(raw,{'id'})
        if set(values)!={'id'}: raise ValueError('Saved warning search required')
        row=saved.resolve(handler.store,handler._principal(),values['id'][0])
        return handler._redirect(live.link(row['notice_version'],row['conversion_revision'],**row['filters']))
    except review.Conflict:
        content=STYLE+'<section class="comparison"><h1>저장한 경고 검색 조건을 확인하세요</h1>'
        content+='<p>조건 저장 기능의 명시적 마이그레이션이 필요하거나 이미 저장한 조건과 요청이 다릅니다. 기존 조건은 덮어쓰지 않았습니다.</p>'
        return handler._send(handler._shell(content+'<a href="'+PATH+'">저장한 경고 검색 조건</a></section>'),409)
