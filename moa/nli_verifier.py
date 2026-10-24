"""Local bidirectional NLI trial, not a document approval or a source oracle.

No framework/network imports: an isolated runner supplies actual tokenizer
lengths and original model labels. Neutral is non-entailment, NOT proof of
missing original evidence. A contradiction is a semantic alert, not a claim
that NLI detects every kind of document error.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re

from .worker_contract import canonical

VERSION = 'moa.bidirectional-nli.v3'
NLI_LABELS = {'entailment', 'neutral', 'contradiction'}
MAX_PAIRS = 2000


def validate_mapping(id2label):
    try:
        mapping = {int(k): v.lower() for k, v in id2label.items()}
        if set(mapping) != {0, 1, 2} or set(mapping.values()) != NLI_LABELS:
            raise ValueError('explicit independently validated semantic label mapping required')
        return mapping
    except (TypeError, AttributeError):
        raise ValueError('explicit semantic label mapping required') from None


def _facts(text):
    """Preserve the full textual input unless it is the known fact schema."""
    if not isinstance(text, str) or len(text) > 300000:
        raise ValueError('bounded actual section text required')
    try: value = json.loads(text)
    except (ValueError, TypeError): return text
    if not isinstance(value, dict): return text
    if set(value) - {'headers', 'rows', 'caption', 'unit', 'conditions', 'facts', 'notes', 'geometry'}:
        raise ValueError('unknown structural fields must not disappear')
    for key in ('caption', 'unit'):
        if key in value and not isinstance(value[key], str): raise ValueError('text fact')
    for key in ('headers', 'conditions', 'facts', 'notes'):
        if key in value and (not isinstance(value[key], list) or
                             any(not isinstance(v, str) for v in value[key])):
            raise ValueError('text fact inventory')
    if 'rows' in value:
        h = value.get('headers', [])
        if not h or not isinstance(value['rows'], list) or any(
                not isinstance(row, list) or len(row) != len(h) or
                any(not isinstance(v, str) for v in row) for row in value['rows']):
            raise ValueError('table context required')
    geometry = value.get('geometry', [])
    if not isinstance(geometry, list) or any(not isinstance(g, list) or len(g) != 4 or
            any(type(v) is not int for v in g) or min(g[:2]) < 0 or min(g[2:]) < 1 for g in geometry):
        raise ValueError('explicit merge inventory required')
    return value


def _flatten_repeated(value):
    # Only the explicit known six-column department layout, not arbitrary
    # six-column data. This changes representation, not a single fact value.
    if not isinstance(value, dict): return value
    value = copy.deepcopy(value)
    if (value.get('headers') == ['연번', '학과', '수업교실'] * 2 and
            not any(g[2:] != [1, 1] for g in value.get('geometry', []))):
        from .verification import department_links
        links = department_links([value['headers']] + value.get('rows', []))
        if links['recognized'] and links['valid']:
            value['headers'] = value['headers'][:3]
            value['rows'] = [row[o:o+3] for row in value['rows'] for o in (0, 3)
                             if any(v.strip() for v in row[o:o+3])]
    return value


def _context(value):
    result = []
    for key, label in (('caption', '안내'), ('unit', '단위'), ('headers', '머리글'),
                       ('conditions', '조건'), ('facts', '필수 내용'), ('notes', '각주')):
        if key in value:
            v = value[key]
            result.append(label + ': ' + (' / '.join(v) if isinstance(v, list) else v))
    if 'geometry' in value:
        result.append('병합 관계(행,열,행병합,열병합): ' + json.dumps(value['geometry'], ensure_ascii=False))
    return '\n'.join(result)


def _row(value, row):
    return '행: ' + ' ; '.join(h + '=' + cell for h, cell in zip(value['headers'], row))


def _text(value):
    if isinstance(value, str): return value
    return '\n'.join([_context(value)] + [_row(value, row) for row in value.get('rows', [])])


def _has_row_reference(value):
    # References are not confined to footnotes. A caption or mandatory
    # condition can make the physical row position part of the meaning.
    return isinstance(value, dict) and bool(re.search(
        r'(?:첫|두|세|네|다섯|여섯|일곱|여덟|아홉|열|\d+)\s*번째\s*행|제?\s*\d+\s*행|(?:위|아래|마지막)\s*행',
        _context(value)))


def audit_pair_overlap(reference_plans, candidate_plans):
    """Audit actual directional model text, independently of parent/pair IDs.

    Different source hashes or template names do not make repeated clauses
    an independent test. This audit consumes no gold or model predictions.
    """
    def inputs(plan):
        return {hashlib.sha256(canonical({k: p[k] for k in
                    ('direction', 'premise', 'hypothesis')})).hexdigest()
                for p in plan['pairs']}
    reference = set().union(*(inputs(p) for p in reference_plans))
    candidates = [inputs(p) for p in candidate_plans]
    all_candidates = set().union(*candidates)
    overlap = reference & all_candidates
    return {'overlapping_directional_inputs': len(overlap),
            'candidate_directional_inputs': len(all_candidates),
            'candidate_cases_with_overlap': sum(bool(v & reference) for v in candidates),
            'candidate_cases_fully_repeated': sum(bool(v) and v <= reference for v in candidates),
            'independent_model_inputs': not overlap,
            'overlap_manifest_sha256': hashlib.sha256(canonical(sorted(overlap))).hexdigest()}


def plan_pairs(state, token_count, *, max_tokens=512):
    """Actual source/DOM pairing, no gold, model output or silent truncation.

    Structural facts are compared in focused units. Tables use keyed rows,
    with ALL header/unit/footnote/merge context on every row. If context exceeds the budget,
    the section is explicitly incomplete instead of dropping it.
    """
    pairs, expected, provided, failures = [], [], [], []
    def add(ident, source, dom):
        expected.append(ident)
        pending = []
        for direction, premise, hypothesis in (('source_to_dom', source, dom), ('dom_to_source', dom, source)):
            count = token_count(premise, hypothesis)
            if type(count) is not int or count < 1 or count > max_tokens:
                failures.append({'unit_id': ident, 'reason': 'input_overflow', 'token_count': count})
                return
            obj = {'unit_id': ident, 'direction': direction, 'premise': premise, 'hypothesis': hypothesis}
            obj['pair_id'] = hashlib.sha256(canonical(obj)).hexdigest()
            obj['token_count'] = count
            pending.append(obj)
        if len(pairs) + len(pending) > MAX_PAIRS:
            failures.append({'unit_id': ident, 'reason': 'pair_budget_exceeded'}); return
        pairs.extend(pending); provided.append(ident)
    try:
        if state.get('file_role') != 'notice': raise ValueError('notice role required; keep other files original')
        sources, rendered = state['source_blocks'], state['rendered_blocks']
        if not sources or len(sources) > 1000 or len(rendered) > 1000: raise ValueError('bounded sections')
        ids = [s['id'] for s in sources]
        if len(set(ids)) != len(ids): raise ValueError('unique source identity')
        if any(not isinstance(d['source_refs'], list) or len(d['source_refs']) != 1 or
               d['source_refs'][0] not in ids for d in rendered): raise ValueError('ambiguous DOM refs')
        for source in sources:
            matches = [d for d in rendered if d['source_refs'] == [source['id']]]
            if len(matches) != 1: raise ValueError('missing or multiply mapped actual DOM')
            actual = [_facts(text) for text in (source['text'], matches[0]['visible_text'])]
            if any(_has_row_reference(v) and v.get('headers') == ['연번', '학과', '수업교실'] * 2
                   for v in actual if isinstance(v, dict)):
                raise ValueError('physical row reference in repeated layout requires human correspondence')
            left, right = (_flatten_repeated(v) for v in actual)
            a, b = _text(left), _text(right)
            if isinstance(left, dict) and isinstance(right, dict):
                # A whole notice paragraph can overwhelm a small changed date.
                # Focus each independent fact, keeping contextual table evidence
                # and order-dependent clauses together, not deleting context.
                from .clause_correspondence import has_order_dependency
                dependent=has_order_dependency(left) or has_order_dependency(right)
                if dependent:
                    # References in a footnote can depend on a date/condition
                    # in another field: keep the entire prose context together.
                    prose=lambda v:'\n'.join(k+': '+text for k in ('conditions','facts','notes') for text in v.get(k,[]))
                    if prose(left) or prose(right):add(source['id']+'/prose',prose(left),prose(right))
                for field in ('caption', 'unit', 'headers', 'geometry', 'conditions', 'facts', 'notes'):
                    lv, rv = left.get(field), right.get(field)
                    if lv is None and rv is None: continue
                    if lv is None or rv is None:
                        failures.append({'reason':'unmatched_structure','field':field});continue
                    if field in ('conditions','facts','notes'):
                        if dependent:continue
                        if len(lv) != len(rv):
                            failures.append({'reason':'unmatched_structure','field':field});continue
                        if not lv:continue
                        # Exact unique correspondence first. A remaining changed
                        # sentence is paired only with its actual unmatched DOM
                        # sentence; this does not copy source facts into DOM.
                        unused=list(range(len(rv)));mapping={}
                        for i,text in enumerate(lv):
                            matches=[j for j in unused if rv[j]==text]
                            if len(matches)==1:
                                mapping[i]=matches[0];unused.remove(matches[0])
                        for i in range(len(lv)):
                            if i not in mapping:mapping[i]=unused.pop(0)
                            add(source['id']+'/'+field+'/'+str(i),lv[i],rv[mapping[i]])
                    else:
                        convert=lambda v:json.dumps(v,ensure_ascii=False) if not isinstance(v,str) else v
                        add(source['id']+'/'+field,field+': '+convert(lv),field+': '+convert(rv))
                if 'rows' not in left and 'rows' not in right:continue
                if not (left.get('rows') and right.get('rows') and left.get('headers') and right.get('headers')):
                    failures.append({'reason':'unmatched_structure','field':'rows'});continue
                lrows,rrows=left['rows'],right['rows']
                lkeys,rkeys=[row[0] for row in lrows],[row[0] for row in rrows]
                if len(set(lkeys))!=len(lkeys) or len(set(rkeys))!=len(rkeys) or set(lkeys)!=set(rkeys):
                    failures.append({'reason':'unmatched_structure','field':'row_keys'});continue
                other={row[0]:row for row in rrows}
                position_dependent=_has_row_reference(left) or _has_row_reference(right)
                # Header, unit, notes and full merge inventory stay on every row.
                fields=('caption','headers','unit','notes','geometry') + (('conditions','facts') if position_dependent else ())
                context=lambda v:_context({k:v[k] for k in fields if k in v})
                for i,row in enumerate(lrows):
                    pos=lambda index:'행 위치: '+str(index+1)+'\n' if position_dependent else ''
                    add(source['id']+'/row/'+str(i),context(left)+'\n'+pos(i)+_row(left,row),
                        context(right)+'\n'+pos(rkeys.index(row[0]))+_row(right,other[row[0]]))
                continue
            if token_count(a, b) <= max_tokens and token_count(b, a) <= max_tokens:
                add(source['id'], a, b); continue
            add(source['id'], a, b)
    except (KeyError, ValueError, TypeError, IndexError, AttributeError) as exc:
        failures.append({'reason': 'unsupported_correspondence', 'detail': str(exc)[:120]})
    return {'version': VERSION, 'pairs': pairs, 'complete': bool(pairs) and not failures and expected == provided,
            'coverage': {'expected': expected, 'provided': provided}, 'failures': failures,
            'state_sha256': hashlib.sha256(canonical(state)).hexdigest(),
            'max_tokens': max_tokens, 'truncation_allowed': False}


def summarize(plan, raw):
    expected = {p['pair_id'] for p in plan['pairs']}
    try:
        complete = (plan['complete'] and bool(expected) and len(raw) == len(expected) and
                    {r['pair_id'] for r in raw} == expected and
                    all(r['label'] in NLI_LABELS for r in raw))
    except (TypeError, KeyError): complete = False
    labels = [r.get('label') for r in raw] if complete else []
    relation = ('incomplete' if not complete else 'contradiction' if 'contradiction' in labels else
                'equivalent' if all(v == 'entailment' for v in labels) else 'not_entailed')
    label = {'equivalent': 'preserved', 'contradiction': 'changed'}.get(relation)
    return {'version': VERSION, 'status': 'completed' if complete else 'incomplete',
            'semantic_relation': relation, 'quality_label': label, 'raw_labels': labels,
            'source_insufficient_inferred': False, 'human_review_required': label != 'preserved',
            'human_approved': False, 'publish_allowed': False, 'operating_model_changed': False}


def combine(direct_and_evidence, semantic):
    if direct_and_evidence not in ('preserved', 'changed', 'insufficient'):
        raise ValueError('known direct/evidence decision required')
    label = direct_and_evidence
    if label == 'preserved':
        candidate = semantic.get('quality_label') if semantic.get('status') == 'completed' else None
        label = candidate if candidate in ('preserved', 'changed') else 'insufficient'
    return {'quality_label': label, 'decision_origin': 'guarded_trial_combination',
            'human_review_required': label != 'preserved', 'human_approved': False,
            'auto_pass_enabled': False, 'publish_allowed': False, 'operating_model_changed': False}
