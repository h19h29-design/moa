"""Unattended synthetic-research campaign control (no operational side effects).

This module owns the local campaign bookkeeping only: an append-only output
ledger, rolling 7-day and per-calendar-day budget checks, atomic state files,
an operator pause marker and a one-hypothesis-at-a-time experiment selector.
It never trains, never talks to the network and never touches the operational
NAS database, collector or registry.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from pathlib import Path


KST = dt.timezone(dt.timedelta(hours=9))


class CampaignRefused(Exception):
    """A start/rearm request that would reuse or reset campaign identity."""


def _abs_policy():
    """The shipped limits, used only if a state file lost its snapshot."""
    return {'rolling_budget_days': 7, 'max_train_time_rolling_seconds': 28800,
            'max_train_time_per_calendar_day_seconds': 10800,
            'max_candidate_runs_per_rolling_window': 8,
            'max_train_time_per_candidate_seconds': 2700}


class BudgetExhausted(Exception):
    """A candidate that would exceed the policy's rolling or daily caps."""


def parse_time(text):
    value = dt.datetime.fromisoformat(str(text).replace('Z', '+00:00'))
    return value.astimezone(KST) if value.tzinfo else value.replace(tzinfo=KST)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _write_atomic(path, value, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw = _canonical(value).encode('utf-8')
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix='.tmp-')
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _read_json(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('state file must not be a symlink')
    return json.loads(path.read_bytes())


class BudgetLedger:
    """Append-only spend record. Budget is recomputed, never reset."""

    def __init__(self, path):
        self.path = Path(path)

    def entries(self):
        if not self.path.exists():
            return []
        if self.path.is_symlink():
            raise ValueError('ledger must not be a symlink')
        rows = []
        for line in self.path.read_bytes().splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def append(self, record):
        entry = dict(record)
        entry.setdefault('at', dt.datetime.now(KST).isoformat(timespec='seconds'))
        seconds = entry.get('train_seconds', 0)
        if type(seconds) not in (int, float) or seconds < 0:
            raise ValueError('train_seconds must be a non-negative number')
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.path.open('ab') as stream:
            stream.write((_canonical(entry) + '\n').encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(self.path, 0o600)
        return entry

    def usage(self, *, now, policy):
        now = parse_time(now)
        window = dt.timedelta(days=policy['rolling_budget_days'])
        rolling_start = now - window
        rolling_seconds = today_seconds = 0.0
        candidates = set()
        for entry in self.entries():
            at = parse_time(entry['at'])
            seconds = float(entry.get('train_seconds') or 0)
            if at >= rolling_start:
                rolling_seconds += seconds
                candidates.add(entry.get('candidate'))
            if at.date() == now.date():
                today_seconds += seconds
        return {
            'rolling_seconds': rolling_seconds,
            'today_seconds': today_seconds,
            'candidates': len(candidates),
            'remaining_rolling_seconds': max(0.0, policy['max_train_time_rolling_seconds'] - rolling_seconds),
            'remaining_today_seconds': max(0.0, policy['max_train_time_per_calendar_day_seconds'] - today_seconds),
            'remaining_candidates': max(0, policy['max_candidate_runs_per_rolling_window'] - len(candidates)),
        }

    def reserve(self, *, now, policy, candidate, train_seconds):
        seconds = float(train_seconds)
        if seconds <= 0:
            raise BudgetExhausted('candidate time must be positive')
        if seconds > policy['max_train_time_per_candidate_seconds']:
            raise BudgetExhausted('candidate exceeds per-candidate training cap')
        usage = self.usage(now=now, policy=policy)
        if usage['remaining_candidates'] <= 0:
            raise BudgetExhausted('rolling candidate cap reached')
        if seconds > usage['remaining_rolling_seconds']:
            raise BudgetExhausted('rolling training-time cap reached')
        if seconds > usage['remaining_today_seconds']:
            raise BudgetExhausted('per-day training-time cap reached')
        return self.append({'at': parse_time(now).isoformat(timespec='seconds'),
                            'candidate': candidate, 'train_seconds': seconds,
                            'kind': 'reservation'})


class Campaign:
    """Atomic STATE.json plus an operator PAUSED marker."""

    def __init__(self, root):
        self.root = Path(root)

    @property
    def state_path(self):
        return self.root / 'STATE.json'

    @property
    def pause_path(self):
        return self.root / 'PAUSED'

    def read_state(self):
        return _read_json(self.state_path)

    def write_state(self, value):
        _write_atomic(self.state_path, value)
        return value

    def pause(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.pause_path.write_text(dt.datetime.now(KST).isoformat(timespec='seconds') + '\n')
        os.chmod(self.pause_path, 0o600)
        return {'status': 'PAUSED_BY_OPERATOR'}

    def resume(self):
        self.pause_path.unlink(missing_ok=True)
        return {'status': 'ACTIVE'}

    def start(self, *, campaign_id, policy):
        if self.state_path.exists():
            existing = self.read_state()
            if existing.get('campaign_id') == campaign_id:
                raise CampaignRefused('campaign id already used; never reset a started campaign')
            if existing.get('status') not in ('COMPLETED', 'RESEARCH_PLATEAU', 'CAMPAIGN_BUDGET_EXHAUSTED'):
                raise CampaignRefused('another campaign is still active')
        record = {'campaign_id': campaign_id, 'status': 'DATA_PREPARING',
                  'policy_version': policy['policy_version'],
                  'started_at': dt.datetime.now(KST).isoformat(timespec='seconds'),
                  'operational_ready': False, 'automatic_approval': False,
                  'model_promotion': False, 'hypotheses': []}
        return self.write_state(record)

    def advance(self):
        if self.pause_path.exists():
            return {'status': 'PAUSED_BY_OPERATOR'}
        return self.read_state()

    def fresh_campaign(self, *, policy, independent_families):
        existing = self.read_state()
        if existing.get('status') != 'RESEARCH_PLATEAU':
            raise CampaignRefused('rearm is only allowed from RESEARCH_PLATEAU')
        required = policy['require_min_new_structural_families_after_plateau']
        used = existing.get('families_used')
        if not isinstance(used, int):
            raise CampaignRefused('families_used must be recorded before rearm')
        # Rearm needs genuinely new families on top of the ones already consumed.
        if (not isinstance(independent_families, int)
                or independent_families - used < required):
            raise CampaignRefused('not enough new independent structural families')
        stamp = dt.datetime.now(KST).strftime('%Y%m%d%H%M%S')
        record = {'campaign_id': '%s-rearm-%s' % (policy['campaign_id'], stamp),
                  'status': 'DATA_PREPARING',
                  'policy_version': policy['policy_version'],
                  'started_at': dt.datetime.now(KST).isoformat(timespec='seconds'),
                  'previous_campaign_id': existing.get('campaign_id'),
                  'families_used': existing.get('families_used'),
                  'independent_families_available': independent_families,
                  'operational_ready': False, 'automatic_approval': False,
                  'model_promotion': False, 'hypotheses': []}
        return self.write_state(record)


class ExperimentSelector:
    """One hypothesis at a time, in a documented default order."""

    ORDER = ('E2', 'E3', 'E4', 'E5', 'E6', 'E7')
    MAX_CONSECUTIVE_REJECTIONS = 3

    def next_hypothesis(self, history):
        rejected = {row['hypothesis'] for row in history if row.get('result') == 'rejected'}
        streak = 0
        for row in history:
            if row.get('result') == 'rejected':
                streak += 1
            elif row.get('result') not in (None, 'parked'):
                streak = 0
        if streak >= self.MAX_CONSECUTIVE_REJECTIONS:
            return 'RESEARCH_PLATEAU'
        for hypothesis in self.ORDER:
            if hypothesis not in rejected:
                return hypothesis
        return 'RESEARCH_PLATEAU'


def tiny_gate(rows, predictions, baseline_accuracy, minimum_recall):
    """Train-only separation check; system-forced abstentions are not model answers."""
    indexed = {}
    for prediction in predictions:
        if prediction.get('prediction_origin') != 'raw_laya':
            raise ValueError('only raw model answers may be scored')
        indexed[prediction['case_id']] = prediction['prediction']
    totals = {}
    hits = {}
    for row in rows:
        label = row['expected']
        totals[label] = totals.get(label, 0) + 1
        if indexed.get(row['case_id']) == label:
            hits[label] = hits.get(label, 0) + 1
    recall = {label: hits.get(label, 0) / count for label, count in totals.items()}
    accuracy = sum(hits.values()) / len(rows)
    passed = (accuracy >= max(0.80, baseline_accuracy + 0.20)
              and all(value >= minimum_recall for value in recall.values()))
    return {'passed': passed, 'accuracy': accuracy, 'recall': recall,
            'changed_recall': recall.get('changed', 0.0),
            'preserved_recall': recall.get('preserved', 0.0), 'items': len(rows)}
