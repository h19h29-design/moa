"""Separate-process, local-only MacBook reload and gold-free binary evaluation."""
from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import time


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(4*1024*1024),b''):h.update(block)
    return h.hexdigest()


def evaluation_inputs(request, records):
    """Match the original per-case forward/reverse sequence without any gold."""
    pending=[];seen=set()
    for original in request:
        if (set(original)!={'case_id','state','questions','operation','family'} or
                original['case_id'] in seen or len(original['questions'])!=1):
            raise ValueError('unique gold-free binary input required')
        seen.add(original['case_id'])
        for mode in ('binary','binary_reverse'):
            row=dict(original,mode=mode)
            qid=next(iter(row['questions']));question=dict(row['questions'][qid])
            labels=list(question['criteria'])
            if set(labels)!={'preserved','changed'}:raise ValueError('exactly two semantic choices required')
            if mode=='binary_reverse':labels.reverse()
            question['criteria']={k:question['criteria'][k] for k in labels}
            row['questions']={qid:question};pending.append(row)
    expected=[(r['case_id'],r['mode']) for r in pending]
    if [(r['case_id'],r['mode']) for r in records]!=expected[:len(records)]:
        raise ValueError('evaluation prefix changed')
    return pending[len(records):]


def evaluate(job_path, control_path):
    control=json.loads(control_path.read_bytes())
    if (sha(Path(__file__))!=control['evaluator_sha256'] or sha(job_path)!=control['job_sha256'] or
            control['maximum_eval_seconds']!=600):raise ValueError('pinned evaluator/job/limit changed')
    incoming=job_path.parent;request_path=incoming/'BINARY_REQUEST.json'
    if sha(request_path)!=control['request_sha256']:raise ValueError('request changed')
    spec=importlib.util.spec_from_file_location('book_binary_worker',incoming/'moa-research-worker.py')
    worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
    job,_,_,ml,policy,runtime,tr=worker.context(job_path)
    import torch
    from laya.common import build_sequence,QTYPES
    run=worker.ROOT/'research'/job['run_id'];saved=json.loads((run/'TRAIN_RESULT.json').read_bytes())
    if saved['status']!='saved' or not saved['training_complete']:raise ValueError('saved completed candidate required')
    receipt=run/'EVALUATION.json';old=json.loads(receipt.read_bytes()) if receipt.exists() else {}
    if old and (old['request_sha256']!=control['request_sha256'] or old['model_sha256']!=saved['sha256']['model.safetensors']):
        raise ValueError('prior evaluation identity changed')
    if old.get('status')=='complete':return old
    elapsed=float(old.get('seconds',0))
    if not 0<=elapsed<=600:raise ValueError('invalid cumulative evaluation seconds')
    records=list(old.get('records',[]))
    pending=evaluation_inputs(json.loads(request_path.read_bytes()),records)
    result=dict(status='failed',records=records,seconds=elapsed,request_sha256=control['request_sha256'],
        model_sha256=saved['sha256']['model.safetensors'],separate_process=True,execution_device='mps',
        system_forced=False,trained=False,operational_promotion=False)
    started=time.monotonic()
    def timeout(*_):raise TimeoutError('evaluation time limit')
    def save():
        result['seconds']=elapsed+time.monotonic()-started;worker.atomic_json(receipt,result)
    if elapsed>=580:result['status']='evaluation_time_limit';save();return result
    previous=signal.signal(signal.SIGALRM,timeout);signal.setitimer(signal.ITIMER_REAL,590-elapsed)
    try:
        with runtime.exclusive(worker.ROOT/'device.lock'):
            if not runtime.resources()['verify_allowed']:
                result['status']='waiting_resources';return result
            ml.verify_model(saved['checkpoint'],saved['sha256'])
            model,tok,_=tr.load_checkpoint(saved['checkpoint'])
            model.encoder.config.reference_compile=False
            model.to('mps');model.eval();model.requires_grad_(False)
            for row in pending:
                gc.collect();torch.mps.synchronize();torch.mps.empty_cache()
                resource=runtime.resources()
                if not resource['verify_allowed'] or resource['available_memory_bytes']<5*2**30 or (run/'PAUSED').exists():
                    result['status']='waiting_resources';return result
                if elapsed+time.monotonic()-started>=580:
                    result['status']='evaluation_time_limit';return result
                qid=next(iter(row['questions']));q=tr.to_internal(qid,row['questions'][qid])
                ids,markers,stats,trunc=build_sequence(tok,policy.state_for(row['state']),q,1024,256,
                    return_stats=True,return_truncation_stats=True)
                if trunc['truncated'] or stats['options_distinct']!=2:raise ValueError('evaluation input truncation')
                batch=tr.collate_items([[dict(ids=ids,markers=markers,qtype=QTYPES[q['t']],
                    target=[0.0]*len(markers))]],tok.pad_token_id)
                with torch.inference_mode():logits=tr._forward(model,batch,torch.device('mps'),False,True)[0]
                keys=list(q['crit']);z=logits.detach().cpu();probabilities=z.softmax(-1).tolist()
                records.append(dict(case_id=row['case_id'],mode=row['mode'],model='binary_candidate',split='dev',
                    operation=row['operation'],family=row['family'],prediction=keys[int(z.argmax())],
                    logits=dict(zip(keys,z.tolist())),probabilities=dict(zip(keys,probabilities)),system_forced=False))
                del batch,logits,z
                if len(records)%12==0:save()
            result['status']='complete'
    except TimeoutError:result['status']='evaluation_time_limit'
    finally:
        signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,previous);save()
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job',type=Path,required=True);parser.add_argument('--control',type=Path,required=True)
    args=parser.parse_args()
    try:
        out=evaluate(args.job,args.control)
        print(json.dumps({k:out[k] for k in ('status','seconds','model_sha256')}))
    except Exception as exc:
        print(json.dumps(dict(status='stopped',error_type=type(exc).__name__)));raise SystemExit(1)
