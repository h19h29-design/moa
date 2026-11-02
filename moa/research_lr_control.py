"""One explicitly authorised scheduler-only replay, after existing capacity returns.

This is a controlled repeat of exposed synthetic train/dev, not a new campaign,
new blind or a permission to repeat other failed experiments. The original
ledger and resource guards remain authoritative. Nothing touches the NAS.
"""
from __future__ import annotations

from collections import Counter
import copy
import datetime as dt
import fcntl
import json
from pathlib import Path
import subprocess

from . import r2_research as research, research_alignment as a, research_campaign as rc
from .research_diagnostics import replay_binary
from .research_dataset import project

BASELINE = 'moa-unattended-20261020-pilot-01-pairs-r03'
RUN = 'moa-unattended-20261020-lr-schedule-01'
SOURCES = ('moa/research_lr_control.py','moa/research_binary.py','moa/research_diagnostics.py',
           'moa/research_campaign.py','moa/research_alignment.py','scripts/moa-research-lr-evaluate.py')
TERMINAL = ('COMPLETE','RECONCILIATION_REQUIRED')


def controlled_configuration(original, item_count):
    fixed = research.training_plan(original,item_count)['configuration']
    if {k for k in original if original[k] != fixed[k]} != {'planned_updates'}:
        raise ValueError('exactly one changed scheduler horizon required')
    return fixed


def budget_readiness(ledger, policy, at, seconds):
    now = rc.parse_time(at)
    def fits(when):
        u = ledger.usage(now=when,policy=policy)
        return (0 < seconds <= policy['max_train_time_per_candidate_seconds'] and
                u['remaining_candidates'] > 0 and
                min(u['remaining_today_seconds'],u['remaining_rolling_seconds']) >= seconds)
    possible = [now.replace(hour=0,minute=0,second=0,microsecond=0)+dt.timedelta(days=1)]
    possible += [rc.parse_time(e['at'])+dt.timedelta(days=policy['rolling_budget_days'],seconds=1)
                 for e in ledger.entries()]
    return {'ready':fits(now),'usage':ledger.usage(now=now,policy=policy),
            'next_eligible_at':next((t.isoformat(timespec='seconds') for t in sorted(set(possible))
                                    if t > now and fits(t)),None)}


def comparison(first, reverse, new_first, new_reverse, *, train_counts):
    if set(first) != {'r1','old_candidate'} or set(reverse) != set(first):
        raise ValueError('both frozen baselines required')
    cases = {r['case_id']:r['expected'] for r in first['old_candidate']}
    for rows in [*first.values(),*reverse.values(),new_first,new_reverse]:
        if len(rows) != len(cases) or {r['case_id']:r['expected'] for r in rows} != cases:
            raise ValueError('same cases and gold required for every model/order')
    models = {k:replay_binary(first[k],reverse[k],train_counts=train_counts) for k in first}
    models['new_candidate'] = replay_binary(new_first,new_reverse,train_counts=train_counts)
    before,after = models['old_candidate']['average'],models['new_candidate']['average']
    return {'scope':'exposed_development_scheduler_control_not_new_blind',
            'models':models,'delta_vs_old':{k:after[k]-before[k] for k in
                ('accuracy','normal_false_positive_rate','error_miss_rate','balanced_accuracy')},
            'new_blind':False,'operational_promotion':False,'human_approved':False}


def evaluation_inputs(request, completed):
    from .research_binary import binary_questions
    inputs = []; identities = set()
    for row in request:
        if (set(row) != {'case_id','state','questions','operation','family'} or
                row['case_id'] in identities or len(row['questions']) != 1 or
                row['questions'] != binary_questions(list(row['questions']))):
            raise ValueError('unique gold-free original binary input required')
        identities.add(row['case_id'])
        for mode in ('binary','binary_reverse'):
            item = copy.deepcopy(row);item['mode'] = mode
            if mode == 'binary_reverse':
                q = next(iter(item['questions'].values()))
                q['criteria'] = dict(reversed(list(q['criteria'].items())))
            inputs.append(item)
    prefix = [(r['case_id'],r['mode']) for r in completed]
    if prefix != [(r['case_id'],r['mode']) for r in inputs[:len(completed)]]:
        raise ValueError('saved evaluation must be a contiguous known prefix')
    return inputs[len(completed):]


def _rows(path):
    return [json.loads(line) for line in path.read_bytes().splitlines() if line.strip()]


def arm(root=a.DEFAULT):
    """Freeze inputs now; do not publish, reserve time or start a process."""
    root = Path(root); folder = root/'lr-control'; w = root/'mini-worker'
    if (folder/'PLAN.json').exists():
        return rc._read_json(folder/'STATE.json')
    if (w/'incoming'/RUN).exists() or (w/'research'/RUN).exists():
        raise ValueError('pre-existing controlled run requires reconciliation, not rearm')
    old_in = w/'incoming'/BASELINE
    old = rc._read_json(old_in/'JOB-r4.json')
    trained = rc._read_json(w/'research'/BASELINE/'TRAIN_RESULT.json')
    audit = rc._read_json(root/'binary/FOLLOWUP_AUDIT.json')
    if (a.sha(old_in/'JOB-r4.json') != audit['job_sha256'] or
            trained['job_sha256'] != audit['job_sha256'] or
            trained['sha256']['model.safetensors'] != audit['checkpoint_sha256'] or
            old.get('execution_device') != 'cpu' or
            rc._read_json(root/'alignment/STATE.json').get('execution_host') != 'mini'):
        raise ValueError('the audited same-device baseline is required')
    for name,digest in trained['sha256'].items():
        if a.sha(Path(trained['checkpoint'])/name) != digest:
            raise ValueError('original candidate changed')
    for name,digest in old['protected_models'].items():
        if a.sha(w/name) != digest: raise ValueError('protected metadata changed')
    if a.sha(root/'alignment/MANIFEST.json') != old['manifest_sha256']:
        raise ValueError('original synthetic lineage manifest changed')
    train = _rows(root/'alignment/data/paired-train.jsonl')
    projected = [dict(row,expected=original['expected'])
                 for row,original in zip(project(train),train)]
    if projected != _rows(old_in/'train-000.jsonl'):
        raise ValueError('train lineage does not match the actual fixed training inputs')
    if any(r['meta'].get('split') != 'train' or r['meta'].get('evaluation_only') is not False or
           r['meta'].get('synthetic_gold') is not True or r['meta'].get('rights_status') != 'allowed'
           for r in train):
        raise ValueError('only the previously authorised synthetic train split may repeat')
    fixed = controlled_configuration(old['configuration'],len(train))
    counts = Counter(next(iter(r['expected'].values())) for r in train)
    dev = _rows(root/'binary/dev.jsonl')
    full_dev = _rows(root/'alignment/data/paired-dev.jsonl')
    if project(full_dev) != _rows(old_in/'dev-000.jsonl'):
        raise ValueError('development lineage does not match the original gold-free inputs')
    original_dev = {r['case_id']:r for r in full_dev}
    if any(r['case_id'] not in original_dev or
           r['state'] != original_dev[r['case_id']]['state'] or
           r['expected'] != original_dev[r['case_id']]['expected'] for r in dev):
        raise ValueError('binary development source differs from the original fixed split')
    cached = [rc._read_json(root/'binary'/name) for name in ('PROBE.json','PROBE_REVERSE.json')]
    old_weights = {'r1':old['reference_models']['r1']['sha256']['model.safetensors'],
                   'candidate':trained['sha256']['model.safetensors']}
    baselines = []
    for record,mode in zip(cached,('binary','binary_reverse')):
        if record['status'] != 'complete' or any(record['models'][k]['checkpoint_sha256'] != v
                                               for k,v in old_weights.items()):
            raise ValueError('frozen baseline observations changed')
        part = {name:[r for r in record['records'] if r['model'] == key and r['split'] == 'dev'
                      and r['mode'] == mode] for name,key in [('r1','r1'),('old_candidate','candidate')]}
        baselines.append(part)
    gold = {r['case_id']:next(iter(r['expected'].values())) for r in dev}
    for part in baselines:
        for records in part.values():
            if len(records) != len(gold) or {r['case_id']:r['expected'] for r in records} != gold:
                raise ValueError('development identities/gold differ from the cached baseline')
    stage = folder/'stage'
    for name,digest in old['bundle_sha256'].items():
        if name not in ('train-000.jsonl','dev-000.jsonl') or a.sha(old_in/name) != digest:
            raise ValueError('fixed original train/dev bundle required')
        a.write_once(stage/name,(old_in/name).read_bytes())
    for name,source in [('moa-research-worker.py','scripts/moa-research-worker.py'),
                        ('r2_research.py','moa/r2_research.py')]:
        a.write_once(stage/name,(a.ROOT/source).read_bytes())
    job = copy.deepcopy(old)
    for key in ('execution_revision','previous_job','evaluation_requests_sha256'):
        job.pop(key,None)
    job.update(run_id=RUN,bundle='incoming/'+RUN,configuration=fixed,
               runner_sha256=a.sha(stage/'moa-research-worker.py'),library='r2_research.py',
               library_sha256=a.sha(stage/'r2_research.py'))
    a.write_once(stage/'JOB.json',research.canonical(job))
    inputs = [{'case_id':r['case_id'],'state':r['state'],'questions':r['questions'],
               'operation':r['meta']['operation'],'family':r['meta']['family']} for r in dev]
    evaluation_inputs(inputs,[])
    a.write_once(folder/'BINARY_REQUEST.json',research.canonical(inputs))
    a.write_once(folder/'GOLD.json',research.canonical(gold))
    a.write_once(folder/'BASELINES.json',research.canonical(baselines))
    snapshot = rc._read_json(root/'STATE.json')['policy_snapshot']
    if fixed['maximum_updates'] > snapshot['max_optimizer_updates_per_candidate']:
        raise ValueError('existing update cap is stricter than the proposed control')
    plan = {'version':'moa.scheduler-control.v1','run_id':RUN,'baseline_run':BASELINE,
            'one_candidate_only':True,'authorised_by':'explicit_user_scheduler_only_request',
            'controlled_repeat_not_new_training_data':True,'new_blind':False,
            'configuration':fixed,'original_configuration':old['configuration'],
            'initial_model_sha256':old['parent_files']['model.safetensors'],
            'old_candidate_sha256':old_weights['candidate'],'data_sha256':trained['data_sha256'],
            'train_counts':{k:counts[k] for k in ('preserved','changed')},
            'stage_sha256':{p.name:a.sha(p) for p in stage.iterdir()},
            'input_sha256':{n:a.sha(folder/n) for n in ('BINARY_REQUEST.json','GOLD.json','BASELINES.json')},
            'implementation_sha256':{n:a.sha(a.ROOT/n) for n in SOURCES},
            'lineage_sha256':{n:a.sha(root/n) for n in ('alignment/MANIFEST.json',
                'alignment/data/paired-train.jsonl','alignment/data/paired-dev.jsonl')},
            'policy_snapshot':snapshot,'evaluation_seconds':min(600,snapshot['max_eval_time_per_candidate_seconds']),
            'execution_host':'mini','execution_device':'cpu','operational_promotion':False}
    a.write_once(folder/'PLAN.json',research.canonical(plan))
    state = {'status':'WAITING_BUDGET','run_id':RUN,'training_issued':False,
             'plan_sha256':a.sha(folder/'PLAN.json'),'operational_promotion':False}
    rc._write_atomic(folder/'STATE.json',state)
    return state


def verify(root, plan, state):
    folder = Path(root)/'lr-control'
    if a.sha(folder/'PLAN.json') != state['plan_sha256'] or plan['run_id'] != RUN:
        raise ValueError('fixed one-candidate plan changed')
    if rc._read_json(Path(root)/'STATE.json')['policy_snapshot'] != plan['policy_snapshot']:
        raise ValueError('policy changed; reconcile without overriding it')
    if rc._read_json(Path(root)/'alignment/STATE.json').get('execution_host') != plan['execution_host']:
        raise ValueError('the control must keep the original execution host')
    for base,files in [(folder/'stage',plan['stage_sha256']),(folder,plan['input_sha256']),
                       (Path(root),plan['lineage_sha256']),(a.ROOT,plan['implementation_sha256'])]:
        if any(a.sha(base/n) != digest for n,digest in files.items()):
            raise ValueError('pinned implementation or input changed')
    if state.get('candidate_allowance_sha256') and a.sha(folder/'CANDIDATE_ALLOWANCE.json') != state['candidate_allowance_sha256']:
        raise ValueError('single-candidate allowance changed')


def _allowance_record(root, plan):
    if plan['run_id'] != RUN or plan['policy_snapshot']['max_candidate_runs_per_rolling_window'] != 8:
        raise ValueError('only the existing eight-slot plan may receive one scoped extension')
    return {'version':'moa.single-candidate-allowance.v1','run_id':RUN,
            'plan_sha256':a.sha(Path(root)/'lr-control/PLAN.json'),
            'base_candidate_limit':8,'additional_candidates':1,
            'authorised_by':'explicit_user_one_extra_candidate_request_2026-10-20'}


def execution_policy(root, plan):
    """Only this fixed run may use slot nine; all time/resource caps stay intact."""
    policy = copy.deepcopy(plan['policy_snapshot'])
    path = Path(root)/'lr-control/CANDIDATE_ALLOWANCE.json'
    if path.exists():
        if research.canonical(rc._read_json(path)) != research.canonical(_allowance_record(root,plan)):
            raise ValueError('invalid or broader single-candidate allowance')
        policy['max_candidate_runs_per_rolling_window'] += 1
    return policy


def grant_candidate_slot(root=a.DEFAULT):
    """Explicit operator action, never called by an automatic tick or training."""
    root = Path(root);folder = root/'lr-control'
    with (folder/'.controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state = rc._read_json(folder/'STATE.json');plan = rc._read_json(folder/'PLAN.json')
        verify(root,plan,state)
        if (state['status'] not in ('WAITING_BUDGET','WAITING_RESOURCE') or state.get('training_issued') or
                (root/'mini-worker/incoming'/RUN).exists() or (root/'mini-worker/research'/RUN).exists() or
                any(e.get('candidate') == RUN for e in rc.BudgetLedger(root/'BUDGET.jsonl').entries())):
            raise ValueError('cannot rearm or extend an issued/finished candidate')
        record = _allowance_record(root,plan)
        path = folder/'CANDIDATE_ALLOWANCE.json'
        a.write_once(path,research.canonical(record))
        state.update(candidate_allowance_sha256=a.sha(path),effective_candidate_limit=9)
        rc._write_atomic(folder/'STATE.json',state)
        return record


def _resources(root):
    return a.resources(root)


def _busy(root):
    path = Path(root)/'mini-worker/device.lock'
    if not path.exists(): return False
    with path.open('rb') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: return True
    return False


def _running(root):
    ps = subprocess.check_output(['ps','-ww','-axo','args='],text=True,errors='replace')
    return any(RUN in line and ('moa-research-worker.py' in line or
               'moa-research-lr-evaluate.py' in line) for line in ps.splitlines())


def _launch(root, action, resume=False):
    if Path(root) != a.DEFAULT: raise ValueError('only the existing isolated auxiliary worker is permitted')
    w = Path(root)/'mini-worker'; incoming = w/'incoming'/RUN
    if action == 'train':
        argv = [str(incoming/'moa-research-worker.py'),'train','--job','JOB.json']
        if resume: argv.append('--resume')
    else:
        argv = [str(a.ROOT/'scripts/moa-research-lr-evaluate.py'),'--root',str(root),'--run-id',RUN]
    with (incoming/('ACTION-'+action+'.log')).open('ab') as log:
        process = subprocess.Popen([str(w/'venv/bin/python'),*argv],cwd=incoming,
            env=a.mini_environment(root),stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    return process.pid


def _training_result(root, plan, result):
    folder = Path(root)/'mini-worker/research'/RUN
    checkpoint = Path(result['checkpoint'])
    if (checkpoint.parent != folder or checkpoint.is_symlink() or
            result['run_id'] != RUN or result['job_sha256'] != plan['stage_sha256']['JOB.json'] or
            result['data_sha256'] != plan['data_sha256'] or
            result['seconds'] > plan['configuration']['maximum_seconds']):
        raise ValueError('saved training identity/budget mismatch')
    if any(a.sha(checkpoint/n) != digest for n,digest in result['sha256'].items()):
        raise ValueError('saved checkpoint changed')
    contract = folder/'TRAINING_CONTRACT.json'
    if (not contract.exists() or
            a.sha(contract) != result.get('training_contract_sha256')):
        raise ValueError('saved effective training contract missing or changed')
    executed = rc._read_json(contract)
    if (executed['job_sha256'] != result['job_sha256'] or
            executed['data_sha256'] != plan['data_sha256'] or
            executed['plan']['configuration'] != plan['configuration']):
        raise ValueError('executed training contract differs from the single-variable plan')


def advance(root=a.DEFAULT, *, at=None):
    root = Path(root); folder = root/'lr-control'; at = at or a.now()
    if (root/'PAUSED').exists(): return {'status':'PAUSED_BY_OPERATOR'}
    with (folder/'.controller.lock').open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: return {'status':'OBSERVE_ONLY','run_id':RUN}
        state = rc._read_json(folder/'STATE.json')
        if state['status'] in TERMINAL: return state
        def save(status, **values):
            state.update(status=status,**values);rc._write_atomic(folder/'STATE.json',state);return state
        try:
            plan = rc._read_json(folder/'PLAN.json');verify(root,plan,state)
            policy = execution_policy(root,plan)
            if _running(root) or _busy(root): return {'status':'OBSERVE_ONLY','run_id':RUN}
            trained_path = root/'mini-worker/research'/RUN/'TRAIN_RESULT.json'
            if not trained_path.exists():
                if state.get('training_issued'):
                    age = (rc.parse_time(at)-rc.parse_time(state['dispatched_at'])).total_seconds()
                    return (save('RECONCILIATION_REQUIRED',reason='issued job has no known saved boundary')
                            if age > 300 else {'status':'DISPATCH_SETTLING','run_id':RUN})
                ledger = rc.BudgetLedger(root/'BUDGET.jsonl')
                if any(e.get('candidate') == RUN for e in ledger.entries()):
                    return save('RECONCILIATION_REQUIRED',reason='reservation exists without a recorded dispatch')
                readiness = budget_readiness(ledger,policy,at,
                                             plan['configuration']['maximum_seconds'])
                if not readiness['ready']: return save('WAITING_BUDGET',budget=readiness)
                resources = _resources(root)
                if not resources.get('train_allowed') or resources['available_memory_bytes'] < 5*2**30:
                    return save('WAITING_RESOURCE',budget=readiness)
                ledger.reserve(now=at,policy=policy,candidate=RUN,
                               train_seconds=plan['configuration']['maximum_seconds'])
                save('PUBLISHING',training_issued=True,training_reserved_at=at,
                     dispatched_at=at,resume_boundaries=[])
                for name in plan['stage_sha256']:
                    a.write_once(root/'mini-worker/incoming'/RUN/name,(folder/'stage'/name).read_bytes())
                pid = _launch(root,'train')
                return save('TRAINING',pid=pid)
            trained = rc._read_json(trained_path);_training_result(root,plan,trained)
            if trained.get('stop_reason') in ('resource_guard','pause_requested'):
                reserved_at = state.get('training_reserved_at')
                ledger = rc.BudgetLedger(root/'BUDGET.jsonl')
                reservations = [e for e in ledger.entries() if e.get('candidate') == RUN]
                if (not reserved_at or len(reservations) != 1 or
                        reservations[0]['at'] != reserved_at or
                        reservations[0]['train_seconds'] != plan['configuration']['maximum_seconds'] or
                        rc.parse_time(at).date() != rc.parse_time(reserved_at).date()):
                    return save('RECONCILIATION_REQUIRED',reason='resume would leave its original daily reservation')
                usage = ledger.usage(now=at,policy=policy)
                if any(usage[key] > policy[limit] for key,limit in (
                        ('rolling_seconds','max_train_time_rolling_seconds'),
                        ('today_seconds','max_train_time_per_calendar_day_seconds'),
                        ('candidates','max_candidate_runs_per_rolling_window'))):
                    return save('RECONCILIATION_REQUIRED',reason='existing reservation exceeds a current budget limit')
                resources = _resources(root)
                if not resources.get('train_allowed') or resources['available_memory_bytes'] < 5*2**30:
                    return save('WAITING_RESOURCE')
                boundary = trained['sha256']['training-state.pt']
                used = state.get('resume_boundaries',[])
                if boundary in used or len(used) >= 4:
                    return save('RECONCILIATION_REQUIRED',reason='repeated/too many resume boundaries')
                save('TRAINING',resume_boundaries=used+[boundary],dispatched_at=at)
                return save('TRAINING',pid=_launch(root,'train',resume=True))
            if (trained.get('status') != 'saved' or not trained.get('training_complete') or
                    trained['updates'] != plan['configuration']['planned_updates']):
                return save('RECONCILIATION_REQUIRED',reason='not a complete equal-update control')
            evaluated_path = folder/'EVALUATION.json'
            evaluated = rc._read_json(evaluated_path) if evaluated_path.exists() else None
            if evaluated and evaluated['status'] == 'complete':
                if (evaluated['model_sha256'] != trained['sha256']['model.safetensors'] or
                        evaluated['request_sha256'] != plan['input_sha256']['BINARY_REQUEST.json'] or
                        evaluated['seconds'] > plan['evaluation_seconds']):
                    raise ValueError('evaluation identity/budget mismatch')
                gold = rc._read_json(folder/'GOLD.json')
                answers = [dict(r,expected=gold[r['case_id']]) for r in evaluated['records']]
                cached = rc._read_json(folder/'BASELINES.json')
                report = comparison(*cached,[r for r in answers if r['mode']=='binary'],
                    [r for r in answers if r['mode']=='binary_reverse'],train_counts=plan['train_counts'])
                report.update(updates=trained['updates'],train_seconds=trained['seconds'],
                    checkpoint_sha256=trained['sha256']['model.safetensors'],separate_reload=True,
                    plan_sha256=state['plan_sha256'],evaluation_seconds=evaluated['seconds'])
                a.write_once(folder/'COMPARISON.json',research.canonical(report))
                lines = ['# MOA 학습률 계획 단일 변수 비교','',
                         '기존에 노출된 합성 개발자료의 통제 비교이며 새 블라인드/실물 성적이 아닙니다.',
                         '운영 모델·자동 승인·공개 설정은 바꾸지 않았습니다.','',
                         '| 모델 | 정답률 | 정상 오탐률 | 실제 오류 검출률 |',
                         '| --- | --- | --- | --- |']
                for name,value in report['models'].items():
                    m = value['average']
                    lines.append('| %s | %.1f%% (%d/%d) | %.1f%% | %.1f%% |' %
                        (name,100*m['accuracy'],m['correct'],m['items'],
                         100*m['normal_false_positive_rate'],100*(1-m['error_miss_rate'])))
                lines += ['','추가 학습을 반복하거나 성적을 운영 적용 허가로 해석하지 않습니다.',
                          '업데이트: %d / 훈련시간: %.2f초 / 체크포인트: `%s`' %
                          (trained['updates'],trained['seconds'],report['checkpoint_sha256'])]
                a.write_once(folder/'RESULT.md',('\n'.join(lines)+'\n').encode())
                return save('COMPLETE',comparison_sha256=a.sha(folder/'COMPARISON.json'),
                            checkpoint_sha256=report['checkpoint_sha256'],completed_at=at)
            if evaluated and evaluated['status'] != 'waiting_resources':
                return save('RECONCILIATION_REQUIRED',reason='evaluation stopped; do not reset its budget')
            if state.get('evaluation_issued') and evaluated is None:
                return save('RECONCILIATION_REQUIRED',reason='evaluation disappeared without a saved boundary')
            if not _resources(root).get('verify_allowed'): return save('WAITING_EVAL_RESOURCE')
            boundary = a.sha(evaluated_path) if evaluated else 'initial'
            used = state.get('evaluation_boundaries',[])
            if boundary in used or len(used) >= 4:
                return save('RECONCILIATION_REQUIRED',reason='evaluation resume limit')
            a.write_once(folder/'EVAL_RESERVATION.json',research.canonical({
                'candidate':RUN,'maximum_seconds':plan['evaluation_seconds'],
                'does_not_reset_old_candidate_evaluation_budget':True}))
            save('EVALUATING',evaluation_issued=True,evaluation_boundaries=used+[boundary])
            return save('EVALUATING',pid=_launch(root,'evaluate'))
        except (ValueError,KeyError,OSError,subprocess.SubprocessError,rc.BudgetExhausted) as exc:
            return save('RECONCILIATION_REQUIRED',reason=type(exc).__name__+'; inspect private receipts')


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['arm','advance','status','grant-candidate-slot'])
    parser.add_argument('--root',type=Path,default=a.DEFAULT)
    args = parser.parse_args()
    if args.action == 'arm': value = arm(args.root)
    elif args.action == 'advance': value = advance(args.root)
    elif args.action == 'grant-candidate-slot': value = grant_candidate_slot(args.root)
    else: value = rc._read_json(args.root/'lr-control/STATE.json')
    print(json.dumps(value,ensure_ascii=False))


if __name__ == '__main__': main()
