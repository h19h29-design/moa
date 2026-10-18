"""Mac mini worker relay: MacBook tunnel endpoint -> NAS worker API.

Stdlib only. Listens on 127.0.0.1:8781 (localhost; the MacBook reaches it
through its own SSH tunnel, nothing is bound to LAN or opened publicly).

Secrets: bearer tokens live in 600 files, never in argv, logs, or URLs.
  relay token (MacBook-facing): ~/.config/moa/relay-token
  NAS worker token (NAS-facing): ~/.config/moa/nas-worker-token
NAS base URL: MOA_NAS_BASE env (default http://192.168.1.111:8321).

Run: python3 scripts/moa-mini-relay.py  (or the LaunchAgent below)
LaunchAgent plist: com.moa.relay.plist in ~/Library/LaunchAgents
"""
from __future__ import annotations
import json
import os
import re
import secrets
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BIND = "127.0.0.1"
PORT = int(os.environ.get("MOA_RELAY_PORT", "8781"))
RELAY_VERSION = "2026-10-18-v2"
NAS_BASE = os.environ.get("MOA_NAS_BASE", "http://192.168.1.111:8321").rstrip("/")
CONF = Path.home() / ".config" / "moa"
MAX_BODY = 1024 * 1024
ALLOWED_POST = ("/v1/worker/claim",)
ARTIFACT_PATH = re.compile(r"/v1/worker/artifacts/[A-Za-z0-9][A-Za-z0-9_.-]{0,199}")
JOB_PATH = re.compile(r"/v1/worker/jobs/[A-Za-z0-9][A-Za-z0-9_.-]{0,199}/(heartbeat|complete|fail)")
LEASE_HEADERS = ("X-MOA-Worker-ID", "X-MOA-Job-ID", "X-MOA-Lease-ID", "X-MOA-Fencing-Token")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_token(name: str) -> str:
    return (CONF / name).read_text().strip()


class Relay(BaseHTTPRequestHandler):
    relay_token = ""
    nas_token = ""

    def log_message(self, *a):
        pass

    def _deny(self, code: int, msg: str):
        data = json.dumps({"ok": False, "error": msg}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _authed(self) -> bool:
        bearer = self.headers.get("Authorization", "")
        if not bearer.startswith("Bearer "):
            return False
        return bool(self.relay_token) and secrets.compare_digest(bearer[7:], self.relay_token)

    def _passthrough(self, body: bytes | None):
        url = NAS_BASE + self.path.split("?")[0]
        headers = {"Authorization": "Bearer " + self.nas_token}
        for name in LEASE_HEADERS:
            value = self.headers.get(name)
            if value is not None:
                if len(value) > 200:
                    return self._deny(400, "invalid lease header")
                headers[name] = value
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=headers, method=self.command)
        try:
            with urllib.request.build_opener(NoRedirect()).open(req, timeout=25) as r:
                payload = r.read(MAX_BODY + 1)
                if len(payload) > MAX_BODY:
                    return self._deny(502, "upstream response too large")
                self.send_response(r.status)
                self.send_header("Content-Type", r.headers.get("Content-Type",
                                 "application/json"))
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(payload)
                print("relay %s %s -> %d" % (self.command, self.path.split("?")[0],
                      r.status), flush=True)
        except urllib.error.HTTPError as exc:
            payload = exc.read(MAX_BODY + 1)
            if len(payload) > MAX_BODY or 300 <= exc.code < 400:
                return self._deny(502, "upstream response refused")
            self.send_response(exc.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            print("relay %s %s -> %d" % (self.command, self.path.split("?")[0],
                  exc.code), flush=True)
        except Exception as exc:
            print("relay %s %s -> upstream error: %s" % (self.command,
                  self.path.split("?")[0], type(exc).__name__), flush=True)
            self._deny(502, "upstream unavailable")

    def do_GET(self):
        if "?" in self.path or "#" in self.path:
            return self._deny(400, "query parameters forbidden")
        path = self.path.split("?")[0]
        if path == "/healthz":
            return self._ok()
        if path != "/v1/worker/capabilities" and not ARTIFACT_PATH.fullmatch(path):
            return self._deny(404, "unknown path")
        if not self._authed():
            return self._deny(401, "relay unauthorized")
        self._passthrough(None)

    def _ok(self):
        data = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if "?" in self.path or "#" in self.path:
            return self._deny(400, "query parameters forbidden")
        path = self.path.split("?")[0]
        ok_path = path in ALLOWED_POST or JOB_PATH.fullmatch(path)
        if not ok_path:
            return self._deny(404, "unknown path")
        if not self._authed():
            return self._deny(401, "relay unauthorized")
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return self._deny(400, "bad length")
        if length <= 0 or length > MAX_BODY or self.headers.get("Transfer-Encoding"):
            return self._deny(413, "body too large")
        self.connection.settimeout(30)
        body = self.rfile.read(length)
        if len(body) != length:
            return self._deny(400, "incomplete body")
        self._passthrough(body)


def main() -> int:
    Relay.relay_token = read_token("relay-token")
    Relay.nas_token = read_token("nas-worker-token")
    if not Relay.relay_token or not Relay.nas_token:
        raise SystemExit("relay tokens missing")
    server = ThreadingHTTPServer((BIND, PORT), Relay)
    print("moa relay on %s:%d -> %s" % (BIND, PORT, NAS_BASE), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
