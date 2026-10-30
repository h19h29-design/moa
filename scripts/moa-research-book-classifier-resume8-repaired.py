"""Book-only adapter: unchanged saved-eight ML algorithm, repaired gateway lifecycle."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path('/Users/jungwoohwang/Library/Application Support/MOA-LAYA')
RUN = 'moa-unattended-20261021-fixed-classifier-01'
FOLDER = 'eight-update-repaired-20261022'
BASE_SHA = '51817ca0211491d7941151eb9bf73f46a97c4d8d9e9b2cd6a3e54e466cdac5d8'


def worker():
    source = ROOT/'incoming'/RUN/'eight-update-launchd-20261022/worker.py'
    if hashlib.sha256(source.read_bytes()).hexdigest() != BASE_SHA:
        raise ValueError('sealed Book continuation algorithm changed')
    spec = importlib.util.spec_from_file_location('sealed_book_resume8', source)
    core = importlib.util.module_from_spec(spec); spec.loader.exec_module(core)
    core.__file__ = globals().get('__file__', '<string>')
    core.FOLDER, core.UNISSUED = FOLDER, 'eight-update-launchd-20261022'
    core.FILES = {'worker.py','base-worker.py','moa/research_gateway_lifecycle.py',
        'moa/research_gateway_hold_launchd.py','moa/research_classifier_resume8_state.py',
        'tests/test_research_classifier_resume8_torch.py'}
    def lease(*, health_checks=True):
        module = core.load('approved_condition_gateway_hold', core.incoming('moa/research_gateway_lifecycle.py'))
        return module.GatewayHold(core.output()/'GATEWAY_RECOVERY.json',
            **({} if health_checks else {'health':lambda *_:False}))
    core.gateway_lease = lease
    return core


if __name__ == '__main__':
    try: worker().main()
    except Exception as exc:
        print(json.dumps(dict(status='refused',error_type=type(exc).__name__)))
        raise SystemExit(1)
