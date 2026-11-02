"""Pinned, read-only structural differences. No parser, jobs, migration or AI.

Only snapshots sharing one receiving/conversion revision can be compared. Block
IDs are stable within that scope, not across reconversions. Review status and
relationship provenance are distinct from text edits or publication approval.
"""
from collections import defaultdict
from urllib.parse import urlencode

from . import school_review as review, school_service as svc
from .core import digest, encode

VERSION = 'moa-school-review-compare-v1'
PAGE_SIZE = 20
MAX_JSON_BYTES = 20 * 1024 * 1024


def link(ident, revision, left, right, **query):
    return '/structure-compare?'+urlencode(dict(id=ident,revision=revision,left=left,right=right,**query))


def _pin(value):
    if not isinstance(value,str) or not 0 < len(value) <= 160 or '\x00' in value or not value.strip():
        raise ValueError('Explicit receiving/conversion/review pins required')
    value.encode('utf-8')


def _slice(items, page):
    count = max(1,(len(items)+PAGE_SIZE-1)//PAGE_SIZE)
    if type(page) is not int or not 1 <= page <= min(10000,count): raise ValueError('Invalid comparison page')
    return {'items':items[(page-1)*PAGE_SIZE:page*PAGE_SIZE],'page':page,'pages':count,'total':len(items)}


def _snapshot(store, principal, ident, revision, selected, original, base_hash):
    if selected == 'original':
        return original, {'id':'original','sequence':0,'status':'unreviewed','actor':'',
            'created_at':'','comment':'','warnings_acknowledged':False,'base_hash':base_hash}
    v = review.view(store,principal,ident,revision_id=revision,event_id=selected)
    if v['base_hash'] != base_hash: raise review.Conflict('Automatic analysis changed')
    event = v['latest']
    metadata = {k:event[k] for k in ('id','sequence','status','actor','created_at','comment',
                                    'warnings_acknowledged','base_hash')}
    return v['analysis'], metadata


def _relations(analysis):
    # One logical edge may carry more than one automatic evidence record. Never
    # silently discard it by indexing only the last record for an endpoint pair.
    grouped = defaultdict(list)
    for edge in analysis['relations']:
        grouped[tuple(edge[k] for k in ('kind','source','target'))].append(edge)
    return {key:sorted(values,key=encode) for key,values in grouped.items()}


def compare(store, principal, ident, *, revision_id, left, right,
            block_page=1, relation_page=1, warning_page=1):
    for pin in (ident,revision_id,left,right): _pin(pin)
    original = svc.structure_for(store,principal,ident,revision_id=revision_id)
    base_hash = digest(encode(original).encode())
    before, left_meta = _snapshot(store,principal,ident,revision_id,left,original,base_hash)
    after, right_meta = _snapshot(store,principal,ident,revision_id,right,original,base_hash)
    left_blocks = {b['id']:b for b in before['blocks']}
    right_blocks = {b['id']:b for b in after['blocks']}
    if left_blocks.keys()!=right_blocks.keys(): raise review.Conflict('Block inventory changed')
    changed = []
    for b in before['blocks']:
        new = right_blocks[b['id']]
        fields = [k for k in ('kind','text') if b[k]!=new[k]]
        if fields:
            changed.append({**{k:b[k] for k in ('id','file_id','sha256','locator','basis','certainty')},
                'fields':fields, 'before':{k:b[k] for k in ('kind','text')},
                'after':{k:new[k] for k in ('kind','text')}})
    left_edges, right_edges = _relations(before), _relations(after)
    edges = []; counts = dict(added_relations=0,removed_relations=0,modified_relations=0,unchanged_relations=0)
    for key in sorted(left_edges.keys() | right_edges.keys()):
        one, two = left_edges.get(key,[]), right_edges.get(key,[])
        if one==two:
            counts['unchanged_relations'] += 1
            continue
        kind = 'added' if not one else 'removed' if not two else 'modified'
        counts[kind+'_relations'] += 1
        edges.append(dict(zip(('kind','source','target'),key)) | {'change':kind,'before':one,'after':two})
    result = {'version':VERSION, 'notice_version':ident, 'conversion_revision':revision_id,
        'base_hash':base_hash, 'private':True, 'publication_approved':False,
        'left':left_meta, 'right':right_meta, 'files':original['files'], 'complete':original['complete'],
        'summary':{'changed_blocks':len(changed),'unchanged_blocks':len(before['blocks'])-len(changed),
                   **counts,'status_changed':left_meta['status']!=right_meta['status']},
        'blocks':_slice(changed,block_page), 'relations':_slice(edges,relation_page),
        'warnings':_slice(original['warnings'],warning_page)}
    if len(encode(result).encode()) > MAX_JSON_BYTES: raise ValueError('Comparison output limit')
    return result
