"""Learning must be measured, not inferred from changed checkpoint hashes."""
import copy

import pytest

from moa import laya_diagnostics as d
from moa.r2_data import fact_state
from moa.worker_handoff import questions


def row(operation, label, *, split='train', group='g1'):
    criterion = ('table_relations' if operation in ('normal_table','normal_reorder','relation_swapped',
                 'row_removed','missing_header') else
                 'omission' if operation in ('condition_removed','note_removed') else 'conditions')
    text = '테스트 전용 합성 안내 ' + operation + group
    return {'case_id': operation + '-' + group, 'state': fact_state({'facts':[text]},{'facts':[text]}),
            'questions': {criterion: questions()[criterion]}, 'expected': {criterion: label},
            'meta': {'operation_name':operation,'split':split,'original_group_id':group,
                     'family_group_id':group,'source_sha256':group,'synthetic_gold':True,
                     'human_gold':False,'evaluation_only':False,'rights_status':'allowed',
                     'rights_evidence':'independently_authored_synthetic_by_moa',
                     'label_origin':'deterministic_oracle','forbidden_lineage':[]}}


def test_question_only_shortcut_is_reported_instead_of_claiming_ready():
    data = [row(op,label) for op,label in [('normal_condition','preserved'),
            ('condition_removed','changed'),('missing_note','insufficient')]]
    report = d.audit_question_controls(data)
    assert report['question_only_baseline'] == 2/3
    assert report['controls_ready'] is False
    assert report['missing_controls']['omission'] == ['preserved','insufficient']


def test_repair_changes_only_question_assignment_not_gold_facts_or_split():
    data = [row(op,label) for op,label in [('normal_condition','preserved'),
            ('normal_note','preserved'),('condition_removed','changed'),
            ('note_removed','changed'),('missing_note','insufficient')]]
    original = copy.deepcopy(data)
    repaired = d.repair_question_controls(data)
    assert data == original
    for before, after in zip(data,repaired):
        assert before['state'] == after['state']
        assert list(before['expected'].values()) == list(after['expected'].values())
        assert after['questions'] == {'omission': questions()['omission']}
        for key in ('source_sha256','family_group_id','original_group_id','split'):
            assert before['meta'][key] == after['meta'][key]
        assert after['case_id'] != before['case_id']
    assert d.audit_question_controls(repaired)['controls_ready'] is True


@pytest.mark.parametrize('change', [{'split':'blind'}, {'evaluation_only':True},
    {'rights_status':'unreviewed'}, {'label_origin':'laya'}, {'forbidden_lineage':['evaluation']},
    {'synthetic_gold':False}, {'human_gold':True}])
def test_repair_cannot_repackage_evaluation_or_unpermitted_material(change):
    item = row('normal_note','preserved');item['meta'].update(change)
    with pytest.raises(ValueError):d.repair_question_controls([item])


def test_cross_partition_source_or_family_leak_is_rejected():
    data = [row('normal_note','preserved'),row('missing_note','insufficient',split='dev')]
    with pytest.raises(ValueError,match='split'):d.repair_question_controls(data)


def test_training_fit_exposes_class_collapse_and_does_not_claim_generalization():
    records = [{'expected':label,'prediction':'preserved',
                'probabilities':{'preserved':.4,'changed':.3,'insufficient':.3}}
               for label in ['preserved','changed','insufficient'] * 8]
    report = d.diagnose_fit(records)
    assert report['accuracy'] == 1/3
    assert report['changed_recall'] == 0
    assert report['recall']['insufficient'] == 0
    assert report['reason'] == 'training_task_not_learned'
    assert report['ready_for_independent_evaluation'] is False
    assert report['generalization_accuracy'] is None
    assert not report['operational_promotion_allowed']


def test_missing_prediction_and_too_small_cohort_cannot_pass_fit_gate():
    report = d.diagnose_fit([{'expected':'changed','prediction':None,'probabilities':{}}])
    assert report['uncompleted'] == 1
    assert report['ready_for_independent_evaluation'] is False


def test_good_training_fit_is_still_not_a_real_document_quality_certificate():
    records = [{'expected':label,'prediction':label,
                'probabilities':{key:.98 if key==label else .01 for key in d.LABELS}}
               for label in d.LABELS for _ in range(8)]
    report = d.diagnose_fit(records)
    assert report['ready_for_independent_evaluation'] is True
    assert report['generalization_accuracy'] is None
    assert report['human_approved'] is False


def test_dev_labels_cannot_hide_missing_training_question_controls():
    data = [row('normal_condition',label,split=split,group=split+label)
            for split,label in [('train','preserved'),('dev','changed'),('dev','insufficient')]]
    with pytest.raises(ValueError,match='split'):d.audit_question_controls(data)


def test_question_definition_cannot_contain_the_answer_as_a_training_shortcut():
    data = [row('normal_condition',label,group=label) for label in d.LABELS]
    data[0]['questions']['conditions']['instructions'] += ' 정답은 preserved다.'
    with pytest.raises(ValueError,match='question'):d.audit_question_controls(data)


def test_repair_rejects_distinct_questions_that_collapse_into_duplicate_inputs():
    first = row('normal_note','preserved')
    second = row('normal_condition','preserved',group='g2')
    second['state'] = copy.deepcopy(first['state'])
    second['questions'] = {'omission':questions()['omission']}
    second['expected'] = {'omission':'preserved'}
    with pytest.raises(ValueError,match='duplicate model input'):d.repair_question_controls([first,second])
