"""Owner-private search and append-only saved export archive/restore tests."""
import concurrent.futures
import sqlite3
import threading
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from moa import school_confirmed_export as batch, school_confirmed_export_saved as saved
from moa import school_confirmed_export_management as management
from moa import school_confirmed_export_refresh as refresh, school_review as review, school_service as svc
from moa.core import Store
from test_school_service import schools
from test_school_structure_review import save
from test_school_review_check import originals
from test_school_confirmed_export import confirmed, verify_zip
from test_school_confirmed_export_saved import IDENT, page, inputs, http_saved, drop_v7
from test_web_school import school_web, client, fetch, login


EVENTS = 'school_confirmed_export_list_events'
RECEIPTS = 'school_confirmed_export_downloads'


def frozen(s, p, *, ident=IDENT, name='합성 확인 완료본 묶음', pins=None):
    if pins is None:
        pins = batch.prepare(s, p, [confirmed(s, p)])['pins']
    saved.create(s, p, ident, pins, name=name)
    return pins


def unchanged_rows(s):
    return [line for line in s.db.iterdump()
        if not line.startswith(('INSERT INTO "'+EVENTS+'"', 'INSERT INTO "'+RECEIPTS+'"'))]


def drop_v8(s):
    s.db.executescript('DROP TABLE IF EXISTS school_warning_search_names; DROP TABLE IF EXISTS school_warning_search_events; DROP TABLE IF EXISTS school_warning_searches; DROP TABLE IF EXISTS school_receipt_searches; DROP TABLE IF EXISTS school_confirmed_export_downloads; DROP TABLE '+EVENTS+';'
        ' DELETE FROM school_service_migrations WHERE version>=8;')


def manage_url(ident=IDENT, **query):
    return '/confirmed-export/saved/manage?'+urlencode({'id':ident, **query})


def management_form(a, base, ident):
    status, raw, headers = fetch(a, base, manage_url(ident))
    assert status == 200 and 'no-store' in headers['Cache-Control']
    doc = page(raw)
    assert doc.select_one('#saved-export-management')
    form = doc.select_one('form[data-export-management]')
    assert form is not None
    acknowledgement = form.select_one('[name=acknowledge]')
    assert acknowledgement.has_attr('required') and not acknowledgement.has_attr('checked')
    return doc, form['action'], {**inputs(form), 'acknowledge':'yes'}


def test_archive_restore_preserve_every_frozen_row_original_and_download_across_connections(schools):
    _, s, p, _ = schools
    pins = frozen(s, p)
    old = saved.get(s, p, IDENT)
    archive = batch.bundle(s, p, pins)
    before, files = unchanged_rows(s), originals(s)
    assert management.get(s, p, IDENT)['state'] == 'active'
    assert management.get(s, p, IDENT)['version'] == 0
    assert management.change(s, p, IDENT, action='archive', expected=0) == 1
    assert not management.listing(s, p)['rows']
    assert management.listing(s, p, state='archived')['rows'][0]['id'] == IDENT
    # Archiving only hides the default directory, not access to frozen pins.
    assert saved.get(s, p, IDENT) == old and batch.bundle(s, p, pins) == archive
    assert refresh.inspect(s, p, IDENT)['can_prepare']
    with Store(s.root) as reopened:
        data = management.get(reopened, p, IDENT)
        assert data['state'] == 'archived' and data['version'] == 1
        assert data['events'] == [{'version':1, 'action':'archive', 'created_at':data['events'][0]['created_at']}]
        assert management.change(reopened, p, IDENT, action='restore', expected=1) == 2
    data = management.get(s, p, IDENT)
    assert data['state'] == 'active' and data['version'] == 2
    assert [e['action'] for e in data['events']] == ['restore', 'archive']
    assert saved.get(s, p, IDENT) == old and batch.bundle(s, p, pins) == archive
    assert unchanged_rows(s) == before and originals(s) == files
    assert s.db.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
    assert not s.db.execute('PRAGMA foreign_key_check').fetchall()


def test_metadata_reads_are_bounded_read_only_and_never_inspect_documents(schools, monkeypatch):
    _, s, p, _ = schools
    frozen(s, p)
    management.change(s, p, IDENT, action='archive', expected=0)
    def forbidden(*args, **kwargs):
        raise AssertionError('Metadata management must not inspect or convert documents')
    monkeypatch.setattr(batch, 'prepare', forbidden)
    monkeypatch.setattr(batch, 'bundle', forbidden)
    before, changes, files = list(s.db.iterdump()), s.db.total_changes, originals(s)
    s.db.execute('PRAGMA query_only=ON')
    assert management.listing(s, p, state='all')['total'] == 1
    assert management.get(s, p, IDENT)['events'][0]['version'] == 1
    assert list(s.db.iterdump()) == before and s.db.total_changes == changes and originals(s) == files


def test_literal_name_search_date_boundaries_state_and_pagination_use_saved_date(schools, monkeypatch):
    _, s, p, other = schools
    pins = batch.prepare(s, p, [confirmed(s, p)])['pins']
    for i, (name, created) in enumerate([
        ('Alpha %_\\ 합성', '2026-10-25T23:59:59+09:00'),
        ('ALPHA %_\\ 합성', '2026-10-26T00:00:00+09:00'),
        ('alpha plain', '2026-10-26T23:59:59+09:00'),
        ('다음날', '2026-10-27T00:00:00+09:00'),
    ]):
        with monkeypatch.context() as patch:
            patch.setattr(svc, '_now', lambda created=created:created)
            frozen(s, p, ident=f'{i:024d}', name=name, pins=pins)
    with monkeypatch.context() as patch:
        patch.setattr(svc, '_now', lambda:'2026-11-01T00:00:00+09:00')
        management.change(s, p, f'{1:024d}', action='archive', expected=0)
    data = management.listing(s, p, q=' alpha ', after='2026-10-26', before='2026-10-26', state='all')
    assert data['filters'] == {'q':'alpha', 'after':'2026-10-26', 'before':'2026-10-26', 'state':'all'}
    assert {r['id'] for r in data['rows']} == {f'{1:024d}', f'{2:024d}'}
    assert management.listing(s, p, q='%_\\', state='all')['total'] == 2
    assert management.listing(s, p, q='%', state='all')['total'] == 2
    assert management.listing(s, p, q='not present', state='all')['total'] == 0
    archived = management.listing(s, p, after='2026-10-26', before='2026-10-26', state='archived')
    assert len(archived['rows']) == 1 and archived['rows'][0]['version'] == 1
    assert management.listing(s, other, state='all')['rows'] == []
    for i in range(4, 25):
        frozen(s, p, ident=f'{i:024d}', name='페이지 합성', pins=pins)
    first = management.listing(s, p, state='all')
    second = management.listing(s, p, state='all', page=2)
    assert first['total'] == 25 and first['pages'] == 2 and len(first['rows']) == 20
    assert len(second['rows']) == 5 and {r['id'] for r in first['rows']}.isdisjoint(r['id'] for r in second['rows'])
    assert management.listing(s, p, state='all', page=100)['rows'] == second['rows']


@pytest.mark.parametrize('values', [
    {'q':'x'*121}, {'q':'bad\nname'}, {'q':'\ud800'}, {'q':None},
    {'after':'2026-02-30'}, {'before':'2026-2-01'}, {'after':'2026-10-26T00:00'},
    {'after':'2026-10-27','before':'2026-10-26'}, {'before':'0000-01-01'},
    {'state':''}, {'state':'deleted'}, {'page':0}, {'page':True}, {'page':1.5}, {'page':1_000_001},
])
def test_invalid_search_values_never_write(schools, values):
    _, s, p, _ = schools
    before = list(s.db.iterdump())
    with pytest.raises((ValueError, UnicodeError)):
        management.listing(s, p, **values)
    assert list(s.db.iterdump()) == before


def test_event_history_is_bounded_paginated_and_retains_the_complete_sequence(schools):
    _, s, p, _ = schools
    frozen(s, p)
    for version in range(22):
        assert management.change(s, p, IDENT, action='archive' if version % 2 == 0 else 'restore', expected=version) == version+1
    first, second = management.get(s, p, IDENT), management.get(s, p, IDENT, page=2)
    assert first['total'] == 22 and first['pages'] == 2 and len(first['events']) == 20
    assert [r['version'] for r in first['events']] == list(range(22, 2, -1))
    assert [r['version'] for r in second['events']] == [2, 1]
    assert management.get(s, p, IDENT, page=999)['events'] == second['events']


@pytest.mark.parametrize('expected', [-1, 2_147_483_647, 2_147_483_648, True, 0.0, '0', None])
def test_invalid_expected_versions_never_append_events(schools, expected):
    _, s, p, _ = schools
    frozen(s, p)
    before = list(s.db.iterdump())
    with pytest.raises(ValueError):
        management.change(s, p, IDENT, action='archive', expected=expected)
    assert list(s.db.iterdump()) == before


def test_stale_replay_wrong_transition_and_wrong_action_are_not_silent_noops(schools):
    _, s, p, _ = schools
    frozen(s, p)
    with pytest.raises(review.Conflict):management.change(s, p, IDENT, action='restore', expected=0)
    assert management.change(s, p, IDENT, action='archive', expected=0) == 1
    before = list(s.db.iterdump())
    for action, expected in [('archive', 0), ('archive', 1), ('restore', 0), ('restore', 2_147_483_646)]:
        with pytest.raises(review.Conflict):management.change(s, p, IDENT, action=action, expected=expected)
    for action in ('delete', '', None):
        with pytest.raises(ValueError):management.change(s, p, IDENT, action=action, expected=1)
    assert list(s.db.iterdump()) == before


def test_two_connections_cannot_consume_the_same_expected_version(schools):
    _, s, p, _ = schools
    frozen(s, p)
    barrier = threading.Barrier(2)
    def attempt(_):
        with Store(s.root) as other:
            barrier.wait(timeout=10)
            try:return management.change(other, p, IDENT, action='archive', expected=0)
            except review.Conflict:return 'conflict'
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(attempt, range(2)))
    assert sorted(result, key=str) == [1, 'conflict']
    assert management.get(s, p, IDENT)['version'] == 1
    assert s.db.execute('SELECT count(*) FROM '+EVENTS).fetchone()[0] == 1


def test_owner_live_membership_and_write_role_are_rechecked(schools):
    _, s, p, other = schools
    frozen(s, p)
    svc.grant_member(s, p.school, 'other-staff', 'uploader')
    same_school = svc.Principal(p.school, 'other-staff')
    for who in (other, same_school):
        assert not management.listing(s, who, state='all')['rows']
        with pytest.raises(LookupError):management.get(s, who, IDENT)
        with pytest.raises(LookupError):management.change(s, who, IDENT, action='archive', expected=0)
    svc.grant_member(s, p.school, p.subject, 'reader')
    assert management.get(s, p, IDENT)['state'] == 'active'
    with pytest.raises(PermissionError):management.change(s, p, IDENT, action='archive', expected=0)
    svc.grant_member(s, p.school, p.subject, 'uploader', active=False)
    with pytest.raises(PermissionError):management.get(s, p, IDENT)
    with pytest.raises(PermissionError):management.change(s, p, IDENT, action='archive', expected=0)
    svc.grant_member(s, p.school, p.subject, 'uploader')
    svc.configure_school(s, p.school, '합성학교', enabled=False)
    with pytest.raises(PermissionError):management.listing(s, p)


@pytest.mark.parametrize('operation', ['UPDATE', 'DELETE'])
def test_event_rows_cannot_be_rewritten_or_deleted(schools, operation):
    _, s, p, _ = schools
    frozen(s, p)
    management.change(s, p, IDENT, action='archive', expected=0)
    before = list(s.db.iterdump())
    statement = 'UPDATE '+EVENTS+' SET action=action' if operation == 'UPDATE' else 'DELETE FROM '+EVENTS
    with pytest.raises(sqlite3.IntegrityError):s.db.execute(statement)
    s.db.rollback()
    assert list(s.db.iterdump()) == before


@pytest.mark.parametrize('damage', ['school','subject','missing','skip','restore_first','invalid_action','zero','overflow'])
def test_database_rejects_event_scope_sequence_and_transition_forgery(schools, damage):
    _, s, p, other = schools
    frozen(s, p)
    values = [IDENT, p.school, p.subject, 1, 'archive', svc._now()]
    if damage == 'school':values[1] = other.school
    elif damage == 'subject':values[2] = other.subject
    elif damage == 'missing':values[0] = 'x'*24
    elif damage == 'skip':values[3] = 2
    elif damage == 'restore_first':values[4] = 'restore'
    elif damage == 'invalid_action':values[4] = 'delete'
    elif damage == 'zero':values[3] = 0
    else:values[3] = 2_147_483_648
    before = list(s.db.iterdump())
    with pytest.raises(sqlite3.IntegrityError):s.db.execute('INSERT INTO '+EVENTS+' VALUES(?,?,?,?,?,?)', values)
    s.db.rollback()
    assert list(s.db.iterdump()) == before


def test_database_rejects_repeated_archive_even_at_the_next_sequence(schools):
    _, s, p, _ = schools
    frozen(s, p)
    management.change(s, p, IDENT, action='archive', expected=0)
    before = list(s.db.iterdump())
    with pytest.raises(sqlite3.IntegrityError):
        s.db.execute('INSERT INTO '+EVENTS+' VALUES(?,?,?,?,?,?)', (IDENT, p.school, p.subject, 2, 'archive', svc._now()))
    s.db.rollback()
    assert list(s.db.iterdump()) == before


def test_v8_migration_is_explicit_additive_backed_up_repeatable_and_keeps_v7_data(schools):
    _, s, p, _ = schools
    pins = frozen(s, p)
    old = saved.get(s, p, IDENT)
    drop_v8(s)
    before, files, version = list(s.db.iterdump()), originals(s), s.db.execute('PRAGMA user_version').fetchone()[0]
    assert management.get(s, p, IDENT)['management_required']
    assert management.listing(s, p)['management_required']
    assert not management.listing(s, p)['migration_required']
    with pytest.raises(review.Conflict):management.change(s, p, IDENT, action='archive', expected=0)
    assert list(s.db.iterdump()) == before
    backup = svc.migrate(s)
    with sqlite3.connect(backup) as previous:
        assert list(previous.iterdump()) == before
        assert previous.execute('SELECT max(version) FROM school_service_migrations').fetchone()[0] == 7
    assert set(before) <= set(s.db.iterdump()) and management.available(s)
    assert saved.get(s, p, IDENT) == old and batch.prepare(s, p, pins, expected=True)['pins'] == pins
    assert originals(s) == files and s.db.execute('PRAGMA user_version').fetchone()[0] == version
    after = list(s.db.iterdump())
    assert svc.migrate(s) is None and list(s.db.iterdump()) == after
    assert not s.db.execute('PRAGMA foreign_key_check').fetchall()


def test_failed_or_unknown_migration_never_repairs_schema_or_changes_originals(schools, monkeypatch):
    _, s, p, _ = schools
    frozen(s, p)
    drop_v8(s)
    before, files = list(s.db.iterdump()), originals(s)
    monkeypatch.setattr(management, 'SCHEMA_V8', management.SCHEMA_V8+'\n INVALID SYNTHETIC DDL;')
    with pytest.raises(sqlite3.Error):svc.migrate(s)
    assert list(s.db.iterdump()) == before and originals(s) == files and not management.available(s)
    s.db.execute("INSERT INTO school_service_migrations VALUES(999,'synthetic')")
    s.db.commit()
    before, backups = list(s.db.iterdump()), list((s.root/'db/backups').iterdir())
    with pytest.raises(RuntimeError):svc.migrate(s)
    assert list(s.db.iterdump()) == before and list((s.root/'db/backups').iterdir()) == backups


def test_http_search_archive_restore_relogin_and_download_keep_old_pins_and_evidence(school_web):
    _, s, base = school_web
    p = svc.Principal('A','a')
    items = [confirmed(s, p)]
    a = client(); csrf = login(a, base)
    _, download, _, _ = http_saved(a, base, csrf, items, name='<img src=x> 합성 Alpha %_\\')
    ident = download['id']; frozen_row = saved.get(s, p, ident)
    expected_zip = fetch(a, base, '/confirmed-export/saved/download', fields=download)[1]
    before, files = unchanged_rows(s), originals(s)
    status, raw, _ = fetch(a, base, '/confirmed-export/saved?'+urlencode({'q':'alpha %_\\'}))
    doc = page(raw)
    assert status == 200 and len(doc.select('[data-export-saved-card]')) == 1
    assert not doc.select('img') and '<img src=x>' in doc.get_text()
    assert doc.select_one('[data-export-manage]')['href'] == manage_url(ident)
    _, endpoint, fields = management_form(a, base, ident)
    assert endpoint == '/confirmed-export/saved/archive'
    assert fetch(a, base, endpoint, fields=fields)[0] == 200
    assert not page(fetch(a, base, '/confirmed-export/saved')[1]).select('[data-export-saved-card]')
    archived = page(fetch(a, base, '/confirmed-export/saved?state=archived')[1])
    assert len(archived.select('[data-export-saved-card]')) == 1
    assert fetch(a, base, '/confirmed-export/saved/download', fields=download)[1] == expected_zip
    _, endpoint, restore = management_form(a, base, ident)
    assert endpoint == '/confirmed-export/saved/restore'
    assert fetch(a, base, endpoint, fields=restore)[0] == 200
    assert fetch(a, base, '/logout', fields={'csrf':csrf})[0] == 200
    fresh = client(); login(fresh, base)
    _, endpoint, fresh_fields = management_form(fresh, base, ident)
    assert endpoint == '/confirmed-export/saved/archive' and fresh_fields['expected'] == '2'
    preview = page(fetch(fresh, base, '/confirmed-export/saved/preview?'+urlencode({'id':ident}))[1])
    current = {**inputs(preview.select_one('[data-export-download]')), 'acknowledge':'yes'}
    assert fetch(fresh, base, '/confirmed-export/saved/download', fields=current)[1] == expected_zip
    verify_zip(expected_zip, s, p, items)
    assert saved.get(s, p, ident) == frozen_row and unchanged_rows(s) == before and originals(s) == files


def test_http_stale_other_tab_returns_refreshed_manage_without_a_second_event(school_web):
    _, s, base = school_web
    p = svc.Principal('A','a'); a = client(); csrf = login(a, base)
    _, download, _, _ = http_saved(a, base, csrf, [confirmed(s, p)])
    _, endpoint, fields = management_form(a, base, download['id'])
    with Store(s.root) as other:management.change(other, p, download['id'], action='archive', expected=0)
    before = list(s.db.iterdump())
    status, raw, _ = fetch(a, base, endpoint, fields=fields)
    assert status == 409 and page(raw).select_one('#saved-export-management')
    form = page(raw).select_one('[data-export-management]')
    assert form['action'] == '/confirmed-export/saved/restore' and inputs(form)['expected'] == '1'
    assert not form.select_one('[name=acknowledge]').has_attr('checked')
    assert list(s.db.iterdump()) == before


def test_http_restoring_a_changed_document_does_not_unblock_old_pins(school_web):
    _, s, base = school_web
    p = svc.Principal('A','a'); item = confirmed(s, p); a = client(); csrf = login(a, base)
    _, download, _, _ = http_saved(a, base, csrf, [item])
    frozen_row = saved.get(s, p, download['id'])
    management.change(s, p, download['id'], action='archive', expected=0)
    save(review, s, p, item['id'], {'kind':'reopen'})
    _, endpoint, fields = management_form(a, base, download['id'])
    assert fetch(a, base, endpoint, fields=fields)[0] == 200
    before = list(s.db.iterdump())
    status, raw, _ = fetch(a, base, '/confirmed-export/saved/download', fields=download)
    assert status == 409 and not raw.startswith(b'PK') and page(raw).select_one('#saved-export-blocked')
    assert saved.get(s, p, download['id']) == frozen_row and list(s.db.iterdump()) == before


@pytest.mark.parametrize('invalid', ['extra','duplicate','blank_extra','blank_duplicate','missing','csrf','origin','utf8','oversize','ack_no','expected'])
def test_http_management_rejects_ambiguous_or_unauthorized_posts_without_writes(school_web, invalid):
    _, s, base = school_web
    p = svc.Principal('A','a'); a = client(); csrf = login(a, base)
    _, download, _, _ = http_saved(a, base, csrf, [confirmed(s, p)])
    _, endpoint, fields = management_form(a, base, download['id'])
    values, headers, expected_status = list(fields.items()), {}, 400
    if invalid == 'extra':values.append(('school','B'))
    elif invalid == 'duplicate':values.append(values[-1])
    elif invalid == 'blank_extra':values.append(('school',''))
    elif invalid == 'blank_duplicate':values.append((values[-1][0],''))
    elif invalid == 'missing':values = [(k,v) for k,v in values if k != 'acknowledge']
    elif invalid == 'csrf':values = [(k,'wrong' if k == 'csrf' else v) for k,v in values]; expected_status = 403
    elif invalid == 'origin':headers['Origin'] = 'https://untrusted.invalid'; expected_status = 403
    elif invalid == 'ack_no':values = [(k,'no' if k == 'acknowledge' else v) for k,v in values]
    elif invalid == 'expected':values = [(k,'00' if k == 'expected' else v) for k,v in values]
    raw = urlencode(values).encode()
    if invalid == 'utf8':raw += b'&unexpected=%FF'
    elif invalid == 'oversize':raw += b'&unexpected='+b'x'*16384; expected_status = 413
    before = list(s.db.iterdump())
    status, body, _ = fetch(a, base, endpoint, data=raw, headers=headers)
    assert status == expected_status and not body.startswith(b'PK') and list(s.db.iterdump()) == before


@pytest.mark.parametrize('path, query', [
    ('/confirmed-export/saved', 'q=%FF'), ('/confirmed-export/saved', 'q=a&q=b'),
    ('/confirmed-export/saved', 'from=2026-02-30'), ('/confirmed-export/saved', 'to=2026-2-1'),
    ('/confirmed-export/saved', 'from=2026-10-27&to=2026-10-26'),
    ('/confirmed-export/saved', 'state=deleted'), ('/confirmed-export/saved', 'school=A'),
    ('/confirmed-export/saved', 'q='+'x'*4097),
    ('/confirmed-export/saved/manage', ''), ('/confirmed-export/saved/manage', 'id='),
    ('/confirmed-export/saved/manage', 'id='+IDENT+'&page=0'),
    ('/confirmed-export/saved/manage', 'id='+IDENT+'&id='+IDENT),
    ('/confirmed-export/saved/manage', 'id='+IDENT+'&subject=other'),
])
def test_http_search_and_management_queries_are_strict_bounded_and_read_only(school_web, path, query):
    _, s, base = school_web
    a = client(); login(a, base); before = list(s.db.iterdump())
    assert fetch(a, base, path+'?'+query)[0] == 400 and list(s.db.iterdump()) == before


def test_http_owner_isolation_live_role_and_membership_cover_management_and_search(school_web):
    _, s, base = school_web
    p = svc.Principal('A','a'); a, b, reader, anonymous = [client() for _ in range(4)]
    csrf = login(a, base); csrf_b = login(b, base, 'b', 'synthetic-b-only'); csrf_r = login(reader, base, 'reader', 'synthetic-reader-only')
    _, download, _, _ = http_saved(a, base, csrf, [confirmed(s, p)])
    ident = download['id']; _, endpoint, fields = management_form(a, base, ident)
    before = list(s.db.iterdump())
    assert fetch(anonymous, base, manage_url(ident))[0] == 401
    assert fetch(anonymous, base, endpoint, fields=fields)[0] == 401
    for other, token, write_status in ((b,csrf_b,404),(reader,csrf_r,403)):
        assert fetch(other, base, manage_url(ident))[0] == 404
        assert fetch(other, base, endpoint, fields={**fields,'csrf':token})[0] == write_status
        assert not page(fetch(other, base, '/confirmed-export/saved?state=all')[1]).select('[data-export-saved-card]')
    assert list(s.db.iterdump()) == before
    svc.grant_member(s, 'A', 'a', 'reader')
    status, raw, _ = fetch(a, base, manage_url(ident))
    assert status == 200 and not page(raw).select('[data-export-management]')
    assert fetch(a, base, endpoint, fields=fields)[0] == 403
    svc.grant_member(s, 'A', 'a', 'uploader', active=False)
    assert fetch(a, base, manage_url(ident))[0] == 401
    assert fetch(a, base, endpoint, fields=fields)[0] == 401


def test_http_filters_and_pagination_links_preserve_search_state(school_web):
    _, s, base = school_web
    p = svc.Principal('A','a'); pins = batch.prepare(s, p, [confirmed(s, p)])['pins']
    for i in range(21):frozen(s, p, ident=f'{i:024d}', name='페이지 Alpha %_', pins=pins)
    a = client(); login(a, base)
    query = {'q':'alpha %_', 'state':'all', 'from':svc._now()[:10], 'to':svc._now()[:10]}
    status, raw, _ = fetch(a, base, '/confirmed-export/saved?'+urlencode(query))
    assert status == 200
    doc = page(raw); assert len(doc.select('[data-export-saved-card]')) == 20
    links = doc.select('nav a')
    next_link = next(link['href'] for link in links if parse_qs(urlsplit(link['href']).query).get('page') == ['2'])
    parsed = parse_qs(urlsplit(next_link).query)
    assert all(parsed[k] == [v] for k,v in query.items())
    assert len(page(fetch(a, base, next_link)[1]).select('[data-export-saved-card]')) == 1


def test_http_without_v8_is_read_only_and_existing_downloads_keep_working(school_web):
    _, s, base = school_web
    p = svc.Principal('A','a'); a = client(); csrf = login(a, base)
    _, download, _, _ = http_saved(a, base, csrf, [confirmed(s, p)])
    drop_v8(s)
    before, files = list(s.db.iterdump()), originals(s)
    assert management.get(s, p, download['id'])['management_required']
    status, raw, _ = fetch(a, base, manage_url(download['id']))
    assert status == 200 and '명시적 마이그레이션' in page(raw).get_text()
    assert not page(raw).select('[data-export-management]')
    assert fetch(a, base, '/confirmed-export/saved/archive', fields={'csrf':csrf,'id':download['id'],'expected':'0','acknowledge':'yes'})[0] == 409
    assert fetch(a, base, '/confirmed-export/saved/download', fields=download)[0] == 200
    assert list(s.db.iterdump()) == before and originals(s) == files and not management.available(s)


def test_missing_v7_stays_an_explicit_migration_boundary(schools):
    _, s, p, _ = schools
    drop_v7(s)
    before = list(s.db.iterdump())
    data = management.listing(s, p)
    assert data['migration_required'] and not data['rows']
    with pytest.raises(review.Conflict):management.get(s, p, IDENT)
    assert list(s.db.iterdump()) == before
