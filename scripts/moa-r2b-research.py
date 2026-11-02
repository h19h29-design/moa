"""One authorized synthetic diagnostic/R2-B candidate; never operating training.

Installed in a versioned private incoming folder, not over the standing worker.
All inputs/implementations/parents are pinned by a secret-free immutable job.
"""
from __future__ import annotations

import argparse
import gc
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import signal
import sys
import threading
import time

for key,value in {'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1',
    'HF_HUB_DISABLE_TELEMETRY':'1','TOKENIZERS_PARALLELISM':'false',
    'WANDB_DISABLED':'true','PYTORCH_ENABLE_MPS_FALLBACK':'0','LAYA_MPS_AMP':'0'}.items():os.environ[key]=value

ROOT=Path.home()/'Library/Application Support/MOA-LAYA'
if (Path(__file__).parent/'r2b_research.py').exists():
    import r2b_research as b
    import r2_research as r
else:
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from moa import r2b_research as b,r2_research as r


def confined(root,name):
    p=root/name
    if Path(name).is_absolute() or '..' in Path(name).parts or p.is_symlink() or not p.resolve().is_relative_to(root.resolve()):
        raise ValueError('private research path boundary')
    return p


def context(path):
    if Path(path).is_symlink():raise ValueError('job symlink')
    path=Path(path).resolve();job=json.loads(path.read_bytes())
    rid=job['run_id']
    if path!=ROOT/'incoming'/rid/'JOB.json' or job['scope']!='authorized_synthetic_research_only':
        raise ValueError('new isolated synthetic job required')
    if set(job['code_sha256'])!={'moa-r2b-research.py','r2b_research.py','r2_research.py'}:
        raise ValueError('complete pinned code inventory required')
    for name,sha in job['code_sha256'].items():
        if r.file_hash(confined(Path(__file__).parent,name))!=sha:raise ValueError('pinned research code changed')
    if job['configuration']!={p:b.phase_config(p) for p in ('diagnostic','r2b')}:raise ValueError('fixed configuration changed')
    sys.path.insert(0,str(ROOT/'app'))
    from workers.laya_local import ml,policy,runtime as rt
    import laya.train as tr
    for module in (ml,policy,rt,tr):
        if r.file_hash(module.__file__)!=job['installed_sha256'][module.__name__]:raise ValueError('installed worker changed')
    verify_protected(job)
    bundle=confined(ROOT,job['bundle']);rows=[]
    for name,sha in job['bundle_sha256'].items():
        if r.file_hash(confined(bundle,name))!=sha:raise ValueError('corrected controls/parity changed')
    for split in ('train','dev'):
        values=[json.loads(x) for x in (bundle/(split+'.jsonl')).read_bytes().splitlines()]
        if any(x['meta']['split']!=split for x in values):raise ValueError('training split identity')
        rows.extend(values)
    r.research_eligibility(rows)
    if r.digest(rows)!=job['rows_sha256']:raise ValueError('corrected row manifest changed')
    if [x['case_id'] for x in b.select_diagnostic(rows)]!=job['diagnostic_case_ids']:raise ValueError('diagnostic selection changed')
    qdefs=policy.questions(list(policy.CRITERIA))
    for row in rows:
        if row['questions']!={k:qdefs[k] for k in row['expected']}:raise ValueError('shared rubric mismatch')
        op=row['meta']['operation'];gold=next(iter(row['expected'].values()))
        truth='insufficient' if not op['complete'] else 'preserved' if op['before']==op['after'] else 'changed'
        if gold!=truth:raise ValueError('independent operation gold mismatch')
    parent=confined(ROOT,job['parent']);ml.verify_model(parent,job['parent_files'])
    adapter_path=confined(ROOT,job['adapter'])
    if r.file_hash(adapter_path)!=job['adapter_sha256']:raise ValueError('actual input adapter changed')
    spec=importlib.util.spec_from_file_location('pinned_adapter',adapter_path)
    adapter=importlib.util.module_from_spec(spec);spec.loader.exec_module(adapter)
    journal=b.Journal(ROOT/'runs'/rid/'LEDGER.json',rid,{'job_sha256':r.file_hash(path)})
    return job,rows,parent,adapter,ml,policy,rt,tr,journal


def verify_protected(job):
    if {n:r.file_hash(ROOT/n) for n in job['protected_models']}!=job['protected_models']:
        raise ValueError('protected model registry/manifest changed')


def selected(raw,phase,adapter,policy):
    rows=b.select_diagnostic(raw) if phase=='diagnostic' else [x for x in raw if x['meta']['split']=='train']
    return adapter.prepare_training_rows(rows,policy.questions(list(policy.CRITERIA)))


def raw_answers(agent,rows,ml,rt,tr):
    """No direct gate, state-completeness branch, calibration fitting or gold prompts."""
    import torch
    values=[];agent.model.eval()
    for row in rows:
        if not rt.resources()['verify_allowed']:raise InterruptedError('verification_resource_guard')
        ml.preflight(agent.tok,row['state'],row['questions'])
        items,skipped=tr.items_from_rows(agent.tok,[row],1024,256)
        if skipped or len(items)!=1:raise ValueError('diagnostic actual input')
        encoded=tr.encode_item(agent.tok,items[0],1024,256,None,False)
        batch=tr.collate_items([[encoded]],agent.tok.pad_token_id)
        with torch.no_grad():logits=tr._forward(agent.model,batch,torch.device('mps'),False,True)[0].detach().cpu()
        output=agent.predict(row['state'],row['questions'],max_len=1024,head_max_len=256)
        if output['usage']['truncated']:raise ValueError('no truncated diagnostic answer')
        key=next(iter(row['questions']));answer=output['answers'][key]
        values.append({'case_id':row['case_id'],'expected':row['expected'][key],
            'prediction':answer['choice'],'prediction_origin':'raw_laya',
            'probabilities':answer['probabilities'],'logits':logits.tolist(),
            'logit_softmax':torch.softmax(logits,-1).tolist(),
            'gold_index':int(batch['target'][0].argmax()),
            'canonical_order':list(b.LABELS),'actual_tokens':len(encoded['ids']),
            'ordered_input_sha256':r.digest({'state':row['state'],'questions':row['questions']}),
            'system_forced_insufficient':False})
    return values


def baseline(path):
    import torch
    job,raw,parent,adapter,ml,policy,rt,tr,journal=context(path)
    dest=ROOT/'runs'/job['run_id']/'DIAGNOSTIC_BASELINE.json'
    if dest.exists():return {'status':'already_complete','receipt_sha256':r.file_hash(dest)}
    if journal.read()['phases']:raise ValueError('baseline must precede optimizer reservation')
    with rt.exclusive(ROOT/'device.lock'):
        if not rt.resources()['verify_allowed']:return {'status':'waiting_resources'}
        started=time.monotonic();agent=ml.load_agent(parent,job['parent_files'])
        result={'status':'completed','rows':raw_answers(agent,selected(raw,'diagnostic',adapter,policy),ml,rt,tr),
            'model_sha256':job['parent_files']['model.safetensors'],'seconds':time.monotonic()-started,
            'raw_model_only':True,'training_started':False}
        r.atomic_json(dest,result);verify_protected(job)
    return {'status':'completed','rows':len(result['rows']),'seconds':result['seconds']}


def train(path,phase,resume=False):
    import torch
    job,raw,parent,adapter,ml,policy,rt,tr,journal=context(path);cfg=b.phase_config(phase)
    folder=ROOT/'candidates'/(job['run_id']+'-'+phase)
    baseline_file=ROOT/'runs'/job['run_id']/'DIAGNOSTIC_BASELINE.json'
    if not baseline_file.exists():raise ValueError('raw diagnostic baseline required')
    base=json.loads(baseline_file.read_bytes())
    with rt.exclusive(ROOT/'device.lock'):
        resources=rt.resources()
        if not resources['train_allowed'] or resources['available_memory_bytes']<5*1024**3:
            return {'status':'waiting_resources','updates':0,'resources':resources}
        fencing=journal.claim(phase,resume)
        if fencing is None:return {'status':'already_saved','phase':phase}
        previous=journal.read()['phases'][phase];steps=previous['updates']
        prior=previous['seconds'] or (base['seconds'] if phase=='diagnostic' else 0.)
        started=time.monotonic();folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        stop=threading.Event();old_handlers={s:signal.signal(s,lambda *_:stop.set()) for s in (signal.SIGINT,signal.SIGTERM)}
        def elapsed():return prior+time.monotonic()-started
        try:
            model,tok,mcfg=tr.load_checkpoint(str(parent));model.encoder.config.reference_compile=False
            model.to('mps');model.encoder.requires_grad_(False)
            gc.collect();torch.mps.synchronize();torch.mps.empty_cache()
            named={n:p for n,p in model.named_parameters() if p.requires_grad}
            if any(n.startswith('encoder.') for n in named) or sum(p.numel() for p in named.values())!=14969347:
                raise ValueError('same R2-A frozen encoder/head inventory required')
            rows=selected(raw,phase,adapter,policy)
            for row in rows:ml.preflight(tok,row['state'],row['questions'])
            items,skipped=tr.items_from_rows(tok,rows,1024,256)
            if skipped or len(items)!=cfg['questions']:raise ValueError('fixed training inventory')
            config=tr.TrainConfig(**{k:v for k,v in cfg.items() if k in tr.TrainConfig.__dataclass_fields__},calib_frac=0,calib_max=0)
            config.validate()
            optimizer=torch.optim.AdamW([{'params':list(named.values()),'lr':config.head_lr}],weight_decay=config.weight_decay)
            scheduler=torch.optim.lr_scheduler.CosineAnnealingLR(optimizer,T_max=cfg['planned_updates'],eta_min=config.min_lr)
            torch.manual_seed(config.seed);order_rng=random.Random(config.seed)
            cursor={'epoch':0,'next_microbatch':0};data_sha=r.digest(rows)
            before=r.parameter_hash_cpu_first(model);enc_before=r.parameter_hash_cpu_first(model,encoder=True)
            history=[]
            if resume:
                source=confined(folder,previous['checkpoint']);r.verify_snapshot(source,previous['files'])
                state=torch.load(source/'training-state.pt',map_location='cpu',weights_only=True)
                if (state['run_id']!=job['run_id'] or state['phase']!=phase or state['step']!=steps or
                    state['configuration']!=cfg or state['data_sha256']!=data_sha):raise ValueError('optimizer resume binding')
                b.validate_cursor(phase,steps,state['cursor'])
                model.load_state_dict(state['head'],strict=False);optimizer.load_state_dict(state['optimizer']);scheduler.load_state_dict(state['scheduler'])
                torch.set_rng_state(state['torch_rng']);torch.mps.set_rng_state(state['mps_rng']);order_rng.setstate(state['option_rng'])
                cursor=state['cursor'];before=state['initial_head_sha256'];history=state['history'];del state
            boundary=(torch.get_rng_state(),torch.mps.get_rng_state(),order_rng.getstate())
            model.train();model.encoder.eval();model.head_checkpointing=False;optimizer.zero_grad(set_to_none=True)
            r.atomic_json(folder/'DATA_MANIFEST.json',{'data_sha256':data_sha,'configuration':cfg,
                'case_ids':[x['case_id'] for x in rows],'trainable_parameters':sum(p.numel() for p in named.values()),
                'encoder_trainable_parameters':0,'optimizer_parameters':sum(p.numel() for g in optimizer.param_groups for p in g['params']),
                'parent_sha256':job['parent_files']['model.safetensors'],'operational_ready':False})

            def save(tag):
                name=f'{tag}-{steps:04d}-{fencing:02d}'
                def writer(out):
                    r.save_checkpoint_cpu_first(model,tok,dict(mcfg,max_len=1024,head_max_len=256,
                        temperature=[1,1,1],temperature_by_options={}),out)
                    state={'head':{n:v.detach().cpu().clone() for n,v in model.state_dict().items() if not n.startswith('encoder.')},
                        'optimizer':optimizer.state_dict(),'scheduler':scheduler.state_dict(),'step':steps,
                        'cursor':dict(cursor),'torch_rng':torch.get_rng_state(),'mps_rng':torch.mps.get_rng_state(),
                        'option_rng':order_rng.getstate(),'configuration':cfg,'data_sha256':data_sha,
                        'run_id':job['run_id'],'phase':phase,'initial_head_sha256':before,'history':history}
                    torch.save(state,out/'training-state.pt')
                    r.atomic_json(out/'RESEARCH_METADATA.json',{'head_fp32_sha256':r.parameter_hash_cpu_first(model),
                        'exported_head_sha256':r.parameter_hash_cpu_first(model,export_roundtrip=True),
                        'encoder_sha256':enc_before,'step':steps,'cursor':cursor,'data_sha256':data_sha})
                    return ml.checkpoint_hashes(out)
                files=r.commit_snapshot(folder/name,writer)
                gc.collect();torch.mps.synchronize();torch.mps.empty_cache()
                return name,files

            def gate():
                if stop.is_set() or (folder/'PAUSED').exists():raise InterruptedError('pause_requested')
                # Reserve cleanup/reload room rather than hiding wall time by clamping it.
                if elapsed()>=cfg['maximum_seconds']-(60 if phase=='diagnostic' else 10):raise InterruptedError('time_limit')
                if not rt.resources()['train_allowed']:raise InterruptedError('resource_guard')

            save('initial' if not resume else 'resumed');reason=None
            try:
                for epoch in range(cursor['epoch'],cfg['epochs']):
                    epoch_items=list(items);random.Random(config.seed+epoch).shuffle(epoch_items)
                    offset0=cursor['next_microbatch'] if epoch==cursor['epoch'] else 0
                    for offset in range(offset0,len(items),config.grad_accum):
                        gate();window=epoch_items[offset:offset+config.grad_accum];losses=[]
                        for item in window:
                            torch.mps.synchronize();torch.mps.empty_cache();gate()
                            order=tr.draw_option_order(item,order_rng,config.shuffle_options)
                            encoded=tr.encode_item(tok,item,1024,256,order,False)
                            batch=tr.collate_items([[encoded]],tok.pad_token_id)
                            logits=tr._forward(model,batch,torch.device('mps'),False,True)
                            loss=tr.soft_ce_loss(logits,batch['target'].to('mps'),batch['marker_mask'].to('mps'))
                            if not torch.isfinite(loss).item():raise ValueError('nonfinite loss')
                            (loss/len(window)).backward();losses.append(float(loss.detach().cpu()))
                        norm=float(torch.nn.utils.clip_grad_norm_(list(named.values()),config.grad_clip).cpu())
                        if not math.isfinite(norm) or norm<=0:raise ValueError('invalid gradient')
                        if any(p.grad is not None for p in model.encoder.parameters()):raise ValueError('frozen encoder gradient')
                        gate();lr=optimizer.param_groups[0]['lr'];optimizer.step();scheduler.step();optimizer.zero_grad(set_to_none=True)
                        torch.mps.synchronize();steps+=1
                        cursor={'epoch':epoch,'next_microbatch':offset+len(window)}
                        if cursor['next_microbatch']==len(items):cursor={'epoch':epoch+1,'next_microbatch':0}
                        b.validate_cursor(phase,steps,cursor)
                        boundary=(torch.get_rng_state(),torch.mps.get_rng_state(),order_rng.getstate())
                        history.append({'step':steps,'mean_loss':sum(losses)/len(losses),'gradient_norm':norm,'head_lr':lr})
                        journal.record(phase,fencing,steps,elapsed())
                        r.atomic_json(folder/'PROGRESS.json',{'status':'training','updates':steps,'seconds':elapsed(),'cursor':cursor,'last':history[-1]})
                        if steps==cfg['planned_updates']//2:save('interim')
                        reason=b.stop_reason(phase,updates=steps,seconds=elapsed(),epoch=cursor['epoch'])
                        if reason:break
                    if reason:break
            except InterruptedError as exc:
                reason=str(exc);optimizer.zero_grad(set_to_none=True)
                torch.set_rng_state(boundary[0]);torch.mps.set_rng_state(boundary[1]);order_rng.setstate(boundary[2])
            name,files=save('checkpoint');after=r.parameter_hash_cpu_first(model)
            complete=reason in ('epoch_limit','update_limit','time_limit') and steps>0 and before!=after
            result={'status':'saved' if complete else 'parked','phase':phase,'run_id':job['run_id'],
                'checkpoint':str(folder/name),'checkpoint_name':name,'sha256':files,'updates':steps,'seconds':elapsed(),
                'configuration':cfg,'cursor':cursor,'history':history,'weights_changed':before!=after,
                'head_before_sha256':before,'head_after_sha256':after,
                'encoder_unchanged':enc_before==r.parameter_hash_cpu_first(model,encoder=True),
                'finite_nonzero_gradients':bool(history) and all(math.isfinite(h['gradient_norm']) and h['gradient_norm']>0 for h in history),
                'stop_reason':reason,'human_approved':False,'operational_promotion':False}
            r.atomic_json(folder/'TRAIN_RESULT.json',result)
            journal.record(phase,fencing,steps,result['seconds'],result['status'],name,files)
            verify_protected(job)
            return {k:v for k,v in result.items() if k not in ('sha256','history')}
        except Exception as exc:
            # Unknown failure is terminal, not permission to reset an optimizer.
            r.atomic_json(folder/'STOPPED.json',{'reason':type(exc).__name__,'updates':steps,'seconds':elapsed(),'automatic_retry':False})
            raise
        finally:
            for sig,handler in old_handlers.items():signal.signal(sig,handler)


def reload_checkpoint(path,phase):
    import torch
    job,raw,parent,adapter,ml,policy,rt,tr,journal=context(path)
    folder=ROOT/'candidates'/(job['run_id']+'-'+phase)
    saved=json.loads((folder/'TRAIN_RESULT.json').read_bytes());cp=Path(saved['checkpoint']);cfg=b.phase_config(phase)
    receipts=b.ReloadReceipts(folder);dest=receipts.path(saved);existing=receipts.read(saved)
    if existing is not None:
        r.verify_snapshot(cp,saved['sha256'])
        if phase=='diagnostic' and saved['status']=='saved':journal.bind_gate(existing['gate'],r.file_hash(dest))
        return {'status':'already_complete','phase':phase,'gate_passed':existing.get('gate',{}).get('passed')}
    with rt.exclusive(ROOT/'device.lock'):
        if not rt.resources()['verify_allowed']:return {'status':'waiting_resources'}
        started=time.monotonic();record=journal.read()['phases'][phase]
        if record.get('checkpoint')!=saved['checkpoint_name']:raise ValueError('saved ledger identity')
        r.verify_snapshot(cp,record['files']);agent=ml.load_agent(cp,saved['sha256'])
        meta=json.loads((cp/'RESEARCH_METADATA.json').read_bytes())
        if r.parameter_hash_cpu_first(agent.model)!=meta['exported_head_sha256']:raise ValueError('export head reload')
        export_head={n:p.detach().cpu().clone() for n,p in agent.model.state_dict().items() if not n.startswith('encoder.')}
        state=torch.load(cp/'training-state.pt',map_location='cpu',weights_only=True)
        rows=selected(raw,phase,adapter,policy)
        if (state['step']!=saved['updates'] or state['run_id']!=job['run_id'] or state['phase']!=phase or
            state['data_sha256']!=r.digest(rows) or state['configuration']!=cfg):raise ValueError('reload optimizer identity')
        b.validate_cursor(phase,state['step'],state['cursor'])
        agent.model.load_state_dict(state['head'],strict=False)
        if r.parameter_hash_cpu_first(agent.model)!=meta['head_fp32_sha256']:raise ValueError('FP32 head reload')
        agent.model.encoder.requires_grad_(False)
        params=[p for p in agent.model.parameters() if p.requires_grad]
        optimizer=torch.optim.AdamW([{'params':params,'lr':cfg['head_lr']}],weight_decay=cfg['weight_decay'])
        scheduler=torch.optim.lr_scheduler.CosineAnnealingLR(optimizer,T_max=cfg['planned_updates'],eta_min=cfg['min_lr'])
        optimizer.load_state_dict(state['optimizer']);scheduler.load_state_dict(state['scheduler'])
        if not optimizer.state or any(int(v['step'])!=state['step'] for v in optimizer.state.values()) or scheduler.last_epoch!=state['step']:
            raise ValueError('optimizer/scheduler steps not restored')
        optimizer_values_finite=all(torch.isfinite(v[k]).all().item() for v in optimizer.state.values() for k in ('exp_avg','exp_avg_sq'))
        if not optimizer_values_finite:raise ValueError('optimizer values not finite')
        agent.model.load_state_dict(export_head,strict=False)
        if r.parameter_hash_cpu_first(agent.model)!=meta['exported_head_sha256']:raise ValueError('export inference restoration')
        del state,optimizer,scheduler,export_head;gc.collect();torch.mps.synchronize();torch.mps.empty_cache()
        # Only the tiny TRAIN subset is used for this learning gate. No dev/test fitting.
        answers=raw_answers(agent,rows if phase=='diagnostic' else rows[:1],ml,rt,tr)
        evidence={k:saved[k] for k in ('updates','seconds','weights_changed','encoder_unchanged','finite_nonzero_gradients')}
        evidence.update(seconds=saved['seconds']+time.monotonic()-started,reload_verified=True)
        receipt={'status':'reloaded','phase':phase,'model_sha256':saved['sha256']['model.safetensors'],
            'export_weights_verified':True,'fp32_head_verified':True,'optimizer_scheduler_verified':True,
            'raw_answers':answers,'evidence':evidence,'independent_evaluation_started':False}
        if phase=='diagnostic':
            baseline_result=json.loads((ROOT/'runs'/job['run_id']/'DIAGNOSTIC_BASELINE.json').read_bytes())
            receipt['gate']=b.diagnostic_gate(baseline_result['rows'],answers,evidence)
        receipts.write(saved,receipt)
        if phase=='diagnostic' and saved['status']=='saved':journal.bind_gate(receipt['gate'],r.file_hash(dest))
        verify_protected(job)
        return {'status':'reloaded','phase':phase,'model_sha256':receipt['model_sha256'],
                'gate_passed':receipt.get('gate',{}).get('passed'),'evidence':evidence}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['inspect','baseline','train','reload'])
    parser.add_argument('--job',type=Path,required=True)
    parser.add_argument('--phase',choices=['diagnostic','r2b']);parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    if args.action=='inspect':
        c=context(args.job);result={'status':'checked','resources':c[6].resources(),'ledger':c[-1].read()}
    elif args.action=='baseline':result=baseline(args.job)
    else:
        if not args.phase:parser.error('--phase required')
        result=train(args.job,args.phase,args.resume) if args.action=='train' else reload_checkpoint(args.job,args.phase)
    print(json.dumps(result),flush=True)


if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'status':'stopped','error_type':type(exc).__name__,'details':'private_receipt_only'}),flush=True)
        raise SystemExit(1)
