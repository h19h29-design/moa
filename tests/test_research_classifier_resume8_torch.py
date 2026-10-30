"""Tiny saved-state checks; actual tensor execution belongs on MacBook."""
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
    spec = importlib.util.spec_from_file_location('resume8_state',
        Path(__file__).parents[1] / 'moa/research_classifier_resume8_state.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


class RemainingWindows(unittest.TestCase):
    def test_completed_eight_windows_are_never_replayed(self):
        rows = list(range(108))
        windows = list(subject().remaining_windows(rows, 8))
        self.assertEqual(len(windows), 48)
        self.assertEqual(windows[0][0:2], (0, 64))
        self.assertEqual(windows[-1][0:2], (3, 104))
        ordered = list(rows); random.Random(20261020).shuffle(ordered)
        self.assertEqual(windows[0][2], ordered[64:72])
        self.assertEqual(rows, list(range(108)))

    def test_refuses_a_different_boundary_or_split(self):
        for rows, updates in [(list(range(108)), 0), (list(range(108)), 9), (list(range(107)), 8)]:
            with self.assertRaises(ValueError): list(subject().remaining_windows(rows, updates))


@unittest.skipIf(torch is None, 'optional tensor checks run on MacBook')
class SavedEightState(unittest.TestCase):
    def values(self):
        model = torch.nn.Linear(3, 2)
        opt = torch.optim.AdamW(model.parameters(), lr=.001, weight_decay=.01)
        sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=56, eta_min=.000001)
        for _ in range(8):
            opt.zero_grad(); model(torch.ones(1, 3)).sum().backward(); opt.step(); sched.step()
        state = dict(head=copy.deepcopy(model.state_dict()), optimizer=copy.deepcopy(opt.state_dict()),
            scheduler=copy.deepcopy(sched.state_dict()), updates=8,
            history=[dict(step=i, loss=.5, gradient_norm=1.) for i in range(1, 9)],
            torch_rng=torch.get_rng_state(), python_rng=random.getstate(), mps_rng=torch.zeros(4, dtype=torch.uint8))
        return model, opt, sched, state

    def test_restores_actual_eight_update_state_and_next_update(self):
        model, opt, sched, state = self.values()
        other = torch.nn.Linear(3, 2)
        opt2 = torch.optim.AdamW(other.parameters(), lr=.001, weight_decay=.01)
        sched2 = torch.optim.lr_scheduler.CosineAnnealingLR(opt2, T_max=56, eta_min=.000001)
        restored_mps = []
        subject().restore_eight_state(torch, other.load_state_dict, other.state_dict,
            opt2, sched2, state, state['head'], restored_mps.append)
        self.assertEqual(sched2.last_epoch, 8)
        self.assertEqual(opt2.param_groups[0]['lr'], opt.param_groups[0]['lr'])
        self.assertTrue(torch.equal(restored_mps[0], state['mps_rng']))
        for m, o, s in [(model, opt, sched), (other, opt2, sched2)]:
            o.zero_grad(); m(torch.ones(1, 3)).sum().backward(); o.step(); s.step()
        self.assertTrue(all(torch.equal(a, b) for a, b in zip(model.parameters(), other.parameters())))
        self.assertEqual(sched2.last_epoch, 9)

    def test_refuses_inconsistent_saved_progress_or_optimizer(self):
        model, opt, sched, state = self.values()
        bad_opt = copy.deepcopy(state['optimizer'])
        next(iter(bad_opt['state'].values()))['step'] = torch.tensor(7.)
        for changed in [dict(state, updates=7), dict(state, history=state['history'][:-1]),
            dict(state, scheduler=dict(state['scheduler'], last_epoch=0)), dict(state, optimizer=bad_opt)]:
            with self.assertRaises(ValueError):
                subject().restore_eight_state(torch, model.load_state_dict, model.state_dict,
                    opt, sched, changed, state['head'], lambda _: None)

    def test_restores_both_cpu_random_states_without_resetting_seed(self):
        model, opt, sched, state = self.values()
        want_torch, want_python = torch.rand(4), random.random()
        torch.manual_seed(1); random.seed(2)
        subject().restore_eight_state(torch, model.load_state_dict, model.state_dict,
            opt, sched, state, state['head'], lambda _: None)
        self.assertTrue(torch.equal(torch.rand(4), want_torch))
        self.assertEqual(random.random(), want_python)


if __name__ == '__main__': unittest.main()
