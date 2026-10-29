"""HermesGateway: hal0's client for the Hermes runs API (ADR-0043 decision 9).

The bridge forwards delegated runs to Hermes with the per-profile keys hal0-api
already holds (``HAL0_HERMES_API_KEY__<PROFILE>``, mirrored by
``hermes_upstreams``). These tests run the client against ``FakeHermes`` and pin
the request shape, the SSE parsing, and the mapping of Hermes failures onto the
two bridge errors.
"""

from __future__ import annotations

import io
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import structlog

from hal0.agents.hermes_api import DEFAULT_PROFILE
from hal0.bridge import hermes_client
from hal0.bridge.errors import BridgeError
from hal0.bridge.hermes_client import HermesGateway, HermesRefused, HermesUnavailable
from tests.bridge._fake_hermes import FakeHermes, default_frames, sse_frame


@pytest.fixture
def fake() -> FakeHermes:
    return FakeHermes()


@pytest.fixture
def gw(fake: FakeHermes, tmp_path: Path) -> HermesGateway:
    return HermesGateway(home=tmp_path, environ=fake.environ(), transport=fake.transport())


def _down(exc: Exception) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


def _gateway_with(
    handler: Callable[[httpx.Request], httpx.Response], fake: FakeHermes, tmp_path: Path
) -> HermesGateway:
    return HermesGateway(
        home=tmp_path, environ=fake.environ(), transport=httpx.MockTransport(handler)
    )


@pytest.fixture
def gw_down(fake: FakeHermes, tmp_path: Path) -> HermesGateway:
    return _gateway_with(_down(httpx.ConnectError("connection refused")), fake, tmp_path)


# -- agent names, base URLs and keys -----------------------------------------------------


def test_profile_of_maps_agent_names(gw: HermesGateway) -> None:
    assert gw.profile_of("hermes") == DEFAULT_PROFILE
    assert gw.profile_of("hermes/coder") == "coder"
    assert gw.profile_of("hermes/hal0-brain") == "hal0-brain"


@pytest.mark.parametrize("agent", ["pi", "", "hermes/", "hermes/..", "hermes/a/b", "hermes/a b"])
def test_profile_of_refuses_anything_else(gw: HermesGateway, agent: str) -> None:
    with pytest.raises(BridgeError) as e:
        gw.profile_of(agent)
    assert e.value.code == "bridge.unknown_agent"
    assert e.value.status == 404


def test_base_for_default_and_named(gw: HermesGateway) -> None:
    assert gw.base_for(DEFAULT_PROFILE) == "http://127.0.0.1:8642"
    assert gw.base_for("coder") == "http://127.0.0.1:8642/p/coder"


def test_key_for_reads_the_live_environment(tmp_path: Path) -> None:
    environ = {"HAL0_HERMES_API_KEY__CODER": "k-coder-0123456789abcdef"}
    gw = HermesGateway(home=tmp_path, environ=environ)
    assert gw.key_for("coder") == "k-coder-0123456789abcdef"
    environ["HAL0_HERMES_API_KEY__CODER"] = "rotated-0123456789abcdef"
    assert gw.key_for("coder") == "rotated-0123456789abcdef"  # reconcile refreshes it in place


@pytest.mark.parametrize("value", [None, "", "   "])
def test_unkeyed_profile_is_503(tmp_path: Path, value: str | None) -> None:
    environ = {} if value is None else {"HAL0_HERMES_API_KEY__RESEARCH_MISSING": value}
    gw = HermesGateway(home=tmp_path, environ=environ)
    with pytest.raises(BridgeError) as e:
        gw.key_for("research-missing")
    assert e.value.code == "bridge.agent_unkeyed"
    assert e.value.status == 503


async def test_unkeyed_profile_makes_no_request(fake: FakeHermes, tmp_path: Path) -> None:
    gw = HermesGateway(home=tmp_path, environ={}, transport=fake.transport())
    with pytest.raises(BridgeError) as e:
        await gw.get_run("coder", "run_x")
    assert e.value.code == "bridge.agent_unkeyed"
    assert fake.calls == []


# -- start_run ---------------------------------------------------------------------------


async def test_start_run_uses_profile_key_and_namespaced_headers(
    gw: HermesGateway, fake: FakeHermes
) -> None:
    out = await gw.start_run(
        "coder",
        input="hi",
        instructions=None,
        reasoning_effort=None,
        session_key="hal0-bridge:cc:s1",
        idempotency_key="hal0-bridge:cc:k1",
    )
    assert out["run_id"].startswith("run_")
    assert out["status"] == "started" and out["replayed"] is False
    req = fake.last_request
    assert req.method == "POST"
    assert req.url.path == "/p/coder/v1/runs"
    assert req.headers["authorization"] == f"Bearer {fake.keys['coder']}"
    assert req.headers["x-hermes-session-key"] == "hal0-bridge:cc:s1"
    assert req.headers["idempotency-key"] == "hal0-bridge:cc:k1"
    assert json.loads(req.content) == {"input": "hi"}


async def test_start_run_on_the_default_profile_omits_unset_headers(
    gw: HermesGateway, fake: FakeHermes
) -> None:
    await gw.start_run(
        DEFAULT_PROFILE,
        input="hi",
        instructions="be brief",
        reasoning_effort="high",
        session_key=None,
        idempotency_key=None,
    )
    req = fake.last_request
    assert req.url.path == "/v1/runs"
    assert req.headers["authorization"] == f"Bearer {fake.keys[DEFAULT_PROFILE]}"
    assert "x-hermes-session-key" not in req.headers
    assert "idempotency-key" not in req.headers
    assert json.loads(req.content) == {
        "input": "hi",
        "instructions": "be brief",
        "model_options": {"reasoning": {"effort": "high"}},
    }


async def test_idempotent_replay_and_conflict(gw: HermesGateway) -> None:
    kw: dict[str, Any] = {
        "instructions": None,
        "reasoning_effort": None,
        "session_key": None,
        "idempotency_key": "hal0-bridge:cc:k1",
    }
    first = await gw.start_run(DEFAULT_PROFILE, input="hi", **kw)
    again = await gw.start_run(DEFAULT_PROFILE, input="hi", **kw)
    assert again["run_id"] == first["run_id"] and again["replayed"] is True
    with pytest.raises(HermesRefused) as e:
        await gw.start_run(DEFAULT_PROFILE, input="another body", **kw)
    assert e.value.hermes_status == 409
    assert e.value.hermes_code == "idempotency_key_conflict"
    assert e.value.code == "bridge.agent_refused"
    assert e.value.status == 409


async def test_start_run_never_retries(fake: FakeHermes, tmp_path: Path) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("connection refused")

    gw = _gateway_with(handler, fake, tmp_path)
    with pytest.raises(HermesUnavailable):
        await gw.start_run(
            DEFAULT_PROFILE,
            input="hi",
            instructions=None,
            reasoning_effort=None,
            session_key=None,
            idempotency_key=None,
        )
    assert attempts == 1


# -- failures ----------------------------------------------------------------------------


async def test_connect_error_is_unavailable(gw_down: HermesGateway) -> None:
    with pytest.raises(HermesUnavailable) as e:
        await gw_down.get_run(DEFAULT_PROFILE, "run_x")
    assert e.value.code == "bridge.agent_unavailable"
    assert e.value.status == 502


async def test_timeout_is_unavailable(fake: FakeHermes, tmp_path: Path) -> None:
    gw = _gateway_with(_down(httpx.ReadTimeout("slow")), fake, tmp_path)
    with pytest.raises(HermesUnavailable):
        await gw.get_run(DEFAULT_PROFILE, "run_x")


async def test_a_non_json_success_body_is_unavailable(fake: FakeHermes, tmp_path: Path) -> None:
    gw = _gateway_with(lambda request: httpx.Response(200, text="<html>"), fake, tmp_path)
    with pytest.raises(HermesUnavailable):
        await gw.get_run(DEFAULT_PROFILE, "run_x")


async def test_a_wrong_key_is_refused_and_says_the_key_is_stale(
    fake: FakeHermes, tmp_path: Path
) -> None:
    environ = fake.environ()
    environ["HAL0_HERMES_API_KEY__CODER"] = "stale-key-0123456789abcdef"
    gw = HermesGateway(home=tmp_path, environ=environ, transport=fake.transport())
    run_id = fake.seed_run(profile="coder")
    with pytest.raises(HermesRefused) as e:
        await gw.get_run("coder", run_id)
    assert e.value.hermes_status == 401
    assert e.value.code == "bridge.agent_refused"
    assert e.value.status == 502  # the caller's own key is fine; hal0's copy of Hermes's is not
    assert "stale" in e.value.message
    assert "coder" in e.value.message
    assert "stale-key-0123456789abcdef" not in str(e.value)
    assert fake.keys["coder"] not in str(e.value)


async def test_hermes_4xx_passes_its_status_and_code_through(
    gw: HermesGateway, fake: FakeHermes
) -> None:
    fake.fail_next(429, "too_many_runs", "concurrency limit")
    with pytest.raises(HermesRefused) as e:
        await gw.start_run(
            DEFAULT_PROFILE,
            input="hi",
            instructions=None,
            reasoning_effort=None,
            session_key=None,
            idempotency_key=None,
        )
    assert (e.value.hermes_status, e.value.hermes_code) == (429, "too_many_runs")
    assert e.value.status == 429
    assert "concurrency limit" in e.value.message


async def test_hermes_5xx_is_a_502(gw: HermesGateway, fake: FakeHermes) -> None:
    fake.fail_next(500, "internal_error", "boom")
    with pytest.raises(HermesRefused) as e:
        await gw.get_run(DEFAULT_PROFILE, "run_x")
    assert e.value.hermes_status == 500
    assert e.value.status == 502


async def test_an_error_body_without_a_code_still_refuses(fake: FakeHermes, tmp_path: Path) -> None:
    gw = _gateway_with(lambda request: httpx.Response(400, text="bad request"), fake, tmp_path)
    with pytest.raises(HermesRefused) as e:
        await gw.get_run(DEFAULT_PROFILE, "run_x")
    assert e.value.hermes_status == 400 and e.value.hermes_code is None
    assert "bad request" in e.value.message


async def test_unknown_run_is_refused_404(gw: HermesGateway) -> None:
    with pytest.raises(HermesRefused) as e:
        await gw.get_run(DEFAULT_PROFILE, "run_missing")
    assert e.value.hermes_status == 404 and e.value.status == 404


# -- the client's own settings -----------------------------------------------------------


async def test_client_ignores_the_proxy_environment_and_sets_the_timeouts(
    fake: FakeHermes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []
    real = httpx.AsyncClient

    def spy(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        seen.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", spy)
    gw = HermesGateway(home=tmp_path, environ=fake.environ(), transport=fake.transport())
    run_id = fake.seed_run()
    await gw.get_run(DEFAULT_PROFILE, run_id)
    assert seen[0]["trust_env"] is False
    assert seen[0]["timeout"] == httpx.Timeout(connect=10, read=600, write=30, pool=10)


# -- get_run and the controls ------------------------------------------------------------


async def test_get_run_walks_the_status_script(gw: HermesGateway, fake: FakeHermes) -> None:
    run_id = fake.seed_run(statuses=["queued", "running", "completed"], output="pong")
    seen = [(await gw.get_run(DEFAULT_PROFILE, run_id))["status"] for _ in range(4)]
    assert seen == ["queued", "running", "completed", "completed"]
    last = await gw.get_run(DEFAULT_PROFILE, run_id)
    assert last["output"] == "pong" and last["run_id"] == run_id
    assert fake.last_request.url.path == f"/v1/runs/{run_id}"
    assert fake.last_request.method == "GET"


async def test_approval_and_clarify_send_generation_and_choice(
    gw: HermesGateway, fake: FakeHermes
) -> None:
    run_id = fake.seed_run(
        profile="coder", pending_approval="req_1", pending_clarify="q_1", generation=4
    )
    await gw.approval("coder", run_id, request_id="req_1", execution_generation=4, choice="once")
    req = fake.last_request
    assert req.url.path == f"/p/coder/v1/runs/{run_id}/approval"
    assert json.loads(req.content) == {
        "request_id": "req_1",
        "execution_generation": 4,
        "choice": "once",
    }
    await gw.clarify(
        "coder", run_id, request_id="q_1", execution_generation=4, answer="the blue one"
    )
    req = fake.last_request
    assert req.url.path == f"/p/coder/v1/runs/{run_id}/clarify"
    assert json.loads(req.content) == {
        "request_id": "q_1",
        "execution_generation": 4,
        "answer": "the blue one",
    }


async def test_stale_generation_and_settled_request_are_409(
    gw: HermesGateway, fake: FakeHermes
) -> None:
    run_id = fake.seed_run(pending_approval="req_1", generation=2)
    with pytest.raises(HermesRefused) as stale:
        await gw.approval(
            DEFAULT_PROFILE, run_id, request_id="req_1", execution_generation=1, choice="once"
        )
    assert (stale.value.hermes_status, stale.value.hermes_code) == (409, "stale_generation")
    with pytest.raises(HermesRefused) as gone:
        await gw.approval(
            DEFAULT_PROFILE, run_id, request_id="req_9", execution_generation=2, choice="once"
        )
    assert gone.value.hermes_code == "approval_not_pending"


async def test_steer_and_stop(gw: HermesGateway, fake: FakeHermes) -> None:
    run_id = fake.seed_run(statuses=["running"])
    await gw.steer(DEFAULT_PROFILE, run_id, "use the other repo")
    req = fake.last_request
    assert req.url.path == f"/v1/runs/{run_id}/steer"
    assert json.loads(req.content) == {"input": "use the other repo"}
    out = await gw.stop(DEFAULT_PROFILE, run_id)
    assert out["status"] == "stopping" and out["run_id"] == run_id
    assert fake.last_request.url.path == f"/v1/runs/{run_id}/stop"
    assert fake.last_request.content == b""
    with pytest.raises(HermesRefused) as e:  # the run is stopping now: steering is refused
        await gw.steer(DEFAULT_PROFILE, run_id, "too late")
    assert e.value.hermes_code == "run_not_running"


# -- health ------------------------------------------------------------------------------


async def test_health_returns_the_status_code(
    gw: HermesGateway, fake: FakeHermes, tmp_path: Path
) -> None:
    assert await gw.health(DEFAULT_PROFILE) == 200
    assert fake.last_request.url.path == "/health"
    assert await gw.health("coder") == 200
    assert fake.last_request.url.path == "/p/coder/health"
    assert await gw.health("nobody") == 404  # a profile the gateway does not serve
    wrong = HermesGateway(
        home=tmp_path,
        environ={"HAL0_HERMES_API_KEY__CODER": "wrong-key-0123456789abcdef"},
        transport=fake.transport(),
    )
    assert await wrong.health("coder") == 401


async def test_health_is_zero_when_the_gateway_is_down(gw_down: HermesGateway) -> None:
    assert await gw_down.health(DEFAULT_PROFILE) == 0


# -- events ------------------------------------------------------------------------------


async def test_events_parses_frames_and_resumes(gw: HermesGateway, fake: FakeHermes) -> None:
    fake.seed_run("run_a")
    seen = [e async for e in gw.events(DEFAULT_PROFILE, "run_a", last_seq=0)]
    assert [e["event"] for e in seen][:2] == ["tool.started", "message.delta"]
    assert all("seq" in e for e in seen)
    assert [e["seq"] for e in seen] == [1, 2, 3, 4, 5]
    assert seen[-1]["event"] == "run.completed" and seen[-1]["output"] == "pong"
    assert fake.last_request.headers["last-event-id"] == "0"
    assert fake.last_request.url.path == "/v1/runs/run_a/events"
    assert fake.last_request.headers["accept"] == "text/event-stream"


async def test_events_resume_sends_only_later_frames(gw: HermesGateway, fake: FakeHermes) -> None:
    fake.seed_run("run_a", profile="coder")
    seen = [e async for e in gw.events("coder", "run_a", last_seq=3)]
    assert [e["seq"] for e in seen] == [4, 5]
    assert fake.last_request.headers["last-event-id"] == "3"
    assert fake.last_request.url.path == "/p/coder/v1/runs/run_a/events"
    assert fake.resume_headers == ["3"]


async def test_events_carry_the_approval_request_payload(
    gw: HermesGateway, fake: FakeHermes
) -> None:
    fake.seed_run("run_a")
    ask = await anext(gw.events(DEFAULT_PROFILE, "run_a", last_seq=2))
    assert ask["event"] == "approval.request"
    assert ask["request_id"] == "req_1" and ask["execution_generation"] == 1
    assert ask["choices"] == ["once", "session", "deny"]


async def test_events_skips_comments_and_takes_seq_from_the_id_line(
    fake: FakeHermes, tmp_path: Path
) -> None:
    # The id line wins over any seq in the payload, and ": ..." lines are ignored
    # wherever they fall, including between the fields of one frame.
    wire = (
        ": open\n\n"
        "id: 7\nevent: message.delta\n: keepalive\ndata: "
        '{"event": "message.delta", "run_id": "run_a", "seq": 99, "delta": "x"}\n\n'
        ": keepalive\n\n"
        "id: 8\nevent: run.completed\ndata: "
        '{"event": "run.completed", "run_id": "run_a", "completed": true}\n\n'
        ": stream closed\n\n"
    )
    gw = _gateway_with(lambda request: httpx.Response(200, text=wire), fake, tmp_path)
    seen = [e async for e in gw.events(DEFAULT_PROFILE, "run_a", last_seq=0)]
    assert [(e["seq"], e["event"]) for e in seen] == [(7, "message.delta"), (8, "run.completed")]
    assert seen[0]["delta"] == "x"


async def test_events_handles_frames_split_across_chunks(fake: FakeHermes, tmp_path: Path) -> None:
    frame = sse_frame(1, "message.delta", "run_a", delta="héllo")
    pieces = [frame[:17], frame[17:40], frame[40:]]

    async def body() -> Any:
        for piece in pieces:
            yield piece.encode()

    gw = _gateway_with(
        lambda request: httpx.Response(200, content=body()),
        fake,
        tmp_path,
    )
    seen = [e async for e in gw.events(DEFAULT_PROFILE, "run_a", last_seq=0)]
    assert [(e["seq"], e["delta"]) for e in seen] == [(1, "héllo")]


async def test_events_flushes_a_final_frame_with_no_closing_blank_line(
    fake: FakeHermes, tmp_path: Path
) -> None:
    wire = 'id: 1\nevent: message.delta\ndata: {"event": "message.delta", "delta": "x"}\n'
    gw = _gateway_with(lambda request: httpx.Response(200, text=wire), fake, tmp_path)
    seen = [e async for e in gw.events(DEFAULT_PROFILE, "run_a", last_seq=0)]
    assert [e["seq"] for e in seen] == [1]


async def test_events_drops_a_frame_with_unparseable_data(fake: FakeHermes, tmp_path: Path) -> None:
    wire = (
        "id: 1\nevent: x\ndata: {not json\n\n"
        'id: 2\nevent: message.delta\ndata: {"event": "message.delta"}\n\n'
    )
    gw = _gateway_with(lambda request: httpx.Response(200, text=wire), fake, tmp_path)
    seen = [e async for e in gw.events(DEFAULT_PROFILE, "run_a", last_seq=0)]
    assert [e["seq"] for e in seen] == [2]


async def test_events_stream_cut_is_unavailable(gw: HermesGateway, fake: FakeHermes) -> None:
    run_id = fake.seed_run()
    fake.script_events(run_id, default_frames(run_id)[:2], then_error=True)
    got: list[int] = []
    with pytest.raises(HermesUnavailable):
        async for e in gw.events(DEFAULT_PROFILE, run_id, last_seq=0):
            got.append(e["seq"])
    assert got == [1, 2]  # what arrived before the cut was delivered; the caller reconnects


async def test_events_for_an_unknown_run_is_refused(gw: HermesGateway) -> None:
    with pytest.raises(HermesRefused) as e:
        async for _ in gw.events(DEFAULT_PROFILE, "run_missing", last_seq=0):
            pass
    assert e.value.hermes_status == 404


async def test_events_connect_error_is_unavailable(gw_down: HermesGateway) -> None:
    with pytest.raises(HermesUnavailable):
        async for _ in gw_down.events(DEFAULT_PROFILE, "run_a", last_seq=0):
            pass


# -- a failure never carries the key into a log (#3137) -----------------------------------

#: ``API_SERVER_HOST=[::1`` written into the Hermes home: an address httpx cannot parse.
_BAD_BASE = "http://[::1:8642"


def _journal_render(exc: BaseException) -> str:
    """``exc`` as hal0-api's journal prints it: structlog's rich traceback, frame locals on.

    ``hal0 serve`` keeps structlog's default renderer, which shows every frame's
    locals (#3137); a wide console keeps a value on one line.
    """
    sio = io.StringIO()
    formatter = structlog.dev.RichTracebackFormatter(show_locals=True, width=400)
    formatter(sio, (type(exc), exc, exc.__traceback__))
    return re.sub(r"\x1b\[[0-9;]*m", "", sio.getvalue())


async def _raised(call: Awaitable[Any]) -> BaseException:
    """The exception ``call`` raises, caught here so the traceback holds no test frame."""
    try:
        await call
    except BaseException as exc:
        return exc
    raise AssertionError("the call did not raise")


async def _drain(stream: AsyncIterator[Any]) -> None:
    async for _ in stream:
        pass


def test_the_journal_renderer_prints_frame_locals() -> None:
    """Control for the tests below: a key held in a local does show up in the render."""

    def holds_a_header(key: str) -> None:
        headers = {"Authorization": f"Bearer {key}"}
        raise RuntimeError(len(headers))

    with pytest.raises(RuntimeError) as e:
        holds_a_header("control-key-0123456789abcdef")
    assert "control-key-0123456789abcdef" in _journal_render(e.value)


@pytest.mark.parametrize("call", ["request", "events"])
async def test_an_address_httpx_cannot_parse_is_unavailable_and_hides_the_key(
    fake: FakeHermes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, call: str
) -> None:
    """``httpx.InvalidURL`` is no ``httpx.HTTPError``: it must still become a bridge error.

    Escaping as itself, it reached ``log.error(exc_info=True)`` in the MCP tool
    guard (and ``log.exception`` in the run watcher), and the rendered frame
    locals printed the profile's key.
    """
    monkeypatch.setattr(hermes_client, "api_server_base", lambda home: _BAD_BASE)
    gw = HermesGateway(home=tmp_path, environ=fake.environ(), transport=fake.transport())
    if call == "request":
        exc = await _raised(gw.get_run(DEFAULT_PROFILE, "run_x"))
    else:
        exc = await _raised(_drain(gw.events(DEFAULT_PROFILE, "run_x", last_seq=0)))
    assert isinstance(exc, HermesUnavailable), repr(exc)
    assert exc.status == 502
    assert "InvalidURL" in exc.message
    key = fake.keys[DEFAULT_PROFILE]
    assert key not in exc.message
    assert key not in _journal_render(exc)


@pytest.mark.parametrize("call", ["request", "events"])
async def test_any_transport_failure_is_unavailable_and_hides_the_key(
    fake: FakeHermes, tmp_path: Path, call: str
) -> None:
    """Not only httpx's own errors: whatever the HTTP call raises maps to ``HermesUnavailable``."""
    gw = _gateway_with(_down(RuntimeError("transport broke")), fake, tmp_path)
    if call == "request":
        exc = await _raised(gw.stop(DEFAULT_PROFILE, "run_x"))
    else:
        exc = await _raised(_drain(gw.events(DEFAULT_PROFILE, "run_x", last_seq=0)))
    assert isinstance(exc, HermesUnavailable), repr(exc)
    assert "transport broke" in exc.message
    assert fake.keys[DEFAULT_PROFILE] not in _journal_render(exc)


async def test_an_error_text_never_carries_the_authorization_header(
    fake: FakeHermes, tmp_path: Path
) -> None:
    """An httpx error whose text quotes the header (h11 does, for a bad value) is masked."""
    key = fake.keys[DEFAULT_PROFILE]
    leaky = httpx.LocalProtocolError(f"Illegal header value b'Bearer {key}'")
    gw = _gateway_with(_down(leaky), fake, tmp_path)
    exc = await _raised(gw.get_run(DEFAULT_PROFILE, "run_x"))
    assert isinstance(exc, HermesUnavailable)
    assert key not in exc.message
    assert key not in _journal_render(exc)


async def test_an_error_text_drops_url_userinfo(fake: FakeHermes, tmp_path: Path) -> None:
    gw = _gateway_with(
        _down(httpx.ConnectError("cannot reach http://hal0:s3cret-pass@127.0.0.1:8642/v1/runs")),
        fake,
        tmp_path,
    )
    exc = await _raised(gw.get_run(DEFAULT_PROFILE, "run_x"))
    assert isinstance(exc, HermesUnavailable)
    assert "s3cret-pass" not in exc.message
    assert "http://127.0.0.1:8642/v1/runs" in exc.message


async def test_the_unkeyed_error_still_passes_through_the_mapping(tmp_path: Path) -> None:
    """``bridge.agent_unkeyed`` is a bridge error of its own, not an unavailable Hermes."""
    gw = HermesGateway(home=tmp_path, environ={})
    for call in (
        gw.get_run(DEFAULT_PROFILE, "r"),
        _drain(gw.events(DEFAULT_PROFILE, "r", last_seq=0)),
    ):
        exc = await _raised(call)
        assert isinstance(exc, BridgeError) and exc.code == "bridge.agent_unkeyed"


async def test_events_ends_at_the_total_deadline(
    gw: HermesGateway, fake: FakeHermes, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake.seed_run("run_a")
    assert hermes_client.EVENTS_DEADLINE_S == 1800
    monkeypatch.setattr(hermes_client, "EVENTS_DEADLINE_S", 0.0)
    seen = [e async for e in gw.events(DEFAULT_PROFILE, "run_a", last_seq=0)]
    assert seen == []  # the caller sees the stream end and reconnects from its last seq
