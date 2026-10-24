"""Actual two-choice training, not a renamed three-way inference response."""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from moa import research_binary as binary, worker_handoff


def worker():
    spec = importlib.util.spec_from_file_location('binary_training_worker',
        Path(__file__).parents[1]/'scripts/moa-research-worker.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def policy():
    return SimpleNamespace(questions=lambda ids:{k:worker_handoff.questions()[k] for k in ids}, CRITERIA=worker_handoff.CRITERIA,
        state_for=lambda s: {k:s[k] for k in ('title','file_role','source_complete',
            'source_blocks','rendered_blocks','attachment_manifest')})


def row(label='changed'):
    return {'case_id':'train-only', 'state':{'title':'합성','file_role':'notice',
        'source_complete':True, 'source_blocks':[{'id':'s','text':'마감: 12월 3일'}],
        'rendered_blocks':[{'id':'h','source_refs':['s'],'visible_text':'마감: 12월 4일'}],
        'attachment_manifest':[]}, 'questions':binary.binary_questions(['conditions']),
        'expected':{'conditions':label}}


def test_binary_training_uses_two_real_targets_and_keeps_gold_out_of_state():
    m=worker(); original=json.loads(json.dumps(row(),sort_keys=True))
    actual=m.queue([original],policy(),binary.binary_questions(['conditions']),
        with_gold=True,semantic_profile='moa.binary-training.v1')[0]
    assert list(actual['questions']['conditions']['criteria'])==['preserved','changed']
    assert actual['expected']=={'conditions':'changed'}
    assert actual['state']==original['state'] and 'expected' not in actual['state']
    assert actual['questions']==binary.model_input(original)['questions']
    without_gold=m.queue([original],policy(),actual['questions'],
        semantic_profile='moa.binary-training.v1')[0]
    assert set(without_gold)=={'case_id','state','questions'}


@pytest.mark.parametrize('problem',['third_gold','third_option','missing_evidence','question_change','unknown_profile'])
def test_binary_training_fails_closed_instead_of_silently_relabeling(problem):
    r=row(); profile='moa.binary-training.v1'
    if problem=='third_gold':r['expected']['conditions']='insufficient'
    if problem=='third_option':r['questions']={'conditions':worker_handoff.questions()['conditions']}
    if problem=='missing_evidence':r['state']['source_complete']=False
    if problem=='question_change':r['questions']['conditions']['instructions']='different'
    if problem=='unknown_profile':profile='binary-ish'
    with pytest.raises(ValueError):
        worker().queue([r],policy(),binary.binary_questions(['conditions']),
            with_gold=True,semantic_profile=profile)


def test_existing_three_choice_jobs_are_unchanged():
    r=row('insufficient');r['questions']={'conditions':worker_handoff.questions()['conditions']}
    actual=worker().queue([r],policy(),r['questions'],with_gold=True)[0]
    assert set(actual['questions']['conditions']['criteria'])=={'preserved','changed','insufficient'}
    assert actual['expected']==r['expected']


def test_controlled_binary_configuration_changes_no_unrelated_training_knobs():
    spec=importlib.util.find_spec('moa.research_binary_control')
    assert spec is not None, 'separate bounded binary training controller required'
    from moa.research_binary_control import configuration
    from moa.research_alignment import paired_configuration
    old=paired_configuration(144); before=copy.deepcopy(old)
    new=configuration(old,108)
    assert new['planned_updates']==56
    assert {k for k in new if new[k]!=old[k]}=={'planned_updates'}
    assert new['maximum_updates']==120 and new['maximum_seconds']==1800
    assert old==before
