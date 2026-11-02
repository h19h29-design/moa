"""Same candidate/permission/budget; a host move is not another experiment."""
import importlib
import importlib.util
import json
import io
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from moa import research_alignment as a, research_campaign as rc
from test_research_binary_control import stopped_zero_boundary


def api():
    assert importlib.util.find_spec('moa.research_book_resume'), 'MacBook-only resume controller required'
    return importlib.import_module('moa.research_book_resume')


def prepared(root, monkeypatch, *, different_boundary=False):
    b = api()
    m, plan, launches, saved = stopped_zero_boundary(root, monkeypatch)
    # Populate the actual job seam, not a synthetic dispatch response.
    path = root / m.FOLDER / 'stage/JOB.json'
    job = dict(run_id=m.RUN, configuration=plan['configuration'], execution_device='cpu',
               scope='unattended_synthetic_research', protected_models={}, parent_files={},
               installed_sha256={}, runner_sha256='r', parent='candidates/r1/checkpoint')
    rc._write_atomic(path, job)
    plan['stage_sha256']['JOB.json'] = a.sha(path)
    rc._write_atomic(root / m.FOLDER / 'PLAN.json', plan)
    state = rc._read_json(root / m.FOLDER / 'STATE.json')
    state['plan_sha256'] = a.sha(root / m.FOLDER / 'PLAN.json')
    preflight = rc._read_json(root / m.FOLDER / 'INPUT_PREFLIGHT.json')
    preflight['job_sha256'] = a.sha(path)
    rc._write_atomic(root / m.FOLDER / 'INPUT_PREFLIGHT.json', preflight)
    state['input_preflight_sha256'] = a.sha(root / m.FOLDER / 'INPUT_PREFLIGHT.json')
    rc._write_atomic(root / m.FOLDER / 'STATE.json', state)
    saved['job_sha256'] = a.sha(path)
    saved['checkpoint_name'] = 'checkpoint-0056'
    contract_path = root / 'mini-worker/research' / m.RUN / 'TRAINING_CONTRACT.json'
    contract = rc._read_json(contract_path)
    contract['job_sha256'] = a.sha(path)
    rc._write_atomic(contract_path, contract)
    saved['training_contract_sha256'] = a.sha(contract_path)
    rc._write_atomic(Path(saved['checkpoint']) / 'HASHES.json', saved['sha256'])
    rc._write_atomic(root / 'mini-worker/research' / m.RUN / 'TRAIN_RESULT.json', saved)
    m.grant_resource_resume(root, at='2026-10-21T09:00:00+09:00')
    rc.Campaign(root).pause()
    before = {n: (root / n).read_bytes() for n in (
        'BUDGET.jsonl', m.FOLDER + '/PLAN.json', m.FOLDER + '/RESOURCE_RESUME_AUTHORIZATION.json')}
    if different_boundary:
        checkpoint=Path(saved['checkpoint']).with_name('different-valid-zero-boundary')
        checkpoint.mkdir()
        (checkpoint/'training-state.pt').write_bytes(b'different but valid zero-update state')
        saved.update(checkpoint=str(checkpoint),checkpoint_name=checkpoint.name,
                     sha256={'training-state.pt':a.sha(checkpoint/'training-state.pt')})
        rc._write_atomic(checkpoint/'HASHES.json',saved['sha256'])
        rc._write_atomic(root/'mini-worker/research'/m.RUN/'TRAIN_RESULT.json',saved)
        m._training_result(root,plan,saved)  # Still valid; it is not the authorised boundary.
    b.prepare(root, at='2026-10-21T11:00:00+09:00')
    return b, m, launches, before


def remote_ready(monkeypatch, b, *, memory=8, allowed=True):
    state = dict(online=True, ready=True, busy=False, running=False, result=None,
                 resources=dict(train_allowed=allowed, verify_allowed=True,
                                available_memory_bytes=int(memory * 2**30)))
    monkeypatch.setattr(b, '_snapshot', lambda root, plan: state)
    return state


def test_prepare_moves_only_execution_not_candidate_grant_or_budget(tmp_path, monkeypatch):
    b, m, launches, before = prepared(tmp_path, monkeypatch)
    assert all((tmp_path / n).read_bytes() == raw for n, raw in before.items())
    assert (tmp_path / 'PAUSED').exists()
    plan = rc._read_json(b.folder(tmp_path) / 'PLAN.json')
    assert plan['execution_host'] == 'macbookair' and plan['execution_device'] == 'mps'
    job = rc._read_json(b.folder(tmp_path) / 'stage/incoming' / m.RUN / 'JOB-r1.json')
    old = rc._read_json(tmp_path / m.FOLDER / 'stage/JOB.json')
    assert job['configuration'] == old['configuration']
    assert job['previous_job'] == {'filename': 'JOB.json', 'sha256': a.sha(tmp_path / m.FOLDER / 'stage/JOB.json')}
    assert plan['additional_candidates'] == plan['additional_train_seconds'] == 0
    assert launches == [('train', False)]  # Only the historical fixture dispatch.
    assert b.prepare(tmp_path, at='2026-10-21T11:00:01+09:00') == plan


def test_prepare_refuses_different_valid_zero_boundary_from_existing_grant(tmp_path, monkeypatch):
    with pytest.raises(ValueError):prepared(tmp_path,monkeypatch,different_boundary=True)


@pytest.mark.parametrize('memory,allowed', [(6.99, True), (8, False)])
def test_book_resource_wait_never_consumes_permission_or_launches_mini(tmp_path, monkeypatch, memory, allowed):
    b, m, launches, before = prepared(tmp_path, monkeypatch)
    remote_ready(monkeypatch, b, memory=memory, allowed=allowed)
    rc.Campaign(tmp_path).resume()
    assert m.advance(tmp_path, at='2026-10-21T11:01:00+09:00')['status'] == 'WAITING_BOOK_RESOURCE'
    assert not (tmp_path / m.FOLDER / 'RESOURCE_RESUME_DISPATCH.json').exists()
    assert all((tmp_path / n).read_bytes() == raw for n, raw in before.items())
    assert launches == [('train', False)]


def test_same_permission_dispatches_on_book_once_and_old_state_restore_cannot_repeat(tmp_path, monkeypatch):
    b, m, mini, before = prepared(tmp_path, monkeypatch)
    snap = remote_ready(monkeypatch, b)
    issued = []
    monkeypatch.setattr(b, '_launch', lambda root, plan, action: issued.append(action) or 4321)
    rc.Campaign(tmp_path).resume()
    old_state = (tmp_path / m.FOLDER / 'STATE.json').read_bytes()
    out = m.advance(tmp_path, at='2026-10-21T11:01:00+09:00')
    assert out['status'] == 'TRAINING_ON_BOOK' and issued == ['train']
    assert rc._read_json(tmp_path / m.FOLDER / 'RESOURCE_RESUME_DISPATCH.json')['resume'] is True
    assert (tmp_path / 'BUDGET.jsonl').read_bytes() == before['BUDGET.jsonl']
    snap['running'] = True
    assert m.advance(tmp_path, at='2026-10-21T11:01:01+09:00')['status'] == 'OBSERVE_BOOK'
    (tmp_path / m.FOLDER / 'STATE.json').write_bytes(old_state)
    assert m.advance(tmp_path, at='2026-10-21T11:02:00+09:00')['status'] == 'RECONCILIATION_REQUIRED'
    assert issued == ['train'] and mini == [('train', False)]


@pytest.mark.parametrize('condition', ['next_day', 'changed_checkpoint', 'changed_budget', 'launch_failure'])
def test_migration_failure_never_resets_budget_or_retries(tmp_path, monkeypatch, condition):
    b, m, mini, before = prepared(tmp_path, monkeypatch)
    remote_ready(monkeypatch, b)
    issued = []
    def launch(*args):
        issued.append('book')
        if condition == 'launch_failure':
            raise ConnectionError('controlled SSH failure')
        return 4321
    monkeypatch.setattr(b, '_launch', launch)
    rc.Campaign(tmp_path).resume()
    if condition == 'changed_checkpoint':
        p = next((tmp_path / 'mini-worker/research' / m.RUN).glob('checkpoint-*/training-state.pt'))
        p.write_bytes(b'corrupt')
    if condition == 'changed_budget':
        (tmp_path / 'BUDGET.jsonl').write_bytes(b'')
    when = '2026-10-22T11:01:00+09:00' if condition == 'next_day' else '2026-10-21T11:01:00+09:00'
    out = m.advance(tmp_path, at=when)
    assert out['status'] == 'RECONCILIATION_REQUIRED'
    assert m.advance(tmp_path, at=when)['status'] == 'RECONCILIATION_REQUIRED'
    assert issued == (['book'] if condition == 'launch_failure' else [])
    assert mini == [('train', False)]


def test_unknown_or_second_resource_stop_is_not_a_new_resume(tmp_path, monkeypatch):
    b, m, mini, before = prepared(tmp_path, monkeypatch)
    snap = remote_ready(monkeypatch, b)
    issued = []
    monkeypatch.setattr(b, '_launch', lambda root, plan, action: issued.append(action) or 4321)
    rc.Campaign(tmp_path).resume()
    m.advance(tmp_path, at='2026-10-21T11:01:00+09:00')
    snap['result'] = dict(stop_reason='resource_guard', updates=1)
    assert m.advance(tmp_path, at='2026-10-21T11:07:00+09:00')['status'] == 'RECONCILIATION_REQUIRED'
    assert issued == ['train'] and mini == [('train', False)]


def test_collection_preserves_exact_hashed_contract_bytes(tmp_path, monkeypatch):
    b=api();raw=b'{\n  "synthetic": true\n}\n';archive=io.BytesIO()
    with tarfile.open(fileobj=archive,mode='w') as tar:
        item=tarfile.TarInfo('research/moa-unattended-20261021-binary-train-01/TRAINING_CONTRACT.json')
        item.size=len(raw);tar.addfile(item,io.BytesIO(raw))
    monkeypatch.setattr(b,'_transport',lambda *args,**kw:archive.getvalue())
    run=b._collect(tmp_path,{})
    assert (run/'TRAINING_CONTRACT.json').read_bytes()==raw


@pytest.mark.parametrize('code,error', [(255,ConnectionError),(1,ValueError)])
def test_ssh_offline_and_remote_identity_refusal_are_not_conflated(monkeypatch,code,error):
    b=api()
    monkeypatch.setattr(b.subprocess,'run',lambda *args,**kw:SimpleNamespace(returncode=code,stdout=b'',stderr=b'private'))
    with pytest.raises(error):b._transport({},'snapshot')


def test_execution_job_ancestry_accepts_only_device_change(tmp_path, monkeypatch):
    b,m,_,_=prepared(tmp_path,monkeypatch)
    spec=importlib.util.spec_from_file_location('book_ancestor_worker',a.ROOT/'scripts/moa-research-worker.py')
    worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
    incoming=b.folder(tmp_path)/'stage/incoming'/m.RUN
    job=rc._read_json(incoming/'JOB-r1.json')
    assert worker.execution_ancestor_hashes(job,incoming)=={a.sha(incoming/'JOB.json')}
    job['configuration']=dict(job['configuration'],head_lr=.5)
    with pytest.raises(ValueError):worker.execution_ancestor_hashes(job,incoming)


def test_book_evaluation_uses_exact_existing_contiguous_two_order_sequence(tmp_path, monkeypatch):
    b,m,_,_=prepared(tmp_path,monkeypatch)
    spec=importlib.util.spec_from_file_location('book_binary_evaluator',a.ROOT/b.EVALUATOR)
    evaluator=importlib.util.module_from_spec(spec);spec.loader.exec_module(evaluator)
    prepare=getattr(evaluator,'evaluation_inputs',None)
    assert callable(prepare), 'gold-free Book order must be testable without loading a model'
    request=rc._read_json(tmp_path/m.FOLDER/'BINARY_REQUEST.json')
    expected=m.evaluation_inputs(request,[])
    assert len(request)>1
    assert prepare(request,[])==expected
    assert prepare(request,expected[:3])==expected[3:]
    with pytest.raises(ValueError):prepare(request,list(reversed(expected[:3])))


def completed_book(root,b,m):
    plan=rc._read_json(b.folder(root)/'PLAN.json');old=rc._read_json(root/m.FOLDER/'PLAN.json')
    run=root/'book-results';cp=run/'checkpoint-r1-0056';cp.mkdir(parents=True)
    (cp/'model.safetensors').write_bytes(b'new binary weights')
    (cp/'training-state.pt').write_bytes(b'continued optimizer state')
    contract=dict(plan={'configuration':old['configuration']},option_labels=['preserved','changed'],
        semantic_profile='moa.binary-training.v1',label_counts=old['train_counts'],
        job_sha256=plan['remote_job_sha256'],data_sha256=old['data_sha256'])
    rc._write_atomic(run/'TRAINING_CONTRACT.json',contract)
    saved=dict(run_id=m.RUN,checkpoint_name=cp.name,updates=56,seconds=100,
        status='saved',training_complete=True,stop_reason='epoch_limit',
        resume_from_updates=0,resume_from_sha256=plan['checkpoint_sha256'],
        job_sha256=plan['remote_job_sha256'],data_sha256=old['data_sha256'],
        training_contract_sha256=a.sha(run/'TRAINING_CONTRACT.json'),
        sha256={n:a.sha(cp/n) for n in ('model.safetensors','training-state.pt')})
    rc._write_atomic(run/'TRAIN_RESULT.json',saved)
    return run,saved


@pytest.mark.parametrize('changed', ['checkpoint_path', 'profile', 'label_counts', 'missing_weights'])
def test_collected_candidate_must_retain_confined_binary_contract(tmp_path, monkeypatch, changed):
    b,m,_,_=prepared(tmp_path,monkeypatch)
    run,saved=completed_book(tmp_path,b,m)
    plan=rc._read_json(b.folder(tmp_path)/'PLAN.json')
    if changed=='checkpoint_path':
        saved['checkpoint_name']='../book-results/checkpoint-r1-0056'
    elif changed=='missing_weights':
        saved['sha256'].pop('model.safetensors')
    else:
        path=run/'TRAINING_CONTRACT.json';contract=rc._read_json(path)
        contract['semantic_profile' if changed=='profile' else 'label_counts']='changed'
        rc._write_atomic(path,contract);saved['training_contract_sha256']=a.sha(path)
    rc._write_atomic(run/'TRAIN_RESULT.json',saved)
    with pytest.raises(ValueError):b._complete(tmp_path,plan,run)


def test_book_checkpoint_is_reloaded_and_compared_without_mini_inference(tmp_path,monkeypatch):
    b,m,mini,before=prepared(tmp_path,monkeypatch);snap=remote_ready(monkeypatch,b);issued=[]
    monkeypatch.setattr(b,'_launch',lambda root,plan,action:issued.append(action) or 4321)
    rc.Campaign(tmp_path).resume();m.advance(tmp_path,at='2026-10-21T11:01:00+09:00')
    run,saved=completed_book(tmp_path,b,m);snap['result']=saved
    monkeypatch.setattr(b,'_collect',lambda *args:run)
    assert m.advance(tmp_path,at='2026-10-21T11:03:00+09:00')['status']=='EVALUATING_ON_BOOK'
    request=rc._read_json(tmp_path/m.FOLDER/'BINARY_REQUEST.json')
    records=[dict(case_id=r['case_id'],mode=r['mode'],model='binary_candidate',split='dev',
        family=r['family'],operation=r['operation'],prediction='changed',
        probabilities={'preserved':.2,'changed':.8},system_forced=False)
        for r in m.evaluation_inputs(request,[])]
    rc._write_atomic(run/'EVALUATION.json',dict(status='complete',records=records,seconds=6,
        separate_process=True,request_sha256=a.sha(tmp_path/m.FOLDER/'BINARY_REQUEST.json'),
        model_sha256=saved['sha256']['model.safetensors']))
    assert m.advance(tmp_path,at='2026-10-21T11:04:00+09:00')['status']=='COMPLETE'
    result=rc._read_json(tmp_path/m.FOLDER/'COMPARISON.json')
    assert result['models']['binary_candidate']['average']['items']==2
    assert result['rule_answers_counted_as_model_success'] is False
    assert result['execution_host']=='macbookair' and result['host_migration'] is True
    assert issued==['train','evaluate'] and mini==[('train',False)]
    assert (tmp_path/'BUDGET.jsonl').read_bytes()==before['BUDGET.jsonl']
