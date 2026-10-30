"""Append-only display names; frozen warning conditions remain untouched."""
import unicodedata
from . import school_service as svc, school_review as review
from .school_confirmed_export import _snapshot
from .school_saved_queue import key, text

SCHEMA_V13 = '''
CREATE TABLE school_warning_search_names(
 search_id TEXT NOT NULL REFERENCES school_warning_searches(id), school TEXT NOT NULL, subject TEXT NOT NULL,
 version INTEGER NOT NULL CHECK(version BETWEEN 1 AND 2147483647),
 name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 120), created_at TEXT NOT NULL,
 PRIMARY KEY(search_id,version), FOREIGN KEY(school,subject) REFERENCES school_service_members(school,subject));
CREATE TRIGGER school_warning_search_name_owner BEFORE INSERT ON school_warning_search_names
 WHEN NOT EXISTS(SELECT 1 FROM school_warning_searches s WHERE s.id=NEW.search_id
 AND s.school=NEW.school AND s.subject=NEW.subject)
 BEGIN SELECT RAISE(ABORT,'Saved warning name owner'); END;
CREATE TRIGGER school_warning_search_name_version BEFORE INSERT ON school_warning_search_names
 WHEN NEW.version != COALESCE((SELECT max(version) FROM school_warning_search_names WHERE search_id=NEW.search_id),0)+1
 OR NEW.name = COALESCE((SELECT name FROM school_warning_search_names WHERE search_id=NEW.search_id ORDER BY version DESC LIMIT 1),
 (SELECT name FROM school_warning_searches WHERE id=NEW.search_id))
 BEGIN SELECT RAISE(ABORT,'Saved warning name transition'); END;
CREATE TRIGGER school_warning_search_names_no_update BEFORE UPDATE ON school_warning_search_names
 BEGIN SELECT RAISE(ABORT,'Saved warning name history is immutable'); END;
CREATE TRIGGER school_warning_search_names_no_delete BEFORE DELETE ON school_warning_search_names
 BEGIN SELECT RAISE(ABORT,'Saved warning name history is immutable'); END;
'''
MAX_VERSION=2147483647
PAGE_SIZE=20


def available(store):
    return bool(store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='school_warning_search_names'").fetchone())


def decorate(store,row,*,installed=None):
    installed=available(store) if installed is None else installed
    result=dict(row,original_name=row['name'],name_version=0,name_required=not installed)
    if 'display_name' in row:
        result.update(name=result.pop('display_name'),name_version=row['name_version'])
    elif installed:
        latest=store.db.execute('SELECT version,name FROM school_warning_search_names WHERE search_id=?'
            ' AND school=? AND subject=? ORDER BY version DESC LIMIT 1',(row['id'],row['school'],row['subject'])).fetchone()
        if latest:result.update(name=latest['name'],name_version=latest['version'])
    return result


def get(store,principal,ident,*,page=1):
    from . import school_warning_search_management as management
    management._page(page)
    with _snapshot(store):
        row=management._metadata(store,principal,ident);params=(ident,principal.school,principal.subject)
        total=0 if row['name_required'] else store.db.execute('SELECT count(*) FROM school_warning_search_names'
            ' WHERE search_id=? AND school=? AND subject=?',params).fetchone()[0]
        pages=max(1,(total+PAGE_SIZE-1)//PAGE_SIZE);page=min(page,pages)
        events=[] if row['name_required'] else [dict(r) for r in store.db.execute(
            'SELECT version,name,created_at FROM school_warning_search_names WHERE search_id=? AND school=? AND subject=?'
            ' ORDER BY version DESC LIMIT ? OFFSET ?',(*params,PAGE_SIZE,(page-1)*PAGE_SIZE))]
        return dict(row,events=events,total=total,page=page,pages=pages)


def _source(store,principal,row,source_version):
    if row['name_required'] or row['management_required']:
        raise review.Conflict('Explicit name migration required')
    if source_version==0:return dict(name=row['original_name'],created_at=row['created_at'])
    event=store.db.execute('SELECT name,created_at FROM school_warning_search_names WHERE search_id=?'
        ' AND school=? AND subject=? AND version=?',
        (row['id'],principal.school,principal.subject,source_version)).fetchone()
    if event is None:raise LookupError('Saved warning name not found')
    return dict(event)


def _source_version(value):
    if type(value) is not int or not 0<=value<=MAX_VERSION:raise ValueError('Invalid warning name source')


def selection(store,principal,ident,*,source_version):
    _source_version(source_version)
    with _snapshot(store):
        row=get(store,principal,ident);source=_source(store,principal,row,source_version)
        return dict(row,source_version=source_version,source_name=source['name'],source_created_at=source['created_at'])


def _change(store,principal,ident,*,name,source_version,expected,expected_state):
    key(ident)
    if any(type(n) is not int or not 0<=n<MAX_VERSION for n in (expected,expected_state)):
        raise ValueError('Invalid warning name version')
    try:
        store.db.execute('BEGIN IMMEDIATE');svc.require_member(store,principal,write=True)
        row=get(store,principal,ident)
        if row['name_required'] or row['management_required']:raise review.Conflict('Explicit name migration required')
        if source_version is not None:name=_source(store,principal,row,source_version)['name']
        if (expected,expected_state)!=(row['name_version'],row['version']) or name==row['name']:
            raise review.Conflict('Warning name or state changed, or same name')
        store.db.execute('INSERT INTO school_warning_search_names VALUES(?,?,?,?,?,?)',
            (ident,principal.school,principal.subject,expected+1,name,svc._now()))
        store.db.commit();return expected+1
    except Exception:
        store.db.rollback();raise


def rename(store,principal,ident,*,name,expected,expected_state):
    name=unicodedata.normalize('NFC',text(name,120,required=True))
    return _change(store,principal,ident,name=name,source_version=None,expected=expected,expected_state=expected_state)


def restore(store,principal,ident,*,source_version,expected,expected_state):
    _source_version(source_version)
    return _change(store,principal,ident,name=None,source_version=source_version,expected=expected,expected_state=expected_state)
