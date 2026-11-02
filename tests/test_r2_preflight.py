import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('r2_preflight', Path(__file__).parents[1]/'scripts/moa-r2-preflight.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_training_and_runtime_token_ids_markers_and_gold_must_all_match():
    module.assert_parity(([11,12,13],[0,2]), ([11,12,13],[0,2]), [0.,1.,0.], 1)
    for train, runtime, target, gold in [
        (([11,12,13],[0,2]),([11,14,13],[0,2]),[0.,1.,0.],1),
        (([11,12,13],[0,2]),([11,12,13],[1,2]),[0.,1.,0.],1),
        (([11,12,13],[0,2]),([11,12,13],[0,2]),[1.,0.,0.],1),
    ]:
        with pytest.raises(ValueError,match='parity'):
            module.assert_parity(train,runtime,target,gold)


def test_checkpoint_paths_are_confined_and_hashing_is_bounded(tmp_path):
    import hashlib
    root = tmp_path/'checkpoint'
    root.mkdir()
    model = root/'model.bin'
    model.write_bytes(b'no model loading' * 100)
    assert module.file_sha(module.confined(root,'model.bin')) == hashlib.sha256(model.read_bytes()).hexdigest()
    for name in ('../outside', '/absolute', 'tokenizer/../../outside'):
        with pytest.raises(ValueError, match='checkpoint path'):
            module.confined(root,name)
    (root/'alias').symlink_to(model)
    with pytest.raises(ValueError, match='checkpoint path'):
        module.confined(root,'alias')
