"""Evaluation recovery must never re-train or depend on copying model weights."""
import base64
import importlib
import importlib.util
import io
import json
import subprocess
from pathlib import Path

import pytest

from moa import research_alignment as a, research_campaign as rc
from test_research_book_resume import prepared, remote_ready, completed_book


def script(name):
    path = a.ROOT / 'scripts' / name
    assert path.exists(), 'small, hash-attested result collection is missing'
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def api():
    assert importlib.util.find_spec('moa.research_book_results'), 'evaluation-only recovery is missing'
    return importlib.import_module('moa.research_book_results')


def test_old_bulk_collection_reproduces_timeout_on_a_bounded_result_channel(tmp_path, monkeypatch):
    old = script('moa-research-book-control.py')
    run = tmp_path / 'research' / old.RUN
    cp = run / 'checkpoint-r1-0056'
    cp.mkdir(parents=True)
    (cp / 'model.safetensors').write_bytes(b'x' * 131072)
    (run / 'TRAIN_RESULT.json').write_text(json.dumps({'checkpoint_name': cp.name}))
    (run / 'TRAINING_CONTRACT.json').write_text('{}')
    monkeypatch.setattr(old, 'ROOT', tmp_path)
    class Channel(io.BytesIO):
        def write(self, data):
            if self.tell() + len(data) > 65536:
                raise subprocess.TimeoutExpired('synthetic bounded result channel', 180)
            return super().write(data)
    monkeypatch.setattr(old.sys, 'stdout', type('Output', (), {'buffer': Channel()})())
    with pytest.raises(subprocess.TimeoutExpired):
        old.collect()


def test_metadata_collector_hashes_weights_but_never_transmits_them(tmp_path, monkeypatch):
    new = script('moa-research-book-results.py')
    run = tmp_path / 'research' / new.RUN
    cp = run / 'checkpoint-r1-0056'
    cp.mkdir(parents=True)
    (cp / 'model.safetensors').write_bytes(b'x' * 131072)
    (cp / 'training-state.pt').write_bytes(b'optimizer')
    saved = {'checkpoint_name': cp.name, 'sha256': {
        'model.safetensors': a.sha(cp / 'model.safetensors'),
        'training-state.pt': a.sha(cp / 'training-state.pt')}}
    (run / 'TRAIN_RESULT.json').write_text(json.dumps(saved))
    (run / 'TRAINING_CONTRACT.json').write_text('{}')
    monkeypatch.setattr(new, 'ROOT', tmp_path)
    result = new.collect()
    assert len(json.dumps(result)) < 4096
    assert set(result['files']) == {'TRAIN_RESULT.json', 'TRAINING_CONTRACT.json'}
    assert result['checkpoint']['model.safetensors']['bytes'] == 131072
    assert result['checkpoint']['model.safetensors']['sha256'] == '15601535eca4a38b7e31ad6494861121cb9f84ccf55d4beb6a707d4f7a87813d'
    raw = base64.b64decode(result['files']['TRAIN_RESULT.json']['base64'])
    assert raw == (run / 'TRAIN_RESULT.json').read_bytes()


def recovery_case(root, monkeypatch):
    b, m, mini, before = prepared(root, monkeypatch)
    snap = remote_ready(monkeypatch, b)
    monkeypatch.setattr(b, '_launch', lambda *args: 4321)
    rc.Campaign(root).resume()
    m.advance(root, at='2026-10-21T11:01:00+09:00')
    run, saved = completed_book(root, b, m)
    saved['checkpoint'] = str(Path('/Users/jungwoohwang/Library/Application Support/MOA-LAYA') /
                              'research' / m.RUN / saved['checkpoint_name'])
    rc._write_atomic(run / 'TRAIN_RESULT.json', saved)
    snap['result'] = saved
    monkeypatch.setattr(b, '_collect', lambda *args: (_ for _ in ()).throw(
        subprocess.TimeoutExpired('bounded result transfer', 180)))
    assert m.advance(root, at='2026-10-21T11:03:00+09:00')['status'] == 'RECONCILIATION_REQUIRED'
    rc.Campaign(root).pause()
    collector = script('moa-research-book-results.py')
    remote = root / 'remote'
    target = remote / 'research' / m.RUN
    target.parent.mkdir(parents=True)
    run.rename(target)
    monkeypatch.setattr(collector, 'ROOT', remote)
    r = api()
    monkeypatch.setattr(r, '_metadata', lambda plan: collector.collect())
    return r, b, m, snap, target, saved, mini, before


def test_recovery_evaluates_saved_model_without_resetting_legacy_state_or_retraining(tmp_path, monkeypatch):
    r, b, m, snap, run, saved, mini, before = recovery_case(tmp_path, monkeypatch)
    legacy = (tmp_path / m.FOLDER / 'STATE.json').read_bytes()
    r.prepare(tmp_path, at='2026-10-21T12:00:00+09:00')
    issued = []
    monkeypatch.setattr(b, '_launch', lambda root, plan, action: issued.append(action) or 6789)
    assert r.advance(tmp_path)['status'] == 'EVALUATING_ON_BOOK'
    snap['running'] = True
    assert r.advance(tmp_path)['status'] == 'OBSERVE_BOOK'
    snap['running'] = False
    request = rc._read_json(tmp_path / m.FOLDER / 'BINARY_REQUEST.json')
    records = [dict(case_id=row['case_id'], mode=row['mode'], model='binary_candidate', split='dev',
        operation=row['operation'], family=row['family'], prediction='changed',
        logits={'preserved': 0., 'changed': 1.},
        probabilities={'preserved': .2, 'changed': .8}, system_forced=False)
        for row in m.evaluation_inputs(request, [])]
    rc._write_atomic(run / 'EVALUATION.json', dict(status='complete', records=records, seconds=6,
        separate_process=True, execution_device='mps', trained=False, system_forced=False,
        operational_promotion=False, model_sha256=saved['sha256']['model.safetensors'],
        request_sha256=a.sha(tmp_path / m.FOLDER / 'BINARY_REQUEST.json')))
    assert r.advance(tmp_path)['status'] == 'COMPLETE'
    result = rc._read_json(r.folder(tmp_path) / 'COMPARISON.json')
    assert result['models']['binary_candidate']['average']['items'] == 2
    assert result['rule_answers_counted_as_model_success'] is False
    assert issued == ['evaluate'] and mini == [('train', False)]
    assert (tmp_path / m.FOLDER / 'STATE.json').read_bytes() == legacy
    assert all((tmp_path / n).read_bytes() == raw for n, raw in before.items())
    assert (tmp_path / 'PAUSED').exists()
    assert r.advance(tmp_path)['status'] == 'COMPLETE'


@pytest.mark.parametrize('fault', ['weights', 'contract', 'path', 'missing_file'])
def test_checkpoint_or_contract_corruption_cannot_be_treated_as_transfer_only(tmp_path, monkeypatch, fault):
    r, b, m, snap, run, saved, _, _ = recovery_case(tmp_path, monkeypatch)
    if fault == 'weights':
        (run / saved['checkpoint_name'] / 'model.safetensors').write_bytes(b'corrupt')
    elif fault == 'contract':
        contract = rc._read_json(run / 'TRAINING_CONTRACT.json')
        contract['semantic_profile'] = 'different'
        rc._write_atomic(run / 'TRAINING_CONTRACT.json', contract)
        saved['training_contract_sha256'] = a.sha(run / 'TRAINING_CONTRACT.json')
        rc._write_atomic(run / 'TRAIN_RESULT.json', saved)
    elif fault == 'path':
        saved['checkpoint_name'] = '../checkpoint-r1-0056'
        rc._write_atomic(run / 'TRAIN_RESULT.json', saved)
    else:
        (run / saved['checkpoint_name'] / 'training-state.pt').unlink()
    with pytest.raises((ValueError, OSError)):
        r.prepare(tmp_path)
    assert not list(r.folder(tmp_path).glob('EVAL_DISPATCH-*.json'))


def test_uncertain_eval_delivery_never_launches_a_second_process(tmp_path, monkeypatch):
    r, b, m, snap, run, saved, _, _ = recovery_case(tmp_path, monkeypatch)
    r.prepare(tmp_path)
    calls = []
    def launch(root, plan, action):
        calls.append(action)
        raise subprocess.TimeoutExpired('uncertain evaluation delivery', 60)
    monkeypatch.setattr(b, '_launch', launch)
    assert r.advance(tmp_path)['status'] == 'RECONCILIATION_REQUIRED'
    assert r.advance(tmp_path)['status'] == 'RECONCILIATION_REQUIRED'
    assert calls == ['evaluate']


@pytest.mark.parametrize('memory,allowed', [(4.99, True), (9, False)])
def test_resource_wait_does_not_consume_eval_dispatch(tmp_path, monkeypatch, memory, allowed):
    r, b, m, snap, run, saved, _, _ = recovery_case(tmp_path, monkeypatch)
    r.prepare(tmp_path)
    snap['resources'].update(available_memory_bytes=int(memory * 2**30), verify_allowed=allowed)
    assert r.advance(tmp_path)['status'] == 'WAITING_BOOK_EVAL_RESOURCE'
    assert not list(r.folder(tmp_path).glob('EVAL_DISPATCH-*.json'))


def test_metadata_rejects_intermediate_symlink_even_inside_checkpoint(tmp_path, monkeypatch):
    collector = script('moa-research-book-results.py')
    run = tmp_path / 'research' / collector.RUN
    cp = run / 'checkpoint-r1-0056'
    (cp / 'actual').mkdir(parents=True)
    (cp / 'actual/config.json').write_bytes(b'{}')
    (cp / 'encoder').symlink_to(cp / 'actual', target_is_directory=True)
    rc._write_atomic(run / 'TRAIN_RESULT.json', dict(checkpoint_name=cp.name,
        sha256={'encoder/config.json': a.sha(cp / 'actual/config.json')}))
    rc._write_atomic(run / 'TRAINING_CONTRACT.json', {})
    monkeypatch.setattr(collector, 'ROOT', tmp_path)
    with pytest.raises(ValueError, match='symlink'):
        collector.collect()


@pytest.mark.parametrize('fault', ['time', 'device', 'forced', 'model', 'options', 'order'])
def test_bad_evaluation_cannot_produce_a_score_or_reset_budget(tmp_path, monkeypatch, fault):
    r, b, m, snap, run, saved, _, _ = recovery_case(tmp_path, monkeypatch)
    r.prepare(tmp_path)
    request = rc._read_json(tmp_path / m.FOLDER / 'BINARY_REQUEST.json')
    records = [dict(case_id=row['case_id'], mode=row['mode'], model='binary_candidate', split='dev',
        operation=row['operation'], family=row['family'], prediction='changed',
        logits={'preserved': 0., 'changed': 1.}, probabilities={'preserved': .2, 'changed': .8},
        system_forced=False) for row in m.evaluation_inputs(request, [])]
    value = dict(status='complete', seconds=6, separate_process=True, execution_device='mps',
        trained=False, system_forced=False, operational_promotion=False, records=records,
        model_sha256=saved['sha256']['model.safetensors'],
        request_sha256=a.sha(tmp_path / m.FOLDER / 'BINARY_REQUEST.json'))
    if fault == 'time': value['seconds'] = 601
    elif fault == 'device': value['execution_device'] = 'cpu'
    elif fault == 'forced': records[0]['system_forced'] = True
    elif fault == 'model': value['model_sha256'] = 'different'
    elif fault == 'options': records[0]['probabilities'] = {'preserved': .1, 'changed': .2, 'insufficient': .7}
    elif fault == 'order': records.reverse()
    rc._write_atomic(run / 'EVALUATION.json', value)
    assert r.advance(tmp_path)['status'] == 'RECONCILIATION_REQUIRED'
    assert not (r.folder(tmp_path) / 'COMPARISON.json').exists()
    assert not list(r.folder(tmp_path).glob('EVAL_DISPATCH-*.json'))


def test_restoring_recovery_state_cannot_repeat_evaluation_dispatch(tmp_path, monkeypatch):
    r, b, m, snap, run, saved, _, _ = recovery_case(tmp_path, monkeypatch)
    r.prepare(tmp_path)
    state = (r.folder(tmp_path) / 'STATE.json').read_bytes()
    calls = []
    monkeypatch.setattr(b, '_launch', lambda root, plan, action: calls.append(action) or 6789)
    assert r.advance(tmp_path)['status'] == 'EVALUATING_ON_BOOK'
    (r.folder(tmp_path) / 'STATE.json').write_bytes(state)
    assert r.advance(tmp_path)['status'] == 'RECONCILIATION_REQUIRED'
    assert calls == ['evaluate']
