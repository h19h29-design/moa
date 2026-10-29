"""Private metadata search and append-only warning preset archive history."""
import unicodedata

from . import school_warning_searches as saved, school_service as svc, school_review as review
from .school_confirmed_export import _snapshot
from .school_saved_queue import key, text

SCHEMA_V12 = '''
CREATE TABLE school_warning_search_events(
 search_id TEXT NOT NULL REFERENCES school_warning_searches(id), school TEXT NOT NULL, subject TEXT NOT NULL,
 version INTEGER NOT NULL CHECK(version BETWEEN 1 AND 2147483647),
 action TEXT NOT NULL CHECK(action IN ('archive','restore')), created_at TEXT NOT NULL,
 PRIMARY KEY(search_id,version), FOREIGN KEY(school,subject) REFERENCES school_service_members(school,subject));
CREATE TRIGGER school_warning_search_event_owner BEFORE INSERT ON school_warning_search_events
 WHEN NOT EXISTS(SELECT 1 FROM school_warning_searches s WHERE s.id=NEW.search_id
 AND s.school=NEW.school AND s.subject=NEW.subject)
 BEGIN SELECT RAISE(ABORT,'Saved warning search owner'); END;
CREATE TRIGGER school_warning_search_event_transition BEFORE INSERT ON school_warning_search_events
 WHEN NEW.version != COALESCE((SELECT max(version) FROM school_warning_search_events
 WHERE search_id=NEW.search_id),0)+1 OR NEW.action != CASE WHEN
 (SELECT action FROM school_warning_search_events WHERE search_id=NEW.search_id
 ORDER BY version DESC LIMIT 1)='archive' THEN 'restore' ELSE 'archive' END
 BEGIN SELECT RAISE(ABORT,'Saved warning search transition'); END;
CREATE TRIGGER school_warning_search_events_no_update BEFORE UPDATE ON school_warning_search_events
 BEGIN SELECT RAISE(ABORT,'Saved warning search history is immutable'); END;
CREATE TRIGGER school_warning_search_events_no_delete BEFORE DELETE ON school_warning_search_events
 BEGIN SELECT RAISE(ABORT,'Saved warning search history is immutable'); END;
'''
PAGE_SIZE = 20
MAX_VERSION = 2147483647


def available(store):
    return bool(store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='school_warning_search_events'").fetchone())


def _page(value):
    if type(value) is not int or not 1<=value<=1_000_000: raise ValueError('Invalid warning search management page')
    return value


def filters(*, q='', state='active'):
    q=unicodedata.normalize('NFC',text(q,120))
    if state not in ('active','archived','all'): raise ValueError('Invalid warning search state')
    return dict(q=q,state=state)


def _fold(value): return unicodedata.normalize('NFC',value.casefold())


def _metadata(store,principal,ident):
    row=saved.get(store,principal,ident)
    row.update(state='active',version=0,management_required=not available(store))
    if not row['management_required']:
        event=store.db.execute('SELECT version,action FROM school_warning_search_events'
            ' WHERE search_id=? AND school=? AND subject=? ORDER BY version DESC LIMIT 1',
            (ident,principal.school,principal.subject)).fetchone()
        if event: row.update(version=event['version'],state='archived' if event['action']=='archive' else 'active')
    return row


def get(store,principal,ident,*,page=1):
    _page(page)
    with _snapshot(store):
        row=_metadata(store,principal,ident); params=(ident,principal.school,principal.subject)
        total=0 if row['management_required'] else store.db.execute(
            'SELECT count(*) FROM school_warning_search_events WHERE search_id=? AND school=? AND subject=?',params).fetchone()[0]
        pages=max(1,(total+PAGE_SIZE-1)//PAGE_SIZE);page=min(page,pages)
        events=[] if row['management_required'] else [dict(r) for r in store.db.execute(
            'SELECT version,action,created_at FROM school_warning_search_events WHERE search_id=? AND school=? AND subject=?'
            ' ORDER BY version DESC LIMIT ? OFFSET ?',(*params,PAGE_SIZE,(page-1)*PAGE_SIZE))]
        return dict(row,events=events,total=total,page=page,pages=pages)


def listing(store,principal,*,q='',state='active',page=1):
    selected=filters(q=q,state=state);_page(page)
    with _snapshot(store):
        svc.require_member(store,principal);installed=available(store)
        empty=dict(rows=[],page=1,pages=1,total=0,filters=selected,
            migration_required=not saved.available(store),management_required=not installed)
        if empty['migration_required']: return empty
        version='COALESCE((SELECT max(e.version) FROM school_warning_search_events e WHERE e.search_id=s.id),0)' if installed else '0'
        action="(SELECT e.action FROM school_warning_search_events e WHERE e.search_id=s.id ORDER BY e.version DESC LIMIT 1)='archive'" if installed else '0'
        prefix=('WITH visible AS (SELECT s.*,'+version+" AS version,CASE WHEN "+action+
            " THEN 'archived' ELSE 'active' END AS state FROM school_warning_searches s WHERE s.school=? AND s.subject=?) ")
        clauses=[];params=[principal.school,principal.subject]
        if selected['q']:
            # Connection-local pure scalar function: no schema or cached result writes.
            store.db.create_function('moa_warning_condition_key',1,_fold,deterministic=True)
            fields=('name','title','q','file_label','code_label','file_filter','code_filter')
            clauses.append('instr(moa_warning_condition_key('+('||char(10)||'.join(fields))+'),?)>0')
            params.append(_fold(selected['q']))
        if state!='all': clauses.append('state=?');params.append(state)
        where=' WHERE '+' AND '.join(clauses) if clauses else ''
        total=store.db.execute(prefix+'SELECT count(*) FROM visible'+where,params).fetchone()[0]
        pages=max(1,(total+PAGE_SIZE-1)//PAGE_SIZE);page=min(page,pages)
        rows=[saved._row(r) for r in store.db.execute(prefix+'SELECT * FROM visible'+where+
            ' ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?',(*params,PAGE_SIZE,(page-1)*PAGE_SIZE))]
        return dict(empty,rows=rows,page=page,pages=pages,total=total)


def change(store,principal,ident,*,action,expected):
    key(ident)
    if action not in ('archive','restore') or type(expected) is not int or not 0<=expected<MAX_VERSION:
        raise ValueError('Invalid warning search transition')
    try:
        store.db.execute('BEGIN IMMEDIATE');svc.require_member(store,principal,write=True)
        row=_metadata(store,principal,ident)
        if row['management_required']: raise review.Conflict('Explicit warning management migration required')
        if expected!=row['version'] or action!=('archive' if row['state']=='active' else 'restore'):
            raise review.Conflict('Warning search state changed')
        version=expected+1
        store.db.execute('INSERT INTO school_warning_search_events VALUES(?,?,?,?,?,?)',
            (ident,principal.school,principal.subject,version,action,svc._now()))
        store.db.commit();return version
    except Exception:
        store.db.rollback();raise
