"""AI-assisted conversion/verification/learning configuration.

Implements the 2026-10-12 auto-QA design proposal (`moa-ai.example.yaml`).
The YAML file is optional: when absent, the built-in defaults below apply.

Hard policy (never configurable to paid):
  * additional cash spend is always 0 — no auto-recharge, no purchased
    credits, no BYOK / paid fallback;
  * JEV output is operational verification only and must never become
    training labels, training-sample selection, or promotion signal;
  * Muse-derived output needs explicit training permission before any
    learning reuse (default: blocked).

Secret handling: this module never reads secret *values*.  Provider checks
report presence only (environment variable set / file exists); actual
verification happens during explicit setup, not at import time.
"""
from __future__ import annotations

import os
from pathlib import Path

CONFIG_VERSION = "moa.ai.v1"

DEFAULTS = {
    "config_version": CONFIG_VERSION,
    "mode": "assisted_auto",
    "cash_budget_usd": 0,
    "source_of_truth": "nas_application_api",
    "providers": {
        "muse": {
            "model": "muse-spark-1.3-contributor-free",
            "endpoint": "https://opencode.ai/zen/v1/responses",
            "secret_name": "MOA_MUSE_API_KEY",
            "activation": "after_verified_free_access_and_data_policy",
            "require_exact_model_match": True,
            "cloud_policy": "allowed_non_sensitive_only",
            "training_reuse": "unverified_blocked",
            "daily_request_cap": 250,
            "paid_fallback": False,
        },
        "jev": {
            "model": "typesafe-ai/jev",
            "endpoint": "https://ai-gateway.vercel.sh/v1/evaluate",
            "secret_name": "MOA_AI_GATEWAY_API_KEY",
            "activation": "after_verified_free_credit_eligibility",
            "purpose": "operational_verification_only",
            "training_reuse": "prohibited",
            "training_sample_selection": "prohibited",
            "model_promotion_use": "prohibited",
            "daily_request_cap": 80,
            "monthly_moa_free_credit_cap_usd": 1.0,
            "max_fraction_verified_remaining_free_credit": 0.8,
            "recharge": False,
            "use_purchased_credits": False,
            "byok_fallback": False,
            "paid_fallback": False,
        },
        "laya": {
            "model": "convaiinnovations/laya-multilingual",
            "device_preference": "mps",
            "max_len": 1024,
            "vision": False,
        },
    },
    "conversion": {
        "daily_new_document_cap": 100,
        "pilot_batches": [5, 20, 100],
        "automatic_repair_max_rounds": 2,
        "unknown_source_action": "preserve_original",
    },
    "human_review": {
        "initial_representative_document_target": 30,
        "daily_display_cap": 3,
        "random_full_audits": 1,
        "maximum_exception_reviews": 2,
        "excess_action": "preserve_original_or_hold",
    },
    "training": {
        "minimum_new_eligible_decisions": 500,
        "minimum_source_groups": 30,
        "maximum_runs_per_week": 2,
        "allowed_label_origins": ["human_independent", "deterministic_oracle"],
        "forbidden_lineage": [
            "jev_output", "jev_label", "jev_selected", "laya_self_label",
        ],
    },
}

NEEDS_PROVIDER_CONFIGURATION = "NEEDS_PROVIDER_CONFIGURATION"


def _deep_merge(base: dict, override: dict) -> dict:
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _parse_simple_yaml(text: str) -> dict:
    """Minimal YAML subset parser (mappings, scalars, flow lists).

    stdlib only — supports the shape of moa-ai.yaml. Raises ValueError
    on anything outside the subset so a hand-edited config fails loud.
    """
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    for lineno, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.strip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if "\t" in raw[:indent]:
            raise ValueError("yaml %d: tabs not allowed" % lineno)
        if ":" not in raw:
            raise ValueError("yaml %d: expected 'key: value'" % lineno)
        key, _, rest = raw.strip().partition(":")
        key = key.strip()
        rest = rest.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if rest == "":
            child: dict = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _parse_scalar(rest, lineno)
    return root


def _parse_scalar(value: str, lineno: int):
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(v.strip(), lineno) for v in inner.split(",")]
    low = value.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    if any(ch in value for ch in "{}[]*&!|>@`"):
        raise ValueError("yaml %d: unsupported value %r" % (lineno, value))
    return value


def load_ai_config(path: str | os.PathLike | None = None) -> tuple[dict, list[str]]:
    """Load effective AI config. Returns (config, warnings)."""
    warnings: list[str] = []
    config = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    candidate = Path(path) if path else Path(
        os.environ.get("MOA_AI_CONFIG", "moa-ai.yaml")
    )
    if candidate.exists():
        try:
            override = _parse_simple_yaml(candidate.read_text(encoding="utf-8"))
        except ValueError as exc:
            warnings.append("config parse failed, defaults kept: %s" % exc)
            override = {}
        config = _deep_merge(config, override)
        warnings.append("loaded %s" % candidate)
    # Cash budget is not raisable through configuration.
    if config.get("cash_budget_usd") not in (0, 0.0):
        warnings.append("cash_budget_usd forced to 0 (configured %r ignored)"
                        % config.get("cash_budget_usd"))
        config["cash_budget_usd"] = 0
    for name in ("muse", "jev"):
        prov = config.get("providers", {}).get(name, {})
        for flag in ("paid_fallback", "recharge", "use_purchased_credits",
                     "byok_fallback"):
            if prov.get(flag):
                warnings.append("%s.%s forced to false" % (name, flag))
                prov[flag] = False
    return config, warnings


def assert_no_paid(config: dict) -> None:
    """Raise if any paid path is enabled. Fail-closed on doubt."""
    if config.get("cash_budget_usd") not in (0, 0.0):
        raise ValueError("cash budget must stay 0")
    for name in ("muse", "jev"):
        prov = config.get("providers", {}).get(name, {})
        for flag in ("paid_fallback", "recharge", "use_purchased_credits",
                     "byok_fallback"):
            if prov.get(flag):
                raise ValueError("%s.%s must stay false" % (name, flag))


def provider_status(config: dict, env: dict | None = None) -> dict:
    """Presence-only provider gate states (no secret values read).

    Returns e.g. {'muse': 'NEEDS_PROVIDER_CONFIGURATION', ...}.
    'ready' is never reported here — actual free-tier verification needs
    explicit setup with account checks, which this turn does not perform.
    """
    env = env if env is not None else os.environ
    states: dict = {}
    for name in ("muse", "jev"):
        secret_name = config["providers"][name]["secret_name"]
        states[name] = ("needs_verification"
                        if secret_name in env and env[secret_name]
                        else NEEDS_PROVIDER_CONFIGURATION)
    states["laya"] = "macbook_side_not_connected"
    return states
