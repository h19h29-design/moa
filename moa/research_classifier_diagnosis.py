"""Read-only collapse diagnostics; never fit thresholds or approve a model."""
from __future__ import annotations

import math
from statistics import mean, pstdev


def replay(records, gold, *, train_counts):
    from .research_classifier import LABELS, score

    if (set(train_counts) != set(LABELS) or
            any(type(n) is not int or n <= 0 for n in train_counts.values())):
        raise ValueError('positive independently counted training labels required')
    raw = score(records, gold)
    if not raw['items'] or any(raw['recall'][k] is None for k in LABELS):
        raise ValueError('both independently labelled classes required')
    if raw['incomplete']:
        raise ValueError('complete probability observations required for this diagnostic')
    groups = {label: [r['probabilities']['changed'] for r in records
                      if gold[r['case_id']] == label] for label in LABELS}
    normal, errors = (groups[k] for k in LABELS)
    auc = sum((x > y) + .5*(x == y) for x in errors for y in normal)/(len(errors)*len(normal))
    predictions = {r['prediction'] for r in records}
    ties = sum(r['probabilities'][LABELS[0]] == r['probabilities'][LABELS[1]] for r in records)
    total = sum(train_counts.values())
    priors = {k: train_counts[k]/total for k in LABELS}
    adjusted = []
    for record in records:
        probabilities = {k: record['probabilities'][k]/train_counts[k] for k in LABELS}
        denominator = sum(probabilities.values())
        probabilities = {k: v/denominator for k, v in probabilities.items()}
        adjusted.append(dict(case_id=record['case_id'], prediction=max(LABELS, key=probabilities.get),
                             probabilities=probabilities, system_forced=False))
    return dict(version='moa.fixed-classifier-diagnosis.v2', raw=raw,
        collapsed_to=next(iter(predictions)) if len(predictions) == 1 else None,
        content_separation_passed=bool(not ties and len(predictions) == 2 and raw['balanced_accuracy'] > .5
                                      and all(raw['recall'][k] > 0 for k in LABELS)),
        tied_observations=ties,
        ranking_auc=auc, score_statistics={k: dict(n=len(v), minimum=min(v), maximum=max(v),
            mean=mean(v), population_stdev=pstdev(v)) for k, v in groups.items()},
        cross_entropy=mean(-math.log(max(r['probabilities'][gold[r['case_id']]], 1e-30)) for r in records),
        constant_prior_cross_entropy=mean(-math.log(priors[gold[r['case_id']]]) for r in records),
        training_prior_entropy=-sum(p*math.log(p) for p in priors.values()),
        prior_only_diagnostic=score(adjusted, gold), threshold_fitted=False,
        new_model_inference=False, new_blind=False, operational_promotion=False,
        scope='exposed_observations_replay_not_a_selection_or_semantic_fix')


def remaining_evaluation(original_seconds, receipts):
    """Stopped runs count too; an unresolved dispatch is not refundable."""
    def valid(seconds):
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
            raise ValueError('known finite nonnegative elapsed time required')
        return seconds
    used = valid(original_seconds)
    for receipt in receipts:
        if receipt.get('status') not in ('complete', 'stopped'):
            raise ValueError('unresolved diagnostic execution must be reconciled')
        used += valid(receipt.get('seconds'))
    if used > 600:
        raise ValueError('cumulative saved-candidate evaluation limit exceeded')
    return 600-used


def resources_allowed(resources, *, admission):
    def capacity(key):
        value = resources.get(key)
        return (value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else -1)
    idle = resources.get('idle_seconds')
    return bool(resources.get('train_allowed') is True and resources.get('ac_power') is True and
        type(idle) in (int, float) and math.isfinite(idle) and idle >= 900 and
        resources.get('memory_pressure_ok') is True and capacity('disk_free_bytes') >= 4.5*2**30 and
        capacity('available_memory_bytes') >= (7 if admission else 5)*2**30)
