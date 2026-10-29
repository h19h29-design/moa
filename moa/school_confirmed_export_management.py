"""Owner-private saved-list search and append-only archive/restore metadata.

Archiving hides a list from the default directory. Frozen pins, names, originals,
review events and download validation are independent and remain unchanged.
"""
from datetime import date
import re

from . import school_confirmed_export as batch, school_confirmed_export_saved as saved
from . import school_confirmed_export_downloads as downloads
from . import school_review as review, school_service as svc
from .school_saved_queue import key, text

SCHEMA_V8 = '''
CREATE TABLE school_confirmed_export_list_events(
 list_id TEXT NOT NULL, school TEXT NOT NULL, subject TEXT NOT NULL,
 version INTEGER NOT NULL CHECK(version BETWEEN 1 AND 2147483647),
 action TEXT NOT NULL CHECK(action IN ('archive','restore')), created_at TEXT NOT NULL,
 PRIMARY KEY(list_id,version),
 FOREIGN KEY(list_id,school) REFERENCES school_confirmed_export_lists(id,school),
 FOREIGN KEY(school,subject) REFERENCES school_service_members(school,subject));
CREATE TRIGGER school_confirmed_export_event_owner BEFORE INSERT ON school_confirmed_export_list_events
 WHEN NOT EXISTS(SELECT 1 FROM school_confirmed_export_lists l WHERE l.id=NEW.list_id
 AND l.school=NEW.school AND l.subject=NEW.subject)
 BEGIN SELECT RAISE(ABORT,'Saved export owner'); END;
CREATE TRIGGER school_confirmed_export_event_transition BEFORE INSERT ON school_confirmed_export_list_events
 WHEN NEW.version != COALESCE((SELECT max(version) FROM school_confirmed_export_list_events
 WHERE list_id=NEW.list_id),0)+1 OR NEW.action != CASE WHEN
 (SELECT action FROM school_confirmed_export_list_events WHERE list_id=NEW.list_id
 ORDER BY version DESC LIMIT 1)='archive' THEN 'restore' ELSE 'archive' END
 BEGIN SELECT RAISE(ABORT,'Saved export transition'); END;
CREATE TRIGGER school_confirmed_export_events_no_update BEFORE UPDATE ON school_confirmed_export_list_events
 BEGIN SELECT RAISE(ABORT,'Saved export history is immutable'); END;
CREATE TRIGGER school_confirmed_export_events_no_delete BEFORE DELETE ON school_confirmed_export_list_events
 BEGIN SELECT RAISE(ABORT,'Saved export history is immutable'); END;
'''
PAGE_SIZE = 20
MAX_VERSION = 2147483647


def available(store):
    return bool(store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
        " AND name='school_confirmed_export_list_events'").fetchone())


def _page(value):
    if type(value) is not int or not 1 <= value <= 1_000_000:
        raise ValueError('Invalid saved export page')
    return value


def _date(value):
    if not isinstance(value,str): raise ValueError('Invalid saved export date')
    if not value: return ''
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',value):
        raise ValueError('Invalid saved export date')
    return date.fromisoformat(value).isoformat()


def _metadata(store, principal, ident):
    key(ident);svc.require_member(store,principal)
    if not saved.available(store): raise review.Conflict('Explicit export list migration required')
    row=store.db.execute('SELECT * FROM school_confirmed_export_lists WHERE id=? AND school=? AND subject=?',
        (ident,principal.school,principal.subject)).fetchone()
    if row is None: raise LookupError('Saved export list not found')
    row=dict(row);row.update(state='active',version=0,management_required=not available(store))
    if not row['management_required']:
        event=store.db.execute('SELECT version,action FROM school_confirmed_export_list_events'
            ' WHERE list_id=? AND school=? AND subject=? ORDER BY version DESC LIMIT 1',
            (ident,principal.school,principal.subject)).fetchone()
        if event: row.update(version=event['version'],state='archived' if event['action']=='archive' else 'active')
    return row


def get(store, principal, ident, *, page=1):
    """Bounded metadata/history only; usable even when document pins are stale."""
    _page(page)
    with batch._snapshot(store):
        row=_metadata(store,principal,ident)
        params=(ident,principal.school,principal.subject)
        total=0 if row['management_required'] else store.db.execute(
            'SELECT count(*) FROM school_confirmed_export_list_events WHERE list_id=? AND school=? AND subject=?',params).fetchone()[0]
        pages=max(1,(total+PAGE_SIZE-1)//PAGE_SIZE);page=min(page,pages)
        events=[] if row['management_required'] else [dict(r) for r in store.db.execute(
            'SELECT version,action,created_at FROM school_confirmed_export_list_events'
            ' WHERE list_id=? AND school=? AND subject=? ORDER BY version DESC LIMIT ? OFFSET ?',
            (*params,PAGE_SIZE,(page-1)*PAGE_SIZE))]
        row.update(events=events,total=total,page=page,pages=pages)
        return row


def listing(store, principal, *, q='', after='', before='', state='active', page=1):
    """Search literal names and inclusive KST saved dates in one read snapshot."""
    q=text(q,120);after=_date(after);before=_date(before);_page(page)
    if state not in ('active','archived','all') or after and before and after>before:
        raise ValueError('Invalid saved export filter')
    filters=dict(q=q,after=after,before=before,state=state)
    with batch._snapshot(store):
        svc.require_member(store,principal)
        installed=available(store)
        empty=dict(rows=[],page=1,pages=1,total=0,filters=filters,
            migration_required=not saved.available(store),management_required=not installed)
        if empty['migration_required']: return empty
        version="COALESCE((SELECT max(e.version) FROM school_confirmed_export_list_events e WHERE e.list_id=l.id),0)" if installed else '0'
        archived="(SELECT e.action FROM school_confirmed_export_list_events e WHERE e.list_id=l.id ORDER BY e.version DESC LIMIT 1)='archive'" if installed else '0'
        prefix=('WITH visible AS (SELECT l.id,l.name,l.item_count,l.created_at,'+version+' AS version,'
            "CASE WHEN "+archived+" THEN 'archived' ELSE 'active' END AS state FROM school_confirmed_export_lists l"
            ' WHERE l.school=? AND l.subject=?) ')
        clauses=[];params=[principal.school,principal.subject]
        if q: clauses.append('instr(lower(name),lower(?))>0');params.append(q)
        if after: clauses.append('substr(created_at,1,10)>=?');params.append(after)
        if before: clauses.append('substr(created_at,1,10)<=?');params.append(before)
        if state!='all': clauses.append('state=?');params.append(state)
        where=' WHERE '+' AND '.join(clauses) if clauses else ''
        total=store.db.execute(prefix+'SELECT count(*) FROM visible'+where,params).fetchone()[0]
        pages=max(1,(total+PAGE_SIZE-1)//PAGE_SIZE);page=min(page,pages)
        rows=[dict(r) for r in store.db.execute(prefix+'SELECT * FROM visible'+where+
            ' ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?',(*params,PAGE_SIZE,(page-1)*PAGE_SIZE))]
        # Delivery counts come from the same read snapshot as the directory rows.
        if rows and downloads.available(store):
            marks=downloads.latest(store,principal,[r['id'] for r in rows])
            for r in rows: r['download']=marks.get(r['id'])
        return {**empty,'rows':rows,'page':page,'pages':pages,'total':total}


def change(store, principal, ident, *, action, expected):
    """Append one transition, checking membership and the displayed version anew."""
    key(ident)
    if action not in ('archive','restore') or type(expected) is not int or not 0<=expected<MAX_VERSION:
        raise ValueError('Invalid saved export transition')
    try:
        store.db.execute('BEGIN IMMEDIATE')
        svc.require_member(store,principal,write=True)
        row=_metadata(store,principal,ident)
        if row['management_required']: raise review.Conflict('Explicit export management migration required')
        wanted='archive' if row['state']=='active' else 'restore'
        if expected!=row['version'] or action!=wanted:
            raise review.Conflict('Saved export management changed')
        version=expected+1
        store.db.execute('INSERT INTO school_confirmed_export_list_events VALUES(?,?,?,?,?,?)',
            (ident,principal.school,principal.subject,version,action,svc._now()))
        store.db.commit()
        return version
    except Exception:
        store.db.rollback()
        raise
