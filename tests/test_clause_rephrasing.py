"""Development examples: clause identity needs values, modality and bindings."""
import copy

import pytest

from moa.r2_data import fact_state
from moa.scoped_verification import compare_scoped_facts


def check(source, actual):
    state = fact_state(source, actual)
    before = copy.deepcopy(state)
    report = compare_scoped_facts(state)
    assert state == before
    assert report['human_approved'] is False
    assert report['whole_original_verified'] is False
    return report


def test_unique_explicit_labels_do_not_need_a_program_specific_prefix():
    clauses = ['견학 집결실: 자연관 302호', '교구 보증금: 7800원',
               '관찰 기록 제출일: 2028년 4월 19일']
    report = check({'conditions': clauses}, {'conditions': clauses[1:] + clauses[:1]})
    assert report['quality_label'] == 'preserved'
    assert report['clause_correspondence']['conditions']['source_to_dom'] == [2, 0, 1]


def test_one_character_field_values_still_have_a_unique_binding():
    clauses = ['학년: 2', '반: 가']
    assert check({'conditions': clauses}, {'conditions': clauses[::-1]})['quality_label'] == 'preserved'


@pytest.mark.parametrize('source,actual', [
    ('견학 집결실은 자연관 302호입니다.', '견학 집결실: 자연관 302호'),
    ('교구 보증금: 7800원', '교구 보증금은 7,800원입니다.'),
    ('참여자는 관찰 기록을 제출합니다.', '참여자는 관찰 기록을 제출해요.'),
    ('보호자는 동의서를 작성하여야 합니다.', '보호자는 동의서를 작성해야 합니다.'),
    ('학생은 반드시 보안경을 지참합니다.', '학생은 반드시 보안경을 지참해 주세요.'),
    ('반드시 2개 선택합니다.', '반드시 2개 선택해 주세요.'),
    ('견학 시작 시각: 14:15', '견학 시작 시각은 14:15입니다.'),
])
def test_bounded_rephrasing_retains_whole_clause_and_evidence(source, actual):
    report = check({'conditions': [source]}, {'conditions': [actual]})
    assert report['quality_label'] == 'preserved'


def test_reorder_and_rephrasing_can_be_proved_together():
    left = {'conditions': ['견학 집결실: 자연관 302호', '교구 보증금은 7800원입니다.',
                           '보호자는 동의서를 작성하여야 합니다.']}
    right = {'conditions': ['보호자는 동의서를 작성해야 합니다.',
                            '견학 집결실은 자연관 302호입니다.', '교구 보증금: 7,800원']}
    assert check(left, right)['quality_label'] == 'preserved'


@pytest.mark.parametrize('noun', ['이상기록', '상기시키기'])
def test_a_reference_substring_inside_a_noun_is_not_a_cross_reference(noun):
    clauses = [noun + ' 담당실: 별관 101호', '부서 운영 시각: 10:35']
    assert check({'conditions': clauses}, {'conditions': clauses[::-1]})['quality_label'] == 'preserved'


@pytest.mark.parametrize('noun', ['전자공학', '후자음'])
def test_ambiguous_reference_roots_keep_conservative_comparison(noun):
    clauses = [noun + ' 담당실: 별관 101호', '부서 운영 시각: 10:35']
    assert check({'conditions': clauses}, {'conditions': clauses[::-1]})['quality_label'] == 'changed'


@pytest.mark.parametrize('reference', ['상기 조건은 필수입니다.', '전자는 필수입니다.',
                                       '후자의 신청은 별도입니다.', '전항에 따라 신청합니다.',
                                       '상기조건은 필수입니다.', '상기한 조건은 필수입니다.',
                                       '후술한 조건은 별도입니다.', '전항에서 정한 학생만 신청합니다.',
                                       '후자에게는 추가 비용이 없습니다.', '상기에도 예외는 있습니다.'])
def test_reference_words_and_their_particles_still_bind_neighboring_clauses(reference):
    clauses = ['견학 집결실: 자연관 302호', '교구 보증금: 7800원']
    assert check({'conditions': clauses, 'notes': [reference]},
                 {'conditions': clauses[::-1], 'notes': [reference]})['quality_label'] == 'changed'


@pytest.mark.parametrize('actual', [
    '견학 집결실: 자연관 303호',
    '교구 보증금: 780원',
    '학생은 반드시 1개를 선택합니다.',
    '보호자는 동의서를 작성할 수 있습니다.',
    '보호자는 동의서를 작성하지 않아야 합니다.',
    '보호자는 참가서를 작성해야 합니다.',
])
def test_a_style_rule_never_waives_a_changed_value_target_or_obligation(actual):
    clauses = ['견학 집결실: 자연관 302호', '교구 보증금: 7800원',
               '학생은 반드시 2개를 선택합니다.', '보호자는 동의서를 작성하여야 합니다.']
    source = clauses[0] if actual.startswith('견학') else clauses[1] if actual.startswith('교구') else clauses[2] if actual.startswith('학생') else clauses[3]
    assert check({'conditions': [source]}, {'conditions': [actual]})['quality_label'] == 'changed'


@pytest.mark.parametrize('suffix', [
    '위 항목의 학생만 신청합니다.', '첫 번째 조건은 예외가 없습니다.',
    '전자는 보증금을 내지 않습니다.', '다만 보호자 동반은 제외합니다.',
])
def test_cross_references_still_block_moving_otherwise_named_clauses(suffix):
    clauses = ['견학 집결실: 자연관 302호', '교구 보증금: 7800원']
    assert check({'conditions': clauses, 'notes': [suffix]},
                 {'conditions': clauses[::-1], 'notes': [suffix]})['quality_label'] == 'changed'


def test_identical_meaning_is_not_inferred_from_a_number_bag_or_same_subject():
    left = {'conditions': ['학생은 반드시 보안경을 지참합니다.',
                           '학생은 반드시 관찰 기록을 제출합니다.']}
    right = {'conditions': ['학생은 반드시 관찰 기록을 지참합니다.',
                            '학생은 반드시 보안경을 제출합니다.']}
    assert check(left, right)['quality_label'] == 'changed'


def test_unknown_paraphrase_is_not_automatically_cleared():
    assert check({'conditions': ['참가자는 실험실에서 대기합니다.']},
                 {'conditions': ['참가자는 실험실에 머무르면 됩니다.']})['quality_label'] == 'changed'


@pytest.mark.parametrize('source,actual', [
    ('참여자는 관찰 기록을 제출합니다.', '참여자는 관찰 기록을 제출하세요.'),
    ('참여자는 관찰 기록을 제출해야 합니다.', '참여자는 관찰 기록을 제출해요.'),
    ('교구 보증금은 7800원입니다.', '교구 보증금은 7800원이 아닙니다.'),
    ('반드시 2개 선택합니다.', '반드시 2개 이상 선택해 주세요.'),
    ('학생은 반드시 2개를 선택합니다.', '보호자는 반드시 2개를 선택해 주세요.'),
    ('신청일: 2028년 4월 19일', '신청일은 2028년 4월 20일입니다.'),
    ('시작 시각: 14:15', '시작 시각은 15:15입니다.'),
    ('신청 대상은 2학년입니다.', '신청 대상은 2학년만입니다.'),
    ('보호자는 반드시 동의서를 작성합니다.', '보호자는 동의서를 작성해 주세요.'),
    ('학생은 반드시 보호자를 확인합니다.', '학생은 보호자가 반드시 확인합니다.'),
    ('학생은 반드시 보안경을 지참합니다.', '보안경은 학생이 지참합니다.'),
])
def test_presentation_rules_preserve_modality_boundaries_actors_and_numeric_relations(source, actual):
    assert check({'conditions': [source]}, {'conditions': [actual]})['quality_label'] == 'changed'


def test_proof_distinguishes_exact_tokens_from_a_bounded_presentation_change():
    report = check({'conditions': ['교구 보증금은 7800원입니다.']},
                   {'conditions': ['교구 보증금: 7,800원']})
    proof = report['clause_correspondence']['conditions']
    assert proof['complete_clause_tokens_equal'] is False
    assert proof['canonical_clause_tokens_equal'] is True
    assert 'copular_field_presentation' in proof['source_rule_ids'][0]


def test_missing_evidence_never_gets_a_rephrasing_pass():
    state = fact_state({'conditions': ['교구 보증금은 7800원입니다.']},
                       {'conditions': ['교구 보증금: 7,800원']})
    state['source_complete'] = False
    report = compare_scoped_facts(state)
    assert report['quality_label'] == 'insufficient'
    assert report['clause_correspondence'] == {}


def test_rephrasing_does_not_hide_a_table_link_unit_or_note_change():
    left = {'headers': ['연번', '학과', '수업교실'],
            'rows': [['1', '광학과', '별관 301호'], ['2', '지질학과', '별관 302호']],
            'conditions': ['교구 보증금: 7800원'], 'unit': '원',
            'notes': ['준비물은 개인별 지참합니다.']}
    for field in ('rows', 'unit', 'notes'):
        right = copy.deepcopy(left)
        right['conditions'] = ['교구 보증금은 7800원입니다.']
        if field == 'rows':
            right['rows'][0][2], right['rows'][1][2] = right['rows'][1][2], right['rows'][0][2]
        elif field == 'unit':
            right['unit'] = '천 원'
        else:
            right['notes'] = []
        assert check(left, right)['quality_label'] == 'changed'


@pytest.mark.parametrize('source,actual', [
    ('학생은 반드시 참석해야 하는 것은 아니지만 동의서를 제출합니다.',
     '학생은 반드시 참석해야 하는 것은 아니지만 동의서를 제출해야 합니다.'),
    ('보호자는 반드시 제출할 필요는 없지만 동의서를 작성합니다.',
     '보호자는 반드시 제출할 필요는 없지만 동의서를 작성해야 합니다.'),
    ('모든 학생은 반드시 한 과목을 선택해야 합니다.',
     '한 과목은 모든 학생이 반드시 선택해야 합니다.'),
    ('각 보호자는 반드시 한 학생을 확인해야 합니다.',
     '한 학생은 각 보호자가 반드시 확인해야 합니다.'),
])
def test_subordinate_obligation_and_quantifier_scope_are_not_presentation_changes(source, actual):
    assert check({'conditions': [source]}, {'conditions': [actual]})['quality_label'] == 'changed'


@pytest.mark.parametrize('reference', [
    '전자만이 참가비를 면제받습니다.', '전자라면 참가비를 면제합니다.',
    '후자에게만은 보증금을 받습니다.', '후자께서 신청서를 제출합니다.',
    '전자라도 참가비를 납부해야 합니다.',
])
def test_reference_particle_chains_are_not_an_independent_clause(reference):
    clauses = ['참가 대상: 재학생', '신청 대상: 졸업생']
    assert check({'conditions': clauses, 'notes': [reference]},
                 {'conditions': clauses[::-1], 'notes': [reference]})['quality_label'] == 'changed'


@pytest.mark.parametrize('reference', [
    '면제 대상은 전자입니다.', '면제 대상은 후자입니다.',
    '전자일 때 참가비를 면제합니다.', '면제 대상이 전자임을 확인합니다.',
    '전자여야만 참가비를 면제받습니다.', '후자든 참가비는 별도입니다.',
])
def test_copular_reference_inflections_still_constrain_other_fields(reference):
    clauses = ['참가 대상: 재학생', '신청 대상: 졸업생']
    assert check({'conditions': clauses, 'notes': [reference]},
                 {'conditions': clauses[::-1], 'notes': [reference]})['quality_label'] == 'changed'


@pytest.mark.parametrize('source,actual', [
    ('학생은 반드시 동의서만을 제출해야 합니다.',
     '동의서만은 학생이 반드시 제출해야 합니다.'),
    ('학생만은 반드시 보호자를 확인해야 합니다.',
     '보호자는 학생만이 반드시 확인해야 합니다.'),
])
def test_auxiliary_particles_do_not_become_unqualified_actor_object_bindings(source, actual):
    assert check({'conditions': [source]}, {'conditions': [actual]})['quality_label'] == 'changed'


@pytest.mark.parametrize('reference', [
    '전자면 참가비를 면제합니다.', '전자란 참가비가 면제되는 학생을 말합니다.',
])
def test_reference_guard_does_not_depend_on_enumerating_korean_endings(reference):
    clauses = ['참가 대상: 재학생', '신청 대상: 졸업생']
    assert check({'conditions': clauses, 'notes': [reference]},
                 {'conditions': clauses[::-1], 'notes': [reference]})['quality_label'] == 'changed'


@pytest.mark.parametrize('source,actual', [
    ('전원은 반드시 하나를 선택해야 합니다.', '하나는 전원이 반드시 선택해야 합니다.'),
    ('학생은 반드시 하나를 선택해야 합니다.', '하나는 학생이 반드시 선택해야 합니다.'),
])
def test_quantified_role_topicalization_still_needs_a_scope_proof(source, actual):
    # A bounded atomic case proof still cannot waive quantifier scope.
    assert check({'conditions': [source]}, {'conditions': [actual]})['quality_label'] == 'changed'


@pytest.mark.parametrize('noun', ['방위', '단위', '가위', '바위'])
def test_spatial_reference_is_not_a_suffix_inside_an_unrelated_noun(noun):
    clauses = [noun + ' 안내: 현장에서 확인', '도구 보증금: 9400원']
    assert check({'conditions': clauses}, {'conditions': clauses[::-1]})['quality_label'] == 'preserved'


@pytest.mark.parametrize('reference', ['위 조건은 필수입니다.', '위 내용은 별도입니다.',
                                       '앞 항목은 선택입니다.', '아래 항목은 별도입니다.',
                                       '최상위 항목의 참가자는 참가비를 면제합니다.',
                                       '최하위 항목의 참가자는 참가비를 면제합니다.',
                                       '차상위 조건의 대상은 보호자입니다.'])
def test_standalone_spatial_references_still_protect_the_referenced_order(reference):
    clauses = ['참가 유형: 학생', '신청 유형: 보호자']
    assert check({'conditions': clauses, 'notes': [reference]},
                 {'conditions': clauses[::-1], 'notes': [reference]})['quality_label'] == 'changed'


@pytest.mark.parametrize('source,actual', [
    ('두레담당자는 반드시 출석표를 확인합니다.', '두레담당자는 반드시 출석표를 확인해 주세요.'),
    ('세척요원은 반드시 각도기를 지참합니다.', '세척요원은 반드시 각도기를 지참해 주세요.'),
])
def test_noun_initial_syllables_are_not_quantifiers_when_no_roles_are_moved(source, actual):
    assert check({'conditions': [source]}, {'conditions': [actual]})['quality_label'] == 'preserved'


@pytest.mark.parametrize('source,actual', [
    ('학생은 반드시 철회하고 제출합니다.', '학생은 반드시 철회하고 제출해야 합니다.'),
    ('학생은 반드시 확인하며 작성합니다.', '학생은 반드시 확인하며 작성해야 합니다.'),
])
def test_verb_connectives_are_not_a_mandatory_instruction_object(source, actual):
    assert check({'conditions': [source]}, {'conditions': [actual]})['quality_label'] == 'changed'
