"""Only the two approved gateway labels may be held, with durable restoration."""
import importlib
import re
from types import SimpleNamespace
import pytest


def api(): return importlib.import_module('moa.research_gateway_hold')


@pytest.fixture(autouse=True,params=['hold','hold_launchd'])
def run_both_gateway_versions(request,monkeypatch):
    monkeypatch.setitem(globals(),'api',lambda:importlib.import_module('moa.research_gateway_'+request.param))


def fixture(tmp_path):
    m=api(); states={name:dict(disabled=False,registered=True,running=True,pid=100+i)
        for i,name in enumerate(m.GATEWAYS)}
    calls=[]; bad=set()
    def run(args, **kw):
        calls.append(args)
        command=args[1]
        out=''; code=0
        if command=='print-disabled':
            out='\n'.join('"%s" => %s'%(name,str(s['disabled']).lower()) for name,s in states.items())
        elif command=='print':
            s=states[args[2].split('/')[-1]]
            code=0 if s['registered'] else 113
            out='state = running\npid = %d'%s['pid'] if s['running'] else 'state = not running'
        else:
            name=(args[2].rsplit('/',1)[-1].removesuffix('.plist'))
            if command=='bootstrap': name=args[3].rsplit('/',1)[-1].removesuffix('.plist')
            assert name in states, 'unrelated service mutation'
            s=states[name]
            if command=='disable': s['disabled']=True
            elif command=='enable': s['disabled']=False
            elif command=='bootout': s.update(registered=False,running=False)
            elif command in ('bootstrap','kickstart'):
                if name in bad: code=5
                else: s.update(registered=True,running=True)
            else: raise AssertionError(args)
        return SimpleNamespace(returncode=code,stdout=out,stderr='')
    lease=m.GatewayHold(tmp_path/'RECOVERY.json',run=run,
        sha=lambda path:m.GATEWAYS[path.stem],health=lambda name,pid:states[name]['running'],wait=lambda _:None)
    return lease,states,calls,bad


def test_two_exact_labels_are_disabled_before_bootout_and_original_state_restored(tmp_path):
    lease,states,calls,_=fixture(tmp_path)
    pending=[]
    lease.pause(on_journal=lambda:pending.append(lease.read()['recovery_required']))
    assert pending==[True]
    assert all(s['disabled'] and not s['registered'] for s in states.values())
    mut=[x for x in calls if x[1] not in ('print','print-disabled')]
    assert [x[1] for x in mut]==['disable','disable','bootout','bootout']
    lease.restore()
    assert all(not s['disabled'] and s['registered'] and s['running'] for s in states.values())
    assert lease.read()['status']=='RESTORED_VERIFIED' and not lease.read()['recovery_required']
    before=len([x for x in calls if x[1] in ('bootstrap','kickstart')])
    lease.restore()
    assert len([x for x in calls if x[1] in ('bootstrap','kickstart')])==before


def test_changed_original_disabled_state_or_config_refuses_without_mutation(tmp_path):
    lease,states,calls,_=fixture(tmp_path)
    states[next(iter(states))]['disabled']=True
    with pytest.raises(ValueError): lease.pause()
    assert not lease.path.exists()
    assert not any(x[1] in ('disable','bootout') for x in calls)


def test_failed_pause_guard_start_restores_originals_before_propagating(tmp_path):
    lease,states,_,_=fixture(tmp_path)
    def fail(): raise RuntimeError('guard did not start')
    with pytest.raises(RuntimeError): lease.pause(on_journal=fail)
    assert all(not s['disabled'] and s['registered'] for s in states.values())
    assert lease.read()['status']=='RESTORED_VERIFIED'


def test_failed_recovery_retains_durable_recovery_obligation(tmp_path):
    lease,states,_,bad=fixture(tmp_path)
    lease.pause(); bad.add(next(iter(states)))
    with pytest.raises(ValueError): lease.restore()
    assert lease.read()['recovery_required'] is True
    assert lease.read()['status']=='RECOVERY_REQUIRED'
