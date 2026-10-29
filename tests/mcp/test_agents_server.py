"""The ``hal0-agents`` MCP server: the run tools of the agent bridge (ADR-0043).

``hal0.mcp.agents`` is a thin projection of :class:`~hal0.bridge.service.BridgeService`
(decision 9), so these tests pin the projection, not the lifecycle (that is
``tests/bridge/test_service_runs.py``): the nine run tool names (the seven teach
tools are in ``test_agents_server_teach.py``), a start-and-wait
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
from hal0.bridge.errors import BRIDGE_ERROR_STATUS, BridgeError
from hal0.bridge.hermes_client import HermesGateway
from hal0.bridge.service import BridgeService
from hal0.bridge.store import BridgeStore, RunRow
from hal0.bridge.watcher import WatcherTiming
from hal0.config.schema import BridgeConfig, Hal0Config
from hal0.events import EventBus
from hal0.mcp import admin, agents
from hal0.mcp.approval_queue import ApprovalQueue
from tests.bridge._fake_hermes import FakeHermes, sse_frame

FAST = WatcherTiming(backoff_s=(0.01,))

#: The run tools. The teach tools (ADR-0043 decision 10) are listed in ``test_agents_server_teach.py``.
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
    """A FakeHermes handler whose new runs stay ``running`` and replay a short plain reply.

    ``chunks`` replaces the two-delta ``pong`` with one ``message.delta`` per chunk and a
    ``run.completed`` whose output is all of them: a long answer for the 16 KiB cap tests.
    """

    def __init__(self, fake: FakeHermes, chunks: list[str] | None = None) -> None:
        self.fake = fake
        self.chunks = chunks

    def __call__(self, request: httpx.Request) -> httpx.Response:
        resp = self.fake(request)
        if request.method == "POST" and request.url.path.endswith("/v1/runs"):
            body = json.loads(resp.read())
            if resp.status_code == 202 and not body["replayed"]:
                rid = body["run_id"]
                run = self.fake.runs[rid]
                run.statuses = ["running"]
                run.polls = 0
                if self.chunks is not None:
                    n = len(self.chunks)
                    run.frames = [
                        (i, sse_frame(i, "message.delta", rid, delta=text))
                        for i, text in enumerate(self.chunks, start=1)
                    ]
                    run.frames.append(
                        (
                            n + 1,
                            sse_frame(
                                n + 1,
                                "run.completed",
                                rid,
                                completed=True,
                                output="".join(self.chunks),
                                usage={"total_tokens": 9},
                            ),
                        )
                    )
                    return resp
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
def pong_chunks(request: pytest.FixtureRequest) -> list[str] | None:
    """The reply the fake Hermes streams, when a test parametrizes it (indirectly)."""
    chunks: list[str] | None = getattr(request, "param", None)
    return chunks


@pytest.fixture
async def svc(
    store: BridgeStore, fake: FakeHermes, tmp_path: Path, pong_chunks: list[str] | None
) -> AsyncIterator[BridgeService]:
    gateway = HermesGateway(
        home=tmp_path,
        environ=fake.environ(),
        transport=httpx.MockTransport(_Pong(fake, pong_chunks)),
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
    listed = sorted(t.name for t in await server.list_tools())
    assert [name for name in listed if name in TOOL_NAMES] == TOOL_NAMES
    assert len(listed) == len(TOOL_NAMES) + 7  # + the teach tools


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


async def test_service_absent_is_bridge_unavailable() -> None:
    """Mounted (so enabled) but no service: 503 ``bridge.unavailable``, as REST answers."""
    server = agents.build_server(
        state=SimpleNamespace(bridge_service=None), principal_resolver=lambda: CC
    )
    with pytest.raises(Exception) as e:
        await server.call_tool("agents_list", {})
    assert "bridge.unavailable" in str(e.value)
    assert "bridge.disabled" not in str(e.value)
    assert BRIDGE_ERROR_STATUS["bridge.unavailable"] == 503


async def test_state_without_the_attribute_is_bridge_unavailable() -> None:
    server = agents.build_server(state=SimpleNamespace(), principal_resolver=lambda: CC)
    with pytest.raises(Exception) as e:
        await server.call_tool("agent_runs_list", {})
    assert "bridge.unavailable" in str(e.value)


async def test_service_is_read_at_call_time(svc: BridgeService) -> None:
    state = SimpleNamespace(bridge_service=None)
    server = agents.build_server(state=state, principal_resolver=lambda: CC)
    with pytest.raises(Exception, match=r"bridge\.unavailable"):
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
            if name == "run_events_window":
                event = {"seq": 4, "ts": 1.0, "event": "message.text", "payload": {"text": "hi"}}
                return [], [event], [], False
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
    assert rec.calls[-1][0] == "run_events_window"
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


async def test_output_over_16_kib_is_cut_with_a_pointer_to_the_next_window() -> None:
    view = {"run_id": "run_1", "status": "completed", "output": "x" * 40_000, "last_seq": 12}
    server = _server(_Recorder(view), CC)
    out = _payload(await server.call_tool("agent_run_status", {"run_id": "run_1"}))
    assert out["output"] == "x" * 16_384
    assert out["truncated"] is True
    assert (out["output_offset"], out["output_total"], out["next_offset"]) == (0, 40_000, 16_384)
    assert out["more"] == "agent_run_status(run_id, offset=16384)"


async def test_status_offset_pages_the_whole_output() -> None:
    text = "".join(f"{i:05d}" for i in range(8_000))  # 40 000 bytes, every window different
    server = _server(_Recorder({"run_id": "run_1", "status": "completed", "output": text}), CC)
    got: list[str] = []
    offset: int | None = 0
    while offset is not None:
        out = _payload(
            await server.call_tool("agent_run_status", {"run_id": "run_1", "offset": offset})
        )
        assert out["output_offset"] == offset and out["output_total"] == 40_000
        got.append(out["output"])
        if out["next_offset"] is None:
            assert "truncated" not in out and "more" not in out
        else:
            assert out["more"] == f"agent_run_status(run_id, offset={out['next_offset']})"
        offset = out["next_offset"]
    assert [len(w) for w in got] == [16_384, 16_384, 7_232]
    assert "".join(got) == text
    past = _payload(
        await server.call_tool("agent_run_status", {"run_id": "run_1", "offset": 99_999})
    )
    assert past["output"] == "" and past["output_offset"] == 40_000 and past["next_offset"] is None


async def test_status_refuses_a_negative_offset_as_the_rest_route_does() -> None:
    server = _server(_Recorder({"run_id": "run_1", "status": "completed", "output": "x"}), CC)
    with pytest.raises(Exception) as e:
        await server.call_tool("agent_run_status", {"run_id": "run_1", "offset": -1})
    assert "bridge.invalid_offset" in str(e.value)
    assert "bridge.internal" not in str(e.value)
    assert BridgeError("bridge.invalid_offset", "x").status == 422


async def test_status_offset_never_splits_a_character() -> None:
    text = "é" * 20_000  # two bytes each: 16 384 is a boundary, 16 385 is not
    server = _server(_Recorder({"run_id": "run_1", "status": "completed", "output": text}), CC)
    mid = _payload(
        await server.call_tool("agent_run_status", {"run_id": "run_1", "offset": 16_385})
    )
    assert mid["output_offset"] == 16_386  # moved forward to the next character
    assert set(mid["output"]) == {"é"}


async def test_status_pages_the_masked_text() -> None:
    """Masking runs on the whole output first: no window edge can split a secret."""
    secret = "Authorization: Bearer hal0bk_cc.s3cr3tvalue0123456789"
    text = "a" * (16_384 - 30) + secret + "b" * 100
    server = _server(_Recorder({"run_id": "run_1", "status": "completed", "output": text}), CC)
    first = _payload(await server.call_tool("agent_run_status", {"run_id": "run_1"}))
    rest = _payload(
        await server.call_tool(
            "agent_run_status", {"run_id": "run_1", "offset": first["next_offset"]}
        )
    )
    assert "s3cr3tvalue" not in first["output"] + rest["output"]
    assert "0123456789" not in first["output"] + rest["output"]


#: 20 deltas of 2.1 KiB: each flushes as its own ``message.text`` event, 42 KB in all.
LONG_REPLY = [f"{i:02d}" + "abcdefghij" * 209 for i in range(20)]


@pytest.mark.parametrize("pong_chunks", [LONG_REPLY], indirect=True)
async def test_following_the_more_pointer_returns_the_text(server: FastMCP) -> None:
    full = "".join(LONG_REPLY)
    done = _payload(
        await server.call_tool("agent_run", {"agent": "hermes", "input": "talk", "wait_s": 5})
    )
    assert done["status"] == "completed" and done["truncated"] is True
    assert done["output"] == full[: len(done["output"])] and len(done["output"]) == 16_384
    assert done["more"] == f"agent_run_status(run_id, offset={done['next_offset']})"
    assert done["output_total"] == len(full.encode())
    text = done["output"]
    offset = done["next_offset"]
    while offset is not None:
        page = _payload(
            await server.call_tool("agent_run_status", {"run_id": done["run_id"], "offset": offset})
        )
        text += page["output"]
        offset = page["next_offset"]
    assert text == full


#: The reviewer's probe: 8 000 four-byte deltas, a 32 000-byte answer. Hermes numbers
#: every delta, the watcher stores ~2 KiB of them per ``message.text`` row.
PROBE_REPLY = ["abcd"] * 8_000


@pytest.mark.parametrize("pong_chunks", [PROBE_REPLY], indirect=True)
async def test_a_long_answer_of_small_deltas_is_recoverable_whole(server: FastMCP) -> None:
    full = "".join(PROBE_REPLY)
    done = _payload(
        await server.call_tool("agent_run", {"agent": "hermes", "input": "talk", "wait_s": 30})
    )
    assert done["status"] == "completed" and done["output_total"] == 32_000
    text, offset = done["output"], done["next_offset"]
    while offset is not None:
        page = _payload(
            await server.call_tool("agent_run_status", {"run_id": done["run_id"], "offset": offset})
        )
        text, offset = text + page["output"], page["next_offset"]
    assert text == full
    # The events alone hold every message.text row: the cap counts rows, not seqs.
    page = _payload(
        await server.call_tool(
            "agent_run_events", {"run_id": done["run_id"], "since_seq": 0, "limit": 500}
        )
    )
    texts = [e["payload"]["text"] for e in page["events"] if e["event"] == "message.text"]
    assert "".join(texts) == full


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
        async def run_events_window(self, *_a: Any, **_kw: Any) -> Any:
            return (
                [],
                [
                    {
                        "seq": 7,
                        "ts": 1.0,
                        "event": "tool.completed",
                        "payload": {"out": "Authorization: Bearer hal0bk_cc.s3cr3tvalue0123456789"},
                    },
                    {
                        "seq": 8,
                        "ts": 2.0,
                        "event": "message.text",
                        "payload": {"text": "z" * 30_000},
                    },
                ],
                [],
                False,
            )

    server = _server(_Events(), CC)
    out = _payload(await server.call_tool("agent_run_events", {"run_id": "run_1"}))
    assert "s3cr3tvalue0123456789" not in json.dumps(out)
    assert len(out["events"][1]["payload"]["text"].encode()) <= 16 * 1024
    assert out["truncated"] is True and out["last_seq"] == 8


async def test_an_empty_events_page_keeps_the_callers_position() -> None:
    class _Empty(_Recorder):
        async def run_events_window(self, *_a: Any, **_kw: Any) -> Any:
            return [], [], [], True

    out = _payload(
        await _server(_Empty(), CC).call_tool("agent_run_events", {"run_id": "r", "since_seq": 41})
    )
    assert out == {"run_id": "r", "events": [], "last_seq": 41}


async def test_runs_list_items_preview_the_output() -> None:
    """A list item carries ``output_preview`` (200 characters) and ``error``, not the output."""
    view = {
        "run_id": "run_1",
        "status": "failed",
        "output": "Authorization: Bearer hal0bk_cc.s3cr3tvalue0123456789 " + "y" * 30_000,
        "error": "e" * 20_000,
        "last_seq": 5,
    }
    server = _server(_Recorder(view), CC)
    listed = _payload(await server.call_tool("agent_runs_list", {}))
    (run,) = listed["runs"]
    assert "output" not in run and "next_offset" not in run and "more" not in run
    assert len(run["output_preview"]) == 200
    assert run["output_preview"].startswith("Authorization: Bearer")
    assert "s3cr3tvalue" not in run["output_preview"]
    assert len(run["error"].encode()) <= 16 * 1024 and run["truncated"] is True
    quiet = _server(_Recorder({"run_id": "run_2", "status": "running", "output": None}), CC)
    (item,) = _payload(await quiet.call_tool("agent_runs_list", {}))["runs"]
    assert item["output_preview"] is None and "truncated" not in item


async def test_pending_and_input_preview_are_scrubbed_like_output() -> None:
    """A clarification's question and the echoed task text can carry a secret too."""
    view = {
        "run_id": "run_1",
        "status": "waiting_for_clarification",
        "input_preview": "deploy with Authorization: Bearer hal0bk_cc.s3cr3tvalue0123456789",
        "pending": {
            "event": "clarify.request",
            "request_id": "r1",
            "question": "Which token? HF_TOKEN=hf_abcdefghijklmnopqrstuvwxyz0123456789",
            "choices": ["a", "b"],
        },
        "last_seq": 3,
    }
    out = _payload(
        await _server(_Recorder(view), CC).call_tool("agent_run_status", {"run_id": "run_1"})
    )
    blob = json.dumps(out)
    assert "s3cr3tvalue0123456789" not in blob
    assert "hf_abcdefghijklmnopqrstuvwxyz0123456789" not in blob
    assert out["pending"]["request_id"] == "r1" and out["pending"]["choices"] == ["a", "b"]
    assert out["input_preview"].startswith("deploy with Authorization: Bearer")
    assert "truncated" not in out


# -- an unexpected failure is not shown to the remote client -------------------------------


class _Exploding(_Recorder):
    """A service whose every call fails with something that is not a BridgeError."""

    def __getattr__(self, name: str) -> Callable[..., Any]:
        async def call(*_a: Any, **_kw: Any) -> Any:
            raise RuntimeError("sqlite3.OperationalError at /var/lib/hal0/bridge.db: disk I/O")

        return call


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("agents_list", {}),
        ("agent_run", {"agent": "hermes", "input": "x"}),
        ("agent_run", {"agent": "hermes", "input": "x", "wait_s": 3}),
        ("agent_run_wait", {"run_id": "r"}),
        ("agent_run_status", {"run_id": "r"}),
        ("agent_run_events", {"run_id": "r"}),
        ("agent_run_answer", {"run_id": "r", "request_id": "q", "answer": "a"}),
        ("agent_run_steer", {"run_id": "r", "input": "x"}),
        ("agent_run_stop", {"run_id": "r"}),
        ("agent_runs_list", {}),
    ],
)
async def test_an_unexpected_exception_becomes_bridge_internal(
    tool: str, args: dict[str, Any]
) -> None:
    """Every tool: detail is logged on hal0-api, the client gets a fixed ``bridge.internal``."""
    import mcp.types as mt
    import structlog

    server = _server(_Exploding(), CC)
    handler = server._mcp_server.request_handlers[mt.CallToolRequest]
    request = mt.CallToolRequest(
        method="tools/call", params=mt.CallToolRequestParams(name=tool, arguments=args)
    )
    with structlog.testing.capture_logs() as logs:
        result = await handler(request)
    assert result.root.isError is True
    text = result.root.content[0].text
    assert "bridge.internal: internal error; see hal0-api logs" in text
    assert "sqlite3" not in text and "/var/lib/hal0" not in text and "RuntimeError" not in text
    (entry,) = [e for e in logs if e["event"] == "hal0.mcp.agents.internal_error"]
    assert entry["log_level"] == "error" and entry["tool"] == tool and entry["client"] == "cc"
    assert "disk I/O" in entry["error"] and entry.get("exc_info") is True


async def test_an_unparseable_hermes_address_never_puts_the_key_in_the_log(
    store: BridgeStore, fake: FakeHermes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reviewer's leak probe (#3137): ``API_SERVER_HOST=[::1`` and ``hal0 serve``'s logging.

    ``httpx.InvalidURL`` used to escape the Hermes client, reach the guard's
    ``log.error(exc_info=True)`` and print the profile key from the frame locals.
    It is now ``bridge.agent_unavailable``, which is never logged with a traceback.
    """
    import io
    import re

    import structlog

    from hal0.bridge import hermes_client

    monkeypatch.setattr(hermes_client, "api_server_base", lambda home: "http://[::1:8642")
    service = BridgeService(
        store=store,
        gateway=HermesGateway(home=tmp_path, environ=fake.environ(), transport=fake.transport()),
        config=BridgeConfig(),
        approval_queue=None,
        events=None,
        timing=FAST,
    )
    sio = io.StringIO()
    saved = structlog.get_config()
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(
                colors=False,
                exception_formatter=structlog.dev.RichTracebackFormatter(
                    show_locals=True, width=400
                ),
            ),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=sio),
        cache_logger_on_first_use=False,
    )
    try:
        with pytest.raises(Exception) as e:
            await _server(service, CC).call_tool("agent_run", {"agent": "hermes", "input": "hi"})
    finally:
        structlog.configure(**saved)
        await service.close()
    assert "bridge.agent_unavailable" in str(e.value)
    assert "bridge.internal" not in str(e.value)
    logged = re.sub(r"\x1b\[[0-9;]*m", "", sio.getvalue())
    assert fake.keys["default"] not in logged
    assert fake.keys["default"] not in str(e.value)


async def test_a_bridge_error_passes_through_unchanged() -> None:
    class _Refusing(_Recorder):
        async def get_run(self, *_a: Any, **_kw: Any) -> Any:
            raise BridgeError("bridge.run_not_found", "no run 'r' for client 'cc'")

    with pytest.raises(Exception) as e:
        await _server(_Refusing(), CC).call_tool("agent_run_status", {"run_id": "r"})
    assert "bridge.run_not_found: no run 'r' for client 'cc'" in str(e.value)
    assert "bridge.internal" not in str(e.value)


def test_internal_has_a_500_in_the_status_table() -> None:
    assert BRIDGE_ERROR_STATUS["bridge.internal"] == 500
    assert BridgeError("bridge.internal", "internal error; see hal0-api logs").status == 500


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
    assert "bridge.unavailable" in str(e.value)
    app.state.bridge_service = svc  # the server holds app.state
    assert _payload(await server.call_tool("agents_list", {}))["agents"]


def test_bundled_server_ids_name_the_agents_server() -> None:
    from hal0.mcp.installed import BUNDLED_SERVER_IDS

    assert "hal0-agents" in BUNDLED_SERVER_IDS


# -- the principal resolver --------------------------------------------------------------


def test_principal_resolver_outside_a_request_is_anon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_mount, "_current_mcp_request", lambda: None)
    assert mcp_mount.principal_resolver().tier == "anon"


def test_principal_resolver_is_the_judged_principal(monkeypatch: pytest.MonkeyPatch) -> None:
    from starlette.requests import Request

    request = Request({"type": "http", "headers": []})
    monkeypatch.setattr(mcp_mount, "_current_mcp_request", lambda: request)
    monkeypatch.setattr(mcp_mount, "judged_principal", lambda _r: CC)
    assert mcp_mount.principal_resolver() == CC


def _no_second_verify(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Make any bridge-key lookup verify (as a racing store would) and record it."""
    from hal0.api import auth as auth_mod

    looked_up: list[str] = []

    def _verifies(key: str) -> AuthPrincipal:
        looked_up.append(key)
        return OTHER

    monkeypatch.setattr(auth_mod, "_bridge_principal", _verifies)
    return looked_up


def test_a_principal_stored_on_the_scope_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """The middleware's verdict is used as is; the resolver does not re-verify the key."""
    from starlette.requests import Request

    looked_up = _no_second_verify(monkeypatch)
    stored = AuthPrincipal(tier="bridge", name="stored", scopes=frozenset({"agents"}))
    request = Request(
        {
            "type": "http",
            "headers": [(b"authorization", b"Bearer hal0bk_stored.abcdefghijklmnopqrstuvwxyz")],
            "hal0.bridge_principal": stored,
        }
    )
    monkeypatch.setattr(mcp_mount, "_current_mcp_request", lambda: request)
    assert mcp_mount.principal_resolver() == stored
    assert looked_up == []


def test_a_bridge_key_the_gate_did_not_store_is_never_verified_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No stored principal: the bridge-shaped bearer resolves as an unknown key does.

    Re-verifying it here could hand a tool an identity the auth gate never
    judged (a key revoked, or a store that failed, between the gate and here).
    """
    from starlette.requests import Request

    looked_up = _no_second_verify(monkeypatch)
    request = Request(
        {
            "type": "http",
            "headers": [(b"authorization", b"Bearer hal0bk_other.abcdefghijklmnopqrstuvwxyz")],
        }
    )
    monkeypatch.setattr(mcp_mount, "_current_mcp_request", lambda: request)
    assert mcp_mount.principal_resolver().tier == "anon"
    assert looked_up == []


# -- streamed text: a secret split across events is still masked (Codex on #3138) ---------


def _text_event(seq: int, text: str) -> dict[str, Any]:
    return {"seq": seq, "ts": 1.0, "event": "message.text", "payload": {"text": text}}


@pytest.mark.parametrize(
    ("left", "right", "in_left", "in_right"),
    [
        # split before the token: the first event shows the header, the second masks it all
        ("call it with Authorization: Bearer ", "supersecret0123456789 and go", "", "supersecret"),
        # split inside the token: both halves masked
        ("call it with Authorization: Bearer super", "secret0123456789 and go", "super", "secret"),
        # a *_KEY= value split inside
        ("export HAL0_ADMIN_KEY=supers", "ecret0123456789\nnext", "supers", "ecret01"),
    ],
)
async def test_a_secret_split_across_two_text_events_is_masked(
    left: str, right: str, in_left: str, in_right: str
) -> None:
    class _Split(_Recorder):
        async def run_events_window(self, *_a: Any, **_kw: Any) -> Any:
            return [], [_text_event(1, left), _text_event(2, right)], [], False

    out = _payload(await _server(_Split(), CC).call_tool("agent_run_events", {"run_id": "r"}))
    first, second = (e["payload"]["text"] for e in out["events"])
    assert "0123456789" not in first + second
    assert in_right not in second and (not in_left or in_left not in first)
    assert "REDACTED" in second and (not in_left or "REDACTED" in first)
    assert second.endswith(("and go", "next"))  # the text after the secret is kept
    assert [e["seq"] for e in out["events"]] == [1, 2] and out["last_seq"] == 2


async def test_a_secret_split_across_pages_is_masked_on_both() -> None:
    """The neighbours outside the window are context: page one's tail, page two's head."""
    left, right = "header Authorization: Bearer super", "secret0123456789abcdef done"

    class _PageOne(_Recorder):
        async def run_events_window(self, *_a: Any, **_kw: Any) -> Any:
            return [], [_text_event(1, left)], [_text_event(2, right)], False

    class _PageTwo(_Recorder):
        async def run_events_window(self, *_a: Any, **_kw: Any) -> Any:
            return [_text_event(1, left)], [_text_event(2, right)], [], True

    one = _payload(await _server(_PageOne(), CC).call_tool("agent_run_events", {"run_id": "r"}))
    two = _payload(
        await _server(_PageTwo(), CC).call_tool("agent_run_events", {"run_id": "r", "since_seq": 1})
    )
    assert [e["seq"] for e in one["events"]] == [1] and one["last_seq"] == 1
    assert [e["seq"] for e in two["events"]] == [2] and two["last_seq"] == 2
    page_one = one["events"][0]["payload"]["text"]
    page_two = two["events"][0]["payload"]["text"]
    assert page_one.startswith("header Authorization: Bearer ") and "super" not in page_one
    assert "secret0123" not in page_two and page_two.endswith(" done")


async def test_a_wait_tail_masks_a_secret_split_with_the_event_before_it() -> None:
    left, right = "token: Bearer abc", "def0123456789xyz then more"
    view = {
        "run_id": "run_1",
        "status": "completed",
        "last_seq": 2,
        "events_tail": [_text_event(2, right)],
        "timed_out": False,
    }

    class _Wait(_Recorder):
        async def events_before(self, *_a: Any, **_kw: Any) -> Any:
            return [_text_event(1, left)]

    out = _payload(
        await _server(_Wait(view), CC).call_tool(
            "agent_run_wait", {"run_id": "run_1", "timeout_s": 1}
        )
    )
    [only] = out["events_tail"]
    assert "def0123456789xyz" not in only["payload"]["text"]
    assert only["payload"]["text"].endswith(" then more")


async def test_text_without_secrets_is_unchanged_across_events() -> None:
    texts = ["Hello, ", "world. Bearer is a word here", "", " and that is all."]

    class _Plain(_Recorder):
        async def run_events_window(self, *_a: Any, **_kw: Any) -> Any:
            events = [_text_event(n + 1, t) for n, t in enumerate(texts)]
            events.insert(2, {"seq": 9, "ts": 1.0, "event": "tool.started", "payload": {}})
            return [], events, [], False

    out = _payload(await _server(_Plain(), CC).call_tool("agent_run_events", {"run_id": "r"}))
    shown = [e["payload"].get("text") for e in out["events"]]
    # "Bearer is" masks "is" as a bearer token, in context as it did per event
    assert shown[0] == "Hello, " and shown[3] == "" and shown[4] == " and that is all."


async def test_a_secret_split_over_three_events_is_masked_when_paged_one_by_one() -> None:
    """The context is the whole adjacent text run, not one neighbour (B1 probe finding)."""
    parts = ["Use Authorization: Bearer ", "q7ZpL0xV9m", "T2wR8sK4nB6cD1 to call it."]
    stored = [_text_event(n + 1, t) for n, t in enumerate(parts)]

    class _Paged(_Recorder):
        async def run_events_window(self, *_a: Any, since_seq: int = 0, **_kw: Any) -> Any:
            page = stored[since_seq : since_seq + 1]
            return stored[:since_seq], page, stored[since_seq + 1 :], False

    server = _server(_Paged(), CC)
    shown = []
    for since in range(3):
        page = _payload(
            await server.call_tool(
                "agent_run_events", {"run_id": "r", "since_seq": since, "limit": 1}
            )
        )
        shown.append(page["events"][0]["payload"]["text"])
    flat = "".join(shown)
    assert "q7ZpL0xV9m" not in flat and "T2wR8sK4nB6cD1" not in flat
    assert shown[0] == "Use Authorization: Bearer " and shown[2].endswith(" to call it.")


# -- the live edge: a token's head before the rest is stored (NB#2 on the PR 3 fix wave) --


def _edge_page(
    *texts: str, after: list[dict[str, Any]] | None = None, at_edge: bool | None = None
) -> type[_Recorder]:
    """A service whose events page is ``texts`` with ``after`` as its right context.

    ``at_edge`` defaults to True without a right context (the page ends at the
    newest stored event) and to False with one.
    """
    events = [_text_event(n + 1, text) for n, text in enumerate(texts)]
    edge = after is None if at_edge is None else at_edge

    class _Page(_Recorder):
        async def run_events_window(self, *_a: Any, **_kw: Any) -> Any:
            return [], events, after or [], edge

    return _Page


async def _shown(service: type[_Recorder]) -> list[str]:
    page = _payload(await _server(service(), CC).call_tool("agent_run_events", {"run_id": "r"}))
    return [e["payload"]["text"] for e in page["events"]]


async def test_a_token_cut_off_at_the_live_edge_is_masked_from_its_prefix() -> None:
    """The page ends after ``ghp_A1b2C3``: 20+ characters would be masked, 6 were not."""
    shown = await _shown(_edge_page("Here it is, gh", "p_A1b2C3"))
    assert "A1b2C3" not in "".join(shown) and "p_A1" not in "".join(shown)
    assert shown[0] == "Here it is, ***REDACTED***" and shown[1] == "***REDACTED***"

    # following with since_seq=last_seq, the rest arrives with the head as left context
    class _Next(_Recorder):
        async def run_events_window(self, *_a: Any, **_kw: Any) -> Any:
            before = [_text_event(1, "Here it is, gh"), _text_event(2, "p_A1b2C3")]
            return before, [_text_event(3, "D4e5F6g7H8i9J0k1L2m3 and more")], [], True

    [rest] = await _shown(_Next)
    assert rest == "***REDACTED*** and more"


@pytest.mark.parametrize(
    "head",
    [
        "hf_Ab12",
        "sk-Ab12",
        "ghp_Ab12",
        "gho_Ab12",
        "github_pat_Ab12",
        "xoxb-12",
        "hal0bk_cc.Ab12",
        "hal0bk_c",
        "AKIA12AB",
        "ghp_",
    ],
)
async def test_every_known_prefix_is_masked_at_the_live_edge(head: str) -> None:
    [shown] = await _shown(_edge_page(f"I got ({head}"))
    assert shown == "I got (***REDACTED***"


@pytest.mark.parametrize(
    "prose",
    ["We call it state-of-the-", "the snake_", "a low-risk-", "ask-", "my_task_", "sk"],
)
async def test_a_page_ending_in_ordinary_prose_is_untouched_at_the_live_edge(prose: str) -> None:
    assert await _shown(_edge_page(prose)) == [prose]


async def test_a_prefix_not_at_the_tail_is_left_to_the_existing_rule() -> None:
    whole = "ghp_" + "Q" * 24
    shown = await _shown(_edge_page(f"use {whole} then ghp_short, done"))
    assert shown == ["use ***REDACTED*** then ghp_short, done"]  # only the whole token


@pytest.mark.parametrize(
    "after",
    [[], [_text_event(3, " is all.")]],
    ids=["closed-by-another-event", "text-stored-after"],
)
async def test_away_from_the_live_edge_a_short_token_head_is_text(
    after: list[dict[str, Any]],
) -> None:
    """With events stored after the page, its last text run is closed: nothing to guess."""
    assert await _shown(_edge_page("see ghp_A1b2C3", after=after)) == ["see ghp_A1b2C3"]


async def test_a_wait_tail_is_the_live_edge() -> None:
    view = {
        "run_id": "run_1",
        "status": "running",
        "last_seq": 2,
        "events_tail": [_text_event(2, "it is hf_A1b2")],
        "timed_out": True,
    }

    class _Wait(_Recorder):
        async def events_before(self, *_a: Any, **_kw: Any) -> Any:
            return []

    out = _payload(
        await _server(_Wait(view), CC).call_tool(
            "agent_run_wait", {"run_id": "run_1", "timeout_s": 1}
        )
    )
    assert out["events_tail"][0]["payload"]["text"] == "it is ***REDACTED***"


async def test_a_right_context_reaching_the_live_edge_masks_the_head_in_the_page() -> None:
    """The page ends inside ``ghp_A1``; the rest of the text is stored and is the newest."""
    page = _edge_page("Here is ghp_A1", after=[_text_event(2, "b2C3")], at_edge=True)
    assert await _shown(page) == ["Here is ***REDACTED***"]


# -- the live edge against the real ledger (M-1 on the PR 3 tail) ------------------------


def _stored_run(
    store: BridgeStore, *events: tuple[str, str | None], status: str = "running"
) -> str:
    """A ``cc`` run in the ledger holding ``events`` as seq 1, 2, ...: (name, text)."""
    rid = "run_stored"
    store.insert_run(
        RunRow(
            run_id=rid,
            client="cc",
            agent="hermes",
            profile="default",
            session_key=None,
            status=status,
            created_at=100.0,
            updated_at=100.0,
            input_preview="ping",
            output=None,
            error=None,
            usage_json=None,
            admission_id=None,
            execution_generation=None,
            pending_request_json=None,
            last_seq=0,
        )
    )
    for seq, (name, text) in enumerate(events, start=1):
        store.append_event(rid, seq, float(seq), name, {"text": text} if text is not None else {})
    return rid


async def _first_shown(server: FastMCP, rid: str) -> str:
    page = _payload(
        await server.call_tool("agent_run_events", {"run_id": rid, "since_seq": 0, "limit": 1})
    )
    text: str = page["events"][0]["payload"]["text"]
    return text


async def test_a_token_head_whose_rest_is_the_newest_stored_text_is_masked(
    server: FastMCP, store: BridgeStore
) -> None:
    """A follower whose limit lags reads the head while the rest is already stored."""
    rid = _stored_run(store, ("message.text", "Here is ghp_A1"), ("message.text", "b2C3"))
    assert await _first_shown(server, rid) == "Here is ***REDACTED***"


async def test_a_right_context_cut_by_the_cap_is_not_the_live_edge(
    server: FastMCP, store: BridgeStore
) -> None:
    """More text is stored past the 8192-character context: the head is not at the edge."""
    events = [("message.text", "use hal0bk_aaa"), ("message.text", "a" * 9000)]
    rid = _stored_run(store, *events, ("message.text", "a" * 10))
    assert await _first_shown(server, rid) == "use hal0bk_aaa"


async def test_the_same_long_context_at_the_live_edge_is_masked(
    server: FastMCP, store: BridgeStore
) -> None:
    rid = _stored_run(store, ("message.text", "use hal0bk_aaa"), ("message.text", "a" * 9000))
    assert await _first_shown(server, rid) == "use ***REDACTED***"


async def test_a_right_context_closed_by_another_event_is_not_the_live_edge(
    server: FastMCP, store: BridgeStore
) -> None:
    events = [("message.text", "Here is ghp_A1"), ("message.text", "b2C3")]
    rid = _stored_run(store, *events, ("tool.started", None))
    assert await _first_shown(server, rid) == "Here is ghp_A1"


# -- a live run's output is the live edge too (item 5 on the PR 3 tail) -------------------


@pytest.mark.parametrize("status", ["running", "stopping", "queued", "waiting_for_approval"])
async def test_a_live_runs_output_is_masked_from_a_token_head_at_its_end(status: str) -> None:
    view = {"run_id": "run_1", "status": status, "output": "Here is ghp_A1b2C3", "last_seq": 3}
    server = _server(_Recorder(view), CC)
    shown = _payload(await server.call_tool("agent_run_status", {"run_id": "run_1"}))
    waited = _payload(await server.call_tool("agent_run_wait", {"run_id": "run_1", "timeout_s": 1}))
    (item,) = _payload(await server.call_tool("agent_runs_list", {}))["runs"]
    for output in (shown["output"], waited["output"], item["output_preview"]):
        assert "ghp_" not in output and output == "Here is ***REDACTED***"
    assert shown["output_total"] == len(b"Here is ***REDACTED***")


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled", "interrupted", "unknown"])
async def test_a_finished_runs_output_is_left_to_the_whole_token_rule(status: str) -> None:
    """A finished run's output is complete: a short ``ghp_`` word is not a token cut short."""
    view = {"run_id": "run_1", "status": status, "output": "Here is ghp_A1b2C3", "last_seq": 3}
    server = _server(_Recorder(view), CC)
    shown = _payload(await server.call_tool("agent_run_status", {"run_id": "run_1"}))
    (item,) = _payload(await server.call_tool("agent_runs_list", {}))["runs"]
    assert shown["output"] == item["output_preview"] == "Here is ghp_A1b2C3"
    whole = {**view, "output": "Here is ghp_" + "A1b2C3" * 4}
    finished = _payload(
        await _server(_Recorder(whole), CC).call_tool("agent_run_status", {"run_id": "run_1"})
    )
    assert finished["output"] == "Here is ***REDACTED***"
