"""Owner-private append-only download receipts for saved confirmed batches."""
import concurrent.futures
import hashlib
import sqlite3
import threading
from urllib.parse import urlencode

import pytest

from moa import school_confirmed_export as batch, school_confirmed_export_saved as saved
from moa import school_confirmed_export_downloads as downloads
from moa import school_confirmed_export_management as management
from moa import school_review as review, school_service as svc
from moa.core import Store
from test_school_service import schools
from test_school_structure_review import save
from test_school_review_check import originals
from test_school_confirmed_export import confirmed
from test_school_confirmed_export_saved import IDENT, page, inputs, http_saved
from test_web_school import school_web, client, fetch, login


RECEIPTS = 'school_confirmed_export_downloads'


def frozen(s, p, *, ident=IDENT, name='합성 확인 완료본 묶음', pins=None):
    if pins is None:
        pins = batch.prepare(s, p, [confirmed(s, p)])['pins']
    saved.create(s, p, ident, pins, name=name)
    return pins


def drop_v9(s):
    s.db.executescript('DROP TABLE '+RECEIPTS+'; DELETE FROM school_service_migrations WHERE version>=9;')


def without_receipts(s):
    return [line for line in s.db.iterdump() if not line.startswith('INSERT INTO "'+RECEIPTS+'"')]


def receipts_url(ident=IDENT, **query):
    return '/confirmed-export/saved/receipts?'+urlencode({'id':ident, **query})


def manage_url(ident=IDENT):
    return '/confirmed-export/saved/manage?'+urlencode({'id':ident})


def test_receipt_records_the_exact_bytes_without_touching_frozen_rows_or_originals(schools):
    _, s, p, _ = schools
    pins = frozen(s, p)
    row = saved.get(s, p, IDENT)
    raw = batch.bundle(s, p, pins)
    before, files = without_receipts(s), originals(s)
    assert downloads.record(s, p, IDENT, raw) == 1
    receipt = downloads.listing(s, p, IDENT)['rows'][0]
    assert receipt['version'] == 1 and receipt['item_count'] == 1
    assert receipt['zip_sha256'] == hashlib.sha256(raw).hexdigest()
    assert receipt['zip_bytes'] == len(raw) and receipt['created_at'].endswith('+09:00')
    assert without_receipts(s) == before and originals(s) == files
    assert saved.get(s, p, IDENT) == row and batch.bundle(s, p, pins) == raw
    with Store(s.root) as reopened:
        assert downloads.record(reopened, p, IDENT, raw) == 2
        assert [r['version'] for r in downloads.listing(reopened, p, IDENT)['rows']] == [2, 1]
    assert s.db.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
    assert not s.db.execute('PRAGMA foreign_key_check').fetchall()


def test_receipt_sequence_is_enforced_by_the_database(schools):
    _, s, p, other = schools
    frozen(s, p)
    assert downloads.record(s, p, IDENT, b'synthetic') == 1
    values = [IDENT, p.school, p.subject, 3, 1, 'a'*64, 9, svc._now()]
    before = list(s.db.iterdump())
    with pytest.raises(sqlite3.IntegrityError):
        s.db.execute('INSERT INTO '+RECEIPTS+' VALUES(?,?,?,?,?,?,?,?)', values)
    s.db.rollback()
    assert list(s.db.iterdump()) == before
    assert downloads.record(s, p, IDENT, b'synthetic') == 2


@pytest.mark.parametrize('operation', ['UPDATE', 'DELETE'])
def test_receipt_rows_cannot_be_rewritten_or_deleted(schools, operation):
    _, s, p, _ = schools
    frozen(s, p)
    downloads.record(s, p, IDENT, b'synthetic')
    before = list(s.db.iterdump())
    statement = ('UPDATE '+RECEIPTS+' SET zip_bytes=zip_bytes') if operation == 'UPDATE' else ('DELETE FROM '+RECEIPTS)
    with pytest.raises(sqlite3.IntegrityError): s.db.execute(statement)
    s.db.rollback()
    assert list(s.db.iterdump()) == before


@pytest.mark.parametrize('damage', ['school','subject','missing','skip','zero','overflow','hash','size','count'])
def test_database_rejects_forged_receipt_scope_sequence_and_hash(schools, damage):
    _, s, p, other = schools
    frozen(s, p)
    values = [IDENT, p.school, p.subject, 1, 1, 'a'*64, 9, svc._now()]
    if damage == 'school': values[1] = other.school
    elif damage == 'subject': values[2] = other.subject
    elif damage == 'missing': values[0] = 'x'*24
    elif damage == 'skip': values[3] = 2
    elif damage == 'zero': values[3] = 0
    elif damage == 'overflow': values[3] = 2_147_483_648
    elif damage == 'hash': values[5] = 'g'*64
    elif damage == 'size': values[6] = 100_663_297
    else: values[4] = 11
    before = list(s.db.iterdump())
    with pytest.raises(sqlite3.IntegrityError):
        s.db.execute('INSERT INTO '+RECEIPTS+' VALUES(?,?,?,?,?,?,?,?)', values)
    s.db.rollback()
    assert list(s.db.iterdump()) == before


def test_receipt_reads_are_metadata_only_and_bounded(schools, monkeypatch):
    _, s, p, _ = schools
    frozen(s, p)
    downloads.record(s, p, IDENT, b'synthetic')
    def forbidden(*args, **kwargs):
        raise AssertionError('Receipts must not inspect or convert documents')
    monkeypatch.setattr(batch, 'prepare', forbidden)
    monkeypatch.setattr(batch, 'bundle', forbidden)
    before, changes, files = list(s.db.iterdump()), s.db.total_changes, originals(s)
    s.db.execute('PRAGMA query_only=ON')
    assert downloads.listing(s, p, IDENT)['total'] == 1
    assert downloads.latest(s, p, [IDENT])[IDENT]['total'] == 1
    assert list(s.db.iterdump()) == before and s.db.total_changes == changes and originals(s) == files


def test_owner_isolation_live_membership_and_reader_receipts(schools):
    _, s, p, other = schools
    frozen(s, p)
    downloads.record(s, p, IDENT, b'synthetic')
    svc.grant_member(s, p.school, 'other-staff', 'uploader')
    for who in (other, svc.Principal(p.school, 'other-staff')):
        with pytest.raises(LookupError): downloads.listing(s, who, IDENT)
        with pytest.raises(LookupError): downloads.record(s, who, IDENT, b'synthetic')
        assert downloads.latest(s, who, [IDENT]) == {}
    svc.grant_member(s, p.school, p.subject, 'reader')
    assert downloads.listing(s, p, IDENT)['total'] == 1
    assert downloads.record(s, p, IDENT, b'synthetic') == 2
    svc.grant_member(s, p.school, p.subject, 'uploader', active=False)
    with pytest.raises(PermissionError): downloads.listing(s, p, IDENT)
    with pytest.raises(PermissionError): downloads.record(s, p, IDENT, b'synthetic')
    svc.grant_member(s, p.school, p.subject, 'uploader')
    svc.configure_school(s, p.school, '합성학교', enabled=False)
    with pytest.raises(PermissionError): downloads.listing(s, p, IDENT)


@pytest.mark.parametrize('raw', ['text', b'', None, 1])
def test_invalid_receipt_bytes_never_write(schools, raw):
    _, s, p, _ = schools
    frozen(s, p)
    before = list(s.db.iterdump())
    with pytest.raises(ValueError): downloads.record(s, p, IDENT, raw)
    assert list(s.db.iterdump()) == before


def test_oversized_receipt_is_rejected_before_any_write(schools, monkeypatch):
    _, s, p, _ = schools
    frozen(s, p)
    monkeypatch.setattr(batch, 'MAX_CONTENT_BYTES', 4)
    before = list(s.db.iterdump())
    with pytest.raises(ValueError): downloads.record(s, p, IDENT, b'12345')
    assert list(s.db.iterdump()) == before


@pytest.mark.parametrize('ident', ['', 'short', 'x'*25, 'x'*23, None, 1])
def test_invalid_list_ids_never_write(schools, ident):
    _, s, p, _ = schools
    frozen(s, p)
    before = list(s.db.iterdump())
    with pytest.raises(ValueError): downloads.record(s, p, ident, b'synthetic')
    with pytest.raises(ValueError): downloads.listing(s, p, ident)
    assert list(s.db.iterdump()) == before


@pytest.mark.parametrize('page_value', [0, True, 1.5, 1_000_001])
def test_invalid_receipt_pages_never_write(schools, page_value):
    _, s, p, _ = schools
    frozen(s, p)
    before = list(s.db.iterdump())
    with pytest.raises(ValueError): downloads.listing(s, p, IDENT, page=page_value)
    assert list(s.db.iterdump()) == before


def test_receipt_pagination_orders_newest_first(schools):
    _, s, p, _ = schools
    frozen(s, p)
    for _ in range(21): downloads.record(s, p, IDENT, b'synthetic')
    first, second = downloads.listing(s, p, IDENT), downloads.listing(s, p, IDENT, page=2)
    assert first['total'] == 21 and first['pages'] == 2 and len(first['rows']) == 20
    assert [r['version'] for r in first['rows']] == list(range(21, 1, -1))
    assert [r['version'] for r in second['rows']] == [1]
    assert downloads.listing(s, p, IDENT, page=99)['rows'] == second['rows']


def test_two_connections_append_distinct_consecutive_receipts(schools):
    _, s, p, _ = schools
    frozen(s, p)
    barrier = threading.Barrier(2)
    def attempt(_):
        with Store(s.root) as other:
            barrier.wait(timeout=10)
            return downloads.record(other, p, IDENT, b'synthetic')
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == [1, 2]
    assert s.db.execute('SELECT count(*) FROM '+RECEIPTS).fetchone()[0] == 2
    assert [r['version'] for r in downloads.listing(s, p, IDENT)['rows']] == [2, 1]


def test_missing_v9_is_an_explicit_read_only_boundary(schools):
    _, s, p, _ = schools
    frozen(s, p)
    downloads.record(s, p, IDENT, b'synthetic')
    drop_v9(s)
    before, files = list(s.db.iterdump()), originals(s)
    data = downloads.listing(s, p, IDENT)
    assert data['receipt_required'] and data['rows'] == [] and data['total'] == 0
    assert downloads.latest(s, p, [IDENT]) == {}
    with pytest.raises(review.Conflict): downloads.record(s, p, IDENT, b'synthetic')
    assert list(s.db.iterdump()) == before and originals(s) == files and not downloads.available(s)


def test_v9_migration_is_explicit_additive_backed_up_repeatable_and_keeps_v8_history(schools):
    _, s, p, _ = schools
    pins = frozen(s, p)
    management.change(s, p, IDENT, action='archive', expected=0)
    old, events, files = saved.get(s, p, IDENT), management.get(s, p, IDENT), originals(s)
    drop_v9(s)
    assert downloads.listing(s, p, IDENT)['receipt_required']
    before = list(s.db.iterdump())
    backup = svc.migrate(s)
    with sqlite3.connect(backup) as previous:
        assert list(previous.iterdump()) == before
        assert previous.execute('SELECT max(version) FROM school_service_migrations').fetchone()[0] == 8
        assert not previous.execute("SELECT 1 FROM sqlite_master WHERE name='"+RECEIPTS+"'").fetchone()
    assert downloads.available(s) and downloads.record(s, p, IDENT, b'synthetic') == 1
    assert saved.get(s, p, IDENT) == old and management.get(s, p, IDENT) == events
    assert originals(s) == files and batch.prepare(s, p, pins, expected=True)['pins'] == pins
    after = list(s.db.iterdump())
    assert svc.migrate(s) is None and list(s.db.iterdump()) == after
    assert not s.db.execute('PRAGMA foreign_key_check').fetchall()


def test_failed_v9_migration_leaves_no_partial_receipt_schema(schools, monkeypatch):
    _, s, p, _ = schools
    frozen(s, p)
    drop_v9(s)
    before, files = list(s.db.iterdump()), originals(s)
    monkeypatch.setattr(downloads, 'SCHEMA_V9', downloads.SCHEMA_V9+'\n INVALID SYNTHETIC DDL;')
    with pytest.raises(sqlite3.Error): svc.migrate(s)
    assert list(s.db.iterdump()) == before and originals(s) == files and not downloads.available(s)


def test_directory_and_manage_show_delivery_marks_inside_one_snapshot(schools):
    _, s, p, other = schools
    pins = batch.prepare(s, p, [confirmed(s, p)])['pins']
    frozen(s, p, ident=f'{1:024d}', name='보낸 묶음', pins=pins)
    frozen(s, p, ident=f'{2:024d}', name='보낼 묶음', pins=pins)
    downloads.record(s, p, f'{1:024d}', b'synthetic')
    data = management.listing(s, p, state='all')
    marks = {r['name']:r['download'] for r in data['rows']}
    assert marks['보낸 묶음']['total'] == 1 and marks['보낼 묶음'] is None
    assert management.listing(s, other, state='all')['rows'] == []
    assert downloads.latest(s, p, [f'{1:024d}', f'{2:024d}', 'x'*24])[f'{1:024d}']['total'] == 1


def test_http_download_records_the_delivered_hash_and_shows_it_everywhere(school_web):
    _, s, base = school_web
    p = svc.Principal('A','a'); a = client(); csrf = login(a, base)
    _, download, _, _ = http_saved(a, base, csrf, [confirmed(s, p)], name='영수증 합성 묶음')
    ident = download['id']
    status, body, headers = fetch(a, base, '/confirmed-export/saved/download', fields=download)
    assert status == 200 and body.startswith(b'PK') and 'no-store' in headers['Cache-Control']
    receipt = downloads.listing(s, p, ident)['rows'][0]
    assert receipt['zip_sha256'] == hashlib.sha256(body).hexdigest() and receipt['zip_bytes'] == len(body)
    status, raw, _ = fetch(a, base, receipts_url(ident))
    doc = page(raw)
    assert status == 200 and doc.select_one('#saved-export-receipts')
    assert doc.select_one('[data-export-receipt-sha]').get_text() == receipt['zip_sha256']
    assert '다운로드 1회' in doc.select_one('[data-export-receipts-count]').get_text()
    directory = page(fetch(a, base, '/confirmed-export/saved')[1])
    assert '다운로드 1회' in directory.select_one('[data-export-download-state]').get_text()
    managed = page(fetch(a, base, manage_url(ident))[1])
    assert '다운로드 1회' in managed.select_one('[data-export-receipts-summary]').get_text()
    assert managed.select_one('[data-export-receipts]')['href'] == receipts_url(ident)


def test_http_download_twice_keeps_identical_bytes_and_two_receipts(school_web):
    _, s, base = school_web
    p = svc.Principal('A','a'); a = client(); csrf = login(a, base)
    _, download, _, _ = http_saved(a, base, csrf, [confirmed(s, p)])
    first = fetch(a, base, '/confirmed-export/saved/download', fields=download)[1]
    second = fetch(a, base, '/confirmed-export/saved/download', fields=download)[1]
    assert first == second
    rows = downloads.listing(s, p, download['id'])['rows']
    assert [r['version'] for r in rows] == [2, 1] and {r['zip_sha256'] for r in rows} == {hashlib.sha256(first).hexdigest()}


def test_http_blocked_download_never_records_a_receipt(school_web):
    _, s, base = school_web
    p = svc.Principal('A','a'); item = confirmed(s, p); a = client(); csrf = login(a, base)
    _, download, _, _ = http_saved(a, base, csrf, [item])
    save(review, s, p, item['id'], {'kind':'reopen'})
    before = list(s.db.iterdump())
    status, body, _ = fetch(a, base, '/confirmed-export/saved/download', fields=download)
    assert status == 409 and not body.startswith(b'PK') and page(body).select_one('#saved-export-blocked')
    assert list(s.db.iterdump()) == before
    assert downloads.listing(s, p, download['id'])['total'] == 0


@pytest.mark.parametrize('invalid', ['extra','duplicate','missing','csrf','origin','utf8','oversize','ack_no','id'])
def test_http_invalid_downloads_never_record_receipts(school_web, invalid):
    _, s, base = school_web
    p = svc.Principal('A','a'); a = client(); csrf = login(a, base)
    _, download, _, _ = http_saved(a, base, csrf, [confirmed(s, p)])
    values, headers, expected = list(download.items()), {}, 400
    if invalid == 'extra': values.append(('school','B'))
    elif invalid == 'duplicate': values.append(values[-1])
    elif invalid == 'missing': values = [(k,v) for k,v in values if k != 'acknowledge']
    elif invalid == 'csrf': values = [(k,'wrong' if k == 'csrf' else v) for k,v in values]; expected = 403
    elif invalid == 'origin': headers['Origin'] = 'https://untrusted.invalid'; expected = 403
    elif invalid == 'ack_no': values = [(k,'no' if k == 'acknowledge' else v) for k,v in values]
    elif invalid == 'id': values = [(k,('bad' if k == 'id' else v)) for k,v in values]
    raw = urlencode(values).encode()
    if invalid == 'utf8': raw += b'&unexpected=%FF'
    elif invalid == 'oversize': raw += b'&unexpected='+b'x'*16384; expected = 413
    before = list(s.db.iterdump())
    status, body, _ = fetch(a, base, '/confirmed-export/saved/download', data=raw, headers=headers)
    assert status == expected and not body.startswith(b'PK') and list(s.db.iterdump()) == before
    assert s.db.execute('SELECT count(*) FROM '+RECEIPTS).fetchone()[0] == 0


def test_http_receipts_are_owner_only_and_survive_relogin_archive_and_reader(school_web):
    _, s, base = school_web
    p = svc.Principal('A','a'); a, b, reader, anonymous = [client() for _ in range(4)]
    csrf = login(a, base); csrf_b = login(b, base, 'b', 'synthetic-b-only')
    login(reader, base, 'reader', 'synthetic-reader-only')
    _, download, _, _ = http_saved(a, base, csrf, [confirmed(s, p)])
    ident = download['id']
    assert fetch(a, base, '/confirmed-export/saved/download', fields=download)[0] == 200
    assert fetch(anonymous, base, receipts_url(ident))[0] == 401
    assert fetch(b, base, receipts_url(ident))[0] == 404
    assert fetch(reader, base, receipts_url(ident))[0] == 404
    management.change(s, p, ident, action='archive', expected=0)
    assert fetch(a, base, '/confirmed-export/saved/download', fields=download)[0] == 200
    assert fetch(a, base, '/logout', fields={'csrf':csrf})[0] == 200
    fresh = client(); login(fresh, base)
    doc = page(fetch(fresh, base, receipts_url(ident))[1])
    assert len(doc.select('[data-export-receipts] li')) == 2 and '보관함' in doc.get_text()
    preview = page(fetch(fresh, base, '/confirmed-export/saved/preview?'+urlencode({'id':ident}))[1])
    current = {**inputs(preview.select_one('[data-export-download]')), 'acknowledge':'yes'}
    svc.grant_member(s, p.school, p.subject, 'reader')
    assert fetch(fresh, base, receipts_url(ident))[0] == 200
    assert fetch(fresh, base, '/confirmed-export/saved/download', fields=current)[0] == 200
    assert downloads.listing(s, p, ident)['total'] == 3


@pytest.mark.parametrize('query', ['', 'id=', 'id='+IDENT+'&page=0', 'id='+IDENT+'&id='+IDENT,
    'id='+IDENT+'&subject=x', 'id='+IDENT+'&page=abc', 'id='+'x'*4097])
def test_http_receipt_queries_are_strict_bounded_and_read_only(school_web, query):
    _, s, base = school_web
    a = client(); login(a, base)
    before = list(s.db.iterdump())
    assert fetch(a, base, '/confirmed-export/saved/receipts?'+query)[0] == 400
    assert list(s.db.iterdump()) == before


def test_http_without_v9_keeps_downloads_working_without_receipts(school_web):
    _, s, base = school_web
    p = svc.Principal('A','a'); a = client(); csrf = login(a, base)
    _, download, _, _ = http_saved(a, base, csrf, [confirmed(s, p)])
    drop_v9(s)
    before, files = list(s.db.iterdump()), originals(s)
    status, body, _ = fetch(a, base, '/confirmed-export/saved/download', fields=download)
    assert status == 200 and body.startswith(b'PK')
    status, raw, _ = fetch(a, base, receipts_url(download['id']))
    assert status == 200 and '명시적 마이그레이션' in page(raw).get_text()
    assert list(s.db.iterdump()) == before and originals(s) == files and not downloads.available(s)
