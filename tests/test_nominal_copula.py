"""Hand-labelled public development cases, not independent evaluation."""
import copy

import pytest

from moa.r2_data import fact_state
from moa.scoped_verification import compare_scoped_facts


def compare(source, dom, field='facts'):
    state = fact_state({field: [source]}, {field: [dom]})
    original = copy.deepcopy(state)
    result = compare_scoped_facts(state)
    assert state == original
    assert not result['human_approved'] and not result['whole_original_verified']
    return result


@pytest.mark.parametrize('source,dom', [
    ('참여 대상은 중학생입니다.', '참여 대상은 중학생이에요.'),
    ('담당은 학부모입니다.', '담당은 학부모예요.'),
    ('준비물은 필기구입니다.', '준비물은 필기구예요.'),
    ('다음 주는 체험 기간입니다.', '다음 주는 체험 기간이에요.'),
    ('참가비는 무료입니다.', '참가비는 무료예요.'),
    ('해당 서류는 필수입니다.', '해당 서류는 필수예요.'),
    ('장소는 도서관입니다.', '장소는 도서관이에요.'),
    ('설명회는 금요일입니다.', '설명회는 금요일이에요.'),
    ('제출 기한은 2032년 4월 18일입니다.', '제출 기한은 2032년 4월 18일이에요.'),
    ('시간은 오후 3시입니다.', '시간은 오후 3시예요.'),
    ('선택 수는 2개입니다.', '선택 수는 2개예요.'),
    ('참가비는 4,200원입니다.', '참가비는 4,200원이에요.'),
    ('참가비는 3만 원입니다.', '참가비는 3만 원이에요.'),
    ('준비금은 2.5천원입니다.', '준비금은 2.5천원이에요.'),
    ('구매량은 1.5kg입니다.', '구매량은 1.5kg이에요.'),
    ('이것입니다.', '이것이에요.'),
    ('「신청」 확인은 필수입니다.', '「신청」 확인은 필수예요.'),
])
@pytest.mark.parametrize('field', ['facts', 'conditions', 'notes'])
def test_present_nominal_speech_level_preserves_independently_recognized_nouns(source, dom, field):
    # Removing nominal support must recreate the FP; no expected value is
    # computed by the normalizer. The entire antecedent and clause order stay.
    assert compare(source, dom, field)['quality_label'] == 'preserved'
    assert compare(dom, source, field)['quality_label'] == 'preserved'


@pytest.mark.parametrize('source,dom', [
    ('아이들이 모입니다.', '아이들이 모예요.'),
    ('학생은 움직입니다.', '학생은 움직이에요.'),
    ('학생은 돈을 떼입니다.', '학생은 돈을 떼예요.'),
    ('설명이 보입니다.', '설명이 보예요.'),
    ('담당은 학부모입니다.', '담당은 학부모이에요.'),
    ('장소는 도서관입니다.', '장소는 도서관예요.'),
    ('대상은 학생입니다.', '대상은 학 생이에요.'),
    ('대상은 학생입니다.', '대상은 교사예요.'),
    ('기간은 2일입니다.', '기간은 3일이에요.'),
    ('시간은 오후 3시입니다.', '시간은 오전 3시예요.'),
    ('참가비는 4,200원입니다.', '참가비는 420원이에요.'),
    ('참가비는 3만 원입니다.', '참가비는 3천 원이에요.'),
    ('선택 수는 2개입니다.', '선택 수는 2명예요.'),
    ('접수는 필수입니다.', '접수는 선택이에요.'),
    ('신청자만 대상입니다.', '신청자도 대상이에요.'),
    ('장소는 도서관입니다.', '장소는 도서관이었어요.'),
    ('대상은 학생입니다.', '대상은 학생이 아니에요.'),
    ('장소는 도서관입니다.', '장소는 도서관일 거예요.'),
    ('“필수”는 안내 문구입니다.', '“선택”은 안내 문구예요.'),
    ('“참가비는 무료입니다.”', '“참가비는 무료예요.”'),
    ('“장소는 도서관입니다.', '“장소는 도서관이에요.'),
    ('필기구입니다.', '필기구 예요.'),
    # Unknown terms / unit pronunciation remain strict, not guessed via suffix.
    ('이름은 누비루입니다.', '이름은 누비루예요.'),
    ('암호는 ABC입니다.', '암호는 ABC예요.'),
    ('수량은 21입니다.', '수량은 21이에요.'),
])
def test_nominal_normalization_cannot_hide_verbs_unknowns_or_content_changes(source, dom):
    assert compare(source, dom)['quality_label'] == 'changed'
    assert compare(dom, source)['quality_label'] == 'changed'


def test_copula_equivalence_does_not_move_references_or_hide_missing_context():
    left = {'facts': ['1. 대상은 학생입니다.', '2. 위 조건은 필수입니다.']}
    right = {'facts': ['2. 위 조건은 필수예요.', '1. 대상은 학생이에요.']}
    assert compare_scoped_facts(fact_state(left, right))['quality_label'] == 'changed'
    right['facts'].reverse()
    state = fact_state(left, right)
    state['source_complete'] = False
    assert compare_scoped_facts(state)['quality_label'] == 'insufficient'
