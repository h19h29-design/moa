import copy

import pytest

from moa.research_control import quality_action


def snapshot():
    return {'run_id': 'quality-new-01', 'past_run_ids': ['old-r2a', 'old-r2b'],
            'prepared': True, 'online': True, 'resource_ready': True,
            'rights': 'self_authored_synthetic_only', 'lineage_overlap': 0, 'gold_validated': True,
            'input_probe_complete': True, 'input_probe_added_value': False,
            'candidate_status': None, 'candidate_count': 0, 'updates': 0, 'seconds': 0,
            'permit': {'candidates': 1, 'max_updates': 40, 'max_seconds': 600},
            'checkpoint_verified': False, 'checkpoint_reloaded': False,
            'evaluation_frozen': False, 'evaluation_complete': False}


def test_frozen_input_probe_without_gain_never_starts_an_optimizer():
    x = snapshot()
    assert quality_action(x) == 'freeze_existing_models'
    x['evaluation_frozen'] = True
    assert quality_action(x) == 'evaluate_existing_only'
    x['evaluation_complete'] = True
    assert quality_action(x) == 'complete_no_new_training'


def test_no_hidden_training_from_missing_probe_proof_or_permissions():
    x = snapshot(); x['input_probe_complete'] = False
    assert quality_action(x) == 'run_fixed_weight_probe'
    x = snapshot(); x.update(input_probe_added_value=True)
    assert quality_action(x) == 'start_one_bounded_candidate'
    for key, value in [('rights', 'unconfirmed'), ('gold_validated', False), ('lineage_overlap', 1)]:
        bad = copy.deepcopy(x); bad[key] = value
        assert quality_action(bad) == 'wait_eligible_data'
    for permit in ({}, {'candidates': 2, 'max_updates': 40, 'max_seconds': 600},
                   {'candidates': 1, 'max_updates': 400, 'max_seconds': 2700}):
        bad = copy.deepcopy(x); bad['permit'] = permit
        assert quality_action(bad) == 'stop_invalid_budget'


@pytest.mark.parametrize('status', ['failed', 'unknown', 'aborted', True])
def test_ambiguous_interruptions_or_reused_run_ids_cannot_restart(status):
    x = snapshot(); x.update(candidate_status=status, input_probe_added_value=True)
    assert quality_action(x) == 'stop_manual_reconciliation'
    x = snapshot(); x['run_id'] = 'old-r2a'
    assert quality_action(x) == 'stop_reused_run_id'


def test_resume_is_only_verified_saved_cursor_under_cumulative_limits():
    x = snapshot(); x.update(input_probe_added_value=True, candidate_status='parked', candidate_count=1)
    assert quality_action(x) == 'stop_manual_reconciliation'
    x['checkpoint_verified'] = True
    assert quality_action(x) == 'resume_one_saved_candidate'
    x['updates'] = 40
    assert quality_action(x) == 'stop_budget_no_restart'
    x['updates'] = 39; x['seconds'] = 600
    assert quality_action(x) == 'stop_budget_no_restart'
    x.update(candidate_status='saved', checkpoint_reloaded=False)
    assert quality_action(x) == 'reload_candidate_only'
