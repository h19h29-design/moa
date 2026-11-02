"""Replay pinned training/tokenizer records without loading or training a model.

Only the already exposed train/dev and cached binary observations are read.
No blind input, network, optimizer, model forward or operating store is used.
"""
from __future__ import annotations

from collections import Counter
import importlib.util
import json
import math
import os
from pathlib import Path
import random
from statistics import mean
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from moa import r2_research as research, research_alignment as alignment
from moa.research_diagnostics import replay_binary

C=Path('/Volumes/KIOXIA-2TB/Cache/MOA/unattended-20261020')
W=C/'mini-worker'
RUN='moa-unattended-20261020-pilot-01-pairs-r03'


def read(path):
    return json.loads(path.read_bytes())


def audit():
    output=C/'binary/FOLLOWUP_AUDIT.json'
    if output.exists():
        value=read(output)
        if value['script_sha256']!=alignment.sha(Path(__file__)):
            raise ValueError('different immutable audit implementation; preserve receipt')
        return value
    before=alignment.sha(C/'BUDGET.jsonl')
    incoming=W/'incoming'/RUN
    result_path=W/'research'/RUN/'TRAIN_RESULT.json'
    result=read(result_path)
    job_path=incoming/'JOB-r4.json'
    job=read(job_path)
    runner=incoming/'moa-research-worker-v4.py'
    if (alignment.sha(job_path)!=result['job_sha256'] or
            alignment.sha(runner)!=job['runner_sha256']):
        raise ValueError('actual executed job/runner integrity mismatch')
    checkpoint=Path(result['checkpoint'])
    for name,sha in result['sha256'].items():
        if alignment.sha(checkpoint/name)!=sha:
            raise ValueError('existing candidate artifact changed')
    protected={name:alignment.sha(W/name) for name in job['protected_models']}
    if protected!=job['protected_models']:
        raise ValueError('protected model metadata changed')
    os.environ.update(MOA_LAYA_HOME=str(W),MOA_RESEARCH_HOME=str(W),
        HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',
        TOKENIZERS_PARALLELISM='false')
    import torch
    import laya.train as tr
    from transformers import AutoTokenizer
    sys.path.insert(0,str(W/'app'))
    from workers.laya_local import policy,runtime
    for module in (tr,policy,runtime):
        if alignment.sha(Path(module.__file__))!=job['installed_sha256'][module.__name__]:
            raise ValueError('installed training implementation changed')
    spec=importlib.util.spec_from_file_location('actually_executed_worker',runner)
    worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)
    cfg=job['configuration']
    with runtime.exclusive(W/'device.lock'):
        if not runtime.resources()['verify_allowed']:
            raise RuntimeError('read-only tokenizer audit waits for resource headroom')
        torch.set_num_threads(2)
        if hasattr(os,'nice'): os.nice(10)
        # This is an optimizer/RNG snapshot, not a loaded LAYA model.
        saved=torch.load(checkpoint/'training-state.pt',map_location='cpu',weights_only=True)
        raw=[json.loads(line) for line in (incoming/'train-000.jsonl').read_bytes().splitlines()]
        rows=worker.queue(raw,policy,job['questions'],with_gold=True)
        if worker.digest(worker.canonical(rows))!=result['data_sha256']:
            raise ValueError('actual training input identity mismatch')
        tok=AutoTokenizer.from_pretrained(checkpoint/'tokenizer',local_files_only=True)
        items,skipped=tr.items_from_rows(tok,rows,1024,256)
        if skipped or len(items)!=len(rows):
            raise ValueError('actual tokenizer item mismatch')
        effective=tr.TrainConfig(**{k:v for k,v in cfg.items() if k in tr.TrainConfig.__dataclass_fields__},
                                 calib_frac=0,calib_max=0,log_every=10)
        effective.validate()
        option_rng=random.Random(effective.seed)
        permutations=Counter()
        positions={label:[0,0,0] for label in ('preserved','changed','insufficient')}
        samples=list(zip(items,rows))
        checked=0
        for epoch in range(effective.epochs):
            ordered=list(samples);random.Random(effective.seed+epoch).shuffle(ordered)
            for item,row in ordered:
                order=tr.draw_option_order(item,option_rng,effective.shuffle_options)
                encoded=tr.encode_item(tok,item,1024,256,order,False)
                if order is None or len(encoded['markers'])!=3 or len(encoded['ids'])>1024:
                    raise ValueError('actual shuffled encoding was not reproduced')
                slot=encoded['target'].index(1.)
                gold=next(iter(row['expected'].values()))
                if list(item['q']['crit'])[order[slot]]!=gold:
                    raise ValueError('gold changed identity after option permutation')
                positions[gold][slot]+=1
                permutations[','.join(map(str,order))]+=1
                checked+=1
        rng_matches=option_rng.getstate()==saved['option_rng']
        if not rng_matches:
            raise ValueError('replayed shuffle stream differs from saved actual RNG')
        label_counts=Counter(next(iter(r['expected'].values())) for r in rows)
        p={k:v/len(rows) for k,v in label_counts.items()}
        history=saved['history']
        plan=research.training_plan(cfg,len(items))
        state_audit={'updates':saved['step'],'optimizer_steps':sorted({int(v['step'])
                    for v in saved['optimizer']['state'].values()}),
            'scheduler_horizon':saved['scheduler']['T_max'],
            'scheduler_step':saved['scheduler']['last_epoch'],
            'last_learning_rate':saved['scheduler']['_last_lr'],
            'resolved_horizon':plan['optimizer_updates'],
            'first_8_loss_mean':mean(x['mean_loss'] for x in history[:8]),
            'last_8_loss_mean':mean(x['mean_loss'] for x in history[-8:]),
            'prior_only_train_ce':-sum(v*math.log(v) for v in p.values()),
            'min_gradient_norm':min(x['gradient_norm'] for x in history),
            'max_gradient_norm':max(x['gradient_norm'] for x in history),
            'nonfinite_losses':sum(not math.isfinite(x['mean_loss']) for x in history)}
    first=read(C/'binary/PROBE.json')['records']
    second=read(C/'binary/PROBE_REVERSE.json')['records']
    analyses={}
    for name in ('r1','candidate'):
        analyses[name]=replay_binary(
            [r for r in first if r['model']==name and r['mode']=='binary' and r['split']=='dev'],
            [r for r in second if r['model']==name and r['mode']=='binary_reverse' and r['split']=='dev'],
            train_counts={k:label_counts[k] for k in ('preserved','changed')})
    if before!=alignment.sha(C/'BUDGET.jsonl'):
        raise ValueError('training ledger changed during read-only audit')
    if alignment.sha(checkpoint/'model.safetensors')!=result['sha256']['model.safetensors']:
        raise ValueError('existing candidate weights changed')
    value={'status':'CONFIGURATION_FAULT_FOUND_NO_TRAINING','at':alignment.now(),
        'run_id':RUN,'script_sha256':alignment.sha(Path(__file__)),
        'job_sha256':result['job_sha256'],'checkpoint_sha256':result['sha256']['model.safetensors'],
        'executed_configuration':cfg,'train_label_counts':dict(label_counts),
        'tokenizer_replay':{'checked_encodings':checked,'gold_mapping_errors':0,
            'option_rng_matches_saved':rng_matches,'option_permutations':dict(permutations),
            'gold_slot_counts':positions},'saved_training':state_audit,'cached_development':analyses,
        'new_optimizer_updates':0,'new_model_inferences':0,'new_candidates':0,
        'training_ledger_sha256_before':before,'training_ledger_sha256_after':before,
        'protected_models':protected,'operating_model_changed':False,'old_blind_opened':False,
        'existing_run_rearmed':False,'causal_training_effect_tested':False}
    alignment.write_once(output,research.canonical(value))
    return value


if __name__=='__main__':
    value=audit()
    print(json.dumps({k:value[k] for k in ('status','tokenizer_replay','saved_training',
        'new_optimizer_updates','new_model_inferences','new_candidates')}))
