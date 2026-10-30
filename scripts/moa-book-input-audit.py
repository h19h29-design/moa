"""CPU tokenizer audit on the installed Book LAYA; no forward/backward/optimizer.

Run with the existing venv. This command only prepares a protected agreement for
MacBook Codex, which remains the sole executor of S1/R1 and model evaluation.
"""
import argparse
import hashlib
import importlib.util
import inspect
import itertools
import json
import os
from pathlib import Path
import signal
import sys

ROOT=Path.home()/'Library/Application Support/MOA-LAYA'
os.environ.update(HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',
                  TOKENIZERS_PARALLELISM='false',WANDB_DISABLED='true')
sys.path.insert(0,str(ROOT/'app'))


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec); spec.loader.exec_module(result)
    return result


def file_sha(path):
    with Path(path).open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--adapter',type=Path,required=True)
    p.add_argument('--common',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args(); signal.alarm(600)
    from workers.laya_local import policy, ml
    from laya import Agent
    from laya import common, train
    adapter=module('moa_recovery_adapter',args.adapter)
    agreement=module('moa_recovery_common',args.common)
    rows,bundle,bundle_sha=adapter.bundle_rows()
    prepared=adapter.prepare_training_rows(rows,policy.questions(list(policy.CRITERIA)))
    base=json.loads((ROOT/'MODEL_MANIFEST.json').read_bytes())
    old=json.loads((ROOT/'candidates/train-pilot-ef81fadafc79bb2cfdee-resource-retry1/TRAIN_RESULT.json').read_bytes())
    for path,hashes in ((Path(base['path']),base['sha256']),(Path(old['checkpoint']),old['sha256'])):
        for name,want in hashes.items():
            target=path/name
            if target.is_symlink() or not target.is_file() or file_sha(target)!=want:
                raise ValueError('existing_checkpoint_integrity')
    registry=json.loads((ROOT/'MODEL_REGISTRY.json').read_bytes())['current']
    if (Path(registry['path']).resolve()!=Path(base['path']).resolve() or registry['sha256']!=base['sha256']):
        raise ValueError('worker_current_is_not_pinned_base')
    cfg=json.loads((Path(base['path'])/'rl_agent_config.json').read_bytes())
    agent_module=sys.modules[Agent.__module__]
    settings=agreement.encoding_settings(cfg,adapter.QUALITY_CONFIG,
             agreement.predict_encoding_overrides(inspect.getsource(ml.predict_payload)),
             parallel=agent_module.uses_parallel_layout(cfg))
    max_len,head_max_len=settings['max_len'],settings['head_max_len']
    tok=agent_module._load_tokenizer(str(Path(base['path'])/'tokenizer'),cfg)
    # Exercise the installed Agent's actual runtime encoder without loading model
    # weights or invoking inference. Training uses items_from_rows/encode_item.
    agent=Agent.__new__(Agent); agent.tok=tok
    agent.cfg=cfg; agent.parallel_options=agent_module.uses_parallel_layout(cfg)
    records=[]; max_tokens=0; tensor_fields=('input_ids','attention_mask','marker_pos','marker_mask','qtype')
    token_ledger={}; token_questions=0
    for original,row in zip(rows,prepared):
        qs=policy.questions(list(row['questions']))
        runtime_state=policy.state_for(json.loads(adapter.canonical(original['state'])))
        if common.serialize_state(row['state'])!=common.serialize_state(runtime_state):
            raise ValueError('actual_state_string_mismatch')
        if json.dumps(row['questions'],ensure_ascii=False)!=json.dumps(qs,ensure_ascii=False):
            raise ValueError('actual_question_string_mismatch')
        items,skipped=train.items_from_rows(tok,[row],max_len,head_max_len)
        if skipped or len(items)!=len(qs): raise ValueError('actual_item_inventory')
        qids=list(qs)
        internal={qid:Agent._to_internal(qs[qid]) for qid in qids}
        runtime_items=agent._encode_state(runtime_state,qids,internal,max_len,head_max_len)
        runtime_batch=common.collate_items([runtime_items],tok.pad_token_id)
        encoded=[train.encode_item(tok,item,max_len,head_max_len) for item in items]
        train_batch=common.collate_items([encoded],tok.pad_token_id)
        for field in tensor_fields:
            if not train_batch[field].equal(runtime_batch[field]): raise ValueError('actual_tensor_mismatch:'+field)
        question_hashes=[]
        for qid,item,encoded_item in zip(qids,items,encoded):
            labels=list(qs[qid]['criteria']); gold=row['expected'][qid]
            if labels[max(range(3),key=item['target'].__getitem__)]!=gold:
                raise ValueError('actual_gold_index_mismatch')
            for order in itertools.permutations(range(3)):
                shuffled=train.encode_item(tok,item,max_len,head_max_len,option_order=list(order))
                got=max(range(3),key=shuffled['target'].__getitem__)
                if labels[order[got]]!=gold: raise ValueError('shuffled_gold_index_mismatch')
            one=common.collate_items([[encoded_item]],tok.pad_token_id)
            question_hashes.append(agreement.record_token_input(token_ledger,
                {k:one[k].tolist() for k in tensor_fields},gold,row['meta']['split']))
            token_questions+=1
        if any(it['state_stats']['truncated'] for it in runtime_items): raise ValueError('actual_truncation')
        max_tokens=max(max_tokens,max(len(it['ids']) for it in runtime_items))
        tensors={k:train_batch[k].tolist() for k in tensor_fields}
        records.append({'row_id':agreement.sha(original),'split':row['meta']['split'],
                        'model_input_sha256':agreement.sha(agreement.model_input(row),ordered=True),
                        'tensor_sha256':agreement.sha(tensors),
                        'gold_indices':[int(x.argmax()) for x in train_batch['target']],
                        'gold_labels':[row['expected'][k] for k in qids],
                        'question_token_sha256':question_hashes,
                        'six_option_permutations_verified':True})
    ready=agreement.build_manifest(prepared,bundle_sha256=bundle_sha,
                    base_sha256=base['sha256']['model.safetensors'],previous_sha256=old['sha256']['model.safetensors'])
    ready.update(training_config=adapter.QUALITY_CONFIG,
                 actual_encoding_settings=settings,
                 checkpoint_files_sha256={'base':base['sha256'],'previous':old['sha256']},
                 rubric_revision=policy.RUBRIC,questions_revision=policy.QUESTIONS_REVISION,
                 rubric_sha256=policy.digest(policy.questions(list(policy.CRITERIA))),
                 shard_hashes={s['file']:s['sha256'] for s in bundle['shards']},
                 actual_tokenizer_revision=base['sha256']['tokenizer/tokenizer.json'],
                 actual_input_audit={'rows':len(records),'tensor_records_sha256':agreement.sha(records),
                                    'all_masks_and_marker_tensors_equal':True,'maximum_tokens':max_tokens,
                                    'fresh_preprocessing':True,'persisted_item_cache_consumed':False,
                                    'token_input_questions_checked':token_questions,
                                    'unique_question_token_inputs':len(token_ledger),
                                    'token_input_gold_conflicts':0,'token_input_split_conflicts':0,
                                    'formatter_revision':adapter.MODEL_INPUT_REVISION,
                                    'installed_modules':{m.__name__:file_sha(m.__file__) for m in (train,common)},
                                    'agent_module_sha256':file_sha(sys.modules[Agent.__module__].__file__)},
                 code_sha256={'adapter':file_sha(args.adapter),'common':file_sha(args.common),
                              'audit':file_sha(__file__),'installed_worker_ml':file_sha(ml.__file__)},
                 execution_claim={'owner':'macbook_codex','training_started_by_mini':False},
                 book_preflight_required=['S1 gradient/optimizer/periodic atomic checkpoint checks',
                                          'independent R1; existing base; no sweep',
                                          'separate durable per-item evaluation, total 1800 seconds'])
    args.output.mkdir(parents=True,exist_ok=True,mode=0o700)
    policy.atomic_json(args.output/'TOKEN_INPUT_AUDIT.json',records)
    policy.atomic_json(args.output/'RECOVERY_READY.json',ready)
    print(json.dumps({'status':ready['status'],'rows':len(records),'maximum_tokens':max_tokens,
                      'ready_sha256':file_sha(args.output/'RECOVERY_READY.json'),
                      'training_started':False,'run_ids':ready['run_ids']}))


if __name__=='__main__': main()
