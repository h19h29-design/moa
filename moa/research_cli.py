"""`python -m moa.research_cli ...` — unattended research campaign control.

Commands never train on their own: `status` and `report` are read-only,
`start`/`advance`/`pause`/`resume` move campaign bookkeeping, and only
`advance` can publish one bounded job to the MacBook.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import research_campaign as rc
from . import research_dataset as ds
from . import research_runner as rr
from . import research_advance as ra


DEFAULT_ROOT = Path('/Volumes/KIOXIA-2TB/Cache/MOA/unattended-20261020')


def _emit(out, value):
    out.write(json.dumps(value, ensure_ascii=False, indent=1) + '\n')


def _error(out, message):
    out.write(json.dumps({'status': 'REFUSED', 'reason': message}, ensure_ascii=False) + '\n')
    raise SystemExit(1)


def load_policy(path):
    return json.loads(Path(path).read_bytes())


def summarize_root(root):
    state_path = root / 'STATE.json'
    if not state_path.exists():
        return {'status': 'NOT_STARTED', 'training_started': False,
                'operational_ready': False, 'human_approvals': 0,
                'real_document_accuracy': None}
    state = json.loads(state_path.read_bytes())
    ledger = rc.BudgetLedger(root / 'BUDGET.jsonl')
    state['budget'] = ledger.usage(now=rc.parse_time(_now()),
                                   policy=state.get('policy_snapshot', _fallback_policy()))
    state['paused'] = (root / 'PAUSED').exists()
    if state.get('alignment_enabled'):
        aligned = root/'alignment/STATE.json'
        if aligned.exists():
            state['previous_campaign_status'] = state['status']
            state['alignment'] = json.loads(aligned.read_bytes())
            state['status'] = state['alignment']['status']
    state.setdefault('operational_ready', False)
    state.setdefault('human_approvals', 0)
    return state


def _now():
    import datetime as dt
    return dt.datetime.now(rc.KST).isoformat(timespec='seconds')


def _fallback_policy():
    return {'rolling_budget_days': 7, 'max_train_time_rolling_seconds': 28800,
            'max_train_time_per_calendar_day_seconds': 10800,
            'max_candidate_runs_per_rolling_window': 8}


def report_text(root):
    state = summarize_root(root)
    lines = ['# MOA 무인 연구 캠페인 보고', '',
             '상태: %s' % state['status'],
             '연구 범위: 합성 원문·독립 정답만 (실제 통신문 학습 없음)',
             '사람 승인: %s건 / 실제 통신문 정답: %s' % (
                 state.get('human_approvals', 0),
                 '미완료' if state.get('real_document_accuracy') is None else
                 state['real_document_accuracy']),
             '운영 모델 교체: 없음', '']
    if state.get('budget'):
        lines += ['## 예산',
                  '- 7일 누적 사용: %.0f초' % state['budget']['rolling_seconds'],
                  '- 오늘 사용: %.0f초' % state['budget']['today_seconds'],
                  '- 남은 후보: %d개' % state['budget']['remaining_candidates'], '']
    paired_report = root/'alignment/COMPARISON.json'
    if state.get('alignment') and paired_report.exists():
        comparison = json.loads(paired_report.read_bytes())
        lines += ['## 대응 구간 합성 개발 시험 (블라인드·실자료 성적 아님)', '']
        for model, value in comparison.get('models', {}).items():
            if model.endswith('_paired'):
                lines.append('- %s: %s/%s, 정상 오탐 %s, 오류 미검출 %s, 입력 초과 %s' % (
                    model, value['correct'], value['items'], value['normal_false_alerts'],
                    value['actual_errors_missed'], value['overflow']))
        lines.append('')
    lines += ['## 실제 통신문 품질',
              '미완료 — 사람이 확인한 독립 정답과 이용 허가가 있는 자료가 0건이라',
              '실제 통신문 98% 달성은 이 캠페인으로 주장하지 않습니다.']
    return '\n'.join(lines) + '\n'


def main(argv=None, out=None):
    parser = argparse.ArgumentParser(prog='moa.research_cli', description=__doc__)
    parser.add_argument('command', choices=['status', 'start', 'advance', 'pause',
                                            'resume', 'report', 'prepare', 'datasets'])
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--policy', type=Path)
    parser.add_argument('--campaign')
    parser.add_argument('--families', type=int, default=ds.FAMILY_COUNT())
    args = parser.parse_args(argv)
    out = out or argparse.Namespace(write=sys.stdout.write)
    campaign = rc.Campaign(args.root)
    try:
        if args.command == 'status':
            _emit(out, summarize_root(args.root))
        elif args.command == 'report':
            out.write(report_text(args.root))
        elif args.command == 'prepare':
            rows = ds.generate(args.campaign or 'unattended-author-seed', families=args.families)
            parts = ds.split_dataset(rows)
            _emit(out, {'status': 'DATA_PREPARING', 'summary': ds.summarize(rows),
                        'splits': {part['name']: len(part['rows']) for part in parts}})
        elif args.command == 'datasets':
            _emit(out, {'status': 'READ_ONLY', 'families_available': ds.FAMILY_COUNT(),
                        'operations': len(ds.NORMALIZATIONS) + len(ds.CHANGES) + len(ds.MISSING)})
        elif args.command == 'pause':
            _emit(out, campaign.pause())
        elif args.command == 'resume':
            _emit(out, campaign.resume())
        elif args.command == 'start':
            if not args.policy:
                _error(out, '--policy is required')
            policy = load_policy(args.policy)
            record = campaign.start(campaign_id=args.campaign or policy['campaign_id'], policy=policy)
            record['policy_snapshot'] = policy
            campaign.write_state(record)
            _emit(out, {k: record[k] for k in ('campaign_id', 'status', 'started_at')})
        elif args.command == 'advance':
            if not (args.root / 'STATE.json').exists():
                _emit(out, {'status': 'NOT_STARTED'})
                return 0
            snapshot = campaign.advance()
            if snapshot.get('status') == 'PAUSED_BY_OPERATOR':
                _emit(out, snapshot)
                return 0
            _emit(out, ra.advance(args.root))
    except (rc.CampaignRefused, rc.BudgetExhausted, ValueError) as exc:
        _error(out, str(exc))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
