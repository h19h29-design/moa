"""Private frozen review lists and explicit, optimistic resume checkpoints.

Only the list cursor changes. Original documents, conversions, research queues
and append-only review events are independent. No migration on read or HTTP.
"""
import json
import re

from . import school_overview, school_review, school_service as svc
from .core import encode

SCHEMA_V3 = '''
CREATE TABLE school_review_lists(
 id TEXT PRIMARY KEY CHECK(length(id)=24), school TEXT NOT NULL, subject TEXT NOT NULL,
 name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 120), query TEXT NOT NULL,
 item_count INTEGER NOT NULL CHECK(item_count BETWEEN 1 AND 20),
 cursor INTEGER NOT NULL CHECK(cursor BETWEEN 1 AND item_count),
 version INTEGER NOT NULL CHECK(version>0), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(id,school), FOREIGN KEY(school,subject) REFERENCES school_service_members(school,subject));
CREATE INDEX school_review_lists_owner ON school_review_lists(school,subject,updated_at DESC,id DESC);
CREATE TABLE school_review_list_items(
 list_id TEXT NOT NULL, school TEXT NOT NULL, position INTEGER NOT NULL CHECK(position BETWEEN 1 AND 20),
 notice_version TEXT NOT NULL REFERENCES school_service_versions(id),
 conversion_revision TEXT NOT NULL REFERENCES mobile_revisions(id),
 title TEXT NOT NULL CHECK(length(title)<=300), PRIMARY KEY(list_id,position), UNIQUE(list_id,notice_version),
 FOREIGN KEY(list_id,school) REFERENCES school_review_lists(id,school));
CREATE TRIGGER school_review_list_item_scope BEFORE INSERT ON school_review_list_items
 WHEN NOT EXISTS(SELECT 1 FROM school_review_lists l
 JOIN school_service_versions v ON v.school=l.school
 JOIN mobile_revisions r ON r.notice_id=v.mobile_id
 WHERE l.id=NEW.list_id AND l.school=NEW.school AND v.id=NEW.notice_version
 AND r.id=NEW.conversion_revision AND NEW.position<=l.item_count)
 BEGIN SELECT RAISE(ABORT,'School review list scope'); END;
CREATE TRIGGER school_review_list_items_no_update BEFORE UPDATE ON school_review_list_items
 BEGIN SELECT RAISE(ABORT,'Frozen review list'); END;
CREATE TRIGGER school_review_list_items_no_delete BEFORE DELETE ON school_review_list_items
 BEGIN SELECT RAISE(ABORT,'Frozen review list'); END;
CREATE TRIGGER school_review_list_fixed BEFORE UPDATE ON school_review_lists
 WHEN NEW.id IS NOT OLD.id OR NEW.school IS NOT OLD.school OR NEW.subject IS NOT OLD.subject
 OR NEW.name IS NOT OLD.name OR NEW.query IS NOT OLD.query OR NEW.item_count IS NOT OLD.item_count
 OR NEW.created_at IS NOT OLD.created_at
 BEGIN SELECT RAISE(ABORT,'Frozen review list'); END;
CREATE TRIGGER school_review_lists_no_delete BEFORE DELETE ON school_review_lists
 BEGIN SELECT RAISE(ABORT,'Saved review list'); END;
'''
PAGE_SIZE = 20


def available(store):
    return bool(store.db.execute("SELECT 1 FROM sqlite_master WHERE name='school_review_lists'").fetchone())


def key(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{24}',value):
        raise ValueError('Invalid saved list')
    return value


def text(value, limit, *, required=False):
    if (not isinstance(value,str) or len(value)>limit or any(ord(c)<32 or ord(c)==127 for c in value)
            or required and not value.strip()):
        raise ValueError('Invalid list text')
    value.encode('utf-8')
    return value.strip()


def _load(store, principal, ident):
    svc.require_member(store,principal)
    key(ident)
    if not available(store): raise school_review.Conflict('Explicit list migration required')
    row = store.db.execute('SELECT * FROM school_review_lists WHERE id=? AND school=? AND subject=?',
        (ident,principal.school,principal.subject)).fetchone()
    if not row: raise LookupError('Saved list not found')
    return dict(row)


def get(store, principal, ident):
    # One read snapshot across metadata/items, including another process's move.
    store.db.execute('SAVEPOINT saved_review_list')
    try:
        row = _load(store,principal,ident)
        items = [dict(r) for r in store.db.execute('SELECT notice_version AS id,conversion_revision AS revision,'
            'title,position FROM school_review_list_items WHERE list_id=? AND school=? ORDER BY position LIMIT 21',
            (ident,principal.school))]
        if len(items)!=row['item_count'] or [r.pop('position') for r in items]!=list(range(1,len(items)+1)):
            raise school_review.Conflict('Saved list is incomplete')
        row['snapshot'] = {'school':principal.school,'subject':principal.subject,
            'query':json.loads(row.pop('query')),'items':items}
        return row
    finally:
        store.db.execute('RELEASE saved_review_list')


def create(store, principal, ident, snapshot, *, name, cursor):
    key(ident); name = text(name,120,required=True)
    if snapshot.get('school')!=principal.school or snapshot.get('subject')!=principal.subject:
        raise PermissionError('List owner mismatch')
    query, items = snapshot['query'], snapshot['items']
    if set(query)!={'q','status','page'}: raise ValueError('Invalid list filter')
    school_overview._validate(**query)
    if type(cursor) is not int or not 1<=cursor<=len(items)<=20: raise ValueError('Invalid list size/position')
    for item in items:
        if set(item)!={'id','revision','title'}: raise ValueError('Invalid list item')
        text(item['title'],300)
    try:
        store.db.execute('BEGIN IMMEDIATE')
        svc.require_member(store,principal,write=True)
        if not available(store): raise school_review.Conflict('Explicit list migration required')
        existing = store.db.execute('SELECT 1 FROM school_review_lists WHERE id=?',(ident,)).fetchone()
        if existing:
            # A repeated save never resets a later checkpoint or renames a list.
            saved = get(store,principal,ident)
            if saved['snapshot']!=snapshot: raise school_review.Conflict('Frozen list mismatch')
        else:
            for item in items:
                owned = store.db.execute('SELECT 1 FROM school_service_versions v JOIN mobile_revisions r'
                    ' ON r.notice_id=v.mobile_id WHERE v.school=? AND v.id=? AND r.id=?',
                    (principal.school,item['id'],item['revision'])).fetchone()
                if not owned: raise LookupError('Pinned document not found')
            now = svc._now()
            store.db.execute('INSERT INTO school_review_lists VALUES(?,?,?,?,?,?,?,?,?,?)',
                (ident,principal.school,principal.subject,name,encode(query),len(items),cursor,1,now,now))
            store.db.executemany('INSERT INTO school_review_list_items VALUES(?,?,?,?,?,?)',
                [(ident,principal.school,i,item['id'],item['revision'],item['title']) for i,item in enumerate(items,1)])
        store.db.commit()
    except Exception:
        store.db.rollback()
        raise
    return ident


def move(store, principal, ident, *, target, expected_version):
    if (type(target) is not int or not 1<=target<=20 or type(expected_version) is not int
            or not 1<=expected_version<2**63-1):
        raise ValueError('Invalid list checkpoint')
    try:
        store.db.execute('BEGIN IMMEDIATE')
        svc.require_member(store,principal,write=True)
        row = _load(store,principal,ident)
        if row['version']!=expected_version: raise school_review.Conflict('Checkpoint changed in another tab')
        if target>row['item_count']: raise ValueError('Invalid list position')
        store.db.execute('UPDATE school_review_lists SET cursor=?,version=version+1,updated_at=? WHERE id=?',
            (target,svc._now(),ident))
        store.db.commit()
    except Exception:
        store.db.rollback()
        raise


def listing(store, principal, *, page=1):
    if type(page) is not int or not 1<=page<=1_000_000: raise ValueError('Invalid saved list page')
    store.db.execute('SAVEPOINT saved_review_lists')
    try:
        svc.require_member(store,principal)
        if not available(store): return {'rows':[],'page':1,'pages':1,'total':0,'migration_required':True}
        params = (principal.school,principal.subject)
        total = store.db.execute('SELECT count(*) FROM school_review_lists WHERE school=? AND subject=?',params).fetchone()[0]
        pages = max(1,(total+PAGE_SIZE-1)//PAGE_SIZE); page = min(page,pages)
        rows = [dict(r) for r in store.db.execute('SELECT id,name,cursor,item_count,created_at,updated_at'
            ' FROM school_review_lists WHERE school=? AND subject=? ORDER BY updated_at DESC,id DESC LIMIT ? OFFSET ?',
            (*params,PAGE_SIZE,(page-1)*PAGE_SIZE))]
        return {'rows':rows,'page':page,'pages':pages,'total':total,'migration_required':False}
    finally:
        store.db.execute('RELEASE saved_review_lists')
