"""Same eight-update checkpoint, one dispatch, immutable earlier failed controls."""
import importlib
import pytest
from moa import research_classifier_resume8_launchd as prior, research_campaign as rc
from test_research_classifier_resume8_launchd import fixture as old_fixture, AT


def test_repaired_control_preserves_seals_budget_and_dispatches_only_once(tmp_path, monkeypatch):
    p, previous, ledger, calls, snap = old_fixture(tmp_path, monkeypatch)
    prior.block_unissued(tmp_path, at=AT)
    prior.authorize(tmp_path, at=AT)
    q = p / prior.FOLDER
    state = rc._read_json(q/'STATE.json')
    rc._write_atomic(q/'STATE.json', dict(state, status='RECONCILIATION_REQUIRED',
        reason='gateway_lifecycle_preflight_failed_before_training_dispatch'))
    rc._write_atomic(q/'PREFLIGHT_STOP.json', dict(training_issued=False, evaluation_issued=False,
        source_integrity_verified=True, ledger_unchanged=True, services_restored_separately=True))
    preserved = {f:f.read_bytes() for f in q.glob('*.json')}
    budget = ledger.path.read_bytes()
    api = importlib.import_module('moa.research_classifier_resume8_repaired').controller()
    remote = prior._remote
    def fixed(action, *args, **kwargs):
        if action == 'launch_resume': calls.append(action); return dict(pid=199)
        value = remote(action, *args, **kwargs)
        if action == 'probe': value['unissued_book_sha256'] = dict(
            authorization=prior.a.sha(q/'AUTHORIZATION.json'), gateway_recovery='a'*64)
        return value
    monkeypatch.setattr(api, '_remote', fixed)
    auth = api.authorize(tmp_path, at=AT)
    assert auth['prior_updates'] == 8
    assert auth['additional_candidates'] == auth['additional_train_seconds'] == 0
    assert auth['remaining_train_seconds'] == 1748.942840917036
    assert api.advance(tmp_path, at=AT)['status'] == 'RESUME_ISSUED'
    snap['train_result'] = dict(status='stopped', updates=12)
    assert api.advance(tmp_path, at=AT)['status'] == 'RECONCILIATION_REQUIRED'
    api.advance(tmp_path, at=AT)
    assert calls.count('launch_resume') == 1
    assert ledger.path.read_bytes() == budget
    assert all(f.read_bytes() == value for f,value in preserved.items())
    with pytest.raises(ValueError): api.authorize(tmp_path, at=AT)


def test_cannot_replace_any_issued_old_control(tmp_path, monkeypatch):
    p, _, _, calls, _ = old_fixture(tmp_path, monkeypatch)
    prior.block_unissued(tmp_path, at=AT); prior.authorize(tmp_path, at=AT)
    q=p/prior.FOLDER
    rc._write_atomic(q/'DISPATCH.json', dict(at=AT))
    api=importlib.import_module('moa.research_classifier_resume8_repaired').controller()
    with pytest.raises(ValueError): api.authorize(tmp_path, at=AT)
    assert not (p/api.FOLDER/'AUTHORIZATION.json').exists()
