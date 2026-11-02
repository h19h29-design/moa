import copy

import pytest

from moa import nli_challenge as c
from moa.nli_verifier import audit_pair_overlap, plan_pairs


def test_independent_author_operation_keeps_normal_paraphrase_but_labels_changed_condition():
    rows=c.generate('unit-private-seed','unit')
    byop={r['meta']['error_type']:r for r in rows}
    assert byop['semantic_paraphrase']['expected'] == {'conditions':'preserved'}
    assert byop['condition']['expected'] == {'conditions':'changed'}
    assert byop['missing_pages']['expected'] == {'conditions':'insufficient'}
    assert byop['identity']['state'] != byop['semantic_paraphrase']['state']
    assert c.validate(rows,set(),set())['rows'] == 18


def test_wrong_authored_gold_or_transformation_is_rejected_without_model_or_checker():
    rows=c.generate('unit-private-seed','unit')
    wrong=copy.deepcopy(rows);wrong[0]['expected']={'table_relations':'changed'}
    with pytest.raises(ValueError,match='replay'):c.validate(wrong,set(),set())
    wrong=copy.deepcopy(rows);wrong[0]['state']['rendered_blocks'][0]['visible_text']='changed content'
    with pytest.raises(ValueError,match='replay'):c.validate(wrong,set(),set())


def test_protected_original_or_template_never_enters_a_new_cohort():
    rows=c.generate('unit-private-seed','unit')
    with pytest.raises(ValueError,match='protected'):c.validate(rows,{rows[0]['meta']['source_sha256']},set())
    with pytest.raises(ValueError,match='protected'):c.validate(rows,set(),{rows[0]['meta']['family']})


def test_independent_cohort_has_no_repeated_actual_atomic_input_and_every_operation_changes_facts():
    reference=c.generate('reference-dev','dev')+c.generate('reference-test','test')
    rows=c.generate('independent-unit-seed','independent')
    assert c.validate(rows,{r['meta']['source_sha256'] for r in reference},
                      {r['meta']['family'] for r in reference})['groups']==4
    assert len(rows)==58
    plans=lambda values:[plan_pairs(v['state'],lambda a,b:50) for v in values]
    assert audit_pair_overlap(plans(reference),plans(rows))['independent_model_inputs']
    for row in rows:
        op=row['meta']['error_type']
        if op in c.CHANGED:
            assert row['state'] != c.variant(row['meta']['authored_source'],'identity')[0]
