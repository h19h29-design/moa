"""NAS-side worker relay (moa.worker.v1). Single source of truth = NAS app DB.

Transport notes (enforced by the web layer, documented here):
  * worker bearer token travels in the HTTP Authorization header only —
    never in URL, query string, or logs;
  * the web password (MOA_REVIEW_TOKEN) and the worker token
    (MOA_WORKER_TOKEN) are separate; either may be unset, in which case
    that surface stays disabled;
  * the MacBook is a pull worker: when it is offline or training, jobs
    wait server-side; the server never fires unbounded outbound requests;
  * worker scope is bounded: lease jobs, permitted train snapshots, and
    own results/model uploads only. No access to JEV logs, sealed audit
    rows, or the full corpus — those paths simply do not exist here.

A worker `complete` records a *machine* check only. Human approval lives
in mobile_reviews / cases and is never written from this module.
"""
from __future__ import annotations

import secrets
import time
import uuid
from . import worker_contract as wc

VERIFY_LEASE_SECONDS = 600
TRAIN_LEASE_SECONDS = 3600
HEARTBEAT_SECONDS = 30
RUBRIC_REVISION = "moa.rubric.v1"

WORKER_DDL = """
CREATE TABLE IF NOT EXISTS worker_jobs(
  job_id TEXT PRIMARY KEY, job_kind TEXT NOT NULL,
  envelope TEXT NOT NULL, idempotency_key TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  lease_id TEXT, fencing_token INTEGER NOT NULL DEFAULT 1,
  lease_expires_at REAL, worker_id TEXT,
  revision_cancelled INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL, updated_at REAL NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS worker_jobs_idem ON worker_jobs(idempotency_key);
CREATE TABLE IF NOT EXISTS worker_results(
  idempotency_key TEXT PRIMARY KEY, job_id TEXT NOT NULL,
  lease_id TEXT NOT NULL, verdict TEXT NOT NULL,
  envelope TEXT NOT NULL, stale INTEGER NOT NULL DEFAULT 0,
  recorded_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS worker_outbox(
  idempotency_key TEXT PRIMARY KEY, envelope TEXT NOT NULL,
  created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS machine_checks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  notice_id TEXT NOT NULL, source_revision TEXT NOT NULL,
  render_revision TEXT NOT NULL, rubric_revision TEXT NOT NULL,
  verdict TEXT NOT NULL, job_id TEXT NOT NULL,
  checked_at REAL NOT NULL,
  UNIQUE(notice_id, source_revision, render_revision, rubric_revision));
"""


def ensure_worker_tables(store) -> None:
    store.db.executescript(WORKER_DDL)
    store.db.commit()


def _now() -> float:
    return time.time()


def contract_hash() -> str:
    """Hash of the vendored envelope schema both sides agree on."""
    import hashlib
    from pathlib import Path
    schema = Path(__file__).resolve().parent.parent / "contracts" / "worker-envelope.schema.json"
    return hashlib.sha256(schema.read_bytes()).hexdigest()


def validator_hash() -> str:
    """Hash of the validator implementation + DDL + rubric revision."""
    import hashlib
    from pathlib import Path
    h = hashlib.sha256()
    h.update(Path(wc.__file__).read_bytes())
    h.update(Path(__file__).read_bytes())
    h.update(WORKER_DDL.encode())
    h.update(RUBRIC_REVISION.encode())
    return h.hexdigest()


def machine_state(store, notice_id: str) -> dict | None:
    """Latest machine check for a notice — never a human approval."""
    ensure_worker_tables(store)
    row = store.db.execute(
        "SELECT verdict, source_revision, render_revision, rubric_revision,"
        " job_id, checked_at FROM machine_checks WHERE notice_id=?"
        " ORDER BY checked_at DESC LIMIT 1", (notice_id,)).fetchone()
    return dict(row) if row else None


def check_worker_token(provided: str, expected: str) -> bool:
    if not expected or not provided:
        return False
    return secrets.compare_digest(provided, expected)


def capabilities(contract_hash: str, validator_hash: str, rubric_revision: str) -> dict:
    return {
        "contract_version": wc.CONTRACT_VERSION,
        "contract_hash": contract_hash,
        "job_kinds": list(wc.JOB_KINDS),
        "validator_hash": validator_hash,
        "rubric_revision": rubric_revision,
        "verify_lease_seconds": VERIFY_LEASE_SECONDS,
        "heartbeat_seconds": HEARTBEAT_SECONDS,
    }


def enqueue_job(store, envelope: dict) -> dict:
    """Queue a job envelope after full validation. Idempotent on re-delivery."""
    ensure_worker_tables(store)
    errors = wc.validate_envelope(envelope)
    if errors:
        return {"ok": False, "errors": errors}
    errors = wc.check_operational_use(envelope)
    if errors:
        return {"ok": False, "errors": errors}
    if envelope["job_kind"] == "verify":
        errors = wc.validate_verify_payload(envelope["payload"])
    elif envelope["job_kind"] in ("train", "validate_candidate"):
        errors = wc.validate_train_payload(envelope["payload"])
    if errors:
        return {"ok": False, "errors": errors}
    now = _now()
    cur = store.db.execute(
        "SELECT job_id FROM worker_results WHERE idempotency_key=?",
        (envelope["idempotency_key"],)).fetchone()
    if cur:
        return {"ok": True, "duplicate": True, "job_id": cur["job_id"]}
    store.db.execute(
        "INSERT OR IGNORE INTO worker_jobs(job_id, job_kind, envelope,"
        " idempotency_key, status, fencing_token, created_at, updated_at)"
        " VALUES(?,?,?,?, 'pending', ?,?,?)",
        (envelope["job_id"], envelope["job_kind"],
         __import__("json").dumps(envelope, ensure_ascii=False),
         envelope["idempotency_key"], envelope["fencing_token"],
         now, now))
    store.db.commit()
    return {"ok": True, "job_id": envelope["job_id"]}


def claim(store, worker_id: str, kinds: list[str]) -> dict | None:
    """Hand out the oldest pending job matching worker capabilities."""
    ensure_worker_tables(store)
    if not worker_id or not kinds:
        return None
    now = _now()
    row = store.db.execute(
        "SELECT job_id, job_kind, envelope, fencing_token FROM worker_jobs"
        " WHERE status='pending' AND revision_cancelled=0"
        " ORDER BY created_at LIMIT 20").fetchall()
    for cand in row:
        if cand["job_kind"] not in kinds:
            continue
        lease_seconds = (TRAIN_LEASE_SECONDS if cand["job_kind"] in
                         ("train", "validate_candidate")
                         else VERIFY_LEASE_SECONDS)
        lease_id = "lease-" + uuid.uuid4().hex[:16]
        fencing = int(cand["fencing_token"]) + 1
        store.db.execute(
            "UPDATE worker_jobs SET status='running', lease_id=?,"
            " fencing_token=?, lease_expires_at=?, worker_id=?, updated_at=?"
            " WHERE job_id=? AND status='pending'",
            (lease_id, fencing, now + lease_seconds, worker_id, now,
             cand["job_id"]))
        if store.db.total_changes:
            store.db.commit()
            import json as _json
            env = _json.loads(cand["envelope"])
            env["lease_id"] = lease_id
            env["fencing_token"] = fencing
            return env
        store.db.commit()
    return None


def heartbeat(store, job_id: str, lease_id: str) -> dict:
    ensure_worker_tables(store)
    now = _now()
    row = store.db.execute(
        "SELECT lease_id, lease_expires_at, status, job_kind"
        " FROM worker_jobs WHERE job_id=?", (job_id,)).fetchone()
    if not row or row["lease_id"] != lease_id:
        return {"ok": False, "error": "unknown lease"}
    if row["status"] != "running":
        return {"ok": False, "error": "job not running"}
    if wc.lease_expired(now, row["lease_expires_at"] or 0):
        store.db.execute("UPDATE worker_jobs SET status='pending', lease_id=NULL,"
                         " updated_at=? WHERE job_id=?", (now, job_id))
        store.db.commit()
        return {"ok": False, "error": "lease expired, re-queued"}
    extra = (TRAIN_LEASE_SECONDS if row["job_kind"] in
             ("train", "validate_candidate") else VERIFY_LEASE_SECONDS)
    store.db.execute("UPDATE worker_jobs SET lease_expires_at=?, updated_at=?"
                     " WHERE job_id=?", (now + extra, now, job_id))
    store.db.commit()
    return {"ok": True, "lease_expires_at": now + extra}


def _record_result(store, envelope: dict, verdict: str, stale: bool) -> None:
    import json as _json
    now = _now()
    store.db.execute(
        "INSERT OR IGNORE INTO worker_results(job_id, lease_id, verdict, envelope,"
        " stale, recorded_at, idempotency_key)"
        " VALUES(?,?,?,?,?,?,?)",
        (envelope["job_id"], envelope["lease_id"], verdict,
         _json.dumps(envelope, ensure_ascii=False), 1 if stale else 0, now,
         envelope["idempotency_key"]))
    store.db.execute(
        "INSERT OR IGNORE INTO worker_outbox(idempotency_key, envelope, created_at)"
        " VALUES(?,?,?)",
        (envelope["idempotency_key"], _json.dumps(envelope, ensure_ascii=False),
         now))
    store.db.commit()


def complete(store, result_env: dict, validator_hash: str) -> dict:
    """Apply a worker result. Stale/expired/mismatched results are recorded,
    never merged. Machine verdicts never become human approvals."""
    ensure_worker_tables(store)
    errors = wc.validate_envelope(result_env)
    if errors:
        return {"ok": False, "errors": errors}
    if result_env["message_type"] != "result":
        return {"ok": False, "errors": ["message_type must be result"]}
    now = _now()
    job = store.db.execute(
        "SELECT status, lease_id, fencing_token, lease_expires_at,"
        " revision_cancelled, job_kind FROM worker_jobs WHERE job_id=?",
        (result_env["job_id"],)).fetchone()
    if not job:
        return {"ok": False, "errors": ["unknown job"]}
    prior = store.db.execute(
        "SELECT verdict FROM worker_results WHERE idempotency_key=?",
        (result_env["idempotency_key"],)).fetchone()
    if prior:
        return {"ok": True, "duplicate": True, "verdict": prior["verdict"]}
    stale_reasons: list[str] = []
    if job["lease_id"] != result_env["lease_id"]:
        stale_reasons.append("lease mismatch")
    if not wc.fencing_ok(int(job["fencing_token"]), int(result_env["fencing_token"])):
        stale_reasons.append("fencing token superseded")
    if wc.lease_expired(now, job["lease_expires_at"] or 0):
        stale_reasons.append("lease expired")
    if job["revision_cancelled"]:
        stale_reasons.append("revision cancelled")
    payload = result_env["payload"]
    if not isinstance(payload, dict):
        return {"ok": False, "errors": ["result payload must be an object"]}
    if payload.get("validator_hash") != validator_hash:
        stale_reasons.append("validator hash changed under result")
    if stale_reasons:
        _record_result(store, result_env, "stale", True)
        return {"ok": False, "stale": True, "reasons": stale_reasons}
    verdict = str(payload.get("verdict", "completed"))
    if result_env["job_kind"] == "verify":
        notice_id = str(payload.get("notice_id", ""))
        if notice_id:
            store.db.execute(
                "INSERT OR IGNORE INTO machine_checks(notice_id, source_revision,"
                " render_revision, rubric_revision, verdict, job_id, checked_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (notice_id, result_env["source_revision"],
                 result_env["render_revision"], result_env["rubric_revision"],
                 verdict, result_env["job_id"], now))
    store.db.execute("UPDATE worker_jobs SET status='done', updated_at=?"
                     " WHERE job_id=?", (now, result_env["job_id"]))
    _record_result(store, result_env, verdict, False)
    store.db.commit()
    return {"ok": True, "verdict": verdict}


def fail(store, job_id: str, lease_id: str, code: str, retryable: bool) -> dict:
    ensure_worker_tables(store)
    now = _now()
    job = store.db.execute("SELECT lease_id, status FROM worker_jobs WHERE job_id=?",
                           (job_id,)).fetchone()
    if not job or job["lease_id"] != lease_id:
        return {"ok": False, "error": "unknown lease"}
    if retryable:
        store.db.execute("UPDATE worker_jobs SET status='pending', lease_id=NULL,"
                         " fencing_token=fencing_token+1, updated_at=?"
                         " WHERE job_id=?", (now, job_id))
    else:
        store.db.execute("UPDATE worker_jobs SET status='failed', updated_at=?"
                         " WHERE job_id=?", (now, job_id))
    store.db.commit()
    return {"ok": True, "status": "pending" if retryable else "failed",
            "code": code}


def cancel_revision(store, source_revision: str) -> int:
    """Cancel queued jobs for a superseded source revision."""
    ensure_worker_tables(store)
    now = _now()
    cur = store.db.execute(
        "UPDATE worker_jobs SET revision_cancelled=1, updated_at=?"
        " WHERE status IN ('pending','running') AND envelope LIKE ?",
        (now, "%" + source_revision + "%")).rowcount
    store.db.commit()
    return cur or 0
