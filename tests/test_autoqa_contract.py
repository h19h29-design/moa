"""Worker contract + relay + AI config tests (2026-10-12 design, contract v1).

Synthetic fixtures only — no real notices, approvals, or network calls.
"""
import json
import tempfile
from pathlib import Path
from moa import worker_api, worker_contract as wc
from moa.ai_config import (DEFAULTS, NEEDS_PROVIDER_CONFIGURATION,
                           assert_no_paid, load_ai_config, provider_status)
from moa.core import Store

VERIFY_PAYLOAD = {
    "scope": "section",
    "source_complete": True,
    "source_evidence_verified": True,
    "file_role": "notice",
    "source_blocks": [{"id": "b1", "kind": "paragraph", "text": "학과 2개를 선택합니다."}],
    "rendered_blocks": [{"id": "r1", "source_refs": ["b1"],
                         "visible_text": "학과 2개를 선택합니다."}],
    "questions": ["omission", "conditions", "table_relations", "attachment_role"],
    "required_context_ids": ["b1"],
    "deterministic_findings": [],
    "coverage_manifest": {"expected_blocks": 1, "provided_blocks": 1},
    "model_policy": {"checkpoint": "current", "max_tokens": 1024, "vision": False},
}
TRAIN_PAYLOAD = {
    "dataset_id": "synth-001",
    "dataset_sha256": "0" * 64,
    "base_checkpoint_sha256": "1" * 64,
    "source_groups": ["g1", "g2"],
    "splits": {"train": ["g1"], "calibration": ["g2"]},
    "label_origin": ["human_independent", "deterministic_oracle"],
    "forbidden_lineage": [],
}


def make_env(kind="verify", payload=None, **over):
    payload = payload if payload is not None else dict(VERIFY_PAYLOAD)
    env = {
        "contract_version": wc.CONTRACT_VERSION,
        "message_type": "job",
        "job_id": "job-synth-1",
        "job_kind": kind,
        "idempotency_key": wc.make_idempotency_key(
            kind, payload, "src-r1", "ren-r1", "rub-r1"),
        "lease_id": "lease-0",
        "fencing_token": 1,
        "input_sha256": wc.input_sha256(kind, payload, "src-r1", "ren-r1", "rub-r1"),
        "source_revision": "src-r1",
        "render_revision": "ren-r1",
        "rubric_revision": "rub-r1",
        "payload": payload,
    }
    env.update(over)
    return env


def test_canonical_hash_stable_across_key_order_unicode_numbers_empty():
    a = {"b": [1, 2.0, "한글", None, True, {}], "a": {"x": "", "y": []}}
    b = {"a": {"y": [], "x": ""}, "b": [1, 2.0, "한글", None, True, {}]}
    assert wc.canonical(a) == wc.canonical(b)
    assert wc.input_sha256("verify", a, "s", "r", "u") == wc.input_sha256(
        "verify", b, "s", "r", "u")
    k1 = wc.make_idempotency_key("verify", a, "s", "r", "u")
    k2 = wc.make_idempotency_key("verify", a, "s", "r", "u")
    assert k1 == k2 and len(k1) == 64


def test_valid_envelopes_accepted():
    assert wc.validate_envelope(make_env()) == []
    assert wc.validate_envelope(make_env("train", dict(TRAIN_PAYLOAD))) == []


def test_envelope_rejections():
    assert any("fencing_token" in e for e in
               wc.validate_envelope(make_env(fencing_token=0)))
    missing = make_env()
    del missing["input_sha256"]
    assert any("missing field" in e for e in wc.validate_envelope(missing))
    assert any("job_kind" in e for e in wc.validate_envelope(make_env(kind="translate")))
    assert any("additional top-level" in e for e in
               wc.validate_envelope(make_env(extra_field=1)))
    tampered = make_env()
    tampered["payload"] = dict(VERIFY_PAYLOAD, scope="other")
    assert any("input_sha256" in e for e in wc.validate_envelope(tampered))


def test_example_only_refused_for_ops():
    assert wc.check_operational_use(make_env(example_only=True)) != []
    assert wc.check_operational_use(make_env()) == []


def test_verify_payload_guards():
    assert wc.validate_verify_payload(dict(VERIFY_PAYLOAD)) == []
    bad = dict(VERIFY_PAYLOAD, source_blocks=[], rendered_blocks=[])
    assert wc.validate_verify_payload(bad) != []
    short = dict(VERIFY_PAYLOAD,
                 coverage_manifest={"expected_blocks": 3, "provided_blocks": 1})
    assert any("shortfall" in e for e in wc.validate_verify_payload(short))


def test_train_lineage_fail_closed():
    assert wc.validate_train_payload(dict(TRAIN_PAYLOAD)) == []
    for origin in ("jev_output", "jev_label", "jev_selected", "laya_self_label"):
        bad = dict(TRAIN_PAYLOAD, label_origin=[origin])
        assert any("forbidden" in e for e in wc.validate_train_payload(bad)), origin
    assert any("muse" in e for e in wc.validate_train_payload(
        dict(TRAIN_PAYLOAD, muse_derived=True)))
    assert any("eval_rows" in e for e in wc.validate_train_payload(
        dict(TRAIN_PAYLOAD, eval_rows=["e1"])))
    assert any("sealed_audit" in e for e in wc.validate_train_payload(
        dict(TRAIN_PAYLOAD, sealed_audit_rows=["a1"])))
    assert any("label_origin" in e for e in wc.validate_train_payload(
        dict(TRAIN_PAYLOAD, label_origin=[])))


def test_lease_and_fencing_logic():
    assert wc.fencing_ok(3, 3) and wc.fencing_ok(3, 4) and not wc.fencing_ok(3, 2)
    assert wc.lease_expired(100.0, 90.0) and not wc.lease_expired(80.0, 90.0)


def _store():
    tmp = Path(tempfile.mkdtemp(prefix="moa-autoqa-"))
    return Store(tmp)


def _claimed_job(store):
    assert worker_api.enqueue_job(store, make_env())["ok"]
    job = worker_api.claim(store, "macbook-laya-01", ["verify"])
    assert job and job["lease_id"].startswith("lease-")
    return job


def _result_for(job, verdict="completed"):
    payload = {"verdict": verdict, "validator_hash": worker_api.validator_hash(),
               "notice_id": "notice-synth-1"}
    return {
        "contract_version": wc.CONTRACT_VERSION,
        "message_type": "result",
        "job_id": job["job_id"],
        "job_kind": job["job_kind"],
        "idempotency_key": wc.make_idempotency_key(
            job["job_kind"], payload, job["source_revision"],
            job["render_revision"], job["rubric_revision"]),
        "lease_id": job["lease_id"],
        "fencing_token": job["fencing_token"],
        "input_sha256": wc.input_sha256(
            job["job_kind"], payload, job["source_revision"],
            job["render_revision"], job["rubric_revision"]),
        "source_revision": job["source_revision"],
        "render_revision": job["render_revision"],
        "rubric_revision": job["rubric_revision"],
        "payload": payload,
    }


def test_claim_heartbeat_complete_records_machine_not_human():
    store = _store()
    job = _claimed_job(store)
    hb = worker_api.heartbeat(store, job["job_id"], job["lease_id"])
    assert hb["ok"]
    res = worker_api.complete(store, _result_for(job), worker_api.validator_hash())
    assert res["ok"] and res["verdict"] == "completed"
    machine = worker_api.machine_state(store, "notice-synth-1")
    assert machine and machine["verdict"] == "completed"
    # Human approval surfaces untouched by worker completion.
    assert store.db.execute("SELECT count(*) FROM mobile_reviews").fetchone()[0] == 0
    assert store.db.execute(
        "SELECT count(*) FROM mobile_notices WHERE approved_revision IS NOT NULL"
    ).fetchone()[0] == 0


def test_duplicate_complete_is_idempotent():
    store = _store()
    job = _claimed_job(store)
    first = worker_api.complete(store, _result_for(job), worker_api.validator_hash())
    second = worker_api.complete(store, _result_for(job), worker_api.validator_hash())
    assert first["ok"] and second.get("duplicate")
    assert store.db.execute("SELECT count(*) FROM machine_checks").fetchone()[0] == 1


def test_wrong_lease_result_is_stale_and_unmerged():
    store = _store()
    job = _claimed_job(store)
    bad = _result_for(job)
    bad["lease_id"] = "lease-stale"
    # Re-hash so the envelope itself stays valid; only the lease is wrong.
    bad["input_sha256"] = wc.input_sha256(
        bad["job_kind"], bad["payload"], bad["source_revision"],
        bad["render_revision"], bad["rubric_revision"])
    bad["idempotency_key"] = bad["input_sha256"]
    res = worker_api.complete(store, bad, worker_api.validator_hash())
    assert res.get("stale")
    assert store.db.execute("SELECT count(*) FROM machine_checks").fetchone()[0] == 0


def test_expired_lease_requeues_on_heartbeat():
    store = _store()
    job = _claimed_job(store)
    store.db.execute("UPDATE worker_jobs SET lease_expires_at=1 WHERE job_id=?",
                     (job["job_id"],))
    store.db.commit()
    res = worker_api.heartbeat(store, job["job_id"], job["lease_id"])
    assert not res["ok"] and "expired" in res["error"]


def test_fail_retryable_bumps_fencing():
    store = _store()
    job = _claimed_job(store)
    before = store.db.execute("SELECT fencing_token FROM worker_jobs WHERE job_id=?",
                              (job["job_id"],)).fetchone()[0]
    res = worker_api.fail(store, job["job_id"], job["lease_id"], "OOM", True)
    after = store.db.execute("SELECT fencing_token, status FROM worker_jobs WHERE job_id=?",
                             (job["job_id"],)).fetchone()
    assert res["ok"] and after["status"] == "pending" and after["fencing_token"] > before


def test_validator_change_rejects_merge():
    store = _store()
    job = _claimed_job(store)
    res = worker_api.complete(store, _result_for(job), "deadbeef" * 8)
    assert res.get("stale")
    assert store.db.execute("SELECT count(*) FROM machine_checks").fetchone()[0] == 0


def test_ai_config_cash_zero_and_no_paid_fallback():
    config, _ = load_ai_config("/nonexistent-moa-ai.yaml")
    assert config["cash_budget_usd"] == 0
    assert_no_paid(config)
    assert config["providers"]["jev"]["training_reuse"] == "prohibited"
    states = provider_status(config, env={})
    assert states["muse"] == NEEDS_PROVIDER_CONFIGURATION
    assert states["jev"] == NEEDS_PROVIDER_CONFIGURATION
    forced, warnings = load_ai_config("/nonexistent-moa-ai.yaml")
    forced["cash_budget_usd"] = 100
    forced["providers"]["jev"]["paid_fallback"] = True
    try:
        assert_no_paid(forced)
    except ValueError:
        pass
    else:
        raise AssertionError("paid config must raise")
    assert DEFAULTS["cash_budget_usd"] == 0


def test_contract_hash_matches_vendored_schema():
    import hashlib
    schema = Path(worker_api.__file__).resolve().parent.parent / "contracts" / "worker-envelope.schema.json"
    assert worker_api.contract_hash() == hashlib.sha256(schema.read_bytes()).hexdigest()
