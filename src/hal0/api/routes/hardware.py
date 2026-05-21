"""Hardware probe + stats endpoints (mounted under /api)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from hal0.api.middleware.auth import require_writer
from hal0.config import paths
from hal0.config.loader import load_hardware_info

# See slots.py for the writer-gate rationale.
_writer = [Depends(require_writer)]

router = APIRouter()


def _flatten_for_ui(info: dict[str, Any]) -> dict[str, Any]:
    """Project HardwareInfo into the fields the Vue Hardware view expects.

    The dashboard reads ``gpu_name``, ``vram_total_mb``, ``gtt_total_mb``,
    etc. — flat shapes from haloai's old stats dict.  We keep the full
    pydantic model under ``info`` so future views can opt into the richer
    schema without breaking the current view.
    """
    gpus = info.get("gpus") or []
    primary_gpu = gpus[0] if gpus else {}
    vendor = primary_gpu.get("vendor", "")
    vram_mb = primary_gpu.get("vram_mb", 0)
    ram_mb = info.get("ram_mb", 0)
    unified_mb = info.get("unified_memory_mb", 0) or ram_mb
    # On AMD UMA the probe's GPUInfo.vram_mb is max(vram, gtt) — i.e. the GTT
    # pool. Surface it as gtt_total_mb; expose a separate dedicated_vram_mb
    # only for non-UMA. This stops the dashboard from treating GTT and VRAM
    # as independent buckets.
    is_uma = vendor == "amd" and vram_mb > ram_mb * 0.5
    return {
        **info,
        "gpu_name": primary_gpu.get("name", ""),
        "gpu_vendor": vendor,
        "vram_total_mb": 0 if is_uma else vram_mb,
        "gtt_total_mb": vram_mb if is_uma else 0,
        "ram_total_mb": ram_mb,
        "ram_available_mb": info.get("ram_available_mb", 0),
        "unified_memory_mb": unified_mb,
        "is_uma": is_uma,
        "disk_free_mb": info.get("disk_free_mb", 0),
        "cpu_name": info.get("cpu_model", ""),
        "cpu_cores": info.get("cpu_cores", 0),
        "cpu_threads": info.get("cpu_threads", 0),
        "npu_present": (info.get("npu") or {}).get("present", False),
        "npu_name": (info.get("npu") or {}).get("name", ""),
    }


@router.get("/hardware")
async def get_hardware(request: Request) -> dict[str, Any]:
    """Return cached /etc/hal0/hardware.json, falling back to a fresh probe.

    The probe is heavy enough (subprocess fanout) that we prefer the
    cached snapshot; ``POST /api/hardware/probe`` forces a re-run.
    """
    target = paths.hardware_json()
    if target.exists():
        try:
            info = load_hardware_info().model_dump(mode="python")
            return _flatten_for_ui(info)
        except Exception:
            pass
    # Cache miss → probe now.
    probe = request.app.state.hardware_probe
    info = (await probe.probe_async()).model_dump(mode="python")
    return _flatten_for_ui(info)


@router.post("/hardware/probe", dependencies=_writer)
async def reprobe_hardware(request: Request) -> dict[str, Any]:
    """Re-run the hardware probe and persist to /etc/hal0/hardware.json."""
    probe = request.app.state.hardware_probe
    info = await probe.probe_async()
    probe.write(info)
    return _flatten_for_ui(info.model_dump(mode="python"))


async def _proxy_upstream_endpoint(
    request: Request, suffix: str, timeout_s: float = 3.0
) -> dict[str, dict[str, Any]]:
    """Fan out ``suffix`` (e.g. ``/api/stats/hardware``) to every upstream's
    base host and return ``{upstream_name: payload}``.

    Upstream base URLs end in ``/v1`` by convention; we strip that to hit
    the upstream's internal API surface (haloai exposes its dashboard
    endpoints at the bare ``/api/...`` path on the same host:port).
    Failures are recorded as ``None`` so callers can render "offline" tiles.
    """
    import httpx

    upstreams = request.app.state.upstreams
    out: dict[str, dict[str, Any]] = {}
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        for u in upstreams.list():
            base = u.url.rstrip("/")
            if base.endswith("/v1"):
                base = base[: -len("/v1")]
            try:
                resp = await client.get(base + suffix)
                if resp.status_code == 200:
                    out[u.name] = resp.json()
                else:
                    out[u.name] = None  # type: ignore[assignment]
            except Exception:
                out[u.name] = None  # type: ignore[assignment]
    return out


async def _npu_status(request: Request) -> dict[str, Any] | None:
    """Build the ``npu_status`` block the dashboard's memory bar reads.

    Shape matches haloai's ``lib.hardware._npu_status``: ``{ok, model_mb}``.

      - ``ok``        — XDNA driver is loaded (taken from the cached probe;
                        no subprocess work happens here).
      - ``model_mb``  — sum of the model file sizes for any slot whose
                        provider is FLM. Read from the model registry so
                        we don't shell out per stats poll. Zero when no
                        FLM slot is loaded — the UI hides the segment.

    Returns ``None`` if no NPU is present so the field stays absent and
    the UI's NPU pill / segment collapse cleanly.
    """
    try:
        info = load_hardware_info().model_dump(mode="python")
    except Exception:
        return None
    npu = info.get("npu") or {}
    if not npu.get("present"):
        return None

    model_mb = 0.0
    slot_manager = getattr(request.app.state, "slot_manager", None)
    registry = getattr(request.app.state, "model_registry", None)
    if slot_manager is not None:
        # Only states where the model is actually resident on the NPU.
        # PULLING/STARTING haven't loaded yet; OFFLINE/UNLOADING/ERROR
        # don't hold weights in GTT.
        live_states = {"warming", "ready", "serving", "idle"}
        try:
            slots = await slot_manager.list()
        except Exception:
            slots = []
        # Build the FLM catalog lookup lazily — only when we actually have
        # an FLM slot live. flm_served_models() is cached, so repeated
        # calls are O(1) after the first probe.
        flm_catalog: dict[str, dict[str, Any]] | None = None
        for s in slots:
            state = str(getattr(s, "state", "") or "").lower()
            if state not in live_states:
                continue
            meta = getattr(s, "metadata", None) or {}
            provider = (meta.get("provider") or "").lower()
            backend = str(getattr(s, "backend", None) or meta.get("backend") or "").lower()
            if provider != "flm" and backend not in ("flm", "npu"):
                continue
            mid = getattr(s, "model_id", None)
            if not mid:
                continue
            # FLM tags ("name:tag") live in their own namespace — they are
            # not in the hal0 model registry. Prefer FLM's own footprint
            # estimate (runtime memory, includes KV cache) over file size.
            sz_mb = 0.0
            if flm_catalog is None:
                try:
                    from hal0.providers.flm import flm_served_models

                    flm_catalog = {e["tag"]: e for e in flm_served_models()}
                except Exception:
                    flm_catalog = {}
            flm_entry = flm_catalog.get(mid)
            if flm_entry:
                footprint_gb = flm_entry.get("footprint_gb") or 0.0
                if footprint_gb > 0:
                    sz_mb = footprint_gb * 1024
                else:
                    sz_mb = (flm_entry.get("size_bytes") or 0) / (1024 * 1024)
            elif registry is not None:
                # Non-FLM-tag model id (rare for npu slots, but possible
                # if someone wires a llamacpp-shaped GGUF through FLM).
                try:
                    m = registry.get(mid)
                    sz_mb = (getattr(m, "size_bytes", 0) or 0) / (1024 * 1024)
                except Exception:
                    sz_mb = 0.0
            model_mb += sz_mb

    return {"ok": True, "model_mb": round(model_mb, 1)}


async def _local_live_stats(request: Request) -> dict[str, Any]:
    """Read live counters from this process's HardwareStats.

    Maps the snapshot() fields onto the names the dashboard expects:
    ``ram_used_mb``, ``ram_used_gb``, ``gtt_used_mb``, ``vram_used_mb``,
    plus a ``gpu_util`` fraction and an ``npu_status`` block. Returned
    values may be ``None`` when a counter isn't available on this host
    (e.g. no AMD/NVIDIA GPU).
    """

    stats = getattr(request.app.state, "hardware_stats", None)
    if stats is None:
        return {}
    # snapshot() is synchronous but hits subprocess + sysfs — kick to a
    # thread so the API event loop stays responsive.
    snap = {}
    try:
        snap = stats.snapshot()
    except Exception:  # defensive — never let stats errors take out the page
        return {}

    # gpu_vram_used_mb is the *single* GPU memory counter the probe knows;
    # on AMD UMA the probe picks max(vram_used, gtt_used) so it surfaces
    # GTT (the real model bytes). Split it back out by re-reading the GTT
    # vs VRAM totals from the existing detector helpers.
    from hal0.hardware.probe import _amd_drm_device, _read_sysfs_mb

    gtt_used: float | None = None
    vram_used: float | None = None
    drm = _amd_drm_device()
    if drm is not None:
        gtt_used = _read_sysfs_mb(drm / "mem_info_gtt_used")
        vram_used = _read_sysfs_mb(drm / "mem_info_vram_used")

    ram_used_gb = snap.get("ram_used_gb") or 0.0
    out: dict[str, Any] = {
        "ram_used_gb": ram_used_gb,
        "ram_used_mb": int(ram_used_gb * 1024),
        "ram_available_gb": snap.get("ram_available_gb"),
        "gtt_used_mb": gtt_used,
        "vram_used_mb": vram_used,
        "gpu_util": snap.get("gpu_util"),
        "gpu_vram_used_mb": snap.get("gpu_vram_used_mb"),
        "gpu_vram_total_mb": snap.get("gpu_vram_total_mb"),
    }
    npu_status = await _npu_status(request)
    if npu_status is not None:
        out["npu_status"] = npu_status
    return out


@router.get("/stats/hardware")
async def stats_hardware(request: Request) -> dict[str, Any]:
    """Aggregate runtime hardware stats across upstreams + local probe.

    Each remote upstream that exposes ``/api/stats/hardware`` contributes
    its snapshot; the response carries both a flattened "primary" view
    (first non-empty upstream wins, for the legacy single-host dashboard
    code) and a ``per_upstream`` map for multi-host visualisations.

    Always merges in this process's live counters so the dashboard's
    unified-memory bar fills in even when no upstream answers /api/stats/
    hardware (which is the single-LXC, slot-only deployment shape).
    """
    per_upstream = await _proxy_upstream_endpoint(request, "/api/stats/hardware")
    # Pydantic v2 flags repeated object ids as circular even when no real
    # cycle exists — so we shallow-copy the chosen payload before stamping
    # the per_upstream map onto it.
    primary: dict[str, Any] = {}
    for payload in per_upstream.values():
        if payload:
            primary = dict(payload)
            break

    if not primary:
        primary = dict(await get_hardware(request))

    # Live counters from this process — overwrite any zero/missing values
    # in the upstream payload (which often has only the static probe shape).
    local = await _local_live_stats(request)
    for key, val in local.items():
        if val is None:
            continue
        if primary.get(key) in (None, 0, 0.0):
            primary[key] = val

    # Proxmox host status (when /etc/hal0/proxmox.json is configured).
    # The merge above already lets an upstream's ``host`` field win if
    # one was reported; for the common single-LXC deployment we attach
    # this process's own pve probe. ``configured: false`` keeps the
    # dashboard quiet on non-Proxmox installs.
    #
    # The payload here is the *slim* projection (no tenants[]) because
    # the dashboard polls /api/stats/hardware every 2.5 s. The Settings
    # card consumes the full shape via /api/settings/proxmox instead.
    from hal0.hardware import pve

    if "host" not in primary or not isinstance(primary.get("host"), dict):
        full = await pve.pve_status()
        transition = pve.pop_transition(full)
        if transition is not None:
            event_bus = getattr(request.app.state, "events", None)
            if event_bus is not None:
                if transition == "became_broken":
                    await event_bus.emit(
                        "system.proxmox_unreachable",
                        "warn",
                        "system",
                        f"Proxmox host integration unreachable: {full.get('error', 'unknown error')}",
                        data={"error": full.get("error")},
                    )
                else:  # recovered
                    await event_bus.emit(
                        "system.proxmox_recovered",
                        "info",
                        "system",
                        f"Proxmox host integration recovered ({full.get('node', '?')})",
                        data={"node": full.get("node")},
                    )
        primary["host"] = pve.project_slim(full)

    primary["per_upstream"] = per_upstream
    primary["upstream_names"] = list(per_upstream.keys())
    return primary


@router.get("/stats/slots")
async def stats_slots(request: Request) -> dict[str, Any]:
    """Per-slot runtime metrics.  Aggregates ``/api/slots/metrics`` across
    upstreams; merges into a single dict keyed by slot name (last upstream
    wins on collision — fine for the single-host dev case)."""
    per_upstream = await _proxy_upstream_endpoint(request, "/api/slots/metrics")
    merged: dict[str, dict[str, Any]] = {}
    for payload in per_upstream.values():
        if isinstance(payload, dict):
            for name, m in payload.items():
                if isinstance(m, dict):
                    merged[name] = m
    return merged
