import json
from pathlib import Path

import pytest

from moa import research_campaign as rc


def policy(tmp_path):
    return {
        'policy_version': 'moa.research.policy.v1',
        'campaign_id': 'moa-test-pilot-01',
        'mode': 'synthetic_research_only',
        'pilot_wall_hours': 72,
        'poll_interval_seconds': 3600,
        'rolling_budget_days': 7,
        'max_train_time_rolling_seconds': 28800,
        'max_train_time_per_calendar_day_seconds': 10800,
        'max_candidate_runs_per_rolling_window': 8,
        'max_train_time_per_candidate_seconds': 2700,
        'max_optimizer_updates_per_candidate': 400,
        'max_concurrent_macbook_jobs': 1,
        'max_consecutive_experiments_without_gain': 3,
        'max_eval_time_per_candidate_seconds': 600,
        'end_of_pilot_action': 'report_and_continue_conditional_watch',
        'scheduler_after_pilot': 'check_for_new_eligible_independent_data',
        'require_min_new_structural_families_after_plateau': 10,
        'allow_rearm_when_verified_human_labels_added': True,
        'allow_rearm_on_same_data_or_cosmetic_renaming': False,
        'allow_budget_reset_by_new_run_id': False,
    }


def test_budget_counts_rolling_window_and_calendar_day(tmp_path):
    ledger = rc.BudgetLedger(tmp_path / 'BUDGET.jsonl')
    ledger.append({'at': '2026-10-20T01:00:00+09:00', 'candidate': 'a', 'train_seconds': 1000})
    ledger.append({'at': '2026-10-20T02:00:00+09:00', 'candidate': 'b', 'train_seconds': 500})
    ledger.append({'at': '2026-10-10T02:00:00+09:00', 'candidate': 'old', 'train_seconds': 9999})
    usage = ledger.usage(now=rc.parse_time('2026-10-20T03:00:00+09:00'), policy=policy(tmp_path))
    assert usage['rolling_seconds'] == 1500
    assert usage['today_seconds'] == 1500
    assert usage['candidates'] == 2
    assert usage['remaining_candidates'] == 6


def test_budget_refuses_over_cap_without_resetting(tmp_path):
    ledger = rc.BudgetLedger(tmp_path / 'BUDGET.jsonl')
    for i in range(8):
        ledger.append({'at': '2026-10-20T0%d:00:00+09:00' % (i + 1), 'candidate': 'c%d' % i, 'train_seconds': 10})
    with pytest.raises(rc.BudgetExhausted):
        ledger.reserve(now=rc.parse_time('2026-10-20T09:00:00+09:00'), policy=policy(tmp_path),
                       candidate='c9', train_seconds=60)


def test_budget_refuses_single_candidate_over_limit(tmp_path):
    ledger = rc.BudgetLedger(tmp_path / 'BUDGET.jsonl')
    with pytest.raises(rc.BudgetExhausted):
        ledger.reserve(now=rc.parse_time('2026-10-20T09:00:00+09:00'), policy=policy(tmp_path),
                       candidate='c1', train_seconds=2701)


def test_same_policy_and_campaign_cannot_restart_after_completion(tmp_path):
    state = tmp_path / 'STATE.json'
    campaign = rc.Campaign(tmp_path)
    campaign.write_state({'campaign_id': 'moa-test-pilot-01', 'status': 'RESEARCH_PLATEAU'})
    with pytest.raises(rc.CampaignRefused):
        campaign.start(campaign_id='moa-test-pilot-01', policy=policy(tmp_path))


def test_pause_file_blocks_advance(tmp_path):
    campaign = rc.Campaign(tmp_path)
    campaign.write_state({'campaign_id': 'moa-test-pilot-01', 'status': 'DATA_PREPARING'})
    campaign.pause()
    assert campaign.advance()['status'] == 'PAUSED_BY_OPERATOR'


def test_rearm_requires_new_independent_families(tmp_path):
    campaign = rc.Campaign(tmp_path)
    campaign.write_state({'campaign_id': 'moa-test-pilot-01', 'status': 'RESEARCH_PLATEAU',
                          'families_used': 24})
    with pytest.raises(rc.CampaignRefused):
        campaign.fresh_campaign(policy=policy(tmp_path), independent_families=30)
    record = campaign.fresh_campaign(policy=policy(tmp_path), independent_families=34)
    assert record['status'] == 'DATA_PREPARING'
    assert record['campaign_id'] != 'moa-test-pilot-01'


def test_state_file_is_atomic_and_private(tmp_path):
    campaign = rc.Campaign(tmp_path)
    campaign.write_state({'campaign_id': 'x', 'status': 'DATA_PREPARING'})
    path = tmp_path / 'STATE.json'
    assert json.loads(path.read_text())['status'] == 'DATA_PREPARING'
    assert (path.stat().st_mode & 0o777) == 0o600


def test_experiment_selector_prefers_untried_hypothesis_and_stops_on_plateau():
    plan = rc.ExperimentSelector()
    assert plan.next_hypothesis([]) == 'E2'
    tried = [{'hypothesis': 'E2', 'result': 'rejected', 'reason': 'insufficient_recall'}]
    assert plan.next_hypothesis(tried) == 'E3'
    dead = [{'hypothesis': h, 'result': 'rejected', 'reason': 'no_semantic_gain'}
            for h in ('E2', 'E3', 'E4')]
    assert plan.next_hypothesis(dead) == 'RESEARCH_PLATEAU'


def test_experiment_selector_never_repeats_a_rejected_hypothesis():
    plan = rc.ExperimentSelector()
    tried = [{'hypothesis': 'E2', 'result': 'rejected', 'reason': 'x'},
             {'hypothesis': 'E3', 'result': 'rejected', 'reason': 'x'},
             {'hypothesis': 'E4', 'result': 'parked', 'reason': 'resource'}]
    # A parked E4 was never rejected, so it is the only unfinished hypothesis.
    assert plan.next_hypothesis(tried) == 'E4'
    tried[-1]['result'] = 'rejected'
    assert plan.next_hypothesis(tried) == 'RESEARCH_PLATEAU'


def test_gate_rejects_candidate_that_cannot_separate_changed_from_preserved():
    rows = [{'case_id': 'a', 'expected': 'changed'}, {'case_id': 'b', 'expected': 'preserved'}]
    predictions = [{'case_id': 'a', 'prediction': 'preserved', 'prediction_origin': 'raw_laya'},
                   {'case_id': 'b', 'prediction': 'preserved', 'prediction_origin': 'raw_laya'}]
    gate = rc.tiny_gate(rows, predictions, baseline_accuracy=0.5, minimum_recall=0.8)
    assert gate['passed'] is False
    assert gate['changed_recall'] == 0.0


def test_gate_ignores_forced_insufficient_as_model_answer():
    rows = [{'case_id': 'a', 'expected': 'changed'}]
    with pytest.raises(ValueError):
        rc.tiny_gate(rows, [{'case_id': 'a', 'prediction': 'insufficient',
                             'prediction_origin': 'system_forced'}], 0.0, 0.8)


def test_budget_seconds_are_never_negative_even_with_pending_reservation(tmp_path):
    ledger = rc.BudgetLedger(tmp_path / 'BUDGET.jsonl')
    ledger.reserve(now=rc.parse_time('2026-10-20T09:00:00+09:00'), policy=policy(tmp_path),
                   candidate='c1', train_seconds=2600)
    usage = ledger.usage(now=rc.parse_time('2026-10-20T09:05:00+09:00'), policy=policy(tmp_path))
    assert usage['remaining_rolling_seconds'] >= 0
    assert usage['remaining_today_seconds'] >= 0
