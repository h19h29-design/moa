import pytest

from moa import research_dataset as ds


def generated(seed='mini-author-2026-10-20'):
    return ds.generate(seed, families=12)


def test_generator_is_deterministic_for_a_seed():
    first = generated()
    second = generated()
    assert ds.digest(first) == ds.digest(second)
    assert ds.digest(generated('other-seed')) != ds.digest(first)


def test_generator_reports_family_and_case_inventory():
    rows = generated()
    summary = ds.summarize(rows)
    assert summary['families'] == 12
    assert summary['rows'] == len(rows)
    assert summary['rights'] == 'independently_authored_synthetic_by_moa'
    assert summary['human_gold'] == 0
    assert summary['real_documents'] == 0


def test_every_row_carries_independent_oracle_and_recorded_operation():
    for row in generated():
        meta = row['meta']
        assert meta['label_origin'] == 'independent_source_and_recorded_operation'
        assert meta['rights_status'] == 'allowed'
        assert meta['forbidden_lineage'] == []
        assert row['expected']
        assert meta['operation']['name']


def test_normalizations_are_not_counted_as_changed():
    rows = generated()
    normal = [r for r in rows if r['meta']['operation']['name'] in ds.NORMALIZATIONS]
    assert normal
    assert all(next(iter(r['expected'].values())) == 'preserved' for r in normal)


def test_semantic_mutations_are_always_changed():
    rows = generated()
    changed = [r for r in rows if r['meta']['operation']['name'] in ds.CHANGES]
    assert changed
    assert all(next(iter(r['expected'].values())) == 'changed' for r in changed)


def test_insufficient_rows_omit_required_evidence_only():
    rows = generated()
    missing = [r for r in rows if r['meta']['operation']['name'].startswith('missing_')]
    assert missing
    for row in missing:
        assert next(iter(row['expected'].values())) == 'insufficient'
        assert row['state']['source_complete'] is False


def test_no_gold_leaks_into_the_model_input():
    leaks = ('expected', 'label', 'gold', 'answer', 'split', 'family', 'error_type')
    for row in generated():
        text = str(row['state'])
        for word in leaks:
            assert word not in text


def test_splits_keep_every_family_inside_one_split():
    for split in ds.split_dataset(generated()):
        families = {row['meta']['family_group_id'] for row in split['rows']}
        assert not (families - set(split['families']))
    parts = ds.split_dataset(generated())
    assert {p['name'] for p in parts} == {'train', 'dev', 'blind'}
    blind_families = next(p for p in parts if p['name'] == 'blind')['families']
    train_families = next(p for p in parts if p['name'] == 'train')['families']
    assert not (set(blind_families) & set(train_families))


def test_blind_projection_strips_gold_and_meta():
    parts = {p['name']: p for p in ds.split_dataset(generated())}
    projected = ds.project(parts['blind']['rows'])
    for row in projected:
        assert set(row) == {'case_id', 'state', 'questions'}


def test_protected_sources_are_refused():
    rows = generated()
    blocked = {row['meta']['source_sha256'] for row in rows[:2]}
    with pytest.raises(ValueError):
        ds.reject_protected(rows, blocked)


def test_summary_marks_research_only_and_no_operational_promotion():
    summary = ds.summarize(generated())
    assert summary['scope'] == 'synthetic_research'
    assert summary['operational_ready'] is False
    assert summary['human_approvals'] == 0


def test_insufficient_is_a_split_class_not_a_forced_regression():
    rows = generated()
    labels = {next(iter(r['expected'].values())) for r in rows}
    assert labels >= {'preserved', 'changed', 'insufficient'}
