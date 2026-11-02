"""CPU-only actual runtime/training batch parity, for all six choice orders.

Requires the immutable incoming handoff and a successful tokenizer preflight.
No model constructor, weights, inference, optimizer or training process is used.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import itertools
import json
import sys
import time
from pathlib import Path

INPUT_FIELDS=('input_ids','attention_mask','marker_pos','marker_mask','qtype')


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def sha(raw): return hashlib.sha256(raw).hexdigest()


def verify_checkpoint(checkpoint, expected_files):
    checkpoint=Path(checkpoint)
    if checkpoint.is_symlink() or not expected_files: raise ValueError('checkpoint manifest/path')
    verified={}
    for name,expected in expected_files.items():
        relative=Path(name);path=checkpoint/relative
        if (relative.is_absolute() or '..' in relative.parts or path.is_symlink()
                or not path.resolve().is_relative_to(checkpoint.resolve())):
            raise ValueError('checkpoint path')
        digest=hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda:stream.read(4*1024*1024),b''):digest.update(chunk)
        verified[name]=digest.hexdigest()
        if verified[name]!=expected: raise ValueError('checkpoint changed after preflight')
    return verified


def check_batch(training,runtime,target,expected):
    if any(training[k].tolist()!=runtime[k].tolist() for k in INPUT_FIELDS):
        raise ValueError('actual model tensor mismatch')
    if target.tolist()!=[expected]: raise ValueError('permuted slot target mismatch')


def run(folder):
    root=Path.home()/'Library/Application Support/MOA-LAYA'
    folder=Path(folder).resolve()
    if not folder.is_relative_to(root/'incoming'): raise ValueError('incoming path required')
    output=folder/'PERMUTATION_PARITY.json'
    if output.exists(): raise ValueError('audit receipt exists')
    handoff=json.loads((folder/'R2_HANDOFF.json').read_bytes())
    parity=json.loads((folder/'TOKENIZER_PARITY.json').read_bytes())
    if parity['status']!='ready' or parity['handoff_sha256']!=sha((folder/'R2_HANDOFF.json').read_bytes()):
        raise ValueError('successful pinned tokenizer preflight required')
    for name,expected in handoff['files_sha256'].items():
        path=folder/name
        if Path(name).name!=name or path.is_symlink() or sha(path.read_bytes())!=expected:
            raise ValueError('handoff integrity')
    if any(p.name.startswith('blind-') for p in folder.iterdir()): raise ValueError('premature blind release')
    ready=json.loads((folder/'R2_READY.json').read_bytes())
    protected=[root/'MODEL_REGISTRY.json',root/'MODEL_MANIFEST.json']
    before={p.name:sha(p.read_bytes()) for p in protected}
    sys.path.insert(0,str(root/'app'))
    from transformers import AutoTokenizer
    from workers.laya_local import ml,policy
    import laya.agent as runtime_module
    import laya.train as training_module
    import laya.common as common
    checkpoint=root/'candidates/moa-laya-recovery-20261018-v1-r1/checkpoint'
    checkpoint_hashes=verify_checkpoint(checkpoint,handoff['checkpoint_files'])
    tok=AutoTokenizer.from_pretrained(str(checkpoint/'tokenizer'),local_files_only=True)
    # The installed runtime supports lightweight input-only objects in its tests.
    # __init__/from_pretrained would load weights; neither is called here.
    runtime=runtime_module.Agent.__new__(runtime_module.Agent)
    runtime.tok=tok
    runtime.cfg=json.loads((checkpoint/'rl_agent_config.json').read_bytes())
    runtime.parallel_options=common.uses_parallel_layout(runtime.cfg)
    adapter_path=root/handoff['installed_adapter']
    if sha(adapter_path.read_bytes())!=handoff['installed_adapter_sha256']: raise ValueError('adapter changed')
    spec=importlib.util.spec_from_file_location('existing_adapter',adapter_path)
    adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)
    qdefs=policy.questions(list(policy.CRITERIA))
    if qdefs!=ready['questions']: raise ValueError('rubric mismatch')
    rows=[]
    for shard in ready['shards']:
        if shard['transport_to_book']:
            if shard['split'] not in ('train','dev'): raise ValueError('wrong partition')
            rows.extend(json.loads(line) for line in (folder/shard['file']).read_bytes().splitlines())
    start=time.monotonic();checks=0;digest=hashlib.sha256()
    for row in rows:
        criterion=next(iter(row['expected']))
        for mode,state in [('facts',row['state']),('text_control',row['legacy_state'])]:
            training=adapter.prepare_training_rows([{**row,'state':state}],qdefs)[0]
            runtime_state=policy.state_for(state)
            ml.preflight(tok,runtime_state,training['questions'])
            items,skipped=training_module.items_from_rows(tok,[training],1024,256)
            if skipped or len(items)!=1: raise ValueError('training item skipped')
            item=items[0]
            if list(item['q']['crit'])!=ready['choice_order']: raise ValueError('choice order mismatch')
            for permutation in itertools.permutations(range(3)):
                if time.monotonic()-start>=600: raise ValueError('audit time limit')
                order=list(permutation)
                q=copy.deepcopy(training_module.to_internal(criterion,qdefs[criterion]))
                q['option_order']=order
                observed=runtime._encode_state(runtime_state,[criterion],{criterion:q},1024,256)
                if observed[0]['state_stats']['truncated']: raise ValueError('runtime truncation')
                encoded=training_module.encode_item(tok,item,1024,256,option_order=order,
                                                   parallel=runtime.parallel_options)
                training_batch=common.collate_items([[encoded]],tok.pad_token_id)
                runtime_batch=common.collate_items([observed],tok.pad_token_id)
                gold=ready['gold_index'][row['expected'][criterion]]
                target=[float(i==gold) for i in order]
                check_batch(training_batch,runtime_batch,training_batch['target'],target)
                digest.update(canonical({'case_id':row['case_id'],'mode':mode,'order':order,
                    **{k:runtime_batch[k].tolist() for k in INPUT_FIELDS},'target':target}))
                checks+=1
    after={p.name:sha(p.read_bytes()) for p in protected}
    if before!=after: raise ValueError('model metadata changed')
    if verify_checkpoint(checkpoint,handoff['checkpoint_files'])!=checkpoint_hashes:
        raise ValueError('checkpoint changed during audit')
    receipt={'status':'passed','rows':len(rows),'representations':2,'permutations_per_input':6,
        'actual_batch_checks':checks,'fields_checked':list(INPUT_FIELDS),'slot_target_parity':True,
        'all_checks_sha256':digest.hexdigest(),'script_sha256':sha(Path(__file__).read_bytes()),
        'handoff_sha256':sha((folder/'R2_HANDOFF.json').read_bytes()),
        'base_tokenizer_receipt_sha256':sha((folder/'TOKENIZER_PARITY.json').read_bytes()),
        'checkpoint_files_sha256':checkpoint_hashes,'checkpoint_reverified_before_and_after':True,
        'installed_code_sha256':{m.__name__:sha(Path(m.__file__).read_bytes())
            for m in (runtime_module,training_module,common,policy,ml)},
        'model_metadata_unchanged':True,'model_loaded':False,'model_inference_calls':0,
        'training_started':False,'elapsed_seconds':time.monotonic()-start}
    with output.open('xb') as stream:stream.write(canonical(receipt)+b'\n')
    output.chmod(0o600)
    return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder',type=Path,required=True)
    args=parser.parse_args()
    try: print(json.dumps(run(args.folder)))
    except Exception as error:
        print(json.dumps({'status':'stopped','error_type':type(error).__name__,'training_started':False}))
        raise SystemExit(1)
