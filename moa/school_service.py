"""Opt-in, private school intake; no live crawler, publication or model calls.

The existing SHA store, mobile parser and SQLite jobs are shared infrastructure.
School ownership/version rows and job kinds are separate from research notices,
quotas and training exports. Provisioning/migration functions are admin-only APIs,
not staff HTTP endpoints. A Principal must come from server-side authentication.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol
from urllib.parse import unquote, urlsplit

from . import mobile
from .core import Store, digest, encode, kst_now
from .net import canonical_url

SCHEMA = '''
CREATE TABLE school_service_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE school_service_schools(
 code TEXT PRIMARY KEY, name TEXT NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
 upload_enabled INTEGER NOT NULL CHECK(upload_enabled IN (0,1)), updated_at TEXT NOT NULL);
CREATE TABLE school_service_members(
 school TEXT NOT NULL REFERENCES school_service_schools(code), subject TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('manager','uploader','reader')),
 active INTEGER NOT NULL CHECK(active IN (0,1)), PRIMARY KEY(school,subject));
CREATE TABLE school_service_sources(
 school TEXT NOT NULL REFERENCES school_service_schools(code), id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('manual','test_adapter')), allowed_url TEXT NOT NULL,
 enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), last_attempt TEXT, last_success TEXT,
 status TEXT NOT NULL DEFAULT 'never', last_error TEXT, cursor TEXT NOT NULL DEFAULT '',
 PRIMARY KEY(school,id));
CREATE TABLE school_service_posts(
 id TEXT PRIMARY KEY, school TEXT NOT NULL, source_id TEXT NOT NULL, post_key TEXT NOT NULL,
 title TEXT NOT NULL, latest_version TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(school,source_id,post_key), UNIQUE(school,id),
 FOREIGN KEY(school,source_id) REFERENCES school_service_sources(school,id),
 FOREIGN KEY(school,id,latest_version) REFERENCES school_service_versions(school,post_id,id));
CREATE TABLE school_service_versions(
 id TEXT PRIMARY KEY, school TEXT NOT NULL, post_id TEXT NOT NULL, sequence INTEGER NOT NULL,
 fingerprint TEXT NOT NULL, mobile_id TEXT NOT NULL UNIQUE REFERENCES mobile_notices(id),
 previous_version TEXT, source_url TEXT NOT NULL, received_by TEXT NOT NULL, received_at TEXT NOT NULL,
 visibility TEXT NOT NULL DEFAULT 'private' CHECK(visibility='private'),
 UNIQUE(post_id,sequence), UNIQUE(post_id,fingerprint), UNIQUE(school,post_id,id),
 FOREIGN KEY(school,post_id) REFERENCES school_service_posts(school,id),
 FOREIGN KEY(school,post_id,previous_version) REFERENCES school_service_versions(school,post_id,id));
CREATE INDEX school_service_versions_school ON school_service_versions(school,post_id);
'''
WRITE_ROLES = ('uploader', 'manager')
KINDS = ('school_receive', 'school_convert')
MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class Principal:
    school: str
    subject: str


@dataclass(frozen=True)
class IncomingFile:
    filename: str
    data: bytes
    role: str = 'attachment'


@dataclass(frozen=True)
class IncomingPost:
    key: str
    title: str
    body_html: str
    files: tuple[IncomingFile, ...] = ()
    source_url: str = ''


@dataclass(frozen=True)
class Receipt:
    post_id: str
    version_id: str
    duplicate: bool


@dataclass(frozen=True)
class ReceiveBatch:
    posts: tuple[IncomingPost, ...]
    cursor: str = ''


class Adapter(Protocol):
    """Future opt-in adapters return source records, never publish or save them."""
    def fetch(self, source: dict, cursor: str) -> ReceiveBatch: ...


@dataclass
class FixtureAdapter:
    """Only fixture inputs in phase 1; deliberately no HTTP/network client."""
    posts: tuple[IncomingPost, ...]
    cursor: str = ''

    def fetch(self, source: dict, cursor: str) -> ReceiveBatch:
        return ReceiveBatch(self.posts, self.cursor)


def _now():
    return kst_now().isoformat()


def migrate(store: Store):
    """Explicit additive migration with a WAL-consistent pre-DDL backup.

    Does not advance core PRAGMA user_version or modify any existing rows.
    Merely opening Store or running the existing collector does not apply this.
    """
    exists = store.db.execute("SELECT 1 FROM sqlite_master WHERE name='school_service_migrations'").fetchone()
    versions = [r[0] for r in store.db.execute('SELECT version FROM school_service_migrations ORDER BY version')] if exists else []
    if versions not in [list(range(1,n)) for n in range(1,11)] or (exists and not versions):
        raise RuntimeError('Unknown school schema; reconcile before migration')
    if versions == list(range(1,10)): return None
    from .school_review import SCHEMA_V2
    from .school_saved_queue import SCHEMA_V3
    from .school_review_drafts import SCHEMA_V4
    from .school_draft_restore import SCHEMA_V5
    from .school_draft_handoff import SCHEMA_V6
    from .school_confirmed_export_saved import SCHEMA_V7
    from .school_confirmed_export_management import SCHEMA_V8
    from .school_confirmed_export_downloads import SCHEMA_V9
    backup = store.backup('school-service-v9-' + uuid.uuid4().hex[:12])
    try:
        store.db.executescript('BEGIN IMMEDIATE;\n' + (SCHEMA if not exists else '') +
            (SCHEMA_V2 if 2 not in versions else '') + (SCHEMA_V3 if 3 not in versions else '') +
            (SCHEMA_V4 if 4 not in versions else '') + (SCHEMA_V5 if 5 not in versions else '') +
            (SCHEMA_V6 if 6 not in versions else '') + (SCHEMA_V7 if 7 not in versions else '') +
            (SCHEMA_V8 if 8 not in versions else '') + SCHEMA_V9)
        if not exists: store.db.execute('INSERT INTO school_service_migrations VALUES(1,?)', (_now(),))
        if 2 not in versions: store.db.execute('INSERT INTO school_service_migrations VALUES(2,?)', (_now(),))
        if 3 not in versions: store.db.execute('INSERT INTO school_service_migrations VALUES(3,?)', (_now(),))
        if 4 not in versions: store.db.execute('INSERT INTO school_service_migrations VALUES(4,?)', (_now(),))
        if 5 not in versions: store.db.execute('INSERT INTO school_service_migrations VALUES(5,?)', (_now(),))
        if 6 not in versions: store.db.execute('INSERT INTO school_service_migrations VALUES(6,?)', (_now(),))
        if 7 not in versions: store.db.execute('INSERT INTO school_service_migrations VALUES(7,?)', (_now(),))
        if 8 not in versions: store.db.execute('INSERT INTO school_service_migrations VALUES(8,?)', (_now(),))
        store.db.execute('INSERT INTO school_service_migrations VALUES(9,?)', (_now(),))
        store.db.commit()
    except Exception:
        store.db.rollback()
        raise
    return backup


def _identifier(value, label):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:@-]{0,119}', value):
        raise ValueError('Invalid ' + label)
    return value


def configure_school(store, code, name, *, enabled=True, upload_enabled=True):
    _identifier(code, 'school code')
    if not name.strip() or len(name) > 300: raise ValueError('Invalid school name')
    store.db.execute('INSERT INTO school_service_schools VALUES(?,?,?,?,?) ON CONFLICT(code) DO UPDATE'
        ' SET name=excluded.name,enabled=excluded.enabled,upload_enabled=excluded.upload_enabled,updated_at=excluded.updated_at',
        (code, name.strip(), int(enabled), int(upload_enabled), _now()))
    store.db.execute("INSERT OR IGNORE INTO school_service_sources(school,id,kind,allowed_url,enabled) VALUES(?,'upload','manual','',1)", (code,))
    store.db.commit()


def grant_member(store, school, subject, role, *, active=True):
    _identifier(subject, 'subject')
    if role not in (*WRITE_ROLES, 'reader'): raise ValueError('Invalid role')
    store.db.execute('INSERT INTO school_service_members VALUES(?,?,?,?) ON CONFLICT(school,subject) DO UPDATE'
        ' SET role=excluded.role,active=excluded.active', (school, subject, role, int(active)))
    store.db.commit()


def require_member(store, principal, *, write=False, upload=False):
    row = store.db.execute('SELECT s.*,m.role FROM school_service_members m JOIN school_service_schools s'
        ' ON s.code=m.school WHERE m.school=? AND m.subject=? AND m.active=1 AND s.enabled=1',
        (principal.school, principal.subject)).fetchone()
    if not row or write and row['role'] not in WRITE_ROLES or upload and not row['upload_enabled']:
        raise PermissionError('School access denied')
    return dict(row)


def _url(value):
    if not isinstance(value, str) or len(value) > 2000: raise ValueError('Invalid source URL')
    decoded = value
    for _ in range(4): decoded = unquote(decoded)
    parts = urlsplit(decoded)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.fragment or \
       any(ord(c) < 33 for c in decoded) or '\\' in decoded or any(p in ('.', '..') for p in parts.path.split('/')):
        raise ValueError('Source requires an explicit HTTPS origin and safe path')
    # Accessing port validates malformed/out-of-range ports.
    origin = (parts.scheme, parts.hostname.lower(), parts.port or 443)
    return origin, parts.path or '/'


def _in_scope(allowed, actual):
    origin, prefix = _url(allowed)
    other, path = _url(actual)
    base = prefix.rstrip('/')
    if origin != other or not (path == base or path.startswith(base + '/')):
        raise ValueError('Source outside configured scope')


def add_source(store, school, ident, *, kind='manual', allowed_url='', enabled=True):
    _identifier(ident, 'source id')
    if kind not in ('manual', 'test_adapter'): raise ValueError('Live website intake is not enabled')
    if kind == 'test_adapter': _url(allowed_url)
    elif allowed_url: raise ValueError('Manual source cannot fetch URLs')
    old = store.db.execute('SELECT * FROM school_service_sources WHERE school=? AND id=?', (school, ident)).fetchone()
    if old and (old['kind'], old['allowed_url']) != (kind, allowed_url):
        raise ValueError('Source identity/scope is immutable; provision a new source')
    store.db.execute('INSERT INTO school_service_sources(school,id,kind,allowed_url,enabled) VALUES(?,?,?,?,?)'
        ' ON CONFLICT(school,id) DO UPDATE SET enabled=excluded.enabled',
        (school, ident, kind, allowed_url, int(enabled)))
    store.db.commit()


def _source(store, school, ident):
    row = store.db.execute('SELECT * FROM school_service_sources WHERE school=? AND id=?', (school, ident)).fetchone()
    if not row or not row['enabled']: raise PermissionError('Source disabled or missing')
    return dict(row)


def _checked(incoming):
    if not isinstance(incoming, IncomingPost) or not incoming.key.strip() or len(incoming.key) > 500:
        raise ValueError('A stable source post key is required')
    if not incoming.title.strip() or len(incoming.title) > 300 or len(incoming.body_html.encode()) > 200_000:
        raise ValueError('Invalid title/body')
    if incoming.source_url: canonical_url(incoming.source_url)
    body = ('<!doctype html><html><head><meta charset="utf-8"></head><body>' +
            incoming.body_html + '</body></html>').encode()
    raw = ([IncomingFile('본문.html', body, 'convert')] if incoming.body_html.strip() else []) + list(incoming.files)
    if not 1 <= len(raw) <= mobile.MAX_FILES or sum(len(f.data) for f in raw) > mobile.MAX_UPLOAD_BYTES:
        raise ValueError('Upload count/size limit exceeded')
    files, seen = [], {}
    for item in raw:
        if item.role not in mobile.ROLES: raise ValueError('Invalid file role')
        meta = mobile.validate_file(item.data, item.filename)
        sha = digest(item.data)
        if sha in seen and seen[sha] != item.role: raise ValueError('Conflicting roles for identical content')
        if sha not in seen:
            files.append(IncomingFile(meta['filename'], item.data, item.role))
            seen[sha] = item.role
    fingerprint = digest(encode({'title': incoming.title.strip(),
        'guides': [digest(f.data) for f in files if f.role == 'convert'],
        'attachments': sorted(digest(f.data) for f in files if f.role == 'attachment')}).encode())
    return files, fingerprint


def _ingest(store, principal, source_id, incoming, *, manual):
    files, fingerprint = _checked(incoming)  # Validate the WHOLE upload before any objects are written.
    try:
        store.db.execute('BEGIN IMMEDIATE')
        require_member(store, principal, write=True, upload=manual)
        source = _source(store, principal.school, source_id)
        if source['kind'] != ('manual' if manual else 'test_adapter'): raise PermissionError('Wrong source kind')
        if not manual: _in_scope(source['allowed_url'], incoming.source_url)
        post = store.db.execute('SELECT * FROM school_service_posts WHERE school=? AND source_id=? AND post_key=?',
            (principal.school, source_id, incoming.key)).fetchone()
        if post:
            old = store.db.execute('SELECT id FROM school_service_versions WHERE post_id=? AND fingerprint=?', (post['id'], fingerprint)).fetchone()
            if old:
                if manual:
                    store.db.execute("UPDATE school_service_sources SET status='ok',last_attempt=?,last_success=?,last_error=NULL WHERE school=? AND id=?", (_now(), _now(), principal.school, source_id))
                store.db.commit()
                return Receipt(post['id'], old['id'], True)  # Never roll latest_version back on replay.
        now = _now()
        pid, vid = (post['id'] if post else uuid.uuid4().hex), uuid.uuid4().hex
        if not post:
            store.db.execute('INSERT INTO school_service_posts VALUES(?,?,?,?,?,NULL,?,?)',
                (pid, principal.school, source_id, incoming.key, incoming.title.strip(), now, now))
        mid = mobile.create_upload(store, [(f.filename, f.data) for f in files], incoming.title,
            [i for i, f in enumerate(files) if f.role == 'attachment'], evaluation=True, commit=False)
        saved = json.loads(mobile.notice_row(store, mid, school=True)['files'])
        for f, original in zip(saved, files): f['role'] = original.role
        store.db.execute('UPDATE mobile_notices SET files=? WHERE id=?', (encode(saved), mid))
        sequence = store.db.execute('SELECT COALESCE(max(sequence),0)+1 FROM school_service_versions WHERE post_id=?', (pid,)).fetchone()[0]
        store.db.execute('INSERT INTO school_service_versions VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (vid, principal.school, pid, sequence, fingerprint, mid, post['latest_version'] if post else None,
             canonical_url(incoming.source_url) if incoming.source_url else '', principal.subject, now, 'private'))
        store.db.execute('UPDATE school_service_posts SET title=?,latest_version=?,updated_at=? WHERE id=?',
            (incoming.title.strip(), vid, now, pid))
        if manual:
            store.db.execute("UPDATE school_service_sources SET status='ok',last_attempt=?,last_success=?,last_error=NULL WHERE school=? AND id=?", (now, now, principal.school, source_id))
        store.db.commit()
        return Receipt(pid, vid, False)
    except Exception:
        store.db.rollback()
        raise


def upload(store, principal, incoming):
    require_member(store, principal, write=True, upload=True)
    return _ingest(store, principal, 'upload', incoming, manual=True)


def list_notices(store, principal):
    require_member(store, principal)
    return [dict(r) for r in store.db.execute('SELECT * FROM school_service_posts WHERE school=? ORDER BY updated_at DESC,id', (principal.school,))]


def get_version(store, principal, ident):
    require_member(store, principal)
    row = store.db.execute('SELECT v.*,p.title,p.source_id,p.post_key FROM school_service_versions v'
        ' JOIN school_service_posts p ON p.id=v.post_id WHERE v.school=? AND v.id=?', (principal.school, ident)).fetchone()
    if not row: raise LookupError('Notice not found')
    mobile_row = mobile.notice_row(store, row['mobile_id'], school=True)
    return {**dict(row), 'title': mobile_row['title'], 'files': json.loads(mobile_row['files']),
            'state': mobile_row['state'], 'revision': mobile_row['latest_revision']}


def source_status(store, principal, source_id=None):
    require_member(store, principal)
    if source_id: return _source(store, principal.school, source_id)
    return [dict(r) for r in store.db.execute('SELECT * FROM school_service_sources WHERE school=? ORDER BY id', (principal.school,))]


def structure_for(store, principal, ident, *, revision_id=''):
    """Read only this school's immutable receiving version + conversion revision."""
    version = get_version(store, principal, ident)
    revision = mobile.revision(store, version['mobile_id'], revision_id, school=True)
    if not revision or not revision['data'].get('structure'): raise LookupError('Structure not available')
    return {**revision['data']['structure'], 'notice_version':ident, 'conversion_revision':revision['id']}


def _enqueue(store, kind, payload, reference):
    try:
        store.db.execute('BEGIN IMMEDIATE')
        active = store.db.execute("SELECT id,payload FROM jobs WHERE kind=? AND ref_id=? AND status IN ('pending','running')", (kind, reference)).fetchone()
        if active:
            comparable = lambda p: {k: v for k, v in p.items() if k not in ('token', 'subject', 'error_type')}
            if comparable(json.loads(active['payload'])) != comparable(payload):
                raise ValueError('A different request is already queued; wait for its result')
            store.db.commit()
            return active['id']
        # A fresh token makes a completed job replay-safe without reusing its result.
        payload = {**payload, 'token': uuid.uuid4().hex}
        store.db.execute('INSERT INTO jobs(kind,ref_id,status,updated,payload) VALUES(?,?,?,?,?)'
            " ON CONFLICT(kind,ref_id) DO UPDATE SET status='pending',attempts=0,next_attempt=NULL,owner=NULL,updated=excluded.updated,payload=excluded.payload",
            (kind, reference, 'pending', _now(), encode(payload)))
        ident = store.db.execute('SELECT id FROM jobs WHERE kind=? AND ref_id=?', (kind, reference)).fetchone()[0]
        if kind == 'school_convert':
            store.db.execute("UPDATE mobile_notices SET state='processing',updated_at=? WHERE id=(SELECT mobile_id FROM school_service_versions WHERE id=? AND school=?)", (_now(), payload['version'], payload['school']))
        store.db.commit()
        return ident
    except Exception:
        store.db.rollback()
        raise


def request_receive(store, principal, source_id):
    require_member(store, principal, write=True)
    if _source(store, principal.school, source_id)['kind'] != 'test_adapter': raise PermissionError('Not an enabled test adapter')
    return _enqueue(store, 'school_receive', {'school': principal.school, 'subject': principal.subject, 'source': source_id}, principal.school + '/' + source_id)


def request_conversion(store, principal, ident, edits=None, expected_revision=None):
    version = get_version(store, principal, ident)
    require_member(store, principal, write=True)
    if expected_revision is not None and expected_revision != (version['revision'] or ''): raise ValueError('Stale revision')
    files = mobile.file_roles(version['files'], edits or {})  # Validate and normalize before enqueue.
    edits = {f['id']: {k: f.get(k) for k in ('role', 'scope', 'form_start', 'boundary_confirmed', 'incomplete')} for f in files}
    return _enqueue(store, 'school_convert', {'school': principal.school, 'subject': principal.subject,
        'version': ident, 'base_revision': version['revision'] or '', 'edits': edits}, ident)


def _claim(store, kind, ident=None):
    # Unlike the legacy claim API, a SELECT+UPDATE is a single writer transaction.
    try:
        store.db.execute('BEGIN IMMEDIATE')
        row = store.db.execute("SELECT * FROM jobs WHERE kind=? AND status='pending' AND (next_attempt IS NULL OR next_attempt<=?) AND (? IS NULL OR id=?) ORDER BY id LIMIT 1", (kind, _now(), ident, ident)).fetchone()
        if row:
            owner = uuid.uuid4().hex
            store.db.execute("UPDATE jobs SET status='running',attempts=attempts+1,owner=?,updated=? WHERE id=? AND status='pending'", (owner, _now(), row['id']))
            row = dict(row); row.update(owner=owner, attempts=row['attempts'] + 1)
        store.db.commit()
        return row
    except Exception:
        store.db.rollback()
        raise


def _finish(store, job, error=None):
    payload = json.loads(job['payload'])
    status = 'done' if error is None else ('failed' if job['attempts'] >= MAX_ATTEMPTS else 'pending')
    if error is not None: payload['error_type'] = type(error).__name__  # Never persist arbitrary exception text/URLs/secrets.
    else: payload.pop('error_type', None)
    next_attempt = (kst_now() + timedelta(seconds=min(3600, 30 * 2 ** job['attempts']))).isoformat() if status == 'pending' else None
    changed = store.db.execute("UPDATE jobs SET status=?,payload=?,next_attempt=?,owner=NULL,updated=? WHERE id=? AND owner=? AND status='running'",
        (status, encode(payload), next_attempt, _now(), job['id'], job['owner'])).rowcount
    if not changed:
        store.db.rollback()
        return 'stale'
    if job['kind'] == 'school_convert' and status == 'failed':
        store.db.execute("UPDATE mobile_notices SET state='conversion_failed',updated_at=? WHERE id=(SELECT mobile_id FROM school_service_versions WHERE id=? AND school=?)", (_now(), payload['version'], payload['school']))
    store.db.commit()
    return 'retry' if status == 'pending' else status


def process_receiving(store, adapters: dict[tuple[str, str], Adapter], *, limit=1, job_id=None):
    results = []
    for _ in range(limit):
        job = _claim(store, 'school_receive', job_id)
        if not job: break
        p = json.loads(job['payload']); principal = Principal(p['school'], p['subject'])
        try:
            require_member(store, principal, write=True)
            source = _source(store, p['school'], p['source'])
            if source['kind'] != 'test_adapter': raise PermissionError('Live adapter disabled')
            store.db.execute('UPDATE school_service_sources SET last_attempt=? WHERE school=? AND id=?', (_now(), p['school'], p['source'])); store.db.commit()
            batch = adapters[(p['school'], p['source'])].fetch(source, source['cursor'])
            if not isinstance(batch, ReceiveBatch) or len(batch.posts) > 100 or len(batch.cursor) > 2000: raise ValueError('Invalid adapter batch')
            for item in batch.posts:
                _in_scope(source['allowed_url'], item.source_url)
                _checked(item)
            for item in batch.posts: _ingest(store, principal, p['source'], item, manual=False)
            store.db.execute("UPDATE school_service_sources SET status='ok',last_success=?,last_error=NULL,cursor=? WHERE school=? AND id=?",
                (_now(), batch.cursor, p['school'], p['source']))
            results.append(_finish(store, job))
        except Exception as exc:
            store.db.rollback()
            store.db.execute("UPDATE school_service_sources SET status='error',last_attempt=?,last_error=? WHERE school=? AND id=?", (_now(), type(exc).__name__, p['school'], p['source']))
            results.append(_finish(store, job, exc))
    return results


def process_conversions(store, *, limit=1):
    results = []
    for _ in range(limit):
        job = _claim(store, 'school_convert')
        if not job: break
        p = json.loads(job['payload']); principal = Principal(p['school'], p['subject'])
        try:
            version = get_version(store, principal, p['version'])
            require_member(store, principal, write=True)
            prior = mobile.revision(store, version['mobile_id'], school=True)
            if not prior or prior['data'].get('school_job_token') != p['token']:
                if (version['revision'] or '') != p['base_revision']: raise ValueError('Stale conversion request')
                # Parsing has no DB writer transaction/global HTTP lock. A
                # dedicated worker connection lets staff keep reading/uploading.
                data = mobile.build_conversion(store, version['mobile_id'], p.get('edits'), use_approved_rules=False, school=True)
                from .document_structure import build_analysis
                data['structure'] = build_analysis(store, data['files'])
                data['school_job_token'] = p['token']
                store.db.execute('BEGIN IMMEDIATE')
                lease = store.db.execute("SELECT 1 FROM jobs WHERE id=? AND owner=? AND status='running'", (job['id'], job['owner'])).fetchone()
                if not lease:
                    store.db.rollback(); results.append('stale'); continue
                # Membership/revision may have changed during a slow parser.
                require_member(store, principal, write=True)
                current = get_version(store, principal, p['version'])
                if (current['revision'] or '') != p['base_revision']: raise ValueError('Stale conversion request')
                mobile.save_revision(store, version['mobile_id'], data, export_rules=False, school=True, commit=False)
            results.append(_finish(store, job))
        except Exception as exc:
            store.db.rollback()
            results.append(_finish(store, job, exc))
    return results


def retry_job(store, principal, ident):
    """Explicit staff retry only for this school's failed jobs, never research jobs."""
    require_member(store, principal, write=True)
    job = store.db.execute('SELECT * FROM jobs WHERE id=? AND kind IN (?,?)', (ident, *KINDS)).fetchone()
    if not job or json.loads(job['payload'])['school'] != principal.school: raise LookupError('Job not found')
    if job['status'] != 'failed': raise ValueError('Only failed jobs may be explicitly retried')
    payload = json.loads(job['payload']); payload['subject'] = principal.subject
    store.db.execute("UPDATE jobs SET status='pending',attempts=0,next_attempt=NULL,owner=NULL,updated=?,payload=? WHERE id=?", (_now(), encode(payload), ident))
    if job['kind'] == 'school_convert':
        store.db.execute("UPDATE mobile_notices SET state='processing' WHERE id=(SELECT mobile_id FROM school_service_versions WHERE id=? AND school=?)", (payload['version'], principal.school))
    store.db.commit()


def recover_stale(store, *, seconds=3600):
    """Opt-in crash recovery, scoped to school jobs; replay tokens prevent duplicate revisions."""
    if seconds < 3600: raise ValueError('Lease must allow slow local converters to finish')
    cutoff = (kst_now() - timedelta(seconds=seconds)).isoformat()
    count = store.db.execute("UPDATE jobs SET status='pending',owner=NULL,next_attempt=NULL WHERE kind IN (?,?) AND status='running' AND updated<?", (*KINDS, cutoff)).rowcount
    store.db.commit()
    return count
