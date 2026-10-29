"""Read-only, owner-scoped receipt search across saved confirmed batches."""
from . import school_confirmed_export as batch, school_confirmed_export_saved as saved
from . import school_confirmed_export_downloads as downloads
from . import school_confirmed_export_management as management, school_service as svc
from .school_saved_queue import text

PAGE_SIZE = 20


def listing(store, principal, *, q='', after='', before='', state='all', page=1):
    """Keep totals, current archive state and bounded rows in one read snapshot."""
    q = text(q, 120); after = management._date(after); before = management._date(before)
    downloads._page(page)
    if state not in ('all', 'active', 'archived') or after and before and after > before:
        raise ValueError('Invalid receipt overview filter')
    filters = dict(q=q, after=after, before=before, state=state)
    with batch._snapshot(store):
        svc.require_member(store, principal)
        empty = dict(rows=[], page=1, pages=1, total=0, batch_count=0, zip_bytes_total=0,
            filters=filters, migration_required=not (
                saved.available(store) and management.available(store) and downloads.available(store)))
        if empty['migration_required']: return empty
        prefix = ("WITH visible AS (SELECT d.list_id,l.name,d.version,d.created_at,d.item_count,"
            "d.zip_bytes,d.zip_sha256,CASE WHEN (SELECT e.action FROM school_confirmed_export_list_events e"
            " WHERE e.list_id=l.id AND e.school=l.school AND e.subject=l.subject"
            " ORDER BY e.version DESC LIMIT 1)='archive' THEN 'archived' ELSE 'active' END AS state"
            " FROM school_confirmed_export_lists l JOIN school_confirmed_export_downloads d"
            " ON d.list_id=l.id AND d.school=l.school AND d.subject=l.subject"
            " WHERE l.school=? AND l.subject=?) ")
        clauses = []; params = [principal.school, principal.subject]
        if q: clauses.append('instr(lower(name),lower(?))>0'); params.append(q)
        # svc._now() records KST. Keep the inclusive local date, as on the
        # existing per-batch receipt screen; do not normalize these dates to UTC.
        if after: clauses.append('substr(created_at,1,10)>=?'); params.append(after)
        if before: clauses.append('substr(created_at,1,10)<=?'); params.append(before)
        if state != 'all': clauses.append('state=?'); params.append(state)
        where = ' WHERE '+' AND '.join(clauses) if clauses else ''
        totals = dict(store.db.execute(prefix+'SELECT count(*) AS total,count(DISTINCT list_id) AS batch_count,'
            ' COALESCE(sum(zip_bytes),0) AS zip_bytes_total FROM visible'+where, params).fetchone())
        pages = max(1, (totals['total']+PAGE_SIZE-1)//PAGE_SIZE); page = min(page, pages)
        rows = [dict(r) for r in store.db.execute(prefix+'SELECT * FROM visible'+where+
            ' ORDER BY created_at DESC,list_id DESC,version DESC LIMIT ? OFFSET ?',
            (*params, PAGE_SIZE, (page-1)*PAGE_SIZE))]
        return {**empty, **totals, 'rows':rows, 'page':page, 'pages':pages}
