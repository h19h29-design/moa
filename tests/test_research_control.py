from moa import research_control as c
import pytest


def test_failed_tiny_gate_is_terminal_even_with_unused_r2b_budget():
    snapshot={'prepared':True,'online':True,'diagnostic_status':'saved','diagnostic_reloaded':True,
              'gate_passed':False,'r2b_status':None,'evaluation_complete':False}
    assert c.next_action(snapshot)=='evaluate_existing_only'
    snapshot['evaluation_complete']=True
    assert c.next_action(snapshot)=='complete_no_new_training'


def test_offline_wait_existing_running_unknown_failure_and_explicit_resume():
    x={'prepared':True,'online':False}
    assert c.next_action(x)=='wait_online'
    x.update(online=True,diagnostic_status='running')
    assert c.next_action(x)=='observe_only'
    x['diagnostic_status']='failed'
    assert c.next_action(x)=='stop_manual_reconciliation'
    x.update(diagnostic_status='parked',checkpoint_verified=False)
    assert c.next_action(x)=='stop_manual_reconciliation'
    x.update(checkpoint_verified=True,resource_ready=True)
    assert c.next_action(x)=='resume_saved_diagnostic'


def test_new_training_requires_passing_bound_diagnostic_and_readiness_not_operating_override():
    x={'prepared':False,'online':True}
    assert c.next_action(x)=='wait_data'
    x.update(prepared=True,diagnostic_status='saved',diagnostic_reloaded=True,gate_passed=True,
             resource_ready=False,r2b_status=None)
    assert c.next_action(x)=='wait_resources'
    x['resource_ready']=True
    assert c.next_action(x)=='start_one_r2b'
    x['r2b_status']='saved';x['r2b_reloaded']=False
    assert c.next_action(x)=='reload_r2b'
    x['r2b_reloaded']=True
    assert c.next_action(x)=='freeze_and_evaluate'


@pytest.mark.parametrize('field',['diagnostic_status','r2b_status'])
@pytest.mark.parametrize('value',['aborted','complete','garbage',True])
def test_unrecognized_state_never_starts_a_new_candidate(field,value):
    x={'prepared':True,'online':True,'diagnostic_status':'saved','diagnostic_reloaded':True,
       'gate_passed':True,'r2b_status':None,'resource_ready':True}
    x[field]=value
    assert c.next_action(x)=='stop_manual_reconciliation'
