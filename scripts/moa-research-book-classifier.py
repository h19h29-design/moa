"""Single fixed-classifier Book endpoint; no CPU/Mini or operational fallback."""
from __future__ import annotations

import fcntl
import gc
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import random
import re
import shlex
import signal
import subprocess
import sys
import tarfile
import time

ROOT = Path('/Users/jungwoohwang/Library/Application Support/MOA-LAYA')
RUN = 'moa-unattended-20261021-fixed-classifier-01'
OLD = 'moa-unattended-20261021-binary-train-01'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for part in iter(lambda: f.read(4 * 2**20), b''): h.update(part)
    return h.hexdigest()


def write_once(path, raw):
    if path.is_symlink(): raise ValueError('symlink refused')
    if path.exists():
        if path.read_bytes() != raw: raise ValueError('existing research artifact must not be overwritten')
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('xb') as f:
        os.chmod(path, 0o600); f.write(raw); f.flush(); os.fsync(f.fileno())


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix('.pending')
    if tmp.is_symlink() or path.is_symlink(): raise ValueError('state symlink refused')
    with tmp.open('wb') as f:
        os.chmod(tmp, 0o600); f.write(canonical(value)); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def confined(name):
    p = Path(name)
    base = ROOT / 'incoming' / RUN
    out = base / p
    if (p.is_absolute() or '..' in p.parts or base.is_symlink() or out.is_symlink() or
            not out.resolve().is_relative_to(base.resolve())):
        raise ValueError('confined new candidate artifacts required')
    return out


def validate_plan(job):
    cfg = job['configuration']
    wanted = dict(epochs=4, planned_updates=56, maximum_updates=120, maximum_seconds=1800,
        micro_batch=1, grad_accum=8, freeze_encoder=True, head_lr=.001, min_lr=.000001,
        weight_decay=.01, grad_clip=1, seed=20261020, max_len=1024, head_max_len=256,
        loss='soft-ce', shuffle_options=['choice'], amp=False, gradient_checkpointing=False)
    if (job['run_id'] != RUN or job['version'] != 'moa.fixed-binary-cls.v1' or
            job['execution_host'] != 'macbookair' or job['execution_device'] != 'mps' or
            job['label_to_index'] != {'preserved': 0, 'changed': 1} or
            job['evaluation_maximum_seconds'] != 600 or job['model_promotion'] is not False or cfg != wanted):
        raise ValueError('one fixed Book classifier with unchanged training configuration required')


def allowed(resources, *, admission):
    return bool(resources['train_allowed'] and resources['ac_power'] and
        resources['idle_seconds'] is not None and resources['idle_seconds'] >= 900 and
        resources['memory_pressure_ok'] and resources['disk_free_bytes'] >= 4 * 2**30 and
        resources['available_memory_bytes'] >= (7 if admission else 5) * 2**30)


def runtime():
    if sys.platform != 'darwin' or not ROOT.is_dir(): raise ValueError('MacBook-only existing runtime required')
    sys.path.insert(0, str(ROOT / 'app'))
    from workers.laya_local import runtime as r
    return r


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def job_context(payload):
    expected = {'moa/research_classifier.py', 'tests/test_research_classifier_torch.py',
                'runner.py', 'train.json', 'dev.json', 'JOB.json'}
    if payload['run_id'] != RUN or set(payload['stage_sha256']) != expected:
        raise ValueError('exact fixed-classifier manifest required')
    for name, value in payload['stage_sha256'].items():
        if sha(confined(name)) != value: raise ValueError('staged classifier hash mismatch')
    job = json.loads(confined('JOB.json').read_bytes())
    validate_plan(job)
    if job['files'] != {n: v for n, v in payload['stage_sha256'].items() if n != 'JOB.json'}:
        raise ValueError('job and file manifest differ')
    return job


def source_context(job):
    expected = f'incoming/{OLD}/JOB-r1.json'
    if job['source_job'] != expected or sha(ROOT / expected) != job['source_job_sha256']:
        raise ValueError('original immutable R1 execution context required')
    os.chdir(ROOT / 'incoming' / OLD)
    worker = load_module('original_research_worker', Path.cwd() / 'moa-research-worker.py')
    original, _, parent, ml, policy, r, tr = worker.context(ROOT / expected)
    if original['configuration'] != job['configuration']:
        raise ValueError('old comparison training variables changed')
    os.chdir(ROOT / 'incoming' / RUN)
    return original, parent, ml, policy, r, tr


def offline():
    os.environ.update(MOA_LAYA_HOME=str(ROOT), HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
        HF_HUB_DISABLE_TELEMETRY='1', TOKENIZERS_PARALLELISM='false')
    os.environ.pop('MOA_RESEARCH_HOME', None)
    import socket
    def denied(*a, **kw): raise RuntimeError('local research network denied')
    socket.socket.connect = denied


def upload(payload):
    names = payload['stage_sha256']
    seen = set()
    if (ROOT / 'research' / RUN / 'TRAIN_DISPATCH.json').exists():
        raise ValueError('cannot restage dispatched training')
    raw = sys.stdin.buffer.read(8 * 2**20 + 1)
    if len(raw) > 8 * 2**20: raise ValueError('staging size cap')
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as tar:
        for member in tar:
            if not member.isfile() or member.name not in names or member.name in seen:
                raise ValueError('unexpected stage archive member')
            data = tar.extractfile(member).read(4 * 2**20 + 1)
            if len(data) > 4 * 2**20 or hashlib.sha256(data).hexdigest() != names[member.name]:
                raise ValueError('staging digest or size mismatch')
            write_once(confined(member.name), data); seen.add(member.name)
    if seen != set(names): raise ValueError('incomplete stage')
    job_context(payload)
    return dict(status='STAGED_NOT_TRAINED', files=len(seen), training_updates=0)


def inspect(payload):
    job_context(payload)
    r = runtime(); res = r.resources(); busy = False
    with (ROOT / 'device.lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: busy = True
    run = ROOT / 'research' / RUN
    running = False
    for line in subprocess.check_output(['ps', '-axo', 'pid=,args='], text=True).splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2 or int(fields[0]) == os.getpid(): continue
        try: args = shlex.split(fields[1])
        except ValueError: continue
        if len(args) >= 3 and args[1] == str(confined('runner.py')):
            running = True
    result = dict(resources=res, busy=busy, running=running,
        train_admission=not busy and not running and allowed(res, admission=True)
        and res['disk_free_bytes'] >= 4.5 * 2**30,
        evaluation_admission=not busy and not running and allowed(res, admission=True))
    for key, name in [('train_result', 'TRAIN_RESULT.json'), ('evaluation', 'EVALUATION.json')]:
        if (run / name).is_file():
            result[key] = json.loads((run / name).read_bytes())
    return result


def preflight(payload):
    offline(); job = job_context(payload)
    original, parent, ml, _, r, _ = source_context(job)
    c = load_module('fixed_classifier_core', confined('moa/research_classifier.py'))
    # Tokenizer/shape check only. No R1 weights are loaded and no forward runs.
    with r.exclusive(ROOT / 'device.lock'):
        res = r.resources()
        if not res['verify_allowed']: raise ValueError('tokenizer preflight resource guard')
        from transformers import AutoTokenizer
        tok = AutoTokenizer.from_pretrained(parent / 'tokenizer', local_files_only=True)
        lengths = []
        for split in ('train', 'dev'):
            rows = json.loads(confined(split + '.json').read_bytes())
            if len(rows) != (108 if split == 'train' else 54): raise ValueError('fixed split count')
            for row in rows:
                if split == 'train': c.target(row)
                elif 'expected' in row: raise ValueError('development gold must stay on Mini')
                actual = c.encode(tok, row)
                import copy
                shuffled = copy.deepcopy(row)
                qid = next(iter(shuffled['questions']))
                question = shuffled['questions'][qid]
                question['criteria'] = dict(reversed(list(question['criteria'].items())))
                if split == 'train': shuffled['expected'][qid] = c.LABELS[1 - c.target(row)]
                if c.encode(tok, shuffled) != actual:
                    raise ValueError('target or option order leaked into classifier tokenizer input')
                lengths.append(len(actual['input_ids']))
        ml.verify_model(str(parent), original['parent_files'])
    return dict(status='INPUT_PREFLIGHT_PASSED_NOT_TRAINED', input_items=len(lengths),
        maximum_tokens=max(lengths), truncated_inputs=0, training_updates=0, model_inferences=0,
        execution_host='macbookair', job_sha256=payload['stage_sha256']['JOB.json'])


def launch(payload, training):
    job_context(payload)
    snap = inspect(payload)
    if not snap['train_admission' if training else 'evaluation_admission']:
        raise ValueError('Book admission refused')
    run = ROOT / 'research' / RUN
    run.mkdir(parents=True, exist_ok=True, mode=0o700)
    if training:
        reservation = json.load(sys.stdin)
        if (reservation.get('candidate') != RUN or reservation.get('train_seconds') != 1800 or
                reservation.get('kind') != 'reservation'):
            raise ValueError('unchanged Mini ledger reservation required')
        write_once(run / 'RESERVATION.json', canonical(reservation))
    else:
        saved = json.loads((run / 'TRAIN_RESULT.json').read_bytes())
        if saved['status'] != 'saved': raise ValueError('saved candidate required before evaluation')
    action = 'train' if training else 'evaluate'
    marker = run / ('TRAIN_DISPATCH.json' if training else 'EVAL_DISPATCH.json')
    with (ROOT / 'device.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with marker.open('xb') as f:
            os.chmod(marker, 0o600); f.write(canonical(dict(run_id=RUN, action=action)))
            f.flush(); os.fsync(f.fileno())
        args = dict(payload, action=action)
        env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', MOA_LAYA_HOME=str(ROOT))
        env.pop('MOA_RESEARCH_HOME', None)
        with (run / (action + '.log')).open('ab') as log:
            os.chmod(run / (action + '.log'), 0o600)
            p = subprocess.Popen([str(ROOT / 'venv-smoke/bin/python'), str(confined('runner.py')),
                json.dumps(args)], cwd=ROOT / 'incoming' / RUN, env=env, stdin=subprocess.DEVNULL,
                stdout=log, stderr=log, start_new_session=True, preexec_fn=lambda: os.nice(10))
    return dict(pid=p.pid, action=action, execution_host='macbookair')


def loaded_model(parent, c, torch):
    from laya.common import build_model
    from safetensors.torch import load_file
    from transformers import AutoTokenizer
    cfg = json.loads((parent / 'rl_agent_config.json').read_bytes())
    base = build_model(cfg, encoder_dir=str(parent / 'encoder'))
    base.load_state_dict(load_file(str(parent / 'model.safetensors')), strict=True)
    base.encoder.config.reference_compile = False
    torch.manual_seed(20261020)
    model = c.build_classifier(base.float()).to('mps')
    tok = AutoTokenizer.from_pretrained(parent / 'tokenizer', local_files_only=True)
    return model, tok


def parameter_sha(model):
    h = hashlib.sha256()
    for name, value in model.state_dict().items():
        h.update(name.encode()); h.update(value.detach().cpu().contiguous().numpy().tobytes())
    return h.hexdigest()


def execute(payload, training):
    if Path(__file__).resolve() != confined('runner.py').resolve():
        raise ValueError('training/evaluation require the pinned Book file, never inline or Mini')
    offline(); job = job_context(payload)
    original, parent, ml, _, r, _ = source_context(job)
    c = load_module('fixed_classifier_core', confined('moa/research_classifier.py'))
    import torch
    if not torch.backends.mps.is_available(): raise ValueError('MPS required, no CPU fallback')
    torch.set_num_threads(2)
    run = ROOT / 'research' / RUN
    marker = run / ('TRAIN_DISPATCH.json' if training else 'EVAL_DISPATCH.json')
    if not marker.is_file(): raise ValueError('single durable dispatch required')
    if (run / ('TRAIN_RESULT.json' if training else 'EVALUATION.json')).exists():
        raise ValueError('completed or stopped execution must not be repeated')
    reservation = json.loads((run / 'RESERVATION.json').read_bytes())
    import datetime as dt
    day = dt.datetime.fromisoformat(reservation['at']).date()
    start = time.monotonic(); maximum = 1800 if training else 600
    def expired(*_): raise InterruptedError('time_limit')
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, maximum)
    result = dict(status='stopped', run_id=RUN, job_sha256=payload['stage_sha256']['JOB.json'],
        execution_host='macbookair', execution_device='mps', trained=training,
        system_forced=False, operational_promotion=False, updates=0, records=[])
    minimum = []
    def guard(admission=False):
        res = r.resources(); minimum.append(res['available_memory_bytes'])
        today = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).date()
        if (not allowed(res, admission=admission) or today != day or (run / 'PAUSED').exists()):
            raise InterruptedError('resource_or_reservation_day_guard')
        if time.monotonic() - start > maximum - 20: raise InterruptedError('time_limit')
    try:
        with r.exclusive(ROOT / 'device.lock'):
            guard(True)
            model, tok = loaded_model(parent, c, torch)
            guard()
            split = 'train' if training else 'dev'
            rows = json.loads(confined(split + '.json').read_bytes())
            encoded = [(row, c.encode(tok, row)) for row in rows]
            def tensor_forward(encoded):
                return model(torch.tensor([encoded['input_ids']], device='mps'),
                             torch.tensor([encoded['attention_mask']], device='mps'))
            if training:
                named = {n: p for n, p in model.named_parameters() if p.requires_grad}
                if not named or any(n.startswith('encoder.') for n in named):
                    raise ValueError('only fixed classification head may train')
                initial = parameter_sha(model.encoder)
                optimizer = torch.optim.AdamW(named.values(), lr=.001, weight_decay=.01)
                scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=56, eta_min=.000001)
                random.seed(20261020); torch.manual_seed(20261020); torch.mps.manual_seed(20261020)
                model.train(); history = []
                def checkpoint(kind):
                    name = 'checkpoint-%04d-%s' % (result['updates'], kind)
                    folder = run / name
                    folder.mkdir(mode=0o700, exist_ok=True)
                    state = c.head_state(model)
                    for filename, value in [('head.pt', state), ('training-state.pt', dict(head=state,
                            optimizer=optimizer.state_dict(), scheduler=scheduler.state_dict(),
                            updates=result['updates'], history=history, torch_rng=torch.get_rng_state(),
                            mps_rng=torch.mps.get_rng_state(), python_rng=random.getstate()))]:
                        out = folder / filename
                        if out.exists(): raise ValueError('checkpoint must never be overwritten')
                        with out.open('xb') as f:
                            os.chmod(out, 0o600); torch.save(value, f); f.flush(); os.fsync(f.fileno())
                    write_once(folder / 'CLASSIFIER.json', canonical(dict(version=c.VERSION,
                        label_to_index=job['label_to_index'], parent=original['parent'],
                        parent_files=original['parent_files'], encoder_copied=False,
                        head_sha256=sha(folder / 'head.pt'), pooling='cls', loss='unweighted_cross_entropy',
                        previous_criterion='one_hot_soft_ce', configuration=job['configuration'])))
                    result.update(checkpoint_name=name, head_sha256=sha(folder / 'head.pt'),
                        training_state_sha256=sha(folder / 'training-state.pt'), checkpoint_kind=kind)
                    atomic(run / 'CHECKPOINT.json', {k: result[k] for k in
                        ('checkpoint_name', 'head_sha256', 'training_state_sha256', 'updates')})
                try:
                    for epoch in range(4):
                        ordered = list(encoded); random.Random(20261020 + epoch).shuffle(ordered)
                        for offset in range(0, len(ordered), 8):
                            guard(); optimizer.zero_grad(set_to_none=True)
                            window = ordered[offset:offset+8]; losses = []
                            for row, ids in window:
                                gc.collect(); torch.mps.synchronize(); torch.mps.empty_cache(); guard()
                                logits = tensor_forward(ids)
                                loss = torch.nn.functional.cross_entropy(logits,
                                    torch.tensor([c.target(row)], device='mps'))
                                if not torch.isfinite(loss).all(): raise ValueError('nonfinite loss')
                                (loss / len(window)).backward(); losses.append(float(loss.detach().cpu()))
                                del logits, loss
                            norm = float(torch.nn.utils.clip_grad_norm_(list(named.values()), 1.).cpu())
                            if not math.isfinite(norm) or norm <= 0: raise ValueError('invalid gradient')
                            optimizer.step(); scheduler.step(); result['updates'] += 1
                            history.append(dict(step=result['updates'], loss=sum(losses)/len(losses), gradient_norm=norm))
                            atomic(run / 'PROGRESS.json', dict(updates=result['updates'],
                                seconds=time.monotonic()-start, job_sha256=result['job_sha256']))
                            if result['updates'] == 28: checkpoint('interim')
                    result['stop_reason'] = 'epoch_limit'
                except InterruptedError as exc:
                    optimizer.zero_grad(set_to_none=True)
                    result['stop_reason'] = str(exc)
                checkpoint('final_or_safe_stop')
                result['encoder_unchanged'] = parameter_sha(model.encoder) == initial
                result['status'] = ('saved' if result['updates'] == 56 and
                    result.get('stop_reason') == 'epoch_limit' and result['encoder_unchanged'] else 'stopped')
            else:
                trained = json.loads((run / 'TRAIN_RESULT.json').read_bytes())
                if trained['status'] != 'saved': raise ValueError('saved training completion required')
                name = trained['checkpoint_name']
                if not re.fullmatch('checkpoint-[0-9]{4}-final_or_safe_stop', name):
                    raise ValueError('checkpoint boundary')
                cp = run / name
                if (sha(cp / 'head.pt') != trained['head_sha256'] or
                        sha(cp / 'training-state.pt') != trained['training_state_sha256']):
                    raise ValueError('saved checkpoint changed')
                state = torch.load(cp / 'training-state.pt', map_location='cpu', weights_only=True)
                head = torch.load(cp / 'head.pt', map_location='cpu', weights_only=True)
                if (state['updates'] != 56 or state['scheduler']['last_epoch'] != 56 or
                        set(head) != set(state['head']) or
                        any(not torch.equal(head[n], state['head'][n]) for n in head)):
                    raise ValueError('separate saved state/optimizer scheduler reload mismatch')
                c.load_head(model, head)
                if any(not torch.equal(c.head_state(model)[n], head[n]) for n in head):
                    raise ValueError('head reload differs from actual saved tensors')
                model.eval(); model.requires_grad_(False)
                for row, ids in encoded:
                    gc.collect(); torch.mps.synchronize(); torch.mps.empty_cache(); guard()
                    with torch.inference_mode(): z = tensor_forward(ids).detach().cpu()[0]
                    probs = z.softmax(-1).tolist()
                    result['records'].append(dict(case_id=row['case_id'], prediction=c.LABELS[int(z.argmax())],
                        probabilities=dict(zip(c.LABELS, probs)), system_forced=False))
                    del z
                result.update(status='complete', reload_exact=True, head_sha256=trained['head_sha256'])
            ml.verify_model(str(parent), original['parent_files'])
    except Exception as exc:
        result.update(status='stopped', error_type=type(exc).__name__)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        result.update(seconds=time.monotonic()-start, minimum_available_memory_bytes=min(minimum) if minimum else None)
        atomic(run / ('TRAIN_RESULT.json' if training else 'EVALUATION.json'), result)
    return result


def main():
    payload = json.loads(sys.argv[1]); action = payload['action']
    runtime()  # No helper action, even staging, can fall back to Mini.
    if action == 'stage': result = upload(payload)
    elif action == 'snapshot': result = inspect(payload)
    elif action == 'preflight': result = preflight(payload)
    elif action == 'selftest':
        job_context(payload)
        r = runtime()
        with r.exclusive(ROOT / 'device.lock'):
            if not r.resources()['verify_allowed']: raise ValueError('unit check resources')
            p = subprocess.run([str(ROOT / 'venv-smoke/bin/python'),
                str(confined('tests/test_research_classifier_torch.py'))], cwd=ROOT / 'incoming' / RUN,
                capture_output=True, text=True, timeout=30)
        found = re.search(r'Ran (\d+) tests?', p.stderr)
        result = dict(returncode=p.returncode, tests=int(found.group(1)) if found else 0,
            research_training_updates=0, research_model_inferences=0, scope='tiny_tensor_unit_checks_only')
    elif action in ('launch_train', 'launch_evaluate'):
        result = launch(payload, action == 'launch_train')
    elif action in ('train', 'evaluate'): result = execute(payload, action == 'train')
    else: raise ValueError('unsupported classifier action')
    print(json.dumps(result, allow_nan=False))


if __name__ == '__main__':
    try: main()
    except Exception as exc:
        print(json.dumps(dict(status='refused', error_type=type(exc).__name__)))
        raise SystemExit(1)
