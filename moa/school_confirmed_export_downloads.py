"""Owner-private, append-only delivery receipts for saved export batches.

Receipts record that a saved batch ZIP was built and handed to the client. They
never gate delivery, never rewrite frozen pins and never touch originals.
"""
import csv
from datetime import date
import hashlib
import io
import re

from . import school_confirmed_export as batch, school_confirmed_export_saved as saved
from . import school_review as review, school_service as svc
from .school_saved_queue import key

SCHEMA_V9 = '''
CREATE TABLE school_confirmed_export_downloads(
 list_id TEXT NOT NULL, school TEXT NOT NULL, subject TEXT NOT NULL,
 version INTEGER NOT NULL CHECK(version BETWEEN 1 AND 2147483647),
 item_count INTEGER NOT NULL CHECK(item_count BETWEEN 1 AND 10),
 zip_sha256 TEXT NOT NULL CHECK(length(zip_sha256)=64 AND zip_sha256 NOT GLOB '*[^0-9a-f]*'),
 zip_bytes INTEGER NOT NULL CHECK(zip_bytes BETWEEN 1 AND 100663296),
 created_at TEXT NOT NULL,
 PRIMARY KEY(list_id,version),
 FOREIGN KEY(list_id,school) REFERENCES school_confirmed_export_lists(id,school),
 FOREIGN KEY(school,subject) REFERENCES school_service_members(school,subject));
CREATE TRIGGER school_confirmed_export_download_owner BEFORE INSERT ON school_confirmed_export_downloads
 WHEN NOT EXISTS(SELECT 1 FROM school_confirmed_export_lists l WHERE l.id=NEW.list_id
 AND l.school=NEW.school AND l.subject=NEW.subject)
 BEGIN SELECT RAISE(ABORT,'Saved export owner'); END;
CREATE TRIGGER school_confirmed_export_download_sequence BEFORE INSERT ON school_confirmed_export_downloads
 WHEN NEW.version != COALESCE((SELECT max(version) FROM school_confirmed_export_downloads
 WHERE list_id=NEW.list_id),0)+1
 BEGIN SELECT RAISE(ABORT,'Saved export receipt sequence'); END;
CREATE TRIGGER school_confirmed_export_downloads_no_update BEFORE UPDATE ON school_confirmed_export_downloads
 BEGIN SELECT RAISE(ABORT,'Saved export receipts are immutable'); END;
CREATE TRIGGER school_confirmed_export_downloads_no_delete BEFORE DELETE ON school_confirmed_export_downloads
 BEGIN SELECT RAISE(ABORT,'Saved export receipts are immutable'); END;
'''
PAGE_SIZE = 20
MAX_VERSION = 2147483647
MAX_EXPORT_ROWS = 5000


class ExportTooLarge(ValueError):
    """The requested receipt report must be narrowed, never silently truncated."""


def available(store):
    return bool(store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
        " AND name='school_confirmed_export_downloads'").fetchone())


def _page(value):
    if type(value) is not int or not 1 <= value <= 1_000_000:
        raise ValueError('Invalid saved export receipt page')
    return value


def _owner(store, principal, ident):
    """Owner check shared by record/read; never inspects documents."""
    key(ident)
    svc.require_member(store, principal)
    if not saved.available(store): raise review.Conflict('Explicit export list migration required')
    row = store.db.execute('SELECT name,item_count FROM school_confirmed_export_lists'
        ' WHERE id=? AND school=? AND subject=?', (ident, principal.school, principal.subject)).fetchone()
    if row is None: raise LookupError('Saved export list not found')
    return row


def record(store, principal, ident, raw):
    """Append one delivery receipt for the exact bytes handed to the client."""
    if not isinstance(raw, (bytes, bytearray)) or not 0 < len(raw) <= batch.MAX_CONTENT_BYTES:
        raise ValueError('Invalid saved export bundle')
    digest = hashlib.sha256(bytes(raw)).hexdigest()
    try:
        store.db.execute('BEGIN IMMEDIATE')
        row = _owner(store, principal, ident)
        if not available(store): raise review.Conflict('Explicit export receipt migration required')
        version = store.db.execute('SELECT COALESCE(max(version),0)+1'
            ' FROM school_confirmed_export_downloads WHERE list_id=?', (ident,)).fetchone()[0]
        if version > MAX_VERSION: raise review.Conflict('Saved export receipt sequence exhausted')
        store.db.execute('INSERT INTO school_confirmed_export_downloads VALUES(?,?,?,?,?,?,?,?)',
            (ident, principal.school, principal.subject, version, row['item_count'],
             digest, len(raw), svc._now()))
        store.db.commit()
        return version
    except Exception:
        store.db.rollback()
        raise


def _selection(principal, ident, after, before):
    dates = []
    for value in (after, before):
        if not isinstance(value, str) or value and not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
            raise ValueError('Invalid receipt date')
        dates.append(date.fromisoformat(value).isoformat() if value else '')
    after, before = dates
    if after and before and after > before: raise ValueError('Invalid receipt date range')
    where = ' WHERE list_id=? AND school=? AND subject=?'
    params = [ident, principal.school, principal.subject]
    # Receipt timestamps are written by svc._now() in KST. Filter their local
    # date, not SQLite date(), which normalizes offset timestamps to UTC.
    if after: where += ' AND substr(created_at,1,10)>=?'; params.append(after)
    if before: where += ' AND substr(created_at,1,10)<=?'; params.append(before)
    return {'after':after, 'before':before}, where, params


def listing(store, principal, ident, *, page=1, after='', before=''):
    """Bounded receipt metadata; works even when frozen pins are stale."""
    _page(page)
    filters, where, params = _selection(principal, ident, after, before)
    with batch._snapshot(store):
        _owner(store, principal, ident)
        empty = dict(rows=[], page=1, pages=1, total=0, filters=filters, receipt_required=not available(store))
        if empty['receipt_required']: return empty
        total = store.db.execute('SELECT count(*) FROM school_confirmed_export_downloads'+where, params).fetchone()[0]
        pages = max(1, (total+PAGE_SIZE-1)//PAGE_SIZE); page = min(page, pages)
        rows = [dict(r) for r in store.db.execute('SELECT version,item_count,zip_sha256,zip_bytes,created_at'
            ' FROM school_confirmed_export_downloads'+where+
            ' ORDER BY version DESC LIMIT ? OFFSET ?', (*params, PAGE_SIZE, (page-1)*PAGE_SIZE))]
        return {**empty, 'rows':rows, 'page':page, 'pages':pages, 'total':total}


def _csv_text(value):
    text = str(value)
    # Quote CSV delimiters separately; quoting alone does not prevent spreadsheet
    # formula execution. Preserve the stored value and escape only this export.
    if text[:1] in ('\t', '\r', '\n') or text.lstrip()[:1] in ('=', '+', '-', '@'):
        return "'"+text
    return text


def csv_report(store, principal, ident, *, after='', before=''):
    """Export all matching receipt metadata in one bounded, read-only snapshot."""
    _, where, params = _selection(principal, ident, after, before)
    with batch._snapshot(store):
        owner = _owner(store, principal, ident)
        if not available(store): raise review.Conflict('Explicit export receipt migration required')
        rows = store.db.execute('SELECT version,created_at,item_count,zip_bytes,zip_sha256'
            ' FROM school_confirmed_export_downloads'+where+' ORDER BY version DESC LIMIT ?',
            (*params, MAX_EXPORT_ROWS+1)).fetchall()
        if len(rows) > MAX_EXPORT_ROWS: raise ExportTooLarge('Narrow the receipt date range')
        output = io.StringIO(newline='')
        writer = csv.writer(output, quoting=csv.QUOTE_ALL, lineterminator='\r\n')
        writer.writerow(['묶음 ID', '묶음 이름', '다운로드 회차', '기록 시각 (한국 시간)', '문서 수', 'ZIP 바이트', 'ZIP SHA-256'])
        for row in rows:
            writer.writerow([_csv_text(v) for v in (ident, owner['name'], *row)])
        return output.getvalue().encode('utf-8-sig')


def latest(store, principal, ids):
    """Counts and last delivery per listed batch, inside the caller's snapshot."""
    ids = [key(ident) for ident in ids][:PAGE_SIZE]
    if not ids or not available(store): return {}
    marks = ','.join('?'*len(ids))
    rows = store.db.execute('SELECT d.list_id AS list_id, count(*) AS total,'
        ' (SELECT e.created_at FROM school_confirmed_export_downloads e'
        '  WHERE e.list_id=d.list_id ORDER BY e.version DESC LIMIT 1) AS created_at'
        ' FROM school_confirmed_export_downloads d WHERE d.school=? AND d.subject=?'
        ' AND d.list_id IN ('+marks+') GROUP BY d.list_id',
        (principal.school, principal.subject, *ids))
    return {r['list_id']:{'total':r['total'], 'created_at':r['created_at']} for r in rows}
