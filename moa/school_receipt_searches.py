"""Durable owner-private receipt conditions, never a frozen receipt result set."""
from . import school_confirmed_export as batch, school_receipt_overview as overview
from . import school_confirmed_export_downloads as downloads, school_review as review, school_service as svc
from .school_saved_queue import key, text

SCHEMA_V10 = '''
CREATE TABLE school_receipt_searches(
 id TEXT PRIMARY KEY CHECK(length(id)=24), school TEXT NOT NULL, subject TEXT NOT NULL,
 name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 120),
 q TEXT NOT NULL CHECK(length(q)<=120),
 after_date TEXT NOT NULL CHECK(length(after_date) IN (0,10)),
 before_date TEXT NOT NULL CHECK(length(before_date) IN (0,10)),
 state TEXT NOT NULL CHECK(state IN ('all','active','archived')), created_at TEXT NOT NULL,
 FOREIGN KEY(school,subject) REFERENCES school_service_members(school,subject));
CREATE INDEX school_receipt_searches_owner
 ON school_receipt_searches(school,subject,created_at DESC,id DESC);
CREATE TRIGGER school_receipt_searches_no_update BEFORE UPDATE ON school_receipt_searches
 BEGIN SELECT RAISE(ABORT,'Saved receipt conditions are immutable'); END;
CREATE TRIGGER school_receipt_searches_no_delete BEFORE DELETE ON school_receipt_searches
 BEGIN SELECT RAISE(ABORT,'Saved receipt conditions are immutable'); END;
'''
PAGE_SIZE = 20


def available(store):
    return bool(store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='school_receipt_searches'").fetchone())


def _row(row):
    data = dict(row)
    data['filters'] = overview.filters(q=data.pop('q'), after=data.pop('after_date'),
        before=data.pop('before_date'), state=data.pop('state'))
    return data


def get(store, principal, ident):
    key(ident)
    with batch._snapshot(store):
        svc.require_member(store, principal)
        if not available(store): raise review.Conflict('Explicit receipt search migration required')
        row = store.db.execute('SELECT * FROM school_receipt_searches WHERE id=? AND school=? AND subject=?',
            (ident, principal.school, principal.subject)).fetchone()
        if row is None: raise LookupError('Saved receipt search not found')
        return _row(row)


def create(store, principal, ident, *, name, q='', after='', before='', state='all'):
    """One immutable preset per form ID; identical retries do not duplicate it."""
    key(ident); name = text(name, 120, required=True)
    selected = overview.filters(q=q, after=after, before=before, state=state)
    try:
        store.db.execute('BEGIN IMMEDIATE')
        svc.require_member(store, principal, write=True)
        if not available(store): raise review.Conflict('Explicit receipt search migration required')
        if store.db.execute('SELECT 1 FROM school_receipt_searches WHERE id=?', (ident,)).fetchone():
            row = get(store, principal, ident)
            if row['name'] != name or row['filters'] != selected:
                raise review.Conflict('Saved receipt conditions changed')
        else:
            store.db.execute('INSERT INTO school_receipt_searches VALUES(?,?,?,?,?,?,?,?,?)',
                (ident, principal.school, principal.subject, name, selected['q'], selected['after'],
                 selected['before'], selected['state'], svc._now()))
        store.db.commit()
    except Exception:
        store.db.rollback()
        raise
    return ident


def listing(store, principal, *, page=1):
    downloads._page(page)
    with batch._snapshot(store):
        svc.require_member(store, principal)
        if not available(store): return dict(rows=[], page=1, pages=1, total=0, migration_required=True)
        params = (principal.school, principal.subject)
        total = store.db.execute('SELECT count(*) FROM school_receipt_searches WHERE school=? AND subject=?', params).fetchone()[0]
        pages = max(1, (total+PAGE_SIZE-1)//PAGE_SIZE); page = min(page, pages)
        rows = [_row(r) for r in store.db.execute('SELECT * FROM school_receipt_searches WHERE school=? AND subject=?'
            ' ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?', (*params, PAGE_SIZE, (page-1)*PAGE_SIZE))]
        return dict(rows=rows, page=page, pages=pages, total=total, migration_required=False)
