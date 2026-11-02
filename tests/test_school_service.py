"""Synthetic school A/B only. No network, operational DB or credentials."""
import importlib
import importlib.util
import io
import json
import sqlite3
import zipfile

import pytest

from moa.core import Store, digest


def service():
    assert importlib.util.find_spec('moa.school_service') is not None, 'School receiving service is missing'
    return importlib.import_module('moa.school_service')


@pytest.fixture
def schools(tmp_path):
    m = service()
    with Store(tmp_path) as store:
        m.migrate(store)
        for code in ('B10:A0001', 'B10:B0002'):
            m.configure_school(store, code, code + ' 합성학교')
            m.grant_member(store, code, code[-1] + '-teacher', 'uploader')
            m.grant_member(store, code, code[-1] + '-reader', 'reader')
        yield m, store, m.Principal('B10:A0001', '1-teacher'), m.Principal('B10:B0002', '2-teacher')


def post(m, text='10월 30일까지 12,000원', key='notice-1', url=''):
    return m.IncomingPost(key, '합성 행사 안내', '<p>' + text + '</p>',
                          (m.IncomingFile('신청서.txt', b'synthetic original form', 'attachment'),), url)


def test_school_migration_is_explicit_additive_backed_up_and_repeatable(tmp_path):
    m = service()
    with Store(tmp_path) as store:
        store.db.execute("INSERT INTO usage VALUES('2026-10-22',123)")
        store.db.commit()
        before = store.db.execute('SELECT * FROM usage').fetchall()
        version = store.db.execute('PRAGMA user_version').fetchone()[0]
        backup = m.migrate(store)
        assert backup.is_file() and backup.parent == tmp_path / 'db/backups'
        with sqlite3.connect(backup) as old:
            assert old.execute('SELECT requests FROM usage').fetchone()[0] == 123
            assert not old.execute("SELECT name FROM sqlite_master WHERE name='school_service_schools'").fetchone()
        assert m.migrate(store) is None
        assert store.db.execute('PRAGMA user_version').fetchone()[0] == version
        assert store.db.execute('SELECT * FROM usage').fetchall() == before
        assert store.db.execute('PRAGMA foreign_key_check').fetchall() == []
        assert store.db.execute('PRAGMA quick_check').fetchone()[0] == 'ok'


def test_upload_groups_body_and_files_but_never_enters_research_queue(schools):
    m, s, a, b = schools
    s.enqueue('analyse', 'research-sentinel')
    s.db.commit()
    receipt = m.upload(s, a, post(m))
    version = m.get_version(s, a, receipt.version_id)
    assert version['sequence'] == 1 and version['previous_version'] is None
    assert [f['role'] for f in version['files']] == ['convert', 'attachment']
    assert len(version['files']) == 2 and version['visibility'] == 'private'
    assert s.db.execute('SELECT count(*) FROM notices').fetchone()[0] == 0
    assert s.db.execute('SELECT kind,ref_id FROM jobs').fetchall()[0]['ref_id'] == 'research-sentinel'
    assert s.db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 1
    assert m.list_notices(s, b) == []


def test_exact_duplicate_and_duplicate_attachment_are_not_new_versions(schools):
    m, s, a, _ = schools
    incoming = post(m)
    incoming = m.IncomingPost(incoming.key, incoming.title, incoming.body_html,
        incoming.files + (m.IncomingFile('renamed.txt', incoming.files[0].data, 'attachment'),))
    first = m.upload(s, a, incoming)
    second = m.upload(s, a, incoming)
    assert second.duplicate and first.version_id == second.version_id
    assert len(m.get_version(s, a, first.version_id)['files']) == 2
    assert s.db.execute('SELECT count(*) FROM school_service_versions').fetchone()[0] == 1


def test_correction_keeps_old_version_and_replayed_old_content_never_rolls_back(schools):
    m, s, a, _ = schools
    one = m.upload(s, a, post(m))
    two = m.upload(s, a, post(m, '10월 31일까지 15,000원'))
    assert one.post_id == two.post_id and one.version_id != two.version_id
    current = m.get_version(s, a, two.version_id)
    assert current['previous_version'] == one.version_id and current['sequence'] == 2
    assert m.upload(s, a, post(m)).duplicate
    assert m.list_notices(s, a)[0]['latest_version'] == two.version_id
    assert s.object_path(m.get_version(s, a, one.version_id)['files'][0]['sha256']).read_bytes() != s.object_path(current['files'][0]['sha256']).read_bytes()


def test_identical_a_b_content_uses_shared_bytes_without_shared_ownership(schools):
    m, s, a, b = schools
    one = m.upload(s, a, post(m))
    two = m.upload(s, b, post(m))
    assert one.version_id != two.version_id and one.post_id != two.post_id
    va, vb = m.get_version(s, a, one.version_id), m.get_version(s, b, two.version_id)
    assert [f['sha256'] for f in va['files']] == [f['sha256'] for f in vb['files']]
    with pytest.raises(LookupError): m.get_version(s, b, one.version_id)
    with pytest.raises(LookupError): m.request_conversion(s, b, one.version_id)
    assert len(m.list_notices(s, a)) == len(m.list_notices(s, b)) == 1


def test_member_revocation_reader_and_disabled_school_fail_before_writing(schools):
    m, s, a, _ = schools
    reader = m.Principal(a.school, '1-reader')
    with pytest.raises(PermissionError): m.upload(s, reader, post(m))
    m.grant_member(s, a.school, a.subject, 'uploader', active=False)
    with pytest.raises(PermissionError): m.upload(s, a, post(m))
    with pytest.raises(PermissionError): m.list_notices(s, a)
    m.grant_member(s, a.school, a.subject, 'uploader')
    m.configure_school(s, a.school, '합성학교', upload_enabled=False)
    with pytest.raises(PermissionError): m.upload(s, a, post(m))
    assert s.db.execute('SELECT count(*) FROM mobile_notices').fetchone()[0] == 0


def test_all_input_is_validated_before_any_object_or_notice_write(schools):
    m, s, a, _ = schools
    bad = m.IncomingPost('bad', '제목', '<p>본문</p>', (
        m.IncomingFile('good.txt', b'good', 'attachment'),
        m.IncomingFile('fake.pdf', b'<html>not a pdf</html>', 'convert')))
    with pytest.raises(ValueError): m.upload(s, a, bad)
    assert not list((s.root/'objects').glob('*/*'))
    assert s.db.execute('SELECT count(*) FROM school_service_posts').fetchone()[0] == 0


def test_queue_conversion_real_html_and_zip_keep_originals_private(schools):
    m, s, a, _ = schools
    receipt = m.upload(s, a, post(m))
    m.request_conversion(s, a, receipt.version_id)
    assert s.db.execute("SELECT count(*) FROM jobs WHERE kind='school_convert'").fetchone()[0] == 1
    assert m.process_conversions(s) == ['done']
    version = m.get_version(s, a, receipt.version_id)
    assert version['state'] == 'candidate' and version['revision']
    from moa import mobile
    row = mobile.notice_row(s, version['mobile_id'], school=True)
    assert row['split'] == 'eval' and row['approved_revision'] is None
    result = mobile.revision(s, version['mobile_id'], school=True)['data']
    assert result['recommendations'] == []
    with zipfile.ZipFile(io.BytesIO(mobile.bundle(s, version['mobile_id'], school=True))) as bundle:
        assert '12,000원' in bundle.read('index.html').decode()
        assert b'synthetic original form' in [bundle.read(n) for n in bundle.namelist() if n.startswith('attachments/')]
    assert not (s.root/'learning/mobile-approved-rules.jsonl').exists()
    assert m.process_conversions(s) == []


def test_fixture_adapter_only_receives_within_explicit_source_scope(schools):
    m, s, a, _ = schools
    m.add_source(s, a.school, 'fixture', kind='test_adapter', allowed_url='https://a.example.test/notices/')
    adapter = m.FixtureAdapter((post(m, url='https://a.example.test/notices/1'),), cursor='page2')
    m.request_receive(s, a, 'fixture')
    assert m.process_receiving(s, {(a.school, 'fixture'): adapter}) == ['done']
    state = m.source_status(s, a, 'fixture')
    assert state['status'] == 'ok' and state['last_success'] and state['cursor'] == 'page2'
    assert len(m.list_notices(s, a)) == 1
    assert m.process_receiving(s, {(a.school, 'fixture'): adapter}) == []


@pytest.mark.parametrize('url', ['https://b.example.test/notices/1', 'https://a.example.test/other/1',
    'https://a.example.test/notices/../other/1', 'https://a.example.test/notices/%2e%2e/other/1',
    'http://a.example.test/notices/1', 'https://a.example.test/notices2/1'])
def test_scope_violation_is_not_saved_or_published(schools, url):
    m, s, a, _ = schools
    m.add_source(s, a.school, 'fixture', kind='test_adapter', allowed_url='https://a.example.test/notices/')
    m.request_receive(s, a, 'fixture')
    assert m.process_receiving(s, {(a.school, 'fixture'): m.FixtureAdapter((post(m, url=url),))}) == ['retry']
    assert m.source_status(s, a, 'fixture')['status'] == 'error'
    assert m.list_notices(s, a) == []


def test_transient_adapter_failure_retries_same_job_without_duplicates(schools):
    m, s, a, _ = schools
    m.add_source(s, a.school, 'fixture', kind='test_adapter', allowed_url='https://a.example.test/notices/')
    class Adapter:
        def __init__(self): self.calls = 0
        def fetch(self, source, cursor):
            self.calls += 1
            if self.calls == 1: raise RuntimeError('private detail must not be saved')
            return m.ReceiveBatch((post(m, url='https://a.example.test/notices/1'),), 'after-1')
    adapter = Adapter()
    job = m.request_receive(s, a, 'fixture')
    assert m.process_receiving(s, {(a.school, 'fixture'): adapter}) == ['retry']
    assert 'private detail' not in s.db.execute('SELECT payload FROM jobs WHERE id=?', (job,)).fetchone()[0]
    assert m.process_receiving(s, {(a.school, 'fixture'): adapter}) == []
    s.db.execute("UPDATE jobs SET next_attempt=NULL WHERE id=?", (job,)); s.db.commit()
    assert m.process_receiving(s, {(a.school, 'fixture'): adapter}) == ['done']
    row = s.db.execute('SELECT attempts,status FROM jobs WHERE id=?', (job,)).fetchone()
    assert tuple(row) == (2, 'done') and len(m.list_notices(s, a)) == 1


def test_unregistered_or_live_adapter_is_not_activated(schools):
    m, s, a, _ = schools
    with pytest.raises(ValueError): m.add_source(s, a.school, 'live', kind='website', allowed_url='https://a.example.test/notices/')
    with pytest.raises(PermissionError): m.request_receive(s, a, 'upload')
    assert s.db.execute('SELECT count(*) FROM jobs').fetchone()[0] == 0


def test_parser_child_keeps_external_tmp_and_cache(monkeypatch, tmp_path):
    from moa import learn
    captured = {}
    class Result:
        returncode = 0
        stdout = b'{"status":"extracted","text":"synthetic","tables":[]}'
    def run(*args, **kwargs):
        captured.update(kwargs['env']); return Result()
    monkeypatch.setattr(learn.subprocess, 'run', run)
    for name in ('TMPDIR', 'XDG_CACHE_HOME', 'PYTHONPYCACHEPREFIX'):
        monkeypatch.setenv(name, str(tmp_path/name))
    with Store(tmp_path/'store') as store:
        obj = store.object(b'<html>synthetic</html>', 'guide.html')
        learn.extract_asset(store, {**obj, 'kind':'html'})
    for name in ('TMPDIR', 'XDG_CACHE_HOME', 'PYTHONPYCACHEPREFIX'):
        assert captured.get(name) == str(tmp_path/name)


def test_conversion_queue_state_stale_revision_and_crash_recovery(schools, monkeypatch):
    from moa import mobile
    m, s, a, _ = schools
    receipt = m.upload(s, a, post(m))
    job_id = m.request_conversion(s, a, receipt.version_id, expected_revision='')
    assert m.get_version(s, a, receipt.version_id)['state'] == 'processing'
    finish = m._finish
    def crash(*args, **kwargs):
        s.db.commit()  # Simulate an older worker's persisted revision before its finish acknowledgement.
        raise KeyboardInterrupt('synthetic process loss after persisted revision')
    monkeypatch.setattr(m, '_finish', crash)
    with pytest.raises(KeyboardInterrupt): m.process_conversions(s)
    first = m.get_version(s, a, receipt.version_id)['revision']
    assert first
    s.db.execute("UPDATE jobs SET updated='2000-01-01T00:00:00+09:00' WHERE id=?", (job_id,))
    s.enqueue('analyse', 'research-running')
    s.db.execute("UPDATE jobs SET status='running',updated='2000-01-01' WHERE kind='analyse'")
    s.db.commit()
    assert m.recover_stale(s) == 1
    monkeypatch.setattr(m, '_finish', finish)
    assert m.process_conversions(s) == ['done']
    assert m.get_version(s, a, receipt.version_id)['revision'] == first
    assert s.db.execute('SELECT count(*) FROM mobile_revisions').fetchone()[0] == 1
    assert s.db.execute("SELECT status FROM jobs WHERE kind='analyse'").fetchone()[0] == 'running'
    with pytest.raises(ValueError): m.request_conversion(s, a, receipt.version_id, expected_revision='')


def test_parallel_workers_claim_school_job_once(schools):
    import concurrent.futures
    m, s, a, _ = schools
    receipt = m.upload(s, a, post(m))
    m.request_conversion(s, a, receipt.version_id)
    def claim():
        with Store(s.root) as separate: return m._claim(separate, 'school_convert')
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))
    assert sum(r is not None for r in results) == 1
    assert s.db.execute('SELECT attempts FROM jobs').fetchone()[0] == 1


def test_failed_retry_is_school_scoped_bounded_and_preserves_context(schools):
    m, s, a, b = schools
    m.add_source(s, a.school, 'fixture', kind='test_adapter', allowed_url='https://a.example.test/notices/')
    ident = m.request_receive(s, a, 'fixture')
    for attempt in range(5):
        assert m.process_receiving(s, {}) == (['failed'] if attempt == 4 else ['retry'])
        s.db.execute('UPDATE jobs SET next_attempt=NULL'); s.db.commit()
    with pytest.raises(LookupError): m.retry_job(s, b, ident)
    m.retry_job(s, a, ident)
    assert m.process_receiving(s, {(a.school, 'fixture'): m.FixtureAdapter((post(m, url='https://a.example.test/notices/1'),))}) == ['done']
    assert m.source_status(s, a, 'fixture')['last_error'] is None


def test_upload_updates_manual_source_status_and_bad_url_writes_nothing(schools):
    m, s, a, _ = schools
    with pytest.raises(ValueError): m.upload(s, a, post(m, url='file:///private'))
    assert not list((s.root/'objects').glob('*/*'))
    m.upload(s, a, post(m))
    status = m.source_status(s, a, 'upload')
    assert status['last_attempt'] and status['last_success'] and status['status'] == 'ok'


def test_ordered_guide_files_are_corrections_but_attachment_order_is_not(schools):
    m, s, a, _ = schools
    first = m.IncomingFile('first.html', b'<html><p>First section</p></html>', 'convert')
    second = m.IncomingFile('second.html', b'<html><p>Second section</p></html>', 'convert')
    x = m.IncomingFile('x.txt', b'original x')
    y = m.IncomingFile('y.txt', b'original y')
    one = m.upload(s, a, m.IncomingPost('ordered', 'Ordered notice', '', (first, second, x, y)))
    assert m.upload(s, a, m.IncomingPost('ordered', 'Ordered notice', '', (first, second, y, x))).duplicate
    two = m.upload(s, a, m.IncomingPost('ordered', 'Ordered notice', '', (second, first, x, y)))
    assert not two.duplicate and two.version_id != one.version_id
    assert m.get_version(s, a, two.version_id)['previous_version'] == one.version_id


def test_active_conversion_reuses_same_request_but_rejects_conflicting_roles(schools):
    m, s, a, _ = schools
    receipt = m.upload(s, a, post(m))
    first = m.request_conversion(s, a, receipt.version_id)
    assert m.request_conversion(s, a, receipt.version_id, {'0': {'role': 'convert'}}) == first
    with pytest.raises(ValueError, match='already queued'):
        m.request_conversion(s, a, receipt.version_id, {'0': {'role': 'attachment'}})
    assert s.db.execute('SELECT attempts FROM jobs WHERE id=?', (first,)).fetchone()[0] == 0


@pytest.mark.parametrize('changed', ['membership', 'lease'])
def test_conversion_rechecks_authority_and_lease_after_slow_parse(schools, monkeypatch, changed):
    from moa import mobile
    m, s, a, _ = schools
    receipt = m.upload(s, a, post(m))
    ident = m.request_conversion(s, a, receipt.version_id)
    original = mobile.build_conversion
    def parse(*args, **kwargs):
        data = original(*args, **kwargs)
        with Store(s.root) as other:
            if changed == 'membership':
                m.grant_member(other, a.school, a.subject, 'uploader', active=False)
            else:
                other.db.execute("UPDATE jobs SET owner='replacement-worker' WHERE id=?", (ident,))
                other.db.commit()
        return data
    monkeypatch.setattr(mobile, 'build_conversion', parse)
    assert m.process_conversions(s) == (['retry'] if changed == 'membership' else ['stale'])
    assert s.db.execute('SELECT count(*) FROM mobile_revisions').fetchone()[0] == 0
    if changed == 'lease':
        assert s.db.execute('SELECT owner FROM jobs WHERE id=?', (ident,)).fetchone()[0] == 'replacement-worker'


def test_legacy_queues_mutations_and_rule_exports_cannot_process_school_rows(schools):
    from moa import mobile
    m, s, a, _ = schools
    receipt = m.upload(s, a, post(m))
    m.request_conversion(s, a, receipt.version_id)
    assert m.process_conversions(s) == ['done']
    version = m.get_version(s, a, receipt.version_id)
    mid, rid = version['mobile_id'], version['revision']
    # Even a parser upgrade or a mistakenly enqueued legacy job must not touch it.
    payload = json.loads(s.db.execute('SELECT payload FROM mobile_revisions WHERE id=?', (rid,)).fetchone()[0])
    payload['parser_version'] = 'old-parser'
    s.db.execute('UPDATE mobile_revisions SET payload=? WHERE id=?', (json.dumps(payload), rid))
    s.db.commit()
    assert mobile.queue_outdated(s) == 0
    for action in (lambda: mobile.notice_row(s, mid), lambda: mobile.bundle(s, mid),
                   lambda: mobile.generate(s, mid), lambda: mobile.queue_conversion(s, mid, rid, {}, ''),
                   lambda: mobile.add_attachments(s, mid, rid, [('extra.txt', b'extra')]),
                   lambda: mobile.decide(s, mid, rid, 'held', 'unscoped')):
        with pytest.raises(LookupError): action()
    s.enqueue('mobile', mid, {'base_revision': rid, 'edits': {}, 'title': ''}); s.db.commit()
    assert mobile.drain_mobile(s) == ['error']
    assert m.get_version(s, a, receipt.version_id)['state'] == 'candidate'
    assert s.db.execute('SELECT count(*) FROM mobile_revisions').fetchone()[0] == 1
    assert s.db.execute('SELECT count(*) FROM mobile_reviews').fetchone()[0] == 0
    assert not (s.root/'learning/mobile-approved-rules.jsonl').read_text().strip()


def test_school_shared_bytes_do_not_reserve_or_exclude_existing_research_examples(schools):
    from moa import corpus, mobile
    from moa.learn import learn, review
    from test_corpus import GUIDE
    from test_moa import school
    m, s, a, _ = schools
    nid, _ = s.save_notice(school(), 'https://example.test/research', 'Research guide', GUIDE, [], '2026-10-22')
    learn(s, nid)
    s.db.execute("UPDATE cases SET split='train' WHERE notice_id=?", (nid,)); s.db.commit()
    record = corpus.classify_notice(s, nid)
    mid = record['mobile_id']
    rev = mobile.generate(s, mid)
    mobile.decide(s, mid, rev['id'], 'approved', 'synthetic', compared=True, rights=True, privacy=True)
    case = s.db.execute('SELECT * FROM cases WHERE notice_id=? LIMIT 1', (nid,)).fetchone()
    review(s, case['id'], 'approved', layout='grade_cards', reviewer='synthetic', rights_reviewed=True, privacy_reviewed=True)
    case = s.db.execute('SELECT * FROM cases WHERE id=?', (case['id'],)).fetchone()
    before = mobile.approved_rules(s)
    assert before and corpus.case_allowed(s, case)
    m.upload(s, a, m.IncomingPost('shared', 'School guide', '', (m.IncomingFile('guide.html', GUIDE.encode(), 'convert'),)))
    assert not corpus._file_evaluation(s, digest(GUIDE.encode()))
    assert corpus.case_allowed(s, case) and mobile.approved_rules(s) == before
    assert corpus.classify_notice(s, nid)['use'] == record['use']
    # Genuine research evaluation reservations retain their original protection.
    mobile.create_upload(s, [('evaluation.html', GUIDE.encode())], evaluation=True)
    assert corpus._file_evaluation(s, digest(GUIDE.encode()))
