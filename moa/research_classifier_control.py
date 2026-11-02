"""One fixed binary classifier, Book-only, using the unchanged campaign ledger.

This manual candidate is isolated from completed/pinned controllers. No slot
grant, budget reset, Mini inference, automatic promotion or background loop.
"""
from __future__ import annotations

import argparse
from collections import Counter
import datetime as dt
import fcntl
import io
import json
from pathlib import Path
import shlex
import subprocess
import tarfile

from . import research_campaign as rc, research_alignment as a, research_binary_control as old
from . import research_classifier as classifier, research_book_results as saved
from . import r2_research as research, research_runner as rr

RUN = 'moa-unattended-20261021-fixed-classifier-01'
FOLDER = 'fixed-classifier'
TRAIN_SECONDS = 1800
HELPER = 'scripts/moa-research-book-classifier.py'
SOURCES = ('moa/research_classifier.py', 'moa/research_classifier_control.py', HELPER,
           'tests/test_research_classifier_torch.py')


def admission(ledger, policy, *, at):
    """Read-only check; old single-candidate allowances never apply to this run."""
    def permitted(t):
        v = ledger.usage(now=t, policy=policy)
        return (v['remaining_candidates'] > 0 and v['remaining_today_seconds'] >= TRAIN_SECONDS and
                v['remaining_rolling_seconds'] >= TRAIN_SECONDS and
                policy['max_train_time_per_candidate_seconds'] >= TRAIN_SECONDS)
    now = rc.parse_time(at)
    usage = ledger.usage(now=now, policy=policy)
    possible = [rc.parse_time(e['at']) + dt.timedelta(days=policy['rolling_budget_days'], seconds=1)
                for e in ledger.entries()]
    possible.append((now + dt.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0))
    earliest = now if permitted(now) else next((t for t in sorted(set(possible)) if t > now and permitted(t)), None)
    return dict(status='BUDGET_AVAILABLE' if permitted(now) else 'WAITING_BUDGET', usage=usage,
        earliest_budget_at=earliest.isoformat(timespec='seconds') if earliest else None,
        required_reservation_seconds=TRAIN_SECONDS, additional_candidate_allowance=0,
        execution_host='macbookair', model_promotion=False)


def compare(records, gold, historical):
    return dict(models={**{k: v['average'] for k, v in historical.items()},
        'fixed_classifier': classifier.score(records, gold)},
        baseline_execution='frozen_historical_same_dev_not_rerun',
        classifier_observations_per_item=1, baseline_observations_per_item=2,
        comparison_variable='option_scoring_replaced_by_fixed_cls_binary_head',
        rule_answers_counted_as_model_success=False, new_blind=False,
        threshold_fitted=False, operational_promotion=False)


def _lock(root):
    path = Path(root) / 'binary-training/.controller.lock'
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path.open('a')


def prepare(root=a.DEFAULT):
    root = Path(root)
    p = root / FOLDER
    with _lock(root) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (p / 'PLAN.json').exists():
            plan = rc._read_json(p / 'PLAN.json')
            verify(root, plan)
            return rc._read_json(p / 'STATE.json')
        if not (root / 'PAUSED').is_file():
            raise ValueError('keep completed campaign paused while preparing this single candidate')
        rows, audit, pins = old._source_data(root)  # Independent operation replay + actual source inventory.
        original = rc._read_json(root / old.FOLDER / 'PLAN.json')
        if len(rows['train']) != 108 or len(rows['dev']) != 54:
            raise ValueError('fixed permitted train108/dev54 comparison required')
        if audit['train']['label_counts'] != {'preserved': 36, 'changed': 72}:
            raise ValueError('class balance is not another experimental variable')
        original_job = root / old.FOLDER / 'book-transfer-20261021/stage/incoming' / old.RUN / 'JOB-r1.json'
        job = rc._read_json(original_job)
        cfg = original['configuration']
        if (cfg['planned_updates'] != 56 or cfg['maximum_seconds'] != 1800 or
                cfg['maximum_updates'] != 120 or cfg['epochs'] != 4 or
                cfg['micro_batch'] != 1 or cfg['grad_accum'] != 8 or not cfg['freeze_encoder']):
            raise ValueError('keep original fixed training caps/configuration')
        files = {'moa/research_classifier.py': (a.ROOT / SOURCES[0]).read_bytes(),
            'runner.py': (a.ROOT / HELPER).read_bytes(),
            'tests/test_research_classifier_torch.py': (a.ROOT / SOURCES[3]).read_bytes()}
        for split in ('train', 'dev'):
            output = []
            for row in rows[split]:
                classifier.validate(row)
                classifier.target(row)
                keys = ('case_id', 'state', 'questions', 'expected') if split == 'train' else ('case_id', 'state', 'questions')
                output.append({k: row[k] for k in keys})
            files[split + '.json'] = research.canonical(output)
        baseline = saved.folder(root) / 'COMPARISON.json'
        if rc._read_json(baseline).get('operational_promotion') is not False:
            raise ValueError('frozen research baseline only')
        pins.update({'binary-training/PLAN.json': a.sha(root / old.FOLDER / 'PLAN.json'),
            str(original_job.relative_to(root)): a.sha(original_job),
            str(baseline.relative_to(root)): a.sha(baseline)})
        # Existing ledger before-image is immutable; later legitimate appends
        # are allowed, but rewriting/resetting its prefix is not.
        a.write_once(p / 'BUDGET_BEFORE.jsonl', (root / 'BUDGET.jsonl').read_bytes())
        a.write_once(p / 'DATA_AUDIT.json', research.canonical(audit))
        gold = {r['case_id']: next(iter(r['expected'].values())) for r in rows['dev']}
        a.write_once(p / 'GOLD.json', research.canonical(gold))
        import hashlib
        job = dict(version=classifier.VERSION, run_id=RUN, execution_host='macbookair',
            execution_device='mps', source_job=str(original_job.relative_to(root / old.FOLDER / 'book-transfer-20261021/stage')),
            source_job_sha256=a.sha(original_job), configuration=cfg,
            label_to_index={'preserved': 0, 'changed': 1},
            files={n: hashlib.sha256(v).hexdigest() for n, v in files.items()},
            evaluation_maximum_seconds=600, model_promotion=False,
            source_evidence_audit_sha256=a.sha(p / 'DATA_AUDIT.json'))
        for name, raw in files.items(): a.write_once(p / 'stage' / name, raw)
        a.write_once(p / 'stage/JOB.json', research.canonical(job))
        policy = rc._read_json(root / 'STATE.json')['policy_snapshot']
        plan = dict(run_id=RUN, profile=classifier.VERSION, prepared_at=a.now(),
            configuration=cfg, policy=policy, protected_inputs=pins,
            source_sha256={n: a.sha(a.ROOT / n) for n in SOURCES},
            stage_sha256={n: a.sha(p / 'stage' / n) for n in (*files, 'JOB.json')},
            gold_sha256=a.sha(p / 'GOLD.json'), audit_sha256=a.sha(p / 'DATA_AUDIT.json'),
            budget_before_sha256=a.sha(p / 'BUDGET_BEFORE.jsonl'), baseline_path=str(baseline.relative_to(root)),
            maximum_candidates=1, execution_host='macbookair',
            admission_memory_bytes=7 * 2**30, running_memory_bytes=5 * 2**30,
            new_blind=False, operational_promotion=False)
        a.write_once(p / 'PLAN.json', research.canonical(plan))
        gate = admission(rc.BudgetLedger(root / 'BUDGET.jsonl'), policy, at=a.now())
        state = dict(gate, run_id=RUN, prepared=True, training_issued=False,
                     training_updates=0, trained=False, evaluated=False, new_classifier_checkpoint=False)
        rc._write_atomic(p / 'STATE.json', state)
        return state


def verify(root, plan):
    root, p = Path(root), Path(root) / FOLDER
    if (plan['run_id'] != RUN or plan['profile'] != classifier.VERSION or
            plan['maximum_candidates'] != 1 or plan['execution_host'] != 'macbookair' or
            plan['operational_promotion'] is not False):
        raise ValueError('only one isolated Book classifier authorised')
    for name, sha in plan['source_sha256'].items():
        if a.sha(a.ROOT / name) != sha: raise ValueError('pinned classifier implementation changed')
    for name, sha in plan['protected_inputs'].items():
        if a.sha(root / name) != sha: raise ValueError('protected source/development lineage changed')
    for name, sha in plan['stage_sha256'].items():
        if a.sha(p / 'stage' / name) != sha: raise ValueError('staged classifier input changed')
    if (a.sha(p / 'GOLD.json') != plan['gold_sha256'] or a.sha(p / 'DATA_AUDIT.json') != plan['audit_sha256'] or
            a.sha(p / 'BUDGET_BEFORE.jsonl') != plan['budget_before_sha256'] or
            not (root / 'BUDGET.jsonl').read_bytes().startswith((p / 'BUDGET_BEFORE.jsonl').read_bytes()) or
            rc._read_json(root / 'STATE.json')['policy_snapshot'] != plan['policy']):
        raise ValueError('original policy/budget/source audit must be preserved')


def _remote(action, plan, *, raw=None, timeout=60):
    command = shlex.quote(rr.REMOTE_ROOT + '/venv-smoke/bin/python') + ' -c ' + shlex.quote(
        (a.ROOT / HELPER).read_text()) + ' ' + shlex.quote(json.dumps(dict(action=action,
            run_id=RUN, stage_sha256=plan['stage_sha256'])))
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
        '-o', 'ConnectTimeout=8', '-l', 'jungwoohwang', 'macbookair', command], input=raw,
        capture_output=True, timeout=timeout)
    if result.returncode or len(result.stdout) > 2 * 2**20:
        raise ValueError('Book classifier action incomplete; preserve dispatch/no automatic retry')
    return json.loads(result.stdout)


def preflight(root=a.DEFAULT):
    root, p = Path(root), Path(root) / FOLDER
    with _lock(root) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        plan = rc._read_json(p / 'PLAN.json'); verify(root, plan)
        if (p / 'DISPATCH.json').exists(): raise ValueError('no restaging a dispatched candidate')
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode='w') as tar:
            for name in plan['stage_sha256']: tar.add(p / 'stage' / name, arcname=name, recursive=False)
        _remote('stage', plan, raw=archive.getvalue())
        unit = _remote('selftest', plan)
        if unit.get('returncode') != 0 or unit.get('tests') != 2:
            raise ValueError('Book tensor-unit tests failed; do not train')
        a.write_once(p / 'BOOK_UNIT.json', research.canonical(unit))
        result = _remote('preflight', plan)
        if (result.get('input_items') != 162 or result.get('truncated_inputs') != 0 or
                result.get('model_inferences') != 0 or result.get('training_updates') != 0):
            raise ValueError('tokenizer-only classifier preflight incomplete')
        a.write_once(p / 'INPUT_PREFLIGHT.json', research.canonical(result))
        return result


def advance(root=a.DEFAULT, *, at=None):
    root, p = Path(root), Path(root) / FOLDER
    at = at or a.now()
    with _lock(root) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        plan = rc._read_json(p / 'PLAN.json'); verify(root, plan)
        state = rc._read_json(p / 'STATE.json')
        if state['status'] in ('COMPLETE', 'RECONCILIATION_REQUIRED'): return state
        def save(status, **values):
            state.update(status=status, **values)
            rc._write_atomic(p / 'STATE.json', state)
            return state
        ledger = rc.BudgetLedger(root / 'BUDGET.jsonl')
        if not (p / 'DISPATCH.json').exists():
            policy = rc._read_json(root / 'STATE.json')['policy_snapshot']
            gate = admission(ledger, policy, at=at)
            if gate['status'] != 'BUDGET_AVAILABLE':
                return save('WAITING_BUDGET', budget=gate)
            if any(e.get('candidate') == RUN for e in ledger.entries()):
                return save('RECONCILIATION_REQUIRED', reason='charged_without_dispatch_do_not_retry')
            if not (p / 'INPUT_PREFLIGHT.json').exists(): return save('WAITING_INPUT_PREFLIGHT')
            if not (root / 'PAUSED').exists(): return save('WAITING_OLD_COORDINATOR_PAUSE')
            snapshot = _remote('snapshot', plan)
            if not snapshot['train_admission']: return save('WAITING_BOOK_RESOURCE', resources=snapshot['resources'])
            charged = ledger.reserve(now=at, policy=policy, candidate=RUN, train_seconds=TRAIN_SECONDS)
            a.write_once(p / 'RESERVATION.json', research.canonical(charged))
            a.write_once(p / 'DISPATCH.json', research.canonical(dict(at=at, run_id=RUN,
                job_sha256=plan['stage_sha256']['JOB.json'], reservation_sha256=a.sha(p / 'RESERVATION.json'))))
            try:
                result = _remote('launch_train', plan, raw=research.canonical(charged))
                return save('TRAINING_ISSUED', training_issued=True, pid=result['pid'])
            except Exception:
                return save('RECONCILIATION_REQUIRED', reason='uncertain_single_training_dispatch')
        snapshot = _remote('snapshot', plan)
        if snapshot['busy'] or snapshot.get('running'): return save('OBSERVING_BOOK')
        trained = snapshot.get('train_result')
        if not trained or trained.get('status') != 'saved':
            return save('RECONCILIATION_REQUIRED', reason='uncompleted_training_no_automatic_resume')
        if (trained['run_id'] != RUN or trained['job_sha256'] != plan['stage_sha256']['JOB.json'] or
                trained['updates'] != 56 or not 0 < trained['seconds'] <= TRAIN_SECONDS or
                trained['execution_device'] != 'mps' or trained['encoder_unchanged'] is not True):
            return save('RECONCILIATION_REQUIRED', reason='training_receipt_mismatch')
        a.write_once(p / 'TRAIN_RESULT.json', research.canonical(trained))
        if not (p / 'EVAL_DISPATCH.json').exists():
            if not snapshot['evaluation_admission']: return save('WAITING_BOOK_EVALUATION_RESOURCE')
            a.write_once(p / 'EVAL_DISPATCH.json', research.canonical(dict(at=at, maximum_seconds=600)))
            try:
                result = _remote('launch_evaluate', plan)
                return save('EVALUATION_ISSUED', pid=result['pid'], trained=True, training_updates=trained['updates'])
            except Exception:
                return save('RECONCILIATION_REQUIRED', reason='uncertain_single_evaluation_dispatch')
        evaluated = snapshot.get('evaluation')
        if not evaluated or evaluated.get('status') != 'complete':
            return save('RECONCILIATION_REQUIRED', reason='uncompleted_evaluation_no_automatic_retry')
        if (evaluated['job_sha256'] != plan['stage_sha256']['JOB.json'] or evaluated['seconds'] > 600 or
                evaluated['reload_exact'] is not True or evaluated['head_sha256'] != trained['head_sha256'] or
                evaluated['system_forced'] is not False or evaluated['execution_device'] != 'mps'):
            return save('RECONCILIATION_REQUIRED', reason='separate_reload_or_evaluation_mismatch')
        a.write_once(p / 'EVALUATION.json', research.canonical(evaluated))
        result = compare(evaluated['records'], rc._read_json(p / 'GOLD.json'),
                         rc._read_json(root / plan['baseline_path'])['models'])
        a.write_once(p / 'COMPARISON.json', research.canonical(result))
        return save('COMPLETE', trained=True, evaluated=True, training_updates=trained['updates'],
                    new_classifier_checkpoint=True, head_sha256=trained['head_sha256'], comparison=result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'preflight', 'advance', 'status'))
    args = parser.parse_args()
    result = (rc._read_json(a.DEFAULT / FOLDER / 'STATE.json') if args.action == 'status' else
              globals()[args.action]())
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == '__main__': main()
