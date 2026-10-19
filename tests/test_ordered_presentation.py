"""Public development examples, not real originals or independent evaluation."""
import copy

import pytest

from moa.r2_data import fact_state
from moa.scoped_verification import compare_scoped_facts


def compare(left, right):
    state = fact_state(left, right)
    before = copy.deepcopy(state)
    report = compare_scoped_facts(state)
    assert state == before
    assert not report['human_approved'] and not report['whole_original_verified']
    return report


@pytest.mark.parametrize('source,dom', [
    ('1. 위 조건을 모두 확인한 후 등록합니다.', '1. 위 조건을 모두 확인한 후 등록해요.'),
    ('교육은 별관에서 진행됩니다.', '교육은 별관에서 진행돼요.'),
    ('해당 학생은 추가로 신청할 수 있습니다.', '해당 학생은 추가로 신청할 수 있어요.'),
    ('다음 항목은 예외가 없습니다.', '다음 항목은 예외가 없어요.'),
    ('시작은 14:25입니다.\n이후에는 변경이 없습니다.',
     '시작은 14:25입니다. 이후에는 변경이 없어요.'),
    ('학생은 신청하지 않아도 됩니다.', '학생은 신청하지 않아도 돼요.'),
])
@pytest.mark.parametrize('field', ['facts', 'conditions', 'notes'])
def test_only_terminal_present_speech_level_can_change_without_reordering(source, dom, field):
    # Removing ordered presentation support must recreate the real audit's
    # false positives; all other characters and clause positions stay bound.
    result = compare({field: [source]}, {field: [dom]})
    assert result['quality_label'] == 'preserved'


@pytest.mark.parametrize('source,dom', [
    ("'추가 모집'에 참여할 수 있습니다.", "'추가 모집'에 참여할 수 있어요."),
    ('“안전 체험”은 별관에서 진행됩니다.', '“안전 체험”은 별관에서 진행돼요.'),
    ('「탐구 주간」에는 예외가 없습니다.', '「탐구 주간」에는 예외가 없어요.'),
    ('『독서 “짝” 활동』에 참가합니다.', '『독서 “짝” 활동』에 참가해요.'),
    ('안내에는 "참여합니다."라고 표시합니다.', '안내에는 "참여합니다."라고 표시해요.'),
])
@pytest.mark.parametrize('field', ['facts', 'conditions', 'notes'])
def test_literal_balanced_quotation_before_unquoted_terminal_ending_is_preserved(source, dom, field):
    # The quotation itself is immutable; a blanket quotation veto recreates FP.
    assert compare({field: [source]}, {field: [dom]})['quality_label'] == 'preserved'
    assert compare({field: [dom]}, {field: [source]})['quality_label'] == 'preserved'


@pytest.mark.parametrize('prefix', ['참가 기회가 늘어날', '서류를 확인할 수 있을', '보호자가 동의한'])
def test_separate_dependent_noun_copula_retains_its_entire_adnominal_prefix(prefix):
    # Explicit separated 것 + copula, not a final-consonant or verb-stem guess.
    source, dom = prefix + ' 것입니다.', prefix + ' 것이에요.'
    assert compare({'facts': [source]}, {'facts': [dom]})['quality_label'] == 'preserved'
    assert compare({'facts': [dom]}, {'facts': [source]})['quality_label'] == 'preserved'


@pytest.mark.parametrize('source,dom', [
    ("'필수' 항목이 있습니다.", "'선택' 항목이 있어요."),
    ('“참여합니다.”라고 안내합니다.', '“참여해요.”라고 안내해요.'),
    ('“별관에서 진행됩니다.”', '“별관에서 진행돼요.”'),
    ("'필수 항목이 있습니다.", "'필수 항목이 있어요."),
    ('「선택』 항목이 있습니다.', '「선택』 항목이 있어요.'),
    ('“「선택”」 항목이 있습니다.', '“「선택”」 항목이 있어요.'),
    ('"선택\\" 항목이 있습니다.', '"선택\\" 항목이 있어요.'),
    ('참가할 것입니다.', '참가하지 않을 것이에요.'),
    ('선택해야 할 것입니다.', '선택할 것이에요.'),
    ('참가할 것입니다.', '참가한 것이에요.'),
    ('선택할 것입니다.', '선택할 것예요.'),
    ('이것입니다.', '이것이에요.'),
    ('안내문을 죽입니다.', '안내문을 죽이에요.'),
    ('성장할 수 있을 것입니다.', '성장할 수 있을것이에요.'),
])
def test_quotation_and_copula_proof_never_rewrites_content_or_guesses_verbs(source, dom):
    assert compare({'facts': [source]}, {'facts': [dom]})['quality_label'] == 'changed'


@pytest.mark.parametrize('source,dom', [
    ("학생은 '필수 항목을 확인합니다.", "학생은 '필수 항목을 확인해요."),
    ('학생은 「필수』 항목을 확인합니다.', '학생은 「필수』 항목을 확인해요.'),
    ("학생은 \\'필수\\' 항목을 확인합니다.", "학생은 \\'필수\\' 항목을 확인해요."),
    ('학생은 『「필수』」 항목을 확인합니다.', '학생은 『「필수』」 항목을 확인해요.'),
    ("학생은 '추가  모집' 항목을 확인합니다.", "학생은 '추가 모집' 항목을 확인해요."),
    ("학생은 '2개'를 선택합니다.", "학생은 '2 개'를 선택해요."),
])
def test_failed_literal_quote_proof_cannot_fall_back_to_fuzzy_clause_tokens(source, dom):
    # A legacy clause normalization must not override the failed literal proof.
    assert compare({'facts': [source]}, {'facts': [dom]})['quality_label'] == 'changed'


@pytest.mark.parametrize('source,dom', [
    ('준비물은 개인용 필기구입니다.', '준비물은 개인용 필기구예요.'),
    ('참가 대상은 보호자와 학생입니다.', '참가 대상은 보호자와 학생이에요.'),
])
@pytest.mark.parametrize('field', ['facts', 'conditions', 'notes'])
def test_unproven_nominal_copula_keeps_strict_comparison(source, dom, field):
    # Final consonants cannot prove whether ...입니다 is a copula or a verb.
    # These valid paraphrases remain an explicit unsupported false-positive
    # boundary until independent lexical/syntactic evidence is available.
    assert compare({field: [source]}, {field: [dom]})['quality_label'] == 'changed'


@pytest.mark.parametrize('source,dom', [
    ('학생은 반드시 등록합니다.', '학생은 등록해요.'),
    ('신청은 2029년 6월 12일에 마감됩니다.', '신청은 2029년 6월 13일에 마감돼요.'),
    ('활동은 15:25에 시작됩니다.', '활동은 15:35에 시작돼요.'),
    ('비용은 8,600원이 됩니다.', '비용은 860원이 돼요.'),
    ('반드시 2개를 선택합니다.', '반드시 3개를 선택해요.'),
    ('예외가 있습니다.', '예외가 없어요.'),
    ('예외가 없습니다.', '예외가 있어요.'),
    ('선택할 수 있습니다.', '선택해야 해요.'),
    ('학생이 보호자를 확인합니다.', '보호자가 학생을 확인해요.'),
    ('신청하지 않아도 됩니다.', '신청하지 않으면 돼요.'),
    ('학생은 등록합니다.', '학생은 등록했어요.'),
    ('신청인은 보호자입니다.', '신청인은 보호자이에요.'),
    ('담당자는 학생입니다.', '담당자는 학생예요.'),
    ('안내에는 “참여합니다.”라고 표시합니다.', '안내에는 “참여해요.”라고 표시해요.'),
    ('참가 대상은 학생입니다.', '참가 대상은 학 생이에요.'),
    ('수업이 끝나면 학생들이 모입니다.', '수업이 끝나면 학생들이 모예요.'),
    ('참가 대상이 보입니다.', '참가 대상이 보예요.'),
    ('인솔자와 함께 움직입니다.', '인솔자와 함께 움직이에요.'),
])
def test_presentation_cannot_waive_values_roles_polarity_modality_quotes_or_word_boundaries(source, dom):
    assert compare({'facts': [source]}, {'facts': [dom]})['quality_label'] == 'changed'


def test_order_references_still_bind_clauses_even_with_equivalent_endings():
    left = {'conditions': ['1. 먼저 접수합니다.', '2. 이후 납부합니다.']}
    right = {'conditions': ['2. 이후 납부해요.', '1. 먼저 접수해요.']}
    assert compare(left, right)['quality_label'] == 'changed'


def test_ordered_presentation_does_not_hide_duplicate_or_missing_clauses():
    left = {'facts': ['1. 접수합니다.', '2. 변경은 없습니다.']}
    for right in ({'facts': ['1. 접수해요.']},
                  {'facts': ['1. 접수해요.', '2. 변경은 없어요.', '2. 변경은 없어요.']}):
        assert compare(left, right)['quality_label'] == 'changed'


def test_role_or_context_guard_is_not_waived_by_presentation_equivalence():
    state = fact_state({'facts': ['1. 변경은 없습니다.']}, {'facts': ['1. 변경은 없어요.']})
    state['source_complete'] = False
    assert compare_scoped_facts(state)['quality_label'] == 'insufficient'


def test_style_change_does_not_waive_department_links_units_or_notes():
    left = {'headers': ['연번', '학과', '교실'],
            'rows': [['1', '항해학과', '탐구실'], ['2', '광물학과', '측정실']],
            'unit': '원', 'notes': ['다음 조건은 필수입니다.'],
            'conditions': ['참가비를 납부할 수 있습니다.']}
    for field in ('rows', 'unit', 'notes'):
        right = copy.deepcopy(left)
        right['conditions'] = ['참가비를 납부할 수 있어요.']
        if field == 'rows':
            right['rows'][0][2], right['rows'][1][2] = right['rows'][1][2], right['rows'][0][2]
        elif field == 'unit':
            right['unit'] = '천 원'
        else:
            right['notes'] = []
        assert compare(left, right)['quality_label'] == 'changed'
