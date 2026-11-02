import copy
import hashlib

import pytest

from moa.evidence import assess_evidence, evidence_input
from moa.r2_data import fact_state


def provenance():
    return {'source_revision': 'source-v1', 'render_revision': 'html-v1',
            'file': {'sha256': 'a' * 64, 'integrity_verified': True,
                     'expected_pages': ['p1', 'p2'], 'available_pages': ['p1', 'p2']},
            'scope': {'required_context_ids': ['b1', 'header1', 'note1'],
                      'provided_context_ids': ['b1', 'header1', 'note1'],
                      'table_inventory_complete': True},
            'tables': [{'id': 't1', 'source_block_id': 'b1', 'required': {'headers': ['header1'], 'footnotes': ['note1'], 'merges': []},
                        'provided': {'headers': ['header1'], 'footnotes': ['note1'], 'merges': []}}],
            'role': {'value': 'notice', 'confirmed_by': 'synthetic_author',
                     'source_sha256': 'a' * 64, 'source_revision': 'source-v1'}}


def state():
    return fact_state({'facts': ['참가 금액: 2000원']}, {'facts': ['참가 금액: 2000원']})


def test_four_evidence_dimensions_are_independent_and_bound_to_actual_inputs():
    p, s = provenance(), state()
    result = assess_evidence(p, s)
    assert [result[k] for k in ('file_complete', 'scope_complete', 'table_context_complete', 'role_confirmed')] == [True] * 4
    assert result['source_complete'] and not result['review_required']
    p['file']['available_pages'] = ['p1']
    result = assess_evidence(p, s)
    assert result['file_complete'] is False and result['source_complete'] is True
    assert result['review_required'] and 'missing_file_pages' in result['reasons']
    # A correct visible section is not certification of an incomplete file.
    assert result['scope_complete'] is True


@pytest.mark.parametrize('dimension', ['scope', 'headers', 'footnotes', 'merges', 'role'])
def test_missing_context_and_unconfirmed_roles_never_disappear_into_one_boolean(dimension):
    p = provenance()
    if dimension == 'scope':
        p['scope']['provided_context_ids'] = ['b1']
    elif dimension == 'role':
        p['role']['confirmed_by'] = 'parser_hint'
    else:
        if dimension == 'merges':
            p['tables'][0]['required']['merges'] = ['merge1']
        p['tables'][0]['provided'][dimension] = []
    result = assess_evidence(p, state())
    assert result['review_required']
    if dimension != 'role':
        assert result['source_complete'] is False
    else:
        assert result['role_confirmed'] is False


def test_unknown_pages_unknown_table_inventory_and_stale_role_are_not_verified():
    p = provenance()
    p['file']['expected_pages'] = None
    p['scope']['table_inventory_complete'] = False
    p['role']['source_revision'] = 'old'
    r = assess_evidence(p, state())
    assert r['file_complete'] is None and r['table_context_complete'] is None
    assert not r['source_complete'] and not r['role_confirmed'] and r['review_required']


def test_model_or_gold_cannot_be_metadata_and_duplicate_inventories_are_rejected():
    for key in ('gold', 'prediction', 'source_complete'):
        p = provenance(); p[key] = True
        with pytest.raises(ValueError):
            assess_evidence(p, state())
    p = provenance(); p['scope']['provided_context_ids'].append('b1')
    with pytest.raises(ValueError):
        assess_evidence(p, state())


def test_evidence_input_changes_only_explicit_evidence_encoding_and_derived_complete():
    s = state(); before = copy.deepcopy(s)
    report = assess_evidence(provenance(), s)
    encoded = evidence_input(s, report)
    assert set(encoded) == set(s)
    assert encoded['source_blocks'] == s['source_blocks'] and encoded['rendered_blocks'] == s['rendered_blocks']
    assert 'file_pages=complete' in encoded['title'] and 'table_context=complete' in encoded['title']
    assert s == before and encoded['source_complete'] is True
    changed = copy.deepcopy(s); changed['rendered_blocks'][0]['visible_text'] = 'stale'
    with pytest.raises(ValueError):
        evidence_input(changed, report)
    p = provenance(); p['tables'][0]['provided']['headers'] = []
    assert evidence_input(s, assess_evidence(p, s))['source_complete'] is False


def test_valid_role_claim_cannot_cross_file_or_revision_boundary():
    p = provenance(); p['role']['source_sha256'] = 'b' * 64
    assert not assess_evidence(p, state())['role_confirmed']
    p = provenance(); p['role']['value'] = 'form'
    assert not assess_evidence(p, state())['role_confirmed']


def test_actual_compared_table_requires_a_bound_inventory_even_if_empty_is_claimed_complete():
    p = provenance(); p['tables'] = []
    s = fact_state({'headers': ['학과', '교실'], 'rows': [['천문', '관측실']]},
                   {'headers': ['학과', '교실'], 'rows': [['천문', '관측실']]})
    r = assess_evidence(p, s)
    assert r['table_context_complete'] is False and not r['source_complete'] and r['review_required']
    p = provenance()
    assert assess_evidence(p, s)['table_context_complete'] is True
    p['tables'][0]['source_block_id'] = 'unrelated-block'
    with pytest.raises(ValueError): assess_evidence(p, s)
