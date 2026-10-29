"""A scripted Hermes API server for the bridge tests (ADR-0043).

``FakeHermes`` is an :class:`httpx.MockTransport` handler that speaks the
slice of the hermes-agent 0.22.4 runs API the bridge uses. The gateway client
tests (``test_hermes_client``) and the later watcher, run-tool and MCP tests
share it, so it stays small and does only what the real server does:

* ``POST /v1/runs`` and ``POST /p/<profile>/v1/runs`` answer 202
  ``{"run_id", "status": "started", "replayed": false}``. The same
  ``Idempotency-Key`` with an identical body replays (``replayed: true`` plus
  ``Idempotency-Replayed: true``); with a different body it is a 409
  ``idempotency_key_conflict``.
* ``GET .../runs/<id>`` walks a scripted status sequence, one entry per poll,
  the last one sticking.
* ``GET .../runs/<id>/events`` serves the scripted SSE frames (``: open``,
  ``id:`` / ``event:`` / ``data:``, ``: keepalive``, ``: stream closed``) and
  honours ``Last-Event-ID`` by sending only frames with a higher ``seq``.
* ``POST .../approval|clarify|steer|stop`` are recorded; a wrong
  ``execution_generation`` is a 409 ``stale_generation``, a wrong request id a
  409 ``approval_not_pending``, and steering a run that is not ``running`` a
  409 ``run_not_running``.
* ``GET /health`` and ``/p/<profile>/health`` answer 200, or 404 for a profile
  with no key.
* Every request needs ``Authorization: Bearer <that profile's key>`` (401
  otherwise), and every request is recorded in :attr:`FakeHermes.calls`.

Failure injection: :meth:`fail_next` makes the next non-health request answer a
chosen error; :meth:`replay_next` makes the next ``POST /runs`` replay a run;
:meth:`script_events` can end a stream with a transport error.

Typical use::

    fake = FakeHermes()
    run_id = fake.seed_run(statuses=["running", "completed"], frames=[...])
    gateway = HermesGateway(home=tmp, environ=fake.environ(), transport=fake.transport())
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

DEFAULT_PROFILE = "default"

#: Hermes run statuses that end a run.
TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})


def sse_frame(seq: int, event: str, run_id: str, **fields: Any) -> str:
    """One SSE frame as Hermes sends it: ``id`` / ``event`` / ``data`` and a blank line."""
    payload = {"event": event, "run_id": run_id, "timestamp": 1_700_000_000.0 + seq, "seq": seq}
    payload.update(fields)
    return f"id: {seq}\nevent: {event}\ndata: {json.dumps(payload)}\n\n"


def default_frames(run_id: str) -> list[tuple[int, str]]:
    """A short run: a tool call, some text, a gated command, then completion."""
    return [
        (1, sse_frame(1, "tool.started", run_id, tool="terminal")),
        (2, sse_frame(2, "message.delta", run_id, delta="po")),
        (
            3,
            sse_frame(
                3,
                "approval.request",
                run_id,
                request_id="req_1",
                command="rm -rf build",
                description="delete build output",
                pattern_key="rm",
                pattern_keys=["rm"],
                allow_session=True,
                allow_permanent=False,
                choices=["once", "session", "deny"],
                execution_generation=1,
            ),
        ),
        (4, sse_frame(4, "message.delta", run_id, delta="ng")),
        (
            5,
            sse_frame(
                5,
                "run.completed",
                run_id,
                completed=True,
                partial=False,
                interrupted=False,
                output="pong",
                usage={"total_tokens": 9},
            ),
        ),
    ]


@dataclass
class _Run:
    run_id: str
    profile: str
    statuses: list[str]
    frames: list[tuple[int, str]]
    generation: int = 1
    pending_approval: str | None = None
    pending_clarify: str | None = None
    output: str | None = None
    usage: dict[str, Any] | None = None
    stream_error: bool = False
    polls: int = 0

    def status(self) -> str:
        return self.statuses[min(self.polls, len(self.statuses) - 1)]

    def poll(self) -> str:
        current = self.status()
        self.polls += 1
        return current


@dataclass
class _Forced:
    status: int
    code: str
    message: str


@dataclass
class FakeHermes:
    """Scripted Hermes API server; see the module docstring."""

    keys: dict[str, str] = field(
        default_factory=lambda: {
            DEFAULT_PROFILE: "default-key-0123456789abcdef",
            "coder": "coder-key-0123456789abcdef",
        }
    )
    calls: list[httpx.Request] = field(default_factory=list)
    runs: dict[str, _Run] = field(default_factory=dict)
    #: Last-Event-ID header of each events request, in order (``None`` when absent).
    resume_headers: list[str | None] = field(default_factory=list)
    _idempotency: dict[tuple[str, str], tuple[bytes, str]] = field(default_factory=dict)
    _forced: list[_Forced] = field(default_factory=list)
    _replay_next: str | None = None

    # -- wiring -----------------------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        """The transport to inject into ``HermesGateway(transport=...)``."""
        return httpx.MockTransport(self)

    def environ(self) -> dict[str, str]:
        """The environment hal0-api would hold: one ``HAL0_HERMES_API_KEY__*`` per profile."""
        from hal0.agents.hermes_api import env_key_name

        return {env_key_name(profile): key for profile, key in self.keys.items()}

    @property
    def last_request(self) -> httpx.Request:
        return self.calls[-1]

    def requests_to(self, suffix: str) -> list[httpx.Request]:
        """Recorded requests whose path ends with ``suffix`` (``"/approval"``, ``"/stop"``...)."""
        return [r for r in self.calls if r.url.path.endswith(suffix)]

    # -- scripting --------------------------------------------------------------

    def seed_run(
        self,
        run_id: str | None = None,
        *,
        profile: str = DEFAULT_PROFILE,
        statuses: Sequence[str] = ("running", "completed"),
        frames: Sequence[tuple[int, str]] | None = None,
        generation: int = 1,
        pending_approval: str | None = None,
        pending_clarify: str | None = None,
        output: str | None = None,
        usage: dict[str, Any] | None = None,
    ) -> str:
        """Register a run the fake already knows about; returns its id."""
        rid = run_id or _new_run_id()
        self.runs[rid] = _Run(
            run_id=rid,
            profile=profile,
            statuses=list(statuses),
            frames=list(frames) if frames is not None else default_frames(rid),
            generation=generation,
            pending_approval=pending_approval,
            pending_clarify=pending_clarify,
            output=output,
            usage=usage,
        )
        return rid

    def script_events(
        self,
        run_id: str,
        frames: Sequence[tuple[int, str]],
        *,
        then_error: bool = False,
    ) -> None:
        """Replace a run's SSE frames; ``then_error`` ends the stream with a read error."""
        run = self.runs[run_id]
        run.frames = list(frames)
        run.stream_error = then_error

    def fail_next(self, status: int, code: str, message: str = "scripted failure") -> None:
        """Make the next non-health request answer ``status`` with ``{"error": {"code": ...}}``."""
        self._forced.append(_Forced(status, code, message))

    def replay_next(self, run_id: str) -> None:
        """Make the next ``POST /runs`` answer as an idempotent replay of ``run_id``."""
        self._replay_next = run_id

    # -- the handler ------------------------------------------------------------

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        profile, rest = _split_path(request.url.path)
        if profile not in self.keys:
            return _error(404, "profile_not_found", f"no profile {profile!r}")
        if request.headers.get("authorization") != f"Bearer {self.keys[profile]}":
            return _error(401, "invalid_api_key", "invalid API key")
        if rest == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if self._forced:
            forced = self._forced.pop(0)
            return _error(forced.status, forced.code, forced.message)
        if request.method == "POST" and rest == "/v1/runs":
            return self._start(request, profile)
        parts = rest.split("/")  # ["", "v1", "runs", "<id>", ...]
        if len(parts) >= 4 and parts[1:3] == ["v1", "runs"]:
            run = self.runs.get(parts[3])
            if run is None or run.profile != profile:
                return _error(404, "run_not_found", f"no run {parts[3]!r}")
            tail = "/".join(parts[4:])
            if request.method == "GET" and tail == "":
                return self._get(run)
            if request.method == "GET" and tail == "events":
                return self._events(request, run)
            if request.method == "POST":
                return self._control(request, run, tail)
        return _error(404, "not_found", f"{request.method} {rest}")

    def _start(self, request: httpx.Request, profile: str) -> httpx.Response:
        body = request.content
        key = request.headers.get("idempotency-key")
        if self._replay_next is not None:
            rid, self._replay_next = self._replay_next, None
            return _started(rid, replayed=True)
        if key is not None:
            seen = self._idempotency.get((profile, key))
            if seen is not None:
                if seen[0] != body:
                    return _error(409, "idempotency_key_conflict", "key reused with another body")
                return _started(seen[1], replayed=True)
        rid = self.seed_run(profile=profile, statuses=("running", "completed"))
        if key is not None:
            self._idempotency[(profile, key)] = (body, rid)
        return _started(rid, replayed=False)

    def _get(self, run: _Run) -> httpx.Response:
        status = run.poll()
        done = status in TERMINAL
        return httpx.Response(
            200,
            json={
                "object": "hermes.run",
                "run_id": run.run_id,
                "status": status,
                "session_id": "sess_1",
                "admission_id": "adm_1",
                "execution_generation": run.generation,
                "output": run.output if done else None,
                "usage": run.usage if done else None,
                "pending_controls": {
                    "approval": run.pending_approval,
                    "clarify": run.pending_clarify,
                },
            },
        )

    def _events(self, request: httpx.Request, run: _Run) -> httpx.Response:
        resume = request.headers.get("last-event-id")
        self.resume_headers.append(resume)
        after = int(resume) if resume and resume.isdigit() else 0
        chunks = [": open\n\n"]
        for seq, frame in run.frames:
            if seq > after:
                chunks.append(frame)
                chunks.append(": keepalive\n\n")
        fail = run.stream_error

        async def body() -> AsyncIterator[bytes]:
            for chunk in chunks:
                yield chunk.encode()
            if fail:
                raise httpx.ReadError("stream cut")
            yield b": stream closed\n\n"

        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=_AsyncBody(body())
        )

    def _control(self, request: httpx.Request, run: _Run, action: str) -> httpx.Response:
        raw = request.content
        data: dict[str, Any] = json.loads(raw) if raw else {}
        if action in {"approval", "clarify"}:
            if data.get("execution_generation") != run.generation:
                return _error(409, "stale_generation", "the run moved on")
            pending = run.pending_approval if action == "approval" else run.pending_clarify
            if pending is None or data.get("request_id") != pending:
                return _error(409, f"{action}_not_pending", f"no pending {action}")
            if action == "approval":
                run.pending_approval = None
            else:
                run.pending_clarify = None
            return httpx.Response(200, json={"run_id": run.run_id, "status": "resolved"})
        if action == "steer":
            if run.status() != "running":
                return _error(409, "run_not_running", "steer needs a running run")
            return httpx.Response(200, json={"run_id": run.run_id, "status": "steered"})
        if action == "stop":
            run.statuses = ["stopping"]
            run.polls = 0
            return httpx.Response(
                202, json={"run_id": run.run_id, "status": "stopping", "admission_id": "adm_1"}
            )
        return _error(404, "not_found", f"POST {action}")


class _AsyncBody(httpx.AsyncByteStream):
    """Adapts an async generator of bytes to an httpx response stream."""

    def __init__(self, inner: AsyncIterator[bytes]) -> None:
        self._inner = inner

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._inner:
            yield chunk

    async def aclose(self) -> None:
        return None


def _new_run_id() -> str:
    return "run_" + uuid.uuid4().hex


def _split_path(path: str) -> tuple[str, str]:
    """``/p/coder/v1/runs`` -> ``("coder", "/v1/runs")``; no prefix means the default profile."""
    if path.startswith("/p/"):
        _, _, tail = path[3:].partition("/")
        name = path[3:].split("/", 1)[0]
        return name, "/" + tail
    return DEFAULT_PROFILE, path


def _error(status: int, code: str, message: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": message}})


def _started(run_id: str, *, replayed: bool) -> httpx.Response:
    headers = {"Idempotency-Replayed": "true"} if replayed else {}
    return httpx.Response(
        202,
        headers=headers,
        json={"run_id": run_id, "status": "started", "replayed": replayed},
    )
