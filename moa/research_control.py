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
