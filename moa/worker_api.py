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

import hashlib
import json
import secrets
import time
import uuid
from . import worker_contract as wc
from .worker_handoff import RUBRIC_REVISION

VERIFY_LEASE_SECONDS = 600
TRAIN_LEASE_SECONDS = 3600
HEARTBEAT_SECONDS = 30
MAX_ARTIFACT_BYTES = 1024 * 1024

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
CREATE TABLE IF NOT EXISTS worker_job_routes(
  job_id TEXT PRIMARY KEY REFERENCES worker_jobs(job_id),
  target_worker_id TEXT, validator_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS worker_artifacts(
  artifact_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL,
  content BLOB NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS worker_job_artifacts(
  job_id TEXT NOT NULL REFERENCES worker_jobs(job_id),
  artifact_id TEXT NOT NULL REFERENCES worker_artifacts(artifact_id),
  PRIMARY KEY(job_id, artifact_id));
CREATE TABLE IF NOT EXISTS worker_result_attempts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
  envelope TEXT NOT NULL, reasons TEXT NOT NULL, recorded_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS worker_job_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
  event TEXT NOT NULL, worker_id TEXT NOT NULL, recorded_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS worker_readiness(
  worker_id TEXT PRIMARY KEY, validator_hash TEXT NOT NULL,
  ack TEXT NOT NULL, recorded_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS verification_evidence(
  job_id TEXT PRIMARY KEY, notice_id TEXT NOT NULL,
  source_revision TEXT NOT NULL, render_revision TEXT NOT NULL,
  html_sha256 TEXT NOT NULL, rule_report TEXT NOT NULL,
  browser_report TEXT NOT NULL, model_report TEXT,
  final_verdict TEXT NOT NULL DEFAULT 'needs_review', updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS pilot_documents(
  mobile_id TEXT PRIMARY KEY, revision_id TEXT NOT NULL,
  manifest TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS training_receipts(
  worker_id TEXT NOT NULL, bundle_id TEXT NOT NULL,
  receipt TEXT NOT NULL, recorded_at REAL NOT NULL,
  PRIMARY KEY(worker_id,bundle_id));
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
    # The question semantics and hard content gate are part of this validator.
    for name in ('worker_handoff.py', 'verification.py'):
        h.update(Path(__file__).with_name(name).read_bytes())
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
    from .worker_handoff import rubric_sha256, QUESTIONS_REVISION
    return {
        "contract_version": wc.CONTRACT_VERSION,
        "contract_hash": contract_hash,
        "job_kinds": list(wc.JOB_KINDS),
        "validator_hash": validator_hash,
        "rubric_revision": rubric_revision,
        "rubric_sha256": rubric_sha256(),
        "questions_revision": QUESTIONS_REVISION,
        "handoff_path": "/v1/worker/handoff",
        "readiness_path": "/v1/worker/ready",
        "auto_pass_enabled": False,
        "auto_publish_enabled": False,
        "verify_lease_seconds": VERIFY_LEASE_SECONDS,
        "heartbeat_seconds": HEARTBEAT_SECONDS,
        "canonicalization": wc.CANONICALIZATION,
        "canonicalization_spec": "json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8'); no newline; no Unicode normalization",
        "result_ack": True,
        "result_ack_mode": "inline_complete_response",
        "model_upload": False,
        "artifact_download": True,
        "max_artifact_bytes": MAX_ARTIFACT_BYTES,
        "artifact_lease_headers": ["X-MOA-Worker-ID", "X-MOA-Job-ID", "X-MOA-Lease-ID", "X-MOA-Fencing-Token"],
    }


def record_readiness(store, ack: dict) -> dict:
    from .worker_handoff import readiness_errors
    errors = readiness_errors(ack)
    if errors:
        return {'ok': False, 'errors': errors}
    ensure_worker_tables(store)
    with store.db:
        store.db.execute('INSERT INTO worker_readiness VALUES(?,?,?,?)'
                         ' ON CONFLICT(worker_id) DO UPDATE SET validator_hash=excluded.validator_hash,'
                         ' ack=excluded.ack,recorded_at=excluded.recorded_at',
                         (ack['worker_id'], validator_hash(), json.dumps(ack, ensure_ascii=False), _now()))
    return {'ok': True, 'accepted': True, 'worker_id': ack['worker_id'],
            'validator_hash': validator_hash()}


def ready(store, worker_id: str) -> bool:
    ensure_worker_tables(store)
    row = store.db.execute('SELECT validator_hash FROM worker_readiness WHERE worker_id=?',
                           (worker_id,)).fetchone()
    return bool(row and row['validator_hash'] == validator_hash())


def evidence_state(store, notice_id: str) -> list:
    ensure_worker_tables(store)
    rows = store.db.execute('SELECT job_id,source_revision,render_revision,html_sha256,'
                            'rule_report,browser_report,model_report,final_verdict FROM verification_evidence'
                            ' WHERE notice_id=? ORDER BY updated_at DESC', (notice_id,))
    return [{**dict(r), **{key: json.loads(r[key]) if r[key] else None
             for key in ('rule_report', 'browser_report', 'model_report')}} for r in rows]


def put_artifact(store, content: bytes) -> dict:
    """Immutable, bounded JSON artifact. No filesystem path supplied by workers."""
    ensure_worker_tables(store)
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_ARTIFACT_BYTES:
        raise ValueError("artifact size outside limit")
    json.loads(content.decode("utf-8"))
    digest = hashlib.sha256(content).hexdigest()
    artifact_id = "artifact-" + digest
    with store.db:
        store.db.execute(
            "INSERT OR IGNORE INTO worker_artifacts VALUES(?,?,?,?)",
            (artifact_id, digest, content, _now()))
    return {"artifact_id": artifact_id, "sha256": digest}


def _source_manifest_errors(store, env: dict) -> list[str]:
    payload = env["payload"]
    ref = payload.get("source_manifest_artifact")
    if not isinstance(ref, dict) or not isinstance(ref.get("artifact_id"), str):
        return ["source manifest artifact required"]
    row = store.db.execute("SELECT sha256, content FROM worker_artifacts WHERE artifact_id=?",
                           (ref["artifact_id"],)).fetchone()
    if not row or ref.get("sha256") != row["sha256"]:
        return ["source manifest artifact/hash unknown"]
    if len(row["content"]) > MAX_ARTIFACT_BYTES or hashlib.sha256(row["content"]).hexdigest() != ref["sha256"]:
        return ["source manifest integrity failure"]
    try:
        manifest = json.loads(row["content"])
    except (ValueError, UnicodeDecodeError):
        return ["invalid source manifest JSON"]
    expected = {b["id"]: hashlib.sha256(b["text"].encode("utf-8")).hexdigest()
                for b in payload["source_blocks"]}
    if (not isinstance(manifest, dict) or manifest.get("source_revision") != env["source_revision"] or
            manifest.get("scope") != payload["scope"] or manifest.get("complete") is not True or
            manifest.get("block_sha256") != expected):
        return ["source manifest does not cover this revision/scope/blocks"]
    return []


def enqueue_job(store, envelope: dict, *, target_worker_id: str | None = None) -> dict:
    """Queue a job envelope after full validation. Idempotent on re-delivery."""
    ensure_worker_tables(store)
    errors = wc.validate_envelope(envelope)
    if errors:
        return {"ok": False, "errors": errors}
    if envelope["message_type"] != "job":
        return {"ok": False, "errors": ["message_type must be job"]}
    errors = wc.check_operational_use(envelope)
    if errors:
        return {"ok": False, "errors": errors}
    if envelope["job_kind"] == "verify":
        errors = wc.validate_verify_payload(envelope["payload"])
        if envelope['payload'].get('require_rubric_hash') and envelope['rubric_revision'] != RUBRIC_REVISION:
            errors.append('current shared rubric required')
    elif envelope["job_kind"] in ("train", "validate_candidate"):
        errors = wc.validate_train_payload(envelope["payload"])
    if errors:
        return {"ok": False, "errors": errors}
    if envelope["job_kind"] == "verify":
        errors = _source_manifest_errors(store, envelope)
        if errors:
            return {"ok": False, "errors": errors}
    if target_worker_id is not None and (not isinstance(target_worker_id, str) or not wc.ASCII_ID.fullmatch(target_worker_id)):
        return {"ok": False, "errors": ["invalid target worker ID"]}
    now = _now()
    cur = store.db.execute(
        "SELECT job_id FROM worker_results WHERE idempotency_key=?",
        (envelope["idempotency_key"],)).fetchone()
    if cur:
        return {"ok": True, "duplicate": True, "job_id": cur["job_id"]}
    existing = store.db.execute("SELECT job_id FROM worker_jobs WHERE idempotency_key=?",
                                (envelope["idempotency_key"],)).fetchone()
    if existing:
        return {"ok": True, "duplicate": True, "job_id": existing["job_id"]}
    if store.db.execute("SELECT 1 FROM worker_jobs WHERE job_id=?", (envelope["job_id"],)).fetchone():
        return {"ok": False, "errors": ["job_id already has different input"]}
    inserted = store.db.execute(
        "INSERT OR IGNORE INTO worker_jobs(job_id, job_kind, envelope,"
        " idempotency_key, status, fencing_token, created_at, updated_at)"
        " VALUES(?,?,?,?, 'pending', ?,?,?)",
        (envelope["job_id"], envelope["job_kind"],
         __import__("json").dumps(envelope, ensure_ascii=False),
         envelope["idempotency_key"], envelope["fencing_token"],
         now, now)).rowcount
    if inserted != 1:
        store.db.commit()
        existing = store.db.execute("SELECT job_id FROM worker_jobs WHERE idempotency_key=?",
                                    (envelope["idempotency_key"],)).fetchone()
        if existing:
            return {"ok": True, "duplicate": True, "job_id": existing["job_id"]}
        return {"ok": False, "errors": ["job_id already has different input"]}
    store.db.execute("INSERT INTO worker_job_routes VALUES(?,?,?)",
                     (envelope["job_id"], target_worker_id, validator_hash()))
    if envelope["job_kind"] == "verify":
        store.db.execute("INSERT INTO worker_job_artifacts VALUES(?,?)",
                         (envelope["job_id"], envelope["payload"]["source_manifest_artifact"]["artifact_id"]))
    store.db.commit()
    return {"ok": True, "job_id": envelope["job_id"]}


def claim(store, worker_id: str, kinds: list[str], *, worker_code_sha256=None) -> dict | None:
    """Hand out the oldest pending job matching worker capabilities."""
    ensure_worker_tables(store)
    if (not isinstance(worker_id, str) or not wc.ASCII_ID.fullmatch(worker_id) or
            not isinstance(kinds, list) or not kinds or
            any(not isinstance(k, str) or k not in wc.JOB_KINDS for k in kinds)):
        return None
    now = _now()
    store.db.execute(
        "UPDATE worker_jobs SET status='pending', lease_id=NULL, updated_at=?"
        " WHERE status='running' AND lease_expires_at<=? AND revision_cancelled=0"
        " AND job_id IN (SELECT job_id FROM worker_job_routes WHERE target_worker_id IS NULL OR target_worker_id=?)",
        (now, now, worker_id))
    row = store.db.execute(
        "SELECT j.job_id, j.job_kind, j.envelope, j.fencing_token FROM worker_jobs j"
        " JOIN worker_job_routes r ON r.job_id=j.job_id"
        " WHERE j.status='pending' AND j.revision_cancelled=0"
        " AND (r.target_worker_id IS NULL OR r.target_worker_id=?)"
        " ORDER BY j.created_at LIMIT 20", (worker_id,)).fetchall()
    for cand in row:
        if cand["job_kind"] not in kinds:
            continue
        if json.loads(cand['envelope'])['payload'].get('requires_readiness') and not ready(store, worker_id):
            continue
        if json.loads(cand['envelope'])['payload'].get('requires_readiness'):
            ack = store.db.execute('SELECT ack FROM worker_readiness WHERE worker_id=?', (worker_id,)).fetchone()
            if not ack or json.loads(ack[0]).get('worker_code_sha256') != worker_code_sha256:
                continue
        lease_seconds = (TRAIN_LEASE_SECONDS if cand["job_kind"] in
                         ("train", "validate_candidate")
                         else VERIFY_LEASE_SECONDS)
        lease_id = "lease-" + uuid.uuid4().hex[:16]
        fencing = int(cand["fencing_token"]) + 1
        changed = store.db.execute(
            "UPDATE worker_jobs SET status='running', lease_id=?,"
            " fencing_token=?, lease_expires_at=?, worker_id=?, updated_at=?"
            " WHERE job_id=? AND status='pending'",
            (lease_id, fencing, now + lease_seconds, worker_id, now,
             cand["job_id"])).rowcount
        if changed == 1:
            store.db.execute("INSERT INTO worker_job_events(job_id,event,worker_id,recorded_at) VALUES(?,?,?,?)",
                             (cand["job_id"], "claim", worker_id, now))
            store.db.commit()
            import json as _json
            env = _json.loads(cand["envelope"])
            env["lease_id"] = lease_id
            env["fencing_token"] = fencing
            return env
        store.db.commit()
    store.db.commit()
    return None


def _lease_errors(job, lease_id, fencing_token, worker_id, now) -> list[str]:
    if not job:
        return ["unknown job"]
    errors = []
    if job["status"] != "running" or job["revision_cancelled"]:
        errors.append("job not running or revision cancelled")
    if job["lease_id"] != lease_id or job["worker_id"] != worker_id:
        errors.append("lease or worker mismatch")
    if type(fencing_token) is not int or not wc.fencing_ok(job["fencing_token"], fencing_token):
        errors.append("fencing token mismatch")
    if wc.lease_expired(now, job["lease_expires_at"] or 0):
        errors.append("lease expired")
    return errors


def get_artifact(store, artifact_id: str, *, job_id: str, lease_id: str,
                 fencing_token: int, worker_id: str) -> tuple[int, bytes | dict]:
    """Only an active owning lease may fetch an artifact referenced by its job."""
    ensure_worker_tables(store)
    if not isinstance(artifact_id, str) or not wc.ASCII_ID.fullmatch(artifact_id):
        return 404, {"ok": False, "error": "unknown artifact"}
    job = store.db.execute("SELECT * FROM worker_jobs WHERE job_id=?", (job_id,)).fetchone()
    if _lease_errors(job, lease_id, fencing_token, worker_id, _now()):
        return 409, {"ok": False, "error": "active owning lease required"}
    row = store.db.execute(
        "SELECT a.content,a.sha256 FROM worker_artifacts a"
        " JOIN worker_job_artifacts r ON r.artifact_id=a.artifact_id"
        " WHERE r.job_id=? AND a.artifact_id=?", (job_id, artifact_id)).fetchone()
    if not row:
        return 404, {"ok": False, "error": "unknown artifact"}
    content = row["content"]
    if len(content) > MAX_ARTIFACT_BYTES or hashlib.sha256(content).hexdigest() != row["sha256"]:
        return 409, {"ok": False, "error": "artifact integrity failure"}
    return 200, content


def heartbeat(store, job_id: str, lease_id: str, *, fencing_token=None, worker_id=None) -> dict:
    ensure_worker_tables(store)
    now = _now()
    row = store.db.execute(
        "SELECT * FROM worker_jobs WHERE job_id=?", (job_id,)).fetchone()
    errors = _lease_errors(row, lease_id, fencing_token, worker_id, now)
    if errors:
        # A stale caller cannot change the current lease. Expiration recovery
        # is also done on the next claim if the owner has gone offline.
        return {"ok": False, "error": "; ".join(errors)}
    extra = (TRAIN_LEASE_SECONDS if row["job_kind"] in
             ("train", "validate_candidate") else VERIFY_LEASE_SECONDS)
    store.db.execute("UPDATE worker_jobs SET lease_expires_at=?, updated_at=?"
                     " WHERE job_id=?", (now + extra, now, job_id))
    store.db.execute("INSERT INTO worker_job_events(job_id,event,worker_id,recorded_at) VALUES(?,?,?,?)",
                     (job_id, "heartbeat", worker_id, now))
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


def complete(store, result_env: dict, validator_hash: str, *, worker_id=None) -> dict:
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
        "SELECT * FROM worker_jobs WHERE job_id=?",
        (result_env["job_id"],)).fetchone()
    if not job:
        return {"ok": False, "errors": ["unknown job"]}
    original = json.loads(job["envelope"])
    for key in ("job_kind", "input_sha256", "idempotency_key", "source_revision", "render_revision", "rubric_revision"):
        if result_env[key] != original[key]:
            errors.append("result does not echo job %s" % key)
    if wc.check_operational_use(result_env):
        errors.append("example_only result refused")
    if errors:
        return {"ok": False, "errors": errors}
    prior = store.db.execute(
        "SELECT verdict,envelope,stale FROM worker_results WHERE idempotency_key=?",
        (result_env["idempotency_key"],)).fetchone()
    if prior:
        if (not prior["stale"] and job["worker_id"] == worker_id and
                wc.canonical(json.loads(prior["envelope"])) == wc.canonical(result_env)):
            return _ack(result_env, prior["verdict"], duplicate=True)
        return {"ok": False, "stale": True, "reasons": ["conflicting result already recorded"]}
    stale_reasons = _lease_errors(job, result_env["lease_id"], result_env["fencing_token"], worker_id, now)
    if original['payload'].get('requires_readiness') and original['payload'].get('notice_id'):
        current = store.db.execute('SELECT latest_revision FROM mobile_notices WHERE id=?',
                                  (original['payload']['notice_id'],)).fetchone()
        if not current or current[0] != result_env['render_revision']:
            stale_reasons.append('notice revision changed under result')
    payload = result_env["payload"]
    if not isinstance(payload, dict):
        return {"ok": False, "errors": ["result payload must be an object"]}
    if payload.get("validator_hash") != validator_hash:
        stale_reasons.append("validator hash changed under result")
    route = store.db.execute("SELECT validator_hash FROM worker_job_routes WHERE job_id=?",
                             (result_env["job_id"],)).fetchone()
    if not route or route["validator_hash"] != validator_hash:
        stale_reasons.append("job validator revision no longer current")
    if stale_reasons:
        # Rejected attempts cannot occupy the accepted-result idempotency key.
        with store.db:
            store.db.execute("INSERT INTO worker_result_attempts(job_id,envelope,reasons,recorded_at) VALUES(?,?,?,?)",
                             (result_env["job_id"], json.dumps(result_env, ensure_ascii=False), json.dumps(stale_reasons), now))
        return {"ok": False, "stale": True, "reasons": stale_reasons}
    verdict = str(payload.get("status", "error"))
    if result_env["job_kind"] == "verify":
        errors = _source_manifest_errors(store, original) + wc.validate_verify_result(payload, original["payload"])
        if errors:
            return {"ok": False, "errors": errors}
        if original['payload'].get('require_rubric_hash'):
            from .worker_handoff import rubric_sha256
            if payload.get('rubric_sha256') != rubric_sha256():
                return {'ok': False, 'errors': ['actual model question definitions mismatch']}
        if payload["status"] == "completed":
            verdict = "machine_passed" if all(a["label"] == "preserved" and a["calibrated"] for a in payload["answers"].values()) else "needs_review"
        # The input job, never worker output, owns the notice association.
        notice_id = str(original["payload"].get("notice_id", ""))
        if notice_id:
            # Trial policy: no real document auto-passes, including calibrated outputs.
            verdict = 'needs_review'
            store.db.execute(
                "INSERT OR IGNORE INTO machine_checks(notice_id, source_revision,"
                " render_revision, rubric_revision, verdict, job_id, checked_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (notice_id, result_env["source_revision"],
                 result_env["render_revision"], result_env["rubric_revision"],
                 verdict, result_env["job_id"], now))
        with store.db:
            store.db.execute('UPDATE verification_evidence SET model_report=?,final_verdict=?,updated_at=?'
                             ' WHERE job_id=? AND source_revision=? AND render_revision=?',
                             (json.dumps(payload, ensure_ascii=False), 'needs_review', now,
                              result_env['job_id'], result_env['source_revision'], result_env['render_revision']))
    store.db.execute("UPDATE worker_jobs SET status='done', updated_at=?"
                     " WHERE job_id=?", (now, result_env["job_id"]))
    store.db.execute("INSERT INTO worker_job_events(job_id,event,worker_id,recorded_at) VALUES(?,?,?,?)",
                     (result_env["job_id"], "complete", worker_id, now))
    _record_result(store, result_env, verdict, False)
    store.db.commit()
    return _ack(result_env, verdict)


def _ack(env: dict, verdict: str, duplicate: bool = False) -> dict:
    return {"ok": True, "accepted": True, "duplicate": duplicate,
            "job_id": env["job_id"], "idempotency_key": env["idempotency_key"],
            "input_sha256": env["input_sha256"], "verdict": verdict}


def fail(store, job_id: str, lease_id: str, code: str, retryable: bool, *, fencing_token=None, worker_id=None) -> dict:
    ensure_worker_tables(store)
    now = _now()
    job = store.db.execute("SELECT * FROM worker_jobs WHERE job_id=?",
                           (job_id,)).fetchone()
    errors = _lease_errors(job, lease_id, fencing_token, worker_id, now)
    if errors:
        return {"ok": False, "error": "; ".join(errors)}
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
