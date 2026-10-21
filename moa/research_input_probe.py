"""Manual frozen-weight input comparison; no campaign dispatch or training."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
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
INITIAL_FOLDER = 'binary-training/book-transfer-20261021/paired-input-probe-20261021'
FOLDER = 'binary-training/book-transfer-20261021/paired-input-probe-r1-20261021'
INITIAL_REVISION = 'dde9f5b1deb42095aca670357cf88c8767f45e39'


def prior_seconds(state, audit):
    prior = state['evaluation_progress']['seconds']
    reserved, observed = audit['cumulative_reserved_seconds'], audit['cumulative_observed_seconds']
    if (any(type(x) not in (int, float) or not math.isfinite(x) for x in (prior, reserved, observed)) or
            not 0 <= prior <= observed <= reserved <= 510 or abs(reserved - prior - 90) > 1e-6 or
            audit['training_updates'] != 0 or audit['model_inferences'] != 0):
        raise ValueError('carry the whole prior audit reservation without resetting time')
    return reserved


def carry_preload_reservation(prior, failed):
    if (failed['prior_evaluation_seconds'] != prior or failed['reserved_seconds'] != 90 or
            failed['maximum_cumulative_seconds'] != 600 or failed['training_updates'] != 0 or
            failed['new_candidates'] != 0 or failed['execution_host'] != 'macbookair' or prior + 90 > 510):
        raise ValueError('preserve failed preload reservation in full; do not erase time')
    return prior + 90


def _preload_check():
    """Read metadata only, proving the prior relative-library failure boundary."""
    code = """import json,hashlib
from pathlib import Path
r=Path('/Users/jungwoohwang/Library/Application Support/MOA-LAYA')
i=r/'incoming'/'moa-unattended-20261021-binary-train-01'
j=json.loads((i/'JOB-r1.json').read_bytes()); lib=Path(j['library'])
if lib.is_absolute() or '..' in lib.parts: raise ValueError('expected relative runtime')
p=r/'research'/'moa-unattended-20261021-binary-train-01'/'input-probe-20261021'/'RESULT.json'
print(json.dumps(dict(runtime_relative=True,ssh_home_library_exists=lib.exists(),
    incoming_library_matches=hashlib.sha256((i/lib).read_bytes()).hexdigest()==j['library_sha256'],
    probe_receipt_exists=p.exists(),model_loaded=False,model_inferences=0,training_updates=0)))
"""
    response = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
        '-o', 'ConnectTimeout=8', '-l', 'jungwoohwang', 'macbookair',
        'python3 -c ' + shlex.quote(code)], capture_output=True, timeout=15)
    if response.returncode or len(response.stdout) > 4096:
        raise ValueError('read-only preload boundary check incomplete; do not dispatch')
    value = json.loads(response.stdout)
    if value != dict(runtime_relative=True, ssh_home_library_exists=False, incoming_library_matches=True,
                     probe_receipt_exists=False, model_loaded=False, model_inferences=0, training_updates=0):
        raise ValueError('not the known missing-runtime preload failure; do not retry')
    return value


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
        initial = root / INITIAL_FOLDER
        failed = rc._read_json(initial / 'RESERVATION.json')
        if (initial / 'OBSERVATIONS.json').exists() or (initial / 'COMPARISON.json').exists():
            raise ValueError('prior execution has observations; no preload correction')
        for key, name in [('helper', HELPER), ('controller', 'moa/research_input_probe.py')]:
            original_source = subprocess.run(['git', 'show', INITIAL_REVISION + ':' + name],
                cwd=a.ROOT, capture_output=True, timeout=10, check=True).stdout
            if hashlib.sha256(original_source).hexdigest() != failed['pins'][key]:
                raise ValueError('failed implementation before-image differs')
        prior = carry_preload_reservation(prior, failed)
        pins = dict(budget=a.sha(root / 'BUDGET.jsonl'), recovery_state=a.sha(recovery.folder(root) / 'STATE.json'),
            recovery_plan=a.sha(recovery.folder(root) / 'PLAN.json'), audit=a.sha(audit_path),
            helper=a.sha(a.ROOT / HELPER), controller=a.sha(Path(__file__)),
            failed_preload_reservation=a.sha(initial / 'RESERVATION.json'))
        if any(failed['pins'][key] != pins[key] for key in ('budget', 'recovery_state', 'recovery_plan', 'audit')):
            raise ValueError('original preload identity or budget changed')
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
        boundary = _preload_check()
        a.write_once(p / 'PRELOAD_RECONCILIATION.json', research.canonical(dict(
            boundary=boundary, original_revision=INITIAL_REVISION, original_reservation_sha256=pins['failed_preload_reservation'],
            reason='relative_runtime_missing_from_ssh_home_before_model_load',
            correction='use_existing_incoming_working_directory_only', original_reservation_preserved=True,
            training_updates=0, new_candidates=0, at=a.now())))
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
