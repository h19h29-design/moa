"""Run also on Book's existing venv; tiny CPU tensors, no LAYA/model/data access."""
import copy
import importlib.util
from pathlib import Path

import pytest

torch=pytest.importorskip('torch')


def test_optimizer_scheduler_saved_boundary_has_identical_next_update(tmp_path):
    torch.manual_seed(19)
    model=torch.nn.Linear(3,3)
    opt=torch.optim.AdamW(model.parameters(),lr=0.0003)
    sched=torch.optim.lr_scheduler.CosineAnnealingLR(opt,T_max=60,eta_min=1e-6)
    x=torch.randn(8,3);target=torch.tensor([0,1,2,0,1,2,0,1])
    def update(m,o,s):
        o.zero_grad();torch.nn.functional.cross_entropy(m(x),target).backward();o.step();s.step()
    update(model,opt,sched)
    path=tmp_path/'boundary.pt'
    torch.save({'head':model.state_dict(),'optimizer':opt.state_dict(),'scheduler':sched.state_dict(),
                'step':1,'rng':torch.get_rng_state()},path)
    update(model,opt,sched)
    expected=copy.deepcopy(model.state_dict())
    restored=torch.nn.Linear(3,3);ro=torch.optim.AdamW(restored.parameters(),lr=0.0003)
    rs=torch.optim.lr_scheduler.CosineAnnealingLR(ro,T_max=60,eta_min=1e-6)
    state=torch.load(path,weights_only=True,map_location='cpu')
    restored.load_state_dict(state['head']);ro.load_state_dict(state['optimizer']);rs.load_state_dict(state['scheduler'])
    torch.set_rng_state(state['rng']);update(restored,ro,rs)
    assert all(torch.equal(v,restored.state_dict()[k]) for k,v in expected.items())
    assert rs.last_epoch==2 and all(int(v['step'])==2 for v in ro.state.values())


def test_actual_installed_laya_config_accepts_fixed_limits():
    tr=pytest.importorskip('laya.train')
    # Installed Book research code is an explicit, private test target.
    local=Path(__file__).parents[1]/'moa/r2_research.py'
    if not local.exists():local=Path.home()/'Library/Application Support/MOA-LAYA/incoming/moa-r2-one-shot-20261019/code-v2/r2_research.py'
    spec=importlib.util.spec_from_file_location('research',local)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    for phase,want in [('s1',40),('r2a',60)]:
        config=module.phase_config(phase)
        actual=tr.TrainConfig(**{k:v for k,v in config.items() if k in tr.TrainConfig.__dataclass_fields__})
        actual.validate()
        assert tr.optimizer_updates(config['questions'],1,8,actual.epochs)==want
