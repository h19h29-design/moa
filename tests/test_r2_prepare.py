"""No fake browser success, blind answer transport, or same-number padding."""
import copy
import json
import pytest

from moa import r2_prepare as p
from moa import r2_data as r2


def topics():
    # Unit fixtures, not the private R2 author seeds or blind sources.
    return [(f'시험 주제 {chr(0xac00+i)}', f'대상 {chr(0xb098+i)}', f'위치 {chr(0xb2e4+i)}',
             f'항목 {chr(0xb77c+i)}', f'항목 {chr(0xb9c8+i)}', '왼쪽', '오른쪽',
             f'시험 조건 {chr(0xbc14+i)}을 확인해야 합니다.',
             f'시험 각주 {chr(0xc0ac+i)}는 별도입니다.') for i in range(20)]


def test_authored_native_sources_are_independent_and_grouped_before_mutations(tmp_path):
    result = p.prepare(tmp_path / 'bundle', [], topics())
    assert result['groups'] == 20
    assert result['split_groups'] == {'train': 12, 'dev': 4, 'blind': 4}
    assert result['new_real_training_originals'] == 0
    assert result['human_approvals'] == 0 and result['training_started'] is False
    assert not (tmp_path / 'bundle' / 'R2_READY.json').exists()
    spec = json.loads((tmp_path / 'bundle' / 'DOM_SPEC.json').read_bytes())
    assert len(spec['documents']) == 20
    assert len({d['source']['sha256'] for d in spec['documents']}) == 20
    assert all(d['source']['rights_status'] == 'allowed' for d in spec['documents'])
    # No browser evidence yet -> no claim of train/dev/blind readiness.
    with pytest.raises(ValueError, match='browser'):
        p.finalize(tmp_path / 'bundle', {'documents': []})


def test_protected_template_is_excluded_and_reported_not_replaced(tmp_path):
    source = p.authored_sources(topics())[0]
    result = p.prepare(tmp_path / 'bundle', [dict(source, id='protected')], topics())
    assert result['groups'] == 19 and result['excluded_sources'] >= 1
    assert result['target_group_shortfall'] == 81


def test_fact_scope_cannot_drop_required_context_or_repair_dom_from_source():
    native = {'paragraphs': ['題', '期限: 7일', '시각: 10시', '금액: 3원', '선택: 2개',
                             '장비 반출 금지', '원/일', '대여자는 보증금을 별도 납부'],
              'table': {'headers': ['장비', '보관함'], 'rows': [['돋보기', '가함'], ['저울', '나함']],
                        'geometry': []}}
    observation = {'paragraphs': list(native['paragraphs']), 'table': copy.deepcopy(native['table'])}
    observation['paragraphs'][1] = '期限: 8일'
    before, after = p.scope_facts(native, observation, 'scalar', 1)
    assert before['conditions'] == after['conditions'] == ['장비 반출 금지']
    assert before['notes'] == after['notes'] == ['대여자는 보증금을 별도 납부']
    assert after['facts'] != before['facts']
    observation['paragraphs'][1] = ''
    _, after = p.scope_facts(native, observation, 'scalar', 1)
    assert after['facts'] == []


def test_existing_ready_bundle_is_not_overwritten(tmp_path):
    root = tmp_path/'bundle'
    root.mkdir()
    ready = root/'R2_READY.json'
    ready.write_text('{"frozen":true}')
    with pytest.raises(ValueError, match='already'):
        p.finalize(root, {'documents':[]})
    assert ready.read_text() == '{"frozen":true}'


def test_partition_file_cannot_change_after_source_freeze(tmp_path):
    root = tmp_path/'bundle'
    p.prepare(root, [], topics()[:2])
    path = root/'SPLITS.json'
    path.write_bytes(path.read_bytes().replace(b'train', b'blind'))
    with pytest.raises(ValueError,match='frozen'):
        p.finalize(root, {'documents':[]})


def test_unordered_keyed_rows_keep_multiplicity_and_pair_identity():
    source = {'headers':['대상','보관함'], 'rows':[['돋보기','가함'],['저울','나함']],
              'unit':'개', 'notes':['각 대상은 지정 보관함 하나만 사용합니다.']}
    reordered = copy.deepcopy(source)
    reordered['rows'].reverse()
    assert r2.operation_label(p._truth(source),p._truth(reordered)) == 'preserved'
    for rows in ([['돋보기','가함']],
                 [['돋보기','가함'],['저울','나함'],['저울','나함']],
                 [['돋보기','가함'],['저울','나함'],['추가 물품','다함']],
                 [['돋보기','나함'],['저울','가함']]):
        changed = {**source,'rows':rows}
        assert r2.operation_label(p._truth(source),p._truth(changed)) == 'changed'


def test_formatter_and_generator_are_frozen_before_variants(tmp_path):
    root=tmp_path/'bundle'
    p.prepare(root,[],topics())
    freeze=json.loads((root/'SOURCE_FREEZE.json').read_bytes())
    freeze['code_sha256']['generator']='0'*64
    (root/'SOURCE_FREEZE.json').write_text(json.dumps(freeze))
    with pytest.raises(ValueError,match='code.*changed'):
        p.finalize(root,{'documents':[]})


def test_all_protected_sources_cannot_become_an_empty_ready_bundle(tmp_path):
    root=tmp_path/'bundle'
    sources=p.authored_sources(topics())
    result=p.prepare(root,sources,topics())
    assert result['groups']==0 and result['target_group_shortfall']==100
    with pytest.raises(ValueError,match='eligible source'):
        p.finalize(root,{'documents':[]})
