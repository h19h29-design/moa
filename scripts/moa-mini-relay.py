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
import secrets
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BIND = "127.0.0.1"
PORT = int(os.environ.get("MOA_RELAY_PORT", "8781"))
RELAY_VERSION = "2026-10-17-v1"
NAS_BASE = os.environ.get("MOA_NAS_BASE", "http://192.168.1.111:8321").rstrip("/")
CONF = Path.home() / ".config" / "moa"
MAX_BODY = 1024 * 1024
ALLOWED_POST = ("/v1/worker/claim",)


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
        self.end_headers()
        self.wfile.write(data)

    def _authed(self) -> bool:
        bearer = self.headers.get("Authorization", "")
        if not bearer.startswith("Bearer "):
            return False
        return secrets.compare_digest(bearer[7:], self.relay_token)

    def _passthrough(self, body: bytes | None):
        url = NAS_BASE + self.path.split("?")[0]
        headers = {"Authorization": "Bearer " + self.nas_token}
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=headers, method=self.command)
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                payload = r.read(MAX_BODY + 1)
                self.send_response(r.status)
                self.send_header("Content-Type", r.headers.get("Content-Type",
                                 "application/json"))
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                print("relay %s %s -> %d" % (self.command, self.path.split("?")[0],
                      r.status), flush=True)
        except urllib.error.HTTPError as exc:
            payload = exc.read(MAX_BODY + 1)
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
        path = self.path.split("?")[0]
        if path == "/healthz":
            return self._ok()
        if path != "/v1/worker/capabilities":
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
        path = self.path.split("?")[0]
        ok_path = path in ALLOWED_POST or (
            path.startswith("/v1/worker/jobs/") and
            (path.endswith("/heartbeat") or path.endswith("/complete") or
             path.endswith("/fail")))
        if not ok_path:
            return self._deny(404, "unknown path")
        if not self._authed():
            return self._deny(401, "relay unauthorized")
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return self._deny(400, "bad length")
        if length <= 0 or length > MAX_BODY:
            return self._deny(413, "body too large")
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
