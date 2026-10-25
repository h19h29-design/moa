"""Explicit saved-input submission with private, immutable review provenance."""
import copy

from . import school_review as review, school_review_drafts as drafts, school_service as svc
from . import school_draft_compare as compare
from .core import encode

SCHEMA_V6 = '''
CREATE TABLE school_review_draft_handoffs(
 review_id TEXT PRIMARY KEY REFERENCES school_structure_reviews(id),
 draft_id TEXT NOT NULL UNIQUE REFERENCES school_review_drafts(id));
CREATE TRIGGER school_draft_handoff_scope BEFORE INSERT ON school_review_draft_handoffs
 WHEN NOT EXISTS(SELECT 1 FROM school_structure_reviews r JOIN school_review_drafts d ON d.id=NEW.draft_id
 WHERE r.id=NEW.review_id AND r.school=d.school AND r.actor=d.subject
 AND r.notice_version=d.notice_version AND r.conversion_revision=d.conversion_revision
 AND r.base_hash=d.base_hash AND coalesce(r.previous_id,'')=d.base_review
 AND r.action=d.action_kind AND r.status='draft' AND r.warnings_acknowledged=0)
 BEGIN SELECT RAISE(ABORT,'Invalid private input handoff'); END;
CREATE TRIGGER school_draft_handoff_no_update BEFORE UPDATE ON school_review_draft_handoffs
 BEGIN SELECT RAISE(ABORT,'Private input handoffs are immutable'); END;
CREATE TRIGGER school_draft_handoff_no_delete BEFORE DELETE ON school_review_draft_handoffs
 BEGIN SELECT RAISE(ABORT,'Private input handoffs are immutable'); END;
'''


def available(store):
    return bool(store.db.execute("SELECT 1 FROM sqlite_master WHERE name='school_review_draft_handoffs'").fetchone())


def _receipt(store, principal, column, ident):
    if not available(store): return None
    row=store.db.execute('SELECT h.draft_id,h.review_id,r.notice_version,r.conversion_revision,r.sequence'
        ' FROM school_review_draft_handoffs h JOIN school_structure_reviews r ON r.id=h.review_id'
        ' JOIN school_review_drafts d ON d.id=h.draft_id WHERE h.'+column+'=?'
        ' AND r.school=? AND r.actor=? AND d.school=r.school AND d.subject=r.actor',
        (ident,principal.school,principal.subject)).fetchone()
    return dict(row) if row else None


def for_draft(store, principal, ident):
    drafts.get(store,principal,ident)
    return _receipt(store,principal,'draft_id',ident)


def for_review(store, principal, ident):
    svc.require_member(store,principal); drafts.key(ident)
    # Another staff member may read the formal review, never its private source.
    return _receipt(store,principal,'review_id',ident)


def preview(store, principal, ident):
    store.db.execute('SAVEPOINT private_draft_handoff_preview')
    try:
        data=compare.compare(store,principal,ident); row=data['draft']
        receipt=_receipt(store,principal,'draft_id',ident)
        reasons=list(data['reasons'])
        if not available(store): reasons.append('handoff_migration_required')
        if receipt: reasons.append('already_submitted')
        if not row['payload']['comment'].strip(): reasons.append('reason_required')
        if not reasons and data['comparable']:
            v,overlay,_=review._load(store,principal,row['notice_version'],row['conversion_revision'])
            try:
                review._change(v['analysis'],overlay,row['payload']['action'],v['status'])
                if (len(encode(overlay).encode())>review.MAX_OVERLAY_BYTES or
                        len(encode(review._apply(v['original'],overlay)).encode())>review.MAX_VIEW_BYTES):
                    raise ValueError('Review size limit')
            except ValueError:
                reasons.append('invalid_review_input')
        return {**data,'receipt':receipt,'submit_reasons':reasons,
                'can_submit':not reasons and data['comparable']}
    finally:
        store.db.execute('RELEASE private_draft_handoff_preview')


def validate_source(store, principal, ident, *, notice, revision, base_hash, expected_review, action, comment):
    """Called only inside review.save's writer transaction, before any INSERT."""
    data=preview(store,principal,ident); row=data['draft']
    if (not data['can_submit'] or row['notice_version']!=notice or row['conversion_revision']!=revision
            or row['base_hash']!=base_hash or row['base_review']!=expected_review):
        raise review.Conflict('Saved input or formal review changed; reopen preview')
    if action!=row['payload']['action'] or comment!=row['payload']['comment'].strip():
        raise ValueError('Only the saved input may be submitted')


def submit(store, principal, ident, *, base_hash, expected_review):
    drafts.key(ident); drafts.key(expected_review,empty=True)
    row=drafts.get(store,principal,ident)
    return review.save(store,principal,row['notice_version'],revision_id=row['conversion_revision'],
        base_hash=base_hash,expected_review=expected_review,action=copy.deepcopy(row['payload']['action']),
        comment=row['payload']['comment'],source_draft=ident)
