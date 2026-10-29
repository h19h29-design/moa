"""Save and reload receipt conditions with existing school sessions and CSRF."""
import secrets
from urllib.parse import urlencode

from . import school_receipt_searches as saved, school_receipt_overview_web as overview_web
from . import school_service as svc, school_review as review, school_confirmed_export_downloads as downloads
from .school_saved_queue_web import query, number
from .school_review_web import _hidden
from .school_compare_web import STYLE
from .web import esc

PATH = '/confirmed-export/receipt-searches'
STATE_LABELS = {'all':'전체', 'active':'사용 중', 'archived':'보관함'}


def save_form(handler, filters):
    if not saved.available(handler.store):
        return '<p data-receipt-search-required>검색 조건 저장에는 명시적 마이그레이션이 필요합니다. 현재 영수증 검색·CSV는 계속 사용할 수 있습니다.</p>'
    if svc.require_member(handler.store, handler._principal())['role'] not in svc.WRITE_ROLES: return ''
    content = '<form method="post" action="'+PATH+'/save" data-receipt-search-save>'+handler._csrf_field()
    content += _hidden('id', secrets.token_urlsafe(18))
    for name, value in (('q',filters['q']), ('from',filters['after']), ('to',filters['before']), ('state',filters['state'])):
        content += _hidden(name, value)
    content += '<label>검색 조건 이름<input name="name" maxlength="120" required></label><button>현재 검색 조건 저장</button></form>'
    return content+'<p>검색 버튼을 눌러 적용한 이름·기간·보관 상태를 내 계정에 저장합니다. 입력 중인 조건은 먼저 검색하세요.</p>'


def directory(handler, raw):
    values = query(raw, {'page'})
    data = saved.listing(handler.store, handler._principal(), page=number(values.get('page',['1'])[0], 1_000_000))
    content = STYLE+'<section class="comparison" id="receipt-searches"><h1>저장한 영수증 검색 조건</h1>'
    content += '<p>내 학교·내 계정에만 보이며 재로그인 후에도 불러올 수 있습니다. 이름·기간·보관 상태를 저장하며 영수증 결과를 고정하지 않습니다.</p>'
    content += '<p>불러오기와 CSV 다운로드 시점의 영수증·묶음 현재 상태로 다시 검색합니다. 전체 CSV는 모든 페이지를 포함해 최대 '+format(downloads.MAX_EXPORT_ROWS, ',')+'건이며, 초과하면 범위를 좁혀야 합니다.</p>'
    if data['migration_required']:
        content += '<p data-receipt-search-required>검색 조건 저장 기능의 명시적 마이그레이션이 필요합니다. 이 화면은 DB를 변경하지 않습니다.</p>'
    elif not data['rows']:
        content += '<p data-receipt-search-empty>저장한 검색 조건이 없습니다. 영수증을 검색한 뒤 현재 조건에 이름을 붙여 저장하세요.</p>'
    for row in data['rows']:
        f = row['filters']
        content += '<article class="card" data-receipt-search-card><h2>'+esc(row['name'])+'</h2>'
        content += '<p>묶음 이름: '+esc(f['q'] or '전체')+' · 기록일: '+esc(f['after'] or '시작 제한 없음')+' ~ '+esc(f['before'] or '끝 제한 없음')+'</p>'
        content += '<p>묶음 현재 상태: '+STATE_LABELS[f['state']]+' · 조건 저장: '+esc(row['created_at'])+'</p>'
        content += '<a data-receipt-search-open href="'+PATH+'/open?'+esc(urlencode({'id':row['id']}))+'">조건 불러오기</a> · '
        content += '<a data-receipt-search-csv href="'+esc(overview_web.link(**f,csv=True))+'">이 조건의 전체 CSV 다운로드</a></article>'
    content += '<nav aria-label="저장한 검색 조건 페이지">'+str(data['page'])+' / '+str(data['pages'])+'페이지 · 총 '+str(data['total'])+'개'
    for page, label in ((data['page']-1,'이전 검색 조건'), (data['page']+1,'다음 검색 조건')):
        if 1 <= page <= data['pages']: content += ' · <a href="'+PATH+'?page='+str(page)+'">'+label+'</a>'
    content += '</nav><p><a href="'+overview_web.PATH+'">영수증 검색으로 돌아가기</a></p></section>'
    return handler._send(handler._shell(content))


def dispatch(handler, path, *, raw=None, form=None):
    try:
        if form is not None:
            if set(form) != {'csrf','id','name','q','from','to','state'} or any(len(v)!=1 for v in form.values()):
                raise ValueError('Invalid receipt search fields')
            saved.create(handler.store, handler._principal(), form['id'][0], name=form['name'][0],
                q=form['q'][0], after=form['from'][0], before=form['to'][0], state=form['state'][0])
            return handler._redirect(PATH)
        if path == PATH: return directory(handler, raw)
        values = query(raw, {'id'})
        if set(values) != {'id'}: raise ValueError('Saved receipt search required')
        row = saved.get(handler.store, handler._principal(), values['id'][0])
        return handler._redirect(overview_web.link(**row['filters']))
    except review.Conflict:
        content = STYLE+'<section class="comparison"><h1>저장한 검색 조건을 확인하세요</h1>'
        content += '<p>검색 조건 저장 기능의 명시적 마이그레이션이 필요하거나, 이미 저장한 조건과 요청이 다릅니다. 기존 조건은 덮어쓰지 않았습니다.</p>'
        content += '<a href="'+PATH+'">저장한 검색 조건</a> · <a href="'+overview_web.PATH+'">영수증 다시 검색</a></section>'
        return handler._send(handler._shell(content), 409)
