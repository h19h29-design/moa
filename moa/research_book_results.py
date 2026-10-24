"""Explicit evaluation-only recovery of a saved MacBook candidate.

Keep the old terminal state, permission, budget and pinned implementation intact.
Use the same controller/device locks and the unchanged gold-free Book evaluator.
There is no training path or budget grant in this module.
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import math
from pathlib import Path
import shlex
import subprocess

from . import research_alignment as a, research_campaign as rc, r2_research as research
from . import research_binary_control as binary, research_book_resume as book

SELF = 'moa/research_book_results.py'
COLLECTOR = 'scripts/moa-research-book-results.py'
RECOVERY = 'evaluation-recovery-20261021'
LIMIT = 2 * 1024 * 1024


def folder(root):
    return book.folder(root) / RECOVERY


def _metadata(plan):
    command = 'python3 -c ' + shlex.quote((a.ROOT / COLLECTOR).read_text())
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
        '-o', 'ConnectTimeout=8', '-l', 'jungwoohwang', 'macbookair', command],
        capture_output=True, timeout=60)
    if result.returncode == 255:
        raise ConnectionError('MacBook SSH unavailable')
    if result.returncode or len(result.stdout) > LIMIT:
        raise ValueError('MacBook small result channel refused/oversize')
    return json.loads(result.stdout)


def validate(root, transfer, bundle):
    """Validate actual remote digests AND original binary training semantics."""
    old = rc._read_json(Path(root) / binary.FOLDER / 'PLAN.json')
    files = bundle['files']
    if (bundle['run_id'] != binary.RUN or
            set(files) not in ({'TRAIN_RESULT.json', 'TRAINING_CONTRACT.json'},
                              {'TRAIN_RESULT.json', 'TRAINING_CONTRACT.json', 'EVALUATION.json'})):
        raise ValueError('invalid small result inventory')
    decoded = {}
    for name, value in files.items():
        if len(value['base64']) > 4 * LIMIT // 3 + 4:
            raise ValueError('metadata size limit')
        raw = base64.b64decode(value['base64'], validate=True)
        if len(raw) > LIMIT or hashlib.sha256(raw).hexdigest() != value['sha256']:
            raise ValueError('metadata hash/size mismatch')
        decoded[name] = json.loads(raw)
    saved, contract = decoded['TRAIN_RESULT.json'], decoded['TRAINING_CONTRACT.json']
    name = Path(saved['checkpoint_name'])
    if (name.is_absolute() or len(name.parts) != 1 or name.name in ('.', '..') or
            name.name != bundle['checkpoint_name'] or
            (saved.get('checkpoint') is not None and saved['checkpoint'] !=
             str(Path('/Users/jungwoohwang/Library/Application Support/MOA-LAYA') /
                 'research' / binary.RUN / name))):
        raise ValueError('unconfined saved MacBook checkpoint')
    digests = saved['sha256']
    attested = bundle['checkpoint']
    if (not {'model.safetensors', 'training-state.pt'} <= set(digests) or set(digests) != set(attested) or
            any(Path(n).is_absolute() or '..' in Path(n).parts or
                value['sha256'] != digests[n] or not isinstance(value['bytes'], int) or value['bytes'] <= 0
                for n, value in attested.items())):
        raise ValueError('actual remote checkpoint digest/inventory mismatch')
    if (saved['run_id'] != binary.RUN or saved['job_sha256'] != transfer['remote_job_sha256'] or
            saved['data_sha256'] != old['data_sha256'] or
            saved['updates'] != old['configuration']['planned_updates'] or
            not transfer['carried_seconds'] <= saved['seconds'] <= old['configuration']['maximum_seconds'] or
            saved['status'] != 'saved' or saved['training_complete'] is not True or
            saved['stop_reason'] != 'epoch_limit' or saved.get('resume_from_updates') != 0 or
            saved.get('resume_from_sha256') != transfer['checkpoint_sha256'] or
            contract['plan']['configuration'] != old['configuration'] or
            contract.get('option_labels') != ['preserved', 'changed'] or
            contract.get('semantic_profile') != binary.PROFILE or contract.get('label_counts') != old['train_counts'] or
            contract['job_sha256'] != saved['job_sha256'] or contract['data_sha256'] != saved['data_sha256'] or
            files['TRAINING_CONTRACT.json']['sha256'] != saved['training_contract_sha256']):
        raise ValueError('saved binary candidate contract/limits differ')
    return saved, decoded


def _record(root, bundle):
    raw = research.canonical(bundle)
    a.write_once(folder(root) / 'receipts' / (hashlib.sha256(raw).hexdigest() + '.json'), raw)


def prepare(root=a.DEFAULT, *, at=None):
    """Manual operator action only; never invoked by the campaign timer."""
    root = Path(root)
    with (root / binary.FOLDER / '.controller.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (folder(root) / 'PLAN.json').exists():
            plan = rc._read_json(folder(root) / 'PLAN.json')
            verify(root, plan)
            return plan
        transfer = rc._read_json(book.folder(root) / 'PLAN.json')
        book.verify(root, transfer)
        state = rc._read_json(root / binary.FOLDER / 'STATE.json')
        if (not (root / 'PAUSED').exists() or state['status'] != 'RECONCILIATION_REQUIRED' or
                state.get('reason') != 'TimeoutExpired' or
                list(book.folder(root).glob('EVAL_DISPATCH-*.json')) or state.get('book_evaluation_boundaries')):
            raise ValueError('explicit paused collection-timeout recovery before any evaluation required')
        bundle = _metadata(transfer)
        saved, decoded = validate(root, transfer, bundle)
        if 'EVALUATION.json' in decoded:
            raise ValueError('unexpected preexisting evaluation; preserve/reconcile its budget')
        plan = dict(version='moa.book-evaluation-recovery.v1',
            authorised_by='explicit_user_saved_model_evaluation_only_2026-10-21',
            prepared_at=at or a.now(), run_id=binary.RUN, execution_host='macbookair', execution_device='mps',
            additional_candidates=0, additional_train_seconds=0, maximum_eval_seconds=600,
            maximum_eval_dispatches=4, minimum_running_memory_bytes=5 * 2**30,
            transfer_plan_sha256=a.sha(book.folder(root) / 'PLAN.json'),
            legacy_state_sha256=a.sha(root / binary.FOLDER / 'STATE.json'),
            train_result_sha256=bundle['files']['TRAIN_RESULT.json']['sha256'],
            checkpoint_sha256=saved['sha256'],
            source_sha256={n: a.sha(a.ROOT / n) for n in (SELF, COLLECTOR)})
        _record(root, bundle)
        a.write_once(folder(root) / 'PLAN.json', research.canonical(plan))
        rc._write_atomic(folder(root) / 'STATE.json', dict(status='READY_FOR_EVALUATION',
            run_id=binary.RUN, plan_sha256=a.sha(folder(root) / 'PLAN.json'), dispatches=[]))
        return plan


def verify(root, plan):
    root = Path(root)
    if (not (root / 'PAUSED').exists() or plan['run_id'] != binary.RUN or
            plan['authorised_by'] != 'explicit_user_saved_model_evaluation_only_2026-10-21' or
            plan['execution_host'] != 'macbookair' or plan['execution_device'] != 'mps' or
            plan['additional_candidates'] != 0 or plan['additional_train_seconds'] != 0 or
            plan['maximum_eval_seconds'] != 600 or plan['maximum_eval_dispatches'] != 4 or
            plan['minimum_running_memory_bytes'] != 5 * 2**30 or
            a.sha(root / binary.FOLDER / 'STATE.json') != plan['legacy_state_sha256'] or
            a.sha(book.folder(root) / 'PLAN.json') != plan['transfer_plan_sha256'] or
            any(a.sha(a.ROOT / n) != digest for n, digest in plan['source_sha256'].items())):
        raise ValueError('evaluation-only recovery boundary changed')
    book.verify(root, rc._read_json(book.folder(root) / 'PLAN.json'))


def _evaluation(root, plan, saved, value):
    old = rc._read_json(Path(root) / binary.FOLDER / 'PLAN.json')
    if (value['model_sha256'] != saved['sha256']['model.safetensors'] or
            value['request_sha256'] != old['input_sha256']['BINARY_REQUEST.json'] or
            not isinstance(value['seconds'], (float, int)) or not math.isfinite(value['seconds']) or
            not 0 <= value['seconds'] <= plan['maximum_eval_seconds'] or
            value.get('separate_process') is not True or value.get('execution_device') != 'mps' or
            value.get('trained') is not False or value.get('system_forced') is not False or
            value.get('operational_promotion') is not False):
        raise ValueError('evaluation identity/limits/raw-model boundary changed')
    request = rc._read_json(Path(root) / binary.FOLDER / 'BINARY_REQUEST.json')
    pending = binary.evaluation_inputs(request, value['records'])
    for row, source in zip(value['records'], binary.evaluation_inputs(request, [])):
        if (row.get('system_forced') is not False or row.get('model') != 'binary_candidate' or
                row.get('split') != 'dev' or row.get('operation') != source['operation'] or
                row.get('family') != source['family'] or row['prediction'] not in ('preserved', 'changed')):
            raise ValueError('evaluation raw record changed')
        for key in ('probabilities', 'logits'):
            if set(row[key]) != {'preserved', 'changed'} or not all(math.isfinite(v) for v in row[key].values()):
                raise ValueError('invalid two-choice scores')
        probs = row['probabilities']
        if any(not 0 <= v <= 1 for v in probs.values()) or abs(sum(probs.values()) - 1) > 1e-5:
            raise ValueError('invalid model probabilities')
    return pending


def _finish(root, plan, saved, evaluated):
    base = Path(root) / binary.FOLDER
    old, gold = rc._read_json(base / 'PLAN.json'), rc._read_json(base / 'GOLD.json')
    rows = [dict(r, expected=gold[r['case_id']]) for r in evaluated['records']]
    result = binary.comparison(rc._read_json(base / 'BASELINES.json'),
        [r for r in rows if r['mode'] == 'binary'], [r for r in rows if r['mode'] == 'binary_reverse'],
        train_counts=old['train_counts'], rule_excluded_dev=rc._read_json(base / 'DATA_AUDIT.json')['dev']['rule_excluded'])
    result.update(updates=saved['updates'], train_seconds=saved['seconds'],
        checkpoint_sha256=saved['sha256']['model.safetensors'], separate_reload=True,
        execution_host='macbookair', execution_device='mps', host_migration=True,
        evaluation_seconds=evaluated['seconds'], recovery='metadata_only_result_channel',
        baselines='frozen_historical_same_input_and_two_orders_not_newly_reexecuted')
    a.write_once(folder(root) / 'COMPARISON.json', research.canonical(result))
    lines = ['# MOA 저장 이진 후보 — 맥북 평가 결과', '',
        '기존 노출 합성 dev54. 새 블라인드·실물 성적이 아닙니다.',
        '기존 모델은 고정된 과거 동일 입력/선택지 두 순서 성적이며 이번에 재실행하지 않았습니다.', '',
        '| 모델 | 정답률 | 정상 오탐률 | 오류 검출률 |', '|---|---:|---:|---:|']
    for name, value in result['models'].items():
        score = value['average']
        lines.append('| %s | %.1f%% | %.1f%% | %.1f%% |' % (name, 100 * score['accuracy'],
            100 * score['normal_false_positive_rate'], 100 * (1 - score['error_miss_rate'])))
    lines += ['', '두 순서의 확률 평균 판정. 규칙 정답 미합산. 추가 학습·운영 적용·자동 승인 없음.']
    a.write_once(folder(root) / 'RESULT.md', ('\n'.join(lines) + '\n').encode())
    return result


def advance(root=a.DEFAULT):
    root = Path(root)
    with (root / binary.FOLDER / '.controller.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return dict(status='OBSERVE_ONLY')
        p = folder(root)
        state = rc._read_json(p / 'STATE.json')
        if state['status'] in binary.TERMINAL:
            return state
        def save(status, **values):
            state.update(status=status, **values)
            rc._write_atomic(p / 'STATE.json', state)
            return state
        try:
            plan = rc._read_json(p / 'PLAN.json')
            if state['plan_sha256'] != a.sha(p / 'PLAN.json'):
                raise ValueError('recovery plan changed')
            verify(root, plan)
            transfer = rc._read_json(book.folder(root) / 'PLAN.json')
            snap = book._snapshot(root, transfer)
            if not snap.get('online'):
                return save('WAITING_BOOK_ONLINE')
            if not snap.get('ready'):
                raise ValueError('pinned Book environment changed')
            if snap.get('running') or snap.get('busy'):
                return dict(status='OBSERVE_BOOK', run_id=binary.RUN)
            bundle = _metadata(transfer)
            saved, decoded = validate(root, transfer, bundle)
            if (bundle['files']['TRAIN_RESULT.json']['sha256'] != plan['train_result_sha256'] or
                    saved['sha256'] != plan['checkpoint_sha256']):
                raise ValueError('saved candidate changed since recovery approval')
            _record(root, bundle)
            evaluated = decoded.get('EVALUATION.json')
            if evaluated:
                pending = _evaluation(root, plan, saved, evaluated)
                previous = state.get('evaluation_progress', dict(seconds=0, records=[]))
                if (evaluated['seconds'] < previous['seconds'] or
                        evaluated['records'][:len(previous['records'])] != previous['records']):
                    raise ValueError('evaluation budget/progress rolled back')
                save(state['status'], evaluation_progress=dict(seconds=evaluated['seconds'], records=evaluated['records']))
                if evaluated['status'] == 'complete':
                    if pending:
                        raise ValueError('incomplete evaluation marked complete')
                    _finish(root, plan, saved, evaluated)
                    return save('COMPLETE', comparison_sha256=a.sha(p / 'COMPARISON.json'), completed_at=a.now())
                if evaluated['status'] != 'waiting_resources' or evaluated['seconds'] >= 580:
                    raise ValueError('evaluation stopped or cumulative time exhausted; do not reset')
            elif state['dispatches'] or list(p.glob('EVAL_DISPATCH-*.json')):
                raise ValueError('issued evaluation has no saved boundary; never relaunch uncertain delivery')
            resource = snap['resources']
            if not resource['verify_allowed'] or resource['available_memory_bytes'] < plan['minimum_running_memory_bytes']:
                return save('WAITING_BOOK_EVAL_RESOURCE', available_memory_bytes=resource['available_memory_bytes'])
            boundary = bundle['files']['EVALUATION.json']['sha256'] if evaluated else 'initial'
            dispatches = state['dispatches']
            if (boundary in dispatches or len(dispatches) >= plan['maximum_eval_dispatches'] or
                    len(list(p.glob('EVAL_DISPATCH-*.json'))) != len(dispatches)):
                raise ValueError('evaluation boundary repeated/dispatch history changed')
            a.write_once(p / ('EVAL_DISPATCH-%d.json' % len(dispatches)), research.canonical(
                dict(boundary=boundary, at=a.now(), action='evaluate', training=False)))
            save('EVALUATING_ON_BOOK', dispatches=dispatches + [boundary])
            return save('EVALUATING_ON_BOOK', book_eval_pid=book._launch(root, transfer, 'evaluate'))
        except (ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
            return save('RECONCILIATION_REQUIRED', reason=type(exc).__name__)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'advance', 'status'])
    parser.add_argument('--root', type=Path, default=a.DEFAULT)
    args = parser.parse_args()
    if args.command == 'prepare':
        plan = prepare(args.root)
        result = dict(status='READY_FOR_EVALUATION', run_id=plan['run_id'], training=False)
    elif args.command == 'advance':
        result = advance(args.root)
    else:
        result = rc._read_json(folder(args.root) / 'STATE.json')
    print(json.dumps({k: v for k, v in result.items() if k != 'evaluation_progress'}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
