"""Private R2 dataset guards and fact inputs. No model or production I/O.

An original/template component is assigned before transformations. Gold is
operation truth; direct checks and model predictions are never label sources.
"""
from __future__ import annotations

import hashlib
import copy
import json
import re
import unicodedata
from collections import Counter

from .worker_contract import canonical
from .worker_handoff import questions

INPUT_REVISION = 'moa.laya.fact-bundle.v1'
LABELS = ('preserved', 'changed', 'insufficient')
SPLITS = ('train', 'dev', 'blind')
STATE_FIELDS = ('title', 'file_role', 'source_complete', 'source_blocks',
                'rendered_blocks', 'attachment_manifest')
MAX_GROUPS, MAX_PER_GROUP, MAX_QUESTIONS = 100, 20, 2000
MAX_SHARD_BYTES = 512 * 1024


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def prose_key(text):
    # Repackaging, whitespace and date/amount corrections are not new groups.
    return re.sub(r'\d+|\s+', '', unicodedata.normalize('NFC', text))


def _shingles(text):
    value = prose_key(text)
    return {value[i:i+5] for i in range(len(value)-4)} if len(value) >= 80 else set()


def group_sources(sources, protected):
    """Connected components, including transitive aliases of protected originals.

    Family IDs must come from semantic header/merge/unit/condition evidence,
    never just table width/height. Near-whole-prose copies are also joined.
    This registry supplements, not replaces, the existing corpus eval guards.
    """
    nodes = list(sources) + list(protected)
    if len({s['id'] for s in sources}) != len(sources):
        raise ValueError('duplicate source ID')
    parents = list(range(len(nodes)))
    def find(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]; i = parents[i]
        return i
    def join(a, b):
        a, b = find(a), find(b)
        if a != b: parents[b] = a
    index = {}
    sketches = []
    for i, record in enumerate(nodes):
        if not re.fullmatch('[0-9a-f]{64}', record['sha256']):
            raise ValueError('invalid original sha256')
        keys = [('sha', record['sha256'])]
        keys += [('sha', s) for s in record.get('lineage_sha256', [])]
        keys += [('family', s) for s in record.get('family_ids', []) if s]
        if record.get('text', '').strip(): keys += [('prose', prose_key(record['text']))]
        for key in keys:
            if key in index: join(i, index[key])
            else: index[key] = i
        sketch = _shingles(record.get('text', ''))
        if sketch:
            for j, other in sketches:
                if len(sketch & other) / len(sketch | other) >= .85: join(i, j)
            sketches.append((i, sketch))
    blocked = {find(i) for i in range(len(sources), len(nodes))}
    blocked.update(find(i) for i, s in enumerate(nodes) if s.get('evaluation_only'))
    groups, rejected = {}, {}
    for i, record in enumerate(sources):
        component = find(i)
        if component in blocked:
            rejected[record['id']] = 'protected_evaluation_component'; continue
        if record.get('rights_status') != 'allowed' or not record.get('rights_evidence'):
            rejected[record['id']] = 'rights_unreviewed'; continue
        groups.setdefault(component, []).append(record)
    result = [{'id': sha(canonical(sorted(s['sha256'] for s in members))), 'members': members}
              for members in groups.values()]
    return {'groups': sorted(result, key=lambda g: g['id']), 'rejected': rejected}


def assign_splits(groups, frozen=None):
    if len(groups) > MAX_GROUPS: raise ValueError('maximum 100 source/template groups')
    frozen = dict(frozen or {})
    if any(s not in SPLITS for s in frozen.values()): raise ValueError('invalid frozen split')
    result = dict(frozen)
    counts = Counter()
    pending = []
    for group in sorted(groups, key=lambda g: g['id']):
        old = {frozen[m['sha256']] for m in group['members'] if m['sha256'] in frozen}
        if len(old) > 1: raise ValueError('conflicting frozen split in a family')
        if old:
            split = old.pop(); counts[split] += 1
            result.update({m['sha256']: split for m in group['members']})
        else: pending.append(group)
    n = len(groups)
    quotas = {'train': round(n*.6), 'dev': round(n*.2)}
    quotas['blind'] = n-quotas['train']-quotas['dev']
    for group in pending:
        split = max(SPLITS, key=lambda s: quotas[s]-counts[s])
        counts[split] += 1
        result.update({m['sha256']: split for m in group['members']})
    return result


def fact_state(source, actual_dom):
    """Only evidence is serialized; metadata, gold and rule hints stay outside.

    source/actual_dom are independently obtained compact fact objects. No
    missing DOM value is filled from the source. Absent context remains absent.
    source_complete means this provided section was read, not whole-file gold.
    """
    fields = {'headers', 'rows', 'geometry', 'caption', 'unit', 'notes', 'conditions', 'facts'}
    for evidence in (source, actual_dom):
        if not isinstance(evidence, dict) or set(evidence)-fields:
            raise ValueError('fact input contains non-evidence fields')
    return {'title': '', 'file_role': 'notice', 'source_complete': True,
            'source_blocks': [{'id': 'b1', 'text': canonical(source).decode()}],
            'rendered_blocks': [{'id': 'r1', 'source_refs': ['b1'],
                                 'visible_text': canonical(actual_dom).decode()}],
            'attachment_manifest': []}


def operation_label(before, after, evidence_complete=True):
    return 'insufficient' if not evidence_complete else ('preserved' if before == after else 'changed')


def text_state(state):
    """Context-matched plain-text S0 control, not a replay of prior eval cases."""
    def text(raw):
        facts = json.loads(raw)
        lines = [facts.get('caption','')]
        if facts.get('headers'): lines.append(' / '.join(facts['headers']))
        lines += [' / '.join(row) for row in facts.get('rows',[])]
        lines += facts.get('facts', [])
        lines += [facts.get('unit','')] + facts.get('conditions',[]) + facts.get('notes',[])
        if facts.get('geometry'): lines.append('병합 관계: '+canonical(facts['geometry']).decode())
        return '\n'.join(line for line in lines if line)
    result = copy.deepcopy(state)
    result['source_blocks'][0]['text'] = text(state['source_blocks'][0]['text'])
    result['rendered_blocks'][0]['visible_text'] = text(state['rendered_blocks'][0]['visible_text'])
    return result


def validate_operation(before, after, expected, complete):
    actual = operation_label(before, after, complete)
    if expected == 'changed' and actual == 'preserved': raise ValueError('no-op mutation')
    if actual != expected: raise ValueError('independent oracle mismatch')


def validate_rows(rows, assignments):
    counts, payloads = Counter(), set()
    qdefs = questions()
    for row in rows:
        meta, state = row['meta'], row['state']
        if meta['split'] != assignments.get(meta['source_sha256']): raise ValueError('split mismatch')
        if meta.get('label_origin') != 'deterministic_oracle' or meta.get('forbidden_lineage') != []:
            raise ValueError('forbidden label source')
        if meta.get('rights_status') != 'allowed' or not meta.get('rights_evidence'):
            raise ValueError('row rights')
        if set(state) != set(STATE_FIELDS) or len(row['expected']) != 1:
            raise ValueError('state fields or question cardinality')
        if row['questions'] != {k: qdefs[k] for k in row['expected']}:
            raise ValueError('question definitions')
        op = meta['operation']
        validate_operation(op['before'], op['after'], next(iter(row['expected'].values())), op['complete'])
        if meta.get('evaluation_only'): raise ValueError('evaluation derivative')
        counts[meta['original_group_id']] += 1
        key = sha(canonical({'state': state, 'questions': row['questions']}))
        if key in payloads: raise ValueError('duplicate model input')
        payloads.add(key)
    if len(rows) > MAX_QUESTIONS or any(c > MAX_PER_GROUP for c in counts.values()):
        raise ValueError('question cap')
    return {'rows': len(rows), 'groups': len(counts), 'payload_sha256': sorted(payloads)}


def metrics(records):
    """Whole denominator; separate confusions, missing and group independence."""
    cm = {a: {b: 0 for b in LABELS} for a in LABELS}
    gold = Counter(r['expected'] for r in records)
    if set(gold)-set(LABELS): raise ValueError('gold label')
    missing, correct = 0, 0
    for row in records:
        p = row.get('prediction')
        if p is None: missing += 1; continue
        if p not in LABELS: raise ValueError('prediction label')
        cm[row['expected']][p] += 1; correct += p == row['expected']
    n = len(records)
    normal_requests = sum(r.get('prediction') != 'preserved' for r in records if r['expected'] == 'preserved')
    return {'items': n, 'groups': len({r['group_id'] for r in records}),
            'accuracy_all': correct/n if n else None, 'correct': correct,
            'uncompleted': missing, 'confusion': cm, 'gold_counts': dict(gold),
            'changed_detected': cm['changed']['changed'], 'changed_total': gold['changed'],
            'changed_to_preserved': cm['changed']['preserved'],
            'insufficient_to_preserved': cm['insufficient']['preserved'],
            'normal_false_positives': cm['preserved']['changed'],
            'normal_false_positive_rate': cm['preserved']['changed']/gold['preserved'] if gold['preserved'] else None,
            'normal_review_requests': normal_requests,
            'review_share': sum(r.get('prediction') != 'preserved' for r in records)/n if n else None,
            'by_error': {e: {'items': sum(r['error'] == e for r in records),
                             'correct': sum(r['error'] == e and r.get('prediction') == r['expected'] for r in records)}
                         for e in sorted({r['error'] for r in records})}}
