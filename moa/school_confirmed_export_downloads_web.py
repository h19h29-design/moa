"""Read-only owner receipts for saved batch downloads; no document access."""
from urllib.parse import urlencode

from . import school_confirmed_export_downloads as downloads
from . import school_confirmed_export_management as management, school_review as review
from .school_compare_web import STYLE
from .school_saved_queue_web import query, number
from .web import esc


def link(ident, *, page=1, after='', before=''):
    return '/confirmed-export/saved/receipts?'+urlencode(
        {'id':ident, **({'from':after} if after else {}), **({'to':before} if before else {}),
         **({'page':page} if page != 1 else {})})


def export(handler, raw):
    q = query(raw, {'id','from','to'})
    if 'id' not in q: raise ValueError('Saved export list required')
    ident = q['id'][0]; after = q.get('from',[''])[0]; before = q.get('to',[''])[0]
    try:
        data = downloads.csv_report(handler.store, handler._principal(), ident, after=after, before=before)
    except (downloads.ExportTooLarge, review.Conflict) as error:
        if isinstance(error, downloads.ExportTooLarge):
            status = 400
            message = '영수증이 '+format(downloads.MAX_EXPORT_ROWS, ',')+'건을 넘었습니다. 기간을 좁혀 다시 내려받으세요. 일부만 내보내지 않습니다.'
        else:
            status = 409
            message = '다운로드 영수증 기능의 명시적 마이그레이션이 필요합니다. 이 요청은 DB를 변경하지 않습니다.'
        content = STYLE+'<section class="comparison"><h1>영수증 CSV를 내려받을 수 없습니다</h1><p>'+message+'</p>'
        content += '<a href="'+esc(link(ident, after=after, before=before))+'">다운로드 영수증으로 돌아가기</a></section>'
        return handler._send(handler._shell(content), status)
    return handler._send_bytes(data, 'confirmed-export-receipts.csv', 'text/csv; charset=utf-8')


def render(handler, raw):
    from .school_confirmed_export_saved_web import link as preview_link
    from .school_confirmed_export_management_web import link as management_link
    q = query(raw, {'id','page','from','to'})
    if 'id' not in q: raise ValueError('Saved export list required')
    principal = handler._principal(); ident = q['id'][0]
    page = number(q.get('page',['1'])[0], 1_000_000)
    try:
        row = management.get(handler.store, principal, ident)
    except review.Conflict:
        content = STYLE+'<section class="comparison" id="saved-export-receipts"><h1>다운로드 영수증</h1>'
        content += '<p>목록 저장 기능의 명시적 마이그레이션이 필요합니다. 이 화면은 DB를 변경하지 않습니다.</p>'
        return handler._send(handler._shell(content+'<p><a href="/confirmed-export/saved">저장한 묶음 목록</a></p></section>'))
    data = downloads.listing(handler.store, principal, ident, page=page,
        after=q.get('from',[''])[0], before=q.get('to',[''])[0])
    filters = data['filters']
    content = STYLE+'<section class="comparison" id="saved-export-receipts"><h1>다운로드 영수증</h1>'
    content += '<h2 data-export-receipts-name>'+esc(row['name'])+'</h2>'
    content += '<p>'+('보관함' if row['state']=='archived' else '사용 중')+' · 목록 저장 '+esc(row['created_at'])+' · '+str(row['item_count'])+'건</p>'
    content += ('<p>검증된 ZIP을 내보내기 직전에 남기는 읽기 전용 기록이며, 수신 완료를 보증하지 않습니다. '
        '영수증은 다운로드 허용·차단에 관여하지 않으며, 고정 버전·원본·검수 이력은 바뀌지 않습니다.</p>')
    content += '<form method="get" action="/confirmed-export/saved/receipts" data-export-receipt-filters>'
    content += '<input type="hidden" name="id" value="'+esc(ident)+'">'
    content += '<label>기록일 시작<input type="date" name="from" value="'+esc(filters['after'])+'"></label>'
    content += '<label>기록일 끝<input type="date" name="to" value="'+esc(filters['before'])+'"></label>'
    content += '<button>영수증 검색</button> <a href="'+esc(link(ident))+'">기간 초기화</a></form>'
    content += '<p>한국 시간의 영수증 기록일 기준이며 양 끝 날짜를 포함합니다.</p>'
    if data['receipt_required']:
        content += '<p data-export-receipts-required>다운로드 영수증 기능의 명시적 마이그레이션이 필요합니다. 기존 다운로드는 계속 동작합니다.</p>'
    elif not data['rows']:
        content += '<p data-export-receipts-empty>'+('이 기간에 기록된 영수증이 없습니다. 기간을 바꾸거나 초기화하세요.'
            if filters['after'] or filters['before'] else '아직 내려받은 기록이 없습니다. 묶음 확인 화면에서 확인란을 선택해 ZIP을 받으면 여기에 남습니다.')+'</p>'
    else:
        content += '<p data-export-receipts-count>다운로드 '+str(data['total'])+'회 · '+str(data['page'])+' / '+str(data['pages'])+'페이지</p>'
        content += '<ol data-export-receipts>'
        for receipt in data['rows']:
            content += '<li>#'+str(receipt['version'])+' · '+esc(receipt['created_at'])+' · '+str(receipt['item_count'])+'건 · '+str(receipt['zip_bytes'])+'바이트 · <code data-export-receipt-sha>'+esc(receipt['zip_sha256'])+'</code></li>'
        content += '</ol><p class="compare-note">SHA-256은 실제로 전송한 ZIP 바이트의 값입니다. 보관·복원은 이 기록을 바꾸지 않습니다.</p>'
    if not data['receipt_required']:
        csv_url = '/confirmed-export/saved/receipts.csv?'+urlencode({'id':ident,
            **({'from':filters['after']} if filters['after'] else {}),
            **({'to':filters['before']} if filters['before'] else {})})
        content += '<p><a data-export-receipts-csv href="'+esc(csv_url)+'">검색된 영수증 전체 CSV 다운로드</a></p>'
        content += '<p>현재 페이지를 포함한 검색 결과 전체를 최대 '+format(downloads.MAX_EXPORT_ROWS, ',')+'건까지 내려받습니다. '
        content += '기록이 없으면 열 제목만 내보냅니다. CSV 다운로드는 새 영수증을 만들지 않습니다.</p>'
    content += '<nav aria-label="다운로드 영수증 페이지">'+str(data['page'])+' / '+str(data['pages'])+'페이지'
    for page, label in ((data['page']-1,'이전 영수증'),(data['page']+1,'다음 영수증')):
        if 1 <= page <= data['pages']: content += ' · <a href="'+esc(link(row['id'], page=page, **filters))+'">'+label+'</a>'
    content += '</nav><p><a data-export-saved-resume href="'+esc(preview_link(row['id']))+'">고정 묶음 확인·다운로드</a>'
    content += ' · <a href="'+esc(management_link(row['id']))+'">보관·복원 이력</a>'
    from .school_receipt_overview_web import link as overview_link
    content += ' · <a data-receipt-overview-link href="'+esc(overview_link(**filters))+'">내 영수증 모아보기</a>'
    content += ' · <a href="/confirmed-export/saved">저장한 묶음 목록</a></p></section>'
    return handler._send(handler._shell(content))
