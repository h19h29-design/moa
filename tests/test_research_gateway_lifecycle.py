"""Regression: bootout succeeds before launchd removes a SIGTERMed job."""
import importlib
import json
from types import SimpleNamespace

import pytest


def fixture(tmp_path, module='moa.research_gateway_lifecycle'):
    m = importlib.import_module(module)
    tick = [0]
    states = {name: dict(disabled=False, registered=True, running=True,
                        pid=100+i, removed_at=None)
              for i, name in enumerate(m.GATEWAYS)}
    mutations = []

    def run(args, **kw):
        action = args[1]
        for s in states.values():
            if s['removed_at'] is not None and tick[0] >= s['removed_at']:
                s.update(registered=False, running=False, removed_at=None)
        if action == 'print-disabled':
            out = '\n'.join('"%s" => %s' % (n, 'disabled' if s['disabled'] else 'enabled')
                            for n, s in states.items())
            return SimpleNamespace(returncode=0, stdout=out, stderr='')
        name = args[-1].rsplit('/', 1)[-1].removesuffix('.plist')
        assert name in states, 'must never mutate another service'
        s = states[name]
        if action == 'print':
            state = 'SIGTERMed' if s['removed_at'] is not None else 'running'
            return SimpleNamespace(returncode=0 if s['registered'] else 113,
                stdout='state = %s\npid = %d' % (state, s['pid']), stderr='')
        mutations.append((action, name, tick[0]))
        if action == 'disable': s['disabled'] = True
        elif action == 'enable': s['disabled'] = False
        elif action == 'bootout': s.update(running=False, removed_at=tick[0]+5)
        elif action == 'bootstrap':
            if s['registered'] or s['disabled']:
                return SimpleNamespace(returncode=5, stdout='', stderr='Input/output error')
            s.update(registered=True, running=True)
        elif action == 'kickstart':
            # Accepted during removal, but cannot cancel an in-flight bootout.
            if s['removed_at'] is None: s['running'] = True
        else: raise AssertionError(action)
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    def wait(seconds): tick[0] += seconds
    lease = m.GatewayHold(tmp_path/'GATEWAY_RECOVERY.json', run=run,
        sha=lambda p: m.GATEWAYS[p.stem], health=lambda n, pid: states[n]['running'], wait=wait)
    return lease, states, mutations, tick


def test_real_sigtermed_transition_is_awaited_before_held_or_restored(tmp_path):
    lease, states, mutations, tick = fixture(tmp_path)
    lease.pause()
    assert tick[0] >= 5
    assert all(s['disabled'] and not s['registered'] for s in states.values())
    lease.restore()
    assert all(not s['disabled'] and s['registered'] and s['running'] for s in states.values())
    assert lease.read()['status'] == 'RESTORED_VERIFIED'


def test_recovery_reconciles_after_a_job_disappears_mid_restore(tmp_path):
    lease, states, mutations, tick = fixture(tmp_path)
    lease.write(dict(status='PREPARING', recovery_required=True,
        original={n:dict(s) for n,s in states.items()}), once=True)
    for s in states.values(): s.update(disabled=True, running=False, removed_at=5)
    lease.restore()
    assert all(not s['disabled'] and s['registered'] and s['running'] for s in states.values())
    assert len([x for x in mutations if x[0]=='bootstrap']) == 2


def test_pause_timeout_still_restores_and_cannot_rearm(tmp_path):
    lease, states, mutations, tick = fixture(tmp_path)
    def fail(): raise RuntimeError('guardian readiness failure')
    with pytest.raises(RuntimeError): lease.pause(on_journal=fail)
    assert not lease.read()['recovery_required']
    with pytest.raises(ValueError): lease.pause()
    assert not [x for x in mutations if x[0] == 'disable']
