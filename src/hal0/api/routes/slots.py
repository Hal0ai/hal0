"""Slot lifecycle endpoints (mounted under /api/slots).

Phase 1: real SlotManager-backed lifecycle wired alongside synthetic
upstream-backed entries. Real slots win on name collision; synthetic
entries persist for remote-upstream visibility in the dashboard until
every upstream has a corresponding local slot.

SSE endpoints (note: there is no ``/api/slots/{name}/events`` — the
stream is split by concern):

- ``GET /api/slots/{name}/state/stream`` — state-machine transitions
  for one slot (``starting → warming → ready → serving → idle …``).
- ``GET /api/slots/{name}/logs/stream`` — line-by-line journal tail
  for the slot's systemd unit.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from hal0.api.middleware.auth import require_writer
from hal0.api.middleware.error_codes import BadRequest, Hal0Error
from hal0.slots.manager import Slot, SlotManager

# Reusable writer-scope gate applied per-route on every POST/PUT/PATCH/DELETE.
# The router itself is mounted with require_token at include_router() time
# (see hal0.api.create_app), which keeps GETs open to read-only tokens
# while these per-route deps enforce the writer scope on mutations.
_writer = [Depends(require_writer)]

router = APIRouter()


class NotImplementedYet(Hal0Error):
    code = "system.not_implemented"
    status = 501


# ── helpers ────────────────────────────────────────────────────────────────


def _get_slot_manager(request: Request) -> SlotManager:
    """Pull the SlotManager off app.state (wired in the lifespan).

    Missing manager raises a typed system.internal so the error envelope
    middleware renders consistently — should never happen outside tests
    that bypass the lifespan.
    """
    sm = getattr(request.app.state, "slot_manager", None)
    if sm is None:
        raise Hal0Error(
            "slot_manager not initialised on app.state",
            details={"hint": "lifespan did not run"},
        )
    return sm


def _slot_to_dict(slot: Slot, request: Request | None = None) -> dict[str, Any]:
    """Serialise a real Slot snapshot into the API shape.

    Adds ``kind="local"`` so the UI can distinguish real slots from the
    synthetic upstream-backed entries (which carry ``kind="remote"`` or
    similar and ``_synthetic: true``).

    When ``request`` is provided, also includes a ``models`` list pulled
    from the shared model cache. For an FLM slot serving chat + embed +
    asr concurrently, this surfaces all three tags so the dashboard can
    render the slot as multi-model instead of showing only the chat tag.
    """
    base = slot.as_dict()
    base["kind"] = "local"
    base["status"] = slot.state.value
    # Lift backend / provider out of metadata to the top level so the UI
    # doesn't have to dig — the slot snapshot's `backend` is only set on
    # transitions that pass it explicitly, but metadata carries both
    # consistently after create / update_config.
    meta = base.get("metadata") or {}
    if not base.get("backend") and meta.get("backend"):
        base["backend"] = meta.get("backend")
    if not base.get("provider") and meta.get("provider"):
        base["provider"] = meta.get("provider")
    if request is not None:
        cache = getattr(request.app.state, "model_cache", {}) or {}
        loaded = list(cache.get(slot.name, []))
        if slot.model_id and slot.model_id in loaded:
            loaded.remove(slot.model_id)
            loaded.insert(0, slot.model_id)
        base["models"] = loaded
    return base


def _synthesize_slots_from_upstreams(request: Request) -> list[dict[str, Any]]:
    """Build virtual slot entries from configured upstreams.

    Until every upstream has a corresponding local slot, the dashboard
    still needs to show remote-backed inference targets. Each upstream
    surfaces as a read-only slot entry: status="serving" when its model
    cache is populated, "offline" otherwise.

    The slot's ``model`` reflects the most recently dispatched model id
    for this upstream (tracked in ``app.state.last_used_model``); falls
    back to the first non-alias from the catalog before any inference
    has happened.
    """
    upstreams = request.app.state.upstreams
    cache = getattr(request.app.state, "model_cache", {})
    last_used = getattr(request.app.state, "last_used_model", {})
    out: list[dict[str, Any]] = []
    for u in upstreams.list():
        models = cache.get(u.name, [])
        from hal0.api.routes.models import _is_alias  # local to avoid cycle

        real_models = [m for m in models if not _is_alias(m)]
        primary_model = (
            last_used.get(u.name)
            or (real_models[0] if real_models else "")
            or (models[0] if models else "")
        )
        out.append(
            {
                "name": u.name,
                "kind": u.kind,
                "model": primary_model,
                "status": "serving" if models else "offline",
                "backend": "remote" if u.kind == "remote" else "vulkan",
                "provider": "remote-upstream" if u.kind == "remote" else "llama-server",
                "url": u.url,
                "advertised_models": len(models),
                "last_used_model": last_used.get(u.name) or None,
                "_synthetic": True,
                "_synthetic_reason": (
                    "Backed by remote upstream; install a local slot of the same name to take over."
                ),
            }
        )
    return out


# ── list / create ──────────────────────────────────────────────────────────


@router.get("")
async def list_slots(request: Request) -> list[dict[str, object]]:
    """List configured slots.

    Merges real SlotManager-backed entries with synthetic upstream-backed
    ones. Real slots win on name collision so the dashboard sees a single
    authoritative row per slot name once a local slot is installed.
    """
    sm = _get_slot_manager(request)
    real_slots = await sm.list()
    real_entries: list[dict[str, Any]] = [_slot_to_dict(s, request) for s in real_slots]
    real_names = {entry["name"] for entry in real_entries}

    synthetic = _synthesize_slots_from_upstreams(request)
    merged: list[dict[str, Any]] = list(real_entries)
    for entry in synthetic:
        if entry["name"] not in real_names:
            merged.append(entry)
    return merged


@router.post("", status_code=201, dependencies=_writer)
async def create_slot(request: Request) -> dict[str, object]:
    """Create a new slot. Body: SlotConfig schema.

    Writes /etc/hal0/slots/<name>.toml, the systemd drop-in override, the
    env file, and the initial state.json. Does NOT start the slot — the
    caller follows with POST /api/slots/<name>/load when ready.
    """
    sm = _get_slot_manager(request)
    try:
        body = await request.json()
    except Exception as exc:
        raise BadRequest(
            "request body must be valid JSON",
            details={"error": str(exc)},
            code="request.invalid_json",
        ) from exc
    if not isinstance(body, dict):
        raise BadRequest("request body must be a JSON object", code="request.not_an_object")

    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        raise BadRequest(
            "slot 'name' is required (non-empty string)",
            code="slot.name_required",
        )

    snap = await sm.create(name, body)
    return _slot_to_dict(snap, request)


# ── metrics / capacity ─────────────────────────────────────────────────────


def _local_throughput_tps(request: Request, window_s: float = 5.0) -> float:
    """Compute current tokens/sec from the rolling tps_events window.

    Rate is ``tokens / (last_event_ts - first_event_ts_in_window)`` rather
    than ``tokens / window_s`` so short bursts read at their real rate
    instead of being smeared across the full lookback. Decays to 0 once
    all events age out.
    """
    import time

    events = getattr(request.app.state, "tps_events", None)
    if not events:
        return 0.0
    now = time.monotonic()
    in_window = [(ts, tok) for ts, tok in events if now - ts <= window_s]
    if len(in_window) < 2:
        return 0.0
    total_tokens = sum(tok for _, tok in in_window)
    span = in_window[-1][0] - in_window[0][0]
    # Bias slightly toward the window so a stale-but-recent burst still
    # decays instead of pegging at peak forever.
    effective_span = max(span, (now - in_window[-1][0]))
    if effective_span <= 0:
        return 0.0
    return total_tokens / effective_span


@router.get("/metrics")
async def slot_metrics(request: Request) -> dict[str, Any]:
    """Per-slot runtime metrics keyed by slot name.

    Drives the dashboard's per-slot GTT bars + throughput sparkline.
    Proxies remote upstreams via /api/stats/slots; real local SlotManager
    metrics merge in once the manager is wired.

    Adds a synthetic ``__hal0_local__`` entry carrying current TPS
    measured from the streaming dispatcher path — covers the case where
    the upstream (e.g. FLM/NPU on haloai) doesn't report tps itself.
    """
    from hal0.api.routes.hardware import stats_slots

    merged = await stats_slots(request)
    tps = _local_throughput_tps(request)
    if tps > 0 or "__hal0_local__" not in merged:
        merged["__hal0_local__"] = {
            "name": "__hal0_local__",
            "tokens_per_sec": tps,
            "_synthetic": True,
        }
    return merged


@router.get("/capacity")
async def slot_capacity() -> dict[str, object]:
    raise NotImplementedYet("slot_capacity: Phase 1")


# ── per-slot ───────────────────────────────────────────────────────────────


@router.get("/{name}")
async def get_slot(name: str, request: Request) -> dict[str, object]:
    """Return a snapshot of a single slot.

    Real slots come from the SlotManager; if the name isn't a configured
    local slot, fall through to the synthetic upstream-backed entry.
    SlotNotFound surfaces as the typed slot.not_found envelope.
    """
    sm = _get_slot_manager(request)
    try:
        snap = await sm.status(name)
        return _slot_to_dict(snap, request)
    except Exception:
        # Fall through to synthetic lookup before re-raising — a remote
        # upstream named ``haloai`` should be observable via this endpoint
        # even though it isn't a real slot.
        for entry in _synthesize_slots_from_upstreams(request):
            if entry["name"] == name:
                return entry
        raise


@router.delete("/{name}", dependencies=_writer)
async def delete_slot(name: str, request: Request) -> dict[str, object]:
    """Delete a slot. If the slot is running, it is stopped first.

    Built-in slots (primary/embed/stt/tts) cannot be deleted — the
    SlotManager raises a typed error which the envelope middleware
    surfaces as 4xx.
    """
    sm = _get_slot_manager(request)
    await sm.delete(name)
    return {"name": name, "deleted": True}


@router.get("/{name}/config")
async def get_slot_config(name: str, request: Request) -> dict[str, object]:
    """Return the slot's TOML config as a dict."""
    sm = _get_slot_manager(request)
    cfg = await sm.get_config(name)
    return cfg


@router.put("/{name}/config", dependencies=_writer)
async def update_slot_config(name: str, request: Request) -> dict[str, object]:
    """Update a slot's config. Body: partial SlotConfig (shallow merge)."""
    sm = _get_slot_manager(request)
    try:
        body = await request.json()
    except Exception as exc:
        raise Hal0Error(
            "request body must be valid JSON",
            details={"error": str(exc)},
        ) from exc
    if not isinstance(body, dict):
        raise Hal0Error("request body must be a JSON object")
    snap = await sm.update_config(name, body)
    return _slot_to_dict(snap, request)


@router.patch("/{name}/defaults", dependencies=_writer)
async def update_slot_defaults(name: str, request: Request) -> dict[str, object]:
    """Update slot defaults (ctx_size, temperature, etc.).

    Convenience wrapper over update_config — body keys merge into the
    slot's [model] sub-table rather than the top level.
    """
    sm = _get_slot_manager(request)
    try:
        body = await request.json()
    except Exception as exc:
        raise Hal0Error("request body must be valid JSON", details={"error": str(exc)}) from exc
    if not isinstance(body, dict):
        raise Hal0Error("request body must be a JSON object")
    snap = await sm.update_config(name, {"model": body})
    return _slot_to_dict(snap, request)


@router.post("/{name}/backend", dependencies=_writer)
async def set_slot_backend(name: str, request: Request) -> dict[str, object]:
    """Switch a slot's backend (e.g., vulkan → rocm)."""
    sm = _get_slot_manager(request)
    try:
        body = await request.json()
    except Exception as exc:
        raise Hal0Error("request body must be valid JSON", details={"error": str(exc)}) from exc
    backend = body.get("backend") if isinstance(body, dict) else None
    if not isinstance(backend, str) or not backend.strip():
        raise Hal0Error("'backend' is required in request body")
    snap = await sm.update_config(name, {"backend": backend})
    return _slot_to_dict(snap, request)


# ── lifecycle ──────────────────────────────────────────────────────────────


@router.post("/{name}/load", dependencies=_writer)
async def load_slot(name: str, request: Request) -> dict[str, object]:
    """Load a model into a slot. Optional body: {"model_id": "..."}."""
    sm = _get_slot_manager(request)
    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        # POST without a body is fine — fall back to the slot's default model.
        body = {}
    model_id = body.get("model_id") if isinstance(body, dict) else None
    snap = await sm.load(name, model_id=model_id)
    return _slot_to_dict(snap, request)


@router.post("/{name}/unload", dependencies=_writer)
async def unload_slot(name: str, request: Request) -> dict[str, object]:
    sm = _get_slot_manager(request)
    snap = await sm.unload(name)
    return _slot_to_dict(snap, request)


@router.post("/{name}/restart", dependencies=_writer)
async def restart_slot(name: str, request: Request) -> dict[str, object]:
    sm = _get_slot_manager(request)
    snap = await sm.restart(name)
    return _slot_to_dict(snap, request)


@router.post("/{name}/swap", dependencies=_writer)
async def swap_slot(name: str, request: Request) -> dict[str, object]:
    """Hot-swap a slot's model. Body: {"model_id": "..."}."""
    sm = _get_slot_manager(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    model_id = body.get("model_id") if isinstance(body, dict) else None
    if not model_id:
        raise Hal0Error(
            "swap requires a non-empty model_id in the request body",
            details={"slot": name},
        )
    snap = await sm.swap(name, model_id)
    return _slot_to_dict(snap, request)


# ── logs ───────────────────────────────────────────────────────────────────
#
# Real journalctl-backed log access is a Phase 2 surface — the SlotManager
# doesn't expose a logs() method today (state lives in journald, not in
# the manager). Leaving these as typed 501 stubs so a UI build that
# touches them gets a clear envelope rather than a 404.


@router.get("/{name}/logs")
async def slot_logs(name: str, request: Request, lines: int = 200) -> dict[str, object]:
    """Return the last ``lines`` of this slot's journal output.

    Best-effort: on hosts without systemd or where the slot has never
    started, returns an empty string with a hint. The UI tolerates that
    (renders "No logs available") rather than treating it as an error.
    """
    import asyncio as _asyncio
    import shutil

    sm = _get_slot_manager(request)
    # Validate slot exists so unknown names get the typed slot.not_found
    # envelope instead of an empty 200.
    await sm.status(name)

    if shutil.which("journalctl") is None:
        return {"name": name, "logs": "", "hint": "journalctl not available on this host"}

    cmd = [
        "journalctl",
        "-u",
        f"hal0-slot@{name}.service",
        "-n",
        str(max(1, min(int(lines or 200), 5000))),
        "--no-pager",
        "-o",
        "short-iso",
    ]
    proc = await _asyncio.create_subprocess_exec(
        *cmd,
        stdout=_asyncio.subprocess.PIPE,
        stderr=_asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await _asyncio.wait_for(proc.communicate(), timeout=5.0)
    except TimeoutError:
        with contextlib_suppress():
            proc.kill()
        return {"name": name, "logs": "", "hint": "journalctl timed out"}
    return {"name": name, "logs": stdout.decode("utf-8", errors="replace")}


def contextlib_suppress():
    """Local helper so the import isn't pulled in just for one suppress."""
    import contextlib

    return contextlib.suppress(ProcessLookupError, OSError)


@router.get("/{name}/logs/stream")
async def slot_logs_stream(name: str, request: Request) -> StreamingResponse:
    """SSE stream that tails this slot's journal output line-by-line.

    Best-effort: gracefully exits when journalctl is missing or the slot
    has no journal entries yet. Client disconnects close the subprocess.
    """
    import asyncio as _asyncio
    import shutil

    sm = _get_slot_manager(request)
    await sm.status(name)  # 404 fast if unknown

    async def event_source() -> Any:
        if shutil.which("journalctl") is None:
            yield 'event: error\ndata: {"message":"journalctl unavailable"}\n\n'
            return
        cmd = [
            "journalctl",
            "-u",
            f"hal0-slot@{name}.service",
            "-f",
            "-n",
            "0",
            "--output=cat",
            "--no-pager",
        ]
        proc = await _asyncio.create_subprocess_exec(
            *cmd,
            stdout=_asyncio.subprocess.PIPE,
            stderr=_asyncio.subprocess.DEVNULL,
        )
        try:
            assert proc.stdout is not None
            async for raw in proc.stdout:
                line = raw.decode("utf-8", errors="replace").rstrip("\n")
                if not line:
                    continue
                yield f"data: {json.dumps(line)}\n\n"
        except asyncio.CancelledError:
            raise
        finally:
            with contextlib_suppress():
                proc.kill()
            with contextlib_suppress():
                await proc.wait()

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── state ──────────────────────────────────────────────────────────────────


@router.get("/{name}/state")
async def slot_state(name: str, request: Request) -> dict[str, object]:
    """Return just the state-machine fields for this slot.

    Lighter than /api/slots/{name} — used by clients that poll for a
    transition without needing the full metadata payload.
    """
    sm = _get_slot_manager(request)
    snap = await sm.status(name)
    return {
        "name": snap.name,
        "state": snap.state.value,
        "port": snap.port,
        "model_id": snap.model_id,
        "backend": snap.backend,
    }


@router.get("/{name}/state/stream")
async def slot_state_stream(name: str, request: Request) -> StreamingResponse:
    """SSE stream of state transitions for ``name`` (and only ``name``).

    The SlotManager's state_stream() is fanned out across all slots; this
    endpoint filters to a single slot to keep the wire chatty only where
    the UI is looking. Initial event carries the current snapshot so a
    client that subscribes after a transition still sees the latest
    state without a separate GET.

    SSE event shape::

        event: state
        data: {"name": "...", "state": "ready", "port": 8081, ...}
    """
    sm = _get_slot_manager(request)
    # Confirm the slot exists before opening the long-lived stream — keeps
    # the 404 surface fast and synchronous.
    snap = await sm.status(name)
    initial = {
        "name": snap.name,
        "state": snap.state.value,
        "port": snap.port,
        "model_id": snap.model_id,
        "backend": snap.backend,
    }

    async def event_source() -> Any:
        # Initial snapshot so late subscribers don't wait for the next
        # transition just to learn the current state.
        yield f"event: state\ndata: {json.dumps(initial)}\n\n"
        try:
            async for rec in sm.state_stream():
                if rec.name != name:
                    continue
                payload = {
                    "name": rec.name,
                    "state": rec.state.value,
                    "port": rec.port,
                    "model_id": rec.model_id,
                    "message": rec.message,
                    "updated_at": rec.updated_at,
                }
                yield f"event: state\ndata: {json.dumps(payload)}\n\n"
        except asyncio.CancelledError:
            # Client disconnected — let the generator wind down cleanly so
            # the SlotManager removes the subscriber queue.
            raise

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
