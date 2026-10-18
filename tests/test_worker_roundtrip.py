"""Synthetic-only worker wire tests. No real model results or production access."""
import copy
import hashlib
import importlib.util
import json
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import pytest

from moa import worker_api as api, worker_contract as wc, worker_smoke as smoke
from moa.core import Store
from moa.web_worker import WorkerMixin
from test_autoqa_contract import _result_for


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path, thread_safe=True) as db:
        yield db


def claimed(store):
    assert smoke.publish(store)["ok"]
    job = api.claim(store, smoke.WORKER_ID, ["verify"])
    assert job
    return job


def finish(store, env, worker=smoke.WORKER_ID):
    return api.complete(store, env, api.validator_hash(), worker_id=worker)


def test_publish_one_idempotent_targeted_job_and_manifest(store):
    first = smoke.publish(store)
    second = smoke.publish(store)
    assert first["job_id"] == second["job_id"] == smoke.JOB_ID
    assert second["duplicate"]
    assert store.db.execute("SELECT count(*) FROM worker_jobs").fetchone()[0] == 1
    assert store.db.execute("SELECT count(*) FROM worker_artifacts").fetchone()[0] == 1
    assert api.claim(store, "other-worker", ["verify"]) is None
    job = api.claim(store, smoke.WORKER_ID, ["verify"])
    assert wc.validate_envelope(job) == []
    assert "example_only" not in job
    assert job["payload"]["model_policy"] == {"checkpoint": "current", "max_tokens": 1024, "vision": False}
    code, content = artifact(store, job)
    assert code == 200
    assert hashlib.sha256(content).hexdigest() == job["payload"]["source_manifest_artifact"]["sha256"]
    manifest = json.loads(content)
    assert manifest["complete"] and manifest["scope"] == "section"
    assert manifest["block_sha256"]["b1"] == hashlib.sha256(job["payload"]["source_blocks"][0]["text"].encode()).hexdigest()


def artifact(store, job, **overrides):
    kw = {"job_id": job["job_id"], "lease_id": job["lease_id"],
          "fencing_token": job["fencing_token"], "worker_id": smoke.WORKER_ID}
    kw.update(overrides)
    return api.get_artifact(store, job["payload"]["source_manifest_artifact"]["artifact_id"], **kw)


def test_artifact_needs_live_owning_lease_and_integrity(store):
    job = claimed(store)
    assert artifact(store, job, worker_id="other-worker")[0] == 409
    assert artifact(store, job, lease_id="wrong")[0] == 409
    assert artifact(store, job, fencing_token=job["fencing_token"] + 1)[0] == 409
    unrelated = api.put_artifact(store, b'{"unrelated":"synthetic"}')
    assert api.get_artifact(store, unrelated["artifact_id"], job_id=job["job_id"],
                            lease_id=job["lease_id"], fencing_token=job["fencing_token"],
                            worker_id=smoke.WORKER_ID)[0] == 404
    store.db.execute("UPDATE worker_artifacts SET content=?", (b'{}',))
    store.db.commit()
    assert artifact(store, job)[0] == 409
    with pytest.raises(ValueError):
        api.put_artifact(store, b" " * (api.MAX_ARTIFACT_BYTES + 1))


def test_manifest_block_hash_not_trusted_from_payload_flag(store):
    job = claimed(store)
    env = copy.deepcopy(job)
    env["job_id"] = "different-job"
    env["payload"]["source_blocks"][0]["text"] = "변조된 합성 문장"
    env["payload"]["source_complete"] = True
    env["payload"]["source_evidence_verified"] = True
    digest = wc.input_sha256(env["job_kind"], env["payload"], env["source_revision"], env["render_revision"], env["rubric_revision"])
    env["input_sha256"] = env["idempotency_key"] = digest
    assert not api.enqueue_job(store, env)["ok"]


def test_result_echoes_input_and_stale_attempt_does_not_poison_retry(store):
    job = claimed(store)
    good = _result_for(job)
    assert good["payload"] != job["payload"] and good["input_sha256"] == job["input_sha256"]
    bad = copy.deepcopy(good)
    bad["fencing_token"] += 1
    assert finish(store, bad)["stale"]
    assert store.db.execute("SELECT count(*) FROM worker_results").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM worker_result_attempts").fetchone()[0] == 1
    assert finish(store, good)["accepted"]
    assert finish(store, good)["duplicate"]
    assert not finish(store, good, worker="other-worker")["ok"]
    conflicting = copy.deepcopy(good)
    conflicting["payload"]["answers"]["conditions"]["label"] = "changed"
    assert not finish(store, conflicting)["ok"]
    assert artifact(store, job)[0] == 409  # completion closes artifact access
    assert store.db.execute("SELECT count(*) FROM machine_checks").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM mobile_reviews").fetchone()[0] == 0


@pytest.mark.parametrize("field", ["source_revision", "render_revision", "rubric_revision", "input_sha256", "idempotency_key"])
def test_result_must_match_persisted_job(store, field):
    job = claimed(store)
    env = _result_for(job)
    env[field] = "0" * 64 if "sha256" in field or field == "idempotency_key" else "another-revision"
    assert not finish(store, env)["ok"]


def test_expired_offline_worker_reclaimed_with_new_fence(store):
    job = claimed(store)
    store.db.execute("UPDATE worker_jobs SET lease_expires_at=1")
    store.db.commit()
    assert artifact(store, job)[0] == 409
    next_job = api.claim(store, smoke.WORKER_ID, ["verify"])
    assert next_job["lease_id"] != job["lease_id"]
    assert next_job["fencing_token"] > job["fencing_token"]
    assert finish(store, _result_for(job))["stale"]
    assert finish(store, _result_for(next_job))["ok"]


def test_cancelled_or_wrong_identity_cannot_extend_lease_or_complete(store):
    job = claimed(store)
    hb = lambda **kw: api.heartbeat(store, job["job_id"], job["lease_id"], **kw)
    assert not hb(worker_id="other-worker", fencing_token=job["fencing_token"])["ok"]
    assert not hb(worker_id=smoke.WORKER_ID, fencing_token=job["fencing_token"] + 1)["ok"]
    assert api.cancel_revision(store, job["source_revision"]) == 1
    assert not hb(worker_id=smoke.WORKER_ID, fencing_token=job["fencing_token"])["ok"]
    assert finish(store, _result_for(job))["stale"]
    assert api.claim(store, smoke.WORKER_ID, ["verify"]) is None


def test_complete_not_a_quality_pass_and_invalid_distributions_rejected(store):
    job = claimed(store)
    env = _result_for(job)
    env["payload"]["answers"]["conditions"]["raw_probabilities"]["changed"] = float("nan")
    assert not finish(store, env)["ok"]
    env = _result_for(job)
    env["payload"]["truncation"] = True
    assert not finish(store, env)["ok"]
    env = _result_for(job)
    answer = env["payload"]["answers"]["conditions"]
    answer.update(label="changed", calibrated=False, calibrated_probabilities=None)
    assert finish(store, env)["verdict"] == "needs_review"


@contextmanager
def serving(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:%d" % server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def request(base, path, data=None, headers=None):
    req = urllib.request.Request(base + path, data=None if data is None else json.dumps(data).encode(), headers=headers or {})
    try:
        r = urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError as err:
        r = err
    with r:
        return r.status, r.read()


def relay_module():
    path = Path(__file__).resolve().parents[1] / "scripts/moa-mini-relay.py"
    spec = importlib.util.spec_from_file_location("moa_test_relay", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_http_relay_claim_artifact_heartbeat_complete_ack(store, monkeypatch):
    class API(WorkerMixin, BaseHTTPRequestHandler):
        worker_token = "synthetic-upstream-token"
        def _security_headers(self): pass
        def log_message(self, *args): pass
        def do_GET(self):
            return self._worker_get(urlsplit(self.path).path, parse_qs(urlsplit(self.path).query))
        def do_POST(self):
            return self._worker_post(self.path, self.rfile.read(int(self.headers.get("Content-Length", 0))))
    API.store = store
    relay = relay_module()
    relay.Relay.relay_token = "synthetic-relay-token"
    relay.Relay.nas_token = API.worker_token
    headers = {"Authorization": "Bearer " + relay.Relay.relay_token}
    with serving(API) as upstream:
        monkeypatch.setattr(relay, "NAS_BASE", upstream)
        with serving(relay.Relay) as base:
            assert request(base, "/healthz")[0] == 200
            assert request(base, "/v1/worker/capabilities")[0] == 401
            code, caps = request(base, "/v1/worker/capabilities", headers=headers)
            assert code == 200 and json.loads(caps)["contract_hash"] == smoke.EXPECTED_CONTRACT_HASH
            claim_body = {"worker_id": smoke.WORKER_ID, "kinds": ["verify"]}
            assert request(base, "/v1/worker/claim", claim_body, headers) == (204, b"")
            smoke.publish(store)
            code, body = request(base, "/v1/worker/claim", claim_body, headers)
            assert code == 200
            job = json.loads(body)["job"]
            artifact_path = "/v1/worker/artifacts/" + job["payload"]["source_manifest_artifact"]["artifact_id"]
            assert request(base, artifact_path)[0] == 401
            assert request(base, artifact_path, headers=headers)[0] == 409
            artifact_headers = {**headers, "X-MOA-Worker-ID": smoke.WORKER_ID, "X-MOA-Job-ID": job["job_id"],
                                "X-MOA-Lease-ID": job["lease_id"], "X-MOA-Fencing-Token": str(job["fencing_token"])}
            code, data = request(base, artifact_path, headers=artifact_headers)
            assert code == 200 and hashlib.sha256(data).hexdigest() == job["payload"]["source_manifest_artifact"]["sha256"]
            assert request(base, artifact_path + "?token=forbidden", headers=artifact_headers)[0] == 400
            prefix = "/v1/worker/jobs/" + job["job_id"]
            identity = {"worker_id": smoke.WORKER_ID, "lease_id": job["lease_id"], "fencing_token": job["fencing_token"]}
            assert request(base, prefix + "/heartbeat", identity, headers)[0] == 200
            env = _result_for(job)
            body = {"worker_id": smoke.WORKER_ID, "result": env}
            assert request(base, "/v1/worker/jobs/wrong-job/complete", body, headers)[0] == 400
            code, ack = request(base, prefix + "/complete", body, headers)
            assert code == 200 and json.loads(ack)["accepted"]
            assert json.loads(request(base, prefix + "/complete", body, headers)[1])["duplicate"]
            assert smoke.status(store.root)["status"] == "done"
            assert [e["event"] for e in smoke.status(store.root)["events"]] == ["claim", "heartbeat", "complete"]
            assert store.db.execute("SELECT count(*) FROM mobile_reviews").fetchone()[0] == 0


def test_relay_refuses_oversized_upstream(monkeypatch):
    relay = relay_module()
    relay.Relay.relay_token = "synthetic-relay-token"
    relay.Relay.nas_token = "synthetic-upstream-token"
    class Oversized(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"x" * (relay.MAX_BODY + 1))
    with serving(Oversized) as upstream:
        monkeypatch.setattr(relay, "NAS_BASE", upstream)
        with serving(relay.Relay) as base:
            assert request(base, "/v1/worker/artifacts/allowed-id", headers={"Authorization": "Bearer " + relay.Relay.relay_token})[0] == 502
            assert request(base, "/v1/worker/artifacts/../capabilities")[0] == 404


def test_relay_does_not_forward_credentials_on_redirect(monkeypatch):
    relay = relay_module()
    relay.Relay.relay_token = "synthetic-relay-token"
    relay.Relay.nas_token = "synthetic-upstream-token"
    visited = []
    class Destination(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            visited.append(True)
            self.send_response(200)
            self.end_headers()
    with serving(Destination) as destination:
        class Redirect(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", destination + "/forbidden")
                self.end_headers()
        with serving(Redirect) as upstream:
            monkeypatch.setattr(relay, "NAS_BASE", upstream)
            with serving(relay.Relay) as base:
                assert request(base, "/v1/worker/capabilities", headers={"Authorization": "Bearer " + relay.Relay.relay_token})[0] == 502
    assert visited == []


def test_concurrent_publication_creates_only_one_job_and_route(store):
    from concurrent.futures import ThreadPoolExecutor
    barrier = threading.Barrier(2)
    def publish_once(_):
        with Store(store.root) as connection:
            barrier.wait(timeout=5)
            return smoke.publish(connection)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish_once, range(2)))
    assert all(r["ok"] and r["job_id"] == smoke.JOB_ID for r in results)
    assert store.db.execute("SELECT count(*) FROM worker_jobs").fetchone()[0] == 1
    assert store.db.execute("SELECT count(*) FROM worker_job_routes").fetchone()[0] == 1
