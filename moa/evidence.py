"""Evidence availability, derived from extractor inventories, never predictions.

This reports what was available, not whether an extraction is correct. A parser
must not invent page/header/footnote/merge inventories it could not observe.
Whole-file and section completeness intentionally remain separate dimensions.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re

from .worker_contract import canonical

VERSION = 'moa.evidence-inventory.v2'
_SHA = re.compile(r'[0-9a-f]{64}\Z')


def _shape(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError('exact provenance schema required; no labels or predictions')


def _ids(value):
    if (not isinstance(value, list) or len(value) > 10000 or
            any(not isinstance(v, str) or not v or len(v) > 160 for v in value) or
            len(set(value)) != len(value)):
        raise ValueError('unique bounded evidence inventory required')
    return set(value)


def _coverage(expected, provided, *, allow_empty=False):
    actual = _ids(provided)
    if expected is None:
        return None
    required = _ids(expected)
    if not required and not allow_empty:
        return None
    return required <= actual


def state_binding(state):
    # Bind facts, refs, title and role, not a previous caller's complete flag.
    return hashlib.sha256(canonical({k: v for k, v in state.items() if k != 'source_complete'})).hexdigest()


def _compared_table_blocks(state):
    """Bind inventories to the tables actually compared, not a global flag.

    Each factual JSON block represents one table. A table visible only in the
    DOM still requires its source block's header/footnote/merge inventory.
    """
    def is_table(text):
        try:
            facts = json.loads(text)
            return isinstance(facts, dict) and bool(facts.get('headers') or facts.get('rows'))
        except (ValueError, TypeError):
            return False
    needed = {b['id'] for b in state['source_blocks'] if is_table(b['text'])}
    for block in state['rendered_blocks']:
        if is_table(block['visible_text']): needed.update(block['source_refs'])
    return needed


def assess_evidence(provenance, state):
    return _assess_evidence(provenance, state, require_render_coverage=True)


def assess_source_evidence(provenance, state):
    """Research source-acquisition gate, independent of HTML content omissions.

    This does not certify the DOM or alter the existing production v2 gate.
    Missing HTML belongs to content comparison, not source acquisition.
    """
    report = _assess_evidence(provenance, state, require_render_coverage=False)
    report.update(version='moa.source-evidence.v1',
                  authority='source_acquisition_inventory_not_model_or_gold')
    return report


def _assess_evidence(provenance, state, *, require_render_coverage):
    _shape(provenance, ('source_revision', 'render_revision', 'file', 'scope', 'tables', 'role'))
    if any(not isinstance(provenance[k], str) or not provenance[k] for k in ('source_revision', 'render_revision')):
        raise ValueError('source and render revision required')
    file, scope, role = (provenance[k] for k in ('file', 'scope', 'role'))
    _shape(file, ('sha256', 'integrity_verified', 'expected_pages', 'available_pages'))
    _shape(scope, ('required_context_ids', 'provided_context_ids', 'table_inventory_complete'))
    _shape(role, ('value', 'confirmed_by', 'source_sha256', 'source_revision'))
    if not isinstance(file['sha256'], str) or not _SHA.fullmatch(file['sha256']):
        raise ValueError('original file hash required')
    pages = _coverage(file['expected_pages'], file['available_pages'])
    file_ok = pages if file['integrity_verified'] is True else False
    scope_ok = _coverage(scope['required_context_ids'], scope['provided_context_ids'])
    # Every compared source block and actual DOM reference must be inventoried.
    source_ids = [b['id'] for b in state['source_blocks']]
    refs = [r for block in state['rendered_blocks'] for r in block['source_refs']]
    if (not source_ids or len(set(source_ids)) != len(source_ids) or
            (require_render_coverage and set(refs) != set(source_ids)) or
            (not require_render_coverage and scope_ok is True and
             not _ids(scope['required_context_ids']) <= set(source_ids)) or
            not set(source_ids) <= _ids(scope['provided_context_ids'])):
        scope_ok = False
    tables = provenance['tables']
    if not isinstance(tables, list) or len(tables) > 1000:
        raise ValueError('bounded table inventory required')
    table_ok = True if scope['table_inventory_complete'] is True else None
    seen, bound_blocks = set(), set()
    for table in tables:
        _shape(table, ('id', 'source_block_id', 'required', 'provided'))
        if not isinstance(table['id'], str) or not table['id'] or table['id'] in seen:
            raise ValueError('unique table identity required')
        seen.add(table['id'])
        block_id = table['source_block_id']
        if not isinstance(block_id, str) or block_id not in source_ids or block_id in bound_blocks:
            raise ValueError('one table inventory per actual source block required')
        bound_blocks.add(block_id)
        for side in ('required', 'provided'):
            _shape(table[side], ('headers', 'footnotes', 'merges'))
        for field in ('headers', 'footnotes', 'merges'):
            ok = _coverage(table['required'][field], table['provided'][field], allow_empty=field != 'headers')
            if not _ids(table['provided'][field]) <= _ids(scope['provided_context_ids']):
                ok = False
            if ok is False:
                table_ok = False
            elif ok is None and table_ok is True:
                table_ok = None
    if not _compared_table_blocks(state) <= bound_blocks:
        table_ok = False
    role_ok = (role['value'] == state.get('file_role') == 'notice' and
               role['confirmed_by'] in ('human_file_review', 'synthetic_author') and
               role['source_sha256'] == file['sha256'] and role['source_revision'] == provenance['source_revision'])
    reasons = []
    for name, ok, missing in (
            ('file_pages', file_ok, 'missing_file_pages'),
            ('scope_context', scope_ok, 'missing_scope_context'),
            ('table_context', table_ok, 'missing_table_context'),
            ('file_role', role_ok, 'unconfirmed_file_role')):
        if ok is not True:
            reasons.append(missing if ok is False else 'unknown_' + name)
    return {'version': VERSION, 'source_revision': provenance['source_revision'],
            'render_revision': provenance['render_revision'], 'file_sha256': file['sha256'],
            'file_complete': file_ok, 'scope_complete': scope_ok, 'table_context_complete': table_ok,
            'role_confirmed': role_ok, 'source_complete': scope_ok is True and table_ok is True,
            'review_required': bool(reasons), 'reasons': reasons,
            'state_binding_sha256': state_binding(state),
            'provenance_sha256': hashlib.sha256(canonical(provenance)).hexdigest(),
            'authority': 'extractor_inventory_not_model_or_gold', 'human_approved': False}


def evidence_input(state, report):
    """Explicit availability encoding for frozen-weight A/B input experiments.

    The model receives no quality label or rule answer. The ordinary six-field
    worker contract is retained. This encoding is separately versioned/evaluated.
    """
    if report.get('version') != VERSION or report.get('state_binding_sha256') != state_binding(state):
        raise ValueError('evidence does not bind to this input')
    def status(value):
        return 'complete' if value is True else 'missing' if value is False else 'unknown'
    encoded = copy.deepcopy(state)
    parts = [name + '=' + status(report[key]) for name, key in (
        ('file_pages', 'file_complete'), ('scope_context', 'scope_complete'),
        ('table_context', 'table_context_complete'), ('notice_role', 'role_confirmed'))]
    encoded['title'] = (state.get('title', '') + '\nEvidence availability (not a quality answer): ' + '; '.join(parts)).strip()
    encoded['source_complete'] = report['source_complete'] is True
    return encoded


def verify_evidence(state, provenance):
    """Keep known content findings separate from a missing-evidence decision."""
    from .scoped_verification import compare_scoped_facts
    evidence = assess_evidence(provenance, state)
    scoped = copy.deepcopy(state)
    scoped['source_complete'] = evidence['source_complete']
    direct = compare_scoped_facts(scoped)
    # Missing file/role/context never turns into a quality pass, even if a
    # visible portion happens to match. A confirmed mismatch stays recorded.
    final = 'insufficient' if evidence['review_required'] else direct['quality_label']
    return {'evidence': evidence, 'direct_content': direct, 'quality_label': final,
            'authority': 'deterministic_evidence_and_content', 'model_answer': None,
            'human_approved': False, 'publish_allowed': False}
