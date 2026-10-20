"""Durable, single-candidate source/DOM input experiment in the existing campaign.

Uses the existing SSH, worker, policy and append-only budget ledger. Each tick
does one bounded action. The old campaign, models and blind shard are preserved.
No operational service or database is reachable from this controller.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess

from . import research_campaign as rc, research_pair_data as data, research_runner as rr
from .research_dataset import canonical, digest, project

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = Path('/Volumes/KIOXIA-2TB/Cache/MOA/unattended-20261020')
RUN = 'moa-unattended-20261020-pilot-01-pairs-r03'
NODE_MODULES = '/Users/mac-mini/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'
NODE = '/Users/mac-mini/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node'
GATE = {'accuracy_min': .80, 'balanced_accuracy_min': .80, 'changed_recall_min': .85,
        'preserved_recall_min': .90, 'insufficient_recall_min': .70,
        'minimum_balanced_gain': .05, 'maximum_overflow': 0}


def now():
    return dt.datetime.now(rc.KST).isoformat(timespec='seconds')


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4*1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def write_once(path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        if path.is_symlink() or path.read_bytes() != raw:
            raise ValueError('immutable research artifact changed: '+path.name)
        return
    with path.open('xb') as stream:
        os.chmod(path, 0o600)
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())


def _read(path):
    return json.loads(Path(path).read_bytes())


def _ssh(command, *, script=None, timeout=60):
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                             '-o', 'ConnectTimeout=8', '-l', 'jungwoohwang', 'macbookair', command],
                            input=script, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise ConnectionError('MacBook action unavailable; private details not emitted')
    return result.stdout


def worker_root(root=DEFAULT):
    state = _read(Path(root)/'alignment/STATE.json')
    return Path(root)/'mini-worker' if state.get('execution_host') == 'mini' else None


def mini_environment(root=DEFAULT):
    worker = worker_root(root)
    if worker != DEFAULT/'mini-worker':
        raise ValueError('only the isolated auxiliary worker is permitted')
    return dict(os.environ, MOA_RESEARCH_HOME=str(worker), MOA_LAYA_HOME=str(worker),
                HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')


def remote(root=DEFAULT):
    worker = worker_root(root)
    root_expr = ('pathlib.Path('+repr(str(worker))+')' if worker else
                 "pathlib.Path.home()/'Library/Application Support/MOA-LAYA'")
    script = '''import pathlib,json,os,subprocess,fcntl
r=ROOT_EXPRESSION
folder=r/'research'/%r
out={'online':True,'files':{}}
for p in folder.glob('*.json'):
 if p.name in ('TRAIN_RESULT.json','PROGRESS.json','RELOAD.json','RESOURCE_STOP.json','INPUT_PREFLIGHT.json') or p.name.startswith('EVAL-'):
  out['files'][p.name]=json.loads(p.read_bytes())
ps=subprocess.check_output(['ps','-ww','-axo','pid=,args='],text=True,errors='replace')
out['running']=False
for line in ps.splitlines():
 parts=line.strip().split(None,1)
 if len(parts)==2 and int(parts[0])!=os.getpid() and 'moa-research-worker' in parts[1] and '.py' in parts[1]:out['running']=True
out['device_busy']=False
if (r/'device.lock').exists():
 with (r/'device.lock').open('rb') as stream:
  try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:out['device_busy']=True
print(json.dumps(out))
''' % RUN
    script = script.replace('ROOT_EXPRESSION', root_expr)
    if worker is not None:
        out = subprocess.run([str(worker/'venv/bin/python'), '-c', script], check=True,
                             capture_output=True, text=True, timeout=30, env=mini_environment(root))
        return json.loads(out.stdout)
    return json.loads(_ssh('python3 -', script=script))


def resources(root=DEFAULT):
    worker = worker_root(root)
    if worker is None:
        from .research_advance import _resources
        return _resources()
    script = ('import sys,json;sys.path.insert(0,'+repr(str(worker/'app'))+');'
              'from workers.laya_local import runtime;print(json.dumps(runtime.resources()))')
    out = subprocess.run([str(worker/'venv/bin/python'), '-c', script], check=True,
                         capture_output=True, text=True, timeout=30, env=mini_environment(root))
    return json.loads(out.stdout)


def prepare(root=DEFAULT):
    root = Path(root)
    folder = root/'alignment'
    if (folder/'STATE.json').exists():
        return _read(folder/'STATE.json')
    old_state = _read(root/'STATE.json')
    if old_state.get('status') != 'RESEARCH_PLATEAU':
        raise ValueError('existing campaign must be reconciled before rearm')
    if not (root/'EXECUTION_AUDIT.json').exists():
        raise ValueError('historical receipts/budget audit required first')
    docs = data.author()
    html_inventory = []
    for doc in docs:
        path = folder/'html'/(doc['case_id']+'.html')
        write_once(path, doc['html'].encode())
        html_inventory.append({'case_id': doc['case_id'], 'html_path': str(path)})
    write_once(folder/'AUTHOR.json', canonical(docs))
    write_once(folder/'DOM_INPUT.json', canonical(html_inventory))
    if not (folder/'DOM.json').exists():
        env = dict(os.environ, MOA_NODE_MODULES=NODE_MODULES)
        subprocess.run([NODE, str(ROOT/'scripts/moa-research-pair-dom.cjs'),
                        str(folder/'DOM_INPUT.json'), str(folder/'DOM.json')],
                       env=env, check=True, timeout=120, capture_output=True)
    protected = set()
    # Explicit exposed development/training inventory. Never open the sealed
    # blind shard to collect its source hashes or use it in development.
    for split in ('train', 'dev'):
        for line in (root/'data'/(split+'.jsonl')).read_bytes().splitlines():
            row = json.loads(line)
            protected.add(row['meta']['source_sha256'])
            protected.add(row['meta']['family_group_id'])
    paired, whole, summary = data.build(docs, _read(folder/'DOM.json'), protected_hashes=protected)
    if summary['families'] < 10:
        raise ValueError('ten genuinely new structural groups required')
    for variant, rows in (('paired', paired), ('whole', whole)):
        for split in ('train', 'dev'):
            subset = [r for r in rows if r['meta']['split'] == split]
            write_once(folder/'data'/(variant+'-'+split+'.jsonl'),
                       b''.join(canonical(r)+b'\n' for r in subset))
    summary.update(protected_exposed_identities=len(protected),
                   old_blind_opened=False, old_blind_seal_sha256=sha(root/'BLIND_SEAL.json'),
                   source_schema_inventory=data.schemas(),
                   gate=GATE, group_split='12 source schemas train / 6 source schemas dev')
    write_once(folder/'MANIFEST.json', canonical(summary))
    write_once(folder/'SELECTION_CRITERIA.json', canonical(GATE))
    write_once(folder/'PREVIOUS_STATE.json', canonical(old_state))
    state = {'status': 'PREPARED', 'run_id': RUN, 'prepared_at': now(),
             'manifest_sha256': sha(folder/'MANIFEST.json'), 'gate': GATE,
             'training_authorized_candidates': 1, 'published': False,
             'operational_promotion': False, 'old_blind_opened': False}
    rc._write_atomic(folder/'STATE.json', state)
    old_state['alignment_enabled'] = True
    old_state['alignment_state'] = str(folder/'STATE.json')
    rc._write_atomic(root/'STATE.json', old_state)
    return state


def rows(root, variant, split):
    return [json.loads(line) for line in (Path(root)/'alignment/data'/
            (variant+'-'+split+'.jsonl')).read_bytes().splitlines()]


def stage(root):
    root = Path(root)
    folder = root/'alignment/stage'
    spec = importlib.util.spec_from_file_location('research_publisher', ROOT/'scripts/moa-research-publish.py')
    pub = importlib.util.module_from_spec(spec); spec.loader.exec_module(pub)
    config = {'epochs': 4, 'maximum_updates': 120, 'planned_updates': 120,
              'maximum_seconds': 1800, 'micro_batch': 1, 'grad_accum': 8,
              'freeze_encoder': True, 'loss': 'soft-ce', 'head_lr': .001, 'min_lr': 1e-6,
              'weight_decay': .01, 'grad_clip': 1., 'seed': 20261020,
              'max_len': 1024, 'head_max_len': 256, 'amp': False,
              'gradient_checkpointing': False, 'shuffle_options': ['choice']}
    bundles = {}
    for split in ('train', 'dev'):
        records = rows(root, 'paired', split)
        # Train labels only; held-out labels/operations/lineage never reach Book.
        projected = project(records)
        if split == 'train':
            for value, original in zip(projected, records):
                value['expected'] = original['expected']
        path = folder/(split+'-000.jsonl')
        write_once(path, b''.join(canonical(r)+b'\n' for r in projected))
        bundles[path.name] = sha(path)
    references = {'r1': {'path': pub.PARENT, 'sha256': pub.PARENT_FILES}}
    # Pin existing E3/E4 by the verified remote receipt, never a mutable alias.
    audit = _read(root/'EXECUTION_AUDIT.json')
    for name, rid in [('e3', 'moa-unattended-20261020-pilot-01-r01'),
                      ('e4', 'moa-unattended-20261020-pilot-01-r02')]:
        saved = audit['runs'][rid]['result']
        references[name] = {'path': 'research/'+rid+'/'+saved['checkpoint_name'],
                            'sha256': saved['sha256']}
    requests = {}
    for model in (*references, 'candidate'):
        records = []
        for variant in ('whole', 'paired'):
            for row in project(rows(root, variant, 'dev')):
                row['case_id'] += '--'+variant
                records.append(row)
        name = 'development-'+model+'.json'
        write_once(folder/name, canonical({'scope': 'new_development_only',
                   'contains_gold': False, 'model': model, 'rows': records}))
        requests[name] = sha(folder/name)
    for name, source_path in [('moa-research-worker.py', ROOT/'scripts/moa-research-worker.py'),
                               ('r2_research.py', ROOT/'moa/r2_research.py')]:
        write_once(folder/name, source_path.read_bytes())
    job = rr.job_payload(run_id=RUN, bundle='incoming/'+RUN, bundle_sha256=bundles,
                         configuration=config, parent=pub.PARENT, parent_files=pub.PARENT_FILES,
                         protected_models=pub.PROTECTED, runner_sha=sha(folder/'moa-research-worker.py'),
                         library='r2_research.py', library_sha=sha(folder/'r2_research.py'),
                         installed=pub.INSTALLED, questions=pub.questions())
    job.update(reference_models=references, evaluation_requests_sha256=requests,
               resource_policy={'minimum_memory_gib': 5},
               input_version=data.INPUT_VERSION, manifest_sha256=sha(root/'alignment/MANIFEST.json'))
    write_once(folder/'JOB.json', canonical(job))
    destination = rr.REMOTE_ROOT+'/incoming/'+RUN
    _ssh('mkdir -p '+shlex.quote(destination)+' && chmod 700 '+shlex.quote(destination))
    tar = subprocess.Popen(['tar', 'cf', '-']+[p.name for p in folder.iterdir()],
                           cwd=folder, stdout=subprocess.PIPE)
    try:
        transfer = subprocess.run(rr.remote_argv(rr.DEFAULT_HOST, ['inspect', '--job', 'JOB.json'], RUN)[:-1]+
            ['cd '+shlex.quote(destination)+' && tar xf - && chmod 600 *'],
            stdin=tar.stdout, capture_output=True, timeout=120)
        tar.stdout.close()
        if transfer.returncode or tar.wait():
            raise ConnectionError('research bundle transfer failed')
    finally:
        if tar.poll() is None:
            tar.terminate(); tar.wait()
    out = rr.ssh_capture(rr.remote_argv(rr.DEFAULT_HOST, ['inspect', '--job', 'JOB.json'], RUN), timeout=120)
    rc._write_atomic(root/'alignment/PUBLISHED.json', {'at': now(), 'job_sha256': sha(folder/'JOB.json'),
                     'files': sorted(p.name for p in folder.iterdir()), 'inspect': out})
    return out


def launch(action, request=None, resume=False, root=DEFAULT):
    state = _read(Path(root)/'alignment/STATE.json')
    argv = ['train' if action == 'train' else action, '--job', state.get('job_name', 'JOB.json')]
    if request:
        argv += ['--request', request]
    if resume:
        argv += ['--resume']
    worker = worker_root(root)
    if worker is not None:
        incoming = worker/'incoming'/RUN
        runner = incoming/state['runner_name']
        job = incoming/state['job_name']
        if sha(runner) != _read(job)['runner_sha256']:
            raise ValueError('auxiliary worker changed')
        log = incoming/('ACTION-'+action+('-'+request if request else '')+'.log')
        with log.open('ab') as stream:
            process = subprocess.Popen([str(worker/'venv/bin/python'), str(runner), *argv],
                cwd=incoming, env=mini_environment(root), stdin=subprocess.DEVNULL,
                stdout=stream, stderr=stream, start_new_session=True)
        rc._write_atomic(Path(root)/'alignment/LAST_DISPATCH.json',
                         {'at': now(), 'pid': process.pid, 'host': 'mini', 'action': action,
                          'run_id': RUN, 'job_sha256': sha(job)})
        return
    command = rr.remote_argv(rr.DEFAULT_HOST, argv, RUN,
                             runner_name=state.get('runner_name', rr.RUNNER_NAME))[-1]
    log = rr.REMOTE_ROOT+'/incoming/'+RUN+'/ACTION-'+action+('-'+request if request else '')+'.log'
    # nohup detaches from the coordinating Codex/SSH session. The device lock
    # remains the final authority even if a scheduler tick races another caller.
    _ssh('nohup /bin/sh -c '+shlex.quote(command)+' > '+shlex.quote(log)+' 2>&1 < /dev/null &')


def revise_execution(root=DEFAULT):
    """Preserve v1; pin a cache-cleanup repair for its zero-update boundary.

    No candidate, data, budget, input, optimizer or scheduler setting is reset.
    The worker validates the old job byte hash and unchanged settings again.
    """
    root = Path(root)
    state_path = root/'alignment/STATE.json'
    state = _read(state_path)
    observed = remote(root)
    saved = observed['files'].get('TRAIN_RESULT.json', {})
    if (observed['running'] or observed['device_busy'] or saved.get('updates') != 0
            or saved.get('stop_reason') != 'resource_guard'):
        raise ValueError('known zero-update resource boundary required')
    folder = root/'alignment/stage'
    old = _read(folder/'JOB.json')
    revision = dict(old, execution_revision=2,
                    previous_job={'filename': 'JOB.json', 'sha256': sha(folder/'JOB.json')})
    write_once(folder/'moa-research-worker-v2.py', (ROOT/'scripts/moa-research-worker.py').read_bytes())
    revision['runner_sha256'] = sha(folder/'moa-research-worker-v2.py')
    write_once(folder/'JOB-r2.json', canonical(revision))
    destination = rr.REMOTE_ROOT+'/incoming/'+RUN
    tar = subprocess.Popen(['tar', 'cf', '-', 'moa-research-worker-v2.py', 'JOB-r2.json'],
                           cwd=folder, stdout=subprocess.PIPE)
    result = subprocess.run(rr.remote_argv(rr.DEFAULT_HOST, ['inspect', '--job', 'JOB-r2.json'], RUN)[:-1]+
        ['cd '+shlex.quote(destination)+' && tar xf - && chmod 600 moa-research-worker-v2.py JOB-r2.json'],
        stdin=tar.stdout, capture_output=True, timeout=120)
    tar.stdout.close()
    if result.returncode or tar.wait():
        raise ConnectionError('execution revision transfer failed')
    verified = rr.ssh_capture(rr.remote_argv(rr.DEFAULT_HOST, ['inspect', '--job', 'JOB-r2.json'], RUN,
                                runner_name='moa-research-worker-v2.py'), timeout=120)
    write_once(root/'alignment/EXECUTION_REVISION_2.json', canonical({
        'at': now(), 'previous_job_sha256': sha(folder/'JOB.json'),
        'job_sha256': sha(folder/'JOB-r2.json'), 'configuration_unchanged': old['configuration'] == revision['configuration'],
        'reason': 'release MPS cache before resource check; discard no optimizer updates',
        'resume_boundary': saved, 'inspect': verified}))
    state.update(execution_revision=2, runner_name='moa-research-worker-v2.py', job_name='JOB-r2.json',
                 last_resume_boundary=None, status='MEMORY_REPAIR_PREPARED')
    rc._write_atomic(state_path, state)
    return {'status': 'MEMORY_REPAIR_PREPARED', 'run_id': RUN, 'candidates_added': 0}


def metrics(gold, answers):
    labels = ('preserved', 'changed', 'insufficient')
    by_id = {a['case_id']: a for a in answers}
    if len(by_id) != len(answers) or set(by_id) != {r['case_id'] for r in gold}:
        raise ValueError('exact evaluation identity required')
    matrix = {g: {p: 0 for p in (*labels, 'incomplete')} for g in labels}
    overflow = 0
    by_operation = {}
    for row in gold:
        a = by_id[row['case_id']]
        if a.get('prediction_origin') != 'raw_laya' or a.get('system_forced') is True:
            raise ValueError('raw model answers only')
        actual = next(iter(row['expected'].values()))
        pred = a['prediction'] if a.get('status') == 'completed' else 'incomplete'
        matrix[actual][pred] += 1
        overflow += a.get('status') == 'input_overflow'
        operation = row.get('meta', {}).get('operation', 'unspecified')
        group = by_operation.setdefault(operation, {'items': 0, 'correct': 0,
                                                     'changed_to_preserved': 0})
        group['items'] += 1
        group['correct'] += actual == pred
        group['changed_to_preserved'] += actual == 'changed' and pred == 'preserved'
    totals = {g: sum(matrix[g].values()) for g in labels}
    recalls = {g: matrix[g][g]/totals[g] if totals[g] else 0. for g in labels}
    correct = sum(matrix[g][g] for g in labels)
    return {'items': len(gold), 'correct': correct, 'accuracy': correct/len(gold),
            'balanced_accuracy': sum(recalls.values())/3, 'recall': recalls,
            'confusion': matrix, 'totals': totals, 'overflow': overflow,
            'normal_false_alerts': matrix['preserved']['changed']+matrix['preserved']['insufficient'],
            'normal_false_alert_rate': (matrix['preserved']['changed']+
                matrix['preserved']['insufficient'])/totals['preserved'],
            'changed_to_preserved': matrix['changed']['preserved'],
            'actual_errors_missed': totals['changed']-matrix['changed']['changed'],
            'actual_error_detection_rate': matrix['changed']['changed']/totals['changed'],
            'changed_passed_as_normal_rate': matrix['changed']['preserved']/totals['changed'],
            'normal_misclassified_as_error': matrix['preserved']['changed'],
            'incomplete': sum(matrix[g]['incomplete'] for g in labels),
            'by_operation': by_operation,
            'unnecessary_review_requests': matrix['preserved']['changed']+
                matrix['preserved']['insufficient']+matrix['preserved']['incomplete']}


def score(root, files):
    report = {'scope': 'new_synthetic_development_not_blind', 'models': {},
              'gate_fixed_before_scores': _read(Path(root)/'alignment/SELECTION_CRITERIA.json')}
    paired_answers = {}
    for model in ('r1', 'e3', 'e4', 'candidate'):
        receipt = files.get('EVAL-development-'+model+'.json')
        if not receipt or receipt['status'] != 'complete':
            continue
        for variant in ('whole', 'paired'):
            gold = rows(root, variant, 'dev')
            answers = []
            for item in receipt['answers']:
                if item['case_id'].endswith('--'+variant):
                    item = dict(item, case_id=item['case_id'].removesuffix('--'+variant))
                    answers.append(item)
            result = metrics(gold, answers)
            if variant == 'paired':
                paired_answers[model] = answers
            result.update(seconds_both_variants=receipt['seconds'],
                          checkpoint_sha256=receipt['model_sha256'],
                          peak_rss_bytes=receipt['peak_rss_bytes'])
            report['models'][model+'_'+variant] = result
    from .research_pair_metrics import compare_with_direct
    report['direct_comparison'] = compare_with_direct(rows(root, 'paired', 'dev'), paired_answers)
    rc._write_atomic(Path(root)/'alignment/COMPARISON.json', report)
    return report


def selection(report):
    value = report['models']['candidate_paired']
    baseline = report['models']['r1_paired']
    return (value['accuracy'] >= GATE['accuracy_min'] and
            value['balanced_accuracy'] >= GATE['balanced_accuracy_min'] and
            value['balanced_accuracy'] >= baseline['balanced_accuracy']+GATE['minimum_balanced_gain'] and
            value['overflow'] <= GATE['maximum_overflow'] and
            all(value['recall'][label] >= GATE[label+'_recall_min']
                for label in ('changed', 'preserved', 'insufficient')))


def advance(root=DEFAULT):
    root = Path(root)
    if (root/'PAUSED').exists():
        return {'status': 'PAUSED_BY_OPERATOR'}
    folder = root/'alignment'
    lock_path = (Path('/Users/mac-mini/Library/Application Support/MOA-Research/alignment.lock')
                 if root == DEFAULT else folder/'.controller.lock')
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock_path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'OBSERVE_ONLY'}
        state = _read(folder/'STATE.json')
        if state['status'] in ('COMPLETE_REJECTED', 'SEALED_AWAITING_FINAL_BLIND', 'RECONCILIATION_REQUIRED'):
            return {k: state[k] for k in ('status', 'run_id')}
        dispatch = folder/'LAST_DISPATCH.json'
        if dispatch.exists():
            age = (rc.parse_time(now())-rc.parse_time(_read(dispatch)['at'])).total_seconds()
            if age < 15:
                return {'status': 'DISPATCH_SETTLING', 'run_id': RUN}
        try:
            remote_state = remote(root)
        except (ConnectionError, subprocess.TimeoutExpired):
            return {'status': 'WAITING_WORKER', 'run_id': RUN}
        if remote_state['running'] or remote_state['device_busy']:
            return {'status': 'OBSERVE_ONLY', 'run_id': RUN}
        if not state['published']:
            out = stage(root)
            state.update(published=True, status='PUBLISHED')
            rc._write_atomic(folder/'STATE.json', state)
            return {'status': 'PUBLISHED', 'run_id': RUN, 'rows': out['rows']}
        files = remote_state['files']
        for name, value in files.items():
            rc._write_atomic(folder/'receipts'/name, value)
        for model in ('r1', 'e3', 'e4'):
            name = 'EVAL-development-'+model+'.json'
            receipt = files.get(name)
            if not receipt or receipt['status'] == 'waiting_resources':
                launch('evaluate', 'development-'+model+'.json', root=root)
                state['status'] = 'BASELINE_EVALUATING'
                rc._write_atomic(folder/'STATE.json', state)
                return {'status': 'BASELINE_EVALUATING', 'model': model, 'run_id': RUN}
            if receipt['status'] != 'complete':
                state.update(status='RECONCILIATION_REQUIRED', reason='baseline evaluation incomplete')
                rc._write_atomic(folder/'STATE.json', state)
                return {'status': state['status']}
        report = score(root, files)
        trained = files.get('TRAIN_RESULT.json')
        if trained is None or trained.get('stop_reason') in ('resource_guard', 'pause_requested'):
            resource = resources(root)
            if not resource or not resource['train_allowed'] or resource['available_memory_bytes'] < 5*2**30:
                return {'status': 'WAITING_RESOURCE', 'run_id': RUN,
                        'from_updates': trained.get('updates') if trained else 0}
            if trained is None:
                if state.get('train_issued'):
                    state.update(status='RECONCILIATION_REQUIRED', reason='issued job disappeared without saved boundary')
                    rc._write_atomic(folder/'STATE.json', state)
                    return {'status': state['status']}
                campaign = _read(root/'STATE.json')
                ledger = rc.BudgetLedger(root/'BUDGET.jsonl')
                try:
                    ledger.reserve(now=now(), policy=campaign['policy_snapshot'], candidate=RUN, train_seconds=1800)
                except rc.BudgetExhausted:
                    return {'status': 'CAMPAIGN_BUDGET_EXHAUSTED'}
                state['train_issued'] = True
                state['train_issued_at'] = now()
            else:
                boundary = trained['sha256']['training-state.pt']
                if state.get('last_resume_boundary') == boundary:
                    return {'status': 'RECONCILIATION_REQUIRED', 'reason': 'same boundary already resumed'}
                # Repeated zero-progress stops are not new learning. Wait for
                # materially better headroom before spending another window.
                prior = state.get('resume_resource_boundary') or {}
                if (prior.get('updates') == trained['updates'] and
                        resource['available_memory_bytes'] < prior['available_memory_bytes']+512*2**20):
                    return {'status': 'WAITING_BETTER_HEADROOM', 'run_id': RUN,
                            'from_updates': trained['updates']}
                state['last_resume_boundary'] = boundary
                state['resume_resource_boundary'] = {
                    'updates': trained['updates'], 'available_memory_bytes': resource['available_memory_bytes']}
            state['status'] = 'TRAINING'
            rc._write_atomic(folder/'STATE.json', state)
            launch('train', resume=trained is not None, root=root)
            return {'status': 'TRAINING_RESUMED' if trained else 'TRAINING',
                    'run_id': RUN, 'from_updates': trained['updates'] if trained else 0}
        if trained.get('status') != 'saved' or trained.get('training_complete') is not True:
            state.update(status='RECONCILIATION_REQUIRED', reason='unknown/incomplete training stop')
            rc._write_atomic(folder/'STATE.json', state)
            return {'status': state['status']}
        reloaded = files.get('EVAL-development-candidate.json')
        if not reloaded or reloaded['status'] == 'waiting_resources':
            launch('evaluate', 'development-candidate.json', root=root)
            state['status'] = 'CANDIDATE_RELOADING_EVALUATING'
            rc._write_atomic(folder/'STATE.json', state)
            return {'status': state['status'], 'updates': trained['updates']}
        if reloaded['status'] != 'complete':
            state.update(status='RECONCILIATION_REQUIRED', reason='candidate evaluation incomplete')
        else:
            report = score(root, files)
            passed = selection(report)
            state.update(status='SEALED_AWAITING_FINAL_BLIND' if passed else 'COMPLETE_REJECTED',
                         dev_gate_passed=passed, completed_at=now(),
                         checkpoint_sha256=trained['sha256']['model.safetensors'],
                         rejection_reason=None if passed else 'predeclared per-class development criteria not met',
                         next_action='final blind once' if passed else 'wait_for_independent_data_and_remaining_budget')
            write_once(folder/'FINAL_SELECTION.json', canonical(state))
        rc._write_atomic(folder/'STATE.json', state)
        return {k: state[k] for k in ('status', 'run_id')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'advance', 'status', 'revise-execution'])
    parser.add_argument('--root', type=Path, default=DEFAULT)
    args = parser.parse_args()
    value = (prepare(args.root) if args.action == 'prepare' else advance(args.root)
             if args.action == 'advance' else revise_execution(args.root)
             if args.action == 'revise-execution' else _read(args.root/'alignment/STATE.json'))
    print(json.dumps(value, ensure_ascii=False))


if __name__ == '__main__':
    main()
