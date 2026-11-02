"""Replaying exposed observations is diagnosis, not another blind/model run."""
import importlib
import importlib.util

import pytest


def api():
    assert importlib.util.find_spec('moa.research_diagnostics'), 'cached causal diagnostics required'
    return importlib.import_module('moa.research_diagnostics')


def observed(identity, gold, changed):
    return {'case_id': identity, 'expected': gold,
            'prediction': 'changed' if changed > .5 else 'preserved',
            'probabilities': {'preserved': 1-changed, 'changed': changed},
            'system_forced': False}


def test_separates_order_failures_from_persistent_content_failures():
    first = [observed('n1','preserved',.8), observed('n2','preserved',.2),
             observed('e1','changed',.4), observed('e2','changed',.9)]
    second = [observed('n1','preserved',.6), observed('n2','preserved',.4),
              observed('e1','changed',.8), observed('e2','changed',.7)]
    report = api().replay_binary(first, second, train_counts={'preserved':2,'changed':4})
    assert report['average']['correct'] == 3
    assert report['average']['normal_false_positive_rate'] == .5
    assert report['order_flips'] == 1
    assert report['normal_wrong_both_orders'] == 1
    assert report['ranking_auc'] == .75
    assert report['score_means']['preserved'] == pytest.approx(.5)
    assert report['score_means']['changed'] == pytest.approx(.7)
    assert report['prior_only_diagnostic']['error_miss_rate'] == .5
    assert report['new_model_inference'] is False
    assert report['threshold_fitted'] is False
    assert report['operational_promotion'] is False


def test_tied_content_scores_are_not_called_good_discrimination():
    rows = [observed('n','preserved',.6), observed('e','changed',.6)]
    report = api().replay_binary(rows, rows, train_counts={'preserved':1,'changed':2})
    assert report['ranking_auc'] == .5
    assert report['average']['balanced_accuracy'] == .5
    assert report['prior_only_diagnostic']['normal_false_positive_rate'] == 0
    assert report['prior_only_diagnostic']['error_miss_rate'] == 1


@pytest.mark.parametrize('fault', ['missing','duplicate','gold','forced','bad_prediction'])
def test_replay_rejects_mismatched_or_nonraw_observations(fault):
    first = [observed('n','preserved',.7), observed('e','changed',.8)]
    second = [observed('n','preserved',.7), observed('e','changed',.8)]
    if fault == 'missing': second.pop()
    if fault == 'duplicate': second.append(second[0])
    if fault == 'gold': second[0]['expected']='changed'
    if fault == 'forced': second[0]['system_forced']=True
    if fault == 'bad_prediction': second[0]['prediction']='preserved'
    with pytest.raises(ValueError):
        api().replay_binary(first, second, train_counts={'preserved':1,'changed':2})


def test_prior_diagnostic_uses_only_positive_training_counts():
    rows = [observed('n','preserved',.6), observed('e','changed',.6)]
    with pytest.raises(ValueError):
        api().replay_binary(rows, rows, train_counts={'preserved':0,'changed':2})
