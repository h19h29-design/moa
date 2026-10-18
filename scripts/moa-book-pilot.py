"""Bounded MacBook protocol adapter, reusing its installed LAYA without replacing it.

Run in the existing venv-smoke; no installs, external models or promotion.
Training is a separate explicit, resource-gated command, never a daemon claim.
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


MODEL_INPUT_REVISION = 'moa.laya.runtime-input.v1'


def prepare_training_rows(rows, question_definitions):
    """Match policy.state_for/questions insertion order, without changing facts/gold.

    Canonical JSON hashes intentionally ignore key order; LAYA's serialize_state
    does not. Never rewrite immutable bundle files or silently coerce a rubric.
    Nested maps follow the canonical wire representation used by NAS jobs.
    """
    prepared = json.loads(canonical(rows))
    fields = ('title', 'file_role', 'source_complete', 'source_blocks',
              'rendered_blocks', 'attachment_manifest')
    for row in prepared:
        if set(row['state']) != set(fields): raise ValueError('training_state_fields')
        row['state'] = {key: row['state'][key] for key in fields}
        for key, question in row['questions'].items():
            if key not in question_definitions or question != question_definitions[key]:
                raise ValueError('training_rubric_mismatch')
        row['questions'] = json.loads(json.dumps({key: question_definitions[key] for key in row['questions']}))
    return prepared


def quality_metrics(records, *, breakdown=True):
    """Raw model accuracy is not the rules' verdict or a release gate."""
    from collections import Counter
    labels = ('preserved', 'changed', 'insufficient')
    if any(r['expected'] not in labels or r['prediction'] not in labels for r in records):
        raise ValueError('quality_label')
    n = len(records)
    cm = {a: {b: 0 for b in labels} for a in labels}
    for r in records: cm[r['expected']][r['prediction']] += 1
    gold = Counter(r['expected'] for r in records)
    recall = {a: cm[a][a]/gold[a] if gold[a] else None for a in labels}
    f1 = []
    for a in labels:
        denom = gold[a] + sum(cm[b][a] for b in labels)
        f1.append(2*cm[a][a]/denom if denom else 0)
    observed = [v for v in recall.values() if v is not None]
    out = {'items': n, 'accuracy': sum(cm[a][a] for a in labels)/n if n else None,
           'majority_baseline': max(gold.values())/n if n else None,
           'gold_counts': dict(gold), 'prediction_counts': dict(Counter(r['prediction'] for r in records)),
           'confusion': cm, 'recall': recall, 'balanced_accuracy': sum(observed)/len(observed) if observed else None,
           'macro_f1': sum(f1)/len(f1) if n else None, 'changed_recall': recall['changed'],
           'changed_missed_as_preserved': cm['changed']['preserved'],
           'unsafe_false_preserved': cm['changed']['preserved'] + cm['insufficient']['preserved'],
           'auto_promotion_allowed': False}
    for name, actual, predicted in (
            ('changed_to_preserved_rate','changed','preserved'),
            ('changed_to_insufficient_rate','changed','insufficient'),
            ('insufficient_to_preserved_rate','insufficient','preserved'),
            ('preserved_to_changed_rate','preserved','changed')):
        out[name] = cm[actual][predicted]/gold[actual] if gold[actual] else None
    predicted_preserved = sum(cm[a]['preserved'] for a in labels)
    out['predicted_preserved_error_rate'] = out['unsafe_false_preserved']/predicted_preserved if predicted_preserved else None
    if breakdown:
        for field in ('criterion', 'mutation'):
            out['by_'+field] = {key: quality_metrics([r for r in records if r.get(field)==key], breakdown=False)
                               for key in sorted({r[field] for r in records if field in r})}
    return out


QUALITY_CONFIG = {'epochs':1, 'micro_batch':1, 'grad_accum':5,
                  'head_lr':0.0003, 'min_lr':0.000001, 'shuffle_options':['choice'],
                  'freeze_encoder':True, 'amp':False, 'gradient_checkpointing':False,
                  'max_len':1024, 'head_max_len':256, 'loss':'soft-ce',
                  'calib_frac':0, 'calib_max':0, 'log_every':50, 'seed':20261018}


def reserve_quality_run(root, rows, manifest_sha, permit, now, *, recovery=False):
    """One user-authorized additional run, not a permanent weekly-limit change."""
    approval = 'explicit_user_one_local_resource_recovery' if recovery else 'explicit_user_one_additional_local_run'
    if (permit.get('approval')!=approval or
            permit.get('additional_runs')!=1 or permit.get('manifest_sha256')!=manifest_sha or
            permit.get('maximum_active_seconds')!=2700 or permit.get('maximum_optimizer_updates')!=120 or
            permit.get('encoder_frozen') is not True or permit.get('paid_models_allowed') is not False or
            permit.get('operational_promotion_allowed') is not False):
        raise ValueError('quality_approval')
    suffix = '-accuracy-recovery-01' if recovery else '-accuracy-01'
    folder = root/'runs'/('train-pilot-'+manifest_sha[:20]+suffix)
    if folder.exists(): raise ValueError('quality_attempt_already_reserved')
    if recovery:
        failed = root/'runs'/('train-pilot-'+manifest_sha[:20]+'-accuracy-01')
        candidate = root/'candidates'/failed.name
        prior_parent = json.loads((failed/'PARENT_RESULT.json').read_bytes())
        prior_progress = json.loads((candidate/'progress.json').read_bytes())
        tail = (failed/'training.log').read_bytes()[-4096:]
        if (prior_parent.get('status')!='interrupted' or prior_parent.get('exit_code')!=1 or
                prior_progress.get('optimizer_updates')!=120 or
                b'resource_or_pause_gate' not in tail or (candidate/'checkpoint/model.safetensors').exists()):
            raise ValueError('quality_recovery_not_eligible')
    ledger = {identity:r['meta']['split'] for r in rows for identity in
              (r['meta']['original_group_id'],r['meta']['family_group_id'],r['meta']['source_sha256'])}
    dataset_sha = digest(canonical(rows)); prior_complete = False; weekly = 0
    for path in (root/'runs').glob('*/reservation.json'):
        import datetime
        prior = json.loads(path.read_bytes())
        if datetime.datetime.fromisoformat(prior['reserved_at']).isocalendar()[:2]==now.isocalendar()[:2]: weekly += 1
        for key, split in ledger.items():
            if key in prior.get('split_ledger',{}) and prior['split_ledger'][key]!=split:
                raise ValueError('historical_eval_group_leakage')
        outcome = path.parent/'training-result.json'
        if prior.get('dataset_sha256')==dataset_sha and prior.get('manifest_sha256')==manifest_sha and outcome.is_file():
            prior_complete |= json.loads(outcome.read_bytes()).get('status')=='completed'
    if weekly >= (4 if recovery else 3): raise ValueError('weekly_run_limit')
    if not prior_complete: raise ValueError('quality_requires_completed_baseline_run')
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    # Exclusive creation is the durable one-time consumption of the approval.
    reservation = {'manifest_sha256':manifest_sha,'dataset_sha256':dataset_sha,'split_ledger':ledger,
                   'reserved_at':now.isoformat(),'max_wall_seconds':2700,'max_epochs':1,'max_updates':120,
                   'encoder_frozen':True,'training_config':QUALITY_CONFIG,
                   'model_input_revision':MODEL_INPUT_REVISION,'approval':permit,
                   'weekly_limit_exception':approval,'champion_changed':False}
    (folder/'reservation.json').write_bytes(canonical(reservation))
    (folder/'reservation.json').chmod(0o600)
    return folder


def evaluate_quality_model(model, tok, rows):
    import torch
    from workers.laya_local import ml
    from laya.train import items_from_rows, calibration_records
    maximum_tokens = 0
    for row in rows:
        measured = ml.preflight(tok, row['state'], row['questions'])
        maximum_tokens = max(maximum_tokens,max(v['actual_tokens'] for v in measured.values()))
    items, skipped = items_from_rows(tok, rows, 1024, 256)
    if skipped or len(items)!=len(rows): raise ValueError('audit_skipped_rows')
    rec = calibration_records(model,tok,items,torch.device('mps'),1024,256,batch_size=1)
    predictions = []
    for row, record in zip(rows, rec):
        qid, q = next(iter(row['questions'].items()))
        labels = list(q['criteria'])
        target = labels[int(torch.tensor(record[2]).argmax())]
        if target!=row['expected'][qid]: raise ValueError('audit_target_mapping')
        probs = torch.tensor(record[1],dtype=torch.float64).softmax(0).tolist()
        predictions.append({'criterion':qid,'mutation':row['meta']['mutation']['operation'],
            'source_group':row['meta']['original_group_id'],'family':row['meta']['family_group_id'],
            'expected':target,'prediction':labels[max(range(len(probs)),key=probs.__getitem__)],
            'probabilities':dict(zip(labels,probs))})
    return {'metrics':quality_metrics(predictions),'predictions':predictions,'maximum_tokens':maximum_tokens}


def persist_quality_checkpoint(output, evidence, writer):
    """Commit trained weights before any potentially resource-gated evaluation."""
    path = output/'checkpoint'
    hashes = writer(path)
    model_file = path/'model.safetensors'
    if not model_file.is_file() or model_file.is_symlink(): raise ValueError('checkpoint_file_missing')
    with model_file.open('rb') as stream:
        actual = hashlib.file_digest(stream,'sha256').hexdigest()
        os.fsync(stream.fileno())
    if hashes.get('model.safetensors')!=actual: raise ValueError('checkpoint_digest_mismatch')
    result = {**evidence,'status':'checkpoint_saved_pending_evaluation','checkpoint':str(path),'sha256':hashes}
    private_json(output/'TRAIN_RESULT.json',result)
    private_json(output/'progress.json',{'status':result['status'],'optimizer_updates':result['optimizer_updates'],
                                         'maximum_updates':120})
    return result


def quality_train_child(folder):
    """Single predeclared experiment. No tuning, early selection or promotion."""
    import math
    import torch
    from workers.laya_local import ml, policy, runtime
    from laya.train import TrainConfig, items_from_rows, train_model, load_checkpoint, save_checkpoint, optimizer_updates
    signal.alarm(2700)
    started = time.monotonic()
    rows, manifest, mhash = bundle_rows()
    request = json.loads((folder/'reservation.json').read_bytes())
    if (request['manifest_sha256']!=mhash or request['training_config']!=QUALITY_CONFIG or
            request['dataset_sha256']!=policy.digest(rows)):
        raise ValueError('quality_reservation_mismatch')
    prepared = prepare_training_rows(rows,policy.questions(list(policy.CRITERIA)))
    policy.validate_dataset(prepared,manifest,minimum=True)
    splits = {s:[r for r in prepared if r['meta']['split']==s] for s in ('train','calibration','validation')}
    output = ROOT/'candidates'/folder.name
    output.mkdir(mode=0o700,parents=True,exist_ok=False)
    private_json(output/'DATA_MANIFEST.json',manifest)
    base = ml.manifest(); ml.verify_model(base['path'],base['sha256'])
    model,tok,cfg = load_checkpoint(base['path'])
    model.encoder.config.reference_compile = False
    model.to('mps'); model.encoder.requires_grad_(False)
    stop = threading.Event()
    for sig in (signal.SIGTERM,signal.SIGINT): signal.signal(sig,lambda *_:stop.set())
    def resource_gate(_model,_args):
        resources = runtime.resources()
        if stop.is_set() or training_stop_reason(resources,time.monotonic()-started):
            private_json(folder/'RESOURCE_STOP.json',{'phase':'model_forward','resources':resources,
                                                       'elapsed':time.monotonic()-started})
            raise InterruptedError('resource_or_pause_gate')
        torch.mps.synchronize(); torch.mps.empty_cache()
    model.encoder.register_forward_pre_hook(resource_gate)
    for row in prepared: ml.preflight(tok,row['state'],row['questions'])
    items,skipped = items_from_rows(tok,splits['train'],1024,256)
    if skipped or len(items)!=600: raise ValueError('quality_training_inventory')
    config = TrainConfig(**QUALITY_CONFIG)
    budget = optimizer_updates(len(items),config.micro_batch,config.grad_accum,config.epochs)
    if budget!=120 or budget>request['max_updates']: raise ValueError('quality_update_budget')
    # Both formats and all knobs are frozen in reservation BEFORE these results.
    before_dev = evaluate_quality_model(model,tok,splits['calibration'])
    before_test = evaluate_quality_model(model,tok,splits['validation'])
    private_json(folder/'BASELINE_EVALUATION.json',{'development':before_dev,'held_out':before_test})
    before = ml.parameter_hash(model); encoder_before = ml.parameter_hash(model,encoder=True)
    steps = 0; real_step = torch.optim.AdamW.step
    def step(optimizer,*args,**kwargs):
        nonlocal steps
        resources = runtime.resources()
        if steps>=budget or stop.is_set() or training_stop_reason(resources,time.monotonic()-started):
            private_json(folder/'RESOURCE_STOP.json',{'phase':'optimizer_boundary','resources':resources,
                                                       'optimizer_updates':steps,'elapsed':time.monotonic()-started})
            raise InterruptedError('quality_training_stop')
        result = real_step(optimizer,*args,**kwargs)
        torch.mps.synchronize(); steps += 1
        private_json(output/'progress.json',{'status':'training','optimizer_updates':steps,'maximum_updates':budget})
        return result
    torch.optim.AdamW.step = step
    try:
        history = train_model(model,tok,items,config,torch.device('mps'),1024,256)
    finally:
        torch.optim.AdamW.step = real_step
    after = ml.parameter_hash(model)
    if steps!=budget or before==after or encoder_before!=ml.parameter_hash(model,encoder=True) or any(not math.isfinite(v) for v in history):
        raise ValueError('quality_optimizer_evidence')
    exported = ml.parameter_hash(model,export_roundtrip=True)
    evidence = {'optimizer_updates':steps,'training_config':QUALITY_CONFIG,
              'manifest_sha256':mhash,'dataset_sha256':policy.digest(rows),'model_input_revision':MODEL_INPUT_REVISION,
              'counts':{s:len(r) for s,r in splits.items()},'loss_history':history,
              'head_parameter_sha256_before':before,'head_parameter_sha256_after':after,
              'exported_head_parameter_sha256':exported,'reload_verified':False,
              'encoder_unchanged':True,'base_checkpoint_sha256':base['sha256']['model.safetensors'],
              'development_before':before_dev,'held_out_before':before_test,
              'held_out_is_small_synthetic_development_test':True,'calibration_status':'unfitted',
              'promotion':'blocked_insufficient_independent_gold','champion_changed':False,
              'seconds':time.monotonic()-started,'paid_model_calls':0}
    def save(path):
        save_checkpoint(model,tok,dict(cfg,max_len=1024,head_max_len=256,temperature=[1,1,1],temperature_by_options={}),str(path))
        return ml.checkpoint_hashes(path)
    result = persist_quality_checkpoint(output,evidence,save)
    private_json(folder/'training-result.json',result)
    signal.alarm(0)


def evaluate_quality_candidate(folder):
    """Separate verification process; retries never consume another optimizer run."""
    from workers.laya_local import ml,policy,runtime
    import torch
    path = ROOT/'candidates'/folder.name
    result = json.loads((path/'TRAIN_RESULT.json').read_bytes())
    if result['status']=='completed': return {'status':'completed','duplicate':True,'run_id':folder.name}
    if result['status']!='checkpoint_saved_pending_evaluation': raise ValueError('quality_checkpoint_not_ready')
    if not runtime.resources()['verify_allowed']: raise ValueError('verify_resources_not_ready')
    rows,manifest,mhash = bundle_rows()
    if result['manifest_sha256']!=mhash or result['training_config']!=QUALITY_CONFIG:
        raise ValueError('quality_checkpoint_metadata')
    prepared = prepare_training_rows(rows,policy.questions(list(policy.CRITERIA)))
    signal.alarm(900)
    try:
        with runtime.exclusive(ROOT/'device.lock'):
            agent = ml.load_agent(Path(result['checkpoint']),result['sha256'])
            def resource_gate(_model,_args):
                if not runtime.resources()['verify_allowed']: raise InterruptedError('evaluation_resource_gate')
                torch.mps.synchronize(); torch.mps.empty_cache()
            agent.model.encoder.register_forward_pre_hook(resource_gate)
            reloaded = ml.parameter_hash(agent.model)
            if reloaded!=result['exported_head_parameter_sha256']: raise ValueError('quality_reload_parameter_mismatch')
            reports = {split:evaluate_quality_model(agent.model,agent.tok,[r for r in prepared if r['meta']['split']==split])
                       for split in ('calibration','validation','train')}
            from workers.laya_local.fixtures import evaluation_cases
            reload_inference = ml.predict_payload(agent,evaluation_cases()[0]['payload'],result['sha256']['model.safetensors'])
            result.update(status='completed',reload_verified=True,reload_parameter_sha256=reloaded,
                          development_after=reports['calibration'],held_out_after=reports['validation'],
                          train_diagnostic=reports['train'],reload_inference=reload_inference,
                          metrics_stage='actual_reloaded_fp16_checkpoint',champion_changed=False)
            private_json(path/'TRAIN_RESULT.json',result)
            private_json(folder/'training-result.json',result)
            private_json(path/'progress.json',{'status':'completed','optimizer_updates':120,'maximum_updates':120})
            return {'status':'completed','run_id':folder.name,'checkpoint_sha256':result['sha256']['model.safetensors'],
                    'metrics':{k:{field:v for field,v in data['metrics'].items() if not field.startswith('by_')}
                               for k,data in reports.items()},'champion_changed':False}
    finally:
        signal.alarm(0)


def improve_quality(*, recovery=False):
    from workers.laya_local import runtime
    import datetime
    rows,manifest,mhash = bundle_rows()
    permit_path = ROOT/'incoming'/('QUALITY_RECOVERY_APPROVAL_20261018.json' if recovery else 'QUALITY_RUN_APPROVAL_20261018.json')
    stat = permit_path.lstat()
    if permit_path.is_symlink() or stat.st_uid!=os.getuid() or stat.st_mode & 0o077:
        raise ValueError('quality_approval_permissions')
    permit = json.loads(permit_path.read_bytes())
    if training_stop_reason(runtime.resources(),0): raise ValueError('training_resources_not_ready')
    with runtime.exclusive(ROOT/'device.lock'):
        folder = reserve_quality_run(ROOT,rows,mhash,permit,datetime.datetime.now(datetime.timezone.utc),recovery=recovery)
        with (folder/'training.log').open('ab') as log:
            (folder/'training.log').chmod(0o600)
            started = time.monotonic()
            child = subprocess.Popen([sys.executable,__file__,'quality-child','--run-folder',str(folder)],
                                     stdout=log,stderr=log,cwd=ROOT/'app')
            reason = supervise_training(child,runtime.resources)
        saved = ROOT/'candidates'/folder.name/'TRAIN_RESULT.json'
        saved_status = json.loads(saved.read_bytes())['status'] if saved.exists() else None
        stop_path = folder/'RESOURCE_STOP.json'
        stop_record = json.loads(stop_path.read_bytes()) if stop_path.exists() else None
        outcome = {'run_id':folder.name,'status':saved_status if saved_status else 'interrupted',
                   'reason':reason,'exit_code':child.returncode,'seconds':time.monotonic()-started,
                   'resource_stop':stop_record,'champion_changed':False,'approval_consumed':True}
        private_json(folder/'PARENT_RESULT.json',outcome)
        return outcome


def claimed_job(response):
    if response is None: return None
    if not isinstance(response, dict) or response.get('ok') is not True or not isinstance(response.get('job'), dict):
        raise ValueError('invalid_claim_response_wrapper')
    return response['job']


ACK_KEYS = ('job_id', 'lease_id', 'fencing_token', 'input_sha256', 'idempotency_key')


def confirm_ack(ack, job):
    required = ('job_id', 'input_sha256', 'idempotency_key')
    if not isinstance(ack, dict) or ack.get('accepted') is not True or any(
            key not in ack or ack[key] != job[key] for key in required) or any(
            key in ack and ack[key] != job[key] for key in ('lease_id','fencing_token')):
        raise ValueError('result_ack_not_verified')


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
        confirm_ack(ack, job)
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
    from workers.laya_local import policy
    prepared = prepare_training_rows(rows, policy.questions(list(policy.CRITERIA)))
    result = ml.train(prepared, manifest, ROOT / 'candidates' / folder.name)
    result['model_input_revision'] = MODEL_INPUT_REVISION
    result['raw_bundle_dataset_sha256'] = policy.digest(rows)
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


def audit_quality(*, split='calibration', formats=('artifact', 'runtime')):
    """Read-only, resource-gated local audit; never retune against held-out test.

    Keeps per-criterion/mutation raw predictions. Does not use the model's own
    output as gold, fit temperatures, modify checkpoints, or reserve a train run.
    """
    if split not in ('train', 'calibration'): raise ValueError('audit_split_forbidden')
    if not formats or any(v not in ('artifact', 'runtime') for v in formats):
        raise ValueError('audit_input_format')
    import gc
    import torch
    from workers.laya_local import ml, policy, runtime
    from laya.train import load_checkpoint, items_from_rows, calibration_records
    rows, manifest, mhash = bundle_rows()
    rows = [r for r in rows if r['meta']['split']==split]
    prior = ROOT / 'candidates/train-pilot-ef81fadafc79bb2cfdee-resource-retry1/TRAIN_RESULT.json'
    trained = json.loads(prior.read_bytes())
    base = ml.manifest()
    checkpoints = {'base': (Path(base['path']), base['sha256']),
                   'candidate': (Path(trained['checkpoint']), trained['sha256'])}
    report = {'purpose':'development_diagnostic_not_release_evaluation', 'split':split,
              'validation_used':False, 'training_performed':False, 'manifest_sha256':mhash,
              'model_input_revision':MODEL_INPUT_REVISION, 'models':{},
              'champion_changed':False, 'auto_promotion_allowed':False}
    signal.alarm(900)
    try:
        with runtime.exclusive(ROOT/'device.lock'):
            for name, (path, hashes) in checkpoints.items():
                if not runtime.resources()['verify_allowed']: raise ValueError('audit_resources')
                ml.verify_model(path, hashes)
                model, tok, cfg = load_checkpoint(str(path))
                model.encoder.config.reference_compile = False
                model.to('mps').eval()
                def resource_gate(_model, _args):
                    if not runtime.resources()['verify_allowed']: raise ValueError('audit_resources')
                    torch.mps.synchronize(); torch.mps.empty_cache()
                model.encoder.register_forward_pre_hook(resource_gate)
                output = {}
                for form in formats:
                    selected = (rows if form=='artifact' else
                                prepare_training_rows(rows, policy.questions(list(policy.CRITERIA))))
                    for row in selected: ml.preflight(tok, row['state'], row['questions'])
                    items, skipped = items_from_rows(tok, selected, 1024, 256)
                    if skipped or len(items)!=len(selected): raise ValueError('audit_skipped_rows')
                    rec = calibration_records(model, tok, items, torch.device('mps'),1024,256,batch_size=8)
                    predictions = []
                    for row, record in zip(selected, rec):
                        qid, q = next(iter(row['questions'].items()))
                        labels = list(q['criteria'])
                        target = labels[int(torch.tensor(record[2]).argmax())]
                        if target!=row['expected'][qid]: raise ValueError('audit_target_mapping')
                        probs = torch.tensor(record[1],dtype=torch.float64).softmax(0).tolist()
                        predictions.append({'criterion':qid, 'mutation':row['meta']['mutation']['operation'],
                            'source_group':row['meta']['original_group_id'],'family':row['meta']['family_group_id'],
                            'expected':target,'prediction':labels[max(range(len(probs)),key=probs.__getitem__)],
                            'probabilities':dict(zip(labels,probs))})
                    output[form] = {'metrics':quality_metrics(predictions), 'predictions':predictions}
                report['models'][name] = {'checkpoint_sha256':hashes['model.safetensors'], 'formats':output}
                del model; gc.collect(); torch.mps.empty_cache()
        name = 'quality-audit-'+split+'-'+str(time.time_ns())+'.json'
        private_json(ROOT/'runs'/name,report)
        return {'report':name, 'split':split, 'models':{model:{form:r['metrics'] for form,r in
                    data['formats'].items()} for model,data in report['models'].items()}}
    finally:
        signal.alarm(0)


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
            confirm_ack(ack, result)
            private_json(folder / 'ack.json', ack)
        except urllib.error.HTTPError as e:
            if e.code != 409: raise
            private_json(folder / 'stale.json', {'status':'stale', 'http_status':409})


def handle_job_error(client, job, error):
    folder = ROOT / 'runs' / (job['job_id'] + '-' + str(job['fencing_token']))
    path = folder / 'result.json'
    if path.is_file():
        pending = json.loads(path.read_bytes())
        if pending.get('message_type') == 'result' and all(pending.get(k) == job.get(k) for k in ACK_KEYS):
            # A lost HTTP response is not an inference failure. Preserve the
            # lease so flush_results can replay the exact idempotent envelope.
            return 'pending_ack'
    transient = isinstance(error, (urllib.error.URLError, TimeoutError, ConnectionError)) or (
        isinstance(error, ValueError) and str(error) == 'heartbeat_not_confirmed')
    if isinstance(error, urllib.error.HTTPError):
        transient = error.code == 429 or 500 <= error.code < 600
    client.call('/v1/worker/jobs/'+job['job_id']+'/fail', {'worker_id':client.worker,
                'lease_id':job['lease_id'],'fencing_token':job['fencing_token'],
                'code':'standing_execution_error','retryable':transient})
    return 'requeued' if transient else 'failed'


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
                        handle_job_error(client, job, error)
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
    parser.add_argument('action', choices=['ready', 'run', 'receipt', 'daemon', 'train', 'train-child', 'audit', 'improve', 'quality-child', 'quality-evaluate'])
    parser.add_argument('--recovery',action='store_true',help='separately approved resource-recovery attempt, never automatic')
    parser.add_argument('--audit-split', choices=['train','calibration'], default='calibration')
    parser.add_argument('--audit-format', choices=['artifact','runtime','both'], default='both')
    parser.add_argument('--run-folder', type=Path)
    parser.add_argument('--retry-resource', action='store_true', help='at most one early resource-abort restart; no quality tuning')
    parser.add_argument('--max-jobs', type=int, default=100)
    parser.add_argument('--max-seconds', type=int, default=1800)
    args = parser.parse_args()
    if not 1 <= args.max_jobs <= 100 or not 1 <= args.max_seconds <= 2700:
        raise ValueError('bounded_run_limits')
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', WANDB_DISABLED='true')
    if args.action == 'daemon': standing_worker(); return
    if args.action == 'audit':
        formats = ('artifact','runtime') if args.audit_format=='both' else (args.audit_format,)
        print(json.dumps(audit_quality(split=args.audit_split,formats=formats))); return
    if args.action == 'train': print(json.dumps(train_bundle(retry_resource=args.retry_resource))); return
    if args.action == 'improve': print(json.dumps(improve_quality(recovery=args.recovery))); return
    if args.action in ('train-child','quality-child','quality-evaluate'):
        folder = args.run_folder.resolve()
        if folder.parent != (ROOT / 'runs').resolve() or not folder.name.startswith('train-pilot-'):
            raise ValueError('training_run_path')
        if args.action=='quality-evaluate': print(json.dumps(evaluate_quality_candidate(folder)))
        elif args.action=='quality-child': quality_train_child(folder)
        else: train_child(folder)
        return
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
