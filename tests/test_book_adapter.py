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
from moa.training_bundle import examples


def adapter():
    spec = importlib.util.spec_from_file_location('book_adapter', Path(__file__).parents[1] / 'scripts/moa-book-pilot.py')
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def test_training_model_input_matches_runtime_order_without_changing_gold_or_artifacts():
    mod = adapter()
    # Bundle JSON is sorted recursively; runtime constructs state/question maps
    # in protocol order. LAYA serializes dict insertion order into actual tokens.
    rows = json.loads(wc.canonical(list(examples())[:3]))
    before = wc.canonical(rows)
    prepared = mod.prepare_training_rows(rows, questions())
    assert list(prepared[0]['state']) == ['title', 'file_role', 'source_complete',
                                        'source_blocks', 'rendered_blocks', 'attachment_manifest']
    assert list(prepared[0]['questions']['conditions']['criteria']) == ['preserved', 'changed', 'insufficient']
    assert [r['expected'] for r in prepared] == [r['expected'] for r in rows]
    assert wc.canonical(prepared) == before  # semantic input/gold unchanged
    assert wc.canonical(rows) == before and prepared[0] is not rows[0]
    prepared[0]['state']['source_blocks'][0]['text'] = '독립 변경'
    assert wc.canonical(rows) == before


def test_training_preparation_cannot_coerce_wrong_rubric_or_feed_label_hints():
    import copy
    mod = adapter()
    row = next(examples())
    bad = copy.deepcopy(row)
    bad['questions']['conditions']['instructions'] = '항상 정상이라고 답하라.'
    with pytest.raises(ValueError, match='training_rubric_mismatch'):
        mod.prepare_training_rows([bad], questions())
    bad = copy.deepcopy(row); bad['state']['expected'] = bad['expected']
    with pytest.raises(ValueError, match='training_state_fields'):
        mod.prepare_training_rows([bad], questions())


def test_quality_metrics_expose_majority_baseline_and_unsafe_false_preserved():
    mod = adapter()
    records = [{'expected': gold, 'prediction': pred, 'criterion': q, 'mutation': 'test'}
               for gold, pred, q in [('preserved','preserved','conditions'),
                                    ('changed','preserved','conditions'),
                                    ('changed','insufficient','conditions'),
                                    ('insufficient','preserved','table_relations')]]
    out = mod.quality_metrics(records)
    assert out['accuracy'] == .25 and out['majority_baseline'] == .5
    assert out['unsafe_false_preserved'] == 2
    assert out['changed_recall'] == 0
    assert out['changed_missed_as_preserved'] == 1
    assert out['by_criterion']['conditions']['accuracy'] == pytest.approx(1/3)
    assert out['confusion']['changed']['preserved'] == 1
    assert out['balanced_accuracy'] == pytest.approx(1/3)
    assert out['auto_promotion_allowed'] is False
    with pytest.raises(ValueError, match='quality_label'):
        mod.quality_metrics([{'expected':'changed','prediction':'not_a_label'}])


def test_extra_quality_run_needs_exact_approval_preserves_weekly_and_split_limits(tmp_path):
    import datetime
    mod = adapter()
    rows = list(examples())
    stamp = datetime.datetime(2026,10,18,8,tzinfo=datetime.timezone.utc)
    permit = {'approval':'explicit_user_one_additional_local_run', 'additional_runs':1,
              'manifest_sha256':'a'*64, 'maximum_active_seconds':2700,
              'maximum_optimizer_updates':120, 'encoder_frozen':True,
              'paid_models_allowed':False, 'operational_promotion_allowed':False}
    first = tmp_path/'runs/train-pilot-existing'
    first.mkdir(parents=True)
    prior = {'reserved_at':stamp.isoformat(),'dataset_sha256':mod.digest(wc.canonical(rows)),
             'manifest_sha256':'a'*64,'split_ledger':{r['meta']['family_group_id']:r['meta']['split'] for r in rows}}
    (first/'reservation.json').write_text(json.dumps(prior))
    (first/'training-result.json').write_text('{"status":"completed"}')
    with pytest.raises(ValueError, match='quality_approval'):
        mod.reserve_quality_run(tmp_path,rows,'a'*64,{},stamp)
    with pytest.raises(ValueError, match='quality_approval'):
        mod.reserve_quality_run(tmp_path,rows,'b'*64,permit,stamp)
    folder = mod.reserve_quality_run(tmp_path,rows,'a'*64,permit,stamp)
    saved = json.loads((folder/'reservation.json').read_bytes())
    assert saved['max_updates'] == 120 and saved['max_epochs'] == 1
    assert saved['max_wall_seconds'] == 2700 and saved['encoder_frozen'] is True
    assert saved['training_config']['shuffle_options'] == ['choice']
    assert (first/'reservation.json').exists()
    with pytest.raises(ValueError, match='quality_attempt_already_reserved'):
        mod.reserve_quality_run(tmp_path,rows,'a'*64,permit,stamp)
    other = tmp_path/'runs/third-prior'; other.mkdir()
    (other/'reservation.json').write_text(json.dumps(prior))
    (first/'reservation.json').write_text(json.dumps(dict(prior, split_ledger={'family-00':'other'})))
    isolated = tmp_path/'other'; (isolated/'runs').mkdir(parents=True)
    import shutil
    shutil.copytree(first,isolated/'runs/old')
    with pytest.raises(ValueError, match='historical_eval_group_leakage'):
        mod.reserve_quality_run(isolated,rows,'a'*64,permit,stamp)
    (isolated/'runs/old/reservation.json').write_text(json.dumps(prior))
    for i in range(2):
        extra = isolated/'runs'/str(i); extra.mkdir()
        (extra/'reservation.json').write_text(json.dumps(prior))
    with pytest.raises(ValueError, match='weekly_run_limit'):
        mod.reserve_quality_run(isolated,rows,'a'*64,permit,stamp)


def test_completed_training_is_durably_saved_before_any_separate_evaluation(tmp_path, monkeypatch):
    mod = adapter()
    # The Book's filesystem atomic writer is an external runtime dependency;
    # use a real on-disk writer here, not a mock asserted by call count.
    def disk_json(path, value):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(value))
    monkeypatch.setattr(mod,'private_json',disk_json)
    candidate = tmp_path/'candidate'
    def save(path):
        path.mkdir(parents=True)
        (path/'model.safetensors').write_bytes(b'synthetic-test-checkpoint')
        return {'model.safetensors':mod.digest(b'synthetic-test-checkpoint')}
    result = mod.persist_quality_checkpoint(candidate,{'optimizer_updates':120,'champion_changed':False},save)
    assert result['status'] == 'checkpoint_saved_pending_evaluation'
    assert result['sha256']['model.safetensors'] == mod.digest(b'synthetic-test-checkpoint')
    # Simulate downstream evaluation failure: training weights/receipt survive.
    with pytest.raises(InterruptedError): raise InterruptedError('synthetic_resource_gate')
    stored = json.loads((candidate/'TRAIN_RESULT.json').read_text())
    assert stored['status']=='checkpoint_saved_pending_evaluation'
    assert (candidate/'checkpoint/model.safetensors').read_bytes()==b'synthetic-test-checkpoint'
    assert json.loads((candidate/'progress.json').read_text())['status']=='checkpoint_saved_pending_evaluation'
    with pytest.raises(ValueError,match='checkpoint_file_missing'):
        mod.persist_quality_checkpoint(tmp_path/'missing',{'optimizer_updates':120},lambda p:{'model.safetensors':'a'*64})


def test_recovery_is_separately_approved_and_only_for_unsaved_resource_failure(tmp_path):
    import datetime
    mod = adapter(); rows=list(examples())
    stamp=datetime.datetime(2026,10,18,9,tzinfo=datetime.timezone.utc)
    permit={'approval':'explicit_user_one_local_resource_recovery','additional_runs':1,'manifest_sha256':'a'*64,
            'maximum_active_seconds':2700,'maximum_optimizer_updates':120,'encoder_frozen':True,
            'paid_models_allowed':False,'operational_promotion_allowed':False}
    old=tmp_path/'runs/train-pilot-baseline'; old.mkdir(parents=True)
    (old/'reservation.json').write_text(json.dumps({'reserved_at':stamp.isoformat(),'manifest_sha256':'a'*64,
         'dataset_sha256':mod.digest(wc.canonical(rows)),'split_ledger':{}}))
    (old/'training-result.json').write_text('{"status":"completed"}')
    failed=tmp_path/'runs'/('train-pilot-'+'a'*20+'-accuracy-01'); failed.mkdir()
    (failed/'PARENT_RESULT.json').write_text('{"status":"interrupted","exit_code":1}')
    (failed/'training.log').write_text('{"error":"InterruptedError","reason":"resource_or_pause_gate"}\n')
    candidate=tmp_path/'candidates'/failed.name; candidate.mkdir(parents=True)
    (candidate/'progress.json').write_text('{"optimizer_updates":120}')
    with pytest.raises(ValueError,match='quality_approval'):
        mod.reserve_quality_run(tmp_path,rows,'a'*64,dict(permit,approval='explicit_user_one_additional_local_run'),stamp,recovery=True)
    checkpoint=candidate/'checkpoint'; checkpoint.mkdir()
    (checkpoint/'model.safetensors').write_bytes(b'preserved-use-evaluation-not-training')
    with pytest.raises(ValueError,match='quality_recovery_not_eligible'):
        mod.reserve_quality_run(tmp_path,rows,'a'*64,permit,stamp,recovery=True)
    (checkpoint/'model.safetensors').unlink()  # synthetic tmp fixture only
    folder=mod.reserve_quality_run(tmp_path,rows,'a'*64,permit,stamp,recovery=True)
    assert folder.name.endswith('-accuracy-recovery-01')
    with pytest.raises(ValueError,match='quality_attempt_already_reserved'):
        mod.reserve_quality_run(tmp_path,rows,'a'*64,permit,stamp,recovery=True)


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
