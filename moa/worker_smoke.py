"""Publish exactly one idempotent synthetic verify; never run a model or forge results.

NAS: python -m moa.worker_smoke --data-dir /data --publish
Read-only status: python -m moa.worker_smoke --data-dir /data
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from . import worker_api as api, worker_contract as wc
from .core import Store

EXPECTED_CONTRACT_HASH = "a3b085c9b48e72418c6737f3da4f4d61a5d0bbba59a006be2e582271fb6da1fb"
JOB_ID = "job-synthetic-verify-20261018-01"
WORKER_ID = "macbook-laya-01"
SOURCE_REVISION = "synthetic-deadline-source-v1"
RENDER_REVISION = "synthetic-deadline-render-v1"


def publish(store) -> dict:
    if api.contract_hash() != EXPECTED_CONTRACT_HASH:
        raise ValueError("contract hash mismatch; publication stopped")
    source = [{"id": "b1", "text": "합성 행사 신청 마감은 2026년 10월 30일 오후 5시입니다."}]
    manifest = {"source_revision": SOURCE_REVISION, "scope": "section", "complete": True,
                "block_sha256": {b["id"]: hashlib.sha256(b["text"].encode("utf-8")).hexdigest() for b in source}}
    artifact = api.put_artifact(store, wc.canonical(manifest))
    payload = {
        "scope": "section", "file_role": "notice", "source_blocks": source,
        "rendered_blocks": [{"id": "r1", "source_refs": ["b1"],
                             "visible_text": "합성 행사 신청 마감은 2026년 10월 31일 오후 5시입니다."}],
        "questions": ["conditions"], "required_context_ids": ["b1"],
        "coverage_manifest": {"expected_blocks": 1, "provided_blocks": 1},
        "model_policy": {"checkpoint": "current", "max_tokens": 1024, "vision": False},
        "deterministic_findings": [], "source_manifest_artifact": artifact,
    }
    digest = wc.input_sha256("verify", payload, SOURCE_REVISION, RENDER_REVISION, api.RUBRIC_REVISION)
    env = {
        "contract_version": wc.CONTRACT_VERSION, "message_type": "job", "job_id": JOB_ID,
        "job_kind": "verify", "idempotency_key": digest, "lease_id": "unclaimed", "fencing_token": 1,
        "input_sha256": digest, "source_revision": SOURCE_REVISION, "render_revision": RENDER_REVISION,
        "rubric_revision": api.RUBRIC_REVISION, "payload": payload,
    }
    result = api.enqueue_job(store, env, target_worker_id=WORKER_ID)
    if not result.get("ok"):
        raise ValueError("synthetic publication refused: " + "; ".join(result.get("errors", [])))
    return {**result, "artifact_id": artifact["artifact_id"], "target_worker_id": WORKER_ID,
            "verify_lease_seconds": api.VERIFY_LEASE_SECONDS, "heartbeat_seconds": api.HEARTBEAT_SECONDS}


def status(root: Path) -> dict:
    # No Store constructor, migrations, or writes during status checks.
    with sqlite3.connect((root / "db/moa.sqlite3").as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='worker_jobs'").fetchone():
            return {"published": False, "job_id": JOB_ID}
        row = db.execute("SELECT job_id,status,worker_id,fencing_token,lease_expires_at FROM worker_jobs WHERE job_id=?", (JOB_ID,)).fetchone()
        if not row:
            return {"published": False, "job_id": JOB_ID}
        events = [dict(r) for r in db.execute(
            "SELECT event,worker_id,recorded_at FROM worker_job_events WHERE job_id=? ORDER BY id", (JOB_ID,))]
        result = db.execute("SELECT verdict,stale FROM worker_results WHERE job_id=?", (JOB_ID,)).fetchone()
        return {"published": True, **dict(row), "events": events, "result": dict(result) if result else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    root = args.data_dir.resolve()
    if not (root / "db/moa.sqlite3").is_file():
        parser.error("existing MOA DB required; refusing to create an empty data directory")
    if args.publish:
        with Store(root) as store:
            print(json.dumps(publish(store)))
    else:
        print(json.dumps(status(root)))


if __name__ == "__main__":
    main()
