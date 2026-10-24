import copy

import pytest

from moa import evidence_challenge as c


def test_operation_gold_is_replayable_without_model_or_checker_answers():
    rows = c.generate('unit-test-author-seed', 'unit')
    audit = c.validate(rows, set())
    assert audit['items'] == len(c.OPERATIONS)
    assert audit['labels'] == {'preserved': 4, 'changed': 12, 'insufficient': 6}
    for row in rows:
        op = row['meta']['error_type']
        expected = next(iter(row['expected'].values()))
        assert expected == ('preserved' if op in c.NORMAL else 'insufficient' if op in c.MISSING else 'changed')
    bad = copy.deepcopy(rows); bad[0]['expected'] = {'conditions': 'changed'}
    with pytest.raises(ValueError): c.validate(bad, set())


def test_splits_are_source_and_template_disjoint_and_projection_has_no_gold():
    dev, test = c.generate('fixture-dev', 'dev'), c.generate('fixture-test', 'test')
    assert not {r['meta']['family'] for r in dev} & {r['meta']['family'] for r in test}
    assert not {r['meta']['source_sha256'] for r in dev} & {r['meta']['source_sha256'] for r in test}
    freeze = {'candidate_sha256': {'r1': 'a' * 64}, 'settings_sha256': 'b' * 64, 'checker_sha256': 'c' * 64}
    projected = c.project(dev, freeze, explicit=True)
    assert all(set(r) == {'case_id', 'state', 'questions'} for r in projected)
    assert all('Evidence availability' in r['state']['title'] for r in projected)
    with pytest.raises(ValueError): c.project(dev, {}, explicit=True)
    with pytest.raises(ValueError): c.validate(test, {test[0]['meta']['source_sha256']})


def test_availability_is_recomputed_from_observed_inventory_not_expected_label():
    rows = c.generate('inventory-proof', 'unit')
    by_op = {r['meta']['error_type']: r for r in rows}
    file = by_op['missing_pages']['evidence']
    assert file['file_complete'] is False and file['scope_complete'] is True
    assert by_op['missing_header']['evidence']['table_context_complete'] is False
    assert by_op['role_unverified']['evidence']['role_confirmed'] is False
    # An actual content error still has sufficient evidence.
    assert not by_op['amount']['evidence']['review_required']


def test_alias_case_or_group_id_cannot_inflate_evaluation_denominators():
    rows = c.generate('dedup-fixture', 'unit')
    alias = copy.deepcopy(rows[0]); alias['case_id'] += '-alias'
    with pytest.raises(ValueError): c.validate(rows + [alias], set())
    rows[0]['meta']['original_group_id'] = 'forged-group'
    with pytest.raises(ValueError): c.validate(rows, set())
