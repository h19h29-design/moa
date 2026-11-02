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
import math
import re

CONTRACT_VERSION = "moa.worker.v1"
JOB_KINDS = ("verify", "train", "validate_candidate")
MESSAGE_TYPES = ("job", "result")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ASCII_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,199}$")
QUESTIONS = frozenset({"omission", "addition", "conditions", "table_relations", "attachment_role"})
CANONICALIZATION = "python-json-sort-keys-utf8-v1"
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
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


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
        try:
            canonical(env)
        except (ValueError, TypeError, UnicodeEncodeError):
            return ["envelope must be finite UTF-8 JSON"]
    if not errors and env["message_type"] == "job":
        try:
            expect = input_sha256(env["job_kind"], env["payload"],
                                  env["source_revision"], env["render_revision"],
                                  env["rubric_revision"])
        except (ValueError, TypeError):
            return ["payload must be finite JSON"]
        if env["input_sha256"] != expect:
            errors.append("input_sha256 does not match canonical input")
        if env["idempotency_key"] != expect:
            errors.append("idempotency_key does not match canonical input")
    # A result echoes the JOB hashes; its own payload is different. The NAS
    # compares these fields to the persisted job before accepting the result.
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
    if errors:
        return errors
    ids = [b.get("id") if isinstance(b, dict) else None for b in blocks]
    if any(not isinstance(i, str) or not ASCII_ID.fullmatch(i) for i in ids):
        return ["source block IDs must be ASCII identifiers"]
    if len(set(ids)) != len(ids) or any(not isinstance(b.get("text"), str) for b in blocks):
        errors.append("source blocks need unique IDs and text")
    render_ids = set()
    for b in rendered:
        if not isinstance(b, dict) or not isinstance(b.get("id"), str) or not ASCII_ID.fullmatch(b["id"]):
            errors.append("render block IDs must be ASCII identifiers")
            continue
        if b["id"] in render_ids:
            errors.append("duplicate render ID")
        render_ids.add(b["id"])
        refs = b.get("source_refs")
        if (not isinstance(refs, list) or not refs or
                any(not isinstance(i, str) or i not in ids for i in refs) or
                not isinstance(b.get("visible_text"), str)):
            errors.append("render block needs known source_refs and visible_text")
    questions = payload.get("questions")
    if (not isinstance(questions, list) or not questions or
            any(not isinstance(q, str) or q not in QUESTIONS for q in questions) or
            len(questions) != len(set(questions))):
        errors.append("questions must be a non-empty subset of supported questions")
    context = payload.get("required_context_ids")
    if not isinstance(context, list) or any(not isinstance(i, str) or i not in ids for i in context):
        errors.append("required_context_ids must reference provided source IDs")
    if payload.get("scope") != "section" or payload.get("file_role") != "notice":
        errors.append("verify currently supports section scope and notice role")
    manifest = payload.get("coverage_manifest", {})
    if not isinstance(manifest, dict):
        errors.append("coverage_manifest must be an object")
    else:
        expected = manifest.get("expected_blocks")
        provided = manifest.get("provided_blocks")
        if type(expected) is not int or type(provided) is not int or expected < 1:
            errors.append("coverage_manifest needs integer expected/provided_blocks")
        elif provided < expected:
            errors.append("coverage shortfall: %d provided < %d expected"
                          % (provided, expected))
        elif expected != len(blocks) or provided != len(blocks):
            errors.append("coverage counts must match supplied source blocks")
    policy = payload.get("model_policy", {})
    if not isinstance(policy, dict):
        errors.append("model_policy must be an object")
    return errors


def validate_verify_result(payload: dict, job_payload: dict) -> list[str]:
    """Check model evidence without treating task completion as approval."""
    errors = []
    status = payload.get("status")
    if status not in ("completed", "deferred", "error"):
        return ["result status must be completed, deferred or error"]
    if status != "completed":
        return []
    answers = payload.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(job_payload["questions"]):
        return ["answers must cover exactly the requested questions"]
    labels = {"preserved", "changed", "insufficient"}
    for answer in answers.values():
        if (not isinstance(answer, dict) or not isinstance(answer.get("label"), str) or
                answer["label"] not in labels):
            errors.append("invalid answer label")
            continue
        for key in ("raw_probabilities", "calibrated_probabilities"):
            dist = answer.get(key)
            if key == "calibrated_probabilities" and dist is None:
                continue
            if (not isinstance(dist, dict) or set(dist) != labels or
                    any(type(x) not in (int, float) or not math.isfinite(x) or not 0 <= x <= 1 for x in dist.values()) or
                    abs(sum(dist.values()) - 1) > 1e-6):
                errors.append("invalid probability distribution")
        if type(answer.get("calibrated")) is not bool:
            errors.append("calibrated must be boolean")
        elif answer["calibrated"] and answer.get("calibrated_probabilities") is None:
            errors.append("calibrated distribution missing")
    for key, blocks in (("inspected_source_ids", "source_blocks"), ("inspected_render_ids", "rendered_blocks")):
        inspected = payload.get(key)
        expected = {b["id"] for b in job_payload[blocks]}
        if (not isinstance(inspected, list) or any(not isinstance(i, str) for i in inspected) or
                set(inspected) != expected):
            errors.append("%s must cover the supplied blocks" % key)
    if payload.get("truncation") is not False or payload.get("missing_context_ids") != []:
        errors.append("truncated or missing-context results must be deferred")
    if type(payload.get("actual_tokens")) is not int or payload["actual_tokens"] < 1:
        errors.append("actual_tokens must be a positive integer")
    if not isinstance(payload.get("checkpoint_sha256"), str) or not HEX64.fullmatch(payload["checkpoint_sha256"]):
        errors.append("checkpoint_sha256 missing or invalid")
    for key in ("model_revision", "tokenizer_revision"):
        if not isinstance(payload.get(key), str) or not payload[key]:
            errors.append("%s missing" % key)
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
    return incoming_token == stored_token


def lease_expired(server_now_ts: float, lease_expires_at_ts: float) -> bool:
    """Server-clock comparison only — client clocks never decide."""
    return server_now_ts >= lease_expires_at_ts
