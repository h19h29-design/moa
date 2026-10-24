"""Case-bound atomic roles, never a bag of nouns or a quantifier rewrite."""
import pytest

from moa.r2_data import fact_state
from moa.scoped_verification import compare_scoped_facts


def label(left, right):
    return compare_scoped_facts(fact_state({'conditions': [left]},
                                          {'conditions': [right]}))['quality_label']


@pytest.mark.parametrize('left,right', [
    ('안전담당자는 반드시 출입증을 확인합니다.', '출입증은 안전담당자가 반드시 확인합니다.'),
    ('탐구학생은 반드시 면장갑을 지참합니다.', '면장갑은 탐구학생이 반드시 지참합니다.'),
    ('보호자는 반드시 참가서를 작성해야 합니다.', '참가서는 보호자가 반드시 작성하여야 합니다.'),
    ('지도교사는 반드시 등록표를 제출합니다.', '등록표는 지도교사가 반드시 제출해 주세요.'),
    ('교사는 반드시 책을 지참합니다.', '책은 교사가 반드시 지참합니다.'),
])
def test_unquantified_atomic_roles_keep_the_same_actor_object_and_must(left, right):
    assert label(left, right) == 'preserved'
    assert label(right, left) == 'preserved'


@pytest.mark.parametrize('left,right', [
    ('안전담당자는 반드시 출입증을 확인합니다.', '출입증은 탐구학생이 반드시 확인합니다.'),
    ('안전담당자는 반드시 출입증을 확인합니다.', '안전담당자는 출입증이 반드시 확인합니다.'),
    ('지도교사는 반드시 등록표를 제출합니다.', '등록표는 지도교사가 제출합니다.'),
    ('지도교사는 반드시 등록표를 제출합니다.', '등록표는 지도교사가 반드시 지참합니다.'),
    ('지도교사는 반드시 등록표를 제출합니다.', '신청서는 지도교사가 반드시 제출합니다.'),
    ('지도교사는 반드시 등록표를 제출합니다.', '등록표는 지도교사가 반드시 제출하지 않습니다.'),
    ('지도교사는 반드시 등록표를 제출합니다.', '등록표는 지도교사가 반드시 제출할 수 있습니다.'),
    ('지도교사만은 반드시 등록표를 제출합니다.', '등록표는 지도교사만이 반드시 제출합니다.'),
    ('지도교사는 반드시 등록표만을 제출합니다.', '등록표만은 지도교사가 반드시 제출합니다.'),
    ('지도교사는 반드시 등록표도 제출합니다.', '등록표는 지도교사가 반드시 제출합니다.'),
    ('모든 학생은 반드시 한 과목을 선택합니다.', '한 과목은 모든 학생이 반드시 선택합니다.'),
    ('학생은 반드시 하나를 선택합니다.', '하나는 학생이 반드시 선택합니다.'),
    ('전원은 반드시 참가서를 작성합니다.', '참가서는 전원이 반드시 작성합니다.'),
    ('학생모두는 반드시 참가서를 작성합니다.', '참가서는 학생모두가 반드시 작성합니다.'),
    ('각자는 반드시 참가서를 작성합니다.', '참가서는 각자가 반드시 작성합니다.'),
    ('학생은 반드시 두개를 선택합니다.', '두개는 학생이 반드시 선택합니다.'),
    ('학생은 반드시 삼과목을 선택합니다.', '삼과목은 학생이 반드시 선택합니다.'),
    ('학생은 반드시 아무것을 확인합니다.', '아무것은 학생이 반드시 확인합니다.'),
    ('학생은 반드시 자신을 확인합니다.', '자신은 학생이 반드시 확인합니다.'),
    ('학생은 반드시 확인하고 제출합니다.', '확인하고는 학생이 반드시 제출합니다.'),
    ('학생은 반드시 신청하면 참여합니다.', '신청하면은 학생이 반드시 참여합니다.'),
    ('전교생은 반드시 한쪽을 선택합니다.', '한쪽은 전교생이 반드시 선택합니다.'),
    ('양측은 반드시 한쪽을 선택합니다.', '한쪽은 양측이 반드시 선택합니다.'),
    ('학생은 반드시 절반을 선택합니다.', '절반은 학생이 반드시 선택합니다.'),
    ('각인은 반드시 출입증을 확인합니다.', '출입증은 각인이 반드시 확인합니다.'),
    ('학생에게는 반드시 등록표를 제출합니다.', '등록표는 학생에게가 반드시 제출합니다.'),
    ('학부모께는 반드시 참가서를 제출합니다.', '참가서는 학부모께가 반드시 제출합니다.'),
    ('학생은 반드시 출입증을 확인합니다.', '출입증은 학생가 반드시 확인합니다.'),
    ('보호자는 반드시 출입증을 확인합니다.', '출입증은 보호자이 반드시 확인합니다.'),
    ('학생에게서는 반드시 등록표를 제출합니다.', '등록표는 학생에게서가 반드시 제출합니다.'),
    ('교사로서는 반드시 등록표를 제출합니다.', '등록표는 교사로서가 반드시 제출합니다.'),
    ('보호자처럼은 반드시 등록표를 제출합니다.', '등록표는 보호자처럼이 반드시 제출합니다.'),
    ('개개인은 반드시 참가서를 작성합니다.', '참가서는 개개인이 반드시 작성합니다.'),
    ('전직원은 반드시 참가서를 작성합니다.', '참가서는 전직원이 반드시 작성합니다.'),
])
def test_role_rewrite_must_not_hide_a_semantic_or_scope_change(left, right):
    assert label(left, right) == 'changed'


def test_distinct_actions_for_one_subject_cannot_be_exchanged():
    left = ['지도교사는 반드시 등록표를 제출합니다.', '지도교사는 반드시 면장갑을 지참합니다.']
    right = ['면장갑은 지도교사가 반드시 제출합니다.', '등록표는 지도교사가 반드시 지참합니다.']
    assert compare_scoped_facts(fact_state({'conditions': left}, {'conditions': right}))['quality_label'] == 'changed'


def test_full_role_correspondence_carries_a_trace_and_is_not_human_approval():
    left = {'conditions': ['안전담당자는 반드시 출입증을 확인합니다.']}
    right = {'conditions': ['출입증은 안전담당자가 반드시 확인합니다.']}
    report = compare_scoped_facts(fact_state(left, right))
    assert report['quality_label'] == 'preserved'
    proof = report['clause_correspondence']['conditions']
    assert proof['source_role_bindings'] == proof['dom_role_bindings'] == [
        {'actor': '안전담당자', 'object': '출입증', 'action': '확인', 'obligation': 'explicit_must'}]
    assert report['human_approved'] is False
    assert report['whole_original_verified'] is False
