"""Lossless source/observed-DOM section pairing for isolated research inputs.

Alignment uses source references and a declared context graph, never an oracle
label or checker verdict. Missing HTML is not repaired from the source. Headers,
units, notes and prerequisite clauses travel with their dependent section.
"""
from __future__ import annotations

import copy

VERSION = 'moa.source-dom-pair.v1'


def _indexed(rows, label):
    result = {}
    for row in rows:
        identity = row.get('id')
        if not isinstance(identity, str) or not identity:
            raise ValueError('missing ' + label + ' identity')
        if identity in result:
            raise ValueError('duplicate ' + label + ' identity')
        result[identity] = row
    return result


def align(source_sections, observed_dom, *, focus_ids):
    sources = _indexed(source_sections, 'source')
    _indexed(observed_dom, 'DOM')
    if not focus_ids or any(x not in sources for x in focus_ids):
        raise ValueError('explicit existing source focus required')
    selected, missing = set(), set()
    pending = list(focus_ids)
    while pending:
        identity = pending.pop()
        if identity in selected or identity in missing:
            continue
        if identity not in sources:
            missing.add(identity)
            continue
        selected.add(identity)
        refs = sources[identity].get('context_ids', [])
        if not isinstance(refs, list) or any(not isinstance(x, str) for x in refs):
            raise ValueError('invalid context references')
        pending.extend(refs)
    source_blocks = [{'id': s['id'], 'text': s['text']}
                     for s in source_sections if s['id'] in selected]
    if any(not isinstance(b['text'], str) for b in source_blocks):
        raise ValueError('source text must be explicit')
    rendered, mapped, unmapped = [], set(), []
    for block in observed_dom:
        refs = block.get('source_refs')
        if (not isinstance(refs, list) or any(not isinstance(x, str) for x in refs)
                or not isinstance(block.get('visible_text'), str)):
            raise ValueError('observed DOM fields required')
        # An unreferenced addition or unknown reference must not disappear just
        # because it cannot be aligned. Known unrelated document sections can.
        unknown = not refs or any(r not in sources and r not in missing for r in refs)
        if selected.intersection(refs) or set(missing).intersection(refs) or unknown:
            rendered.append({k: copy.deepcopy(block[k]) for k in
                             ('id', 'source_refs', 'visible_text')})
            mapped.update(refs)
            if unknown:
                unmapped.append(block['id'])
    return {'title': '', 'file_role': 'notice', 'source_complete': not missing,
            'source_blocks': source_blocks, 'rendered_blocks': rendered,
            'attachment_manifest': [],
            'alignment': {'version': VERSION, 'focus_ids': list(focus_ids),
                          'missing_context_ids': sorted(missing),
                          'missing_render_ids': sorted(selected - mapped),
                          'unmapped_dom_ids': unmapped,
                          'observed_dom_ids': [b['id'] for b in rendered]}}


def model_state(alignment):
    """Strip trace/labels; exactly the installed LAYA state contract."""
    return {k: copy.deepcopy(alignment[k]) for k in
            ('title', 'file_role', 'source_complete', 'source_blocks',
             'rendered_blocks', 'attachment_manifest')}
