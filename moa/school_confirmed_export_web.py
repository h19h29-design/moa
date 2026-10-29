"""Version-pinned selection, explicit preview and private confirmed batch download."""
import json
import re
import secrets
from urllib.parse import urlencode

from . import school_confirmed_export as batch, school_overview as overview
from . import school_review as review
from .school_overview_web import query as overview_query
from .school_saved_queue_web import query
from .school_review_check_web import link as check_link
from .school_review_web import _hidden
from .school_compare_web import STYLE
from .core import encode
from .web import esc

MAX_PREPARED = 4


def remember(handler, data, *, source=None):
    """Session-only preparation; durable saving and download remain explicit."""
    p=handler._principal();entries=handler._session().setdefault('school_confirmed_exports',{})
    while len(entries)>=MAX_PREPARED: entries.pop(next(iter(entries)))
    key=secrets.token_urlsafe(18)
    entries[key]={'school':p.school,'subject':p.subject,'pins':data['pins'],'source':source}
    return key


def _load(handler, key):
    if not re.fullmatch(r'[A-Za-z0-9_-]{24}',key): raise ValueError('Invalid prepared bundle')
    p=handler._principal()
    item=handler._session().get('school_confirmed_exports',{}).get(key)
    if not item or (item['school'],item['subject'])!=(p.school,p.subject):
        raise LookupError('Prepared bundle not found')
    return batch.prepare(handler.store,p,item['pins'],expected=True)


def directory(handler, raw):
    q=query(raw,{'q','page'})
    result=overview.overview(handler.store,handler._principal(),**overview_query(urlencode({**{k:v[0] for k,v in q.items()},'status':'confirmed'})))
    content=STYLE+'<section class="comparison" id="confirmed-export"><h1>확인 완료본 묶음 다운로드</h1>'
    content+='<p>학교의 최신 확인 완료본을 최대 10건 선택하세요. 원본 합계 64MiB, HTML·근거를 포함한 묶음 내용 96MiB로 제한합니다. 원본·검수 이력은 보존합니다.</p>'
    content+='<p><a data-export-saved-directory href="/confirmed-export/saved">저장한 묶음·이어서 다운로드</a></p>'
    content+='<form method="get" action="/confirmed-export"><label>제목 검색<input type="search" name="q" maxlength="200" value="'+esc(result['q'])+'"></label><button>검색</button></form>'
    content+='<p data-export-count>확인 완료 '+str(result['total'])+'건 · '+str(result['page'])+' / '+str(result['pages'])+'페이지</p>'
    content+='<form method="post" action="/confirmed-export/prepare" data-export-selection>'+handler._csrf_field()
    offered=0
    for row in result['rows']:
        if not row['structure_available'] or row['state']!='candidate': continue
        pin={'id':row['notice_version'],'revision':row['revision'],'event':row['review_id']};offered+=1
        content+='<article class="card"><label><input type="checkbox" name="item" value="'+esc(encode(pin))+'"> '+esc(row['title'])+'</label>'
        content+='<p>수신 #'+str(row['sequence'])+' · 검수 #'+str(row['review_sequence'])+' · '+esc(row['review_actor'])+' · '+esc(row['review_at'])+'</p>'
        content+='<a href="'+esc(check_link(pin['id'],pin['revision'],pin['event']))+'">이 확인 기록·HTML 보기</a></article>'
    if offered: content+='<button>선택한 확인 완료본 준비</button>'
    else: content+='<p>이 페이지에 내려받을 수 있는 확인 완료본이 없습니다. 문서 구조 검수와 확인 상태를 먼저 확인하세요.</p>'
    content+='</form><nav aria-label="확인 완료본 페이지">'
    for number,label in ((result['page']-1,'이전 페이지'),(result['page']+1,'다음 페이지')):
        if 1<=number<=result['pages']: content+='<a href="/confirmed-export?'+esc(urlencode({'q':result['q'],'page':number}))+'">'+label+'</a> · '
    return handler._send(handler._shell(content+'</nav><p><a href="/#review-overview">학교 현황으로</a></p></section>'))


def render(handler, raw):
    q=query(raw,{'bundle'})
    if set(q)!={'bundle'}: raise ValueError('Prepared bundle required')
    key=q['bundle'][0];data=_load(handler,key)
    source=handler._session()['school_confirmed_exports'][key].get('source')
    return preview(handler,data,key,source=source)


def preview(handler, data, key, *, saved_row=None, source=None):
    content=STYLE+'<section class="comparison" id="confirmed-export-preview"><h1>'+('저장한 확인 완료본 묶음' if saved_row else '확인 완료본 묶음 준비 결과')+'</h1>'
    if saved_row:
        content+='<h2 data-export-saved-name>'+esc(saved_row['name'])+'</h2><p>목록 저장 '+esc(saved_row['created_at'])+' · 내 계정 전용</p>'
        from .school_confirmed_export_refresh_web import link as status_link
        content+='<p><a data-export-status-link href="'+esc(status_link(key))+'">문서별 상태·변경 확인</a></p>'
        from .school_confirmed_export_management_web import link as management_link
        content+='<p><a data-export-manage href="'+esc(management_link(key))+'">목록 보관·복원 이력</a></p>'
        from .school_confirmed_export_downloads_web import link as receipt_link
        content+='<p><a data-export-receipts href="'+esc(receipt_link(key))+'">다운로드 영수증</a></p>'
    elif source:
        from .school_confirmed_export_refresh_web import link as status_link
        content+='<p class="compare-note" data-export-refresh-source>저장 목록 <a href="'+esc(status_link(source['id']))+'">'+esc(source['name'])+'</a>의 최신 확인본으로 준비했습니다. 원래 저장 목록은 보존됩니다. 아래 포함 문서·확인 시점·남은 경고를 다시 대조하세요.</p>'
    content+='<p data-export-summary>'+str(len(data['documents']))+'건 · 원본 합계 '+str(data['original_bytes'])+'바이트 · 비공개</p>'
    content+='<p class="compare-note">표시한 수신·변환·검수 버전만 내려받습니다. 준비 뒤 정정·재변환·검수 수정·확인 해제가 있으면 전체 다운로드를 중단합니다. 공개 승인은 포함하지 않습니다.</p>'
    for d in data['documents']:
        e=d['review']
        content+='<article class="card" data-export-document><h2>'+esc(d['title'])+'</h2><p>수신 #'+str(d['receiving_sequence'])+' · 검수 #'+str(e['sequence'])+' · '+esc(e['actor'])+' · '+esc(e['created_at'])+'</p>'
        content+='<p>파일 '+str(d['file_count'])+'개 · 경고 '+str(len(d['warnings']))+'개 · '+('분석기가 보고한 완전성 충족' if d['analysis_complete'] else '일부 미확정·누락 있음')+'</p>'
        content+='<a href="'+esc(check_link(d['notice_version'],d['conversion_revision'],e['id']))+'">고정된 확인 기록·HTML 대조</a></article>'
    content+='<p>ZIP에는 목록 HTML·근거 JSON과 문서별 수정 HTML·원본 파일이 포함됩니다. 경고·미확정 범위도 그대로 포함합니다.</p>'
    if saved_row:
        content+='<p>저장한 목록은 재로그인·서버 재시작 후에도 유지됩니다. 다운로드마다 같은 버전을 재검사하며 확인란은 직접 다시 선택해야 합니다.</p>'
    else:
        from .school_confirmed_export_saved_web import save_form
        content+=save_form(handler,key)
        content+='<p>저장하지 않은 준비 목록은 현재 로그인에서만 유지됩니다. 로그아웃·서버 재시작·준비 목록 4개 초과 시 다시 선택하세요.</p>'
    endpoint='/confirmed-export/saved/download' if saved_row else '/confirmed-export/download'
    content+='<form action="'+endpoint+'" method="post" data-export-download>'+handler._csrf_field()+_hidden('id' if saved_row else 'bundle',key)
    content+='<label><input type="checkbox" name="acknowledge" value="yes" required> 포함 문서·검수 시점·남은 경고를 확인했습니다.</label><button>확인한 묶음 ZIP 다운로드</button></form>'
    return handler._send(handler._shell(content+'<p><a href="/confirmed-export/saved">저장한 묶음 목록</a> · <a href="/confirmed-export">다시 선택</a> · <a href="/#review-overview">학교 현황으로</a></p></section>'))


def submit(handler, path, form):
    if path=='/confirmed-export/prepare':
        if set(form)!={'csrf','item'} or len(form['csrf'])!=1 or not 1<=len(form['item'])<=batch.MAX_NOTICES:
            raise ValueError('Invalid selection fields')
        items=[json.loads(value) for value in form['item']]
        data=batch.prepare(handler.store,handler._principal(),items)
        key=remember(handler,data)
        return handler._redirect('/confirmed-export/preview?'+urlencode({'bundle':key}))
    if set(form)!={'csrf','bundle','acknowledge'} or any(len(v)!=1 for v in form.values()) or form['acknowledge'][0]!='yes':
        raise ValueError('Explicit batch download acknowledgement required')
    data=_load(handler,form['bundle'][0])
    return handler._send_bytes(batch.bundle(handler.store,handler._principal(),data['pins']),'confirmed-notices.zip','application/zip')


def dispatch(handler, path, *, raw=None, form=None):
    try:
        if form is None and path in ('/confirmed-export/receipts','/confirmed-export/receipts.csv'):
            from . import school_receipt_overview_web
            if path.endswith('.csv'): return school_receipt_overview_web.export(handler,raw)
            return school_receipt_overview_web.render(handler,raw)
        if path in ('/confirmed-export/saved/manage','/confirmed-export/saved/archive','/confirmed-export/saved/restore'):
            from . import school_confirmed_export_management_web
            return school_confirmed_export_management_web.dispatch(handler,path,raw=raw,form=form)
        if path in ('/confirmed-export/saved/status','/confirmed-export/saved/prepare-current'):
            from . import school_confirmed_export_refresh_web
            return school_confirmed_export_refresh_web.dispatch(handler,raw=raw,form=form)
        if path in ('/confirmed-export/saved','/confirmed-export/saved/preview','/confirmed-export/save','/confirmed-export/saved/download','/confirmed-export/saved/receipts','/confirmed-export/saved/receipts.csv'):
            from . import school_confirmed_export_saved_web
            return school_confirmed_export_saved_web.dispatch(handler,path,raw=raw,form=form)
        if form is not None: return submit(handler,path,form)
        return directory(handler,raw) if path=='/confirmed-export' else render(handler,raw)
    except batch.Limit as error:
        return handler._send(handler._shell('<h1>문서를 나눠 선택하세요</h1><p>'+esc(str(error))+'</p><a href="/confirmed-export">확인 완료본 다시 선택</a>'),400)
    except review.Conflict:
        return handler._send(handler._shell('<h1>묶음 준비 상태를 다시 확인하세요</h1><p>선택한 수신·변환·검수 버전 또는 원본 무결성이 달라졌습니다. 최신 확인 상태와 원본을 대조한 뒤 다시 선택하세요. 일부 문서만 내려받지 않습니다.</p><a href="/confirmed-export">확인 완료본 다시 선택</a>'),409)
