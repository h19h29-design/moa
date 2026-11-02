"""A target-specific slot must not reset history or buy time/resources."""
import importlib
import json

import pytest

from moa import research_campaign as rc, research_classifier_control as control


AT = '2026-10-21T20:00:00+09:00'


def api():
    return importlib.import_module('moa.research_classifier_slot')


def fixture(root, monkeypatch, seconds=100, charges_at='2026-10-21T00:00:00+09:00'):
    folder = root / control.FOLDER
    folder.mkdir()
    policy = dict(rolling_budget_days=7, max_train_time_rolling_seconds=28800,
        max_train_time_per_calendar_day_seconds=10800, max_candidate_runs_per_rolling_window=8,
        max_train_time_per_candidate_seconds=2700, max_eval_time_per_candidate_seconds=600)
    rc._write_atomic(root / 'STATE.json', dict(policy_snapshot=policy))
    (root / 'PAUSED').touch()
    rc._write_atomic(folder / 'PLAN.json', dict(run_id=control.RUN, policy=policy,
        stage_sha256={'JOB.json': 'a' * 64}, admission_memory_bytes=7 * 2**30,
        running_memory_bytes=5 * 2**30, execution_host='macbookair'))
    rc._write_atomic(folder / 'STATE.json', dict(status='WAITING_BUDGET', training_issued=False))
    rc._write_atomic(folder / 'INPUT_PREFLIGHT.json', dict(input_items=162, truncated_inputs=0))
    ledger = rc.BudgetLedger(root / 'BUDGET.jsonl')
    for n in range(10):
        ledger.append(dict(at=charges_at, candidate='old-' + str(n),
                           train_seconds=seconds))
    # Source/data validation is exercised by control's tests and live verifier;
    # this fixture isolates the new permission/ledger boundary from private data.
    monkeypatch.setattr(control, 'verify', lambda *_: None)
    return folder, ledger, policy


def remote(monkeypatch, folder, *, memory=8):
    def call(action, plan, *, raw=None, **kwargs):
        if action == 'snapshot':
            return dict(train_admission=memory >= 7, busy=False, running=False,
                resources=dict(available_memory_bytes=memory * 2**30, ac_power=True,
                    idle_seconds=1200, memory_pressure_ok=True, train_allowed=True,
                    disk_free_bytes=6 * 2**30))
        if action == 'launch_train':
            reservation = json.loads(raw)
            assert reservation['candidate'] == control.RUN and reservation['train_seconds'] == 1800
            assert (folder / 'SLOT_CONSUMED.json').is_file()
            assert (folder / 'DISPATCH.json').is_file()
            return dict(pid=123, execution_host='macbookair')
        raise AssertionError('unexpected remote action')
    monkeypatch.setattr(control, '_remote', call)


def test_explicit_slot_is_scoped_and_does_not_mutate_policy_or_ledger(tmp_path, monkeypatch):
    folder, ledger, policy = fixture(tmp_path, monkeypatch)
    before = ledger.path.read_bytes()
    allowance = api().grant(tmp_path, at=AT)
    assert allowance['run_id'] == control.RUN and allowance['additional_candidates'] == 1
    assert allowance['additional_train_seconds'] == 0
    assert ledger.path.read_bytes() == before
    assert rc._read_json(tmp_path / 'STATE.json')['policy_snapshot'] == policy
    assert control.admission(ledger, policy, at=AT)['status'] == 'WAITING_BUDGET'
    assert api().execution_policy(tmp_path, at=AT)['max_candidate_runs_per_rolling_window'] == 11
    assert api().grant(tmp_path, at=AT) == allowance  # Idempotent, not another slot.
    changed = dict(allowance, run_id='another-candidate')
    rc._write_atomic(folder / 'CANDIDATE_ALLOWANCE.json', changed)
    with pytest.raises(ValueError):
        api().execution_policy(tmp_path, at=AT)


def test_resource_wait_does_not_consume_permission_or_reserve_time(tmp_path, monkeypatch):
    folder, ledger, _ = fixture(tmp_path, monkeypatch)
    api().grant(tmp_path, at=AT)
    remote(monkeypatch, folder, memory=6.9)
    before = ledger.path.read_bytes()
    out = api().advance(tmp_path, at=AT)
    assert out['status'] == 'WAITING_BOOK_RESOURCE'
    assert ledger.path.read_bytes() == before
    assert not (folder / 'DISPATCH.json').exists()
    assert not (folder / 'SLOT_CONSUMED.json').exists()


@pytest.mark.parametrize('seconds,charges_at', [(950, '2026-10-21T00:00:00+09:00'),
                                               (2800, '2026-10-20T00:00:00+09:00')])
def test_extra_slot_does_not_extend_daily_or_weekly_time(tmp_path, monkeypatch, seconds, charges_at):
    folder, ledger, _ = fixture(tmp_path, monkeypatch, seconds=seconds, charges_at=charges_at)
    api().grant(tmp_path, at=AT)
    # 9,500 today leaves 1,300; 28,000 in the week leaves 800 (< 1,800).
    before = ledger.path.read_bytes()
    assert api().advance(tmp_path, at=AT)['status'] == 'WAITING_BUDGET'
    assert ledger.path.read_bytes() == before and not (folder / 'DISPATCH.json').exists()


def test_permission_is_consumed_before_single_launch_and_never_rearmed(tmp_path, monkeypatch):
    folder, ledger, _ = fixture(tmp_path, monkeypatch)
    before = ledger.path.read_bytes()
    api().grant(tmp_path, at=AT)
    remote(monkeypatch, folder)
    out = api().advance(tmp_path, at=AT)
    assert out['status'] == 'TRAINING_ISSUED'
    assert ledger.path.read_bytes().startswith(before)
    charged = [e for e in ledger.entries() if e.get('candidate') == control.RUN]
    assert len(charged) == 1 and charged[0]['train_seconds'] == 1800
    assert rc._read_json(folder / 'SLOT_CONSUMED.json')['run_id'] == control.RUN
    with pytest.raises(ValueError):
        api().grant(tmp_path, at=AT)
    # The original pinned controller handles only observation/evaluation now.
    monkeypatch.setattr(control, 'advance', lambda root, **kw: dict(status='OBSERVING_BOOK'))
    assert api().advance(tmp_path, at=AT)['status'] == 'OBSERVING_BOOK'
    assert len([e for e in ledger.entries() if e.get('candidate') == control.RUN]) == 1


def test_uncertain_dispatch_is_not_retried_or_refunded(tmp_path, monkeypatch):
    folder, ledger, _ = fixture(tmp_path, monkeypatch)
    api().grant(tmp_path, at=AT)
    remote(monkeypatch, folder)
    ready = control._remote
    def unavailable(action, *args, **kwargs):
        if action == 'launch_train':
            raise TimeoutError('unknown remote launch outcome')
        return ready(action, *args, **kwargs)
    monkeypatch.setattr(control, '_remote', unavailable)
    result = api().advance(tmp_path, at=AT)
    assert result['status'] == 'RECONCILIATION_REQUIRED'
    assert api().advance(tmp_path, at=AT) == result
    assert len([e for e in ledger.entries() if e.get('candidate') == control.RUN]) == 1


@pytest.mark.parametrize('fault', ['deleted_allowance', 'history_changed', 'charged_without_dispatch'])
def test_lost_permission_or_charge_history_fails_closed(tmp_path, monkeypatch, fault):
    folder, ledger, _ = fixture(tmp_path, monkeypatch)
    api().grant(tmp_path, at=AT)
    if fault == 'deleted_allowance':
        (folder / 'CANDIDATE_ALLOWANCE.json').unlink()
    elif fault == 'history_changed':
        ledger.path.write_bytes(b'')  # Isolated corruption fixture only.
    else:
        ledger.append(dict(at=AT, candidate=control.RUN, train_seconds=1800, kind='reservation'))
    out = api().advance(tmp_path, at=AT)
    assert out['status'] == 'RECONCILIATION_REQUIRED'
    assert not (folder / 'DISPATCH.json').exists()
