"""Private receipt date search / CSV export must never mutate any saved data."""
import csv
import io
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from moa import school_confirmed_export as batch, school_confirmed_export_downloads as downloads
from moa import school_confirmed_export_management as management, school_confirmed_export_saved as saved
from moa import school_review as review, school_service as svc
from moa.core import Store
from test_school_service import schools
from test_school_confirmed_export_downloads import frozen, drop_v9, receipts_url
from test_school_confirmed_export_saved import IDENT, page, http_saved
from test_school_confirmed_export import confirmed
from test_school_review_check import originals
from test_school_structure_review import save
from test_web_school import school_web, client, fetch, login


def csv_url(ident=IDENT, **query):
    return '/confirmed-export/saved/receipts.csv?'+urlencode({'id':ident, **query})


def rows(raw):
    assert raw.startswith(b'\xef\xbb\xbf')
    return list(csv.reader(io.StringIO(raw.decode('utf-8-sig'), newline='')))


def dated(s, p, ident, monkeypatch, stamps):
    with monkeypatch.context() as m:
        for stamp in stamps:
            m.setattr(svc, '_now', lambda: stamp)
            downloads.record(s, p, ident, b'synthetic-delivery')


def test_csv_all_pages_exact_metadata_read_only_and_durable(schools, monkeypatch):
    _, s, p, _ = schools
    frozen(s, p, name='합성 "학교", 안내 묶음')
    for _ in range(21): downloads.record(s, p, IDENT, b'synthetic-delivery')
    before, files, changes = list(s.db.iterdump()), originals(s), s.db.total_changes
    def forbidden(*args, **kwargs): raise AssertionError('No document access or new receipt on read')
    for module, names in ((batch, ('bundle','prepare')), (downloads, ('record',)), (saved, ('get',))):
        for name in names: monkeypatch.setattr(module, name, forbidden)
    s.db.execute('PRAGMA query_only=ON')
    raw = downloads.csv_report(s, p, IDENT)
    report = rows(raw)
    assert len(report) == 22 and len(report[0]) == 7
    assert [int(row[2]) for row in report[1:]] == list(range(21, 0, -1))
    first = downloads.listing(s, p, IDENT)['rows'][0]
    assert report[1] == [IDENT, '합성 "학교", 안내 묶음', '21', first['created_at'], '1',
        str(first['zip_bytes']), first['zip_sha256']]
    assert 'subject' not in report[0] and 'school' not in report[0]
    assert list(s.db.iterdump()) == before and originals(s) == files and s.db.total_changes == changes
    with Store(s.root) as reopened: assert downloads.csv_report(reopened, p, IDENT) == raw


def test_inclusive_kst_dates_and_pagination_match_csv(schools, monkeypatch):
    _, s, p, _ = schools
    frozen(s, p)
    dated(s, p, IDENT, monkeypatch, ['2026-10-28T23:59:59+09:00']+
        ['2026-10-29T00:00:00+09:00']*21+['2026-10-29T23:59:59+09:00', '2026-10-30T00:00:00+09:00'])
    expected = list(range(23, 1, -1))
    query = dict(after='2026-10-29', before='2026-10-29')
    first, second = [downloads.listing(s, p, IDENT, page=page_number, **query) for page_number in (1, 2)]
    assert first['total'] == 22 and first['pages'] == 2 and second['page'] == 2
    assert [r['version'] for r in first['rows']+second['rows']] == expected
    assert [int(r[2]) for r in rows(downloads.csv_report(s, p, IDENT, **query))[1:]] == expected
    assert downloads.listing(s, p, IDENT, before='2026-10-28')['total'] == 1
    assert downloads.listing(s, p, IDENT, after='2026-10-30')['total'] == 1
    assert downloads.listing(s, p, IDENT, after='9999-12-31')['total'] == 0
    assert downloads.listing(s, p, IDENT, after='0001-01-01')['total'] == 24


@pytest.mark.parametrize('after,before', [
    (None,''), ('',False), ('2026-1-01',''), ('2026-02-29',''), ('','2026-04-31'),
    ('0000-01-01',''), ('２０２６-10-29',''), ('2026-10-29 ',''),
    ('2026-10-29T00:00:00',''), ('2026-10-30','2026-10-29'), ("' OR 1=1 --",'')])
def test_invalid_dates_fail_without_writes(schools, after, before):
    _, s, p, _ = schools
    frozen(s, p)
    before_db = list(s.db.iterdump())
    for read in (downloads.listing, downloads.csv_report):
        with pytest.raises(ValueError): read(s, p, IDENT, after=after, before=before)
    assert list(s.db.iterdump()) == before_db


def test_empty_csv_is_header_only_not_a_fabricated_receipt(schools, monkeypatch):
    _, s, p, _ = schools
    frozen(s, p)
    empty = downloads.csv_report(s, p, IDENT)
    assert len(rows(empty)) == 1
    dated(s, p, IDENT, monkeypatch, ['2026-10-29T12:00:00+09:00'])
    before = list(s.db.iterdump())
    assert downloads.csv_report(s, p, IDENT, before='2026-10-28') == empty
    assert list(s.db.iterdump()) == before


@pytest.mark.parametrize('name', ['=1+1', '+SUM(1,2)', '-1+1', '@SUM(1,2)', '=HYPERLINK("https://invalid.test","x")'])
def test_csv_escapes_spreadsheet_formulas_without_rewriting_names(schools, name):
    _, s, p, _ = schools
    frozen(s, p, name=name)
    downloads.record(s, p, IDENT, b'synthetic')
    before = list(s.db.iterdump())
    assert rows(downloads.csv_report(s, p, IDENT))[1][1] == "'"+name
    assert saved.get(s, p, IDENT)['name'] == name and list(s.db.iterdump()) == before


def test_csv_formula_like_list_id_is_also_safe(schools):
    _, s, p, _ = schools
    ident = '-'+'x'*23
    frozen(s, p, ident=ident)
    downloads.record(s, p, ident, b'synthetic')
    assert rows(downloads.csv_report(s, p, ident))[1][0] == "'"+ident


def test_export_limit_is_all_or_nothing_and_can_be_narrowed(schools, monkeypatch):
    _, s, p, _ = schools
    frozen(s, p)
    dated(s, p, IDENT, monkeypatch, ['2026-10-28T12:00:00+09:00', '2026-10-29T12:00:00+09:00'])
    monkeypatch.setattr(downloads, 'MAX_EXPORT_ROWS', 2)
    assert len(rows(downloads.csv_report(s, p, IDENT))) == 3
    downloads.record(s, p, IDENT, b'synthetic')
    before = list(s.db.iterdump())
    with pytest.raises(downloads.ExportTooLarge): downloads.csv_report(s, p, IDENT)
    assert len(rows(downloads.csv_report(s, p, IDENT, before='2026-10-28'))) == 2
    assert list(s.db.iterdump()) == before and downloads.listing(s, p, IDENT)['total'] == 3


def test_csv_snapshot_excludes_receipt_appended_during_the_read(schools):
    _, s, p, _ = schools
    frozen(s, p)
    downloads.record(s, p, IDENT, b'synthetic')
    appended = []
    with Store(s.root) as second:
        def append_once(sql):
            if sql.startswith('SELECT version,created_at,item_count') and not appended:
                appended.append(downloads.record(second, p, IDENT, b'synthetic-later'))
        s.db.set_trace_callback(append_once)
        try: raw = downloads.csv_report(s, p, IDENT)
        finally: s.db.set_trace_callback(None)
    assert appended == [2] and [r[2] for r in rows(raw)[1:]] == ['1']
    assert [r[2] for r in rows(downloads.csv_report(s, p, IDENT))[1:]] == ['2', '1']


def test_csv_owner_live_membership_and_missing_schema(schools):
    _, s, p, other = schools
    frozen(s, p)
    downloads.record(s, p, IDENT, b'synthetic')
    svc.grant_member(s, p.school, 'other-staff', 'uploader')
    for who in (other, svc.Principal(p.school, 'other-staff')):
        with pytest.raises(LookupError): downloads.csv_report(s, who, IDENT)
    svc.grant_member(s, p.school, p.subject, 'reader')
    assert len(rows(downloads.csv_report(s, p, IDENT))) == 2
    svc.grant_member(s, p.school, p.subject, 'uploader', active=False)
    with pytest.raises(PermissionError): downloads.csv_report(s, p, IDENT)
    svc.grant_member(s, p.school, p.subject, 'uploader')
    svc.configure_school(s, p.school, '합성학교', enabled=False)
    with pytest.raises(PermissionError): downloads.csv_report(s, p, IDENT)
    svc.configure_school(s, p.school, '합성학교')
    drop_v9(s)
    before = list(s.db.iterdump())
    with pytest.raises(review.Conflict): downloads.csv_report(s, p, IDENT)
    assert list(s.db.iterdump()) == before and not downloads.available(s)


def test_http_filters_export_all_pages_headers_and_preservation(school_web, monkeypatch):
    _, s, base = school_web
    p = svc.Principal('A','a'); a = client(); csrf = login(a, base)
    _, download, _, _ = http_saved(a, base, csrf, [confirmed(s, p)], name='=1+1')
    ident = download['id']
    dated(s, p, ident, monkeypatch, ['2026-10-28T23:59:59+09:00']+['2026-10-29T00:00:00+09:00']*21)
    before, files = list(s.db.iterdump()), originals(s)
    status, raw, _ = fetch(a, base, receipts_url(ident, **{'from':'2026-10-29', 'to':'2026-10-29'}))
    doc = page(raw)
    assert status == 200 and doc.select_one('[data-export-receipt-filters]')['method'] == 'get'
    assert doc.select_one('input[name="from"]')['value'] == '2026-10-29'
    assert len(doc.select('[data-export-receipts] li')) == 20
    next_link = doc.find('a', string='다음 영수증')['href']
    assert parse_qs(urlsplit(next_link).query) == {'id':[ident], 'from':['2026-10-29'], 'to':['2026-10-29'], 'page':['2']}
    second = page(fetch(a, base, next_link)[1])
    assert len(second.select('[data-export-receipts] li')) == 1
    href = second.select_one('[data-export-receipts-csv]')['href']
    assert 'page=' not in href
    status, raw, headers = fetch(a, base, href)
    assert status == 200 and len(rows(raw)) == 22 and rows(raw)[1][1] == "'=1+1"
    assert headers['Content-Type'] == 'text/csv; charset=utf-8'
    assert 'attachment' in headers['Content-Disposition'] and 'confirmed-export-receipts.csv' in headers['Content-Disposition']
    assert 'no-store' in headers['Cache-Control'] and 'private' in headers['Cache-Control']
    assert headers['X-Content-Type-Options'] == 'nosniff' and 'frame-ancestors' in headers['Content-Security-Policy']
    assert int(headers['Content-Length']) == len(raw)
    assert list(s.db.iterdump()) == before and originals(s) == files


def test_http_csv_survives_relogin_archive_and_stale_pins_but_cannot_bypass_download_block(school_web):
    _, s, base = school_web
    p = svc.Principal('A','a'); item = confirmed(s, p); a = client(); csrf = login(a, base)
    _, download, _, _ = http_saved(a, base, csrf, [item])
    ident = download['id']
    assert fetch(a, base, '/confirmed-export/saved/download', fields=download)[0] == 200
    report = fetch(a, base, csv_url(ident))[1]
    management.change(s, p, ident, action='archive', expected=0)
    save(review, s, p, item['id'], {'kind':'reopen'})
    assert fetch(a, base, '/logout', fields={'csrf':csrf})[0] == 200
    fresh = client(); login(fresh, base)
    before, files = list(s.db.iterdump()), originals(s)
    assert fetch(fresh, base, csv_url(ident))[1] == report
    assert fetch(fresh, base, '/confirmed-export/saved/preview?'+urlencode({'id':ident}))[0] == 409
    assert list(s.db.iterdump()) == before and originals(s) == files


def test_http_csv_privacy_current_roles_and_get_only(school_web):
    _, s, base = school_web
    p = svc.Principal('A','a'); a, b, reader = client(), client(), client()
    csrf = login(a, base); login(b, base, 'b', 'synthetic-b-only'); login(reader, base, 'reader', 'synthetic-reader-only')
    _, download, _, _ = http_saved(a, base, csrf, [confirmed(s, p)])
    ident = download['id']; endpoint = csv_url(ident)
    downloads.record(s, p, ident, b'synthetic')
    before = list(s.db.iterdump())
    assert fetch(client(), base, endpoint)[0] == 401
    assert fetch(b, base, endpoint)[0] == fetch(reader, base, endpoint)[0] == 404
    assert fetch(a, base, endpoint, fields={'csrf':csrf})[0] == 404
    assert list(s.db.iterdump()) == before
    svc.grant_member(s, p.school, p.subject, 'reader')
    assert fetch(a, base, endpoint)[0] == 200
    svc.grant_member(s, p.school, p.subject, 'reader', active=False)
    assert fetch(a, base, endpoint)[0] == 401
    svc.grant_member(s, p.school, p.subject, 'reader')
    svc.configure_school(s, p.school, '합성학교', enabled=False)
    assert fetch(a, base, endpoint)[0] == 401


@pytest.mark.parametrize('query', ['', 'id=', 'id=short', 'id='+IDENT+'&page=1',
    'id='+IDENT+'&id='+IDENT, 'id='+IDENT+'&from=2026-10-29&from=2026-10-29',
    'id='+IDENT+'&school=B', 'id='+IDENT+'&subject=x', 'id='+IDENT+'&to=2026-02-30',
    'id='+IDENT+'&from=%FF', 'id='+'x'*4097])
def test_http_csv_rejects_malformed_queries_without_changes(school_web, query):
    _, s, base = school_web
    a = client(); login(a, base)
    before = list(s.db.iterdump())
    assert fetch(a, base, '/confirmed-export/saved/receipts.csv?'+query)[0] == 400
    assert list(s.db.iterdump()) == before


def test_http_missing_schema_and_limit_are_explicit_not_empty_success(school_web, monkeypatch):
    _, s, base = school_web
    p = svc.Principal('A','a'); a = client(); csrf = login(a, base)
    _, download, _, _ = http_saved(a, base, csrf, [confirmed(s, p)])
    ident = download['id']
    downloads.record(s, p, ident, b'synthetic')
    monkeypatch.setattr(downloads, 'MAX_EXPORT_ROWS', 0)
    before = list(s.db.iterdump())
    status, raw, headers = fetch(a, base, csv_url(ident))
    assert status == 400 and '기간을 좁혀' in page(raw).get_text() and 'Content-Disposition' not in headers
    assert list(s.db.iterdump()) == before
    drop_v9(s); before = list(s.db.iterdump())
    status, raw, headers = fetch(a, base, csv_url(ident))
    assert status == 409 and '명시적 마이그레이션' in page(raw).get_text() and 'Content-Disposition' not in headers
    doc = page(fetch(a, base, receipts_url(ident))[1])
    assert not doc.select_one('[data-export-receipts-csv]')
    assert list(s.db.iterdump()) == before and not downloads.available(s)


def test_http_empty_filter_and_html_escaping(school_web, monkeypatch):
    _, s, base = school_web
    p = svc.Principal('A','a'); a = client(); csrf = login(a, base)
    name = '<img src=x onerror=alert(1)>, "합성"'
    _, download, _, _ = http_saved(a, base, csrf, [confirmed(s, p)], name=name)
    ident = download['id']
    dated(s, p, ident, monkeypatch, ['2026-10-29T12:00:00+09:00'])
    doc = page(fetch(a, base, receipts_url(ident, to='2026-10-28'))[1])
    assert doc.select_one('[data-export-receipts-name]').get_text() == name and not doc.select('img')
    assert '이 기간에' in doc.select_one('[data-export-receipts-empty]').get_text()
    assert len(rows(fetch(a, base, doc.select_one('[data-export-receipts-csv]')['href'])[1])) == 1
