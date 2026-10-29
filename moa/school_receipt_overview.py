"""Read-only, owner-scoped receipt search across saved confirmed batches."""
import csv
import io

from . import school_confirmed_export as batch, school_confirmed_export_saved as saved
from . import school_confirmed_export_downloads as downloads
from . import school_confirmed_export_management as management, school_service as svc, school_review as review
from .school_saved_queue import text

PAGE_SIZE = 20


def _selection(principal, q, after, before, state):
    """Share exact filters and owner scope between the screen and CSV."""
    q = text(q, 120); after = management._date(after); before = management._date(before)
    if state not in ('all', 'active', 'archived') or after and before and after > before:
        raise ValueError('Invalid receipt overview filter')
    filters = dict(q=q, after=after, before=before, state=state)
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
    return filters, prefix, where, params


def _available(store):
    return saved.available(store) and management.available(store) and downloads.available(store)


def listing(store, principal, *, q='', after='', before='', state='all', page=1):
    """Keep totals, current archive state and bounded rows in one read snapshot."""
    downloads._page(page)
    filters, prefix, where, params = _selection(principal, q, after, before, state)
    with batch._snapshot(store):
        svc.require_member(store, principal)
        empty = dict(rows=[], page=1, pages=1, total=0, batch_count=0, zip_bytes_total=0,
            filters=filters, migration_required=not _available(store))
        if empty['migration_required']: return empty
        totals = dict(store.db.execute(prefix+'SELECT count(*) AS total,count(DISTINCT list_id) AS batch_count,'
            ' COALESCE(sum(zip_bytes),0) AS zip_bytes_total FROM visible'+where, params).fetchone())
        pages = max(1, (totals['total']+PAGE_SIZE-1)//PAGE_SIZE); page = min(page, pages)
        rows = [dict(r) for r in store.db.execute(prefix+'SELECT * FROM visible'+where+
            ' ORDER BY created_at DESC,list_id DESC,version DESC LIMIT ? OFFSET ?',
            (*params, PAGE_SIZE, (page-1)*PAGE_SIZE))]
        return {**empty, **totals, 'rows':rows, 'page':page, 'pages':pages}


def csv_report(store, principal, *, q='', after='', before='', state='all'):
    """All matching batches, one bounded snapshot; never create a receipt."""
    _, prefix, where, params = _selection(principal, q, after, before, state)
    with batch._snapshot(store):
        svc.require_member(store, principal)
        if not _available(store): raise review.Conflict('Explicit receipt overview migration required')
        rows = store.db.execute(prefix+'SELECT * FROM visible'+where+
            ' ORDER BY created_at DESC,list_id DESC,version DESC LIMIT ?',
            (*params, downloads.MAX_EXPORT_ROWS+1)).fetchall()
        if len(rows) > downloads.MAX_EXPORT_ROWS:
            raise downloads.ExportTooLarge('Narrow the receipt name, date or state filters')
        output = io.StringIO(newline='')
        writer = csv.writer(output, quoting=csv.QUOTE_ALL, lineterminator='\r\n')
        writer.writerow(['묶음 ID', '묶음 이름', '다운로드 회차', '기록 시각 (한국 시간)',
            '문서 수', 'ZIP 바이트', 'ZIP SHA-256', '묶음 현재 상태'])
        for row in rows:
            values = (row['list_id'], row['name'], row['version'], row['created_at'],
                row['item_count'], row['zip_bytes'], row['zip_sha256'],
                '보관함' if row['state']=='archived' else '사용 중')
            writer.writerow([downloads._csv_text(v) for v in values])
        return output.getvalue().encode('utf-8-sig')
