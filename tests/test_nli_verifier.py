"""Independent literal fixtures for the shadow-only NLI boundary."""
import json

import pytest

from moa import nli_verifier as n


def state(left='신청 마감은 10월 15일입니다.', right=None):
    return {'file_role':'notice', 'source_complete':True,
            'source_blocks':[{'id':'b1','text':left}],
            'rendered_blocks':[{'id':'d1','source_refs':['b1'],'visible_text':left if right is None else right}],
            'attachment_manifest':[], 'title':'합성 안내'}


def test_generic_model_labels_cannot_be_guessed_from_numeric_ids():
    with pytest.raises(ValueError, match='semantic'):
        n.validate_mapping({'0':'LABEL_0','1':'LABEL_1','2':'LABEL_2'})
    assert n.validate_mapping({'0':'entailment','1':'neutral','2':'contradiction'}) == {0:'entailment',1:'neutral',2:'contradiction'}


def test_both_directions_are_required_and_preserve_actual_unequal_text():
    p = n.plan_pairs(state(right='신청 마감은 10월 16일입니다.'), lambda a,b:20)
    assert p['complete'] and len(p['pairs']) == 2
    assert p['pairs'][0]['premise'] == '신청 마감은 10월 15일입니다.'
    assert p['pairs'][0]['hypothesis'] == '신청 마감은 10월 16일입니다.'
    assert p['pairs'][1]['premise'] == p['pairs'][0]['hypothesis']
    assert p['pairs'][1]['hypothesis'] == p['pairs'][0]['premise']


def test_unknown_or_missing_dom_refs_cannot_be_silently_excluded():
    s = state(); s['rendered_blocks'][0]['source_refs'] = ['other']
    assert not n.plan_pairs(s, lambda a,b:20)['complete']
    s = state(); s['source_blocks'].append({'id':'b2','text':'신청서는 원본 첨부를 사용합니다.'})
    assert not n.plan_pairs(s, lambda a,b:20)['complete']


def test_neutral_does_not_mean_missing_original_and_one_direction_is_not_equivalence():
    p = n.plan_pairs(state(), lambda a,b:20)
    raw = [{'pair_id':p['pairs'][0]['pair_id'],'label':'entailment'},
           {'pair_id':p['pairs'][1]['pair_id'],'label':'neutral'}]
    out = n.summarize(p, raw)
    assert out['semantic_relation'] == 'not_entailed'
    assert out['quality_label'] is None and out['source_insufficient_inferred'] is False
    assert out['human_review_required'] and not out['publish_allowed']


def test_partial_outputs_and_token_overflow_cannot_turn_into_preserved():
    p = n.plan_pairs(state(), lambda a,b:513)
    assert not p['complete'] and p['failures'][0]['reason'] == 'input_overflow'
    p = n.plan_pairs(state(), lambda a,b:20)
    out = n.summarize(p, [{'pair_id':p['pairs'][0]['pair_id'],'label':'entailment'}])
    assert out['quality_label'] is None and out['status'] == 'incomplete'


def test_long_table_chunks_keep_each_row_header_unit_footnote_and_merge_context():
    table = {'caption':'대여 안내','headers':['물품','대상','보관함'],
             'rows':[['악기'+str(i),'2학년','보관함'+str(i)] for i in range(20)],
             'unit':'대여료: 원','notes':['분실한 경우 반드시 담당 교사에게 알립니다.'],
             'geometry':[[0,0,1,3]]}
    s = state(json.dumps(table,ensure_ascii=False))
    p = n.plan_pairs(s, lambda a,b:600 if a.count('보관함')>3 else 100)
    assert p['complete']
    row_pairs=[v for v in p['pairs'] if '/row/' in v['unit_id']]
    assert len(row_pairs)==40
    for i in range(20):
        pair = row_pairs[2*i]
        assert '악기'+str(i) in pair['premise'] and '보관함'+str(i) in pair['premise']
        assert all(text in pair['premise'] for text in ('물품','대상','대여료: 원','분실한 경우','병합'))


def test_short_table_still_compares_date_as_a_focused_pair_without_losing_other_evidence():
    left={'headers':['물품','교실'],'rows':[['공책','101']],
          'conditions':['신청 마감: 2030년 3월 12일','대상: 2학년'],
          'notes':['원본 영수증을 제출합니다.'],'unit':'수량: 개'}
    right=dict(left,conditions=['신청 마감: 2030년 3월 13일','대상: 2학년'])
    p=n.plan_pairs(state(json.dumps(left,ensure_ascii=False),json.dumps(right,ensure_ascii=False)),lambda a,b:50)
    date=[v for v in p['pairs'] if '/conditions/0' in v['unit_id']]
    assert p['complete'] and len(date)==2
    assert '12일' in date[0]['premise'] and '13일' in date[0]['hypothesis']
    assert '공책' not in date[0]['premise']
    assert any('/row/0' in v for v in p['coverage']['provided'])
    assert any('/notes/0' in v for v in p['coverage']['provided'])


def test_referencing_condition_sentences_are_not_detached_from_each_other():
    facts={'conditions':['신청 마감: 2030년 3월 12일','위 기한 이후에는 접수하지 않습니다.']}
    p=n.plan_pairs(state(json.dumps(facts,ensure_ascii=False)),lambda a,b:50)
    assert p['complete'] and len(p['pairs'])==2
    assert all('위 기한' in v['premise'] and '12일' in v['premise'] for v in p['pairs'])


def test_a_first_row_footnote_keeps_the_actual_reading_position_after_key_matching():
    left={'headers':['대상','보조금'],'rows':[['가팀','100원'],['나팀','200원']],
          'notes':['첫 번째 행의 대상만 추가 지원합니다.']}
    right=dict(left,rows=list(reversed(left['rows'])))
    p=n.plan_pairs(state(json.dumps(left,ensure_ascii=False),json.dumps(right,ensure_ascii=False)),lambda a,b:50)
    pair=next(v for v in p['pairs'] if '/row/0' in v['unit_id'] and v['direction']=='source_to_dom')
    assert '행 위치: 1' in pair['premise'] and '행 위치: 2' in pair['hypothesis']
    assert '첫 번째 행' in pair['premise'] and '첫 번째 행' in pair['hypothesis']


@pytest.mark.parametrize('field', ['conditions','facts','caption'])
def test_row_reference_in_every_prose_field_remains_bound_to_actual_row_position(field):
    left={'headers':['대상','보조금'],'rows':[['가팀','100원'],['나팀','200원']],
          field:'첫 번째 행의 대상만 참가합니다.' if field=='caption' else ['첫 번째 행의 대상만 참가합니다.']}
    right=dict(left,rows=list(reversed(left['rows'])))
    p=n.plan_pairs(state(json.dumps(left,ensure_ascii=False),json.dumps(right,ensure_ascii=False)),lambda a,b:50)
    assert p['complete']
    pair=next(v for v in p['pairs'] if '/row/0' in v['unit_id'] and v['direction']=='source_to_dom')
    assert '첫 번째 행' in pair['premise'] and '첫 번째 행' in pair['hypothesis']
    assert '행 위치: 1' in pair['premise'] and '행 위치: 2' in pair['hypothesis']


def test_positional_reference_in_six_column_table_is_not_reinterpreted_as_flat_row_position():
    left={'headers':['연번','학과','수업교실']*2,
          'rows':[['1','미술','101','3','국어','103'],['2','영어','102','4','과학','104']],
          'conditions':['첫 번째 행의 대상만 참가합니다.']}
    right={'headers':left['headers'][:3], 'rows':[['1','미술','101'],['2','영어','102'],['3','국어','103'],['4','과학','104']],
           'conditions':left['conditions']}
    p=n.plan_pairs(state(json.dumps(left,ensure_ascii=False),json.dumps(right,ensure_ascii=False)),lambda a,b:50)
    assert not p['complete'] and p['failures']


def test_atomic_split_audit_ignores_parent_ids_and_rejects_shared_model_inputs():
    a=n.plan_pairs(state(),lambda a,b:50)
    b=n.plan_pairs(state(),lambda a,b:50)
    for pair in b['pairs']:pair['unit_id']='new-parent/'+pair['unit_id'];pair['pair_id']='different-parent-'+pair['pair_id']
    report=n.audit_pair_overlap([a],[b])
    assert report['overlapping_directional_inputs']==2
    assert report['candidate_cases_fully_repeated']==1 and not report['independent_model_inputs']
    unique=n.plan_pairs(state('새로운 물품 반납 장소는 2층 보관실입니다.'),lambda a,b:50)
    assert n.audit_pair_overlap([a],[unique])['independent_model_inputs']


def test_direct_mismatch_and_incomplete_evidence_cannot_be_waived_by_any_model():
    semantic={'status':'completed','quality_label':'preserved','semantic_relation':'equivalent'}
    assert n.combine('changed', semantic)['quality_label'] == 'changed'
    assert n.combine('insufficient', semantic)['quality_label'] == 'insufficient'
    assert n.combine('preserved', dict(semantic,quality_label=None))['quality_label'] == 'insufficient'


def test_same_revision_with_wrong_pair_identity_is_not_a_valid_model_result():
    p=n.plan_pairs(state(),lambda a,b:20)
    raw=[{'pair_id':'not-the-request','label':'entailment'}]*2
    assert n.summarize(p,raw)['status']=='incomplete'
