"""Emit MOA_MACBOOK_CONNECTION.json (public connection info, no secrets).

Stdlib only. The worker token value, private keys, and passwords are never
written here — only the pickup procedure and local path are recorded.
Run: .venv312/bin/python scripts/make-macbook-connection.py
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
schema_path = ROOT / "contracts" / "worker-envelope.schema.json"
contract_hash = hashlib.sha256(schema_path.read_bytes()).hexdigest()
doc = {
    "contract_version": "moa.worker.v1",
    "contract_hash": contract_hash,
    "mini_ssh_alias": "moa-mini (set after Tailscale host + dedicated key confirmed)",
    "mini_broker_bind": "127.0.0.1",
    "mini_broker_port": 8781,
    "macbook_tunnel_port": 18781,
    "worker_id": "macbook-laya-01",
    "worker_token_procedure": (
        "The Mac mini operator generates MOA_WORKER_TOKEN server-side and hands "
        "it over through a separate safe channel (NOT chat, NOT this file). "
        "On the MacBook store it with mode 600, e.g. "
        "~/.config/moa/worker-token (file containing the token only), "
        "or the login Keychain, and export MOA_WORKER_TOKEN at tunnel time."
    ),
    "worker_token_local_path": "~/.config/moa/worker-token",
    "setup_state": "awaiting_coordinator",
    "tunnel_example": (
        "ssh -N -T -o ExitOnForwardFailure=yes "
        "-o ServerAliveInterval=30 -o ServerAliveCountMax=3 "
        "-L 127.0.0.1:18781:127.0.0.1:8781 moa-mini"
    ),
}
out = ROOT / "MOA_MACBOOK_CONNECTION.json"
out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(str(out))
print("contract_hash=" + contract_hash)
