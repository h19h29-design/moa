import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('r2_permutations',Path(__file__).parents[1]/'scripts/moa-r2-permutation-audit.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class Tensor:
    def __init__(self,value): self.value=value
    def tolist(self): return self.value

def test_all_five_model_tensors_and_slot_target_must_match():
    left={k:Tensor([[1,2]]) for k in module.INPUT_FIELDS}
    module.check_batch(left,dict(left),Tensor([[0.,1.,0.]]),[0.,1.,0.])
    for key in module.INPUT_FIELDS:
        right={**left,key:Tensor([[1,3]])}
        with pytest.raises(ValueError,match='model tensor'):
            module.check_batch(left,right,Tensor([[0.,1.,0.]]),[0.,1.,0.])
    with pytest.raises(ValueError,match='slot target'):
        module.check_batch(left,dict(left),Tensor([[1.,0.,0.]]),[0.,1.,0.])


def test_audit_rechecks_actual_checkpoint_and_refuses_unsafe_paths(tmp_path):
    import hashlib
    model=tmp_path/'model.bin';model.write_bytes(b'frozen model')
    expected={'model.bin':hashlib.sha256(model.read_bytes()).hexdigest()}
    assert module.verify_checkpoint(tmp_path,expected)==expected
    model.write_bytes(b'changed after preflight')
    with pytest.raises(ValueError,match='checkpoint'):
        module.verify_checkpoint(tmp_path,expected)
    for name in ('../outside','/absolute'):
        with pytest.raises(ValueError,match='checkpoint'):
            module.verify_checkpoint(tmp_path,{name:'0'*64})
    (tmp_path/'linked').symlink_to(tmp_path.parent,target_is_directory=True)
    with pytest.raises(ValueError,match='checkpoint'):
        module.verify_checkpoint(tmp_path,{'linked/outside':'0'*64})
    with pytest.raises(ValueError,match='checkpoint'):
        module.verify_checkpoint(tmp_path,{})
