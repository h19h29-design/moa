"""Small tensor-unit checks, NOT training or evaluation of a research model.

Run on Book's existing Python; the Mini's dependency-light suite may skip this.
"""
import copy
import importlib.util
import io
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    import torch
    from torch import nn
except ImportError:
    torch = nn = None


def subject():
    path = Path(__file__).parents[1] / 'moa/research_classifier.py'
    spec = importlib.util.spec_from_file_location('fixed_classifier', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipIf(torch is None, 'optional Torch unit checks run on MacBook')
class ClassifierTensorContract(unittest.TestCase):
    def base(self):
        class Encoder(nn.Module):
            def __init__(self):
                super().__init__()
                self.config = SimpleNamespace(hidden_size=8)
                self.embedding = nn.Embedding(64, 8)

            def forward(self, input_ids, attention_mask):
                return SimpleNamespace(last_hidden_state=self.embedding(input_ids))
        base = nn.Module()
        base.encoder = Encoder()
        layer = nn.TransformerEncoderLayer(8, 2, 16, dropout=0., batch_first=True)
        base.head = nn.TransformerEncoder(layer, 1, enable_nested_tensor=False)
        base.type_emb = nn.Embedding(3, 8)
        base.scorer = nn.Sequential(nn.LayerNorm(8), nn.Linear(8, 8), nn.GELU(), nn.Linear(8, 1))
        return base

    def test_real_head_has_fixed_two_logits_and_frozen_encoder_gradients(self):
        torch.manual_seed(7)
        base = self.base()
        before = {n: x.clone() for n, x in base.encoder.state_dict().items()}
        model = subject().build_classifier(base)
        ids = torch.tensor([[1, 4, 5, 2], [1, 4, 6, 2]])
        logits = model(ids, torch.ones_like(ids))
        self.assertEqual(tuple(logits.shape), (2, 2))
        nn.functional.cross_entropy(logits, torch.tensor([0, 1])).backward()
        self.assertTrue(all(p.grad is None for p in model.encoder.parameters()))
        self.assertTrue(all(torch.equal(before[n], x) for n, x in model.encoder.state_dict().items()))
        self.assertGreater(float(model.classifier[-1].weight.grad.abs().sum()), 0.)
        self.assertTrue(torch.isfinite(model.classifier[-1].weight.grad).all())
        with self.assertRaises(TypeError): model(ids, torch.ones_like(ids), option_order=[1, 0])

    def test_head_only_checkpoint_reloads_exactly_and_refuses_missing_weights(self):
        module = subject()
        torch.manual_seed(8)
        original = self.base()
        replica = copy.deepcopy(original)
        first, second = [module.build_classifier(b).eval() for b in (original, replica)]
        saved = module.head_state(first)
        self.assertTrue(saved and all(not n.startswith('encoder.') for n in saved))
        stream = io.BytesIO()
        torch.save(saved, stream)
        stream.seek(0)
        module.load_head(second, torch.load(stream, weights_only=True))
        x = torch.tensor([[1, 9, 8, 2]])
        with torch.inference_mode():
            self.assertTrue(torch.equal(first(x, torch.ones_like(x)), second(x, torch.ones_like(x))))
        with self.assertRaises(ValueError): module.load_head(second, {})
        altered = dict(saved, **{'encoder.injected': torch.zeros(1)})
        with self.assertRaises(ValueError): module.load_head(second, altered)


if __name__ == '__main__':
    unittest.main()
