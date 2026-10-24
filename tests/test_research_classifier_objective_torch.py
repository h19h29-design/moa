"""Tiny tensor math only; never loads a research model or takes an optimizer step."""
import math
import unittest

try:
    import torch
except ImportError:
    torch = None


def api():
    from moa import research_classifier_objective
    return research_classifier_objective


COUNTS = {'preserved':36, 'changed':72}


@unittest.skipIf(torch is None, 'optional Torch dependency unavailable')
class ObjectiveMathTests(unittest.TestCase):
    def test_microbatch_one_weight_is_not_cancelled_by_weighted_mean(self):
        logits = torch.tensor([[0., 0.]], requires_grad=True)
        normal = api().balanced_cross_entropy(logits, torch.tensor([0]), training_counts=COUNTS)
        error = api().balanced_cross_entropy(logits, torch.tensor([1]), training_counts=COUNTS)
        self.assertAlmostEqual(normal.item(), 1.5*math.log(2), places=6)
        self.assertAlmostEqual(error.item(), .75*math.log(2), places=6)
        # torch CE(weight=..., reduction='mean') would divide this single
        # item's weight away. The effective microbatch here really is one.
        cancelled = torch.nn.functional.cross_entropy(logits, torch.tensor([0]),
            weight=torch.tensor([1.5,.75]))
        self.assertAlmostEqual(cancelled.item(), math.log(2), places=6)


    def test_majority_prior_is_no_longer_a_stationary_constant_solution(self):
        targets = torch.tensor([0,1,1])
        bias = torch.tensor([0.,math.log(2)], requires_grad=True)
        logits = bias.expand(3,-1)
        original = torch.nn.functional.cross_entropy(logits,targets)
        old_gradient, = torch.autograd.grad(original,bias,retain_graph=True)
        self.assertTrue(torch.allclose(old_gradient,torch.zeros(2),atol=1e-7))
        repaired = api().balanced_cross_entropy(logits,targets,training_counts=COUNTS)
        gradient, = torch.autograd.grad(repaired,bias)
        # A descent step would lower the error-vs-normal logit, unlike the
        # original unweighted objective at the 2:1 class prior. No step runs.
        self.assertAlmostEqual(gradient[1].item(), 1/6, places=6)
        self.assertAlmostEqual(gradient[0].item(), -1/6, places=6)


    def test_accumulated_single_item_gradients_match_full_batch_objective(self):
        labels = torch.tensor([0,1,1])
        values = torch.tensor([[.1,.4], [.5,.2], [.3,.6]])
        batch = values.clone().requires_grad_()
        loss = api().balanced_cross_entropy(batch,labels,training_counts=COUNTS)
        expected, = torch.autograd.grad(loss,batch)
        single = values.clone().requires_grad_()
        for i in range(3):
            (api().balanced_cross_entropy(single[i:i+1],labels[i:i+1],
                training_counts=COUNTS)/3).backward()
        self.assertTrue(torch.allclose(single.grad,expected,atol=1e-7))


    def test_repair_does_not_solve_semantics_by_claiming_all_normal_is_success(self):
        bias = torch.zeros(2, requires_grad=True)
        loss = api().balanced_cross_entropy(bias.expand(3,-1), torch.tensor([0,1,1]),
            training_counts=COUNTS)
        gradient, = torch.autograd.grad(loss,bias)
        self.assertTrue(torch.allclose(gradient,torch.zeros(2),atol=1e-7))
        # This is only removal of the majority-prior incentive. Content
        # separation must still pass the independent diagnostic gate.
        self.assertAlmostEqual(loss.item(), math.log(2), places=6)


    def test_invalid_logits_or_targets_cannot_train(self):
        for values, labels in [([],[]), ([[0.,1.,2.]],[0]),
                ([[float('nan'),0.]],[0]), ([[0.,1.]],[2]), ([[0.,1.]],[0.])]:
            with self.subTest(values=values, labels=labels), self.assertRaises(ValueError):
                api().balanced_cross_entropy(torch.tensor(values),torch.tensor(labels),training_counts=COUNTS)
