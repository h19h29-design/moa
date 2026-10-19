"""Private synthetic-research queue; deliberately NOT the operational READY gate.

Only the coordinator's fixed S1/R2-A experiment is authorized. A killed process
cannot silently reserve a new run; clean pauses resume the saved boundary with
the same cumulative budget. No model, network, DB or subprocess operations here.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path

LABELS = ('preserved', 'changed', 'insufficient')
CAPS = {'s1': (40, 600), 'r2a': (400, 2700)}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode()


def digest(value): return hashlib.sha256(canonical(value)).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(4*1024*1024), b''): h.update(chunk)
    return h.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name+'.pending-'+str(os.getpid()))
    with temporary.open('xb') as stream:
        os.chmod(temporary, 0o600)
        stream.write(canonical(value)+b'\n'); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def research_eligibility(rows):
    counts, splits, ids = Counter(), {}, set()
    for row in rows:
        m = row['meta']; split = m['split']
        if (split not in ('train', 'dev') or m.get('synthetic_gold') is not True or
            m.get('human_gold') is not False or m.get('evaluation_only') is not False or
            m.get('rights_status') != 'allowed' or
            m.get('rights_evidence') != 'independently_authored_synthetic_by_moa' or
            m.get('label_origin') != 'deterministic_oracle' or m.get('forbidden_lineage') != []):
            raise ValueError('synthetic research eligibility')
        if len(row['expected']) != 1 or next(iter(row['expected'].values())) not in LABELS:
            raise ValueError('research label')
        if row['case_id'] in ids: raise ValueError('duplicate research case')
        ids.add(row['case_id']); counts[split] += 1
        for key in ('original_group_id', 'family_group_id', 'source_sha256'):
            identity = (key, m[key])
            if identity in splits and splits[identity] != split: raise ValueError('research split leakage')
            splits[identity] = split
    if counts != {'train': 240, 'dev': 80}: raise ValueError('fixed v4 inventory required')
    return {'scope':'synthetic_research', 'counts':dict(counts), 'rows_sha256':digest(rows),
            'new_real_training_originals':0, 'operational_ready':False,
            'automatic_approval':False, 'model_promotion':False}


def select_s1(rows):
    buckets = {label: [] for label in LABELS}
    for row in sorted(rows, key=lambda r: r['case_id']):
        if row['meta']['split'] == 'train': buckets[next(iter(row['expected'].values()))].append(row)
    picked = [row for label, n in zip(LABELS, (11,11,10)) for row in buckets[label][:n]]
    if len(picked) != 32: raise ValueError('S1 class coverage')
    return picked


def phase_config(phase):
    if phase not in CAPS: raise ValueError('fixed research phase only')
    updates, seconds = CAPS[phase]
    n, epochs = (32, 10) if phase == 's1' else (240, 2)
    return {'parent':'immutable_r1', 'questions':n, 'epochs':epochs,
            'maximum_updates':updates, 'maximum_seconds':seconds,
            'planned_updates':min(updates, ((n+7)//8)*epochs),
            'micro_batch':1, 'grad_accum':8, 'freeze_encoder':True,
            'loss':'soft-ce', 'head_lr':0.0003, 'min_lr':0.000001,
            'weight_decay':0.01, 'grad_clip':1.0, 'seed':20261019,
            'max_len':1024, 'head_max_len':256, 'amp':False,
            'gradient_checkpointing':False, 'shuffle_options':['choice']}


def stop_reason(phase, *, updates, seconds, epoch):
    c = phase_config(phase)
    if epoch >= c['epochs']: return 'epoch_limit'
    if updates >= c['maximum_updates']: return 'update_limit'
    if seconds >= c['maximum_seconds']: return 'time_limit'
    return None


class Journal:
    """Fenced single-owner JSON queue. Callers also hold the existing MPS lock."""
    def __init__(self, path, run_id, identity):
        self.path, self.run_id, self.identity = Path(path), run_id, identity
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.lock():
            if self.path.exists():
                old = self.read()
                if old['run_id'] != run_id or old['identity'] != identity: raise ValueError('journal identity changed')
            else:
                atomic_json(self.path, {'run_id':run_id, 'identity':identity, 'phases':{},
                                       'scope':'synthetic_research', 'promotion_allowed':False})

    @contextlib.contextmanager
    def lock(self):
        with self.path.with_name(self.path.name+'.lock').open('a') as stream:
            os.chmod(stream.name, 0o600)
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield

    def read(self): return json.loads(self.path.read_bytes())

    def claim(self, phase, resume=False):
        phase_config(phase)
        with self.lock():
            data = self.read(); phases = data['phases']; previous = phases.get(phase)
            if any(p['status'] == 'running' for p in phases.values()): raise ValueError('running phase needs reconciliation')
            if previous and previous['status'] == 'saved': return None
            if phase == 'r2a' and not phases.get('s1',{}).get('reload_verified'):
                raise ValueError('S1 save/reload required')
            if previous and (not resume or previous['status'] != 'parked' or not previous.get('checkpoint')):
                raise ValueError('resume requires saved optimizer boundary')
            current = dict(previous or {'updates':0, 'seconds':0., 'fencing':0})
            current.update(status='running', fencing=current['fencing']+1)
            phases[phase] = current; atomic_json(self.path, data)
            return current['fencing']

    def record(self, phase, fencing, updates, seconds, status='running', checkpoint=None, checkpoint_files=None):
        if status not in ('running','parked','saved','failed'): raise ValueError('phase status')
        with self.lock():
            data = self.read(); p = data['phases'][phase]
            if p['fencing'] != fencing or p['status'] != 'running': raise ValueError('stale fencing token')
            if updates < p['updates'] or seconds < p['seconds']: raise ValueError('nonmonotonic budget')
            max_updates, max_seconds = CAPS[phase]
            others = [v for k,v in data['phases'].items() if k != phase]
            if (updates > max_updates or seconds > max_seconds or
                updates+sum(v['updates'] for v in others) > 440 or
                seconds+sum(v['seconds'] for v in others) > 3300): raise ValueError('research budget exceeded')
            p.update(updates=updates, seconds=seconds, status=status)
            if checkpoint is not None: p['checkpoint'] = checkpoint
            if checkpoint_files is not None: p['checkpoint_files'] = checkpoint_files
            atomic_json(self.path, data)

    def mark_reloaded(self, phase, checkpoint):
        with self.lock():
            data = self.read(); p = data['phases'][phase]
            if p['status'] != 'saved' or p['checkpoint'] != checkpoint: raise ValueError('saved checkpoint identity')
            if p['updates']<1: raise ValueError('actual updates required for diagnostic')
            p['reload_verified'] = True; atomic_json(self.path, data)

    def freeze(self, evidence):
        required = {'candidate_sha256','formatter_sha256','rubric_sha256','checker_sha256',
                    'settings_sha256','selection'}
        if set(evidence) != required: raise ValueError('freeze fields')
        if any(len(evidence[k]) != 64 for k in required-{'selection'}): raise ValueError('freeze digest')
        with self.lock():
            data = self.read()
            if not data['phases'].get('r2a',{}).get('reload_verified'): raise ValueError('reloaded candidate required')
            if 'freeze' in data and data['freeze'] != evidence: raise ValueError('candidate selection already frozen')
            data['freeze'] = evidence; atomic_json(self.path,data)
        return digest(evidence)


def future_candidate(evidence, used):
    new = [r for r in evidence if r.get('gold_sha256') not in used and r.get('gold_sha256') and
           r.get('label_origin') in ('independent_oracle','human_approved') and
           r.get('rights_status') == 'allowed' and r.get('evaluation_only') is False]
    return {'ready':bool(new), 'new_gold':len(new), 'auto_start':False,
            'reason':'independent_new_gold' if new else 'waiting_for_independent_new_gold'}


def commit_snapshot(destination, writer):
    destination = Path(destination)
    if destination.exists(): raise FileExistsError('checkpoint preserved')
    pending = destination.with_name(destination.name+'.pending')
    pending.mkdir(parents=True, exist_ok=False, mode=0o700)
    hashes = writer(pending)
    for name, expected in hashes.items():
        path = pending/name
        if Path(name).is_absolute() or '..' in Path(name).parts or path.is_symlink() or not path.is_file() or file_hash(path) != expected:
            raise ValueError('checkpoint digest mismatch')
        with path.open('rb') as stream: os.fsync(stream.fileno())
    os.rename(pending, destination)
    fd = os.open(destination.parent, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)
    return hashes


def verify_snapshot(root, hashes):
    root=Path(root)
    if root.is_symlink() or not hashes: raise ValueError('checkpoint digest manifest')
    for name,expected in hashes.items():
        p=root/name
        if (Path(name).is_absolute() or '..' in Path(name).parts or p.is_symlink() or
            not p.resolve().is_relative_to(root.resolve()) or not p.is_file() or file_hash(p)!=expected):
            raise ValueError('checkpoint digest mismatch')


def validate_resume_state(state, *, phase, run_id, data_sha256, steps):
    cfg=phase_config(phase)
    if (state.get('step')!=steps or state.get('run_id')!=run_id or state.get('phase')!=phase or
        state.get('data_sha256')!=data_sha256 or state.get('configuration')!=cfg):
        raise ValueError('resume identity/configuration')
    cursor=state['cursor'];epoch,offset=cursor['epoch'],cursor['next_microbatch']
    if (type(epoch)!=int or type(offset)!=int or not 0<=epoch<=cfg['epochs'] or
        not 0<=offset<cfg['questions'] or offset%8 or epoch==cfg['epochs'] and offset or
        epoch*((cfg['questions']+7)//8)+offset//8!=steps):
        raise ValueError('resume cursor/update mismatch')


def validate_evaluation_request(request, qdefs):
    if set(request)!={'evaluation_id','model','rows','freeze_sha256'}: raise ValueError('evaluation request fields')
    if request['model'] not in ('r1','r2a'): raise ValueError('fixed evaluation model')
    if not isinstance(request['evaluation_id'],str) or not re.fullmatch('[a-zA-Z0-9_-]{1,80}',request['evaluation_id']):
        raise ValueError('evaluation identifier')
    rows=request['rows'];seen=set()
    if not isinstance(rows,list) or not 1<=len(rows)<=200: raise ValueError('evaluation inventory')
    for row in rows:
        if set(row)!={'case_id','state','questions'}: raise ValueError('gold-free rows required')
        cid=row['case_id'];qs=row['questions']
        if not isinstance(cid,str) or not re.fullmatch('[a-zA-Z0-9_-]{1,128}',cid) or cid in seen:
            raise ValueError('unique nonempty case ID required')
        seen.add(cid)
        if not isinstance(qs,dict) or len(qs)!=1 or any(k not in qdefs or v!=qdefs[k] for k,v in qs.items()):
            raise ValueError('exactly one supported evaluation question')


def authorize_evaluation(ledger, request, model_sha256):
    freeze=ledger.get('freeze')
    if not isinstance(freeze,dict) or not freeze or not ledger.get('phases',{}).get('r2a',{}).get('reload_verified'):
        raise ValueError('saved reloaded frozen candidate required')
    if request.get('freeze_sha256')!=digest(freeze): raise ValueError('frozen settings mismatch')
    if request['model']=='r2a' and model_sha256!=freeze['candidate_sha256']:
        raise ValueError('frozen candidate mismatch')
