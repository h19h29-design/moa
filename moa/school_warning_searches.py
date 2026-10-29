"""Owner-private immutable warning conditions, pinned to one conversion."""
from urllib.parse import urlencode
from . import school_service as svc, school_review as review, school_warning_search as search
from .school_confirmed_export import _snapshot
from .school_saved_queue import key, text

SCHEMA_V11 = '''
CREATE TABLE school_warning_searches(
 id TEXT PRIMARY KEY CHECK(length(id)=24), school TEXT NOT NULL, subject TEXT NOT NULL,
 name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 120),
 notice_version TEXT NOT NULL REFERENCES school_service_versions(id),
 conversion_revision TEXT NOT NULL REFERENCES mobile_revisions(id),
 title TEXT NOT NULL, q TEXT NOT NULL CHECK(length(q)<=120),
 file_filter TEXT NOT NULL CHECK(length(file_filter)<=512),
 code_filter TEXT NOT NULL CHECK(length(code_filter)<=512),
 file_label TEXT NOT NULL, code_label TEXT NOT NULL, created_at TEXT NOT NULL,
 FOREIGN KEY(school,subject) REFERENCES school_service_members(school,subject));
CREATE INDEX school_warning_searches_owner
 ON school_warning_searches(school,subject,created_at DESC,id DESC);
CREATE TRIGGER school_warning_searches_scope BEFORE INSERT ON school_warning_searches
 WHEN NOT EXISTS(SELECT 1 FROM school_service_versions v
 JOIN mobile_revisions r ON r.notice_id=v.mobile_id
 WHERE v.school=NEW.school AND v.id=NEW.notice_version AND r.id=NEW.conversion_revision)
 BEGIN SELECT RAISE(ABORT,'Saved warning search scope'); END;
CREATE TRIGGER school_warning_searches_no_update BEFORE UPDATE ON school_warning_searches
 BEGIN SELECT RAISE(ABORT,'Saved warning conditions are immutable'); END;
CREATE TRIGGER school_warning_searches_no_delete BEFORE DELETE ON school_warning_searches
 BEGIN SELECT RAISE(ABORT,'Saved warning conditions are immutable'); END;
'''
PAGE_SIZE = 20


def available(store):
    return bool(store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='school_warning_searches'").fetchone())


def _row(row):
    data = dict(row)
    data['filters'] = dict(q=data.pop('q'), file=data.pop('file_filter'), code=data.pop('code_filter'))
    return data


def get(store, principal, ident):
    key(ident)
    with _snapshot(store):
        svc.require_member(store, principal)
        if not available(store): raise review.Conflict('Explicit warning search migration required')
        row = store.db.execute('SELECT * FROM school_warning_searches WHERE id=? AND school=? AND subject=?',
            (ident,principal.school,principal.subject)).fetchone()
        if row is None: raise LookupError('Saved warning search not found')
        return _row(row)


def create(store, principal, ident, *, name, notice, revision, q='', file='', code=''):
    key(ident); name = text(name,120,required=True)
    review._text(file,512); review._text(code,512)
    try:
        store.db.execute('BEGIN IMMEDIATE')
        svc.require_member(store,principal,write=True)
        if not available(store): raise review.Conflict('Explicit warning search migration required')
        # A colliding form ID never exposes or overwrites another owner's row.
        old = get(store,principal,ident) if store.db.execute(
            'SELECT 1 FROM school_warning_searches WHERE id=?',(ident,)).fetchone() else None
        data = search.listing(store,principal,notice,revision,q=q,file=file,code=code)
        filters = data['filters']
        if len(urlencode({'id':notice,'revision':revision,**filters}))>4096:
            raise ValueError('Saved warning URL too large')
        if old:
            if (old['name'],old['notice_version'],old['conversion_revision'],old['filters']) != (name,notice,revision,filters):
                raise review.Conflict('Saved warning conditions changed')
        else:
            def label(options, value):
                return next(o['label'] for o in options if o['value']==value) if value else '전체'
            title = svc.get_version(store,principal,notice)['title']
            store.db.execute('INSERT INTO school_warning_searches VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (ident,principal.school,principal.subject,name,notice,revision,title,filters['q'],
                 filters['file'],filters['code'],label(data['files'],file),label(data['codes'],code),svc._now()))
        store.db.commit()
    except Exception:
        store.db.rollback()
        raise
    return ident


def listing(store, principal, *, page=1):
    if type(page) is not int or not 1<=page<=1_000_000: raise ValueError('Invalid saved warning page')
    with _snapshot(store):
        svc.require_member(store,principal)
        if not available(store): return dict(rows=[],page=1,pages=1,total=0,migration_required=True)
        params=(principal.school,principal.subject)
        total=store.db.execute('SELECT count(*) FROM school_warning_searches WHERE school=? AND subject=?',params).fetchone()[0]
        pages=max(1,(total+PAGE_SIZE-1)//PAGE_SIZE); page=min(page,pages)
        rows=[_row(r) for r in store.db.execute('SELECT * FROM school_warning_searches WHERE school=? AND subject=?'
            ' ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?',(*params,PAGE_SIZE,(page-1)*PAGE_SIZE))]
        return dict(rows=rows,page=page,pages=pages,total=total,migration_required=False)


def resolve(store, principal, ident):
    with _snapshot(store):
        row=get(store,principal,ident)
        search.listing(store,principal,row['notice_version'],row['conversion_revision'],**row['filters'])
        return row
