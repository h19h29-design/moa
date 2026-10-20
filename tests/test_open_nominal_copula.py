"""Development grammar cases, not fresh independent evaluation or human gold."""
import pytest

from moa.r2_data import fact_state
from moa.scoped_verification import compare_scoped_facts


@pytest.mark.parametrize('source,dom', [
    ('이름은 「누비루」입니다.', '이름은 「누비루」예요.'),
    ('교실 명칭은 해든솔교실입니다.', '교실 명칭은 해든솔교실이에요.'),
    ('행사명은 "고요한 돌결"입니다.', '행사명은 "고요한 돌결"이에요.'),
    ('우리 학교는 가온샘중학교입니다.', '우리 학교는 가온샘중학교예요.'),
    ('오늘 장소는 나래울배움터입니다.', '오늘 장소는 나래울배움터예요.'),
    ('수업 장소는 누리샘제2교실입니다.', '수업 장소는 누리샘제2교실이에요.'),
    ('지원 학과는 은하돌탐구학과입니다.', '지원 학과는 은하돌탐구학과예요.'),
    ('준비물은 「물결돌」입니다.', '준비물은 「물결돌」이에요.'),
    ("제목은 '푸른하루'입니다.", "제목은 '푸른하루'예요."),
    ('이는 “늘봄터”입니다.', '이는 “늘봄터”예요.'),
])
@pytest.mark.parametrize('field', ['facts', 'conditions', 'notes'])
def test_nominal_evidence_supports_unregistered_names_without_rewriting_them(source, dom, field):
    for a, b in ((source, dom), (dom, source)):
        result = compare_scoped_facts(fact_state({field: [a]}, {field: [b]}))
        assert result['quality_label'] == 'preserved'
        assert result['clause_correspondence'][field]['complete_ordered_prefixes_equal']
        assert not result['human_approved'] and not result['whole_original_verified']


@pytest.mark.parametrize('source,dom', [
    ('아이들이 모입니다.', '아이들이 모예요.'),
    ('도서관은 움직입니다.', '도서관은 움직이에요.'),
    ('장소는 보입니다.', '장소는 보예요.'),
    ('학생은 비용을 줄입니다.', '학생은 비용을 줄이에요.'),
    ('해든솔은 움직입니다.', '해든솔은 움직이에요.'),
    ('이름은 화면에 보입니다.', '이름은 화면에 보예요.'),
    ('학교명은 담당자가 붙입니다.', '학교명은 담당자가 붙이에요.'),
    ('이름은 보입니다.', '이름은 보예요.'),
    ('대상은 누비루입니다.', '대상은 누비루예요.'),  # no nominal evidence
    ('이름은 누비루입니다.', '이름은 누비루예요.'),
    ('학교는 가온샘중학교입니다.', '학교는 다른샘중학교예요.'),
    ('학과는 은하돌탐구학과입니다.', '학과는 은하돌관측학과예요.'),
    ('명칭은 누비루입니다.', '명칭은 누비로예요.'),
    ('이름은 해든솔입니다.', '이름은 해든솔예요.'),
    ('이름은 누비루입니다.', '이름은 누비루이에요.'),
    ('준비물은 「물결돌」입니다.', '준비물은 「물결꽃」이에요.'),
    ('준비물은 「물결돌」입니다.', '준비물은 「물결돌」예요.'),
    ('준비물은 「물결돌입니다.', '준비물은 「물결돌이에요.'),
    ('준비물은 「물결돌」입니다.', '준비물은 「물결돌이에요.」'),
    ('학교는 가온샘중학교입니다.', '학교는 가온샘중학교이었어요.'),
    ('학교는 가온샘중학교입니다.', '학교는 가온샘중학교가 아니에요.'),
    ('학교는 가온샘중학교입니다.', '학교만 가온샘중학교예요.'),
    ('교실은 누리샘제2교실입니다.', '교실은 누리샘제3교실이에요.'),
    ('명칭은 누비루입니다. 참가비는 3,000원입니다.',
     '명칭은 누비루예요. 참가비는 300원이에요.'),
])
def test_nominal_evidence_never_hides_verb_or_literal_content_changes(source, dom):
    for a, b in ((source, dom), (dom, source)):
        assert compare_scoped_facts(fact_state({'facts': [a]}, {'facts': [b]}))['quality_label'] == 'changed'


def test_unknown_name_proof_does_not_override_context_or_source_reference_guards():
    state = fact_state({'facts': ['이름은 「누비루」입니다.']}, {'facts': ['이름은 「누비루」예요.']})
    assert compare_scoped_facts(state)['quality_label'] == 'preserved'
    state['source_complete'] = False
    assert compare_scoped_facts(state)['quality_label'] == 'insufficient'
    state['source_complete'] = True
    state['rendered_blocks'][0]['source_refs'] = ['other']
    assert compare_scoped_facts(state)['quality_label'] != 'preserved'
