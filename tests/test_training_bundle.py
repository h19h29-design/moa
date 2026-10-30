import copy
import hashlib
import json
import pytest
from moa.training_bundle import build, examples, validate_rows, receipt_errors, BUNDLE_ID


def test_independent_korean_gold_group_split_and_bounded_shards(tmp_path):
    manifest = build(tmp_path)
    assert manifest['source_groups'] == 50 and manifest['template_families'] == 10
    assert manifest['rows'] == 1000 and manifest['human_gold'] == 0
    assert [manifest['split_counts'][s]['rows'] for s in ('train', 'calibration', 'validation')] == [600, 200, 200]
    rows = []
    for shard in manifest['shards']:
        raw = (tmp_path / 'training-bundles' / BUNDLE_ID / shard['file']).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == shard['sha256']
        assert len(raw) == shard['bytes'] < 1048576
        rows.extend(json.loads(line) for line in raw.splitlines())
    validate_rows(rows)
    assert build(tmp_path) == manifest
    assert {label for r in rows for label in r['expected'].values()} == {'preserved', 'changed', 'insufficient'}


def test_no_eval_family_leakage_or_model_self_labels():
    rows = list(examples())
    bad = copy.deepcopy(rows)
    bad[0]['meta']['split'] = 'calibration' if bad[0]['meta']['split'] == 'train' else 'train'
    with pytest.raises(ValueError, match='leakage'): validate_rows(bad)
    bad = copy.deepcopy(rows)
    bad[0]['meta']['label_origin'] = 'laya_self_label'
    with pytest.raises(ValueError): validate_rows(bad)
    bad = copy.deepcopy(rows)
    bad[0]['state']['expected'] = bad[0]['expected']
    with pytest.raises(ValueError, match='hint'): validate_rows(bad)


def test_receipt_requires_all_shards_and_original_manifest(tmp_path):
    m = build(tmp_path)
    receipt = {'bundle_id': BUNDLE_ID,
               'manifest_sha256': hashlib.sha256((tmp_path/'training-bundles'/BUNDLE_ID/'TRAINING_BUNDLE_READY.json').read_bytes()).hexdigest(),
               'shard_sha256': {s['file']: s['sha256'] for s in m['shards']}}
    assert receipt_errors(tmp_path, receipt) == []
    receipt['shard_sha256'].pop(next(iter(receipt['shard_sha256'])))
    assert receipt_errors(tmp_path, receipt)
