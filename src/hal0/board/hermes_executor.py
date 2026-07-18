"""Concrete Hermes :class:`~hal0.board.dispatch.BoardExecutor` (HP-executor, KB-5).

This is the ``hal0-hermes-executor`` control-plane adapter named in the
integration design (``docs/superpowers/specs/2026-07-18-hal0-hermes-integration-suite-design.md``
§"Kanban executor bridge"): a NARROW bridge that dispatches ONE immutable hal0
board attempt to a Hermes worker, inspects/cancels that external run, and
reconciles after a disconnect. It plugs into the KB-5 seam
(:mod:`hal0.board.dispatch`) via :func:`register`; with no Hermes configured it
never registers, so the board keeps working fully with an empty registry — the
seam's shipped, inert state.

Invariants (authoritative — from the :class:`BoardExecutor` Protocol docstring
and the design §"Kanban executor bridge"):

* **Side-effect-free on canonical hal0 state.** This executor holds NO board
  store and never mutates a card's lane, deps, ownership, approval, or
  completion. It talks HTTP to Hermes and reports back ONLY through the returned
  :class:`AttemptHandle`; hal0's own writeback (owned by
  :class:`hal0.board.store.BoardStore`) turns that handle into an append-only
  run/event. There is no path from here to a store mutator.
* **Acts only on its OWN run.** Every method operates on the single attempt a
  handle identifies (``card_id`` / ``attempt_id`` + the Hermes ``run_id`` /
  ``session_id`` / ``board_id`` / ``task_id`` correlation it fills in).
* **No board mirroring into Hermes.** hal0 stays canonical. Dispatch starts a
  Hermes *run* referencing the hal0 ``card_id``; it never creates or updates a
  Hermes kanban card/board. Raw prompts/transcripts/credentials stay in Hermes;
  hal0 keeps summaries + pointers.

Handle-state model (the ``status`` a returned :class:`AttemptHandle` carries):

* ``running``   — Hermes accepted the dispatch / the worker is live (heartbeat).
* ``blocked``   — the worker needs operator input (a structured **handoff**).
  Surfaced as a blocked handle with the handoff payload in ``detail`` — NEVER by
  mutating board state; hal0 decides what a blocked attempt means for a card.
* ``done`` / ``failed`` — terminal outcomes reported by Hermes.
* ``cancelled`` — a confirmed :meth:`cancel`.
* ``lost``      — :meth:`reconcile` could not recover the run (Hermes has no
  record of it, or Hermes is unreachable). An honest "we don't know / it's
  gone", never a silent success.

Transport / auth conventions mirror :class:`hal0.board.HermesKanbanClient`
(loopback Hermes gateway, ``HERMES_DASHBOARD_BASE_URL``; the ephemeral
per-process session bearer sent as both ``X-Hermes-Session-Token`` and
``Authorization: Bearer``; ``X-hal0-Agent`` for audit attribution) — but this
bridge is SYNCHRONOUS because the KB-5 seam's methods are synchronous, so it uses
a sync :class:`httpx.Client` (tests inject a ``MockTransport`` — no sockets).
"""

from __future__ import annotations

import os
import re
import secrets
from collections.abc import Callable
from typing import Any

import httpx

from hal0.board import (
    DEFAULT_BASE_URL,
    _default_agent_id,
    _default_session_token,
)
from hal0.board.dispatch import AttemptHandle, register_executor

# ── handle-state vocabulary ──────────────────────────────────────────────────

STATUS_RUNNING = "running"
STATUS_BLOCKED = "blocked"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
STATUS_LOST = "lost"

#: Terminal states — an attempt in one of these never advances further.
_TERMINAL: frozenset[str] = frozenset({STATUS_DONE, STATUS_FAILED, STATUS_CANCELLED, STATUS_LOST})

#: Map a Hermes worker ``state`` string onto a hal0 handle status. Anything
#: unrecognised is treated as still-``running`` (a heartbeat we don't have a
#: richer label for), never as a silent completion.
_STATE_MAP: dict[str, str] = {
    "pending": STATUS_RUNNING,
    "queued": STATUS_RUNNING,
    "running": STATUS_RUNNING,
    "active": STATUS_RUNNING,
    "in_progress": STATUS_RUNNING,
    "blocked": STATUS_BLOCKED,
    "waiting": STATUS_BLOCKED,
    "handoff": STATUS_BLOCKED,
    "needs_input": STATUS_BLOCKED,
    "done": STATUS_DONE,
    "completed": STATUS_DONE,
    "succeeded": STATUS_DONE,
    "failed": STATUS_FAILED,
    "error": STATUS_FAILED,
    "cancelled": STATUS_CANCELLED,
    "canceled": STATUS_CANCELLED,
    "lost": STATUS_LOST,
}

# Hermes injects its per-process session bearer into the dashboard HTML it serves
# at ``/`` (loopback, no prior auth). Mirrors the async client's harvest so the
# rotating token needs no manual provisioning.
_TOKEN_RE = re.compile(r'window\.__HERMES_SESSION_TOKEN__\s*=\s*"([^"]+)"')

# The Hermes worker/run ledger surface (the "detailed worker ledger" the design
# calls Hermes Kanban). Attempts are dispatched as *runs* here — this is NOT the
# board CRUD surface, so nothing on hal0's board is mirrored into a Hermes card.
WORKER_BASE_PATH = "/api/plugins/kanban/runs"


def _map_state(raw: Any) -> str:
    if not isinstance(raw, str):
        return STATUS_RUNNING
    return _STATE_MAP.get(raw.strip().lower(), STATUS_RUNNING)


# ── sync loopback gateway ────────────────────────────────────────────────────


class _HermesGateway:
    """Minimal SYNC HTTP client for the Hermes worker/run surface.

    Same auth shape as :class:`hal0.board.HermesKanbanClient` (session bearer in
    both accepted header forms + ``X-hal0-Agent``), but synchronous so the KB-5
    seam's sync methods can call it. Tests inject ``http_client`` backed by an
    :class:`httpx.MockTransport` — no sockets are ever opened.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token_resolver: Callable[[], str | None] | None = None,
        agent_id: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._resolve_token = token_resolver or _default_session_token
        self._agent_id = agent_id or _default_agent_id()
        self._owns = http_client is None
        self._http = http_client or httpx.Client(
            base_url=self._base_url,
            timeout=httpx.Timeout(30.0, connect=3.0),
        )
        self._session_token: str | None = None

    def _headers(self, token: str | None) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "X-hal0-Agent": self._agent_id}
        if token:
            headers["X-Hermes-Session-Token"] = token
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _current_token(self, *, force_refresh: bool = False) -> str | None:
        pinned = self._resolve_token()
        if pinned:
            return pinned
        if force_refresh:
            self._session_token = None
        if self._session_token is None:
            self._session_token = self._fetch_html_token()
        return self._session_token

    def _fetch_html_token(self) -> str | None:
        try:
            resp = self._http.get("/")
        except httpx.HTTPError:
            return None
        if resp.status_code >= 400:
            return None
        match = _TOKEN_RE.search(resp.text or "")
        return match.group(1) if match else None

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
    ) -> tuple[int, Any]:
        """Send one request; return ``(status_code, parsed_json)``.

        A transport failure surfaces as status ``0`` with an ``{"error": ...}``
        body so callers can translate it into an honest handle state (never a
        raised exception that would blow up the seam's dispatch loop). Any HTTP
        status is returned verbatim so callers can distinguish 404-run-unknown
        from a live run.
        """

        def _send(token: str | None) -> httpx.Response:
            return self._http.request(method, path, headers=self._headers(token), json=json_body)

        try:
            resp = _send(self._current_token())
            # A 401 on a harvested (non-pinned) token means Hermes rotated it on
            # restart — drop the cache, re-harvest once, and retry.
            if resp.status_code == 401 and not self._resolve_token():
                resp = _send(self._current_token(force_refresh=True))
        except httpx.HTTPError as exc:
            return 0, {"error": str(exc)}
        if not resp.content:
            return resp.status_code, {}
        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, {"raw": resp.text}

    def close(self) -> None:
        if self._owns:
            self._http.close()


# ── the executor ─────────────────────────────────────────────────────────────


class HermesBoardExecutor:
    """Concrete :class:`~hal0.board.dispatch.BoardExecutor` for target ``hermes``.

    Holds NO board store — it can only talk HTTP and return handles, which is the
    structural guarantee behind the "side-effect-free on canonical hal0 state"
    invariant. Construct with an injected gateway/``http_client`` in tests; in
    production :func:`register` builds one from the environment.
    """

    target = "hermes"

    def __init__(
        self,
        *,
        gateway: _HermesGateway | None = None,
        base_url: str | None = None,
        token_resolver: Callable[[], str | None] | None = None,
        agent_id: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._gw = gateway or _HermesGateway(
            base_url=base_url,
            token_resolver=token_resolver,
            agent_id=agent_id,
            http_client=http_client,
        )

    # ── correlation helpers ──────────────────────────────────────────────

    @staticmethod
    def _new_attempt_id(card_id: str) -> str:
        return f"hp-{card_id}-{secrets.token_hex(4)}"

    @staticmethod
    def _correlation(payload: dict[str, Any]) -> dict[str, Any]:
        """Pull the Hermes-side correlation ids out of a worker response."""
        out: dict[str, Any] = {}
        for field, keys in (
            ("run_id", ("run_id", "runId", "id")),
            ("session_id", ("session_id", "sessionId")),
            ("board_id", ("board_id", "boardId", "board")),
            ("task_id", ("task_id", "taskId")),
        ):
            for key in keys:
                val = payload.get(key)
                if val:
                    out[field] = str(val)
                    break
        return out

    @staticmethod
    def _detail(handle: AttemptHandle, payload: dict[str, Any], status: str) -> dict[str, Any]:
        """Merge worker telemetry (heartbeat / handoff) into the handle detail.

        Append-only, executor-local metadata — this rides on the returned handle
        and NEVER reshapes canonical board state. A ``blocked`` status carries
        the structured handoff payload so an operator can see what input the
        worker is waiting on.
        """
        detail = dict(handle.detail)
        for key in ("heartbeat", "heartbeat_at", "progress", "message"):
            if key in payload:
                detail[key] = payload[key]
        if status == STATUS_BLOCKED:
            handoff = payload.get("handoff") or payload.get("block") or payload.get("reason")
            if handoff is not None:
                detail["handoff"] = handoff
        error = payload.get("error")
        if error is not None:
            detail["error"] = error
        return detail

    # ── BoardExecutor protocol ───────────────────────────────────────────

    def dispatch(self, card_id: str, *, context: dict[str, Any]) -> AttemptHandle:
        """Start one Hermes worker run for ``card_id``; return its handle.

        Posts the hal0 correlation (``card_id`` / ``attempt_id``) plus the
        caller's ``context`` (summaries/pointers only — no board mirroring) to
        the Hermes run surface. On a transport/upstream failure the attempt is
        recorded as an immediately-``failed`` handle (honest: we tried and it
        did not start) rather than raising into the seam's dispatch loop.
        """
        attempt_id = self._new_attempt_id(card_id)
        base = AttemptHandle(
            card_id=card_id,
            attempt_id=attempt_id,
            target=self.target,
            executor=self.target,
            status="pending",
        )
        status_code, payload = self._gw.request(
            "POST",
            WORKER_BASE_PATH,
            json_body={"card_id": card_id, "attempt_id": attempt_id, "context": context or {}},
        )
        if status_code == 0 or status_code >= 400 or not isinstance(payload, dict):
            reason = "unreachable" if status_code == 0 else f"upstream_{status_code}"
            return base.with_status(
                STATUS_FAILED,
                detail={**base.detail, "reason": reason, "error": _err(payload)},
            )
        status = _map_state(payload.get("state") or payload.get("status"))
        correlation = self._correlation(payload)
        handle = base.with_status(status, **correlation)
        return handle.with_status(status, detail=self._detail(handle, payload, status))

    def inspect(self, handle: AttemptHandle) -> AttemptHandle:
        """Poll the run's current state (incl. worker heartbeat / handoff).

        A read: on a transient transport failure the LAST-KNOWN handle is
        returned unchanged (reconcile, not inspect, is the disconnect-recovery
        path). Terminal handles and handles with no ``run_id`` are returned as-is.
        """
        if handle.status in _TERMINAL or not handle.run_id:
            return handle
        status_code, payload = self._gw.request("GET", f"{WORKER_BASE_PATH}/{handle.run_id}")
        if status_code == 0 or status_code >= 400 or not isinstance(payload, dict):
            return handle  # transient — keep last-known state
        status = _map_state(payload.get("state") or payload.get("status"))
        updated = handle.with_status(status, **self._correlation(payload))
        return updated.with_status(status, detail=self._detail(updated, payload, status))

    def cancel(self, handle: AttemptHandle) -> AttemptHandle:
        """Cancel the external run; return the terminal handle.

        A confirmed cancel yields a ``cancelled`` handle. If Hermes cannot be
        reached (or answers an error), the handle's status is left UNCHANGED and
        the failure is recorded in ``detail['cancel_error']`` — we do not claim a
        cancellation we could not confirm; :meth:`reconcile` resolves it later.
        """
        if handle.status in _TERMINAL or not handle.run_id:
            return handle.with_status(STATUS_CANCELLED) if not handle.run_id else handle
        status_code, payload = self._gw.request(
            "POST", f"{WORKER_BASE_PATH}/{handle.run_id}/cancel"
        )
        if status_code == 0 or status_code >= 400:
            return handle.with_status(
                handle.status,
                detail={**handle.detail, "cancel_error": _err(payload)},
            )
        return handle.with_status(STATUS_CANCELLED)

    def reconcile(self, handle: AttemptHandle) -> AttemptHandle:
        """Re-sync after a disconnect: RECOVER the run's state, or declare it LOST.

        * Hermes answers with the run → recover: map its state onto the handle.
        * Hermes answers 404 (no record) or the handle never got a ``run_id`` →
          the run is gone: ``lost``.
        * Hermes is unreachable → we cannot confirm anything: ``lost`` with a
          ``reason`` of ``unreachable`` (honest "we don't know", not a guess it
          is still running).
        """
        if not handle.run_id:
            return handle.with_status(STATUS_LOST, detail={**handle.detail, "reason": "no_run_id"})
        status_code, payload = self._gw.request("GET", f"{WORKER_BASE_PATH}/{handle.run_id}")
        if status_code == 0:
            return handle.with_status(
                STATUS_LOST,
                detail={**handle.detail, "reason": "unreachable", "error": _err(payload)},
            )
        if status_code == 404:
            return handle.with_status(
                STATUS_LOST, detail={**handle.detail, "reason": "run_unknown"}
            )
        if status_code >= 400 or not isinstance(payload, dict):
            return handle.with_status(
                STATUS_LOST, detail={**handle.detail, "reason": f"upstream_{status_code}"}
            )
        status = _map_state(payload.get("state") or payload.get("status"))
        recovered = handle.with_status(status, **self._correlation(payload))
        detail = {**self._detail(recovered, payload, status), "reconciled": True}
        return recovered.with_status(status, detail=detail)

    def close(self) -> None:
        self._gw.close()


def _err(payload: Any) -> Any:
    if isinstance(payload, dict):
        return payload.get("error") or payload.get("upstream") or payload
    return payload


# ── registration (inert by default) ──────────────────────────────────────────


def _is_configured() -> bool:
    """Is Hermes configured for this process? (config presence, NO network call).

    The signal is an explicit ``HERMES_DASHBOARD_BASE_URL`` in the environment —
    the operator pointing hal0 at a Hermes gateway. Absent it, the bridge stays
    inert: :func:`register` returns ``False``, the KB-5 registry stays empty, and
    the board runs fully with no executor (the seam's shipped state). Reachability
    is deliberately NOT probed here — a configured-but-down Hermes still registers
    and surfaces its failures honestly per-dispatch.
    """
    return bool(os.environ.get("HERMES_DASHBOARD_BASE_URL", "").strip())


def register(app_or_config: Any = None) -> bool:
    """Register the Hermes :class:`BoardExecutor` for target ``hermes`` — if configured.

    Wired into app startup (:mod:`hal0.api`). Returns ``True`` when an executor
    was registered, ``False`` when Hermes is not configured (inert). ``app_or_config``
    is accepted for the startup call site but is intentionally not required — the
    guard reads the environment, so there is no network call at import/startup.
    """
    if not _is_configured():
        return False
    base_url = os.environ.get("HERMES_DASHBOARD_BASE_URL") or None
    register_executor("hermes", HermesBoardExecutor(base_url=base_url))
    return True


__all__ = [
    "WORKER_BASE_PATH",
    "HermesBoardExecutor",
    "register",
]
