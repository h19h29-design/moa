"""Case accumulation, not unattended model training or self-approved gold labels."""
from __future__ import annotations

import json
import os
import re
import resource
import subprocess
import sys
from pathlib import Path

from .core import Store, atomic_write, digest, encode, kst_now, write_json, write_jsonl
from .extract import LAYOUTS, html_document, table_pattern

PII = re.compile(r'\b\d{6}\s*[-]\s*[1-8]\d{6}\b|\b01[016789][- .]?\d{3,4}[- .]?\d{4}\b|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}')


def _limits():
    # Linux-only worker is memory/CPU bounded and never executes document macros.
    resource.setrlimit(resource.RLIMIT_AS,(700*1024*1024,700*1024*1024))
    resource.setrlimit(resource.RLIMIT_CPU,(40,40))
    resource.setrlimit(resource.RLIMIT_FSIZE,(64*1024*1024,64*1024*1024))


def extract_asset(store: Store, asset: dict) -> dict:
    sha=asset['sha256']
    path=store.root/'extracted'/f'{sha}-v1.json'
    if path.exists():
        cached=json.loads(path.read_text())
        if cached.get('status')!='parse_error': return cached
    kind=asset.get('kind','unsupported')
    if kind in ('image','hwp','legacy_ole','docx','unsupported'):
        result={'status':'needs_vision' if kind=='image' else 'needs_parser','text':'','tables':[]}
    else:
        try:
            p=subprocess.run([sys.executable,'-m','moa.extract',str(store.object_path(sha)),kind],
                capture_output=True,timeout=55,preexec_fn=_limits,
                env={k:v for k,v in os.environ.items() if k in ('PATH','PYTHONPATH','LANG','LC_ALL','HOME','TZ')})
            if p.returncode or len(p.stdout)>30*1024*1024:
                raise ValueError('문서 분석 작업 실패')
            result=json.loads(p.stdout)
        except (subprocess.TimeoutExpired,ValueError,OSError) as e:
            result={'status':'parse_error','error':type(e).__name__,'text':'','tables':[]}
    result['parser_version']='moa-local-v1'
    write_json(path,result)
    return result


def learn(store: Store, ident: str) -> dict:
    doc=store.notice(ident)
    results=[('body',html_document(doc['body_html']))] if doc['body_html'] else []
    results.extend((a['sha256'],extract_asset(store,a)) for a in doc['assets'])
    total=0
    pending=[]
    for source,result in results:
        if result['status']!='extracted': pending.append({'source':source,'status':result['status']})
        for index,table in enumerate(result.get('tables',[])):
            if not table['cells']: continue
            pred=table_pattern(table)
            case_id=digest((ident+':'+source+':'+str(index)).encode())
            old=store.db.execute('SELECT layout FROM cases WHERE pattern=? AND approved=1',
                                 (pred['pattern'],)).fetchall()
            reviewed={r[0] for r in old}
            if len(reviewed)==1:
                pred['layout']=next(iter(reviewed))
                pred['method']='approved-pattern-suggestion'  # suggestion != approval
            raw=encode(table)
            privacy_flag=bool(PII.search(raw))
            safe_table=json.loads(PII.sub('[개인정보 검토 필요]',raw))
            case={'id':case_id,'notice_id':ident,'source':source,'table_index':index,
                  'source_url':doc['url'],'school':doc['school'], 'table':safe_table,
                  'suggestion':pred,'privacy_flag':privacy_flag,'rights':'unreviewed',
                  'status':'candidate','created_at':kst_now().isoformat(),
                  'split_group':pred['pattern']}
            store.db.execute('''INSERT OR IGNORE INTO cases(id,notice_id,pattern,layout,payload)
                                 VALUES(?,?,?,?,?)''',
                             (case_id,ident,pred['pattern'],pred['layout'],encode(case)))
            total+=1
    status='partial' if pending else 'extracted'
    store.db.execute('UPDATE notices SET analysis_status=? WHERE id=?',(status,ident))
    store.db.commit()
    result={'notice_id':ident,'tables':total,'status':status,'pending':pending}
    write_json(store.root/'extracted'/f'{ident}-notice.json',result)
    return result


def approve(store: Store, case_id: str, layout: str, reviewer: str,
            rights_reviewed: bool, privacy_reviewed: bool):
    if layout not in LAYOUTS or not reviewer.strip():
        raise ValueError('검수자 이름과 유효한 레이아웃이 필요합니다.')
    if not rights_reviewed or not privacy_reviewed:
        raise ValueError('원문/표 정확성, 이용 권한 및 개인정보 검수 후에만 승인할 수 있습니다.')
    if not store.db.execute('SELECT 1 FROM cases WHERE id=?',(case_id,)).fetchone():
        raise ValueError('사례 ID를 찾지 못했습니다.')
    store.db.execute('UPDATE cases SET approved=1,layout=?,reviewer=?,reviewed_at=? WHERE id=?',
                     (layout,reviewer.strip(),kst_now().isoformat(),case_id))
    store.db.commit()


def export_learning(store: Store):
    # Streaming atomic replacement; repeated exports never append duplicate rows.
    def records(approved):
        for row in store.db.execute('SELECT * FROM cases WHERE approved=? ORDER BY id',(approved,)):
            d=json.loads(row['payload'])
            if approved:
                d.update(status='human_reviewed',rights='operator_reviewed',layout=row['layout'],
                         reviewer=row['reviewer'],reviewed_at=row['reviewed_at'])
            yield d
    for filename,approved in [('candidates.jsonl',0),('approved.jsonl',1)]:
        write_jsonl(store.root/'learning'/filename,records(approved))
    rows=store.db.execute("SELECT pattern,count(*) AS examples,sum(approved) AS approved FROM cases GROUP BY pattern ORDER BY examples DESC")
    write_jsonl(store.root/'learning/patterns.jsonl',(dict(row) for row in rows))


def search_cases(store: Store, query: str, include_candidates: bool = False) -> list[dict]:
    if len(query)>200: raise ValueError('검색어 길이 제한')
    query=query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
    rows=store.db.execute("SELECT id,pattern,layout,approved,payload FROM cases WHERE payload LIKE ? ESCAPE '\\' AND (approved=1 OR ?=1) LIMIT 20",
                          ('%'+query+'%',int(include_candidates)))
    return [dict(r) for r in rows]
