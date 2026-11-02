"""One explicit scheduler-only replay must wait, charge once and never rearm."""
import copy
import importlib
import importlib.util
import json

import pytest

from moa import r2_research as research, research_campaign as rc


def api():
    assert importlib.util.find_spec('moa.research_lr_control'), 'budget-aware scheduler replay required'
    return importlib.import_module('moa.research_lr_control')


def policy():
    return dict(rolling_budget_days=7,max_train_time_rolling_seconds=28800,
                max_train_time_per_calendar_day_seconds=10800,
                max_candidate_runs_per_rolling_window=8,
                max_train_time_per_candidate_seconds=2700,
                max_optimizer_updates_per_candidate=400,max_eval_time_per_candidate_seconds=600)


def configuration():
    cfg={k:v for k,v in research.phase_config('r2a').items() if k not in ('parent','questions')}
    return dict(cfg,epochs=4,maximum_updates=120,planned_updates=120,
                maximum_seconds=1800,seed=20261020,head_lr=.001)


def test_single_variable_control_changes_only_the_scheduler_not_initial_lr_or_data():
    original=configuration(); before=copy.deepcopy(original)
    fixed=api().controlled_configuration(original,144)
    assert fixed['planned_updates']==72
    assert {k for k in original if original[k]!=fixed[k]}=={'planned_updates'}
    assert original==before
    with pytest.raises(ValueError): api().controlled_configuration(fixed,144)


def test_expired_reservations_restore_capacity_without_resetting_or_editing_history(tmp_path):
    ledger=rc.BudgetLedger(tmp_path/'BUDGET.jsonl')
    for i in range(8):
        ledger.append({'at':'2026-10-20T13:00:00+09:00','candidate':'old-'+str(i),'train_seconds':100})
    before=ledger.path.read_bytes()
    wait=api().budget_readiness(ledger,policy(),'2026-10-20T21:00:00+09:00',1800)
    assert wait['ready'] is False
    assert wait['next_eligible_at']=='2026-10-27T13:00:01+09:00'
    assert api().budget_readiness(ledger,policy(),'2026-10-27T13:00:01+09:00',1800)['ready'] is True
    assert ledger.path.read_bytes()==before


def test_time_limits_still_block_an_available_candidate_slot(tmp_path):
    ledger=rc.BudgetLedger(tmp_path/'BUDGET.jsonl')
    ledger.append({'at':'2026-10-20T10:00:00+09:00','candidate':'one','train_seconds':10000})
    assert not api().budget_readiness(ledger,policy(),'2026-10-20T21:00:00+09:00',1800)['ready']


def test_comparison_counts_the_same_cases_and_does_not_hide_false_alerts():
    def row(identity,gold,p):
        return dict(case_id=identity,expected=gold,prediction='changed' if p>.5 else 'preserved',
                    probabilities={'changed':p,'preserved':1-p},system_forced=False)
    old=[row('normal','preserved',.8),row('error','changed',.8)]
    fixed=[row('normal','preserved',.2),row('error','changed',.8)]
    result=api().comparison({'r1':old,'old_candidate':old},
                            {'r1':old,'old_candidate':old},fixed,fixed,
                            train_counts={'preserved':1,'changed':2})
    assert result['models']['new_candidate']['average']['accuracy']==1
    assert result['delta_vs_old']['accuracy']==.5
    assert result['delta_vs_old']['normal_false_positive_rate']==-1
    assert result['operational_promotion'] is False
    assert result['new_blind'] is False


def test_comparison_refuses_a_different_evaluation_denominator():
    with pytest.raises(ValueError):
        api().comparison({}, {}, [], [],train_counts={'preserved':1,'changed':2})


def test_evaluation_preserves_both_real_option_orders_and_rejects_gold_or_skipped_progress():
    from moa.research_binary import binary_questions
    request=[dict(case_id='one',state={'title':'same'},questions=binary_questions(['conditions']),
                  operation='identity',family='one')]
    pending=api().evaluation_inputs(request,[])
    assert [x['mode'] for x in pending]==['binary','binary_reverse']
    assert [list(x['questions']['conditions']['criteria']) for x in pending]==[
        ['preserved','changed'],['changed','preserved']]
    assert pending[0]['state']==pending[1]['state']=={'title':'same'}
    completed=[{'case_id':'one','mode':'binary'}]
    assert len(api().evaluation_inputs(request,completed))==1
    with pytest.raises(ValueError):
        api().evaluation_inputs(request,[{'case_id':'one','mode':'binary_reverse'}])
    with pytest.raises(ValueError):
        api().evaluation_inputs([dict(request[0],expected='preserved')],[])


def test_existing_entrypoint_routes_explicit_control_without_rearming_old_experiments(tmp_path,monkeypatch):
    from moa import research_advance as existing
    rc._write_atomic(tmp_path/'STATE.json',{'status':'RESEARCH_PLATEAU','alignment_enabled':True})
    rc._write_atomic(tmp_path/'lr-control/PLAN.json',{'one_candidate_only':True})
    monkeypatch.setattr(api(),'advance',lambda root:{'status':'WAITING_BUDGET','run_id':'controlled'})
    assert existing.advance(tmp_path)=={'status':'WAITING_BUDGET','run_id':'controlled'}


def test_status_is_read_only_and_exposes_the_pending_control(tmp_path):
    from moa.research_cli import summarize_root
    rc._write_atomic(tmp_path/'STATE.json',{'status':'RESEARCH_PLATEAU','policy_snapshot':policy()})
    rc._write_atomic(tmp_path/'lr-control/STATE.json',{'status':'WAITING_BUDGET','training_issued':False})
    assert summarize_root(tmp_path)['lr_control']['training_issued'] is False
    assert not (tmp_path/'BUDGET.jsonl').exists()


def fixture(root):
    from moa.worker_handoff import questions
    from moa.research_binary import binary_questions
    from moa.research_alignment import sha
    m=api(); w=root/'mini-worker'; old=w/'incoming'/m.BASELINE
    state={'title':'합성','file_role':'notice','source_complete':True,
           'source_blocks':[{'id':'s','text':'신청은 1개'}],
           'rendered_blocks':[{'id':'h','source_refs':['s'],'visible_text':'신청은 1개'}],
           'attachment_manifest':[]}
    q={'conditions':questions()['conditions']}
    train=[{'case_id':'train-'+str(i),'state':state,'questions':q,
            'expected':{'conditions':['preserved','changed','changed','insufficient'][i%4]},
            'meta':{'split':'train','synthetic_gold':True,'evaluation_only':False,
                    'rights_status':'allowed'}} for i in range(144)]
    old.mkdir(parents=True)
    (root/'alignment/data').mkdir(parents=True)
    (root/'alignment/data/paired-train.jsonl').write_bytes(b'\n'.join(research.canonical(r) for r in train)+b'\n')
    projected=[{k:r[k] for k in ('case_id','state','questions','expected')} for r in train]
    (old/'train-000.jsonl').write_bytes(b'\n'.join(research.canonical(r) for r in projected)+b'\n')
    (old/'dev-000.jsonl').write_bytes(b'{}\n')
    parent=w/'candidates/r1';parent.mkdir(parents=True)
    (parent/'model.safetensors').write_bytes(b'original r1')
    hashes={'model.safetensors':sha(parent/'model.safetensors')}
    for name in ('MODEL_REGISTRY.json','MODEL_MANIFEST.json'):
        rc._write_atomic(w/name,{'protected':True})
    protected={n:sha(w/n) for n in ('MODEL_REGISTRY.json','MODEL_MANIFEST.json')}
    job=dict(run_id=m.BASELINE,configuration=configuration(),parent='candidates/r1',
        parent_files=hashes,bundle_sha256={n:sha(old/n) for n in ('train-000.jsonl','dev-000.jsonl')},
        execution_device='cpu',protected_models=protected,installed_sha256={},
        questions=questions(),resource_policy={'minimum_memory_gib':5},
        scope='unattended_synthetic_research',reference_models={'r1':{'path':'candidates/r1','sha256':hashes}})
    rc._write_atomic(root/'alignment/MANIFEST.json',{'scope':'previously authorised synthetic source'})
    job['manifest_sha256']=sha(root/'alignment/MANIFEST.json')
    rc._write_atomic(old/'JOB-r4.json',job)
    cp=w/'research'/m.BASELINE/'checkpoint-0072';cp.mkdir(parents=True)
    (cp/'model.safetensors').write_bytes(b'old rejected candidate')
    result={'checkpoint':str(cp),'sha256':{'model.safetensors':sha(cp/'model.safetensors')},
            'job_sha256':sha(old/'JOB-r4.json'),'data_sha256':'d'*64,'updates':72}
    rc._write_atomic(cp.parent/'TRAIN_RESULT.json',result)
    rc._write_atomic(root/'binary/FOLLOWUP_AUDIT.json',{'job_sha256':result['job_sha256'],
        'checkpoint_sha256':result['sha256']['model.safetensors']})
    dev=[dict(case_id=k,state=state,questions=binary_questions(['conditions']),
              expected={'conditions':g},meta={'operation':op,'family':'f','split':'dev'})
         for k,g,op in [('normal','preserved','identity'),('error','changed','relation')]]
    original_dev=[dict(r,questions=q) for r in dev]
    (root/'alignment/data/paired-dev.jsonl').write_bytes(b'\n'.join(research.canonical(r) for r in original_dev)+b'\n')
    (old/'dev-000.jsonl').write_bytes(b'\n'.join(research.canonical({k:r[k] for k in ('case_id','state','questions')})
                                               for r in original_dev)+b'\n')
    job['bundle_sha256']['dev-000.jsonl']=sha(old/'dev-000.jsonl')
    rc._write_atomic(old/'JOB-r4.json',job)
    result['job_sha256']=sha(old/'JOB-r4.json')
    rc._write_atomic(cp.parent/'TRAIN_RESULT.json',result)
    rc._write_atomic(root/'binary/FOLLOWUP_AUDIT.json',{'job_sha256':result['job_sha256'],
        'checkpoint_sha256':result['sha256']['model.safetensors']})
    (root/'binary/dev.jsonl').write_bytes(b'\n'.join(research.canonical(r) for r in dev)+b'\n')
    records=[]
    for model in ('r1','candidate'):
        for row in dev:
            records.append(dict(case_id=row['case_id'],expected=next(iter(row['expected'].values())),
                probabilities={'preserved':.2,'changed':.8},prediction='changed',model=model,
                mode='binary',split='dev',operation=row['meta']['operation'],family='f',system_forced=False))
    models={'r1':{'checkpoint_sha256':hashes['model.safetensors']},
            'candidate':{'checkpoint_sha256':result['sha256']['model.safetensors']}}
    rc._write_atomic(root/'binary/PROBE.json',{'status':'complete','models':models,'records':records})
    rc._write_atomic(root/'binary/PROBE_REVERSE.json',{'status':'complete','models':models,
        'records':[dict(r,mode='binary_reverse') for r in records]})
    rc._write_atomic(root/'STATE.json',{'status':'RESEARCH_PLATEAU','policy_snapshot':policy()})
    rc._write_atomic(root/'alignment/STATE.json',{'execution_host':'mini','status':'COMPLETE_REJECTED'})
    return m.arm(root)


def test_arm_is_idempotent_but_never_charges_or_dispatches_before_capacity(tmp_path):
    m=api(); state=fixture(tmp_path)
    assert state['status']=='WAITING_BUDGET'
    original=(tmp_path/'lr-control/PLAN.json').read_bytes()
    assert m.arm(tmp_path)==state
    assert (tmp_path/'lr-control/PLAN.json').read_bytes()==original
    assert not (tmp_path/'BUDGET.jsonl').exists()
    assert not (tmp_path/'mini-worker/incoming'/m.RUN).exists()


def test_future_tick_reserves_and_dispatches_only_one_candidate(tmp_path,monkeypatch):
    m=api();fixture(tmp_path)
    ledger=rc.BudgetLedger(tmp_path/'BUDGET.jsonl')
    for i in range(8):ledger.append({'at':'2026-10-20T13:00:00+09:00',
        'candidate':'old-'+str(i),'train_seconds':100})
    launches=[]
    monkeypatch.setattr(m,'_resources',lambda root:{'train_allowed':True,'available_memory_bytes':6*2**30})
    monkeypatch.setattr(m,'_busy',lambda root:False)
    monkeypatch.setattr(m,'_running',lambda root:bool(launches))
    monkeypatch.setattr(m,'_launch',lambda root,action,resume=False:launches.append((action,resume)) or 1234)
    assert m.advance(tmp_path,at='2026-10-20T21:00:00+09:00')['status']=='WAITING_BUDGET'
    assert launches==[] and len(ledger.entries())==8
    assert m.advance(tmp_path,at='2026-10-27T14:00:00+09:00')['status']=='TRAINING'
    assert launches==[('train',False)]
    assert ledger.entries()[-1]['candidate']==m.RUN and ledger.entries()[-1]['train_seconds']==1800
    assert m.advance(tmp_path,at='2026-10-27T14:01:00+09:00')['status']=='OBSERVE_ONLY'
    assert len(ledger.entries())==9 and len(launches)==1


def test_unknown_stop_is_not_a_reason_to_train_again(tmp_path,monkeypatch):
    m=api();fixture(tmp_path)
    state=rc._read_json(tmp_path/'lr-control/STATE.json')
    state.update(training_issued=True,dispatched_at='2026-10-20T13:00:00+09:00',status='TRAINING')
    rc._write_atomic(tmp_path/'lr-control/STATE.json',state)
    monkeypatch.setattr(m,'_busy',lambda root:False)
    monkeypatch.setattr(m,'_running',lambda root:False)
    outcome=m.advance(tmp_path,at='2026-10-20T21:00:00+09:00')
    assert outcome['status']=='RECONCILIATION_REQUIRED'
    assert outcome['reason']=='issued job has no known saved boundary'
    assert not (tmp_path/'BUDGET.jsonl').exists()


def test_changed_snapshot_refuses_execution_instead_of_silently_upgrading(tmp_path):
    m=api();fixture(tmp_path)
    stage=tmp_path/'lr-control/stage/train-000.jsonl'
    stage.write_bytes(b'changed')
    assert m.advance(tmp_path,at='2026-10-27T14:00:00+09:00')['status']=='RECONCILIATION_REQUIRED'
    assert not (tmp_path/'BUDGET.jsonl').exists()


def test_orphan_reservation_is_reconciled_without_being_recharged(tmp_path,monkeypatch):
    m=api();fixture(tmp_path)
    ledger=rc.BudgetLedger(tmp_path/'BUDGET.jsonl')
    ledger.reserve(now='2026-10-27T13:00:01+09:00',policy=policy(),candidate=m.RUN,train_seconds=1800)
    before=ledger.path.read_bytes()
    monkeypatch.setattr(m,'_busy',lambda root:False)
    monkeypatch.setattr(m,'_running',lambda root:False)
    monkeypatch.setattr(m,'_resources',lambda root:{'train_allowed':True,'available_memory_bytes':6*2**30})
    result=m.advance(tmp_path,at='2026-10-27T14:00:00+09:00')
    assert result['status']=='RECONCILIATION_REQUIRED'
    assert result['reason']=='reservation exists without a recorded dispatch'
    assert ledger.path.read_bytes()==before


def test_low_resources_do_not_consume_the_newly_available_budget(tmp_path,monkeypatch):
    m=api();fixture(tmp_path)
    monkeypatch.setattr(m,'_busy',lambda root:False)
    monkeypatch.setattr(m,'_running',lambda root:False)
    monkeypatch.setattr(m,'_resources',lambda root:{'train_allowed':True,'available_memory_bytes':4*2**30})
    assert m.advance(tmp_path,at='2026-10-27T14:00:00+09:00')['status']=='WAITING_RESOURCE'
    assert not (tmp_path/'BUDGET.jsonl').exists()


@pytest.mark.parametrize('when,expected',[
    ('2026-10-27T14:30:00+09:00','TRAINING'),
    ('2026-10-28T14:00:00+09:00','RECONCILIATION_REQUIRED'),
])
def test_resume_keeps_the_original_daily_reservation_without_moving_or_recharging_it(
        tmp_path,monkeypatch,when,expected):
    m=api();fixture(tmp_path);launches=[]
    ledger=rc.BudgetLedger(tmp_path/'BUDGET.jsonl')
    ledger.reserve(now='2026-10-27T14:00:00+09:00',policy=policy(),candidate=m.RUN,train_seconds=1800)
    before=ledger.path.read_bytes()
    state=rc._read_json(tmp_path/'lr-control/STATE.json')
    state.update(training_issued=True,training_reserved_at='2026-10-27T14:00:00+09:00',
                 dispatched_at='2026-10-27T14:00:00+09:00',status='WAITING_RESOURCE')
    rc._write_atomic(tmp_path/'lr-control/STATE.json',state)
    rc._write_atomic(tmp_path/'mini-worker/research'/m.RUN/'TRAIN_RESULT.json',
                     {'stop_reason':'resource_guard','sha256':{'training-state.pt':'a'*64}})
    monkeypatch.setattr(m,'_training_result',lambda *args:None)
    monkeypatch.setattr(m,'_busy',lambda root:False)
    monkeypatch.setattr(m,'_running',lambda root:False)
    monkeypatch.setattr(m,'_resources',lambda root:{'train_allowed':True,'available_memory_bytes':6*2**30})
    monkeypatch.setattr(m,'_launch',lambda root,action,resume=False:launches.append((action,resume)) or 1234)
    assert m.advance(tmp_path,at=when)['status']==expected
    assert launches==([('train',True)] if expected=='TRAINING' else [])
    assert ledger.path.read_bytes()==before


def test_training_receipt_requires_the_actual_executed_configuration_contract(tmp_path):
    from moa.research_alignment import sha
    m=api();fixture(tmp_path)
    plan=rc._read_json(tmp_path/'lr-control/PLAN.json')
    cp=tmp_path/'mini-worker/research'/m.RUN/'checkpoint-0072';cp.mkdir(parents=True)
    (cp/'model.safetensors').write_bytes(b'synthetic checkpoint')
    trained=dict(run_id=m.RUN,checkpoint=str(cp),job_sha256=plan['stage_sha256']['JOB.json'],
                 data_sha256='d'*64,seconds=100,sha256={'model.safetensors':sha(cp/'model.safetensors')})
    with pytest.raises(ValueError,match='contract'):
        m._training_result(tmp_path,plan,trained)


def test_complete_control_is_compared_and_never_relaunched(tmp_path,monkeypatch):
    from moa.research_alignment import sha
    m=api();fixture(tmp_path);launches=[]
    monkeypatch.setattr(m,'_busy',lambda root:False)
    monkeypatch.setattr(m,'_running',lambda root:False)
    monkeypatch.setattr(m,'_resources',lambda root:{'train_allowed':True,'verify_allowed':True,
                                                   'available_memory_bytes':6*2**30})
    monkeypatch.setattr(m,'_launch',lambda root,action,resume=False:launches.append(action) or 1234)
    assert m.advance(tmp_path,at='2026-10-27T14:00:00+09:00')['status']=='TRAINING'
    plan=rc._read_json(tmp_path/'lr-control/PLAN.json')
    cp=tmp_path/'mini-worker/research'/m.RUN/'checkpoint-0072';cp.mkdir(parents=True)
    (cp/'model.safetensors').write_bytes(b'synthetic test checkpoint, not actual learning')
    trained=dict(run_id=m.RUN,checkpoint=str(cp),job_sha256=plan['stage_sha256']['JOB.json'],
        data_sha256='d'*64,seconds=100,updates=72,status='saved',training_complete=True,
        stop_reason='epoch_limit',sha256={'model.safetensors':sha(cp/'model.safetensors')})
    rc._write_atomic(cp.parent/'TRAINING_CONTRACT.json',dict(job_sha256=trained['job_sha256'],
        data_sha256='d'*64,plan={'configuration':plan['configuration']}))
    trained['training_contract_sha256']=sha(cp.parent/'TRAINING_CONTRACT.json')
    rc._write_atomic(cp.parent/'TRAIN_RESULT.json',trained)
    assert m.advance(tmp_path,at='2026-10-27T14:02:00+09:00')['status']=='EVALUATING'
    raw=[]
    for identity,p,op in [('normal',.2,'identity'),('error',.8,'relation')]:
        for mode in ('binary','binary_reverse'):
            raw.append(dict(case_id=identity,mode=mode,model='new_candidate',split='dev',operation=op,
                family='f',probabilities={'preserved':1-p,'changed':p},
                prediction='changed' if p>.5 else 'preserved',system_forced=False))
    rc._write_atomic(tmp_path/'lr-control/EVALUATION.json',dict(status='complete',records=raw,seconds=10,
        model_sha256=trained['sha256']['model.safetensors'],request_sha256=plan['input_sha256']['BINARY_REQUEST.json']))
    assert m.advance(tmp_path,at='2026-10-27T14:03:00+09:00')['status']=='COMPLETE'
    result=rc._read_json(tmp_path/'lr-control/COMPARISON.json')
    assert result['models']['new_candidate']['average']['accuracy']==1
    assert (tmp_path/'lr-control/RESULT.md').exists()
    assert m.advance(tmp_path,at='2026-11-10T14:03:00+09:00')['status']=='COMPLETE'
    assert launches==['train','evaluate']
    assert len(rc.BudgetLedger(tmp_path/'BUDGET.jsonl').entries())==1


def grant_extra(root):
    m=api()
    assert hasattr(m,'grant_candidate_slot'), 'explicit single-run candidate allowance required'
    return m.grant_candidate_slot(root)


def test_one_extra_candidate_is_scoped_and_preserves_all_other_limits(tmp_path,monkeypatch):
    m=api();fixture(tmp_path);launches=[]
    ledger=rc.BudgetLedger(tmp_path/'BUDGET.jsonl')
    for i in range(8):
        ledger.append({'at':'2026-10-20T13:00:00+09:00','candidate':'old-'+str(i),'train_seconds':100})
    history=ledger.path.read_bytes();original=(tmp_path/'STATE.json').read_bytes()
    monkeypatch.setattr(m,'_busy',lambda root:False)
    monkeypatch.setattr(m,'_running',lambda root:bool(launches))
    monkeypatch.setattr(m,'_resources',lambda root:{'train_allowed':True,'available_memory_bytes':6*2**30})
    monkeypatch.setattr(m,'_launch',lambda root,action,resume=False:launches.append((action,resume)) or 1234)
    assert m.advance(tmp_path,at='2026-10-20T21:00:00+09:00')['status']=='WAITING_BUDGET'
    grant=grant_extra(tmp_path)
    assert grant['run_id']==m.RUN
    assert ledger.path.read_bytes()==history
    assert (tmp_path/'STATE.json').read_bytes()==original
    assert grant_extra(tmp_path)==grant
    plan=rc._read_json(tmp_path/'lr-control/PLAN.json')
    scoped=m.execution_policy(tmp_path,plan)
    assert {k for k in scoped if scoped[k]!=policy()[k]}=={'max_candidate_runs_per_rolling_window'}
    assert scoped['max_candidate_runs_per_rolling_window']==9
    assert m.advance(tmp_path,at='2026-10-20T21:01:00+09:00')['status']=='TRAINING'
    assert ledger.path.read_bytes().startswith(history)
    assert ledger.entries()[-1]['train_seconds']==1800 and len(ledger.entries())==9
    assert rc.BudgetLedger(ledger.path).usage(now='2026-10-20T21:01:00+09:00',policy=policy())['remaining_candidates']==0
    assert m.advance(tmp_path,at='2026-10-20T21:02:00+09:00')['status']=='OBSERVE_ONLY'
    assert launches==[('train',False)] and len(ledger.entries())==9
    with pytest.raises(ValueError): grant_extra(tmp_path)
    rc._write_atomic(tmp_path/'mini-worker/research'/m.RUN/'TRAIN_RESULT.json',
                     {'stop_reason':'resource_guard','sha256':{'training-state.pt':'b'*64}})
    monkeypatch.setattr(m,'_training_result',lambda *args:None)
    monkeypatch.setattr(m,'_running',lambda root:False)
    assert m.advance(tmp_path,at='2026-10-20T21:03:00+09:00')['status']=='TRAINING'
    assert launches==[('train',False),('train',True)] and len(ledger.entries())==9


@pytest.mark.parametrize('cause,expected',[
    ('time','WAITING_BUDGET'),('memory','WAITING_RESOURCE'),('active_user','WAITING_RESOURCE'),
])
def test_extra_slot_never_relaxes_time_memory_or_idle_guards(tmp_path,monkeypatch,cause,expected):
    m=api();fixture(tmp_path)
    ledger=rc.BudgetLedger(tmp_path/'BUDGET.jsonl')
    for i in range(8):
        ledger.append({'at':'2026-10-20T13:00:00+09:00','candidate':'old-'+str(i),
                       'train_seconds':1250 if cause=='time' else 100})
    before=ledger.path.read_bytes();grant_extra(tmp_path)
    monkeypatch.setattr(m,'_busy',lambda root:False)
    monkeypatch.setattr(m,'_running',lambda root:False)
    monkeypatch.setattr(m,'_resources',lambda root:{'train_allowed':cause!='active_user',
        'available_memory_bytes':4*2**30 if cause=='memory' else 6*2**30})
    def forbidden(*args,**kwargs): pytest.fail('unsafe training launch')
    monkeypatch.setattr(m,'_launch',forbidden)
    outcome=m.advance(tmp_path,at='2026-10-20T21:00:00+09:00')
    assert outcome['status']==expected
    assert outcome['budget']['ready'] is (cause!='time')
    assert ledger.path.read_bytes()==before


@pytest.mark.parametrize('mutation',['other_run','changed_plan','extra_two','more_time'])
def test_extra_slot_refuses_another_job_or_any_broader_permission(tmp_path,mutation):
    m=api();fixture(tmp_path);grant_extra(tmp_path)
    path=tmp_path/'lr-control/CANDIDATE_ALLOWANCE.json'
    grant=rc._read_json(path)
    if mutation=='other_run': grant['run_id']='another-job'
    elif mutation=='changed_plan': grant['plan_sha256']='0'*64
    elif mutation=='extra_two': grant['additional_candidates']=2
    else: grant['maximum_seconds']=2700
    rc._write_atomic(path,grant)
    plan=rc._read_json(tmp_path/'lr-control/PLAN.json')
    with pytest.raises(ValueError): m.execution_policy(tmp_path,plan)
