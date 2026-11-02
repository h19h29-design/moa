"""Read-only school review overview. No migration, parser, model or job dispatch.

Counts and pagination use metadata, never whole review overlays/audits. Only the
displayed page inspects bounded conversion payloads for structure availability.
Stored review status is not a fresh integrity check or publication approval.
"""
from __future__ import annotations

from . import school_review, school_service as svc

PAGE_SIZE = 20
STATUS = {'unreviewed':'미검수', 'draft':'수정 중', 'confirmed':'확인 완료', 'conversion_failed':'변환 오류'}


def _validate(q, status, page):
    if (not isinstance(q, str) or len(q) > 200 or any(ord(c) < 32 or ord(c) == 127 for c in q)
        or not isinstance(status, str) or status not in ('', *STATUS)
        or type(page) is not int or not 1 <= page <= 1_000_000):
        raise ValueError('Invalid overview query')
    return q.strip()


def _latest_query(installed):
    # Both branches are fixed SQL, never request input. v1 remains read-only;
    # merely opening this screen must not install the optional review schema.
    fields = ('e.id AS review_id,e.sequence AS review_sequence,e.status AS review_status,'
              'e.actor AS review_actor,e.created_at AS review_at') if installed else (
              'NULL AS review_id,NULL AS review_sequence,NULL AS review_status,'
              'NULL AS review_actor,NULL AS review_at')
    join = '''LEFT JOIN school_structure_reviews e ON e.id=(
        SELECT id FROM school_structure_reviews WHERE school=p.school AND post_id=p.id
        AND notice_version=v.id AND conversion_revision=r.id ORDER BY sequence DESC LIMIT 1)''' if installed else ''
    return '''WITH metadata AS (
        SELECT p.id,p.title,p.source_id,p.updated_at,v.id AS notice_version,v.sequence,
               n.state,r.id AS revision,r.sequence AS conversion_sequence,'''+fields+'''
        FROM school_service_posts p
        LEFT JOIN school_service_versions v ON v.school=p.school AND v.post_id=p.id AND v.id=p.latest_version
        LEFT JOIN mobile_notices n ON n.id=v.mobile_id
        LEFT JOIN mobile_revisions r ON r.notice_id=n.id AND r.id=n.latest_revision
        '''+join+'''
        WHERE p.school=?), latest AS (
        SELECT *,CASE WHEN state='conversion_failed' THEN 'conversion_failed'
                      WHEN state='processing' OR revision IS NULL THEN 'unreviewed'
                      ELSE COALESCE(review_status,'unreviewed') END AS status
        FROM metadata) '''


def overview(store, principal, *, q='', status='', page=1):
    """Latest receiving/conversion status, literal title filter and bounded page.

    School-wide counts do not change with filters. New receipts/conversions do
    not inherit old confirmations. Processing/failed conversions cannot appear
    confirmed, even if a prior saved conversion has a confirmation. A savepoint
    provides one read snapshot across counts/rows and preserves any caller txn.
    """
    q = _validate(q, status, page)
    store.db.execute('SAVEPOINT school_overview')
    try:
        school = svc.require_member(store, principal)
        installed = school_review.available(store)
        latest = _latest_query(installed)
        counts = dict.fromkeys(STATUS, 0)
        for row in store.db.execute(latest+'SELECT status,count(*) AS n FROM latest GROUP BY status', (principal.school,)):
            counts[row['status']] = row['n']
        # Treat SQL wildcard characters as text; all values are bound parameters.
        literal = q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        where = " WHERE title LIKE ? ESCAPE '\\' AND (?='' OR status=?)"
        params = (principal.school, '%'+literal+'%', status, status)
        total = store.db.execute(latest+'SELECT count(*) FROM latest'+where, params).fetchone()[0]
        pages = max(1, (total+PAGE_SIZE-1)//PAGE_SIZE)
        page = min(page, pages)
        rows = [dict(r) for r in store.db.execute(latest+'SELECT * FROM latest'+where+
            ' ORDER BY updated_at DESC,id LIMIT ? OFFSET ?', (*params, PAGE_SIZE, (page-1)*PAGE_SIZE))]
        for row in rows:
            row['structure_available'] = False
            if row['revision']:
                # JSON evaluation is deliberately after pagination and capped.
                ready = store.db.execute('''SELECT CASE
                    WHEN length(CAST(payload AS BLOB))>? THEN 0
                    WHEN NOT json_valid(payload) THEN 0
                    ELSE COALESCE(json_type(payload,'$.structure')='object'
                         AND json_type(payload,'$.structure.blocks')='array',0) END
                    FROM mobile_revisions WHERE id=?''', (school_review.MAX_VIEW_BYTES, row['revision'])).fetchone()
                row['structure_available'] = bool(ready and ready[0])
        return {'school_name':school['name'], 'counts':counts, 'school_total':sum(counts.values()),
                'total':total, 'page':page, 'pages':pages, 'rows':rows, 'q':q, 'status':status,
                'migration_required':not installed}
    finally:
        store.db.execute('RELEASE school_overview')
