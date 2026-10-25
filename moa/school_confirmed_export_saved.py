"""Owner-private, immutable confirmed export lists; no original/review writes."""
import re

from . import school_confirmed_export as batch, school_review as review, school_service as svc
from .school_saved_queue import key, text

SCHEMA_V7 = '''
CREATE TABLE school_confirmed_export_lists(
 id TEXT PRIMARY KEY CHECK(length(id)=24), school TEXT NOT NULL, subject TEXT NOT NULL,
 name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 120),
 item_count INTEGER NOT NULL CHECK(item_count BETWEEN 1 AND 10), created_at TEXT NOT NULL,
 UNIQUE(id,school), FOREIGN KEY(school,subject) REFERENCES school_service_members(school,subject));
CREATE INDEX school_confirmed_export_lists_owner
 ON school_confirmed_export_lists(school,subject,created_at DESC,id DESC);
CREATE TABLE school_confirmed_export_items(
 list_id TEXT NOT NULL, school TEXT NOT NULL, position INTEGER NOT NULL CHECK(position BETWEEN 1 AND 10),
 notice_version TEXT NOT NULL, conversion_revision TEXT NOT NULL, review_id TEXT NOT NULL,
 snapshot_sha256 TEXT NOT NULL CHECK(length(snapshot_sha256)=64 AND snapshot_sha256 NOT GLOB '*[^0-9a-f]*'),
 title TEXT NOT NULL CHECK(length(title)<=300),
 PRIMARY KEY(list_id,position), UNIQUE(list_id,notice_version),
 FOREIGN KEY(list_id,school) REFERENCES school_confirmed_export_lists(id,school),
 FOREIGN KEY(school,notice_version,conversion_revision,review_id)
 REFERENCES school_structure_reviews(school,notice_version,conversion_revision,id));
CREATE TRIGGER school_confirmed_export_item_scope BEFORE INSERT ON school_confirmed_export_items
 WHEN NOT EXISTS(SELECT 1 FROM school_confirmed_export_lists l JOIN school_structure_reviews r
 ON r.school=l.school WHERE l.id=NEW.list_id AND l.school=NEW.school
 AND NEW.position<=l.item_count AND r.notice_version=NEW.notice_version
 AND r.conversion_revision=NEW.conversion_revision AND r.id=NEW.review_id AND r.status='confirmed')
 BEGIN SELECT RAISE(ABORT,'Confirmed export scope'); END;
CREATE TRIGGER school_confirmed_export_lists_no_update BEFORE UPDATE ON school_confirmed_export_lists
 BEGIN SELECT RAISE(ABORT,'Saved export list is immutable'); END;
CREATE TRIGGER school_confirmed_export_lists_no_delete BEFORE DELETE ON school_confirmed_export_lists
 BEGIN SELECT RAISE(ABORT,'Saved export list is immutable'); END;
CREATE TRIGGER school_confirmed_export_items_no_update BEFORE UPDATE ON school_confirmed_export_items
 BEGIN SELECT RAISE(ABORT,'Saved export pins are immutable'); END;
CREATE TRIGGER school_confirmed_export_items_no_delete BEFORE DELETE ON school_confirmed_export_items
 BEGIN SELECT RAISE(ABORT,'Saved export pins are immutable'); END;
'''
PAGE_SIZE = 20


def available(store):
    return store.db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN"
        " ('school_confirmed_export_lists','school_confirmed_export_items')").fetchone()[0]==2


def get(store, principal, ident):
    """Read saved pins even when their current confirmation is no longer usable."""
    key(ident)
    with batch._snapshot(store):
        svc.require_member(store,principal)
        if not available(store): raise review.Conflict('Explicit export list migration required')
        row=store.db.execute('SELECT * FROM school_confirmed_export_lists WHERE id=? AND school=? AND subject=?',
            (ident,principal.school,principal.subject)).fetchone()
        if row is None: raise LookupError('Saved export list not found')
        row=dict(row)
        items=[dict(r) for r in store.db.execute('SELECT notice_version AS id,conversion_revision AS revision,'
            'review_id AS event,snapshot_sha256,title,position FROM school_confirmed_export_items'
            ' WHERE list_id=? AND school=? ORDER BY position LIMIT 11',(ident,principal.school))]
        if (len(items)!=row['item_count'] or
            [r.pop('position') for r in items]!=list(range(1,len(items)+1)) or
            any(not re.fullmatch(r'[0-9a-f]{64}',r['snapshot_sha256']) for r in items)):
            raise review.Conflict('Saved export list is incomplete')
        row['items']=items
        row['pins']=[{k:r[k] for k in ('id','revision','event','snapshot_sha256')} for r in items]
        return row


def create(store, principal, ident, pins, *, name):
    """Atomically validate and save one prepared list, idempotent on exact replay."""
    key(ident); name=text(name,120,required=True)
    try:
        store.db.execute('BEGIN IMMEDIATE')
        svc.require_member(store,principal,write=True)
        if not available(store): raise review.Conflict('Explicit export list migration required')
        exists=store.db.execute('SELECT 1 FROM school_confirmed_export_lists WHERE id=?',(ident,)).fetchone()
        if exists:
            row=get(store,principal,ident)
            if row['name']!=name or row['pins']!=pins: raise review.Conflict('Saved export list changed')
        # Keep validation inside the write transaction so a concurrent edit
        # cannot slip between the confirmation check and the saved pins.
        data=batch.prepare(store,principal,pins,expected=True)
        if not exists:
            store.db.execute('INSERT INTO school_confirmed_export_lists VALUES(?,?,?,?,?,?)',
                (ident,principal.school,principal.subject,name,len(data['pins']),svc._now()))
            store.db.executemany('INSERT INTO school_confirmed_export_items VALUES(?,?,?,?,?,?,?,?)',
                [(ident,principal.school,i,pin['id'],pin['revision'],pin['event'],pin['snapshot_sha256'],d['title'])
                 for i,(pin,d) in enumerate(zip(data['pins'],data['documents']),1)])
        store.db.commit()
    except Exception:
        store.db.rollback()
        raise
    return ident


def listing(store, principal, *, page=1):
    if type(page) is not int or not 1<=page<=1_000_000: raise ValueError('Invalid saved export page')
    with batch._snapshot(store):
        svc.require_member(store,principal)
        if not available(store): return {'rows':[],'page':1,'pages':1,'total':0,'migration_required':True}
        params=(principal.school,principal.subject)
        total=store.db.execute('SELECT count(*) FROM school_confirmed_export_lists WHERE school=? AND subject=?',params).fetchone()[0]
        pages=max(1,(total+PAGE_SIZE-1)//PAGE_SIZE);page=min(page,pages)
        rows=[dict(r) for r in store.db.execute('SELECT id,name,item_count,created_at FROM school_confirmed_export_lists'
            ' WHERE school=? AND subject=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?',
            (*params,PAGE_SIZE,(page-1)*PAGE_SIZE))]
        return {'rows':rows,'page':page,'pages':pages,'total':total,'migration_required':False}
