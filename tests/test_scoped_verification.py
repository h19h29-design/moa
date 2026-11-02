"""A normal repeated table must not teach or report a content error."""
import copy

import pytest

from moa.r2_data import fact_state
from moa.scoped_verification import compare_scoped_facts


def repeated_table():
    original = {'headers': ['연번','학과','수업교실'] * 2,
                'rows': [['1','천문학과','관측실','3','조형학과','공작실'],
                         ['2','해양학과','탐구실','','','']],
                'caption': '탐구 활동 안내', 'notes': ['각자 2개를 선택합니다.']}
    mobile = {'headers': ['연번','학과','수업교실'],
              'rows': [['3','조형학과','공작실'], ['1','천문학과','관측실'], ['2','해양학과','탐구실']],
              'caption': '탐구 활동 안내', 'notes': ['각자 2개를 선택합니다.']}
    return original, mobile


def test_repeated_columns_may_become_one_keyed_list_without_a_false_alarm():
    original, mobile = repeated_table()
    state = fact_state(original, mobile)
    before = copy.deepcopy(state)
    report = compare_scoped_facts(state)
    assert report['quality_label'] == 'preserved'
    assert state == before
    assert not report['human_approved'] and not report['whole_original_verified']


@pytest.mark.parametrize('mutation', ['swap_rooms','drop_department','duplicate_ordinal','drop_note','change_header'])
def test_reflow_does_not_hide_a_wrong_pair_missing_condition_or_header(mutation):
    original, mobile = repeated_table()
    if mutation == 'swap_rooms':
        mobile['rows'][0][2], mobile['rows'][1][2] = mobile['rows'][1][2], mobile['rows'][0][2]
    elif mutation == 'drop_department':
        mobile['rows'].pop()
    elif mutation == 'duplicate_ordinal':
        mobile['rows'][0][0] = mobile['rows'][1][0]
    elif mutation == 'drop_note':
        mobile['notes'] = []
    else:
        mobile['headers'][2] = '담당자'
    assert compare_scoped_facts(fact_state(original, mobile))['quality_label'] == 'changed'


def test_unknown_merged_relationship_and_missing_context_are_not_assumed_correct():
    original, mobile = repeated_table()
    original['geometry'] = [[1,1,2,1]]
    assert compare_scoped_facts(fact_state(original,mobile))['quality_label'] != 'preserved'
    # Equal coordinates can span different semantic cells after changing width.
    mobile['geometry'] = [[1,1,2,1]]
    assert compare_scoped_facts(fact_state(original,mobile))['quality_label'] != 'preserved'
    state = fact_state(*repeated_table());state['source_complete'] = False
    assert compare_scoped_facts(state)['quality_label'] == 'insufficient'
