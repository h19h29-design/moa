"""Human-approved lifecycle repair, reusing sealed training/control algorithms.

Earlier authorizations, terminal states, and dispatches are never edited. This
adapter seals one unissued continuation of the same saved eight-update model.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import shlex
import subprocess

from . import research_alignment as a, research_campaign as rc, r2_research as research
from . import research_classifier_control as control, research_classifier_resume8_launchd as prior
from . import research_runner as rr

FOLDER = 'eight-update-repaired-20261022'
HELPER = 'scripts/moa-research-book-classifier-resume8-repaired.py'
BASE_SHA = '00e3b253cc8351da292c8cd39dbec06eda776a908ac0e787f961f48ac0b3f477'


def preserved_unissued(root, at):
    _, _, state = prior._verify(root, at)
    q = root / control.FOLDER / prior.FOLDER
    if any((q/n).exists() for n in ('DISPATCH.json','EVAL_DISPATCH.json')):
        raise ValueError('an issued control cannot be superseded')
    receipt = rc._read_json(q/'PREFLIGHT_STOP.json')
    if (state['status'] != 'RECONCILIATION_REQUIRED' or
            state.get('reason') != 'gateway_lifecycle_preflight_failed_before_training_dispatch' or
            receipt.get('training_issued') is not False or receipt.get('evaluation_issued') is not False or
            not all(receipt.get(k) is True for k in ('source_integrity_verified','ledger_unchanged','services_restored_separately')) or
            any((q/n).exists() for n in ('DISPATCH.json','EVAL_DISPATCH.json'))):
        raise ValueError('only the restored never-dispatched lifecycle failure may be superseded')
    return {n:a.sha(q/n) for n in ('AUTHORIZATION.json','STATE.json','PREFLIGHT_STOP.json')}


def remote(action, plan, auth=None, *, raw=None, timeout=60):
    payload = dict(action=action, run_id=control.RUN, stage_sha256=plan['stage_sha256'], folder=FOLDER)
    if auth is not None:
        payload.update(authorization_sha256=hashlib.sha256(research.canonical(auth)).hexdigest(),
                       resume_sha256=auth['resume_sha256'])
    command = shlex.quote(rr.REMOTE_ROOT+'/venv-smoke/bin/python')+' -c '+shlex.quote(
        (a.ROOT/HELPER).read_text())+' '+shlex.quote(json.dumps(payload))
    result = subprocess.run(['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes',
        '-o','ConnectTimeout=8','-l','jungwoohwang','macbookair',command], input=raw,
        capture_output=True, timeout=max(timeout,120) if action in ('pause_gateways','restore_gateways') else timeout)
    if len(result.stdout)>2*2**20: raise ValueError('Book response exceeds cap')
    if result.returncode:
        # Raw stderr/argv can contain private data; the Book lifecycle journal has codes only.
        raise ValueError('Book '+action+' refused; inspect sanitized lifecycle journal')
    return json.loads(result.stdout)


def controller():
    source = Path(prior.__file__)
    if hashlib.sha256(source.read_bytes()).hexdigest() != BASE_SHA:
        raise ValueError('sealed continuation controller changed')
    spec = importlib.util.spec_from_file_location('moa._sealed_resume8_repaired_runtime', source)
    core = importlib.util.module_from_spec(spec); spec.loader.exec_module(core)
    # A separate namespace reuses the reviewed algorithm without mutating its old module/seals.
    core.__file__ = __file__
    core.FOLDER, core.HELPER = FOLDER, HELPER
    core.SOURCES = {'worker.py':HELPER,
        'base-worker.py':'scripts/moa-research-book-classifier-resume8-launchd.py',
        'moa/research_gateway_lifecycle.py':'moa/research_gateway_lifecycle.py',
        'moa/research_gateway_hold_launchd.py':'moa/research_gateway_hold_launchd.py',
        'moa/research_classifier_resume8_state.py':'moa/research_classifier_resume8_state.py',
        'tests/test_research_classifier_resume8_torch.py':'tests/test_research_classifier_resume8_torch.py'}
    core._blocked_unissued, core._remote = preserved_unissued, remote
    return core


if __name__ == '__main__': controller().main()
