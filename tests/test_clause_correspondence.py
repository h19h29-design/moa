"""Reflow requires a semantic binding, not a sorted bag of sentences."""
import copy

import pytest

from moa.r2_data import fact_state
from moa.scoped_verification import compare_scoped_facts


CLAUSES = [
    '신청 마감: 2027년 11월 21일',
    '활동 시작: 15:30',
    '참가 금액: 12000원',
    '반드시 2개 선택',
    '전학 예정자는 신청 대상에서 제외합니다.',
]
NOTES = ['준비물은 개인별 지참합니다.', '비가 오면 실내 활동으로 변경합니다.']


def test_independent_named_clauses_can_reflow_without_changing_their_values():
    left = {'conditions': CLAUSES, 'notes': NOTES,
            'facts': ['신청은 학교 안내 페이지에서 합니다.']}
    right = copy.deepcopy(left)
    right['conditions'] = [CLAUSES[i] for i in [3, 1, 4, 0, 2]]
    right['notes'].reverse()
    state = fact_state(left, right)
    before = copy.deepcopy(state)
    report = compare_scoped_facts(state)
    assert report['quality_label'] == 'preserved'
    assert state == before
    assert report['clause_correspondence']['conditions']['source_to_dom'] == [3, 1, 4, 0, 2]


@pytest.mark.parametrize('clauses', [
    ['1. 신청서를 작성합니다.', '2. 신청서를 제출합니다.'],
    ['먼저 교실로 갑니다.', '이후 신청서를 제출합니다.'],
    ['신청 마감: 2027년 11월 21일', '위 기한 이후에는 접수하지 않습니다.'],
    ['참가 대상: 1학년', '다만 전학 예정자는 제외합니다.'],
    ['활동 시작: 15:30', '활동 시작: 16:30'],
    ['참가 대상: 1학년', '해당 학생은 신청서를 제출합니다.'],
    ['선택 순서: 국어, 수학', '선택 순서: 수학, 국어'],
])
def test_ordered_ambiguous_or_dependent_clauses_do_not_get_a_free_pass(clauses):
    assert compare_scoped_facts(fact_state({'conditions': clauses},
        {'conditions': list(reversed(clauses))}))['quality_label'] == 'changed'


@pytest.mark.parametrize('mutation', ['amount', 'count', 'negation', 'omission', 'duplicate', 'unit'])
def test_binding_still_detects_critical_changes_after_a_normal_reorder(mutation):
    left = {'conditions': CLAUSES, 'notes': NOTES, 'unit': '참가비: 원'}
    right = copy.deepcopy(left)
    right['conditions'].reverse()
    if mutation == 'amount':
        right['conditions'][2] = '참가 금액: 1200원'
    elif mutation == 'count':
        right['conditions'][1] = '반드시 1개 선택'
    elif mutation == 'negation':
        right['conditions'][0] = '전학 예정자는 신청 대상에 포함합니다.'
    elif mutation == 'omission':
        right['notes'].pop()
    elif mutation == 'duplicate':
        right['notes'].append(right['notes'][0])
    else:
        right['unit'] = '참가비: 천 원'
    assert compare_scoped_facts(fact_state(left, right))['quality_label'] == 'changed'


def test_repeated_table_and_independent_clauses_can_reflow_together():
    from tests.test_scoped_verification import repeated_table
    left, right = repeated_table()
    left['conditions'], right['conditions'] = CLAUSES, list(reversed(CLAUSES))
    assert compare_scoped_facts(fact_state(left, right))['quality_label'] == 'preserved'


@pytest.mark.parametrize('reference', ['위 첫 번째 조건은 예외 없이 적용합니다.', '조건 1번에 해당하는 학생은 별도 신청합니다.'])
def test_unchanged_footnote_can_make_another_fields_order_significant(reference):
    left = {'conditions': CLAUSES, 'notes': [reference]}
    right = copy.deepcopy(left); right['conditions'].reverse()
    assert compare_scoped_facts(fact_state(left, right))['quality_label'] == 'changed'


def test_row_position_references_are_not_erased_by_keyed_row_correspondence():
    left = {'headers': ['팀', '모임실'], 'rows': [['가팀', '강당'], ['나팀', '로비']],
            'notes': ['첫 번째 행의 팀은 먼저 입장합니다.']}
    right = copy.deepcopy(left); right['rows'].reverse()
    assert compare_scoped_facts(fact_state(left, right))['quality_label'] == 'changed'


def test_former_and_latter_reference_prevent_cross_field_reordering():
    left = {'conditions': ['참가 대상: 재학생', '신청 대상: 졸업생'],
            'notes': ['전자는 참가비를 면제합니다.']}
    right = copy.deepcopy(left); right['conditions'].reverse()
    assert compare_scoped_facts(fact_state(left, right))['quality_label'] == 'changed'


def test_positional_note_must_still_refer_to_original_six_column_row():
    from tests.test_scoped_verification import repeated_table
    left, right = repeated_table()
    right['rows'] = [left['rows'][0][:3], left['rows'][0][3:], left['rows'][1][:3]]
    left['notes'] = right['notes'] = ['첫 번째 행에 있는 학과는 참가비를 면제합니다.']
    assert compare_scoped_facts(fact_state(left, right))['quality_label'] == 'changed'
    rows = [[str(i), '교실'+str(i)] for i in range(1, 7)]
    left = {'headers': ['팀', '모임실'], 'rows': rows, 'notes': ['다섯 번째 행의 팀은 먼저 입장합니다.']}
    right = copy.deepcopy(left); right['rows'].reverse()
    assert compare_scoped_facts(fact_state(left, right))['quality_label'] == 'changed'
