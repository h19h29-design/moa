"""One read-only, budgeted frozen-weight diagnosis. Never trains or opens blind.

Uses only existing exposed synthetic train/dev, with original lineage retained.
The existing candidate's 600s evaluation ceiling also covers this diagnostic.
An immutable reservation prevents an unknown interruption from resetting time.
"""
from __future__ import annotations

import copy
import gc
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import time
from collections import Counter

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from moa import research_binary as binary, research_alignment as alignment
from moa.research_campaign import _write_atomic
from moa.research_dataset import canonical

C=Path('/Volumes/KIOXIA-2TB/Cache/MOA/unattended-20261020')
W=C/'mini-worker'
RUN='moa-unattended-20261020-pilot-01-pairs-r03'
OUT=C/'binary'


def read(path):
    return json.loads(path.read_bytes())


def rows(path):
    return [json.loads(line) for line in path.read_bytes().splitlines() if line.strip()]


def inventory(row, doc):
    source_bytes=canonical(doc['source_sections'])
    sha=hashlib.sha256(source_bytes).hexdigest()
    acquired={b['id']:b['text'] for b in doc['source_sections']}
    if any(acquired.get(b['id'])!=b['text'] for b in row['state']['source_blocks']):
        raise ValueError('source acquisition content mismatch')
    alignment.write_once(OUT/'sources'/(row['case_id']+'.json'),source_bytes)
    ids=[b['id'] for b in row['state']['source_blocks']]
    return {'source_revision':sha,'render_revision':row['meta']['dom_sha256'],
        'file':{'sha256':sha,'integrity_verified':True,'expected_pages':['synthetic-1'],
            'available_pages':['synthetic-1']},
        'scope':{'required_context_ids':['head','unit','note','condition','main'],
            'provided_context_ids':ids,'table_inventory_complete':True},
        'tables':[{'id':'table-main','source_block_id':'main',
            'required':{'headers':['head'],'footnotes':['note'],'merges':[]},
            'provided':{'headers':['head'] if 'head' in ids else [],
                'footnotes':['note'] if 'note' in ids else [],'merges':[]}}],
        'role':{'value':'notice','confirmed_by':'synthetic_author','source_sha256':sha,'source_revision':sha}}


def prepare():
    docs={d['case_id']:d for d in read(C/'alignment/AUTHOR.json')}
    result={}
    for split in ('train','dev'):
        original=rows(C/'alignment/data'/('paired-'+split+'.jsonl'))
        provenances={r['case_id']:inventory(r,docs[r['case_id']]) for r in original}
        part=binary.prepare_split(original,provenances,split=split)
        alignment.write_once(OUT/(split+'.jsonl'),b'\n'.join(canonical(r) for r in part['rows'])+b'\n')
        alignment.write_once(OUT/(split+'-inventories.json'),canonical(provenances))
        result[split]={'rows':len(part['rows']),'rule_excluded':len(part['excluded_ids']),
            'label_counts':dict(Counter(next(iter(r['expected'].values())) for r in part['rows'])),
            'families':len({r['meta']['family_group_id'] for r in part['rows']}),
            'sha256':alignment.sha(OUT/(split+'.jsonl'))}
    manifest={'input_version':binary.VERSION,'splits':result,'training_authorized':False,
        'independent_new_groups':0,'evaluation_lineage':'existing_exposed_train_dev_derivative',
        'acquisition_author_sha256':alignment.sha(C/'alignment/AUTHOR.json'),
        'old_blind_opened':False,'source_manifest_sha256':alignment.sha(C/'alignment/MANIFEST.json')}
    alignment.write_once(OUT/'MANIFEST.json',canonical(manifest))
    return manifest


def probe(*, reverse=False):
    prepare()
    receipt=OUT/('PROBE_REVERSE.json' if reverse else 'PROBE.json')
    reservation=OUT/('REVERSE_EVAL_RESERVATION.json' if reverse else 'EVAL_RESERVATION.json')
    if receipt.exists():
        return read(receipt)
    previous=sum(read(p)['seconds'] for p in (C/'alignment/receipts').glob('EVAL-*.json'))
    if reverse:
        if read(OUT/'PROBE.json')['status']!='complete':
            raise ValueError('complete canonical probe required')
        previous+=read(OUT/'EVAL_RESERVATION.json')['reserved_seconds']
    reserved=50 if reverse else 90
    if previous+reserved>600:
        raise ValueError('existing evaluation budget exhausted; no reset')
    if reservation.exists():
        raise ValueError('prior diagnostic reservation exists; reconcile, do not retry')
    ledger_sha=alignment.sha(C/'BUDGET.jsonl')
    alignment.write_once(reservation,canonical({'at':alignment.now(),
        'prior_seconds':previous,'reserved_seconds':reserved,'maximum_total_seconds':600,
        'optimizer_updates':0,'parent_run_id':RUN}))
    started=time.monotonic()
    result={'status':'running','scope':'exposed_development_and_training_fit_not_blind',
        'optimizer_updates':0,'training_budget_sha256_before':ledger_sha,'models':{},'records':[]}
    def timeout(*_):
        raise TimeoutError('reserved inference time reached')
    old_alarm=signal.signal(signal.SIGALRM,timeout)
    signal.setitimer(signal.ITIMER_REAL,reserved-5)
    try:
        os.environ.update(MOA_LAYA_HOME=str(W),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',
            HF_HUB_DISABLE_TELEMETRY='1',TOKENIZERS_PARALLELISM='false',WANDB_DISABLED='true')
        import torch
        import laya.train as tr
        from laya.common import build_sequence,QTYPES
        from laya.train import to_internal
        sys.path.insert(0,str(W/'app'))
        from workers.laya_local import policy,runtime,ml
        torch.set_num_threads(2);os.nice(10)
        job=read(W/'incoming'/RUN/'JOB-r4.json')
        saved=read(W/'research'/RUN/'TRAIN_RESULT.json')
        refs={'candidate':(Path(saved['checkpoint']),saved['sha256']),
            'r1':(W/job['reference_models']['r1']['path'],job['reference_models']['r1']['sha256'])}
        original={s:rows(C/'alignment/data'/('paired-'+s+'.jsonl')) for s in ('train','dev')}
        b_rows={s:{r['case_id']:r for r in rows(OUT/(s+'.jsonl'))} for s in original}
        selected=[]
        # Full dev, plus a balanced 18-row train-fit diagnostic; no new examples.
        selected += [('dev',r) for r in original['dev']]
        for gold in ('preserved','changed','insufficient'):
            selected += [('train',r) for r in sorted(original['train'],key=lambda r:r['case_id'])
                if next(iter(r['expected'].values()))==gold][:6]
        if reverse:
            selected=[(s,r) for s,r in selected if s=='dev' and r['case_id'] in b_rows[s]]
        with runtime.exclusive(W/'device.lock'):
            if not runtime.resources()['verify_allowed']:
                raise RuntimeError('inference resource guard')
            for name,(path,hashes) in refs.items():
                ml.verify_model(path,hashes)
                model,tok,cfg=tr.load_checkpoint(str(path))
                model.encoder.config.reference_compile=False
                model.to('cpu');model.eval();model.requires_grad_(False)
                weights_before={k:p._version for k,p in model.named_parameters()}
                resource_checked=time.monotonic()
                for split,row in selected:
                    qid=next(iter(row['questions']))
                    state=policy.state_for(row['state'])
                    modes={}
                    if split=='train' or row['meta']['operation'] in ('identity','relation'):
                        modes['three']=policy.questions([qid])
                    if row['case_id'] in b_rows[split]:
                        modes['binary']=binary.model_input(b_rows[split][row['case_id']])['questions']
                    # Permutation probes for matched normal/error on the same
                    # source group. They diagnose position sensitivity only.
                    if split=='dev' and row['meta']['operation'] in ('identity','relation'):
                        reverse_q=copy.deepcopy(modes['three'])
                        reverse_q[qid]['criteria']=dict(reversed(list(reverse_q[qid]['criteria'].items())))
                        modes['reverse_options']=reverse_q
                    if receipt.name=='PROBE_REVERSE.json':
                        qs=binary.model_input(b_rows[split][row['case_id']])['questions']
                        qs[qid]['criteria']=dict(reversed(list(qs[qid]['criteria'].items())))
                        modes={'binary_reverse':qs}
                    for mode,qs in modes.items():
                        if time.monotonic()-resource_checked>=2:
                            if not runtime.resources()['verify_allowed']:
                                raise RuntimeError('inference resource guard')
                            resource_checked=time.monotonic()
                        q=to_internal(qid,qs[qid])
                        ids,markers,stats,trunc=build_sequence(tok,state,q,1024,256,
                            return_stats=True,return_truncation_stats=True)
                        if trunc['truncated'] or stats['options_distinct']!=len(q['crit']):
                            raise ValueError('diagnostic input overflow')
                        item={'ids':ids,'markers':markers,'qtype':QTYPES[q['t']],
                            'target':[0.0]*len(markers)}
                        batch=tr.collate_items([[item]],tok.pad_token_id)
                        with torch.inference_mode():
                            z=tr._forward(model,batch,torch.device('cpu'),False,True)[0]
                        keys=list(q['crit']);prob=z.softmax(-1).tolist()
                        result['records'].append({'model':name,'split':split,'mode':mode,
                            'case_id':row['case_id'],'operation':row['meta']['operation'],
                            'family':row['meta']['family'],'expected':next(iter(row['expected'].values())),
                            'prediction':keys[int(z.argmax())], 'probabilities':dict(zip(keys,prob)),
                            'logits':dict(zip(keys,z.tolist())),'tokens':len(ids),'system_forced':False})
                unchanged=all(p._version==weights_before[k] for k,p in model.named_parameters())
                result['models'][name]={'checkpoint_sha256':hashes['model.safetensors'],
                    'weights_unchanged':unchanged,'trained':False}
                if not unchanged:raise ValueError('read-only probe mutated a tensor')
                del model;gc.collect()
                if alignment.sha(path/'model.safetensors')!=hashes['model.safetensors']:
                    raise ValueError('stored checkpoint changed')
        result['status']='complete'
    except (TimeoutError,RuntimeError) as error:
        result.update(status='partial_stopped',reason=str(error))
    finally:
        signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,old_alarm)
        result['seconds']=time.monotonic()-started
        result['training_budget_sha256_after']=alignment.sha(C/'BUDGET.jsonl')
        if result['training_budget_sha256_after']!=ledger_sha:
            raise ValueError('training ledger changed during read-only diagnosis')
        _write_atomic(receipt,result)
    return result


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reverse-options',action='store_true')
    args=parser.parse_args()
    out=probe(reverse=args.reverse_options)
    print(json.dumps({'status':out['status'],'seconds':out['seconds'],
        'inference_records':len(out['records']),'optimizer_updates':0}))
