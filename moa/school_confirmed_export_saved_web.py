"""Persistent owner-only lists; every resume/download rechecks the frozen pins."""
import sqlite3
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
    from .school_confirmed_export_refresh_web import link as status_link
    from . import school_confirmed_export_management as management
    from .school_confirmed_export_management_web import link as management_link
    q=query(raw,{'page','q','from','to','state'})
    data=management.listing(handler.store,handler._principal(),page=number(q.get('page',['1'])[0],1_000_000),
        q=q.get('q',[''])[0],after=q.get('from',[''])[0],before=q.get('to',[''])[0],state=q.get('state',['active'])[0])
    f=data['filters']
    content=web.STYLE+'<section class="comparison" id="saved-confirmed-exports"><h1>저장한 확인 완료본 묶음</h1>'
    content+='<p>내 학교·내 담당자 계정에만 보이는 비공개 목록입니다. 재로그인·서버 재시작 후에도 이어서 다운로드할 수 있습니다.</p>'
    content+='<p>저장 시점의 문서 순서와 확인 버전을 유지합니다. 열기·다운로드 때 다시 검사하며, 달라진 버전으로 자동 대체하지 않습니다.</p>'
    content+='<p>보관함으로 옮긴 목록은 기본 목록에서 숨기며 삭제하지 않습니다. 기간은 한국 시간의 목록 저장일 기준이며 양 끝 날짜를 포함합니다.</p>'
    content+='<form method="get" action="/confirmed-export/saved" data-export-saved-filters><label>목록 이름 검색<input type="search" name="q" maxlength="120" value="'+esc(f['q'])+'"></label>'
    content+='<label>저장일 시작<input type="date" name="from" value="'+esc(f['after'])+'"></label><label>저장일 끝<input type="date" name="to" value="'+esc(f['before'])+'"></label>'
    content+='<label>표시 범위<select name="state">'
    for value,label in (('active','사용 중'),('archived','보관함'),('all','전체')):
        content+='<option value="'+value+'"'+(' selected' if f['state']==value else '')+'>'+label+'</option>'
    content+='</select></label><button>목록 검색</button> <a href="/confirmed-export/saved">검색 초기화</a></form>'
    if data['migration_required']: content+='<p>목록 저장 기능의 명시적 마이그레이션이 필요합니다. 이 화면은 DB를 변경하지 않습니다.</p>'
    elif data['management_required']: content+='<p>보관·복원 기능의 명시적 마이그레이션이 필요합니다. 기존 저장 목록은 계속 확인할 수 있습니다.</p>'
    if not data['migration_required'] and not data['rows']: content+='<p>조건에 맞는 저장 묶음이 없습니다. 검색 범위·보관함을 확인하거나 새 목록을 저장하세요.</p>'
    for row in data['rows']:
        content+='<article class="card" data-export-saved-card><h2>'+esc(row['name'])+'</h2><p>'+('보관함' if row['state']=='archived' else '사용 중')+' · '+str(row['item_count'])+'건 · 목록 저장 '+esc(row['created_at'])+'</p>'
        content+='<a data-export-saved-resume href="'+esc(link(row['id']))+'">묶음 확인·이어서 다운로드</a> · <a data-export-status-link href="'+esc(status_link(row['id']))+'">문서별 상태·변경 확인</a>'
        content+=' · <a data-export-manage href="'+esc(management_link(row['id']))+'">보관·복원 이력</a>'
        if 'download' in row:
            mark=row['download']
            content+='<p data-export-download-state>'+('다운로드 '+str(mark['total'])+'회 · 마지막 '+esc(mark['created_at'])
                if mark else '아직 내려받지 않음')+'</p>'
        content+='</article>'
    content+='<nav aria-label="저장한 묶음 페이지">'+str(data['page'])+' / '+str(data['pages'])+'페이지 · 총 '+str(data['total'])+'개'
    for page,label in ((data['page']-1,'이전 목록'),(data['page']+1,'다음 목록')):
        filters={k:v for k,v in (('q',f['q']),('from',f['after']),('to',f['before']),('state',f['state'] if f['state']!='active' else '')) if v}
        if 1<=page<=data['pages']: content+=' · <a href="/confirmed-export/saved?'+esc(urlencode({**filters,'page':page}))+'">'+label+'</a>'
    return handler._send(handler._shell(content+'</nav><p><a href="/confirmed-export">새 확인 완료본 선택</a> · <a href="/#review-overview">학교 현황으로</a></p></section>'))


def _blocked(handler, row):
    from .school_confirmed_export_refresh_web import link as status_link
    content=web.STYLE+'<section class="comparison" id="saved-export-blocked"><h1>저장한 묶음 다운로드가 차단됐습니다</h1>'
    content+='<h2>'+esc(row['name'])+'</h2><p>저장 뒤 수신·변환·검수 버전 또는 원본 무결성이 달라졌습니다. '
    content+='일부 문서만 내려받거나 최신본으로 바꾸지 않습니다. 저장 목록·원본·이전 검수 이력은 보존됩니다.</p><ul>'
    content+=''.join('<li>'+esc(item['title'])+'</li>' for item in row['items'])
    content+='</ul><p><a data-export-status-link href="'+esc(status_link(row['id']))+'">문서별 변경 사유·최신 확인 상태 대조</a></p>'
    content+='<p>최신 확인 상태와 원본을 대조한 뒤 새 목록으로 저장하세요.</p>'
    return handler._send(handler._shell(content+'<p><a href="/confirmed-export">최신 확인 완료본 선택</a> · <a href="/confirmed-export/saved">저장한 묶음 목록</a></p></section>'),409)


def dispatch(handler, path, *, raw=None, form=None):
    if form is None and path=='/confirmed-export/saved': return directory(handler,raw)
    if form is None and path=='/confirmed-export/saved/receipts':
        from .school_confirmed_export_downloads_web import render
        return render(handler,raw)
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
    _receipt(handler,row['id'],raw)
    return handler._send_bytes(raw,'confirmed-notices.zip','application/zip')


def _receipt(handler, ident, raw):
    """Best-effort receipt for the validated bytes; never blocks the download."""
    from . import school_confirmed_export_downloads as downloads
    try: downloads.record(handler.store,handler._principal(),ident,raw)
    except (sqlite3.Error,review.Conflict,LookupError,ValueError,PermissionError):
        handler.store.db.rollback()
