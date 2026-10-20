"""Bounded unattended-research runner executed on the MacBook.

Reads one pinned JOB.json from the private incoming folder, trains only the
frozen head over synthetic training rows, saves before evaluating, reloads in a
separate process, and returns raw model answers. No operating model, registry,
database, network call or arbitrary shell is reachable from here.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import gc
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import re
import signal
import sys
import threading
import time

for name, value in {'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
                    'HF_HUB_DISABLE_TELEMETRY': '1', 'TOKENIZERS_PARALLELISM': 'false',
                    'WANDB_DISABLED': 'true', 'PYTORCH_ENABLE_MPS_FALLBACK': '0',
                    'LAYA_MPS_AMP': '0'}.items():
    os.environ[name] = value

MINI_ROOT = Path('/Volumes/KIOXIA-2TB/Cache/MOA/unattended-20261020/mini-worker')
ROOT = Path(os.environ.get('MOA_RESEARCH_HOME', Path.home() / 'Library/Application Support/MOA-LAYA'))
if os.environ.get('MOA_RESEARCH_HOME') and ROOT != MINI_ROOT:
    raise ValueError('only the isolated external-SSD auxiliary worker is allowed')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode()


def file_hash(path):
    return digest(Path(path).read_bytes())


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('wb') as stream:
        stream.write(canonical(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def confined(root, name):
    path = root / name
    if (Path(name).is_absolute() or '..' in Path(name).parts or path.is_symlink()
            or not path.resolve().is_relative_to(root.resolve())):
        raise ValueError('private path boundary')
    return path


def context(job_path):
    job_path = Path(job_path)
    if job_path.is_symlink():
        raise ValueError('job symlink refused')
    job_path = job_path.resolve()
    job = json.loads(job_path.read_bytes())
    run_id = job['run_id']
    if (job_path.parent != ROOT / 'incoming' / run_id
            or not re.fullmatch(r'JOB(?:-r[1-9][0-9]*)?\.json', job_path.name)
            or job.get('scope') != 'unattended_synthetic_research'
            or not isinstance(run_id, str) or not run_id.replace('-', '').isalnum()):
        raise ValueError('fixed research job required')
    execution_ancestor_hashes(job, job_path.parent)
    if job.get('execution_device', 'mps') not in ('mps', 'cpu'):
        raise ValueError('unsupported research device')
    if job.get('execution_device') == 'cpu' and ROOT != MINI_ROOT:
        raise ValueError('CPU auxiliary execution is isolated to the Mini')
    if file_hash(Path(__file__)) != job['runner_sha256']:
        raise ValueError('research runner changed')
    if file_hash(Path(job['library'])) != job['library_sha256']:
        raise ValueError('research library changed')
    sys.path.insert(0, str(ROOT / 'app'))
    from workers.laya_local import ml, policy, runtime
    import laya.train as tr
    for module in (ml, policy, runtime, tr):
        if file_hash(Path(module.__file__)) != job['installed_sha256'][module.__name__]:
            raise ValueError('installed implementation changed')
    bundle = confined(ROOT, job['bundle'])
    rows_by_split = {}
    for name, expected in job['bundle_sha256'].items():
        path = confined(bundle, name)
        if file_hash(path) != expected:
            raise ValueError('bundle changed')
    for name in job['bundle_sha256']:
        if name.endswith('.jsonl'):
            split = name.split('-')[0]
            rows_by_split[split] = [json.loads(line) for line in
                                    (bundle / name).read_bytes().splitlines() if line.strip()]
    protected = {name: file_hash(ROOT / name)
                 for name in ('MODEL_REGISTRY.json', 'MODEL_MANIFEST.json')}
    if protected != job['protected_models']:
        raise ValueError('operational model metadata changed')
    parent = confined(ROOT, job['parent'])
    ml.verify_model(str(parent), job['parent_files'])
    return job, rows_by_split, parent, ml, policy, runtime, tr


def validate_execution_revision(job, folder):
    previous = job.get('previous_job')
    if previous is None:
        return None
    if (set(previous) != {'filename', 'sha256'} or
            not re.fullmatch(r'JOB(?:-r[1-9][0-9]*)?\.json', previous['filename'])):
        raise ValueError('fixed previous execution job required')
    path = confined(Path(folder), previous['filename'])
    if file_hash(path) != previous['sha256']:
        raise ValueError('previous execution job changed')
    old = json.loads(path.read_bytes())
    ignored = {'runner_sha256', 'execution_revision', 'previous_job', 'execution_device'}
    if ({k: v for k, v in old.items() if k not in ignored} !=
            {k: v for k, v in job.items() if k not in ignored}):
        raise ValueError('execution revision changed training/data/settings')
    return previous['sha256']


def execution_device(job, torch):
    device = job.get('execution_device', 'mps')
    if device == 'cpu':
        if ROOT != MINI_ROOT:
            raise ValueError('CPU execution outside the isolated auxiliary worker')
        torch.set_num_threads(2)
        if hasattr(os, 'nice'):
            os.nice(10)
    return torch.device(device)


def execution_ancestor_hashes(job, folder):
    """Validate a bounded immutable repair chain; allow zero-step host moves."""
    result = set()
    current = job
    while current.get('previous_job'):
        digest = validate_execution_revision(current, folder)
        if digest in result or len(result) >= 8:
            raise ValueError('cyclic/excessive execution repair chain')
        result.add(digest)
        current = json.loads(confined(folder, current['previous_job']['filename']).read_bytes())
    return result


def queue(rows, policy, questions, with_gold=False):
    prepared = []
    for row in rows:
        state = policy.state_for({**row['state'], 'questions': list(row['questions'])})
        qs = policy.questions(list(row['questions']))
        if row['questions'] != qs:
            raise ValueError('question contract differs; do not silently replace it')
        entry = {'case_id': row['case_id'], 'state': state, 'questions': qs}
        if with_gold:
            # Training labels stay on the worker only for the train split; they are
            # never part of `state`, so the model input carries no answer.
            entry['expected'] = dict(row['expected'])
        prepared.append(entry)
    return prepared


def resource_allowed(runtime, job, training=True):
    current = runtime.resources()
    # The campaign policy is stricter than the installed runtime (5 vs 3 GiB).
    # Enforce both without changing the shared/operational worker installation.
    minimum = max(5, job.get('resource_policy', {}).get('minimum_memory_gib', 5))
    allowed = current['train_allowed' if training else 'verify_allowed']
    if training:
        allowed = allowed and current['available_memory_bytes'] >= minimum * 2**30
    return bool(allowed), current


def training_headroom(runtime, job, torch):
    # Test the live working set after releasing only our own unused MPS cache.
    # Checking first made every next microbatch look like genuine low memory.
    gc.collect(); torch.mps.synchronize(); torch.mps.empty_cache()
    return resource_allowed(runtime, job)


def training_configuration(cfg, item_count, helper, tr):
    """Enforce the published plan before a model is loaded or optimized."""
    plan = helper.training_plan(cfg,item_count)
    if cfg['planned_updates'] != plan['optimizer_updates']:
        raise ValueError('scheduler horizon differs from actual capped optimizer windows')
    effective = tr.TrainConfig(**{k:v for k,v in cfg.items() if k in tr.TrainConfig.__dataclass_fields__},
                               calib_frac=0,calib_max=0,log_every=10)
    effective.validate()
    return effective, plan


def train(job_path, resume=False):
    import torch
    job, rows_by_split, parent, ml, policy, runtime, tr = context(job_path)
    cfg = job['configuration']
    device = execution_device(job, torch)
    if (not 1 <= cfg['maximum_updates'] <= 400 or
            not 30 <= cfg['maximum_seconds'] <= 2700 or not cfg['freeze_encoder']):
        raise ValueError('research caps/frozen encoder required')
    folder = ROOT / 'research' / job['run_id']
    result_file = folder / 'TRAIN_RESULT.json'
    if result_file.exists() and not resume:
        return json.loads(result_file.read_bytes())
    raw_rows = rows_by_split['train']
    sampling = job.get('train_sampling')
    if sampling == 'balanced_oversample':
        # E4 done properly: the earlier class_prior multiply-and-renormalise was
        # a no-op on one-hot targets, so balance the training data instead.
        # Gold labels stay untouched; only which train rows repeat changes.
        buckets = {}
        for row in raw_rows:
            label = next(iter(row['expected'].values()))
            buckets.setdefault(label, []).append(row)
        largest = max(len(v) for v in buckets.values())
        balanced = []
        for label in sorted(buckets):
            items_in_class = sorted(buckets[label], key=lambda r: r['case_id'])
            repeat = -(-largest // len(items_in_class))
            balanced.extend((items_in_class * repeat)[:largest])
        raw_rows = sorted(balanced, key=lambda r: r['case_id'])
    elif sampling not in (None, 'none'):
        raise ValueError('unknown train sampling mode')
    rows = queue(raw_rows, policy, job['questions'], with_gold=True)
    identity_sha = digest(canonical(job))
    data_sha = digest(canonical(rows))
    spec = importlib.util.spec_from_file_location('research_checkpoint_helpers', job['library'])
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    config, plan = training_configuration(cfg,len(rows),helper,tr)
    contract = {'version':'moa.executed-training-contract.v1','run_id':job['run_id'],
                'job_sha256':identity_sha,'data_sha256':data_sha,'plan':plan,
                'effective_laya_config':asdict(config),
                'label_counts':dict(Counter(next(iter(row['expected'].values())) for row in rows)),
                'installed_sha256':job['installed_sha256']}
    contract_sha = digest(canonical(contract))
    with runtime.exclusive(ROOT / 'device.lock'):
        if not resource_allowed(runtime, job)[0]:
            return {'status': 'parked_resources', 'resources': runtime.resources()}
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        contract_path = folder/'TRAINING_CONTRACT.json'
        if contract_path.exists():
            if contract_path.read_bytes() != canonical(contract):
                raise ValueError('saved effective training contract changed')
        else:
            atomic_json(contract_path,contract)
        started = time.monotonic()
        model, tok, model_cfg = tr.load_checkpoint(str(parent))
        model.encoder.config.reference_compile = False
        model.to(device)
        model.encoder.requires_grad_(False)
        if not torch.backends.mps.is_available():
            raise RuntimeError('MPS required')
        named = {n: p for n, p in model.named_parameters() if p.requires_grad}
        if any(n.startswith('encoder.') for n in named):
            raise ValueError('encoder must stay frozen')
        for row in rows:
            ml.preflight(tok, row['state'], row['questions'])
        items, skipped = tr.items_from_rows(tok, rows, 1024, 256)
        if len(items) != len(rows):
            raise ValueError('training row did not become an item')
        if skipped or len(items) != len(rows):
            raise ValueError('no skipped training inputs allowed')
        optimizer = torch.optim.AdamW([{'params': list(named.values()), 'lr': config.head_lr}],
                                      weight_decay=config.weight_decay)
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
            optimizer, T_max=plan['optimizer_updates'], eta_min=config.min_lr)
        torch.manual_seed(config.seed)
        order_rng = random.Random(config.seed)
        before = parameter_hash(model)
        history, steps, cursor = [], 0, {'epoch': 0, 'next_microbatch': 0}
        resume_from, previous_seconds, first_step, resume_sha = None, 0., 0, None
        if resume and result_file.exists():
            # Resume only from a saved checkpoint whose identity still matches
            # this job; an unknown or damaged boundary is never retried.
            saved = json.loads(result_file.read_bytes())
            compatible_zero = (saved.get('updates') == 0 and saved.get('job_sha256') in
                               execution_ancestor_hashes(job, Path(job_path).parent))
            if (saved.get('run_id') != job['run_id']
                    or saved.get('data_sha256') != data_sha
                    or saved.get('job_sha256') != identity_sha and not compatible_zero
                    or saved.get('stop_reason') not in ('resource_guard', 'pause_requested')):
                raise ValueError('resume boundary identity mismatch')
            resume_from = confined(folder, saved['checkpoint_name'])
            if str(resume_from) != saved['checkpoint']:
                raise ValueError('saved checkpoint outside this worker')
            for name, expected in saved['sha256'].items():
                if file_hash(resume_from / name) != expected:
                    raise ValueError('saved checkpoint changed')
            steps = int(saved['updates'])
            stored = torch.load(resume_from / 'training-state.pt', map_location='cpu',
                                weights_only=False)
            if stored.get('step') != steps:
                raise ValueError('resume step mismatch')
            if (stored.get('job_sha256') != saved.get('job_sha256') or stored.get('data_sha256') != data_sha
                    or stored.get('run_id') != job['run_id']):
                raise ValueError('resume optimizer identity mismatch')
            cursor = dict(stored['cursor'])
            history = list(stored.get('history') or [])
            before = stored.get('initial_head_sha256') or before
            previous_seconds = float(saved['seconds'])
            first_step, resume_sha = steps, saved['sha256']['training-state.pt']
            if steps >= cfg['maximum_updates'] or previous_seconds >= cfg['maximum_seconds']:
                return {'status': 'budget_complete', 'updates': steps, 'seconds': previous_seconds}
        stop = threading.Event()
        previous = {sig: signal.signal(sig, lambda *_: stop.set())
                    for sig in (signal.SIGTERM, signal.SIGINT)}

        def save(tag, full=False):
            revision = '-r'+str(job['execution_revision']) if job.get('execution_revision') else ''
            name = '%s%s-%04d' % (tag, revision, steps)
            destination = folder / name
            if destination.exists():
                return name, json.loads((destination / 'HASHES.json').read_bytes())
            destination.mkdir(mode=0o700)
            # Interim optimizer boundaries need only the head: the frozen parent
            # remains hash-pinned. Preserve every existing model and boundary.
            if full:
                exported = helper.checkpoint_training_config(
                    dict(model_cfg,max_len=1024,head_max_len=256,
                         temperature=[1,1,1],temperature_by_options={}),
                    run_id=job['run_id'],updates=steps,cursor=cursor,contract_sha256=contract_sha)
                helper.save_checkpoint_cpu_first(model,tok,exported,destination)
            state = {'head': {n: v.detach().cpu().clone() for n, v in model.state_dict().items()
                              if not n.startswith('encoder.')},
                     'optimizer': optimizer.state_dict(), 'scheduler': scheduler.state_dict(),
                     'step': steps, 'cursor': dict(cursor),
                     'python_rng': random.getstate(), 'option_rng': order_rng.getstate(),
                     'run_id': job['run_id'], 'data_sha256': data_sha,
                     'job_sha256': identity_sha,
                     'training_contract_sha256':contract_sha,
                     'cumulative_seconds': previous_seconds + time.monotonic() - started,
                     'history': history,
                     'torch_rng': torch.get_rng_state(), 'mps_rng': torch.mps.get_rng_state(),
                     'initial_head_sha256': before}
            torch.save(state, destination / 'training-state.pt')
            hashes = {str(p.relative_to(destination)): file_hash(p)
                      for p in sorted(destination.rglob('*')) if p.is_file()}
            atomic_json(destination / 'HASHES.json', hashes)
            gc.collect()
            torch.mps.synchronize()
            torch.mps.empty_cache()
            return name, hashes

        def gate():
            coordinator_paused = ROOT == MINI_ROOT and (MINI_ROOT.parent/'PAUSED').exists()
            if stop.is_set() or (folder / 'PAUSED').exists() or coordinator_paused:
                raise InterruptedError('pause_requested')
            if previous_seconds + time.monotonic() - started >= cfg['maximum_seconds'] - 20:
                raise InterruptedError('time_limit')
            allowed, current = training_headroom(runtime, job, torch)
            if not allowed:
                # Record why, so a stop is diagnosable without guessing.
                atomic_json(folder / 'RESOURCE_STOP.json', current)
                raise InterruptedError('resource_guard')

        reason = None
        pending_window = None
        try:
            model.train()
            model.encoder.eval()
            optimizer.zero_grad(set_to_none=True)
            if resume_from is not None:
                # Restore head weights and optimizer/scheduler state from the
                # saved boundary so training continues rather than restarting.
                model.load_state_dict(stored['head'], strict=False)
                optimizer.load_state_dict(stored['optimizer'])
                scheduler.load_state_dict(stored['scheduler'])
                if stored.get('python_rng'):
                    random.setstate(stored['python_rng'])
                if stored.get('option_rng'):
                    order_rng.setstate(stored['option_rng'])
                torch.set_rng_state(stored['torch_rng'])
                torch.mps.set_rng_state(stored['mps_rng'])
                if compatible_zero:
                    # No optimizer update occurred in v1. Discard its partial
                    # microbatch RNG advance and restore the declared start.
                    torch.manual_seed(config.seed)
                    order_rng.seed(config.seed)
            for epoch in range(cursor['epoch'], config.epochs):
                epoch_items = list(items)
                random.Random(config.seed + epoch).shuffle(epoch_items)
                first = (cursor['next_microbatch'] if epoch == cursor['epoch'] else 0)
                for offset in range(first, len(items), config.grad_accum):
                    gate()
                    pending_window = (order_rng.getstate(), torch.get_rng_state(),
                                      torch.mps.get_rng_state(), random.getstate())
                    window = epoch_items[offset:offset + config.grad_accum]
                    losses = []
                    for item in window:
                        gate()
                        # MPS keeps freed blocks in its cache; without this the
                        # process creeps up ~25 updates then trips the guard.
                        gc.collect()
                        torch.mps.synchronize()
                        torch.mps.empty_cache()
                        order = tr.draw_option_order(item, order_rng, config.shuffle_options)
                        encoded = tr.encode_item(tok, item, 1024, 256, order, False)
                        batch = tr.collate_items([[encoded]], tok.pad_token_id)
                        logits = tr._forward(model, batch, device, False, True)
                        loss = tr.soft_ce_loss(logits, batch['target'].to(device),
                                               batch['marker_mask'].to(device))
                        # Class balance is handled by resampling the train rows
                        # (train_sampling) because scaling a one-hot target and
                        # renormalising is mathematically a no-op.
                        if not torch.isfinite(loss).item():
                            raise ValueError('nonfinite loss')
                        (loss / len(window)).backward()
                        losses.append(float(loss.detach().cpu()))
                        del loss, logits, batch, encoded
                    norm = float(torch.nn.utils.clip_grad_norm_(
                        list(named.values()), config.grad_clip).cpu())
                    if not math.isfinite(norm) or norm <= 0:
                        raise ValueError('invalid gradient')
                    optimizer.step()
                    scheduler.step()
                    optimizer.zero_grad(set_to_none=True)
                    pending_window = None
                    steps += 1
                    cursor = {'epoch': epoch, 'next_microbatch': offset + len(window)}
                    if cursor['next_microbatch'] == len(items):
                        cursor = {'epoch': epoch + 1, 'next_microbatch': 0}
                    history.append({'step': steps, 'mean_loss': sum(losses) / len(losses),
                                    'gradient_norm': norm})
                    atomic_json(folder / 'PROGRESS.json',
                                {'status': 'training', 'updates': steps,
                                 'seconds': previous_seconds + time.monotonic() - started,
                                 'job_sha256': identity_sha, 'cursor': cursor})
                    if steps % 40 == 0 and steps < cfg['maximum_updates']:
                        save('interim')
                    if steps >= cfg['maximum_updates']:
                        reason = 'update_limit'
                        break
                if reason:
                    break
            else:
                reason = 'epoch_limit'
        except InterruptedError as exc:
            reason = str(exc)
            if pending_window is not None:
                optimizer.zero_grad(set_to_none=True)
                order_rng.setstate(pending_window[0]); torch.set_rng_state(pending_window[1])
                torch.mps.set_rng_state(pending_window[2]); random.setstate(pending_window[3])
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        complete = reason in ('epoch_limit', 'update_limit', 'time_limit')
        name, hashes = save('checkpoint', full=complete)
        after = parameter_hash(model)
        result = {'status': 'saved' if complete and steps and after != before else 'parked',
                  'run_id': job['run_id'], 'checkpoint': str(folder / name),
                  'checkpoint_name': name, 'sha256': hashes, 'updates': steps,
                  'seconds': previous_seconds + time.monotonic() - started,
                  'session_seconds': time.monotonic() - started, 'history': history[-5:],
                  'weights_changed': after != before, 'stop_reason': reason,
                  'data_sha256': data_sha, 'job_sha256': identity_sha,
                  'training_contract_sha256':contract_sha,
                  'resume_from_updates': first_step, 'resume_from_sha256': resume_sha,
                  'initial_head_sha256': before, 'final_head_sha256': after,
                  'execution_device': str(device),
                  'training_complete': complete, 'operational_promotion': False}
        atomic_json(result_file, result)
        with (folder/'SESSIONS.jsonl').open('ab') as stream:
            stream.write(canonical(result)+b'\n'); stream.flush(); os.fsync(stream.fileno())
        return result


def parameter_hash(model):
    state = {n: v.detach().cpu().float().contiguous() for n, v in model.state_dict().items()
             if not n.startswith('encoder.')}
    return digest(b''.join(state[k].numpy().tobytes() for k in sorted(state)))


def reload_and_predict(job_path):
    import torch
    job, rows_by_split, parent, ml, policy, runtime, tr = context(job_path)
    folder = ROOT / 'research' / job['run_id']
    saved = json.loads((folder / 'TRAIN_RESULT.json').read_bytes())
    if saved.get('status') != 'saved':
        return {'status': 'not_saved', 'stop_reason': saved.get('stop_reason')}
    checkpoint = Path(saved['checkpoint'])
    for name, expected in saved['sha256'].items():
        if file_hash(checkpoint / name) != expected:
            raise ValueError('saved checkpoint changed')
    receipt = folder / 'RELOAD.json'
    if receipt.exists():
        existing = json.loads(receipt.read_bytes())
        # A receipt for an older checkpoint must never stand in for this one.
        if existing.get('checkpoint_sha256') == saved['sha256'].get('model.safetensors'):
            return existing
    with runtime.exclusive(ROOT / 'device.lock'):
        if not runtime.resources()['verify_allowed']:
            return {'status': 'waiting_resources'}
        started = time.monotonic()
        agent = ml.load_agent(checkpoint, saved['sha256'])
        answers = []
        for split in ('dev',):
            for row in queue(rows_by_split[split], policy, job['questions']):
                entry = {'case_id': row['case_id'], 'split': split,
                         'prediction_origin': 'raw_laya'}
                try:
                    ml.preflight(agent.tok, row['state'], row['questions'])
                    out = agent.predict(row['state'], row['questions'], max_len=1024, head_max_len=256)
                    answer = out['answers'][next(iter(row['questions']))]
                    entry.update(status='completed', prediction=answer['choice'],
                                 probabilities=answer['probabilities'])
                except ValueError as exc:
                    if not str(exc).startswith(('token_budget_exceeded',
                                               'question_head_would_truncate')):
                        raise
                    entry.update(status='input_overflow', prediction=None)
                answers.append(entry)
        record = {'status': 'reloaded', 'run_id': job['run_id'],
                  'model_sha256': saved['sha256'].get('model.safetensors'),
                  'checkpoint_sha256': saved['sha256'].get('model.safetensors'),
                  'checkpoint_name': saved.get('checkpoint_name'),
                  'updates': saved.get('updates'),
                  'answers': answers, 'seconds': time.monotonic() - started,
                  'separate_process': True, 'operational_promotion': False}
        atomic_json(receipt, record)
        return {k: v for k, v in record.items() if k != 'answers'}


def diagnose(job_path):
    """Tiny balanced overfit: can this head separate the three labels at all?

    Trains on a small balanced train subset and scores those same items. This
    answers a different question from the dev score: whether the model can fit
    learnable input at all, before blaming data or representation.
    """
    import torch
    job, rows_by_split, parent, ml, policy, runtime, tr = context(job_path)
    folder = ROOT / 'research' / job['run_id']
    receipt = folder / 'DIAGNOSTIC.json'
    if receipt.exists():
        return json.loads(receipt.read_bytes())
    per_class = 9
    buckets = {}
    for row in rows_by_split['train']:
        label = next(iter(row['expected'].values()))
        buckets.setdefault(label, []).append(row)
    chosen = []
    for label in ('preserved', 'changed', 'insufficient'):
        if len(buckets.get(label, [])) < per_class:
            return {'status': 'insufficient_class_coverage', 'label': label,
                    'available': len(buckets.get(label, []))}
        chosen += sorted(buckets[label], key=lambda r: r['case_id'])[:per_class]
    rows = queue(chosen, policy, job['questions'], with_gold=True)
    with runtime.exclusive(ROOT / 'device.lock'):
        if not runtime.resources()['train_allowed']:
            return {'status': 'parked_resources'}
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        started = time.monotonic()
        model, tok, model_cfg = tr.load_checkpoint(str(parent))
        model.encoder.config.reference_compile = False
        model.to('mps')
        model.encoder.requires_grad_(False)
        items, skipped = tr.items_from_rows(tok, rows, 1024, 256)
        if len(items) != len(rows):
            return {'status': 'item_build_failed', 'skipped': skipped}
        named = {n: p for n, p in model.named_parameters() if p.requires_grad}
        config = tr.TrainConfig(head_lr=0.001, min_lr=0.0001, weight_decay=0.0,
                                grad_clip=1.0, seed=20261020, epochs=40,
                                micro_batch=1, grad_accum=1, max_len=1024,
                                head_max_len=256, amp=False, loss='soft-ce',
                                calib_frac=0, calib_max=0, log_every=10)
        optimizer = torch.optim.AdamW([{'params': list(named.values()), 'lr': config.head_lr}])
        order_rng = random.Random(config.seed)
        torch.manual_seed(config.seed)
        before = evaluate_items(model, tok, items, tr, torch)
        model.train()
        model.encoder.eval()
        history = []
        for step in range(40):
            optimizer.zero_grad(set_to_none=True)
            total = 0.0
            for item in items:
                order = tr.draw_option_order(item, order_rng, config.shuffle_options)
                encoded = tr.encode_item(tok, item, 1024, 256, order, False)
                batch = tr.collate_items([[encoded]], tok.pad_token_id)
                logits = tr._forward(model, batch, torch.device('mps'), False, True)
                loss = tr.soft_ce_loss(logits, batch['target'].to('mps'),
                                       batch['marker_mask'].to('mps'))
                (loss / len(items)).backward()
                total += float(loss.detach().cpu())
            norm = float(torch.nn.utils.clip_grad_norm_(list(named.values()), 1.0).cpu())
            if not math.isfinite(norm):
                return {'status': 'nonfinite_gradient', 'step': step}
            optimizer.step()
            if step % 5 == 0 or step == 39:
                history.append({'step': step + 1, 'loss': total / len(items)})
        after = evaluate_items(model, tok, items, tr, torch)
        record = {'status': 'complete', 'run_id': job['run_id'],
                  'items': len(items), 'per_class': per_class,
                  'before': before, 'after': after, 'history': history,
                  'updates': 40, 'seconds': time.monotonic() - started,
                  'operational_promotion': False}
        atomic_json(receipt, record)
        return record


def evaluate_request(job_path, request_path):
    """Hash-pinned, gold-free DEVELOPMENT A/B evaluation, never blind selection.

    The coordinator retains gold and scores raw predictions. Repeated calls
    resume the same request, model and cumulative evaluation budget.
    """
    import torch
    job, _, parent, ml, policy, runtime, tr = context(job_path)
    device = execution_device(job, torch)
    request_path = Path(request_path)
    if file_hash(request_path) != job['evaluation_requests_sha256'][request_path.name]:
        raise ValueError('evaluation request changed')
    request = json.loads(request_path.read_bytes())
    if request.get('scope') != 'new_development_only' or request.get('contains_gold') is not False:
        raise ValueError('development-only gold-free evaluation required')
    for row in request['rows']:
        if set(row) != {'case_id', 'state', 'questions'}:
            raise ValueError('gold-free model input required')
    folder = ROOT/'research'/job['run_id']
    if request['model'] == 'candidate':
        saved = json.loads((folder/'TRAIN_RESULT.json').read_bytes())
        if saved.get('training_complete') is not True or saved.get('status') != 'saved':
            return {'status': 'training_not_complete'}
        checkpoint, hashes = Path(saved['checkpoint']), saved['sha256']
    else:
        reference = job['reference_models'][request['model']]
        checkpoint, hashes = confined(ROOT, reference['path']), reference['sha256']
    receipt = folder/('EVAL-'+request_path.stem+'.json')
    request_sha = file_hash(request_path)
    old = json.loads(receipt.read_bytes()) if receipt.exists() else {}
    if old:
        if old['request_sha256'] != request_sha or old['model_sha256'] != hashes['model.safetensors']:
            raise ValueError('evaluation receipt identity changed')
        if old['status'] == 'complete':
            return {k: v for k, v in old.items() if k != 'answers'}
    with runtime.exclusive(ROOT/'device.lock'):
        if not resource_allowed(runtime, job, training=False)[0]:
            return {'status': 'waiting_resources'}
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        started = time.monotonic()
        if str(device) == 'cpu':
            from laya import Agent
            ml.verify_model(checkpoint, hashes)
            agent = Agent(str(checkpoint), device='cpu', expected_sha256=hashes, backend='eager')
            agent.amp_enabled = False
        else:
            agent = ml.load_agent(checkpoint, hashes)
        answers = list(old.get('answers', []))
        elapsed_before = float(old.get('seconds', 0))
        status = 'complete'
        for row in queue(request['rows'], policy, job['questions'])[len(answers):]:
            gc.collect(); torch.mps.synchronize(); torch.mps.empty_cache()
            if elapsed_before+time.monotonic()-started >= 580:
                status = 'evaluation_time_limit'; break
            if not resource_allowed(runtime, job, training=False)[0]:
                status = 'waiting_resources'; break
            entry = {'case_id': row['case_id'], 'prediction_origin': 'raw_laya'}
            try:
                tokens = ml.preflight(agent.tok, row['state'], row['questions'])
                out = agent.predict(row['state'], row['questions'], max_len=1024, head_max_len=256)
                answer = out['answers'][next(iter(row['questions']))]
                entry.update(status='completed', prediction=answer['choice'],
                             probabilities=answer['probabilities'], tokens=tokens,
                             system_forced=False)
            except ValueError as exc:
                if not str(exc).startswith(('token_budget_exceeded', 'question_head_would_truncate')):
                    raise
                entry.update(status='input_overflow', prediction=None)
            answers.append(entry)
        record = {'status': status, 'request_sha256': request_sha,
                  'model_sha256': hashes['model.safetensors'], 'model': request['model'],
                  'answers': answers, 'seconds': elapsed_before+time.monotonic()-started,
                  'separate_process': True, 'peak_rss_bytes': __import__('resource').getrusage(
                      __import__('resource').RUSAGE_SELF).ru_maxrss,
                  'mps_driver_bytes': torch.mps.driver_allocated_memory(),
                  'execution_device': str(device),
                  'operational_promotion': False}
        atomic_json(receipt, record)
        return {k: v for k, v in record.items() if k != 'answers'}


def evaluate_items(model, tok, items, tr, torch):
    """Greedy argmax accuracy and per-class recall on the same items."""
    from collections import Counter
    model.eval()
    labels = ('preserved', 'changed', 'insufficient')
    correct = 0
    totals = Counter()
    hits = Counter()
    with torch.no_grad():
        for item in items:
            encoded = tr.encode_item(tok, item, 1024, 256, None, False)
            batch = tr.collate_items([[encoded]], tok.pad_token_id)
            logits = tr._forward(model, batch, torch.device('mps'), False, True)
            row = logits[0].mean(0) if logits.dim() > 2 else logits[0]
            prediction = labels[int(row.argmax().item())]
            gold = labels[item['target'].index(max(item['target']))]
            totals[gold] += 1
            if prediction == gold:
                correct += 1
                hits[gold] += 1
    return {'accuracy': correct / len(items) if items else 0.0,
            'correct': correct, 'items': len(items),
            'recall': {k: hits[k] / totals[k] for k in totals}}


def predict_blind(job_path, request_path):
    import torch
    job, rows_by_split, parent, ml, policy, runtime, tr = context(job_path)
    request = json.loads(Path(request_path).read_bytes())
    folder = ROOT / 'research' / job['run_id']
    saved = json.loads((folder / 'TRAIN_RESULT.json').read_bytes())
    if request['model'] == 'candidate':
        checkpoint = Path(saved['checkpoint'])
        hashes = saved['sha256']
        if not (folder / 'RELOAD.json').exists():
            return {'status': 'reload_required'}
    else:
        checkpoint = confined(ROOT, job['parent'])
        hashes = job['parent_files']
    receipt = folder / ('BLIND-%s.json' % request['model'])
    if receipt.exists():
        return json.loads(receipt.read_bytes())
    with runtime.exclusive(ROOT / 'device.lock'):
        if not runtime.resources()['verify_allowed']:
            return {'status': 'waiting_resources'}
        started = time.monotonic()
        agent = ml.load_agent(checkpoint, hashes)
        answers = []
        for row in queue(request['rows'], policy, job['questions']):
            entry = {'case_id': row['case_id'], 'prediction_origin': 'raw_laya'}
            try:
                ml.preflight(agent.tok, row['state'], row['questions'])
                out = agent.predict(row['state'], row['questions'], max_len=1024, head_max_len=256)
                answer = out['answers'][next(iter(row['questions']))]
                entry.update(status='completed', prediction=answer['choice'],
                             probabilities=answer['probabilities'])
            except ValueError as exc:
                if not str(exc).startswith(('token_budget_exceeded',
                                           'question_head_would_truncate')):
                    raise
                entry.update(status='input_overflow', prediction=None)
            answers.append(entry)
        record = {'status': 'complete', 'model': request['model'],
                  'answers': answers, 'seconds': time.monotonic() - started,
                  'operational_promotion': False}
        atomic_json(receipt, record)
        return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['inspect', 'train', 'reload', 'blind', 'diagnose', 'evaluate'])
    parser.add_argument('--job', type=Path, required=True)
    parser.add_argument('--request', type=Path)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if args.action == 'inspect':
        job, rows, parent, ml, policy, runtime, tr = context(args.job)
        out = {'status': 'checked', 'resources': runtime.resources(),
               'rows': {k: len(v) for k, v in rows.items()}}
    elif args.action == 'train':
        out = train(args.job, args.resume)
    elif args.action == 'reload':
        out = reload_and_predict(args.job)
    elif args.action == 'diagnose':
        out = diagnose(args.job)
    elif args.action == 'evaluate':
        if not args.request:
            parser.error('--request required')
        out = evaluate_request(args.job, args.request)
    else:
        if not args.request:
            parser.error('--request required')
        out = predict_blind(args.job, args.request)
        out = {k: v for k, v in out.items() if k != 'answers'}
    print(json.dumps(out, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'status': 'stopped', 'error_type': type(exc).__name__,
                          'reason': str(exc)[:400] if isinstance(
                              exc, (ValueError, InterruptedError, FileExistsError))
                          else 'execution_error_details_private'}, ensure_ascii=False), flush=True)
        raise SystemExit(1)
