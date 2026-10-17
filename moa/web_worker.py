"""Worker relay HTTP mixin (moa.worker.v1). Auth separated from web login.

* Bearer token comes from the Authorization header only. Tokens in URL,
  query strings, or logs are never accepted or written.
* The worker token (MOA_WORKER_TOKEN) only leases jobs and files results.
  It cannot approve, hold, or edit human reviews — those actions do not
  exist on these routes by construction.
* All responses are JSON. Failures name the reason, never the secret.
"""
from __future__ import annotations
import json
from urllib.parse import urlsplit
from . import worker_api
from . import worker_contract as wc
from .core import encode

MAX_WORKER_BYTES = 1024 * 1024


class WorkerMixin:
    worker_token: str = ""

    def _send_json(self, value, status: int = 200):
        data = encode(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _worker_bearer(self) -> str:
        bearer = self.headers.get("Authorization", "")
        if bearer.startswith("Bearer "):
            return bearer[7:]
        return ""

    def _worker_authed(self) -> bool:
        return worker_api.check_worker_token(self._worker_bearer(), self.worker_token)

    def _worker_get(self, path: str, query: dict):
        if not self._worker_authed():
            return self._send_json({"ok": False, "error": "worker unauthorized"}, 401)
        if path == "/v1/worker/capabilities":
            return self._send_json({"ok": True,
                                    **worker_api.capabilities(
                                        worker_api.contract_hash(),
                                        worker_api.validator_hash(),
                                        worker_api.RUBRIC_REVISION)})
        if path.startswith("/v1/worker/artifacts/"):
            return self._send_json({"ok": False, "error": "unknown artifact"}, 404)
        return self._send_json({"ok": False, "error": "unknown worker path"}, 404)

    def _worker_post(self, path: str, raw: bytes):
        if not self._worker_authed():
            return self._send_json({"ok": False, "error": "worker unauthorized"}, 401)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._send_json({"ok": False, "errors": ["body must be UTF-8 JSON object"]}, 400)
        if path == "/v1/worker/claim":
            if not isinstance(body, dict):
                return self._send_json({"ok": False, "errors": ["body must be an object"]}, 400)
            job = worker_api.claim(self.store, str(body.get("worker_id", "")),
                                   list(body.get("kinds", []) or []))
            if job is None:
                return self._send_json({"ok": True, "job": None}, 204)
            return self._send_json({"ok": True, "job": job})
        if path.endswith("/heartbeat") and path.startswith("/v1/worker/jobs/"):
            job_id = path[len("/v1/worker/jobs/"):-len("/heartbeat")]
            res = worker_api.heartbeat(self.store, job_id, str(body.get("lease_id", "")))
            return self._send_json(res, 200 if res.get("ok") else 409)
        if path.endswith("/complete") and path.startswith("/v1/worker/jobs/"):
            env = body.get("result") if isinstance(body, dict) else None
            if not isinstance(env, dict):
                return self._send_json({"ok": False, "errors": ["result envelope missing"]}, 400)
            res = worker_api.complete(self.store, env, worker_api.validator_hash())
            if res.get("ok"):
                return self._send_json(res)
            code = 409 if res.get("stale") else 400
            return self._send_json(res, code)
        if path.endswith("/fail") and path.startswith("/v1/worker/jobs/"):
            job_id = path[len("/v1/worker/jobs/"):-len("/fail")]
            res = worker_api.fail(self.store, job_id, str(body.get("lease_id", "")),
                                  str(body.get("code", "error")),
                                  bool(body.get("retryable", True)))
            return self._send_json(res, 200 if res.get("ok") else 409)
        return self._send_json({"ok": False, "error": "unknown worker path"}, 404)
