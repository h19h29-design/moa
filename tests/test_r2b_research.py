"""Fail closed on a saved-but-unlearned diagnostic or a second candidate."""
import pytest

from moa import r2b_research as b
from moa import r2_research as old
from moa.worker_handoff import questions


def rows():
    return [{'case_id':f'{q}-{label}-{i}', 'questions':{q:questions()[q]},
             'expected':{q:label}, 'meta':{'split':split}}
            for split,n in [('train',5),('dev',2)]
            for q in ('conditions','omission','table_relations')
            for label in ('preserved','changed','insufficient') for i in range(n)]


def raw(labels, predictions):
    return [{'case_id':str(i),'expected':label,'prediction':p,'prediction_origin':'raw_laya'}
            for i,(label,p) in enumerate(zip(labels,predictions))]


def evidence():
    return {'updates':40, 'seconds':180., 'weights_changed':True,
            'reload_verified':True,'encoder_unchanged':True,'finite_nonzero_gradients':True}


def test_diagnostic_selection_is_stratified_train_only_not_first_hashes():
    selected=b.select_diagnostic(rows())
    assert len(selected)==27 and all(r['meta']['split']=='train' for r in selected)
    for q in ('conditions','omission','table_relations'):
        for label in ('preserved','changed','insufficient'):
            assert sum(set(r['questions'])=={q} and r['expected'][q]==label for r in selected)==3
    assert selected==b.select_diagnostic(list(reversed(rows())))


def test_saved_weights_with_majority_predictions_must_not_start_full_training():
    labels=['preserved','changed','insufficient']*9
    report=b.diagnostic_gate(raw(labels,['preserved']*27),raw(labels,['preserved']*27),evidence())
    assert report['passed'] is False and report['after']['recall']['insufficient']==0


@pytest.mark.parametrize('override',[{'reload_verified':False},{'weights_changed':False},
    {'updates':0},{'updates':41},{'seconds':601},{'finite_nonzero_gradients':False}])
def test_perfect_answers_cannot_replace_real_bounded_updates_and_reload(override):
    labels=['preserved','changed','insufficient']*9
    e=evidence();e.update(override)
    assert not b.diagnostic_gate(raw(labels,['preserved']*27),raw(labels,labels),e)['passed']


def test_clear_improvement_in_all_three_classes_is_required():
    labels=['preserved','changed','insufficient']*9
    result=b.diagnostic_gate(raw(labels,['preserved']*27),raw(labels,labels),evidence())
    assert result['passed'] and result['accuracy_gain']==pytest.approx(2/3)
    assert not result['operational_promotion_allowed']


def test_rule_forced_insufficient_is_not_model_learning():
    labels=['preserved','changed','insufficient']*9
    post=raw(labels,labels);post[-1]['prediction_origin']='source_complete_rule'
    with pytest.raises(ValueError,match='raw'):b.diagnostic_gate(raw(labels,labels),post,evidence())


def test_fixed_epoch_budget_does_not_fill_unused_400_update_allowance():
    assert b.phase_config('diagnostic')['planned_updates']==40
    cfg=b.phase_config('r2b')
    assert cfg['epochs']==2 and cfg['planned_updates']==60 and cfg['maximum_updates']==400
    assert b.stop_reason('r2b',updates=60,seconds=200,epoch=2)=='epoch_limit'
    assert old.CAPS=={'s1':(40,600),'r2a':(400,2700)}


def test_new_journal_does_not_reopen_failed_or_running_attempts(tmp_path):
    journal=b.Journal(tmp_path/'LEDGER.json','moa-r2b-recovery-20261019',{'sha':'a'*64})
    with pytest.raises(ValueError,match='diagnostic'):journal.claim('r2b')
    token=journal.claim('diagnostic')
    with pytest.raises(ValueError,match='running'):journal.claim('diagnostic')
    journal.record('diagnostic',token,40,180.,'saved',checkpoint='cp',files={'x':'a'*64})
    with pytest.raises(ValueError,match='diagnostic'):journal.claim('r2b')
    labels=['preserved','changed','insufficient']*9
    journal.bind_gate(b.diagnostic_gate(raw(labels,['preserved']*27),raw(labels,labels),evidence()),'b'*64)
    candidate_token=journal.claim('r2b')
    journal.record('r2b',candidate_token,60,200.,'saved',checkpoint='cp2',files={'x':'c'*64})
    assert journal.claim('r2b') is None
    with pytest.raises(ValueError):b.Journal(journal.path,journal.run_id,{'sha':'changed'})


def test_unknown_stop_or_stale_fencing_cannot_consume_a_second_candidate(tmp_path):
    j=b.Journal(tmp_path/'LEDGER.json','moa-r2b-recovery-20261019',{'sha':'a'*64})
    token=j.claim('diagnostic')
    with pytest.raises(ValueError):j.record('diagnostic',token+1,1,1.)
    j.record('diagnostic',token,1,1.,'failed')
    with pytest.raises(ValueError,match='resume'):j.claim('diagnostic',resume=True)


def test_only_bound_optimizer_checkpoint_can_resume_without_resetting_budget(tmp_path):
    j=b.Journal(tmp_path/'LEDGER.json','moa-r2b-recovery-20261019',{'sha':'a'*64})
    t=j.claim('diagnostic');j.record('diagnostic',t,8,30.,'parked',checkpoint='cp',files={'x':'a'*64})
    t=j.claim('diagnostic',resume=True)
    assert j.read()['phases']['diagnostic']['updates']==8
    with pytest.raises(ValueError,match='monotonic'):j.record('diagnostic',t,0,0.)


@pytest.mark.parametrize('phase,step,cursor',[
    ('diagnostic',8,{'epoch':2,'next_microbatch':0}),
    ('diagnostic',7,{'epoch':1,'next_microbatch':24}),
    ('r2b',60,{'epoch':2,'next_microbatch':0}),
])
def test_resume_cursor_counts_partial_last_window_without_repeating_data(phase,step,cursor):
    b.validate_cursor(phase,step,cursor)


@pytest.mark.parametrize('step,cursor',[
    (8,{'epoch':0,'next_microbatch':0}),
    (3,{'epoch':0,'next_microbatch':25}),
    (40,{'epoch':11,'next_microbatch':0}),
])
def test_resume_cursor_cannot_reset_epochs_or_invent_optimizer_steps(step,cursor):
    with pytest.raises(ValueError,match='cursor'):b.validate_cursor('diagnostic',step,cursor)


def test_park_reload_resume_final_reload_preserves_each_checkpoint_receipt(tmp_path):
    receipts=b.ReloadReceipts(tmp_path)
    parked={'checkpoint_name':'checkpoint-0008-01','sha256':{'model.safetensors':'a'*64,'training-state.pt':'b'*64}}
    final={'checkpoint_name':'checkpoint-0040-01','sha256':{'model.safetensors':'c'*64,'training-state.pt':'d'*64}}
    mid={'model_sha256':'a'*64,'gate':{'passed':False}}
    end={'model_sha256':'c'*64,'gate':{'passed':False}}
    assert receipts.read(parked) is None
    receipts.write(parked,mid)
    before=receipts.path(parked).read_bytes()
    assert receipts.read(final) is None
    receipts.write(final,end)
    assert receipts.read(final)['model_sha256']=='c'*64
    assert receipts.path(parked).read_bytes()==before
    assert receipts.read(parked)['model_sha256']=='a'*64
    receipts.write(final,end)  # An exact repeat is idempotent, not another reload.
    with pytest.raises(ValueError,match='identity'):
        receipts.write(final,mid)


def test_legacy_reload_receipt_is_archived_not_mistaken_for_new_checkpoint(tmp_path):
    legacy=tmp_path/'RELOAD_RESULT.json';legacy.write_text('{"model_sha256":"old"}')
    before=legacy.read_bytes();receipts=b.ReloadReceipts(tmp_path)
    saved={'checkpoint_name':'checkpoint-0040-01','sha256':{'model.safetensors':'c'*64}}
    assert receipts.read(saved) is None
    receipts.write(saved,{'model_sha256':'c'*64})
    assert any(p.read_bytes()==before for p in (tmp_path/'reloads').glob('legacy-*.json'))
