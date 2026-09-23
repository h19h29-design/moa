"""Backfill campaigns: bounded, resumable collection of past notices.

A campaign fixes its window/target/seed at creation. Progress is checkpointed per
school/board/page in campaign_schools, so a restart resumes where it stopped.
Backfill notices carry campaign_id and never count toward the daily incremental goal;
SHA-256 object dedup is shared with incremental collection.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import time
from datetime import date, timedelta
from urllib.parse import urlsplit

from .core import Store, digest, encode, kst_now, write_json
from .crawl import find_boards, next_page_url, open_pages, post_links

MAX_PAGES = 40          # per board per campaign; guards infinite paging loops
SEEN_BOUND = 2000       # bounded per-school memory of visited posts/pages


def create_campaign(store: Store, years: int = 2, target: int = 10000,
                    cap: int = 20000, note: str = '') -> dict:
    existing = store.active_campaign()
    if existing:
        return existing
    end = kst_now().date()
    start = end - timedelta(days=int(365.25*years))
    cur = store.db.execute(
        'INSERT INTO campaigns(created_at,window_start,window_end,target,cap,seed,status,note)'
        " VALUES(?,?,?,?,?,?,'active',?)",
        (kst_now().isoformat(), start.isoformat(), end.isoformat(), target, cap,
         digest(('moa-backfill:'+end.isoformat()).encode())[:16], note[:300]))
    store.db.commit()
    return dict(store.db.execute('SELECT * FROM campaigns WHERE id=?',
                                 (cur.lastrowid,)).fetchone())


def plan(store: Store, schools: list[dict]) -> dict:
    """Dry-run: what a campaign would cover, without fetching anything."""
    campaign = store.active_campaign()
    excluded = {c.strip() for c in
                os.environ.get('EXCLUDE_OFFICES', 'V10').split(',') if c.strip()}
    eligible = [s for s in schools if s['office_code'] not in excluded]
    blocked = sum(1 for s in eligible
                  if store.school_blocked(s['office_code']+':'+s['school_code']))
    return {'campaign': campaign, 'schools_total': len(schools),
            'schools_eligible': len(eligible), 'schools_blocked_cached': blocked,
            'unique_documents_now': _total(store),
            'window': '캠페인 생성일 기준 최근 2년', 'target_total': 10000, 'cap_total': 20000,
            'per_school_campaign_cap': 10, 'per_school_daily_cap': 2}


def set_status(store: Store, status: str) -> dict:
    campaign = store.active_campaign()
    if not campaign:
        raise ValueError('활성 캠페인이 없습니다. backfill start로 생성하세요.')
    if status not in ('active', 'paused'):
        raise ValueError('status는 active|paused 중 하나입니다.')
    store.db.execute('UPDATE campaigns SET status=? WHERE id=?', (status, campaign['id']))
    store.db.commit()
    campaign['status'] = status
    return campaign


def status(store: Store) -> dict:
    campaign = store.active_campaign()
    if not campaign:
        return {'status': 'none', 'unique_documents': _total(store)}
    cid = campaign['id']
    rows = store.db.execute(
        'SELECT status, count(*) AS n, sum(collected) AS c FROM campaign_schools'
        ' WHERE campaign_id=? GROUP BY status', (cid,)).fetchall()
    return {'campaign': campaign, 'unique_documents': _total(store),
            'campaign_new_documents': store.count_campaign(cid),
            'remaining_to_target': max(0, campaign['target']-_total(store)),
            'remaining_to_cap': max(0, campaign['cap']-_total(store)),
            'schools': {r['status']: {'count': r['n'], 'collected': r['c'] or 0}
                        for r in rows}}


def _total(store: Store) -> int:
    return store.db.execute('SELECT count(*) FROM notices').fetchone()[0]


def _school_order(store: Store, campaign: dict, schools: list[dict]) -> list[dict]:
    """Least-recently-worked first, interleaved across offices; blocked schools last."""
    rng = random.Random(campaign['seed'] + kst_now().date().isoformat())
    rows = {r['school']: r for r in store.db.execute(
        'SELECT school,status,updated,collected FROM campaign_schools WHERE campaign_id=?',
        (campaign['id'],))}

    def rank(s):
        key = s['office_code']+':'+s['school_code']
        row = rows.get(key)
        if store.school_blocked(key):
            return (3, '')
        if row and row['status'] in ('done', 'capped'):
            return (2, row['updated'])
        return (0 if not row else 1, row['updated'] if row else '')

    ordered = sorted(schools, key=rank)
    offices = {}
    for s in ordered:
        offices.setdefault(s['office_code'], []).append(s)
    for group in offices.values():
        rng.shuffle(group)
    out = []
    while any(offices.values()):
        for office in sorted(offices):
            if offices[office]:
                out.append(offices[office].pop())
    return out


def run_batch(store: Store, fetcher, campaign: dict, batch_notices: int = 30,
              max_minutes: int = 20, school_cap: int = 10,
              school_daily_cap: int = 2) -> dict:
    """One bounded batch: returns after batch_notices new docs or max_minutes."""
    from .app import _block_kind, collect_post, heartbeat, integer
    school_cap = integer('BACKFILL_SCHOOL_CAP', school_cap, 1, 100)
    school_daily_cap = integer('BACKFILL_SCHOOL_DAILY', school_daily_cap, 1, 50)
    deadline = time.monotonic() + max_minutes*60
    day = kst_now().date().isoformat()
    start = date.fromisoformat(campaign['window_start'])
    end = date.fromisoformat(campaign['window_end'])
    schools_path = store.root/'registry/schools.json'
    if not schools_path.exists():
        raise RuntimeError('학교목록이 없습니다. sync-schools를 먼저 실행하세요.')
    schools = json.loads(schools_path.read_text())['schools']
    excluded = {c.strip() for c in
                os.environ.get('EXCLUDE_OFFICES', 'V10').split(',') if c.strip()}
    overrides_path = store.root/'registry/overrides.json'
    overrides = json.loads(overrides_path.read_text()) if overrides_path.exists() else {}
    stats = {'campaign_id': campaign['id'], 'new': 0, 'duplicates': 0, 'errors': 0,
             'schools_touched': 0, 'stopped': None}
    for school in _school_order(store, campaign, schools):
        if stats['new'] >= batch_notices:
            stats['stopped'] = 'batch_notices'
            break
        if time.monotonic() > deadline:
            stats['stopped'] = 'time_budget'
            break
        if _total(store) >= campaign['cap']:
            store.db.execute("UPDATE campaigns SET status='cap_reached' WHERE id=?",
                             (campaign['id'],))
            store.db.commit()
            stats['stopped'] = 'cap_reached'
            break
        if school['office_code'] in excluded:
            continue
        key = school['office_code']+':'+school['school_code']
        if store.school_blocked(key):
            continue
        cs = store.campaign_school(campaign['id'], key) or {}
        if cs.get('status') in ('done', 'capped'):
            continue
        if (cs.get('collected') or 0) >= school_cap:
            store.campaign_school_update(campaign['id'], key, status='capped')
            continue
        if cs.get('day') != day:
            cs['day'], cs['day_collected'] = day, 0
        if (cs.get('day_collected') or 0) >= school_daily_cap:
            continue
        stats['schools_touched'] += 1
        heartbeat(store.root, 'backfill:'+key)
        cursor = json.loads(cs['cursor']) if cs.get('cursor') else {}
        try:
            _backfill_school(store, fetcher, campaign, school, cursor, cs,
                             overrides.get(key, {}), start, end, day,
                             school_cap, school_daily_cap, stats, deadline,
                             batch_notices)
        except Exception as exc:
            stats['errors'] += 1
            kind, retry = _block_kind(str(exc))
            store.record_school(key, kind, str(exc)[:200], retry_days=retry)
            store.campaign_school_update(campaign['id'], key, status='error',
                                         reason=str(exc)[:200],
                                         cursor=encode(cursor) if cursor else None)
    if stats['stopped'] is None:
        stats['stopped'] = 'candidates_exhausted'
        left = [s for s in schools
                if s['office_code'] not in excluded
                and not store.school_blocked(s['office_code']+':'+s['school_code'])
                and (store.campaign_school(campaign['id'], s['office_code']+':'+s['school_code'])
                     or {}).get('status') not in ('done', 'capped')]
        if not left:
            store.db.execute("UPDATE campaigns SET status='coverage_exhausted' WHERE id=?",
                             (campaign['id'],))
            store.db.commit()
    write_json(store.root/'reports'/'backfill-latest.json',
               {'at': kst_now().isoformat(), **stats, **status(store)})
    return stats


def _backfill_school(store, fetcher, campaign, school, cursor, cs, override,
                     start, end, day, school_cap, school_daily_cap, stats, deadline,
                     batch_notices):
    from .app import collect_post, integer
    key = school['office_code']+':'+school['school_code']
    cid = campaign['id']
    if 'boards' not in cursor:
        state = store.school_state(key) or {}
        known = json.loads(state['boards']) if state.get('boards') else None
        cached = store.db.execute('SELECT url FROM boards WHERE school=?', (key,)).fetchone()
        boards, found_hosts = find_boards(fetcher, school, override,
                                          cached[0] if cached else None, known=known)
        cursor = {'boards': boards, 'board': boards[0], 'board_idx': 0,
                  'seen_posts': [], 'seen_pages': [], 'pages_done': 0,
                  'hosts': sorted(found_hosts)}
    seen_posts = list(cursor.get('seen_posts', []))
    seen_pages = set(cursor.get('seen_pages', []))
    boards = cursor['boards']
    board = cursor.get('board') or boards[0]
    board_idx = cursor.get('board_idx', 0)
    hosts = set(cursor.get('hosts') or []) or {urlsplit(board).hostname}
    pages_done = cursor.get('pages_done', 0)
    while True:
        if time.monotonic() > deadline:
            break
        if stats['new'] >= batch_notices:
            break
        if (cs.get('collected') or 0) >= school_cap or \
                (cs.get('day_collected') or 0) >= school_daily_cap:
            break
        pages, hosts = open_pages(fetcher, board, hosts)
        page = pages[-1]
        found = post_links(page.text, page.url, override.get('post_selector', ''))
        dated = [p for p in found if p.get('published_date')]
        new_posts = [p for p in found if p['url'] not in seen_posts]
        for post in new_posts:
            if (cs.get('collected') or 0) >= school_cap or \
                    (cs.get('day_collected') or 0) >= school_daily_cap:
                break
            if stats['new'] >= batch_notices:
                break
            if time.monotonic() > deadline:
                break
            published = post.get('published_date')
            if published:
                try:
                    d = date.fromisoformat(published)
                except ValueError:
                    d = None
                if d and (d < start or d > end):
                    seen_posts.append(post['url'])
                    continue
            if shutil.disk_usage(store.root).free < integer('MIN_FREE_MB', 1024, 100, 100000)*1024*1024:
                raise RuntimeError('저장공간 하한 도달: 기존 자료를 삭제하지 않고 중단합니다.')
            try:
                ident, new, _, _ = collect_post(store, fetcher, school, post, hosts,
                                                override, day, campaign_id=cid)
                if new:
                    stats['new'] += 1
                    cs['collected'] = (cs.get('collected') or 0) + 1
                    cs['day_collected'] = (cs.get('day_collected') or 0) + 1
                else:
                    stats['duplicates'] += 1
            except Exception:
                stats['errors'] += 1
            seen_posts.append(post['url'])
        seen_posts = seen_posts[-SEEN_BOUND:]
        seen_pages.add(page.url)
        pages_done += 1
        nxt = next_page_url(page.text, page.url)
        # Stop a board when: no next page, a page repeats, the page cap is hit, or a full
        # dated page is entirely older than the window (one old pinned post never stops it).
        all_dated_old = len(dated) >= 3 and all(
            date.fromisoformat(p['published_date']) < start for p in dated)
        board_done = (not nxt or nxt in seen_pages or pages_done >= MAX_PAGES
                      or not new_posts or all_dated_old)
        if board_done:
            if board_idx + 1 < len(boards):
                board_idx += 1
                board = boards[board_idx]
                pages_done = 0
                seen_pages = set()
                cursor.update(board=board, board_idx=board_idx, seen_posts=seen_posts,
                              seen_pages=[], pages_done=0, hosts=sorted(hosts))
                store.campaign_school_update(cid, key, collected=cs['collected'],
                                             day=day, day_collected=cs['day_collected'],
                                             cursor=encode(cursor), status='active')
                continue
            store.campaign_school_update(cid, key, collected=cs['collected'], day=day,
                                         day_collected=cs['day_collected'],
                                         cursor=encode(cursor), status='done')
            store.record_school(key, 'ok', boards=boards)
            return
        cursor.update(board=nxt, board_idx=board_idx, seen_posts=seen_posts,
                      seen_pages=sorted(seen_pages)[-SEEN_BOUND:], pages_done=pages_done, hosts=sorted(hosts))
        # Checkpoint after every page: a restart resumes from this exact list page.
        store.campaign_school_update(cid, key, collected=cs['collected'], day=day,
                                     day_collected=cs['day_collected'],
                                     cursor=encode(cursor), status='active')
        board = nxt
    # Batch/time caps interrupted mid-board: persist the cursor so a restart resumes
    # from the last completed page instead of re-scanning it.
    cursor.update(board=board, board_idx=board_idx, seen_posts=seen_posts,
                  seen_pages=sorted(seen_pages)[-SEEN_BOUND:], pages_done=pages_done, hosts=sorted(hosts))
    store.campaign_school_update(cid, key, collected=cs.get('collected') or 0, day=day,
                                 day_collected=cs.get('day_collected') or 0,
                                 cursor=encode(cursor), status='active')
