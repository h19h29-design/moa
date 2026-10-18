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

    def ready(self):
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
               'worker_code_sha256': self.code, 'daemon_ready': False,
               'execution_mode': 'bounded_pull_client', 'executor_ready': True}
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
    parser.add_argument('action', choices=['ready', 'run', 'receipt'])
    parser.add_argument('--max-jobs', type=int, default=100)
    parser.add_argument('--max-seconds', type=int, default=1800)
    args = parser.parse_args()
    if not 1 <= args.max_jobs <= 100 or not 1 <= args.max_seconds <= 2700:
        raise ValueError('bounded_run_limits')
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', WANDB_DISABLED='true')
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
