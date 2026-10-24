"""Gold-free, resumable local research inference, never train or approve.

Only a sealed request under the private MOA-LAYA evaluations directory is read.
Model files, installed implementation, settings and inputs are content-pinned.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import time

for k,v in {'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','HF_HUB_DISABLE_TELEMETRY':'1',
            'TOKENIZERS_PARALLELISM':'false','WANDB_DISABLED':'true','LAYA_MPS_AMP':'0'}.items():os.environ[k]=v
ROOT=Path.home()/'Library/Application Support/MOA-LAYA'


def canonical(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def write(p,v):
    temp=p.with_suffix('.pending')
    with temp.open('wb') as f:
        os.chmod(temp,0o600);f.write(canonical(v)+b'\n');f.flush();os.fsync(f.fileno())
    temp.replace(p)


def run(path,model_name):
    if path.is_symlink() or not path.resolve().is_relative_to(ROOT/'evaluations') or path.stat().st_size>2000000:
        raise ValueError('private bounded request only')
    request=json.loads(path.read_bytes());rid=request['run_id']
    if path.resolve()!=ROOT/'evaluations'/rid/'REQUEST.json' or not rid.startswith('moa-r2b-'):
        raise ValueError('isolated evaluation identity')
    if request['runner_sha256']!=sha(__file__) or request['mode']!='synthetic_research_shadow_only':raise ValueError('pinned inference code')
    freeze=request['freeze'];rows=request['rows']
    if (request['inputs_sha256']!=hashlib.sha256(canonical(rows)).hexdigest() or not 1<=len(rows)<=2000 or
        len({r['case_id'] for r in rows})!=len(rows) or
        any(set(r)!={'case_id','state','questions'} for r in rows)):raise ValueError('gold-free exact input inventory')
    sys.path.insert(0,str(ROOT/'app'))
    from workers.laya_local import ml,policy,runtime as rt
    for m in (ml,policy,rt):
        if sha(m.__file__)!=request['installed_sha256'][m.__name__]:raise ValueError('installed implementation changed')
    for row in rows:
        if row['questions']!=policy.questions(list(row['questions'])):raise ValueError('actual shared rubric')
        if set(row['state'])!={'title','file_role','source_complete','source_blocks','rendered_blocks','attachment_manifest'}:
            raise ValueError('evidence-only state')
    parent=request['models'][model_name];checkpoint=ROOT/parent['checkpoint']
    if not checkpoint.resolve().is_relative_to(ROOT/'candidates') or checkpoint.is_symlink():raise ValueError('candidate boundary')
    if parent['files']['model.safetensors']!=freeze['candidate_sha256'][model_name]:raise ValueError('candidate freeze')
    folder=path.parent/model_name;folder.mkdir(mode=0o700,exist_ok=True)
    result_file=folder/'RESULTS.jsonl';budget_file=folder/'BUDGET.json';reservation=folder/'REQUEST_SHA256.json'
    before={n:sha(ROOT/n) for n in request['protected_models']}
    if before!=request['protected_models']:raise ValueError('protected registry changed')
    with rt.exclusive(ROOT/'device.lock'):
        if reservation.exists() and json.loads(reservation.read_bytes())!=sha(path):raise ValueError('immutable request reservation')
        write(reservation,sha(path))
        results=[json.loads(x) for x in result_file.read_bytes().splitlines()] if result_file.exists() else []
        seen={x['case_id'] for x in results}
        if len(seen)!=len(results) or seen-{x['case_id'] for x in rows}:raise ValueError('result identity')
        if len(seen)==len(rows):return {'status':'already_complete','model':model_name,'items':len(rows)}
        budget=json.loads(budget_file.read_bytes()) if budget_file.exists() else {'seconds':0.,'status':'not_started'}
        if budget['status']=='running':raise ValueError('unexplained interruption; reconciliation required')
        if budget['seconds']>=600:return {'status':'budget_exhausted','model':model_name,'items':len(seen)}
        if not rt.resources()['verify_allowed']:return {'status':'waiting_resources','model':model_name}
        started=time.monotonic();prior=budget['seconds'];write(budget_file,{'seconds':prior,'status':'running'})
        signal.alarm(max(1,int(600-prior)))
        agent=ml.load_agent(checkpoint,parent['files'])
        for row in rows:
            if row['case_id'] in seen:continue
            if prior+time.monotonic()-started>=590 or not rt.resources()['verify_allowed']:break
            state=policy.state_for(row['state']);qs=policy.questions(list(row['questions']))
            record={'case_id':row['case_id'],'prediction_origin':'raw_laya','system_forced_insufficient':False,
                'model_sha256':parent['files']['model.safetensors'],
                'ordered_input_sha256':hashlib.sha256(canonical({'state':state,'questions':qs})).hexdigest()}
            try:
                ml.preflight(agent.tok,state,qs)
                answer=agent.predict(state,qs,max_len=1024,head_max_len=256)
                if answer['usage']['truncated']:raise ValueError('unexpected truncation')
                value=answer['answers'][next(iter(qs))]
                record.update(status='completed',prediction=value['choice'],probabilities=value['probabilities'])
            except ValueError as exc:
                if not str(exc).startswith(('token_budget_exceeded','question_head_would_truncate')):raise
                record.update(status='input_overflow',prediction=None)
            with result_file.open('ab') as out:
                os.chmod(result_file,0o600);out.write(canonical(record)+b'\n');out.flush();os.fsync(out.fileno())
            results.append(record);seen.add(row['case_id'])
        signal.alarm(0)
        budget={'seconds':prior+time.monotonic()-started,'status':'completed' if len(seen)==len(rows) else 'parked'}
        write(budget_file,budget)
        if before!={n:sha(ROOT/n) for n in before}:raise ValueError('model metadata modified')
        result={'status':budget['status'],'model':model_name,'items':len(rows),'processed':len(seen),
                'completed':sum(r['status']=='completed' for r in results),
                'input_overflow':sum(r['status']=='input_overflow' for r in results),'seconds':budget['seconds'],
                'training_started':False,'human_approved':False,'operating_model_changed':False}
        write(folder/'RECEIPT.json',result);return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--request',type=Path,required=True)
    p.add_argument('--model',required=True,choices=['r1','r2a','r2b']);a=p.parse_args()
    try:print(json.dumps(run(a.request,a.model)),flush=True)
    except Exception as exc:
        print(json.dumps({'status':'stopped','error_type':type(exc).__name__,'details':'private_receipt_only'}),flush=True)
        raise SystemExit(1)
