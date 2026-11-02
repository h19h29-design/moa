"""Offline shadow evaluation; no database mutation, training, approval or promotion."""
from __future__ import annotations

from .pilot import _observations, section_parts


def inference_sections(manifest, view):
    """Bounded complete source/actual-DOM sections, without gold or rule hints.

    Evidence flags describe the provided parser section, not a human audit of
    the whole original. Model predictions must retain this scope in reports.
    """
    observed = {b['source_refs'][0]: b for b in _observations(manifest, view)}
    result = []
    for source in manifest['sources']:
        if source.get('role') == 'form' or source['id'] not in observed:
            continue
        for index, part in enumerate(section_parts(source, observed[source['id']])):
            sid = source['id'] + '_p' + str(index)
            payload = {'scope': 'section', 'file_role': 'notice', 'title': '',
                'source_complete': bool(manifest['parser_coverage_complete']),
                'source_evidence_verified': True,
                'source_blocks': [{'id': sid, 'text': part['source_text']}],
                'rendered_blocks': [{'id': 'r1', 'source_refs': [sid], 'visible_text': part['render_text']}],
                'questions': ['table_relations'] if 'table_rows' in source else ['conditions', 'omission', 'addition'],
                'required_context_ids': [sid], 'coverage_manifest': {'expected_blocks': 1, 'provided_blocks': 1},
                # "current" is the installed input policy name, not selection
                # of a registry checkpoint. The runner pins an explicit R1 hash.
                'model_policy': {'checkpoint': 'current', 'max_tokens': 1024, 'vision': False},
                'attachment_manifest': [], 'deterministic_findings': []}
            result.append({'source_id': source['id'], 'part_index': index,
                'covered_rows': part['covered_rows'], 'context_rows': part['context_rows'], 'payload': payload})
    return result


def benchmark_metrics(records):
    """Independent-gold denominators only. Missing inference is never a correct answer."""
    valid = [r for r in records if r.get('expected') in ('changed', 'preserved', 'insufficient')]
    out = {'independent_gold_items': len(valid), 'excluded_without_independent_gold': len(records)-len(valid),
           'by_error': {}, 'real_world_accuracy_claim': False}
    def combined(row):
        a, b = row['rule_label'], row['model_label']
        if 'changed' in (a, b): return 'changed'
        if 'insufficient' in (a, b): return 'insufficient'
        return 'preserved' if a == b == 'preserved' else None
    scorers = [('rules', lambda r: r['rule_label']),
               ('model', lambda r: r['model_label']), ('combined', combined)]
    if any('quality_label' in r for r in valid):
        scorers.append(('evidence_combined', lambda r: r.get('quality_label')))
    for name, predict in scorers:
        measured = [(r, predict(r)) for r in valid]
        errors = [(r, p) for r, p in measured if r['expected'] == 'changed']
        normal = [(r, p) for r, p in measured if r['expected'] == 'preserved']
        ran = [(r, p) for r, p in measured if p is not None]
        out[name] = {'evaluated': len(ran), 'unrun': len(measured)-len(ran),
            'accuracy': sum(p == r['expected'] for r,p in ran)/len(ran) if ran else None,
            'accuracy_all': sum(p == r['expected'] for r,p in ran)/len(valid) if valid else None,
            'coverage': len(ran)/len(valid) if valid else None,
            'errors': len(errors), 'changed_detected': sum(p == 'changed' for _,p in errors),
            'changed_recall': sum(p == 'changed' for _,p in errors)/len(errors) if errors else None,
            'unsafe_false_preserved': sum(p == 'preserved' for r,p in measured if r['expected'] != 'preserved'),
            'normal': len(normal), 'normal_false_positives': sum(p == 'changed' for _,p in normal),
            'normal_false_positive_rate': sum(p == 'changed' for _,p in normal)/len(normal) if normal else None,
            'normal_review_rate': sum(p != 'preserved' for _,p in normal)/len(normal) if normal else None}
        for error in sorted({r['error'] for r in valid if r['expected'] == 'changed'}):
            group = [(r,p) for r,p in errors if r['error'] == error]
            item = out['by_error'].setdefault(error, {'items': len(group)})
            item[name+'_detected'] = sum(p == 'changed' for _,p in group)
            item[name+'_blocked'] = sum(p != 'preserved' for _,p in group)
    return out
