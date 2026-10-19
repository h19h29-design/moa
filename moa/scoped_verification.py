"""Versioned, shadow-only comparison for native section / actual DOM facts.

The frozen R2-v4 checker stays unchanged. No model answer, training label or
mobile renderer participates in the independent table correspondence check.
This does not certify original-file extraction or authorize publication.
"""
from __future__ import annotations

import copy
import hashlib
import json

from .verification import compare_fact_bundle, content_tokens, department_links

VERSION = 'moa.scoped-content-gate.v2'


def compare_scoped_facts(state):
    report = compare_fact_bundle(state)
    transforms = []
    if state.get('source_complete') is True:
        try:
            sources, rendered = state['source_blocks'], state['rendered_blocks']
            if len(sources) != 1 or len(rendered) != 1 or rendered[0]['source_refs'] != [sources[0]['id']]:
                raise ValueError('ambiguous scope')
            left = json.loads(sources[0]['text'])
            right = json.loads(rendered[0]['visible_text'])
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
                normalized = copy.deepcopy(state)
                normalized_left = dict(left, headers=headers[:3], rows=flat)
                normalized['source_blocks'][0]['text'] = json.dumps(normalized_left, ensure_ascii=False)
                report = compare_fact_bundle(normalized)
                transforms.append('explicit_repeated_department_triples_to_keyed_list')
        except (ValueError, KeyError, TypeError, IndexError, AttributeError):
            # The existing strict checker owns all malformed/unsupported cases.
            # Never turn a failed normalization into content identity.
            pass
    return {**report, 'scoped_gate_version': VERSION, 'normalizations': transforms,
            'input_sha256': hashlib.sha256(json.dumps(state, ensure_ascii=False, sort_keys=True,
                separators=(',', ':'), allow_nan=False).encode()).hexdigest(),
            'mode': 'shadow_only', 'human_approved': False, 'whole_original_verified': False}
