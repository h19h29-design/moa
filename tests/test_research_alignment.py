import importlib.util
from pathlib import Path

import pytest

from moa import research_alignment as a, research_advance as advance


def test_incomplete_answers_stay_in_denominator_and_forced_labels_are_not_raw():
    rows = [{'case_id': label, 'expected': {'table_relations': label}}
            for label in ('preserved', 'changed', 'insufficient')]
    answers = [{'case_id': row['case_id'], 'status': 'completed',
                'prediction': row['case_id'], 'prediction_origin': 'raw_laya'} for row in rows]
    answers[1].update(status='input_overflow', prediction=None)
    result = a.metrics(rows, answers)
    assert result['accuracy'] == 2/3
    assert result['actual_errors_missed'] == 1
    assert result['overflow'] == 1
    answers[0]['system_forced'] = True
    with pytest.raises(ValueError, match='raw'):
        a.metrics(rows, answers)


def test_gate_rejects_high_accuracy_single_label_collapse():
    collapse = {'accuracy': .9, 'balanced_accuracy': 1/3, 'overflow': 0,
                'recall': {'changed': 1., 'preserved': 0., 'insufficient': 0.}}
    assert not a.selection({'models': {'candidate_paired': collapse, 'r1_paired': collapse}})


def test_saved_resource_stop_is_resumed_not_misreported_as_evaluated(tmp_path, monkeypatch):
    from moa.research_campaign import _write_atomic
    _write_atomic(tmp_path/'STATE.json', {'active_run': 'run-001', 'status': 'TRAINING'})
    monkeypatch.setattr(advance, 'worker_running', lambda: False)
    monkeypatch.setattr(advance, 'remote_state', lambda *_: {
        'online': True, 'result': {'status': 'saved', 'stop_reason': 'resource_guard'}, 'reload': 'reloaded'})
    monkeypatch.setattr(advance, '_resume_if_allowed', lambda *_: {'status': 'TRAINING_RESUMED'})
    assert advance.advance(tmp_path)['status'] == 'TRAINING_RESUMED'


def test_campaign_five_gib_guard_is_stricter_than_installed_three_gib():
    path = Path(__file__).parents[1]/'scripts/moa-research-worker.py'
    spec = importlib.util.spec_from_file_location('pair_worker', path)
    worker = importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)
    class Runtime:
        def resources(self):
            return {'train_allowed': True, 'verify_allowed': True,
                    'available_memory_bytes': 4*2**30}
    assert worker.resource_allowed(Runtime(), {})[0] is False
    assert worker.resource_allowed(Runtime(), {}, training=False)[0] is True


def test_own_cached_mps_memory_is_released_before_the_guard_is_checked():
    path = Path(__file__).parents[1]/'scripts/moa-research-worker.py'
    spec = importlib.util.spec_from_file_location('memory_guard_worker', path)
    worker = importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)
    trace = []
    class MPS:
        def synchronize(self): trace.append('sync')
        def empty_cache(self): trace.append('release')
    class Torch:
        mps = MPS()
    class Runtime:
        def resources(self):
            trace.append('measure')
            return {'train_allowed': True, 'verify_allowed': True,
                    'available_memory_bytes': (6 if 'release' in trace else 4)*2**30}
    assert worker.resource_allowed(Runtime(), {})[0] is False
    trace.clear()
    assert worker.training_headroom(Runtime(), {}, Torch())[0] is True
    assert trace == ['sync', 'release', 'measure']


def test_execution_repair_cannot_change_data_or_training_settings(tmp_path):
    import json
    path = Path(__file__).parents[1]/'scripts/moa-research-worker.py'
    spec = importlib.util.spec_from_file_location('revision_worker', path)
    worker = importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)
    old = {'runner_sha256': 'a'*64, 'configuration': {'head_lr': .001}, 'bundle': 'same'}
    (tmp_path/'JOB.json').write_text(json.dumps(old))
    revision = dict(old, runner_sha256='b'*64, execution_revision=2,
                    previous_job={'filename': 'JOB.json', 'sha256': worker.file_hash(tmp_path/'JOB.json')})
    assert worker.validate_execution_revision(revision, tmp_path) == revision['previous_job']['sha256']
    revision['configuration'] = {'head_lr': .01}
    with pytest.raises(ValueError, match='training/data/settings'):
        worker.validate_execution_revision(revision, tmp_path)


def test_auxiliary_repair_chains_previous_job_without_changing_learning(tmp_path):
    import json
    path = Path(__file__).parents[1]/'scripts/moa-research-worker.py'
    spec = importlib.util.spec_from_file_location('revision_three', path)
    worker = importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)
    old = {'configuration': {'grad_accum': 8}, 'runner_sha256': 'a'*64, 'execution_revision': 2}
    (tmp_path/'JOB-r2.json').write_text(json.dumps(old))
    new = dict(old, execution_revision=3, runner_sha256='b'*64,
               previous_job={'filename': 'JOB-r2.json', 'sha256': worker.file_hash(tmp_path/'JOB-r2.json')})
    assert worker.validate_execution_revision(new, tmp_path) == new['previous_job']['sha256']


def test_auxiliary_cannot_redirect_execution_to_an_arbitrary_directory(tmp_path):
    from moa.research_campaign import _write_atomic
    _write_atomic(tmp_path/'alignment/STATE.json', {'execution_host': 'mini'})
    with pytest.raises(ValueError, match='isolated'):
        a.mini_environment(tmp_path)


def test_zero_update_host_migration_validates_the_whole_repair_chain(tmp_path):
    import json
    path = Path(__file__).parents[1]/'scripts/moa-research-worker.py'
    spec = importlib.util.spec_from_file_location('repair_chain', path)
    worker = importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)
    first = {'configuration': {'grad_accum': 8}, 'runner_sha256': 'a'*64}
    (tmp_path/'JOB.json').write_text(json.dumps(first))
    second = dict(first, runner_sha256='b'*64, execution_revision=2,
                  previous_job={'filename': 'JOB.json', 'sha256': worker.file_hash(tmp_path/'JOB.json')})
    (tmp_path/'JOB-r2.json').write_text(json.dumps(second))
    third = dict(second, runner_sha256='c'*64, execution_revision=3, execution_device='cpu',
                 previous_job={'filename': 'JOB-r2.json', 'sha256': worker.file_hash(tmp_path/'JOB-r2.json')})
    assert worker.execution_ancestor_hashes(third, tmp_path) == {
        worker.file_hash(tmp_path/'JOB.json'), worker.file_hash(tmp_path/'JOB-r2.json')}
    (tmp_path/'JOB.json').write_text('{}')
    with pytest.raises(ValueError, match='changed'):
        worker.execution_ancestor_hashes(third, tmp_path)


def test_process_probe_tolerates_foreign_encoding_and_untruncated_commands(monkeypatch):
    import json
    captured = []
    monkeypatch.setattr(a, 'worker_root', lambda *_: None)
    def remote(command, *, script):
        captured.append(script)
        return json.dumps({'online': True, 'files': {}, 'running': False, 'device_busy': False})
    monkeypatch.setattr(a, '_ssh', remote)
    assert a.remote()['online'] is True
    assert "'-ww'" in captured[0]
    assert "errors='replace'" in captured[0]
    assert 'comm=' not in captured[0]
