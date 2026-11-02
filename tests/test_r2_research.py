"""Synthetic research authorization is not the operational real-data READY gate."""
import copy
import hashlib
import json

import pytest

from moa import r2_research as r


def row(i, split='train', label='preserved'):
    return {'case_id': str(i), 'state': {}, 'questions': {'conditions': {}},
            'expected': {'conditions': label}, 'meta': {
                'split': split, 'original_group_id': split+'-g'+str(i//20),
                'source_sha256': hashlib.sha256((split+str(i//20)).encode()).hexdigest(),
                'family_group_id': split+'-f'+str(i//20),
                'rights_status': 'allowed', 'rights_evidence': 'independently_authored_synthetic_by_moa',
                'synthetic_gold': True, 'human_gold': False, 'evaluation_only': False,
                'forbidden_lineage': [], 'label_origin': 'deterministic_oracle'}}


def rows():
    return [row(i) for i in range(240)]+[row(i+240, 'dev') for i in range(80)]


def test_only_independently_authored_train_dev_are_research_eligible():
    audit = r.research_eligibility(rows())
    assert audit['scope'] == 'synthetic_research'
    assert audit['counts'] == {'train': 240, 'dev': 80}
    assert audit['operational_ready'] is False
    for key, value in [('synthetic_gold', False), ('evaluation_only', True),
                       ('rights_status', 'unreviewed'), ('label_origin', 'laya'),
                       ('forbidden_lineage', ['old-eval'])]:
        bad = rows(); bad[0]['meta'][key] = value
        with pytest.raises(ValueError): r.research_eligibility(bad)
    bad = rows(); bad[0]['meta']['split'] = 'blind'
    with pytest.raises(ValueError): r.research_eligibility(bad)
    bad = rows(); bad[-1]['meta']['family_group_id'] = bad[0]['meta']['family_group_id']
    with pytest.raises(ValueError, match='split'): r.research_eligibility(bad)


def test_s1_selection_is_bounded_balanced_train_only_and_stable():
    data = rows()
    for i, item in enumerate(data):
        item['expected']['conditions'] = r.LABELS[i % 3]
    a = r.select_s1(data)
    assert a == r.select_s1(list(reversed(data)))
    assert len(a) == 32 and all(x['meta']['split'] == 'train' for x in a)
    assert sorted(sum(next(iter(x['expected'].values())) == label for x in a)
                  for label in r.LABELS) == [10, 11, 11]


def test_first_limit_wins_and_s1_weights_cannot_seed_r2():
    assert r.phase_config('s1')['planned_updates'] == 40
    assert r.phase_config('r2a')['planned_updates'] == 60
    assert r.stop_reason('r2a', updates=60, seconds=1, epoch=2) == 'epoch_limit'
    assert r.stop_reason('r2a', updates=399, seconds=2700, epoch=0) == 'time_limit'
    assert r.stop_reason('s1', updates=40, seconds=1, epoch=1) == 'update_limit'
    assert r.phase_config('r2a')['parent'] == 'immutable_r1'
    with pytest.raises(ValueError): r.phase_config('r2b')


def test_queue_fencing_idempotence_monotonic_budgets_and_no_retraining(tmp_path):
    path = tmp_path/'LEDGER.json'
    q = r.Journal(path, 'one-shot', {'data_sha256':'a'*64})
    first = q.claim('s1')
    with pytest.raises(ValueError, match='running'): q.claim('s1')
    with pytest.raises(ValueError, match='fencing'): q.record('s1', first+1, 1, 2)
    q.record('s1', first, 4, 10, status='parked', checkpoint='saved-4')
    second = q.claim('s1', resume=True)
    assert second == first+1
    with pytest.raises(ValueError, match='fencing'): q.record('s1', first, 4, 10)
    with pytest.raises(ValueError, match='nonmonotonic'): q.record('s1', second, 3, 11)
    with pytest.raises(ValueError, match='budget'): q.record('s1', second, 41, 11)
    q.record('s1', second, 40, 600, status='saved', checkpoint='saved-40')
    q.mark_reloaded('s1', 'saved-40')
    assert q.claim('s1') is None
    assert q.read()['phases']['s1']['reload_verified'] is True
    with pytest.raises(ValueError, match='identity'): r.Journal(path, 'one-shot', {'data_sha256':'b'*64})


def test_crash_never_silently_restarts_an_unreconciled_phase(tmp_path):
    q = r.Journal(tmp_path/'ledger', 'run', {})
    q.claim('s1')
    with pytest.raises(ValueError, match='running'): q.claim('r2a')
    with pytest.raises(ValueError, match='running'): q.claim('s1', resume=True)


def test_blind_requires_saved_reloaded_candidate_and_fixed_selection(tmp_path):
    q = r.Journal(tmp_path/'ledger', 'run', {})
    freeze = dict(candidate_sha256='a'*64, formatter_sha256='b'*64,
                  rubric_sha256='c'*64, checker_sha256='d'*64,
                  settings_sha256='e'*64, selection='one_r2a_no_blind_tuning')
    with pytest.raises(ValueError, match='reloaded'): q.freeze(freeze)
    token = q.claim('s1'); q.record('s1', token, 1, 1, status='saved', checkpoint='s1')
    q.mark_reloaded('s1', 's1')
    token = q.claim('r2a'); q.record('r2a', token, 1, 1, status='saved', checkpoint='r2')
    q.mark_reloaded('r2a', 'r2')
    assert q.freeze(freeze) == r.digest(freeze)
    with pytest.raises(ValueError, match='frozen'): q.freeze(dict(freeze, selection='new'))


def test_future_queue_needs_new_independent_gold_not_self_labels(tmp_path):
    old = {'source_sha256':'a', 'gold_sha256':'b', 'label_origin':'independent_oracle',
           'rights_status':'allowed', 'evaluation_only':False}
    assert r.future_candidate([old], used=[])['ready']
    assert not r.future_candidate([old], used=['b'])['ready']
    for origin in ('laya', 'jev', 'unreviewed_hold'):
        assert not r.future_candidate([dict(old,label_origin=origin)], used=[])['ready']
    assert not r.future_candidate([dict(old,evaluation_only=True)], used=[])['ready']


def test_atomic_checkpoint_does_not_overwrite_and_rejects_bad_hash(tmp_path):
    def writer(path):
        (path/'state').write_bytes(b'good')
        return {'state': hashlib.sha256(b'good').hexdigest()}
    saved = tmp_path/'saved'
    r.commit_snapshot(saved, writer)
    assert (saved/'state').read_bytes() == b'good'
    with pytest.raises(FileExistsError): r.commit_snapshot(saved, writer)
    with pytest.raises(ValueError, match='digest'):
        r.commit_snapshot(tmp_path/'bad', lambda p: {'missing':'0'*64})


def test_unfrozen_or_different_candidate_never_authorizes_evaluation():
    request={'evaluation_id':'blind','model':'r1','freeze_sha256':r.digest(None),
             'rows':[{'case_id':'one','state':{},'questions':{'conditions':{}}}]}
    with pytest.raises(ValueError,match='frozen'):r.authorize_evaluation({},request,'a'*64)
    freeze={'candidate_sha256':'a'*64}
    data={'freeze':freeze,'phases':{'r2a':{'reload_verified':True}}}
    request.update(freeze_sha256=r.digest(freeze),model='r2a')
    with pytest.raises(ValueError,match='candidate'):r.authorize_evaluation(data,request,'b'*64)
    r.authorize_evaluation(data,request,'a'*64)


@pytest.mark.parametrize('ids,questions', [(['x','x'],{'conditions':{}}), ([''],{'conditions':{}}),
                                        (['x'],{}), (['x'],{'conditions':{},'omission':{}})])
def test_bad_evaluation_inventory_is_rejected_before_model_load(ids,questions):
    request={'evaluation_id':'blind','model':'r1','freeze_sha256':'a'*64,
             'rows':[{'case_id':i,'state':{},'questions':questions} for i in ids]}
    with pytest.raises(ValueError):r.validate_evaluation_request(request,{'conditions':{},'omission':{}})


def test_resume_requires_intact_snapshot_and_matching_cursor_configuration(tmp_path):
    cp=tmp_path/'snapshot';cp.mkdir();(cp/'training-state.pt').write_bytes(b'original')
    hashes={'training-state.pt':r.file_hash(cp/'training-state.pt')}
    r.verify_snapshot(cp,hashes)
    (cp/'training-state.pt').write_bytes(b'changed')
    with pytest.raises(ValueError,match='digest'):r.verify_snapshot(cp,hashes)
    state={'step':3,'cursor':{'epoch':0,'next_microbatch':24},'phase':'s1','run_id':'run',
           'data_sha256':'a'*64,'configuration':r.phase_config('s1')}
    r.validate_resume_state(state,phase='s1',run_id='run',data_sha256='a'*64,steps=3)
    state['cursor']['next_microbatch']=16
    with pytest.raises(ValueError,match='cursor'):r.validate_resume_state(state,phase='s1',run_id='run',data_sha256='a'*64,steps=3)


def test_zero_step_diagnostic_cannot_unlock_r2(tmp_path):
    q=r.Journal(tmp_path/'ledger','run',{})
    t=q.claim('s1');q.record('s1',t,0,600,status='saved',checkpoint='initial')
    with pytest.raises(ValueError,match='actual updates'):q.mark_reloaded('s1','initial')
