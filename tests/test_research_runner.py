import json
from pathlib import Path

import pytest

from moa import research_runner as rr


def test_remote_runner_rejects_unknown_host_and_arbitrary_command():
    with pytest.raises(ValueError):
        rr.remote_argv('user@evil.example', ['rm', '-rf', '/'])


def test_remote_runner_only_allows_pinned_actions():
    with pytest.raises(ValueError):
        rr.remote_argv(rr.DEFAULT_HOST, ['python3', '-c', 'print(1)'])
    argv = rr.remote_argv(rr.DEFAULT_HOST, ['inspect'])
    assert argv[0] == 'ssh'
    assert 'inspect' in argv[-1]


def test_job_payload_carries_no_blind_rows():
    job = rr.job_payload(run_id='moa-research-test-1', bundle='incoming/x',
                         bundle_sha256={'train-000.jsonl': 'a' * 64, 'dev-000.jsonl': 'b' * 64},
                         configuration={'maximum_updates': 40, 'epochs': 10},
                         parent='candidates/x/checkpoint', parent_files={'model.safetensors': 'c' * 64},
                         protected_models={'MODEL_REGISTRY.json': 'd' * 64},
                         runner_sha='e' * 64, library='moa/r2_research.py', library_sha='f' * 64,
                         installed={'x': 'a' * 64}, questions={})
    encoded = json.dumps(job)
    assert 'blind' not in encoded
    assert job['scope'] == 'unattended_synthetic_research'
    assert job['operational_promotion'] is False


def test_job_payload_refuses_blind_shard():
    with pytest.raises(ValueError):
        rr.job_payload(run_id='r', bundle='incoming/x',
                       bundle_sha256={'blind-000.jsonl': 'a' * 64},
                       configuration={}, parent='p', parent_files={}, protected_models={},
                       runner_sha='e' * 64, library='l', library_sha='f' * 64,
                       installed={}, questions={})


def test_job_payload_refuses_unsafe_run_id():
    with pytest.raises(ValueError):
        rr.job_payload(run_id='../etc', bundle='incoming/x', bundle_sha256={},
                       configuration={}, parent='p', parent_files={}, protected_models={},
                       runner_sha='e' * 64, library='l', library_sha='f' * 64,
                       installed={}, questions={})


def test_evaluation_compares_models_on_identical_items():
    rows = [{'case_id': 'a', 'expected': {'conditions': 'changed'}},
            {'case_id': 'b', 'expected': {'conditions': 'preserved'}}]
    candidate = [{'case_id': 'a', 'prediction': 'changed'}, {'case_id': 'b', 'prediction': 'changed'}]
    baseline = [{'case_id': 'a', 'prediction': 'preserved'}, {'case_id': 'b', 'prediction': 'preserved'}]
    report = rr.compare(rows, {'candidate': candidate, 'R1': baseline})
    assert report['models']['candidate']['accuracy'] == 0.5
    assert report['models']['R1']['accuracy'] == 0.5
    assert report['models']['R1']['changed_to_preserved'] == 1


def test_evaluation_refuses_missing_predictions_instead_of_scoring_zero_silently():
    rows = [{'case_id': 'a', 'expected': {'conditions': 'changed'}}]
    with pytest.raises(ValueError):
        rr.compare(rows, {'R1': []})


def test_evaluation_keeps_overflow_separate_from_wrong_answers():
    rows = [{'case_id': 'a', 'expected': {'conditions': 'changed'}}]
    report = rr.compare(rows, {'candidate': [{'case_id': 'a', 'prediction': None,
                                              'status': 'input_overflow'}]})
    assert report['models']['candidate']['overflow'] == 1
    assert report['models']['candidate']['scored'] == 0
