"""Case accumulation, analysis queue, review queue, and family grouping.

Candidates are never auto-promoted: only human-approved rows feed suggestions,
and eval-split rows never feed search or rules.
"""
from __future__ import annotations

import json
import os
import re
import resource
import subprocess
import sys
from pathlib import Path

from .core import Store, digest, encode, kst_now, write_json, write_jsonl
from .extract import LAYOUTS, PARSER_VERSION, html_document, table_pattern

PII = re.compile(r'\b\d{6}\s*[-]\s*[1-8]\d{6}\b|\b01[016789][- .]?\d{3,4}[- .]?\d{4}\b|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}')
# Roles that make a table risky to lose: dates, money, audience, units, exceptions.
RISK = re.compile(r'20\d{2}|\d{1,2}월|\d{1,2}일|[\d,]+\s*원|\d+\s*명|무료|유료|제외|마감|기한|동의|서명')
UNIT = re.compile(r'원|명|시간|분|cm|kg|%|학년|학급|교실')
NORMALISE = re.compile(r'[0-9]+|[일이삼사오육칠팔구십]?학년|테스트|\s+')


def _limits():
    # Linux-only worker is memory/CPU bounded and never executes document macros.
    resource.setrlimit(resource.RLIMIT_AS, (700*1024*1024, 700*1024*1024))
    resource.setrlimit(resource.RLIMIT_CPU, (40, 40))
    resource.setrlimit(resource.RLIMIT_FSIZE, (64*1024*1024, 64*1024*1024))


def extract_asset(store: Store, asset: dict) -> dict:
    sha = asset['sha256']
    path = store.root/'extracted'/f'{sha}-{PARSER_VERSION}.json'
    if path.exists():
        cached = json.loads(path.read_text())
        # A cached failure is retried only when the parser version changed since.
        if cached.get('status') != 'parse_error' and cached.get('parser_version') == PARSER_VERSION:
            return cached
    kind = asset.get('kind', 'unsupported')
    if kind in ('image', 'hwp', 'legacy_ole', 'docx', 'unsupported'):
        result = {'status': 'needs_vision' if kind == 'image' else 'needs_parser',
                  'text': '', 'tables': [], 'kind': kind}
    else:
        try:
            p = subprocess.run([sys.executable, '-m', 'moa.extract', str(store.object_path(sha)), kind],
                capture_output=True, timeout=55, preexec_fn=_limits,
                env={k: v for k, v in os.environ.items() if k in ('PATH', 'PYTHONPATH', 'LANG', 'LC_ALL', 'HOME', 'TZ')})
            if p.returncode or len(p.stdout) > 30*1024*1024:
                raise ValueError('문서 분석 작업 실패')
            result = json.loads(p.stdout)
        except (subprocess.TimeoutExpired, ValueError, OSError) as e:
            result = {'status': 'parse_error', 'error': type(e).__name__, 'text': '', 'tables': []}
    result['parser_version'] = PARSER_VERSION
    write_json(path, result)
    return result


def template_family(table: dict) -> str:
    """Semantic family: header meaning, cell roles, merge shape, units — not just rows/cols."""
    cells = table['cells']
    header_text = ' '.join(c['text'] for c in cells if c['row'] == 0 or c['col'] == 0 or c.get('header'))
    normalised = sorted({t for t in (NORMALISE.sub(' ', header_text)).split() if len(t) > 1})[:20]
    roles = sorted(set(re.findall(r'학년|학급|시간|시각|일시|장소|대상|비용|준비물|기한|동의|서명|성명|보호자|인원|금액', header_text)))
    units = sorted(set(UNIT.findall(' '.join(c['text'] for c in cells[:200]))))
    merges = sum(1 for c in cells if c['rowspan'] > 1 or c['colspan'] > 1)
    signature = {'headers': normalised, 'roles': roles, 'units': units,
                 'shape': [min(table['rows'], 50), min(table['cols'], 20), min(merges, 50)],
                 'nested': bool(table.get('nested'))}
    return digest(encode(signature).encode())[:24]


def split_for(group: str) -> str:
    """Group-level split so revisions of one form never straddle train/eval."""
    bucket = int(digest(group.encode())[:8], 16) % 10
    return 'eval' if bucket == 9 else 'dev' if bucket == 8 else 'train'


def learn(store: Store, ident: str) -> dict:
    doc = store.notice(ident)
    results = [('body', html_document(doc['body_html']))] if doc['body_html'] else []
    results.extend((a['sha256'], extract_asset(store, a)) for a in doc['assets'])
    total = 0
    pending = []
    for source, result in results:
        if result['status'] != 'extracted':
            pending.append({'source': source, 'status': result['status']})
        for index, table in enumerate(result.get('tables', [])):
            if not table['cells']:
                continue
            pred = table_pattern(table)
            family = template_family(table)
            case_id = digest((ident+':'+source+':'+str(index)).encode())
            old = store.db.execute(
                "SELECT layout FROM cases WHERE pattern=? AND approved=1 AND split!='eval'",
                (pred['pattern'],)).fetchall()
            reviewed = {r[0] for r in old}
            if len(reviewed) == 1:
                pred['layout'] = next(iter(reviewed))
                pred['method'] = 'approved-pattern-suggestion'  # suggestion != approval
            raw = encode(table)
            privacy_flag = bool(PII.search(raw))
            safe_table = json.loads(PII.sub('[개인정보 검토 필요]', raw))
            case = {'id': case_id, 'notice_id': ident, 'source': source, 'table_index': index,
                    'source_url': doc['url'], 'school': doc['school'], 'table': safe_table,
                    'suggestion': pred, 'family_id': family,
                    'privacy_flag': privacy_flag, 'rights': 'unreviewed',
                    'status': 'candidate', 'created_at': kst_now().isoformat(),
                    'split_group': family}
            store.db.execute(
                'INSERT OR IGNORE INTO cases(id,notice_id,pattern,layout,payload,family_id,split)'
                ' VALUES(?,?,?,?,?,?,?)',
                (case_id, ident, pred['pattern'], pred['layout'], encode(case),
                 family, split_for(family)))
            # Pre-v2 cases keep their row (and approval); only fill the new columns.
            store.db.execute('UPDATE cases SET family_id=?, split=? WHERE id=? AND family_id IS NULL',
                             (family, split_for(family), case_id))
            total += 1
    status = 'partial' if pending else 'extracted'
    has_table = store.db.execute('SELECT 1 FROM cases WHERE notice_id=? LIMIT 1', (ident,)).fetchone()
    if pending:
        table_state = 'unknown'
    elif has_table or total:
        table_state = 'table_present'
    else:
        table_state = 'no_table'
    store.db.execute('UPDATE notices SET analysis_status=?, table_state=? WHERE id=?',
                     (status, table_state, ident))
    store.db.commit()
    result = {'notice_id': ident, 'tables': total, 'status': status,
              'table_state': table_state, 'pending': pending}
    write_json(store.root/'extracted'/f'{ident}-notice.json', result)
    return result


def drain_analyse(store: Store, limit: int = 50, owner: str = 'worker') -> dict:
    """Consume the persistent analysis queue; surviving jobs keep their attempts."""
    done, failed = 0, 0
    for job in store.claim_jobs('analyse', limit, owner):
        try:
            learn(store, job['ref_id'])
            store.finish_job(job['id'])
            done += 1
        except Exception as exc:
            store.fail_job(job['id'], type(exc).__name__ + ': ' + str(exc)[:200])
            failed += 1
    return {'done': done, 'failed': failed,
            'pending': store.db.execute(
                "SELECT count(*) FROM jobs WHERE kind='analyse' AND status='pending'").fetchone()[0]}


def build_review_queue(store: Store, day: str, limit: int = 20) -> list[dict]:
    """Priority review candidates: new families first, then scarce/risky/underrepresented."""
    rows = store.db.execute(
        "SELECT c.*, n.office, n.school AS scode, n.table_state FROM cases c"
        " JOIN notices n ON n.id=c.notice_id"
        " WHERE c.review_status='candidate' AND c.split!='eval'").fetchall()
    family_approved = {}
    for r in store.db.execute(
            "SELECT family_id, sum(approved) AS a, count(*) AS n FROM cases GROUP BY family_id"):
        family_approved[r['family_id']] = r['a'] or 0
    office_counts = {r[0]: r[1] for r in store.db.execute(
        'SELECT n.office, count(*) FROM cases c JOIN notices n ON n.id=c.notice_id'
        ' GROUP BY n.office')}
    picked = []
    for row in rows:
        case = json.loads(row['payload'])
        table = case.get('table', {})
        raw = encode(table)
        reasons, score = [], 0
        fam = row['family_id'] or ''
        if family_approved.get(fam, 0) == 0:
            score += 100
            reasons.append('신규 양식')
        elif family_approved.get(fam, 0) < 3:
            score += 50
            reasons.append('승인 사례 부족')
        if RISK.search(raw):
            score += 40
            reasons.append('날짜·금액·대상 확인 필요')
        if any(c.get('rowspan', 1) > 1 or c.get('colspan', 1) > 1 for c in table.get('cells', [])):
            score += 30
            reasons.append('병합 셀')
        if office_counts.get(row['office'], 0) < 5:
            score += 20
            reasons.append('부족 지역')
        if case.get('privacy_flag'):
            score += 10
            reasons.append('개인정보 검토')
        picked.append({'case_id': row['id'], 'score': score, 'reasons': reasons,
                       'notice_id': row['notice_id'], 'office': row['office'],
                       'school': case.get('school', {}).get('school_name'),
                       'layout': row['layout'], 'family_id': fam})
    picked.sort(key=lambda c: (-c['score'], c['case_id']))
    chosen = picked[:limit]
    for c in chosen:
        store.db.execute('INSERT OR REPLACE INTO review_queue(day,case_id,score,reasons)'
                         ' VALUES(?,?,?,?)', (day, c['case_id'], c['score'], encode(c['reasons'])))
        store.db.execute('UPDATE cases SET queue_reason=? WHERE id=?',
                         (encode(c['reasons']), c['case_id']))
    store.db.commit()
    return chosen


def review(store: Store, case_id: str, status: str, layout: str | None = None,
           reviewer: str = '', note: str = '', correction: dict | None = None,
           rights_reviewed: bool = False, privacy_reviewed: bool = False) -> dict:
    """Human review: approved / rejected / held / candidate(승인 취소). History is kept."""
    if status not in ('approved', 'rejected', 'held', 'candidate'):
        raise ValueError('status는 approved|rejected|held|candidate 중 하나입니다.')
    row = store.db.execute('SELECT * FROM cases WHERE id=?', (case_id,)).fetchone()
    if not row:
        raise ValueError('사례 ID를 찾지 못했습니다.')
    if status == 'approved':
        if layout not in LAYOUTS or not reviewer.strip():
            raise ValueError('검수자 이름과 유효한 레이아웃이 필요합니다.')
        if not rights_reviewed or not privacy_reviewed:
            raise ValueError('원문/표 정확성, 이용 권한 및 개인정보 검수 후에만 승인할 수 있습니다.')
    history = json.loads(row['review_history'] or '[]')
    history.append({'at': kst_now().isoformat(), 'reviewer': reviewer.strip(),
                    'from': row['review_status'], 'to': status, 'note': note[:500]})
    store.db.execute(
        'UPDATE cases SET review_status=?, approved=?, layout=COALESCE(?,layout),'
        ' reviewer=?, reviewed_at=?, correction=COALESCE(?,correction), review_history=?'
        ' WHERE id=?',
        (status, 1 if status == 'approved' else 0, layout, reviewer.strip() or None,
         kst_now().isoformat(), encode(correction) if correction else None,
         encode(history), case_id))
    store.db.commit()
    return {'case_id': case_id, 'status': status}


def approve(store: Store, case_id: str, layout: str, reviewer: str,
            rights_reviewed: bool, privacy_reviewed: bool):
    review(store, case_id, 'approved', layout=layout, reviewer=reviewer,
           rights_reviewed=rights_reviewed, privacy_reviewed=privacy_reviewed)


def export_learning(store: Store):
    # Streaming atomic replacement; repeated exports never append duplicate rows.
    # Eval-split rows are exported separately and never mixed into search/rules data.
    def records(approved, splits):
        marks = ','.join("'%s'" % s for s in splits)
        for row in store.db.execute(
                'SELECT * FROM cases WHERE approved=? AND split IN (%s) ORDER BY id' % marks,
                (approved,)):
            d = json.loads(row['payload'])
            if approved:
                d.update(status='human_reviewed', rights='operator_reviewed',
                         layout=row['layout'], reviewer=row['reviewer'],
                         reviewed_at=row['reviewed_at'], split=row['split'])
                if row['correction']:
                    d['correction'] = json.loads(row['correction'])
            yield d
    write_jsonl(store.root/'learning'/'candidates.jsonl',
                records(0, ('train', 'dev')))
    write_jsonl(store.root/'learning'/'approved.jsonl',
                records(1, ('train', 'dev')))
    write_jsonl(store.root/'learning'/'eval.jsonl',
                (json.loads(r['payload']) | {'split': 'eval'}
                 for r in store.db.execute(
                     "SELECT * FROM cases WHERE split='eval' ORDER BY id")))
    rows = store.db.execute(
        'SELECT family_id, count(*) AS examples, sum(approved) AS approved'
        ' FROM cases GROUP BY family_id ORDER BY examples DESC')
    write_jsonl(store.root/'learning'/'families.jsonl', (dict(row) for row in rows))
    rows = store.db.execute(
        'SELECT pattern,count(*) AS examples,sum(approved) AS approved'
        ' FROM cases GROUP BY pattern ORDER BY examples DESC')
    write_jsonl(store.root/'learning'/'patterns.jsonl', (dict(row) for row in rows))


def search_cases(store: Store, query: str, include_candidates: bool = False) -> list[dict]:
    if len(query) > 200:
        raise ValueError('검색어 길이 제한')
    query = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    rows = store.db.execute(
        "SELECT id,pattern,layout,approved,payload FROM cases"
        " WHERE payload LIKE ? ESCAPE '\\' AND split!='eval'"
        ' AND (approved=1 OR ?=1) LIMIT 20',
        ('%'+query+'%', int(include_candidates)))
    return [dict(r) for r in rows]
