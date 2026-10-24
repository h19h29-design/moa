"""Local research diagnostics; never treats saved weights as learned competence.

Labels are independent operation truth. Repairs only reassign an applicable
question to remove question-only shortcuts, preserving facts, labels and splits.
Neither a fit gate nor a scoped comparison is a production approval gate.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from collections import Counter

from .worker_handoff import questions

LABELS = ('preserved', 'changed', 'insufficient')
CONTROL_REVISION = 'moa.question-controls.v1'


def audit_question_controls(rows):
    rows = list(rows)
    splits = {row.get('meta',{}).get('split') for row in rows}
    if splits and (len(splits) != 1 or not splits <= {'train','dev'}):
        raise ValueError('audit a single train/dev split')
    definitions = questions()
    counts = {}
    for row in rows:
        if len(row['expected']) != 1 or set(row['questions']) != set(row['expected']):
            raise ValueError('one aligned question required')
        criterion, label = next(iter(row['expected'].items()))
        if criterion not in definitions or row['questions'][criterion] != definitions[criterion]:
            raise ValueError('shared question definition required')
        if label not in LABELS:
            raise ValueError('independent label required')
        counts.setdefault(criterion, Counter())[label] += 1
    n = sum(sum(c.values()) for c in counts.values())
    missing = {q:[label for label in LABELS if not c[label]] for q,c in counts.items()}
    return {'rows':n, 'counts':{q:{label:c[label] for label in LABELS} for q,c in counts.items()},
            'missing_controls':{q:labels for q,labels in missing.items() if labels},
            'question_only_baseline':sum(max(c.values()) for c in counts.values())/n if n else None,
            'controls_ready':bool(n) and not any(missing.values()),
            'training_started':False, 'operational_ready':False}


def repair_question_controls(rows):
    result, splits, ids, payloads = copy.deepcopy(rows), {}, set(), set()
    definitions = questions()
    for row in result:
        meta = row['meta']
        if (meta.get('split') not in ('train','dev') or meta.get('synthetic_gold') is not True or
            meta.get('human_gold') is not False or meta.get('evaluation_only') is not False or
            meta.get('rights_status') != 'allowed' or
            meta.get('rights_evidence') != 'independently_authored_synthetic_by_moa' or
            meta.get('label_origin') != 'deterministic_oracle' or meta.get('forbidden_lineage') != []):
            raise ValueError('permitted independent synthetic train/dev only')
        if row['case_id'] in ids:
            raise ValueError('duplicate case')
        ids.add(row['case_id'])
        for key in ('original_group_id','family_group_id','source_sha256'):
            identity = key,meta[key]
            if identity in splits and splits[identity] != meta['split']:
                raise ValueError('source/family split leakage')
            splits[identity] = meta['split']
        if len(row['expected']) != 1 or set(row['questions']) != set(row['expected']):
            raise ValueError('one aligned question required')
        previous, label = next(iter(row['expected'].items()))
        if label not in LABELS or previous not in definitions or row['questions'][previous] != definitions[previous]:
            raise ValueError('question or independent label mismatch')
        # These are genuinely omission questions in both normal and changed
        # examples. A changed-only omission bucket encouraged a question shortcut.
        criterion = 'omission' if meta['operation_name'] in (
            'normal_condition','normal_note','missing_note') else previous
        row['questions'] = {criterion:copy.deepcopy(definitions[criterion])}
        row['expected'] = {criterion:label}
        payload = json.dumps({'state':row['state'],'questions':row['questions']},
            ensure_ascii=False,sort_keys=True,separators=(',', ':'),allow_nan=False)
        if payload in payloads:
            raise ValueError('duplicate model input after question repair')
        payloads.add(payload)
        # If a future shard carries soft gold, move its key; never change its values.
        if 'gold' in row:
            if set(row['gold']) != {previous}:
                raise ValueError('gold question mismatch')
            row['gold'] = {criterion:row['gold'][previous]}
        parent = row['case_id']
        row['case_id'] = 'qc1-' + hashlib.sha256(parent.encode()).hexdigest()[:24]
        meta.update(question_control_revision=CONTROL_REVISION, parent_case_id=parent,
                    previous_question=previous)
    return result


def diagnose_fit(records, *, minimum_per_label=8, minimum_recall=.8):
    """A train-only underfit alarm, not a generalization/98% quality claim."""
    confusion = {a:{b:0 for b in LABELS} for a in LABELS}
    counts, predictions, losses = Counter(), Counter(), []
    uncompleted = 0
    for row in records:
        actual, predicted = row['expected'],row['prediction']
        if actual not in LABELS or predicted not in (*LABELS,None):
            raise ValueError('raw model label')
        counts[actual] += 1
        if predicted is None:
            uncompleted += 1
        else:
            predictions[predicted] += 1
            confusion[actual][predicted] += 1
        probs = row.get('probabilities',{})
        if probs:
            if (set(probs) != set(LABELS) or any(not isinstance(v,(int,float)) or
                not math.isfinite(v) or not 0 <= v <= 1 for v in probs.values()) or
                abs(sum(probs.values())-1) > .002):
                raise ValueError('raw model probabilities')
            losses.append(-math.log(max(probs[actual],1e-12)))
    n = sum(counts.values())
    recalls = {label:confusion[label][label]/counts[label] if counts[label] else None for label in LABELS}
    accuracy = sum(confusion[label][label] for label in LABELS)/n if n else None
    sufficient = all(counts[label] >= minimum_per_label for label in LABELS)
    learned = sufficient and not uncompleted and all(recalls[label] >= minimum_recall for label in LABELS)
    return {'cohort':'training_diagnostic_only', 'items':n, 'accuracy':accuracy,
            'majority_baseline':max(counts.values())/n if n else None,
            'confusion':confusion, 'prediction_counts':dict(predictions), 'recall':recalls,
            'changed_recall':recalls['changed'], 'uncompleted':uncompleted,
            'normal_false_positive_rate':confusion['preserved']['changed']/counts['preserved'] if counts['preserved'] else None,
            'mean_nll':sum(losses)/len(losses) if losses else None,
            'uniform_nll':math.log(3), 'generalization_accuracy':None,
            'reason':'training_task_learned_requires_independent_test' if learned else
                     'training_task_not_learned' if sufficient else 'insufficient_diagnostic_support',
            'ready_for_independent_evaluation':learned,
            'minimum_examples_per_label':minimum_per_label, 'minimum_recall':minimum_recall,
            'human_approved':False, 'operational_promotion_allowed':False}
