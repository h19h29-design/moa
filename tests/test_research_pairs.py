import copy

import pytest

from moa import research_pairs as pairs


def sections():
    return [
        {'id': 'intro', 'text': '학교 축제 안내', 'context_ids': []},
        {'id': 'head', 'text': '학과 | 교실', 'context_ids': []},
        {'id': 'unit', 'text': '재료비 단위: 원', 'context_ids': []},
        {'id': 'note', 'text': '※ 우천 시 교실 변경, 공지 확인 필수', 'context_ids': []},
        {'id': 'rows', 'text': '철학과 | 2-1\n문헌정보과 | 2-2',
         'context_ids': ['head', 'unit', 'note']},
    ]


def blocks():
    return [{'id': 'dom-' + s['id'], 'source_refs': [s['id']],
             'visible_text': s['text']} for s in sections()]


def test_pair_keeps_relationships_and_context_but_not_unrelated_document():
    result = pairs.align(sections(), blocks(), focus_ids=['rows'])
    assert [s['id'] for s in result['source_blocks']] == ['head', 'unit', 'note', 'rows']
    assert result['source_blocks'][-1]['text'] == '철학과 | 2-1\n문헌정보과 | 2-2'
    assert '학교 축제' not in str(result)
    assert result['source_complete'] is True
    assert result['alignment']['observed_dom_ids'] == [b['id'] for b in blocks()[1:]]


def test_missing_source_context_is_evidence_gap_but_missing_dom_is_not_hidden():
    src = [s for s in sections() if s['id'] != 'note']
    result = pairs.align(src, blocks(), focus_ids=['rows'])
    assert result['source_complete'] is False
    assert result['alignment']['missing_context_ids'] == ['note']
    rendered = [b for b in blocks() if b['source_refs'] != ['rows']]
    result = pairs.align(sections(), rendered, focus_ids=['rows'])
    assert result['source_complete'] is True
    assert result['alignment']['missing_render_ids'] == ['rows']
    assert not any(b['visible_text'] == sections()[-1]['text']
                   for b in result['rendered_blocks'])


def test_unmapped_dom_addition_is_retained_and_no_source_repair_occurs():
    dom = blocks() + [{'id': 'new', 'source_refs': [], 'visible_text': '입장료 5000원'}]
    result = pairs.align(sections(), dom, focus_ids=['rows'])
    assert result['rendered_blocks'][-1]['visible_text'] == '입장료 5000원'
    assert result['alignment']['unmapped_dom_ids'] == ['new']
    assert '입장료' not in str(result['source_blocks'])


def test_pair_refuses_duplicate_identity_and_keeps_actual_dom_changed_values():
    dom = blocks()
    dom[-1]['visible_text'] = '철학과 | 2-2\n문헌정보과 | 2-1'
    result = pairs.align(sections(), dom, focus_ids=['rows'])
    assert result['rendered_blocks'][-1]['visible_text'] == dom[-1]['visible_text']
    with pytest.raises(ValueError, match='duplicate'):
        pairs.align(sections() + [copy.deepcopy(sections()[0])], dom, focus_ids=['rows'])


def test_gold_free_worker_input_has_no_operation_or_expected_fields():
    result = pairs.align(sections(), blocks(), focus_ids=['rows'])
    state = pairs.model_state(result)
    assert set(state) == {'title', 'file_role', 'source_complete', 'source_blocks',
                          'rendered_blocks', 'attachment_manifest'}
    assert 'alignment' not in state
    assert 'expected' not in state
