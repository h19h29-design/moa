"""Private unfinished form inputs, never a review overlay or approval.

Explicit saves append bounded snapshots. The latest per-field snapshot is a CAS
head; older snapshots, source objects and formal review events stay untouched.
"""
import json
import re
import uuid

from . import school_review as review, school_service as svc
from .core import digest, encode

SCHEMA_V4 = '''
CREATE TABLE school_review_drafts(
 id TEXT PRIMARY KEY CHECK(length(id)=32), school TEXT NOT NULL, subject TEXT NOT NULL,
 notice_version TEXT NOT NULL REFERENCES school_service_versions(id),
 conversion_revision TEXT NOT NULL REFERENCES mobile_revisions(id),
 target_key TEXT NOT NULL CHECK(length(target_key)=64), sequence INTEGER NOT NULL CHECK(sequence>0),
 previous_id TEXT, base_hash TEXT NOT NULL CHECK(length(base_hash)=64), base_review TEXT NOT NULL,
 title TEXT NOT NULL CHECK(length(title)<=300), action_kind TEXT NOT NULL
 CHECK(action_kind IN ('block','relation_add','relation_remove')),
 payload TEXT NOT NULL CHECK(length(CAST(payload AS BLOB))<=100000), created_at TEXT NOT NULL,
 UNIQUE(school,subject,notice_version,conversion_revision,target_key,sequence),
 UNIQUE(school,subject,notice_version,conversion_revision,target_key,id),
 FOREIGN KEY(school,subject) REFERENCES school_service_members(school,subject),
 FOREIGN KEY(school,subject,notice_version,conversion_revision,target_key,previous_id)
 REFERENCES school_review_drafts(school,subject,notice_version,conversion_revision,target_key,id));
CREATE INDEX school_review_drafts_owner ON school_review_drafts(school,subject,created_at DESC,id DESC);
CREATE TRIGGER school_review_draft_scope BEFORE INSERT ON school_review_drafts
 WHEN NOT EXISTS(SELECT 1 FROM school_service_versions v JOIN mobile_revisions r ON r.notice_id=v.mobile_id
 WHERE v.school=NEW.school AND v.id=NEW.notice_version AND r.id=NEW.conversion_revision)
 OR (NEW.base_review!='' AND NOT EXISTS(SELECT 1 FROM school_structure_reviews r
 WHERE r.school=NEW.school AND r.notice_version=NEW.notice_version
 AND r.conversion_revision=NEW.conversion_revision AND r.id=NEW.base_review))
 BEGIN SELECT RAISE(ABORT,'School input draft scope'); END;
CREATE TRIGGER school_review_draft_no_update BEFORE UPDATE ON school_review_drafts
 BEGIN SELECT RAISE(ABORT,'Input draft snapshots are immutable'); END;
CREATE TRIGGER school_review_draft_no_delete BEFORE DELETE ON school_review_drafts
 BEGIN SELECT RAISE(ABORT,'Input draft snapshots are immutable'); END;
'''
METADATA = 'id,target_key,sequence,base_hash,base_review,created_at,action_kind'


def available(store):
    return bool(store.db.execute("SELECT 1 FROM sqlite_master WHERE name='school_review_drafts'").fetchone())


def key(value, *, empty=False):
    if empty and value=='': return value
    if not isinstance(value,str) or not re.fullmatch('[a-f0-9]{32}',value): raise ValueError('Invalid input draft')
    return value


def target(action):
    kind=action['kind']
    if kind not in ('block','relation_add','relation_remove'): raise ValueError('Unsupported input draft')
    part=action['block_id'] if kind=='block' else action['relation'] if kind=='relation_remove' else ''
    return digest(encode([kind,part]).encode())


def _payload(action, comment, analysis):
    if not isinstance(action,dict): raise ValueError('Invalid draft action')
    kind=action.get('kind')
    fields={'block':{'kind','block_id','block_kind','text'},
            'relation_add':{'kind','relation'},'relation_remove':{'kind','relation'}}
    if not isinstance(kind,str) or kind not in fields or set(action)!=fields[kind]: raise ValueError('Invalid draft fields')
    if kind=='block':
        if (not isinstance(action['block_kind'],str) or action['block_kind'] not in review.BLOCK_LABELS
                or not any(b['id']==action['block_id'] for b in analysis['blocks'])):
            raise ValueError('Invalid draft block')
        review._text(action['text'],review.MAX_TEXT)  # Unfinished, even empty, is valid input.
    else:
        relation=action['relation']
        if not isinstance(relation,dict) or set(relation)!={'kind','source','target'}: raise ValueError('Invalid draft relation')
        for value in relation.values(): review._text(value,160)
        if relation['kind'] not in review.RELATION_LABELS: raise ValueError('Invalid draft relation kind')
        if kind=='relation_remove' and not any(all(r[k]==v for k,v in relation.items()) for r in analysis['relations']):
            raise ValueError('Missing draft relation')
        # Addition endpoints may be unfinished. Formal review.save still validates
        # ownership, graph semantics and nonempty input when explicitly submitted.
    result=encode({'action':action,'comment':review._text(comment,review.MAX_COMMENT)})
    if len(result.encode())>100000: raise ValueError('Input draft too large')
    return result


def metadata(store, principal, ident, revision, targets):
    svc.get_version(store,principal,ident)
    if len(targets)>41: raise ValueError('Too many draft targets')
    if not available(store) or not targets: return {}
    rows=store.db.execute('SELECT '+METADATA+' FROM school_review_drafts d WHERE school=? AND subject=?'
        ' AND notice_version=? AND conversion_revision=? AND target_key IN ('+','.join('?' for _ in targets)+')'
        ' AND NOT EXISTS(SELECT 1 FROM school_review_drafts n WHERE n.school=d.school AND n.subject=d.subject'
        ' AND n.notice_version=d.notice_version AND n.conversion_revision=d.conversion_revision'
        ' AND n.target_key=d.target_key AND n.sequence>d.sequence)',
        (principal.school,principal.subject,ident,revision,*targets))
    return {r['target_key']:dict(r) for r in rows}


def save(store, principal, ident, *, revision_id, base_hash, expected_review, expected_draft, action, comment,
         acknowledge_same=False):
    key(expected_draft,empty=True)
    try:
        store.db.execute('BEGIN IMMEDIATE')
        svc.require_member(store,principal,write=True)
        if not available(store): raise review.Conflict('Explicit input draft migration required')
        v,_,version=review._load(store,principal,ident,revision_id)
        if not v['editable'] or not revision_id: raise review.Conflict('Latest editable version required')
        if v['base_hash']!=base_hash or (v['latest']['id'] if v['latest'] else '')!=expected_review:
            raise review.Conflict('Review changed since input began')
        payload=_payload(action,comment,v['analysis']); topic=target(action)
        head=metadata(store,principal,ident,revision_id,[topic]).get(topic)
        if acknowledge_same and head and head['base_hash']==base_hash and head['base_review']==expected_review:
            saved=store.db.execute('SELECT previous_id,payload FROM school_review_drafts WHERE id=?',(head['id'],)).fetchone()
            # Only acknowledge this head or the exact request that created it.
            # A lost response must not append again, rebase or skip a newer edit.
            if (expected_draft in (head['id'],saved['previous_id'] or '') and saved['payload']==payload):
                store.db.commit()
                return head['id']
        if (head['id'] if head else '')!=expected_draft: raise review.Conflict('Input draft changed in another tab')
        draft_id=uuid.uuid4().hex
        store.db.execute('INSERT INTO school_review_drafts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (draft_id,principal.school,principal.subject,ident,revision_id,topic,head['sequence']+1 if head else 1,
             expected_draft or None,base_hash,expected_review,version['title'],action['kind'],payload,svc._now()))
        store.db.commit()
        return draft_id
    except Exception:
        store.db.rollback()
        raise


def get(store, principal, ident):
    svc.require_member(store,principal); key(ident)
    if not available(store): raise review.Conflict('Explicit input draft migration required')
    row=store.db.execute('SELECT * FROM school_review_drafts WHERE id=? AND school=? AND subject=?',
        (ident,principal.school,principal.subject)).fetchone()
    if not row: raise LookupError('Input draft not found')
    result=dict(row); svc.get_version(store,principal,result['notice_version'])
    result['payload']=json.loads(result['payload'])
    from .school_draft_restore import available as restore_available
    origin = store.db.execute('SELECT source_id FROM school_review_draft_restores WHERE draft_id=?',
                              (ident,)).fetchone() if restore_available(store) else None
    result['restored_from'] = origin['source_id'] if origin else None
    return result


def listing(store, principal, *, page=1):
    if type(page) is not int or not 1<=page<=1_000_000: raise ValueError('Invalid draft page')
    store.db.execute('SAVEPOINT private_input_drafts')
    try:
        svc.require_member(store,principal)
        if not available(store): return {'rows':[],'page':1,'pages':1,'migration_required':True}
        scope=(' FROM school_review_drafts d WHERE school=? AND subject=? AND NOT EXISTS('
            'SELECT 1 FROM school_review_drafts n WHERE n.school=d.school AND n.subject=d.subject'
            ' AND n.notice_version=d.notice_version AND n.conversion_revision=d.conversion_revision'
            ' AND n.target_key=d.target_key AND n.sequence>d.sequence)')
        params=(principal.school,principal.subject)
        total=store.db.execute('SELECT count(*)'+scope,params).fetchone()[0]
        pages=max(1,(total+19)//20); page=min(page,pages)
        rows=[dict(r) for r in store.db.execute('SELECT '+METADATA+',title'+scope+
            ' ORDER BY created_at DESC,id DESC LIMIT 20 OFFSET ?',(*params,(page-1)*20))]
        return {'rows':rows,'page':page,'pages':pages,'migration_required':False}
    finally:
        store.db.execute('RELEASE private_input_drafts')
