"""Independent synthetic facts; never production labels or model fixtures as gold."""
import copy

from moa.verification import compare, normalise
from moa.worker_handoff import questions, readiness_errors, handoff


def paragraph(text):
    return [{'id': 's1', 'text': text}]


def rendered(text, **extra):
    return [{'id': 'r1', 'source_refs': ['s1'], 'visible_text': text,
             'visible': True, 'clipped': False, **extra}]


def test_deadline_change_not_overridden_by_model():
    report = compare(paragraph('신청 마감은 2026년 10월 30일 오후 5시입니다.'),
                     rendered('신청 마감은 2026년 10월 31일 오후 5시입니다.'))
    assert report['status'] == 'needs_review'
    assert any(x['code'] == 'date_mismatch' and x['severity'] == 'critical' for x in report['findings'])


def test_equivalent_date_time_and_explicit_year_context():
    a = '신청 마감: 2026년 10월 30일 오후 5시, 참가비 12,000원'
    b = '신청 마감: 10월 30일 17:00, 참가비 12000원'
    assert compare(paragraph(a), rendered(b))['status'] == 'passed'
    assert normalise('오전 12시') != normalise('오후 12시')
    assert compare(paragraph(a), rendered(b.replace('12000', '13000')))['status'] == 'needs_review'


def test_same_number_bag_cannot_hide_roles_or_row_exchange():
    source = [{'id': 's1', 'text': '', 'table_rows': [['학과', '교실'], ['법학과', '2-1'], ['역사학과', '2-2']]}]
    view = rendered('', table_rows=[['학과', '교실'], ['법학과', '2-2'], ['역사학과', '2-1']])
    assert 'table_relation_mismatch' in {f['code'] for f in compare(source, view)['findings']}
    src = [{'id': 's1', 'text': '행사일 10월 30일'}, {'id': 's2', 'text': '신청 마감 10월 20일'}]
    got = [{'id': 'r1', 'source_refs': ['s1'], 'visible_text': '행사일 10월 20일'},
           {'id': 'r2', 'source_refs': ['s2'], 'visible_text': '신청 마감 10월 30일'}]
    assert compare(src, got)['status'] == 'needs_review'


def test_selection_condition_and_footnote_omission_are_hard_findings():
    for source, candidate in [('반드시 2개 선택', '반드시 1개 선택'),
                              ('선착순 마감. 미신청 시 임의 배정.', '선착순 마감.'),
                              ('참가비 20,000원. ※ 형제 참가자는 무료.', '참가비 20,000원.')]:
        findings = compare(paragraph(source), rendered(candidate))['findings']
        assert findings and all(x['severity'] == 'critical' for x in findings)


def test_intentionally_attached_form_requires_real_original():
    src = [{'id': 's1', 'text': '제출은 10월 30일까지'},
           {'id': 's2', 'text': '신청자 ( ) 보호자 ( )', 'role': 'form', 'attachment_id': 'f1'}]
    att = [{'id': 'f1', 'available': True, 'sha256': 'a' * 64}]
    assert compare(src, rendered(src[0]['text']), attachments=att)['status'] == 'passed'
    assert compare(src, rendered(src[0]['text']), attachments=[])['status'] == 'needs_review'


def test_incomplete_hidden_clipped_and_missing_header_cannot_pass():
    src = paragraph('신청 마감 10월 30일')
    assert compare(src, rendered(src[0]['text']), source_complete=False)['status'] == 'needs_review'
    for attr in ({'visible': False}, {'clipped': True}):
        assert compare(src, rendered(src[0]['text'], **attr))['status'] == 'needs_review'
    assert compare([{'id': 's1', 'text': '', 'table_rows': [['학과', '교실'], ['법학과', '2-1']]}],
                   rendered('', table_rows=[['법학과', '2-1']]))['status'] == 'needs_review'


def test_readiness_requires_actual_question_mapping_and_vectors():
    ready = handoff('a' * 40)
    ack = {'worker_id': 'macbook-laya-01', 'contract_hash': ready['contract_hash'],
           'validator_hash': ready['validator_hash'],
           'rubric_sha256': ready['rubric_sha256'], 'questions': questions(),
           'canonical_vectors': ready['canonical_vectors'],
           'source_block_hash': 'sha256-text-utf8', 'max_artifact_bytes': 1048576,
           'worker_code_sha256': 'b' * 64, 'daemon_ready': True}
    assert readiness_errors(ack) == []
    wrong = copy.deepcopy(ack)
    wrong['questions']['conditions']['instructions'] = '날짜는 비교하지 않는다.'
    assert readiness_errors(wrong)
    wrong = copy.deepcopy(ack)
    wrong['canonical_vectors'][0]['sha256'] = '0' * 64
    assert readiness_errors(wrong)


def test_each_critical_fact_is_reported_even_inside_a_table():
    src = [{'id': 's1', 'text': '', 'table_rows': [
        ['일정', '비용 및 선택'], ['2026.10.30 17:00', '12,000원 / 2개 선택']]}]
    got = rendered('', table_rows=[
        ['일정', '비용 및 선택'], ['2026.10.31 18:00', '13,000원 / 1개 선택']])
    codes = {f['code'] for f in compare(src, got)['findings']}
    assert {'date_mismatch', 'time_mismatch', 'amount_mismatch', 'selection_mismatch'} <= codes


def test_repeated_source_columns_can_be_reflowed_without_changing_links():
    raw = [['연번', '학과', '수업교실', '연번', '학과', '수업교실'],
           ['1', '역사교육과', '2-1', '3', '행정학과', '2-3'],
           ['2', '신소재공학과', '2-2', '', '', '']]
    flat = [['연번', '학과', '수업교실'], ['1', '역사교육과', '2-1'],
            ['2', '신소재공학과', '2-2'], ['3', '행정학과', '2-3']]
    source = [{'id': 's1', 'text': '', 'table_rows': raw}]
    report = compare(source, rendered('', table_rows=flat))
    assert report['status'] == 'passed'
    assert report['direct_comparison'][0]['department_links']['expected_count'] == 3


def test_raw_source_links_override_a_renderer_and_model_sharing_the_same_error():
    raw = [['연번', '학과', '교실', '연번', '학과', '교실'],
           ['1', '법학과', '2-1', '2', '역사학과', '2-2']]
    wrong = [['연번', '학과', '교실'], ['1', '법학과', '2-2'], ['2', '역사학과', '2-1']]
    source = [{'id': 's1', 'text': '', 'table_rows': wrong, 'original_table_rows': raw}]
    report = compare(source, rendered('', table_rows=wrong))
    assert 'department_classroom_mismatch' in {f['code'] for f in report['findings']}


def test_duplicate_department_row_cannot_satisfy_one_to_one_preservation():
    rows = [['연번', '학과', '교실'], ['1', '법학과', '2-1'], ['2', '역사학과', '2-2']]
    source = [{'id': 's1', 'text': '', 'table_rows': rows}]
    got = [rows[0], rows[1], rows[1]]
    assert 'department_classroom_mismatch' in {f['code'] for f in compare(source, rendered('', table_rows=got))['findings']}


def test_combined_verdict_cannot_hide_rules_or_raw_model_answers():
    from moa.verification import combine_verdict
    rule = compare(paragraph('반드시 2개 선택'), rendered('반드시 1개 선택'))
    model = {'status': 'completed', 'criteria': {'conditions': {'model_label': 'preserved', 'label': 'preserved'}}}
    result = combine_verdict(rule, [model], browser_ok=True)
    assert result['status'] == 'needs_review'
    assert result['model_labels'] == ['preserved']
    assert result['rule_status'] == 'needs_review'
    assert not result['human_approved'] and not result['auto_publish_enabled']
    assert combine_verdict({'status': 'passed', 'findings': []}, [], browser_ok=True)['status'] == 'needs_review'


def test_equivalent_critical_formatting_is_not_an_error():
    source = paragraph('마감 2026. 10. 30. 오후 5시, 12,000원, 2개 선택')
    got = rendered('마감 2026-10-30 17:00, 12000원, 2개 선택')
    assert compare(source, got)['status'] == 'passed'


def test_short_numeric_dates_and_spaced_selection_units_are_structural_facts():
    report = compare(paragraph('신청 10. 30. 17:00까지 2 과목 선택'),
                     rendered('신청 10. 31. 18:00까지 1 과목 선택'))
    assert {'date_mismatch', 'time_mismatch', 'selection_mismatch'} <= {f['code'] for f in report['findings']}


def test_repeated_table_reflow_cannot_discard_a_preamble_condition():
    source = [{'id': 's1', 'text': '', 'table_rows': [
        ['선착순 마감'], ['연번','학과','교실','연번','학과','교실'],
        ['1','법학과','2-1','2','역사학과','2-2']]}]
    got = rendered('', table_rows=[['연번','학과','교실'],['1','법학과','2-1'],['2','역사학과','2-2']])
    assert compare(source, got)['status'] == 'needs_review'


def test_caption_and_footnote_date_changes_are_reported_as_dates():
    source = [{'id':'s1','text':'','table_rows':[['대상','금액'],['1학년','1000원']], 'note':'마감 10월 30일'}]
    got = rendered('',table_rows=[['대상','금액'],['1학년','1000원']],note='마감 10월 31일')
    assert 'date_mismatch' in {f['code'] for f in compare(source,got)['findings']}


def test_equivalent_clock_padding_and_currency_spaces_do_not_raise_false_alerts():
    assert compare(paragraph('오전 9시 참가비 12,000 원'),rendered('9:00 참가비 12000원'))['status']=='passed'
