"""Condition-based lifecycle for the two approved gateways; sealed history stays intact."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import time

_source = Path(__file__).with_name('research_gateway_hold_launchd.py')
if hashlib.sha256(_source.read_bytes()).hexdigest() != 'b84cce7deb3f5c5469c0de11026f1c336947715d5851e36745ce3c7edf790048':
    raise ValueError('sealed gateway inspector changed')
_spec = importlib.util.spec_from_file_location('sealed_gateway_inspector', _source)
_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_base)
GATEWAYS, PLISTS = _base.GATEWAYS, _base.PLISTS


class GatewayHold(_base.GatewayHold):
    """Reconcile observed state, not successful launchctl submission alone."""

    def event(self, **values):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path.with_name('LIFECYCLE.jsonl'), os.O_WRONLY|os.O_APPEND|os.O_CREAT, 0o600)
        with os.fdopen(fd, 'a') as f:
            json.dump(dict(at=time.time(), **values), f, sort_keys=True)
            f.write('\n'); f.flush(); os.fsync(f.fileno())

    def command(self, *args):
        p = super().command(*args)
        if args[0] not in ('print', 'print-disabled'):
            # Only approved action/label/return code, never plist/env/stderr contents.
            self.event(action=args[0], target=args[-1].split('/')[-1], returncode=p.returncode)
        return p

    def pause(self, *, on_journal=None):
        if self.path.exists():
            state = self.read()
            if state['status'] == 'PAUSED':
                values = self.inspect()
                if all(s['disabled'] and not s['registered'] for s in values.values()): return state
            raise ValueError('gateway hold already used or changed; do not rearm')
        before = self.inspect()
        if any(s['disabled'] or not s['registered'] or not s['running'] or not s['healthy'] for s in before.values()):
            raise ValueError('both original services must be enabled and healthy')
        state = dict(status='PREPARING', recovery_required=True, original=before, started_at=time.time())
        self.write(state, once=True)
        try:
            if on_journal: on_journal()
            for name in GATEWAYS:
                if self.command('disable', 'gui/501/'+name).returncode: raise ValueError('gateway disable failed')
            for name in GATEWAYS:
                if self.command('bootout', 'gui/501/'+name).returncode: raise ValueError('gateway bootout failed')
            # bootout is asynchronous: SIGTERMed remains registered until teardown ends.
            for _ in range(35):
                after = self.inspect()
                if any(not s['disabled'] for s in after.values()): raise ValueError('gateway hold unexpectedly re-enabled')
                if all(not s['registered'] for s in after.values()):
                    state.update(status='PAUSED', held=after)
                    self.write(state); self.event(transition='PAUSED'); return state
                self.wait(1)
            raise ValueError('gateway teardown deadline exceeded')
        except BaseException:
            self.restore()
            raise

    def restore(self):
        state = self.read()
        if set(state['original']) != set(GATEWAYS) or any(s['disabled'] for s in state['original'].values()):
            raise ValueError('unexpected original state, no broad restore')
        state.update(status='RECOVERY_REQUIRED', recovery_required=True)
        self.write(state)
        for tick in range(60):
            values = self.inspect()
            if all(not s['disabled'] and s['registered'] and s['running'] and s['healthy'] for s in values.values()):
                state.update(status='RESTORED_VERIFIED', recovery_required=False,
                             restored=values, restored_at=time.time())
                self.write(state); self.event(transition='RESTORED_VERIFIED'); return state
            for name, s in values.items():
                if s['disabled']:
                    if self.command('enable', 'gui/501/'+name).returncode: raise ValueError('gateway re-enable failed')
                # Re-evaluate every iteration: the job may disappear AFTER a prior kickstart.
                if not s['registered'] and tick % 2 == 0:
                    self.command('bootstrap', 'gui/501', str(PLISTS/(name+'.plist')))
                elif s['registered'] and not s['running'] and tick % 5 == 0:
                    self.command('kickstart', 'gui/501/'+name)
            self.wait(1)
        raise ValueError('gateway recovery deadline exceeded; obligation retained')
