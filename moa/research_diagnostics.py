"""Read-only diagnostics over exposed, already executed model observations.

These functions do not infer, fit a threshold, train or qualify a model. The
prior counterfactual is deliberately reported separately from raw predictions.
"""
from __future__ import annotations

from statistics import mean

from .research_binary import LABELS, average_orders, metrics


def _index(rows):
    result = {}
    for row in rows:
        identity = row['case_id']
        if not identity or identity in result or row.get('system_forced'):
            raise ValueError('unique raw observations required')
        if row['expected'] not in LABELS or row['prediction'] not in LABELS:
            raise ValueError('raw binary observations required')
        probs = row['probabilities']
        average_orders(probs, probs)  # Validate the actual two-option distribution.
        if probs[row['prediction']] < max(probs.values()):
            raise ValueError('observation label differs from its raw probabilities')
        result[identity] = row
    return result


def replay_binary(first, second, *, train_counts):
    """Separate order sensitivity from content ranking without another forward.

    The class-prior odds come from train counts, never a threshold search on
    dev/test. Even if their correction reduces false alerts it must not be
    mistaken for semantic improvement when real errors become false passes.
    """
    if (set(train_counts) != set(LABELS) or
            any(type(v) is not int or v <= 0 for v in train_counts.values())):
        raise ValueError('positive binary training counts required')
    left, right = _index(first), _index(second)
    if not left or set(left) != set(right):
        raise ValueError('same nonempty observation identities required')
    combined, counterfactual, scores = [], [], {k: [] for k in LABELS}
    flips = both_wrong = 0
    for identity, a in left.items():
        b = right[identity]
        if any(a.get(k) != b.get(k) for k in ('expected','model','split','operation','family')):
            raise ValueError('the same input/model/gold is required across orders')
        averaged = average_orders(a['probabilities'], b['probabilities'])
        combined.append({'expected': a['expected'], 'prediction': averaged['prediction']})
        score = averaged['probabilities']['changed']
        scores[a['expected']].append(score)
        adjusted = {k: averaged['probabilities'][k]/train_counts[k] for k in LABELS}
        pred = None if adjusted['preserved'] == adjusted['changed'] else max(adjusted,key=adjusted.get)
        counterfactual.append({'expected':a['expected'],'prediction':pred})
        flips += a['prediction'] != b['prediction']
        both_wrong += a['expected'] == 'preserved' and a['prediction'] == b['prediction'] == 'changed'
    normal, errors = scores['preserved'], scores['changed']
    auc = (sum((x > y) + .5*(x == y) for x in errors for y in normal)/
           (len(errors)*len(normal))) if errors and normal else None
    return {'version':'moa.cached-binary-diagnosis.v1','first':metrics(list(left.values())),
            'second':metrics(list(right.values())),'average':metrics(combined),
            'ranking_auc':auc,'score_means':{k:mean(v) if v else None for k,v in scores.items()},
            'order_flips':flips,'normal_wrong_both_orders':both_wrong,
            'prior_only_diagnostic':metrics(counterfactual),
            'prior_odds_changed_over_preserved':train_counts['changed']/train_counts['preserved'],
            'threshold_fitted':False,'new_model_inference':False,'operational_promotion':False,
            'scope':'replay_of_exposed_observations_not_a_new_blind'}
