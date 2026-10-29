"""hal0's client for the Hermes runs API (ADR-0043 decision 9).

The agent bridge delegates a run to Hermes with the per-profile key hal0-api
already holds: ``hermes_upstreams.reconcile_hermes_upstreams`` mirrors each
profile's ``API_SERVER_KEY`` into the process environment as
``HAL0_HERMES_API_KEY__<PROFILE>`` (ADR-0035), and :meth:`HermesGateway.key_for`
reads it from there on every call. A key is never logged, never put in an error
message and never handed to a bridge client.

Hermes serves the default profile at ``/v1/runs`` and a named profile under
``/p/<profile>/v1/runs``. The client is deliberately thin:

* it makes one attempt per call and never retries a ``POST`` (a retried
  ``POST /runs`` could start a second run; the idempotency key is the caller's
  tool for safe repeats);
* it never reconnects an event stream: :meth:`HermesGateway.events` ends when
  the stream ends, and the run watcher reconnects with ``Last-Event-ID``;
* it reports exactly two failures, :class:`HermesUnavailable` (nothing usable
  came back) and :class:`HermesRefused` (Hermes answered with an error), both
  :class:`~hal0.bridge.errors.BridgeError`.

Timeouts follow ADR-0035 decision 5 (the Hermes upstreams use 600 s idle and
1800 s total for streams): 600 s read, and :data:`EVENTS_DEADLINE_S` for a whole
event stream.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import structlog

from hal0.agents.hermes_api import (
    DEFAULT_PROFILE,
    api_server_base,
    env_key_name,
    hermes_home,
)
from hal0.bridge.errors import BridgeError

__all__ = ["EVENTS_DEADLINE_S", "HermesGateway", "HermesRefused", "HermesUnavailable"]

log = structlog.get_logger(__name__)

#: Longest one :meth:`HermesGateway.events` call keeps a stream open (ADR-0035
#: decision 5: Hermes streams are bounded at 1800 s). Read at call time.
EVENTS_DEADLINE_S: float = 1800.0

_TIMEOUT = httpx.Timeout(connect=10, read=600, write=30, pool=10)
_HEALTH_TIMEOUT = httpx.Timeout(3.0)

#: The agent id of the default profile; ``hermes/<profile>`` names a profile.
_AGENT = "hermes"

#: A Hermes profile name as it may appear in a URL path segment.
_PROFILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")

#: Longest error text copied from a Hermes response into a bridge error.
_MAX_ERROR_TEXT = 300


class HermesUnavailable(BridgeError):
    """Hermes could not be reached or sent nothing usable (502 ``bridge.agent_unavailable``).

    Raised for a refused connection, a timeout, a stream that broke mid-way and a
    success response that is not JSON.
    """

    def __init__(self, message: str) -> None:
        super().__init__("bridge.agent_unavailable", message)


class HermesRefused(BridgeError):
    """Hermes answered with an error status (``bridge.agent_refused``).

    ``hermes_status`` and ``hermes_code`` are Hermes's own: the HTTP status and
    the ``error.code`` of its response body (None when the body has none), so a
    caller can tell ``stale_generation`` from ``run_not_running``. The bridge
    status passes Hermes's 4xx through, except 401 and 403: those mean *hal0's*
    key was refused, which is not the bridge client's fault, so they and every
    5xx report 502.
    """

    def __init__(self, hermes_status: int, hermes_code: str | None, message: str) -> None:
        passthrough = 400 <= hermes_status < 500 and hermes_status not in (401, 403)
        super().__init__(
            "bridge.agent_refused", message, status=hermes_status if passthrough else 502
        )
        self.hermes_status = hermes_status
        self.hermes_code = hermes_code


class HermesGateway:
    """Client for one Hermes API server and its profiles.

    ``home`` is the Hermes home the API server's address is read from (default
    :func:`~hal0.agents.hermes_api.hermes_home`), ``environ`` the mapping the
    profile keys are read from on every call (default ``os.environ``, which the
    upstream reconcile updates in place), and ``transport`` an optional httpx
    transport for tests.
    """

    def __init__(
        self,
        *,
        home: Path | None = None,
        environ: Mapping[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._home = home
        self._environ: Mapping[str, str] = environ if environ is not None else os.environ
        self._transport = transport

    # -- naming, addresses and keys ----------------------------------------------------

    @staticmethod
    def profile_of(agent: str) -> str:
        """The Hermes profile an agent id names: ``hermes`` is the default profile.

        ``hermes/<profile>`` names a profile; any other id, or a profile name that
        is not a plain URL segment, raises ``bridge.unknown_agent``.
        """
        if agent == _AGENT:
            return DEFAULT_PROFILE
        head, sep, tail = agent.partition("/")
        if head == _AGENT and sep and _PROFILE_NAME.fullmatch(tail):
            return tail
        raise BridgeError(
            "bridge.unknown_agent",
            f"unknown agent {agent!r}: use 'hermes' or 'hermes/<profile>'",
        )

    def base_for(self, profile: str) -> str:
        """Base URL of ``profile``: the API server's, plus ``/p/<profile>`` for a named one."""
        home = self._home if self._home is not None else hermes_home()
        base = api_server_base(home)
        return base if profile == DEFAULT_PROFILE else f"{base}/p/{quote(profile, safe='')}"

    def key_for(self, profile: str) -> str:
        """The API key hal0 holds for ``profile``; ``bridge.agent_unkeyed`` when it holds none."""
        name = env_key_name(profile)
        value = self._environ.get(name, "").strip()
        if not value:
            raise BridgeError(
                "bridge.agent_unkeyed",
                f"hal0 holds no API key for Hermes profile {profile!r} ({name} is unset)",
            )
        return value

    # -- requests ----------------------------------------------------------------------

    def _client(self, timeout: httpx.Timeout = _TIMEOUT) -> httpx.AsyncClient:
        # The Hermes API server is local to this box: ignore the proxy environment.
        return httpx.AsyncClient(transport=self._transport, trust_env=False, timeout=timeout)

    def _headers(self, profile: str, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.key_for(profile)}", **(extra or {})}

    async def _request(
        self,
        method: str,
        profile: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        url = self.base_for(profile) + path
        request_headers = self._headers(profile, headers)
        try:
            async with self._client() as client:
                resp = await client.request(method, url, json=body, headers=request_headers)
        except httpx.HTTPError as exc:
            raise _unavailable(profile, exc) from exc
        if resp.status_code >= 400:
            raise _refusal(resp, profile)
        return resp

    async def _json(
        self,
        method: str,
        profile: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        resp = await self._request(method, profile, path, body=body, headers=headers)
        try:
            out = resp.json()
        except ValueError as exc:
            raise HermesUnavailable(
                f"Hermes profile {profile!r} answered {method} {path} with a body that is not JSON"
            ) from exc
        if not isinstance(out, dict):
            raise HermesUnavailable(
                f"Hermes profile {profile!r} answered {method} {path} with JSON that is not an object"
            )
        if resp.headers.get("idempotency-replayed", "").lower() == "true":
            out["replayed"] = True
        return out

    # -- runs --------------------------------------------------------------------------

    async def start_run(
        self,
        profile: str,
        *,
        input: str,  # Hermes's own field name
        instructions: str | None,
        reasoning_effort: str | None,
        session_key: str | None,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        """``POST /v1/runs``: start a run; returns ``{"run_id", "status", "replayed"}``.

        ``session_key`` (``X-Hermes-Session-Key``, at most 256 characters) binds the
        run to a Hermes conversation and ``idempotency_key`` makes a repeated call
        return the first run with ``replayed`` true. Both are sent as given: the
        bridge namespaces them per client before it calls here (decision 9).
        """
        body: dict[str, Any] = {"input": input}
        if instructions is not None:
            body["instructions"] = instructions
        if reasoning_effort is not None:
            body["model_options"] = {"reasoning": {"effort": reasoning_effort}}
        headers: dict[str, str] = {}
        if session_key is not None:
            headers["X-Hermes-Session-Key"] = session_key
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        return await self._json("POST", profile, "/v1/runs", body=body, headers=headers)

    async def get_run(self, profile: str, run_id: str) -> dict[str, Any]:
        """``GET /v1/runs/<id>``: the run's status, output, usage and pending controls."""
        return await self._json("GET", profile, _run_path(run_id))

    async def approval(
        self,
        profile: str,
        run_id: str,
        *,
        request_id: str,
        execution_generation: int,
        choice: str,
    ) -> dict[str, Any]:
        """``POST .../approval``: answer a pending approval (409 ``stale_generation`` when stale)."""
        body = {
            "request_id": request_id,
            "execution_generation": execution_generation,
            "choice": choice,
        }
        return await self._json("POST", profile, _run_path(run_id, "approval"), body=body)

    async def clarify(
        self,
        profile: str,
        run_id: str,
        *,
        request_id: str,
        execution_generation: int,
        answer: str,
    ) -> dict[str, Any]:
        """``POST .../clarify``: answer the run's clarifying question."""
        body = {
            "request_id": request_id,
            "execution_generation": execution_generation,
            "answer": answer,
        }
        return await self._json("POST", profile, _run_path(run_id, "clarify"), body=body)

    async def steer(
        self,
        profile: str,
        run_id: str,
        input: str,  # Hermes's own field name
    ) -> dict[str, Any]:
        """``POST .../steer``: add guidance to a run that is still ``running``."""
        return await self._json("POST", profile, _run_path(run_id, "steer"), body={"input": input})

    async def stop(self, profile: str, run_id: str) -> dict[str, Any]:
        """``POST .../stop``: ask Hermes to stop the run; returns ``status: "stopping"``."""
        return await self._json("POST", profile, _run_path(run_id, "stop"))

    async def health(self, profile: str) -> int:
        """HTTP status of the profile's ``/health``; ``0`` when Hermes cannot be reached.

        Like :func:`~hal0.agents.hermes_api.probe_status`, 4xx and 5xx are returned
        rather than raised (404 means the gateway does not serve the profile, 401 that
        the key was refused). Sends the profile's key when hal0 holds one.
        """
        key = self._environ.get(env_key_name(profile), "").strip()
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        try:
            async with self._client(_HEALTH_TIMEOUT) as client:
                resp = await client.get(self.base_for(profile) + "/health", headers=headers)
        except httpx.HTTPError:
            return 0
        return resp.status_code

    # -- events ------------------------------------------------------------------------

    async def events(
        self, profile: str, run_id: str, *, last_seq: int
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream a run's events from Hermes, resuming after ``last_seq``.

        Sends ``Last-Event-ID: <last_seq>`` and yields each event's JSON payload as a
        dict with ``seq`` set from the frame's ``id:`` line. Comment lines
        (``: open``, ``: keepalive``, ``: stream closed``) are skipped, and a frame
        whose data is not a JSON object, or that has no sequence number, is dropped
        with a warning.

        The iterator ends when Hermes closes the stream or after
        :data:`EVENTS_DEADLINE_S`; it does not reconnect, so a caller that has not
        seen a terminal event reconnects from the last ``seq`` it kept. A stream that
        breaks raises :class:`HermesUnavailable`, an error status
        :class:`HermesRefused`.
        """
        url = self.base_for(profile) + _run_path(run_id, "events")
        headers = self._headers(
            profile, {"Accept": "text/event-stream", "Last-Event-ID": str(last_seq)}
        )
        deadline = time.monotonic() + EVENTS_DEADLINE_S
        seq: int | None = None
        data: list[str] = []
        try:
            async with (
                self._client() as client,
                client.stream("GET", url, headers=headers) as resp,
            ):
                if resp.status_code >= 400:
                    await resp.aread()
                    raise _refusal(resp, profile)
                async for line in resp.aiter_lines():
                    if time.monotonic() >= deadline:
                        return
                    if line == "":
                        event = _frame_to_event(run_id, seq, data)
                        seq, data = None, []
                        if event is not None:
                            yield event
                    elif line.startswith(":"):
                        continue
                    else:
                        name, _, value = line.partition(":")
                        value = value.removeprefix(" ")
                        if name == "id":
                            seq = int(value) if value.isdecimal() else None
                        elif name == "data":
                            data.append(value)
                        # "event:" is redundant (the payload names its event) and
                        # "retry:" is for browsers; both are ignored.
                event = _frame_to_event(run_id, seq, data)
                if event is not None:
                    yield event
        except httpx.HTTPError as exc:
            raise _unavailable(profile, exc) from exc


def _run_path(run_id: str, action: str = "") -> str:
    path = f"/v1/runs/{quote(run_id, safe='')}"
    return f"{path}/{action}" if action else path


def _frame_to_event(run_id: str, seq: int | None, data: list[str]) -> dict[str, Any] | None:
    """The event a finished SSE frame carries, or None when it carries none."""
    if not data:
        return None
    try:
        payload = json.loads("\n".join(data))
    except ValueError:
        log.warning("bridge.hermes_event_unparseable", run_id=run_id, seq=seq)
        return None
    if not isinstance(payload, dict):
        log.warning("bridge.hermes_event_not_an_object", run_id=run_id, seq=seq)
        return None
    if seq is None:
        payload_seq = payload.get("seq")
        if not isinstance(payload_seq, int):
            log.warning("bridge.hermes_event_without_seq", run_id=run_id)
            return None
        seq = payload_seq
    payload["seq"] = seq
    return payload


def _unavailable(profile: str, exc: httpx.HTTPError) -> HermesUnavailable:
    detail = str(exc) or type(exc).__name__
    return HermesUnavailable(f"Hermes profile {profile!r} is unavailable: {detail}")


def _refusal(resp: httpx.Response, profile: str) -> HermesRefused:
    """Turn an error response into :class:`HermesRefused`, copying Hermes's code and text."""
    code: str | None = None
    text = ""
    try:
        body = resp.json()
    except ValueError:
        body = None
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        raw_code = error.get("code")
        code = raw_code if isinstance(raw_code, str) else None
        raw_message = error.get("message")
        text = raw_message if isinstance(raw_message, str) else ""
    elif isinstance(error, str):
        text = error
    if not text:
        text = resp.text
    text = text.strip()[:_MAX_ERROR_TEXT]
    status = resp.status_code
    if status == 401:
        message = (
            f"Hermes rejected hal0's API key for profile {profile!r} (401): "
            "hal0's copy of the key is stale or wrong"
        )
    else:
        label = f"{status} {code}" if code else str(status)
        message = f"Hermes refused the request for profile {profile!r} ({label})"
    if text:
        message = f"{message}: {text}"
    return HermesRefused(status, code, message)
