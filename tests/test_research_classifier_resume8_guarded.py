"""Safety corrections preserve the unissued seal, not reset a spent permission."""
import importlib
import pytest
from moa import research_campaign as rc, research_classifier_resume8 as prior
from test_research_classifier_resume8 import fixture as eight_fixture, AT


def api():return importlib.import_module('moa.research_classifier_resume8_guarded')


def fixture(tmp_path,monkeypatch):
    p,previous,ledger,stopped,calls,snap=eight_fixture(tmp_path,monkeypatch)
    prior.authorize(tmp_path,at=AT)
    remote=prior._remote
    def corrected(action,*args,**kwargs):
        value=remote(action,*args,**kwargs)
        if action=='probe':value['unissued_book_sha256']={'authorization':prior.a.sha(p/prior.FOLDER/'AUTHORIZATION.json')}
        return value
    monkeypatch.setattr(api(),'_remote',corrected)
    return p,previous,ledger,calls,snap


def test_unissued_review_block_is_append_only_and_budget_neutral(tmp_path,monkeypatch):
    p,previous,ledger,calls,_=fixture(tmp_path,monkeypatch)
    q=p/prior.FOLDER
    originals={x:x.read_bytes() for x in list(p.glob('*.json'))+list(previous.glob('*.json'))}
    auth=(q/'AUTHORIZATION.json').read_bytes();budget=ledger.path.read_bytes()
    state=(q/'STATE.json').read_bytes()
    api().block_unissued(tmp_path,at=AT)
    assert rc._read_json(q/'STATE.json')['status']=='RECONCILIATION_REQUIRED'
    assert rc._read_json(q/'REVIEW_BLOCKER.json')['previous_state_sha256']==prior.hashlib.sha256(state).hexdigest()
    assert (q/'AUTHORIZATION.json').read_bytes()==auth and ledger.path.read_bytes()==budget
    assert all(x.read_bytes()==raw for x,raw in originals.items())
    assert 'launch_resume' not in calls
    new=api().authorize(tmp_path,at=AT)
    assert new['prior_updates']==8 and new['additional_candidates']==new['additional_train_seconds']==0
    assert new['remaining_train_seconds']==1748.942840917036
    assert ledger.path.read_bytes()==budget


def test_already_issued_overlay_cannot_be_replaced(tmp_path,monkeypatch):
    p,_,_,calls,_=fixture(tmp_path,monkeypatch)
    rc._write_atomic(p/prior.FOLDER/'DISPATCH.json',{'at':AT})
    with pytest.raises(ValueError):api().block_unissued(tmp_path,at=AT)
    assert not (p/api().FOLDER/'AUTHORIZATION.json').exists()
    assert 'launch_resume' not in calls


def test_corrected_binding_requires_original_review_block_and_stays_once_only(tmp_path,monkeypatch):
    p,_,ledger,calls,snap=fixture(tmp_path,monkeypatch)
    with pytest.raises((ValueError,FileNotFoundError)):api().authorize(tmp_path,at=AT)
    api().block_unissued(tmp_path,at=AT);api().authorize(tmp_path,at=AT)
    # The test remote uses the old fixture's marker assertion; replace launch only.
    remote=api()._remote
    def launch(action,*args,**kw):
        if action=='launch_resume':calls.append(action);return {'pid':99}
        return remote(action,*args,**kw)
    monkeypatch.setattr(api(),'_remote',launch)
    budget=ledger.path.read_bytes()
    assert api().advance(tmp_path,at=AT)['status']=='RESUME_ISSUED'
    snap['train_result']={'status':'stopped','updates':10}
    assert api().advance(tmp_path,at=AT)['status']=='RECONCILIATION_REQUIRED'
    api().advance(tmp_path,at=AT)
    assert calls.count('launch_resume')==1 and ledger.path.read_bytes()==budget
    with pytest.raises(ValueError):api().authorize(tmp_path,at=AT)
