"""Research-only evidence gate followed by genuine two-choice semantics.

No training, operational approval or model promotion is reachable here. A rule
decision is never reported as a model's insufficient prediction. Existing
evaluation lineage remains evaluation-only when preparing the dev derivative.
"""
from __future__ import annotations

import copy
import math
from collections import Counter

from .evidence import assess_source_evidence
from .worker_handoff import CRITERIA, questions

VERSION = 'moa.evidence-then-binary.v1'
LABELS = ('preserved', 'changed')


def binary_questions(criteria):
    base = questions()
    if not criteria or any(k not in base for k in criteria):
        raise ValueError('known nonempty semantic questions required')
    return {k: {'type': 'choice', 'instructions': CRITERIA[k] +
        ' 제공된 증거만 사용하라. 근거 확보 검사를 통과한 구간의 내용과 관계만 비교하라.',
        'criteria': {label: base[k]['criteria'][label] for label in LABELS}}
        for k in criteria}


def model_input(row):
    qs=binary_questions(list(row['questions']))
    if row['questions']!=qs:
        raise ValueError('different binary question contract')
    state={k:copy.deepcopy(row['state'][k]) for k in
        ('title','file_role','source_complete','source_blocks','rendered_blocks','attachment_manifest')}
    return {'case_id':row['case_id'],'state':state,'questions':qs}


def average_orders(first, second):
    """Equal-weight two-order inference, not calibration or new model training.

    The caller must actually infer both permutations of the same question and
    input. Never construct this by renormalizing an old three-choice response.
    """
    for probs in (first,second):
        if (set(probs)!=set(LABELS) or any(type(p) not in (float,int) or
                not math.isfinite(p) or not 0<=p<=1 for p in probs.values()) or
                abs(sum(probs.values())-1)>.002):
            raise ValueError('two actual two-option distributions required')
    probs={k:(first[k]+second[k])/2 for k in LABELS}
    prediction=None if probs['preserved']==probs['changed'] else max(probs,key=probs.get)
    return {'prediction':prediction,'probabilities':probs,
        'prediction_origin':'two_actual_order_average_not_retraining','calibrated':False}


def route(state, provenance, *, direct_content=None, model_raw=None):
    evidence = assess_source_evidence(provenance, state)
    eligible = not evidence['review_required']
    if model_raw is not None:
        probs = model_raw.get('probabilities', {})
        if (set(probs) != set(LABELS) or model_raw.get('prediction') not in LABELS or
                any(type(p) not in (int,float) or not math.isfinite(p) or not 0 <= p <= 1
                    for p in probs.values()) or abs(sum(probs.values())-1) > .002):
            raise ValueError('raw inference must have exactly two actual options')
        if not eligible:
            raise ValueError('model must not be called on incomplete source evidence')
        if probs[model_raw['prediction']] < max(probs.values()):
            raise ValueError('raw label differs from actual two-option argmax')
    hard = direct_content or {}
    if hard.get('prediction') == 'changed' and hard.get('confirmed_mismatch') is True:
        label, origin = 'changed', 'direct_confirmed_mismatch'
    elif not eligible:
        label, origin = 'insufficient', 'evidence_rule'
    elif model_raw is None:
        label, origin = 'insufficient', 'semantic_inference_pending'
    else:
        label, origin = model_raw['prediction'], 'raw_binary_model'
    return {'version': VERSION, 'evidence': evidence, 'model_eligible': eligible,
            'quality_label': label, 'decision_origin': origin,
            'direct_content': copy.deepcopy(direct_content), 'model_raw': copy.deepcopy(model_raw),
            'review_required': True, 'human_approved': False, 'publish_allowed': False,
            'operational_promotion': False}


def prepare_split(rows, provenances, *, split):
    if split not in ('train','dev'):
        raise ValueError('evaluation/blind material cannot enter a training preparation')
    output, excluded, ids = [], [], set()
    for row in rows:
        identity, meta = row['case_id'], row['meta']
        if identity in ids:
            raise ValueError('duplicate input identity')
        ids.add(identity)
        if meta.get('split') != split or (split == 'train' and meta.get('evaluation_only') is not False):
            raise ValueError('evaluation lineage or split mismatch')
        if (meta.get('synthetic_gold') is not True or meta.get('human_gold') is not False or
                meta.get('rights_status') != 'allowed' or
                meta.get('rights_evidence') != 'independently_authored_synthetic_by_moa' or
                meta.get('label_origin') not in ('independent_fact_edit_replay',
                    'independent_source_and_recorded_operation') or meta.get('forbidden_lineage') != []):
            raise ValueError('independent permitted synthetic truth required')
        if set(row['questions']) != set(row['expected']) or len(row['expected']) != 1:
            raise ValueError('one aligned semantic question required')
        report = assess_source_evidence(provenances[identity], row['state'])
        if report['review_required']:
            excluded.append(identity)
            continue
        if next(iter(row['expected'].values())) not in LABELS:
            raise ValueError('independent truth contradicts complete evidence inventory')
        new = copy.deepcopy(row)
        # Restore the same stable six-field order as the installed policy.
        new['state'] = {k: copy.deepcopy(row['state'][k]) for k in
            ('title','file_role','source_complete','source_blocks','rendered_blocks','attachment_manifest')}
        new['state']['source_complete'] = report['source_complete']
        new['questions'] = binary_questions(list(row['questions']))
        new['meta'].update(binary_input_version=VERSION, parent_case_id=identity,
                           evidence_provenance_sha256=report['provenance_sha256'])
        output.append(new)
    return {'rows':output, 'excluded_ids':excluded, 'training_authorized':False,
            'lineage':'derivative_of_existing_train_or_dev_not_a_new_blind',
            'operational_ready':False}


def metrics(records):
    counts=Counter()
    matrix={g:{p:0 for p in (*LABELS,'incomplete')} for g in LABELS}
    for row in records:
        gold,pred=row['expected'],row['prediction']
        if gold not in LABELS or pred not in (*LABELS,None):
            raise ValueError('binary raw model answers only; do not count rule insufficiency')
        if row.get('system_forced'):
            raise ValueError('binary raw model answers only')
        counts[gold]+=1
        matrix[gold][pred or 'incomplete']+=1
    n=sum(counts.values())
    recalls={g:matrix[g][g]/counts[g] if counts[g] else None for g in LABELS}
    return {'items':n,'correct':sum(matrix[g][g] for g in LABELS),'confusion':matrix,
        'accuracy':sum(matrix[g][g] for g in LABELS)/n if n else None,
        'balanced_accuracy':sum(recalls.values())/2 if all(counts[g] for g in LABELS) else None,
        'recall':recalls,'normal_false_positive_rate':matrix['preserved']['changed']/counts['preserved'] if counts['preserved'] else None,
        'error_miss_rate':1-recalls['changed'] if counts['changed'] else None,
        'majority_baseline':max(counts.values())/n if n else None,
        'incomplete':sum(matrix[g]['incomplete'] for g in LABELS)}
