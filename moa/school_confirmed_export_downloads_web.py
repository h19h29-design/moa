"""Read-only owner receipts for saved batch downloads; no document access."""
from urllib.parse import urlencode

from . import school_confirmed_export_downloads as downloads
from . import school_confirmed_export_management as management, school_review as review
from .school_compare_web import STYLE
from .school_saved_queue_web import query, number
from .web import esc


def link(ident, *, page=1):
    return '/confirmed-export/saved/receipts?'+urlencode(
        {'id':ident, **({'page':page} if page != 1 else {})})


def render(handler, raw):
    from .school_confirmed_export_saved_web import link as preview_link
    from .school_confirmed_export_management_web import link as management_link
    q = query(raw, {'id','page'})
    if 'id' not in q: raise ValueError('Saved export list required')
    principal = handler._principal(); ident = q['id'][0]
    page = number(q.get('page',['1'])[0], 1_000_000)
    try:
        row = management.get(handler.store, principal, ident)
    except review.Conflict:
        content = STYLE+'<section class="comparison" id="saved-export-receipts"><h1>다운로드 영수증</h1>'
        content += '<p>목록 저장 기능의 명시적 마이그레이션이 필요합니다. 이 화면은 DB를 변경하지 않습니다.</p>'
        return handler._send(handler._shell(content+'<p><a href="/confirmed-export/saved">저장한 묶음 목록</a></p></section>'))
    data = downloads.listing(handler.store, principal, ident, page=page)
    content = STYLE+'<section class="comparison" id="saved-export-receipts"><h1>다운로드 영수증</h1>'
    content += '<h2 data-export-receipts-name>'+esc(row['name'])+'</h2>'
    content += '<p>'+('보관함' if row['state']=='archived' else '사용 중')+' · 목록 저장 '+esc(row['created_at'])+' · '+str(row['item_count'])+'건</p>'
    content += ('<p>저장한 묶음의 ZIP을 실제로 내려보낸 뒤 남기는 읽기 전용 기록입니다. '
        '영수증은 다운로드 허용·차단에 관여하지 않으며, 고정 버전·원본·검수 이력은 바뀌지 않습니다.</p>')
    if data['receipt_required']:
        content += '<p data-export-receipts-required>다운로드 영수증 기능의 명시적 마이그레이션이 필요합니다. 기존 다운로드는 계속 동작합니다.</p>'
    elif not data['rows']:
        content += '<p data-export-receipts-empty>아직 내려받은 기록이 없습니다. 묶음 확인 화면에서 확인란을 선택해 ZIP을 받으면 여기에 남습니다.</p>'
    else:
        content += '<p data-export-receipts-count>다운로드 '+str(data['total'])+'회 · '+str(data['page'])+' / '+str(data['pages'])+'페이지</p>'
        content += '<ol data-export-receipts>'
        for receipt in data['rows']:
            content += '<li>#'+str(receipt['version'])+' · '+esc(receipt['created_at'])+' · '+str(receipt['item_count'])+'건 · '+str(receipt['zip_bytes'])+'바이트 · <code data-export-receipt-sha>'+esc(receipt['zip_sha256'])+'</code></li>'
        content += '</ol><p class="compare-note">SHA-256은 실제로 전송한 ZIP 바이트의 값입니다. 보관·복원은 이 기록을 바꾸지 않습니다.</p>'
    content += '<nav aria-label="다운로드 영수증 페이지">'+str(data['page'])+' / '+str(data['pages'])+'페이지'
    for page, label in ((data['page']-1,'이전 영수증'),(data['page']+1,'다음 영수증')):
        if 1 <= page <= data['pages']: content += ' · <a href="'+esc(link(row['id'], page=page))+'">'+label+'</a>'
    content += '</nav><p><a data-export-saved-resume href="'+esc(preview_link(row['id']))+'">고정 묶음 확인·다운로드</a>'
    content += ' · <a href="'+esc(management_link(row['id']))+'">보관·복원 이력</a>'
    content += ' · <a href="/confirmed-export/saved">저장한 묶음 목록</a></p></section>'
    return handler._send(handler._shell(content))
