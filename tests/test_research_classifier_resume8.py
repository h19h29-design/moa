"""A new human permission continues the saved eight, never resetting history."""
import hashlib
import importlib
import pytest

from moa import research_campaign as rc, research_classifier_resume as zero
from moa import r2_research as research
from test_research_classifier_resume import fixture as zero_fixture, AT


def api(): return importlib.import_module('moa.research_classifier_resume8')


def fixture(tmp_path, monkeypatch):
    p, ledger, original, _, _ = zero_fixture(tmp_path, monkeypatch)
    zero.authorize(tmp_path, at=AT); zero.advance(tmp_path, at=AT)
    prior = p / zero.FOLDER
    prior_state = rc._read_json(prior / 'STATE.json')
    rc._write_atomic(prior / 'STATE.json', dict(prior_state, status='RECONCILIATION_REQUIRED',
        training_updates=8, trained=False, evaluated=False))
    stopped = dict(original, updates=8, seconds=41.35238887462765,
        cumulative_train_seconds=51.05715908296406, resumed_exact=True,
        authorization_sha256=prior_state['authorization_sha256'], records=[],
        checkpoint_name='checkpoint-0008-final_or_safe_stop')
    rc._write_atomic(prior / 'TRAIN_STOP_RESULT.json', stopped)
    calls=[]
    snap=dict(resources=dict(available_memory_bytes=8*2**30), gateways_stopped=True,
        gateways_disabled=True, busy=False, running=False, train_admission=True, evaluation_admission=True)
    def remote(action, plan, auth=None, **kw):
        calls.append(action)
        if action == 'probe': return dict(stop=stopped, classifier_sha256='d'*64,
            stop_sha256=hashlib.sha256(research.canonical(stopped)).hexdigest(),
            prior_book_sha256={'authorization':'f'*64,'dispatch':'e'*64})
        if action == 'stage': return dict(status='STAGED')
        if action == 'selftest': return dict(returncode=0, tests=5)
        if action == 'snapshot': return dict(snap)
        if action in ('launch_resume','launch_evaluate'):
            assert (p / api().FOLDER / ('DISPATCH.json' if action=='launch_resume' else 'EVAL_DISPATCH.json')).is_file()
            return dict(pid=43)
        raise AssertionError(action)
    monkeypatch.setattr(api(), '_remote', remote)
    return p, prior, ledger, stopped, calls, snap


def test_authorized_eight_update_resume_preserves_both_histories_and_budget(tmp_path, monkeypatch):
    p, prior, ledger, stopped, _, _ = fixture(tmp_path, monkeypatch)
    before={path:path.read_bytes() for path in list(p.glob('*.json'))+list(prior.glob('*.json'))}
    budget=ledger.path.read_bytes()
    auth=api().authorize(tmp_path, at=AT)
    assert auth['prior_updates']==8 and auth['maximum_resumes']==1
    assert auth['prior_train_seconds']==51.05715908296406
    assert auth['remaining_train_seconds']==1748.942840917036
    assert auth['additional_candidates']==auth['additional_train_seconds']==0
    assert auth['checkpoint_name']=='checkpoint-0008-final_or_safe_stop'
    assert all(path.read_bytes()==raw for path,raw in before.items())
    assert ledger.path.read_bytes()==budget


@pytest.mark.parametrize('fault',['progress','cumulative_time','unknown_stop','prior_dispatch','next_day'])
def test_unknown_or_altered_prior_boundary_is_not_authorized(tmp_path, monkeypatch, fault):
    p, prior, _, stopped, calls, _ = fixture(tmp_path, monkeypatch)
    at=AT
    if fault=='progress': stopped['updates']=9
    if fault=='cumulative_time': stopped['cumulative_train_seconds']=50.
    if fault=='unknown_stop': stopped['stop_reason']='unknown'
    if fault=='prior_dispatch': (prior/'DISPATCH.json').unlink()
    if fault=='next_day': at='2026-10-23T00:00:01+09:00'
    rc._write_atomic(prior/'TRAIN_STOP_RESULT.json', stopped)
    with pytest.raises((ValueError,FileNotFoundError)): api().authorize(tmp_path, at=at)
    assert 'launch_resume' not in calls


def test_memory_or_automatic_restart_protection_failure_does_not_consume(tmp_path, monkeypatch):
    p, _, _, _, calls, snap=fixture(tmp_path, monkeypatch)
    api().authorize(tmp_path, at=AT)
    snap['gateways_disabled']=False
    assert api().advance(tmp_path, at=AT)['status']=='WAITING_BOOK_RESOURCE'
    assert not (p/api().FOLDER/'DISPATCH.json').exists()
    snap['gateways_disabled']=True; snap['resources']['available_memory_bytes']=int(6.9*2**30)
    assert api().advance(tmp_path, at=AT)['status']=='WAITING_BOOK_RESOURCE'
    assert 'launch_resume' not in calls


def test_single_continuation_new_stop_never_reissues_or_changes_budget(tmp_path, monkeypatch):
    p, _, ledger, _, calls, snap=fixture(tmp_path, monkeypatch)
    api().authorize(tmp_path, at=AT); before=ledger.path.read_bytes()
    assert api().advance(tmp_path, at=AT)['status']=='RESUME_ISSUED'
    snap['train_result']=dict(status='stopped',updates=10)
    out=api().advance(tmp_path, at=AT)
    assert out['status']=='RECONCILIATION_REQUIRED'
    assert api().advance(tmp_path, at=AT)==out
    assert calls.count('launch_resume')==1 and ledger.path.read_bytes()==before
    with pytest.raises(ValueError): api().authorize(tmp_path,at=AT)


def test_complete_continuation_has_one_separate_reload_evaluation(tmp_path, monkeypatch):
    p,_,_,_,calls,snap=fixture(tmp_path,monkeypatch)
    auth=api().authorize(tmp_path,at=AT); api().advance(tmp_path,at=AT)
    key=rc._read_json(p/api().FOLDER/'STATE.json')['authorization_sha256']
    snap['train_result']=dict(status='saved',updates=56,seconds=150.,
        cumulative_train_seconds=201.05715908296406,resumed_exact=True,encoder_unchanged=True,
        job_sha256='a'*64,authorization_sha256=key,execution_host='macbookair',
        execution_device='mps',head_sha256='e'*64)
    assert api().advance(tmp_path,at=AT)['status']=='EVALUATION_ISSUED'
    rc._write_atomic(p/'GOLD.json',{'n':'preserved','e':'changed'})
    rc._write_atomic(tmp_path/'baseline.json',dict(models={'r1':{'average':{'accuracy':.5}}}))
    records=[dict(case_id='n',prediction='preserved',probabilities=dict(preserved=.9,changed=.1),system_forced=False),
        dict(case_id='e',prediction='changed',probabilities=dict(preserved=.2,changed=.8),system_forced=False)]
    snap['evaluation']=dict(status='complete',reload_exact=True,seconds=5.,head_sha256='e'*64,
        authorization_sha256=key,execution_device='mps',system_forced=False,records=records)
    out=api().advance(tmp_path,at=AT)
    assert out['status']=='COMPLETE'
    assert out['comparison']['models']['fixed_classifier']['accuracy']==1.
    assert out['comparison']['models']['fixed_classifier']['normal_false_positive_rate']==0.
    assert calls.count('launch_resume')==calls.count('launch_evaluate')==1


def test_uncertain_dispatch_is_terminal_and_cannot_reconsume_permission(tmp_path, monkeypatch):
    p,_,ledger,_,_,_=fixture(tmp_path,monkeypatch)
    api().authorize(tmp_path,at=AT); before=ledger.path.read_bytes(); remote=api()._remote
    def uncertain(action,*args,**kw):
        if action=='launch_resume': raise TimeoutError('unknown launch outcome')
        return remote(action,*args,**kw)
    monkeypatch.setattr(api(),'_remote',uncertain)
    out=api().advance(tmp_path,at=AT)
    assert out['status']=='RECONCILIATION_REQUIRED'
    assert (p/api().FOLDER/'DISPATCH.json').is_file()
    assert api().advance(tmp_path,at=AT)==out and ledger.path.read_bytes()==before


def test_changed_eight_update_receipt_after_authorization_never_launches(tmp_path,monkeypatch):
    p,prior,_,stopped,calls,_=fixture(tmp_path,monkeypatch)
    api().authorize(tmp_path,at=AT)
    rc._write_atomic(prior/'TRAIN_STOP_RESULT.json',dict(stopped,head_sha256='f'*64))
    assert api().advance(tmp_path,at=AT)['status']=='RECONCILIATION_REQUIRED'
    assert 'launch_resume' not in calls and not (p/api().FOLDER/'DISPATCH.json').exists()
