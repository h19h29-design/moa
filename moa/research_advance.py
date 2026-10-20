"""Idempotent one-step advance for the unattended research campaign.

Designed to be called by launchd every hour (and manually): each call reads
state, does at most one safe step, writes receipts, and never starts a second
training job while one is running. Unknown states stop for manual review.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

from . import research_campaign as rc
from . import research_runner as rr

CAMPAIGN = Path('/Volumes/KIOXIA-2TB/Cache/MOA/unattended-20261020')
ROOT = Path(__file__).resolve().parents[1]
REMOTE_BASE = '/Users/jungwoohwang/Library/Application Support/MOA-LAYA'


def _now():
    return dt.datetime.now(rc.KST).isoformat(timespec='seconds')


def _log(root, event):
    path = root / 'EVENTS.jsonl'
    record = dict(event)
    record.setdefault('at', _now())
    with path.open('ab') as stream:
        stream.write((json.dumps(record, ensure_ascii=False, sort_keys=True) + '\n').encode())
    import os
    os.chmod(path, 0o600)


def remote_state(run_id):
    """Read-only status of one run folder on the MacBook."""
    script = (
        'import json,pathlib;'
        'r=pathlib.Path.home()/"Library/Application Support/MOA-LAYA/research"/%r;'
        'o={"exists":r.exists()};'
        'o["files"]=sorted(p.name for p in r.iterdir()) if r.exists() else [];'
        'o["result"]=json.loads((r/"TRAIN_RESULT.json").read_text()) if (r/"TRAIN_RESULT.json").exists() else None;'
        'o["reload"]=json.loads((r/"RELOAD.json").read_text())["status"] if (r/"RELOAD.json").exists() else None;'
        'o["progress"]=json.loads((r/"PROGRESS.json").read_text())["updates"] if (r/"PROGRESS.json").exists() else None;'
        'print(json.dumps(o))' % run_id)
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
                             'jungwoohwang@192.168.1.71',
                             'python3 -c ' + _quote(script)],
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        return {'online': False}
    lines = [line for line in result.stdout.strip().splitlines() if line.startswith('{')]
    if not lines:
        return {'online': False}
    payload = json.loads(lines[-1])
    payload['online'] = True
    return payload


def _quote(value):
    import shlex
    return shlex.quote(value)


def worker_running():
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
                             'jungwoohwang@192.168.1.71',
                             'pgrep -f moa-research-worker.py | wc -l'],
                            capture_output=True, text=True, timeout=30)
    try:
        return int(result.stdout.strip() or 0) > 0
    except ValueError:
        return True


def advance(root=CAMPAIGN):
    root = Path(root)
    state_path = root / 'STATE.json'
    if not state_path.exists():
        return {'status': 'NOT_STARTED'}
    if (root / 'PAUSED').exists():
        return {'status': 'PAUSED_BY_OPERATOR'}
    state = json.loads(state_path.read_bytes())
    run_id = state.get('active_run')
    if not run_id:
        return {'status': 'IDLE_NO_ELIGIBLE_EXPERIMENT',
                'reason': 'no run has been published; eligibility and budget are checked by the operator'}
    if worker_running():
        return {'status': 'OBSERVE_ONLY', 'run_id': run_id}
    remote = remote_state(run_id)
    if not remote.get('online'):
        _log(root, {'event': 'worker_offline', 'run_id': run_id})
        return {'status': 'WAITING_WORKER', 'run_id': run_id}
    result = remote.get('result')
    if result is None:
        return {'status': 'TRAINING_INTERRUPTED_OR_PENDING', 'run_id': run_id,
                'progress_updates': remote.get('progress'),
                'note': 'a run folder exists without TRAIN_RESULT; retry only at a known checkpoint boundary'}
    if result.get('status') != 'saved':
        return {'status': 'PARKED', 'run_id': run_id,
                'stop_reason': result.get('stop_reason'), 'updates': result.get('updates')}
    if not remote.get('reload'):
        payload = rr.ssh_capture(rr.remote_argv(rr.DEFAULT_HOST, ['reload', '--job', 'JOB.json'],
                                                run_id=run_id), timeout=1800)
        _log(root, {'event': 'reloaded', 'run_id': run_id, 'model': payload.get('model_sha256')})
        return {'status': 'CHECKPOINT_SAVED', 'run_id': run_id, 'reload': payload}
    return {'status': 'DEV_EVALUATED', 'run_id': run_id, 'model': remote.get('reload')}
