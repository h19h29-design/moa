"""Research-only two-choice semantics must not claim rule answers as model skill."""
import copy
import importlib

import pytest

from moa import evidence


def api():
    spec = importlib.util.find_spec('moa.research_binary')
    assert spec is not None, 'separate evidence gate and two-choice research path required'
    return importlib.import_module('moa.research_binary')


def state():
    return {'title':'', 'file_role':'notice', 'source_complete':True,
        'source_blocks':[{'id':'head','text':'학과 | 교실'},
                         {'id':'main','text':'보존학 | 2-1'},
                         {'id':'note','text':'※ 사전 신청 필수'}],
        'rendered_blocks':[{'id':'h-'+k,'source_refs':[k],'visible_text':v}
            for k,v in [('head','학과 | 교실'),('main','보존학 | 2-1'),('note','※ 사전 신청 필수')]],
        'attachment_manifest':[]}


def provenance():
    return {'source_revision':'src-1','render_revision':'dom-1',
        'file':{'sha256':'a'*64,'integrity_verified':True,
                'expected_pages':['1'],'available_pages':['1']},
        'scope':{'required_context_ids':['head','main','note'],
                 'provided_context_ids':['head','main','note'],'table_inventory_complete':True},
        'tables':[{'id':'t1','source_block_id':'main',
            'required':{'headers':['head'],'footnotes':['note'],'merges':[]},
            'provided':{'headers':['head'],'footnotes':['note'],'merges':[]}}],
        'role':{'value':'notice','confirmed_by':'synthetic_author',
                'source_sha256':'a'*64,'source_revision':'src-1'}}


def test_dom_omission_is_not_missing_source_evidence():
    s,p = state(),provenance()
    s['rendered_blocks'] = []
    assess = getattr(evidence, 'assess_source_evidence', None)
    assert callable(assess), 'source acquisition must not depend on HTML omissions'
    r = assess(p,s)
    assert not r['review_required'] and r['source_complete']
    assert r['state_binding_sha256'] == evidence.state_binding(s)
    assert r['authority'] == 'source_acquisition_inventory_not_model_or_gold'
    # The existing production v2 behavior remains unchanged.
    assert evidence.assess_evidence(p,s)['review_required']


@pytest.mark.parametrize('missing', ['page','header','note','role','unknown_pages'])
def test_evidence_gap_prevents_model_call_even_if_complete_boolean_is_true(missing):
    m=api();s,p=state(),provenance()
    if missing=='page': p['file']['available_pages']=[]
    if missing=='unknown_pages': p['file']['expected_pages']=None
    if missing in ('header','note'):
        k='head' if missing=='header' else 'note'
        s['source_blocks']=[b for b in s['source_blocks'] if b['id']!=k]
        p['scope']['provided_context_ids'].remove(k)
        p['tables'][0]['provided']['headers' if missing=='header' else 'footnotes']=[]
    if missing=='role':p['role']['confirmed_by']='parser_hint'
    result=m.route(s,p)
    assert not result['model_eligible']
    assert result['quality_label']=='insufficient'
    assert result['model_raw'] is None
    assert result['decision_origin']=='evidence_rule'
    assert not result['human_approved'] and not result['publish_allowed']


def test_only_two_actual_options_no_third_probability_renormalization():
    m=api()
    q=m.binary_questions(['table_relations'])['table_relations']
    assert list(q['criteria'])==['preserved','changed']
    with pytest.raises(ValueError,match='two'):
        m.route(state(),provenance(),model_raw={'prediction':'preserved',
            'probabilities':{'preserved':.7,'changed':.2,'insufficient':.1}})
    out=m.route(state(),provenance(),model_raw={'prediction':'changed',
        'probabilities':{'preserved':.2,'changed':.8}})
    assert out['model_eligible'] and out['quality_label']=='changed'
    assert out['model_raw']['prediction']=='changed'


@pytest.mark.parametrize('missing', ['head','note'])
def test_acquired_context_must_also_be_present_in_actual_model_input(missing):
    s,p=state(),provenance()
    s['source_blocks']=[b for b in s['source_blocks'] if b['id']!=missing]
    # The acquisition inventory still lists it, but the semantic model cannot
    # compare evidence it did not actually receive.
    result=api().route(s,p)
    assert not result['model_eligible']
    assert result['decision_origin']=='evidence_rule'
    assert 'missing_scope_context' in result['evidence']['reasons']


def test_confirmed_direct_mismatch_cannot_be_overridden_by_binary_model():
    out=api().route(state(),provenance(),direct_content={
        'prediction':'changed','confirmed_mismatch':True},model_raw={
        'prediction':'preserved','probabilities':{'preserved':.9,'changed':.1}})
    assert out['quality_label']=='changed' and out['decision_origin']=='direct_confirmed_mismatch'
    assert out['model_raw']['prediction']=='preserved'


def test_training_preparation_excludes_gaps_preserves_groups_and_protects_eval():
    m=api()
    def row(identity,label):
        return {'case_id':identity,'state':state(), 'questions':{'table_relations':{}},
            'expected':{'table_relations':label},'meta':{'split':'train','synthetic_gold':True,
                'human_gold':False,'evaluation_only':False,'rights_status':'allowed',
                'rights_evidence':'independently_authored_synthetic_by_moa',
                'label_origin':'independent_fact_edit_replay','forbidden_lineage':[],
                'family_group_id':'family-'+identity,'original_group_id':'group-'+identity}}
    rows=[row('a','preserved'),row('b','changed'),row('c','insufficient')]
    inventories={r['case_id']:provenance() for r in rows}
    inventories['c']['file']['expected_pages']=None
    original=copy.deepcopy(rows)
    result=m.prepare_split(rows,inventories,split='train')
    assert [r['case_id'] for r in result['rows']]==['a','b']
    assert result['excluded_ids']==['c']
    assert result['rows'][0]['meta']['family_group_id']=='family-a'
    assert rows==original and not result['training_authorized']
    rows[0]['meta']['evaluation_only']=True
    with pytest.raises(ValueError,match='evaluation'):
        m.prepare_split(rows,inventories,split='train')


def test_binary_model_metrics_cannot_count_rule_insufficiency_as_model_success():
    m=api()
    score=getattr(m,'metrics',None)
    assert callable(score), 'binary performance must use only evidence-complete model answers'
    rows=[{'expected':g,'prediction':p} for g,p in
        [('preserved','preserved'),('preserved','changed'),
         ('changed','preserved'),('changed','changed')]]
    result=score(rows)
    assert result['accuracy']==.5 and result['balanced_accuracy']==.5
    assert result['normal_false_positive_rate']==.5 and result['error_miss_rate']==.5
    with pytest.raises(ValueError,match='binary'):
        score(rows+[{'expected':'insufficient','prediction':'insufficient'}])


def test_binary_input_order_survives_json_transport_without_gold_or_rule_answers():
    import json
    m=api()
    normalize=getattr(m,'model_input',None)
    assert callable(normalize), 'one explicit two-choice tokenizer input seam required'
    row={'case_id':'x','state':state(),'questions':m.binary_questions(['table_relations']),
         'expected':{'table_relations':'changed'},'meta':{'operation':'test'}}
    transported=json.loads(json.dumps(row,sort_keys=True))
    actual=normalize(transported)
    assert list(actual['questions']['table_relations']['criteria'])==['preserved','changed']
    assert list(actual['state'])==['title','file_role','source_complete','source_blocks',
                                  'rendered_blocks','attachment_manifest']
    assert set(actual)=={'case_id','state','questions'}
    assert actual['state']['rendered_blocks']==row['state']['rendered_blocks']
    transported['questions']['table_relations']['instructions']='different question'
    with pytest.raises(ValueError,match='question'):
        normalize(transported)


def test_two_actual_option_orders_are_averaged_without_fitting_a_threshold():
    m=api();combine=getattr(m,'average_orders',None)
    assert callable(combine), 'remove positional dependence without fitting weights'
    a={'preserved':.8,'changed':.2};b={'changed':.6,'preserved':.4}
    result=combine(a,b)
    assert result['probabilities']['preserved']==pytest.approx(.6)
    assert result['prediction']=='preserved'
    assert combine(a,b)==combine(b,a)
    assert combine({'preserved':.8,'changed':.2},{'preserved':.2,'changed':.8})['prediction'] is None
    with pytest.raises(ValueError,match='two'):
        combine(a,{'preserved':.4,'changed':.4,'insufficient':.2})
