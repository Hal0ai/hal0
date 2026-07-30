"""Slot lifecycle state machine.

Defines the canonical SlotState enum used by SlotManager, the dashboard SSE
stream, and the state.json persistence layer.

State machine (PLAN.md §5 Tier 3):

    offline → pulling → starting → warming → ready ←──┐
                                       │      ↑       │
                                       │      ↓       │
                                       └──→ idle ←──serving
                                              │
                                              ↓
                                          unloading → offline
                                              ↑
                                            error

``idle`` covers two cases the dashboard renders the same way but the
state machine reaches via different edges:

  1. **process-up, no model**: ``warming → idle`` when the upstream is
     reachable but ``/v1/models`` is empty (e.g. ``llama-server --model ""``).
     This is preferable to ``ready`` because routers must NOT pick a slot
     that can't fulfil an inference request — see issue #31.
  2. **warm but quiet**: ``ready → idle`` when no request has landed for
     longer than the idle timeout. Surfaces a candidate for unload.

Transitions are atomic, persisted to /var/lib/hal0/slots/<name>/state.json,
and streamable via SSE.  The dashboard surfaces real transitions, not
systemd snapshots.

See PLAN.md §5 Tier 3 and ARCHITECTURE.md §State.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import suppress
from enum import StrEnum
from pathlib import Path
from typing import Any

from hal0.errors import Hal0Error

# ── Enum ─────────────────────────────────────────────────────────────────────


class SlotState(StrEnum):
    """Lifecycle states for a hal0 inference slot.

    Each value is also its JSON/SSE wire representation.
    """

    OFFLINE = "offline"
    """Slot is not running.  No systemd unit active."""

    PULLING = "pulling"
    """Model files are being downloaded or verified.  systemd unit not yet started."""

    STARTING = "starting"
    """systemd unit has been started; waiting for the container to come up."""

    WARMING = "warming"
    """Container is up; health probe is returning non-ready responses while the
    model loads into VRAM / GTT."""

    READY = "ready"
    """Slot passed the full health probe (non-empty /v1/models + sentinel
    completion). Ready to serve requests."""

    SERVING = "serving"
    """An inference request is actively in-flight on this slot."""

    IDLE = "idle"
    """Container is up but cannot fulfil inference requests right now.

    Reached two ways (both surface as ``idle`` on the wire):

      - ``warming → idle``: process started successfully but ``/v1/models``
        is empty (the slot was launched with ``--model ""`` or the model
        file is missing). Routers MUST treat this distinctly from ``ready``
        — see issue #31.
      - ``ready → idle``: a previously-ready slot has received no request
        for longer than the idle timeout. Candidate for unloading.
    """

    UNLOADING = "unloading"
    """Graceful shutdown in progress.  systemd stop issued; waiting for the
    container to exit."""

    ERROR = "error"
    """Slot has failed.  Details in state.json and journald."""


#: Legal transitions: {from_state -> set of reachable states}
#: Enforcement is the SlotManager's responsibility; this is a reference map.
LEGAL_TRANSITIONS: dict[SlotState, frozenset[SlotState]] = {
    SlotState.OFFLINE: frozenset({SlotState.PULLING, SlotState.STARTING}),
    SlotState.PULLING: frozenset({SlotState.STARTING, SlotState.ERROR, SlotState.OFFLINE}),
    SlotState.STARTING: frozenset({SlotState.WARMING, SlotState.ERROR, SlotState.OFFLINE}),
    SlotState.WARMING: frozenset(
        {SlotState.READY, SlotState.IDLE, SlotState.ERROR, SlotState.OFFLINE}
    ),
    SlotState.READY: frozenset(
        {SlotState.SERVING, SlotState.IDLE, SlotState.UNLOADING, SlotState.ERROR}
    ),
    SlotState.SERVING: frozenset({SlotState.READY, SlotState.IDLE, SlotState.ERROR}),
    SlotState.IDLE: frozenset({SlotState.SERVING, SlotState.UNLOADING, SlotState.READY}),
    SlotState.UNLOADING: frozenset({SlotState.OFFLINE, SlotState.ERROR}),
    SlotState.ERROR: frozenset({SlotState.OFFLINE, SlotState.PULLING, SlotState.STARTING}),
}


def is_transition_legal(from_state: SlotState, to_state: SlotState) -> bool:
    """Return True if the transition from_state → to_state is allowed."""
    return to_state in LEGAL_TRANSITIONS.get(from_state, frozenset())


# ── dispatchable ready-set (single source of truth, #696 / DR-8) ─────────────

#: States in which a slot is safe to dispatch inference requests to.
#: This is the ONE canonical definition (finding DR-8): SlotManager, the GPU
#: arbiter, stack apply, the Prometheus metrics renderer, and the slot_view
#: aggregator all reference this set instead of re-declaring their own copy.
#: Because SlotState is a StrEnum, membership works for BOTH enum members and
#: their lowercase wire strings — ``"ready" in DISPATCHABLE_STATES`` is True —
#: so the string-based snapshot callers (metrics / slot_view) share it too.
DISPATCHABLE_STATES: frozenset[SlotState] = frozenset(
    {SlotState.READY, SlotState.SERVING, SlotState.IDLE}
)


def is_dispatchable_state(state: SlotState | str) -> bool:
    """Return True when *state* is in the dispatchable ready-set.

    Accepts a :class:`SlotState` or a raw wire string and applies the same
    str/StrEnum normalisation the snapshot callers use, so a ``SlotState``
    member, its ``.value``, or a plain lowercase string all classify alike.
    """
    return str(getattr(state, "value", state) or "").lower() in DISPATCHABLE_STATES


#: Providers that serve a baked-in model and don't require an explicit model_id.
#: Must stay in sync with ui/src/composables/useSlotStats.js SELF_MANAGED_PROVIDERS.
SELF_MANAGED_PROVIDERS: frozenset[str] = frozenset({"kokoro", "qwen3tts", "moonshine", "vibevoice"})


def provider_requires_model(provider: str | None) -> bool:
    """True when a slot of this provider needs an explicit model_id to serve."""
    return (provider or "").lower() not in SELF_MANAGED_PROVIDERS


# ── Typed errors ─────────────────────────────────────────────────────────────
#
# TIER1: Replaces haloai's silent `{"ok": False, "error": "..."}` dict-return
# pattern at lib/slots.py:59-69 et al.  Every failure surfaces a typed
# Hal0Error subclass so the FastAPI error envelope middleware can render a
# structured response with a stable `slot.*` code.


class SlotError(Hal0Error):
    """Base class for slot-subsystem errors.  Namespace: slot.*"""

    code: str = "slot.error"
    status: int = 500


class SlotNotFound(SlotError):
    """Slot name does not correspond to a configured slot."""

    code = "slot.not_found"
    status = 404


class IllegalSlotTransition(SlotError):
    """Caller attempted a state transition that is not in LEGAL_TRANSITIONS."""

    code = "slot.illegal_transition"
    status = 409


class SlotNotReady(SlotError):
    """Slot exists but is not in READY/SERVING/IDLE state."""

    code = "slot.not_ready"
    status = 503


class SlotSpawnFailed(SlotError):
    """systemctl start (or env write) failed."""

    code = "slot.spawn_failed"
    status = 500


class SlotHealthFailed(SlotError):
    """Health probe did not converge within the grace window."""

    code = "slot.health_failed"
    status = 503


class SlotCrashLooping(SlotError):
    """Load refused by the crash-loop breaker (issue i4).

    A slot in ERROR is re-loadable, but not at request cadence: after a
    load failure, further ``load()`` calls inside the exponential backoff
    window — or against a slot parked after too many consecutive failures
    — raise this WITHOUT any state transition (no STARTING event, no
    journal row, no systemctl call).  ``details`` carries ``retry_after_s``
    so the error middleware emits a ``Retry-After`` header exactly like
    ``SlotLoading``; operator actions (manual load/restart, config edit)
    reset the breaker.
    """

    code = "slot.crash_looping"
    status = 503


class SlotTerminateTimeout(SlotError):
    """The container stop did not return inside its timeout (#1224).

    ``systemctl stop`` against an already-failed unit can block indefinitely.
    The stop runs in an executor thread that we cannot kill, so this does NOT
    mean the stop was cancelled — it means we stopped *waiting* on it, so the
    caller (and the HTTP request behind it) can make forward progress instead
    of hanging. The abandoned thread retires on its own if the stop ever
    completes.
    """

    code = "slot.terminate_timeout"
    status = 504


class SlotConfigError(SlotError):
    """Slot TOML missing or invalid for the requested operation."""

    code = "slot.config_error"
    status = 400


class NpuExclusivityViolation(SlotError):
    """Two ``device=npu, type=llm, enabled=true`` slots cannot coexist.

    The AMDXDNA hardware context admits exactly one NPU LLM at a time
    (plan §5.3). Surfaced as 409 so the dashboard can
    distinguish "you already have one — disable it first" from a 400
    schema error.
    """

    code = "slot.npu_exclusivity_violation"
    status = 409


class SlotPinned(SlotError):
    """A pinned slot was targeted by a manual unload/delete without ``force``.

    §21.10 operator-pin hardening: an anchor pinned either via the default
    anchor set (``agent``/``utility``/``npu``) or an explicit
    ``SlotConfig.pinned = true`` must not be unloaded or deleted by a plain
    ``POST /{name}/unload`` / ``DELETE /{name}`` — that guards against an
    accidental click taking down an always-warm anchor. Pass ``force=true``
    to bypass. Automatic eviction (idle/pressure) already excludes pinned
    slots at the candidate-selection stage, so this error is reserved for
    the manual API surface.
    """

    code = "slot.pinned"
    status = 409


# ── Persistence ──────────────────────────────────────────────────────────────


# TIER3: state.json schema.  Writing through this helper guarantees an atomic
# rename so a dashboard SSE reader never observes a half-written transition.


class SlotStateRecord:
    """Serialisable snapshot of a slot's lifecycle state on disk.

    Stored as JSON at /var/lib/hal0/slots/<name>/state.json.  The schema is
    intentionally flat to keep the file human-readable for debugging.
    """

    __slots__ = ("extra", "message", "model_id", "name", "port", "state", "updated_at")

    def __init__(
        self,
        name: str,
        state: SlotState,
        *,
        model_id: str | None = None,
        port: int = 0,
        updated_at: float | None = None,
        message: str = "",
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.state = state
        self.model_id = model_id
        self.port = port
        self.updated_at = updated_at if updated_at is not None else time.time()
        self.message = message
        self.extra: dict[str, Any] = extra or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "model_id": self.model_id,
            "port": self.port,
            "updated_at": self.updated_at,
            "message": self.message,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SlotStateRecord:
        try:
            state = SlotState(data["state"])
        except (KeyError, ValueError) as exc:
            # TIER1: never silently swallow.  Surface a typed error.
            raise SlotConfigError(
                f"state.json has invalid state: {data!r}",
                details={"data": data},
            ) from exc
        return cls(
            name=data.get("name", ""),
            state=state,
            model_id=data.get("model_id"),
            port=int(data.get("port", 0) or 0),
            updated_at=float(data.get("updated_at", time.time())),
            message=data.get("message", ""),
            extra=data.get("extra") or {},
        )


def write_state_atomic(path: Path | str, record: SlotStateRecord) -> None:
    """Persist a SlotStateRecord atomically.

    Tier 1 fix: matches the env-file pattern.  Same-directory tmpfile +
    os.replace() guarantees readers (SSE clients, dashboard) never see a
    truncated state.json.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(record.to_dict(), indent=2, sort_keys=True) + "\n"

    tmp_path: Path | None = None
    try:
        fd, tmp_str = tempfile.mkstemp(prefix=".hal0-state-", suffix=".tmp", dir=path.parent)
        tmp_path = Path(tmp_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            with suppress(OSError):
                os.close(fd)
            raise
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            with suppress(OSError):
                tmp_path.unlink(missing_ok=True)


def read_state(path: Path | str) -> SlotStateRecord | None:
    """Read a state.json file.  Returns None when the file does not exist.

    A malformed file raises SlotConfigError (Tier 1 — no silent swallow).
    """
    path = Path(path)
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except OSError as exc:
        raise SlotConfigError(
            f"failed to read state.json at {path}: {exc}",
            details={"path": str(path)},
        ) from exc
    except json.JSONDecodeError as exc:
        raise SlotConfigError(
            f"state.json at {path} is not valid JSON: {exc}",
            details={"path": str(path)},
        ) from exc
    return SlotStateRecord.from_dict(data)


__all__ = [
    "DISPATCHABLE_STATES",
    "LEGAL_TRANSITIONS",
    "SELF_MANAGED_PROVIDERS",
    "IllegalSlotTransition",
    "NpuExclusivityViolation",
    "SlotConfigError",
    "SlotCrashLooping",
    "SlotError",
    "SlotHealthFailed",
    "SlotNotFound",
    "SlotNotReady",
    "SlotPinned",
    "SlotSpawnFailed",
    "SlotState",
    "SlotStateRecord",
    "is_dispatchable_state",
    "is_transition_legal",
    "provider_requires_model",
    "read_state",
    "write_state_atomic",
]
