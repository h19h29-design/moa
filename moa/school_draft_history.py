"""Read-only history of one owner's immutable, same-target input snapshots."""
from . import school_review_drafts as drafts

SCOPE = ('school', 'subject', 'notice_version', 'conversion_revision', 'target_key')


def history(store, principal, ident, *, page=1):
    if type(page) is not int or not 1 <= page <= 1_000_000:
        raise ValueError('Invalid draft history page')
    store.db.execute('SAVEPOINT private_draft_history')
    try:
        anchor = drafts.get(store, principal, ident)
        params = tuple(anchor[k] for k in SCOPE)
        scope = ' FROM school_review_drafts WHERE ' + ' AND '.join(k+'=?' for k in SCOPE)
        head = dict(store.db.execute('SELECT '+drafts.METADATA+scope+
            ' ORDER BY sequence DESC LIMIT 1', params).fetchone())
        # Keep pagination fixed to the chosen snapshot even as autosave appends.
        scope += ' AND sequence<=?'
        params += (anchor['sequence'],)
        total = store.db.execute('SELECT count(*)'+scope, params).fetchone()[0]
        pages = max(1, (total+19)//20); page = min(page, pages)
        rows = [dict(r) for r in store.db.execute('SELECT '+drafts.METADATA+',previous_id'+scope+
            ' ORDER BY sequence DESC LIMIT 20 OFFSET ?', (*params, (page-1)*20))]
        return dict(anchor=anchor, head=head, rows=rows, total=total, page=page, pages=pages)
    finally:
        store.db.execute('RELEASE private_draft_history')


def pair(store, principal, before_id, after_id):
    drafts.key(before_id); drafts.key(after_id)
    store.db.execute('SAVEPOINT private_draft_pair')
    try:
        before = drafts.get(store, principal, before_id)
        after = drafts.get(store, principal, after_id)
        if any(before[k] != after[k] for k in (*SCOPE, 'action_kind')):
            raise LookupError('Draft history scope not found')
        if before['sequence'] > after['sequence']:
            raise ValueError('Choose snapshots in ascending order')
        return dict(before=before, after=after,
            same_base=all(before[k] == after[k] for k in ('base_hash', 'base_review')))
    finally:
        store.db.execute('RELEASE private_draft_pair')
