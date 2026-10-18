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
