"""Daily collection, backfill scheduling, persisted quotas, and a NAS-local scheduler."""
from __future__ import annotations

import argparse
import html
import json
import os
import random
import re
import shutil
import signal
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .core import Budget, Store, atomic_write, digest, encode, kst_now, quota, run_lock, write_json
from .crawl import detail, discover, open_pages, sampled_schools, sync_schools
from .extract import LAYOUTS, detect_kind
from .learn import (approve, build_review_queue, drain_analyse, export_learning, learn,
                    review, search_cases)
from .net import Fetcher
from . import backfill


def integer(name: str, default: int, low: int = 1, high: int = 10000) -> int:
    n=int(os.environ.get(name,default))
    if not low<=n<=high: raise ValueError(f'{name}은 {low}~{high} 범위여야 합니다.')
    return n


def heartbeat(root: Path, phase: str):
    write_json(root/'heartbeat.json',{'at':kst_now().isoformat(),'phase':phase})


def registry(store: Store, fetcher) -> tuple[list[dict], str | None]:
    path=store.root/'registry/schools.json'
    cached=json.loads(path.read_text()) if path.exists() else None
    stale=not cached or (kst_now()-datetime.fromisoformat(cached['synced_at'])).days>=7
    if stale:
        try:
            return sync_schools(fetcher,store.root,os.environ.get('NEIS_API_KEY','')),None
        except Exception as exc:
            if not cached: raise
            return cached['schools'], '기존 학교목록 사용: '+str(exc)
    return cached['schools'],None


def _block_kind(reason: str) -> tuple[str, float]:
    """Failure class → (state, days until recheck). Permanent blocks are retried rarely."""
    if 'robots' in reason:
        return 'robots', 7
    if '로그인' in reason or '인증' in reason or '권한' in reason:
        return 'login', 30
    if 'Name or service' in reason or 'getaddrinfo' in reason or 'address associated' in reason:
        return 'dns', 1
    if 'CERTIFICATE' in reason or 'SSL' in reason:
        return 'tls', 1
    if '게시판 링크' in reason:
        return 'no_board', 7
    if '미지원' in reason or '빈 게시판' in reason:
        return 'unsupported', 3
    return 'error', 1


def collect_post(store: Store, fetcher, school: dict, post: dict, hosts: set,
                 override: dict, day: str, campaign_id: int | None = None,
                 extra_hosts: set | None = None) -> tuple[str, bool, int, int]:
    """Fetch one post (body + attachments) and persist it. Returns (id, new, robots, failed)."""
    extra_hosts = extra_hosts if extra_hosts is not None else set()
    pages, _ = open_pages(fetcher, post['url'], hosts)
    page = pages[-1]
    notice = detail(page.text, page.url, post['title'], override.get('body_selector', ''))
    downloads = []
    skipped = []
    failed = []
    total_bytes = 0
    for attachment in notice['attachments']:
        heartbeat(store.root, 'attachment:'+school['office_code'])
        # Office-wide notices often attach files on the office portal
        # (e.g. www.<office>.go.kr). Follow the school's own published
        # attachment hosts, bounded and still public-IP/robots/size checked.
        host = urlsplit(attachment['url']).hostname
        if host and host not in hosts and host not in extra_hosts and len(extra_hosts) < 3:
            extra_hosts.add(host)
            hosts.add(host)
        try:
            r = fetcher.get(attachment['url'], allowed_hosts=hosts, limit=20*1024*1024)
        except PermissionError:
            # The site's own robots.txt forbids this download path. Never bypass it;
            # keep the notice with provenance that the file was not fetched.
            skipped.append({'url': attachment['url'], 'filename': attachment['filename'],
                            'reason': 'robots.txt'})
            continue
        except Exception as exc:
            # File access refused / viewer-only / network error: the notice body is
            # still kept, with the failed attachment recorded instead of hidden.
            failed.append({'url': attachment['url'], 'filename': attachment['filename'],
                           'reason': type(exc).__name__+': '+str(exc)[:120]})
            continue
        kind = detect_kind(r.data, attachment['filename'])
        if kind in ('unsupported', 'html'):
            failed.append({'url': attachment['url'], 'filename': attachment['filename'],
                           'reason': '첨부 대신 HTML/미지원 응답'})
            continue
        total_bytes += len(r.data)
        if total_bytes > 50*1024*1024:
            raise ValueError('통신문 첨부 합계 50MB 초과')
        downloads.append((attachment, r, kind))
    body_text = len(BeautifulSoup(notice['body_html'], 'html.parser').get_text(' ', strip=True))
    if not downloads and body_text < 10:
        raise ValueError('본문 없음 + 첨부 확보 실패: 수집으로 세지 않음')
    assets = []
    for attachment, r, kind in downloads:
        a = store.object(r.data, attachment['filename'])
        a.update(kind=kind, source_url=attachment['url'], final_url=r.url)
        assets.append(a)
    ident, new = store.save_notice(school, page.url, notice['title'], notice['body_html'],
                                   assets, day, page_bytes=page.data, skipped_assets=skipped,
                                   failed_assets=failed,
                                   published_date=post.get('published_date'),
                                   campaign_id=campaign_id)
    return ident, new, len(skipped), len(failed)


def collect(store: Store, fetcher, schools: list[dict], day: str,
            max_schools: int = 50, max_posts: int = 12,
            seoul_target: int = 50, gyeonggi_target: int = 50, other_target: int = 20,
            only_office: str | None = None) -> dict:
    groups=defaultdict(list)
    for school in schools:
        groups[school['office_code']].append(school)
    excluded={c.strip() for c in os.environ.get('EXCLUDE_OFFICES','V10').split(',') if c.strip()}
    error_cap=integer('MAX_ERRORS_PER_REGION',500,20,5000)
    overrides_path=store.root/'registry/overrides.json'
    overrides=json.loads(overrides_path.read_text()) if overrides_path.exists() else {}
    report={'schema_version':1,'day':day,'started_at':kst_now().isoformat(),
            'count_unit':'unique_notice_not_school_or_attachment','regions':{},'status':'complete',
            'sampling':'date-seeded, school-level-stratified, public/reachable boards only',
            'excluded_offices':sorted(excluded),'daily_target_total':None}
    offices=sorted(groups)
    random.Random(day).shuffle(offices)
    report['daily_target_total']=sum(quota(o,groups[o][0]['office_name'],
                                         seoul_target,gyeonggi_target,other_target)
                                     for o in offices if o not in excluded or o==only_office)
    for office in offices:
        if only_office and office!=only_office: continue
        if office in excluded: continue
        pool=groups[office]
        goal=quota(office,pool[0]['office_name'],seoul_target,gyeonggi_target,other_target)
        before=store.count_day(day,office)
        stats={'office_name':pool[0]['office_name'],'target':goal,'before':before,'new':0,
               'duplicates':0,'schools_tried':0,'schools_skipped_cached':0,
               'errors':[],'error_count':0,
               'attachments_skipped_robots':0,'attachments_failed':0}
        report['regions'][office]=stats
        school_cap=integer('MAX_NOTICES_PER_SCHOOL',1,1,10)
        extra_hosts=set()
        for school in sampled_schools(pool,day,office)[:max_schools]:
            if store.count_day(day,office)>=goal: break
            school_before=store.db.execute(
                'SELECT count(*) FROM notices WHERE day=? AND office=? AND school=?'
                ' AND campaign_id IS NULL',
                (day,office,school['school_code'])).fetchone()[0]
            if school_before>=school_cap: continue
            key=office+':'+school['school_code']
            override=overrides.get(key,{})
            if override.get('disabled'):
                continue
            blocked=store.school_blocked(key)
            if blocked:
                stats['schools_skipped_cached']+=1
                continue
            stats['schools_tried']+=1
            heartbeat(store.root,'collect:'+office)
            try:
                cached=store.db.execute('SELECT url FROM boards WHERE school=?',(key,)).fetchone()
                try:
                    posts,hosts=discover(fetcher,school,override,cached[0] if cached else None)
                except (ValueError,RuntimeError,PermissionError):
                    if not cached: raise
                    store.db.execute('DELETE FROM boards WHERE school=?',(key,))
                    store.db.commit()
                    posts,hosts=discover(fetcher,school,override)
                seed=digest((day+':'+key).encode())
                random.Random(seed).shuffle(posts)
                school_new=school_before
                for post in posts[:max_posts]:
                    heartbeat(store.root,'post:'+office)
                    if store.count_day(day,office)>=goal or school_new>=school_cap: break
                    try:
                        published=post.get('published_date')
                        if published:
                            d=date.fromisoformat(published)
                            if d>date.fromisoformat(day) or d<date.fromisoformat(day)-timedelta(days=integer('LOOKBACK_DAYS',365,1,3650)):
                                continue
                        if shutil.disk_usage(store.root).free<integer('MIN_FREE_MB',1024,100,100000)*1024*1024:
                            raise RuntimeError('저장공간 하한 도달: 기존 자료를 삭제하지 않고 중단합니다.')
                        ident,new,nskipped,nfailed=collect_post(
                            store,fetcher,school,post,hosts,override,day,extra_hosts=extra_hosts)
                        stats['attachments_skipped_robots']+=nskipped
                        stats['attachments_failed']+=nfailed
                        if new:
                            stats['new']+=1
                            school_new+=1
                        else:
                            stats['duplicates']+=1
                        store.db.execute('INSERT OR REPLACE INTO boards VALUES(?,?,?)',
                                         (key,post['board_url'],kst_now().isoformat()))
                        store.db.commit()
                    except Exception as exc:
                        stats['error_count']+=1
                        if len(stats['errors'])<error_cap:
                            stats['errors'].append({'school':school['school_name'],'url':post['url'],
                                                   'reason':str(exc)[:300]})
                store.record_school(key,'ok',boards=[p['board_url'] for p in posts if p.get('board_url')][:4])
            except Exception as exc:
                stats['error_count']+=1
                if len(stats['errors'])<error_cap:
                    stats['errors'].append({'school':school['school_name'],'home_url':school['home_url'],
                                           'reason':str(exc)[:300]})
                kind,retry=_block_kind(str(exc))
                store.record_school(key,kind,str(exc)[:200],retry_days=retry)
        stats['total_today']=store.count_day(day,office)
        stats['shortfall']=max(0,goal-stats['total_today'])
        if extra_hosts:
            stats['attachment_hosts_used']=sorted(extra_hosts)
        if stats['shortfall']: report['status']='partial'
        print(encode({'event':'region_done','office':office,**stats}),flush=True)
    if not report['regions']:
        raise ValueError('수집할 교육청/학교가 없습니다.')
    # Analysis is queue-based; draining here keeps the daily report accurate while
    # surviving jobs remain resumable if the process dies mid-drain.
    drain_analyse(store,integer('ANALYSE_BATCH',200,1,2000),owner='collect')
    report['finished_at']=kst_now().isoformat()
    report['http_requests']=fetcher.requests
    report['unique_documents_total']=store.db.execute('SELECT count(*) FROM notices').fetchone()[0]
    report['analysis_pending_total']=store.db.execute("SELECT count(*) FROM notices WHERE analysis_status!='extracted'").fetchone()[0]
    report['case_count']=store.db.execute('SELECT count(*) FROM cases').fetchone()[0]
    report['approved_count']=store.db.execute('SELECT count(*) FROM cases WHERE approved=1').fetchone()[0]
    export_learning(store)
    atomic_write(store.root/'reports/latest.html',
        ('<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
         '<title>MOA 수집 보고서</title><style>body{font-family:system-ui;max-width:960px;margin:32px auto;padding:16px}'
         'pre{white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>MOA 일일 수집 보고서</h1>'
         '<p>수집 완료와 표 분석 완료는 서로 다른 상태입니다. 후보는 정답 데이터가 아닙니다.</p><pre>'
         +html.escape(json.dumps(report,ensure_ascii=False,indent=2))+'</pre></html>').encode())
    return report


def run_once(root: Path, only_office: str | None = None) -> dict:
    with run_lock(root),Store(root) as store:
        day=kst_now().date().isoformat()
        run_id=store.start_run(day)
        heartbeat(root,'starting')
        try:
            meter=Budget(store,day,integer('MAX_HTTP_REQUESTS',5000,10,20000))
            fetcher=Fetcher(delay=integer('REQUEST_DELAY_SECONDS',2,1,60),
                            max_requests=meter.cap,meter=meter)
            schools,warning=registry(store,fetcher)
            report=collect(store,fetcher,schools,day,
                max_schools=integer('MAX_SCHOOLS_PER_REGION',50,1,500),
                max_posts=integer('MAX_POSTS_PER_SCHOOL',12,1,50),
                seoul_target=integer('SEOUL_DAILY_TARGET',50,1,200),
                gyeonggi_target=integer('GYEONGGI_DAILY_TARGET',50,1,200),
                other_target=integer('OTHER_DAILY_TARGET',20,1,200),only_office=only_office)
            if warning: report['registry_warning']=warning
            report['analysis_queue']=drain_analyse(store,integer('ANALYSE_BATCH',200,1,2000),
                                                   owner='daily')
        except Exception as exc:
            report={'day':day,'status':'error','error':str(exc)[:400],'error_type':type(exc).__name__}
        report['only_office']=only_office
        store.finish_run(run_id,report)
        heartbeat(root,'idle')
        print(encode(report),flush=True)
        return report


def schedule(root: Path):
    when=os.environ.get('DAILY_AT','04:00')
    if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',when): raise ValueError('DAILY_AT은 HH:MM 형식입니다.')
    stop=False
    def halt(*_):
        nonlocal stop
        stop=True
    signal.signal(signal.SIGTERM,halt)
    signal.signal(signal.SIGINT,halt)
    initial=os.environ.get('RUN_ON_START','true').lower()=='true'
    while not stop:
        now=kst_now()
        with Store(root) as store:
            done=store.attempted(now.date().isoformat())
            last=store.last_national_run(now.date().isoformat())
        retry_ready=True
        if last and last.get('finished'):
            try:
                elapsed=(kst_now()-datetime.fromisoformat(last['finished'])).total_seconds()
                retry_ready=elapsed>=integer('RETRY_MINUTES',60,5,1440)*60
            except (TypeError,ValueError):
                retry_ready=True
        if not done and retry_ready and (initial or now.strftime('%H:%M')>=when):
            try:
                run_once(root)
            except RuntimeError as exc:
                print(encode({'event':'schedule_skipped','reason':str(exc)}),flush=True)
        else:
            # The daily run always wins the lock; backfill only uses the leftover window
            # and yields between batches so 04:00 incremental is never starved.
            try:
                with run_lock(root), Store(root) as store:
                    store.requeue_stale_jobs()
                    drained=drain_analyse(store,integer('ANALYSE_BATCH',200,1,2000),owner='scheduler')
                    if drained['done'] or drained['failed']:
                        print(encode({'event':'analyse_batch',**drained}),flush=True)
                    campaign=store.active_campaign()
                    # A backed-up analysis queue slows backfill first; incremental never waits.
                    if campaign and campaign['status']=='active' and drained['pending']==0:
                        meter=Budget(store,now.date().isoformat(),
                                     integer('MAX_HTTP_REQUESTS',5000,10,20000))
                        if meter.remaining>integer('BACKFILL_MIN_REQUESTS_LEFT',200,0,10000):
                            fetcher=Fetcher(delay=integer('REQUEST_DELAY_SECONDS',2,1,60),
                                            max_requests=meter.remaining,meter=meter)
                            result=backfill.run_batch(store,fetcher,campaign,
                                batch_notices=integer('BACKFILL_BATCH_NOTICES',30,1,500),
                                max_minutes=integer('BACKFILL_BATCH_MINUTES',20,1,240))
                            print(encode({'event':'backfill_batch',**result}),flush=True)
                            drain_analyse(store,integer('ANALYSE_BATCH',200,1,2000),
                                          owner='scheduler')
            except RuntimeError as exc:
                if '실행 중' not in str(exc):
                    print(encode({'event':'backfill_skipped','reason':str(exc)[:200]}),flush=True)
        initial=False
        heartbeat(root,'idle')
        for _ in range(30):
            if stop: break
            time.sleep(1)


def cli(argv=None) -> int:
    parser=argparse.ArgumentParser(description='MOA 가정통신문 수집·표 사례 축적기')
    parser.add_argument('--data',type=Path,default=Path(os.environ.get('MOA_DATA_DIR','/data')))
    sub=parser.add_subparsers(dest='command',required=True)
    run=sub.add_parser('run',help='오늘 목표의 부족분 수집');run.add_argument('--office')
    sub.add_parser('schedule',help='NAS 내부 일일 스케줄러')
    sub.add_parser('sync-schools',help='학교목록 전체 동기화')
    sub.add_parser('status',help='수집/분석/검수 건수')
    sub.add_parser('doctor',help='설정/저장경로 점검')
    sub.add_parser('health',help='스케줄러 생존 점검')
    sub.add_parser('export',help='후보/승인/패턴 JSONL 재생성')
    analyse=sub.add_parser('analyse',help='분석 대기열 처리(또는 --id로 단건 재분석)')
    analyse.add_argument('--id');analyse.add_argument('--batch',type=int,default=200)
    bf=sub.add_parser('backfill',help='과거자료 수집 캠페인')
    bf.add_argument('action',choices=['plan','start','run','status','pause','resume'])
    bf.add_argument('--batch',type=int,default=30);bf.add_argument('--max-minutes',type=int,default=20)
    rq=sub.add_parser('review-queue',help='오늘의 우선검수 후보 생성/출력')
    rq.add_argument('--limit',type=int,default=None)
    rv=sub.add_parser('review',help='사례 검수: 승인/수정승인/반려/보류/승인취소')
    rv.add_argument('case_id')
    rv.add_argument('--status',choices=['approved','rejected','held','candidate'],required=True)
    rv.add_argument('--layout',choices=LAYOUTS)
    rv.add_argument('--reviewer',default='')
    rv.add_argument('--note',default='')
    rv.add_argument('--correction',type=Path,help='수정된 표 JSON 파일')
    rv.add_argument('--rights-reviewed',action='store_true')
    rv.add_argument('--privacy-reviewed',action='store_true')
    find=sub.add_parser('search',help='승인 사례 검색');find.add_argument('query');find.add_argument('--candidates',action='store_true')
    approve_parser=sub.add_parser('approve',help='원문 대조·이용권한·개인정보 확인 후 사례 승인')
    approve_parser.add_argument('case_id');approve_parser.add_argument('--layout',choices=LAYOUTS,required=True)
    approve_parser.add_argument('--reviewer',required=True)
    approve_parser.add_argument('--rights-reviewed',action='store_true')
    approve_parser.add_argument('--privacy-reviewed',action='store_true')
    args=parser.parse_args(argv)
    root=args.data.resolve()
    try:
        if args.command=='schedule': schedule(root);return 0
        if args.command=='run': return 0 if run_once(root,args.office)['status']=='complete' else 2
        if args.command=='health':
            heartbeat_data=json.loads((root/'heartbeat.json').read_text())
            return 0 if (kst_now()-datetime.fromisoformat(heartbeat_data['at'])).total_seconds()<600 else 1
        if args.command=='doctor':
            root.mkdir(parents=True,exist_ok=True)
            check={'data_directory':str(root),'declared_nas_path':os.environ.get('NAS_DATA_DIR','(local)'),
                   'writable':os.access(root,os.W_OK),'free_gb':round(shutil.disk_usage(root).free/1024**3,2),
                   'neis_key_set':bool(os.environ.get('NEIS_API_KEY')),
                   'registry_exists':(root/'registry/schools.json').exists(),
                   'daily_at_kst':os.environ.get('DAILY_AT','04:00'),
                   'remote_ai_enabled':False,'model_training_enabled':False}
            print(json.dumps(check,ensure_ascii=False,indent=2))
            return 0 if check['writable'] and (check['neis_key_set'] or check['registry_exists']) else 2
        readonly = args.command in ('status','search','review-queue') or \
            (args.command=='backfill' and args.action in ('plan','status','start','pause','resume'))
        with (run_lock(root) if not readonly else _no_lock()),Store(root) as store:
            if args.command=='sync-schools':
                result=sync_schools(Fetcher(),root,os.environ.get('NEIS_API_KEY',''))
                print(f'{len(result)}개 학교 홈페이지 동기화')
            elif args.command=='status':
                last=store.db.execute('SELECT day,status,finished FROM runs ORDER BY id DESC LIMIT 1').fetchone()
                today=kst_now().date().isoformat()
                print(json.dumps({
                  'documents':store.db.execute('SELECT count(*) FROM notices').fetchone()[0],
                  'documents_today':store.db.execute(
                      'SELECT count(*) FROM notices WHERE day=? AND campaign_id IS NULL',(today,)).fetchone()[0],
                  'regions_today':{row['office']:row['n'] for row in store.db.execute(
                      'SELECT office,count(*) AS n FROM notices WHERE day=? AND campaign_id IS NULL'
                      ' GROUP BY office ORDER BY office',(today,))},
                  'partial_capture':store.db.execute(
                      "SELECT count(*) FROM notices WHERE capture='partial_capture'").fetchone()[0],
                  'table_state':{r[0]:r[1] for r in store.db.execute(
                      'SELECT table_state,count(*) FROM notices GROUP BY table_state')},
                  'unique_objects_on_disk':sum(1 for _ in (root/'objects').glob('*/*')),
                  'sightings':store.db.execute('SELECT count(*) FROM sightings').fetchone()[0],
                  'table_cases':store.db.execute('SELECT count(*) FROM cases').fetchone()[0],
                  'families':store.db.execute('SELECT count(DISTINCT family_id) FROM cases').fetchone()[0],
                  'review_pending_cases':store.db.execute(
                      "SELECT count(*) FROM cases WHERE review_status='candidate'").fetchone()[0],
                  'review':{r[0]:r[1] for r in store.db.execute(
                      'SELECT review_status,count(*) FROM cases GROUP BY review_status')},
                  'splits':{r[0]:r[1] for r in store.db.execute(
                      'SELECT split,count(*) FROM cases GROUP BY split')},
                  'approved':store.db.execute('SELECT count(*) FROM cases WHERE approved=1').fetchone()[0],
                  'analysis':{r[0]:r[1] for r in store.db.execute('SELECT analysis_status,count(*) FROM notices GROUP BY analysis_status')},
                  'jobs':{r[0]:r[1] for r in store.db.execute(
                      "SELECT status,count(*) FROM jobs WHERE kind='analyse' GROUP BY status")},
                  'requests_today':store.request_spent(today),
                  'backfill':backfill.status(store),
                  'external_ai':'미사용',
                  'last_run':dict(last) if last else None},ensure_ascii=False,indent=2))
            elif args.command=='export': export_learning(store)
            elif args.command=='search': print(json.dumps(search_cases(store,args.query,args.candidates),ensure_ascii=False,indent=2))
            elif args.command=='analyse':
                if args.id:
                    print(encode(learn(store,args.id)),flush=True)
                else:
                    store.requeue_stale_jobs()
                    print(encode(drain_analyse(store,args.batch,owner='cli')),flush=True)
                export_learning(store)
            elif args.command=='backfill':
                if args.action=='plan':
                    schools_path=root/'registry/schools.json'
                    schools=json.loads(schools_path.read_text())['schools'] if schools_path.exists() else []
                    print(json.dumps(backfill.plan(store,schools),ensure_ascii=False,indent=2))
                elif args.action=='start':
                    print(json.dumps(backfill.create_campaign(store),ensure_ascii=False,indent=2))
                elif args.action in ('pause','resume'):
                    print(json.dumps(backfill.set_status(store,'paused' if args.action=='pause' else 'active'),ensure_ascii=False))
                elif args.action=='status':
                    print(json.dumps(backfill.status(store),ensure_ascii=False,indent=2))
                elif args.action=='run':
                    campaign=store.active_campaign()
                    if not campaign:
                        raise ValueError('활성 캠페인이 없습니다. backfill start로 생성하세요.')
                    if campaign['status']!='active':
                        raise ValueError('캠페인 상태: '+campaign['status'])
                    meter=Budget(store,kst_now().date().isoformat(),
                                 integer('MAX_HTTP_REQUESTS',5000,10,20000))
                    fetcher=Fetcher(delay=integer('REQUEST_DELAY_SECONDS',2,1,60),
                                    max_requests=meter.remaining,meter=meter)
                    print(encode(backfill.run_batch(store,fetcher,campaign,
                        batch_notices=args.batch,max_minutes=args.max_minutes)),flush=True)
            elif args.command=='review-queue':
                day=kst_now().date().isoformat()
                limit=args.limit or integer('REVIEW_QUEUE_SIZE',20,1,200)
                print(json.dumps(build_review_queue(store,day,limit),ensure_ascii=False,indent=2))
            elif args.command=='review':
                correction=json.loads(args.correction.read_text()) if args.correction else None
                print(encode(review(store,args.case_id,args.status,layout=args.layout,
                    reviewer=args.reviewer,note=args.note,correction=correction,
                    rights_reviewed=args.rights_reviewed,
                    privacy_reviewed=args.privacy_reviewed)),flush=True)
                export_learning(store)
            elif args.command=='approve':
                approve(store,args.case_id,args.layout,args.reviewer,args.rights_reviewed,args.privacy_reviewed)
                export_learning(store)
        return 0
    except Exception as exc:
        print(encode({'status':'error','error':str(exc)[:400],'type':type(exc).__name__}),flush=True)
        return 2


def _no_lock():
    from contextlib import nullcontext
    return nullcontext()
