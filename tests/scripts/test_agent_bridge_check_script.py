"""Contract tests for scripts/agent-bridge-check.sh (the live agent-bridge probe).

The REAL script runs against a fake Streamable-HTTP MCP server and a stub
``hal0`` CLI, so no hal0-api and no Hermes is involved. The fake server
answers the way hal0-api does for a bridge key: ``initialize`` returns an
``Mcp-Session-Id``, ``/mcp/memory`` and ``/mcp/admin`` are 403 until the
client holds that scope, a revoked key is 401. It can answer as plain JSON or
as server-sent events, because the real mounts answer with SSE.

Pinned here: the exit codes (0 pass, 1 fail, 2 usage, 3 pass with the pong step
skipped), that ``--mode auto`` reads the default profile's health and model id,
that the key is parsed from the ``claude mcp add`` line and never printed, and
that the probe client is revoked even when the probe stops early.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "agent-bridge-check.sh"
_TOKEN = (
    "fake-token-for-tests-0123456789abcdef"  # stands in for the secret the probe must not print
)
_KEY_RE = re.compile(r"^Bearer (hal0bk_ci-probe-\d+\.[A-Za-z0-9_-]+)$")

# The stub `hal0`: the three verbs the probe calls. `add` prints what the real
# command prints (a panel that wraps the key, then one pasteable line per scope);
# `scopes` and `revoke` tell the fake server, which is at $HAL0_API_URL.
_HAL0_STUB = f"""#!{sys.executable}
import json, os, sys, urllib.request

args = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(args) + "\\n")
base = os.environ["HAL0_API_URL"]


def control(action, **body):
    req = urllib.request.Request(
        base + "/__control/" + action, data=json.dumps(body).encode(), method="POST"
    )
    urllib.request.urlopen(req, timeout=10).read()


if args[:2] == ["bridge", "add"]:
    key = "hal0bk_" + args[2] + ".{_TOKEN}"
    print("+----- " + args[2] + " -----+")
    print("| " + key[:30] + " |")   # a narrow terminal wraps the key here
    print("| " + key[30:] + " |")
    print()
    print("# hal0-agents")
    print(
        "claude mcp add --transport http hal0-agents http://203.0.113.7:8080/mcp/agents/mcp "
        '--header "Authorization: Bearer ' + key + '"'
    )
elif args[:2] == ["bridge", "scopes"]:
    control("scopes", scopes=args[3:])
elif args[:2] == ["bridge", "revoke"]:
    control("revoke")
else:
    sys.exit(64)
"""


@dataclass
class Fake:
    """What the fake hal0-api does; mutated by the stub through /__control/."""

    sse: bool = False
    health: int = 200
    model_id: str = "qwen3"
    status: str = "completed"
    output: str = "Pong."
    ignore_revoke: bool = False
    leak_approve: bool = False
    scopes: set[str] = field(default_factory=lambda: {"agents"})
    revoked: bool = False
    tool_calls: list[str] = field(default_factory=list)


def _handler(fake: Fake) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: Any) -> None:  # silence the test output
            pass

        def _send(
            self, code: int, body: bytes = b"", headers: dict[str, str] | None = None
        ) -> None:
            self.send_response(code)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _rpc(self, msg_id: Any, result: Any, headers: dict[str, str] | None = None) -> None:
            payload = json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result})
            if fake.sse:
                body = f"event: message\r\ndata: {payload}\r\n\r\n".encode()
                ctype = "text/event-stream"
            else:
                body, ctype = payload.encode(), "application/json"
            self._send(200, body, {"Content-Type": ctype, **(headers or {})})

        def _tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
            fake.tool_calls.append(name)
            if name == "agents_list":
                profile = {
                    "name": "default",
                    "agent_id": "hermes",
                    "keyed": True,
                    "health": fake.health,
                    "model_id": fake.model_id,
                }
                return {
                    "agents": [
                        {"id": "hermes", "profiles": [profile]},
                        {"id": "pi", "runnable": False},
                    ]
                }
            if name == "agent_run":
                assert args["agent"] == "hermes" and args["wait_s"] == 180
                assert "pong" in args["input"]
                return {"run_id": "run_1", "status": fake.status, "output": fake.output}
            return {"run_id": args.get("run_id"), "status": "stopping"}

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            if self.path.startswith("/__control/"):
                action = self.path.rsplit("/", 1)[1]
                if action == "revoke":
                    fake.revoked = not fake.ignore_revoke
                elif action == "scopes":
                    fake.scopes = set(json.loads(raw)["scopes"])
                return self._send(200, b"{}")
            match = re.fullmatch(r"/mcp/(agents|memory|admin)/mcp", self.path)
            if not match:
                return self._send(404)
            mount = match.group(1)
            auth = _KEY_RE.match(self.headers.get("Authorization", ""))
            if not auth or fake.revoked:
                return self._send(401)
            if mount not in fake.scopes:
                return self._send(403)
            if "text/event-stream" not in self.headers.get("Accept", ""):
                return self._send(406)
            msg = json.loads(raw)
            method = msg.get("method")
            if method == "initialize":
                return self._rpc(
                    msg["id"],
                    {"protocolVersion": "2025-06-18"},
                    {"Mcp-Session-Id": "sess-" + mount[1:]},
                )
            if self.headers.get("Mcp-Session-Id") != "sess-" + mount[1:]:
                return self._send(400)
            if method == "notifications/initialized":
                return self._send(202)
            if method == "tools/list":
                names = {
                    "agents": ["agents_list", "agent_run"],
                    "memory": ["memory_add"],
                    "admin": ["slot_list", "approval_list"],
                }[mount]
                if mount == "admin" and fake.leak_approve:
                    names.append("approval_approve")
                return self._rpc(msg["id"], {"tools": [{"name": n} for n in names]})
            if method == "tools/call":
                payload = self._tool(msg["params"]["name"], msg["params"]["arguments"])
                return self._rpc(
                    msg["id"],
                    {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": False},
                )
            return self._send(400)

    return Handler


@pytest.fixture
def box(tmp_path: Path):
    """A running fake hal0-api: yields (fake, base_url, hal0_stub_path, log_path)."""
    fake = Fake()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(fake))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    stub = tmp_path / "hal0"
    stub.write_text(_HAL0_STUB)
    stub.chmod(0o755)
    try:
        yield fake, f"http://127.0.0.1:{server.server_address[1]}", stub, tmp_path / "stub.log"
    finally:
        server.shutdown()
        server.server_close()


def _run(box_: Any, *args: str, base: str | None = None) -> subprocess.CompletedProcess[str]:
    _fake, url, stub, log = box_
    env = {
        "PATH": os.environ["PATH"],
        "HAL0_BIN": str(stub),
        "STUB_LOG": str(log),
        "TMPDIR": str(log.parent),
    }
    return subprocess.run(
        ["bash", str(_SCRIPT), "--base", base or url, *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def _stub_calls(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


def _verbs(log: Path) -> list[str]:
    return [" ".join(c[:2]) for c in _stub_calls(log)]


def test_help_lists_the_modes_and_exit_codes() -> None:
    proc = subprocess.run(
        ["bash", str(_SCRIPT), "--help"], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0
    for word in ("--mode", "reduced", "--base", "--keep", "3 passed"):
        assert word in proc.stdout


@pytest.mark.parametrize(
    "args",
    [
        ["--mode", "bogus"],
        ["--mode"],
        ["--frobnicate"],
        ["--base", "ftp://nope"],
    ],
)
def test_bad_arguments_exit_2_before_touching_the_box(args: list[str], tmp_path: Path) -> None:
    log = tmp_path / "stub.log"
    stub = tmp_path / "hal0"
    stub.write_text(_HAL0_STUB)
    stub.chmod(0o755)
    env = {"PATH": os.environ["PATH"], "HAL0_BIN": str(stub), "STUB_LOG": str(log)}
    proc = subprocess.run(
        ["bash", str(_SCRIPT), *args], env=env, capture_output=True, text=True, check=False
    )
    assert proc.returncode == 2
    assert "Usage:" in proc.stderr
    assert not log.exists()


@pytest.mark.parametrize("sse", [False, True], ids=["json", "sse"])
def test_reduced_mode_passes_with_exit_3_and_skips_the_pong_step(box, sse: bool) -> None:
    fake, _url, _stub, log = box
    fake.sse = sse
    proc = _run(box, "--mode", "reduced")
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "SKIPPED (reduced)" in proc.stdout
    assert "agent_run" not in fake.tool_calls
    assert _verbs(log) == ["bridge add", "bridge scopes", "bridge revoke"]
    add = _stub_calls(log)[0]
    assert re.fullmatch(r"ci-probe-\d+", add[2]) and add[3:] == ["--scope", "agents"]
    assert _stub_calls(log)[1][3:] == ["agents", "memory", "admin"]
    for step in ("[1]", "[2]", "[3]", "[5a]", "[5b]", "[6]"):
        assert f"PASS: {step}" in proc.stdout
    assert _TOKEN not in proc.stdout + proc.stderr  # the key is never printed
    assert "hal0bk_" not in proc.stdout + proc.stderr


@pytest.mark.parametrize("sse", [False, True], ids=["json", "sse"])
def test_full_mode_needs_pong_in_a_completed_run(box, sse: bool) -> None:
    fake, *_ = box
    fake.sse = sse
    proc = _run(box, "--mode", "full")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASS: [4] agent_run completed and answered pong" in proc.stdout
    assert "RESULT: PASS" in proc.stdout


def test_full_mode_fails_when_the_answer_has_no_pong(box) -> None:
    fake, _url, _stub, log = box
    fake.output = "I would rather not."
    proc = _run(box, "--mode", "full")
    assert proc.returncode == 1
    assert "FAIL: [4]" in proc.stderr
    assert "without pong" in proc.stderr
    assert _verbs(log)[-1] == "bridge revoke"  # later steps still ran and the client was revoked


def test_a_run_that_has_not_finished_fails_and_is_stopped(box) -> None:
    fake, *_ = box
    fake.status = "running"
    proc = _run(box, "--mode", "full")
    assert proc.returncode == 1
    assert "status running" in proc.stderr
    assert "agent_run_stop" in fake.tool_calls


def test_auto_mode_goes_full_when_the_default_profile_serves_a_model(box) -> None:
    fake, *_ = box
    proc = _run(box)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "--mode auto chose full" in proc.stdout
    assert "agent_run" in fake.tool_calls


@pytest.mark.parametrize(
    ("health", "model_id"), [(503, "qwen3"), (200, "")], ids=["down", "no-model"]
)
def test_auto_mode_goes_reduced_without_a_healthy_model(box, health: int, model_id: str) -> None:
    fake, *_ = box
    fake.health, fake.model_id = health, model_id
    proc = _run(box)
    assert proc.returncode == 3
    assert "--mode auto chose reduced" in proc.stdout
    assert "agent_run" not in fake.tool_calls


def test_a_key_that_still_works_after_revoke_fails_the_probe(box) -> None:
    fake, *_ = box
    fake.ignore_revoke = True
    proc = _run(box, "--mode", "reduced")
    assert proc.returncode == 1
    assert "FAIL: [6]" in proc.stderr
    assert "expected HTTP 401" in proc.stderr


def test_an_admin_tool_list_with_approval_approve_fails_the_probe(box) -> None:
    fake, *_ = box
    fake.leak_approve = True
    proc = _run(box, "--mode", "reduced")
    assert proc.returncode == 1
    assert "approval_approve" in proc.stderr


def test_keep_skips_the_revoke(box) -> None:
    _fake, _url, _stub, log = box
    proc = _run(box, "--mode", "reduced", "--keep")
    assert proc.returncode == 3  # --keep alone never changes the exit code: reduced still does
    assert "SKIPPED (--keep)" in proc.stdout
    assert "bridge revoke" not in _verbs(log)


def test_keep_with_a_full_run_exits_0(box) -> None:
    proc = _run(box, "--mode", "full", "--keep")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_a_probe_that_cannot_reach_the_box_still_revokes_its_client(box) -> None:
    _fake, _url, _stub, log = box
    proc = _run(box, base="http://127.0.0.1:9")  # nothing listens on the discard port
    assert proc.returncode == 1
    assert "FAIL: [2]" in proc.stderr
    assert _verbs(log) == ["bridge add", "bridge revoke"]
