"""Separate content signal from label/order bias without fitting or training."""
import copy
import importlib
import importlib.util
import json
from pathlib import Path
import struct

import pytest


def api():
    assert importlib.util.find_spec('moa.research_binary_forensics'), 'read-only binary forensics required'
    return importlib.import_module('moa.research_binary_forensics')


def collector():
    path = Path(__file__).parents[1] / 'scripts/moa-research-book-forensics.py'
    assert path.exists(), 'MacBook saved-state audit required'
    spec = importlib.util.spec_from_file_location('book_forensics', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def observed(identity, gold, score, mode):
    return dict(case_id=identity, expected=gold, model='binary_candidate', split='dev',
        family='f', operation='identity' if gold == 'preserved' else 'relation',
        mode=mode, prediction='changed' if score > .5 else 'preserved',
        probabilities={'preserved': 1-score, 'changed': score}, system_forced=False)


def paired():
    # Same original evidence, different actual output; expectation is authored here.
    rows = [dict(case_id=i, state=dict(source_blocks=[dict(id='s', text='원문')]),
        family='f', operation=op, questions={'table_relations': {}})
        for i, op in [('n', 'identity'), ('e', 'relation')]]
    first = [observed('n', 'preserved', .7, 'binary'), observed('e', 'changed', .6, 'binary')]
    second = [observed('n', 'preserved', .9, 'binary_reverse'),
              observed('e', 'changed', .8, 'binary_reverse')]
    return rows, first, second


def test_content_contrast_cannot_mistake_label_collapse_for_semantic_gain():
    rows, first, second = paired()
    out = api().content_contrasts(rows, first, second, train_counts={'preserved': 1, 'changed': 2})
    assert out['paired_source_groups'] == 1
    assert out['content_pairs'] == 1
    assert out['changed_score_increased_pairs'] == 0
    assert out['mean_changed_minus_normal_score'] == pytest.approx(-.1)
    assert out['mean_absolute_order_change'] == pytest.approx(.2)
    assert out['same_input_contrasts_only'] is True
    assert out['model_inferences'] == 0 and out['training_updates'] == 0
    assert out['threshold_fitted'] is False and out['operational_promotion'] is False


@pytest.mark.parametrize('fault', ['source', 'family', 'missing', 'gold', 'forced', 'prediction'])
def test_contrasts_reject_unmatched_sources_and_corrupt_raw_observations(fault):
    rows, first, second = paired()
    if fault == 'source': rows[1]['state']['source_blocks'][0]['text'] = '다른 원문'
    elif fault == 'family': rows[1]['family'] = 'other'
    elif fault == 'missing': rows.pop()
    elif fault == 'gold': second[0]['expected'] = 'changed'
    elif fault == 'forced': first[0]['system_forced'] = True
    elif fault == 'prediction': first[0]['prediction'] = 'preserved'
    with pytest.raises(ValueError):
        api().content_contrasts(rows, first, second, train_counts={'preserved': 1, 'changed': 2})


def tensor_file(path, encoder=b'abcd', head=b'efgh', *, offsets=None):
    header = {'encoder.weight': {'dtype': 'U8', 'shape': [4], 'data_offsets': [0, 4]},
              'scorer.weight': {'dtype': 'U8', 'shape': [4], 'data_offsets': offsets or [4, 8]}}
    raw = json.dumps(header, separators=(',', ':')).encode()
    path.write_bytes(struct.pack('<Q', len(raw)) + raw + encoder + head)


def test_tensor_fingerprints_distinguish_frozen_encoder_from_changed_head(tmp_path):
    a, b = tmp_path / 'old.safetensors', tmp_path / 'saved.safetensors'
    tensor_file(a)
    tensor_file(b, head=b'1234')
    old, saved = collector().tensor_groups(a), collector().tensor_groups(b)
    assert saved['encoder'] == old['encoder']
    assert saved['head'] != old['head']
    tensor_file(b, encoder=b'4321')
    assert collector().tensor_groups(b)['encoder'] != old['encoder']


@pytest.mark.parametrize('offsets', [[3, 7], [4, 9], [-1, 3]])
def test_tensor_reader_refuses_overlap_truncation_or_negative_offsets(tmp_path, offsets):
    path = tmp_path / 'bad.safetensors'
    tensor_file(path, offsets=offsets)
    with pytest.raises(ValueError):
        collector().tensor_groups(path)


def test_diagnostic_reservation_cannot_reset_prior_eval_or_be_dispatched_twice(tmp_path):
    path = tmp_path / 'RESERVATION.json'
    value = api().reserve(path, prior_seconds=55.5, pins={'model': 'fixed'})
    assert value['reserved_seconds'] == 90
    assert value['prior_evaluation_seconds'] == 55.5
    assert value['maximum_cumulative_seconds'] == 600
    before = path.read_bytes()
    with pytest.raises(ValueError):
        api().reserve(path, prior_seconds=55.5, pins={'model': 'fixed'})
    assert path.read_bytes() == before


@pytest.mark.parametrize('seconds', [-1, float('nan'), 511, 600])
def test_no_new_reservation_when_existing_eval_leaves_insufficient_time(tmp_path, seconds):
    path = tmp_path / 'RESERVATION.json'
    with pytest.raises(ValueError):
        api().reserve(path, prior_seconds=seconds, pins={'model': 'fixed'})
    assert not path.exists()
