"""Read-only, school-scoped review metadata on isolated synthetic SQLite."""
import importlib
import importlib.util

import pytest

from moa import mobile, school_review as review, school_service as svc
from moa.core import Store, digest
from test_school_service import schools
from test_school_structure import converted
from test_school_structure_review import save


def overview(store, principal, **kwargs):
    assert importlib.util.find_spec('moa.school_overview'), 'School overview query is missing'
    return importlib.import_module('moa.school_overview').overview(store, principal, **kwargs)


def upload(s, principal, key, title='합성 통신문'):
    return svc.upload(s, principal, svc.IncomingPost(key, title, '<p>합성 원본</p>'))


def test_current_review_counts_are_scoped_and_latest_event_wins(schools):
    _, s, a, b = schools
    one, _ = converted(svc, s, a, key='one')
    two, _ = converted(svc, s, a, key='two')
    three = upload(s, a, 'three')
    failure = upload(s, a, 'failure')
    s.db.execute("UPDATE mobile_notices SET state='conversion_failed' WHERE id=(SELECT mobile_id FROM school_service_versions WHERE id=?)", (failure.version_id,))
    s.db.commit()
    save(review, s, a, one.version_id, {'kind':'confirm', 'acknowledge_warnings':True})
    latest = save(review, s, a, one.version_id, {'kind':'reopen'})
    save(review, s, a, two.version_id, {'kind':'confirm', 'acknowledge_warnings':True})
    foreign, _ = converted(svc, s, b, key='foreign')
    save(review, s, b, foreign.version_id, {'kind':'confirm', 'acknowledge_warnings':True})
    result = overview(s, a)
    assert result['counts'] == {'unreviewed':1, 'draft':1, 'confirmed':1, 'conversion_failed':1}
    assert result['total'] == result['school_total'] == 4
    rows = {r['notice_version']:r for r in result['rows']}
    assert set(rows) == {one.version_id, two.version_id, three.version_id, failure.version_id}
    assert rows[one.version_id]['review_id'] == latest
    assert rows[one.version_id]['review_actor'] == a.subject
    assert rows[one.version_id]['review_sequence'] == 2
    assert rows[one.version_id]['review_at']
    assert rows[three.version_id]['state'] == 'files_ready'
    assert rows[three.version_id]['structure_available'] is False
    assert rows[two.version_id]['structure_available'] is True
    assert overview(s, b)['counts']['confirmed'] == 1


def test_reconversion_and_new_receipt_do_not_inherit_confirmation(schools):
    _, s, a, _ = schools
    first, old = converted(svc, s, a)
    save(review, s, a, first.version_id, {'kind':'confirm', 'acknowledge_warnings':True})
    svc.request_conversion(s, a, first.version_id)
    pending = overview(s, a)
    assert pending['counts']['confirmed'] == 0
    assert pending['rows'][0]['status'] == 'unreviewed'
    assert pending['rows'][0]['state'] == 'processing'
    assert svc.process_conversions(s) == ['done']
    fresh = overview(s, a)['rows'][0]
    assert fresh['revision'] != old['revision'] and fresh['review_id'] is None
    assert fresh['status'] == 'unreviewed'
    save(review, s, a, first.version_id, {'kind':'confirm', 'acknowledge_warnings':True})
    second, _ = converted(svc, s, a, price='15000')
    result = overview(s, a)
    assert result['total'] == 1
    assert result['rows'][0]['notice_version'] == second.version_id
    assert result['rows'][0]['sequence'] == 2 and result['rows'][0]['status'] == 'unreviewed'


def test_failure_does_not_display_previous_confirmation_as_current(schools):
    _, s, a, _ = schools
    receipt, version = converted(svc, s, a)
    save(review, s, a, receipt.version_id, {'kind':'confirm', 'acknowledge_warnings':True})
    s.db.execute("UPDATE mobile_notices SET state='conversion_failed' WHERE id=?", (version['mobile_id'],)); s.db.commit()
    assert overview(s, a)['counts'] == {'unreviewed':0, 'draft':0, 'confirmed':0, 'conversion_failed':1}


@pytest.mark.parametrize('query,title', [('학교','한글 학교 안내'),('%','할인 100% 안내'),
    ('_','파일_안내'),('\\','경로\\안내'),("' OR 1=1 --", "문자 ' OR 1=1 -- 안내")])
def test_title_search_is_literal_and_never_matches_foreign_school(schools, query, title):
    _, s, a, b = schools
    own = upload(s, a, 'own', title)
    upload(s, a, 'not-matching', '대조용 제목')
    upload(s, b, 'foreign', title)
    result = overview(s, a, q=query)
    assert result['total'] == 1 and result['school_total'] == 2
    assert [r['notice_version'] for r in result['rows']] == [own.version_id]


def test_filter_combines_with_search_and_paging_is_stable(schools):
    _, s, a, b = schools
    for i in range(23): upload(s, a, str(i), '일정 '+str(i))
    upload(s, a, 'other', '별도 안내')
    upload(s, b, 'foreign', '일정 외부')
    s.db.execute("UPDATE school_service_posts SET updated_at='synthetic-tie'"); s.db.commit()
    expected = [r[0] for r in s.db.execute('SELECT latest_version FROM school_service_posts WHERE school=? AND title LIKE ? ORDER BY id', (a.school, '일정%'))]
    first = overview(s, a, q='일정', status='unreviewed')
    second = overview(s, a, q='일정', status='unreviewed', page=2)
    assert first['total'] == second['total'] == 23 and first['pages'] == 2
    assert [r['notice_version'] for r in first['rows']] == expected[:20]
    assert [r['notice_version'] for r in second['rows']] == expected[20:]
    assert overview(s, a, q='일정', page=999)['page'] == 2
    empty = overview(s, a, q='일정', status='confirmed', page=2)
    assert empty['rows'] == [] and empty['total'] == 0 and empty['page'] == empty['pages'] == 1
    assert empty['counts']['unreviewed'] == 24


@pytest.mark.parametrize('kwargs', [{'status':'unknown'}, {'q':'x'*201}, {'q':'x\x00'},
    {'q':'x\n'}, {'q':None}, {'page':0}, {'page':-1}, {'page':True}, {'page':'1'}, {'page':1000001}])
def test_bounded_invalid_queries_are_rejected(schools, kwargs):
    _, s, a, _ = schools
    with pytest.raises(ValueError): overview(s, a, **kwargs)


def test_reader_allowed_but_revoked_members_and_disabled_school_denied(schools):
    _, s, a, _ = schools
    upload(s, a, 'one')
    reader = svc.Principal(a.school, '1-reader')
    assert overview(s, reader)['total'] == 1
    svc.grant_member(s, a.school, '1-reader', 'reader', active=False)
    with pytest.raises(PermissionError): overview(s, reader)
    svc.configure_school(s, a.school, '합성 학교', enabled=False)
    with pytest.raises(PermissionError): overview(s, a)


def test_queries_preserve_db_originals_and_do_not_load_audit_overlays(schools):
    _, s, a, _ = schools
    receipt, _ = converted(svc, s, a)
    save(review, s, a, receipt.version_id, {'kind':'confirm', 'acknowledge_warnings':True})
    before = list(s.db.iterdump())
    files = {str(p):(p.stat().st_mtime_ns, digest(p.read_bytes())) for folder in ('objects','extracted')
             for p in (s.root/folder).rglob('*') if p.is_file()}
    reads = []
    def access(action, table, column, *args):
        import sqlite3
        if action == sqlite3.SQLITE_READ: reads.append((table,column))
        return sqlite3.SQLITE_OK
    s.db.set_authorizer(access)
    try:
        assert overview(s, a)['rows'][0]['status'] == 'confirmed'
        assert overview(s, a, q='not-found')['rows'] == []
    finally: s.db.set_authorizer(None)
    assert ('school_structure_reviews','overlay') not in reads
    assert ('school_structure_reviews','changes') not in reads
    assert list(s.db.iterdump()) == before
    assert {str(p):(p.stat().st_mtime_ns,digest(p.read_bytes())) for folder in ('objects','extracted')
            for p in (s.root/folder).rglob('*') if p.is_file()} == files
    assert not s.db.in_transaction


def test_v1_and_old_conversion_are_readable_without_implicit_migration(tmp_path):
    with Store(tmp_path) as s:
        s.db.executescript(svc.SCHEMA)
        s.db.execute("INSERT INTO school_service_migrations VALUES(1,'synthetic')"); s.db.commit()
        svc.configure_school(s, 'A', '합성학교'); svc.grant_member(s, 'A', 'a', 'uploader')
        a = svc.Principal('A', 'a'); receipt = upload(s, a, 'legacy')
        version = svc.get_version(s, a, receipt.version_id)
        data = mobile.build_conversion(s, version['mobile_id'], school=True, use_approved_rules=False)
        mobile.save_revision(s, version['mobile_id'], data, export_rules=False, school=True)
        before = list(s.db.iterdump())
        result = overview(s, a)
        assert result['migration_required'] is True
        assert result['rows'][0]['status'] == 'unreviewed'
        assert result['rows'][0]['structure_available'] is False
        assert list(s.db.iterdump()) == before and not review.available(s)


def test_restoration_is_listed_as_new_draft_not_prior_confirmation(schools):
    _, s, a, _ = schools
    receipt, _ = converted(svc, s, a)
    source = save(review, s, a, receipt.version_id, {'kind':'confirm', 'acknowledge_warnings':True})
    block = next(b for b in review.view(s, a, receipt.version_id)['analysis']['blocks'] if b['kind']=='body')
    save(review, s, a, receipt.version_id, {'kind':'block', 'block_id':block['id'], 'block_kind':'body', 'text':'복원 전 합성 수정'})
    latest = save(review, s, a, receipt.version_id, {'kind':'restore', 'source_event':source, 'acknowledge_restore':True})
    result = overview(s, a)
    assert result['counts']['confirmed'] == 0 and result['counts']['draft'] == 1
    assert result['rows'][0]['review_id'] == latest and result['rows'][0]['review_sequence'] == 3


def test_counts_and_rows_share_snapshot_while_other_connection_receives(schools):
    _, s, a, _ = schools
    upload(s, a, 'original')
    inserted, errors = [], []
    def during_count(sql):
        if 'SELECT count(*) FROM latest' in sql and not inserted:
            inserted.append(True)
            try:
                with Store(s.root) as other: upload(other, a, 'concurrent')
            except Exception as exc: errors.append(type(exc).__name__)
    s.db.set_trace_callback(during_count)
    try: result = overview(s, a)
    finally: s.db.set_trace_callback(None)
    assert inserted and not errors
    assert result['school_total'] == result['total'] == len(result['rows']) == 1
    assert overview(s, a)['total'] == 2


def test_caller_transaction_is_not_committed_by_read_overview(schools):
    _, s, a, _ = schools
    s.db.execute("INSERT INTO usage VALUES('synthetic-uncommitted',17)")
    assert overview(s, a)['total'] == 0
    assert s.db.in_transaction
    s.db.rollback()
    assert not s.db.execute("SELECT 1 FROM usage WHERE day='synthetic-uncommitted'").fetchone()


def test_structure_probe_is_bounded_to_the_displayed_page(schools, monkeypatch):
    _, s, a, _ = schools
    for i in range(21): converted(svc, s, a, key=str(i))
    queries = []
    s.db.set_trace_callback(queries.append)
    try: result = overview(s, a)
    finally: s.db.set_trace_callback(None)
    assert result['total'] == 21 and len(result['rows']) == 20
    assert sum('FROM mobile_revisions WHERE id=' in sql for sql in queries) == 20
    # An oversized conversion is not decoded or offered as a ready review link.
    monkeypatch.setattr(review, 'MAX_VIEW_BYTES', 1)
    assert all(not row['structure_available'] for row in overview(s, a)['rows'])
