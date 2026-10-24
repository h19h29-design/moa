"""Installed LAYA settings/target checks. No model is loaded or trained."""
import importlib.util
import random
from pathlib import Path
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

torch=pytest.importorskip('torch')
tr=pytest.importorskip('laya.train')

from moa import r2_research as r


def worker():
    path=Path(__file__).parents[1]/'scripts/moa-research-worker.py'
    spec=importlib.util.spec_from_file_location('training_contract_worker',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def cfg():
    return {k:v for k,v in dict(r.phase_config('r2a'),epochs=4,
        maximum_updates=120,planned_updates=120).items() if k not in ('parent','questions')}


def test_runner_rejects_a_shorter_actual_epoch_plan_before_loading_weights():
    prepare=getattr(worker(),'training_configuration',None)
    assert callable(prepare), 'runner must enforce the published effective settings'
    with pytest.raises(ValueError,match='horizon'):
        prepare(cfg(),144,r,tr)
    effective,plan=prepare(r.training_plan(cfg(),144)['configuration'],144,r,tr)
    assert plan['optimizer_updates']==72
    assert effective.shuffle_options==['choice']
    assert effective.loss=='soft-ce' and effective.freeze_encoder is True


def test_actual_shuffled_target_and_loss_gradient_follow_the_gold_label():
    from tokenizers import Tokenizer,models,pre_tokenizers
    from transformers import PreTrainedTokenizerFast
    vocab={word:i for i,word in enumerate(['[PAD]','[UNK]','[MASK]',
        'preserved','changed','insufficient','same','different','unknown'])}
    backend=Tokenizer(models.WordLevel(vocab,unk_token='[UNK]'))
    backend.pre_tokenizer=pre_tokenizers.Whitespace()
    tok=PreTrainedTokenizerFast(tokenizer_object=backend,pad_token='[PAD]',
                                unk_token='[UNK]',mask_token='[MASK]')
    question={'type':'choice','instructions':'Compare supplied facts',
              'criteria':{'preserved':'same','changed':'different','insufficient':'unknown'}}
    internal=tr.to_internal('q',question)
    gold='changed'
    item,reason=tr.make_item(tok,internal,tr.target_from_expected(internal,gold),
                             [12,13,14],256,1024)
    assert reason is None
    seen=set()
    for _ in range(40):
        # A real encoder is unnecessary: test the installed tokenizer seam,
        # permutation and objective, not another model optimization experiment.
        order=tr.draw_option_order(item,random.Random(_),['choice'])
        encoded=tr.encode_item(tok,item,1024,256,order,False)
        target=torch.tensor([encoded['target']],dtype=torch.float32)
        slot=int(target.argmax())
        assert list(internal['crit'])[order[slot]]==gold
        logits=torch.zeros_like(target,requires_grad=True)
        tr.soft_ce_loss(logits,target,torch.ones_like(target,dtype=torch.bool)).backward()
        assert float(logits.grad[0,slot])<0
        assert all(float(logits.grad[0,i])>0 for i in range(3) if i!=slot)
        seen.add(tuple(order))
    assert len(seen)==6


def test_binary_training_actual_tokenizer_target_and_loss_have_no_third_slot():
    from tokenizers import Tokenizer,models,pre_tokenizers
    from transformers import PreTrainedTokenizerFast
    from moa.research_binary import binary_questions
    vocab={word:i for i,word in enumerate(['[PAD]','[UNK]','[MASK]',
        'preserved','changed'])}
    backend=Tokenizer(models.WordLevel(vocab,unk_token='[UNK]'))
    backend.pre_tokenizer=pre_tokenizers.Whitespace()
    tok=PreTrainedTokenizerFast(tokenizer_object=backend,pad_token='[PAD]',
        unk_token='[UNK]',mask_token='[MASK]')
    question=tr.to_internal('conditions',binary_questions(['conditions'])['conditions'])
    for gold in ('preserved','changed'):
        item,reason=tr.make_item(tok,question,tr.target_from_expected(question,gold),
            [12,13,14],256,1024)
        assert reason is None
        for order in ([0,1],[1,0]):
            encoded=tr.encode_item(tok,item,1024,256,order,False)
            assert len(encoded['target'])==len(encoded['markers'])==2
            target=torch.tensor([encoded['target']],dtype=torch.float32)
            slot=int(target.argmax())
            assert list(question['crit'])[order[slot]]==gold
            logits=torch.zeros_like(target,requires_grad=True)
            tr.soft_ce_loss(logits,target,torch.ones_like(target,dtype=torch.bool)).backward()
            assert float(logits.grad[0,slot])<0 and float(logits.grad[0,1-slot])>0


def test_real_training_entrypoint_accepts_binary_profile_before_resource_guard_without_loading_weights(tmp_path,monkeypatch):
    from moa.research_binary import binary_questions
    from moa.worker_handoff import questions,CRITERIA
    m=worker();monkeypatch.setattr(m,'ROOT',tmp_path)
    monkeypatch.setattr(m,'execution_device',lambda job,torch:torch.device('cpu'))
    p=SimpleNamespace(CRITERIA=CRITERIA,questions=lambda ids:{k:questions()[k] for k in ids},
        state_for=lambda state:{k:v for k,v in state.items() if k!='questions'})
    s=dict(title='합성',file_role='notice',source_complete=True,
        source_blocks=[{'id':'s','text':'신청: 2개'}],rendered_blocks=[],attachment_manifest=[])
    rows=[dict(case_id=str(i),state=s,questions=binary_questions(['conditions']),
        expected={'conditions':label}) for i,label in enumerate(('preserved','changed'))]
    config=r.training_plan(dict(cfg(),maximum_seconds=1800),len(rows))['configuration']
    job=dict(run_id='unit-binary-not-a-research-run',configuration=config,
        semantic_profile='moa.binary-training.v1',questions=binary_questions(['conditions']),
        installed_sha256={},library=str(Path(__file__).parents[1]/'moa/r2_research.py'))
    ml=SimpleNamespace()  # No model loading or optimizer is reachable in this test.
    runtime=SimpleNamespace(exclusive=lambda path:nullcontext(),resources=lambda:{
        'train_allowed':False,'available_memory_bytes':2*2**30})
    monkeypatch.setattr(m,'context',lambda path:(job,{'train':rows},tmp_path,ml,p,runtime,tr))
    assert m.train(tmp_path/'JOB.json')['status']=='parked_resources'
    assert not (tmp_path/'research').exists()
