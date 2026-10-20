"""A binary candidate requires its own explicit allowance beyond standard capacity."""
import copy
import hashlib
import importlib
import importlib.util
import json

import pytest

from moa import research_alignment as a, research_campaign as rc, r2_research as research


def api():
    assert importlib.util.find_spec('moa.research_binary_control'), 'bounded binary controller required'
    return importlib.import_module('moa.research_binary_control')


def fixture(root):
    m=api();folder=root/m.FOLDER
    cfg=a.paired_configuration(108)
    policy=dict(rolling_budget_days=7,max_train_time_rolling_seconds=28800,
        max_train_time_per_calendar_day_seconds=10800,max_candidate_runs_per_rolling_window=8,
        max_train_time_per_candidate_seconds=2700,max_optimizer_updates_per_candidate=400,
        max_eval_time_per_candidate_seconds=600)
    rc._write_atomic(root/'STATE.json',{'status':'RESEARCH_PLATEAU','policy_snapshot':policy})
    rc._write_atomic(root/'alignment/STATE.json',{'execution_host':'mini'})
    for name in ('JOB.json','moa-research-worker.py','r2_research.py','train-000.jsonl','dev-000.jsonl'):
        rc._write_atomic(folder/'stage'/name,{'synthetic_test_only':name})
    rc._write_atomic(folder/'GOLD.json',{'normal':'preserved','error':'changed'})
    from moa.research_binary import binary_questions
    request=[dict(case_id=k,state={'title':'synthetic'},questions=binary_questions(['conditions']),
        family='f',operation=op) for k,op in [('normal','identity'),('error','relation')]]
    records=[dict(case_id=k,expected=g,prediction='changed',probabilities={'changed':.8,'preserved':.2},
        model='frozen',split='dev',family='f',operation=op,system_forced=False)
        for k,g,op in [('normal','preserved','identity'),('error','changed','relation')]]
    rc._write_atomic(folder/'BINARY_REQUEST.json',request)
    rc._write_atomic(folder/'BASELINES.json',[
        {k:records for k in ('r1','paired','lr_control')} for _ in range(2)])
    rc._write_atomic(folder/'DATA_AUDIT.json',{'dev':{'rule_excluded':18}})
    plan=dict(run_id=m.RUN,configuration=cfg,policy_snapshot=policy,data_sha256='d'*64,
        execution_host='mini',execution_device='cpu',evaluation_seconds=600,
        stage_sha256={p.name:a.sha(p) for p in (folder/'stage').iterdir()},
        input_sha256={n:a.sha(folder/n) for n in ('GOLD.json','BINARY_REQUEST.json','BASELINES.json','DATA_AUDIT.json')},
        implementation_sha256={},lineage_sha256={},train_counts={'preserved':36,'changed':72},
        budget_prefix={'bytes':0,'sha256':hashlib.sha256(b'').hexdigest()},
        one_candidate_only=True,semantic_profile='moa.binary-training.v1')
    rc._write_atomic(folder/'PLAN.json',plan)
    preflight=dict(status='ready',job_sha256=plan['stage_sha256']['JOB.json'],
        data_sha256=plan['data_sha256'],optimizer_updates=0,model_loaded=False,
        option_labels=['preserved','changed'],skipped=0,truncated=0)
    rc._write_atomic(folder/'INPUT_PREFLIGHT.json',preflight)
    rc._write_atomic(folder/'STATE.json',dict(status='WAITING_BUDGET',run_id=m.RUN,
        training_issued=False,plan_sha256=a.sha(folder/'PLAN.json'),
        input_preflight_sha256=a.sha(folder/'INPUT_PREFLIGHT.json')))
    return plan


def runtime(monkeypatch,m,launches):
    monkeypatch.setattr(m,'_running',lambda root:False)
    monkeypatch.setattr(m,'_busy',lambda root:False)
    monkeypatch.setattr(m,'_resources',lambda root:{'train_allowed':True,
        'verify_allowed':True,'available_memory_bytes':6*2**30})
    monkeypatch.setattr(m,'_launch',lambda root,action,resume=False:launches.append((action,resume)) or 1234)


def test_ninth_slot_consumption_is_not_reused_by_binary_candidate(tmp_path,monkeypatch):
    m=api();fixture(tmp_path);launches=[];runtime(monkeypatch,m,launches)
    ledger=rc.BudgetLedger(tmp_path/'BUDGET.jsonl')
    for i in range(9):ledger.append({'at':'2026-10-20T13:00:00+09:00',
        'candidate':'used-'+str(i),'train_seconds':100})
    rc._write_atomic(tmp_path/'lr-control/CANDIDATE_ALLOWANCE.json',{'additional_candidates':1})
    before=ledger.path.read_bytes()
    out=m.advance(tmp_path,at='2026-10-21T01:00:00+09:00')
    assert out['status']=='WAITING_BUDGET' and not out['training_issued']
    assert out['budget']['next_eligible_at']=='2026-10-27T13:00:01+09:00'
    assert ledger.path.read_bytes()==before and not launches
    assert not (tmp_path/'mini-worker/incoming'/m.RUN).exists()


def test_first_available_tick_charges_once_and_does_not_duplicate_training(tmp_path,monkeypatch):
    m=api();fixture(tmp_path);launches=[];runtime(monkeypatch,m,launches)
    assert m.advance(tmp_path,at='2026-10-28T12:00:00+09:00')['status']=='TRAINING'
    ledger=rc.BudgetLedger(tmp_path/'BUDGET.jsonl')
    assert len(ledger.entries())==1 and ledger.entries()[0]['train_seconds']==1800
    assert m.advance(tmp_path,at='2026-10-28T12:00:01+09:00')['status']=='DISPATCH_SETTLING'
    assert launches==[('train',False)] and len(ledger.entries())==1
    assert m.advance(tmp_path,at='2026-10-28T12:06:00+09:00')['status']=='RECONCILIATION_REQUIRED'
    assert len(ledger.entries())==1 and len(launches)==1


@pytest.mark.parametrize('block',['memory','daily_time','preflight','changed_plan'])
def test_other_safety_limits_still_prevent_a_free_slot_from_launching(block,tmp_path,monkeypatch):
    m=api();fixture(tmp_path);launches=[];runtime(monkeypatch,m,launches)
    if block=='memory':monkeypatch.setattr(m,'_resources',lambda root:{
        'train_allowed':True,'available_memory_bytes':4*2**30})
    if block=='daily_time':rc.BudgetLedger(tmp_path/'BUDGET.jsonl').append({
        'at':'2026-10-28T10:00:00+09:00','candidate':'other','train_seconds':10000})
    if block=='preflight':(tmp_path/m.FOLDER/'INPUT_PREFLIGHT.json').unlink()
    if block=='changed_plan':rc._write_atomic(tmp_path/m.FOLDER/'PLAN.json',{'modified':True})
    out=m.advance(tmp_path,at='2026-10-28T12:00:00+09:00')
    assert out['status'] in ('WAITING_RESOURCE','WAITING_BUDGET','RECONCILIATION_REQUIRED')
    assert not launches
    assert not any(e.get('candidate')==m.RUN for e in rc.BudgetLedger(tmp_path/'BUDGET.jsonl').entries())


def test_new_binary_plan_precedes_old_completed_lr_plan_and_status_is_read_only(tmp_path,monkeypatch):
    from moa import research_advance as existing, research_cli as cli
    m=api();fixture(tmp_path)
    rc._write_atomic(tmp_path/'lr-control/PLAN.json',{'completed_old_plan':True})
    monkeypatch.setattr(m,'advance',lambda root:{'status':'WAITING_BUDGET','run_id':m.RUN})
    assert existing.advance(tmp_path)['run_id']==m.RUN
    assert cli.summarize_root(tmp_path)['binary_training_control']['training_issued'] is False
    assert not (tmp_path/'BUDGET.jsonl').exists()


def test_binary_comparison_preserves_three_baselines_and_does_not_count_rule_answers():
    m=api()
    def answer(identity,gold,p):
        return dict(case_id=identity,expected=gold,prediction='changed' if p>.5 else 'preserved',
            probabilities={'preserved':1-p,'changed':p},system_forced=False)
    old=[answer('normal','preserved',.8),answer('error','changed',.8)]
    new=[answer('normal','preserved',.2),answer('error','changed',.8)]
    frozen=[{k:copy.deepcopy(old) for k in ('r1','paired','lr_control')} for _ in range(2)]
    result=m.comparison(frozen,new,new,train_counts={'preserved':36,'changed':72},
        rule_excluded_dev=18)
    assert set(result['models'])=={'r1','paired','lr_control','binary_candidate'}
    assert result['models']['binary_candidate']['average']['normal_false_positive_rate']==0
    assert result['models']['binary_candidate']['average']['items']==2
    assert result['rule_excluded_dev']==18 and result['new_blind'] is False
    assert result['operational_promotion'] is False
    bad=copy.deepcopy(frozen);bad[0]['r1'][0]['system_forced']=True
    with pytest.raises(ValueError):m.comparison(bad,new,new,
        train_counts={'preserved':36,'changed':72},rule_excluded_dev=18)


def test_terminal_candidate_is_never_rearmed_or_replaced(tmp_path,monkeypatch):
    m=api();fixture(tmp_path);launches=[];runtime(monkeypatch,m,launches)
    path=tmp_path/m.FOLDER/'STATE.json';state=json.loads(path.read_bytes())
    state.update(status='COMPLETE',checkpoint_sha256='a'*64);rc._write_atomic(path,state)
    assert m.advance(tmp_path,at='2026-10-28T12:00:00+09:00')==state
    assert not launches and not (tmp_path/'BUDGET.jsonl').exists()


def test_deleting_or_rewriting_existing_budget_history_is_rejected(tmp_path,monkeypatch):
    m=api();plan=fixture(tmp_path);launches=[];runtime(monkeypatch,m,launches)
    ledger=rc.BudgetLedger(tmp_path/'BUDGET.jsonl')
    ledger.append({'at':'2026-10-20T13:00:00+09:00','candidate':'protected-old','train_seconds':100})
    raw=ledger.path.read_bytes()
    plan['budget_prefix']={'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
    folder=tmp_path/m.FOLDER;rc._write_atomic(folder/'PLAN.json',plan)
    state=rc._read_json(folder/'STATE.json');state['plan_sha256']=a.sha(folder/'PLAN.json')
    rc._write_atomic(folder/'STATE.json',state)
    ledger.path.write_bytes(b'')  # Deliberately corrupt the isolated test fixture only.
    assert m.advance(tmp_path,at='2026-10-28T12:00:00+09:00')['status']=='RECONCILIATION_REQUIRED'
    assert not launches


def saved_training(root,plan,*,reason=None):
    m=api();run=root/'mini-worker/research'/m.RUN;cp=run/'checkpoint-0056'
    cp.mkdir(parents=True)
    (cp/'model.safetensors').write_bytes(b'test binary model')
    (cp/'training-state.pt').write_bytes(b'test optimizer boundary')
    contract=dict(job_sha256=plan['stage_sha256']['JOB.json'],data_sha256=plan['data_sha256'],
        plan={'configuration':plan['configuration']},semantic_profile='moa.binary-training.v1',
        option_labels=['preserved','changed'],label_counts=plan['train_counts'])
    rc._write_atomic(run/'TRAINING_CONTRACT.json',contract)
    result=dict(run_id=m.RUN,checkpoint=str(cp),seconds=50,updates=40 if reason else 56,
        stop_reason=reason,status='saved',training_complete=not reason,
        job_sha256=contract['job_sha256'],data_sha256=plan['data_sha256'],
        training_contract_sha256=a.sha(run/'TRAINING_CONTRACT.json'),
        sha256={n:a.sha(cp/n) for n in ('model.safetensors','training-state.pt')})
    rc._write_atomic(run/'TRAIN_RESULT.json',result)
    return result


def test_saved_weights_without_a_real_dispatch_and_reservation_cannot_be_evaluated(tmp_path,monkeypatch):
    m=api();plan=fixture(tmp_path);launches=[];runtime(monkeypatch,m,launches)
    saved_training(tmp_path,plan)
    assert m.advance(tmp_path,at='2026-10-28T12:00:00+09:00')['status']=='RECONCILIATION_REQUIRED'
    assert not launches and not (tmp_path/m.FOLDER/'EVAL_RESERVATION.json').exists()


@pytest.mark.parametrize('late',[False,True])
def test_known_resource_boundary_resumes_only_inside_original_daily_reservation(late,tmp_path,monkeypatch):
    m=api();plan=fixture(tmp_path);launches=[];runtime(monkeypatch,m,launches)
    m.advance(tmp_path,at='2026-10-28T12:00:00+09:00')
    saved_training(tmp_path,plan,reason='resource_guard')
    result=m.advance(tmp_path,at='2026-10-29T12:01:00+09:00' if late else '2026-10-28T12:01:00+09:00')
    assert result['status']==('RECONCILIATION_REQUIRED' if late else 'TRAINING')
    assert launches==([('train',False)] if late else [('train',False),('train',True)])
    assert len(rc.BudgetLedger(tmp_path/'BUDGET.jsonl').entries())==1


def test_saved_binary_candidate_reloads_then_compares_once_and_stops(tmp_path,monkeypatch):
    m=api();plan=fixture(tmp_path);launches=[];runtime(monkeypatch,m,launches)
    m.advance(tmp_path,at='2026-10-28T12:00:00+09:00');trained=saved_training(tmp_path,plan)
    assert m.advance(tmp_path,at='2026-10-28T12:02:00+09:00')['status']=='EVALUATING'
    request=rc._read_json(tmp_path/m.FOLDER/'BINARY_REQUEST.json')
    records=[dict(case_id=r['case_id'],mode=r['mode'],model='binary_candidate',split='dev',
        family=r['family'],operation=r['operation'],prediction='changed',
        probabilities={'preserved':.2,'changed':.8},system_forced=False)
        for r in m.evaluation_inputs(request,[])]
    rc._write_atomic(tmp_path/m.FOLDER/'EVALUATION.json',dict(status='complete',records=records,
        model_sha256=trained['sha256']['model.safetensors'],separate_process=True,seconds=5,
        request_sha256=plan['input_sha256']['BINARY_REQUEST.json']))
    out=m.advance(tmp_path,at='2026-10-28T12:03:00+09:00')
    assert out['status']=='COMPLETE'
    comparison=rc._read_json(tmp_path/m.FOLDER/'COMPARISON.json')
    assert comparison['separate_reload'] and comparison['rule_excluded_dev']==18
    assert set(comparison['models'])=={'r1','paired','lr_control','binary_candidate'}
    assert m.advance(tmp_path,at='2026-11-01T12:00:00+09:00')==out
    assert launches==[('train',False),('evaluate',False)]


@pytest.mark.parametrize('seconds',[-1,601])
def test_invalid_accumulated_evaluation_time_cannot_produce_a_success(seconds,tmp_path,monkeypatch):
    m=api();plan=fixture(tmp_path);launches=[];runtime(monkeypatch,m,launches)
    m.advance(tmp_path,at='2026-10-28T12:00:00+09:00');trained=saved_training(tmp_path,plan)
    rc._write_atomic(tmp_path/m.FOLDER/'EVALUATION.json',dict(status='waiting_resources',records=[],
        model_sha256=trained['sha256']['model.safetensors'],seconds=seconds,
        request_sha256=plan['input_sha256']['BINARY_REQUEST.json']))
    assert m.advance(tmp_path,at='2026-10-28T12:03:00+09:00')['status']=='RECONCILIATION_REQUIRED'
    assert launches==[('train',False)]


def previous_consumed_allowance(root):
    """Independent historical ninth-slot receipt; no model or training is run."""
    from moa import research_lr_control as old
    folder=root/'lr-control'
    rc._write_atomic(folder/'PLAN.json',dict(run_id=old.RUN,
        policy_snapshot={'max_candidate_runs_per_rolling_window':8}))
    receipt=dict(version='moa.single-candidate-allowance.v1',run_id=old.RUN,
        plan_sha256=a.sha(folder/'PLAN.json'),base_candidate_limit=8,additional_candidates=1,
        authorised_by='explicit_user_one_extra_candidate_request_2026-10-20')
    rc._write_atomic(folder/'CANDIDATE_ALLOWANCE.json',receipt)
    rc._write_atomic(folder/'STATE.json',dict(status='COMPLETE',
        candidate_allowance_sha256=a.sha(folder/'CANDIDATE_ALLOWANCE.json')))
    ledger=rc.BudgetLedger(root/'BUDGET.jsonl')
    for i in range(9):
        ledger.append(dict(at='2026-10-20T13:00:00+09:00',
            candidate=old.RUN if i==8 else 'used-'+str(i),train_seconds=100))
    return ledger


def grant_binary_slot(root):
    m=api()
    assert hasattr(m,'grant_candidate_slot'), 'explicit binary-only extra candidate permission required'
    return m.grant_candidate_slot(root)


def test_explicit_binary_extra_slot_charges_only_this_candidate_once(tmp_path,monkeypatch):
    m=api();plan=fixture(tmp_path);ledger=previous_consumed_allowance(tmp_path)
    launches=[];runtime(monkeypatch,m,launches)
    history=ledger.path.read_bytes();snapshot=(tmp_path/'STATE.json').read_bytes()
    assert m.advance(tmp_path,at='2026-10-21T08:00:00+09:00')['status']=='WAITING_BUDGET'
    receipt=grant_binary_slot(tmp_path)
    assert receipt['run_id']==m.RUN and receipt['additional_candidates']==1
    assert grant_binary_slot(tmp_path)==receipt
    scoped=m.execution_policy(tmp_path,plan)
    assert {k for k in scoped if scoped[k]!=plan['policy_snapshot'][k]}=={'max_candidate_runs_per_rolling_window'}
    assert scoped['max_candidate_runs_per_rolling_window']==10
    assert ledger.path.read_bytes()==history and (tmp_path/'STATE.json').read_bytes()==snapshot
    assert m.advance(tmp_path,at='2026-10-21T08:01:00+09:00')['status']=='TRAINING'
    assert ledger.path.read_bytes().startswith(history) and len(ledger.entries())==10
    assert ledger.entries()[-1]['candidate']==m.RUN and ledger.entries()[-1]['train_seconds']==1800
    assert launches==[('train',False)]
    with pytest.raises(ValueError):grant_binary_slot(tmp_path)
    saved_training(tmp_path,plan,reason='resource_guard')
    assert m.advance(tmp_path,at='2026-10-21T08:02:00+09:00')['status']=='TRAINING'
    assert launches==[('train',False),('train',True)] and len(ledger.entries())==10


@pytest.mark.parametrize('cause,expected',[
    ('time','WAITING_BUDGET'),('memory','WAITING_RESOURCE'),('active_user','WAITING_RESOURCE')])
def test_binary_extra_slot_preserves_time_memory_and_idle_guards(tmp_path,monkeypatch,cause,expected):
    m=api();fixture(tmp_path);ledger=previous_consumed_allowance(tmp_path)
    if cause=='time':ledger.append(dict(at='2026-10-21T07:00:00+09:00',
        candidate='used-0',train_seconds=10000))
    history=ledger.path.read_bytes();grant_binary_slot(tmp_path)
    launches=[];runtime(monkeypatch,m,launches)
    monkeypatch.setattr(m,'_resources',lambda root:dict(train_allowed=cause!='active_user',
        available_memory_bytes=(4 if cause=='memory' else 6)*2**30))
    out=m.advance(tmp_path,at='2026-10-21T08:00:00+09:00')
    assert out['status']==expected and not launches and ledger.path.read_bytes()==history


@pytest.mark.parametrize('mutation',['other_run','extra_two','more_time','previous_receipt','unbound'])
def test_binary_allowance_rejects_broader_or_unbound_permission(tmp_path,monkeypatch,mutation):
    m=api();fixture(tmp_path);previous_consumed_allowance(tmp_path);grant_binary_slot(tmp_path)
    folder=tmp_path/m.FOLDER;path=folder/'CANDIDATE_ALLOWANCE.json';receipt=rc._read_json(path)
    if mutation=='other_run':receipt['run_id']='another-candidate'
    elif mutation=='extra_two':receipt['additional_candidates']=2
    elif mutation=='more_time':receipt['maximum_seconds']=2700
    elif mutation=='previous_receipt':
        rc._write_atomic(tmp_path/'lr-control/CANDIDATE_ALLOWANCE.json',dict(additional_candidates=9))
    else:
        state=rc._read_json(folder/'STATE.json');state.pop('candidate_allowance_sha256')
        rc._write_atomic(folder/'STATE.json',state)
    if mutation not in ('previous_receipt','unbound'):rc._write_atomic(path,receipt)
    launches=[];runtime(monkeypatch,m,launches)
    assert m.advance(tmp_path,at='2026-10-21T08:00:00+09:00')['status']=='RECONCILIATION_REQUIRED'
    assert not launches
