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
import re
from . import worker_api
from . import worker_contract as wc
from .core import encode

MAX_WORKER_BYTES = 1024 * 1024


class WorkerMixin:
    worker_token: str = ""

    def _send_json(self, value, status: int = 200):
        data = b"" if status == 204 else encode(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
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
            if query:
                return self._send_json({"ok": False, "error": "query parameters forbidden"}, 400)
            try:
                fencing = int(self.headers.get("X-MOA-Fencing-Token", ""))
            except ValueError:
                return self._send_json({"ok": False, "error": "lease headers required"}, 409)
            code, content = worker_api.get_artifact(
                self.store, path[len("/v1/worker/artifacts/"):],
                job_id=self.headers.get("X-MOA-Job-ID", ""),
                lease_id=self.headers.get("X-MOA-Lease-ID", ""),
                fencing_token=fencing, worker_id=self.headers.get("X-MOA-Worker-ID", ""))
            if code != 200:
                return self._send_json(content, code)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self._security_headers()
            self.end_headers()
            return self.wfile.write(content)
        return self._send_json({"ok": False, "error": "unknown worker path"}, 404)

    def _worker_post(self, path: str, raw: bytes):
        if not self._worker_authed():
            return self._send_json({"ok": False, "error": "worker unauthorized"}, 401)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._send_json({"ok": False, "errors": ["body must be UTF-8 JSON object"]}, 400)
        if not isinstance(body, dict):
            return self._send_json({"ok": False, "errors": ["body must be an object"]}, 400)
        if path == "/v1/worker/claim":
            kinds = body.get("kinds")
            worker_id = body.get("worker_id")
            if (not isinstance(worker_id, str) or not wc.ASCII_ID.fullmatch(worker_id) or
                    not isinstance(kinds, list) or not kinds or
                    any(not isinstance(k, str) or k not in wc.JOB_KINDS for k in kinds)):
                return self._send_json({"ok": False, "error": "invalid worker_id or kinds"}, 400)
            job = worker_api.claim(self.store, worker_id, kinds)
            if job is None:
                return self._send_json({"ok": True, "job": None}, 204)
            return self._send_json({"ok": True, "job": job})
        if not re.fullmatch(r"/v1/worker/jobs/[A-Za-z0-9][A-Za-z0-9_.-]{0,199}/(heartbeat|complete|fail)", path):
            return self._send_json({"ok": False, "error": "unknown worker path"}, 404)
        identity = {"worker_id": body.get("worker_id"), "fencing_token": body.get("fencing_token")}
        if path.endswith("/heartbeat"):
            job_id = path[len("/v1/worker/jobs/"):-len("/heartbeat")]
            res = worker_api.heartbeat(self.store, job_id, str(body.get("lease_id", "")), **identity)
            return self._send_json(res, 200 if res.get("ok") else 409)
        if path.endswith("/complete") and path.startswith("/v1/worker/jobs/"):
            env = body.get("result") if isinstance(body, dict) else None
            if not isinstance(env, dict):
                return self._send_json({"ok": False, "errors": ["result envelope missing"]}, 400)
            if env.get("job_id") != path[len("/v1/worker/jobs/"):-len("/complete")]:
                return self._send_json({"ok": False, "errors": ["path/result job_id mismatch"]}, 400)
            res = worker_api.complete(self.store, env, worker_api.validator_hash(), worker_id=body.get("worker_id"))
            if res.get("ok"):
                return self._send_json(res)
            code = 409 if res.get("stale") else 400
            return self._send_json(res, code)
        if path.endswith("/fail") and path.startswith("/v1/worker/jobs/"):
            job_id = path[len("/v1/worker/jobs/"):-len("/fail")]
            res = worker_api.fail(self.store, job_id, str(body.get("lease_id", "")),
                                  str(body.get("code", "error")),
                                  body.get("retryable") is True, **identity)
            return self._send_json(res, 200 if res.get("ok") else 409)
        return self._send_json({"ok": False, "error": "unknown worker path"}, 404)
