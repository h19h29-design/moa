"""Cross-batch receipt search stays private, read-only and snapshot-consistent."""
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from moa import school_receipt_overview as overview, school_receipt_overview_web as web
from moa import school_confirmed_export as batch, school_confirmed_export_saved as saved
from moa import school_confirmed_export_downloads as downloads
from moa import school_confirmed_export_management as management
from moa import school_review as review, school_service as svc
from moa.core import Store
from test_school_service import schools
from test_web_school import school_web, client, fetch, login
from test_school_confirmed_export_downloads import frozen
from test_school_confirmed_export_receipt_report import dated, rows as csv_rows
from test_school_confirmed_export_saved import IDENT, page, http_saved
from test_school_confirmed_export import confirmed
from test_school_structure_review import save
from test_school_review_check import originals


def fixture(s, p, monkeypatch):
    pins = frozen(s, p, name='봄 안내')
    frozen(s, p, ident='b'*24, name='여름 보관 안내', pins=pins)
    frozen(s, p, ident='c'*24, name='아직 보내지 않은 안내', pins=pins)
    dated(s, p, IDENT, monkeypatch, ['2026-10-28T23:59:59+09:00', '2026-10-29T00:00:00+09:00'])
    dated(s, p, 'b'*24, monkeypatch, ['2026-10-29T23:59:59+09:00', '2026-10-30T00:00:00+09:00'])
    management.change(s, p, 'b'*24, action='archive', expected=0)
    return pins


def test_overview_joins_all_owned_batches_and_summarizes_matching_receipts(schools, monkeypatch):
    _, s, p, _ = schools
    fixture(s, p, monkeypatch)
    data = overview.listing(s, p)
    assert data['total'] == 4 and data['batch_count'] == 2 and data['zip_bytes_total'] == 4*18
    assert [(r['list_id'],r['version']) for r in data['rows']] == [('b'*24,2),('b'*24,1),(IDENT,2),(IDENT,1)]
    assert {r['name'] for r in data['rows']} == {'봄 안내','여름 보관 안내'}
    assert [r['state'] for r in data['rows']] == ['archived','archived','active','active']
    assert all(r['item_count'] == 1 and r['zip_bytes'] == 18 for r in data['rows'])
    assert data['filters'] == dict(q='',after='',before='',state='all')


def test_filters_combine_name_current_state_and_inclusive_kst_receipt_dates(schools, monkeypatch):
    _, s, p, _ = schools
    fixture(s, p, monkeypatch)
    data = overview.listing(s, p, after='2026-10-29', before='2026-10-29')
    assert data['total'] == 2 and data['batch_count'] == 2 and data['zip_bytes_total'] == 36
    assert [(r['list_id'],r['version']) for r in data['rows']] == [('b'*24,1),(IDENT,2)]
    one = overview.listing(s, p, q=' 보관 ', state='archived', after='2026-10-29', before='2026-10-29')
    assert one['total'] == one['batch_count'] == 1 and one['rows'][0]['name'] == '여름 보관 안내'
    assert overview.listing(s, p, state='active')['total'] == 2
    assert overview.listing(s, p, before='2026-10-28')['total'] == 1
    assert overview.listing(s, p, after='2026-10-30')['total'] == 1
    assert overview.listing(s, p, after='9999-12-31')['total'] == 0
    assert overview.listing(s, p, after='0001-01-01')['total'] == 4
    management.change(s, p, 'b'*24, action='restore', expected=1)
    assert overview.listing(s, p, state='archived')['total'] == 0
    assert overview.listing(s, p, state='active')['total'] == 4


@pytest.mark.parametrize('q,expected', [('%',1),('_',1),('\\',1),('ALPHA',1),("' OR 1=1 --",0)])
def test_name_search_is_literal_and_ascii_case_insensitive(schools, q, expected):
    _, s, p, _ = schools
    pins = frozen(s, p, name='100%_\\ Alpha')
    frozen(s, p, ident='b'*24, name='다른 묶음', pins=pins)
    for ident in (IDENT,'b'*24): downloads.record(s, p, ident, b'synthetic')
    result = overview.listing(s, p, q=q)
    assert result['total'] == expected and result['batch_count'] == expected
    if expected: assert result['rows'][0]['list_id'] == IDENT


def test_owner_school_scoping_and_live_membership(schools, monkeypatch):
    _, s, p, other = schools
    fixture(s, p, monkeypatch)
    svc.grant_member(s, p.school, 'second', 'uploader')
    second = svc.Principal(p.school,'second')
    for who,ident in ((second,'d'*24),(other,'e'*24)):
        frozen(s, who, ident=ident, name='other private batch')
        downloads.record(s, who, ident, b'private-synthetic')
    assert overview.listing(s, p, q='other')['total'] == 0
    assert overview.listing(s, p)['total'] == 4
    assert [r['list_id'] for r in overview.listing(s, second)['rows']] == ['d'*24]
    assert [r['list_id'] for r in overview.listing(s, other)['rows']] == ['e'*24]
    svc.grant_member(s, p.school, p.subject, 'reader')
    assert overview.listing(s, p)['total'] == 4
    svc.grant_member(s, p.school, p.subject, 'reader', active=False)
    with pytest.raises(PermissionError): overview.listing(s, p)
    svc.grant_member(s, p.school, p.subject, 'reader')
    svc.configure_school(s, p.school, '합성학교', enabled=False)
    with pytest.raises(PermissionError): overview.listing(s, p)


def test_read_only_metadata_never_inspects_documents_and_survives_stale_pins(schools, monkeypatch):
    _, s, p, _ = schools
    item = confirmed(s, p)
    frozen(s, p, pins=batch.prepare(s, p, [item])['pins'])
    downloads.record(s, p, IDENT, b'synthetic')
    save(review, s, p, item['id'], {'kind':'reopen'})
    before, files, changes = list(s.db.iterdump()), originals(s), s.db.total_changes
    def forbidden(*args, **kwargs): raise AssertionError('Overview must read receipt metadata only')
    for module,names in ((batch,('prepare','bundle')), (saved,('get',)),
                         (downloads,('record','listing')), (management,('get',))):
        for name in names: monkeypatch.setattr(module,name,forbidden)
    s.db.execute('PRAGMA query_only=ON')
    data = overview.listing(s, p)
    assert data['total'] == 1 and data['rows'][0]['list_id'] == IDENT
    assert list(s.db.iterdump()) == before and originals(s) == files and s.db.total_changes == changes
    with Store(s.root) as reopened: assert overview.listing(reopened, p) == data


def test_tied_times_use_stable_order_and_pages_keep_global_totals(schools, monkeypatch):
    _, s, p, _ = schools
    pins = batch.prepare(s, p, [confirmed(s, p)])['pins']
    for n in range(3):
        ident = f'{n:024d}'
        frozen(s, p, ident=ident, pins=pins)
        dated(s, p, ident, monkeypatch, ['2026-10-29T12:00:00+09:00']*7)
    first, second = overview.listing(s, p), overview.listing(s, p, page=2)
    expected = [(f'{n:024d}',v) for n in range(2,-1,-1) for v in range(7,0,-1)]
    assert [(r['list_id'],r['version']) for r in first['rows']+second['rows']] == expected
    assert first['total'] == second['total'] == 21
    assert first['batch_count'] == second['batch_count'] == 3
    assert first['zip_bytes_total'] == second['zip_bytes_total'] == 21*18
    assert len(first['rows']) == 20 and len(second['rows']) == 1 and first['pages'] == 2
    assert overview.listing(s, p, page=1_000_000)['rows'] == second['rows']


def test_totals_rows_and_current_archive_state_share_a_snapshot(schools):
    _, s, p, _ = schools
    frozen(s, p); downloads.record(s, p, IDENT, b'one')
    changed = []
    with Store(s.root) as second:
        def append(sql):
            if 'SELECT * FROM visible' in sql and not changed:
                changed.append(downloads.record(second, p, IDENT, b'twox'))
                management.change(second, p, IDENT, action='archive', expected=0)
        s.db.set_trace_callback(append)
        try: data = overview.listing(s, p)
        finally: s.db.set_trace_callback(None)
    assert changed == [2] and data['total'] == 1 and data['zip_bytes_total'] == 3
    assert [r['state'] for r in data['rows']] == ['active']
    next_data = overview.listing(s, p)
    assert next_data['total'] == 2 and next_data['zip_bytes_total'] == 7
    assert [r['state'] for r in next_data['rows']] == ['archived','archived']


@pytest.mark.parametrize('args', [dict(q=None),dict(q='x'*121),dict(q='bad\nname'),
    dict(after='2026-02-29'),dict(before=None),dict(after='２０２６-10-29'),
    dict(before='2026-1-01'),dict(after='2026-10-30',before='2026-10-29'),
    dict(state='unknown'),dict(page=0),dict(page=True),dict(page=1.5),dict(page=1_000_001)])
def test_invalid_search_never_changes_data(schools, args):
    _, s, p, _ = schools
    before = list(s.db.iterdump())
    with pytest.raises(ValueError): overview.listing(s, p, **args)
    assert list(s.db.iterdump()) == before


@pytest.mark.parametrize('table', ['school_confirmed_export_items','school_confirmed_export_list_events','school_confirmed_export_downloads'])
def test_missing_schema_is_reported_without_installing_or_faking_empty_history(schools, table):
    _, s, p, _ = schools
    frozen(s, p); downloads.record(s, p, IDENT, b'synthetic')
    s.db.execute('DROP TABLE '+table); s.db.commit()
    before = list(s.db.iterdump())
    result = overview.listing(s, p)
    assert result['migration_required'] and result['rows'] == []
    assert list(s.db.iterdump()) == before


def test_no_receipts_is_a_valid_empty_result(schools):
    _, s, p, _ = schools
    frozen(s, p)
    result = overview.listing(s, p)
    assert not result['migration_required'] and result['rows'] == []
    assert result['total'] == result['batch_count'] == result['zip_bytes_total'] == 0
    assert result['page'] == result['pages'] == 1


def test_http_filters_pagination_and_links_to_scoped_csv(school_web, monkeypatch):
    _, s, base = school_web
    p = svc.Principal('A','a'); a = client(); csrf = login(a,base)
    _, download, _, _ = http_saved(a,base,csrf,[confirmed(s,p)],name='찾기 %_\\ Alpha')
    ident = download['id']
    dated(s,p,ident,monkeypatch,['2026-10-29T00:00:00+09:00']*21)
    before, files = list(s.db.iterdump()), originals(s)
    directory = page(fetch(a,base,'/confirmed-export/saved')[1])
    assert directory.select_one('[data-receipt-overview-link]')['href'] == web.PATH
    url = web.link(q='%_\\',after='2026-10-29',before='2026-10-29',state='active')
    status, raw, headers = fetch(a,base,url)
    doc = page(raw)
    assert status == 200 and 'no-store' in headers['Cache-Control'] and headers['X-Content-Type-Options'] == 'nosniff'
    assert 'frame-ancestors' in headers['Content-Security-Policy'] and not headers.get('Access-Control-Allow-Origin')
    assert doc.select_one('[data-receipt-overview-filters]')['method'] == 'get'
    assert '묶음 1개 · 영수증 21건' in doc.select_one('[data-receipt-overview-summary]').get_text()
    assert len(doc.select('[data-receipt-overview-card]')) == 20
    next_link = doc.find('a',string='다음 영수증')['href']
    assert parse_qs(urlsplit(next_link).query) == {'q':['%_\\'],'from':['2026-10-29'],'to':['2026-10-29'],'state':['active'],'page':['2']}
    second = page(fetch(a,base,next_link)[1])
    assert len(second.select('[data-receipt-overview-card]')) == 1
    single_link = second.select_one('[data-receipt-overview-batch]')['href']
    assert parse_qs(urlsplit(single_link).query) == {'id':[ident],'from':['2026-10-29'],'to':['2026-10-29']}
    single = page(fetch(a,base,single_link)[1])
    assert parse_qs(urlsplit(single.select_one('[data-receipt-overview-link]')['href']).query) == {'from':['2026-10-29'],'to':['2026-10-29']}
    csv_url = single.select_one('[data-export-receipts-csv]')['href']
    assert len(csv_rows(fetch(a,base,csv_url)[1])) == 22
    assert list(s.db.iterdump()) == before and originals(s) == files


def test_http_overview_privacy_roles_relogin_and_get_only(school_web):
    _, s, base = school_web
    p = svc.Principal('A','a'); a,b,reader = client(),client(),client()
    csrf = login(a,base); login(b,base,'b','synthetic-b-only'); login(reader,base,'reader','synthetic-reader-only')
    _, download, _, _ = http_saved(a,base,csrf,[confirmed(s,p)],name='only-owner-batch')
    downloads.record(s,p,download['id'],b'synthetic')
    for who in (b,reader):
        status, raw, _ = fetch(who,base,web.PATH)
        assert status == 200 and 'only-owner-batch' not in raw.decode()
        assert '영수증 0건' in page(raw).select_one('[data-receipt-overview-summary]').get_text()
    assert fetch(client(),base,web.PATH)[0] == 401
    before = list(s.db.iterdump())
    assert fetch(a,base,web.PATH,fields={'csrf':csrf})[0] == 404 and list(s.db.iterdump()) == before
    management.change(s,p,download['id'],action='archive',expected=0)
    assert fetch(a,base,'/logout',fields={'csrf':csrf})[0] == 200
    fresh = client(); login(fresh,base)
    before = list(s.db.iterdump())
    doc = page(fetch(fresh,base,web.link(state='archived'))[1])
    assert doc.select_one('[data-receipt-overview-card] h2').get_text() == 'only-owner-batch'
    assert list(s.db.iterdump()) == before
    svc.grant_member(s,p.school,p.subject,'reader')
    assert len(page(fetch(fresh,base,web.PATH)[1]).select('[data-receipt-overview-card]')) == 1
    svc.grant_member(s,p.school,p.subject,'reader',active=False)
    assert fetch(fresh,base,web.PATH)[0] == 401
    svc.grant_member(s,p.school,p.subject,'reader')
    svc.configure_school(s,p.school,'합성학교',enabled=False)
    assert fetch(fresh,base,web.PATH)[0] == 401


@pytest.mark.parametrize('raw', ['id='+IDENT,'school=B','subject=a','q=x&q=y','q='+('x'*121),
    'from=2026-02-30','to=%FF','from=2026-10-30&to=2026-10-29','state=deleted',
    'page=0','page=1000001','q='+'x'*4097])
def test_http_rejects_unknown_duplicate_malformed_or_overlong_query(school_web,raw):
    _, s, base = school_web
    a = client(); login(a,base)
    before = list(s.db.iterdump())
    assert fetch(a,base,web.PATH+'?'+raw)[0] == 400
    assert list(s.db.iterdump()) == before


def test_http_empty_missing_schema_and_escaped_names(school_web):
    _, s, base = school_web
    p = svc.Principal('A','a'); a = client(); csrf = login(a,base)
    name = '<img src=x onerror=alert(1)> & "합성"'
    _, download, _, _ = http_saved(a,base,csrf,[confirmed(s,p)],name=name)
    downloads.record(s,p,download['id'],b'synthetic')
    before = list(s.db.iterdump())
    doc = page(fetch(a,base,web.link(q='<img'))[1])
    assert doc.select_one('[data-receipt-overview-card] h2').get_text() == name and not doc.select('img')
    assert doc.select_one('input[name="q"]')['value'] == '<img'
    empty = page(fetch(a,base,web.link(q='missing'))[1])
    assert empty.select_one('[data-receipt-overview-empty]') and '영수증 0건' in empty.get_text()
    assert list(s.db.iterdump()) == before
    s.db.execute('DROP TABLE school_confirmed_export_downloads'); s.db.commit()
    before = list(s.db.iterdump())
    doc = page(fetch(a,base,web.PATH)[1])
    assert doc.select_one('[data-receipt-overview-required]')
    assert not doc.select_one('[data-receipt-overview-summary]') and not doc.select_one('[data-receipt-overview-empty]')
    assert list(s.db.iterdump()) == before
