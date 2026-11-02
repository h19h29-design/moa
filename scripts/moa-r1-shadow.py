"""MacBook offline R1 inference only. No training, registry write or NAS API claims.

Use the already installed venv-smoke and app. Input/output must be private files;
the coordinator keeps originals and independent gold outside inference payloads.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

R1_SHA = '13b5e9fa365754bb043cbf725879b76ed0fbca900ca381df10067cd502fa123e'


def validate_input(data):
    if (data.get('mode') != 'shadow_eval_only' or data.get('training_allowed') is not False or
            data.get('promotion_allowed') is not False):
        raise ValueError('shadow_only_policy_required')
    if type(data.get('maximum_seconds')) is not int or not 1 <= data['maximum_seconds'] <= 1800:
        raise ValueError('bounded_inference_required')
    if data.get('checkpoint_sha256') != R1_SHA or data.get('checkpoint_files', {}).get('model.safetensors') != R1_SHA:
        raise ValueError('explicit_r1_checkpoint_required')
    cases = data.get('cases', [])
    ids = [c['case_id'] for c in cases]
    if not cases or len(ids) > 2000 or len(ids) != len(set(ids)):
        raise ValueError('bounded_unique_cases_required')
    if any(not re.fullmatch(r'[A-Za-z0-9_.-]{1,180}', i) for i in ids):
        raise ValueError('unsafe_case_id')


def run(input_path, output_path):
    raw = input_path.read_bytes()
    data = json.loads(raw); validate_input(data)
    input_sha = hashlib.sha256(raw).hexdigest()
    if input_path.stat().st_mode & 0o077:
        raise ValueError('private_input_permissions_required')
    root = Path.home()/'Library/Application Support/MOA-LAYA'
    sys.path.insert(0, str(root/'app'))
    from workers.laya_local import ml, runtime
    from workers.laya_local.policy import atomic_json
    checkpoint = root/'candidates/moa-laya-recovery-20261018-v1-r1/checkpoint'
    if checkpoint.is_symlink(): raise ValueError('checkpoint_symlink_forbidden')
    protected = [root/'MODEL_REGISTRY.json', root/'MODEL_MANIFEST.json']
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected if p.is_file()}
    output_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Never mix partial predictions from different inputs or implementations.
    if output_path.exists(): raise ValueError('output_exists_use_receipt_not_reexecution')
    start = time.monotonic(); done = 0; stop_reason = None
    with runtime.exclusive(root/'device.lock'):
        if not runtime.resources()['verify_allowed']: raise ValueError('resource_gate')
        if (root/'PAUSED').exists(): raise ValueError('worker_paused')
        agent = ml.load_agent(checkpoint, data['checkpoint_files'])
        with output_path.open('x') as out:
            output_path.chmod(0o600)
            for index, case in enumerate(data['cases']):
                if time.monotonic()-start >= data['maximum_seconds']:
                    stop_reason = 'wall_time_limit'; break
                if (root/'PAUSED').exists() or (index % 20 == 0 and not runtime.resources()['verify_allowed']):
                    stop_reason = 'resource_or_pause_gate'; break
                result = ml.predict_payload(agent, case['payload'], R1_SHA)
                # ml.manifest() describes the base; never attribute its revision
                # to this explicitly loaded R1 candidate.
                result['model_revision'] = 'moa-laya-recovery-20261018-v1-r1-shadow'
                result['tokenizer_revision'] = data['checkpoint_files'].get('tokenizer/tokenizer.json')
                record = {'case_id': case['case_id'], 'input_sha256': input_sha,
                    'payload_sha256': hashlib.sha256(json.dumps(case['payload'],ensure_ascii=False,
                        sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                    'checkpoint_sha256': R1_SHA, 'result': result}
                out.write(json.dumps(record,ensure_ascii=False)+'\n'); out.flush(); os.fsync(out.fileno())
                done += 1
                atomic_json(output_path.with_suffix('.progress.json'), {'completed': done, 'total': len(data['cases']),
                    'seconds': time.monotonic()-start, 'training_started': False, 'promotion_allowed': False})
    after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected if p.is_file()}
    receipt = {'status': 'completed' if done == len(data['cases']) else 'partial', 'stop_reason': stop_reason,
        'input_sha256': input_sha, 'checkpoint_sha256': R1_SHA,
        'predictions_sha256': hashlib.sha256(output_path.read_bytes()).hexdigest(),
        'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'completed': done, 'total': len(data['cases']), 'seconds': time.monotonic()-start,
        'model_registry_unchanged': before == after, 'training_started': False,
        'operational_promotion': False, 'human_approvals_created': 0}
    atomic_json(output_path.with_suffix('.receipt.json'), receipt)
    if before != after: raise RuntimeError('protected_model_metadata_changed')
    return receipt


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    try:
        print(json.dumps(run(args.input, args.output)))
    except Exception as error:
        # Never echo source content, payloads, provider logs or credentials.
        print(json.dumps({'status': 'stopped', 'error_type': type(error).__name__}), file=sys.stderr)
        raise SystemExit(1)
