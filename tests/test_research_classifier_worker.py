"""The Book research endpoint cannot become an unrestricted execution path."""
import importlib.util
import json
from pathlib import Path

import pytest


def helper():
    path = Path(__file__).parents[1] / 'scripts/moa-research-book-classifier.py'
    spec = importlib.util.spec_from_file_location('classifier_book_worker', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upload_confines_archive_and_refuses_overwriting_previous_research_artifacts(tmp_path, monkeypatch):
    m = helper()
    monkeypatch.setattr(m, 'ROOT', tmp_path)
    root = tmp_path / 'incoming' / m.RUN
    root.mkdir(parents=True)
    for bad in ('../escape', '/tmp/escape', 'nested/../../escape'):
        with pytest.raises(ValueError): m.confined(bad)
    target = root / 'file'
    target.write_bytes(b'original')
    m.write_once(target, b'original')
    with pytest.raises(ValueError): m.write_once(target, b'replacement')
    assert target.read_bytes() == b'original'


def test_safety_requires_all_ac_idle_pressure_disk_memory_and_pause_guards():
    m = helper()
    r = dict(ac_power=True, idle_seconds=900, memory_pressure_ok=True,
        disk_free_bytes=5 * 2**30, available_memory_bytes=8 * 2**30,
        train_allowed=True, verify_allowed=True)
    assert m.allowed(r, admission=True) and m.allowed(r, admission=False)
    for patch in ({'ac_power': False}, {'idle_seconds': 899}, {'memory_pressure_ok': False},
                  {'disk_free_bytes': 3 * 2**30}, {'train_allowed': False},
                  {'available_memory_bytes': 7 * 2**30 - 1}):
        assert not m.allowed(dict(r, **patch), admission=True)
    assert m.allowed(dict(r, available_memory_bytes=5 * 2**30), admission=False)
    assert not m.allowed(dict(r, available_memory_bytes=5 * 2**30 - 1), admission=False)


def test_worker_plan_cannot_change_training_variables_or_enable_cpu_or_reuse_resume():
    m = helper()
    from moa import research_classifier as c
    job = dict(run_id=m.RUN, version=c.VERSION, execution_host='macbookair', execution_device='mps',
        configuration=dict(epochs=4, planned_updates=56, maximum_updates=120,
            maximum_seconds=1800, micro_batch=1, grad_accum=8, freeze_encoder=True,
            head_lr=.001, min_lr=.000001, weight_decay=.01, grad_clip=1, seed=20261020,
            max_len=1024, head_max_len=256, loss='soft-ce', shuffle_options=['choice'],
            amp=False, gradient_checkpointing=False),
        label_to_index={'preserved': 0, 'changed': 1}, evaluation_maximum_seconds=600,
        model_promotion=False)
    m.validate_plan(job)
    for field, value in [('execution_device', 'cpu'), ('run_id', 'old-candidate'),
                         ('model_promotion', True), ('label_to_index', {'preserved': 1, 'changed': 0})]:
        with pytest.raises(ValueError): m.validate_plan(dict(job, **{field: value}))
    for key, value in [('epochs', 5), ('head_lr', .01), ('freeze_encoder', False),
                       ('maximum_seconds', 2700), ('planned_updates', 120)]:
        bad = dict(job, configuration=dict(job['configuration'], **{key: value}))
        with pytest.raises(ValueError): m.validate_plan(bad)
