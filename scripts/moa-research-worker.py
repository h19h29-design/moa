"""Bounded unattended-research runner executed on the MacBook.

Reads one pinned JOB.json from the private incoming folder, trains only the
frozen head over synthetic training rows, saves before evaluating, reloads in a
separate process, and returns raw model answers. No operating model, registry,
database, network call or arbitrary shell is reachable from here.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
from pathlib import Path
import random
import signal
import sys
import threading
import time

for name, value in {'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
                    'HF_HUB_DISABLE_TELEMETRY': '1', 'TOKENIZERS_PARALLELISM': 'false',
                    'WANDB_DISABLED': 'true', 'PYTORCH_ENABLE_MPS_FALLBACK': '0',
                    'LAYA_MPS_AMP': '0'}.items():
    os.environ[name] = value

ROOT = Path.home() / 'Library/Application Support/MOA-LAYA'


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
    if (job_path != ROOT / 'incoming' / run_id / 'JOB.json'
            or job.get('scope') != 'unattended_synthetic_research'
            or not isinstance(run_id, str) or not run_id.replace('-', '').isalnum()):
        raise ValueError('fixed research job required')
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


def queue(rows, policy, questions, with_gold=False):
    prepared = []
    for row in rows:
        state = policy.state_for({**row['state'], 'questions': list(row['questions'])})
        qs = policy.questions(list(row['questions']))
        entry = {'case_id': row['case_id'], 'state': state, 'questions': qs}
        if with_gold:
            # Training labels stay on the worker only for the train split; they are
            # never part of `state`, so the model input carries no answer.
            entry['expected'] = dict(row['expected'])
        prepared.append(entry)
    return prepared


def train(job_path, resume=False):
    import torch
    job, rows_by_split, parent, ml, policy, runtime, tr = context(job_path)
    cfg = job['configuration']
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
    with runtime.exclusive(ROOT / 'device.lock'):
        if not runtime.resources()['train_allowed']:
            return {'status': 'parked_resources', 'resources': runtime.resources()}
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        started = time.monotonic()
        model, tok, model_cfg = tr.load_checkpoint(str(parent))
        model.encoder.config.reference_compile = False
        model.to('mps')
        model.encoder.requires_grad_(False)
        if not torch.backends.mps.is_available():
            raise RuntimeError('MPS required')
        named = {n: p for n, p in model.named_parameters() if p.requires_grad}
        if any(n.startswith('encoder.') for n in named):
            raise ValueError('encoder must stay frozen')
        config = tr.TrainConfig(**{k: v for k, v in cfg.items() if k in tr.TrainConfig.__dataclass_fields__},
                                calib_frac=0, calib_max=0, log_every=10)
        config.validate()
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
            optimizer, T_max=max(1, cfg['planned_updates']), eta_min=config.min_lr)
        torch.manual_seed(config.seed)
        order_rng = random.Random(config.seed)
        before = parameter_hash(model)
        history, steps, cursor = [], 0, {'epoch': 0, 'next_microbatch': 0}
        resume_from = None
        if resume and result_file.exists():
            # Resume only from a saved checkpoint whose identity still matches
            # this job; an unknown or damaged boundary is never retried.
            saved = json.loads(result_file.read_bytes())
            if (saved.get('run_id') != job['run_id']
                    or saved.get('data_sha256') != digest(canonical(
                        [r['case_id'] for r in rows]))):
                raise ValueError('resume boundary identity mismatch')
            resume_from = Path(saved['checkpoint'])
            for name, expected in saved['sha256'].items():
                if file_hash(resume_from / name) != expected:
                    raise ValueError('saved checkpoint changed')
            steps = int(saved['updates'])
            stored = torch.load(resume_from / 'training-state.pt', map_location='cpu',
                                weights_only=False)
            if stored.get('step') != steps:
                raise ValueError('resume step mismatch')
            cursor = dict(stored['cursor'])
            history = list(stored.get('history') or [])
            before = stored.get('initial_head_sha256') or before
        stop = threading.Event()
        previous = {sig: signal.signal(sig, lambda *_: stop.set())
                    for sig in (signal.SIGTERM, signal.SIGINT)}
        data_sha = digest(canonical([row['case_id'] for row in rows]))

        def save(tag):
            name = '%s-%04d' % (tag, steps)
            destination = folder / name
            if destination.exists():
                return name, json.loads((destination / 'HASHES.json').read_bytes())
            destination.mkdir(mode=0o700)
            tr.save_checkpoint(model, tok, dict(model_cfg, max_len=1024, head_max_len=256,
                                                temperature=[1, 1, 1], temperature_by_options={}),
                               str(destination))
            state = {'head': {n: v.detach().cpu().clone() for n, v in model.state_dict().items()
                              if not n.startswith('encoder.')},
                     'optimizer': optimizer.state_dict(), 'scheduler': scheduler.state_dict(),
                     'step': steps, 'cursor': dict(cursor),
                     'python_rng': random.getstate(), 'option_rng': order_rng.getstate(),
                     'run_id': job['run_id'], 'data_sha256': data_sha,
                     'initial_head_sha256': before}
            torch.save(state, destination / 'training-state.pt')
            hashes = {p.name: file_hash(p) for p in sorted(destination.iterdir()) if p.is_file()}
            atomic_json(destination / 'HASHES.json', hashes)
            gc.collect()
            torch.mps.synchronize()
            torch.mps.empty_cache()
            prune_checkpoints(folder, keep={name, 'checkpoint'})
            return name, hashes

        def gate():
            if stop.is_set() or (folder / 'PAUSED').exists():
                raise InterruptedError('pause_requested')
            if time.monotonic() - started >= cfg['maximum_seconds'] - 10:
                raise InterruptedError('time_limit')
            current = runtime.resources()
            if not current['train_allowed']:
                # Record why, so a stop is diagnosable without guessing.
                atomic_json(folder / 'RESOURCE_STOP.json', current)
                raise InterruptedError('resource_guard')

        reason = None
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
            for epoch in range(cursor['epoch'], config.epochs):
                epoch_items = list(items)
                random.Random(config.seed + epoch).shuffle(epoch_items)
                first = (cursor['next_microbatch'] if epoch == cursor['epoch'] else 0)
                for offset in range(first, len(items), config.grad_accum):
                    gate()
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
                        logits = tr._forward(model, batch, torch.device('mps'), False, True)
                        loss = tr.soft_ce_loss(logits, batch['target'].to('mps'),
                                               batch['marker_mask'].to('mps'))
                        # Class balance is handled by resampling the train rows
                        # (train_sampling) because scaling a one-hot target and
                        # renormalising is mathematically a no-op.
                        if not torch.isfinite(loss).item():
                            raise ValueError('nonfinite loss')
                        (loss / len(window)).backward()
                        losses.append(float(loss.detach().cpu()))
                    norm = float(torch.nn.utils.clip_grad_norm_(
                        list(named.values()), config.grad_clip).cpu())
                    if not math.isfinite(norm) or norm <= 0:
                        raise ValueError('invalid gradient')
                    optimizer.step()
                    scheduler.step()
                    optimizer.zero_grad(set_to_none=True)
                    steps += 1
                    cursor = {'epoch': epoch, 'next_microbatch': offset + len(window)}
                    if cursor['next_microbatch'] == len(items):
                        cursor = {'epoch': epoch + 1, 'next_microbatch': 0}
                    history.append({'step': steps, 'mean_loss': sum(losses) / len(losses),
                                    'gradient_norm': norm})
                    atomic_json(folder / 'PROGRESS.json',
                                {'status': 'training', 'updates': steps,
                                 'seconds': time.monotonic() - started, 'cursor': cursor})
                    if steps % 10 == 0:
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
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        name, hashes = save('checkpoint')
        after = parameter_hash(model)
        result = {'status': 'saved' if steps and after != before else 'parked',
                  'run_id': job['run_id'], 'checkpoint': str(folder / name),
                  'checkpoint_name': name, 'sha256': hashes, 'updates': steps,
                  'seconds': time.monotonic() - started, 'history': history[-5:],
                  'weights_changed': after != before, 'stop_reason': reason,
                  'data_sha256': data_sha, 'operational_promotion': False}
        atomic_json(result_file, result)
        return result


def parameter_hash(model):
    state = {n: v.detach().cpu().float().contiguous() for n, v in model.state_dict().items()
             if not n.startswith('encoder.')}
    return digest(b''.join(state[k].numpy().tobytes() for k in sorted(state)))


def prune_checkpoints(folder, keep=()):
    """Keep the newest snapshot only; older ones are 1.2GB each.

    A resource stop must resume from the newest checkpoint, so anything older
    than the one just written has no remaining use and would fill the disk.
    """
    import shutil
    snapshots = sorted(p for p in Path(folder).iterdir()
                       if p.is_dir() and (p.name.startswith('interim-')
                                          or p.name.startswith('checkpoint-')))
    for old in snapshots[:-1]:
        if old.name in keep:
            continue
        shutil.rmtree(old, ignore_errors=True)


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
    parser.add_argument('action', choices=['inspect', 'train', 'reload', 'blind', 'diagnose'])
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
