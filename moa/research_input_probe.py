"""Manual frozen-weight input comparison; no campaign dispatch or training."""
from __future__ import annotations

import argparse
import fcntl
import json
import math
from pathlib import Path
import shlex
import subprocess

from . import research_alignment as a, research_campaign as rc, r2_research as research
from . import research_binary_control as binary, research_book_resume as book
from . import research_book_results as recovery, research_binary_forensics as forensics
from .research_diagnostics import replay_binary

HELPER = 'scripts/moa-research-book-input-probe.py'
FOLDER = 'binary-training/book-transfer-20261021/paired-input-probe-20261021'


def prior_seconds(state, audit):
    prior = state['evaluation_progress']['seconds']
    reserved, observed = audit['cumulative_reserved_seconds'], audit['cumulative_observed_seconds']
    if (any(type(x) not in (int, float) or not math.isfinite(x) for x in (prior, reserved, observed)) or
            not 0 <= prior <= observed <= reserved <= 510 or abs(reserved - prior - 90) > 1e-6 or
            audit['training_updates'] != 0 or audit['model_inferences'] != 0):
        raise ValueError('carry the whole prior audit reservation without resetting time')
    return reserved


def compare(request, original, projected, gold, *, train_counts):
    expected = binary.evaluation_inputs(request, [])
    if set(gold) != {r['case_id'] for r in request}:
        raise ValueError('exact fixed development truth required')
    outputs = {}
    for name, observations in [('original_input', original), ('paired_input', projected)]:
        if len(observations) != len(expected):
            raise ValueError('incomplete input comparison')
        rows = []
        for actual, source in zip(observations, expected):
            if (any(actual.get(k) != source[k] for k in ('case_id', 'mode', 'family', 'operation')) or
                    actual.get('system_forced') is not False or actual.get('model') != 'binary_candidate' or
                    actual.get('split') != 'dev'):
                raise ValueError('same raw model and exact development inputs/orders required')
            rows.append(dict(actual, expected=gold[actual['case_id']]))
        outputs[name] = replay_binary([r for r in rows if r['mode'] == 'binary'],
            [r for r in rows if r['mode'] == 'binary_reverse'], train_counts=train_counts)
    return dict(outputs, training_updates=0, new_candidates=0, new_blind=False, threshold_fitted=False,
        operational_promotion=False, scope='one_input_representation_only_on_exposed_dev',
        same_saved_weights=True, separate_rule_scores=True)


def run(root=a.DEFAULT):
    root = Path(root)
    p = root / FOLDER
    if not (root / forensics.FOLDER / 'AUDIT.json').is_file():
        raise ValueError('completed immutable forensic receipt required; do not start another audit')
    # The completed audit validates itself under the same lock. Do this before
    # acquiring it here: a second flock on a different descriptor is not reentrant.
    audit = forensics.audit(root)
    with (root / binary.FOLDER / '.controller.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        plan = rc._read_json(recovery.folder(root) / 'PLAN.json')
        recovery.verify(root, plan)
        state = rc._read_json(recovery.folder(root) / 'STATE.json')
        audit_path = root / forensics.FOLDER / 'AUDIT.json'
        if rc._read_json(audit_path) != audit:
            raise ValueError('completed forensic receipt changed')
        if state['status'] != 'COMPLETE':
            raise ValueError('saved-model baseline must already be complete')
        prior = prior_seconds(state, audit)
        pins = dict(budget=a.sha(root / 'BUDGET.jsonl'), recovery_state=a.sha(recovery.folder(root) / 'STATE.json'),
            recovery_plan=a.sha(recovery.folder(root) / 'PLAN.json'), audit=a.sha(audit_path),
            helper=a.sha(a.ROOT / HELPER), controller=a.sha(Path(__file__)))
        if (p / 'COMPARISON.json').exists():
            result = rc._read_json(p / 'COMPARISON.json')
            if result['pins'] != pins:
                raise ValueError('completed input experiment changed; do not rerun')
            return result
        if (p / 'RESERVATION.json').exists():
            raise ValueError('previous input probe issued; never retry uncertain delivery')
        transfer = rc._read_json(book.folder(root) / 'PLAN.json')
        snap = book._snapshot(root, transfer)
        if (not snap.get('online') or not snap.get('ready') or snap.get('busy') or snap.get('running') or
                not snap['resources']['verify_allowed'] or snap['resources']['available_memory_bytes'] < 7 * 2**30):
            return dict(status='WAITING_BOOK_RESOURCE', training_updates=0, new_candidates=0)
        reservation = forensics.reserve(p / 'RESERVATION.json', prior_seconds=prior, pins=pins)
        control = dict(run_id=binary.RUN, job_sha256=transfer['remote_job_sha256'], maximum_seconds=90,
            checkpoint_sha256=plan['checkpoint_sha256'],
            request_sha256=rc._read_json(root / binary.FOLDER / 'PLAN.json')['input_sha256']['BINARY_REQUEST.json'],
            original_evaluator_sha256=transfer['source_sha256'][book.EVALUATOR])
        executable = '/Users/jungwoohwang/Library/Application Support/MOA-LAYA/venv-smoke/bin/python'
        command = shlex.quote(executable) + ' -c ' + shlex.quote((a.ROOT / HELPER).read_text())
        process = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ConnectTimeout=8', '-l', 'jungwoohwang', 'macbookair', command],
            input=research.canonical(control), capture_output=True, timeout=95)
        if process.returncode or len(process.stdout) > 2 * 1024 * 1024:
            raise ValueError('input probe incomplete; preserve reservation, no automatic retry')
        raw = json.loads(process.stdout)
        a.write_once(p / 'OBSERVATIONS.json', research.canonical(raw))
        if (raw['status'] != 'complete' or raw['model_sha256'] != control['checkpoint_sha256']['model.safetensors'] or
                raw['request_sha256'] != control['request_sha256'] or raw['trained'] is not False or
                raw['training_updates'] != 0 or raw['weights_unchanged'] is not True or
                raw['training_state_unchanged'] is not True or raw['execution_host'] != 'macbookair' or
                raw['execution_device'] != 'mps' or not 0 <= raw['seconds'] <= 90 or raw['truncated_inputs'] != 0 or
                raw['minimum_available_memory_bytes'] < 5 * 2**30):
            raise ValueError('input-only completion failed; do not retry or qualify')
        base = root / binary.FOLDER
        request = rc._read_json(base / 'BINARY_REQUEST.json')
        original = rc._read_json(base / 'PLAN.json')
        result = compare(request, state['evaluation_progress']['records'], raw['records'],
            rc._read_json(base / 'GOLD.json'), train_counts=original['train_counts'])
        recovery.verify(root, plan)
        if pins['budget'] != a.sha(root / 'BUDGET.jsonl'):
            raise ValueError('original training budget changed')
        result.update(status='COMPLETE_NOT_FOR_OPERATION', pins=pins, at=a.now(),
            model_sha256=raw['model_sha256'], paired_input_sha256=raw['paired_input_sha256'],
            observed_seconds=raw['seconds'], cumulative_reserved_seconds=prior + reservation['reserved_seconds'],
            cumulative_observed_seconds=audit['cumulative_observed_seconds'] + raw['seconds'],
            maximum_input_tokens=raw['maximum_input_tokens'], truncated_inputs=0,
            minimum_available_memory_bytes=raw['minimum_available_memory_bytes'], model_inferences=len(raw['records']))
        a.write_once(p / 'COMPARISON.json', research.canonical(result))
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['run', 'status'])
    args = parser.parse_args()
    result = run() if args.command == 'run' else rc._read_json(a.DEFAULT / FOLDER / 'COMPARISON.json')
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
