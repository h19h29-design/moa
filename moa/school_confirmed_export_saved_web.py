"""Persistent owner-only lists; every resume/download rechecks the frozen pins."""
from urllib.parse import urlencode

from . import school_confirmed_export as batch, school_confirmed_export_saved as saved
from . import school_confirmed_export_web as web, school_review as review
from .school_saved_queue_web import query, number
from .school_review_web import _hidden
from .web import esc


def link(ident):
    return '/confirmed-export/saved/preview?'+urlencode({'id':ident})


def save_form(handler, key):
    from . import school_service as svc
    if svc.require_member(handler.store,handler._principal())['role'] not in svc.WRITE_ROLES: return ''
    if not saved.available(handler.store):
        return '<p>목록 저장에는 명시적 마이그레이션이 필요합니다. 현재 준비 목록은 로그인 중에만 유지됩니다.</p>'
    try: saved.get(handler.store,handler._principal(),key)
    except LookupError: pass
    else: return '<p><a data-export-saved-open href="'+esc(link(key))+'">이미 저장한 묶음 목록 열기</a></p>'
    return ('<form action="/confirmed-export/save" method="post" data-export-save>'+handler._csrf_field()+
        _hidden('bundle',key)+'<label>묶음 목록 이름<input name="name" maxlength="120" value="확인 완료본 묶음" required></label>'
        '<button>목록 저장·나중에 다운로드</button></form><p>문서 순서·확인 버전을 내 계정에 저장합니다. '
        '재로그인 후에도 열 수 있으며, 저장만으로 다운로드하거나 공개하지 않습니다.</p>')


def directory(handler, raw):
    q=query(raw,{'page'})
    data=saved.listing(handler.store,handler._principal(),page=number(q.get('page',['1'])[0],1_000_000))
    content=web.STYLE+'<section class="comparison" id="saved-confirmed-exports"><h1>저장한 확인 완료본 묶음</h1>'
    content+='<p>내 학교·내 담당자 계정에만 보이는 비공개 목록입니다. 재로그인·서버 재시작 후에도 이어서 다운로드할 수 있습니다.</p>'
    content+='<p>저장 시점의 문서 순서와 확인 버전을 유지합니다. 열기·다운로드 때 다시 검사하며, 달라진 버전으로 자동 대체하지 않습니다.</p>'
    if data['migration_required']: content+='<p>목록 저장 기능의 명시적 마이그레이션이 필요합니다. 이 화면은 DB를 변경하지 않습니다.</p>'
    elif not data['rows']: content+='<p>저장한 묶음이 없습니다. 확인 완료본을 선택·준비한 뒤 목록을 저장하세요.</p>'
    for row in data['rows']:
        content+='<article class="card"><h2>'+esc(row['name'])+'</h2><p>'+str(row['item_count'])+'건 · 목록 저장 '+esc(row['created_at'])+'</p>'
        content+='<a data-export-saved-resume href="'+esc(link(row['id']))+'">묶음 확인·이어서 다운로드</a></article>'
    content+='<nav aria-label="저장한 묶음 페이지">'+str(data['page'])+' / '+str(data['pages'])+'페이지 · 총 '+str(data['total'])+'개'
    for page,label in ((data['page']-1,'이전 목록'),(data['page']+1,'다음 목록')):
        if 1<=page<=data['pages']: content+=' · <a href="/confirmed-export/saved?page='+str(page)+'">'+label+'</a>'
    return handler._send(handler._shell(content+'</nav><p><a href="/confirmed-export">새 확인 완료본 선택</a> · <a href="/#review-overview">학교 현황으로</a></p></section>'))


def _blocked(handler, row):
    content=web.STYLE+'<section class="comparison" id="saved-export-blocked"><h1>저장한 묶음 다운로드가 차단됐습니다</h1>'
    content+='<h2>'+esc(row['name'])+'</h2><p>저장 뒤 수신·변환·검수 버전 또는 원본 무결성이 달라졌습니다. '
    content+='일부 문서만 내려받거나 최신본으로 바꾸지 않습니다. 저장 목록·원본·이전 검수 이력은 보존됩니다.</p><ul>'
    content+=''.join('<li>'+esc(item['title'])+'</li>' for item in row['items'])
    content+='</ul><p>최신 확인 상태와 원본을 대조한 뒤 새 목록으로 저장하세요.</p>'
    return handler._send(handler._shell(content+'<p><a href="/confirmed-export">최신 확인 완료본 선택</a> · <a href="/confirmed-export/saved">저장한 묶음 목록</a></p></section>'),409)


def dispatch(handler, path, *, raw=None, form=None):
    if form is None and path=='/confirmed-export/saved': return directory(handler,raw)
    if form is not None and path=='/confirmed-export/save':
        if set(form)!={'csrf','bundle','name'} or any(len(v)!=1 for v in form.values()):
            raise ValueError('Invalid saved export fields')
        key=form['bundle'][0];data=web._load(handler,key)
        saved.create(handler.store,handler._principal(),key,data['pins'],name=form['name'][0])
        return handler._redirect(link(key))
    if form is None:
        q=query(raw,{'id'})
        if set(q)!={'id'}: raise ValueError('Saved export list required')
        ident=q['id'][0]
    else:
        if (set(form)!={'csrf','id','acknowledge'} or any(len(v)!=1 for v in form.values()) or
            form['acknowledge'][0]!='yes'): raise ValueError('Explicit saved export acknowledgement required')
        ident=form['id'][0]
    row=saved.get(handler.store,handler._principal(),ident)
    try:
        if form is None:
            data=batch.prepare(handler.store,handler._principal(),row['pins'],expected=True)
            return web.preview(handler,data,ident,saved_row=row)
        raw=batch.bundle(handler.store,handler._principal(),row['pins'])
    except (review.Conflict,LookupError): return _blocked(handler,row)
    return handler._send_bytes(raw,'confirmed-notices.zip','application/zip')
