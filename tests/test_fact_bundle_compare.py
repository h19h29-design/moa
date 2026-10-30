import copy
import json

from moa import r2_data, verification


def evidence():
    return {'headers':['번호','학과','수업교실'],
            'rows':[['1','국어교육과','2-1'],['2','물리학과','2-2']],
            'geometry':[[0,0,1,3]], 'caption':'학과 배정', 'unit':'원/회',
            'facts':['신청 마감: 2027년 3월 5일','선택 수: 2개'],
            'conditions':['보호 장갑을 착용해야 합니다.'], 'notes':['보증금 별도']}


def test_actual_fact_bundle_relation_reorder_is_not_pair_swap():
    left=evidence();right=copy.deepcopy(left);right['rows'].reverse()
    report=verification.compare_fact_bundle(r2_data.fact_state(left,right))
    assert report['quality_label']=='preserved'
    right['rows'][0][-1],right['rows'][1][-1]=right['rows'][1][-1],right['rows'][0][-1]
    report=verification.compare_fact_bundle(r2_data.fact_state(left,right))
    assert report['quality_label']=='changed' and 'row_relation_mismatch' in report['findings']


def test_fact_bundle_checks_context_units_notes_conditions_and_duplicate_rows():
    left=evidence()
    for field,mutate in [('unit',lambda _: '천원/회'),('notes',lambda _: []),
                         ('conditions',lambda _: []),('headers',lambda _: ['번호','수업교실','학과']),
                         ('rows',lambda rows:rows+rows[:1]),('facts',lambda _:['선택 수: 3개'])]:
        right=copy.deepcopy(left);right[field]=mutate(right[field])
        assert verification.compare_fact_bundle(r2_data.fact_state(left,right))['quality_label']=='changed'
    state=r2_data.fact_state(left,left);state['source_complete']=False
    assert verification.compare_fact_bundle(state)['quality_label']=='insufficient'


def test_malformed_or_unsupported_fact_input_is_not_identity_proof():
    state=r2_data.fact_state(evidence(),evidence())
    for raw in ('not json',json.dumps({'model_answer':'preserved'}),json.dumps({'rows':[['x']]})):
        bad=copy.deepcopy(state);bad['source_blocks'][0]['text']=raw
        assert verification.compare_fact_bundle(bad)['quality_label']=='insufficient'


def test_fact_bundle_gold_and_model_result_are_never_read():
    left=evidence();right=copy.deepcopy(left);right['rows'][0][-1]='3-4'
    state=r2_data.fact_state(left,right)
    state['expected']='preserved';state['model_label']='preserved'
    assert verification.compare_fact_bundle(state)['quality_label']=='changed'


def test_flat_cards_can_drop_unmerged_cell_coordinates_but_not_real_merges():
    left=evidence();left['geometry']=[[0,0,1,1],[0,1,1,1],[0,2,1,1]]
    right=copy.deepcopy(left);right['geometry']=[];right['rows'].reverse()
    assert verification.compare_fact_bundle(r2_data.fact_state(left,right))['quality_label']=='preserved'
    left['geometry']=[[0,0,2,1],[0,1,1,2]]
    assert verification.compare_fact_bundle(r2_data.fact_state(left,right))['quality_label']=='changed'


def test_empty_extraction_never_proves_content_preservation():
    for raw in ({'geometry':[]},{'headers':[]},{'facts':[]},{'facts':['  ']},{'unit':''}):
        assert verification.compare_fact_bundle(r2_data.fact_state(raw,raw))['quality_label']=='insufficient'
