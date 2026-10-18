"""Bounded MacBook protocol adapter, reusing its installed LAYA without replacing it.

Run in the existing venv-smoke; no installs, external models, training or promotion.
The older daemon cannot claim these jobs without this executor's code identity.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import signal
import subprocess
import fcntl
import urllib.request
import urllib.error

ROOT = Path.home() / 'Library/Application Support/MOA-LAYA'
sys.path.insert(0, str(ROOT / 'app'))


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def claimed_job(response):
    if response is None: return None
    if not isinstance(response, dict) or response.get('ok') is not True or not isinstance(response.get('job'), dict):
        raise ValueError('invalid_claim_response_wrapper')
    return response['job']


def identity():
    from workers.laya_local import policy, ml, runtime
    return digest(b''.join(Path(f).read_bytes() for f in (__file__, policy.__file__, ml.__file__, runtime.__file__)))


class Client:
    def __init__(self):
        from workers.laya_local.runtime import connection, read_secret
        self.connection = connection()
        self.worker = self.connection['worker_id']
        self.secret_path = self.connection['token_file']
        read_secret(self.secret_path)  # strict local permissions, no value logged
        self.base = 'http://127.0.0.1:' + str(self.connection['local_tunnel_port'])
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs): raise ValueError('redirect_forbidden')
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.code = identity()

    def call(self, route, body=None, *, headers=None, raw=False):
        from workers.laya_local.runtime import read_secret
        req = urllib.request.Request(self.base + route, data=None if body is None else canonical(body),
              headers={'Authorization': 'Bearer ' + read_secret(self.secret_path),
                       'Content-Type': 'application/json', **(headers or {})})
        with self.opener.open(req, timeout=25) as response:
            if response.status == 204: return None
            content = response.read(1048577)
            if len(content) > 1048576: raise ValueError('response_too_large')
        return content if raw else json.loads(content)

    def ready(self, *, standing=False):
        from workers.laya_local import policy as p
        h = self.call('/v1/worker/handoff')
        schema = ROOT / 'app/contracts/worker-envelope.schema.json'
        actual_questions = p.questions(list(p.CRITERIA))
        vectors = [{'input': v['input'], 'utf8_hex': p.canonical(v['input']).hex(),
                    'sha256': digest(p.canonical(v['input']))} for v in h['canonical_vectors']]
        if (h['contract_hash'] != digest(schema.read_bytes()) or h['contract_version'] != p.CONTRACT or
                h['canonicalization'] != p.CANONICALIZATION or h['rubric_revision'] != p.RUBRIC or
                h['questions'] != actual_questions or h['rubric_sha256'] != digest(p.canonical(actual_questions)) or
                vectors != h['canonical_vectors'] or h['source_block_hash'] != 'sha256-text-utf8' or
                h['max_artifact_bytes'] != 1048576 or h['result_ack'] is not True or
                h['model_upload'] is not False):
            raise ValueError('actual_contract_conformance_mismatch')
        self.handoff = h
        ack = {'worker_id': self.worker, 'contract_hash': digest(schema.read_bytes()),
               'rubric_sha256': digest(p.canonical(actual_questions)), 'questions': actual_questions,
               'canonical_vectors': vectors, 'source_block_hash': 'sha256-text-utf8',
               'max_artifact_bytes': 1048576, 'validator_hash': h['validator_hash'],
               'worker_code_sha256': self.code, 'daemon_ready': standing,
               'execution_mode': 'standing_pull_client' if standing else 'bounded_pull_client', 'executor_ready': True}
        answer = self.call('/v1/worker/ready', ack)
        if not answer.get('accepted'): raise ValueError('readiness_not_accepted')
        private_json(ROOT / 'incoming/WORKER_READY.json', ack)
        return {'worker_id': self.worker, 'execution_mode': ack['execution_mode'],
                'conformance_accepted': True, 'worker_code_sha256': self.code}

    def verify(self, job, agent):
        from workers.laya_local import policy as p, ml, runtime
        p.validate_envelope(job, json.loads((ROOT / 'app/contracts/worker-envelope.schema.json').read_bytes()))
        input_value = {k: job[k] for k in ('job_kind', 'payload', 'source_revision', 'render_revision', 'rubric_revision')}
        if job['input_sha256'] != digest(p.canonical(input_value)): raise ValueError('input_hash_mismatch')
        if job['job_kind'] != 'verify' or not job['job_id'].startswith('job-pilot-'):
            raise ValueError('not_a_new_pilot_verify_job')
        payload = job['payload']; desc = payload['source_manifest_artifact']
        manifest_raw = self.call('/v1/worker/artifacts/' + p.safe_id(desc['artifact_id']), raw=True, headers={
              'X-MOA-Worker-ID': self.worker, 'X-MOA-Job-ID': job['job_id'],
              'X-MOA-Lease-ID': job['lease_id'], 'X-MOA-Fencing-Token': str(job['fencing_token'])})
        manifest = json.loads(manifest_raw)
        if (digest(manifest_raw) != desc['sha256'] or manifest.get('source_revision') != job['source_revision'] or
                manifest.get('scope') != 'section' or manifest.get('complete') is not True or
                manifest.get('block_sha256') != {b['id']: digest(b['text'].encode()) for b in payload['source_blocks']}):
            raise ValueError('source_manifest_not_verified')
        folder = ROOT / 'runs' / (job['job_id'] + '-' + str(job['fencing_token']))
        folder.mkdir(parents=True, mode=0o700, exist_ok=True)
        private_json(folder / 'job.json', job)
        private_json(folder / 'source_manifest.json', manifest)
        stop = threading.Event(); heartbeat_errors = []
        def beat():
            while not stop.is_set():
                try:
                    answer = self.call('/v1/worker/jobs/' + job['job_id'] + '/heartbeat', {
                        'worker_id': self.worker, 'lease_id': job['lease_id'], 'fencing_token': job['fencing_token']})
                    if not answer.get('ok'): heartbeat_errors.append('heartbeat_rejected')
                except Exception: heartbeat_errors.append('heartbeat_failed')
                stop.wait(20)
        thread = threading.Thread(target=beat, daemon=True); thread.start()
        try:
            verified = {**payload, 'source_complete': True, 'source_evidence_verified': True}
            # Completeness is only for this hash-verified section. Parent document
            # coverage remains separate and is never upgraded by a local model.
            with runtime.exclusive(ROOT / 'device.lock'):
                local = ml.predict_payload(agent, verified)
            private_json(folder / 'local-inference.json', local)
        finally:
            stop.set(); thread.join(timeout=30)
        if heartbeat_errors: raise ValueError('heartbeat_not_confirmed')
        result = result_payload(local, job, self.handoff['validator_hash'])
        envelope = {**job, 'message_type': 'result', 'payload': result}
        private_json(folder / 'result.json', envelope)
        ack = self.call('/v1/worker/jobs/' + job['job_id'] + '/complete', {'worker_id': self.worker, 'result': envelope})
        if not ack.get('accepted') or any(ack.get(k) != job[k] for k in ('job_id', 'input_sha256', 'idempotency_key')):
            raise ValueError('result_ack_not_verified')
        private_json(folder / 'ack.json', ack)
        return {'job_id': job['job_id'], 'accepted': True, 'model_status': local['status'],
                'verdict': ack['verdict'], 'actual_model_called': bool(local.get('model_called'))}


def private_json(path, data):
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    from workers.laya_local.policy import atomic_json
    atomic_json(path, data)
    path.chmod(0o600)


def training_stop_reason(resources, elapsed):
    if elapsed >= 2700: return 'wall_budget_exceeded'
    if resources.get('train_allowed') is not True: return 'resource_or_pause_gate'
    return None


def supervise_training(child, resources):
    started = time.monotonic(); stopped = threading.Event(); reason = None
    signals = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
    previous = {s:signal.signal(s, lambda *_:stopped.set()) for s in signals}
    try:
        while child.poll() is None:
            reason = 'parent_stopped' if stopped.is_set() else training_stop_reason(resources(), time.monotonic()-started)
            if reason: break
            stopped.wait(2)
        return reason
    finally:
        if child.poll() is None:
            child.terminate()
            grace = max(0, min(20, 2700-(time.monotonic()-started)))
            try: child.wait(timeout=grace)
            except subprocess.TimeoutExpired: child.kill(); child.wait()
        for s, handler in previous.items(): signal.signal(s, handler)


def shard_path(folder, name):
    import re
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]+\.jsonl', name):
        raise ValueError('shard_path')
    p = folder / name
    if p.is_symlink(): raise ValueError('shard_path')
    return p


def resource_retry_allowed(parent, progress, *, completed, retries):
    return (parent.get('status') == 'interrupted' and parent.get('reason') == 'resource_or_pause_gate'
            and isinstance(progress.get('optimizer_updates'), int) and 0 <= progress['optimizer_updates'] <= 1
            and not completed and retries == 0)


def bundle_rows():
    from workers.laya_local import policy as p
    folder = ROOT / 'incoming/moa-ko-synthetic-20261018-v1'
    raw = (folder / 'TRAINING_BUNDLE_READY.json').read_bytes()
    receipt = json.loads((folder / 'SSH_RECEIPT.json').read_bytes())
    if digest(raw) != receipt['manifest_sha256']: raise ValueError('training_manifest_hash')
    manifest = json.loads(raw)
    if (manifest.get('maximum_epochs') != 1 or manifest.get('maximum_active_seconds') != 2700 or
            manifest.get('maximum_optimizer_updates') != 200):
        raise ValueError('training_budget_manifest')
    rows = []
    for shard in manifest['shards']:
        content = shard_path(folder, shard['file']).read_bytes()
        if len(content) > 524288 or len(content) != shard['bytes'] or digest(content) != shard['sha256']:
            raise ValueError('training_shard_hash')
        parsed = [json.loads(line) for line in content.splitlines() if line.strip()]
        if len(parsed) != shard['rows']: raise ValueError('training_shard_rows')
        rows.extend(parsed)
    p.validate_dataset(rows, manifest, minimum=True)
    return rows, manifest, digest(raw)


def train_child(folder):
    from workers.laya_local import ml
    # Independent hard deadline also survives an unexpected parent SIGKILL.
    signal.alarm(2700)
    rows, manifest, mhash = bundle_rows()
    request = json.loads((folder / 'reservation.json').read_bytes())
    if request['manifest_sha256'] != mhash: raise ValueError('training_manifest_changed')
    # Clear only unused MPS allocations between variable-length examples, never
    # lower the RAM/pressure gate or alter weights, dtype, labels or examples.
    import torch
    import laya.train as engine
    original_load = engine.load_checkpoint
    def low_cache_load(*args, **kwargs):
        model, tok, cfg = original_load(*args, **kwargs)
        def clear_unused(_model, _args):
            torch.mps.synchronize(); torch.mps.empty_cache()
        model.encoder.register_forward_pre_hook(clear_unused)
        return model, tok, cfg
    engine.load_checkpoint = low_cache_load
    result = ml.train(rows, manifest, ROOT / 'candidates' / folder.name)
    private_json(folder / 'training-result.json', result)
    signal.alarm(0)


def train_bundle(*, retry_resource=False):
    """Explicit local training, not a fictitious model-upload API job.

    The parent owns the device lock and enforces resources and a45min wall
    deadline across preflight, baseline evaluation, training, reload and eval.
    Existing trainer weights/configuration and champion remain untouched.
    """
    from workers.laya_local import runtime, policy
    rows, manifest, mhash = bundle_rows()
    folder = ROOT / 'runs' / ('train-pilot-' + mhash[:20])
    if (folder / 'training-result.json').is_file():
        result = json.loads((folder / 'training-result.json').read_bytes())
        return {'run_id':folder.name, 'status':result['status'], 'duplicate':True}
    retry_of = None
    if folder.exists():
        if not retry_resource: raise ValueError('training_attempt_already_reserved')
        parent = json.loads((folder / 'PARENT_RESULT.json').read_bytes())
        candidate = ROOT / 'candidates' / folder.name
        progress = json.loads((candidate / 'progress.json').read_bytes())
        retries = len(list((ROOT / 'runs').glob(folder.name+'-resource-retry*')))
        if not resource_retry_allowed(parent, progress, completed=(candidate / 'TRAIN_RESULT.json').exists(), retries=retries):
            raise ValueError('resource_retry_not_eligible')
        # Preserve all prior reservations. The only duplicate exception is this
        # one early resource abort, not a completed/tuned candidate. Weekly and
        # historical split boundaries remain enforced across both attempts.
        import datetime
        week = datetime.datetime.now(datetime.timezone.utc).isocalendar()[:2]
        reservations = list((ROOT / 'runs').glob('*/reservation.json'))
        weekly = 0
        for path in reservations:
            prior = json.loads(path.read_bytes())
            if datetime.datetime.fromisoformat(prior['reserved_at']).isocalendar()[:2] == week: weekly += 1
            for row in rows:
                for identity in (row['meta']['original_group_id'],row['meta']['family_group_id'],row['meta']['source_sha256']):
                    old_split = prior.get('split_ledger', {}).get(identity)
                    if old_split and old_split != row['meta']['split']: raise ValueError('historical_eval_group_leakage')
            if path.parent != folder and (prior['dataset_sha256'] == policy.digest(rows) or
                    set(prior.get('decision_signatures', [])) & set(runtime.decision_signatures(rows))):
                raise ValueError('training_decisions_already_attempted')
        if weekly >= 2: raise ValueError('weekly_run_limit')
        retry_of = folder.name
        folder = folder.with_name(folder.name+'-resource-retry1')
    else:
        runtime.run_limits(policy.digest(rows), rows)
    if training_stop_reason(runtime.resources(), 0): raise ValueError('training_resources_not_ready')
    with runtime.exclusive(ROOT / 'device.lock'):
        folder.mkdir(mode=0o700, parents=True)
        ledger = {identity:r['meta']['split'] for r in rows for identity in
                  (r['meta']['original_group_id'],r['meta']['family_group_id'],r['meta']['source_sha256'])}
        import datetime
        private_json(folder / 'reservation.json', {'manifest_sha256':mhash, 'dataset_sha256':policy.digest(rows),
                     'decision_signatures':runtime.decision_signatures(rows), 'split_ledger':ledger,
                     'reserved_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                     'max_wall_seconds':2700, 'max_epochs':1, 'max_updates':128, 'encoder_frozen':True,
                     'retry_of':retry_of,'memory_policy':'empty_unused_mps_cache_before_encoder_v1'})
        started = time.monotonic(); reason = None
        logpath = folder / 'training.log'
        with logpath.open('ab') as log:
            logpath.chmod(0o600)
            child = subprocess.Popen([sys.executable, __file__, 'train-child', '--run-folder', str(folder)],
                                     stdout=log, stderr=log, cwd=ROOT / 'app')
            last_resources = {}
            def check_resources():
                nonlocal last_resources
                last_resources = runtime.resources()
                return last_resources
            reason = supervise_training(child, check_resources)
        outcome = {'run_id':folder.name, 'status':'completed' if child.returncode == 0 else 'interrupted',
                   'reason':reason, 'exit_code':child.returncode, 'seconds':time.monotonic()-started,
                   'manifest_sha256':mhash, 'champion_changed':False}
        if reason: outcome['stop_resources'] = last_resources
        private_json(folder / 'PARENT_RESULT.json', outcome)
        return outcome


def device_available():
    with (ROOT / 'device.lock').open('a') as f:
        try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return False
        fcntl.flock(f, fcntl.LOCK_UN)
        return True


def flush_results(client):
    """Replay lost complete responses, never silently discard an old lease."""
    for folder in sorted((ROOT / 'runs').glob('job-pilot-*')):
        path = folder / 'result.json'
        if not path.is_file() or (folder / 'ack.json').exists() or (folder / 'stale.json').exists(): continue
        result = json.loads(path.read_bytes())
        try:
            ack = client.call('/v1/worker/jobs/' + result['job_id'] + '/complete',
                              {'worker_id':client.worker, 'result':result})
            if not ack.get('accepted') or any(ack.get(k) != result[k] for k in
                    ('job_id','lease_id','fencing_token','input_sha256','idempotency_key')):
                raise ValueError('outbox_ack_not_verified')
            private_json(folder / 'ack.json', ack)
        except urllib.error.HTTPError as e:
            if e.code != 409: raise
            private_json(folder / 'stale.json', {'status':'stale', 'http_status':409})


def standing_worker():
    from workers.laya_local import runtime, ml
    stop = threading.Event(); agent = None; client = None; last_ready = 0
    for sig in (signal.SIGTERM, signal.SIGINT): signal.signal(sig, lambda *_: stop.set())
    def status(state, **extra):
        private_json(ROOT / 'status.json', {'state':state,'execution_mode':'standing_pull_client',
                     'pid':os.getpid(),'updated_at':time.time(),'auto_promotion':False, **extra})
    with runtime.exclusive(ROOT / 'worker.lock'):
        while not stop.is_set():
            job = None
            try:
                if (ROOT / 'PAUSED').exists(): status('paused'); stop.wait(5); continue
                resources = runtime.resources()
                if not resources['verify_allowed'] or not device_available():
                    status('awaiting_resources',resources=resources); stop.wait(5); continue
                if client is None: client = Client()
                if time.monotonic() - last_ready >= 60:
                    client.ready(standing=True); last_ready = time.monotonic()
                flush_results(client)
                job = claimed_job(client.call('/v1/worker/claim', {'worker_id':client.worker,
                                  'kinds':['verify'],'worker_code_sha256':client.code}))
                if not job:
                    status('ready',worker_code_sha256=client.code,daemon_ready=True); stop.wait(10); continue
                status('verifying',job_id=job['job_id'],daemon_ready=True)
                if agent is None: agent = ml.load_agent()
                answer = client.verify(job, agent)
                status('ready',last_job=answer,daemon_ready=True,worker_code_sha256=client.code)
            except Exception as error:
                if job and client:
                    try:
                        client.call('/v1/worker/jobs/'+job['job_id']+'/fail', {'worker_id':client.worker,
                                    'lease_id':job['lease_id'],'fencing_token':job['fencing_token'],
                                    'code':'standing_execution_error','retryable':False})
                    except Exception: pass
                status('deferred_connection_or_policy',error_type=type(error).__name__)
                client = None; last_ready = 0; stop.wait(15)
        status('stopped')


def result_payload(local, job, validator_hash):
    from workers.laya_local import policy as p
    criteria = local.get('criteria', {})
    coverage = local.get('coverage', {})
    return {'status': local['status'], 'validator_hash': validator_hash,
            'rubric_sha256': digest(p.canonical(p.questions(list(p.CRITERIA)))),
            **{k: local.get(k) for k in ('checkpoint_sha256', 'model_revision', 'tokenizer_revision', 'calibration_revision')},
            'answers': {q: {'label': a['label'], 'raw_probabilities': a['raw_distribution'],
                           'calibrated_probabilities': None, 'calibrated': False,
                           'model_label': a['model_label']} for q, a in criteria.items()},
            'inspected_source_ids': coverage.get('source_ids', []), 'inspected_render_ids': coverage.get('render_ids', []),
            'missing_context_ids': coverage.get('missing_context_ids', []),
            'actual_tokens': max((a['actual_tokens'] for a in criteria.values()), default=0),
            'truncation': bool(local.get('truncation')), 'warnings': ['uncalibrated_for_moa', 'shadow_only'],
            'raw_local_inference': local}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['ready', 'run', 'receipt', 'daemon', 'train', 'train-child'])
    parser.add_argument('--run-folder', type=Path)
    parser.add_argument('--retry-resource', action='store_true', help='at most one early resource-abort restart; no quality tuning')
    parser.add_argument('--max-jobs', type=int, default=100)
    parser.add_argument('--max-seconds', type=int, default=1800)
    args = parser.parse_args()
    if not 1 <= args.max_jobs <= 100 or not 1 <= args.max_seconds <= 2700:
        raise ValueError('bounded_run_limits')
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', WANDB_DISABLED='true')
    if args.action == 'daemon': standing_worker(); return
    if args.action == 'train': print(json.dumps(train_bundle(retry_resource=args.retry_resource))); return
    if args.action == 'train-child':
        folder = args.run_folder.resolve()
        if folder.parent != (ROOT / 'runs').resolve() or not folder.name.startswith('train-pilot-'):
            raise ValueError('training_run_path')
        train_child(folder); return
    client = Client()
    if args.action == 'receipt':
        path = ROOT / 'incoming/moa-ko-synthetic-20261018-v1/SSH_RECEIPT.json'
        receipt = json.loads(path.read_bytes())
        print(json.dumps(client.call('/v1/worker/training-receipt', receipt))); return
    print(json.dumps(client.ready()), flush=True)
    if args.action == 'ready': return
    from workers.laya_local import ml, runtime
    if not runtime.resources()['verify_allowed']: raise ValueError('verify_resources_not_ready')
    agent = ml.load_agent()
    started = time.monotonic()
    for _ in range(args.max_jobs):
        if time.monotonic() - started >= args.max_seconds: break
        if not runtime.resources()['verify_allowed']: break
        job = claimed_job(client.call('/v1/worker/claim', {'worker_id': client.worker, 'kinds': ['verify'], 'worker_code_sha256': client.code}))
        if not job: break
        print(json.dumps(client.verify(job, agent)), flush=True)


if __name__ == '__main__':
    try: main()
    except Exception as error:
        import re
        detail = str(error)
        safe_reason = detail if re.fullmatch(r'[a-z0-9_:-]{1,120}', detail) else 'bounded_pilot_stopped'
        print(json.dumps({'error': type(error).__name__, 'reason': safe_reason,
                          'http_status': error.code if isinstance(error, urllib.error.HTTPError) else None}), file=sys.stderr)
        raise SystemExit(1)
