"""MacBook-only saved-state/tokenizer audit. No model load, forward or optimizer."""
from __future__ import annotations

from collections import Counter
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import signal
from statistics import mean
import struct
import sys
import time

ROOT = Path('/Users/jungwoohwang/Library/Application Support/MOA-LAYA')
RUN = 'moa-unattended-20261021-binary-train-01'


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def tensor_groups(path):
    """Stream tensor bytes by group; never materialize model weights in RAM."""
    digests = {k: hashlib.sha256() for k in ('encoder', 'head')}
    counts = Counter()
    with Path(path).open('rb') as stream:
        size = os.fstat(stream.fileno()).st_size
        raw = stream.read(8)
        if len(raw) != 8:
            raise ValueError('truncated tensor header')
        length = struct.unpack('<Q', raw)[0]
        if not 0 < length <= 16 * 1024 * 1024 or 8 + length > size:
            raise ValueError('tensor header limit')
        header = json.loads(stream.read(length))
        tensors = [(n, v) for n, v in header.items() if n != '__metadata__']
        previous = 0
        for name, meta in sorted(tensors, key=lambda x: x[1]['data_offsets'][0]):
            start, end = meta['data_offsets']
            if type(start) is not int or type(end) is not int or not previous <= start <= end <= size-8-length:
                raise ValueError('invalid tensor range/overlap')
            previous = end
            group = 'encoder' if name.startswith('encoder.') else 'head'
            digests[group].update(json.dumps([name, meta['dtype'], meta['shape']], separators=(',', ':')).encode())
            stream.seek(8 + length + start)
            remaining = end-start
            while remaining:
                data = stream.read(min(4 * 1024 * 1024, remaining))
                if not data:
                    raise ValueError('truncated tensor body')
                digests[group].update(data)
                remaining -= len(data)
            counts[group] += 1
        if previous != size-8-length or not all(counts[k] for k in digests):
            raise ValueError('incomplete encoder/head tensor inventory')
    return {k: dict(sha256=h.hexdigest(), tensors=counts[k]) for k, h in digests.items()}


def audit(request):
    started = time.monotonic()
    if request['run_id'] != RUN or request['maximum_seconds'] != 90:
        raise ValueError('fixed read-only audit boundary')
    def timeout(*_):
        raise TimeoutError('read-only audit time limit')
    signal.signal(signal.SIGALRM, timeout)
    signal.setitimer(signal.ITIMER_REAL, 85)
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
        TOKENIZERS_PARALLELISM='false', MOA_LAYA_HOME=str(ROOT))
    import socket
    def no_network(*_a, **_kw):
        raise RuntimeError('audit has no network access')
    socket.socket.connect = no_network
    import torch
    import laya.train as tr
    from transformers import AutoTokenizer
    sys.path.insert(0, str(ROOT / 'app'))
    from workers.laya_local import policy, runtime
    torch.set_num_threads(2)
    os.nice(10)
    incoming = ROOT / 'incoming' / RUN
    run = ROOT / 'research' / RUN
    read = lambda p: json.loads(p.read_bytes())
    job_path = incoming / 'JOB-r1.json'
    job, saved = read(job_path), read(run / 'TRAIN_RESULT.json')
    if sha(job_path) != request['job_sha256'] or saved['job_sha256'] != request['job_sha256']:
        raise ValueError('actual job mismatch')
    checkpoint = Path(saved['checkpoint'])
    if checkpoint.is_symlink() or checkpoint.resolve().parent != run.resolve():
        raise ValueError('checkpoint boundary')
    weights = checkpoint / 'model.safetensors'
    state_path = checkpoint / 'training-state.pt'
    before = dict(model=sha(weights), state=sha(state_path))
    if before['model'] != request['model_sha256'] or before['state'] != saved['sha256']['training-state.pt']:
        raise ValueError('actual saved artifact hash mismatch')
    for module in (tr, policy, runtime):
        if sha(module.__file__) != job['installed_sha256'][module.__name__]:
            raise ValueError('installed implementation mismatch')
    runner = incoming / 'moa-research-worker.py'
    if sha(runner) != job['runner_sha256']:
        raise ValueError('executed runner changed')
    spec = importlib.util.spec_from_file_location('executed_binary_worker', runner)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    resources = []
    with runtime.exclusive(ROOT / 'device.lock'):
        def guard():
            current = runtime.resources()
            if not current['verify_allowed'] or current['available_memory_bytes'] < 5 * 2**30:
                raise RuntimeError('audit resource guard')
            resources.append(current['available_memory_bytes'])
        guard()
        stored = torch.load(state_path, map_location='cpu', weights_only=True)
        raw = [json.loads(x) for x in (incoming / 'train-000.jsonl').read_bytes().splitlines() if x.strip()]
        rows = worker.queue(raw, policy, job['questions'], with_gold=True, semantic_profile=job['semantic_profile'])
        if worker.digest(worker.canonical(rows)) != saved['data_sha256']:
            raise ValueError('actual training input differs')
        tok = AutoTokenizer.from_pretrained(checkpoint / 'tokenizer', local_files_only=True)
        items, skipped = tr.items_from_rows(tok, rows, 1024, 256)
        if skipped or len(items) != len(rows):
            raise ValueError('actual tokenizer dropped training rows')
        cfg = job['configuration']
        rng = random.Random(cfg['seed'])
        positions = {label: [0, 0] for label in ('preserved', 'changed')}
        perms = Counter()
        encodings = {}; unknown = tokens = maximum = 0
        for epoch in range(cfg['epochs']):
            ordered = list(zip(items, rows))
            random.Random(cfg['seed'] + epoch).shuffle(ordered)
            for item, row in ordered:
                order = tr.draw_option_order(item, rng, cfg['shuffle_options'])
                encoded = tr.encode_item(tok, item, 1024, 256, order, False)
                if len(encoded['markers']) != 2 or len(encoded['ids']) > 1024:
                    raise ValueError('training encoding mismatch')
                gold = next(iter(row['expected'].values()))
                slot = encoded['target'].index(1.)
                if list(item['q']['crit'])[order[slot]] != gold:
                    raise ValueError('gold/option identity mismatch')
                positions[gold][slot] += 1
                perms[','.join(map(str, order))] += 1
                ids = encoded['ids']
                unknown += ids.count(tok.unk_token_id)
                tokens += len(ids); maximum = max(maximum, len(ids))
                key = tuple(ids), tuple(encoded['markers'])
                if key in encodings and encodings[key] != slot:
                    raise ValueError('identical tokenizer input has conflicting target slots')
                encodings[key] = slot
            guard()
        if rng.getstate() != stored['option_rng']:
            raise ValueError('actual saved option RNG mismatch')
        history = stored['history']
        counts = Counter(next(iter(r['expected'].values())) for r in rows)
        fractions = [v/len(rows) for v in counts.values()]
        parent = (ROOT / job['parent']).resolve()
        if not parent.is_relative_to((ROOT / 'candidates').resolve()):
            raise ValueError('original model boundary')
        if sha(parent / 'model.safetensors') != job['parent_files']['model.safetensors']:
            raise ValueError('parent model changed')
        original, trained = tensor_groups(parent / 'model.safetensors'), tensor_groups(weights)
        guard()
        result = dict(status='complete', model_sha256=before['model'], job_sha256=request['job_sha256'],
            optimizer_updates=stored['step'], scheduler_steps=stored['scheduler']['last_epoch'],
            scheduler_horizon=stored['scheduler']['T_max'], final_learning_rate=stored['scheduler']['_last_lr'],
            optimizer_steps=sorted({int(v['step']) for v in stored['optimizer']['state'].values()}),
            first_8_loss_mean=mean(x['mean_loss'] for x in history[:8]),
            last_8_loss_mean=mean(x['mean_loss'] for x in history[-8:]),
            prior_only_train_cross_entropy=-sum(x*math.log(x) for x in fractions),
            minimum_gradient_norm=min(x['gradient_norm'] for x in history),
            maximum_gradient_norm=max(x['gradient_norm'] for x in history),
            nonfinite_losses=sum(not math.isfinite(x['mean_loss']) for x in history),
            actual_tokenizer_encodings=sum(sum(v) for v in positions.values()), gold_slot_counts=positions,
            option_permutations=dict(perms), option_rng_matches_saved=True, gold_mapping_errors=0,
            unknown_tokens=unknown, input_tokens=tokens, maximum_tokens=maximum,
            conflicting_input_targets=0, encoder_identical_to_r1=original['encoder']==trained['encoder'],
            head_differs_from_r1=original['head']!=trained['head'], parameter_groups=trained,
            minimum_available_memory_bytes=min(resources), model_loaded=False, model_inferences=0,
            training_updates=0, new_candidates=0, execution_host='macbookair')
    result.update(training_state_unchanged=before['state']==sha(state_path),
                  weights_unchanged=before['model']==sha(weights), seconds=time.monotonic()-started)
    signal.setitimer(signal.ITIMER_REAL, 0)
    return result


if __name__ == '__main__':
    try:
        print(json.dumps(audit(json.load(sys.stdin)), allow_nan=False))
    except Exception as error:
        print(json.dumps(dict(status='stopped', error_type=type(error).__name__)))
        raise SystemExit(1)
