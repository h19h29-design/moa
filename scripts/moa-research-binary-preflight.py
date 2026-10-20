"""Verify the actual installed two-choice tokenizer/target seam; never load weights."""
from __future__ import annotations

import fcntl
import importlib.util
import json
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from moa import research_alignment as a, research_binary_control as control
from moa import research_campaign as rc, r2_research as research


def preflight(root):
    root=Path(root)
    if root!=a.DEFAULT:raise ValueError('only the existing isolated worker is allowed')
    with (root/control.FOLDER/'.controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return _preflight(root)


def _preflight(root):
    folder=root/control.FOLDER;w=root/'mini-worker';stage=folder/'stage'
    plan=rc._read_json(folder/'PLAN.json');state=rc._read_json(folder/'STATE.json')
    control.verify(root,plan,state)
    if state.get('input_preflight_sha256'):return rc._read_json(folder/'INPUT_PREFLIGHT.json')
    if state.get('training_issued'):raise ValueError('preflight cannot replace a dispatched input')
    os.environ.update(MOA_RESEARCH_HOME=str(w),MOA_LAYA_HOME=str(w),HF_HUB_OFFLINE='1',
        TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',TOKENIZERS_PARALLELISM='false')
    sys.path.insert(0,str(w/'app'))
    from workers.laya_local import ml,policy,runtime
    import laya.train as tr
    from transformers import AutoTokenizer
    from laya.common import build_sequence
    job=rc._read_json(stage/'JOB.json')
    for module in (ml,policy,runtime,tr):
        if a.sha(module.__file__)!=job['installed_sha256'][module.__name__]:
            raise ValueError('installed implementation changed')
    spec=importlib.util.spec_from_file_location('binary_preflight_worker',stage/'moa-research-worker.py')
    worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
    parent=worker.confined(w,job['parent'])
    # Parent tokenizer bytes are pinned independently of its model weights.
    tokenizer_hashes={n:h for n,h in job['parent_files'].items() if n.startswith('tokenizer/')}
    if not tokenizer_hashes or any(a.sha(parent/n)!=h for n,h in tokenizer_hashes.items()):
        raise ValueError('pinned parent tokenizer changed')
    with runtime.exclusive(w/'device.lock'):
        if not runtime.resources()['verify_allowed']:return {'status':'waiting_resources'}
        tok=AutoTokenizer.from_pretrained(parent/'tokenizer',local_files_only=True)
        train=worker.queue(control._rows(stage/'train-000.jsonl'),policy,job['questions'],
            with_gold=True,semantic_profile=control.PROFILE)
        if worker.digest(worker.canonical(train))!=plan['data_sha256']:
            raise ValueError('actual worker train input differs from staged binary data')
        items,skipped=tr.items_from_rows(tok,train,1024,256)
        if skipped or len(items)!=len(train):raise ValueError('no skipped binary training rows')
        maximum=0;permutations=0
        for row,item in zip(train,items):
            qid=next(iter(row['questions']));gold=row['expected'][qid]
            keys=list(tr.to_internal(qid,row['questions'][qid])['crit'])
            ml.preflight(tok,row['state'],row['questions'])
            for order in ([0,1],[1,0]):
                encoded=tr.encode_item(tok,item,1024,256,order,False)
                target=encoded['target'];markers=encoded['markers']
                if (len(target)!=2 or len(markers)!=2 or sum(target)!=1 or
                        keys[order[target.index(max(target))]]!=gold):
                    raise ValueError('binary tokenizer gold index/target mismatch')
                maximum=max(maximum,len(encoded['ids']));permutations+=1
        dev=control._rows(stage/'dev-000.jsonl')
        for row in dev:
            if set(row)!={'case_id','state','questions'}:raise ValueError('dev must be gold-free')
            normalized=worker.queue([row],policy,job['questions'],semantic_profile=control.PROFILE)[0]
            qid=next(iter(normalized['questions']))
            for order in (False,True):
                q=tr.to_internal(qid,normalized['questions'][qid])
                if order:q['crit']=dict(reversed(list(q['crit'].items())))
                ids,markers,stats,trunc=build_sequence(tok,normalized['state'],q,1024,256,
                    return_stats=True,return_truncation_stats=True)
                if len(markers)!=2 or stats['options_distinct']!=2 or trunc['truncated']:
                    raise ValueError('binary development tokenizer overflow or missing choice')
                maximum=max(maximum,len(ids));permutations+=1
        result=dict(status='ready',run_id=control.RUN,job_sha256=plan['stage_sha256']['JOB.json'],
            data_sha256=plan['data_sha256'],tokenizer_sha256=tokenizer_hashes,
            option_labels=['preserved','changed'],train_rows=len(train),dev_rows=len(dev),
            actual_option_permutations=permutations,max_tokens=maximum,skipped=0,truncated=0,
            optimizer_updates=0,model_loaded=False,model_inference=False)
        a.write_once(folder/'INPUT_PREFLIGHT.json',research.canonical(result))
    state.update(input_preflight_sha256=a.sha(folder/'INPUT_PREFLIGHT.json'),status='WAITING_BUDGET')
    rc._write_atomic(folder/'STATE.json',state)
    return result


if __name__=='__main__':
    result=preflight(a.DEFAULT)
    print(json.dumps(result,ensure_ascii=False))
