import json
from pathlib import Path

import pytest

from moa import research_cli as cli


def test_status_does_not_start_training(tmp_path, capsys):
    root = tmp_path / 'campaign'
    cli.main(['status', '--root', str(root)])
    printed = json.loads(capsys.readouterr().out)
    assert printed['status'] == 'NOT_STARTED'
    assert printed['training_started'] is False
    assert not (root / 'STATE.json').exists()


def test_start_creates_state_and_is_idempotent_by_refusal(tmp_path, capsys):
    root = tmp_path / 'campaign'
    policy = tmp_path / 'policy.json'
    policy.write_text(json.dumps({'policy_version': 'moa.research.policy.v1',
                                  'campaign_id': 'c1', 'rolling_budget_days': 7,
                                  'max_train_time_rolling_seconds': 100,
                                  'max_train_time_per_calendar_day_seconds': 100,
                                  'max_candidate_runs_per_rolling_window': 2,
                                  'max_train_time_per_candidate_seconds': 50,
                                  'require_min_new_structural_families_after_plateau': 10}))
    cli.main(['start', '--root', str(root), '--policy', str(policy)])
    assert json.loads(capsys.readouterr().out)['status'] == 'DATA_PREPARING'
    with pytest.raises(SystemExit):
        cli.main(['start', '--root', str(root), '--policy', str(policy)])
    assert 'already used' in capsys.readouterr().out


def test_pause_and_resume_change_only_the_marker(tmp_path, capsys):
    root = tmp_path / 'campaign'
    cli.main(['pause', '--root', str(root)])
    assert json.loads(capsys.readouterr().out)['status'] == 'PAUSED_BY_OPERATOR'
    cli.main(['resume', '--root', str(root)])
    assert json.loads(capsys.readouterr().out)['status'] == 'ACTIVE'


def test_report_states_real_quality_is_not_proven(tmp_path, capsys):
    root = tmp_path / 'campaign'
    cli.main(['status', '--root', str(root)])
    capsys.readouterr()
    cli.main(['report', '--root', str(root)])
    text = capsys.readouterr().out
    assert '실제 통신문' in text
    assert '미완료' in text


def test_advance_refuses_when_no_job_and_never_invents_progress(tmp_path, capsys):
    root = tmp_path / 'campaign'
    cli.main(['advance', '--root', str(root)])
    printed = json.loads(capsys.readouterr().out)
    assert printed['status'] == 'NOT_STARTED'
