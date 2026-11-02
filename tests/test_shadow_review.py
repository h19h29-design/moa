import copy
import pytest

from moa.r2_data import fact_state
from moa import shadow_review as s


def state():return fact_state({'facts':['참가비 12000원']},{'facts':['참가비 21000원']})


def raw(label='preserved'):
    return {'prediction':label,'prediction_origin':'raw_laya','system_forced_insufficient':False,
            'status':'completed','source_revision':'src1','render_revision':'html1'}


def test_scoped_mismatch_cannot_be_overruled_by_laya_preserved():
    result=s.assess(state(),raw(),source_revision='src1',render_revision='html1',browser_ok=True,role_verified=True)
    assert result['direct']['quality_label']=='changed'
    assert result['model']['prediction']=='preserved'
    assert result['final']['quality_label']=='changed' and result['priority_reasons']==['model_direct_conflict','direct_mismatch']
    assert not result['human_approved'] and not result['publish_allowed']


def test_forms_unknown_roles_and_stale_or_forced_answers_stay_out_of_quality_pass():
    form=state();form['file_role']='form'
    result=s.assess(form,raw(),source_revision='src1',render_revision='html1',browser_ok=True)
    assert result['file_action']=='original_attachment_only' and result['final']['quality_label']=='insufficient'
    normal=fact_state({'facts':['조건 유지']},{'facts':['조건 유지']})
    stale=raw();stale['render_revision']='old'
    result=s.assess(normal,stale,source_revision='src1',render_revision='html1',browser_ok=True)
    assert result['model_status']=='stale_revision' and result['final']['quality_label']=='insufficient'
    forced=raw('insufficient');forced['prediction_origin']='source_complete_rule'
    result=s.assess(normal,forced,source_revision='src1',render_revision='html1',browser_ok=True)
    assert result['model_status']=='not_raw_model' and result['model_accuracy_eligible'] is False


def test_browser_failure_new_structure_and_unverified_role_request_human_not_approval():
    st=state();before=copy.deepcopy(st)
    report=s.assess(st,raw(),source_revision='src1',render_revision='html1',browser_ok=False,new_type=True,role_verified=False)
    assert st==before and report['priority_reasons'][0]=='new_type'
    assert report['final']['quality_label']=='insufficient' and report['human_review_required']
    assert report['source_revision']=='src1' and report['render_revision']=='html1'


def test_unknown_six_columns_are_not_assumed_to_be_department_reflow():
    a={'headers':['구분','이름','실','번호','대상','장소'],'rows':[['a','b','c','d','e','f']]}
    b={'headers':['구분','이름','실'],'rows':[['a','b','c'],['d','e','f']]}
    out=s.assess(fact_state(a,b),raw(),source_revision='src1',render_revision='html1',browser_ok=True)
    assert out['direct']['quality_label']!='preserved' and out['final']['quality_label']!='preserved'


def test_native_dom_adapter_uses_original_rows_not_normalized_renderer_source():
    original=[['연번','학과','수업교실']*2,['1','천문','별실','2','조형','공작실']]
    src={'text':'원문','role':'notice','original_table_rows':original,'table_rows':[['wrong']]}
    dom={'visible_text':'화면','table_rows':[['연번','학과','수업교실'],['1','천문','별실'],['2','조형','공작실']]}
    st=s.native_dom_state(src,dom,source_complete=True)
    assert s.assess(st,raw(),source_revision='src1',render_revision='html1',browser_ok=True)['direct']['quality_label']=='preserved'
    src['role']='form'
    assert s.native_dom_state(src,dom,source_complete=True)['file_role']=='form'


def test_ambiguous_or_ragged_native_table_requires_review_not_text_identity():
    src={'text':'동일','table_rows':[['표 제목'],['구분','내용']]}
    st=s.native_dom_state(src,{'visible_text':'동일','table_rows':src['table_rows']},source_complete=True)
    assert st['source_complete'] is False


@pytest.mark.parametrize('role',[None,'unknown','reference','consent'])
def test_missing_or_unconfirmed_roles_never_become_mobile_notice_candidates(role):
    src={'text':'조건 유지'}
    if role is not None:src['role']=role
    st=s.native_dom_state(src,{'visible_text':'조건 유지'},source_complete=True)
    out=s.assess(st,raw(),source_revision='src1',render_revision='html1',browser_ok=True,role_verified=True)
    assert st['file_role']==(role or 'unknown')
    assert out['file_action']=='original_attachment_only'
    assert out['final']['quality_label']=='insufficient' and out['human_review_required']


def test_notice_role_needs_explicit_confirmation_not_a_default_true():
    st=fact_state({'facts':['동일']},{'facts':['동일']})
    out=s.assess(st,raw(),source_revision='src1',render_revision='html1',browser_ok=True)
    assert out['final']['quality_label']=='insufficient' and 'file_role_unverified' in out['priority_reasons']
