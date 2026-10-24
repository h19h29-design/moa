"""One MacBook-only, frozen-weight input projection. Never trains or reads gold."""
from __future__ import annotations

from collections import defaultdict
import gc
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import time

ROOT = Path('/Users/jungwoohwang/Library/Application Support/MOA-LAYA')
RUN = 'moa-unattended-20261021-binary-train-01'
VERSION = 'moa.lossless-source-dom-paired-view.v1'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def paired_state(state):
    """Use supplied correspondence only; preserve every block exactly once.

    A separate DOM inventory retains actual order. Multi-reference blocks are
    displayed under their first known source, with every reference unchanged.
    Missing evidence is never synthesized from the source or a model answer.
    """
    if set(state) != {'title', 'file_role', 'source_complete', 'source_blocks',
                      'rendered_blocks', 'attachment_manifest'}:
        raise ValueError('exact gold-free state contract required')
    sources, outputs = state['source_blocks'], state['rendered_blocks']
    if not isinstance(sources, list) or not isinstance(outputs, list):
        raise ValueError('block lists required')
    known, dom_ids = set(), set()
    for row in sources:
        if (set(row) != {'id', 'text'} or not isinstance(row['id'], str) or not row['id'] or
                row['id'] in known or not isinstance(row['text'], str)):
            raise ValueError('unique literal source evidence required')
        known.add(row['id'])
    paired = defaultdict(list)
    for row in outputs:
        if (set(row) != {'id', 'source_refs', 'visible_text'} or not isinstance(row['id'], str) or
                not row['id'] or row['id'] in dom_ids or not isinstance(row['visible_text'], str) or
                not isinstance(row['source_refs'], list) or
                any(not isinstance(ref, str) for ref in row['source_refs'])):
            raise ValueError('unique actual DOM evidence required')
        dom_ids.add(row['id'])
        key = next((ref for ref in row['source_refs'] if ref in known), None)
        paired[key].append(row)
    metadata = {k: v for k, v in state.items() if k not in ('source_blocks', 'rendered_blocks')}
    lines = ['문서 정보: ' + canonical(metadata),
             '실제 DOM 순서: ' + canonical([row['id'] for row in outputs])]
    for source in sources:
        lines.append('원문: ' + canonical(source))
        matches = paired[source['id']]
        lines.extend('실제 HTML: ' + canonical(row) for row in matches)
        if not matches:
            lines.append('대응 HTML 없음 (자동 보충하지 않음)')
    if paired[None]:
        lines.append('원문 대응 없음: 실제 HTML을 그대로 유지')
        lines.extend('실제 HTML: ' + canonical(row) for row in paired[None])
    return '\n'.join(lines)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def load_context(worker, job_path):
    """Match the existing worker's launch directory without changing its pins."""
    if job_path.parent.resolve() != (ROOT / 'incoming' / RUN).resolve() or job_path.is_symlink():
        raise ValueError('fixed incoming directory required')
    os.chdir(job_path.parent)
    return worker.context(job_path)


def probe(control):
    started = time.monotonic()
    if control['run_id'] != RUN or control['maximum_seconds'] != 90:
        raise ValueError('fixed input-only experiment boundary')
    def timeout(*_):
        raise TimeoutError('input probe time limit')
    signal.signal(signal.SIGALRM, timeout)
    signal.setitimer(signal.ITIMER_REAL, 85)
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                      TOKENIZERS_PARALLELISM='false', MOA_LAYA_HOME=str(ROOT))
    import socket
    def no_network(*_a, **_kw):
        raise RuntimeError('local probe has no network access')
    socket.socket.connect = no_network
    import torch
    from transformers import AutoTokenizer
    from laya.common import build_sequence, QTYPES
    torch.set_num_threads(2)
    os.nice(10)
    incoming = ROOT / 'incoming' / RUN
    job_path = incoming / 'JOB-r1.json'
    request_path = incoming / 'BINARY_REQUEST.json'
    if sha(job_path) != control['job_sha256'] or sha(request_path) != control['request_sha256']:
        raise ValueError('actual job/request changed')
    spec = importlib.util.spec_from_file_location('input_probe_worker', incoming / 'moa-research-worker.py')
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    job, _, _, ml, _, runtime, tr = load_context(worker, job_path)
    spec = importlib.util.spec_from_file_location('original_evaluator', incoming / 'moa-research-book-evaluate.py')
    original = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(original)
    if sha(incoming / 'moa-research-book-evaluate.py') != control['original_evaluator_sha256']:
        raise ValueError('original two-order evaluator changed')
    run = ROOT / 'research' / RUN
    saved = json.loads((run / 'TRAIN_RESULT.json').read_bytes())
    checkpoint = Path(saved['checkpoint'])
    if (checkpoint.is_symlink() or checkpoint.resolve().parent != run.resolve() or
            saved['job_sha256'] != control['job_sha256'] or saved['sha256'] != control['checkpoint_sha256']):
        raise ValueError('saved candidate boundary changed')
    receipt = run / 'input-probe-20261021' / 'RESULT.json'
    if receipt.exists():
        raise ValueError('prior probe exists; no rerun or overwrite')
    request = json.loads(request_path.read_bytes())
    if len(request) != 54:
        raise ValueError('only the existing exposed development subset is allowed')
    pending = original.evaluation_inputs(request, [])
    result = dict(status='incomplete', version=VERSION, model_sha256=saved['sha256']['model.safetensors'],
        request_sha256=control['request_sha256'], records=[], execution_host='macbookair', execution_device='mps',
        system_forced=False, trained=False, training_updates=0, new_candidates=0, new_blind=False,
        threshold_fitted=False, operational_promotion=False, maximum_seconds=90)
    resources, lengths = [], []
    try:
        with runtime.exclusive(ROOT / 'device.lock'):
            def guard(minimum):
                current = runtime.resources()
                if (not current['verify_allowed'] or current['available_memory_bytes'] < minimum or
                        (run / 'PAUSED').exists()):
                    raise RuntimeError('MacBook input probe resource guard')
                resources.append(current['available_memory_bytes'])
            guard(7 * 2**30)
            ml.verify_model(str(checkpoint), saved['sha256'])
            tok = AutoTokenizer.from_pretrained(checkpoint / 'tokenizer', local_files_only=True)
            encoded = []
            views = []
            for row in pending:
                qid = next(iter(row['questions']))
                q = tr.to_internal(qid, row['questions'][qid])
                view = paired_state(row['state'])
                ids, markers, stats, trunc = build_sequence(tok, view, q, 1024, 256,
                    return_stats=True, return_truncation_stats=True)
                if trunc['truncated'] or len(ids) > 1024 or stats['options_distinct'] != 2:
                    raise ValueError('input overflow cannot count as success')
                encoded.append((row, q, ids, markers))
                lengths.append(len(ids)); views.append(view)
            result['paired_input_sha256'] = hashlib.sha256(canonical(views).encode()).hexdigest()
            result['maximum_input_tokens'] = max(lengths)
            result['truncated_inputs'] = 0
            guard(7 * 2**30)
            model, _, _ = tr.load_checkpoint(str(checkpoint))
            model.encoder.config.reference_compile = False
            model.to('mps'); model.eval(); model.requires_grad_(False)
            for row, q, ids, markers in encoded:
                gc.collect(); torch.mps.synchronize(); torch.mps.empty_cache()
                guard(5 * 2**30)
                batch = tr.collate_items([[dict(ids=ids, markers=markers, qtype=QTYPES[q['t']],
                    target=[0.0] * len(markers))]], tok.pad_token_id)
                with torch.inference_mode():
                    logits = tr._forward(model, batch, torch.device('mps'), False, True)[0]
                z = logits.detach().cpu(); keys = list(q['crit'])
                result['records'].append(dict(case_id=row['case_id'], mode=row['mode'], model='binary_candidate',
                    split='dev', operation=row['operation'], family=row['family'], prediction=keys[int(z.argmax())],
                    logits=dict(zip(keys, z.tolist())), probabilities=dict(zip(keys, z.softmax(-1).tolist())),
                    system_forced=False))
                del batch, logits, z
            guard(5 * 2**30)
            result['weights_unchanged'] = sha(checkpoint / 'model.safetensors') == result['model_sha256']
            result['training_state_unchanged'] = sha(checkpoint / 'training-state.pt') == saved['sha256']['training-state.pt']
            result['status'] = 'complete'
    except Exception as exc:
        result.update(status='stopped', error_type=type(exc).__name__)
    finally:
        result.update(seconds=time.monotonic() - started,
            minimum_available_memory_bytes=min(resources) if resources else None)
        signal.setitimer(signal.ITIMER_REAL, 0)
        worker.atomic_json(receipt, result)
    return result


if __name__ == '__main__':
    try:
        print(json.dumps(probe(json.load(sys.stdin)), allow_nan=False))
    except Exception as exc:
        print(json.dumps(dict(status='stopped', error_type=type(exc).__name__)))
        raise SystemExit(1)
