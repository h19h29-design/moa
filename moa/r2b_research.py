"""Bounded R2-B research authorization, separate from every old/operating run."""
from __future__ import annotations

import contextlib
import fcntl
import json
import math
import os
import re
from collections import Counter
from pathlib import Path

try:
    from . import r2_research as r
except ImportError:  # Same pinned helper in the isolated MacBook code folder.
    import r2_research as r

LABELS = r.LABELS
VERSION = 'moa.r2b.bounded.v1'
CRITERIA = ('conditions','omission','table_relations')


def phase_config(phase):
    if phase not in ('diagnostic','r2b'): raise ValueError('authorized R2-B phases only')
    cfg = r.phase_config('s1' if phase=='diagnostic' else 'r2a')
    if phase=='diagnostic': cfg.update(questions=27)
    return cfg


def stop_reason(phase, *, updates, seconds, epoch):
    cfg=phase_config(phase)
    if epoch>=cfg['epochs']: return 'epoch_limit'
    if updates>=cfg['maximum_updates']: return 'update_limit'
    if seconds>=cfg['maximum_seconds']: return 'time_limit'
    return None


def validate_cursor(phase,updates,cursor):
    cfg=phase_config(phase);epoch=cursor.get('epoch');offset=cursor.get('next_microbatch')
    n=cfg['questions'];accum=cfg['grad_accum'];per_epoch=math.ceil(n/accum)
    if (type(updates) is not int or type(epoch) is not int or type(offset) is not int or
        not 0<=epoch<=cfg['epochs'] or not 0<=offset<n or offset%accum or
        (epoch==cfg['epochs'] and offset) or updates!=epoch*per_epoch+offset//accum):
        raise ValueError('optimizer cursor/epoch boundary mismatch')


def select_diagnostic(rows):
    chosen=[]
    for criterion in CRITERIA:
        for label in LABELS:
            group=sorted((x for x in rows if x['meta']['split']=='train' and
                          x['expected']=={criterion:label}),key=lambda x:x['case_id'])
            if len(group)<3: raise ValueError('diagnostic question/class coverage')
            chosen.extend(group[:3])
    if len({x['case_id'] for x in chosen})!=27: raise ValueError('unique diagnostic training cases')
    return chosen


def _summary(records):
    counts=Counter();cm={label:{p:0 for p in LABELS} for label in LABELS};ids={}
    for row in records:
        if row.get('prediction_origin')!='raw_laya': raise ValueError('raw LAYA answer required')
        cid,label,p=row['case_id'],row['expected'],row['prediction']
        if cid in ids or label not in LABELS or p not in (*LABELS,None): raise ValueError('raw diagnostic identity/label')
        ids[cid]=label;counts[label]+=1
        if p is not None:cm[label][p]+=1
    n=len(records)
    return {'items':n,'counts':dict(counts),'confusion':cm,
            'accuracy':sum(cm[k][k] for k in LABELS)/n if n else 0.,
            'recall':{k:cm[k][k]/counts[k] if counts[k] else 0. for k in LABELS}},ids


def diagnostic_gate(before, after, evidence):
    pre,pre_ids=_summary(before);post,post_ids=_summary(after)
    if pre_ids!=post_ids: raise ValueError('same diagnostic inputs and independent gold required')
    n=post['items'];cfg=phase_config('diagnostic')
    gain=post['accuracy']-pre['accuracy']
    actual=(type(evidence.get('updates')) is int and 1<=evidence['updates']<=cfg['maximum_updates'] and
            isinstance(evidence.get('seconds'),(int,float)) and math.isfinite(evidence['seconds']) and
            0<evidence['seconds']<=cfg['maximum_seconds'] and
            all(evidence.get(k) is True for k in ('weights_changed','reload_verified',
                 'encoder_unchanged','finite_nonzero_gradients')))
    support=24<=n<=32 and all(post['counts'].get(k,0)>=8 for k in LABELS)
    passed=actual and support and gain>=.20 and all(post['recall'][k]>=.8 for k in LABELS)
    return {'version':VERSION,'passed':passed,'before':pre,'after':post,'accuracy_gain':gain,
            'minimum_class_recall':.8,'minimum_accuracy_gain':.20,'evidence':dict(evidence),
            'raw_records_sha256':r.digest({'before':before,'after':after}),
            'operational_promotion_allowed':False}


class ReloadReceipts:
    """Immutable receipts per full checkpoint; the old filename is a view only."""
    def __init__(self,folder):self.folder=Path(folder)

    def identity(self,saved):
        name=saved['checkpoint_name'];files=saved['sha256']
        if (not isinstance(name,str) or Path(name).name!=name or name in ('','.','..') or
            not files or 'model.safetensors' not in files or any(
                not re.fullmatch('[0-9a-f]{64}',str(sha)) for sha in files.values())):
            raise ValueError('reload checkpoint identity')
        return r.digest({'checkpoint_name':name,'files':files})

    def path(self,saved):return self.folder/'reloads'/(self.identity(saved)+'.json')

    def read(self,saved):
        path=self.path(saved)
        if not path.exists():return None
        receipt=json.loads(path.read_bytes())
        if (receipt.get('checkpoint_identity')!=self.identity(saved) or
            receipt.get('model_sha256')!=saved['sha256']['model.safetensors']):
            raise ValueError('reload receipt identity')
        return receipt

    def write(self,saved,receipt):
        if receipt.get('model_sha256')!=saved['sha256']['model.safetensors']:
            raise ValueError('reload receipt identity')
        path=self.path(saved);path.parent.mkdir(mode=0o700,exist_ok=True)
        bound={**receipt,'checkpoint_identity':self.identity(saved)}
        existing=self.read(saved)
        if existing is not None and existing!=bound:raise ValueError('immutable reload receipt changed')
        if existing is None:r.atomic_json(path,bound)
        latest=self.folder/'RELOAD_RESULT.json'
        if latest.exists() and latest.read_bytes()!=path.read_bytes():
            previous=path.parent/('legacy-'+r.file_hash(latest)+'.json')
            if not previous.exists():
                with previous.open('xb') as out:out.write(latest.read_bytes())
                previous.chmod(0o600)
        r.atomic_json(latest,bound)
        return bound


class Journal:
    """A new authorization, never a reset or alias for the closed S1/R2-A."""
    def __init__(self,path,run_id,identity):
        if not re.fullmatch(r'moa-r2b-[a-zA-Z0-9_-]{1,70}',run_id): raise ValueError('new R2-B run identity required')
        self.path,self.run_id,self.identity=Path(path),run_id,identity
        if self.path.is_symlink(): raise ValueError('journal symlink')
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with self.lock():
            if self.path.exists():
                old=self.read()
                if old['run_id']!=run_id or old['identity']!=identity: raise ValueError('journal identity changed')
            else:r.atomic_json(self.path,{'run_id':run_id,'identity':identity,'version':VERSION,
                'phases':{},'operational_ready':False,'promotion_allowed':False})

    @contextlib.contextmanager
    def lock(self):
        with self.path.with_name(self.path.name+'.lock').open('a') as stream:
            os.chmod(stream.name,0o600);fcntl.flock(stream,fcntl.LOCK_EX);yield

    def read(self):return json.loads(self.path.read_bytes())

    def claim(self,phase,resume=False):
        phase_config(phase)
        with self.lock():
            data=self.read();phases=data['phases'];old=phases.get(phase)
            if any(p['status']=='running' for p in phases.values()):raise ValueError('running phase requires reconciliation')
            if old and old['status']=='saved':return None
            if phase=='r2b' and not data.get('diagnostic_gate',{}).get('passed'):
                raise ValueError('diagnostic raw learning/save/reload gate failed or absent')
            if old and (not resume or old['status']!='parked' or not old.get('checkpoint') or not old.get('files')):
                raise ValueError('resume requires saved optimizer boundary; failed attempts never retry')
            p=dict(old or {'updates':0,'seconds':0.,'fencing':0})
            p.update(status='running',fencing=p['fencing']+1)
            phases[phase]=p;r.atomic_json(self.path,data);return p['fencing']

    def record(self,phase,fencing,updates,seconds,status='running',checkpoint=None,files=None):
        cfg=phase_config(phase)
        if status not in ('running','parked','saved','failed'):raise ValueError('phase status')
        with self.lock():
            data=self.read();p=data['phases'][phase]
            if p['status']!='running' or p['fencing']!=fencing:raise ValueError('stale fencing token')
            if updates<p['updates'] or seconds<p['seconds']:raise ValueError('nonmonotonic budget')
            if type(updates) is not int or updates>cfg['planned_updates'] or not math.isfinite(seconds) or seconds>cfg['maximum_seconds']:
                raise ValueError('authorized phase budget exceeded')
            p.update(updates=updates,seconds=seconds,status=status)
            if checkpoint is not None:p.update(checkpoint=checkpoint,files=files)
            r.atomic_json(self.path,data)

    def bind_gate(self,report,receipt_sha256):
        with self.lock():
            data=self.read();p=data['phases'].get('diagnostic',{})
            if p.get('status')!='saved' or report.get('version')!=VERSION or report['evidence']['updates']!=p['updates']:
                raise ValueError('saved diagnostic gate binding')
            if not re.fullmatch('[0-9a-f]{64}',receipt_sha256):raise ValueError('diagnostic receipt digest')
            bound={**report,'receipt_sha256':receipt_sha256}
            if 'diagnostic_gate' in data and data['diagnostic_gate']!=bound:raise ValueError('diagnostic gate already sealed')
            data['diagnostic_gate']=bound;r.atomic_json(self.path,data)
