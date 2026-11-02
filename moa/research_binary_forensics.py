"""Manual, read-only forensics of the completed binary candidate; never trains."""
from __future__ import annotations

import argparse
from collections import defaultdict
import fcntl
import hashlib
import json
import math
from pathlib import Path
import shlex
from statistics import mean
import subprocess

from . import research_alignment as a, research_campaign as rc, r2_research as research
from . import research_book_results as recovery, research_book_resume as book
from .research_diagnostics import replay_binary

HELPER = 'scripts/moa-research-book-forensics.py'
FOLDER = 'binary-training/book-transfer-20261021/forensics-20261021'


def content_contrasts(rows, first, second, *, train_counts):
    """Source-matched descriptive contrasts, NOT a causal training experiment.

    Output transformations can also differ in layout. No threshold is fitted,
    no new inference is run and a source-matched pair is not a new sample.
    """
    replay = replay_binary(first, second, train_counts=train_counts)
    left, right = ({r['case_id']: r for r in part} for part in (first, second))
    if len(rows) != len(left) or {r['case_id'] for r in rows} != set(left):
        raise ValueError('exact request and observation identities required')
    groups = defaultdict(lambda: {'preserved': [], 'changed': []})
    order_changes = []
    for row in rows:
        identity = row['case_id']
        x, y = left[identity], right[identity]
        if any(row[k] != x[k] for k in ('family', 'operation')):
            raise ValueError('input/observation metadata mismatch')
        source = hashlib.sha256(research.canonical(row['state']['source_blocks'])).hexdigest()
        key = source, row['family'], tuple(row['questions'])
        score = (x['probabilities']['changed'] + y['probabilities']['changed']) / 2
        groups[key][x['expected']].append(score)
        order_changes.append(abs(x['probabilities']['changed'] - y['probabilities']['changed']))
    differences, paired = [], 0
    for group in groups.values():
        if group['preserved'] and group['changed']:
            paired += 1
            differences.extend(e - n for e in group['changed'] for n in group['preserved'])
    if not differences:
        raise ValueError('no matched normal/error source contrast')
    return dict(paired_source_groups=paired, content_pairs=len(differences),
        changed_score_increased_pairs=sum(x > 0 for x in differences),
        mean_changed_minus_normal_score=mean(differences),
        mean_absolute_content_change=mean(abs(x) for x in differences),
        mean_absolute_order_change=mean(order_changes), same_input_contrasts_only=True,
        layout_may_also_differ=True, causal_training_effect_tested=False,
        replay=replay, model_inferences=0, training_updates=0,
        threshold_fitted=False, operational_promotion=False)


def reserve(path, *, prior_seconds, pins):
    if (type(prior_seconds) not in (int, float) or not math.isfinite(prior_seconds) or
            not 0 <= prior_seconds <= 510):
        raise ValueError('no room for the full read-only audit reservation')
    if Path(path).exists():
        raise ValueError('prior diagnostic reservation exists; do not retry/reset')
    value = dict(prior_evaluation_seconds=prior_seconds, reserved_seconds=90,
        maximum_cumulative_seconds=600, pins=pins, at=a.now(),
        training_updates=0, new_candidates=0, execution_host='macbookair')
    a.write_once(Path(path), research.canonical(value))
    return value


def audit(root=a.DEFAULT):
    root = Path(root)
    p = root / FOLDER
    with (root / 'binary-training/.controller.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        plan = rc._read_json(recovery.folder(root) / 'PLAN.json')
        recovery.verify(root, plan)
        state = rc._read_json(recovery.folder(root) / 'STATE.json')
        if state['status'] != 'COMPLETE':
            raise ValueError('complete saved-model evaluation required')
        cp = recovery.folder(root) / 'COMPARISON.json'
        if a.sha(cp) != state['comparison_sha256']:
            raise ValueError('completed evaluation comparison changed')
        pins = dict(budget=a.sha(root / 'BUDGET.jsonl'), recovery_plan=a.sha(recovery.folder(root) / 'PLAN.json'),
                    recovery_state=a.sha(recovery.folder(root) / 'STATE.json'),
                    helper=a.sha(a.ROOT / HELPER), controller=a.sha(Path(__file__)))
        if (p / 'AUDIT.json').exists():
            result = rc._read_json(p / 'AUDIT.json')
            if result['pins'] != pins:
                raise ValueError('immutable audit changed; do not rerun')
            return result
        if (p / 'RESERVATION.json').exists():
            raise ValueError('unknown prior audit; no automatic retry')
        transfer = rc._read_json(book.folder(root) / 'PLAN.json')
        snapshot = book._snapshot(root, transfer)
        if (not snapshot.get('online') or not snapshot.get('ready') or snapshot.get('busy') or
                snapshot.get('running') or not snapshot['resources']['verify_allowed'] or
                snapshot['resources']['available_memory_bytes'] < 5 * 2**30):
            return dict(status='WAITING_BOOK_RESOURCE', training_updates=0, model_inferences=0)
        old = root / 'binary-training'
        rows = rc._read_json(old / 'BINARY_REQUEST.json')
        gold = rc._read_json(old / 'GOLD.json')
        original = rc._read_json(old / 'PLAN.json')
        records = [dict(x, expected=gold[x['case_id']]) for x in state['evaluation_progress']['records']]
        contrast = content_contrasts(rows,
            [x for x in records if x['mode'] == 'binary'],
            [x for x in records if x['mode'] == 'binary_reverse'], train_counts=original['train_counts'])
        reservation = reserve(p / 'RESERVATION.json',
            prior_seconds=state['evaluation_progress']['seconds'], pins=pins)
        request = dict(run_id=book.binary.RUN, job_sha256=transfer['remote_job_sha256'],
            model_sha256=plan['checkpoint_sha256']['model.safetensors'], maximum_seconds=90)
        code = (a.ROOT / HELPER).read_text()
        remote_python = '/Users/jungwoohwang/Library/Application Support/MOA-LAYA/venv-smoke/bin/python'
        command = shlex.quote(remote_python) + ' -c ' + shlex.quote(code)
        proc = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ConnectTimeout=8', '-l', 'jungwoohwang', 'macbookair', command],
            input=research.canonical(request), capture_output=True, timeout=95)
        if proc.returncode or len(proc.stdout) > 2 * 1024 * 1024:
            raise ValueError('read-only Book audit incomplete; preserve reservation, do not retry')
        remote = json.loads(proc.stdout)
        if (remote['status'] != 'complete' or remote['model_sha256'] != request['model_sha256'] or
                remote['job_sha256'] != request['job_sha256'] or remote['model_inferences'] != 0 or
                remote['training_updates'] != 0 or not 0 <= remote['seconds'] <= 90 or
                remote['training_state_unchanged'] is not True or remote['weights_unchanged'] is not True):
            raise ValueError('read-only Book audit identity/limit differs; no retry')
        recovery.verify(root, plan)
        if a.sha(root / 'BUDGET.jsonl') != pins['budget']:
            raise ValueError('training budget changed during read-only audit')
        result = dict(status='DIAGNOSIS_COMPLETE_NOT_A_MODEL_FIX', at=a.now(), pins=pins,
            saved_state=remote, cached_development=contrast,
            cumulative_reserved_seconds=reservation['prior_evaluation_seconds']+90,
            cumulative_observed_seconds=reservation['prior_evaluation_seconds']+remote['seconds'],
            model_inferences=0, training_updates=0, new_candidates=0, new_blind=False,
            operating_model_changed=False, automatic_approval=False, operational_promotion=False)
        a.write_once(p / 'AUDIT.json', research.canonical(result))
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['audit', 'status'])
    args = parser.parse_args()
    result = audit() if args.command == 'audit' else rc._read_json(a.DEFAULT / FOLDER / 'AUDIT.json')
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
