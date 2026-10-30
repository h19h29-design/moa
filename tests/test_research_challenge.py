import copy
import json

import pytest

from moa import research_challenge as c


def test_new_challenge_has_independent_groups_truth_and_all_error_types():
    rows=c.generate('unit-test-author-seed')
    report=c.validate(rows,protected_source_hashes=set())
    assert report['groups']==8 and report['items']==128
    assert report['labels']=={'preserved':32,'changed':80,'insufficient':16}
    assert set(report['errors']) >= {'date','time','amount','selection','conditions','table_relations','omission','addition','unit','footnote'}
    assert all(r['meta']['split']=='test' and r['meta']['evaluation_only'] for r in rows)
    assert len({json.dumps(r['state'],sort_keys=True)+str(r['questions']) for r in rows})==128


def test_gold_is_recomputed_from_author_facts_not_model_or_checker_outputs():
    rows=c.generate('unit-test-author-seed')
    changed=next(r for r in rows if next(iter(r['expected'].values()))=='changed')
    broken=copy.deepcopy(rows);target=next(r for r in broken if r['case_id']==changed['case_id'])
    target['expected']={next(iter(target['expected'])):'preserved'}
    with pytest.raises(ValueError,match='oracle'):c.validate(broken,set())
    with pytest.raises(ValueError,match='protected'):c.validate(rows,{rows[0]['meta']['source_sha256']})


def test_gold_free_projection_and_seal_precondition():
    rows=c.generate('unit-test-author-seed')
    with pytest.raises(ValueError,match='freeze'):c.project(rows,None)
    out=c.project(rows,{'candidate_sha256':{'r1':'a'*64},'settings_sha256':'b'*64,'checker_sha256':'c'*64})
    assert len(out)==128
    assert all(set(r)=={'case_id','state','questions'} for r in out)
    serialized=json.dumps(out)
    for forbidden in ('expected','operation','unit-test-author-seed','label_origin','error_type'):
        assert forbidden not in serialized


def test_hard_mismatch_overrides_model_and_forced_insufficient_is_not_a_raw_answer():
    assert c.combine('changed','preserved')=='changed'
    assert c.combine('preserved','changed')=='preserved'
    assert c.combine('insufficient','changed')=='changed'
    rows=c.generate('unit-test-author-seed')
    raw=[{'case_id':r['case_id'],'prediction':'preserved','prediction_origin':'raw_laya'} for r in rows]
    report=c.evaluate(rows,raw,lambda s:{'quality_label':'insufficient'})
    assert report['model']['correct']==32 and report['model']['changed_detected']==0
    assert report['additional_errors_caught']==0
    raw[0]['prediction_origin']='source_complete_rule'
    with pytest.raises(ValueError,match='raw'):c.evaluate(rows,raw,lambda s:{'quality_label':'insufficient'})


@pytest.mark.parametrize('label',['changed','insufficient',None])
def test_combination_reports_extra_normal_review_even_when_quality_stays_preserved(label):
    rows=[r for r in c.generate('review-burden-seed') if next(iter(r['expected'].values()))=='preserved'][:1]
    raw=[{'case_id':rows[0]['case_id'],'prediction':label,'prediction_origin':'raw_laya',
          'status':'completed' if label else 'input_overflow'}]
    report=c.evaluate(rows,raw,lambda s:{'quality_label':'preserved'})
    assert report['combined']['correct']==1
    assert report['extra_reviews_after_direct_identity_priority']==1
