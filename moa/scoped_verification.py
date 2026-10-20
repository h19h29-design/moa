"""Versioned, shadow-only comparison for native section / actual DOM facts.

The frozen R2-v4 checker stays unchanged. No model answer, training label or
mobile renderer participates in the independent table correspondence check.
This does not certify original-file extraction or authorize publication.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re

from .verification import compare_fact_bundle, content_tokens, department_links
from .clause_correspondence import (clause_correspondence, has_order_dependency, normalize_clause,
                                    ordered_presentation_correspondence, normalize_ordered_presentation)

VERSION = 'moa.scoped-content-gate.v18'


def compare_scoped_facts(state):
    report = compare_fact_bundle(state)
    transforms = []
    correspondence = {}
    if state.get('source_complete') is True:
        try:
            sources, rendered = state['source_blocks'], state['rendered_blocks']
            if len(sources) != 1 or len(rendered) != 1 or rendered[0]['source_refs'] != [sources[0]['id']]:
                raise ValueError('ambiguous scope')
            left = json.loads(sources[0]['text'])
            right = json.loads(rendered[0]['visible_text'])
            original_rows = copy.deepcopy(left.get('rows', []))
            normalized = copy.deepcopy(state)
            independent_context = not (has_order_dependency(left) or has_order_dependency(right))
            headers, target = left.get('headers', []), right.get('headers', [])
            # Only this explicit semantic layout may change shape. In particular,
            # six arbitrary columns are not evidence of two repeated triples.
            repeated = (len(headers) == 6 and len(target) == 3 and
                        [content_tokens(v) for v in headers[:3]] ==
                        [content_tokens(v) for v in headers[3:]] ==
                        [content_tokens(v) for v in target])
            links = department_links([headers] + left.get('rows', []))
            # Equal merge coordinates do not imply equal cell relationships
            # after changing width. Unknown merges need their own proof.
            unmerged = not any(g[2:] != [1,1] for facts in (left,right)
                               for g in facts.get('geometry',[]))
            if repeated and links['recognized'] and links['valid'] and unmerged:
                flat = []
                for row in left['rows']:
                    for offset in (0, 3):
                        group = row[offset:offset+3]
                        if any(str(v).strip() for v in group):
                            flat.append(group)
                left = dict(left, headers=headers[:3], rows=flat)
                transforms.append('explicit_repeated_department_triples_to_keyed_list')
            for field in ('conditions', 'notes', 'facts'):
                if left.get(field, []) == right.get(field, []):
                    continue
                ordered = ordered_presentation_correspondence(left.get(field, []), right.get(field, []))
                if ordered:
                    correspondence[field] = ordered
                    left[field] = [normalize_ordered_presentation(v)[0] for v in left[field]]
                    right[field] = [normalize_ordered_presentation(v)[0] for v in right[field]]
                    transforms.append('ordered_' + field + '_presentation')
                    continue
                # A failed literal quotation proof must not be weakened by the
                # older clause/token path (which canonicalizes spaces/numbers).
                # Even unchanged quoted clauses conservatively bind this list's
                # order until there is a separate quotation-aware move proof.
                if any(re.search(r'''["'“”‘’「」『』\\]''', v)
                       for v in left.get(field, []) + right.get(field, [])):
                    continue
                if not independent_context:
                    continue
                proof = clause_correspondence(left.get(field, []), right.get(field, []))
                if proof:
                    correspondence[field] = proof
                    # Compute each side from its own observed clauses. Never
                    # fill a DOM value from the corresponding source clause.
                    left[field] = [normalize_clause(v)[0] for v in left[field]]
                    actual = [normalize_clause(v)[0] for v in right[field]]
                    right[field] = [actual[i] for i in proof['source_to_dom']]
                    transforms.append('independent_' + field + '_correspondence')
            if transforms:
                normalized['source_blocks'][0]['text'] = json.dumps(left, ensure_ascii=False)
                normalized['rendered_blocks'][0]['visible_text'] = json.dumps(right, ensure_ascii=False)
                report = compare_fact_bundle(normalized)
            # Keyed rows do not make a positional footnote independent. Preserve
            # reading-order constraints even when every keyed cell still exists.
            prose = '\n'.join(str(v) for facts in (left, right) for key in ('caption', 'conditions', 'notes', 'facts')
                              for v in (facts.get(key, []) if isinstance(facts.get(key, []), list) else [facts.get(key, '')]))
            if (re.search(r'(?:첫|두|세|네|다섯|여섯|일곱|여덟|아홉|열|\d+)\s*번째\s*행|제?\s*\d+\s*행|(?:앞|위|다음)\s*행', prose) and
                    original_rows != right.get('rows', []) and report['quality_label'] == 'preserved'):
                report = dict(report, quality_label='changed', findings=report.get('findings', []) + ['position_dependent_row_order'])
        except (ValueError, KeyError, TypeError, IndexError, AttributeError):
            # The existing strict checker owns all malformed/unsupported cases.
            # Never turn a failed normalization into content identity.
            pass
    return {**report, 'scoped_gate_version': VERSION, 'normalizations': transforms,
            'clause_correspondence': correspondence,
            'input_sha256': hashlib.sha256(json.dumps(state, ensure_ascii=False, sort_keys=True,
                separators=(',', ':'), allow_nan=False).encode()).hexdigest(),
            'mode': 'shadow_only', 'human_approved': False, 'whole_original_verified': False}
