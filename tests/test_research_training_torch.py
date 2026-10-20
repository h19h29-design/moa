"""Installed LAYA settings/target checks. No model is loaded or trained."""
import importlib.util
import random
from pathlib import Path

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
