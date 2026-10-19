"""Fixed MacBook-only synthetic S1/R2-A jobs over the existing strict SSH path.

Copy this script and moa/r2_research.py to a private incoming code directory.
The coordinator pins their hashes in RESEARCH_JOB.json. Never install over the
standing worker, change its registry, or reuse its operational training gate.
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
import re
import signal
import sys
import threading
import time

for name, value in {'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1',
                    'HF_HUB_DISABLE_TELEMETRY':'1','TOKENIZERS_PARALLELISM':'false',
                    'WANDB_DISABLED':'true','PYTORCH_ENABLE_MPS_FALLBACK':'0',
                    'LAYA_MPS_AMP':'0'}.items(): os.environ[name] = value

ROOT = Path.home()/'Library/Application Support/MOA-LAYA'


def helper(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def library():
    local = Path(__file__).with_name('r2_research.py')
    if local.exists(): return helper('r2_research', local)
    return helper('r2_research', Path(__file__).resolve().parents[1]/'moa/r2_research.py')


r = library()


def confined(root, name):
    p = root/name
    if Path(name).is_absolute() or '..' in Path(name).parts or p.is_symlink() or not p.resolve().is_relative_to(root.resolve()):
        raise ValueError('private path boundary')
    return p


def context(path):
    path = Path(path).resolve()
    if not path.is_relative_to(ROOT/'incoming'): raise ValueError('incoming job required')
    job = json.loads(path.read_bytes())
    if job.get('scope') != 'synthetic_research' or not re.fullmatch('[a-zA-Z0-9_-]{1,80}', job['run_id']):
        raise ValueError('fixed synthetic research job required')
    if r.file_hash(Path(__file__)) != job['runner_sha256'] or r.file_hash(Path(r.__file__)) != job['library_sha256']:
        raise ValueError('research implementation changed')
    sys.path.insert(0,str(ROOT/'app'))
    from workers.laya_local import ml, policy, runtime
    import laya.train as tr
    for mod in (ml, policy, runtime, tr):
        if r.file_hash(Path(mod.__file__)) != job['installed_sha256'][mod.__name__]:
            raise ValueError('installed implementation changed')
    bundle = confined(ROOT, job['bundle'])
    handoff_file = bundle/'R2_HANDOFF.json'
    if r.file_hash(handoff_file) != job['handoff_sha256']: raise ValueError('handoff changed')
    handoff = json.loads(handoff_file.read_bytes())
    for name, sha in handoff['files_sha256'].items():
        if r.file_hash(confined(bundle,name)) != sha: raise ValueError('handoff file changed')
    ready = json.loads((bundle/'R2_READY.json').read_bytes())
    for name, sha in job['parity_receipts'].items():
        if r.file_hash(confined(bundle,name)) != sha: raise ValueError('actual token parity receipt changed')
    token_audit=json.loads((bundle/'TOKENIZER_PARITY.json').read_bytes())
    permutations=json.loads((bundle/'PERMUTATION_PARITY.json').read_bytes())
    if (token_audit.get('status')!='ready' or token_audit.get('checked_sequences')!=640 or
        token_audit.get('overflows')!=[] or not token_audit.get('no_truncation') or
        token_audit.get('max_actual_tokens',1025)>1024 or
        permutations.get('status')!='passed' or permutations.get('actual_batch_checks')!=3840 or
        permutations.get('permutations_per_input')!=6 or not permutations.get('checkpoint_reverified_before_and_after')):
        raise ValueError('actual input audit not passed')
    if policy.questions(list(policy.CRITERIA)) != ready['questions']: raise ValueError('question implementation mismatch')
    if r.digest(ready['questions']) != handoff['rubric_sha256']: raise ValueError('rubric mismatch')
    if ready['choice_order'] != list(r.LABELS) or ready['gold_index'] != {k:i for i,k in enumerate(r.LABELS)}:
        raise ValueError('label mapping mismatch')
    raw = []
    for shard in ready['shards']:
        if shard['transport_to_book']:
            if shard['split'] not in ('train','dev'): raise ValueError('evaluation data in training bundle')
            raw.extend(json.loads(line) for line in (bundle/shard['file']).read_bytes().splitlines())
    eligibility = r.research_eligibility(raw)
    parent = confined(ROOT, job['parent'])
    ml.verify_model(parent, handoff['checkpoint_files'])
    protected = {name:r.file_hash(ROOT/name) for name in ('MODEL_REGISTRY.json','MODEL_MANIFEST.json')}
    if protected != job['protected_models']: raise ValueError('operational model metadata changed')
    adapter_file = confined(ROOT,handoff['installed_adapter'])
    if r.file_hash(adapter_file) != handoff['installed_adapter_sha256']: raise ValueError('actual input adapter changed')
    adapter = helper('r2_adapter',adapter_file)
    identity = {'handoff_sha256':job['handoff_sha256'], 'job_sha256':r.file_hash(path)}
    journal = r.Journal(ROOT/'runs'/job['run_id']/'LEDGER.json',job['run_id'],identity)
    return job, handoff, raw, eligibility, parent, protected, adapter, ml, policy, runtime, tr, journal


def check_protected(job, handoff, parent, ml):
    ml.verify_model(parent,handoff['checkpoint_files'])
    if {n:r.file_hash(ROOT/n) for n in job['protected_models']} != job['protected_models']:
        raise ValueError('protected model changed')


def train(path, phase, resume=False):
    import torch
    job, handoff, raw, eligibility, parent, protected, adapter, ml, policy, rt, tr, journal = context(path)
    cfg = r.phase_config(phase)
    # All settings come from this pinned implementation, never shell expressions.
    rows = r.select_s1(raw) if phase == 's1' else [x for x in raw if x['meta']['split']=='train']
    rows = adapter.prepare_training_rows(rows, policy.questions(list(policy.CRITERIA)))
    folder = ROOT/'candidates'/(job['run_id']+'-'+phase)
    result_file = folder/'TRAIN_RESULT.json'
    with rt.exclusive(ROOT/'device.lock'):
        if not rt.resources()['train_allowed']:
            return {'status':'parked_resources','resources':rt.resources(),'optimizer_updates':0}
        fencing = journal.claim(phase,resume=resume)
        if fencing is None:
            return {'status':'already_saved','result':str(result_file),'optimizer_updates':journal.read()['phases'][phase]['updates']}
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        previous = journal.read()['phases'][phase]
        steps, prior_seconds = previous['updates'], previous['seconds']
        started = time.monotonic()
        model, tok, model_cfg = tr.load_checkpoint(str(parent))
        model.encoder.config.reference_compile = False
        model.to('mps'); model.encoder.requires_grad_(False)
        if not torch.backends.mps.is_available(): raise RuntimeError('MPS required')
        named = {n:p for n,p in model.named_parameters() if p.requires_grad}
        trainable = sum(p.numel() for p in named.values())
        if any(n.startswith('encoder.') for n in named) or trainable != 14969347:
            raise ValueError('encoder freeze or head inventory mismatch')
        config = tr.TrainConfig(**{k:v for k,v in cfg.items() if k in tr.TrainConfig.__dataclass_fields__},
                                calib_frac=0,calib_max=0,log_every=10)
        config.validate()
        for row in rows: ml.preflight(tok,row['state'],row['questions'])
        items, skipped = tr.items_from_rows(tok,rows,1024,256)
        if skipped or len(items)!=len(rows): raise ValueError('no skipped training inputs allowed')
        optimizer = torch.optim.AdamW([{'params':list(named.values()),'lr':config.head_lr}],weight_decay=config.weight_decay)
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer,T_max=cfg['planned_updates'],eta_min=config.min_lr)
        torch.manual_seed(config.seed); order_rng = random.Random(config.seed)
        cursor = {'epoch':0,'next_microbatch':0}
        data_sha = r.digest(rows)
        before, encoder_before = ml.parameter_hash(model), ml.parameter_hash(model,encoder=True)
        if resume:
            source = confined(folder,previous['checkpoint'])
            r.verify_snapshot(source,previous.get('checkpoint_files'))
            state = torch.load(source/'training-state.pt',map_location='cpu',weights_only=True)
            r.validate_resume_state(state,phase=phase,run_id=job['run_id'],data_sha256=data_sha,steps=steps)
            model.load_state_dict(state['head'],strict=False)
            optimizer.load_state_dict(state['optimizer']); scheduler.load_state_dict(state['scheduler'])
            torch.set_rng_state(state['torch_rng']); torch.mps.set_rng_state(state['mps_rng'])
            random.setstate(state['python_rng']); order_rng.setstate(state['option_rng'])
            cursor = state['cursor']; before = state['initial_head_sha256']
        r.atomic_json(folder/'DATA_MANIFEST.json',{'scope':'synthetic_research','eligibility':eligibility,
            'phase':phase,'rows':len(rows),'case_ids':[x['case_id'] for x in rows],
            'data_sha256':data_sha,'configuration':cfg,'parent_sha256':handoff['expected_model_sha256'],
            'optimizer_reset_from_R1':not resume,'operational_ready':False})
        stop = threading.Event()
        old = {sig:signal.signal(sig,lambda *_:stop.set()) for sig in (signal.SIGTERM,signal.SIGINT)}
        history, gradient_norms = [], []
        snapshots=json.loads((folder/'SNAPSHOTS.json').read_bytes()) if (folder/'SNAPSHOTS.json').exists() else []
        boundary_rng = (torch.get_rng_state(),torch.mps.get_rng_state(),order_rng.getstate())
        model.train(); model.encoder.eval(); model.head_checkpointing=False
        optimizer.zero_grad(set_to_none=True)

        def elapsed(): return prior_seconds+time.monotonic()-started

        def save(tag):
            name = tag+'-%04d-%02d'%(steps,fencing)
            def writer(output):
                # Save first. No calibration or validation is called inside training.
                tr.save_checkpoint(model,tok,dict(model_cfg,max_len=1024,head_max_len=256,
                    temperature=[1,1,1],temperature_by_options={}),str(output))
                state = {'head':{n:v.detach().cpu().clone() for n,v in model.state_dict().items() if not n.startswith('encoder.')},
                    'optimizer':optimizer.state_dict(),'scheduler':scheduler.state_dict(),'step':steps,
                    'cursor':dict(cursor),'torch_rng':torch.get_rng_state(),'mps_rng':torch.mps.get_rng_state(),
                    'python_rng':random.getstate(),'option_rng':order_rng.getstate(),'configuration':cfg,
                    'data_sha256':data_sha,'run_id':job['run_id'],'phase':phase,'initial_head_sha256':before}
                torch.save(state,output/'training-state.pt')
                r.atomic_json(output/'RESEARCH_METADATA.json',{'run_id':job['run_id'],'phase':phase,
                    'optimizer_updates':steps,'head_fp32_sha256':ml.parameter_hash(model),
                    'exported_head_sha256':ml.parameter_hash(model,export_roundtrip=True),
                    'data_sha256':data_sha,'configuration':cfg,'cursor':cursor,
                    'independent_evaluation_started':False,'operational_promotion':False})
                return ml.checkpoint_hashes(output)
            hashes = r.commit_snapshot(folder/name,writer)
            snapshots.append({'name':name,'updates':steps,'sha256':hashes['model.safetensors']})
            r.atomic_json(folder/'SNAPSHOTS.json',snapshots)
            return name,hashes

        def gate():
            if stop.is_set() or (folder/'PAUSED').exists(): raise InterruptedError('pause_requested')
            reason = r.stop_reason(phase,updates=steps,seconds=elapsed(),epoch=cursor['epoch'])
            if reason: raise InterruptedError(reason)
            resources = rt.resources()
            if not resources['train_allowed']:
                r.atomic_json(folder/'RESOURCE_STOP.json',resources)
                raise InterruptedError('resource_guard')

        reason = None
        try:
            save('initial' if not resume else 'resumed')
            last_save = time.monotonic()
            for epoch in range(cursor['epoch'],config.epochs):
                epoch_items=list(items);random.Random(config.seed+epoch).shuffle(epoch_items)
                start_at = cursor['next_microbatch'] if epoch==cursor['epoch'] else 0
                for offset in range(start_at,len(items),config.grad_accum):
                    gate();window=epoch_items[offset:offset+config.grad_accum];losses=[]
                    for item in window:
                        gate();torch.mps.synchronize();torch.mps.empty_cache()
                        encoded=tr.encode_item(tok,item,1024,256,tr.draw_option_order(item,order_rng,config.shuffle_options),False)
                        batch=tr.collate_items([[encoded]],tok.pad_token_id)
                        logits=tr._forward(model,batch,torch.device('mps'),False,True)
                        loss=tr.soft_ce_loss(logits,batch['target'].to('mps'),batch['marker_mask'].to('mps'))
                        if not torch.isfinite(loss).item(): raise ValueError('nonfinite loss')
                        (loss/len(window)).backward();losses.append(float(loss.detach().cpu()))
                    norm=float(torch.nn.utils.clip_grad_norm_(list(named.values()),config.grad_clip).cpu())
                    if not math.isfinite(norm) or norm==0: raise ValueError('invalid gradient')
                    gate();optimizer.step();scheduler.step();optimizer.zero_grad(set_to_none=True)
                    torch.mps.synchronize();steps+=1;gradient_norms.append(norm)
                    cursor={'epoch':epoch,'next_microbatch':offset+len(window)}
                    if cursor['next_microbatch']==len(items): cursor={'epoch':epoch+1,'next_microbatch':0}
                    boundary_rng=(torch.get_rng_state(),torch.mps.get_rng_state(),order_rng.getstate())
                    history.append({'step':steps,'mean_loss':sum(losses)/len(losses),'gradient_norm':norm})
                    journal.record(phase,fencing,steps,min(cfg['maximum_seconds'],elapsed()))
                    r.atomic_json(folder/'PROGRESS.json',{'status':'training','updates':steps,
                        'seconds':elapsed(),'cursor':cursor,'last_loss':history[-1]['mean_loss']})
                    if steps%10==0 or time.monotonic()-last_save>=300:
                        save('interim');last_save=time.monotonic()
                    if r.stop_reason(phase,updates=steps,seconds=elapsed(),epoch=cursor['epoch']):
                        reason=r.stop_reason(phase,updates=steps,seconds=elapsed(),epoch=cursor['epoch']);break
                if reason: break
        except InterruptedError as exc:
            reason=str(exc);optimizer.zero_grad(set_to_none=True)
            torch.set_rng_state(boundary_rng[0]);torch.mps.set_rng_state(boundary_rng[1]);order_rng.setstate(boundary_rng[2])
        finally:
            for sig,handler in old.items(): signal.signal(sig,handler)
        training_seconds=elapsed()
        name,hashes=save('checkpoint')
        after=ml.parameter_hash(model)
        complete = reason in ('epoch_limit','update_limit','time_limit') and steps>0 and before!=after
        result={'status':'saved' if complete else 'parked','phase':phase,'run_id':job['run_id'],
            'checkpoint':str(folder/name),'checkpoint_name':name,'sha256':hashes,
            'optimizer_updates':steps,'training_seconds':training_seconds,'wall_seconds_including_final_save':elapsed(),
            'configuration':cfg,'trainable_parameters':trainable,'encoder_unchanged':encoder_before==ml.parameter_hash(model,encoder=True),
            'head_before_sha256':before,'head_after_sha256':after,'weights_changed':before!=after,
            'history':history,'stop_reason':reason,'raw_evaluation':False,'human_approved':False,'operational_promotion':False}
        r.atomic_json(result_file,result)
        journal.record(phase,fencing,steps,min(cfg['maximum_seconds'],training_seconds),
                       status='saved' if complete else 'parked',checkpoint=name,checkpoint_files=hashes)
        check_protected(job,handoff,parent,ml)
        return {k:v for k,v in result.items() if k not in ('history','sha256')}


def reload_checkpoint(path, phase):
    import torch
    job,handoff,raw,_,parent,_,adapter,ml,policy,rt,tr,journal=context(path)
    folder=ROOT/'candidates'/(job['run_id']+'-'+phase)
    result=json.loads((folder/'TRAIN_RESULT.json').read_bytes())
    cfg=result['configuration'];checkpoint=Path(result['checkpoint'])
    with rt.exclusive(ROOT/'device.lock'):
        if not rt.resources()['verify_allowed']: return {'status':'parked_resources'}
        phase_record=journal.read()['phases'][phase]
        if phase_record.get('checkpoint')!=result['checkpoint_name']: raise ValueError('checkpoint ledger binding')
        r.verify_snapshot(checkpoint,phase_record['checkpoint_files'])
        agent=ml.load_agent(checkpoint,result['sha256'])
        meta=json.loads((checkpoint/'RESEARCH_METADATA.json').read_bytes())
        if ml.parameter_hash(agent.model)!=meta['exported_head_sha256']: raise ValueError('export reload hash')
        state=torch.load(checkpoint/'training-state.pt',map_location='cpu',weights_only=True)
        selected=r.select_s1(raw) if phase=='s1' else [x for x in raw if x['meta']['split']=='train']
        selected=adapter.prepare_training_rows(selected,policy.questions(list(policy.CRITERIA)))
        r.validate_resume_state(state,phase=phase,run_id=job['run_id'],data_sha256=r.digest(selected),steps=result['optimizer_updates'])
        agent.model.encoder.requires_grad_(False)
        trainable=[p for p in agent.model.parameters() if p.requires_grad]
        optimizer=torch.optim.AdamW([{'params':trainable,'lr':cfg['head_lr']}],weight_decay=cfg['weight_decay'])
        scheduler=torch.optim.lr_scheduler.CosineAnnealingLR(optimizer,T_max=cfg['planned_updates'],eta_min=cfg['min_lr'])
        optimizer.load_state_dict(state['optimizer']);scheduler.load_state_dict(state['scheduler'])
        if any(int(v['step'])!=state['step'] for v in optimizer.state.values()) or scheduler.last_epoch!=state['step']:
            raise ValueError('optimizer/scheduler reload step')
        probe=adapter.prepare_training_rows([next(x for x in raw if x['meta']['split']=='train')],policy.questions(list(policy.CRITERIA)))[0]
        ml.preflight(agent.tok,probe['state'],probe['questions'])
        out=agent.predict(probe['state'],probe['questions'],max_len=1024,head_max_len=256)
        if out['usage']['truncated']: raise ValueError('reload probe truncation')
        agent.model.load_state_dict(state['head'],strict=False)
        if ml.parameter_hash(agent.model)!=meta['head_fp32_sha256']: raise ValueError('FP32 head reload')
        proof={'status':'reloaded','phase':phase,'optimizer_updates':state['step'],
               'export_weights_verified':True,'fp32_head_verified':True,'optimizer_scheduler_verified':True,
               'actual_model_probe':True,'probe_is_training_row':True,'independent_evaluation_started':False,
               'model_sha256':result['sha256']['model.safetensors']}
        r.atomic_json(folder/'RELOAD_RESULT.json',proof)
        if result['status']=='saved' and result['weights_changed']: journal.mark_reloaded(phase,result['checkpoint_name'])
        check_protected(job,handoff,parent,ml)
        return proof


def evaluate(path, request_path):
    """Gold-free inference request, outside training inputs/caches; durable journal."""
    job,handoff,_,_,parent,_,adapter,ml,policy,rt,tr,journal=context(path)
    request_path=Path(request_path).resolve()
    evaluation_root=ROOT/'evaluations'/job['run_id']
    if not request_path.is_relative_to(evaluation_root): raise ValueError('evaluation-only path required')
    request=json.loads(request_path.read_bytes())
    r.validate_evaluation_request(request,policy.questions(list(policy.CRITERIA)))
    rows=request['rows']
    output=evaluation_root/request['evaluation_id'];output.mkdir(parents=True,exist_ok=True,mode=0o700)
    reservation=output/'REQUEST_SHA256.json'
    journal_path=output/'RESULTS.jsonl'
    budget_path=evaluation_root/'BUDGET.json'
    if request['model']=='r1': checkpoint,hashes=parent,handoff['checkpoint_files']
    else:
        saved=json.loads((ROOT/'candidates'/(job['run_id']+'-r2a')/'TRAIN_RESULT.json').read_bytes())
        checkpoint,hashes=Path(saved['checkpoint']),saved['sha256']
    with rt.exclusive(ROOT/'device.lock'):
        # All read-modify-write state is loaded AFTER the shared device lock.
        r.authorize_evaluation(journal.read(),request,hashes['model.safetensors'])
        if reservation.exists() and json.loads(reservation.read_bytes())!=r.file_hash(request_path): raise ValueError('evaluation identity changed')
        r.atomic_json(reservation,r.file_hash(request_path))
        results=[json.loads(x) for x in journal_path.read_bytes().splitlines()] if journal_path.exists() else []
        seen={x['case_id'] for x in results}
        if len(seen)!=len(results): raise ValueError('duplicate inference journal')
        if seen-set(x['case_id'] for x in rows): raise ValueError('foreign inference results')
        if len(results)==len(rows): return {'status':'already_complete','rows':len(rows),'model':request['model']}
        budget=json.loads(budget_path.read_bytes()) if budget_path.exists() else {'seconds':job['adopted_s0_seconds'],'slices':[]}
        if any(s['status']=='running' for s in budget['slices']): raise ValueError('abandoned evaluation requires budget reconciliation')
        allowance=min(600,2700-budget['seconds'])
        if allowance<=0: return {'status':'evaluation_budget_exhausted'}
        if not rt.resources()['verify_allowed']: return {'status':'parked_resources'}
        started=time.monotonic();session={'status':'running','reserved_seconds':allowance,'seconds':0.}
        budget['slices'].append(session);r.atomic_json(budget_path,budget)
        agent=ml.load_agent(checkpoint,hashes)
        for row in rows:
            if row['case_id'] in seen: continue
            if time.monotonic()-started>=allowance-10 or not rt.resources()['verify_allowed']: break
            # Actual runtime ordering. No expected, operation, split or rule answer enters the model.
            state=policy.state_for({**row['state'],'questions':list(row['questions'])})
            qs=policy.questions(list(row['questions']))
            record={'case_id':row['case_id'],'model_sha256':hashes['model.safetensors'],
                    'ordered_input_sha256':r.digest({'state':state,'questions':qs})}
            try:
                ml.preflight(agent.tok,state,qs)
                out=agent.predict(state,qs,max_len=1024,head_max_len=256)
                if out['usage']['truncated']: raise ValueError('unexpected truncation')
                answer=out['answers'][next(iter(qs))]
                record.update(status='completed',prediction=answer['choice'],probabilities=answer['probabilities'])
            except ValueError as exc:
                if not str(exc).startswith(('token_budget_exceeded','question_head_would_truncate')): raise
                record.update(status='context_overflow',prediction=None)
            with journal_path.open('ab') as stream:
                os.chmod(journal_path,0o600);stream.write(r.canonical(record)+b'\n');stream.flush();os.fsync(stream.fileno())
            results.append(record)
            seen.add(row['case_id'])
            elapsed=time.monotonic()-started;budget['seconds']+=elapsed-session['seconds'];session['seconds']=elapsed
            r.atomic_json(budget_path,budget)
        elapsed=time.monotonic()-started;budget['seconds']+=elapsed-session['seconds'];session.update(seconds=elapsed,status='finished')
        r.atomic_json(budget_path,budget)
        check_protected(job,handoff,parent,ml)
        return {'status':'complete' if len(results)==len(rows) else 'partial','model':request['model'],
                'rows':len(rows),'completed':len(results),'evaluation_seconds_total':budget['seconds']}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['inspect','train','reload','evaluate','freeze'])
    p.add_argument('--job',type=Path,required=True)
    p.add_argument('--phase',choices=['s1','r2a'])
    p.add_argument('--resume',action='store_true')
    p.add_argument('--request',type=Path)
    a=p.parse_args()
    if a.command=='inspect':
        c=context(a.job);out={'status':'checked','eligibility':c[3],'resources':c[9].resources(),'ledger':c[-1].read()}
    elif a.command=='train':
        if not a.phase: p.error('--phase required')
        out=train(a.job,a.phase,a.resume)
    elif a.command=='reload':
        if not a.phase: p.error('--phase required')
        out=reload_checkpoint(a.job,a.phase)
    elif a.command=='freeze':
        c=context(a.job);job=c[0];saved=json.loads((ROOT/'candidates'/(job['run_id']+'-r2a')/'TRAIN_RESULT.json').read_bytes())
        evidence={'candidate_sha256':saved['sha256']['model.safetensors'],
            'formatter_sha256':c[1]['formatter_sha256'],'rubric_sha256':c[1]['rubric_sha256'],
            'checker_sha256':job['checker_sha256'],'settings_sha256':r.digest(r.phase_config('r2a')),
            'selection':'one_r2a_no_blind_tuning'}
        out={'status':'frozen','freeze_sha256':c[-1].freeze(evidence),'evidence':evidence}
        r.atomic_json(ROOT/'runs'/job['run_id']/'CANDIDATE_FREEZE.json',out)
    else:
        if not a.request: p.error('--request required')
        out=evaluate(a.job,a.request)
    print(json.dumps(out,ensure_ascii=False),flush=True)


if __name__=='__main__':
    try: main()
    except Exception as exc:
        print(json.dumps({'status':'stopped','error_type':type(exc).__name__,
                          'reason':str(exc) if isinstance(exc,(ValueError,InterruptedError,FileExistsError)) else 'execution_error_details_private'}),flush=True)
        raise SystemExit(1)
