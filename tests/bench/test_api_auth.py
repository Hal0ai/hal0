"""#2478 — on an auth-required box every bench → hal0-api call must carry the
box service key, or the worker 401s on the registry fetch and queued
benchmarks never run.

A real loopback HTTP stub stands in for hal0-api and rejects any request
without the expected ``Authorization: Bearer`` header with a 401, like the
auth middleware does. The key is discovered the way the worker discovers it
under systemd: no key in the process env, only ``api.env`` on disk (read via
``$HAL0_HOME/etc/hal0/api.env`` here instead of ``/etc/hal0/api.env``).

Every endpoint the bench package reads is a CLIENT-class route (or OPEN), so it
presents the client key — the least-privilege tier — even when the admin key
is also readable. ``server_ab.py`` (Tier B/C cells) presents the admin key only
for the ADMIN routes it drives (slot config PUT and restart). Tool Bench hands
the client key to tool-eval-bench through its environment, never argv.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
import time
import types
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from hal0.bench import cli, control, evalrun, planner, runner
from hal0.bench.adapters import tool_eval
from hal0.security.exposure import AuthClass, classify
from hal0.service_identity import is_loopback_url, open_url

_SERVER_AB = Path(__file__).resolve().parents[2] / "installer" / "bench" / "server_ab.py"


def _load_server_ab():
    """server_ab.py is a stdlib script, not a package — load it by path."""
    spec = importlib.util.spec_from_file_location("server_ab_2478", _SERVER_AB)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


#: Paths the stub answers like a slot's own llama-server port: no auth, and
#: a bench client must never send the box key there.
SLOT_PORT_PATHS = {"/completion"}
LANDING_PATH = "/landing"

#: A non-loopback --api (TEST-NET-3). Requests to it are intercepted before
#: any connection is attempted.
REMOTE_API = "http://203.0.113.1:8080"


class _Stub:
    """hal0-api stand-in that enforces each route's real tier (``classify``):
    OPEN needs nothing, CLIENT needs the client or admin key, ADMIN needs the
    admin key (403 for the client key), like ``hal0.api.auth._decide``."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, str, str | None]] = []
        #: path -> absolute Location to answer with a 302.
        self.redirects: dict[str, str] = {}
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self) -> None:
                auth = self.headers.get("Authorization")
                path = self.path.split("?", 1)[0]
                length = int(self.headers.get("content-length") or 0)
                if length:
                    self.rfile.read(length)
                stub.seen.append((self.command, path, auth))
                if path in stub.redirects:
                    self.send_response(302)
                    self.send_header("Location", stub.redirects[path])
                    self.send_header("content-length", "0")
                    self.end_headers()
                    return
                if path == LANDING_PATH:
                    # A redirect target on another origin: open, so the test
                    # sees whatever the client chose to send it.
                    self._send(200, SLOTS)
                    return
                if path in SLOT_PORT_PATHS:
                    self._send(200, {"content": "ok", "timings": {}})
                    return
                tier = {f"Bearer {CLIENT_KEY}": "client", f"Bearer {ADMIN_KEY}": "admin"}.get(
                    auth or "", "anon"
                )
                need = classify(self.command, path)
                if need is AuthClass.CLIENT and tier == "anon":
                    self._send(401, {"error": {"code": "auth.required"}})
                    return
                needs_admin = need not in (AuthClass.OPEN, AuthClass.CLIENT)
                if needs_admin and tier != "admin":
                    self._send(401 if tier == "anon" else 403, {"error": {}})
                    return
                if self.command == "GET":
                    if path not in ROUTES:
                        self._send(404, {})
                        return
                    self._send(200, ROUTES[path])
                    return
                self._send(200, {})

            do_GET = _handle
            do_PUT = _handle
            do_POST = _handle

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
        return {auth for _, _, auth in self.seen}


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


# ── server_ab.py (Tier B/C cells) ──────────────────────────────────────────────


@pytest.fixture(params=["hal0-importable", "stdlib-only"])
def server_ab(request, monkeypatch):
    """Both key-resolution paths: with hal0 importable (service_identity) and
    the box's real one — system python3, no hal0 venv (stdlib fallback)."""
    if request.param == "stdlib-only":
        monkeypatch.setitem(sys.modules, "hal0.service_identity", None)
    return _load_server_ab()


def test_server_ab_routes_tiers() -> None:
    assert classify("GET", "/api/slots") is AuthClass.CLIENT
    assert classify("PUT", "/api/slots/gpu-a/config") is AuthClass.ADMIN
    assert classify("POST", "/api/slots/gpu-a/restart") is AuthClass.ADMIN


def test_server_ab_slot_lookup_uses_the_client_key(api_env, stub, server_ab) -> None:
    assert server_ab._get_slot(stub.url, "gpu-a") == SLOTS[0]
    assert stub.keys_sent() == {f"Bearer {CLIENT_KEY}"}


def test_server_ab_slot_config_and_restart_use_the_admin_key(api_env, stub, server_ab) -> None:
    server_ab._apply_extra_args(stub.url, "gpu-a", "--cache-reuse 256")

    assert stub.seen == [
        ("PUT", "/api/slots/gpu-a/config", f"Bearer {ADMIN_KEY}"),
        ("POST", "/api/slots/gpu-a/restart", f"Bearer {ADMIN_KEY}"),
    ]


def test_server_ab_falls_back_to_admin_key_for_reads(api_env, stub, server_ab) -> None:
    api_env.write_text(f"HAL0_ADMIN_KEY={ADMIN_KEY}\n", encoding="utf-8")

    assert server_ab._get_slot(stub.url, "gpu-a") == SLOTS[0]
    assert stub.keys_sent() == {f"Bearer {ADMIN_KEY}"}


def test_server_ab_prefers_env_over_api_env(api_env, stub, server_ab, monkeypatch) -> None:
    api_env.write_text("", encoding="utf-8")
    monkeypatch.setenv("HAL0_CLIENT_KEY", CLIENT_KEY)

    assert server_ab._get_slot(stub.url, "gpu-a") == SLOTS[0]
    assert stub.keys_sent() == {f"Bearer {CLIENT_KEY}"}


def test_server_ab_never_sends_the_key_to_a_slot_port(api_env, stub, server_ab) -> None:
    server_ab._http("POST", f"{stub.url}/completion", {"prompt": "x"})
    assert stub.seen == [("POST", "/completion", None)]


# ── Tool Bench (tool-eval-bench) ──────────────────────────────────────────────


def _happy_eval_doc(scenario_id: str) -> dict:
    return {
        "status": "completed",
        "run_id": "r1",
        "tool_eval_bench_version": "2.5.0",
        "final_score": 2,
        "total_scenarios": 1,
        "scores": {
            "scenario_results": [
                {
                    "scenario_id": scenario_id,
                    "status": "pass",
                    "points": 2,
                    "summary": "ok",
                    "expected_behavior": "ok",
                    "tool_calls_made": [],
                    "duration_seconds": 1.0,
                    "turn_count": 1,
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                }
            ]
        },
    }


def test_tool_bench_sends_the_client_key_through_env_not_argv(api_env, stub, tmp_path) -> None:
    """The fake runner behaves like pinned tool-eval-bench v2.5.0: it reads
    the key from ``TOOL_EVAL_API_KEY`` and calls ``{base_url}/chat/completions``
    with it as a Bearer token."""
    seen: dict[str, object] = {}

    def upstream_like(argv, timeout_s, env=None):
        seen["argv"] = list(argv)
        key = (env or {}).get(tool_eval.API_KEY_ENV)
        base = argv[argv.index("--base-url") + 1]
        req = urllib.request.Request(
            f"{base}/chat/completions",
            data=b"{}",
            method="POST",
            headers={"Authorization": f"Bearer {key}"} if key else {},
        )
        urllib.request.urlopen(req, timeout=5).read()
        out_path = argv[argv.index("--json-file") + 1]
        Path(out_path).write_text(json.dumps(_happy_eval_doc("s1")), encoding="utf-8")
        return 0, "", ""

    rec = evalrun.run_task(
        evalrun.Task(id="s1", kind="A"), "m1", "run-1", stub.url, tmp_path, runner=upstream_like
    )

    assert rec.outcome == "ok", rec.note
    assert stub.seen == [("POST", "/v1/chat/completions", f"Bearer {CLIENT_KEY}")]
    argv_text = " ".join(seen["argv"])
    assert "--api-key" not in argv_text
    assert CLIENT_KEY not in argv_text
    assert ADMIN_KEY not in argv_text


def test_tool_eval_adapter_keeps_the_key_off_argv(tmp_path) -> None:
    req = tool_eval.ToolEvalRequest(
        python_exe=sys.executable,
        base_url="http://x/v1",
        model="m",
        output_path=tmp_path / "out.json",
        api_key="sekrit",
    )
    argv = tool_eval.build_argv(req)
    assert not any("sekrit" in a for a in argv)
    assert "--api-key" not in argv
    assert tool_eval.build_env(req)[tool_eval.API_KEY_ENV] == "sekrit"

    keyless = tool_eval.ToolEvalRequest(
        python_exe=sys.executable, base_url="http://x/v1", model="m", output_path=tmp_path / "o"
    )
    assert tool_eval.build_env(keyless) is None


def test_tool_eval_default_runner_hands_the_env_to_the_child() -> None:
    import os

    script = f"import os, sys; sys.stdout.write(os.environ.get({tool_eval.API_KEY_ENV!r}, ''))"
    rc, out, _ = tool_eval._default_runner(
        [sys.executable, "-c", script],
        30,
        env={**os.environ, tool_eval.API_KEY_ENV: "from-env"},
    )
    assert rc == 0
    assert out == "from-env"


# ── the key never leaves the box (review B1) ──────────────────────────────────


@pytest.mark.parametrize(
    ("url", "loopback"),
    [
        ("http://127.0.0.1:8080", True),
        ("http://localhost:8080/api", True),
        ("http://LOCALHOST:8080", True),
        ("http://[::1]:8080", True),
        ("http://127.0.0.2:8080", False),  # strict allowlist, not 127/8
        ("http://10.0.0.5:8080", False),
        (REMOTE_API, False),
        ("http://127.0.0.1.evil.example:8080", False),
        ("http://user@203.0.113.1:8080", False),
        ("http://evil@127.0.0.1:8080", False),  # userinfo never counts (#2499)
        ("http://user:pw@localhost:8080", False),
        ("http://@[::1]:8080", False),
        ("/api/slots", False),
        ("", False),
        ("http://[bad", False),
    ],
)
def test_is_loopback_url(url, loopback, server_ab) -> None:

    assert is_loopback_url(url) is loopback
    assert server_ab._is_loopback_url(url) is loopback  # the stdlib copy agrees


@pytest.fixture
def remote_calls(monkeypatch) -> list[tuple[str, str | None]]:
    """Intercept urlopen: record (url, Authorization) and fail like an
    unreachable host, so a non-loopback --api never touches the network."""
    calls: list[tuple[str, str | None]] = []

    def fake_urlopen(req, *a, **k):
        if isinstance(req, str):
            calls.append((req, None))
        else:
            calls.append((req.full_url, dict(req.header_items()).get("Authorization")))
        raise urllib.error.URLError("intercepted")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return calls


def test_non_loopback_api_gets_no_key_from_planner_or_runner(api_env, remote_calls) -> None:
    with pytest.raises(urllib.error.URLError):
        planner.fetch_registry_models(REMOTE_API)
    assert runner._get_json(REMOTE_API, "/api/slots") is None
    runner.fetch_host(REMOTE_API)

    assert remote_calls
    assert all(url.startswith(REMOTE_API) for url, _ in remote_calls)
    assert {auth for _, auth in remote_calls} == {None}


def test_non_loopback_api_gets_no_key_from_server_ab(api_env, remote_calls, server_ab) -> None:
    with pytest.raises(urllib.error.URLError):
        server_ab._get_slot(REMOTE_API, "gpu-a")
    with pytest.raises(urllib.error.URLError):
        server_ab._apply_extra_args(REMOTE_API, "gpu-a", "--cache-reuse 256")

    assert [url for url, _ in remote_calls] == [
        f"{REMOTE_API}/api/slots",
        f"{REMOTE_API}/api/slots/gpu-a/config",
    ]
    assert {auth for _, auth in remote_calls} == {None}


def test_non_loopback_api_gives_tool_bench_no_key(api_env, tmp_path) -> None:
    seen: dict[str, object] = {}

    def runner_spy(argv, timeout_s, env=None):
        seen["env"] = env
        return 1, "", "no server"

    evalrun.run_task(
        evalrun.Task(id="s1", kind="A"), "m1", "run-1", REMOTE_API, tmp_path, runner=runner_spy
    )
    # No env override at all: the child inherits ours, which holds no key.
    assert seen["env"] is None


def test_redirect_does_not_carry_the_key(api_env, stub) -> None:
    """A 302 from this box's API to another origin must not forward the key."""
    port = stub.url.rsplit(":", 1)[1]
    elsewhere = f"http://localhost:{port}{LANDING_PATH}"  # different origin
    stub.redirects = {"/api/models": elsewhere, "/api/slots": elsewhere}

    planner.fetch_registry_models(stub.url)
    assert runner._get_json(stub.url, "/api/slots") == SLOTS

    assert stub.seen == [
        ("GET", "/api/models", f"Bearer {CLIENT_KEY}"),
        ("GET", LANDING_PATH, None),
        ("GET", "/api/slots", f"Bearer {CLIENT_KEY}"),
        ("GET", LANDING_PATH, None),
    ]


def test_server_ab_redirect_does_not_carry_the_key(api_env, stub, server_ab) -> None:
    port = stub.url.rsplit(":", 1)[1]
    stub.redirects = {"/api/slots": f"http://localhost:{port}{LANDING_PATH}"}

    assert server_ab._get_slot(stub.url, "gpu-a") == SLOTS[0]
    assert stub.seen == [
        ("GET", "/api/slots", f"Bearer {CLIENT_KEY}"),
        ("GET", LANDING_PATH, None),
    ]


@pytest.mark.parametrize("bad_api", ["", "not a url", "http://[bad"])
def test_runner_get_json_returns_none_for_a_malformed_api(api_env, bad_api) -> None:
    assert runner._get_json(bad_api, "/api/slots") is None


# ── loopback requests never go through a proxy (#2505) ────────────────────────


class _FakeProxy:
    """A real listener standing in for an HTTP forward proxy. It records every
    request it receives (request target + Authorization) and answers a known
    hal0-api path with its stub JSON, so a remote request routed through it
    completes without touching the network."""

    def __init__(self, host: str = "127.0.0.1") -> None:
        self.seen: list[tuple[str, str, str | None]] = []
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def _handle(self) -> None:
                length = int(self.headers.get("content-length") or 0)
                if length:
                    self.rfile.read(length)
                proxy.seen.append((self.command, self.path, self.headers.get("Authorization")))
                path = urllib.parse.urlsplit(self.path).path
                raw = json.dumps(ROUTES.get(path, {})).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = _handle
            do_PUT = _handle
            do_POST = _handle

            def log_message(self, *a: object) -> None:
                pass

        self.server = ThreadingHTTPServer((host, 0), Handler)
        self.url = f"http://{host}:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


_PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")


@pytest.fixture(params=["HTTP_PROXY", "ALL_PROXY"])
def proxied(request, monkeypatch) -> Iterator[tuple[str, _FakeProxy]]:
    """The proxy environment pointing at a live fake proxy, NO_PROXY unset."""
    for name in _PROXY_VARS:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    monkeypatch.delenv("REQUEST_METHOD", raising=False)  # getproxies' CGI guard
    # urlopen caches its default opener, and that opener's ProxyHandler read
    # the proxy environment when it was built. Drop it so this env applies.
    monkeypatch.setattr(urllib.request, "_opener", None)
    proxy = _FakeProxy()
    monkeypatch.setenv(request.param, proxy.url)
    try:
        yield request.param, proxy
    finally:
        proxy.close()


def test_loopback_bench_reads_bypass_the_proxy(api_env, stub, proxied) -> None:
    _, proxy = proxied

    assert planner.fetch_registry_models(stub.url) == REGISTRY
    assert runner._get_json(stub.url, "/api/slots") == SLOTS
    assert runner.fetch_host(stub.url).name == "stubbox"

    assert proxy.seen == []
    assert stub.keys_sent() == {f"Bearer {CLIENT_KEY}"}


def test_server_ab_loopback_requests_bypass_the_proxy(api_env, stub, proxied, server_ab) -> None:
    _, proxy = proxied

    assert server_ab._get_slot(stub.url, "gpu-a") == SLOTS[0]
    server_ab._apply_extra_args(stub.url, "gpu-a", "--cache-reuse 256")
    server_ab._http("POST", f"{stub.url}/completion", {"prompt": "x"})

    assert proxy.seen == []
    assert stub.keys_sent() == {f"Bearer {CLIENT_KEY}", f"Bearer {ADMIN_KEY}", None}


def test_open_url_and_its_stdlib_copy_agree(stub, proxied, server_ab) -> None:
    _, proxy = proxied
    for opener in (open_url, server_ab._open_url):
        with opener(f"{stub.url}{LANDING_PATH}", timeout=5) as resp:
            assert json.loads(resp.read()) == SLOTS
    assert proxy.seen == []


def _skip_unless_http_proxy(var: str) -> None:
    if var != "HTTP_PROXY":
        pytest.skip("urllib's default opener does not read ALL_PROXY")


def test_remote_bench_reads_still_use_the_proxy(api_env, proxied) -> None:
    """A non-loopback --api keeps urllib's default opener, so the proxy
    environment is honoured exactly as before: HTTP_PROXY routes through the
    proxy, without the key. (urllib's default opener never reads ALL_PROXY.)"""
    var, proxy = proxied
    _skip_unless_http_proxy(var)

    assert planner.fetch_registry_models(REMOTE_API) == REGISTRY
    assert runner._get_json(REMOTE_API, "/api/slots") == SLOTS
    with open_url(f"{REMOTE_API}/api/hardware", timeout=10) as resp:
        assert json.loads(resp.read()) == HARDWARE

    assert proxy.seen == [
        ("GET", f"{REMOTE_API}/api/models", None),
        ("GET", f"{REMOTE_API}/api/slots", None),
        ("GET", f"{REMOTE_API}/api/hardware", None),
    ]


def test_server_ab_remote_requests_still_use_the_proxy(api_env, proxied, server_ab) -> None:
    var, proxy = proxied
    _skip_unless_http_proxy(var)

    assert server_ab._get_slot(REMOTE_API, "gpu-a") == SLOTS[0]
    with server_ab._open_url(f"{REMOTE_API}/api/hardware", timeout=10) as resp:
        assert json.loads(resp.read()) == HARDWARE

    assert proxy.seen == [
        ("GET", f"{REMOTE_API}/api/slots", None),
        ("GET", f"{REMOTE_API}/api/hardware", None),
    ]


def test_bench_upload_to_loopback_bypasses_the_proxy(proxied, tmp_path) -> None:
    _, proxy = proxied
    worker = _FakeProxy()  # any 200-answering listener stands in for the worker
    bundle = tmp_path / "b.tar.gz"
    bundle.write_bytes(b"\x1f\x8b")
    try:
        assert cli._upload_bundle(bundle, worker.url, "upload-token") == 0
    finally:
        worker.close()

    assert proxy.seen == []
    assert worker.seen == [("POST", "/v1/bundles", "Bearer upload-token")]


def test_tool_eval_child_reaches_loopback_directly(api_env, stub, proxied) -> None:
    """tool-eval-bench runs as a child process with an httpx-style client,
    which honours HTTP_PROXY *and* ALL_PROXY. Its keyed env must keep loopback
    off the proxy while a remote host still goes through it."""
    pytest.importorskip("httpx")
    _, proxy = proxied
    request = tool_eval.ToolEvalRequest(
        python_exe=sys.executable,
        base_url=f"{stub.url}/v1",
        model="m",
        output_path=Path("unused.json"),
        api_key=CLIENT_KEY,
    )
    env = tool_eval.build_env(request)
    assert env is not None
    script = (
        "import os, httpx\n"
        f"h = {{'Authorization': 'Bearer ' + os.environ[{tool_eval.API_KEY_ENV!r}]}}\n"
        f"httpx.get({stub.url + '/api/slots'!r}, headers=h, timeout=10)\n"
        f"httpx.get({REMOTE_API + '/api/models'!r}, timeout=10)\n"
    )
    rc, _, err = tool_eval._default_runner([sys.executable, "-c", script], 60, env=env)

    assert rc == 0, err
    assert stub.seen == [("GET", "/api/slots", f"Bearer {CLIENT_KEY}")]
    assert proxy.seen == [("GET", f"{REMOTE_API}/api/models", None)]


_LOOPBACK_NO_PROXY = "127.0.0.1,::1,localhost"


@pytest.mark.parametrize(
    ("given", "merged"),
    [
        ({}, _LOOPBACK_NO_PROXY),
        ({"NO_PROXY": "mirror.corp"}, f"mirror.corp,{_LOOPBACK_NO_PROXY}"),
        ({"no_proxy": "mirror.corp"}, f"mirror.corp,{_LOOPBACK_NO_PROXY}"),
        (
            {"NO_PROXY": "mirror.corp, localhost", "no_proxy": "cache.corp,mirror.corp"},
            "mirror.corp,localhost,cache.corp,127.0.0.1,::1",
        ),
    ],
)
def test_tool_eval_no_proxy_is_one_merged_value(given, merged) -> None:
    """Both names get the union of both variables plus loopback: urllib and
    httpx let ``no_proxy`` shadow ``NO_PROXY``, so they must agree."""
    env = tool_eval._with_loopback_no_proxy({"PATH": "/bin", **given})
    assert env["NO_PROXY"] == env["no_proxy"] == merged
    assert env["PATH"] == "/bin"


@pytest.mark.parametrize("given", [{"NO_PROXY": "*"}, {"no_proxy": "*"}, {"NO_PROXY": "a,*"}])
def test_tool_eval_no_proxy_star_is_left_untouched(given) -> None:
    assert tool_eval._with_loopback_no_proxy(dict(given)) == given


@pytest.mark.parametrize("operator_no_proxy", ["127.0.0.2", "*"])
def test_tool_eval_child_keeps_the_operators_uppercase_no_proxy(
    api_env, stub, proxied, monkeypatch, operator_no_proxy
) -> None:
    """An uppercase-only NO_PROXY (a mirror host, or ``*``) still sends that
    host DIRECT from an httpx child; the merged lowercase copy must not
    shadow it. 127.0.0.2 stands in for a mirror: reachable, but not on the
    loopback allowlist."""
    pytest.importorskip("httpx")
    _, proxy = proxied
    monkeypatch.setenv("NO_PROXY", operator_no_proxy)
    mirror = _FakeProxy(host="127.0.0.2")
    try:
        request = tool_eval.ToolEvalRequest(
            python_exe=sys.executable,
            base_url=f"{stub.url}/v1",
            model="m",
            output_path=Path("unused.json"),
            api_key=CLIENT_KEY,
        )
        env = tool_eval.build_env(request)
        assert env is not None
        script = f"import httpx\nhttpx.get({mirror.url + '/api/health'!r}, timeout=10)\n"
        rc, _, err = tool_eval._default_runner([sys.executable, "-c", script], 60, env=env)
    finally:
        mirror.close()

    assert rc == 0, err
    assert proxy.seen == []
    assert mirror.seen == [("GET", "/api/health", None)]


# ── a loopback request never follows a redirect off the box (#2505) ──────────


def test_loopback_redirect_to_a_remote_host_is_refused(api_env, stub, proxied) -> None:
    _, proxy = proxied
    stub.redirects = {
        "/api/models": f"{REMOTE_API}/api/models",
        "/api/slots": f"{REMOTE_API}/api/slots",
    }

    with pytest.raises(urllib.error.HTTPError, match="non-loopback"):
        planner.fetch_registry_models(stub.url)
    assert runner._get_json(stub.url, "/api/slots") is None

    assert proxy.seen == []
    assert [p for _, p, _ in stub.seen] == ["/api/models", "/api/slots"]


def test_open_url_copies_agree_on_redirects(stub, server_ab) -> None:
    port = stub.url.rsplit(":", 1)[1]
    stub.redirects = {
        "/to-remote": f"{REMOTE_API}/api/slots",
        "/to-loopback": f"http://localhost:{port}{LANDING_PATH}",
    }
    for opener in (open_url, server_ab._open_url):
        with pytest.raises(urllib.error.HTTPError, match="non-loopback"):
            opener(f"{stub.url}/to-remote", timeout=5)
        with opener(f"{stub.url}/to-loopback", timeout=5) as resp:
            assert json.loads(resp.read()) == SLOTS


def test_server_ab_loopback_redirect_to_a_remote_host_is_refused(api_env, stub, server_ab) -> None:
    stub.redirects = {"/api/slots": f"{REMOTE_API}/api/slots"}

    with pytest.raises(urllib.error.HTTPError, match="non-loopback"):
        server_ab._get_slot(stub.url, "gpu-a")
    assert [p for _, p, _ in stub.seen] == ["/api/slots"]


def test_bench_upload_token_is_not_carried_across_a_redirect(stub, tmp_path) -> None:
    """The upload token is an unredirected header: a 302 from the worker to
    another (loopback) origin reaches it without the token."""
    port = stub.url.rsplit(":", 1)[1]
    stub.redirects = {"/v1/bundles": f"http://localhost:{port}/api/health"}
    bundle = tmp_path / "b.tar.gz"
    bundle.write_bytes(b"\x1f\x8b")

    assert cli._upload_bundle(bundle, stub.url, "upload-token") == 0

    assert stub.seen == [
        ("POST", "/v1/bundles", "Bearer upload-token"),
        ("GET", "/api/health", None),
    ]
