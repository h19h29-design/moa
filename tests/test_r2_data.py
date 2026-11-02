"""R2 guards: a bad split/oracle/DOM alignment must fail before transport."""
import copy
import hashlib
import pytest

from moa import r2_data as r2


def source(name, text, *, family='', allowed=True):
    return {'id': name, 'sha256': hashlib.sha256(name.encode()).hexdigest(),
            'text': text, 'family_ids': [family] if family else [],
            'rights_status': 'allowed' if allowed else 'unreviewed',
            'rights_evidence': 'independently_authored_synthetic', 'lineage_sha256': []}


def test_protected_repackaging_and_corrected_dates_are_excluded():
    old = source('old', '우천 시 실내 전시실로 이동합니다. 신청 마감은 2026년 10월 20일입니다.')
    repack = source('repack', old['text'].replace('20일', '21일'))
    aliases = source('alias', '새로운 문구', family='known-form')
    protected = dict(old, family_ids=['known-form'])
    rows = [repack, aliases, source('new', '교내 장비는 반출할 수 없으며 손상 시 즉시 보고하세요.')]
    grouped = r2.group_sources(rows, [protected])
    assert [m['id'] for g in grouped['groups'] for m in g['members']] == ['new']
    assert set(grouped['rejected']) == {'repack', 'alias'}


def test_indirect_family_alias_cannot_escape_the_protection_registry():
    old = source('old', '평가 전용 원문', family='family-one')
    alias = source('alias', '다른 원문', family='family-one')
    child = source('child', '전혀 다른 내용', family='family-two')
    child['lineage_sha256'] = [alias['sha256']]
    assert not r2.group_sources([alias, child], [old])['groups']


def test_semantic_family_groups_not_just_table_dimensions():
    a = source('a', '기기 대여', family='equipment')
    b = source('b', '정정 대여', family='equipment')
    c = source('c', '급식 횟수', family='meals')
    groups = r2.group_sources([a, b, c], [])['groups']
    assert sorted(len(g['members']) for g in groups) == [1, 2]
    split = r2.assign_splits(groups)
    assert split[a['sha256']] == split[b['sha256']]
    fixed = {a['sha256']: 'dev'}
    assert r2.assign_splits(groups, fixed)[b['sha256']] == 'dev'
    with pytest.raises(ValueError, match='split'):
        r2.assign_splits(groups, {a['sha256']: 'dev', b['sha256']: 'train'})


def test_unreviewed_rights_and_eval_derivatives_are_never_training_candidates():
    held = source('held', '미확인 원문', allowed=False)
    evaluation = dict(source('eval', '시험 자료'), evaluation_only=True)
    descendant = source('descendant', '파생 원문')
    descendant['lineage_sha256'] = [evaluation['sha256']]
    result = r2.group_sources([held, evaluation, descendant], [])
    assert not result['groups']
    assert len(result['rejected']) == 3


def test_fact_input_keeps_headers_unit_notes_merges_and_actual_dom_values():
    before = {'headers': ['항목', '대여료'], 'unit': '원/일',
              'rows': [['망원경', '2000']], 'geometry': [[0, 0, 2, 1]],
              'notes': ['파손 보증금은 별도입니다.'], 'conditions': ['학교 밖 반출 금지']}
    actual_dom = copy.deepcopy(before)
    actual_dom['rows'][0][1] = '3000'
    state = r2.fact_state(before, actual_dom)
    assert '3000' in state['rendered_blocks'][0]['visible_text']
    assert '2000' in state['source_blocks'][0]['text']
    for value in ('원/일', '파손 보증금', '학교 밖 반출 금지', 'geometry', 'headers'):
        assert value in state['source_blocks'][0]['text']
    assert set(state) == {'title', 'file_role', 'source_complete', 'source_blocks',
                          'rendered_blocks', 'attachment_manifest'}
    assert not any(k in str(state) for k in ('expected', 'mutation', 'gold_index', 'split'))


def test_gold_is_operation_truth_not_validator_output_and_noop_rejected():
    assert r2.operation_label({'deadline': '12일'}, {'deadline': '13일'}) == 'changed'
    assert r2.operation_label({'a': 1}, {'a': 1}) == 'preserved'
    assert r2.operation_label({}, {}, evidence_complete=False) == 'insufficient'
    with pytest.raises(ValueError, match='no.op'):
        r2.validate_operation({'a': 1}, {'a': 1}, 'changed', True)
    with pytest.raises(ValueError, match='oracle'):
        r2.validate_operation({'a': 1}, {'a': 2}, 'preserved', True)


def test_group_caps_and_frozen_partition_ratios():
    sources = [source(str(i), f'완전히 독립적인 주제 {chr(0xac00+i)}의 안내 조건', family=f'f{i}') for i in range(20)]
    groups = r2.group_sources(sources, [])['groups']
    result = r2.assign_splits(groups)
    assert [list(result.values()).count(s) for s in ('train', 'dev', 'blind')] == [12, 4, 4]
    with pytest.raises(ValueError, match='100'):
        r2.assign_splits([{'id': str(i), 'members': [source(str(i), str(i))]} for i in range(101)])


def test_metrics_include_missing_and_separate_dangerous_confusions():
    rows = [dict(expected='changed', prediction='preserved', group_id='g1', error='relation'),
            dict(expected='preserved', prediction='changed', group_id='g1', error='normal'),
            dict(expected='insufficient', prediction=None, group_id='g2', error='header')]
    m = r2.metrics(rows)
    assert m['items'] == 3 and m['accuracy_all'] == 0
    assert m['uncompleted'] == 1 and m['groups'] == 2
    assert m['confusion']['changed']['preserved'] == 1
    assert m['normal_false_positives'] == 1 and m['normal_review_requests'] == 1
    assert m['by_error']['relation']['items'] == 1


def test_plain_control_uses_real_rows_not_python_list_representations():
    state = r2.fact_state({'headers':['장비','보관함'],'rows':[['돋보기','가함']],
                           'unit':'원/1회','notes':['추가 보증금 별도'],'conditions':['교외 반출 금지']},
                          {'headers':['장비','보관함'],'rows':[['돋보기','나함']],
                           'unit':'원/1회','notes':['추가 보증금 별도'],'conditions':['교외 반출 금지']})
    plain = r2.text_state(state)
    assert '장비 / 보관함\n돋보기 / 가함' in plain['source_blocks'][0]['text']
    assert '돋보기 / 나함' in plain['rendered_blocks'][0]['visible_text']
    assert "['" not in plain['source_blocks'][0]['text']
    assert '교외 반출 금지' in plain['rendered_blocks'][0]['visible_text']
