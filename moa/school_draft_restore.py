"""Explicit reuse of an older private input, append-only and never rebased."""
import uuid

from . import school_draft_compare as compare, school_review_drafts as drafts
from . import school_review as review, school_service as svc

SCHEMA_V5 = '''
CREATE TABLE school_review_draft_restores(
 draft_id TEXT PRIMARY KEY REFERENCES school_review_drafts(id),
 source_id TEXT NOT NULL REFERENCES school_review_drafts(id));
CREATE TRIGGER school_draft_restore_scope BEFORE INSERT ON school_review_draft_restores
 WHEN NOT EXISTS(SELECT 1 FROM school_review_drafts d
 JOIN school_review_drafts s ON s.id=NEW.source_id
 JOIN school_review_drafts h ON h.id=d.previous_id
 WHERE d.id=NEW.draft_id AND d.school=s.school AND d.subject=s.subject
 AND d.notice_version=s.notice_version AND d.conversion_revision=s.conversion_revision
 AND d.target_key=s.target_key AND d.action_kind=s.action_kind
 AND h.school=s.school AND h.subject=s.subject AND h.notice_version=s.notice_version
 AND h.conversion_revision=s.conversion_revision AND h.target_key=s.target_key
 AND h.action_kind=s.action_kind AND s.sequence<h.sequence AND d.sequence=h.sequence+1
 AND d.base_hash=s.base_hash AND d.base_review=s.base_review
 AND h.base_hash=s.base_hash AND h.base_review=s.base_review AND d.payload=s.payload)
 BEGIN SELECT RAISE(ABORT,'Invalid private input restore origin'); END;
CREATE TRIGGER school_draft_restore_no_update BEFORE UPDATE ON school_review_draft_restores
 BEGIN SELECT RAISE(ABORT,'Private input restore origins are immutable'); END;
CREATE TRIGGER school_draft_restore_no_delete BEFORE DELETE ON school_review_draft_restores
 BEGIN SELECT RAISE(ABORT,'Private input restore origins are immutable'); END;
'''


def available(store):
    return bool(store.db.execute("SELECT 1 FROM sqlite_master WHERE name='school_review_draft_restores'").fetchone())


def preview(store, principal, source_id):
    store.db.execute('SAVEPOINT private_draft_restore_preview')
    try:
        data = compare.compare(store, principal, source_id)
        head = drafts.get(store, principal, data['head']['id'])
        source = data['draft']
        reasons = [r for r in data['reasons'] if r!='newer_input']
        if not available(store): reasons.append('restore_migration_required')
        if source['sequence']>=head['sequence']: reasons.append('already_latest')
        if any(source[k]!=head[k] for k in ('base_hash','base_review')):
            reasons.append('head_base_changed')
        return {**data, 'head_draft':head, 'restore_reasons':reasons,
                'can_restore':not reasons and data['comparable']}
    finally:
        store.db.execute('RELEASE private_draft_restore_preview')


def restore(store, principal, source_id, *, expected_draft, base_hash, expected_review):
    drafts.key(source_id); drafts.key(expected_draft); drafts.key(expected_review,empty=True)
    try:
        store.db.execute('BEGIN IMMEDIATE')
        svc.require_member(store, principal, write=True)
        data = preview(store, principal, source_id)
        source, head = data['draft'], data['head_draft']
        if (not data['can_restore'] or head['id']!=expected_draft
                or source['base_hash']!=base_hash or source['base_review']!=expected_review):
            raise review.Conflict('Private input or review changed; reopen restore preview')
        # Validate the original partial form again under the same locked base.
        view, _, _ = review._load(store, principal, source['notice_version'], source['conversion_revision'])
        if (not view['editable'] or view['base_hash']!=base_hash
                or (view['latest']['id'] if view['latest'] else '')!=expected_review):
            raise review.Conflict('Review changed before private input restore')
        payload = drafts._payload(source['payload']['action'], source['payload']['comment'], view['analysis'])
        ident = uuid.uuid4().hex
        store.db.execute('INSERT INTO school_review_drafts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (ident, principal.school, principal.subject, source['notice_version'], source['conversion_revision'],
             source['target_key'], head['sequence']+1, head['id'], base_hash, expected_review,
             source['title'], source['action_kind'], payload, svc._now()))
        store.db.execute('INSERT INTO school_review_draft_restores VALUES(?,?)', (ident, source_id))
        store.db.commit()
        return ident
    except Exception:
        store.db.rollback()
        raise
