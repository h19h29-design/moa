import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('r1_shadow', Path(__file__).parents[1]/'scripts/moa-r1-shadow.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def config():
    return {'mode': 'shadow_eval_only', 'training_allowed': False, 'promotion_allowed': False,
            'maximum_seconds': 600, 'checkpoint_sha256': '13b5e9fa365754bb043cbf725879b76ed0fbca900ca381df10067cd502fa123e',
            'checkpoint_files': {'model.safetensors': '13b5e9fa365754bb043cbf725879b76ed0fbca900ca381df10067cd502fa123e'},
            'cases': [{'case_id': 'synthetic-1', 'payload': {'questions': ['conditions']}}]}


def test_runner_rejects_promotion_training_unbounded_and_wrong_checkpoint():
    assert runner.validate_input(config()) is None
    for key,value in [('mode','train'),('training_allowed',True),('promotion_allowed',True),
                      ('maximum_seconds',1801),('checkpoint_sha256','a'*64)]:
        data=config();data[key]=value
        with pytest.raises(ValueError): runner.validate_input(data)


def test_runner_rejects_duplicate_case_ids_and_missing_checkpoint_binding():
    data=config();data['cases']*=2
    with pytest.raises(ValueError): runner.validate_input(data)
    data=config();data['checkpoint_files']={'model.safetensors':'b'*64}
    with pytest.raises(ValueError): runner.validate_input(data)
