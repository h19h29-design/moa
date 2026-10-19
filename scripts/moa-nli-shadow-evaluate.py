"""Pinned local-only NLI inference in a separate MacBook environment.

No weights are updated. Original label IDs/probabilities are always retained;
semantic mapping, if present, is a separate Mini-sealed dev-only artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import sys
import time

ROOT = Path.home()/'Library/Application Support/MOA-LAYA'
TRIAL = ROOT/'trials/nli-review-20261019'
for key, value in {'HF_HUB_OFFLINE':'1', 'TRANSFORMERS_OFFLINE':'1', 'HF_HUB_DISABLE_TELEMETRY':'1',
                   'TOKENIZERS_PARALLELISM':'false', 'WANDB_DISABLED':'true'}.items(): os.environ[key] = value


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def write(path, value):
    tmp=path.with_suffix('.pending')
    with tmp.open('wb') as out:
        os.chmod(tmp,0o600);out.write(canonical(value)+b'\n');out.flush();os.fsync(out.fileno())
    tmp.replace(path)


def run(path):
    if (path.is_symlink() or not path.resolve().is_relative_to(TRIAL/'requests') or
            path.stat().st_size>8_000_000): raise ValueError('private bounded request')
    request=json.loads(path.read_bytes())
    if (path.name!='REQUEST.json' or path.parent.name!=request['run_id'] or
            request['runner_sha256']!=sha(__file__) or request['mode']!='local_shadow_inference'):
        raise ValueError('pinned trial request')
    if sha(path.parent/'ADAPTER.json')!=request['adapter_manifest_sha256']:
        raise ValueError('adapter manifest')
    adapter=json.loads((path.parent/'ADAPTER.json').read_bytes())
    code=path.parent/'code'
    for name,expected in adapter.items():
        if name not in ('moa/__init__.py','moa/nli_verifier.py','moa/verification.py','moa/worker_contract.py','moa/clause_correspondence.py') or sha(code/name)!=expected:
            raise ValueError('allowlisted pinned implementation')
    sys.path.insert(0,str(code))
    from moa import nli_verifier as n
    sys.path.insert(0,str(ROOT/'app'))
    from workers.laya_local import runtime as rt
    rows=request['rows']
    if (not 1<=len(rows)<=2000 or len({r['case_id'] for r in rows})!=len(rows) or
            any(set(r)!={'case_id','state'} for r in rows) or
            hashlib.sha256(canonical(rows)).hexdigest()!=request['inputs_sha256']):
        raise ValueError('exact gold-free input inventory')
    if request['model_revision']!='a00e0933b1b9e00d5355195f7c7a8c968dd71040':raise ValueError('pinned revision')
    for name,expected in request['model_files'].items():
        if '/' in name or (TRIAL/'model'/name).is_symlink() or sha(TRIAL/'model'/name)!=expected:
            raise ValueError('pinned local model files')
    mapping=n.validate_mapping(request['semantic_mapping']) if request.get('semantic_mapping') else None
    protected=request['protected_models']
    if {p:sha(ROOT/p) for p in protected}!=protected:raise ValueError('preserved LAYA metadata')
    folder=path.parent/'output';folder.mkdir(mode=0o700,exist_ok=True)
    result_path=folder/'RESULTS.jsonl';state_path=folder/'STATE.json'
    with rt.exclusive(ROOT/'device.lock'):
        prior_seconds=0.
        if state_path.exists():
            prior=json.loads(state_path.read_bytes())
            if prior['request_sha256']!=sha(path):raise ValueError('immutable run identity')
            if prior['status']=='completed':return prior
            if prior['status']=='running':raise ValueError('unknown interruption; no automatic retry')
            prior_seconds=prior.get('seconds_total',prior.get('seconds_this_process',0.))
        res=rt.resources()
        if not (res['ac_power'] and res['idle_seconds'] is not None and res['idle_seconds']>=900 and
                res['memory_pressure_ok'] and res['available_memory_bytes']>=5*2**30 and res['disk_free_bytes']>=4*2**30):
            return {'status':'waiting_resources','run_id':request['run_id']}
        started=time.monotonic();max_seconds=request['limits']['max_seconds']
        if type(max_seconds) is not int or not 1<=max_seconds<=600:raise ValueError('bounded inference')
        if prior_seconds>=max_seconds:return {'status':'budget_exhausted','run_id':request['run_id']}
        state={'status':'running','request_sha256':sha(path),'run_id':request['run_id']}
        write(state_path,state);signal.alarm(max(1,int(max_seconds-prior_seconds)))
        import socket
        def blocked(*args,**kwargs):raise RuntimeError('all inference network access disabled')
        socket.socket.connect=blocked;socket.create_connection=blocked
        import torch
        import psutil
        from transformers import AutoTokenizer,AutoModelForSequenceClassification
        torch.set_num_threads(2)
        tok=AutoTokenizer.from_pretrained(TRIAL/'model',local_files_only=True,trust_remote_code=False)
        model=AutoModelForSequenceClassification.from_pretrained(TRIAL/'model',local_files_only=True,
                    trust_remote_code=False,use_safetensors=True,attn_implementation='eager')
        model.eval();model.requires_grad_(False)
        maximum=min(512,tok.model_max_length,model.config.max_position_embeddings)
        count=lambda a,b:len(tok(a,b,add_special_tokens=True,truncation=False)['input_ids'])
        previous=[json.loads(line) for line in result_path.read_bytes().splitlines()] if result_path.exists() else []
        seen={v['case_id'] for v in previous}
        if len(seen)!=len(previous) or seen-{r['case_id'] for r in rows}:raise ValueError('result identity')
        output=list(previous);inference_seconds=0.;pair_count=0;max_rss=psutil.Process().memory_info().rss
        for row in rows:
            if row['case_id'] in seen:continue
            current=rt.resources()
            if (prior_seconds+time.monotonic()-started>=max_seconds-10 or not current['memory_pressure_ok'] or
                    current['available_memory_bytes']<3*2**30):break
            plan=n.plan_pairs(row['state'],count,max_tokens=maximum);raw=[];row_start=time.monotonic()
            if plan['complete']:
                for pair in plan['pairs']:
                    encoded=tok(pair['premise'],pair['hypothesis'],return_tensors='pt',truncation=False)
                    if encoded['input_ids'].shape[-1]>maximum:raise ValueError('unexpected input overflow')
                    with torch.inference_mode():
                        logits=model(**encoded).logits[0]
                        probabilities=logits.softmax(-1).tolist();index=int(logits.argmax())
                    raw.append({'pair_id':pair['pair_id'],'index':index,'native_label':model.config.id2label[index],
                                'label':mapping[index] if mapping else None,'probabilities':probabilities,
                                'token_count':encoded['input_ids'].shape[-1],
                                'actual_pair_sha256':hashlib.sha256(canonical({'premise':pair['premise'],'hypothesis':pair['hypothesis']})).hexdigest()})
                    max_rss=max(max_rss,psutil.Process().memory_info().rss);pair_count+=1
            elapsed=time.monotonic()-row_start;inference_seconds+=elapsed
            summary=n.summarize(plan,raw) if mapping else {'status':'raw_ids_only','quality_label':None}
            record={'case_id':row['case_id'],'state_sha256':hashlib.sha256(canonical(row['state'])).hexdigest(),
                    'plan':plan,'raw':raw,'summary':summary,'seconds':elapsed,
                    'prediction_origin':'raw_nli_with_separate_trial_projection','system_forced_insufficient':False}
            with result_path.open('ab') as out:
                os.chmod(result_path,0o600);out.write(canonical(record)+b'\n');out.flush();os.fsync(out.fileno())
            output.append(record)
        signal.alarm(0)
        if {p:sha(ROOT/p) for p in protected}!=protected:raise ValueError('operating metadata changed')
        receipt={**state,'status':'completed' if len(output)==len(rows) else 'parked_resources_or_budget',
                 'items':len(rows),'processed':len(output),'pair_inferences_this_process':pair_count,
                 'seconds_this_process':time.monotonic()-started,'inference_seconds_this_process':inference_seconds,
                 'seconds_total':prior_seconds+time.monotonic()-started,
                 'peak_rss_bytes':max(max_rss,resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
                 'device':'cpu','threads':2,'batch_size':1,'max_tokens':maximum,'truncation':False,
                 'network_enabled':False,'model_revision':request['model_revision'],
                 'model_sha256':request['model_files']['model.safetensors'],
                 'training_started':False,'human_approved':False,'operating_model_changed':False}
        write(state_path,receipt);write(folder/'RECEIPT.json',receipt);return receipt


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--request',type=Path,required=True);a=p.parse_args()
    try:print(json.dumps(run(a.request)),flush=True)
    except Exception as exc:
        print(json.dumps({'status':'stopped','error_type':type(exc).__name__,'detail':'private_receipt_only'}),flush=True)
        raise SystemExit(1)
