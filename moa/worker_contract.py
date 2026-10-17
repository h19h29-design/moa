"""Mac mini <-> MacBook worker contract (moa.worker.v1).

Implements `04_WORKER_CONTRACT.md` + `worker-envelope.schema.json` as code.
The NAS application is the single source of truth; this module is pure
validation/canonicalisation with no I/O so both sides share the exact
same bytes-to-hash behaviour.

Canonical JSON rule (both languages must match): UTF-8, keys sorted,
no whitespace separators, `ensure_ascii=False`. Hash target is the input
object {job_kind, payload, source_revision, render_revision,
rubric_revision} — lease/fencing/idempotency are verified separately
and are NOT part of the hash.
"""
from __future__ import annotations

import hashlib
import json
import re

CONTRACT_VERSION = "moa.worker.v1"
JOB_KINDS = ("verify", "train", "validate_candidate")
MESSAGE_TYPES = ("job", "result")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_TOP_FIELDS = (
    "contract_version", "message_type", "job_id", "job_kind",
    "idempotency_key", "lease_id", "fencing_token", "input_sha256",
    "source_revision", "render_revision", "rubric_revision", "payload",
)
ALLOWED_TOP_FIELDS = set(REQUIRED_TOP_FIELDS) | {"example_only"}

# Training lineage that must never enter train payloads (fail-closed).
FORBIDDEN_LINEAGE = frozenset({
    "jev_output", "jev_label", "jev_selected", "laya_self_label",
})
ALLOWED_LABEL_ORIGINS = frozenset({"human_independent", "deterministic_oracle"})


def canonical(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def input_object(job_kind: str, payload: dict, source_revision: str,
                 render_revision: str, rubric_revision: str) -> dict:
    return {
        "job_kind": job_kind,
        "payload": payload,
        "source_revision": source_revision,
        "render_revision": render_revision,
        "rubric_revision": rubric_revision,
    }


def input_sha256(job_kind: str, payload: dict, source_revision: str,
                 render_revision: str, rubric_revision: str) -> str:
    return hashlib.sha256(canonical(input_object(
        job_kind, payload, source_revision, render_revision,
        rubric_revision))).hexdigest()


def make_idempotency_key(job_kind: str, payload: dict, source_revision: str,
                         render_revision: str, rubric_revision: str) -> str:
    """Idempotency key = sha256 of the same canonical input object."""
    return input_sha256(job_kind, payload, source_revision,
                        render_revision, rubric_revision)


def validate_envelope(env: dict) -> list[str]:
    """Validate the envelope only. Returns error list (empty = valid)."""
    errors: list[str] = []
    if not isinstance(env, dict):
        return ["envelope must be an object"]
    for field in REQUIRED_TOP_FIELDS:
        if field not in env:
            errors.append("missing field: %s" % field)
    for field in env:
        if field not in ALLOWED_TOP_FIELDS:
            errors.append("additional top-level field forbidden: %s" % field)
    if errors:
        return errors
    if env["contract_version"] != CONTRACT_VERSION:
        errors.append("contract_version must be %s" % CONTRACT_VERSION)
    if env["message_type"] not in MESSAGE_TYPES:
        errors.append("bad message_type")
    if env["job_kind"] not in JOB_KINDS:
        errors.append("job_kind must be one of %s" % "|".join(JOB_KINDS))
    for key in ("job_id", "lease_id", "source_revision", "render_revision",
                "rubric_revision"):
        value = env[key]
        if not isinstance(value, str) or not (1 <= len(value) <= 200):
            errors.append("%s must be a 1..200 char string" % key)
    for key in ("idempotency_key", "input_sha256"):
        value = env[key]
        if not isinstance(value, str) or not HEX64.match(value):
            errors.append("%s must be lowercase hex sha256" % key)
    token = env["fencing_token"]
    if not isinstance(token, int) or isinstance(token, bool) or token < 1:
        errors.append("fencing_token must be an integer >= 1")
    if not isinstance(env["payload"], dict):
        errors.append("payload must be an object")
    if "example_only" in env and not isinstance(env["example_only"], bool):
        errors.append("example_only must be boolean")
    if not errors:
        expect = input_sha256(env["job_kind"], env["payload"],
                              env["source_revision"], env["render_revision"],
                              env["rubric_revision"])
        if env["input_sha256"] != expect:
            errors.append("input_sha256 does not match canonical input")
        if env["idempotency_key"] != expect:
            errors.append("idempotency_key does not match canonical input")
    return errors


def check_operational_use(env: dict) -> list[str]:
    """Operational guards: example fixtures never enter the ops path."""
    if env.get("example_only"):
        return ["example_only input refused for operational use"]
    return []


def validate_verify_payload(payload: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["verify payload must be an object"]
    blocks = payload.get("source_blocks")
    rendered = payload.get("rendered_blocks")
    if not isinstance(blocks, list) or not blocks:
        errors.append("source_blocks must be a non-empty list")
    if not isinstance(rendered, list) or not rendered:
        errors.append("rendered_blocks must be a non-empty list")
    manifest = payload.get("coverage_manifest", {})
    if not isinstance(manifest, dict):
        errors.append("coverage_manifest must be an object")
    else:
        expected = manifest.get("expected_blocks")
        provided = manifest.get("provided_blocks")
        if not isinstance(expected, int) or not isinstance(provided, int):
            errors.append("coverage_manifest needs integer expected/provided_blocks")
        elif provided < expected:
            errors.append("coverage shortfall: %d provided < %d expected"
                          % (provided, expected))
    policy = payload.get("model_policy", {})
    if not isinstance(policy, dict):
        errors.append("model_policy must be an object")
    return errors


def validate_train_payload(payload: dict) -> list[str]:
    """Fail-closed training lineage gate."""
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["train payload must be an object"]
    lineage = payload.get("label_origin", [])
    if not isinstance(lineage, list) or not lineage:
        errors.append("label_origin must be a non-empty list")
    else:
        for origin in lineage:
            if origin in FORBIDDEN_LINEAGE:
                errors.append("forbidden lineage refused: %s" % origin)
            elif origin not in ALLOWED_LABEL_ORIGINS:
                errors.append("unknown label_origin refused: %s" % origin)
    forbidden = payload.get("forbidden_lineage", [])
    if isinstance(forbidden, list) and forbidden:
        errors.append("payload carries forbidden_lineage: %s" % sorted(
            str(x) for x in forbidden))
    if payload.get("muse_derived") and not payload.get(
            "muse_training_permission"):
        errors.append("muse-derived data refused without explicit permission")
    groups = payload.get("source_groups", [])
    if not isinstance(groups, list) or not groups:
        errors.append("source_groups must be a non-empty list")
    splits = payload.get("splits", {})
    if not isinstance(splits, dict) or not splits:
        errors.append("splits manifest missing")
    for forbidden_key in ("eval_rows", "sealed_audit_rows", "jev_log_refs"):
        if payload.get(forbidden_key):
            errors.append("%s must not enter training" % forbidden_key)
    return errors


def fencing_ok(stored_token: int, incoming_token: int) -> bool:
    """A re-lease mints a strictly greater token; replays lose."""
    return incoming_token >= stored_token


def lease_expired(server_now_ts: float, lease_expires_at_ts: float) -> bool:
    """Server-clock comparison only — client clocks never decide."""
    return server_now_ts > lease_expires_at_ts
