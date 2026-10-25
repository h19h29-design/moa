"""Read-only final review summary and the existing append-only confirmation."""
from . import school_review as review, school_review_compare as compare
from . import school_review_export as export, school_service as svc


def preview(store, principal, ident, *, revision_id, event_id):
    for value in (ident,revision_id,event_id): review._text(value,160,required=True)
    store.db.execute('SAVEPOINT school_review_check')
    try:
        current=review.view(store,principal,ident,revision_id=revision_id)
        selected,overlay,_=review._load(store,principal,ident,revision_id,event_id)
        saved=export.snapshot(store,principal,ident,revision_id=revision_id,event_id=event_id)
        differences=compare.compare(store,principal,ident,revision_id=revision_id,left='original',right=event_id)
        reasons=[]
        if svc.require_member(store,principal)['role'] not in svc.WRITE_ROLES: reasons.append('read_only')
        if current['migration_required']: reasons.append('migration_required')
        if current['stale']: reasons.append('version_changed')
        if not current['latest'] or current['latest']['id']!=event_id: reasons.append('review_changed')
        if selected['status']=='confirmed': reasons.append('already_confirmed')
        if not selected['analysis']['blocks']: reasons.append('no_blocks')
        elif selected['status']!='confirmed':
            try:
                review._change(selected['analysis'],overlay,{'kind':'confirm','acknowledge_warnings':True},selected['status'])
            except ValueError: reasons.append('invalid_relations')
        # Check the same immutable HTML that the sandboxed iframe will load.
        # Rendering is pure: no output files, model, jobs or new review event.
        export.render_html(saved)
        return {'view':selected,'current':current,'saved':saved,'summary':differences['summary'],
                'reasons':reasons,'can_confirm':not reasons}
    finally:
        store.db.execute('RELEASE school_review_check')


def confirm(store, principal, ident, *, revision_id, event_id, base_hash, comment, acknowledge):
    for value in (ident,revision_id,event_id): review._text(value,160,required=True)
    if acknowledge is not True: raise ValueError('Explicit final review acknowledgement required')
    # The existing writer rechecks membership, current receiving/conversion,
    # analysis hash, expected parent and relation validity under BEGIN IMMEDIATE.
    return review.save(store,principal,ident,revision_id=revision_id,base_hash=base_hash,
        expected_review=event_id,action={'kind':'confirm','acknowledge_warnings':True},comment=comment)
