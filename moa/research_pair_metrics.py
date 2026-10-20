"""Score the direct checker and raw model separately on observed paired inputs.

The adapter parses the actual DOM text, including dt/dd card text. It does not
read the author's oracle or expected labels. Unsupported layouts abstain.
"""
from __future__ import annotations

import json

from .scoped_verification import compare_scoped_facts


def _facts(blocks, source=False):
    by_id = {}
    for b in blocks:
        refs = [b['id']] if source else b['source_refs']
        if len(refs) != 1 or refs[0] in by_id:
            raise ValueError('ambiguous source/DOM scope')
        by_id[refs[0]] = b['text' if source else 'visible_text']
    if set(by_id) != {'head', 'unit', 'note', 'condition', 'main'}:
        raise ValueError('missing/unmapped context')
    headers = [s.strip() for s in by_id['head'].split('|')]
    lines = [s.strip() for s in by_id['main'].splitlines() if s.strip()]
    if lines and all('|' in line for line in lines):
        rows = [[cell.strip() for cell in line.split('|')] for line in lines]
    else:
        width = len(headers)
        if len(lines) % (2*width):
            raise ValueError('incomplete card relation')
        rows = []
        for offset in range(0, len(lines), 2*width):
            values = lines[offset:offset+2*width]
            if values[::2] != headers:
                raise ValueError('unrecognized card header correspondence')
            rows.append(values[1::2])
    if not rows or any(len(row) != len(headers) for row in rows):
        raise ValueError('incomplete table row')
    return {'headers': headers, 'rows': rows, 'unit': by_id['unit'],
            'conditions': [by_id['condition']], 'notes': [by_id['note']]}


def direct(row):
    state = row['state']
    try:
        left = _facts(state['source_blocks'], source=True)
        right = _facts(state['rendered_blocks'])
    except ValueError as error:
        return {'prediction': 'insufficient', 'reason': str(error),
                'prediction_origin': 'source_dom_inventory_and_direct_checker',
                'confirmed_mismatch': False}
    scoped = {'source_complete': True, 'file_role': state['file_role'],
              'source_blocks': [{'id': 'scope', 'text': json.dumps(left, ensure_ascii=False)}],
              'rendered_blocks': [{'id': 'dom', 'source_refs': ['scope'],
                                   'visible_text': json.dumps(right, ensure_ascii=False)}]}
    result = compare_scoped_facts(scoped)
    return {'prediction': result['quality_label'], 'reason': result.get('findings', []),
            'prediction_origin': 'source_dom_inventory_and_direct_checker',
            'confirmed_mismatch': result['quality_label'] == 'changed',
            'checker_version': result['scoped_gate_version']}


def compare_with_direct(rows, model_answers):
    labels = ('preserved', 'changed', 'insufficient')
    direct_by_id = {r['case_id']: direct(r) for r in rows}
    result = {'items': len(rows), 'direct': {}, 'models': {}}
    matrix = {g: {p: 0 for p in labels} for g in labels}
    for row in rows:
        gold = next(iter(row['expected'].values()))
        matrix[gold][direct_by_id[row['case_id']]['prediction']] += 1
    result['direct'] = {'confusion': matrix, 'correct': sum(matrix[g][g] for g in labels),
                        'normal_false_alerts': matrix['preserved']['changed']+matrix['preserved']['insufficient'],
                        'actual_errors_missed': sum(matrix['changed'].values())-matrix['changed']['changed'],
                        'prediction_origin': 'rules_and_evidence_inventory_not_model'}
    for model, answers in model_answers.items():
        indexed = {a['case_id']: a for a in answers}
        extra, unnecessary = 0, 0
        combined = {g: {p: 0 for p in labels} for g in labels}
        for row in rows:
            identity = row['case_id']
            gold = next(iter(row['expected'].values()))
            hard = direct_by_id[identity]['prediction']
            raw = indexed[identity].get('prediction')
            extra += gold == 'changed' and hard != 'changed' and raw == 'changed'
            unnecessary += gold == 'preserved' and hard == 'preserved' and raw != 'preserved'
            # A model disagreement asks for review; it cannot replace a direct
            # confirmed mismatch or a source-inventory insufficiency with pass.
            final = hard if hard != 'preserved' or raw == 'preserved' else 'insufficient'
            combined[gold][final] += 1
        result['models'][model] = {'additional_error_alerts_over_direct': extra,
                                  'unnecessary_reviews_added_by_model': unnecessary,
                                  'combined_confusion': combined,
                                  'combined_correct': sum(combined[g][g] for g in labels)}
    return result
