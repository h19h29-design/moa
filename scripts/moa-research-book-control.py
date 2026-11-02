"""Fixed authenticated-SSH control endpoint; stdlib only, no training on Mini.

This script is passed to the existing MacBook Python over SSH. It accepts only
the pinned run, confined file manifest and fixed worker/evaluation actions.
"""
from __future__ import annotations

import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile

ROOT=Path('/Users/jungwoohwang/Library/Application Support/MOA-LAYA')
RUN='moa-unattended-20261021-binary-train-01'


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(4*1024*1024),b''):h.update(block)
    return h.hexdigest()


def confined(name):
    path=Path(name);destination=ROOT/path
    if (path.is_absolute() or '..' in path.parts or path.parts[:2] not in
            (('incoming',RUN),('research',RUN)) or destination.is_symlink() or
            not destination.resolve().is_relative_to(ROOT.resolve())):
        raise ValueError('private MacBook artifact boundary')
    return destination


def job_context(plan):
    incoming=ROOT/'incoming'/RUN
    job=json.loads((incoming/'JOB-r1.json').read_bytes())
    if job['run_id']!=RUN or job.get('execution_device')!='mps' or sha(incoming/'JOB-r1.json')!=plan['remote_job_sha256']:
        raise ValueError('fixed MacBook execution job required')
    return incoming,job


def snapshot(plan):
    sys.path.insert(0,str(ROOT/'app'))
    from workers.laya_local import runtime
    resources=runtime.resources()
    ready=True
    for name,digest in plan['stage_sha256'].items():
        path=confined(name)
        # TRAIN_RESULT is intentionally mutable only after the one dispatch.
        if name==f'research/{RUN}/TRAIN_RESULT.json' and (ROOT/'research'/RUN/'BOOK_TRAIN_DISPATCH.json').exists():
            continue
        if not path.is_file() or sha(path)!=digest:ready=False
    incoming,job=job_context(plan) if ready else (None,None)
    if job:
        for name,digest in job['protected_models'].items():
            if name not in ('MODEL_REGISTRY.json','MODEL_MANIFEST.json') or sha(ROOT/name)!=digest:
                raise ValueError('protected model registry changed')
        parent=ROOT/job['parent']
        if not parent.resolve().is_relative_to((ROOT/'candidates').resolve()):
            raise ValueError('fixed research parent required')
        for name,digest in job['parent_files'].items():
            path=parent/name
            if not path.resolve().is_relative_to(parent.resolve()) or sha(path)!=digest:
                raise ValueError('original R1 parent changed')
        modules={key:ROOT/'app'/Path(*key.split('.')).with_suffix('.py')
                 for key in job['installed_sha256'] if key.startswith('workers.')}
        installed=list((ROOT/'venv-smoke/lib').glob('python*/site-packages/laya/train.py'))
        if len(installed)!=1:raise ValueError('existing LAYA installation not unambiguous')
        modules['laya.train']=installed[0]
        if set(modules)!=set(job['installed_sha256']) or any(sha(path)!=job['installed_sha256'][name] for name,path in modules.items()):
            raise ValueError('installed MacBook implementation differs')
    busy=False
    with (ROOT/'device.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:busy=True
    running=False
    for line in subprocess.check_output(['ps','-axo','pid=,args='],text=True,errors='replace').splitlines():
        fields=line.strip().split(None,1)
        if len(fields)!=2 or int(fields[0])==os.getpid():continue
        try:args=shlex.split(fields[1])
        except ValueError:continue
        scripts=[x for x in args[1:3] if Path(x).name in ('moa-research-worker.py','moa-research-book-evaluate.py')]
        if scripts and any(RUN in x for x in args):running=True
        elif scripts and any(str(ROOT/'incoming'/RUN) in fields[1] for _ in (0,)):running=True
    run=ROOT/'research'/RUN
    result=json.loads((run/'TRAIN_RESULT.json').read_bytes()) if (run/'TRAIN_RESULT.json').exists() else None
    return dict(online=True,ready=ready,busy=busy,running=running,resources=resources,result=result)


def upload(plan, stream):
    expected=plan['stage_sha256'];seen=set()
    with tarfile.open(fileobj=stream,mode='r|') as tar:
        for member in tar:
            if not member.isfile() or member.name not in expected or member.name in seen:
                raise ValueError('unexpected upload member')
            path=confined(member.name);data=tar.extractfile(member).read()
            if hashlib.sha256(data).hexdigest()!=expected[member.name]:raise ValueError('upload hash mismatch')
            if path.exists():
                if sha(path)!=expected[member.name]:raise ValueError('existing MacBook artifact must be preserved')
            else:
                path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                with path.open('xb') as output:output.write(data);output.flush();os.fsync(output.fileno())
                os.chmod(path,0o600)
            seen.add(member.name)
    if seen!=set(expected):raise ValueError('incomplete upload')
    state=snapshot(plan)
    return dict(ready=state['ready'],run_id=RUN,files=len(seen),
                checkpoint_sha256=plan['checkpoint_sha256'],training_started=False)


def launch(plan, action):
    state=snapshot(plan)
    if not state['ready'] or state['busy'] or state['running']:raise ValueError('existing MacBook worker busy/not ready')
    resource=state['resources'];training=action=='train'
    if not resource['train_allowed' if training else 'verify_allowed'] or resource['available_memory_bytes']<(7 if training else 5)*2**30:
        raise ValueError('MacBook safety admission failed')
    import datetime as dt
    today=dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()
    if today!=dt.datetime.fromisoformat(plan['reservation_at']).date():raise ValueError('original reservation day ended')
    incoming,job=job_context(plan);run=ROOT/'research'/RUN
    with (ROOT/'device.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if training:
            marker=run/'BOOK_TRAIN_DISPATCH.json'
            with marker.open('x') as output:
                json.dump(dict(run_id=RUN,grant=plan['original_grant_sha256'],resume=True),output)
                output.flush();os.fsync(output.fileno())
            os.chmod(marker,0o600)
        executable='moa-research-worker.py' if training else 'moa-research-book-evaluate.py'
        args=([executable,'train','--job',str(incoming/'JOB-r1.json'),'--resume'] if training else
              [executable,'--job',str(incoming/'JOB-r1.json'),'--control',str(incoming/'BOOK_CONTROL.json')])
        env=dict(os.environ,MOA_LAYA_HOME=str(ROOT),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1')
        env.pop('MOA_RESEARCH_HOME',None)
        with (incoming/('BOOK-'+action+'.log')).open('ab') as log:
            process=subprocess.Popen([str(ROOT/'venv-smoke/bin/python'),*args],cwd=incoming,env=env,
                stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True,
                preexec_fn=lambda:os.nice(10))
    return dict(pid=process.pid,execution_host='macbookair',action=action,resume=training)


def collect():
    run=ROOT/'research'/RUN;result=json.loads((run/'TRAIN_RESULT.json').read_bytes())
    cp=confined(f'research/{RUN}/'+result['checkpoint_name'])
    if cp.parent!=run or not cp.is_dir():raise ValueError('invalid checkpoint path')
    files=[run/'TRAIN_RESULT.json',run/'TRAINING_CONTRACT.json',*sorted(cp.rglob('*'))]
    if (run/'EVALUATION.json').exists():files.append(run/'EVALUATION.json')
    with tarfile.open(fileobj=sys.stdout.buffer,mode='w|') as tar:
        for path in files:
            if path.is_symlink():raise ValueError('result symlink refused')
            if path.is_file():tar.add(path,arcname=str(path.relative_to(ROOT)),recursive=False)


def main():
    payload=json.loads(sys.argv[1]);plan=payload['plan'];action=payload['action']
    if plan['run_id']!=RUN or plan['execution_host']!='macbookair' or plan['maximum_resume_dispatches']!=1:
        raise ValueError('fixed MacBook-only candidate required')
    if action=='collect':collect();return
    out=(upload(plan,sys.stdin.buffer) if action=='upload' else snapshot(plan) if action=='snapshot'
         else launch(plan,action) if action in ('train','evaluate') else None)
    if out is None:raise ValueError('unsupported action')
    print(json.dumps(out))


if __name__=='__main__':main()
