"""The ``hal0-agents`` MCP server: the run tools of the agent bridge (ADR-0043).

``hal0.mcp.agents`` is a thin projection of :class:`~hal0.bridge.service.BridgeService`
(decision 9), so these tests pin the projection, not the lifecycle (that is
``tests/bridge/test_service_runs.py``): the nine tool names, a start-and-wait
round trip against ``FakeHermes``, the caller each call acts as, a foreign run
reading as a missing one, failures raised (never returned as success payloads),
and the 16 KiB cap on a run's text.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP

from hal0.api import mcp_mount
from hal0.api.auth import ANON, AuthPrincipal
from hal0.bridge.hermes_client import HermesGateway
from hal0.bridge.service import BridgeService
from hal0.bridge.store import BridgeStore
from hal0.bridge.watcher import WatcherTiming
from hal0.config.schema import BridgeConfig, Hal0Config
from hal0.events import EventBus
from hal0.mcp import admin, agents
from hal0.mcp.approval_queue import ApprovalQueue
from tests.bridge._fake_hermes import FakeHermes, sse_frame

FAST = WatcherTiming(backoff_s=(0.01,))

TOOL_NAMES = sorted(
    [
        "agents_list",
        "agent_run",
        "agent_run_wait",
        "agent_run_status",
        "agent_run_events",
        "agent_run_answer",
        "agent_run_steer",
        "agent_run_stop",
        "agent_runs_list",
    ]
)

CC = AuthPrincipal(tier="bridge", name="cc", scopes=frozenset({"agents"}))
OTHER = AuthPrincipal(tier="bridge", name="other", scopes=frozenset({"agents"}))


def _payload(out: Any) -> dict[str, Any]:
    """A tool result as a dict, whichever shape FastMCP's ``call_tool`` hands back."""
    if isinstance(out, tuple):
        out = out[1] if isinstance(out[1], dict) else out[0]
    if isinstance(out, dict):
        return out
    parsed: dict[str, Any] = json.loads(out[0].text)
    return parsed


class _Pong:
    """A FakeHermes handler whose new runs stay ``running`` and replay a short plain reply."""

    def __init__(self, fake: FakeHermes) -> None:
        self.fake = fake

    def __call__(self, request: httpx.Request) -> httpx.Response:
        resp = self.fake(request)
        if request.method == "POST" and request.url.path.endswith("/v1/runs"):
            body = json.loads(resp.read())
            if resp.status_code == 202 and not body["replayed"]:
                rid = body["run_id"]
                run = self.fake.runs[rid]
                run.statuses = ["running"]
                run.polls = 0
                run.frames = [
                    (1, sse_frame(1, "message.delta", rid, delta="po")),
                    (2, sse_frame(2, "message.delta", rid, delta="ng")),
                    (
                        3,
                        sse_frame(
                            3,
                            "run.completed",
                            rid,
                            completed=True,
                            output="pong",
                            usage={"total_tokens": 9},
                        ),
                    ),
                ]
        return resp


@pytest.fixture
def fake() -> FakeHermes:
    return FakeHermes()


@pytest.fixture
def store(tmp_path: Path) -> Iterator[BridgeStore]:
    opened = BridgeStore(tmp_path / "bridge.db")
    opened.open()
    yield opened


@pytest.fixture
async def svc(store: BridgeStore, fake: FakeHermes, tmp_path: Path) -> AsyncIterator[BridgeService]:
    gateway = HermesGateway(
        home=tmp_path, environ=fake.environ(), transport=httpx.MockTransport(_Pong(fake))
    )
    service = BridgeService(
        store=store,
        gateway=gateway,
        config=BridgeConfig(),
        approval_queue=ApprovalQueue(),
        events=EventBus(),
        timing=FAST,
    )
    yield service
    await service.close()


def _server(
    svc: Any, principal: AuthPrincipal | Callable[[], AuthPrincipal] | None = CC
) -> FastMCP:
    resolver = principal if callable(principal) else (lambda: principal or ANON)
    return agents.build_server(
        state=SimpleNamespace(bridge_service=svc), principal_resolver=resolver
    )


@pytest.fixture
def server(svc: BridgeService) -> FastMCP:
    return _server(svc, CC)


@pytest.fixture
def server_other(svc: BridgeService) -> FastMCP:
    return _server(svc, OTHER)


@pytest.fixture
def server_anon(svc: BridgeService) -> FastMCP:
    return _server(svc, ANON)


# -- the tool list -----------------------------------------------------------------------


async def test_tools_list_names(server: FastMCP) -> None:
    assert sorted(t.name for t in await server.list_tools()) == TOOL_NAMES


async def test_every_tool_has_a_description_and_no_ctx_parameter(server: FastMCP) -> None:
    for tool in await server.list_tools():
        assert tool.description, tool.name
        assert "ctx" not in tool.inputSchema.get("properties", {}), tool.name


async def test_agent_run_schema_is_typed(server: FastMCP) -> None:
    run = next(t for t in await server.list_tools() if t.name == "agent_run")
    props = run.inputSchema["properties"]
    assert set(props) == {
        "agent",
        "input",
        "session_key",
        "instructions",
        "reasoning_effort",
        "idempotency_key",
        "wait_s",
    }
    assert set(run.inputSchema["required"]) == {"agent", "input"}


def test_definitions_built_once() -> None:
    assert agents._agents_tool_definitions() is agents._agents_tool_definitions()


# -- running -----------------------------------------------------------------------------


async def test_agent_run_with_wait_round_trip(server: FastMCP) -> None:
    out = _payload(
        await server.call_tool("agent_run", {"agent": "hermes", "input": "Reply pong", "wait_s": 5})
    )
    assert out["status"] == "completed" and out["output"] == "pong"
    assert out["client"] == "cc"


async def test_agent_run_without_wait_returns_the_started_view(server: FastMCP) -> None:
    out = _payload(await server.call_tool("agent_run", {"agent": "hermes", "input": "Reply pong"}))
    assert out["run_id"].startswith("run_")
    assert out["status"] in ("started", "queued", "running")
    assert "timed_out" not in out  # no wait, so no wait fields


async def test_wait_then_status_then_events_then_list(server: FastMCP) -> None:
    started = _payload(await server.call_tool("agent_run", {"agent": "hermes", "input": "go"}))
    run_id = started["run_id"]
    done = _payload(await server.call_tool("agent_run_wait", {"run_id": run_id, "timeout_s": 5}))
    assert done["status"] == "completed" and done["timed_out"] is False
    status = _payload(await server.call_tool("agent_run_status", {"run_id": run_id}))
    assert status["status"] == "completed" and status["output"] == "pong"
    events = _payload(await server.call_tool("agent_run_events", {"run_id": run_id}))
    assert [e["event"] for e in events["events"]][-1] == "run.completed"
    assert events["last_seq"] >= 1
    listed = _payload(await server.call_tool("agent_runs_list", {"limit": 5}))
    assert [r["run_id"] for r in listed["runs"]] == [run_id]


async def test_agents_list_names_the_profiles(server: FastMCP) -> None:
    out = _payload(await server.call_tool("agents_list", {}))
    ids = [a["id"] for a in out["agents"]]
    assert ids == ["hermes", "pi"]


# -- identity ----------------------------------------------------------------------------


async def test_anon_principal_is_error(server_anon: FastMCP) -> None:
    with pytest.raises(Exception) as e:
        await server_anon.call_tool("agents_list", {})
    assert "auth.forbidden" in str(e.value)


async def test_other_clients_run_is_not_found(
    server: FastMCP, server_other: FastMCP, svc: BridgeService
) -> None:
    started = _payload(await server.call_tool("agent_run", {"agent": "hermes", "input": "x"}))
    for tool in ("agent_run_status", "agent_run_stop"):
        with pytest.raises(Exception) as e:
            await server_other.call_tool(tool, {"run_id": started["run_id"]})
        assert "bridge.run_not_found" in str(e.value)
    listed = _payload(await server_other.call_tool("agent_runs_list", {}))
    assert listed["runs"] == []


async def test_the_resolver_is_asked_on_every_call(svc: BridgeService) -> None:
    principals = iter([CC, OTHER])
    server = _server(svc, lambda: next(principals))
    first = _payload(await server.call_tool("agent_run", {"agent": "hermes", "input": "x"}))
    assert first["client"] == "cc"
    second = _payload(await server.call_tool("agent_run", {"agent": "hermes", "input": "y"}))
    assert second["client"] == "other"


async def test_no_resolver_means_anonymous(svc: BridgeService) -> None:
    server = agents.build_server(state=SimpleNamespace(bridge_service=svc))
    with pytest.raises(Exception) as e:
        await server.call_tool("agents_list", {})
    assert "auth.forbidden" in str(e.value)


# -- failures are raised -----------------------------------------------------------------


async def test_service_absent_is_bridge_disabled() -> None:
    server = agents.build_server(
        state=SimpleNamespace(bridge_service=None), principal_resolver=lambda: CC
    )
    with pytest.raises(Exception) as e:
        await server.call_tool("agents_list", {})
    assert "bridge.disabled" in str(e.value)


async def test_state_without_the_attribute_is_bridge_disabled() -> None:
    server = agents.build_server(state=SimpleNamespace(), principal_resolver=lambda: CC)
    with pytest.raises(Exception) as e:
        await server.call_tool("agent_runs_list", {})
    assert "bridge.disabled" in str(e.value)


async def test_service_is_read_at_call_time(svc: BridgeService) -> None:
    state = SimpleNamespace(bridge_service=None)
    server = agents.build_server(state=state, principal_resolver=lambda: CC)
    with pytest.raises(Exception, match=r"bridge\.disabled"):
        await server.call_tool("agents_list", {})
    state.bridge_service = svc
    assert _payload(await server.call_tool("agents_list", {}))["agents"]


async def test_bridge_error_marks_the_result_is_error(server_other: FastMCP) -> None:
    """Through the protocol handler: a raised failure is ``isError``, not a success payload."""
    import mcp.types as mt

    handler = server_other._mcp_server.request_handlers[mt.CallToolRequest]
    request = mt.CallToolRequest(
        method="tools/call",
        params=mt.CallToolRequestParams(name="agent_run_status", arguments={"run_id": "run_nope"}),
    )
    result = await handler(request)
    assert result.root.isError is True
    assert "bridge.run_not_found" in result.root.content[0].text


async def test_steering_a_finished_run_is_a_409_error(server: FastMCP) -> None:
    started = _payload(await server.call_tool("agent_run", {"agent": "hermes", "input": "x"}))
    await server.call_tool("agent_run_wait", {"run_id": started["run_id"], "timeout_s": 5})
    with pytest.raises(Exception) as e:
        await server.call_tool("agent_run_steer", {"run_id": started["run_id"], "input": "more"})
    assert "bridge.run_not_running" in str(e.value)


# -- the service is called with the caller and the typed arguments -----------------------


class _Recorder:
    """A BridgeService stand-in recording each call as ``(method, caller, args)``."""

    def __init__(self, view: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, Any, dict[str, Any]]] = []
        self.view = view if view is not None else {"run_id": "run_1", "status": "running"}

    def __getattr__(self, name: str) -> Callable[..., Any]:
        async def call(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, args[0] if args else None, {"args": args[1:], **kwargs}))
            if name == "list_agents":
                return {"agents": []}
            if name == "list_runs":
                return [self.view]
            if name == "run_events":
                return [{"seq": 4, "ts": 1.0, "event": "message.text", "payload": {"text": "hi"}}]
            return dict(self.view)

        return call


async def test_arguments_reach_the_service() -> None:
    rec = _Recorder()
    server = _server(rec, CC)
    await server.call_tool(
        "agent_run",
        {
            "agent": "hermes/coder",
            "input": "do it",
            "session_key": "s1",
            "instructions": "be brief",
            "reasoning_effort": "low",
            "idempotency_key": "k1",
        },
    )
    method, caller, kwargs = rec.calls[-1]
    assert method == "start_run" and (caller.tier, caller.name) == ("bridge", "cc")
    assert kwargs == {
        "args": (),
        "agent": "hermes/coder",
        "input": "do it",
        "session_key": "s1",
        "instructions": "be brief",
        "reasoning_effort": "low",
        "idempotency_key": "k1",
    }

    await server.call_tool("agent_run_wait", {"run_id": "run_1", "timeout_s": 7})
    assert rec.calls[-1][0] == "wait_run" and rec.calls[-1][2]["timeout_s"] == 7
    await server.call_tool(
        "agent_run_answer", {"run_id": "run_1", "request_id": "r", "answer": "a"}
    )
    assert rec.calls[-1][2] == {"args": ("run_1",), "request_id": "r", "answer": "a"}
    await server.call_tool("agent_run_steer", {"run_id": "run_1", "input": "more"})
    assert rec.calls[-1][0] == "steer_run" and rec.calls[-1][2]["input"] == "more"
    await server.call_tool("agent_run_stop", {"run_id": "run_1"})
    assert rec.calls[-1][0] == "stop_run"
    await server.call_tool("agent_runs_list", {"status": "running", "limit": 3})
    assert rec.calls[-1][2] == {"args": (), "status": "running", "limit": 3}
    await server.call_tool("agent_run_events", {"run_id": "run_1", "since_seq": 3, "limit": 9})
    assert rec.calls[-1][2] == {"args": ("run_1",), "since_seq": 3, "limit": 9}


async def test_admin_principal_acts_as_admin() -> None:
    rec = _Recorder()
    server = _server(rec, AuthPrincipal(tier="admin"))
    await server.call_tool("agent_runs_list", {})
    _, caller, _ = rec.calls[-1]
    assert (caller.tier, caller.name) == ("admin", "admin")


async def test_wait_s_zero_does_not_wait_and_positive_waits() -> None:
    rec = _Recorder()
    server = _server(rec, CC)
    await server.call_tool("agent_run", {"agent": "hermes", "input": "x"})
    assert [c[0] for c in rec.calls] == ["start_run"]
    await server.call_tool("agent_run", {"agent": "hermes", "input": "x", "wait_s": 9})
    assert [c[0] for c in rec.calls][-2:] == ["start_run", "wait_run"]
    assert rec.calls[-1][2]["timeout_s"] == 9


# -- output: redaction and the 16 KiB cap ------------------------------------------------


async def test_output_and_error_are_scrubbed() -> None:
    view = {
        "run_id": "run_1",
        "status": "failed",
        "output": "curl -H 'Authorization: Bearer hal0bk_cc.s3cr3tvalue0123456789' http://x",
        "error": "boom HF_TOKEN=hf_abcdefghijklmnopqrstuvwxyz0123456789",
    }
    server = _server(_Recorder(view), CC)
    out = _payload(await server.call_tool("agent_run_status", {"run_id": "run_1"}))
    assert "s3cr3tvalue0123456789" not in out["output"]
    assert "hf_abcdefghijklmnopqrstuvwxyz0123456789" not in out["error"]
    assert out["output"].startswith("curl -H 'Authorization: Bearer")


async def test_output_over_16_kib_is_cut_with_a_pointer_to_the_events() -> None:
    view = {"run_id": "run_1", "status": "completed", "output": "x" * 40_000, "last_seq": 12}
    server = _server(_Recorder(view), CC)
    out = _payload(await server.call_tool("agent_run_status", {"run_id": "run_1"}))
    assert len(out["output"].encode()) <= 16 * 1024
    assert out["truncated"] is True
    assert out["more"] == "agent_run_events(run_id, since_seq=12)"


async def test_output_under_the_cap_is_whole() -> None:
    view = {"run_id": "run_1", "status": "completed", "output": "x" * 16_384, "last_seq": 2}
    server = _server(_Recorder(view), CC)
    out = _payload(await server.call_tool("agent_run_status", {"run_id": "run_1"}))
    assert out["output"] == "x" * 16_384
    assert "truncated" not in out and "more" not in out


async def test_the_cap_never_splits_a_character() -> None:
    view = {"run_id": "run_1", "status": "completed", "output": "é" * 20_000, "last_seq": 1}
    server = _server(_Recorder(view), CC)
    out = _payload(await server.call_tool("agent_run_status", {"run_id": "run_1"}))
    assert out["truncated"] is True
    out["output"].encode()  # a split code point would not survive the round trip
    assert set(out["output"]) == {"é"}


async def test_events_are_scrubbed_and_capped_too() -> None:
    class _Events(_Recorder):
        async def run_events(self, *_a: Any, **_kw: Any) -> list[dict[str, Any]]:
            return [
                {
                    "seq": 7,
                    "ts": 1.0,
                    "event": "tool.completed",
                    "payload": {"out": "Authorization: Bearer hal0bk_cc.s3cr3tvalue0123456789"},
                },
                {"seq": 8, "ts": 2.0, "event": "message.text", "payload": {"text": "z" * 30_000}},
            ]

    server = _server(_Events(), CC)
    out = _payload(await server.call_tool("agent_run_events", {"run_id": "run_1"}))
    assert "s3cr3tvalue0123456789" not in json.dumps(out)
    assert len(out["events"][1]["payload"]["text"].encode()) <= 16 * 1024
    assert out["truncated"] is True and out["last_seq"] == 8


async def test_an_empty_events_page_keeps_the_callers_position() -> None:
    class _Empty(_Recorder):
        async def run_events(self, *_a: Any, **_kw: Any) -> list[dict[str, Any]]:
            return []

    out = _payload(
        await _server(_Empty(), CC).call_tool("agent_run_events", {"run_id": "r", "since_seq": 41})
    )
    assert out == {"run_id": "r", "events": [], "last_seq": 41}


async def test_runs_list_scrubs_and_caps_each_run() -> None:
    view = {"run_id": "run_1", "status": "completed", "output": "y" * 30_000, "last_seq": 5}
    server = _server(_Recorder(view), CC)
    listed = _payload(await server.call_tool("agent_runs_list", {}))
    (run,) = listed["runs"]
    assert run["truncated"] is True and len(run["output"].encode()) <= 16 * 1024


# -- the mount ---------------------------------------------------------------------------


def _mount(monkeypatch: pytest.MonkeyPatch, *, enabled: bool = True) -> FastAPI:
    monkeypatch.setattr(admin, "install_admin_route_map", lambda _app: None)
    app = FastAPI()
    app.state.hal0_config = Hal0Config(bridge=BridgeConfig(enabled=enabled))
    app.state.approval_queue = ApprovalQueue()
    mcp_mount.mount_mcp_servers(app, approval_queue=app.state.approval_queue)
    return app


def test_mounted_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _mount(monkeypatch)
    assert "hal0-agents" in app.state.mcp_servers
    assert any(getattr(r, "path", None) == "/mcp/agents" for r in app.routes)
    assert app.state.mcp_servers["hal0-agents"].session_manager in app.state.mcp_session_managers


def test_not_mounted_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _mount(monkeypatch, enabled=False)
    assert "hal0-agents" not in app.state.mcp_servers
    assert not any(getattr(r, "path", None) == "/mcp/agents" for r in app.routes)


async def test_mount_tolerates_the_service_appearing_later(
    monkeypatch: pytest.MonkeyPatch, svc: BridgeService
) -> None:
    """The boot phase publishes ``app.state.bridge_service`` after the mount (ADR-0043)."""
    from starlette.requests import Request

    app = _mount(monkeypatch)
    request = Request({"type": "http", "headers": [], "hal0.bridge_principal": CC})
    monkeypatch.setattr(mcp_mount, "_current_mcp_request", lambda: request)
    server = app.state.mcp_servers["hal0-agents"]
    with pytest.raises(Exception) as e:
        await server.call_tool("agents_list", {})
    assert "bridge.disabled" in str(e.value)
    app.state.bridge_service = svc  # the server holds app.state
    assert _payload(await server.call_tool("agents_list", {}))["agents"]


def test_bundled_server_ids_name_the_agents_server() -> None:
    from hal0.mcp.installed import BUNDLED_SERVER_IDS

    assert "hal0-agents" in BUNDLED_SERVER_IDS


# -- the principal resolver --------------------------------------------------------------


def test_principal_resolver_outside_a_request_is_anon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_mount, "_current_mcp_request", lambda: None)
    assert mcp_mount.principal_resolver().tier == "anon"


def test_principal_resolver_reads_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    from starlette.requests import Request

    request = Request({"type": "http", "headers": []})
    monkeypatch.setattr(mcp_mount, "_current_mcp_request", lambda: request)
    monkeypatch.setattr(mcp_mount, "resolve_principal_from_scope", lambda _r: CC)
    assert mcp_mount.principal_resolver() == CC


def test_a_principal_stored_on_the_scope_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """The middleware's verdict is used as is; the resolver does not re-verify the key."""
    from starlette.requests import Request

    stored = AuthPrincipal(tier="bridge", name="stored", scopes=frozenset({"agents"}))
    request = Request({"type": "http", "headers": [], "hal0.bridge_principal": stored})
    monkeypatch.setattr(mcp_mount, "_current_mcp_request", lambda: request)

    def _no_reverify(_r: Any) -> AuthPrincipal:
        raise AssertionError("resolve_principal_from_scope called with a stored principal")

    monkeypatch.setattr(mcp_mount, "resolve_principal_from_scope", _no_reverify)
    assert mcp_mount.principal_resolver() == stored
