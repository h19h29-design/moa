"""Pure bounded controller decisions, independent of transport and old permits."""


def next_action(s):
    if not s.get('prepared'):return 'wait_data'
    if not s.get('online'):return 'wait_online'
    diag=s.get('diagnostic_status');full=s.get('r2b_status')
    if any(p not in (None,'running','failed','unknown','parked','saved') for p in (diag,full)):
        return 'stop_manual_reconciliation'
    if 'running' in (diag,full):return 'observe_only'
    if any(p in ('failed','unknown') for p in (diag,full)):return 'stop_manual_reconciliation'
    if diag=='parked':
        if not s.get('checkpoint_verified'):return 'stop_manual_reconciliation'
        return 'resume_saved_diagnostic' if s.get('resource_ready') else 'wait_resources'
    if diag is None:return 'start_diagnostic' if s.get('resource_ready') else 'wait_resources'
    if not s.get('diagnostic_reloaded'):return 'reload_diagnostic'
    if not s.get('gate_passed'):
        return 'complete_no_new_training' if s.get('evaluation_complete') else 'evaluate_existing_only'
    if full=='parked':
        if not s.get('checkpoint_verified'):return 'stop_manual_reconciliation'
        return 'resume_saved_r2b' if s.get('resource_ready') else 'wait_resources'
    if full is None:return 'start_one_r2b' if s.get('resource_ready') else 'wait_resources'
    if not s.get('r2b_reloaded'):return 'reload_r2b'
    return 'complete_no_new_training' if s.get('evaluation_complete') else 'freeze_and_evaluate'


def quality_action(s):
    """New permit, not a reset/reinterpretation of the closed R2-A/R2-B runs.

    Transport executes these decisions under the existing device lock and
    immutable request journal. Unknown interruptions are never automatic retry.
    A new trial is unnecessary when frozen-input evidence adds no value.
    """
    if not s.get('run_id') or s['run_id'] in s.get('past_run_ids', []):
        return 'stop_reused_run_id'
    if (not s.get('prepared') or s.get('rights') != 'self_authored_synthetic_only' or
            s.get('gold_validated') is not True or s.get('lineage_overlap') != 0):
        return 'wait_eligible_data'
    status = s.get('candidate_status')
    if status not in (None, 'running', 'saved', 'parked'):
        return 'stop_manual_reconciliation'
    if status == 'running': return 'observe_only'
    if not s.get('online'): return 'wait_online'
    if not s.get('input_probe_complete'):
        return 'run_fixed_weight_probe' if s.get('resource_ready') else 'wait_resources'
    if not s.get('input_probe_added_value') and status is None:
        if s.get('evaluation_complete'): return 'complete_no_new_training'
        return 'evaluate_existing_only' if s.get('evaluation_frozen') else 'freeze_existing_models'
    permit = s.get('permit', {})
    if (permit != {'candidates': 1, 'max_updates': 40, 'max_seconds': 600} or
            s.get('candidate_count') not in (0, 1)):
        return 'stop_invalid_budget'
    if status == 'saved':
        if not s.get('checkpoint_verified'): return 'stop_manual_reconciliation'
        if not s.get('checkpoint_reloaded'): return 'reload_candidate_only'
        return 'complete_no_new_training' if s.get('evaluation_complete') else 'freeze_and_evaluate'
    if (not isinstance(s.get('updates'), int) or s['updates'] < 0 or
            not isinstance(s.get('seconds'), (int, float)) or not 0 <= s['seconds'] < 600 or s['updates'] >= 40):
        return 'stop_budget_no_restart'
    if status == 'parked':
        if not s.get('checkpoint_verified') or s.get('candidate_count') != 1:
            return 'stop_manual_reconciliation'
        return 'resume_one_saved_candidate' if s.get('resource_ready') else 'wait_resources'
    if s.get('candidate_count') != 0 or s['updates'] != 0 or s['seconds'] != 0:
        return 'stop_manual_reconciliation'
    return 'start_one_bounded_candidate' if s.get('resource_ready') else 'wait_resources'
