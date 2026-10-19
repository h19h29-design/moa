"""MacBook CPU/tokenizer audit only. No model loading, inference or training.

Run in the existing venv-smoke, on a hash-pinned incoming R2 handoff. The actual
installed training item builder and runtime formatter must produce equal token
IDs, marker positions, option order and expected-label targets. Never truncate
an over-budget row to make the audit green.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import time
from pathlib import Path


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def sha(raw): return hashlib.sha256(raw).hexdigest()


def file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(4*1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def confined(root, name):
    relative = Path(name)
    path = root/relative
    if relative.is_absolute() or '..' in relative.parts or path.is_symlink():
        raise ValueError('checkpoint path')
    if not path.resolve().is_relative_to(root.resolve()): raise ValueError('checkpoint path')
    return path


def assert_parity(training_sequence, runtime_sequence, target, gold_index):
    if training_sequence[:2] != runtime_sequence[:2]: raise ValueError('token/marker parity')
    if target != [float(i==gold_index) for i in range(3)]: raise ValueError('gold index parity')


def run(folder):
    root = Path.home()/'Library/Application Support/MOA-LAYA'
    folder = Path(folder).resolve()
    if not folder.is_relative_to(root/'incoming'): raise ValueError('private incoming path required')
    output = folder/'TOKENIZER_PARITY.json'
    if output.exists(): raise ValueError('preflight receipt already exists')
    handoff = json.loads((folder/'R2_HANDOFF.json').read_bytes())
    for name, expected in handoff['files_sha256'].items():
        p = folder/name
        if Path(name).name != name or p.is_symlink() or sha(p.read_bytes()) != expected:
            raise ValueError('handoff integrity')
    ready = json.loads((folder/'R2_READY.json').read_bytes())
    if any(p.name.startswith('blind-') for p in folder.iterdir()): raise ValueError('blind shard sent prematurely')
    checkpoint = root/'candidates/moa-laya-recovery-20261018-v1-r1/checkpoint'
    if checkpoint.is_symlink(): raise ValueError('checkpoint symlink')
    for name, expected in handoff['checkpoint_files'].items():
        if file_sha(confined(checkpoint,name)) != expected: raise ValueError('R1 checkpoint changed')
    protected = [root/'MODEL_REGISTRY.json',root/'MODEL_MANIFEST.json']
    before = {p.name:sha(p.read_bytes()) for p in protected}
    import sys
    sys.path.insert(0,str(root/'app'))
    from transformers import AutoTokenizer
    from workers.laya_local import ml,policy
    from laya.common import build_sequence,serialize_state
    from laya.train import items_from_rows,to_internal
    adapter_file = root/handoff['installed_adapter']
    if sha(adapter_file.read_bytes()) != handoff['installed_adapter_sha256']:
        raise ValueError('installed adapter changed')
    spec=importlib.util.spec_from_file_location('moa_r2_existing_adapter',adapter_file)
    adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)
    qdefs=policy.questions(list(policy.CRITERIA))
    if qdefs != ready['questions']: raise ValueError('actual question definitions mismatch')
    if sha(canonical(qdefs)) != ready['rubric_sha256']: raise ValueError('actual question hash mismatch')
    tok=AutoTokenizer.from_pretrained(str(checkpoint/'tokenizer'),local_files_only=True)
    rows=[]
    for shard in ready['shards']:
        if not shard['transport_to_book']: continue
        if shard['split'] not in ('train','dev'): raise ValueError('unexpected split')
        rows.extend(json.loads(line) for line in (folder/shard['file']).read_bytes().splitlines())
    prepared=adapter.prepare_training_rows(rows,qdefs)
    start=time.monotonic(); results=[]; overflows=[]; max_tokens=0
    for row in prepared:
        if time.monotonic()-start >= 600: raise ValueError('preflight time budget reached')
        criterion=next(iter(row['expected']))
        for mode,state in [('facts',row['state']),('text_control',row['legacy_state'])]:
            # Rebuild both states using the installed runtime's insertion order.
            payload={**state,'questions':[criterion]}
            runtime_state=policy.state_for(payload)
            training=adapter.prepare_training_rows([{**row,'state':state}],qdefs)[0]
            if serialize_state(training['state']) != serialize_state(runtime_state):
                raise ValueError('serialized state parity')
            try:
                measured=ml.preflight(tok,runtime_state,training['questions'])
            except ValueError as error:
                if not str(error).startswith('token_budget_exceeded:'): raise
                overflows.append({'case_id':row['case_id'],'mode':mode,'reason':'context_overflow'})
                continue
            items,skipped=items_from_rows(tok,[training],max_len=1024,head_max_len=256)
            if skipped or len(items)!=1: raise ValueError('training item skipped')
            item=items[0]; q=to_internal(criterion,qdefs[criterion])
            if list(q['crit']) != ready['choice_order']: raise ValueError('actual choice order')
            runtime_sequence=build_sequence(tok,runtime_state,q,max_len=1024,head_max_len=256,
                                              return_truncation_stats=True)
            training_sequence=build_sequence(tok,training['state'],item['q'],state_ids=item['state_ids'],
                                               max_len=1024,head_max_len=256,return_truncation_stats=True)
            expected=next(iter(row['expected'].values()))
            assert_parity(training_sequence,runtime_sequence,item['target'],ready['gold_index'][expected])
            if runtime_sequence[-1]['truncated']: raise ValueError('unexpected truncation')
            # Check a non-identity option order as well; do not train with raw
            # canonical JSON's alphabetical criteria ordering.
            order=[2,0,1]
            a=build_sequence(tok,runtime_state,q,max_len=1024,head_max_len=256,option_order=order)
            b=build_sequence(tok,training['state'],item['q'],state_ids=item['state_ids'],
                             max_len=1024,head_max_len=256,option_order=order)
            if a[:2]!=b[:2]: raise ValueError('shuffled option parity')
            n=measured[criterion]['actual_tokens'];max_tokens=max(max_tokens,n)
            results.append({'case_id':row['case_id'],'mode':mode,'tokens':n,'gold_index':ready['gold_index'][expected],
                'input_ids_sha256':sha(canonical(runtime_sequence[:2]))})
    after={p.name:sha(p.read_bytes()) for p in protected}
    if before!=after: raise ValueError('protected model metadata changed')
    receipt={'status':'ready' if not overflows else 'context_overflow_requires_review',
        'runner_sha256':sha(Path(__file__).read_bytes()),'handoff_sha256':sha((folder/'R2_HANDOFF.json').read_bytes()),
        'ready_sha256':sha((folder/'R2_READY.json').read_bytes()),'received_rows':len(rows),
        'checked_sequences':len(results),'overflows':overflows,'max_actual_tokens':max_tokens,
        'serialized_state_parity':True,'token_ids_marker_parity':True,'gold_index_parity':True,
        'shuffled_options_checked':True,'no_truncation':True,'blind_data_received':False,
        'tokenizer_sha256':handoff['checkpoint_files']['tokenizer/tokenizer.json'],
        'installed_policy_sha256':sha(Path(policy.__file__).read_bytes()),
        'installed_ml_sha256':sha(Path(ml.__file__).read_bytes()),
        'model_metadata_before':before,'model_metadata_after':after,
        'training_started':False,'model_loaded':False,'model_inference_calls':0,
        'elapsed_seconds':time.monotonic()-start,'cases':results}
    output.write_bytes(canonical(receipt)+b'\n');output.chmod(0o600)
    return {k:v for k,v in receipt.items() if k not in ('cases','model_metadata_before','model_metadata_after')}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',type=Path,required=True)
    args=parser.parse_args()
    try: print(json.dumps(run(args.folder)))
    except Exception as error:
        print(json.dumps({'status':'stopped','error_type':type(error).__name__,
                          'training_started':False}))
        raise SystemExit(1)
