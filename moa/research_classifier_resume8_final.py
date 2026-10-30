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
from . import research_runner as rr, research_classifier_resume as zero
from . import research_classifier_resume8_guarded as unissued

FOLDER = 'eight-update-final-20261022'
VERSION = 'moa.fixed-classifier-explicit-eight-guarded.v2'
HELPER = 'scripts/moa-research-book-classifier-resume8-final.py'
SOURCES = {'worker.py': HELPER,
    'moa/research_gateway_hold_numeric.py': 'moa/research_gateway_hold_numeric.py',
    'moa/research_classifier_resume8_state.py': 'moa/research_classifier_resume8_state.py',
    'tests/test_research_classifier_resume8_torch.py': 'tests/test_research_classifier_resume8_torch.py'}
GATEWAYS = {
    'ai.openclaw.gateway': 'c9a8402b54e092331f281993a4f6c5bc367572ea6311db072a68ee84b7889f19',
    'ai.openclaw.h19h19': 'cf0945ddb483d7080eee0d1f85bf40b81c08d7ed216f7c2655441b044bd4c4fc'}
ORIGINALS = ('PLAN.json', 'STATE.json', 'CANDIDATE_ALLOWANCE.json', 'SLOT_CONSUMED.json',
             'DISPATCH.json', 'RESERVATION.json', 'TRAIN_STOP_RESULT.json')


PREVIOUS = ('AUTHORIZATION.json', 'STATE.json', 'DISPATCH.json', 'TRAIN_STOP_RESULT.json', 'BOOK_UNIT.json')


UNISSUED_FILES = ('AUTHORIZATION.json', 'STATE.json', 'REVIEW_BLOCKER.json')


def _blocked_unissued(root, at):
    _, old_auth, old_state = unissued._verify(root, at)
    q = root / control.FOLDER / unissued.FOLDER
    receipt = rc._read_json(q / 'REVIEW_BLOCKER.json')
    if (old_state.get('status') != 'RECONCILIATION_REQUIRED' or
            old_state.get('reason') != 'numeric_health_preflight_blocked_before_dispatch' or
            old_state.get('review_blocker_sha256') != a.sha(q / 'REVIEW_BLOCKER.json') or
            receipt.get('reason') != 'numeric_health_preflight_blocked_before_dispatch' or
            receipt.get('authorization_sha256') != a.sha(q / 'AUTHORIZATION.json') or
            receipt.get('unissued_book_sha256') != {'authorization': a.sha(q / 'AUTHORIZATION.json')} or
            any((q / n).exists() for n in ('DISPATCH.json', 'EVAL_DISPATCH.json'))):
        raise ValueError('only the never-dispatched review-blocked seal may be superseded')
    return {name:a.sha(q / name) for name in UNISSUED_FILES}


def block_unissued(root=a.DEFAULT, *, at=None):
    """Fail-closed an unissued implementation; never cancel or reset a dispatch."""
    root, at = Path(root), at or a.now()
    q = root / control.FOLDER / unissued.FOLDER
    with control._lock(root) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if any((q / n).exists() for n in ('DISPATCH.json', 'EVAL_DISPATCH.json')):
            raise ValueError('cannot supersede any issued implementation')
        if (q / 'REVIEW_BLOCKER.json').exists():
            return _blocked_unissued(root, at)
        plan, _, state = unissued._verify(root, at)
        if state['status'] not in ('AUTHORIZED_NOT_DISPATCHED', 'WAITING_BOOK_RESOURCE'):
            raise ValueError('review block requires the untouched unissued state')
        # probe refuses any remote training/evaluation dispatch or service journal.
        probe = _remote('probe', plan)
        if probe['unissued_book_sha256'] != {'authorization': a.sha(q / 'AUTHORIZATION.json')}:
            raise ValueError('unissued Book authorization differs from preserved seal')
        receipt = dict(reason='numeric_health_preflight_blocked_before_dispatch', at=at,
            authorization_sha256=a.sha(q / 'AUTHORIZATION.json'),
            previous_state_sha256=a.sha(q / 'STATE.json'),
            unissued_book_sha256=probe['unissued_book_sha256'],
            no_training_or_evaluation_issued=True, no_services_changed=True,
            findings=['numeric loopback endpoint health preflight'])
        a.write_once(q / 'REVIEW_BLOCKER.json', research.canonical(receipt))
        rc._write_atomic(q / 'STATE.json', dict(state, status='RECONCILIATION_REQUIRED',
            reason=receipt['reason'], review_blocker_sha256=a.sha(q / 'REVIEW_BLOCKER.json')))
        return _blocked_unissued(root, at)


def _original(root, at):
    _blocked_unissued(root, at)
    plan, previous_auth, previous_state = zero._verify(root, at)
    p = root / control.FOLDER; previous = p / zero.FOLDER
    stopped = rc._read_json(previous / 'TRAIN_STOP_RESULT.json')
    original = rc._read_json(p / 'TRAIN_STOP_RESULT.json')
    reservation = rc._read_json(p / 'RESERVATION.json')
    if (previous_state['status'] != 'RECONCILIATION_REQUIRED' or previous_state['training_updates'] != 8 or
            previous_state['trained'] or previous_state['evaluated'] or
            not all((previous / name).is_file() for name in PREVIOUS) or
            any((previous / n).exists() for n in ('EVAL_DISPATCH.json', 'EVALUATION.json')) or
            stopped['run_id'] != control.RUN or stopped['status'] != 'stopped' or
            stopped.get('stop_reason') != 'resource_or_reservation_day_guard' or
            stopped['updates'] != 8 or stopped.get('resumed_exact') is not True or
            stopped.get('encoder_unchanged') is not True or stopped.get('system_forced') is not False or
            stopped['checkpoint_name'] != 'checkpoint-0008-final_or_safe_stop' or
            stopped['checkpoint_kind'] != 'final_or_safe_stop' or
            stopped['authorization_sha256'] != previous_state['authorization_sha256'] or
            stopped['job_sha256'] != plan['stage_sha256']['JOB.json'] or
            stopped['execution_host'] != 'macbookair' or stopped['execution_device'] != 'mps' or
            not 0 < stopped['seconds'] < previous_auth['remaining_train_seconds'] or
            stopped['cumulative_train_seconds'] != original['seconds'] + stopped['seconds'] or
            not 0 < stopped['cumulative_train_seconds'] < 1780):
        raise ValueError('only the explicitly authorised known eight-update checkpoint may continue')
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
            auth['prior_train_seconds'] != stopped['cumulative_train_seconds'] or
            auth['remaining_train_seconds'] != 1800 - stopped['cumulative_train_seconds'] or
            auth['evaluation_maximum_seconds'] != 600 or auth['prior_updates'] != 8 or auth['gateways'] != GATEWAYS or
            any(a.sha(p / zero.FOLDER / n) != digest for n, digest in auth['previous_sha256'].items()) or
            auth['unissued_sha256'] != _blocked_unissued(root, at) or
            auth['admission_memory_bytes'] != 7 * 2**30 or auth['running_memory_bytes'] != 5 * 2**30 or
            a.sha(root / 'BUDGET.jsonl') != auth['ledger_sha256'] or
            any(a.sha(p / n) != digest for n, digest in auth['original_sha256'].items()) or
            a.sha(Path(__file__)) != auth['controller_sha256'] or
            any(a.sha(a.ROOT / source) != auth['resume_sha256'][n] for n, source in SOURCES.items())):
        raise ValueError('resume authorization or original history changed')
    return plan, auth, state


def authorize(root=a.DEFAULT, *, at=None):
    """Human-only entry point for the current explicit eight-update continuation."""
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
            raise ValueError('eight-update receipt cannot contain model observations')
        local = dict(stopped)
        if local.pop('records', []) != [] or projected != local:
            raise ValueError('Book saved stop differs from preserved receipt')
        if hashlib.sha256(research.canonical(probe['stop'])).hexdigest() != probe['stop_sha256']:
            raise ValueError('actual Book receipt digest mismatch')
        auth = dict(version=VERSION, run_id=control.RUN, authorised_at=at,
            authority='explicit_user_eight_update_resume_and_two_gateway_restart_hold_2026-10-22',
            execution_host='macbookair', execution_device='mps', maximum_resumes=1,
            additional_candidates=0, additional_train_seconds=0, prior_updates=8,
            prior_train_seconds=stopped['cumulative_train_seconds'],
            remaining_train_seconds=1800-stopped['cumulative_train_seconds'], evaluation_maximum_seconds=600,
            reservation=reservation, original_sha256={n: a.sha(p / n) for n in ORIGINALS},
            previous_sha256={n: a.sha(p / zero.FOLDER / n) for n in PREVIOUS},
            previous_folder=zero.FOLDER, prior_book_sha256=probe['prior_book_sha256'],
            unissued_sha256=_blocked_unissued(root, at), unissued_book_sha256=probe['unissued_book_sha256'],
            original_book_stop_sha256=rc._read_json(p / zero.FOLDER / 'AUTHORIZATION.json')['book_stop_sha256'],
            zero_checkpoint_sha256=rc._read_json(p / zero.FOLDER / 'AUTHORIZATION.json')['checkpoint_sha256'],
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
            additional_train_seconds=0, training_updates=8, trained=False, evaluated=False))
        return auth


def _stage(plan, auth, q):
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w') as tar:
        for name, source in SOURCES.items(): tar.add(a.ROOT / source, arcname=name, recursive=False)
        tar.add(q / 'AUTHORIZATION.json', arcname='AUTHORIZATION.json', recursive=False)
    _remote('stage', plan, auth, raw=archive.getvalue())
    unit = _remote('selftest', plan, auth)
    if unit.get('returncode') != 0 or unit.get('tests') != 5:
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
                if (not snap['gateways_stopped'] or not snap.get('gateways_disabled') or not snap['train_admission'] or snap['busy'] or snap['running'] or
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
                if not snap['evaluation_admission'] or not snap['gateways_stopped'] or not snap.get('gateways_disabled'):
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



def hold(root=a.DEFAULT, *, at=None):
    root, at = Path(root), at or a.now()
    q = root / control.FOLDER / FOLDER
    with control._lock(root) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        plan, auth, _ = _verify(root, at)
        if (q / 'DISPATCH.json').exists(): raise ValueError('hold cannot rearm a consumed continuation')
        if not (q / 'BOOK_UNIT.json').exists(): _stage(plan, auth, q)
        return _remote('pause_gateways', plan, auth, timeout=60)


def restore(root=a.DEFAULT):
    # Recovery must remain possible after the reservation date or a safety stop.
    root = Path(root); p = root / control.FOLDER
    return _remote('restore_gateways', rc._read_json(p / 'PLAN.json'),
                   rc._read_json(p / FOLDER / 'AUTHORIZATION.json'), timeout=60)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('block_unissued', 'authorize', 'hold', 'advance', 'restore', 'status'))
    args = parser.parse_args()
    out = (rc._read_json(a.DEFAULT / control.FOLDER / FOLDER / 'STATE.json') if args.action == 'status'
           else globals()[args.action]())
    if args.action == 'authorize':
        out = {k: out[k] for k in ('run_id', 'prior_updates', 'maximum_resumes', 'additional_candidates',
               'additional_train_seconds', 'remaining_train_seconds', 'evaluation_maximum_seconds')}
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == '__main__': main()
