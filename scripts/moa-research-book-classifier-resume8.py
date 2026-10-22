"""Single explicit fixed-head continuation, on the existing MacBook MPS only."""
from __future__ import annotations

import datetime as dt
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
FOLDER = 'eight-update-resume-20261022'
PREVIOUS = 'two-gateway-resume-20261022'
FILES = {'worker.py', 'moa/research_gateway_hold.py', 'moa/research_classifier_resume8_state.py',
         'tests/test_research_classifier_resume8_torch.py'}
GATEWAYS = {
    'ai.openclaw.gateway': 'c9a8402b54e092331f281993a4f6c5bc367572ea6311db072a68ee84b7889f19',
    'ai.openclaw.h19h19': 'cf0945ddb483d7080eee0d1f85bf40b81c08d7ed216f7c2655441b044bd4c4fc'}


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def original(payload):
    if (sys.platform != 'darwin' or not ROOT.is_dir() or payload['run_id'] != RUN or
            payload['folder'] != FOLDER): raise ValueError('existing MacBook-only execution required')
    old_path = ROOT / 'incoming' / RUN / 'runner.py'
    if hashlib.sha256(old_path.read_bytes()).hexdigest() != payload['stage_sha256']['runner.py']:
        raise ValueError('original pinned Book worker changed')
    old = load('pinned_fixed_classifier_worker', old_path)
    job = old.job_context(payload)
    return old, job


def incoming(name):
    p = Path(name); base = ROOT / 'incoming' / RUN / FOLDER; out = base / p
    if (p.is_absolute() or '..' in p.parts or base.is_symlink() or out.is_symlink() or
            not out.resolve().is_relative_to(base.resolve())):
        raise ValueError('confined resume stage required')
    return out


def output(): return ROOT / 'research' / RUN / FOLDER


def probe(payload):
    old, job = original(payload)
    run = ROOT / 'research' / RUN
    previous = run / PREVIOUS
    stopped = json.loads((previous / 'TRAIN_RESULT.json').read_bytes())
    first = json.loads((run / 'TRAIN_RESULT.json').read_bytes())
    if (stopped.get('status') != 'stopped' or stopped.get('updates') != 8 or
            stopped.get('stop_reason') != 'resource_or_reservation_day_guard' or
            stopped.get('checkpoint_name') != 'checkpoint-0008-final_or_safe_stop' or
            stopped.get('resumed_exact') is not True or stopped.get('encoder_unchanged') is not True or
            stopped.get('cumulative_train_seconds') != first['seconds'] + stopped['seconds'] or
            stopped.get('job_sha256') != payload['stage_sha256']['JOB.json'] or
            any((previous / n).exists() for n in ('EVAL_DISPATCH.json', 'EVALUATION.json'))):
        raise ValueError('known never-evaluated eight-update checkpoint required')
    cp = previous / stopped['checkpoint_name']
    if (old.sha(cp / 'head.pt') != stopped['head_sha256'] or
            old.sha(cp / 'training-state.pt') != stopped['training_state_sha256']):
        raise ValueError('saved eight-update tensors changed')
    metadata = json.loads((cp / 'CLASSIFIER.json').read_bytes())
    if (metadata['configuration'] != job['configuration'] or metadata['label_to_index'] != job['label_to_index'] or
            metadata['head_sha256'] != stopped['head_sha256'] or metadata['encoder_copied'] is not False):
        raise ValueError('saved classifier configuration changed')
    return dict(stop=stopped, classifier_sha256=old.sha(cp / 'CLASSIFIER.json'),
        stop_sha256=old.sha(previous / 'TRAIN_RESULT.json'),
        prior_book_sha256={'authorization': old.sha(ROOT / 'incoming' / RUN / PREVIOUS / 'AUTHORIZATION.json'),
                           'dispatch': old.sha(previous / 'TRAIN_DISPATCH.json')})

def context(payload):
    old, job = original(payload)
    if set(payload['resume_sha256']) != FILES: raise ValueError('exact resume source manifest required')
    for name, digest in payload['resume_sha256'].items():
        if old.sha(incoming(name)) != digest: raise ValueError('resume implementation changed')
    if old.sha(incoming('AUTHORIZATION.json')) != payload['authorization_sha256']:
        raise ValueError('resume authorization changed')
    auth = json.loads(incoming('AUTHORIZATION.json').read_bytes())
    prior = probe(payload)
    run = ROOT / 'research' / RUN
    reservation = json.loads((run / 'RESERVATION.json').read_bytes())
    if (auth['version'] != 'moa.fixed-classifier-explicit-eight-resume.v1' or auth['run_id'] != RUN or
            auth['maximum_resumes'] != 1 or auth['prior_updates'] != 8 or auth['previous_folder'] != PREVIOUS or
            auth['prior_book_sha256'] != prior['prior_book_sha256'] or auth['additional_candidates'] != 0 or
            auth['additional_train_seconds'] != 0 or auth['execution_device'] != 'mps' or
            auth['execution_host'] != 'macbookair' or auth['operational_promotion'] is not False or
            auth['original_stage_sha256'] != payload['stage_sha256'] or
            auth['resume_sha256'] != payload['resume_sha256'] or auth['gateways'] != GATEWAYS or
            auth['original_stop'] != prior['stop'] or
            auth['prior_train_seconds'] != prior['stop']['cumulative_train_seconds'] or
            auth['remaining_train_seconds'] != 1800 - prior['stop']['cumulative_train_seconds'] or
            auth['evaluation_maximum_seconds'] != 600 or auth['reservation'] != reservation or
            reservation['candidate'] != RUN or reservation['train_seconds'] != 1800 or
            auth['admission_memory_bytes'] != 7*2**30 or auth['running_memory_bytes'] != 5*2**30 or
            old.sha(run / PREVIOUS / 'TRAIN_RESULT.json') != auth['book_stop_sha256'] or
            old.sha(run / 'TRAIN_RESULT.json') != auth['original_book_stop_sha256'] or
            old.sha(run / 'RESERVATION.json') != auth['original_sha256']['RESERVATION.json']):
        raise ValueError('same saved state, original reservation and unchanged protection required')
    cp = run / PREVIOUS / auth['checkpoint_name']
    if any(old.sha(cp / n) != h for n, h in auth['checkpoint_sha256'].items()):
        raise ValueError('original head/optimizer checkpoint changed')
    if any(old.sha(run / 'checkpoint-0000-final_or_safe_stop' / n) != h
           for n, h in auth['zero_checkpoint_sha256'].items()):
        raise ValueError('original zero-update checkpoint changed')
    return old, job, auth


def same_day(auth):
    zone = dt.timezone(dt.timedelta(hours=9))
    return (dt.datetime.now(zone).date() ==
            dt.datetime.fromisoformat(auth['reservation']['at']).astimezone(zone).date())


def gateways_stopped(old):
    for name, digest in GATEWAYS.items():
        plist = Path('/Users/jungwoohwang/Library/LaunchAgents') / (name + '.plist')
        if old.sha(plist) != digest: raise ValueError('approved gateway configuration changed')
        if subprocess.run(['launchctl', 'print', 'gui/501/' + name], capture_output=True).returncode == 0:
            return False
    return True


def upload(payload):
    old, _ = original(payload)
    if set(payload['resume_sha256']) != FILES or (output() / 'TRAIN_DISPATCH.json').exists():
        raise ValueError('exact unissued resume stage required')
    wanted = dict(payload['resume_sha256'], **{'AUTHORIZATION.json': payload['authorization_sha256']})
    raw = sys.stdin.buffer.read(2*2**20+1)
    if len(raw) > 2*2**20: raise ValueError('resume staging size cap')
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:') as tar:
        for member in tar:
            if not member.isfile() or member.name not in wanted or member.name in seen:
                raise ValueError('unexpected resume member')
            data = tar.extractfile(member).read(2**20+1)
            if len(data) > 2**20 or hashlib.sha256(data).hexdigest() != wanted[member.name]:
                raise ValueError('resume digest or size mismatch')
            old.write_once(incoming(member.name), data); seen.add(member.name)
    if seen != set(wanted): raise ValueError('incomplete resume stage')
    context(payload)
    return dict(status='STAGED', training_updates=0)


def snapshot(payload):
    old, _, auth = context(payload)
    snap = old.inspect(payload)
    q = output()
    # Original stopped receipt stays immutable, not mistaken for the resume's result.
    snap.pop('train_result', None); snap.pop('evaluation', None)
    runner = str(incoming('worker.py'))
    for line in subprocess.check_output(['ps', '-axo', 'pid=,args='], text=True).splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2 or int(fields[0]) == os.getpid(): continue
        try: args = shlex.split(fields[1])
        except ValueError: continue
        if len(args) >= 3 and args[1] == runner:
            try: model_action = json.loads(args[2]).get('action') in ('resume', 'evaluate')
            except (ValueError, TypeError): model_action = False
            if model_action: snap['running'] = True
    snap['gateways_stopped'] = gateways_stopped(old)
    held = gateway_lease().inspect()
    snap['gateways_disabled'] = all(x['disabled'] and not x['registered'] for x in held.values())
    snap['remote_dispatch'] = (q / 'TRAIN_DISPATCH.json').exists()
    snap['evaluation_dispatch'] = (q / 'EVAL_DISPATCH.json').exists()
    snap['train_admission'] = bool(snap['train_admission'] and not snap['running'] and same_day(auth))
    snap['evaluation_admission'] = bool(snap['evaluation_admission'] and not snap['running'] and same_day(auth))
    for key, name in [('train_result', 'TRAIN_RESULT.json'), ('evaluation', 'EVALUATION.json'), ('progress', 'PROGRESS.json')]:
        if (q / name).is_file(): snap[key] = json.loads((q / name).read_bytes())
    return snap


def launch(payload, training):
    old, _, auth = context(payload)
    snap = snapshot(payload)
    if (not snap['gateways_stopped'] or not snap['gateways_disabled'] or
            not snap['train_admission' if training else 'evaluation_admission']):
        raise ValueError('Book resource/day/gateway admission refused')
    q = output(); q.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not training and json.loads((q / 'TRAIN_RESULT.json').read_bytes())['status'] != 'saved':
        raise ValueError('completed saved continuation required')
    marker = q / ('TRAIN_DISPATCH.json' if training else 'EVAL_DISPATCH.json')
    action = 'resume' if training else 'evaluate'
    with (ROOT / 'device.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with marker.open('xb') as f:
            os.chmod(marker, 0o600)
            f.write(old.canonical(dict(run_id=RUN, action=action, authorization_sha256=payload['authorization_sha256'])))
            f.flush(); os.fsync(f.fileno())
        env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', MOA_LAYA_HOME=str(ROOT))
        env.pop('MOA_RESEARCH_HOME', None)
        with (q / (action + '.log')).open('ab') as log:
            os.chmod(q / (action + '.log'), 0o600)
            p = subprocess.Popen([str(ROOT / 'venv-smoke/bin/python'), str(incoming('worker.py')),
                json.dumps(dict(payload, action=action))], cwd=incoming('.'), env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                preexec_fn=lambda: os.nice(10))
    return dict(pid=p.pid, action=action, execution_host='macbookair')


def execute(payload, training):
    old, job, auth = context(payload)
    if Path(__file__).resolve() != incoming('worker.py').resolve():
        raise ValueError('resume/evaluation require the actual pinned Book file')
    q = output(); marker = q / ('TRAIN_DISPATCH.json' if training else 'EVAL_DISPATCH.json')
    result_file = q / ('TRAIN_RESULT.json' if training else 'EVALUATION.json')
    if not marker.is_file() or result_file.exists(): raise ValueError('one durable execution only')
    if json.loads(marker.read_bytes())['authorization_sha256'] != payload['authorization_sha256']:
        raise ValueError('dispatch authorization mismatch')
    start = time.monotonic(); maximum = auth['remaining_train_seconds'] if training else 600
    result = dict(status='stopped', run_id=RUN, job_sha256=payload['stage_sha256']['JOB.json'],
        authorization_sha256=payload['authorization_sha256'], execution_host='macbookair',
        execution_device='mps', system_forced=False, operational_promotion=False, updates=8 if training else 0, records=[])
    minimum = []
    def expired(*_): raise InterruptedError('time_limit')
    signal.signal(signal.SIGALRM, expired); signal.setitimer(signal.ITIMER_REAL, maximum)
    def guard(admission=False):
        res = r.resources(); minimum.append(res['available_memory_bytes'])
        if (not old.allowed(res, admission=admission) or not same_day(auth) or
                (training and res['disk_free_bytes'] < 4.5*2**30) or
                (q / 'PAUSED').exists() or (q.parent / 'PAUSED').exists() or not gateways_held()):
            raise InterruptedError('resource_or_reservation_day_guard')
        if time.monotonic()-start > maximum-20: raise InterruptedError('time_limit')
    try:
        old.offline()
        original_job, parent, ml, _, r, _ = old.source_context(job)
        c = old.load_module('fixed_classifier_core', old.confined('moa/research_classifier.py'))
        restore = load('fixed_resume_state', incoming('moa/research_classifier_resume8_state.py'))
        import torch
        if not torch.backends.mps.is_available(): raise ValueError('MPS required, no Mini/CPU fallback')
        torch.set_num_threads(2)
        with r.exclusive(ROOT / 'device.lock'):
            guard(True)
            model, tok = old.loaded_model(parent, c, torch)
            guard()
            rows = json.loads(old.confined(('train' if training else 'dev') + '.json').read_bytes())
            if len(rows) != (108 if training else 54): raise ValueError('unchanged split required')
            encoded = [(row, c.encode(tok, row)) for row in rows]
            def forward(ids):
                return model(torch.tensor([ids['input_ids']], device='mps'),
                             torch.tensor([ids['attention_mask']], device='mps'))
            if training:
                named = {n: v for n, v in model.named_parameters() if v.requires_grad}
                if not named or any(n.startswith('encoder.') for n in named):
                    raise ValueError('frozen encoder must not train')
                initial = old.parameter_sha(model.encoder)
                optimizer = torch.optim.AdamW(named.values(), lr=.001, weight_decay=.01)
                scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=56, eta_min=.000001)
                cp = q.parent / PREVIOUS / auth['checkpoint_name']
                head = torch.load(cp / 'head.pt', map_location='cpu', weights_only=True)
                state = torch.load(cp / 'training-state.pt', map_location='cpu', weights_only=True)
                restore.restore_eight_state(torch, lambda x: c.load_head(model, x), lambda: c.head_state(model),
                    optimizer, scheduler, state, head, torch.mps.set_rng_state)
                result['resumed_exact'] = True
                result['resumed_from_head_sha256'] = auth['checkpoint_sha256']['head.pt']
                history = list(state['history']); del state, head
                model.train()
                def checkpoint(kind):
                    name = 'checkpoint-%04d-%s' % (result['updates'], kind)
                    folder = q / name; folder.mkdir(mode=0o700, exist_ok=False)
                    head = c.head_state(model)
                    values = {'head.pt': head, 'training-state.pt': dict(head=head,
                        optimizer=optimizer.state_dict(), scheduler=scheduler.state_dict(), updates=result['updates'],
                        history=history, torch_rng=torch.get_rng_state(), mps_rng=torch.mps.get_rng_state(),
                        python_rng=random.getstate())}
                    for filename, value in values.items():
                        with (folder / filename).open('xb') as f:
                            os.chmod(folder / filename, 0o600); torch.save(value, f); f.flush(); os.fsync(f.fileno())
                    old.write_once(folder / 'CLASSIFIER.json', old.canonical(dict(version=c.VERSION,
                        label_to_index=job['label_to_index'], parent=original_job['parent'],
                        parent_files=original_job['parent_files'], encoder_copied=False,
                        head_sha256=old.sha(folder / 'head.pt'), pooling='cls', loss='unweighted_cross_entropy',
                        configuration=job['configuration'], authorization_sha256=payload['authorization_sha256'],
                        resumed_from=auth['checkpoint_sha256'])))
                    result.update(checkpoint_name=name, head_sha256=old.sha(folder / 'head.pt'),
                        training_state_sha256=old.sha(folder / 'training-state.pt'), checkpoint_kind=kind)
                    old.atomic(q / 'CHECKPOINT.json', {k: result[k] for k in
                        ('checkpoint_name', 'head_sha256', 'training_state_sha256', 'updates')})
                try:
                    for epoch, offset, window in restore.remaining_windows(encoded, auth['prior_updates']):
                        guard(); optimizer.zero_grad(set_to_none=True)
                        losses = []
                        for row, ids in window:
                            gc.collect(); torch.mps.synchronize(); torch.mps.empty_cache(); guard()
                            logits = forward(ids)
                            loss = torch.nn.functional.cross_entropy(logits,
                                torch.tensor([c.target(row)], device='mps'))
                            if not torch.isfinite(loss).all(): raise ValueError('nonfinite loss')
                            (loss/len(window)).backward(); losses.append(float(loss.detach().cpu()))
                            del logits, loss
                        norm = float(torch.nn.utils.clip_grad_norm_(list(named.values()), 1.).cpu())
                        if not math.isfinite(norm) or norm <= 0: raise ValueError('invalid gradient')
                        optimizer.step(); scheduler.step(); result['updates'] += 1
                        history.append(dict(step=result['updates'], loss=sum(losses)/len(losses), gradient_norm=norm))
                        old.atomic(q / 'PROGRESS.json', dict(updates=result['updates'],
                            seconds=time.monotonic()-start,
                            cumulative_train_seconds=auth['prior_train_seconds']+time.monotonic()-start))
                        if result['updates'] == 28: checkpoint('interim')
                    result['stop_reason'] = 'epoch_limit'
                except InterruptedError as exc:
                    optimizer.zero_grad(set_to_none=True); result['stop_reason'] = str(exc)
                checkpoint('final_or_safe_stop')
                result['encoder_unchanged'] = old.parameter_sha(model.encoder) == initial
                result['status'] = ('saved' if result['updates'] == 56 and
                    result['stop_reason'] == 'epoch_limit' and result['encoder_unchanged'] else 'stopped')
            else:
                trained = json.loads((q / 'TRAIN_RESULT.json').read_bytes())
                name = trained['checkpoint_name']
                if (trained['status'] != 'saved' or trained['updates'] != 56 or
                        not re.fullmatch('checkpoint-0056-final_or_safe_stop', name)):
                    raise ValueError('completed saved continuation required')
                cp = q / name
                if (old.sha(cp / 'head.pt') != trained['head_sha256'] or
                        old.sha(cp / 'training-state.pt') != trained['training_state_sha256']):
                    raise ValueError('resumed checkpoint changed')
                state = torch.load(cp / 'training-state.pt', map_location='cpu', weights_only=True)
                head = torch.load(cp / 'head.pt', map_location='cpu', weights_only=True)
                if (state['updates'] != 56 or state['scheduler']['last_epoch'] != 56 or
                        set(head) != set(state['head']) or
                        any(not torch.equal(head[n], state['head'][n]) for n in head)):
                    raise ValueError('saved head/training state mismatch')
                c.load_head(model, head)
                if any(not torch.equal(c.head_state(model)[n], head[n]) for n in head):
                    raise ValueError('separate head reload mismatch')
                del state, head
                model.eval(); model.requires_grad_(False)
                for row, ids in encoded:
                    gc.collect(); torch.mps.synchronize(); torch.mps.empty_cache(); guard()
                    with torch.inference_mode(): z = forward(ids).detach().cpu()[0]
                    result['records'].append(dict(case_id=row['case_id'], prediction=c.LABELS[int(z.argmax())],
                        probabilities=dict(zip(c.LABELS, z.softmax(-1).tolist())), system_forced=False))
                    del z
                result.update(status='complete', reload_exact=True, head_sha256=trained['head_sha256'])
            ml.verify_model(str(parent), original_job['parent_files'])
    except Exception as exc:
        result.update(status='stopped', error_type=type(exc).__name__)
        if isinstance(exc, InterruptedError): result['stop_reason'] = str(exc)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        seconds = time.monotonic()-start
        result.update(seconds=seconds, minimum_available_memory_bytes=min(minimum) if minimum else None)
        if training: result['cumulative_train_seconds'] = auth['prior_train_seconds']+seconds
        if seconds > maximum: result.update(status='stopped', stop_reason='cumulative_time_limit')
        old.atomic(result_file, result)
    return result



def gateway_lease():
    module = load('approved_gateway_hold', incoming('moa/research_gateway_hold.py'))
    return module.GatewayHold(output() / 'GATEWAY_RECOVERY.json')


def gateways_held():
    lease = gateway_lease()
    values = lease.inspect()
    return (lease.path.is_file() and lease.read()['recovery_required'] and
            all(x['disabled'] and not x['registered'] for x in values.values()))


def gateway_restore():
    q = output()
    with (q / 'gateway-recovery.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return gateway_lease().restore()


def gateway_guard(payload):
    _, _, auth = context(payload)
    q = output(); lease = gateway_lease(); started = time.monotonic()
    maximum = auth['remaining_train_seconds'] + 600 + 120
    def interrupt(*_): raise InterruptedError('restore approved gateways')
    signal.signal(signal.SIGTERM, interrupt); signal.signal(signal.SIGINT, interrupt)
    try:
        while lease.read()['recovery_required']:
            elapsed = time.monotonic() - started
            trained = json.loads((q / 'TRAIN_RESULT.json').read_bytes()) if (q / 'TRAIN_RESULT.json').exists() else {}
            if ((trained and trained.get('status') != 'saved') or (q / 'EVALUATION.json').exists() or
                    (q / 'RESTORE_REQUESTED').exists() or elapsed > maximum or
                    (elapsed > 120 and not (q / 'TRAIN_DISPATCH.json').exists())):
                break
            time.sleep(3)
    finally:
        if lease.read()['recovery_required']: gateway_restore()
    return dict(status='RESTORED_VERIFIED')


def pause_gateways(payload):
    context(payload)
    q = output(); q.mkdir(parents=True, exist_ok=True, mode=0o700)
    def start_guard():
        with (q / 'gateway-guard.log').open('ab') as log:
            os.chmod(q / 'gateway-guard.log', 0o600)
            p = subprocess.Popen([str(ROOT / 'venv-smoke/bin/python'), str(incoming('worker.py')),
                json.dumps(dict(payload, action='gateway_guard'))], cwd=incoming('.'),
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        # A durable recovery obligation already exists before this callback.
        if p.poll() is not None: raise ValueError('gateway restoration guard failed to start')
    with (q / 'gateway-recovery.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return gateway_lease().pause(on_journal=start_guard)

def main():
    payload = json.loads(sys.argv[1]); action = payload['action']
    old, _ = original(payload)
    if action == 'probe': out = probe(payload)
    elif action == 'pause_gateways': out = pause_gateways(payload)
    elif action == 'restore_gateways':
        context(payload); out = gateway_restore()
    elif action == 'gateway_guard': out = gateway_guard(payload)
    elif action == 'stage': out = upload(payload)
    elif action == 'snapshot': out = snapshot(payload)
    elif action == 'selftest':
        context(payload); r = old.runtime()
        with r.exclusive(ROOT / 'device.lock'):
            if not r.resources()['verify_allowed']: raise ValueError('tiny unit resource guard')
            p = subprocess.run([str(ROOT / 'venv-smoke/bin/python'),
                str(incoming('tests/test_research_classifier_resume8_torch.py'))],
                cwd=incoming('.'), capture_output=True, text=True, timeout=30)
        found = re.search(r'Ran (\d+) tests?', p.stderr)
        out = dict(returncode=p.returncode, tests=int(found.group(1)) if found else 0,
            research_training_updates=0, research_model_inferences=0, scope='tiny_cpu_tensor_unit_checks_only')
    elif action in ('launch_resume', 'launch_evaluate'): out = launch(payload, action == 'launch_resume')
    elif action in ('resume', 'evaluate'): out = execute(payload, action == 'resume')
    else: raise ValueError('unsupported explicit classifier resume action')
    print(json.dumps(out, allow_nan=False))


if __name__ == '__main__':
    try: main()
    except Exception as exc:
        print(json.dumps(dict(status='refused', error_type=type(exc).__name__)))
        raise SystemExit(1)

