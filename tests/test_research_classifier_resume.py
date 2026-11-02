"""One explicit zero-update continuation cannot buy another slot or time."""
import importlib
import hashlib
import json

import pytest

from moa import research_campaign as rc, research_classifier_control as control
from moa import research_classifier_slot as slot
from moa import r2_research as research

AT = '2026-10-22T09:00:00+09:00'


def api():
    return importlib.import_module('moa.research_classifier_resume')


def fixture(root, monkeypatch):
    p = root / control.FOLDER
    p.mkdir()
    (root / 'PAUSED').touch()
    policy = dict(max_train_time_rolling_seconds=28800,
                  max_train_time_per_calendar_day_seconds=10800)
    rc._write_atomic(root / 'STATE.json', dict(policy_snapshot=policy))
    rc._write_atomic(p / 'PLAN.json', dict(run_id=control.RUN, policy=policy,
        stage_sha256={'JOB.json': 'a' * 64}, baseline_path='baseline.json'))
    rc._write_atomic(p / 'STATE.json', dict(status='RECONCILIATION_REQUIRED',
        training_issued=True, training_updates=0, trained=False, evaluated=False))
    for name in ('CANDIDATE_ALLOWANCE.json', 'SLOT_CONSUMED.json', 'DISPATCH.json'):
        rc._write_atomic(p / name, dict(run_id=control.RUN))
    reservation = dict(candidate=control.RUN, train_seconds=1800, kind='reservation',
                       at='2026-10-22T08:00:42+09:00')
    rc._write_atomic(p / 'RESERVATION.json', reservation)
    ledger = rc.BudgetLedger(root / 'BUDGET.jsonl')
    ledger.append(reservation)
    stopped = dict(run_id=control.RUN, status='stopped', stop_reason='resource_or_reservation_day_guard',
        updates=0, seconds=9.704770208336413, encoder_unchanged=True,
        job_sha256='a' * 64, execution_host='macbookair', execution_device='mps',
        checkpoint_name='checkpoint-0000-final_or_safe_stop', checkpoint_kind='final_or_safe_stop',
        head_sha256='b' * 64, training_state_sha256='c' * 64, system_forced=False)
    rc._write_atomic(p / 'TRAIN_STOP_RESULT.json', stopped)
    monkeypatch.setattr(control, 'verify', lambda *_: None)
    monkeypatch.setattr(slot, '_verify_allowance', lambda *_: {})
    calls = []
    snap = dict(resources=dict(available_memory_bytes=8 * 2**30, train_allowed=True,
        ac_power=True, idle_seconds=1200, memory_pressure_ok=True, disk_free_bytes=6 * 2**30),
        busy=False, running=False, gateways_stopped=True, train_admission=True,
        evaluation_admission=True)
    def remote(action, plan, auth=None, *, raw=None, **kw):
        calls.append(action)
        if action == 'probe':
            book_stop = dict(stopped, records=[])
            return dict(stop=book_stop, classifier_sha256='d' * 64,
                stop_sha256=hashlib.sha256(research.canonical(book_stop)).hexdigest())
        if action == 'stage': return dict(status='STAGED')
        if action == 'selftest': return dict(returncode=0, tests=3)
        if action == 'snapshot': return dict(snap)
        if action in ('launch_resume', 'launch_evaluate'):
            path = p / api().FOLDER / ('DISPATCH.json' if action == 'launch_resume' else 'EVAL_DISPATCH.json')
            assert path.is_file(), 'dispatch must be durable before the remote command'
            return dict(pid=42, execution_host='macbookair')
        raise AssertionError(action)
    monkeypatch.setattr(api(), '_remote', remote)
    return p, ledger, stopped, calls, snap


def test_authorization_preserves_every_original_and_subtracts_spent_time(tmp_path, monkeypatch):
    p, ledger, stopped, calls, _ = fixture(tmp_path, monkeypatch)
    originals = {n: (p / n).read_bytes() for n in
        ('PLAN.json', 'STATE.json', 'CANDIDATE_ALLOWANCE.json', 'DISPATCH.json', 'RESERVATION.json')}
    before = ledger.path.read_bytes()
    auth = api().authorize(tmp_path, at=AT)
    assert auth['maximum_resumes'] == 1
    assert auth['additional_candidates'] == auth['additional_train_seconds'] == 0
    assert auth['remaining_train_seconds'] == pytest.approx(1800 - stopped['seconds'])
    assert auth['evaluation_maximum_seconds'] == 600
    assert len(auth['gateways']) == 2 and auth['execution_host'] == 'macbookair'
    assert ledger.path.read_bytes() == before
    assert all((p / n).read_bytes() == raw for n, raw in originals.items())
    assert api().authorize(tmp_path, at=AT) == auth
    assert calls.count('probe') == 1


@pytest.mark.parametrize('fault', ['nonzero', 'unknown_stop', 'wrong_job', 'next_day', 'duplicate_charge'])
def test_only_original_known_zero_boundary_same_day_is_authorizable(tmp_path, monkeypatch, fault):
    p, ledger, stopped, _, _ = fixture(tmp_path, monkeypatch)
    when = AT
    if fault == 'nonzero': stopped['updates'] = 1
    if fault == 'unknown_stop': stopped['stop_reason'] = 'unknown'
    if fault == 'wrong_job': stopped['job_sha256'] = 'f' * 64
    if fault == 'next_day': when = '2026-10-23T00:00:01+09:00'
    if fault == 'duplicate_charge': ledger.append(dict(ledger.entries()[0]))
    rc._write_atomic(p / 'TRAIN_STOP_RESULT.json', stopped)
    with pytest.raises(ValueError): api().authorize(tmp_path, at=when)
    assert not (p / api().FOLDER / 'AUTHORIZATION.json').exists()


def test_low_memory_does_not_consume_resume_or_modify_reservation(tmp_path, monkeypatch):
    p, ledger, _, calls, snap = fixture(tmp_path, monkeypatch)
    api().authorize(tmp_path, at=AT)
    snap.update(train_admission=False)
    snap['resources']['available_memory_bytes'] = int(6.9 * 2**30)
    before = ledger.path.read_bytes()
    out = api().advance(tmp_path, at=AT)
    assert out['status'] == 'WAITING_BOOK_RESOURCE'
    assert not (p / api().FOLDER / 'DISPATCH.json').exists()
    assert 'launch_resume' not in calls and ledger.path.read_bytes() == before


def test_one_resume_then_new_stop_is_terminal_without_relaunch(tmp_path, monkeypatch):
    p, ledger, _, calls, snap = fixture(tmp_path, monkeypatch)
    api().authorize(tmp_path, at=AT)
    before = ledger.path.read_bytes()
    assert api().advance(tmp_path, at=AT)['status'] == 'RESUME_ISSUED'
    snap['train_result'] = dict(status='stopped', updates=0)
    result = api().advance(tmp_path, at=AT)
    assert result['status'] == 'RECONCILIATION_REQUIRED'
    assert api().advance(tmp_path, at=AT) == result
    assert calls.count('launch_resume') == 1 and ledger.path.read_bytes() == before
    with pytest.raises(ValueError): api().authorize(tmp_path, at=AT)


def test_uncertain_dispatch_is_never_reissued(tmp_path, monkeypatch):
    p, ledger, _, calls, _ = fixture(tmp_path, monkeypatch)
    api().authorize(tmp_path, at=AT)
    ready = api()._remote
    def uncertain(action, *args, **kw):
        if action == 'launch_resume': raise TimeoutError('unknown dispatch')
        return ready(action, *args, **kw)
    monkeypatch.setattr(api(), '_remote', uncertain)
    before = ledger.path.read_bytes()
    out = api().advance(tmp_path, at=AT)
    assert out['status'] == 'RECONCILIATION_REQUIRED'
    assert api().advance(tmp_path, at=AT) == out
    assert (p / api().FOLDER / 'DISPATCH.json').exists()
    assert ledger.path.read_bytes() == before


@pytest.mark.parametrize('fault', ['ledger', 'original_state', 'auth', 'day', 'gateways_running'])
def test_changed_history_day_or_unstopped_gateways_never_launch(tmp_path, monkeypatch, fault):
    p, ledger, _, calls, snap = fixture(tmp_path, monkeypatch)
    api().authorize(tmp_path, at=AT)
    when = AT
    if fault == 'ledger': ledger.append(dict(ledger.entries()[0]))
    if fault == 'original_state': rc._write_atomic(p / 'STATE.json', dict(status='WAITING'))
    if fault == 'auth':
        auth = rc._read_json(p / api().FOLDER / 'AUTHORIZATION.json')
        rc._write_atomic(p / api().FOLDER / 'AUTHORIZATION.json', dict(auth, remaining_train_seconds=1800))
    if fault == 'day': when = '2026-10-23T00:00:01+09:00'
    if fault == 'gateways_running': snap.update(gateways_stopped=False)
    out = api().advance(tmp_path, at=when)
    assert out['status'] in ('RECONCILIATION_REQUIRED', 'WAITING_BOOK_RESOURCE')
    assert 'launch_resume' not in calls


def test_saved_resume_is_separately_reloaded_then_compared_without_retraining(tmp_path, monkeypatch):
    p, ledger, _, calls, snap = fixture(tmp_path, monkeypatch)
    auth = api().authorize(tmp_path, at=AT)
    before = ledger.path.read_bytes()
    api().advance(tmp_path, at=AT)
    key = rc._read_json(p / api().FOLDER / 'STATE.json')['authorization_sha256']
    snap['train_result'] = dict(status='saved', updates=56, seconds=30.,
        cumulative_train_seconds=auth['prior_train_seconds']+30., resumed_exact=True,
        encoder_unchanged=True, job_sha256='a'*64, authorization_sha256=key,
        execution_host='macbookair', execution_device='mps', head_sha256='e'*64)
    assert api().advance(tmp_path, at=AT)['status'] == 'EVALUATION_ISSUED'
    records = [dict(case_id='n', prediction='preserved', probabilities=dict(preserved=.9, changed=.1),
                    system_forced=False),
               dict(case_id='e', prediction='changed', probabilities=dict(preserved=.2, changed=.8),
                    system_forced=False)]
    rc._write_atomic(p / 'GOLD.json', {'n': 'preserved', 'e': 'changed'})
    rc._write_atomic(tmp_path / 'baseline.json', dict(models={'r1': {'average': {'accuracy': .5}}}))
    snap['evaluation'] = dict(status='complete', reload_exact=True, seconds=2., head_sha256='e'*64,
        authorization_sha256=key, execution_device='mps', system_forced=False, records=records)
    out = api().advance(tmp_path, at=AT)
    assert out['status'] == 'COMPLETE'
    assert out['comparison']['models']['fixed_classifier']['accuracy'] == 1.
    assert out['comparison']['models']['fixed_classifier']['normal_false_positive_rate'] == 0.
    assert out['comparison']['baseline_observations_per_item'] == 2
    assert out['comparison']['classifier_observations_per_item'] == 1
    assert calls.count('launch_resume') == calls.count('launch_evaluate') == 1
    assert ledger.path.read_bytes() == before


def test_book_durable_dispatch_also_refuses_second_launch(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    from types import SimpleNamespace
    spec = importlib.util.spec_from_file_location('book_explicit_resume',
        Path(__file__).parents[1] / 'scripts/moa-research-book-classifier-resume.py')
    book = importlib.util.module_from_spec(spec); spec.loader.exec_module(book)
    monkeypatch.setattr(book, 'ROOT', tmp_path)
    monkeypatch.setattr(book, 'context', lambda *_: (SimpleNamespace(canonical=research.canonical), {}, {}))
    monkeypatch.setattr(book, 'snapshot', lambda *_: dict(gateways_stopped=True, train_admission=True))
    monkeypatch.setattr(book, 'incoming', lambda name: tmp_path / name)
    monkeypatch.setattr(book, 'output', lambda: tmp_path / 'result')
    launched = []
    def popen(*args, **kw):
        launched.append(args)
        assert (tmp_path / 'result/TRAIN_DISPATCH.json').is_file()
        return SimpleNamespace(pid=42)
    monkeypatch.setattr(book.subprocess, 'Popen', popen)
    payload = dict(authorization_sha256='f'*64)
    assert book.launch(payload, True)['pid'] == 42
    with pytest.raises(FileExistsError): book.launch(payload, True)
    assert len(launched) == 1
