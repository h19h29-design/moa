"""Tiny CPU tensor checks on Book; never a research-model run."""
import copy
import importlib.util
from pathlib import Path
import random
import unittest

try:
    import torch
except ImportError:
    torch = None


def subject():
    spec = importlib.util.spec_from_file_location('resume_state',
        Path(__file__).parents[1] / 'moa/research_classifier_resume_state.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipIf(torch is None, 'optional tensor checks run on MacBook')
class ResumeTensorContract(unittest.TestCase):
    def values(self):
        model = torch.nn.Linear(3, 2)
        opt = torch.optim.AdamW(model.parameters(), lr=.001, weight_decay=.01)
        sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=56, eta_min=.000001)
        state = dict(head=copy.deepcopy(model.state_dict()), optimizer=copy.deepcopy(opt.state_dict()),
            scheduler=copy.deepcopy(sched.state_dict()), updates=0, history=[],
            torch_rng=torch.get_rng_state(), python_rng=random.getstate(), mps_rng=torch.zeros(4, dtype=torch.uint8))
        return model, opt, sched, state

    def test_restores_saved_head_optimizer_scheduler_and_random_states(self):
        model, opt, sched, state = self.values()
        expected_torch = torch.rand(4)
        expected_python = random.random()
        with torch.no_grad(): model.weight.add_(10)
        random.seed(13); torch.manual_seed(91)
        restored_mps = []
        subject().restore_zero_state(torch, model.load_state_dict, model.state_dict,
            opt, sched, state, state['head'], restored_mps.append)
        self.assertTrue(torch.equal(model.weight, state['head']['weight']))
        self.assertEqual(opt.state_dict(), state['optimizer'])
        self.assertEqual(sched.state_dict(), state['scheduler'])
        self.assertTrue(torch.equal(torch.rand(4), expected_torch))
        self.assertEqual(random.random(), expected_python)
        self.assertTrue(torch.equal(restored_mps[0], state['mps_rng']))

    def test_refuses_inconsistent_or_nonzero_boundary(self):
        model, opt, sched, state = self.values()
        for changed in [dict(state, updates=1), dict(state, history=[{'step': 1}]),
                        dict(state, scheduler=dict(state['scheduler'], last_epoch=1))]:
            with self.assertRaises(ValueError):
                subject().restore_zero_state(torch, model.load_state_dict, model.state_dict,
                    opt, sched, changed, state['head'], lambda _: None)
        bad = copy.deepcopy(state['head']); bad['weight'].add_(1)
        with self.assertRaises(ValueError):
            subject().restore_zero_state(torch, model.load_state_dict, model.state_dict,
                opt, sched, state, bad, lambda _: None)

    def test_next_optimizer_update_matches_a_saved_state_continuation(self):
        model, opt, sched, state = self.values()
        other = torch.nn.Linear(3, 2)
        opt2 = torch.optim.AdamW(other.parameters(), lr=.001, weight_decay=.01)
        sched2 = torch.optim.lr_scheduler.CosineAnnealingLR(opt2, T_max=56, eta_min=.000001)
        subject().restore_zero_state(torch, other.load_state_dict, other.state_dict,
            opt2, sched2, state, state['head'], lambda _: None)
        for m, o, s in [(model, opt, sched), (other, opt2, sched2)]:
            o.zero_grad(); m(torch.ones(1, 3)).sum().backward(); o.step(); s.step()
        self.assertTrue(all(torch.equal(a, b) for a, b in zip(model.parameters(), other.parameters())))
        self.assertEqual(sched2.last_epoch, 1)


if __name__ == '__main__': unittest.main()
