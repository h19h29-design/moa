"""Reconcile historical run identities/optimizer boundaries and missing charges.

Read-only on Book; mini writes an immutable private audit and appends missing
budget charges. This never resumes, trains, prunes or changes old checkpoints.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from moa import research_alignment as ctl, research_campaign as rc


def audit(root):
    root = Path(root)
    output = root/'EXECUTION_AUDIT.json'
    if output.exists():
        return json.loads(output.read_bytes())
    script = '''import pathlib,json,hashlib,torch
r=pathlib.Path.home()/'Library/Application Support/MOA-LAYA'
runs={}
for folder in sorted((r/'research').glob('moa-unattended-20261020-pilot-01-*')):
 result_path=folder/'TRAIN_RESULT.json'
 entry={}
 for name,key in [('TRAIN_RESULT.json','result'),('DIAGNOSTIC.json','diagnostic')]:
  p=folder/name
  if p.exists():entry[key]=json.loads(p.read_bytes())
 job=r/'incoming'/folder.name/'JOB.json'
 if job.exists():
  v=json.loads(job.read_bytes());entry['configuration']=v['configuration'];entry['sampling']=v.get('train_sampling');entry['job_sha256']=hashlib.sha256(job.read_bytes()).hexdigest()
 if result_path.exists():
  v=entry['result'];checkpoint=pathlib.Path(v['checkpoint']);entry['actual_sha256']={}
  for filename,expected in v['sha256'].items():
   with (checkpoint/filename).open('rb') as stream:h=hashlib.file_digest(stream,'sha256').hexdigest()
   if h!=expected:raise ValueError('checkpoint changed')
   entry['actual_sha256'][filename]=h
  state=torch.load(checkpoint/'training-state.pt',map_location='cpu',weights_only=False)
  entry['optimizer_boundary']={'run_id':state.get('run_id'),'step':state['step'],
   'cursor':state['cursor'],'optimizer_steps':sorted(set(int(x['step']) for x in state['optimizer']['state'].values())),
   'scheduler_step':state['scheduler'].get('last_epoch'),'initial_head_sha256':state.get('initial_head_sha256')}
  del state
 entry['old_blind_receipts']=[p.name for p in folder.glob('BLIND-*.json')]
 runs[folder.name]=entry
protected={}
for name in ['MODEL_REGISTRY.json','MODEL_MANIFEST.json']:
 protected[name]=hashlib.sha256((r/name).read_bytes()).hexdigest()
print(json.dumps({'runs':runs,'protected_models':protected}))
'''
    python = ctl.rr.REMOTE_ROOT+'/venv-smoke/bin/python'
    import shlex
    result = json.loads(ctl._ssh(shlex.quote(python)+' -', script=script, timeout=120))
    records = []
    for p in sorted(root.glob('*.log')):
        for line in p.read_text().splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict) and value.get('run_id') and 'updates' in value:
                records.append({'log': p.name, **{k: value.get(k) for k in
                    ('run_id', 'updates', 'seconds', 'stop_reason', 'checkpoint_name', 'sha256')}})
    result.update(recorded_at=ctl.now(), resume_session_receipts=records,
                  finding='21-update r2b4 is NOT the r01/r02 120-update run',
                  historical_chain_limitation='old results lack parent-boundary hashes; '
                    'optimizer/cursor and session logs agree, but a full cryptographic resume chain cannot be reconstructed',
                  blind_opened=False)
    prefix = 'moa-unattended-20261020-pilot-01-'
    charges = []
    for suffix, old_id in [('r2b', 'r2b_v1'), ('r2b2', 'r2b_v2'), ('r2b3', 'r2b_v3'),
                           ('diag', 'diag'), ('r2b4', 'r2b_v4'), ('r01', prefix+'r01'),
                           ('r02', prefix+'r02')]:
        entry = result['runs'][prefix+suffix]
        seconds = entry.get('result', entry.get('diagnostic'))['seconds']
        evidence = 'verified final single-session receipt'
        if suffix == 'r01':
            seconds = 2700.
            evidence = 'conservative 45-minute charge: old resumes overwrote cumulative duration'
        if suffix == 'r02':
            seconds = sum(v['seconds'] for v in records if v['run_id'] == prefix+suffix)
            if seconds < entry['result']['seconds']:
                raise ValueError('missing E4 resume receipts')
            evidence = 'TRAIN_R02 + R02_RESUME1 + R02_RESUME2 session durations'
        charges.append({'candidate': old_id, 'actual_run_id': prefix+suffix,
                        'charged_seconds': seconds, 'evidence': evidence})
    result['charges'] = charges
    ctl.write_once(output, ctl.canonical(result))
    ledger = rc.BudgetLedger(root/'BUDGET.jsonl')
    for charge in charges:
        ledger.charge_to(now=ctl.now(), candidate=charge['candidate'],
                        cumulative_seconds=charge['charged_seconds'], evidence=charge['evidence'])
    return result


if __name__ == '__main__':
    result = audit(ctl.DEFAULT)
    print(json.dumps({'status': 'AUDITED', 'runs': len(result['runs']),
                      'finding': result['finding'], 'blind_opened': False}))
