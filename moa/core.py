"""Persistence, quotas, campaigns, job queue, and process locking. No raw corpus in Git."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
SCHEMA_VERSION = 2


def kst_now() -> datetime:
    return datetime.now(KST)


def quota(code: str, name: str, seoul: int = 50, gyeonggi: int = 50, other: int = 20) -> int:
    if code == 'B10' or '서울' in name:
        return seoul
    if code == 'J10' or '경기' in name:
        return gyeonggi
    return other


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.moa-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def write_json(path: Path, value) -> None:
    atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode())


def write_jsonl(path: Path, rows) -> None:
    """Stream atomically so corpus size does not become RAM usage."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.moa-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            for row in rows:
                f.write(encode(row) + '\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextlib.contextmanager
def run_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.run.lock').open('a') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('다른 MOA 작업이 실행 중입니다.') from exc
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


class Store:
    def __init__(self, root: Path, thread_safe: bool = False):
        self.root = Path(root).resolve()
        for name in ('db', 'objects', 'documents', 'extracted', 'learning', 'reports', 'registry'):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        # thread_safe=True is for the local review UI whose server handles each
        # request on a new thread; callers still serialise through web.py's lock.
        self.db = sqlite3.connect(self.root / 'db/moa.sqlite3', timeout=30,
                                  check_same_thread=not thread_safe)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA busy_timeout=30000')
        existed = self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='notices'").fetchone()
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        if existed and version < SCHEMA_VERSION:
            self.backup('schema-v%d-to-v%d' % (version, SCHEMA_VERSION))
            self._migrate(version)
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS notices(
          id TEXT PRIMARY KEY, office TEXT NOT NULL, school TEXT NOT NULL,
          day TEXT NOT NULL, url TEXT NOT NULL, payload TEXT NOT NULL,
          analysis_status TEXT NOT NULL DEFAULT 'pending',
          published_date TEXT, campaign_id INTEGER, capture TEXT NOT NULL DEFAULT 'full',
          table_state TEXT NOT NULL DEFAULT 'unknown');
        CREATE INDEX IF NOT EXISTS notices_day ON notices(day,office);
        CREATE TABLE IF NOT EXISTS sightings(
          notice_id TEXT NOT NULL REFERENCES notices(id), office TEXT NOT NULL,
          school TEXT NOT NULL, url TEXT NOT NULL, first_seen TEXT NOT NULL,
          last_seen TEXT NOT NULL, PRIMARY KEY(notice_id,office,school,url));
        CREATE TABLE IF NOT EXISTS cases(
          id TEXT PRIMARY KEY, notice_id TEXT NOT NULL REFERENCES notices(id),
          pattern TEXT NOT NULL, layout TEXT NOT NULL, payload TEXT NOT NULL,
          approved INTEGER NOT NULL DEFAULT 0, reviewer TEXT, reviewed_at TEXT,
          family_id TEXT, split TEXT NOT NULL DEFAULT 'train',
          review_status TEXT NOT NULL DEFAULT 'candidate',
          queue_reason TEXT, correction TEXT, review_history TEXT);
        CREATE INDEX IF NOT EXISTS cases_pattern ON cases(pattern);
        CREATE TABLE IF NOT EXISTS runs(
          id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL,
          started TEXT NOT NULL, finished TEXT, status TEXT NOT NULL, report TEXT);
        CREATE TABLE IF NOT EXISTS boards(
          school TEXT PRIMARY KEY, url TEXT NOT NULL, checked TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS school_state(
          school TEXT PRIMARY KEY, status TEXT NOT NULL, reason TEXT,
          boards TEXT, next_retry TEXT, updated TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS campaigns(
          id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
          window_start TEXT NOT NULL, window_end TEXT NOT NULL,
          target INTEGER NOT NULL, cap INTEGER NOT NULL, seed TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'active', note TEXT);
        CREATE TABLE IF NOT EXISTS campaign_schools(
          campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
          school TEXT NOT NULL, collected INTEGER NOT NULL DEFAULT 0,
          day TEXT, day_collected INTEGER NOT NULL DEFAULT 0,
          cursor TEXT, status TEXT NOT NULL DEFAULT 'pending', reason TEXT,
          updated TEXT NOT NULL, PRIMARY KEY(campaign_id, school));
        CREATE TABLE IF NOT EXISTS jobs(
          id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
          ref_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
          attempts INTEGER NOT NULL DEFAULT 0, next_attempt TEXT,
          owner TEXT, updated TEXT NOT NULL, payload TEXT,
          UNIQUE(kind, ref_id));
        CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(kind, status, next_attempt);
        CREATE TABLE IF NOT EXISTS usage(
          day TEXT PRIMARY KEY, requests INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS review_queue(
          day TEXT NOT NULL, case_id TEXT NOT NULL, score INTEGER NOT NULL,
          reasons TEXT NOT NULL, PRIMARY KEY(day, case_id));
        ''')
        self.db.execute('CREATE INDEX IF NOT EXISTS notices_campaign ON notices(campaign_id)')
        self.db.execute('CREATE INDEX IF NOT EXISTS cases_family ON cases(family_id)')
        if existed and version < SCHEMA_VERSION:
            # Re-analysis backfills family_id/split on old cases and retries partial docs.
            self.db.execute(
                "INSERT OR IGNORE INTO jobs(kind,ref_id,status,updated)"
                " SELECT 'analyse', id, 'pending', ? FROM notices",
                (kst_now().isoformat(),))
        self.db.execute('PRAGMA user_version=%d' % SCHEMA_VERSION)
        self.db.commit()

    def _migrate(self, version: int) -> None:
        """Idempotent column additions for pre-v2 databases. Existing rows keep defaults."""
        def cols(table):
            return {r[1] for r in self.db.execute('PRAGMA table_info(%s)' % table)}
        if version < 2:
            for table, adds in {
                'notices': [('published_date', 'TEXT'), ('campaign_id', 'INTEGER'),
                            ('capture', "TEXT NOT NULL DEFAULT 'full'"),
                            ('table_state', "TEXT NOT NULL DEFAULT 'unknown'")],
                'cases': [('family_id', 'TEXT'), ('split', "TEXT NOT NULL DEFAULT 'train'"),
                          ('review_status', "TEXT NOT NULL DEFAULT 'candidate'"),
                          ('queue_reason', 'TEXT'), ('correction', 'TEXT'),
                          ('review_history', 'TEXT')],
            }.items():
                have = cols(table)
                for name, decl in adds:
                    if name not in have:
                        self.db.execute('ALTER TABLE %s ADD COLUMN %s %s' % (table, name, decl))
            # Existing candidates keep their candidate status; approved flag dominates.
            self.db.execute("UPDATE cases SET review_status='approved' WHERE approved=1")
            self.db.commit()

    def backup(self, tag: str = 'manual') -> Path:
        """Consistent online backup (WAL-safe) into db/backups/."""
        self.db.commit()
        dest_dir = self.root / 'db' / 'backups'
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / ('moa-%s-%s.sqlite3' % (kst_now().strftime('%Y%m%d-%H%M%S'), tag))
        target = sqlite3.connect(dest)
        try:
            self.db.backup(target)
        finally:
            target.close()
        return dest

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.db.close()

    def object(self, data: bytes, filename: str = '') -> dict:
        sha = digest(data)
        path = self.root / 'objects' / sha[:2] / sha
        if path.exists():
            if path.stat().st_size != len(data) or digest(path.read_bytes()) != sha:
                raise RuntimeError('객체 저장소 무결성 오류: ' + sha)
        else:
            atomic_write(path, data)
        return {'sha256': sha, 'bytes': len(data), 'filename': Path(filename).name,
                'path': str(path.relative_to(self.root))}

    def object_path(self, sha: str) -> Path:
        if not re.fullmatch(r'[a-f0-9]{64}', sha):
            raise ValueError('Invalid object hash')
        return self.root / 'objects' / sha[:2] / sha

    def count_day(self, day: str, office: str) -> int:
        # Incremental daily performance never counts backfill-campaign notices.
        return self.db.execute(
            'SELECT count(*) FROM notices WHERE day=? AND office=? AND campaign_id IS NULL',
            (day, office)).fetchone()[0]

    def count_campaign(self, campaign_id: int) -> int:
        return self.db.execute('SELECT count(*) FROM notices WHERE campaign_id=?',
                               (campaign_id,)).fetchone()[0]

    def save_notice(self, school: dict, url: str, title: str, body_html: str,
                    assets: list[dict], day: str, page_bytes: bytes | None = None,
                    skipped_assets: list[dict] | None = None,
                    failed_assets: list[dict] | None = None,
                    published_date: str | None = None,
                    campaign_id: int | None = None) -> tuple[str, bool]:
        date.fromisoformat(day)
        office = school['office_code']
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}', office):
            raise ValueError('Invalid office code')
        # Preserve all dates/numbers and table geometry; never near-dedupe away revisions.
        fingerprint = {'title': title.strip(), 'html': re.sub(r'>\s+<', '><', body_html.strip()),
                       'assets': sorted({a['sha256'] for a in assets})}
        ident = digest(encode(fingerprint).encode())
        new = self.db.execute('SELECT 1 FROM notices WHERE id=?', (ident,)).fetchone() is None
        if new:
            previous = self.db.execute('SELECT id FROM notices WHERE url=? ORDER BY rowid DESC LIMIT 1',
                                       (url,)).fetchone()
            capture = 'partial_capture' if (skipped_assets or failed_assets or not body_html.strip()) \
                else 'full'
            payload = {'schema_version': 1, 'id': ident, 'school': school, 'url': url,
                       'title': title, 'body_html': body_html, 'assets': assets, 'collected_day': day,
                       'collected_at': kst_now().isoformat(), 'rights': 'unreviewed',
                       'capture': capture, 'published_date': published_date,
                       'previous_version': previous[0] if previous else None}
            if skipped_assets:
                payload['attachments_skipped_robots'] = skipped_assets
            if failed_assets:
                payload['attachments_failed'] = failed_assets
            if page_bytes:
                payload['source_page'] = self.object(page_bytes, 'source.html')
            write_json(self.root / 'documents' / office / day / f'{ident}.json', payload)
            self.db.execute(
                'INSERT INTO notices(id,office,school,day,url,payload,published_date,campaign_id,capture)'
                ' VALUES(?,?,?,?,?,?,?,?,?)',
                (ident, office, school['school_code'], day, url, encode(payload),
                 published_date, campaign_id, capture))
            self.enqueue('analyse', ident)
        now = kst_now().isoformat()
        self.db.execute('''INSERT INTO sightings VALUES(?,?,?,?,?,?)
            ON CONFLICT(notice_id,office,school,url) DO UPDATE SET last_seen=excluded.last_seen''',
            (ident, office, school['school_code'], url, now, now))
        self.db.commit()
        return ident, new

    # ---- persistent job queue -------------------------------------------------

    def enqueue(self, kind: str, ref_id: str, payload: dict | None = None) -> None:
        self.db.execute(
            "INSERT INTO jobs(kind,ref_id,status,updated,payload) VALUES(?,?, 'pending',?,?)"
            ' ON CONFLICT(kind,ref_id) DO NOTHING',
            (kind, ref_id, kst_now().isoformat(), encode(payload) if payload else None))

    def claim_jobs(self, kind: str, limit: int, owner: str) -> list[sqlite3.Row]:
        now = kst_now().isoformat()
        rows = self.db.execute(
            "SELECT * FROM jobs WHERE kind=? AND status='pending'"
            ' AND (next_attempt IS NULL OR next_attempt<=?) ORDER BY id LIMIT ?',
            (kind, now, limit)).fetchall()
        for row in rows:
            self.db.execute(
                "UPDATE jobs SET status='running', owner=?, attempts=attempts+1, updated=? WHERE id=?",
                (owner, now, row['id']))
        self.db.commit()
        return rows

    def finish_job(self, job_id: int) -> None:
        self.db.execute("UPDATE jobs SET status='done', updated=? WHERE id=?",
                        (kst_now().isoformat(), job_id))
        self.db.commit()

    def fail_job(self, job_id: int, reason: str, retry_minutes: int = 30,
                 max_attempts: int = 5) -> None:
        row = self.db.execute('SELECT attempts FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row and row['attempts'] >= max_attempts:
            self.db.execute(
                "UPDATE jobs SET status='error', payload=?, updated=? WHERE id=?",
                (encode({'error': reason[:300]}), kst_now().isoformat(), job_id))
        else:
            nxt = (kst_now() + timedelta(minutes=retry_minutes)).isoformat()
            self.db.execute(
                "UPDATE jobs SET status='pending', next_attempt=?, payload=?, updated=? WHERE id=?",
                (nxt, encode({'error': reason[:300]}), kst_now().isoformat(), job_id))
        self.db.commit()

    def requeue_stale_jobs(self, stale_minutes: int = 30) -> int:
        """Jobs whose owner died mid-run return to pending so nothing is lost."""
        cutoff = (kst_now() - timedelta(minutes=stale_minutes)).isoformat()
        cur = self.db.execute(
            "UPDATE jobs SET status='pending', owner=NULL, updated=? "
            "WHERE status='running' AND updated<?", (kst_now().isoformat(), cutoff))
        self.db.commit()
        return cur.rowcount

    # ---- shared daily request budget ------------------------------------------

    def request_spent(self, day: str) -> int:
        row = self.db.execute('SELECT requests FROM usage WHERE day=?', (day,)).fetchone()
        return row['requests'] if row else 0

    def request_spend(self, day: str, n: int = 1) -> int:
        self.db.execute(
            'INSERT INTO usage(day,requests) VALUES(?,?)'
            ' ON CONFLICT(day) DO UPDATE SET requests=requests+?', (day, n, n))
        self.db.commit()
        return self.request_spent(day)

    # ---- school state cache -----------------------------------------------------

    def school_state(self, key: str) -> dict | None:
        row = self.db.execute('SELECT * FROM school_state WHERE school=?', (key,)).fetchone()
        return dict(row) if row else None

    def record_school(self, key: str, status: str, reason: str = '',
                      boards: list[str] | None = None, retry_days: float = 1.0) -> None:
        nxt = (kst_now() + timedelta(days=retry_days)).isoformat() if status != 'ok' else None
        self.db.execute(
            'INSERT INTO school_state(school,status,reason,boards,next_retry,updated)'
            ' VALUES(?,?,?,?,?,?)'
            ' ON CONFLICT(school) DO UPDATE SET status=excluded.status,'
            ' reason=excluded.reason, boards=COALESCE(excluded.boards,school_state.boards),'
            ' next_retry=excluded.next_retry, updated=excluded.updated',
            (key, status, reason[:300], encode(boards) if boards else None,
             nxt, kst_now().isoformat()))
        self.db.commit()

    def school_blocked(self, key: str) -> str | None:
        """Returns the cached block reason while next_retry is in the future."""
        state = self.school_state(key)
        if not state or state['status'] == 'ok' or not state['next_retry']:
            return None
        if state['next_retry'] > kst_now().isoformat():
            return state['reason'] or state['status']
        return None

    # ---- backfill campaigns -----------------------------------------------------

    def active_campaign(self) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM campaigns WHERE status IN ('active','paused') ORDER BY id DESC"
            ' LIMIT 1').fetchone()
        return dict(row) if row else None

    def campaign_school(self, campaign_id: int, key: str) -> dict | None:
        row = self.db.execute(
            'SELECT * FROM campaign_schools WHERE campaign_id=? AND school=?',
            (campaign_id, key)).fetchone()
        return dict(row) if row else None

    def campaign_school_update(self, campaign_id: int, key: str, **fields) -> None:
        row = self.campaign_school(campaign_id, key) or {}
        merged = {'collected': 0, 'day': None, 'day_collected': 0, 'cursor': None,
                  'status': 'pending', 'reason': None, **row, **fields}
        self.db.execute(
            'INSERT INTO campaign_schools(campaign_id,school,collected,day,day_collected,'
            ' cursor,status,reason,updated) VALUES(?,?,?,?,?,?,?,?,?)'
            ' ON CONFLICT(campaign_id,school) DO UPDATE SET collected=excluded.collected,'
            ' day=excluded.day, day_collected=excluded.day_collected, cursor=excluded.cursor,'
            ' status=excluded.status, reason=excluded.reason, updated=excluded.updated',
            (campaign_id, key, merged['collected'], merged['day'], merged['day_collected'],
             merged['cursor'], merged['status'], merged['reason'], kst_now().isoformat()))
        self.db.commit()


    def notice(self, ident: str) -> dict:
        row = self.db.execute('SELECT payload FROM notices WHERE id=?', (ident,)).fetchone()
        if not row:
            raise ValueError('통신문 ID를 찾을 수 없습니다.')
        return json.loads(row[0])

    def start_run(self, day: str) -> int:
        cur = self.db.execute('INSERT INTO runs(day,started,status) VALUES(?,?,?)',
                              (day, kst_now().isoformat(), 'running'))
        self.db.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, report: dict) -> None:
        self.db.execute('UPDATE runs SET finished=?,status=?,report=? WHERE id=?',
                        (kst_now().isoformat(), report['status'], encode(report), run_id))
        self.db.commit()
        write_json(self.root / 'reports' / f"{report['day']}-{run_id}.json", report)
        write_json(self.root / 'reports/latest.json', report)

    def attempted(self, day: str) -> bool:
        rows = self.db.execute('SELECT report FROM runs WHERE day=? AND finished IS NOT NULL', (day,))
        # A run that ended in a transient error (network/API) does not count as the day's attempt,
        # so the scheduler may retry it; complete/partial runs are never repeated for that day.
        for row in rows:
            if not row[0]:
                continue
            report = json.loads(row[0])
            if report.get('only_office') is None and report.get('status') != 'error':
                return True
        return False

    def last_national_run(self, day: str) -> dict | None:
        rows = self.db.execute(
            "SELECT finished,status,report FROM runs WHERE day=? AND finished IS NOT NULL ORDER BY id DESC",
            (day,)).fetchall()
        for row in rows:
            report = json.loads(row['report']) if row['report'] else {}
            if report.get('only_office') is None:
                return {'finished': row['finished'], 'status': row['status']}
        return None


class Budget:
    """Daily HTTP request budget shared by every MOA process through the usage table."""

    def __init__(self, store: Store, day: str, cap: int):
        self.store, self.day, self.cap = store, day, cap

    @property
    def spent(self) -> int:
        return self.store.request_spent(self.day)

    @property
    def remaining(self) -> int:
        return max(0, self.cap - self.spent)

    def spend(self, n: int = 1) -> None:
        if self.spent + n > self.cap:
            raise RuntimeError('일일 HTTP 요청 상한에 도달했습니다.')
        self.store.request_spend(self.day, n)
