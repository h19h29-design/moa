"""One evidence-gated, genuinely binary synthetic training candidate.

The old ninth-slot permission belongs to the completed LR experiment, not this
run. A separate explicit operator grant can authorise this candidate once;
the original ledger, time/resource guards and models remain unchanged.
The derivative train/dev is exposed development data, never a new blind test.
"""
from __future__ import annotations

from collections import Counter
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess

from . import research_alignment as a, research_campaign as rc, r2_research as research
from . import research_binary as binary, research_lr_control as previous, research_pair_data as data
from .research_diagnostics import replay_binary

RUN = 'moa-unattended-20261021-binary-train-01'
FOLDER = 'binary-training'
PROFILE = 'moa.binary-training.v1'
TERMINAL = ('COMPLETE', 'RECONCILIATION_REQUIRED')
SOURCES = (*previous.SOURCES, 'moa/research_binary_control.py', 'moa/evidence.py',
    'moa/research_advance.py', 'moa/research_cli.py',
    'moa/research_pair_data.py', 'moa/research_dataset.py', 'moa/r2_research.py',
    'moa/worker_handoff.py', 'scripts/moa-research-worker.py',
    'scripts/moa-research-binary-evaluate.py', 'scripts/moa-research-binary-preflight.py')
evaluation_inputs = previous.evaluation_inputs
_busy = previous._busy
_resources = a.resources


def configuration(old, item_count):
    if old['maximum_updates'] > 120 or old['maximum_seconds'] > 1800:
        raise ValueError('retain the stricter existing candidate limits')
    return research.training_plan(old, item_count)['configuration']


def _read(path):
    return rc._read_json(path)


def _rows(path):
    return previous._rows(path)


def _source_data(root):
    """Rebuild gold from authored facts/edits and verify stored source artifacts.

    Evidence exclusion happens before semantic labels are inspected. No model,
    checker output, real notice or sealed evaluation shard supplies any gold.
    """
    manifest = _read(root/'binary/MANIFEST.json')
    if (a.sha(root/'alignment/AUTHOR.json') != manifest['acquisition_author_sha256'] or
            a.sha(root/'alignment/MANIFEST.json') != manifest['source_manifest_sha256']):
        raise ValueError('independent synthetic acquisition lineage changed')
    docs = _read(root/'alignment/AUTHOR.json')
    rebuilt, _, _ = data.build(docs, _read(root/'alignment/DOM.json'))
    acquired = {d['case_id']:d['source_sections'] for d in docs}
    outputs = {}; audit = {}; pins = {}
    for split in ('train','dev'):
        original = _rows(root/'alignment/data'/('paired-'+split+'.jsonl'))
        if original != [r for r in rebuilt if r['meta']['split'] == split]:
            raise ValueError('stored labels/input differ from independent authored operation replay')
        inventories = _read(root/'binary'/(split+'-inventories.json'))
        for row in original:
            identity = row['case_id']; provenance = inventories[identity]
            name = 'binary/sources/'+identity+'.json'
            if (a.sha(root/name) != provenance['file']['sha256'] or
                    _read(root/name) != acquired[identity] or
                    provenance['render_revision'] != row['meta']['dom_sha256']):
                raise ValueError('actual acquired source/DOM does not match evidence inventory')
            pins[name] = a.sha(root/name)
        part = binary.prepare_split(original, inventories, split=split)
        outputs[split] = part['rows']
        if outputs[split] != _rows(root/'binary'/(split+'.jsonl')):
            raise ValueError('prepared binary derivative changed')
        audit[split] = {'rows':len(part['rows']), 'rule_excluded':len(part['excluded_ids']),
            'excluded_ids':part['excluded_ids'],
            'label_counts':dict(Counter(next(iter(r['expected'].values())) for r in part['rows'])),
            'families':len({r['meta']['family_group_id'] for r in part['rows']})}
    for key in ('family_group_id','original_group_id','source_sha256'):
        if ({r['meta'][key] for r in outputs['train']} &
                {r['meta'][key] for r in outputs['dev']}):
            raise ValueError('train/dev source or family leakage')
    audit.update(independent_new_groups=0, new_blind=False,
        scope='existing_permitted_synthetic_train_dev_derivative',
        evidence_authority='stored_source_acquisition_inventory_not_model_or_gold',
        operational_promotion=False)
    for name in ('alignment/AUTHOR.json','alignment/DOM.json','alignment/MANIFEST.json',
                 'binary/MANIFEST.json','binary/train-inventories.json','binary/dev-inventories.json',
                 'alignment/data/paired-train.jsonl','alignment/data/paired-dev.jsonl',
                 'binary/train.jsonl','binary/dev.jsonl'):
        pins[name] = a.sha(root/name)
    return outputs, audit, pins


def comparison(frozen, first, second, *, train_counts, rule_excluded_dev):
    if len(frozen) != 2 or any(set(p) != {'r1','paired','lr_control'} for p in frozen):
        raise ValueError('all three frozen baselines and both actual orders required')
    cases = {r['case_id']:r['expected'] for r in first}
    for rows in [first, second, *frozen[0].values(), *frozen[1].values()]:
        if len(rows) != len(cases) or {r['case_id']:r['expected'] for r in rows} != cases:
            raise ValueError('same cases and independent gold required across every model/order')
    models = {k:replay_binary(frozen[0][k],frozen[1][k],train_counts=train_counts)
              for k in frozen[0]}
    models['binary_candidate'] = replay_binary(first,second,train_counts=train_counts)
    return {'scope':'exposed_synthetic_development_not_blind_or_real_document_accuracy',
        'models':models, 'rule_excluded_dev':rule_excluded_dev,
        'rule_answers_counted_as_model_success':False, 'new_blind':False,
        'operational_promotion':False, 'human_approved':False}


def arm(root=a.DEFAULT):
    """Prepare exactly one explicit binary experiment without consuming a slot."""
    root = Path(root); folder = root/FOLDER; w = root/'mini-worker'
    folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (folder/'.controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if (folder/'PLAN.json').exists():
            state = _read(folder/'STATE.json'); verify(root,_read(folder/'PLAN.json'),state)
            return state
        if (w/'incoming'/RUN).exists() or (w/'research'/RUN).exists():
            raise ValueError('existing run cannot be reset or rearmed')
        old_folder = root/'lr-control'
        old_plan = _read(old_folder/'PLAN.json'); old_state = _read(old_folder/'STATE.json')
        previous.verify(root,old_plan,old_state)
        if old_state['status'] != 'COMPLETE':
            raise ValueError('finish/reconcile the previous candidate first')
        saved = _read(w/'research'/previous.RUN/'TRAIN_RESULT.json')
        previous._training_result(root,old_plan,saved)
        if a.sha(old_folder/'COMPARISON.json') != old_state['comparison_sha256']:
            raise ValueError('previous completed observations changed')
        splits, audit, pins = _source_data(root)
        fixed = configuration(old_plan['configuration'],len(splits['train']))
        stage = folder/'stage'
        for split in ('train','dev'):
            rows = [binary.model_input(r) for r in splits[split]]
            if split == 'train':
                for item,original in zip(rows,splits[split]):item['expected'] = original['expected']
            a.write_once(stage/(split+'-000.jsonl'),b''.join(research.canonical(r)+b'\n' for r in rows))
        a.write_once(folder/'DATA_AUDIT.json',research.canonical(audit))
        a.write_once(stage/'DATA_AUDIT.json',(folder/'DATA_AUDIT.json').read_bytes())
        for name,source in [('moa-research-worker.py','scripts/moa-research-worker.py'),
                            ('r2_research.py','moa/r2_research.py')]:
            a.write_once(stage/name,(a.ROOT/source).read_bytes())
        job = _read(old_folder/'stage/JOB.json')
        if job.get('train_sampling') not in (None,'none'):
            raise ValueError('do not change sampling along with the binary hypothesis')
        job.update(run_id=RUN, bundle='incoming/'+RUN, configuration=fixed, semantic_profile=PROFILE,
            questions=binary.binary_questions(list(job['questions'])),
            bundle_sha256={n:a.sha(stage/n) for n in ('train-000.jsonl','dev-000.jsonl','DATA_AUDIT.json')},
            runner_sha256=a.sha(stage/'moa-research-worker.py'),library='r2_research.py',
            library_sha256=a.sha(stage/'r2_research.py'))
        for name,digest in job['protected_models'].items():
            if a.sha(w/name) != digest:raise ValueError('protected model metadata changed')
        a.write_once(stage/'JOB.json',research.canonical(job))
        dev = splits['dev']
        request = [dict(binary.model_input(r),operation=r['meta']['operation'],family=r['meta']['family']) for r in dev]
        if request != _read(old_folder/'BINARY_REQUEST.json'):
            raise ValueError('binary evaluation must retain the identical baseline inputs')
        evaluation_inputs(request,[])
        gold = {r['case_id']:next(iter(r['expected'].values())) for r in dev}
        evaluated = _read(old_folder/'EVALUATION.json')
        if (evaluated['status'] != 'complete' or evaluated['model_sha256'] != saved['sha256']['model.safetensors'] or
                evaluated['request_sha256'] != a.sha(old_folder/'BINARY_REQUEST.json')):
            raise ValueError('frozen LR baseline identity mismatch')
        frozen = _read(old_folder/'BASELINES.json')
        baselines = []
        for part,mode in zip(frozen,('binary','binary_reverse')):
            baselines.append({'r1':part['r1'],'paired':part['old_candidate'],
                'lr_control':[dict(r,expected=gold[r['case_id']]) for r in evaluated['records'] if r['mode']==mode]})
        # Validate denominator and raw observations before any training is possible.
        comparison(baselines,baselines[0]['lr_control'],baselines[1]['lr_control'],
            train_counts=audit['train']['label_counts'],rule_excluded_dev=audit['dev']['rule_excluded'])
        for name,value in [('BINARY_REQUEST.json',request),('GOLD.json',gold),('BASELINES.json',baselines)]:
            a.write_once(folder/name,research.canonical(value))
        for name in ('STATE.json','mini-worker/MODEL_REGISTRY.json','mini-worker/MODEL_MANIFEST.json',
                     'lr-control/PLAN.json','lr-control/COMPARISON.json','lr-control/EVALUATION.json','lr-control/BASELINES.json'):
            pins[name] = a.sha(root/name)
        ledger_bytes = (root/'BUDGET.jsonl').read_bytes()
        snapshot = old_plan['policy_snapshot']
        if (fixed['maximum_updates'] > snapshot['max_optimizer_updates_per_candidate'] or
                fixed['maximum_seconds'] > snapshot['max_train_time_per_candidate_seconds'] or
                snapshot['max_candidate_runs_per_rolling_window'] != 8):
            raise ValueError('retain the existing standard research limits')
        plan = {'version':PROFILE, 'semantic_profile':PROFILE,'run_id':RUN,
            'authorised_by':'explicit_user_binary_training_request_2026-10-21',
            'one_candidate_only':True,'no_candidate_allowance_extension':True,
            'configuration':fixed,'previous_configuration':old_plan['configuration'],
            'initial_model_sha256':job['parent_files']['model.safetensors'],
            'data_sha256':data.digest(_rows(stage/'train-000.jsonl')),
            'train_counts':audit['train']['label_counts'],
            'stage_sha256':{p.name:a.sha(p) for p in stage.iterdir()},
            'input_sha256':{n:a.sha(folder/n) for n in ('BINARY_REQUEST.json','GOLD.json','BASELINES.json','DATA_AUDIT.json')},
            'implementation_sha256':{n:a.sha(a.ROOT/n) for n in SOURCES},'lineage_sha256':pins,
            'budget_prefix':{'bytes':len(ledger_bytes),'sha256':hashlib.sha256(ledger_bytes).hexdigest()},
            'policy_snapshot':snapshot,'evaluation_seconds':min(600,snapshot['max_eval_time_per_candidate_seconds']),
            'execution_host':'mini','execution_device':'cpu','operational_promotion':False}
        a.write_once(folder/'PLAN.json',research.canonical(plan))
        state = {'status':'WAITING_INPUT_PREFLIGHT','run_id':RUN,'training_issued':False,
            'plan_sha256':a.sha(folder/'PLAN.json'),'operational_promotion':False}
        rc._write_atomic(folder/'STATE.json',state)
        return state


def verify(root, plan, state):
    root = Path(root); folder = root/FOLDER
    if (a.sha(folder/'PLAN.json') != state['plan_sha256'] or plan['run_id'] != RUN or
            plan['semantic_profile'] != PROFILE or plan.get('one_candidate_only') is not True or
            plan['policy_snapshot']['max_candidate_runs_per_rolling_window'] != 8):
        raise ValueError('fixed one-candidate binary plan changed')
    if (_read(root/'STATE.json')['policy_snapshot'] != plan['policy_snapshot'] or
            _read(root/'alignment/STATE.json').get('execution_host') != plan['execution_host']):
        raise ValueError('policy or controlled execution host changed')
    prefix = plan['budget_prefix']
    raw = (root/'BUDGET.jsonl').read_bytes() if (root/'BUDGET.jsonl').exists() else b''
    if len(raw)<prefix['bytes'] or hashlib.sha256(raw[:prefix['bytes']]).hexdigest()!=prefix['sha256']:
        raise ValueError('original append-only budget history changed')
    for base,files in [(folder/'stage',plan['stage_sha256']),(folder,plan['input_sha256']),
                       (root,plan['lineage_sha256']),(a.ROOT,plan['implementation_sha256'])]:
        if any(a.sha(base/n) != digest for n,digest in files.items()):
            raise ValueError('pinned binary implementation, source or input changed')
    if state.get('input_preflight_sha256'):
        if a.sha(folder/'INPUT_PREFLIGHT.json') != state['input_preflight_sha256']:
            raise ValueError('actual tokenizer preflight changed')
        r = _read(folder/'INPUT_PREFLIGHT.json')
        if (r['status'] != 'ready' or r['job_sha256'] != plan['stage_sha256']['JOB.json'] or
                r['data_sha256'] != plan['data_sha256'] or r['optimizer_updates'] != 0 or
                r['model_loaded'] is not False or r['option_labels'] != ['preserved','changed'] or
                r['skipped'] != 0 or r['truncated'] != 0):
            raise ValueError('actual two-choice input preflight did not pass')
    allowance = folder/'CANDIDATE_ALLOWANCE.json'
    if bool(state.get('candidate_allowance_sha256')) != allowance.exists():
        raise ValueError('binary allowance must be bound to its controller state')
    if allowance.exists():
        if a.sha(allowance) != state['candidate_allowance_sha256']:
            raise ValueError('binary candidate allowance changed')
        execution_policy(root,plan)
    resume = folder/'RESOURCE_RESUME_AUTHORIZATION.json'
    if bool(state.get('resource_resume_sha256')) != resume.exists():
        raise ValueError('resource resume must be bound to the original state')
    if resume.exists() and (a.sha(resume) != state['resource_resume_sha256'] or
            _read(resume) != _resource_resume_record(root,plan)):
        raise ValueError('resource resume authorisation changed')
    dispatched=folder/'RESOURCE_RESUME_DISPATCH.json'
    if (bool(state.get('resource_resume_dispatch_sha256')) != dispatched.exists() or
            bool(state.get('resource_resume_dispatched_at')) != dispatched.exists()):
        raise ValueError('resource resume dispatch history cannot be forgotten')
    if dispatched.exists() and (a.sha(dispatched)!=state['resource_resume_dispatch_sha256'] or
            _read(dispatched)!=dict(run_id=RUN,authorisation_sha256=state['resource_resume_sha256'],
                dispatched_at=state['resource_resume_dispatched_at'],resume=True)):
        raise ValueError('resource resume dispatch receipt changed')


def _allowance_record(root, plan):
    root = Path(root); old = root/'lr-control'
    prior = old/'CANDIDATE_ALLOWANCE.json'
    old_plan = _read(old/'PLAN.json'); old_state = _read(old/'STATE.json')
    if (plan['run_id'] != RUN or plan['policy_snapshot']['max_candidate_runs_per_rolling_window'] != 8 or
            old_state['status'] != 'COMPLETE' or
            a.sha(prior) != old_state.get('candidate_allowance_sha256') or
            _read(prior) != previous._allowance_record(root,old_plan) or
            len([e for e in rc.BudgetLedger(root/'BUDGET.jsonl').entries()
                 if e.get('candidate') == previous.RUN]) != 1):
        raise ValueError('the prior consumed allowance must remain independently accounted for')
    return dict(version='moa.binary-candidate-allowance.v1',run_id=RUN,
        plan_sha256=a.sha(root/FOLDER/'PLAN.json'),base_candidate_limit=8,
        prior_consumed_extra_candidates=1,prior_allowance_sha256=a.sha(prior),
        additional_candidates=1,effective_candidate_limit=10,
        authorised_by='explicit_user_one_extra_binary_candidate_request_2026-10-21')


def execution_policy(root, plan):
    """One binary-only slot, beyond the separately spent LR slot; no other change."""
    policy = dict(plan['policy_snapshot'])
    path = Path(root)/FOLDER/'CANDIDATE_ALLOWANCE.json'
    if path.exists():
        if _read(path) != _allowance_record(root,plan):
            raise ValueError('invalid or broader binary candidate allowance')
        policy['max_candidate_runs_per_rolling_window'] = 10
    return policy


def grant_candidate_slot(root=a.DEFAULT):
    """Explicit operator action only, never invoked by advance or the scheduler."""
    root = Path(root); folder = root/FOLDER
    with (folder/'.controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state = _read(folder/'STATE.json'); plan = _read(folder/'PLAN.json')
        verify(root,plan,state)
        if (state['status'] not in ('WAITING_BUDGET','WAITING_RESOURCE') or state.get('training_issued') or
                (root/'mini-worker/incoming'/RUN).exists() or (root/'mini-worker/research'/RUN).exists() or
                any(e.get('candidate') == RUN for e in rc.BudgetLedger(root/'BUDGET.jsonl').entries())):
            raise ValueError('cannot rearm or extend an issued/finished binary candidate')
        record = _allowance_record(root,plan)
        path = folder/'CANDIDATE_ALLOWANCE.json'
        a.write_once(path,research.canonical(record))
        state.update(candidate_allowance_sha256=a.sha(path),effective_candidate_limit=10)
        rc._write_atomic(folder/'STATE.json',state)
        return record


def _resource_resume_record(root, plan):
    """Bind one explicit zero-update recovery to immutable before-images."""
    root=Path(root);folder=root/FOLDER
    before=_read(folder/'RESOURCE_RESUME_STATE.before.json')
    trained=_read(folder/'RESOURCE_RESUME_TRAIN.before.json')
    _training_result(root,plan,trained)
    boundary=trained['sha256']['training-state.pt']
    if (before.get('status')!='RECONCILIATION_REQUIRED' or
            before.get('training_issued') is not True or
            before.get('plan_sha256')!=a.sha(folder/'PLAN.json') or
            trained.get('stop_reason')!='resource_guard' or trained['updates']!=0 or
            trained['seconds']>=plan['configuration']['maximum_seconds']-20 or
            boundary not in before.get('resume_boundaries',[]) or
            before.get('resource_resume_sha256')):
        raise ValueError('only the verified repeated zero-update resource stop may resume once')
    entries=rc.BudgetLedger(root/'BUDGET.jsonl').entries()
    reservations=[e for e in entries if e.get('candidate')==RUN]
    if (len(reservations)!=1 or reservations[0]['at']!=before.get('training_reserved_at') or
            reservations[0]['train_seconds']!=plan['configuration']['maximum_seconds']):
        raise ValueError('one unchanged original reservation required')
    return dict(version='moa.binary-resource-resume.v1',run_id=RUN,
        plan_sha256=a.sha(folder/'PLAN.json'),
        state_before_sha256=a.sha(folder/'RESOURCE_RESUME_STATE.before.json'),
        result_before_sha256=a.sha(folder/'RESOURCE_RESUME_TRAIN.before.json'),
        checkpoint_sha256=boundary,training_reserved_at=before['training_reserved_at'],
        additional_resume_attempts=1,additional_candidates=0,additional_train_seconds=0,
        minimum_admission_memory_bytes=7*2**30,minimum_running_memory_bytes=5*2**30,
        authorised_by='explicit_user_one_same_candidate_resource_resume_2026-10-21')


def grant_resource_resume(root=a.DEFAULT, *, at=None):
    """Operator-only permission, consumed before launch; never reset history."""
    root=Path(root);folder=root/FOLDER;at=at or a.now()
    with (folder/'.controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state=_read(folder/'STATE.json');plan=_read(folder/'PLAN.json')
        verify(root,plan,state)
        if state.get('resource_resume_dispatched_at'):
            raise ValueError('the explicit resource resume has already been consumed')
        path=folder/'RESOURCE_RESUME_AUTHORIZATION.json'
        if path.exists():return _read(path)
        if _running(root) or _busy(root):raise ValueError('existing worker is active')
        if (state['status']!='RECONCILIATION_REQUIRED' or not state.get('training_reserved_at') or
                rc.parse_time(at).date()!=rc.parse_time(state['training_reserved_at']).date()):
            raise ValueError('resume must stay inside the original reservation day')
        trained=_read(root/'mini-worker/research'/RUN/'TRAIN_RESULT.json')
        _training_result(root,plan,trained)
        # Write-once before-images keep the used boundary and failed dispatches.
        a.write_once(folder/'RESOURCE_RESUME_STATE.before.json',(folder/'STATE.json').read_bytes())
        a.write_once(folder/'RESOURCE_RESUME_TRAIN.before.json',
            (root/'mini-worker/research'/RUN/'TRAIN_RESULT.json').read_bytes())
        record=_resource_resume_record(root,plan)
        a.write_once(path,research.canonical(record))
        state.update(status='WAITING_RESOURCE',resource_resume_sha256=a.sha(path),
            resource_resume_authorised_at=at,
            reason='one same-candidate resume authorised; wait for loading headroom and idle')
        rc._write_atomic(folder/'STATE.json',state)
        return record


def _running(root):
    ps = subprocess.check_output(['ps','-ww','-axo','args='],text=True,errors='replace')
    return any(RUN in line and ('moa-research-worker.py' in line or
        'moa-research-binary-evaluate.py' in line) for line in ps.splitlines())


def _launch(root, action, resume=False):
    if Path(root) != a.DEFAULT:raise ValueError('only the existing isolated worker is allowed')
    w = Path(root)/'mini-worker';incoming = w/'incoming'/RUN
    argv = ([str(incoming/'moa-research-worker.py'),'train','--job','JOB.json'] if action=='train' else
            [str(a.ROOT/'scripts/moa-research-binary-evaluate.py'),'--root',str(root),'--run-id',RUN])
    if resume:argv.append('--resume')
    with (incoming/('ACTION-'+action+'.log')).open('ab') as log:
        p = subprocess.Popen([str(w/'venv/bin/python'),*argv],cwd=incoming,env=a.mini_environment(root),
            stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    return p.pid


def _training_result(root, plan, result):
    folder = Path(root)/'mini-worker/research'/RUN; checkpoint = Path(result['checkpoint'])
    if (checkpoint.parent != folder or checkpoint.is_symlink() or result['run_id'] != RUN or
            result['job_sha256'] != plan['stage_sha256']['JOB.json'] or
            result['data_sha256'] != plan['data_sha256'] or
            not 0 <= result['seconds'] <= plan['configuration']['maximum_seconds'] or
            not 0 <= result['updates'] <= plan['configuration']['planned_updates']):
        raise ValueError('saved binary training identity/budget mismatch')
    if any(a.sha(checkpoint/n) != h for n,h in result['sha256'].items()):
        raise ValueError('saved binary checkpoint changed')
    path = folder/'TRAINING_CONTRACT.json'
    if a.sha(path) != result.get('training_contract_sha256'):
        raise ValueError('saved executed binary contract changed')
    contract = _read(path)
    if (contract['job_sha256'] != result['job_sha256'] or contract['data_sha256'] != plan['data_sha256'] or
            contract['plan']['configuration'] != plan['configuration'] or
            contract.get('semantic_profile') != PROFILE or contract.get('option_labels') != ['preserved','changed'] or
            contract.get('label_counts') != plan['train_counts']):
        raise ValueError('executed training is not the planned two-label experiment')


def advance(root=a.DEFAULT, *, at=None):
    root = Path(root);folder = root/FOLDER;at = at or a.now()
    if (root/'PAUSED').exists():return {'status':'PAUSED_BY_OPERATOR'}
    with (folder/'.controller.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {'status':'OBSERVE_ONLY','run_id':RUN}
        state = _read(folder/'STATE.json')
        if state['status'] in TERMINAL:return state
        def save(status, **values):
            state.update(status=status,**values);rc._write_atomic(folder/'STATE.json',state);return state
        try:
            plan = _read(folder/'PLAN.json');verify(root,plan,state)
            if not state.get('input_preflight_sha256'):return save('WAITING_INPUT_PREFLIGHT')
            if _running(root) or _busy(root):return {'status':'OBSERVE_ONLY','run_id':RUN}
            policy = execution_policy(root,plan)  # A new grant never reuses the old permission.
            ledger = rc.BudgetLedger(root/'BUDGET.jsonl')
            path = root/'mini-worker/research'/RUN/'TRAIN_RESULT.json'
            if not path.exists():
                if state.get('training_issued'):
                    age = (rc.parse_time(at)-rc.parse_time(state['dispatched_at'])).total_seconds()
                    return (save('RECONCILIATION_REQUIRED',reason='issued job has no known saved boundary') if age>300
                            else {'status':'DISPATCH_SETTLING','run_id':RUN})
                if any(e.get('candidate')==RUN for e in ledger.entries()):
                    return save('RECONCILIATION_REQUIRED',reason='reservation without recorded dispatch')
                ready = previous.budget_readiness(ledger,policy,at,plan['configuration']['maximum_seconds'])
                if not ready['ready']:return save('WAITING_BUDGET',budget=ready)
                res = _resources(root)
                if not res['train_allowed'] or res['available_memory_bytes']<5*2**30:
                    return save('WAITING_RESOURCE',budget=ready)
                ledger.reserve(now=at,policy=policy,candidate=RUN,train_seconds=plan['configuration']['maximum_seconds'])
                save('PUBLISHING',training_issued=True,training_reserved_at=at,dispatched_at=at,resume_boundaries=[])
                for name in plan['stage_sha256']:
                    a.write_once(root/'mini-worker/incoming'/RUN/name,(folder/'stage'/name).read_bytes())
                return save('TRAINING',pid=_launch(root,'train'))
            trained = _read(path);_training_result(root,plan,trained)
            reservations = [e for e in ledger.entries() if e.get('candidate')==RUN]
            if (state.get('training_issued') is not True or len(reservations)!=1 or
                    reservations[0]['at']!=state.get('training_reserved_at') or
                    reservations[0]['train_seconds']!=plan['configuration']['maximum_seconds']):
                return save('RECONCILIATION_REQUIRED',reason='saved result has no authorised original reservation')
            if trained.get('stop_reason') in ('resource_guard','pause_requested'):
                reserved = state.get('training_reserved_at'); entries = [e for e in ledger.entries() if e.get('candidate')==RUN]
                if (not reserved or len(entries)!=1 or entries[0]['at']!=reserved or
                        entries[0]['train_seconds']!=plan['configuration']['maximum_seconds'] or
                        rc.parse_time(at).date()!=rc.parse_time(reserved).date()):
                    return save('RECONCILIATION_REQUIRED',reason='resume would leave original daily reservation')
                u = ledger.usage(now=at,policy=policy)
                if any(u[k]>policy[cap] for k,cap in [('rolling_seconds','max_train_time_rolling_seconds'),
                    ('today_seconds','max_train_time_per_calendar_day_seconds'),('candidates','max_candidate_runs_per_rolling_window')]):
                    return save('RECONCILIATION_REQUIRED',reason='resume exceeds original limits')
                explicit = (_read(folder/'RESOURCE_RESUME_AUTHORIZATION.json')
                            if state.get('resource_resume_sha256') else None)
                if explicit and state.get('resource_resume_dispatched_at'):
                    age=(rc.parse_time(at)-rc.parse_time(state['resource_resume_dispatched_at'])).total_seconds()
                    if a.sha(path)==explicit['result_before_sha256'] and 0<=age<300:
                        return {'status':'DISPATCH_SETTLING','run_id':RUN}
                    return save('RECONCILIATION_REQUIRED',reason='explicit single resume consumed; no further training dispatch')
                res = _resources(root)
                minimum=explicit['minimum_admission_memory_bytes'] if explicit else 5*2**30
                if not res['train_allowed'] or res['available_memory_bytes']<minimum:
                    return save('WAITING_RESOURCE',minimum_admission_memory_bytes=minimum)
                if explicit:
                    if a.sha(path)!=explicit['result_before_sha256']:
                        raise ValueError('authorised saved resource boundary changed before dispatch')
                    dispatched=folder/'RESOURCE_RESUME_DISPATCH.json'
                    a.write_once(dispatched,research.canonical(dict(run_id=RUN,
                        authorisation_sha256=state['resource_resume_sha256'],dispatched_at=at,resume=True)))
                    save('TRAINING',dispatched_at=at,resource_resume_dispatched_at=at,
                        resource_resume_dispatch_sha256=a.sha(dispatched))
                    return save('TRAINING',pid=_launch(root,'train',resume=True))
                boundary = trained['sha256']['training-state.pt'];used = state.get('resume_boundaries',[])
                if boundary in used or len(used)>=4:return save('RECONCILIATION_REQUIRED',reason='resume boundary repeated/limit')
                save('TRAINING',dispatched_at=at,resume_boundaries=used+[boundary])
                return save('TRAINING',pid=_launch(root,'train',resume=True))
            if (trained.get('status')!='saved' or not trained.get('training_complete') or
                    trained['updates']!=plan['configuration']['planned_updates']):
                return save('RECONCILIATION_REQUIRED',reason='binary candidate incomplete; do not repeat')
            receipt = folder/'EVALUATION.json'; evaluated = _read(receipt) if receipt.exists() else None
            if evaluated and not 0 <= evaluated['seconds'] <= plan['evaluation_seconds']:
                raise ValueError('invalid accumulated evaluation time; do not reset it')
            if evaluated and evaluated['status']=='complete':
                request = _read(folder/'BINARY_REQUEST.json')
                if (evaluated['model_sha256']!=trained['sha256']['model.safetensors'] or
                        evaluated['request_sha256']!=plan['input_sha256']['BINARY_REQUEST.json'] or
                        evaluated['seconds']>plan['evaluation_seconds'] or
                        evaluated.get('separate_process') is not True or evaluation_inputs(request,evaluated['records'])):
                    raise ValueError('evaluation identity, completeness or budget mismatch')
                gold = _read(folder/'GOLD.json')
                answers = [dict(r,expected=gold[r['case_id']]) for r in evaluated['records']]
                report = comparison(_read(folder/'BASELINES.json'),
                    [r for r in answers if r['mode']=='binary'],[r for r in answers if r['mode']=='binary_reverse'],
                    train_counts=plan['train_counts'],rule_excluded_dev=_read(folder/'DATA_AUDIT.json')['dev']['rule_excluded'])
                report.update(updates=trained['updates'],train_seconds=trained['seconds'],
                    checkpoint_sha256=trained['sha256']['model.safetensors'],separate_reload=True,
                    plan_sha256=state['plan_sha256'],evaluation_seconds=evaluated['seconds'])
                a.write_once(folder/'COMPARISON.json',research.canonical(report))
                lines = ['# MOA 정상·오류 이진 학습 비교','',
                    '기존 노출 합성 개발자료입니다. 근거 부족 규칙 성적은 모델 정답에 포함하지 않았습니다.','',
                    '| 모델 | 정답률 | 정상 오탐률 | 오류 검출률 |','| --- | --- | --- | --- |']
                for name,value in report['models'].items():
                    m = value['average']
                    lines.append('| %s | %.1f%% (%d/%d) | %.1f%% | %.1f%% |' % (name,
                        100*m['accuracy'],m['correct'],m['items'],100*m['normal_false_positive_rate'],100*(1-m['error_miss_rate'])))
                lines += ['','자동 추가 학습·운영 적용·실자료 승인·공개는 하지 않습니다.']
                a.write_once(folder/'RESULT.md',('\n'.join(lines)+'\n').encode())
                return save('COMPLETE',comparison_sha256=a.sha(folder/'COMPARISON.json'),
                    checkpoint_sha256=report['checkpoint_sha256'],completed_at=at)
            if evaluated and evaluated['status']!='waiting_resources':
                return save('RECONCILIATION_REQUIRED',reason='evaluation stopped; do not reset budget')
            if state.get('evaluation_issued') and evaluated is None:
                return save('RECONCILIATION_REQUIRED',reason='evaluation has no known saved boundary')
            if not _resources(root)['verify_allowed']:return save('WAITING_EVAL_RESOURCE')
            boundary = a.sha(receipt) if evaluated else 'initial';used = state.get('evaluation_boundaries',[])
            if boundary in used or len(used)>=4:return save('RECONCILIATION_REQUIRED',reason='evaluation resume limit')
            a.write_once(folder/'EVAL_RESERVATION.json',research.canonical({'candidate':RUN,
                'maximum_seconds':plan['evaluation_seconds'],'does_not_reset_old_candidate_evaluation_budget':True}))
            save('EVALUATING',evaluation_issued=True,evaluation_boundaries=used+[boundary])
            return save('EVALUATING',pid=_launch(root,'evaluate'))
        except (ValueError,KeyError,OSError,subprocess.SubprocessError,rc.BudgetExhausted) as exc:
            return save('RECONCILIATION_REQUIRED',reason=type(exc).__name__+'; inspect private receipts')


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['arm','advance','status','grant-candidate-slot','grant-resource-resume'])
    parser.add_argument('--root',type=Path,default=a.DEFAULT);args = parser.parse_args()
    value = (arm(args.root) if args.action=='arm' else advance(args.root) if args.action=='advance'
        else grant_candidate_slot(args.root) if args.action=='grant-candidate-slot'
        else grant_resource_resume(args.root) if args.action=='grant-resource-resume'
        else _read(args.root/FOLDER/'STATE.json'))
    print(json.dumps(value,ensure_ascii=False))


if __name__=='__main__':main()
