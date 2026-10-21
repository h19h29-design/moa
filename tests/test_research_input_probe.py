"""A lossless paired view must not invent output facts or leak answers."""
import copy
import importlib
import importlib.util
import json
from pathlib import Path

import pytest


def helper():
    path = Path(__file__).parents[1] / 'scripts/moa-research-book-input-probe.py'
    assert path.exists(), 'frozen-weight MacBook input probe required'
    spec = importlib.util.spec_from_file_location('input_probe', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def state():
    return dict(title='합성 안내', file_role='notice', source_complete=True,
        attachment_manifest=[dict(name='동의서', role='form')],
        source_blocks=[dict(id='h', text='학과 | 교실'), dict(id='u', text='금액 단위: 원'),
                       dict(id='a', text='우주학과 | 201호'), dict(id='n', text='※ 사전 신청 필수')],
        rendered_blocks=[dict(id='d1', source_refs=['a'], visible_text='우주학과 | 202호'),
                         dict(id='d2', source_refs=['h', 'a'], visible_text='학과 | 교실'),
                         dict(id='d3', source_refs=[], visible_text='추가 문장')])


def decoded_blocks(view, prefix):
    return [json.loads(line.split(': ', 1)[1]) for line in view.splitlines() if line.startswith(prefix + ': ')]


def test_pairing_preserves_header_units_notes_relations_and_actual_dom_without_copying_gold():
    original = state()
    before = copy.deepcopy(original)
    view = helper().paired_state(original)
    assert original == before
    assert decoded_blocks(view, '원문') == original['source_blocks']
    html = decoded_blocks(view, '실제 HTML')
    assert {x['id']: x for x in html} == {x['id']: x for x in original['rendered_blocks']}
    assert len(html) == 3  # A multi-reference DOM block is not duplicated.
    assert view.index('우주학과 | 201호') < view.index('우주학과 | 202호')
    assert '원문 대응 없음' in view
    order = json.loads(next(x.split(': ', 1)[1] for x in view.splitlines() if x.startswith('실제 DOM 순서: ')))
    assert order == ['d1', 'd2', 'd3']
    meta = json.loads(next(x.split(': ', 1)[1] for x in view.splitlines() if x.startswith('문서 정보: ')))
    assert meta == {k: v for k, v in original.items() if k not in ('source_blocks', 'rendered_blocks')}


@pytest.mark.parametrize('fault', ['gold', 'duplicate_source', 'duplicate_dom', 'bad_text', 'bad_refs', 'unknown_field'])
def test_projection_refuses_ambiguous_or_answer_bearing_input(fault):
    row = state()
    if fault == 'gold': row['expected'] = 'changed'
    elif fault == 'duplicate_source': row['source_blocks'].append(row['source_blocks'][0])
    elif fault == 'duplicate_dom': row['rendered_blocks'].append(row['rendered_blocks'][0])
    elif fault == 'bad_text': row['source_blocks'][0]['text'] = None
    elif fault == 'bad_refs': row['rendered_blocks'][0]['source_refs'] = 'a'
    else: row['rendered_blocks'][0]['expected'] = 'changed'
    with pytest.raises(ValueError): helper().paired_state(row)


def test_unresolved_reference_and_missing_rendered_evidence_are_retained_not_filled():
    row = state()
    row['rendered_blocks'][0]['source_refs'] = ['not_acquired']
    view = helper().paired_state(row)
    html = decoded_blocks(view, '실제 HTML')
    assert len(html) == len(row['rendered_blocks'])
    assert any(x['source_refs'] == ['not_acquired'] for x in html)
    assert not any(x['visible_text'] == '금액 단위: 원' for x in html)
    assert '자동 보충하지 않음' in view


def api():
    assert importlib.util.find_spec('moa.research_input_probe'), 'input-only comparison required'
    return importlib.import_module('moa.research_input_probe')


def observation(identity, gold, score, mode):
    return dict(case_id=identity, model='binary_candidate', split='dev', family='f', operation='identity',
        mode=mode, prediction='changed' if score > .5 else 'preserved', system_forced=False,
        probabilities={'preserved': 1-score, 'changed': score},
        logits={'preserved': 0., 'changed': 1. if score > .5 else -1.})


def test_only_input_changes_can_be_compared_without_calling_it_new_blind_or_training():
    from moa.research_binary import binary_questions
    rows = [dict(case_id=i, family='f', operation='identity', state=state(),
        questions=binary_questions(['table_relations'])) for i in ['n', 'e']]
    old = [observation(i, g, .8, mode) for i, g in [('n', 'preserved'), ('e', 'changed')]
           for mode in ['binary', 'binary_reverse']]
    new = [dict(r, prediction='preserved', probabilities={'preserved': .8, 'changed': .2},
                logits={'preserved': 0., 'changed': -1.}) if r['case_id'] == 'n' else r for r in old]
    out = api().compare(rows, old, new, {'n': 'preserved', 'e': 'changed'},
        train_counts={'preserved': 1, 'changed': 2})
    assert out['original_input']['average']['normal_false_positive_rate'] == 1.
    assert out['paired_input']['average']['normal_false_positive_rate'] == 0.
    assert out['paired_input']['average']['error_miss_rate'] == 0.
    assert out['training_updates'] == 0 and out['new_candidates'] == 0
    assert out['new_blind'] is False and out['threshold_fitted'] is False
    assert out['operational_promotion'] is False
    broken = copy.deepcopy(new)
    broken[0]['operation'] = 'different'
    with pytest.raises(ValueError):
        api().compare(rows, old, broken, {'n': 'preserved', 'e': 'changed'}, train_counts={'preserved': 1, 'changed': 2})


def test_prior_audit_full_reservation_not_just_observed_seconds_is_carried():
    recovery = {'evaluation_progress': {'seconds': 55.5}}
    audit = dict(cumulative_reserved_seconds=145.5, cumulative_observed_seconds=60.,
        training_updates=0, model_inferences=0)
    assert api().prior_seconds(recovery, audit) == 145.5
    with pytest.raises(ValueError):
        api().prior_seconds(recovery, dict(audit, cumulative_reserved_seconds=60.))


def test_manual_probe_cannot_dispatch_a_missing_prior_audit(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(api().forensics, 'audit', lambda *_: calls.append('unexpected'))
    with pytest.raises(ValueError, match='completed immutable'):
        api().run(tmp_path)
    assert calls == []


@pytest.mark.parametrize('prior', [float('nan'), -1, 550])
def test_invalid_prior_evaluation_budget_cannot_be_erased(prior):
    with pytest.raises(ValueError):
        api().prior_seconds({'evaluation_progress': {'seconds': prior}},
            dict(cumulative_reserved_seconds=prior + 90, cumulative_observed_seconds=prior + 5,
                 training_updates=0, model_inferences=0))


def test_context_resolves_pinned_relative_library_from_incoming_not_ssh_home(tmp_path, monkeypatch):
    module = helper()
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    incoming = tmp_path / 'incoming' / module.RUN
    incoming.mkdir(parents=True)
    job = incoming / 'JOB-r1.json'
    job.write_text('{}')
    (incoming / 'r2_research.py').write_text('pinned runtime')
    monkeypatch.chdir(tmp_path)
    class Worker:
        def context(self, path):
            assert path == job
            return Path('r2_research.py').read_text()
    assert module.load_context(Worker(), job) == 'pinned runtime'


def test_preload_correction_carries_failed_attempt_full_time_without_budget_reset():
    old = dict(prior_evaluation_seconds=145.5, reserved_seconds=90, maximum_cumulative_seconds=600,
               training_updates=0, new_candidates=0, execution_host='macbookair')
    assert api().carry_preload_reservation(145.5, old) == 235.5
    with pytest.raises(ValueError):
        api().carry_preload_reservation(145.5, dict(old, prior_evaluation_seconds=55.5))

