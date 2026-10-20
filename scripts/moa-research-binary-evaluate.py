"""Separate-process reload of one binary candidate, using gold-free exposed dev.

Both actual choice orders are evaluated. The 600s cumulative allowance belongs
only to this candidate and is never borrowed from/reset on a previous run.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from moa import research_alignment as a, research_campaign as rc, research_binary_control as control


def evaluate(root):
    root=Path(root)
    if root!=a.DEFAULT:raise ValueError('only the existing isolated worker is allowed')
    folder=root/control.FOLDER;w=root/'mini-worker'
    plan=rc._read_json(folder/'PLAN.json');state=rc._read_json(folder/'STATE.json')
    control.verify(root,plan,state)
    reservation=rc._read_json(folder/'EVAL_RESERVATION.json')
    if (not state.get('evaluation_issued') or reservation['candidate']!=control.RUN or
            reservation['maximum_seconds']!=plan['evaluation_seconds']):
        raise ValueError('candidate evaluation dispatch and reservation required')
    trained=rc._read_json(w/'research'/control.RUN/'TRAIN_RESULT.json')
    control._training_result(root,plan,trained)
    model_sha=trained['sha256']['model.safetensors'];receipt=folder/'EVALUATION.json'
    old=rc._read_json(receipt) if receipt.exists() else {}
    if old and (old['request_sha256']!=plan['input_sha256']['BINARY_REQUEST.json'] or
                old['model_sha256']!=model_sha):raise ValueError('saved evaluation identity changed')
    if old.get('status')=='complete':return old
    records=list(old.get('records',[]));elapsed=float(old.get('seconds',0))
    if not 0<=elapsed<=plan['evaluation_seconds']:raise ValueError('invalid saved cumulative evaluation time')
    pending=control.evaluation_inputs(rc._read_json(folder/'BINARY_REQUEST.json'),records)
    result=dict(status='failed',records=records,seconds=elapsed,model_sha256=model_sha,
        request_sha256=plan['input_sha256']['BINARY_REQUEST.json'],separate_process=True,
        execution_device='cpu',trained=False,system_forced=False,operational_promotion=False)
    started=time.monotonic()
    def timeout(*_):raise TimeoutError('candidate evaluation budget exhausted')
    def save():
        result['seconds']=elapsed+time.monotonic()-started;rc._write_atomic(receipt,result)
    if plan['evaluation_seconds']-elapsed<=20:
        result['status']='evaluation_time_limit';save();return result
    previous=signal.signal(signal.SIGALRM,timeout)
    signal.setitimer(signal.ITIMER_REAL,plan['evaluation_seconds']-elapsed-10)
    try:
        os.environ.update(MOA_LAYA_HOME=str(w),MOA_RESEARCH_HOME=str(w),HF_HUB_OFFLINE='1',
            TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',TOKENIZERS_PARALLELISM='false',WANDB_DISABLED='true')
        import torch
        import laya.train as tr
        from laya.common import build_sequence,QTYPES
        incoming=w/'incoming'/control.RUN
        spec=importlib.util.spec_from_file_location('binary_evaluation_worker',incoming/'moa-research-worker.py')
        worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
        _,_,_,ml,policy,runtime,_=worker.context(incoming/'JOB.json')
        torch.set_num_threads(2);os.nice(10)
        with runtime.exclusive(w/'device.lock'):
            if not runtime.resources()['verify_allowed']:
                result['status']='waiting_resources';return result
            ml.verify_model(trained['checkpoint'],trained['sha256'])
            model,tok,_=tr.load_checkpoint(trained['checkpoint'])
            model.encoder.config.reference_compile=False
            model.to('cpu');model.eval();model.requires_grad_(False)
            for row in pending:
                if (root/'PAUSED').exists() or not runtime.resources()['verify_allowed']:
                    result['status']='waiting_resources';return result
                source=policy.state_for(row['state']);qid=next(iter(row['questions']))
                q=tr.to_internal(qid,row['questions'][qid])
                ids,markers,stats,trunc=build_sequence(tok,source,q,1024,256,
                    return_stats=True,return_truncation_stats=True)
                if trunc['truncated'] or stats['options_distinct']!=2:raise ValueError('do not truncate evaluation input')
                batch=tr.collate_items([[dict(ids=ids,markers=markers,qtype=QTYPES[q['t']],
                    target=[0.0]*len(markers))]],tok.pad_token_id)
                with torch.inference_mode():z=tr._forward(model,batch,torch.device('cpu'),False,True)[0]
                keys=list(q['crit']);probs=z.softmax(-1).tolist()
                records.append(dict(case_id=row['case_id'],mode=row['mode'],model='binary_candidate',
                    split='dev',operation=row['operation'],family=row['family'],prediction=keys[int(z.argmax())],
                    probabilities=dict(zip(keys,probs)),logits=dict(zip(keys,z.tolist())),tokens=len(ids),system_forced=False))
                if len(records)%12==0:save()
            result['status']='complete'
    except TimeoutError:result['status']='evaluation_time_limit'
    finally:
        signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,previous);save()
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--run-id',choices=[control.RUN],required=True);args=parser.parse_args()
    try:
        result=evaluate(args.root)
        print(json.dumps({k:result[k] for k in ('status','seconds','model_sha256','separate_process')}))
    except Exception as exc:
        print(json.dumps({'status':'stopped','error_type':type(exc).__name__}));raise SystemExit(1)
