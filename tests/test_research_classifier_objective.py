"""Training-only class weights; no development-set threshold selection."""
import pytest


def api():
    from moa import research_classifier_objective
    return research_classifier_objective


def test_training_counts_balance_total_normal_and_error_loss_mass():
    weights = api().class_weights({'preserved':36, 'changed':72})
    assert weights == (1.5, .75)
    assert weights[0]*36 == weights[1]*72 == 54
    assert (weights[0]*36+weights[1]*72)/108 == 1


@pytest.mark.parametrize('counts', [{}, {'preserved':0,'changed':72},
    {'preserved':True,'changed':72}, {'preserved':36.,'changed':72},
    {'preserved':36,'changed':72,'insufficient':18}])
def test_weights_require_exact_positive_integer_training_inventory(counts):
    with pytest.raises(ValueError): api().class_weights(counts)
