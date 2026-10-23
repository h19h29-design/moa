"""Private staff overlays and append-only audit, not research/model approval.

Immutable automatic analysis, original objects and conversion HTML are never
rewritten. Each event is scoped to one receiving/conversion revision and includes
the previous event, base digest, actor, reason, before/after and overlay snapshot.
Only school_service.migrate explicitly installs this schema.
"""
from __future__ import annotations

import copy
import json
import uuid

from . import school_service as svc
from .core import digest, encode

SCHEMA_V2 = '''
CREATE TABLE school_structure_reviews(
 id TEXT PRIMARY KEY, school TEXT NOT NULL, post_id TEXT NOT NULL,
 notice_version TEXT NOT NULL, conversion_revision TEXT NOT NULL REFERENCES mobile_revisions(id),
 sequence INTEGER NOT NULL CHECK(sequence>0), previous_id TEXT,
 base_hash TEXT NOT NULL CHECK(length(base_hash)=64), actor TEXT NOT NULL,
 created_at TEXT NOT NULL, action TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('draft','confirmed')),
 comment TEXT NOT NULL, warnings_acknowledged INTEGER NOT NULL CHECK(warnings_acknowledged IN (0,1)),
 changes TEXT NOT NULL, overlay TEXT NOT NULL,
 UNIQUE(school,notice_version,conversion_revision,sequence),
 UNIQUE(school,notice_version,conversion_revision,id),
 FOREIGN KEY(school,post_id,notice_version) REFERENCES school_service_versions(school,post_id,id),
 FOREIGN KEY(school,notice_version,conversion_revision,previous_id)
   REFERENCES school_structure_reviews(school,notice_version,conversion_revision,id));
CREATE TRIGGER school_structure_review_revision_scope BEFORE INSERT ON school_structure_reviews
 WHEN NOT EXISTS(SELECT 1 FROM school_service_versions v JOIN mobile_revisions r ON r.notice_id=v.mobile_id
                 WHERE v.id=NEW.notice_version AND v.school=NEW.school AND r.id=NEW.conversion_revision)
 BEGIN SELECT RAISE(ABORT,'School review revision scope'); END;
CREATE TRIGGER school_structure_review_no_update BEFORE UPDATE ON school_structure_reviews
 BEGIN SELECT RAISE(ABORT,'School review history is append only'); END;
CREATE TRIGGER school_structure_review_no_delete BEFORE DELETE ON school_structure_reviews
 BEGIN SELECT RAISE(ABORT,'School review history is append only'); END;
'''

BLOCK_LABELS = {'header':'헤더', 'heading':'제목·소제목', 'body':'본문',
                'footer':'푸터·발신 정보', 'footnote':'각주', 'table':'표'}
RELATION_LABELS = {'header_for':'헤더 → 파일', 'footnote_for':'참조 구역 → 각주',
                   'attachment_reference':'참조 구역 → 다른 첨부 파일',
                   'part_of':'구역 → 포함하는 구역', 'table_header_for':'표 머리 셀 → 표'}
MAX_TEXT = 20_000
MAX_COMMENT = 2000
MAX_OVERLAY_BYTES = 2 * 1024 * 1024
MAX_VIEW_BYTES = 8 * 1024 * 1024
MAX_HISTORY_BYTES = 8 * 1024 * 1024
PAGE_SIZE = 20
EVENT_METADATA = 'id,sequence,previous_id,actor,created_at,action,status,comment,warnings_acknowledged,base_hash'


class Conflict(ValueError):
    """Reopen the latest version before retrying; never silently overwrite."""


def available(store):
    return bool(store.db.execute("SELECT 1 FROM sqlite_master WHERE name='school_structure_reviews'").fetchone())


def _event(row):
    if row is None: return None
    result = dict(row)
    result['changes'] = json.loads(result['changes'])
    result['warnings_acknowledged'] = bool(result['warnings_acknowledged'])
    return result


def _key(relation):
    return encode([relation[k] for k in ('kind','source','target')])


def _apply(original, overlay):
    effective = copy.deepcopy(original)
    for block in effective['blocks']:
        edits = overlay['blocks'].get(block['id'])
        if edits:
            changed_fields = sorted(k for k,value in edits.items() if block[k] != value)
            block.update(edits)
            if changed_fields:
                block['reviewed_fields'] = changed_fields
                block['review_basis'] = 'staff_correction'  # Original locator/basis stay provenance.
    effective['relations'] = [r for r in effective['relations'] if _key(r) not in overlay['removed']]
    effective['relations'].extend(overlay['added'].values())
    return effective


def _load(store, principal, ident, revision_id='', event_id=''):
    version = svc.get_version(store, principal, ident)
    member = svc.require_member(store, principal)
    original = svc.structure_for(store, principal, ident, revision_id=revision_id)
    base_hash = digest(encode(original).encode())
    installed = available(store)
    row = None
    if installed:
        row = store.db.execute('SELECT * FROM school_structure_reviews WHERE school=? AND notice_version=?'
            ' AND conversion_revision=?' + (' AND id=?' if event_id else '') + ' ORDER BY sequence DESC LIMIT 1',
            (principal.school, ident, original['conversion_revision'], *((event_id,) if event_id else ()))).fetchone()
    if event_id and not row: raise LookupError('Review event not found')
    if row and row['base_hash'] != base_hash: raise Conflict('Automatic analysis changed')
    overlay = json.loads(row['overlay']) if row else {'blocks':{}, 'added':{}, 'removed':[]}
    effective = _apply(original, overlay)
    latest_version = store.db.execute('SELECT latest_version FROM school_service_posts WHERE school=? AND id=?',
        (principal.school, version['post_id'])).fetchone()[0]
    stale = (version['revision'] != original['conversion_revision'] or latest_version != ident
             or version['state'] in ('processing','conversion_failed'))
    event = _event(row)
    if event:
        for key in ('overlay','school','post_id','notice_version','conversion_revision'): event.pop(key, None)
    result = {'original':original, 'analysis':effective, 'base_hash':base_hash, 'latest':event,
        'status':row['status'] if row else 'unreviewed', 'stale':stale,
        'editable':installed and not stale and not event_id and member['role'] in svc.WRITE_ROLES,
        'migration_required':not installed, 'historical_event':bool(event_id)}
    return result, overlay, version


def view(store, principal, ident, *, revision_id='', event_id=''):
    """GET never creates a migration, job, review event or confirmation."""
    return _load(store, principal, ident, revision_id, event_id)[0]


def history(store, principal, ident, *, revision_id, before=0):
    original = svc.structure_for(store, principal, ident, revision_id=revision_id)
    if type(before) is not int or not 0 <= before < 2**63: raise ValueError('Invalid history cursor')
    if not available(store): return {'events':[], 'next_before':0}
    # Read sizes first: 20 whole-document restoration audits must not be
    # fetched/decoded together. Preserve complete audit data and use a smaller
    # page (with the same sequence cursor) when the byte budget is reached.
    rows = store.db.execute('SELECT '+EVENT_METADATA+',length(CAST(changes AS BLOB)) AS audit_bytes'
        ' FROM school_structure_reviews WHERE school=? AND notice_version=?'
        ' AND conversion_revision=? AND (?=0 OR sequence<?) ORDER BY sequence DESC LIMIT ?',
        (principal.school, ident, original['conversion_revision'], before, before, PAGE_SIZE+1)).fetchall()
    events, used = [], 0
    for row in rows[:PAGE_SIZE]:
        if used + row['audit_bytes'] > MAX_HISTORY_BYTES:
            if not events: raise ValueError('History audit size limit')
            break
        event = dict(row); used += event.pop('audit_bytes')
        event['changes'] = store.db.execute('SELECT changes FROM school_structure_reviews WHERE id=?',
            (row['id'],)).fetchone()[0]
        events.append(_event(event))
    return {'events':events, 'next_before':events[-1]['sequence'] if len(rows)>len(events) else 0}


def conversions(store, principal, ident, *, before=0):
    """Discover old review histories without loading every revision payload."""
    version = svc.get_version(store,principal,ident)
    if type(before) is not int or not 0 <= before < 2**63: raise ValueError('Invalid conversion cursor')
    rows = store.db.execute('SELECT id,sequence,created_at FROM mobile_revisions WHERE notice_id=?'
        ' AND (?=0 OR sequence<?) ORDER BY sequence DESC LIMIT ?',
        (version['mobile_id'],before,before,PAGE_SIZE+1)).fetchall()
    return {'revisions':[dict(r) for r in rows[:PAGE_SIZE]],
            'next_before':rows[PAGE_SIZE-1]['sequence'] if len(rows)>PAGE_SIZE else 0}


def _text(value, limit, *, required=False):
    if not isinstance(value, str) or len(value)>limit or '\x00' in value or (required and not value.strip()):
        raise ValueError('Invalid review text')
    value.encode('utf-8')  # Reject unpaired surrogates before any write.
    return value


def _relation(value, analysis):
    if not isinstance(value, dict) or set(value) != {'kind','source','target'}:
        raise ValueError('Invalid relation')
    kind, source, target = (value[k] for k in ('kind','source','target'))
    if not all(isinstance(v, str) and len(v)<=160 for v in (kind,source,target)):
        raise ValueError('Invalid relation endpoint')
    if kind not in RELATION_LABELS or source == target: raise ValueError('Invalid relation type')
    blocks = {b['id']:b for b in analysis['blocks']}
    files = {'file:'+f['id'] for f in analysis['files']}
    b = blocks.get(source); t = blocks.get(target)
    valid = False
    if kind == 'header_for':
        valid = b is not None and target == 'file:'+b['file_id'] and (b['kind']=='header' or
            (b.get('zone')=='header' and 'kind' not in b.get('reviewed_fields',[])))
    elif kind == 'footnote_for':
        valid = b is not None and t is not None and t['kind']=='footnote' and b['file_id']==t['file_id']
    elif kind == 'attachment_reference':
        valid = b is not None and target in files and target != 'file:'+b['file_id']
    elif kind == 'part_of':
        valid = b is not None and t is not None and b['file_id']==t['file_id']
        # An explicit containment graph must be acyclic.
        parents = {}
        for r in analysis['relations']:
            if r['kind']=='part_of': parents.setdefault(r['source'],set()).add(r['target'])
        pending, seen = [target], set()
        while pending:
            node = pending.pop()
            if node == source: valid = False; break
            if node not in seen:
                seen.add(node); pending.extend(parents.get(node,()))
    elif kind == 'table_header_for':
        valid = t is not None and t['kind']=='table' and any(
            source == target+f"/r{c['row']}c{c['col']}" for c in t.get('table',{}).get('cells',[]))
    if not valid: raise ValueError('Relation does not match this revision')
    return dict(value)


def _change(analysis, overlay, action, status):
    if not isinstance(action, dict): raise ValueError('Invalid action')
    kind = action.get('kind')
    fields = {'block':{'kind','block_id','block_kind','text'}, 'relation_add':{'kind','relation'},
              'relation_remove':{'kind','relation'}, 'confirm':{'kind','acknowledge_warnings'}, 'reopen':{'kind'}}
    if not isinstance(kind,str) or kind not in fields or set(action) != fields[kind]: raise ValueError('Invalid action fields')
    changes = []
    if kind == 'block':
        block_id = _text(action['block_id'],160)
        b = next((b for b in analysis['blocks'] if b['id']==block_id), None)
        if not b or not isinstance(action['block_kind'],str) or action['block_kind'] not in BLOCK_LABELS:
            raise ValueError('Unknown block or kind')
        after = {'kind':action['block_kind'],'text':_text(action['text'],MAX_TEXT,required=True)}
        before = {k:b[k] for k in ('kind','text')}
        if after == before: raise ValueError('No change')
        overlay['blocks'][block_id] = after
        changes.append({'target':'block','id':block_id,'before':before,'after':after})
    elif kind in ('relation_add','relation_remove'):
        relation = action['relation']
        if not isinstance(relation,dict) or set(relation) != {'kind','source','target'}:
            raise ValueError('Invalid relation')
        if not all(isinstance(v,str) and len(v)<=160 for v in relation.values()) or relation['kind'] not in RELATION_LABELS:
            raise ValueError('Invalid relation')
        key = _key(relation)
        existing = next((r for r in analysis['relations'] if _key(r)==key),None)
        if kind == 'relation_add':
            _relation(relation, analysis)
            if existing: raise ValueError('Duplicate relation')
            if len(analysis['relations']) >= 4000: raise ValueError('Relation limit')
            added = {**relation,'basis':'staff_correction','certainty':'human'}
            overlay['added'][key] = added
            changes.append({'target':'relation','before':None,'after':added})
        else:
            if not existing: raise ValueError('Missing relation')
            if key in overlay['added']: del overlay['added'][key]
            else: overlay['removed'].append(key)
            changes.append({'target':'relation','before':existing,'after':None})
    elif kind == 'confirm':
        if action['acknowledge_warnings'] is not True or not analysis['blocks']: raise ValueError('Explicit scope acknowledgement required')
        if status == 'confirmed': raise ValueError('Already confirmed')
        # Correcting a block can invalidate its old semantic edge. Remove or
        # repair that edge before confirming, not silently hide it.
        for r in analysis['relations']:
            if r['kind'] in RELATION_LABELS: _relation({k:r[k] for k in ('kind','source','target')}, analysis)
    elif kind == 'reopen' and status != 'confirmed':
        raise ValueError('Only a confirmation can be reopened')
    new_status = 'confirmed' if kind=='confirm' else 'draft'
    if new_status != status:
        changes.append({'target':'status','before':status,'after':new_status})
    return changes, new_status


def _restore(store, principal, ident, current, action):
    """Copy one scoped historical overlay, never its approval or audit identity.

    Called inside save's writer transaction. Looking up the source by all three
    scope keys also rejects events from old conversions or other receiving
    versions, even if they happen to use the same block IDs.
    """
    if set(action) != {'kind','source_event','acknowledge_restore'} or action['acknowledge_restore'] is not True:
        raise ValueError('Explicit restore acknowledgement required')
    source_id = _text(action['source_event'],160,required=True)
    source, overlay, _ = _load(store,principal,ident,current['original']['conversion_revision'],source_id)
    if not current['latest'] or source['latest']['sequence'] >= current['latest']['sequence']:
        raise ValueError('A previous review event is required')
    changes = [{'target':'restore_source','before':None,
                'after':{'event_id':source_id,'sequence':source['latest']['sequence']}}]
    blocks = {b['id']:b for b in source['analysis']['blocks']}
    for b in current['analysis']['blocks']:
        before = {k:b[k] for k in ('kind','text')}
        after = {k:blocks[b['id']][k] for k in ('kind','text')}
        if before != after:
            changes.append({'target':'block','id':b['id'],'before':before,'after':after})
    # Preserve every evidence record for a logical edge, not just the last one.
    def edges(analysis):
        grouped = {}
        for r in analysis['relations']: grouped.setdefault(_key(r),[]).append(r)
        return {key:sorted(values,key=encode) for key,values in grouped.items()}
    old_edges, new_edges = edges(current['analysis']), edges(source['analysis'])
    for key in sorted(old_edges.keys() | new_edges.keys()):
        before, after = old_edges.get(key,[]), new_edges.get(key,[])
        if before != after: changes.append({'target':'relation','before':before,'after':after})
    if current['status'] != 'draft':
        changes.append({'target':'status','before':current['status'],'after':'draft'})
    if len(changes)==1: raise ValueError('No change')
    if len(encode(changes).encode()) > MAX_VIEW_BYTES: raise ValueError('Restore audit size limit')
    return copy.deepcopy(overlay), changes, 'draft'


def save(store, principal, ident, *, revision_id, base_hash, expected_review, action, comment):
    """One writer transaction rechecks authorization, version, digest and parent.

    No fields supplied by HTTP confer actor/school authority. Replaying a form
    with a consumed parent yields Conflict; it cannot append a duplicate event.
    """
    comment = _text(comment,MAX_COMMENT,required=True).strip()
    for value in (revision_id,base_hash,expected_review): _text(value,160)
    if not revision_id: raise ValueError('Conversion revision required')
    try:
        store.db.execute('BEGIN IMMEDIATE')
        v, overlay, version = _load(store,principal,ident,revision_id)
        svc.require_member(store,principal,write=True)
        if v['migration_required'] or v['stale']: raise Conflict('Latest review/migration required')
        latest = v['latest']
        if v['base_hash'] != base_hash or (latest['id'] if latest else '') != expected_review:
            raise Conflict('Review was changed by another request')
        if isinstance(action,dict) and action.get('kind')=='restore':
            overlay, changes, status = _restore(store,principal,ident,v,action)
        else:
            changes, status = _change(v['analysis'],overlay,action,v['status'])
        encoded = encode(overlay)
        if len(encoded.encode()) > MAX_OVERLAY_BYTES or len(encode(_apply(v['original'],overlay)).encode()) > MAX_VIEW_BYTES:
            raise ValueError('Review size limit')
        ident_review = uuid.uuid4().hex
        store.db.execute('INSERT INTO school_structure_reviews VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (ident_review,principal.school,version['post_id'],ident,revision_id,
             latest['sequence']+1 if latest else 1,expected_review or None,base_hash,principal.subject,
             svc._now(),action['kind'],status,comment,int(status=='confirmed'),encode(changes),encoded))
        store.db.commit()
        return ident_review
    except Exception:
        store.db.rollback()
        raise
