"""Adapter wire transformation only; no model execution in this test."""
import importlib.util
from pathlib import Path
import sys
import types
import pytest
import subprocess
import json
import urllib.error

from moa import worker_contract as wc
from moa.worker_handoff import questions, CRITERIA, rubric_sha256


def adapter():
    spec = importlib.util.spec_from_file_location('book_adapter', Path(__file__).parents[1] / 'scripts/moa-book-pilot.py')
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def test_training_budget_and_resource_gates_are_not_overridden():
    mod = adapter()
    assert mod.training_stop_reason({'train_allowed': True}, 120) is None
    assert mod.training_stop_reason({'train_allowed': True}, 2700) == 'wall_budget_exceeded'
    assert mod.training_stop_reason({'train_allowed': False}, 0) == 'resource_or_pause_gate'


def test_dataset_shard_path_cannot_escape_bundle(tmp_path):
    mod = adapter()
    with pytest.raises(ValueError, match='shard_path'):
        mod.shard_path(tmp_path, '../secret')
    with pytest.raises(ValueError, match='shard_path'):
        mod.shard_path(tmp_path, '/tmp/secret')
    assert mod.shard_path(tmp_path, 'train-001.jsonl') == tmp_path / 'train-001.jsonl'


def test_training_supervisor_reaps_only_its_child_on_resource_loss():
    mod = adapter()
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    assert mod.supervise_training(child, lambda:{'train_allowed':False}) == 'resource_or_pause_gate'
    assert child.poll() is not None


def test_only_one_early_resource_retry_not_quality_tuning_is_allowed():
    mod = adapter()
    parent = {'status':'interrupted','reason':'resource_or_pause_gate'}
    assert mod.resource_retry_allowed(parent, {'optimizer_updates':1}, completed=False, retries=0)
    assert not mod.resource_retry_allowed(parent, {'optimizer_updates':2}, completed=False, retries=0)
    assert not mod.resource_retry_allowed(parent, {'optimizer_updates':1}, completed=True, retries=0)
    assert not mod.resource_retry_allowed(parent, {'optimizer_updates':1}, completed=False, retries=1)
    assert not mod.resource_retry_allowed({'status':'interrupted','reason':'quality_failed'}, {'optimizer_updates':1}, completed=False, retries=0)


def test_complete_ack_must_confirm_the_exact_lease_and_fence():
    mod = adapter()
    job = dict(job_id='job-pilot-synthetic', lease_id='lease-1', fencing_token=2,
               input_sha256='a'*64, idempotency_key='b'*64)
    mod.confirm_ack({'accepted':True, **job}, job)
    # v1 NAS ACK has three mandatory identity fields. Lease/fence are checked
    # server-side before acceptance; if echoed by a future peer, verify them.
    required = ('job_id', 'input_sha256', 'idempotency_key')
    mod.confirm_ack({'accepted':True, **{k:job[k] for k in required}}, job)
    for key in required:
        with pytest.raises(ValueError, match='ack_not_verified'):
            mod.confirm_ack({'accepted':True, **{k:job[k] for k in required if k != key}}, job)
    for key in job:
        wrong = {'accepted':True, **job, key:'different'}
        with pytest.raises(ValueError, match='ack_not_verified'):
            mod.confirm_ack(wrong, job)


def test_lost_complete_response_keeps_durable_outbox_not_failed_job(tmp_path, monkeypatch):
    mod = adapter(); monkeypatch.setattr(mod, 'ROOT', tmp_path)
    job = dict(job_id='job-pilot-synthetic', lease_id='lease-1', fencing_token=2,
               input_sha256='a'*64, idempotency_key='b'*64)
    folder = tmp_path/'runs/job-pilot-synthetic-2'; folder.mkdir(parents=True)
    (folder/'result.json').write_text(json.dumps({**job, 'message_type':'result'}))
    class Client:
        worker = 'synthetic-worker'
        def call(self, *args): pytest.fail('durable complete must replay before fail')
    assert mod.handle_job_error(Client(), job, urllib.error.URLError('synthetic_disconnect')) == 'pending_ack'


def test_transient_disconnect_before_result_requeues_with_same_fencing(tmp_path, monkeypatch):
    mod = adapter(); monkeypatch.setattr(mod, 'ROOT', tmp_path)
    job = dict(job_id='job-pilot-synthetic', lease_id='lease-1', fencing_token=2)
    class Client:
        worker = 'synthetic-worker'
        def call(self, route, body): self.route, self.body = route, body
    client = Client()
    mod.handle_job_error(client, job, urllib.error.URLError('synthetic_disconnect'))
    assert client.body['retryable'] is True
    assert client.body['fencing_token'] == 2
    mod.handle_job_error(client, job, ValueError('source_manifest_not_verified'))
    assert client.body['retryable'] is False
    mod.handle_job_error(client, job, urllib.error.HTTPError('http://localhost',403,'forbidden',{},None))
    assert client.body['retryable'] is False


def test_book_raw_labels_and_probabilities_are_not_replaced_by_rule_labels(monkeypatch):
    pkg = types.ModuleType('workers'); pkg.__path__ = []
    worker = types.ModuleType('workers.laya_local'); worker.__path__ = []
    policy = types.ModuleType('workers.laya_local.policy')
    policy.questions = lambda keys: {k: questions()[k] for k in keys}
    policy.CRITERIA = CRITERIA; policy.canonical = wc.canonical
    for key, value in [('workers', pkg), ('workers.laya_local', worker), ('workers.laya_local.policy', policy)]:
        monkeypatch.setitem(sys.modules, key, value)
    spec = importlib.util.spec_from_file_location('book_adapter', Path(__file__).parents[1] / 'scripts/moa-book-pilot.py')
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    assert mod.claimed_job({'ok': True, 'job': {'job_id': 'synthetic'}}) == {'job_id': 'synthetic'}
    assert mod.claimed_job(None) is None
    local = {'status': 'completed', 'criteria': {'conditions': {'label': 'preserved', 'model_label': 'preserved',
             'raw_distribution': {'preserved': .8, 'changed': .1, 'insufficient': .1}, 'actual_tokens': 123}},
             'coverage': {'source_ids': ['s1'], 'render_ids': ['r1'], 'missing_context_ids': []},
             'checkpoint_sha256': 'a' * 64, 'model_revision': 'synthetic', 'tokenizer_revision': 'synthetic',
             'truncation': False, 'deterministic_findings': [{'code': 'date_mismatch'}]}
    out = mod.result_payload(local, {}, 'b' * 64)
    assert out['answers']['conditions']['label'] == 'preserved'
    assert out['raw_local_inference'] == local
    assert not out['answers']['conditions']['calibrated']
    assert out['rubric_sha256'] == rubric_sha256()
    assert not wc.validate_verify_result(out, {'questions': ['conditions'], 'source_blocks': [{'id': 's1'}], 'rendered_blocks': [{'id': 'r1'}]})
