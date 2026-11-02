"""Tiny real tensor tests; also run in Book's existing environment without pytest."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import pytest
torch=pytest.importorskip('torch')

source=Path(__file__).parents[1]/'moa/r2_research.py'
if not source.exists():source=Path(__file__).with_name('r2_research.py')
spec=importlib.util.spec_from_file_location('memory_research',source)
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)


class Metadata:
    def save_pretrained(self,path):
        path=Path(path);path.mkdir(parents=True,exist_ok=True)
        (path/'config.json').write_text('{"synthetic":true}',encoding='utf-8')


class Tiny(torch.nn.Module):
    def __init__(self):
        super().__init__();self.encoder=torch.nn.Linear(3,3);self.encoder.config=Metadata()
        self.head=torch.nn.Linear(3,3)
    def forward(self,x):return self.head(self.encoder(x))


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);torch.manual_seed(19);self.model=Tiny()
    def test_cpu_first_export_preserves_values_dtype_and_live_parameters(self):
        original=copy.deepcopy(self.model.state_dict());identities=[id(p) for p in self.model.parameters()]
        exported=r.cpu_fp16_state(self.model)
        self.assertEqual(set(exported),set(original))
        for name,value in exported.items():
            self.assertEqual(value.device.type,'cpu');self.assertEqual(value.dtype,torch.float16)
            self.assertTrue(torch.equal(value,original[name].half()))
        self.assertEqual([id(p) for p in self.model.parameters()],identities)
        self.assertTrue(all(torch.equal(v,self.model.state_dict()[k]) for k,v in original.items()))
    def test_streaming_parameter_digest_matches_existing_byte_contract(self):
        for encoder in (False,True):
            for export_roundtrip in (False,True):
                expected=hashlib.sha256()
                for name,param in self.model.named_parameters():
                    if name.startswith('encoder.')!=encoder:continue
                    value=param.detach().cpu()
                    if export_roundtrip:value=value.half().float()
                    expected.update(name.encode());expected.update(value.contiguous().numpy().tobytes())
                self.assertEqual(r.parameter_hash_cpu_first(self.model,encoder=encoder,
                    export_roundtrip=export_roundtrip),expected.hexdigest())
    def test_export_matches_installed_laya_layout_and_weight_bytes(self):
        import laya.train as tr
        legacy=self.root/'legacy';changed=self.root/'changed';cfg={'max_len':1024,'head_max_len':256}
        original=copy.deepcopy(self.model.state_dict())
        tr.save_checkpoint(self.model,Metadata(),cfg,str(legacy))
        r.save_checkpoint_cpu_first(self.model,Metadata(),cfg,changed)
        left={str(p.relative_to(legacy)):p.read_bytes() for p in legacy.rglob('*') if p.is_file()}
        right={str(p.relative_to(changed)):p.read_bytes() for p in changed.rglob('*') if p.is_file()}
        self.assertEqual(left,right)
        self.assertTrue(all(torch.equal(v,self.model.state_dict()[k]) for k,v in original.items()))
    def test_snapshot_does_not_change_the_next_fp32_adamw_update(self):
        reference=copy.deepcopy(self.model)
        opt=torch.optim.AdamW(self.model.parameters(),lr=0.0003)
        other=torch.optim.AdamW(reference.parameters(),lr=0.0003)
        x=torch.randn(8,3);target=torch.tensor([0,1,2,0,1,2,0,1])
        def step(model,optimizer):
            optimizer.zero_grad();torch.nn.functional.cross_entropy(model(x),target).backward();optimizer.step()
        step(self.model,opt);step(reference,other)
        r.save_checkpoint_cpu_first(self.model,Metadata(),{},self.root/'snapshot')
        step(self.model,opt);step(reference,other)
        self.assertTrue(all(torch.equal(v,reference.state_dict()[k]) for k,v in self.model.state_dict().items()))
        self.assertEqual([int(v['step']) for v in opt.state.values()],[2,2,2,2])
    def test_export_refuses_to_overwrite_an_existing_checkpoint(self):
        folder=self.root/'preserved';folder.mkdir();path=folder/'model.safetensors';path.write_bytes(b'preserve')
        with self.assertRaises(FileExistsError):r.save_checkpoint_cpu_first(self.model,Metadata(),{},folder)
        self.assertEqual(path.read_bytes(),b'preserve')
