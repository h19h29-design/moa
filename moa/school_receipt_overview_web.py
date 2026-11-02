"""Private receipt overview with GET-only filters and existing per-batch links."""
from urllib.parse import urlencode

from . import school_receipt_overview as overview
from . import school_confirmed_export_downloads as downloads, school_review as review
from .school_confirmed_export_downloads_web import link as receipts_link
from .school_confirmed_export_management_web import link as management_link
from .school_compare_web import STYLE
from .school_saved_queue_web import query, number
from .web import esc

PATH = '/confirmed-export/receipts'


def link(*, q='', after='', before='', state='all', page=1, csv=False):
    fields = {k:v for k,v in (('q',q), ('from',after), ('to',before),
        ('state',state if state != 'all' else ''), ('page',page if page != 1 and not csv else '')) if v}
    return PATH+('.csv' if csv else '')+('?' + urlencode(fields) if fields else '')


def export(handler, raw):
    values = query(raw, {'q','from','to','state'})
    filters = dict(q=values.get('q',[''])[0], after=values.get('from',[''])[0],
        before=values.get('to',[''])[0], state=values.get('state',['all'])[0])
    try:
        data = overview.csv_report(handler.store, handler._principal(), **filters)
    except (downloads.ExportTooLarge, review.Conflict) as error:
        if isinstance(error, downloads.ExportTooLarge):
            status = 400
            message = '검색 결과가 '+format(downloads.MAX_EXPORT_ROWS, ',')+'건을 넘었습니다. 이름·기간·보관 상태로 범위를 좁혀 다시 내려받으세요. 일부만 내보내지 않습니다.'
        else:
            status = 409
            message = '저장 묶음·보관 이력·영수증 기능의 명시적 마이그레이션이 필요합니다. 이 요청은 DB를 변경하지 않습니다.'
        content = STYLE+'<section class="comparison"><h1>영수증 CSV를 내려받을 수 없습니다</h1><p>'+message+'</p>'
        content += '<a href="'+esc(link(**filters))+'">영수증 검색 결과로 돌아가기</a></section>'
        return handler._send(handler._shell(content), status)
    return handler._send_bytes(data, 'confirmed-export-receipts-overview.csv', 'text/csv; charset=utf-8')


def render(handler, raw):
    values = query(raw, {'q','from','to','state','page'})
    data = overview.listing(handler.store, handler._principal(), q=values.get('q',[''])[0],
        after=values.get('from',[''])[0], before=values.get('to',[''])[0],
        state=values.get('state',['all'])[0], page=number(values.get('page',['1'])[0], 1_000_000))
    f = data['filters']
    content = STYLE+'<section class="comparison" id="receipt-overview"><h1>내 다운로드 영수증 모아보기</h1>'
    content += '<p>내 학교·내 계정의 저장 묶음에 남은 영수증을 함께 확인합니다. 보관함의 기록도 기본으로 포함합니다.</p>'
    content += '<p>기간은 한국 시간의 영수증 기록일 기준으로 양 끝 날짜를 포함합니다. 보관 상태는 묶음의 현재 상태입니다.</p>'
    content += '<form method="get" action="'+PATH+'" data-receipt-overview-filters>'
    content += '<label>묶음 이름 검색<input type="search" name="q" maxlength="120" value="'+esc(f['q'])+'"></label>'
    content += '<label>기록일 시작<input type="date" name="from" value="'+esc(f['after'])+'"></label>'
    content += '<label>기록일 끝<input type="date" name="to" value="'+esc(f['before'])+'"></label>'
    content += '<label>묶음 현재 상태<select name="state">'
    for value,label in (('all','전체'),('active','사용 중'),('archived','보관함')):
        content += '<option value="'+value+'"'+(' selected' if f['state']==value else '')+'>'+label+'</option>'
    content += '</select></label><button>영수증 검색</button> <a href="'+PATH+'">검색 초기화</a></form>'
    from . import school_receipt_searches_web as searches
    content += '<p><a data-receipt-search-directory href="'+searches.PATH+'">저장한 검색 조건 불러오기</a></p>'
    if not data['migration_required']: content += searches.save_form(handler, f)
    if data['migration_required']:
        content += '<p data-receipt-overview-required>저장 묶음·보관 이력·영수증 기능의 명시적 마이그레이션이 필요합니다. 이 화면은 DB를 변경하지 않습니다.</p>'
    else:
        content += '<p data-receipt-overview-summary>검색 결과: 묶음 '+str(data['batch_count'])+'개 · 영수증 '+str(data['total'])+'건 · 기록된 ZIP 합계 '+format(data['zip_bytes_total'], ',')+'바이트</p>'
        content += '<p>반복 다운로드 기록도 각각 합산합니다. 영수증은 ZIP 전송 직전의 기록이며 수신 완료를 보증하지 않습니다.</p>'
        content += '<p><a data-receipt-overview-csv href="'+esc(link(**f,csv=True))+'">검색 결과 전체 CSV 다운로드</a></p>'
        content += '<p>다운로드 시점에 같은 조건으로 검색된 모든 묶음·모든 페이지의 영수증을 최대 '+format(downloads.MAX_EXPORT_ROWS, ',')+'건까지 내려받습니다. '
        content += '기록이 없으면 열 제목만 내보냅니다. CSV 다운로드는 새 영수증을 만들지 않습니다.</p>'
        if not data['rows']:
            content += '<p data-receipt-overview-empty>조건에 맞는 영수증이 없습니다. 이름·기간·보관 상태를 바꾸거나 검색을 초기화하세요.</p>'
        for row in data['rows']:
            content += '<article class="card" data-receipt-overview-card><h2>'+esc(row['name'])+'</h2>'
            content += '<p>묶음 현재 상태: '+('보관함' if row['state']=='archived' else '사용 중')+' · 영수증 #'+str(row['version'])+' · '+esc(row['created_at'])+'</p>'
            content += '<p>'+str(row['item_count'])+'건 · '+format(row['zip_bytes'], ',')+'바이트 · <code data-receipt-overview-sha>'+esc(row['zip_sha256'])+'</code></p>'
            content += '<a data-receipt-overview-batch href="'+esc(receipts_link(row['list_id'],after=f['after'],before=f['before']))+'">이 묶음의 영수증·CSV</a>'
            content += ' · <a href="'+esc(management_link(row['list_id']))+'">보관·복원 이력</a></article>'
        content += '<nav aria-label="영수증 모아보기 페이지">'+str(data['page'])+' / '+str(data['pages'])+'페이지'
        for page,label in ((data['page']-1,'이전 영수증'),(data['page']+1,'다음 영수증')):
            if 1 <= page <= data['pages']: content += ' · <a href="'+esc(link(**f,page=page))+'">'+label+'</a>'
        content += '</nav>'
    content += '<p><a href="/confirmed-export/saved">저장한 묶음 목록</a></p></section>'
    return handler._send(handler._shell(content))
