"""Backend introspection endpoints.

Mounted under ``/api/backends`` (see :mod:`hal0.api.__init__`):

  - ``GET  /api/backends``           — list every backend with its live status.
  - ``GET  /api/backends/{id}``      — single-backend detail.
  - ``POST /api/backends/npu/load``  — bring up an FLM slot for one model tag.
  - ``POST /api/backends/npu/unload``— tear down a previously loaded NPU slot.

The shape is tuned for the dashboard's Capability-slots footer cards.
Several fields are placeholders for this round (``totalReqPerSec``
returns 0 until a stats source lands).
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, Request

from hal0.api.deps import SlotManagerDep
from hal0.api.middleware.auth import require_writer
from hal0.api.middleware.error_codes import BadRequest
from hal0.capabilities.catalog import available_backends, get_backend
from hal0.capabilities.orchestrator import _CHILD_TO_SLOT
from hal0.config.loader import load_hardware_info
from hal0.errors import Hal0Error, NotFound

router = APIRouter()

# Writer-scope gate for the POST routes (mirrors the slots router pattern).
_writer = [Depends(require_writer)]

# ── NPU dynamic-slot config ───────────────────────────────────────────────────
# The NPU backend supports operator-driven slot creation from the dashboard's
# "+ load NPU model" button. Slots created this way live under the npu- prefix
# and pick ports from a private range so they don't collide with the built-in
# slots (8081-8087 reserve primary / nano / embed / stt / etc.).
_NPU_SLOT_PREFIX = "npu-"
_NPU_PORT_MIN = 8088
_NPU_PORT_MAX = 8099


class _NoFreeSlotPort(Hal0Error):
    """No free port available in the NPU dynamic-slot range."""

    code = "backend.no_slot_port"
    status = 409


def _sanitize_slot_suffix(model_id: str) -> str:
    """Map an FLM tag (e.g. ``qwen3.5:9b``) to a slot-name-safe suffix.

    Slot names land in systemd unit names + on-disk paths, so we lock to
    ``[a-z0-9-]`` and cap length so the rendered unit stays well under
    systemd's 256-char limit.
    """
    cleaned = re.sub(r"[^A-Za-z0-9-]+", "-", model_id).strip("-").lower()
    return cleaned[:48] or "model"


def _npu_slot_name(model_id: str) -> str:
    """Slot name used for a given NPU-loaded model tag."""
    return f"{_NPU_SLOT_PREFIX}{_sanitize_slot_suffix(model_id)}"


# ── helpers ──────────────────────────────────────────────────────────────────


def _hardware_for_backend(backend_id: str) -> str:
    """Render the ``hardware`` field for a backend card.

    Pulls from the cached hardware probe. Returns ``"n/a"`` when the
    relevant component wasn't detected.
    """
    try:
        hw = load_hardware_info()
    except Exception:
        return "n/a"
    if backend_id == "npu":
        if hw.npu and hw.npu.present:
            return hw.npu.name or "AMD XDNA"
        return "n/a"
    if backend_id in {"gpu-vulkan", "gpu-rocm"}:
        if hw.gpus:
            return hw.gpus[0].name or hw.gpus[0].vendor or "GPU"
        return "n/a"
    if backend_id == "cpu":
        return hw.cpu_model or "CPU"
    return "n/a"


def _driver_for_backend(backend_id: str) -> str:
    """Return the driver / runtime string for the backend card."""
    try:
        hw = load_hardware_info()
    except Exception:
        return ""
    if backend_id == "npu":
        if hw.npu and hw.npu.driver:
            return hw.npu.driver
        return "amdxdna"
    if backend_id == "gpu-vulkan":
        if hw.gpus:
            return "Mesa Vulkan"
        return ""
    if backend_id == "gpu-rocm":
        if hw.gpus:
            return hw.gpus[0].driver or "ROCm"
        return ""
    if backend_id == "cpu":
        return ""
    return ""


def _mem_totals_for_backend(backend_id: str) -> tuple[int, int]:
    """Return ``(memUsedMb, memTotalMb)`` best-effort.

    ``memUsedMb`` is set to 0 in this round — wiring per-slot residency
    will piggyback on the existing slot stats surface. ``memTotalMb``
    is derived from the hardware probe so the dashboard's capacity bar
    isn't blank.
    """
    try:
        hw = load_hardware_info()
    except Exception:
        return 0, 0
    if backend_id == "npu":
        # The AMDXDNA driver doesn't expose per-NPU residency yet, so
        # there's no real per-device cap to report. NPU model weights
        # live in the same GTT region of the unified-memory pool that
        # the iGPU pulls from, so advertise the GTT total (or fall back
        # to the unified-memory total). Once the driver surfaces real
        # NPU residency, swap this for the live number.
        if hw.gpus and hw.gpus[0].vram_mb:
            return 0, hw.gpus[0].vram_mb
        if hw.unified_memory_mb:
            return 0, hw.unified_memory_mb
        return 0, 0
    if backend_id in {"gpu-vulkan", "gpu-rocm"}:
        if hw.gpus and hw.gpus[0].vram_mb:
            return 0, hw.gpus[0].vram_mb
        if hw.unified_memory_mb:
            return 0, hw.unified_memory_mb
        return 0, 0
    if backend_id == "cpu":
        return 0, hw.ram_mb or 0
    return 0, 0


_BACKEND_CANONICAL = {
    "vulkan": "gpu-vulkan",
    "rocm": "gpu-rocm",
    "flm": "npu",
    "kokoro": "gpu-vulkan",
    "moonshine": "gpu-vulkan",
    "cpu": "cpu",
}


async def _loaded_children_for_backend(
    backend_id: str, slot_manager: SlotManagerDep
) -> list[dict[str, Any]]:
    """Return the children currently loaded on this backend.

    Two passes:

      1. Walk every (capability slot, child) pair in ``_CHILD_TO_SLOT``
         and surface the ones whose backend matches. These rows carry
         ``source="capability"`` so the UI knows they're managed via the
         capability picker (no per-row remove control).

      2. Walk every other slot (e.g. ``primary``, ad-hoc NPU loads from
         the backend card) and surface the ones whose backend matches.
         These rows carry ``source="slot"`` — direct loads the operator
         created from the NPU card and can unload from there.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    # Pass 1: capability-mapped slots.
    for (slot, child), slot_name in _CHILD_TO_SLOT.items():
        seen.add(slot_name)
        try:
            snap = await slot_manager.status(slot_name)
        except Exception:
            continue
        if snap.state.value in {"offline", "error", "unloading"}:
            continue
        canonical = _BACKEND_CANONICAL.get(snap.backend or "", snap.backend or "")
        if canonical != backend_id:
            continue
        size_mb = 0
        meta = snap.metadata or {}
        if isinstance(meta.get("size_mb"), int):
            size_mb = int(meta["size_mb"])
        out.append(
            {
                "slot": slot,
                "child": child,
                "slotName": slot_name,
                "modelId": snap.model_id or "",
                "sizeMb": size_mb,
                "source": "capability",
            }
        )

    # Pass 2: any other live slot (direct loads, primary, etc.) whose
    # backend matches. Lets the NPU backend card see ad-hoc FLM slots
    # created via POST /api/slots so its "Loaded on NPU" list stays
    # authoritative regardless of how the slot was created.
    try:
        all_slots = await slot_manager.list()
    except Exception:
        all_slots = []
    for snap in all_slots:
        if snap.name in seen:
            continue
        if snap.state.value in {"offline", "error", "unloading"}:
            continue
        canonical = _BACKEND_CANONICAL.get(snap.backend or "", snap.backend or "")
        if canonical != backend_id:
            continue
        size_mb = 0
        meta = snap.metadata or {}
        if isinstance(meta.get("size_mb"), int):
            size_mb = int(meta["size_mb"])
        out.append(
            {
                "slot": snap.name,
                "child": snap.name,
                "slotName": snap.name,
                "modelId": snap.model_id or "",
                "sizeMb": size_mb,
                "source": "slot",
            }
        )
    return out


def _state_for_backend(backend_id: str) -> str:
    """Return ``ready`` / ``offline`` / ``error`` for the backend card."""
    available_ids = {b["id"] for b in available_backends()}
    if backend_id in available_ids:
        return "ready"
    return "offline"


# ── routes ────────────────────────────────────────────────────────────────────


async def _build_backend_payload(
    backend: dict[str, Any], slot_manager: SlotManagerDep
) -> dict[str, Any]:
    """Render one backend descriptor + live status into the response shape."""
    backend_id = backend["id"]
    mem_used, mem_total = _mem_totals_for_backend(backend_id)
    loaded = await _loaded_children_for_backend(backend_id, slot_manager)
    return {
        "id": backend_id,
        "label": backend.get("label", backend_id),
        "short": backend.get("short", backend_id),
        "provider": backend.get("provider", ""),
        "multiplex": bool(backend.get("multiplex", False)),
        "hardware": _hardware_for_backend(backend_id),
        "driver": _driver_for_backend(backend_id),
        "state": _state_for_backend(backend_id),
        "memUsedMb": mem_used,
        "memTotalMb": mem_total,
        "totalReqPerSec": 0,
        "loaded": loaded,
    }


@router.get("")
async def list_backends(request: Request, slot_manager: SlotManagerDep) -> list[dict[str, Any]]:
    """Return one row per available backend with live status."""
    out: list[dict[str, Any]] = []
    for backend in available_backends():
        out.append(await _build_backend_payload(backend, slot_manager))
    return out


@router.get("/{backend_id}")
async def get_backend_details(
    backend_id: str,
    request: Request,
    slot_manager: SlotManagerDep,
) -> dict[str, Any]:
    """Single-backend variant of :func:`list_backends`."""
    backend = get_backend(backend_id)
    if backend is None:
        raise NotFound(
            f"backend {backend_id!r} not available on this host",
            code="backend.not_found",
            details={"id": backend_id},
        )
    return await _build_backend_payload(backend, slot_manager)


# ── NPU dynamic-slot endpoints ────────────────────────────────────────────────


async def _allocate_npu_port(slot_manager: SlotManagerDep) -> int:
    """Return the lowest free port in [_NPU_PORT_MIN, _NPU_PORT_MAX].

    Walks every existing slot's configured port and picks the first
    unused integer in our range. Raises a typed 409 when the range is
    exhausted so the dashboard can surface a clean error envelope.
    """
    used: set[int] = set()
    try:
        all_slots = await slot_manager.list()
    except Exception:
        all_slots = []
    for snap in all_slots:
        if snap.port:
            used.add(int(snap.port))
    for port in range(_NPU_PORT_MIN, _NPU_PORT_MAX + 1):
        if port not in used:
            return port
    raise _NoFreeSlotPort(
        f"no free NPU slot port in [{_NPU_PORT_MIN},{_NPU_PORT_MAX}]",
        details={"range": [_NPU_PORT_MIN, _NPU_PORT_MAX]},
    )


@router.post("/npu/load", dependencies=_writer)
async def load_npu_model(request: Request, slot_manager: SlotManagerDep) -> dict[str, Any]:
    """Create + load an FLM slot for one model tag.

    Body: ``{"model_id": "lfm2:1.2b"}``. The endpoint is idempotent — a
    second call with the same model_id returns the existing slot's
    snapshot rather than failing.

    On the wire this is the "+ load NPU model" button's backing call.
    Until v1.1 the dashboard staged loads in local UI state only; this
    endpoint promotes that into a real slot lifecycle so the model
    actually serves traffic and ``/api/backends/npu`` reports it as
    loaded.
    """
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

    model_id = body.get("model_id")
    if not isinstance(model_id, str) or not model_id.strip():
        raise BadRequest(
            "'model_id' is required (non-empty string)",
            code="backend.model_id_required",
        )
    model_id = model_id.strip()

    slot_name = _npu_slot_name(model_id)

    # Reuse an existing slot if one already exists for this tag; create
    # otherwise. We treat "slot exists" as "config file present" since
    # SlotManager.status raises for unknown slots.
    from hal0.slots.state import SlotConfigError  # local import: avoid cycles

    existed = True
    try:
        await slot_manager.status(slot_name)
    except SlotConfigError:
        existed = False
    except Exception:
        existed = False

    if not existed:
        port = await _allocate_npu_port(slot_manager)
        cfg = {
            "name": slot_name,
            "port": port,
            "backend": "flm",
            "provider": "flm",
            "enabled": True,
            "model": {"default": model_id},
        }
        await slot_manager.create(slot_name, cfg)

    snap = await slot_manager.load(slot_name, model_id=model_id)
    return {
        "slot": snap.name,
        "state": snap.state.value if hasattr(snap.state, "value") else str(snap.state),
        "port": snap.port,
        "model_id": snap.model_id,
        "backend": snap.backend,
        "provider": (snap.metadata or {}).get("provider", ""),
        "created": not existed,
    }


@router.post("/npu/unload", dependencies=_writer)
async def unload_npu_model(request: Request, slot_manager: SlotManagerDep) -> dict[str, Any]:
    """Unload + delete a dynamically-created NPU slot.

    Body: ``{"slot_name": "npu-lfm2-1-2b"}``. Refuses to touch slots
    outside the ``npu-`` prefix so a misdirected call can't take down
    primary/embed/etc. by accident.
    """
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

    slot_name = body.get("slot_name")
    if not isinstance(slot_name, str) or not slot_name.strip():
        raise BadRequest(
            "'slot_name' is required (non-empty string)",
            code="backend.slot_name_required",
        )
    slot_name = slot_name.strip()
    if not slot_name.startswith(_NPU_SLOT_PREFIX):
        raise BadRequest(
            f"refusing to unload non-NPU slot {slot_name!r}",
            code="backend.not_npu_slot",
            details={"slot": slot_name, "prefix": _NPU_SLOT_PREFIX},
        )

    await slot_manager.unload(slot_name)
    await slot_manager.delete(slot_name)
    # Clear systemd's residual "failed" state for the template instance —
    # the docker-run exit on unload trips systemd's failure detector and
    # leaves the unit listed in `systemctl --failed` even after delete.
    # Best-effort; failures here don't affect the unload result.
    import asyncio as _asyncio
    import contextlib as _contextlib

    with _contextlib.suppress(Exception):
        proc = await _asyncio.create_subprocess_exec(
            "systemctl",
            "reset-failed",
            f"hal0-slot@{slot_name}.service",
            stdout=_asyncio.subprocess.DEVNULL,
            stderr=_asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
    return {"ok": True, "slot": slot_name}


__all__ = ["router"]
