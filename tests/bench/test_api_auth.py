"""#2478 — on an auth-required box every bench → hal0-api call must carry the
box service key, or the worker 401s on the registry fetch and queued
benchmarks never run.

A real loopback HTTP stub stands in for hal0-api and rejects any request
without the expected ``Authorization: Bearer`` header with a 401, like the
auth middleware does. The key is discovered the way the worker discovers it
under systemd: no key in the process env, only ``api.env`` on disk (read via
``$HAL0_HOME/etc/hal0/api.env`` here instead of ``/etc/hal0/api.env``).

Every bench endpoint is a CLIENT-class route (or OPEN), so the bench presents
the client key — the least-privilege tier — even when the admin key is also
readable.
"""

from __future__ import annotations

import json
import threading
import time
import types
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from hal0.bench import cli, control, planner, runner
from hal0.security.exposure import AuthClass, classify

CLIENT_KEY = "test-client-key-2478"
ADMIN_KEY = "test-admin-key-2478"

REGISTRY = [{"id": "org/model-a", "path": "/m/A/model-a.gguf", "backend": "vulkan"}]
SLOTS = [{"name": "gpu-a", "model_id": "org/model-a", "status": "idle", "device_class": "gpu"}]
HARDWARE = {"hostname": "stubbox", "platform": "strix-halo", "ram_mb": 65536}

ROUTES: dict[str, object] = {
    "/api/models": {"models": REGISTRY},
    "/api/slots": SLOTS,
    "/api/hardware": HARDWARE,
    "/api/health": {"name": "hal0", "version": "9.9.9"},
    "/api/stats/throughput/history": {"samples": []},
}

# Every hal0-api path the bench package reads. The tier the bench presents is
# only correct while none of these needs ADMIN.
BENCH_API_PATHS = sorted(ROUTES)


class _Stub:
    def __init__(self) -> None:
        self.seen: list[tuple[str, str | None]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                auth = self.headers.get("Authorization")
                path = self.path.split("?", 1)[0]
                stub.seen.append((path, auth))
                if auth not in (f"Bearer {CLIENT_KEY}", f"Bearer {ADMIN_KEY}"):
                    self._send(401, {"error": {"code": "auth.required"}})
                    return
                if path not in ROUTES:
                    self._send(404, {})
                    return
                self._send(200, ROUTES[path])

            def _send(self, status: int, body: object) -> None:
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *a: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def keys_sent(self) -> set[str | None]:
        return {auth for _, auth in self.seen}


@pytest.fixture
def api_env(monkeypatch, tmp_path):
    """Keys only on disk (the systemd worker's situation), both tiers present."""
    monkeypatch.delenv("HAL0_ADMIN_KEY", raising=False)
    monkeypatch.delenv("HAL0_CLIENT_KEY", raising=False)
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    etc = tmp_path / "etc" / "hal0"
    etc.mkdir(parents=True)
    (etc / "api.env").write_text(
        f"HAL0_ADMIN_KEY={ADMIN_KEY}\nHAL0_CLIENT_KEY={CLIENT_KEY}\n", encoding="utf-8"
    )
    return etc / "api.env"


@pytest.fixture
def stub() -> Iterator[_Stub]:
    s = _Stub()
    try:
        yield s
    finally:
        s.close()


def test_bench_api_paths_need_no_more_than_the_client_tier() -> None:
    for path in BENCH_API_PATHS:
        assert classify("GET", path) in (AuthClass.OPEN, AuthClass.CLIENT), path


def test_planner_registry_fetch_authenticates(api_env, stub) -> None:
    models = planner.fetch_registry_models(stub.url)

    assert models == REGISTRY
    assert stub.keys_sent() == {f"Bearer {CLIENT_KEY}"}


def test_runner_reads_authenticate(api_env, stub) -> None:
    assert runner._get_slot(stub.url, "org/model-a") == SLOTS[0]
    assert runner._traffic_in_flight(stub.url) is False  # not the 401 fail-safe
    host = runner.fetch_host(stub.url)
    assert host.name == "stubbox"
    assert host.hal0_version == "9.9.9"

    assert stub.keys_sent() == {f"Bearer {CLIENT_KEY}"}


def test_bench_falls_back_to_admin_key_when_only_admin_is_provisioned(
    api_env, stub, monkeypatch
) -> None:
    api_env.write_text(f"HAL0_ADMIN_KEY={ADMIN_KEY}\n", encoding="utf-8")

    assert planner.fetch_registry_models(stub.url) == REGISTRY
    assert runner._get_slot(stub.url, "org/model-a") == SLOTS[0]
    assert stub.keys_sent() == {f"Bearer {ADMIN_KEY}"}


def test_no_key_sends_no_authorization_header(monkeypatch, tmp_path, stub) -> None:
    """A keyless dev box stays keyless — no empty/garbage bearer is sent."""
    monkeypatch.delenv("HAL0_ADMIN_KEY", raising=False)
    monkeypatch.delenv("HAL0_CLIENT_KEY", raising=False)
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))

    assert runner._get_json(stub.url, "/api/slots") is None  # stub demands auth
    assert stub.keys_sent() == {None}


class _StopLoop(Exception):
    pass


def test_worker_drains_a_queued_model_on_an_auth_required_box(
    api_env, stub, monkeypatch, tmp_path
) -> None:
    """The worker control path end to end up to the GPU: registry fetch,
    model resolution, planning, and the session host block all go through the
    authenticated stub; only the GPU-driving session itself is stubbed."""
    monkeypatch.setenv("HAL0_BENCH_STATE", str(tmp_path / "state"))
    control.set_control(state="running")
    control.enqueue({"id": "q1", "model": "model-a.gguf"})

    seen: dict[str, object] = {}

    def fake_plan(suite, models, store):
        seen["models"] = models
        return []

    def fake_run_session(cells, store, host, **kw):
        seen["host"] = host
        return types.SimpleNamespace(aborted=None, cells_ok=0, cells_failed=0)

    monkeypatch.setattr(cli, "plan", fake_plan)
    monkeypatch.setattr(cli, "run_session", fake_run_session)
    monkeypatch.setattr(cli, "regress_check", lambda store: [])
    monkeypatch.setattr(cli.Store, "reindex", lambda self: None)

    def stop_when_drained(_):
        if not control.read_queue():
            raise _StopLoop

    monkeypatch.setattr(time, "sleep", stop_when_drained)

    with pytest.raises(_StopLoop):
        cli.cmd_worker(types.SimpleNamespace(api=stub.url, poll=0))

    assert seen.get("models") == REGISTRY, "worker never got past the registry fetch"
    assert seen["host"].name == "stubbox"
    assert control.read_queue() == []
    assert control.read_failed() == []
    assert stub.keys_sent() == {f"Bearer {CLIENT_KEY}"}
