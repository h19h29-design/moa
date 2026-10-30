"""One human-authorised continuation, preserving the consumed slot and time.

The pinned original controller, inputs, result, checkpoint, policy and ledger
are not edited. This separate overlay has one durable dispatch and a separate
Book-only reload/evaluation. No grant/refund/reserve or automatic retry path.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import io
import json
from pathlib import Path
import shlex
import subprocess
import tarfile

from . import research_alignment as a, research_campaign as rc, r2_research as research
from . import research_classifier_control as control, research_classifier_slot as slot
from . import research_runner as rr

FOLDER = 'two-gateway-resume-20261022'
VERSION = 'moa.fixed-classifier-explicit-zero-resume.v1'
HELPER = 'scripts/moa-research-book-classifier-resume.py'
SOURCES = {'worker.py': HELPER,
    'moa/research_classifier_resume_state.py': 'moa/research_classifier_resume_state.py',
    'tests/test_research_classifier_resume_torch.py': 'tests/test_research_classifier_resume_torch.py'}
GATEWAYS = {
    'ai.openclaw.gateway': 'c9a8402b54e092331f281993a4f6c5bc367572ea6311db072a68ee84b7889f19',
    'ai.openclaw.h19h19': 'cf0945ddb483d7080eee0d1f85bf40b81c08d7ed216f7c2655441b044bd4c4fc'}
ORIGINALS = ('PLAN.json', 'STATE.json', 'CANDIDATE_ALLOWANCE.json', 'SLOT_CONSUMED.json',
             'DISPATCH.json', 'RESERVATION.json', 'TRAIN_STOP_RESULT.json')


def _original(root, at):
    p = root / control.FOLDER
    plan = rc._read_json(p / 'PLAN.json')
    control.verify(root, plan); slot._verify_allowance(root)
    state = rc._read_json(p / 'STATE.json')
    reservation = rc._read_json(p / 'RESERVATION.json')
    charges = [e for e in rc.BudgetLedger(root / 'BUDGET.jsonl').entries()
               if e.get('candidate') == control.RUN]
    kst = dt.timezone(dt.timedelta(hours=9))
    if (state['status'] != 'RECONCILIATION_REQUIRED' or not state['training_issued'] or
            state['training_updates'] != 0 or state['trained'] or state['evaluated'] or
            not (root / 'PAUSED').is_file() or charges != [reservation] or
            reservation['kind'] != 'reservation' or reservation['train_seconds'] != 1800 or
            rc.parse_time(at).astimezone(kst).date() != rc.parse_time(reservation['at']).astimezone(kst).date() or
            any((p / n).exists() for n in ('EVAL_DISPATCH.json', 'EVALUATION.json')) or
            not all((p / n).is_file() for n in ORIGINALS)):
        raise ValueError('same-day, charged, stopped candidate with no evaluation required')
    stopped = rc._read_json(p / 'TRAIN_STOP_RESULT.json')
    if (stopped['run_id'] != control.RUN or stopped['status'] != 'stopped' or
            stopped.get('stop_reason') != 'resource_or_reservation_day_guard' or
            stopped['updates'] != 0 or not 0 < stopped['seconds'] < 1780 or
            stopped['job_sha256'] != plan['stage_sha256']['JOB.json'] or
            stopped['execution_host'] != 'macbookair' or stopped['execution_device'] != 'mps' or
            stopped['encoder_unchanged'] is not True or stopped['system_forced'] is not False or
            stopped['checkpoint_name'] != 'checkpoint-0000-final_or_safe_stop' or
            stopped['checkpoint_kind'] != 'final_or_safe_stop'):
        raise ValueError('only the explicitly authorised known zero-update checkpoint may continue')
    return plan, stopped, reservation


def _remote(action, plan, auth=None, *, raw=None, timeout=60):
    payload = dict(action=action, run_id=control.RUN, stage_sha256=plan['stage_sha256'], folder=FOLDER)
    if auth is not None:
        payload.update(authorization_sha256=hashlib.sha256(research.canonical(auth)).hexdigest(),
                       resume_sha256=auth['resume_sha256'])
    command = shlex.quote(rr.REMOTE_ROOT + '/venv-smoke/bin/python') + ' -c ' + shlex.quote(
        (a.ROOT / HELPER).read_text()) + ' ' + shlex.quote(json.dumps(payload))
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
        '-o', 'ConnectTimeout=8', '-l', 'jungwoohwang', 'macbookair', command], input=raw,
        capture_output=True, timeout=timeout)
    if result.returncode or len(result.stdout) > 2 * 2**20:
        raise ValueError('Book resume action incomplete; never redispatch')
    return json.loads(result.stdout)


def _verify(root, at):
    p = root / control.FOLDER; q = p / FOLDER
    plan, stopped, reservation = _original(root, at)
    auth = rc._read_json(q / 'AUTHORIZATION.json')
    state = rc._read_json(q / 'STATE.json')
    if (a.sha(q / 'AUTHORIZATION.json') != state['authorization_sha256'] or
            auth['version'] != VERSION or auth['run_id'] != control.RUN or
            auth['execution_host'] != 'macbookair' or auth['maximum_resumes'] != 1 or
            auth['additional_candidates'] != 0 or auth['additional_train_seconds'] != 0 or
            auth['prior_train_seconds'] != stopped['seconds'] or
            auth['remaining_train_seconds'] != 1800 - stopped['seconds'] or
            auth['evaluation_maximum_seconds'] != 600 or auth['gateways'] != GATEWAYS or
            auth['admission_memory_bytes'] != 7 * 2**30 or auth['running_memory_bytes'] != 5 * 2**30 or
            a.sha(root / 'BUDGET.jsonl') != auth['ledger_sha256'] or
            any(a.sha(p / n) != digest for n, digest in auth['original_sha256'].items()) or
            a.sha(Path(__file__)) != auth['controller_sha256'] or
            any(a.sha(a.ROOT / source) != auth['resume_sha256'][n] for n, source in SOURCES.items())):
        raise ValueError('resume authorization or original history changed')
    return plan, auth, state


def authorize(root=a.DEFAULT, *, at=None):
    """Human-only entry point for the current explicit two-gateway resume request."""
    root, at = Path(root), at or a.now()
    p = root / control.FOLDER; q = p / FOLDER
    with control._lock(root) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (q / 'DISPATCH.json').exists(): raise ValueError('explicit resume is already consumed')
        if (q / 'AUTHORIZATION.json').exists(): return _verify(root, at)[1]
        plan, stopped, reservation = _original(root, at)
        probe = _remote('probe', plan)
        # The archived Mini receipt deliberately omits the empty private-record
        # field. Accept only that precise projection, never missing/different facts.
        projected = dict(probe['stop'])
        if projected.pop('records', []) != []:
            raise ValueError('zero-update receipt cannot contain model observations')
        local = dict(stopped)
        if local.pop('records', []) != [] or projected != local:
            raise ValueError('Book saved stop differs from preserved receipt')
        if hashlib.sha256(research.canonical(probe['stop'])).hexdigest() != probe['stop_sha256']:
            raise ValueError('actual Book receipt digest mismatch')
        auth = dict(version=VERSION, run_id=control.RUN, authorised_at=at,
            authority='explicit_user_two_macbook_gateways_one_same_classifier_resume_2026-10-22',
            execution_host='macbookair', execution_device='mps', maximum_resumes=1,
            additional_candidates=0, additional_train_seconds=0, prior_train_seconds=stopped['seconds'],
            remaining_train_seconds=1800-stopped['seconds'], evaluation_maximum_seconds=600,
            reservation=reservation, original_sha256={n: a.sha(p / n) for n in ORIGINALS},
            ledger_sha256=a.sha(root / 'BUDGET.jsonl'), controller_sha256=a.sha(Path(__file__)),
            resume_sha256={n: a.sha(a.ROOT / source) for n, source in SOURCES.items()},
            original_stage_sha256=plan['stage_sha256'], checkpoint_name=stopped['checkpoint_name'],
            checkpoint_sha256={'head.pt': stopped['head_sha256'],
                'training-state.pt': stopped['training_state_sha256'], 'CLASSIFIER.json': probe['classifier_sha256']},
            original_stop=probe['stop'], book_stop_sha256=probe['stop_sha256'],
            gateways=GATEWAYS, admission_memory_bytes=7*2**30,
            running_memory_bytes=5*2**30, operational_promotion=False)
        a.write_once(q / 'AUTHORIZATION.json', research.canonical(auth))
        rc._write_atomic(q / 'STATE.json', dict(status='AUTHORIZED_NOT_DISPATCHED', run_id=control.RUN,
            authorization_sha256=a.sha(q / 'AUTHORIZATION.json'), additional_candidates=0,
            additional_train_seconds=0, training_updates=0, trained=False, evaluated=False))
        return auth


def _stage(plan, auth, q):
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w') as tar:
        for name, source in SOURCES.items(): tar.add(a.ROOT / source, arcname=name, recursive=False)
        tar.add(q / 'AUTHORIZATION.json', arcname='AUTHORIZATION.json', recursive=False)
    _remote('stage', plan, auth, raw=archive.getvalue())
    unit = _remote('selftest', plan, auth)
    if unit.get('returncode') != 0 or unit.get('tests') != 3:
        raise ValueError('Book saved-state tensor checks failed')
    a.write_once(q / 'BOOK_UNIT.json', research.canonical(unit))


def advance(root=a.DEFAULT, *, at=None):
    root, at = Path(root), at or a.now()
    p = root / control.FOLDER; q = p / FOLDER
    with control._lock(root) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = rc._read_json(q / 'STATE.json')
        if state['status'] in ('COMPLETE', 'RECONCILIATION_REQUIRED'): return state
        def save(status, **values):
            state.update(status=status, **values); rc._write_atomic(q / 'STATE.json', state)
            return state
        try:
            plan, auth, _ = _verify(root, at)
            if not (q / 'BOOK_UNIT.json').exists(): _stage(plan, auth, q)
            snap = _remote('snapshot', plan, auth)
            if not (q / 'DISPATCH.json').exists():
                if snap.get('remote_dispatch'):
                    raise ValueError('remote dispatch without local marker must not be retried')
                if (not snap['gateways_stopped'] or not snap['train_admission'] or snap['busy'] or snap['running'] or
                        snap['resources']['available_memory_bytes'] < 7 * 2**30):
                    return save('WAITING_BOOK_RESOURCE', resources=snap['resources'])
                a.write_once(q / 'DISPATCH.json', research.canonical(dict(at=at, run_id=control.RUN,
                    authorization_sha256=state['authorization_sha256'], maximum_resumes=1)))
                try:
                    launch = _remote('launch_resume', plan, auth)
                    return save('RESUME_ISSUED', pid=launch['pid'])
                except Exception:
                    return save('RECONCILIATION_REQUIRED', reason='uncertain_single_resume_dispatch')
            if snap['busy'] or snap['running']:
                return save('OBSERVING_BOOK', progress=snap.get('progress'))
            trained = snap.get('train_result')
            if (not trained or trained.get('status') != 'saved' or trained.get('updates') != 56 or
                    trained.get('encoder_unchanged') is not True or trained.get('resumed_exact') is not True or
                    trained.get('job_sha256') != plan['stage_sha256']['JOB.json'] or
                    trained.get('authorization_sha256') != state['authorization_sha256'] or
                    trained.get('execution_device') != 'mps' or trained.get('execution_host') != 'macbookair' or
                    not 0 < trained.get('seconds', 0) <= auth['remaining_train_seconds'] or
                    trained.get('cumulative_train_seconds') != auth['prior_train_seconds'] + trained['seconds']):
                return save('RECONCILIATION_REQUIRED', reason='new_stop_or_incomplete_resume_no_second_resume',
                            training_updates=trained.get('updates', 0) if trained else 0,
                            stop_reason=trained.get('stop_reason', trained.get('error_type')) if trained else None)
            a.write_once(q / 'TRAIN_RESULT.json', research.canonical(trained))
            if not (q / 'EVAL_DISPATCH.json').exists():
                if snap.get('evaluation_dispatch'): raise ValueError('uncertain evaluation dispatch')
                if not snap['evaluation_admission'] or not snap['gateways_stopped']:
                    return save('WAITING_BOOK_EVALUATION_RESOURCE', trained=True, training_updates=56)
                a.write_once(q / 'EVAL_DISPATCH.json', research.canonical(dict(at=at, maximum_seconds=600)))
                try:
                    launch = _remote('launch_evaluate', plan, auth)
                    return save('EVALUATION_ISSUED', pid=launch['pid'], trained=True, training_updates=56)
                except Exception:
                    return save('RECONCILIATION_REQUIRED', reason='uncertain_single_evaluation_dispatch')
            evaluated = snap.get('evaluation')
            if (not evaluated or evaluated.get('status') != 'complete' or evaluated.get('reload_exact') is not True or
                    evaluated.get('head_sha256') != trained['head_sha256'] or
                    evaluated.get('authorization_sha256') != state['authorization_sha256'] or
                    evaluated.get('execution_device') != 'mps' or evaluated.get('system_forced') is not False or
                    not 0 < evaluated.get('seconds', 0) <= 600):
                return save('RECONCILIATION_REQUIRED', reason='uncompleted_evaluation_no_automatic_retry')
            a.write_once(q / 'EVALUATION.json', research.canonical(evaluated))
            comparison = control.compare(evaluated['records'], rc._read_json(p / 'GOLD.json'),
                rc._read_json(root / plan['baseline_path'])['models'])
            a.write_once(q / 'COMPARISON.json', research.canonical(comparison))
            return save('COMPLETE', trained=True, evaluated=True, training_updates=56,
                        head_sha256=trained['head_sha256'], comparison=comparison,
                        cumulative_train_seconds=trained['cumulative_train_seconds'],
                        evaluation_seconds=evaluated['seconds'])
        except (ValueError, KeyError, OSError, subprocess.SubprocessError):
            return save('RECONCILIATION_REQUIRED', reason='resume_history_or_transport_mismatch')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('authorize', 'advance', 'status'))
    args = parser.parse_args()
    out = (rc._read_json(a.DEFAULT / control.FOLDER / FOLDER / 'STATE.json') if args.action == 'status'
           else globals()[args.action]())
    if args.action == 'authorize':
        out = {k: out[k] for k in ('run_id', 'maximum_resumes', 'additional_candidates',
               'additional_train_seconds', 'remaining_train_seconds', 'evaluation_maximum_seconds')}
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == '__main__': main()
