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
    if state.get('alignment_enabled'):
        from .research_alignment import advance as alignment_advance
        return alignment_advance(root)
    if state.get('status') == 'RESEARCH_PLATEAU':
        return {'status': 'RESEARCH_PLATEAU', 'reason': state.get('plateau_reason')}
    run_id = state.get('active_run')
    if not run_id:
        return _maybe_start(root, state)
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
    if result.get('stop_reason') == 'resource_guard' or result.get('status') != 'saved':
        return _resume_if_allowed(root, state, run_id, result)
    if not remote.get('reload'):
        payload = rr.ssh_capture(rr.remote_argv(rr.DEFAULT_HOST, ['reload', '--job', 'JOB.json'],
                                                run_id=run_id), timeout=1800)
        _log(root, {'event': 'reloaded', 'run_id': run_id, 'model': payload.get('model_sha256')})
        return {'status': 'CHECKPOINT_SAVED', 'run_id': run_id, 'reload': payload}
    return {'status': 'DEV_EVALUATED', 'run_id': run_id, 'model': remote.get('reload')}


def _resources():
    """Read the installed runtime's own guard; never reimplement or relax it."""
    script = ('import json,sys;sys.path.insert(0,str(__import__("pathlib").Path.home()/'
              '"Library/Application Support/MOA-LAYA/app"));'
              'from workers.laya_local import runtime;print(json.dumps(runtime.resources()))')
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
                             'jungwoohwang@192.168.1.71',
                             _quote(REMOTE_BASE + '/venv-smoke/bin/python') + ' -c ' + _quote(script)],
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        return None
    lines = [line for line in result.stdout.strip().splitlines() if line.startswith('{')]
    return json.loads(lines[-1]) if lines else None


def _next_run_id(state):
    """Monotonic per-campaign run ids so a resumed loop never reuses a folder."""
    done = state.get('runs', [])
    return '%s-r%02d' % (state.get('campaign_id', 'moa-research'), len(done) + 1)


MAX_RESUME_ATTEMPTS = 4


def _resume_if_allowed(root, state, run_id, result):
    """Resume a resource-parked run at its saved boundary, but never loop forever."""
    reason = result.get('stop_reason')
    if reason != 'resource_guard':
        return {'status': 'PARKED', 'run_id': run_id, 'stop_reason': reason,
                'updates': result.get('updates'),
                'note': 'unknown stop reasons require manual inspection'}
    attempts = state.setdefault('resume_attempts', {}).get(run_id, 0)
    if attempts >= MAX_RESUME_ATTEMPTS:
        state['status'] = 'RESEARCH_BLOCKED_RESOURCE'
        rc._write_atomic(root / 'STATE.json', state)
        return {'status': 'RESEARCH_BLOCKED_RESOURCE', 'run_id': run_id,
                'attempts': attempts,
                'note': 'the MacBook never held enough headroom for a full candidate'}
    resources = _resources()
    if resources is None:
        return {'status': 'WAITING_WORKER', 'run_id': run_id}
    if not resources.get('train_allowed'):
        return {'status': 'WAITING_RESOURCE', 'run_id': run_id,
                'updates': result.get('updates'), 'resources': resources}
    remote = ('cd "%s/incoming/%s" && "%s/venv-smoke/bin/python" moa-research-worker.py '
              'train --job JOB.json --resume' % (REMOTE_BASE, run_id, REMOTE_BASE))
    subprocess.run(['ssh', '-o', 'BatchMode=yes', 'jungwoohwang@192.168.1.71',
                    'nohup sh -c ' + _quote(remote) + ' > /tmp/' + run_id + '-resume.log 2>&1 &'],
                   check=True, timeout=120)
    state['resume_attempts'][run_id] = attempts + 1
    state['status'] = 'TRAINING'
    rc._write_atomic(root / 'STATE.json', state)
    _log(root, {'event': 'training_resumed', 'run_id': run_id, 'attempt': attempts + 1,
                'from_updates': result.get('updates')})
    return {'status': 'TRAINING_RESUMED', 'run_id': run_id, 'attempt': attempts + 1,
            'from_updates': result.get('updates')}


def _maybe_start(root, state):
    """Publish and launch one bounded experiment when every safety gate allows it."""
    policy = state.get('policy_snapshot') or {}
    ledger = rc.BudgetLedger(root / 'BUDGET.jsonl')
    usage = ledger.usage(now=rc.parse_time(_now()), policy=policy or rc._abs_policy())
    if usage['remaining_candidates'] <= 0:
        return {'status': 'CAMPAIGN_BUDGET_EXHAUSTED', 'usage': usage}
    if usage['remaining_today_seconds'] < 600 or usage['remaining_rolling_seconds'] < 600:
        return {'status': 'CAMPAIGN_BUDGET_EXHAUSTED', 'usage': usage}
    hypothesis = rc.ExperimentSelector().next_hypothesis(state.get('hypotheses', []))
    if hypothesis == 'RESEARCH_PLATEAU':
        return {'status': 'RESEARCH_PLATEAU',
                'reason': 'three consecutive rejected experiments; new independent data required'}
    resources = _resources()
    if resources is None:
        return {'status': 'WAITING_WORKER'}
    if not resources.get('train_allowed'):
        return {'status': 'WAITING_RESOURCE', 'resources': resources,
                'note': 'the MacBook guard requires AC power and 15 idle minutes'}
    run_id = _next_run_id(state)
    plan = {'hypothesis': hypothesis, 'head_lr': 0.001, 'max_updates': 120, 'epochs': 4}
    publish = subprocess.run([sys.executable, str(ROOT / 'scripts/moa-research-publish.py'),
                              '--run-id', run_id, '--head-lr', str(plan['head_lr']),
                              '--max-updates', str(plan['max_updates']),
                              '--epochs', str(plan['epochs'])],
                             capture_output=True, text=True, timeout=600)
    if publish.returncode:
        return {'status': 'PUBLISH_FAILED', 'error': publish.stderr.strip()[-400:]}
    remote = ('cd "%s/incoming/%s" && "%s/venv-smoke/bin/python" moa-research-worker.py train --job JOB.json'
              % (REMOTE_BASE, run_id, REMOTE_BASE))
    subprocess.run(['ssh', '-o', 'BatchMode=yes', 'jungwoohwang@192.168.1.71',
                    'nohup sh -c ' + _quote(remote) + ' > /tmp/' + run_id + '.log 2>&1 &'],
                   check=True, timeout=120)
    state['active_run'] = run_id
    state.setdefault('runs', []).append({'run_id': run_id, 'hypothesis': hypothesis,
                                         'started_at': _now(), 'plan': plan})
    state['status'] = 'TRAINING'
    rc._write_atomic(root / 'STATE.json', state)
    ledger.reserve(now=rc.parse_time(_now()), policy=policy, candidate=run_id,
                   train_seconds=plan['max_updates'] * 6)
    _log(root, {'event': 'training_started', 'run_id': run_id, 'hypothesis': hypothesis})
    return {'status': 'TRAINING', 'run_id': run_id, 'hypothesis': hypothesis, 'plan': plan}
