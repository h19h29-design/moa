"""Fixed class coordinates must not depend on option order or carry gold."""
import copy
import importlib
import json

import pytest

from moa.research_binary import binary_questions


def api():
    return importlib.import_module('moa.research_classifier')


def row(label='changed'):
    return dict(case_id='synthetic-case', state=dict(title='합성 안내', file_role='notice',
        source_complete=True, source_blocks=[dict(id='h', text='학과 | 교실'),
            dict(id='r', text='우주학과 | 201호'), dict(id='n', text='※ 신청 필수')],
        rendered_blocks=[dict(id='d', source_refs=['h', 'r', 'n'],
            visible_text='학과 | 교실\n우주학과 | 202호\n※ 신청 필수')], attachment_manifest=[]),
        questions=binary_questions(['table_relations']), expected={'table_relations': label})


class LiteralTokenizer:
    cls_token_id, sep_token_id, pad_token_id, mask_token_id = 1, 2, 0, 3
    mask_token = '[MASK]'

    def encode(self, text, *, add_special_tokens):
        assert add_special_tokens is False
        return [10 + ord(c) for c in text]


def test_target_and_reversed_criteria_never_change_the_encoded_evidence():
    a = row()
    b = row('preserved')
    b['questions']['table_relations']['criteria'] = dict(reversed(list(
        b['questions']['table_relations']['criteria'].items())))
    first, second = [api().encode(LiteralTokenizer(), x, maximum=4096) for x in (a, b)]
    assert first == second
    text = ''.join(chr(x - 10) for x in first['input_ids'] if x >= 10)
    assert '201호' in text and '202호' in text and '※ 신청 필수' in text
    assert 'source_refs' in text and 'preserved' not in text and 'changed' not in text
    assert first['input_ids'][0] == 1 and first['input_ids'][-1] == 2
    assert 3 not in first['input_ids']  # No option MASKs or marker positions.
    assert set(first) == {'input_ids', 'attention_mask'}
    assert api().target(a) == 1 and api().target(b) == 0


@pytest.mark.parametrize('fault', ['extra_gold', 'unknown_ref', 'duplicate_id', 'missing_evidence',
                                  'insufficient', 'altered_question'])
def test_ambiguous_or_incomplete_input_is_not_silently_a_binary_training_example(fault):
    item = row()
    if fault == 'extra_gold': item['state']['expected'] = 'changed'
    elif fault == 'unknown_ref': item['state']['rendered_blocks'][0]['source_refs'] = ['absent']
    elif fault == 'duplicate_id': item['state']['source_blocks'].append(item['state']['source_blocks'][0])
    elif fault == 'missing_evidence': item['state']['source_complete'] = False
    elif fault == 'insufficient': item['expected']['table_relations'] = 'insufficient'
    else: item['questions']['table_relations']['instructions'] += ' answer=changed'
    with pytest.raises(ValueError):
        api().target(item)
        api().encode(LiteralTokenizer(), item, maximum=4096)


def test_overflow_refuses_instead_of_dropping_source_header_or_footnote():
    with pytest.raises(ValueError, match='overflow'):
        api().encode(LiteralTokenizer(), row(), maximum=25)


def test_no_target_required_for_evaluation_and_input_does_not_mutate():
    x = row()
    del x['expected']
    before = copy.deepcopy(x)
    encoded = api().encode(LiteralTokenizer(), x, maximum=4096)
    assert x == before and encoded['attention_mask'] == [1] * len(encoded['input_ids'])


def test_raw_binary_class_metrics_do_not_include_rule_success_or_duplicate_cases():
    gold = {'n': 'preserved', 'e': 'changed'}
    records = [dict(case_id=i, prediction=p, probabilities={'preserved': a, 'changed': b},
                    system_forced=False) for i, p, a, b in [('n', 'changed', .2, .8),
                                                         ('e', 'changed', .1, .9)]]
    out = api().score(records, gold)
    assert out['accuracy'] == .5 and out['normal_false_positive_rate'] == 1.
    assert out['recall']['changed'] == 1. and out['incomplete'] == 0
    for bad in (records + [records[0]], [dict(records[0], system_forced=True), records[1]],
                [dict(records[0], prediction='preserved'), records[1]]):
        with pytest.raises(ValueError): api().score(bad, gold)


def test_budget_admission_does_not_spend_or_inherit_one_off_slots(tmp_path):
    from moa.research_campaign import BudgetLedger
    control = importlib.import_module('moa.research_classifier_control')
    ledger = BudgetLedger(tmp_path / 'BUDGET.jsonl')
    policy = dict(rolling_budget_days=7, max_train_time_rolling_seconds=28800,
        max_train_time_per_calendar_day_seconds=10800, max_candidate_runs_per_rolling_window=8,
        max_train_time_per_candidate_seconds=2700)
    for n in range(10):
        ledger.append(dict(at='2026-10-21T00:00:00+09:00', candidate='old-' + str(n), train_seconds=100))
    original = ledger.path.read_bytes()
    gate = control.admission(ledger, policy, at='2026-10-21T20:00:00+09:00')
    assert gate['status'] == 'WAITING_BUDGET' and gate['usage']['remaining_candidates'] == 0
    assert gate['earliest_budget_at'] == '2026-10-28T00:00:01+09:00'
    assert ledger.path.read_bytes() == original
    next_gate = control.admission(ledger, policy, at='2026-10-28T00:00:01+09:00')
    assert next_gate['status'] == 'BUDGET_AVAILABLE'


def test_full_reservation_and_daily_limit_are_checked_not_only_candidate_count(tmp_path):
    from moa.research_campaign import BudgetLedger
    control = importlib.import_module('moa.research_classifier_control')
    policy = dict(rolling_budget_days=7, max_train_time_rolling_seconds=28800,
        max_train_time_per_calendar_day_seconds=10800, max_candidate_runs_per_rolling_window=8,
        max_train_time_per_candidate_seconds=2700)
    ledger = BudgetLedger(tmp_path / 'BUDGET.jsonl')
    ledger.append(dict(at='2026-10-21T01:00:00+09:00', candidate='old', train_seconds=9500))
    out = control.admission(ledger, policy, at='2026-10-21T20:00:00+09:00')
    assert out['status'] == 'WAITING_BUDGET'
    assert out['earliest_budget_at'] == '2026-10-22T00:00:00+09:00'


def test_budget_wait_cannot_stage_launch_or_reuse_completed_candidate(tmp_path, monkeypatch):
    control = importlib.import_module('moa.research_classifier_control')
    from moa.research_campaign import BudgetLedger
    from moa import research_campaign as rc
    p = tmp_path / control.FOLDER
    p.mkdir()
    policy = dict(rolling_budget_days=7, max_train_time_rolling_seconds=28800,
        max_train_time_per_calendar_day_seconds=10800, max_candidate_runs_per_rolling_window=8,
        max_train_time_per_candidate_seconds=2700)
    rc._write_atomic(tmp_path / 'STATE.json', dict(policy_snapshot=policy))
    rc._write_atomic(p / 'PLAN.json', dict(run_id=control.RUN))
    rc._write_atomic(p / 'STATE.json', dict(status='PREPARED'))
    ledger = BudgetLedger(tmp_path / 'BUDGET.jsonl')
    for n in range(8):
        ledger.append(dict(at='2026-10-21T01:00:00+09:00', candidate='old-' + str(n), train_seconds=100))
    before = ledger.path.read_bytes()
    calls = []
    monkeypatch.setattr(control, 'verify', lambda *_: None)
    monkeypatch.setattr(control, '_remote', lambda *a, **k: calls.append(a))
    out = control.advance(tmp_path, at='2026-10-21T20:00:00+09:00')
    assert out['status'] == 'WAITING_BUDGET' and calls == []
    assert ledger.path.read_bytes() == before
    assert not (p / 'DISPATCH.json').exists()


def test_small_result_comparison_requires_same_dev_ids_not_option_average(tmp_path):
    control = importlib.import_module('moa.research_classifier_control')
    gold = {'n': 'preserved', 'e': 'changed'}
    records = [dict(case_id=i, prediction=p, probabilities={'preserved': a, 'changed': b},
                    system_forced=False) for i, p, a, b in [('n', 'preserved', .8, .2),
                                                         ('e', 'changed', .1, .9)]]
    old = {'r1': {'average': dict(accuracy=.5, normal_false_positive_rate=1.)}}
    out = control.compare(records, gold, old)
    assert out['models']['fixed_classifier']['accuracy'] == 1.
    assert out['models']['r1']['accuracy'] == .5
    assert out['baseline_execution'] == 'frozen_historical_same_dev_not_rerun'
    assert out['classifier_observations_per_item'] == 1
    assert out['new_blind'] is False and out['operational_promotion'] is False
