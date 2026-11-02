"""Evidence may resolve model contradictions; model labels themselves stay intact."""
import copy

import pytest

from moa.verification import compare, combine_verdict


def paragraph(left, right=None, **options):
    return compare([{'id': 's1', 'text': left}],
                   [{'id': 'r1', 'source_refs': ['s1'], 'visible_text': left if right is None else right}], **options)


def model(label):
    return {'status': 'completed', 'criteria': {'conditions': {'model_label': label, 'label': label}}}


def test_full_source_dom_identity_resolves_false_model_alarm_without_relabeling_it():
    raw = model('changed')
    result = combine_verdict(paragraph('학생은 반드시 2개 학과를 선택합니다.'), [raw], browser_ok=True)
    assert result['quality_label'] == 'preserved'
    assert result['decision_basis'] == 'verified_content_identity'
    assert result['model_labels'] == ['changed'] and raw == model('changed')
    assert not result['human_approved'] and not result['auto_pass_enabled'] and not result['auto_publish_enabled']


@pytest.mark.parametrize('left,right', [
    ('전 학생 신청 가능', '전학생 신청 가능'),
    ('신청 안 한 학생은 제외', '신청안한 학생은 제외'),
    ('고등 학교 지원', '고등학교 지원'),
    ('all students may apply', 'allstudents may apply'),
    ('참고 값 10.1', '참고 값 10.01'),
    ('평균은 10.1.', '평균은 10.01.'),
])
def test_removing_semantic_word_boundaries_or_changing_decimals_is_not_identity(left, right):
    report = paragraph(left, right)
    assert report['status'] == 'needs_review'
    assert combine_verdict(report, [model('preserved')], browser_ok=True)['quality_label'] == 'changed'


def test_identity_requires_coverage_bindings_visibility_and_browser_evidence():
    assert combine_verdict(paragraph('신청 안내', source_complete=False), [model('preserved')], browser_ok=True)['quality_label'] == 'insufficient'
    assert combine_verdict(paragraph('신청 안내'), [model('preserved')], browser_ok=False)['quality_label'] == 'insufficient'
    incomplete = {'status': 'passed', 'findings': []}
    assert combine_verdict(incomplete, [], browser_ok=True)['quality_label'] == 'insufficient'
    for changes in ({'source_refs': ['s2']}, {'visible': False}, {'clipped': True}):
        view = {'id': 'r1', 'source_refs': ['s1'], 'visible_text': '신청 안내', **changes}
        rule = compare([{'id': 's1', 'text': '신청 안내'}], [view])
        assert combine_verdict(rule, [model('preserved')], browser_ok=True)['quality_label'] != 'preserved'


def test_proof_is_bound_to_current_evidence_not_a_boolean_copied_from_an_old_report():
    report = paragraph('신청 마감 2026.10.30.')
    changed = copy.deepcopy(report)
    changed['source_complete'] = False
    assert combine_verdict(changed, [model('preserved')], browser_ok=True)['quality_label'] != 'preserved'
    changed = copy.deepcopy(report)
    changed['findings'] = [{'code': 'date_mismatch', 'severity': 'critical'}]
    assert combine_verdict(changed, [model('preserved')], browser_ok=True)['quality_label'] == 'changed'
    changed = copy.deepcopy(report)
    changed['observed_blocks'] += 1
    assert combine_verdict(changed, [model('preserved')], browser_ok=True)['quality_label'] == 'insufficient'


def test_legitimate_date_time_spacing_reformat_is_proven_but_conditions_are_not_erased():
    rule = paragraph('신청 마감 2026. 10. 30. 오후 5시, 12,000 원, 2 개 선택',
                     '신청 마감 2026-10-30 17:00, 12000원, 2개 선택')
    assert combine_verdict(rule, [model('changed')], browser_ok=True)['quality_label'] == 'preserved'
    changed = paragraph('전 학생은 2개 선택', '전학생은 2개 선택')
    assert combine_verdict(changed, [model('preserved')], browser_ok=True)['quality_label'] == 'changed'


def test_repeated_table_identity_is_proven_from_original_links_with_complete_context():
    raw = [['연번', '학과', '교실', '연번', '학과', '교실'],
           ['1', '법학과', '2-1', '2', '역사학과', '2-2']]
    flat = [['연번', '학과', '교실'], ['1', '법학과', '2-1'], ['2', '역사학과', '2-2']]
    src = [{'id': 's1', 'text': '', 'table_rows': raw, 'note': '전 학생 신청 가능'}]
    view = [{'id': 'r1', 'source_refs': ['s1'], 'table_rows': flat, 'note': '전 학생 신청 가능'}]
    assert combine_verdict(compare(src, view), [model('changed')], browser_ok=True)['quality_label'] == 'preserved'
    view[0]['note'] = '전학생 신청 가능'
    assert combine_verdict(compare(src, view), [model('preserved')], browser_ok=True)['quality_label'] == 'changed'


def test_deferred_model_remains_deferred_when_content_identity_is_independently_proven():
    deferred = {'status': 'deferred', 'reason': 'token_budget_exceeded', 'criteria': {}}
    result = combine_verdict(paragraph('전체 내용이 동일한 긴 안내'), [deferred], browser_ok=True)
    assert result['quality_label'] == 'preserved'
    assert result['model_complete'] is False and deferred['status'] == 'deferred'


def test_repeated_table_reflow_does_not_erase_word_boundaries_in_department_names():
    src = [{'id': 's1', 'text': '', 'table_rows': [
        ['연번','학과','교실','연번','학과','교실'],
        ['1','식품 영양학과','2-1','2','법학과','2-2']]}]
    got = [{'id':'r1','source_refs':['s1'],'table_rows':[
        ['연번','학과','교실'],['1','식품영양학과','2-1'],['2','법학과','2-2']]}]
    assert combine_verdict(compare(src,got),[model('preserved')],browser_ok=True)['quality_label']=='changed'


def test_shared_reflow_bug_cannot_drop_original_preamble_or_original_caption():
    flat=[['연번','학과','교실'],['1','법학과','2-1'],['2','역사학과','2-2']]
    raw=[['전 학생은 선착순 신청'],['연번','학과','교실','연번','학과','교실'],
         ['1','법학과','2-1','2','역사학과','2-2']]
    view=[{'id':'r1','source_refs':['s1'],'table_rows':flat}]
    for original in ({'original_table_rows':raw}, {'original_caption':'전 학생은 선착순 신청'}):
        src=[{'id':'s1','text':'','table_rows':flat,**original}]
        assert combine_verdict(compare(src,view),[model('preserved')],browser_ok=True)['quality_label']=='changed'


def test_repeated_table_may_promote_original_preheader_into_caption_without_dropping_either():
    raw = [['전 학생은 선착순 신청'], ['연번','학과','교실','연번','학과','교실'],
           ['1','법학과','2-1','2','역사학과','2-2']]
    flat = [['연번','학과','교실'],['1','법학과','2-1'],['2','역사학과','2-2']]
    src = [{'id':'s1','text':'','original_table_rows':raw,'table_rows':flat,
            'original_caption':'학과 선택 안내','caption':'학과 선택 안내\n전 학생은 선착순 신청'}]
    view = [{'id':'r1','source_refs':['s1'],'table_rows':flat,'caption':src[0]['caption']}]
    assert compare(src,view)['status'] == 'passed'


def test_reflow_cannot_add_a_new_non_numeric_condition_above_dom_header():
    raw = [['연번','학과','교실','연번','학과','교실'],
           ['1','법학과','2-1','2','역사학과','2-2']]
    src = [{'id':'s1','text':'','table_rows':raw}]
    got = [{'id':'r1','source_refs':['s1'],'table_rows':[
        ['전학생만 신청 가능'],['연번','학과','교실'],['1','법학과','2-1'],['2','역사학과','2-2']]}]
    assert combine_verdict(compare(src,got),[model('preserved')],browser_ok=True)['quality_label'] == 'changed'


def test_original_generic_table_not_a_shared_normalized_copy_is_the_oracle():
    raw = [['대상','조건'],['전 학생','선착순 신청']]
    dropped = [['대상','조건'],['전 학생','신청']]
    src = [{'id':'s1','text':'','original_table_rows':raw,'table_rows':dropped}]
    got = [{'id':'r1','source_refs':['s1'],'table_rows':dropped}]
    assert combine_verdict(compare(src,got),[model('preserved')],browser_ok=True)['quality_label'] == 'changed'
