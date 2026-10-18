"""Synthetic-only recovery agreement tests; these never run an optimizer."""
import copy

import pytest

from moa import laya_recovery as recovery
from moa.training_bundle import examples


def rows():
    return list(examples())


def test_manifest_binds_existing_splits_input_order_and_exact_budgets():
    records = rows()
    out = recovery.build_manifest(records, bundle_sha256='a'*64,
                                  base_sha256='b'*64, previous_sha256='c'*64)
    assert {k: v['questions'] for k,v in out['splits'].items()} == {
        'train':600, 'calibration':200, 'validation':200}
    assert out['budgets']['S1'] == {'maximum_questions':24, 'maximum_updates':40, 'maximum_seconds':600}
    assert out['budgets']['R1'] == {'maximum_candidates':1, 'maximum_updates':120, 'maximum_seconds':2700}
    assert out['budgets']['evaluation'] == {'maximum_slice_seconds':600, 'maximum_total_seconds':1800}
    assert out['training_executor'] == 'macbook_codex_only'
    assert out['operational_promotion_allowed'] is False
    assert out['splits']['validation']['role'] == 'previously_observed_regression_holdout'
    altered = copy.deepcopy(records)
    altered[0]['state'] = dict(reversed(list(altered[0]['state'].items())))
    changed = recovery.build_manifest(altered,bundle_sha256='a'*64,base_sha256='b'*64,previous_sha256='c'*64)
    assert changed['splits']['train']['model_input_sha256'] != out['splits']['train']['model_input_sha256'] or changed['splits']['calibration']['model_input_sha256'] != out['splits']['calibration']['model_input_sha256'] or changed['splits']['validation']['model_input_sha256'] != out['splits']['validation']['model_input_sha256']


def test_identical_effective_inputs_with_conflicting_gold_are_rejected():
    records=rows()
    duplicate=copy.deepcopy(records[0])
    duplicate['expected']={'conditions':'changed'}
    with pytest.raises(ValueError,match='contradictory_gold'):
        recovery.build_manifest(records+[duplicate],bundle_sha256='a'*64,base_sha256='b'*64,previous_sha256='c'*64)


def test_source_and_family_derivatives_cannot_cross_splits():
    for field in ('original_group_id','family_group_id','source_sha256'):
        records=rows()
        train=next(r for r in records if r['meta']['split']=='train')
        test=next(r for r in records if r['meta']['split']=='validation')
        test['meta'][field]=train['meta'][field]
        with pytest.raises(ValueError,match='group_leakage'):
            recovery.build_manifest(records,bundle_sha256='a'*64,base_sha256='b'*64,previous_sha256='c'*64)


def test_wrong_label_and_prohibited_label_origin_fail_before_handoff():
    for key,value,error in [('label_origin','laya_self_label','label_provenance'),
                            ('forbidden_lineage',['jev'],'label_provenance')]:
        records=rows(); records[0]['meta'][key]=value
        with pytest.raises(ValueError,match=error):
            recovery.build_manifest(records,bundle_sha256='a'*64,base_sha256='b'*64,previous_sha256='c'*64)
    records=rows(); records[0]['expected']['conditions']=1
    with pytest.raises(ValueError,match='label_mapping'):
        recovery.build_manifest(records,bundle_sha256='a'*64,base_sha256='b'*64,previous_sha256='c'*64)


def test_identical_source_inputs_cannot_hide_a_derivative_in_another_split():
    records=rows()
    train=next(r for r in records if r['meta']['split']=='train')
    test=next(r for r in records if r['meta']['split']=='validation')
    test['state']=copy.deepcopy(train['state'])
    test['questions']=copy.deepcopy(train['questions'])
    test['expected']=copy.deepcopy(train['expected'])
    with pytest.raises(ValueError,match='effective_input_split_leakage'):
        recovery.build_manifest(records,bundle_sha256='a'*64,base_sha256='b'*64,previous_sha256='c'*64)


def test_manifest_rejects_noncanonical_options_instead_of_reporting_wrong_indices():
    records=rows()
    q=records[0]['questions']['conditions']
    q['criteria']=dict(reversed(list(q['criteria'].items())))
    with pytest.raises(ValueError,match='option_order'):
        recovery.build_manifest(records,bundle_sha256='a'*64,base_sha256='b'*64,previous_sha256='c'*64)


def test_actual_runtime_encoding_settings_are_read_and_must_match_training():
    source='def predict_payload(agent):\n return agent.predict(state, qs, max_len=512, head_max_len=128)\n'
    overrides=recovery.predict_encoding_overrides(source)
    assert overrides=={'max_len':512,'head_max_len':128}
    with pytest.raises(ValueError,match='encoding_config_mismatch'):
        recovery.encoding_settings({'max_len':1024,'head_max_len':256},
                                   {'max_len':1024,'head_max_len':256},overrides,parallel=False)
    assert recovery.encoding_settings({'max_len':1024,'head_max_len':256},
               {'max_len':512,'head_max_len':128},overrides,parallel=False)['max_len']==512
    with pytest.raises(ValueError,match='unsupported_parallel_training'):
        recovery.encoding_settings({'max_len':1024,'head_max_len':256},
                                   {'max_len':512,'head_max_len':128},overrides,parallel=True)
    with pytest.raises(ValueError,match='runtime_encoding_not_literal'):
        recovery.predict_encoding_overrides('def f(agent):\n return agent.predict(s,q,max_len=dynamic)\n')


def test_identical_actual_token_inputs_reject_conflicting_gold_or_cross_split():
    ledger={};tensors={'input_ids':[[1,2]],'attention_mask':[[1,1]],'marker_pos':[[1]],'marker_mask':[[1]],'qtype':[0]}
    first=recovery.record_token_input(ledger,tensors,'preserved','train')
    assert recovery.record_token_input(ledger,copy.deepcopy(tensors),'preserved','train')==first
    with pytest.raises(ValueError,match='token_input_contradictory_gold'):
        recovery.record_token_input(ledger,tensors,'changed','train')
    with pytest.raises(ValueError,match='token_input_split_leakage'):
        recovery.record_token_input(ledger,tensors,'preserved','validation')
