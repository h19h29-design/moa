"""Operator-authorised host transfer of ONE already-reserved binary candidate.

The historical Mini PLAN/grant/checkpoint/budget are never rewritten. A pinned
overlay changes execution location only. A durable receipt consumes the existing
unused resume permission before the SSH launch; uncertain delivery fails closed.
"""
from __future__ import annotations

import fcntl
import io
import json
from pathlib import Path
import shlex
import subprocess
import tarfile

from . import research_alignment as a, research_campaign as rc, r2_research as research
from . import research_binary_control as binary, research_runner as rr

TRANSFER = 'book-transfer-20261021'
CONTROL = 'scripts/moa-research-book-control.py'
EVALUATOR = 'scripts/moa-research-book-evaluate.py'
SELF = 'moa/research_book_resume.py'
CONTROLLER = 'moa/research_binary_control.py'


def folder(root):
    return Path(root)/binary.FOLDER/TRANSFER


def authorised_controller_revision(root, name, original_sha):
    """Only this explicit overlay may replace the controller implementation pin."""
    p = folder(root)/'PLAN.json'
    if name != CONTROLLER or not p.exists():
        return False
    plan = rc._read_json(p)
    old = folder(root)/'archive'/name
    return (plan.get('authorised_by') == 'explicit_user_macbook_only_same_resume_2026-10-21'
            and plan.get('original_controller_sha256') == original_sha
            and a.sha(old) == original_sha
            and a.sha(a.ROOT/name) == plan['source_sha256'][name])


def approved_boundary(root, old, grant):
    path=Path(root)/'mini-worker/research'/binary.RUN/'TRAIN_RESULT.json'
    saved=rc._read_json(path);binary._training_result(root,old,saved)
    if (a.sha(path)!=grant['result_before_sha256'] or
            saved['sha256']['training-state.pt']!=grant['checkpoint_sha256'] or
            saved['updates']!=0 or saved['stop_reason']!='resource_guard'):
        raise ValueError('only the exact originally authorised zero-update boundary may move')
    return saved


def prepare(root=a.DEFAULT, *, at=None):
    """Explicit operator preparation ONLY; never called by the timer."""
    root=Path(root);p=folder(root);at=at or a.now()
    if not (root/'PAUSED').exists():
        raise ValueError('pause the old controller before host transfer')
    p.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (root/binary.FOLDER/'.controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if (p/'PLAN.json').exists():
            plan=rc._read_json(p/'PLAN.json');verify(root,plan);return plan
        base=root/binary.FOLDER;old=rc._read_json(base/'PLAN.json')
        state=rc._read_json(base/'STATE.json');grant=rc._read_json(base/'RESOURCE_RESUME_AUTHORIZATION.json')
        if ((base/'RESOURCE_RESUME_DISPATCH.json').exists() or state.get('resource_resume_dispatched_at') or
                binary._running(root) or binary._busy(root) or not state.get('training_issued') or
                rc.parse_time(at).date()!=rc.parse_time(state['training_reserved_at']).date()):
            raise ValueError('only the unconsumed same-day stopped candidate can move')
        run=root/'mini-worker/research'/binary.RUN
        saved=approved_boundary(root,old,grant)
        names=['PLAN.json','STATE.json','RESOURCE_RESUME_AUTHORIZATION.json']
        if (base/'CANDIDATE_ALLOWANCE.json').exists():names.append('CANDIDATE_ALLOWANCE.json')
        for name in names:
            a.write_once(p/'archive'/name,(base/name).read_bytes())
        a.write_once(p/'archive/BUDGET.jsonl',(root/'BUDGET.jsonl').read_bytes())
        a.write_once(p/'archive/TRAIN_RESULT.json',(run/'TRAIN_RESULT.json').read_bytes())
        original_controller=old['implementation_sha256'].get(CONTROLLER)
        if original_controller and a.sha(p/'archive'/CONTROLLER)!=original_controller:
            raise ValueError('original controller before-image required')
        incoming=Path('incoming')/binary.RUN;stage=p/'stage'
        for name in old['stage_sha256']:
            a.write_once(stage/incoming/name,(base/'stage'/name).read_bytes())
        job=rc._read_json(base/'stage/JOB.json')
        if job.get('previous_job') or job.get('execution_revision'):
            raise ValueError('unexpected existing execution chain; reconcile first')
        revised=dict(job,execution_device='mps',execution_revision=1,
                     previous_job=dict(filename='JOB.json',sha256=a.sha(base/'stage/JOB.json')))
        a.write_once(stage/incoming/'JOB-r1.json',research.canonical(revised))
        translated=dict(saved,checkpoint=str(Path(rr.REMOTE_ROOT)/'research'/binary.RUN/saved['checkpoint_name']))
        for name in [*saved['sha256'],'HASHES.json']:
            a.write_once(stage/'research'/binary.RUN/saved['checkpoint_name']/name,
                         (Path(saved['checkpoint'])/name).read_bytes())
        a.write_once(stage/'research'/binary.RUN/'TRAIN_RESULT.json',research.canonical(translated))
        a.write_once(stage/incoming/'BINARY_REQUEST.json',(base/'BINARY_REQUEST.json').read_bytes())
        a.write_once(stage/incoming/'moa-research-book-evaluate.py',(a.ROOT/EVALUATOR).read_bytes())
        control=dict(run_id=binary.RUN,job_sha256=a.sha(stage/incoming/'JOB-r1.json'),
                     request_sha256=old['input_sha256']['BINARY_REQUEST.json'],
                     evaluator_sha256=a.sha(a.ROOT/EVALUATOR),maximum_eval_seconds=old['evaluation_seconds'],
                     reservation_at=state['training_reserved_at'],minimum_running_memory_bytes=5*2**30)
        a.write_once(stage/incoming/'BOOK_CONTROL.json',research.canonical(control))
        plan=dict(version='moa.book-same-candidate-resume.v1',run_id=binary.RUN,
            authorised_by='explicit_user_macbook_only_same_resume_2026-10-21',prepared_at=at,
            original_plan_sha256=a.sha(base/'PLAN.json'),original_controller_sha256=original_controller,
            original_grant_sha256=a.sha(base/'RESOURCE_RESUME_AUTHORIZATION.json'),
            original_budget_sha256=a.sha(root/'BUDGET.jsonl'),reservation_at=state['training_reserved_at'],
            additional_candidates=0,additional_train_seconds=0,maximum_resume_dispatches=1,
            execution_host='macbookair',execution_device='mps',
            minimum_admission_memory_bytes=grant['minimum_admission_memory_bytes'],
            minimum_running_memory_bytes=grant['minimum_running_memory_bytes'],
            checkpoint_sha256=saved['sha256']['training-state.pt'],carried_seconds=saved['seconds'],
            source_sha256={n:a.sha(a.ROOT/n) for n in (CONTROLLER,SELF,CONTROL,EVALUATOR)},
            stage_sha256={str(x.relative_to(stage)):a.sha(x) for x in stage.rglob('*') if x.is_file()},
            remote_before_sha256=a.sha(stage/'research'/binary.RUN/'TRAIN_RESULT.json'),
            remote_job_sha256=a.sha(stage/incoming/'JOB-r1.json'),
            operational_promotion=False)
        a.write_once(p/'PLAN.json',research.canonical(plan))
        verify(root,plan)  # The overlay explicitly authorises only the archived controller revision.
        return plan


def verify(root, plan):
    root=Path(root);p=folder(root);base=root/binary.FOLDER
    if (plan['run_id']!=binary.RUN or plan['execution_host']!='macbookair' or
            plan['execution_device']!='mps' or plan['additional_candidates']!=0 or
            plan['additional_train_seconds']!=0 or plan['maximum_resume_dispatches']!=1 or
            plan['authorised_by']!='explicit_user_macbook_only_same_resume_2026-10-21' or
            plan['minimum_admission_memory_bytes']<7*2**30 or plan['minimum_running_memory_bytes']<5*2**30):
        raise ValueError('invalid MacBook-only same-candidate transfer')
    for name,expected in plan['source_sha256'].items():
        if a.sha(a.ROOT/name)!=expected:raise ValueError('host transfer implementation changed')
    for name,expected in plan['stage_sha256'].items():
        if a.sha(p/'stage'/name)!=expected:raise ValueError('staged host transfer changed')
    if (a.sha(base/'PLAN.json')!=plan['original_plan_sha256'] or
            a.sha(base/'RESOURCE_RESUME_AUTHORIZATION.json')!=plan['original_grant_sha256'] or
            a.sha(root/'BUDGET.jsonl')!=plan['original_budget_sha256']):
        raise ValueError('original candidate, permission or budget changed')
    state=rc._read_json(base/'STATE.json');old=rc._read_json(base/'PLAN.json')
    binary.verify(root,old,state)
    saved=approved_boundary(root,old,rc._read_json(base/'RESOURCE_RESUME_AUTHORIZATION.json'))
    if plan['checkpoint_sha256']!=saved['sha256']['training-state.pt'] or plan['carried_seconds']!=saved['seconds']:
        raise ValueError('transferred checkpoint/time differs from the approved boundary')
    if bool(state.get('book_dispatch_sha256'))!=(p/'DISPATCH.json').exists():
        raise ValueError('book dispatch history cannot be forgotten')
    if (p/'DISPATCH.json').exists() and a.sha(p/'DISPATCH.json')!=state['book_dispatch_sha256']:
        raise ValueError('book dispatch changed')


def _transport(plan, action, *, raw=None, timeout=60):
    """Fixed existing SSH host; no credentials, shell interpolation or new ports."""
    payload=dict(plan=plan,action=action)
    command='python3 -c '+shlex.quote((a.ROOT/CONTROL).read_text())+' '+shlex.quote(json.dumps(payload))
    argv=['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=8',
          '-l','jungwoohwang','macbookair',command]
    result=subprocess.run(argv,input=raw,capture_output=True,timeout=timeout)
    if result.returncode==255:raise ConnectionError('MacBook SSH unavailable; details private')
    if result.returncode:raise ValueError('MacBook identity/safety action refused; details private')
    return result.stdout


def stage_remote(root=a.DEFAULT):
    root=Path(root);p=folder(root);plan=rc._read_json(p/'PLAN.json');verify(root,plan)
    if not (root/'PAUSED').exists():raise ValueError('staging requires paused coordinator')
    archive=io.BytesIO()
    with tarfile.open(fileobj=archive,mode='w') as tar:
        for name in plan['stage_sha256']:tar.add(p/'stage'/name,arcname=name,recursive=False)
    out=json.loads(_transport(plan,'upload',raw=archive.getvalue(),timeout=180))
    if not out.get('ready'):raise ValueError('MacBook staging attestation failed')
    a.write_once(p/'STAGED.json',research.canonical(out))
    return out


def _snapshot(root, plan):
    try:return json.loads(_transport(plan,'snapshot'))
    except (ConnectionError,subprocess.SubprocessError):return {'online':False}


def _launch(root, plan, action):
    return json.loads(_transport(plan,action))['pid']


def _collect(root, plan):
    """Collect into immutable snapshots; never overwrite the original Mini run."""
    raw=_transport(plan,'collect',timeout=180);p=folder(root)/'collected'
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:') as tar:
        for member in tar:
            name=Path(member.name)
            if (not member.isfile() or name.is_absolute() or '..' in name.parts or
                    name.parts[:2]!=('research',binary.RUN)):
                raise ValueError('unconfined result archive')
            destination=p/name
            data=tar.extractfile(member).read()
            if member.name.endswith('EVALUATION.json'):
                import hashlib
                a.write_once(p/'receipts'/hashlib.sha256(data).hexdigest(),data)
                rc._write_atomic(destination,json.loads(data))
            else:a.write_once(destination,data)
    return p/'research'/binary.RUN


def _complete(root, plan, run):
    base=Path(root)/binary.FOLDER;old=rc._read_json(base/'PLAN.json')
    trained=rc._read_json(run/'TRAIN_RESULT.json');contract=rc._read_json(run/'TRAINING_CONTRACT.json')
    name=Path(trained['checkpoint_name']);checkpoint=run/name
    if (name.is_absolute() or len(name.parts)!=1 or '..' in name.parts or checkpoint.is_symlink() or
            not checkpoint.resolve().is_relative_to(run.resolve()) or
            not {'model.safetensors','training-state.pt'}<=set(trained['sha256']) or
            any(Path(n).is_absolute() or '..' in Path(n).parts or
                not (checkpoint/n).resolve().is_relative_to(checkpoint.resolve()) for n in trained['sha256'])):
        raise ValueError('unconfined or incomplete saved MacBook checkpoint')
    if (trained['run_id']!=binary.RUN or trained['job_sha256']!=plan['remote_job_sha256'] or
            trained['data_sha256']!=old['data_sha256'] or trained['updates']!=old['configuration']['planned_updates'] or
            not plan['carried_seconds']<=trained['seconds']<=old['configuration']['maximum_seconds'] or
            not trained['training_complete'] or trained['status']!='saved' or
            trained.get('resume_from_sha256')!=plan['checkpoint_sha256'] or trained.get('resume_from_updates')!=0 or
            contract['plan']['configuration']!=old['configuration'] or contract.get('option_labels')!=['preserved','changed'] or
            contract.get('semantic_profile')!=binary.PROFILE or contract.get('label_counts')!=old['train_counts'] or
            contract['job_sha256']!=plan['remote_job_sha256'] or contract['data_sha256']!=old['data_sha256'] or
            a.sha(run/'TRAINING_CONTRACT.json')!=trained['training_contract_sha256'] or
            any(a.sha(checkpoint/n)!=digest for n,digest in trained['sha256'].items())):
        raise ValueError('MacBook saved candidate identity/limits/checkpoint mismatch')
    return trained


def advance(root=a.DEFAULT, *, at=None):
    root=Path(root);p=folder(root);base=root/binary.FOLDER;at=at or a.now()
    if (root/'PAUSED').exists():return {'status':'PAUSED_BY_OPERATOR'}
    with (base/'.controller.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {'status':'OBSERVE_ONLY','run_id':binary.RUN}
        state=rc._read_json(base/'STATE.json')
        if state['status'] in binary.TERMINAL:return state
        def save(status,**values):
            state.update(status=status,execution_host='macbookair',**values)
            rc._write_atomic(base/'STATE.json',state);return state
        try:
            plan=rc._read_json(p/'PLAN.json');verify(root,plan)
            if rc.parse_time(at).date()!=rc.parse_time(plan['reservation_at']).date():
                raise ValueError('host resume cannot leave the original KST reservation day')
            snap=_snapshot(root,plan)
            if not snap.get('online'):return save('WAITING_BOOK_ONLINE')
            if not snap.get('ready'):return save('WAITING_BOOK_STAGE')
            if snap.get('running'):return {'status':'OBSERVE_BOOK','run_id':binary.RUN,'execution_host':'macbookair'}
            if snap.get('busy'):return save('WAITING_BOOK_DEVICE')
            if not (p/'DISPATCH.json').exists():
                res=snap['resources']
                if not res['train_allowed'] or res['available_memory_bytes']<plan['minimum_admission_memory_bytes']:
                    return save('WAITING_BOOK_RESOURCE',available_memory_bytes=res['available_memory_bytes'],
                                minimum_admission_memory_bytes=plan['minimum_admission_memory_bytes'])
                a.write_once(base/'RESOURCE_RESUME_DISPATCH.json',research.canonical(dict(run_id=binary.RUN,
                    authorisation_sha256=state['resource_resume_sha256'],dispatched_at=at,resume=True)))
                a.write_once(p/'DISPATCH.json',research.canonical(dict(run_id=binary.RUN,
                    transfer_plan_sha256=a.sha(p/'PLAN.json'),execution_host='macbookair',dispatched_at=at,resume=True)))
                save('TRAINING_ON_BOOK',resource_resume_dispatched_at=at,
                    resource_resume_dispatch_sha256=a.sha(base/'RESOURCE_RESUME_DISPATCH.json'),
                    book_dispatch_sha256=a.sha(p/'DISPATCH.json'))
                return save('TRAINING_ON_BOOK',book_pid=_launch(root,plan,'train'))
            trained=snap.get('result')
            if not trained or trained.get('job_sha256')!=plan['remote_job_sha256']:
                age=(rc.parse_time(at)-rc.parse_time(state['resource_resume_dispatched_at'])).total_seconds()
                if 0<=age<300:return {'status':'BOOK_DISPATCH_SETTLING','run_id':binary.RUN}
                raise ValueError('issued MacBook resume has no new saved boundary; do not retry')
            if trained.get('stop_reason') in ('resource_guard','pause_requested') or not trained.get('training_complete'):
                raise ValueError('single MacBook resume stopped; no further training dispatch')
            run=_collect(root,plan);trained=_complete(root,plan,run)
            evaluation=run/'EVALUATION.json'
            if evaluation.exists():
                evaluated=rc._read_json(evaluation)
                if (evaluated['model_sha256']!=trained['sha256']['model.safetensors'] or
                        evaluated['request_sha256']!=rc._read_json(base/'PLAN.json')['input_sha256']['BINARY_REQUEST.json'] or
                        not 0<=evaluated['seconds']<=600 or evaluated.get('separate_process') is not True):
                    raise ValueError('MacBook evaluation identity/budget changed')
                if evaluated['status']=='complete':
                    request=rc._read_json(base/'BINARY_REQUEST.json')
                    if binary.evaluation_inputs(request,evaluated['records']):raise ValueError('evaluation incomplete')
                    gold=rc._read_json(base/'GOLD.json')
                    answers=[dict(r,expected=gold[r['case_id']]) for r in evaluated['records']]
                    old=rc._read_json(base/'PLAN.json')
                    result=binary.comparison(rc._read_json(base/'BASELINES.json'),
                        [r for r in answers if r['mode']=='binary'],[r for r in answers if r['mode']=='binary_reverse'],
                        train_counts=old['train_counts'],rule_excluded_dev=rc._read_json(base/'DATA_AUDIT.json')['dev']['rule_excluded'])
                    result.update(updates=trained['updates'],train_seconds=trained['seconds'],
                        checkpoint_sha256=trained['sha256']['model.safetensors'],separate_reload=True,
                        execution_host='macbookair',execution_device='mps',host_migration=True,
                        evaluation_seconds=evaluated['seconds'])
                    a.write_once(base/'COMPARISON.json',research.canonical(result))
                    lines=['# MOA 동일 이진 후보 — 맥북 재개 결과','','기존 노출 합성 dev54 결과이며 블라인드·실물 성적이 아닙니다.','',
                           '| 모델 | 정답률 | 정상 오탐률 | 오류 검출률 |','|---|---:|---:|---:|']
                    for name,value in result['models'].items():
                        m=value['average'];lines.append('| %s | %.1f%% | %.1f%% | %.1f%% |'%(name,
                            100*m['accuracy'],100*m['normal_false_positive_rate'],100*(1-m['error_miss_rate'])))
                    lines+=['','근거 규칙은 모델 성적에서 제외. 실행 장치 이전 포함. 운영 적용·자동 승인·공개 없음.']
                    a.write_once(base/'RESULT.md',('\n'.join(lines)+'\n').encode())
                    return save('COMPLETE',comparison_sha256=a.sha(base/'COMPARISON.json'),completed_at=at)
                if evaluated['status']!='waiting_resources':raise ValueError('evaluation stopped; budget not reset')
            if not snap['resources']['verify_allowed']:return save('WAITING_BOOK_EVAL_RESOURCE')
            boundary=a.sha(evaluation) if evaluation.exists() else 'initial'
            used=state.get('book_evaluation_boundaries',[])
            if boundary in used or len(used)>=4:raise ValueError('book evaluation boundary repeated/limit')
            a.write_once(p/('EVAL_DISPATCH-'+str(len(used))+'.json'),research.canonical(dict(boundary=boundary,at=at)))
            save('EVALUATING_ON_BOOK',book_evaluation_boundaries=used+[boundary])
            return save('EVALUATING_ON_BOOK',book_eval_pid=_launch(root,plan,'evaluate'))
        except (ValueError,KeyError,OSError,subprocess.SubprocessError) as exc:
            return save('RECONCILIATION_REQUIRED',reason=(str(exc) if isinstance(exc,ValueError) else type(exc).__name__))
