"""Measured, zero-update migration of the same research candidate to the Mini.

No model registry/operating service is modified. The caller first copies the
explicit, hash-pinned research files through the already authenticated SSH.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import sys
import time
import urllib.error
import urllib.request

from . import research_alignment as a, research_campaign as rc
from .research_dataset import canonical

MINI = a.DEFAULT/'mini-worker'


def environment():
    return dict(os.environ, MOA_RESEARCH_HOME=str(MINI), MOA_LAYA_HOME=str(MINI),
                HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                TOKENIZERS_PARALLELISM='false', LAYA_MPS_AMP='0', WANDB_DISABLED='true')


def probe_services():
    results = {}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for port in (18326, 18327):
        started = time.monotonic()
        try:
            with opener.open('http://127.0.0.1:'+str(port)+'/', timeout=5) as response:
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
        except OSError:
            status = None
        results[str(port)] = {'status': status, 'seconds': time.monotonic()-started}
    return results


def prepare(cpu=False):
    folder = MINI/'incoming'/a.RUN
    state = a._read(a.DEFAULT/'alignment/STATE.json')
    if state.get('execution_host') == 'mini':
        return {'status': 'already_prepared'}
    observed = a.remote()
    saved = observed['files'].get('TRAIN_RESULT.json', {})
    if observed['running'] or observed['device_busy'] or saved.get('updates') != 0:
        raise ValueError('migration requires an idle, known zero-update boundary')
    old_path = folder/('JOB-r3.json' if cpu else 'JOB-r2.json')
    job = a._read(old_path)
    for reference in job['reference_models'].values():
        for name, digest in reference['sha256'].items():
            if a.sha(MINI/reference['path']/name) != digest:
                raise ValueError('copied reference hash mismatch')
    original = MINI/'research'/a.RUN/'TRAIN_RESULT.json'
    local = a._read(original)
    if local != saved:
        raise ValueError('copied worker boundary does not match live Book')
    for name, digest in saved['sha256'].items():
        if a.sha(MINI/'research'/a.RUN/saved['checkpoint_name']/name) != digest:
            raise ValueError('copied boundary changed')
    revision_number = 4 if cpu else 3
    runner = folder/('moa-research-worker-v%d.py' % revision_number)
    a.write_once(runner, (a.ROOT/'scripts/moa-research-worker.py').read_bytes())
    revision = dict(job, runner_sha256=a.sha(runner), execution_revision=revision_number,
                    previous_job={'filename': old_path.name, 'sha256': a.sha(old_path)})
    if cpu:
        revision['execution_device'] = 'cpu'
    a.write_once(folder/('JOB-r%d.json' % revision_number), canonical(revision))
    a.write_once(a.DEFAULT/'alignment/BOOK_BOUNDARY_BEFORE_MIGRATION.json', canonical(saved))
    return {'status': 'prepared', 'configuration_unchanged': job['configuration'] == revision['configuration']}


def benchmark(cpu=False):
    """Full accumulation-window working-set/speed probe, no optimizer step."""
    os.environ.update(environment())
    import torch
    import psutil
    revision = 4 if cpu else 3
    path = MINI/'incoming'/a.RUN/('moa-research-worker-v%d.py' % revision)
    os.chdir(path.parent)
    spec = importlib.util.spec_from_file_location('auxiliary_worker', path)
    worker = importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)
    job, raw, parent, ml, policy, runtime, tr = worker.context(path.parent/('JOB-r%d.json' % revision))
    receipt_path = a.DEFAULT/'alignment'/('MINI_CPU_BENCHMARK.json' if cpu else 'MINI_BENCHMARK.json')
    if receipt_path.exists():
        return a._read(receipt_path)
    initial = runtime.resources()
    if not worker.resource_allowed(runtime, job)[0]:
        return {'status': 'waiting_resources', 'resources': initial}
    with runtime.exclusive(MINI/'device.lock'):
        started = time.monotonic()
        services_before = probe_services()
        samples = []
        def sample(stage):
            gc.collect(); torch.mps.synchronize(); torch.mps.empty_cache()
            value = runtime.resources()
            samples.append({'stage': stage, 'available_memory_bytes': value['available_memory_bytes'],
                'mps_driver_bytes': torch.mps.driver_allocated_memory(),
                'rss_bytes': psutil.Process().memory_info().rss,
                'train_allowed': worker.resource_allowed(runtime, job)[0]})
            return samples[-1]['train_allowed']
        cfg = job['configuration']
        device = worker.execution_device(job, torch)
        model, tok, _ = tr.load_checkpoint(str(parent))
        model.encoder.config.reference_compile = False
        model.to(device); model.encoder.requires_grad_(False)
        model.train(); model.encoder.eval()
        before = worker.parameter_hash(model)
        rows = worker.queue(raw['train'], policy, job['questions'], with_gold=True)
        items, skipped = tr.items_from_rows(tok, rows, 1024, 256)
        if skipped or len(items) != len(rows):
            raise ValueError('benchmark input mismatch')
        torch.manual_seed(cfg['seed']); rng = random.Random(cfg['seed'])
        shuffled = list(items); random.Random(cfg['seed']).shuffle(shuffled)
        sample('model_loaded')
        completed = 0
        forward_started = time.monotonic()
        for item in shuffled[:cfg['grad_accum']]:
            if not sample('before_microbatch'):
                break
            order = tr.draw_option_order(item, rng, cfg['shuffle_options'])
            encoded = tr.encode_item(tok, item, 1024, 256, order, False)
            batch = tr.collate_items([[encoded]], tok.pad_token_id)
            logits = tr._forward(model, batch, device, False, True)
            loss = tr.soft_ce_loss(logits, batch['target'].to(device), batch['marker_mask'].to(device))
            (loss/cfg['grad_accum']).backward()
            completed += 1
            del loss, logits, batch, encoded
        sample('accumulated_window')
        forward_seconds = time.monotonic()-forward_started
        model.zero_grad(set_to_none=True)
        sample('gradients_released')
        unchanged = worker.parameter_hash(model) == before
        services_after = probe_services()
        safe_services = all(services_before[p]['status'] == services_after[p]['status']
                            and services_after[p]['status'] is not None
                            for p in services_before)
        result = {'status': 'safe' if completed == cfg['grad_accum'] and unchanged and safe_services
                    and all(s['train_allowed'] for s in samples) else 'not_safe',
                  'at': a.now(), 'device': 'Mac mini M4', 'physical_memory_bytes': psutil.virtual_memory().total,
                  'seconds': time.monotonic()-started, 'microbatches': completed,
                  'window_seconds': forward_seconds, 'optimizer_updates': 0, 'weights_changed': not unchanged,
                  'samples': samples, 'services_before': services_before, 'services_after': services_after,
                  'execution_device': str(device),
                  'job_sha256': a.sha(path.parent/('JOB-r%d.json' % revision)), 'data_manifest_sha256': job['manifest_sha256'],
                  'minimum_free_bytes': min(s['available_memory_bytes'] for s in samples),
                  'operational_promotion': False}
        a.write_once(receipt_path, canonical(result))
        return result


def activate(cpu=False):
    bench_name = 'MINI_CPU_BENCHMARK.json' if cpu else 'MINI_BENCHMARK.json'
    revision = 4 if cpu else 3
    receipt = a._read(a.DEFAULT/'alignment'/bench_name)
    if receipt['status'] != 'safe' or receipt['optimizer_updates'] != 0 or receipt['weights_changed']:
        raise ValueError('measured safe, no-update benchmark required')
    state_path = a.DEFAULT/'alignment/STATE.json'
    state = a._read(state_path)
    if state.get('execution_host') == 'mini':
        return {'status': 'already_activated'}
    live = a.remote()
    old = a._read(a.DEFAULT/'alignment/BOOK_BOUNDARY_BEFORE_MIGRATION.json')
    if live['running'] or live['device_busy'] or live['files'].get('TRAIN_RESULT.json') != old:
        raise ValueError('Book changed during measured migration')
    folder = MINI/'research'/a.RUN
    overhead = receipt['seconds']
    if cpu and (a.DEFAULT/'alignment/MINI_BENCHMARK.json').exists():
        overhead += a._read(a.DEFAULT/'alignment/MINI_BENCHMARK.json')['seconds']
    saved = dict(old, checkpoint=str(folder/old['checkpoint_name']),
                 seconds=old['seconds']+overhead, migration_compute_seconds=overhead)
    a.write_once(folder/'BOOK_TRAIN_RESULT.json', canonical(old))
    # Preserve Book predictions; evaluate all models again on this same device.
    for path in folder.glob('EVAL-*.json'):
        if path.name.endswith('.book.json'):
            continue
        path.rename(path.with_suffix('.book.json'))
    rc._write_atomic(folder/'TRAIN_RESULT.json', saved)
    state.update(execution_host='mini', execution_revision=revision,
                 runner_name='moa-research-worker-v%d.py' % revision,
                 job_name='JOB-r%d.json' % revision, last_resume_boundary=None, status='AUXILIARY_READY',
                 resume_resource_boundary=None,
                 auxiliary_benchmark_sha256=a.sha(a.DEFAULT/'alignment'/bench_name))
    rc._write_atomic(state_path, state)
    a.write_once(a.DEFAULT/'alignment/AUXILIARY_MIGRATION.json', canonical({
        'at': a.now(), 'source': 'macbookair', 'destination': str(MINI), 'run_id': a.RUN,
        'candidate_added': False, 'updates_carried': 0, 'seconds_carried': saved['seconds'],
        'original_result_sha256': a.sha(a.DEFAULT/'alignment/BOOK_BOUNDARY_BEFORE_MIGRATION.json'),
        'job_sha256': a.sha(MINI/'incoming'/a.RUN/('JOB-r%d.json' % revision)),
        'execution_device': 'cpu' if cpu else 'mps', 'configuration_unchanged': True}))
    return {'status': state['status'], 'run_id': a.RUN, 'candidate_added': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'benchmark', 'activate'])
    parser.add_argument('--cpu', action='store_true')
    args = parser.parse_args()
    print(json.dumps(globals()[args.action](cpu=args.cpu), ensure_ascii=False))


if __name__ == '__main__':
    main()
