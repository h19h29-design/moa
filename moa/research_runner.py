"""Mac mini side of the unattended research loop.

Publishes one pinned job to the MacBook through the existing key-based SSH
alias, runs only the pinned worker actions, collects raw model answers and
scores them against gold that never leaves the mini. Nothing here touches the
operational NAS database, collector schedule or model registry.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import Counter


DEFAULT_HOST = 'macbookair'
REMOTE_ROOT = '/Users/jungwoohwang/Library/Application Support/MOA-LAYA'
RUNNER_NAME = 'moa-research-worker.py'
ACTIONS = ('inspect', 'train', 'reload', 'blind', 'diagnose', 'evaluate')
RUN_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{2,79}')


def remote_argv(host, tail, run_id=None, runner_name=RUNNER_NAME):
    """Build a fixed SSH invocation; the host and action list are whitelisted."""
    if host != DEFAULT_HOST:
        raise ValueError('only the pinned MacBook host is allowed')
    if not re.fullmatch(r'moa-research-worker(?:-v[1-9][0-9]*)?\.py', runner_name):
        raise ValueError('fixed research runner name required')
    if not isinstance(tail, list) or not tail or tail[0] not in ACTIONS:
        raise ValueError('only pinned worker actions are allowed')
    for item in tail:
        if not isinstance(item, str) or '\n' in item or '\x00' in item:
            raise ValueError('invalid worker argument')
    remote = ' '.join(_quote(item) for item in tail)
    python = _quote(REMOTE_ROOT + '/venv-smoke/bin/python')
    if run_id is None:
        return ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                '-o', 'ConnectTimeout=10', '-l', 'jungwoohwang', host,
                'cd %s && %s %s %s' % (_quote(REMOTE_ROOT + '/incoming/latest'),
                                       python, runner_name, remote)]
    if not RUN_ID.fullmatch(str(run_id)):
        raise ValueError('unsafe run id')
    folder = _quote(REMOTE_ROOT + '/incoming/' + run_id)
    return ['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'ConnectTimeout=10', '-l', 'jungwoohwang', host,
            'cd %s && %s %s %s' % (folder, python, runner_name, remote)]


def _quote(value):
    import shlex
    return shlex.quote(value)


def job_payload(*, run_id, bundle, bundle_sha256, configuration, parent, parent_files,
                protected_models, runner_sha, library, library_sha, installed, questions,
                class_prior=None, train_sampling=None):
    if not RUN_ID.fullmatch(str(run_id)) or '..' in str(run_id):
        raise ValueError('unsafe run id')
    if any(str(name).startswith('blind') for name in bundle_sha256):
        raise ValueError('blind rows must never be published to the worker')
    payload = {
        'scope': 'unattended_synthetic_research',
        'run_id': run_id,
        'bundle': bundle,
        'bundle_sha256': dict(bundle_sha256),
        'configuration': dict(configuration),
        'parent': parent,
        'parent_files': dict(parent_files),
        'protected_models': dict(protected_models),
        'runner_sha256': runner_sha,
        'library': library,
        'library_sha256': library_sha,
        'installed_sha256': dict(installed),
        'questions': dict(questions),
        'operational_promotion': False,
        'automatic_approval': False,
    }
    if train_sampling:
        if train_sampling not in ('balanced_oversample', 'none'):
            raise ValueError('unknown train sampling mode')
        payload['train_sampling'] = train_sampling
    return payload


def ssh_capture(argv, timeout=60):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    raw = (result.stdout or '').strip().splitlines()
    payload = None
    for line in reversed(raw):
        try:
            payload = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    if result.returncode or payload is None:
        raise RuntimeError('worker action failed: %s' % (payload or 'no json output'))
    return payload


def score_row(row, prediction):
    label = next(iter(row['expected'].values()))
    return label, prediction.get('prediction'), label == prediction.get('prediction')


def compare(rows, predictions_by_model):
    """Same items, same denominator; overflow is reported separately."""
    expected = {row['case_id']: next(iter(row['expected'].values())) for row in rows}
    report = {'items': len(rows), 'models': {}}
    for name, predictions in predictions_by_model.items():
        indexed = {}
        for entry in predictions:
            indexed[entry['case_id']] = entry
        missing = set(expected) - set(indexed)
        foreign = set(indexed) - set(expected)
        if missing or foreign:
            raise ValueError('prediction identity mismatch for %s' % name)
        counts = Counter()
        correct = scored = overflow = 0
        for case_id, gold in expected.items():
            entry = indexed[case_id]
            if entry.get('status') == 'input_overflow':
                overflow += 1
                continue
            scored += 1
            predicted = entry.get('prediction')
            if predicted == gold:
                correct += 1
            if gold == 'changed' and predicted == 'preserved':
                counts['changed_to_preserved'] += 1
            if gold == 'preserved' and predicted in ('changed', 'insufficient'):
                counts['normal_false_alert'] += 1
            if gold == 'changed' and predicted in ('insufficient', None):
                counts['changed_unresolved'] += 1
        report['models'][name] = {
            'accuracy': correct / scored if scored else 0.0,
            'correct': correct, 'scored': scored, 'overflow': overflow,
            'changed_to_preserved': counts['changed_to_preserved'],
            'normal_false_alert': counts['normal_false_alert'],
            'changed_unresolved': counts['changed_unresolved'],
        }
    return report


def better_than(*, candidate, baseline, minimum_gain=0.05):
    """Adoption test that protects the risky direction first."""
    if candidate['overflow'] > baseline['overflow']:
        return False, 'candidate overflows more inputs than baseline'
    if candidate['changed_to_preserved'] > baseline['changed_to_preserved']:
        return False, 'candidate misses more real changes'
    if candidate['accuracy'] < baseline['accuracy'] + minimum_gain:
        return False, 'accuracy gain below the required minimum'
    return True, 'candidate improves accuracy without more missed changes'
