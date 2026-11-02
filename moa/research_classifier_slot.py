"""Explicit one-slot overlay for the already pinned Book classifier.

The original plan/controller/worker remain byte-identical. This overlay can
reserve and issue that one new candidate only. The original controller then
observes it and performs the separate Book reload/evaluation. No time grant,
Mini model execution, retry, policy reset, old candidate or promotion path.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
from pathlib import Path

from . import research_alignment as a, research_campaign as rc
from . import research_classifier_control as control, r2_research as research

VERSION = 'moa.fixed-classifier-one-slot.v1'
AUTHORITY = 'explicit_user_one_extra_fixed_classifier_candidate_2026-10-21'


def _record(root, *, at):
    p = root / control.FOLDER
    before = p / 'SLOT_BUDGET_BEFORE.jsonl'
    rows = rc.BudgetLedger(before).entries()
    plan = rc._read_json(p / 'PLAN.json')
    policy = plan['policy']
    if (plan['run_id'] != control.RUN or plan['execution_host'] != 'macbookair' or
            policy['max_candidate_runs_per_rolling_window'] != 8 or
            plan['admission_memory_bytes'] != 7 * 2**30 or
            plan['running_memory_bytes'] != 5 * 2**30 or
            any(e.get('candidate') == control.RUN for e in rows)):
        raise ValueError('one unissued fixed classifier with unchanged protections required')
    return dict(version=VERSION, run_id=control.RUN, authorised_by=AUTHORITY,
        authorised_at=rc.parse_time(at).isoformat(timespec='seconds'),
        additional_candidates=1, additional_train_seconds=0,
        plan_sha256=a.sha(p / 'PLAN.json'), policy=policy,
        overlay_sha256=a.sha(Path(__file__)), budget_prefix_bytes=before.stat().st_size,
        budget_prefix_sha256=a.sha(before),
        prior_candidates=sorted({e['candidate'] for e in rows}),
        execution_host='macbookair', model_promotion=False)


def _verify_allowance(root):
    p = root / control.FOLDER
    path = p / 'CANDIDATE_ALLOWANCE.json'
    state = rc._read_json(p / 'STATE.json')
    if not path.is_file() or a.sha(path) != state.get('candidate_allowance_sha256'):
        raise ValueError('missing or changed scoped candidate permission')
    record = rc._read_json(path)
    if record != _record(root, at=record['authorised_at']):
        raise ValueError('permission cannot change scope, time, source, plan or policy')
    raw = (root / 'BUDGET.jsonl').read_bytes()
    n = record['budget_prefix_bytes']
    if len(raw) < n or hashlib.sha256(raw[:n]).hexdigest() != record['budget_prefix_sha256']:
        raise ValueError('original budget prefix must remain intact')
    entries = rc.BudgetLedger(root / 'BUDGET.jsonl').entries()
    if any(e['candidate'] not in {*record['prior_candidates'], control.RUN} for e in entries):
        raise ValueError('another candidate was issued after this scoped permission')
    charges = [e for e in entries if e.get('candidate') == control.RUN]
    if len(charges) > 1 or (charges and (charges[0].get('kind') != 'reservation' or
                                       charges[0]['train_seconds'] != control.TRAIN_SECONDS)):
        raise ValueError('one unchanged whole reservation only')
    return record


def execution_policy(root, *, at):
    """Ephemeral cap for this RUN only; never write the campaign policy."""
    root = Path(root)
    record = _verify_allowance(root)
    policy = dict(record['policy'])
    # The before-image excludes this run and retains separately spent old slots.
    active = rc.BudgetLedger(root / control.FOLDER / 'SLOT_BUDGET_BEFORE.jsonl')
    count = active.usage(now=at, policy=policy)['candidates']
    policy['max_candidate_runs_per_rolling_window'] = max(
        policy['max_candidate_runs_per_rolling_window'], count + 1)
    return policy


def grant(root=a.DEFAULT, *, at=None):
    """Operator-only: the explicit human request, never called by advance."""
    root, at = Path(root), at or a.now()
    p = root / control.FOLDER
    with control._lock(root) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        plan = rc._read_json(p / 'PLAN.json'); control.verify(root, plan)
        state = rc._read_json(p / 'STATE.json')
        if (state['status'] in ('COMPLETE', 'RECONCILIATION_REQUIRED') or state.get('training_issued') or
                (p / 'DISPATCH.json').exists() or (p / 'SLOT_CONSUMED.json').exists() or
                any(e.get('candidate') == control.RUN for e in rc.BudgetLedger(root / 'BUDGET.jsonl').entries())):
            raise ValueError('cannot rearm or extend an issued/finished candidate')
        path = p / 'CANDIDATE_ALLOWANCE.json'
        if path.exists(): return _verify_allowance(root)
        if state.get('candidate_allowance_sha256'):
            raise ValueError('permission history cannot be forgotten')
        if not (root / 'PAUSED').is_file():
            raise ValueError('old campaign must remain paused')
        a.write_once(p / 'SLOT_BUDGET_BEFORE.jsonl', (root / 'BUDGET.jsonl').read_bytes())
        record = _record(root, at=at)
        a.write_once(path, research.canonical(record))
        state.update(candidate_allowance_sha256=a.sha(path), additional_candidate_allowance=1,
                     additional_train_seconds=0, authorised_run_id=control.RUN)
        rc._write_atomic(p / 'STATE.json', state)
        return record


def advance(root=a.DEFAULT, *, at=None):
    root, at = Path(root), at or a.now()
    p = root / control.FOLDER
    delegate = False
    with control._lock(root) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = rc._read_json(p / 'STATE.json')
        if state['status'] in ('COMPLETE', 'RECONCILIATION_REQUIRED'): return state
        def save(status, **values):
            state.update(status=status, **values)
            rc._write_atomic(p / 'STATE.json', state)
            return state
        try:
            plan = rc._read_json(p / 'PLAN.json'); control.verify(root, plan)
            if not state.get('candidate_allowance_sha256') and not (p / 'CANDIDATE_ALLOWANCE.json').exists():
                return save('WAITING_CANDIDATE_AUTHORIZATION')
            _verify_allowance(root)
            ledger = rc.BudgetLedger(root / 'BUDGET.jsonl')
            charges = [e for e in ledger.entries() if e.get('candidate') == control.RUN]
            consumed = p / 'SLOT_CONSUMED.json'
            dispatched = p / 'DISPATCH.json'
            if dispatched.exists():
                expected = dict(run_id=control.RUN,
                    allowance_sha256=state['candidate_allowance_sha256'],
                    reservation_sha256=a.sha(p / 'RESERVATION.json'))
                if (not charges or not consumed.exists() or rc._read_json(consumed) != expected or
                        rc._read_json(p / 'RESERVATION.json') != charges[0]):
                    raise ValueError('single dispatch permission and original charge must agree')
                delegate = True
            else:
                if charges or consumed.exists():
                    raise ValueError('charged/consumed without confirmed dispatch: never retry')
                policy = execution_policy(root, at=at)
                gate = control.admission(ledger, policy, at=at)
                state['budget'] = dict(gate, additional_candidate_allowance=1)
                if gate['status'] != 'BUDGET_AVAILABLE': return save('WAITING_BUDGET')
                if not (p / 'INPUT_PREFLIGHT.json').is_file(): return save('WAITING_INPUT_PREFLIGHT')
                if not (root / 'PAUSED').is_file(): return save('WAITING_OLD_COORDINATOR_PAUSE')
                snapshot = control._remote('snapshot', plan)
                res = snapshot['resources']
                if (not snapshot['train_admission'] or snapshot['busy'] or snapshot.get('running') or
                        res['available_memory_bytes'] < plan['admission_memory_bytes'] or
                        not res['ac_power'] or not res['memory_pressure_ok'] or not res['train_allowed'] or
                        res['idle_seconds'] is None or res['idle_seconds'] < 900 or
                        res['disk_free_bytes'] < int(4.5 * 2**30)):
                    return save('WAITING_BOOK_RESOURCE', resources=res)
                charged = ledger.reserve(now=at, policy=policy, candidate=control.RUN,
                                         train_seconds=control.TRAIN_SECONDS)
                a.write_once(p / 'RESERVATION.json', research.canonical(charged))
                a.write_once(consumed, research.canonical(dict(run_id=control.RUN,
                    allowance_sha256=state['candidate_allowance_sha256'],
                    reservation_sha256=a.sha(p / 'RESERVATION.json'))))
                a.write_once(dispatched, research.canonical(dict(at=at, run_id=control.RUN,
                    job_sha256=plan['stage_sha256']['JOB.json'],
                    reservation_sha256=a.sha(p / 'RESERVATION.json'))))
                try:
                    issued = control._remote('launch_train', plan, raw=research.canonical(charged))
                    return save('TRAINING_ISSUED', training_issued=True, pid=issued['pid'])
                except Exception:
                    return save('RECONCILIATION_REQUIRED', reason='uncertain_single_training_dispatch')
        except (ValueError, KeyError, OSError):
            return save('RECONCILIATION_REQUIRED', reason='scoped_permission_or_preserved_inputs_mismatch')
    if delegate:
        return control.advance(root, at=at)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('grant', 'advance', 'status'))
    args = parser.parse_args()
    result = (rc._read_json(a.DEFAULT / control.FOLDER / 'STATE.json') if args.action == 'status'
              else globals()[args.action]())
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == '__main__': main()
