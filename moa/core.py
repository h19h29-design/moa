"""Persistence, quotas, and process locking. No raw corpus belongs in Git."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')


def kst_now() -> datetime:
    return datetime.now(KST)


def quota(code: str, name: str, seoul: int = 10, other: int = 5) -> int:
    return seoul if code == 'B10' or '서울' in name else other


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
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        for name in ('db', 'objects', 'documents', 'extracted', 'learning', 'reports', 'registry'):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / 'db/moa.sqlite3', timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS notices(
          id TEXT PRIMARY KEY, office TEXT NOT NULL, school TEXT NOT NULL,
          day TEXT NOT NULL, url TEXT NOT NULL, payload TEXT NOT NULL,
          analysis_status TEXT NOT NULL DEFAULT 'pending');
        CREATE INDEX IF NOT EXISTS notices_day ON notices(day,office);
        CREATE TABLE IF NOT EXISTS sightings(
          notice_id TEXT NOT NULL REFERENCES notices(id), office TEXT NOT NULL,
          school TEXT NOT NULL, url TEXT NOT NULL, first_seen TEXT NOT NULL,
          last_seen TEXT NOT NULL, PRIMARY KEY(notice_id,office,school,url));
        CREATE TABLE IF NOT EXISTS cases(
          id TEXT PRIMARY KEY, notice_id TEXT NOT NULL REFERENCES notices(id),
          pattern TEXT NOT NULL, layout TEXT NOT NULL, payload TEXT NOT NULL,
          approved INTEGER NOT NULL DEFAULT 0, reviewer TEXT, reviewed_at TEXT);
        CREATE INDEX IF NOT EXISTS cases_pattern ON cases(pattern);
        CREATE TABLE IF NOT EXISTS runs(
          id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT NOT NULL,
          started TEXT NOT NULL, finished TEXT, status TEXT NOT NULL, report TEXT);
        CREATE TABLE IF NOT EXISTS boards(
          school TEXT PRIMARY KEY, url TEXT NOT NULL, checked TEXT NOT NULL);
        ''')
        self.db.commit()

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
        return self.db.execute('SELECT count(*) FROM notices WHERE day=? AND office=?',
                               (day, office)).fetchone()[0]

    def save_notice(self, school: dict, url: str, title: str, body_html: str,
                    assets: list[dict], day: str, page_bytes: bytes | None = None,
                    skipped_assets: list[dict] | None = None,
                    failed_assets: list[dict] | None = None) -> tuple[str, bool]:
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
            payload = {'schema_version': 1, 'id': ident, 'school': school, 'url': url,
                       'title': title, 'body_html': body_html, 'assets': assets, 'collected_day': day,
                       'collected_at': kst_now().isoformat(), 'rights': 'unreviewed',
                       'previous_version': previous[0] if previous else None}
            if skipped_assets:
                payload['attachments_skipped_robots'] = skipped_assets
            if failed_assets:
                payload['attachments_failed'] = failed_assets
            if page_bytes:
                payload['source_page'] = self.object(page_bytes, 'source.html')
            write_json(self.root / 'documents' / office / day / f'{ident}.json', payload)
            self.db.execute('INSERT INTO notices(id,office,school,day,url,payload) VALUES(?,?,?,?,?,?)',
                            (ident, office, school['school_code'], day, url, encode(payload)))
        now = kst_now().isoformat()
        self.db.execute('''INSERT INTO sightings VALUES(?,?,?,?,?,?)
            ON CONFLICT(notice_id,office,school,url) DO UPDATE SET last_seen=excluded.last_seen''',
            (ident, office, school['school_code'], url, now, now))
        self.db.commit()
        return ident, new

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
