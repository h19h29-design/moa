"""A majority-label collapse is a failure, not a better classifier accuracy."""
import math

import pytest


def api():
    from moa import research_classifier_diagnosis
    return research_classifier_diagnosis


def observations():
    return [dict(case_id=k, prediction='changed',
                 probabilities={'preserved': .32, 'changed': .68}, system_forced=False)
            for k in ('normal', 'error-a', 'error-b')]


def test_majority_collapse_is_rejected_without_changing_raw_decisions():
    records = observations()
    result = api().replay(records, {'normal': 'preserved', 'error-a': 'changed',
                                  'error-b': 'changed'}, train_counts={'preserved': 36, 'changed': 72})
    assert result['raw']['accuracy'] == 2/3
    assert result['raw']['normal_false_positive_rate'] == 1
    assert result['collapsed_to'] == 'changed'
    assert result['content_separation_passed'] is False
    assert result['ranking_auc'] == .5
    assert result['prior_only_diagnostic']['normal_false_positive_rate'] == 1
    assert result['threshold_fitted'] is False
    assert result['operational_promotion'] is False
    assert all(r['prediction'] == 'changed' for r in records)
    assert result['constant_prior_cross_entropy'] == pytest.approx(.6365141682948129)


def test_inverting_every_answer_is_not_a_fix_for_the_other_class():
    records = [dict(r, prediction='preserved',
                    probabilities={'preserved': .68, 'changed': .32}) for r in observations()]
    result = api().replay(records, {'normal': 'preserved', 'error-a': 'changed',
                                  'error-b': 'changed'}, train_counts={'preserved': 36, 'changed': 72})
    assert result['raw']['normal_false_positive_rate'] == 0
    assert result['raw']['recall']['changed'] == 0
    assert result['content_separation_passed'] is False


def test_real_separation_can_pass_but_is_still_not_operational_approval():
    records = observations()[:2]
    records[0] = dict(records[0], prediction='preserved',
                      probabilities={'preserved': .8, 'changed': .2})
    result = api().replay(records, {'normal': 'preserved', 'error-a': 'changed'},
                          train_counts={'preserved': 36, 'changed': 72})
    assert result['content_separation_passed'] is True
    assert result['collapsed_to'] is None
    assert result['ranking_auc'] == 1
    assert result['operational_promotion'] is False


@pytest.mark.parametrize('counts', [{}, {'preserved': 0, 'changed': 2},
                                   {'preserved': True, 'changed': 2}])
def test_missing_or_fabricated_training_counts_cannot_define_prior(counts):
    with pytest.raises(ValueError):
        api().replay(observations(), {'normal': 'preserved', 'error-a': 'changed',
                                      'error-b': 'changed'}, train_counts=counts)


def test_diagnostic_evaluation_time_is_cumulative_not_another_600_seconds():
    assert api().remaining_evaluation(32., [dict(status='complete', seconds=100.)]) == 468.
    assert api().remaining_evaluation(32., [dict(status='stopped', seconds=100.)]) == 468.
    with pytest.raises(ValueError):
        api().remaining_evaluation(32., [dict(status='issued', seconds=None)])
    with pytest.raises(ValueError):
        api().remaining_evaluation(32., [dict(status='complete', seconds=570.)])
    for invalid in (-1., math.nan, math.inf, True):
        with pytest.raises(ValueError): api().remaining_evaluation(invalid, [])


def test_diagnostic_cannot_ignore_the_original_resource_gates():
    resources = dict(train_allowed=True, ac_power=True, idle_seconds=901,
        available_memory_bytes=7*2**30, memory_pressure_ok=True, disk_free_bytes=5*2**30)
    assert api().resources_allowed(resources, admission=True)
    assert not api().resources_allowed(dict(resources, available_memory_bytes=7*2**30-1), admission=True)
    assert api().resources_allowed(dict(resources, available_memory_bytes=5*2**30), admission=False)
    for key, value in [('ac_power', False), ('idle_seconds', 899), ('memory_pressure_ok', False),
                       ('train_allowed', False), ('available_memory_bytes', 5*2**30-1),
                       ('disk_free_bytes', 4*2**30-1)]:
        assert not api().resources_allowed(dict(resources, **{key: value}), admission=False)


def test_constant_prior_baseline_is_scored_on_the_observed_gold_distribution():
    records = [dict(observations()[0], case_id=k,
        probabilities={'preserved':1/3, 'changed':2/3}) for k in ('a','b','c','d')]
    report = api().replay(records,dict(a='preserved',b='preserved',c='preserved',d='changed'),
        train_counts={'preserved':36,'changed':72})
    assert report['constant_prior_cross_entropy'] == pytest.approx(report['cross_entropy'])
    assert report['constant_prior_cross_entropy'] == pytest.approx(.9253254935284468)
    assert report['training_prior_entropy'] == pytest.approx(.6365141682948129)


@pytest.mark.parametrize('key,value', [('ac_power','false'), ('train_allowed','false'),
    ('memory_pressure_ok','unknown'), ('available_memory_bytes',math.inf),
    ('disk_free_bytes',math.inf), ('available_memory_bytes',math.nan),
    ('disk_free_bytes',math.nan), ('available_memory_bytes',True)])
def test_unknown_or_nonfinite_resources_fail_closed(key,value):
    resources = dict(train_allowed=True, ac_power=True, idle_seconds=901,
        available_memory_bytes=7*2**30, memory_pressure_ok=True, disk_free_bytes=5*2**30)
    assert not api().resources_allowed(dict(resources,**{key:value}),admission=False)


def test_incomplete_observations_are_explicitly_rejected():
    records=observations()
    records[0]=dict(records[0],prediction=None,probabilities={})
    with pytest.raises(ValueError,match='complete'):
        api().replay(records,dict(normal='preserved',**{'error-a':'changed','error-b':'changed'}),
            train_counts={'preserved':36,'changed':72})


def test_inconsistent_tie_labels_cannot_count_as_content_separation():
    records=[dict(case_id='normal',prediction='preserved',system_forced=False,
        probabilities={'preserved':.5,'changed':.5}),
        dict(case_id='error',prediction='changed',system_forced=False,
        probabilities={'preserved':.5,'changed':.5})]
    report=api().replay(records,dict(normal='preserved',error='changed'),
        train_counts={'preserved':36,'changed':72})
    assert report['raw']['accuracy']==1
    assert report['content_separation_passed'] is False
    assert report['tied_observations']==2
