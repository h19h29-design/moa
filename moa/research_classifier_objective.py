"""Unissued research objective repair; never trains or changes a saved model.

Only an independently counted training inventory defines the class weights.
This removes the incentive to learn the 2:1 majority prior; it does not prove
content separation. A new candidate allowance and empirical evaluation are
still required. No completed/sealed worker is silently switched to this loss.
"""
from __future__ import annotations

LABELS = ('preserved', 'changed')
VERSION = 'moa.fixed-classifier-class-balanced-ce.v1'


def class_weights(training_counts):
    if (set(training_counts) != set(LABELS) or
            any(type(training_counts[k]) is not int or training_counts[k] <= 0 for k in LABELS)):
        raise ValueError('exact positive integer training class inventory required')
    total = sum(training_counts.values())
    return tuple(total/(len(LABELS)*training_counts[k]) for k in LABELS)


def balanced_cross_entropy(logits, targets, *, training_counts):
    """Mean of per-example weighted losses, including microbatch size one.

    CE(weight=..., reduction='mean') normalizes by observed target weights;
    at microbatch one that cancels the class correction completely. Keep the
    per-example weight before the unchanged outer accumulation-window mean.
    Counts and weights never depend on development labels or predictions.
    """
    import torch
    from torch.nn import functional as F

    weights = class_weights(training_counts)
    if (logits.ndim != 2 or logits.shape[0] == 0 or logits.shape[1] != len(LABELS) or
            not logits.is_floating_point() or not bool(torch.isfinite(logits).all()) or
            targets.ndim != 1 or targets.shape[0] != logits.shape[0] or
            targets.dtype != torch.long or targets.device != logits.device or
            not bool(((targets >= 0) & (targets < len(LABELS))).all())):
        raise ValueError('finite two-class logits and matching integer targets required')
    losses = F.cross_entropy(logits, targets, reduction='none')
    per_class = losses.new_tensor(weights)
    return (losses*per_class[targets]).mean()
