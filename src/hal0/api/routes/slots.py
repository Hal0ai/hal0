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

PR-11 (plan §11 + ADR-0008 §5): list responses are enriched with
config-derived fields (drawer seeds, declared backend) plus live
container state (``container_status`` / ``container_health``), and a
``coresident_group`` ID grouping slots that back the same FLM process
(the NPU trio: ``agent`` + ``stt-npu`` + ``embed-npu``). This is
backward-compatible — every legacy field is preserved; new keys are
purely additive.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import StreamingResponse

from hal0.api._audit import record_action
from hal0.api.middleware.error_codes import BadRequest, Conflict, Hal0Error
from hal0.model_meta import device_to_backend, is_resolvable
from hal0.slot_view import (
    SlotViewAggregator,
    config_enrichment,
    container_enrichment,
    loaded_model_names_from_slots,
    serialize_slot,
    synthesize_upstream_entries,
)
from hal0.slots.manager import Slot, SlotManager

# Auth was removed in ADR-0012. All routes are open on the local network.

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
        # 5xx: internal invariant — the lifespan should always wire this.
        # Not a client validation failure, so it stays at the default 500.
        raise Hal0Error(
            "slot_manager not initialised on app.state",
            details={"hint": "lifespan did not run"},
        )
    return sm


def _slot_to_dict(slot: Slot, request: Request | None = None) -> dict[str, Any]:
    """Serialise a real Slot snapshot into the API shape.

    Request-bound adapter over :func:`hal0.slot_view.serialize_slot`
    (issue #698) — kept here because every per-slot route (create / get /
    config / backend / lifecycle) and ``routes/health.py`` call it with a
    ``Request`` in hand.

    When ``request`` is provided, also includes a ``models`` list pulled
    from the shared model cache. For an FLM slot serving chat + embed +
    asr concurrently, this surfaces all three tags so the dashboard can
    render the slot as multi-model instead of showing only the chat tag.
    """
    model_cache: dict[str, Any] | None = None
    if request is not None:
        model_cache = getattr(request.app.state, "model_cache", {}) or {}
    return serialize_slot(slot, model_cache=model_cache)


async def _config_field_enrichment(request: Request) -> dict[str, dict[str, Any]]:
    """Build per-slot config-derived fields for slot snapshots.

    Request-bound adapter over :func:`hal0.slot_view.config_enrichment`
    (issue #698) — kept here for ``get_slot``'s per-card refresh.
    Never raises — a config read failure returns an empty enrichment so
    the dashboard degrades to the on-disk view rather than 500ing.
    """
    sm = getattr(request.app.state, "slot_manager", None)
    if sm is None:
        return {}
    try:
        configs = await sm.iter_configs()
    except Exception:
        return {}
    return config_enrichment(configs)


async def _container_state_enrichment(request: Request) -> dict[str, dict[str, Any]]:
    """Build per-slot container state for container-backed slots.

    Request-bound adapter over :func:`hal0.slot_view.container_enrichment`
    (issue #698) — kept here for ``get_slot``'s per-card refresh.

    Never raises — probe failures degrade to ``stopped`` / ``False`` rather
    than surfacing as a 500.
    """
    sm = getattr(request.app.state, "slot_manager", None)
    if sm is None:
        return {}
    try:
        configs = await sm.iter_configs()
    except Exception:
        return {}
    return await container_enrichment(
        configs,
        pull_jobs=getattr(request.app.state, "slot_pull_jobs", {}),
    )


async def _loaded_models(request: Request) -> set[str]:
    """Model ids currently served by dispatchable slots.

    The truth source for the synthetic composite slot's ``status``: a
    model is "serving" only when a slot in the dispatchable ready-set
    holds it, not when the catalogue merely lists it. Never raises — a
    slot-list failure yields an empty set so the dashboard degrades to
    "offline" instead of 500ing.

    Adapter over :func:`hal0.slot_view.loaded_model_names_from_slots`
    (issue #698) — kept here for ``routes/health.py``'s composite
    status payload.
    """
    sm = getattr(request.app.state, "slot_manager", None)
    if sm is None:
        return set()
    try:
        slots = await sm.list()
    except Exception:
        return set()
    return loaded_model_names_from_slots(slots)


def _synthesize_slots_from_upstreams(
    request: Request, loaded_models: set[str] | None = None
) -> list[dict[str, Any]]:
    """Build virtual slot entries from configured upstreams.

    Until every upstream has a corresponding local slot, the dashboard
    still needs to show remote-backed inference targets. Each upstream
    surfaces as a read-only slot entry. ``status`` is computed by kind:

      * local composite (``kind="slot"``) — ``serving`` only when one of
        the upstream's advertised models appears in the live loaded
        set (``loaded_models``). The catalogue cache lists every configured
        chat model, so it is NOT a liveness signal; consulting the loaded
        set is what keeps the dashboard from showing evicted models as
        resident. Falls back to the catalogue heuristic only when health
        was unreadable (``loaded_models is None``).
      * remote (``kind="remote"``) — ``serving`` when its model cache is
        populated, since that cache is a live ``/v1/models`` probe of the
        remote. ``offline`` otherwise.

    The slot's ``model`` reflects the most recently dispatched model id
    for this upstream (tracked in ``app.state.last_used_model``); falls
    back to the first non-alias from the catalog before any inference
    has happened.

    Request-bound adapter over
    :func:`hal0.slot_view.synthesize_upstream_entries` (issue #698) —
    kept here for ``get_slot``'s synthetic fall-through and
    ``routes/health.py``'s composite status payload.
    """
    return synthesize_upstream_entries(
        request.app.state.upstreams,
        getattr(request.app.state, "model_cache", {}),
        getattr(request.app.state, "last_used_model", {}),
        loaded_models=loaded_models,
    )


# ── list / create ──────────────────────────────────────────────────────────


@router.get("")
async def list_slots(request: Request) -> list[dict[str, object]]:
    """List configured slots.

    Thin adapter over :meth:`hal0.slot_view.SlotViewAggregator.snapshot`
    (issue #698): the aggregator merges real SlotManager-backed entries
    with synthetic upstream-backed ones (real slots win on name
    collision), enriches each real entry with config-derived fields +
    container probe results, stamps per-slot resident memory, and embeds
    live metrics in the card-expected shape. The route only wires the
    aggregator's dependencies off ``app.state`` and serializes the typed
    :class:`hal0.slot_view.SlotView` rows.
    """
    import functools

    sm = _get_slot_manager(request)
    state = request.app.state
    aggregator = SlotViewAggregator(
        sm,
        registry=getattr(state, "model_registry", None),
        metrics=functools.partial(slot_metrics, request),
        model_cache=getattr(state, "model_cache", {}) or {},
        upstreams=state.upstreams,
        last_used_model=getattr(state, "last_used_model", {}),
        slot_pull_jobs=getattr(state, "slot_pull_jobs", {}),
    )
    return [view.to_dict() for view in await aggregator.snapshot()]


def _next_free_slot_port(start: int = 8081, end: int = 8099) -> int:
    """Return the next free port in the slots range (#275 bug 2).

    Walks ``/etc/hal0/slots/*.toml`` collecting both top-level ``port``
    and nested ``[server] port`` values. Returns the lowest port in
    ``[start, end]`` not already claimed. The 8081-8099 range matches
    PLAN.md §2 ports table.
    """
    import tomllib

    from hal0.config.paths import slots_config_dir

    used: set[int] = set()
    slots_dir = slots_config_dir()
    if slots_dir.is_dir():
        for f in slots_dir.glob("*.toml"):
            try:
                with f.open("rb") as fh:
                    cfg = tomllib.load(fh)
            except (OSError, tomllib.TOMLDecodeError):
                continue
            top = cfg.get("port")
            if isinstance(top, int):
                used.add(top)
            srv = cfg.get("server")
            if isinstance(srv, dict):
                nested = srv.get("port")
                if isinstance(nested, int):
                    used.add(nested)
    for p in range(start, end + 1):
        if p not in used:
            return p
    raise BadRequest(
        f"no free port in {start}-{end} (all slots occupied)",
        code="slot.no_free_port",
    )


def _normalize_create_body(body: dict[str, Any]) -> dict[str, Any]:
    """Normalize a POST /api/slots body to the canonical nested shape.

    Two compat hops (#275 bugs 1 + 2):

    1. Top-level ``model: "name"`` (flat shape) → ``model: {"default":
       "name"}`` (nested [model] table). The serializer reads
       ``cfg.get("model").get("default")`` and the SlotConfig pydantic
       model has a nested ModelConfig — but callers may POST a
       top-level string. The result was ``model_default`` MISSING from
       /api/slots responses for any slot created via POST.
    2. Missing or zero ``port`` → auto-assign via
       :func:`_next_free_slot_port`. Without this, new slots persist
       ``port=0`` and the dashboard card shows ``port=0`` instead of a
       useable port.
    """
    out = dict(body)
    model_val = out.get("model")
    if isinstance(model_val, str):
        out["model"] = {"default": model_val}
    if "port" not in out or not isinstance(out.get("port"), int) or out.get("port") in (0, None):
        out["port"] = _next_free_slot_port()
    return out


@router.post("", status_code=201)
async def create_slot(request: Request) -> dict[str, object]:
    """Create a new slot. Body: SlotConfig schema.

    Writes /etc/hal0/slots/<name>.toml, the systemd drop-in override, the
    env file, and the initial state.json. Does NOT start the slot — the
    caller follows with POST /api/slots/<name>/load when ready.

    Accepts both the flat body shape (top-level ``model: "name"``,
    ``device: "gpu-vulkan"``, no ``port``) and the legacy nested shape
    (``[model] default = "name"``, ``[server] port = 8081``). The body
    is normalized to the nested shape via :func:`_normalize_create_body`
    before persistence so the serializer + persistent TOML loaders see
    one canonical shape.
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

    body = _normalize_create_body(body)
    async with record_action(
        request,
        category="slot",
        action="slot.create",
        target=name,
    ) as _rec:
        snap = await sm.create(name, body)
        _rec.after = {"config": body}
    return _slot_to_dict(snap, request)


# ── metrics / capacity ─────────────────────────────────────────────────────


def _tps_from_events(events: Any, window_s: float = 5.0) -> float:
    """Compute current tokens/sec from a rolling (ts, tokens) deque.

    Rate is ``tokens / (last_event_ts - first_event_ts_in_window)`` rather
    than ``tokens / window_s`` so short bursts read at their real rate
    instead of being smeared across the full lookback. Decays to 0 once
    all events age out.
    """
    import time

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


def _per_slot_local_tps(request: Request, window_s: float = 5.0) -> dict[str, float]:
    """Per-slot/upstream tok/s measured on this process's streaming path.

    Reads the per-name deques populated by v1._instrument_streaming_throughput.
    Empty/missing store returns an empty dict so callers can union without
    a None check.
    """
    store = getattr(request.app.state, "tps_events", None)
    if not store:
        return {}
    return {name: _tps_from_events(events, window_s) for name, events in store.items()}


def _per_slot_ttft(request: Request) -> dict[str, dict[str, float]]:
    """Per-slot TTFT view — latest sample + windowed mean.

    Reads the per-name ttft_events deque populated by
    `v1._instrument_streaming_throughput` and returns a dict of
    ``{slot_name: {"ttft_seconds": latest, "ttft_avg_seconds": mean}}``.
    Slots without any in-window sample are simply absent from the
    result so the UI can render '—' rather than a misleading zero.
    """
    store = getattr(request.app.state, "ttft_events", None)
    if not store:
        return {}
    from hal0.slots.ttft_samples import samples_from_events

    out: dict[str, dict[str, float]] = {}
    for name, events in store.items():
        view = samples_from_events(events)
        cur = view.current_ttft()
        avg = view.avg_ttft()
        if cur is None and avg is None:
            continue
        row: dict[str, float] = {}
        if cur is not None:
            row["ttft_seconds"] = cur
        if avg is not None:
            row["ttft_avg_seconds"] = avg
        out[name] = row
    return out


async def _systemd_show(unit: str, *props: str) -> dict[str, str]:
    """Return ``systemctl show -p <prop>...`` parsed into a dict.

    Empty / missing values are returned as empty strings; the caller
    decides how to interpret. Falls back to an empty dict on any error
    (no systemd, unit missing) so the metrics path can degrade silently
    rather than 500 the dashboard.
    """
    if not props:
        return {}
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl",
            "show",
            unit,
            *(f"--property={p}" for p in props),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
    except (TimeoutError, FileNotFoundError, OSError):
        return {}
    if proc.returncode != 0:
        return {}
    result: dict[str, str] = {}
    for raw in out.decode("utf-8", errors="replace").splitlines():
        if "=" not in raw:
            continue
        k, _, v = raw.partition("=")
        result[k.strip()] = v.strip()
    return result


async def _scrape_llama_metrics(port: int) -> dict[str, Any]:
    """Scrape llama.cpp's /metrics + /slots endpoints on loopback.

    /metrics is parsed for ``requests_processing`` / ``requests_deferred``
    (still emitted by current llama-server master). The KV-cache ratio
    gauge upstream used to emit (``llamacpp:kv_cache_usage_ratio``) was
    removed in the post-refactor server, so we synthesise it from
    /slots: ``max(n_prompt_tokens) / n_ctx`` across the slot's parallel
    sub-slots. This matches what the gauge used to represent — the
    fullest cache slot — and is provider-agnostic (any llama-server
    with a busy parallel slot reports n_prompt_tokens).

    Returns an empty dict on any failure (slot not running, port
    unbound, llama-server built without ``--metrics``, parse error) so
    callers can merge unconditionally.
    """
    if port <= 0:
        return {}
    import httpx

    metrics_url = f"http://127.0.0.1:{port}/metrics"
    slots_url = f"http://127.0.0.1:{port}/slots"
    timeout = httpx.Timeout(0.5)
    out: dict[str, Any] = {}

    # Fan the two scrapes out in parallel; either may 404 (older builds,
    # --no-slots, --no-metrics) and we degrade silently per-endpoint.
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            metrics_resp, slots_resp = await asyncio.gather(
                client.get(metrics_url),
                client.get(slots_url),
                return_exceptions=True,
            )
        except (httpx.HTTPError, OSError):
            return out

    # --- /metrics: still the source of truth for queue depth gauges. ---
    #
    # We intentionally DO NOT scrape `llamacpp:predicted_tokens_seconds`
    # here. That gauge is the lifetime average since llama-server start,
    # not the current rate — surfacing it as tokens_per_sec made the
    # SlotCard's T/S indicator stick at a non-zero average forever.
    # Live tok/s is computed from the dispatcher's rolling window in
    # `_per_slot_local_tps`, which correctly decays to 0 at idle.
    wanted: dict[str, tuple[str, type]] = {
        "llamacpp:requests_processing": ("requests_processing", int),
        "llamacpp:requests_deferred": ("requests_deferred", int),
        # Kept for completeness in case a future llama.cpp reintroduces it;
        # current master (b9279) does not emit this gauge.
        "llamacpp:kv_cache_usage_ratio": ("kv_cache_usage", float),
    }
    # Duck-typed: any object with a status_code + text attr (real httpx
    # Response or a test stub) passes; exceptions returned by gather()
    # fall through to the synthesis branch below.
    if (
        not isinstance(metrics_resp, BaseException)
        and getattr(metrics_resp, "status_code", 0) == 200
    ):
        for line in metrics_resp.text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            entry = wanted.get(parts[0])
            if entry is None:
                continue
            key, caster = entry
            try:
                out[key] = int(float(parts[1])) if caster is int else float(parts[1])
            except (ValueError, TypeError):
                continue

    # --- /slots: KV-cache % via max(n_prompt_tokens)/n_ctx. -------------
    #
    # Newer llama-server (post-server.cpp refactor, b9000-ish onward)
    # exposes ``n_prompt_tokens`` per parallel sub-slot when busy, plus
    # ``n_ctx`` always. Older builds only return id/n_ctx/is_processing,
    # in which case the max is 0 and we skip the synthesised gauge so
    # the UI renders '—' rather than a misleading 0%.
    if (
        "kv_cache_usage" not in out
        and not isinstance(slots_resp, BaseException)
        and getattr(slots_resp, "status_code", 0) == 200
    ):
        try:
            payload = slots_resp.json()
        except (ValueError, TypeError):
            payload = None
        if isinstance(payload, list) and payload:
            max_used = 0
            n_ctx = 0
            for slot in payload:
                if not isinstance(slot, dict):
                    continue
                try:
                    ctx = int(slot.get("n_ctx", 0) or 0)
                except (ValueError, TypeError):
                    ctx = 0
                if ctx > n_ctx:
                    n_ctx = ctx
                # Prefer n_prompt_tokens (current prompt+cache occupancy)
                # if it's there; cache_tokens / n_past are legacy fallbacks
                # used by even-older builds.
                used = 0
                for key in ("n_prompt_tokens", "cache_tokens", "n_past"):
                    v = slot.get(key)
                    if v is None:
                        continue
                    try:
                        iv = int(v)
                    except (ValueError, TypeError):
                        continue
                    if iv > used:
                        used = iv
                if used > max_used:
                    max_used = used
            if n_ctx > 0 and max_used > 0:
                ratio = max_used / float(n_ctx)
                # Clamp — n_prompt_tokens can briefly exceed n_ctx during
                # shift; surfacing >1.0 would look broken in the UI.
                out["kv_cache_usage"] = min(max(ratio, 0.0), 1.0)
    return out


async def _docker_container_mem_bytes(container_name: str) -> int:
    """Cgroup-wide memory.current for a named docker container.

    Walks: ``docker inspect`` → container init pid → ``/proc/<pid>/cgroup``
    (cgroupv2 unified line) → ``/sys/fs/cgroup<path>/memory.current``.
    Returns 0 on any error so the caller can fall back to the systemd
    unit's MemoryCurrent (which under docker only covers the ``docker
    run`` client process, not the workload).
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "inspect",
            "-f",
            "{{.State.Pid}}",
            container_name,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=1.5)
    except (TimeoutError, FileNotFoundError, OSError):
        return 0
    if proc.returncode != 0:
        return 0
    try:
        pid = int(out.decode("utf-8", errors="replace").strip() or 0)
    except ValueError:
        pid = 0
    if pid <= 0:
        return 0
    try:
        with open(f"/proc/{pid}/cgroup", encoding="utf-8") as f:
            cg_line = f.readline().strip()
    except OSError:
        return 0
    # cgroupv2 unified: "0::/system.slice/docker-<id>.scope"
    if "::" not in cg_line:
        return 0
    cg_rel = cg_line.split("::", 1)[1].lstrip("/")
    try:
        with open(f"/sys/fs/cgroup/{cg_rel}/memory.current", encoding="utf-8") as f:
            return int(f.read().strip() or 0)
    except (OSError, ValueError):
        return 0


async def _local_slot_metrics(request: Request) -> dict[str, dict[str, Any]]:
    """Build per-slot live metrics from cgroup + systemd activation time.

    MEM: docker slots run their workload in dockerd-managed cgroups
    that the systemd unit doesn't own (the unit's MainPID is the docker
    CLI itself, ~10 MB). We resolve the container by the predictable
    name ``hal0-slot-<slot>``, walk to its cgroup, and read
    memory.current. For non-docker slots we fall back to the unit's
    own MemoryCurrent.

    UP: ``ActiveEnterTimestampMonotonic`` is on the host's
    CLOCK_MONOTONIC; lxcfs rewrites /proc/uptime to a container-local
    view, so we read CLOCK_MONOTONIC via clock_gettime directly to keep
    the deltas non-negative inside an LXC.
    """
    sm = getattr(request.app.state, "slot_manager", None)
    if sm is None:
        return {}
    try:
        slots = await sm.list()
    except Exception:
        return {}

    import time

    monotonic_now_us = int(time.clock_gettime(time.CLOCK_MONOTONIC) * 1_000_000)

    async def _one(slot: Slot) -> tuple[str, dict[str, Any]]:
        scrape_port = slot.port
        unit = f"hal0-slot@{slot.name}.service"
        # Fan systemd properties + docker cgroup + llama metrics out in
        # parallel — three independent IO waits, no point serialising.
        props_task = asyncio.create_task(
            _systemd_show(
                unit,
                "MemoryCurrent",
                "ActiveEnterTimestampMonotonic",
                "ActiveState",
            )
        )
        mem_task = asyncio.create_task(_docker_container_mem_bytes(f"hal0-slot-{slot.name}"))
        metrics_task = asyncio.create_task(_scrape_llama_metrics(scrape_port))
        props, mem_bytes, llm_metrics = await asyncio.gather(
            props_task, mem_task, metrics_task, return_exceptions=False
        )

        out: dict[str, Any] = {
            "name": slot.name,
            "mem_rss_mb": 0.0,
            "uptime_seconds": 0,
            "requests_processing": 0,
        }
        # Prefer docker container cgroup (the workload); fall back to
        # the systemd unit cgroup for native-host slots.
        if mem_bytes <= 0:
            try:
                mem_bytes = int(props.get("MemoryCurrent", "") or 0)
            except (TypeError, ValueError):
                mem_bytes = 0
        if mem_bytes > 0:
            out["mem_rss_mb"] = mem_bytes / (1024.0 * 1024.0)
        try:
            active_us = int(props.get("ActiveEnterTimestampMonotonic", "0") or 0)
        except ValueError:
            active_us = 0
        if active_us > 0 and monotonic_now_us > active_us:
            out["uptime_seconds"] = int((monotonic_now_us - active_us) / 1_000_000)
        # Layer in live request counts + kv-cache + tok/s scraped from
        # llama-server's /metrics. Non-llama backends (NPU FLM, kokoro,
        # etc.) return an empty dict and we leave requests_processing
        # at its 0 default.
        if llm_metrics:
            out["requests_processing"] = int(llm_metrics.get("requests_processing", 0))
            if "requests_deferred" in llm_metrics:
                out["requests_deferred"] = int(llm_metrics["requests_deferred"])
            if "kv_cache_usage" in llm_metrics:
                out["kv_cache_usage"] = float(llm_metrics["kv_cache_usage"])
        return slot.name, out

    pairs = await asyncio.gather(*(_one(s) for s in slots), return_exceptions=True)
    result: dict[str, dict[str, Any]] = {}
    for item in pairs:
        if isinstance(item, BaseException):
            continue
        name, payload = item
        result[name] = payload
    return result


@router.get("/metrics")
async def slot_metrics(request: Request) -> dict[str, Any]:
    """Per-slot runtime metrics keyed by slot name.

    Drives the dashboard's per-slot tok/s row + sparkline. Three layers:

    1. Remote upstreams' /api/slots/metrics (for haloai-style fanouts).
    2. Local per-slot tok/s measured on the dispatcher's streaming path.
    3. Local per-slot MEM/UP scraped from systemd + /proc for the
       hal0-slot@<name>.service template instance.

    Layer 2 wins over layer 1 on tok/s when locally higher (the local
    rolling window reflects the request that's happening *right now*);
    layer 3 fills MEM/UP for any slot that didn't get values from
    layer 1, which is the single-host LXC case where there are no
    upstreams to proxy.
    """
    from hal0.api.routes.hardware import stats_slots

    merged = await stats_slots(request)
    for name, tps in _per_slot_local_tps(request).items():
        if tps <= 0:
            continue
        entry = merged.get(name)
        if not isinstance(entry, dict):
            entry = {"name": name}
            merged[name] = entry
        existing = entry.get("tokens_per_sec") or entry.get("tps") or 0
        if tps > existing:
            entry["tokens_per_sec"] = tps
    for name, local in (await _local_slot_metrics(request)).items():
        entry = merged.get(name)
        if not isinstance(entry, dict):
            entry = dict(local)
            merged[name] = entry
            continue
        # Only fill fields the upstream didn't already report. Truthy-only
        # to avoid overwriting a real 0 with another real 0; for these
        # three fields a 0 from systemd means "no data", so this is safe.
        for key in ("mem_rss_mb", "uptime_seconds", "requests_processing"):
            if not entry.get(key):
                entry[key] = local.get(key, 0)
        # KV-cache is a gauge — present only on llama-backed slots,
        # which the remote upstream may not know about. Always prefer
        # the local scrape when we have one.
        if "kv_cache_usage" in local:
            entry["kv_cache_usage"] = local["kv_cache_usage"]
        if local.get("ctx"):
            entry["ctx"] = local["ctx"]
    # TTFT samples are captured on the dispatcher's streaming wrapper
    # and only exist locally — fold them in last so they win.
    for name, ttft in _per_slot_ttft(request).items():
        entry = merged.get(name)
        if not isinstance(entry, dict):
            entry = {"name": name}
            merged[name] = entry
        entry.update(ttft)
    return merged


@router.get("/capacity")
async def slot_capacity(request: Request) -> dict[str, object]:
    """Per-slot resident memory for the dashboard memory map.

    Returns ``{"per_slot": {slot_name: {vram_mb, ram_mb, mem_mb, state,
    model_id}}}`` for slots in a resident state. Mirrors the ``per_slot``
    block also stamped onto ``GET /api/stats/hardware``.
    """
    from hal0.slots.capacity import build_per_slot

    sm = _get_slot_manager(request)
    slots = await sm.list()
    registry = getattr(request.app.state, "model_registry", None)
    return {"per_slot": await build_per_slot(slots, registry=registry)}


# ── per-slot ───────────────────────────────────────────────────────────────


@router.get("/{name}")
async def get_slot(name: str, request: Request) -> dict[str, object]:
    """Return a snapshot of a single slot.

    Real slots come from the SlotManager; if the name isn't a configured
    local slot, fall through to the synthetic upstream-backed entry.
    SlotNotFound surfaces as the typed slot.not_found envelope.

    PR-11: real-slot snapshots are enriched with config-derived fields
    so the dashboard's per-card refresh stays consistent with the list
    endpoint.
    """
    sm = _get_slot_manager(request)
    try:
        snap = await sm.status(name)
        out = _slot_to_dict(snap, request)
        enrichment = await _config_field_enrichment(request)
        extra = enrichment.get(name)
        if extra:
            for k, v in extra.items():
                out.setdefault(k, v)
        c_enrichment = await _container_state_enrichment(request)
        c_extra = c_enrichment.get(name)
        if c_extra:
            for k, v in c_extra.items():
                out.setdefault(k, v)
        return out
    except Exception:
        # Fall through to synthetic lookup before re-raising — a remote
        # upstream named ``haloai`` should be observable via this endpoint
        # even though it isn't a real slot.
        for entry in _synthesize_slots_from_upstreams(request):
            if entry["name"] == name:
                return entry
        raise


def _state_value(snap: Any) -> str | None:
    """Extract a serialisable state string from a slot snapshot for audit."""
    state = getattr(snap, "state", None)
    return getattr(state, "value", None) or (str(state) if state is not None else None)


async def _safe_config(sm: Any, name: str) -> dict[str, Any] | None:
    """Best-effort current config snapshot for an audit before-image."""
    try:
        cfg = await sm.get_config(name)
        return cfg if isinstance(cfg, dict) else None
    except Exception:
        return None


@router.delete("/{name}")
async def delete_slot(name: str, request: Request) -> dict[str, object]:
    """Delete a slot. If the slot is running, it is stopped first.

    Built-in slots (primary/embed/stt/tts) cannot be deleted — the
    SlotManager raises a typed error which the envelope middleware
    surfaces as 4xx.
    """
    sm = _get_slot_manager(request)
    async with record_action(request, category="slot", action="slot.delete", target=name):
        await sm.delete(name)
    return {"name": name, "deleted": True}


@router.get("/{name}/config")
async def get_slot_config(name: str, request: Request) -> dict[str, object]:
    """Return the slot's TOML config as a dict."""
    sm = _get_slot_manager(request)
    cfg = await sm.get_config(name)
    return cfg


@router.put("/{name}/config")
async def update_slot_config(name: str, request: Request) -> dict[str, object]:
    """Update a slot's config. Body: partial SlotConfig (shallow merge)."""
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
    before = await _safe_config(sm, name)
    async with record_action(
        request, category="slot", action="slot.edit_config", target=name, before=before
    ) as _rec:
        snap = await sm.update_config(name, body)
        _rec.after = body
    # Spec 1 / Component 2: an explicit ``enabled: false`` write must take a
    # running slot actually offline so the faded card matches reality. The
    # config write alone only flips the on-disk flag; without this a disabled
    # slot would keep its llama-server child resident until the next restart.
    # ``unload`` is idempotent (short-circuits when already OFFLINE), but we
    # gate on a live state so an offline/error slot incurs no /v1/unload call.
    if body.get("enabled") is False:
        from hal0.slots.state import SlotState

        _LIVE = {
            SlotState.STARTING,
            SlotState.WARMING,
            SlotState.READY,
            SlotState.SERVING,
            SlotState.IDLE,
        }
        if snap.state in _LIVE:
            snap = await sm.unload(name)
    return _slot_to_dict(snap, request)


@router.patch("/{name}/defaults")
async def update_slot_defaults(name: str, request: Request) -> dict[str, object]:
    """Update slot defaults (ctx_size, temperature, etc.).

    Convenience wrapper over update_config — body keys merge into the
    slot's [model] sub-table rather than the top level.
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
    before = await _safe_config(sm, name)
    async with record_action(
        request, category="slot", action="slot.edit_defaults", target=name, before=before
    ) as _rec:
        snap = await sm.update_config(name, {"model": body})
        _rec.after = {"model": body}
    return _slot_to_dict(snap, request)


# Map a normalized runtime-backend token to the SlotConfig ``device`` enum
# the TOML persists. ``auto`` clears the device so the load path falls back
# to its default. flm/npu are not selectable through this control (they
# require a profile switch, not a device flip).
_BACKEND_TO_DEVICE: dict[str, str | None] = {
    "rocm": "gpu-rocm",
    "vulkan": "gpu-vulkan",
    "cpu": "cpu",
    "auto": None,
}

# Container images supply the backend builds; a missing image surfaces as
# ``image_status="missing"`` on the slot card and pulls on demand, so no
# on-disk build-presence validation is needed here.
_BACKEND_BUILD_BIN: dict[str, str] = {}


def _backend_build_present(backend: str) -> bool:
    """Backend builds ship inside container images — always present.

    Kept as a seam (module-level, monkeypatchable) for tests and for any
    future host-side build requirement.
    """
    del backend
    return True


def _normalize_backend_token(raw: str) -> str:
    """Normalize a backend/device request token to rocm|vulkan|cpu|auto|flm|npu.

    Accepts the gpu- device forms and folds them onto the backend token so
    the endpoint accepts both ``{"backend":"vulkan"}`` and
    ``{"device":"gpu-vulkan"}``.
    """
    t = raw.strip().lower()
    if t == "gpu-rocm":
        return "rocm"
    if t == "gpu-vulkan":
        return "vulkan"
    return t


@router.post("/{name}/backend")
async def set_slot_backend(name: str, request: Request) -> dict[str, object]:
    """Switch a slot's runtime backend (ADR-0022 control endpoint).

    Body: ``{"backend": "rocm"|"vulkan"|"cpu"|"auto"}``. The alias key
    ``device`` is also accepted and ``gpu-rocm``/``gpu-vulkan`` normalize to
    ``rocm``/``vulkan``.

    Effect: writes the slot's ``device`` field to TOML via
    ``update_config`` (which auto-refreshes the mirrored ``extra.backend``);
    if the slot is currently loaded it is restarted so the model reloads
    under the new backend. Idempotent — when the requested backend already
    equals the declared device (and, when loaded, the actual backend) it is
    a no-op with ``reloaded: false``.

    Validation:
      - ``rocm``/``vulkan`` → 409 ``backend.build_missing`` when the build's
        ``llama-server`` binary is absent.
      - ``cpu`` and ``auto`` are always valid.
      - ``flm``/``npu`` → 400 ``backend.not_selectable``.

    Response 200: the standard ``_slot_to_dict`` payload plus
    ``requested_backend`` / ``declared_backend`` / ``actual_backend`` /
    ``reloaded``.
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
    # Accept either ``backend`` or the alias ``device``.
    raw = body.get("backend")
    if not isinstance(raw, str) or not raw.strip():
        raw = body.get("device")
    if not isinstance(raw, str) or not raw.strip():
        raise BadRequest(
            "'backend' (or 'device') is required in request body",
            code="backend.missing",
        )

    backend = _normalize_backend_token(raw)

    # flm/npu are not selectable via this control — they need a recipe
    # switch, not a llamacpp_backend flip.
    if backend in ("flm", "npu"):
        raise BadRequest(
            f"backend {backend!r} is not selectable via this endpoint "
            "(NPU/FLM requires a recipe change, not a backend flip)",
            code="backend.not_selectable",
        )
    if backend not in _BACKEND_TO_DEVICE:
        raise BadRequest(
            f"backend {backend!r} is not recognised; choose from rocm|vulkan|cpu|auto",
            code="backend.not_selectable",
        )

    # Build-presence validation for the GPU backends.
    if not _backend_build_present(backend):
        bin_path = _BACKEND_BUILD_BIN.get(backend)
        raise Conflict(
            f"backend {backend!r} build is not installed ({bin_path} missing)",
            details={"backend": backend, "expected_binary": bin_path},
            code="backend.build_missing",
        )

    target_device = _BACKEND_TO_DEVICE[backend]

    # Determine current declared device + whether the slot is loaded, so we
    # can short-circuit an idempotent no-op and decide whether to restart.
    cfg = await sm.get_config(name)
    current_device = (cfg.get("device") if isinstance(cfg, dict) else None) or ""
    # Normalized declared backend for the CURRENT device (for the response +
    # the idempotency comparison).
    _recipe, _llamacpp = device_to_backend(current_device)
    current_declared = _llamacpp or (_recipe if _recipe == "flm" else None)

    # Is the slot currently loaded? Container truth is the slot's
    # lifecycle state; per-process backend introspection retired with the
    # legacy runtime (#663: the running image is the backend-of-record,
    # surfaced as ``actual_image`` on the slot card).
    try:
        is_loaded = bool(sm.is_ready_for_dispatch(name))
    except Exception:
        is_loaded = False
    actual_backend = None

    # Idempotency: the requested backend already equals the declared device,
    # AND (when loaded) the actual backend already matches → no-op.
    requested_declared = device_to_backend(target_device)[1] if target_device else None
    already_declared = current_device == (target_device or "")
    already_actual = (
        (not is_loaded) or (actual_backend is None) or (actual_backend == requested_declared)
    )
    if already_declared and already_actual:
        snap = await sm.status(name)
        out = _slot_to_dict(snap, request)
        out["requested_backend"] = backend
        out["declared_backend"] = current_declared
        out["actual_backend"] = actual_backend if actual_backend else None
        out["reloaded"] = False
        return out

    # Persist the new device. ``auto`` clears the device field entirely so
    # the load path falls back to its default on the next load.
    async with record_action(
        request,
        category="slot",
        action="slot.set_backend",
        target=name,
        before={"device": current_declared},
        message=f"backend → {backend}",
    ) as _rec:
        await sm.update_config(name, {"device": target_device or ""})

        reloaded = False
        if is_loaded:
            # Restart so the model reloads under the new backend (the
            # container unit re-renders from the updated TOML).
            await sm.restart(name)
            reloaded = True
        _rec.after = {"backend": backend, "device": target_device}

    snap = await sm.status(name)
    out = _slot_to_dict(snap, request)
    # Recompute declared/actual from the post-change state.
    new_cfg = await sm.get_config(name)
    new_device = (new_cfg.get("device") if isinstance(new_cfg, dict) else None) or ""
    _nrecipe, _nllamacpp = device_to_backend(new_device)
    new_declared = _nllamacpp or (_nrecipe if _nrecipe == "flm" else None)
    new_actual = None
    out["requested_backend"] = backend
    out["declared_backend"] = new_declared
    out["actual_backend"] = new_actual if new_actual else None
    out["reloaded"] = reloaded
    return out


# ── lifecycle ──────────────────────────────────────────────────────────────


@router.post("/{name}/load")
async def load_slot(name: str, request: Request) -> dict[str, object]:
    """Load a model into a slot. Optional body: {"model_id": "..."}.

    Validates ``model_id`` against the registry up-front when supplied
    — a bad id otherwise tunnels into ``SlotManager.load``, which
    happily spawns a container that never goes healthy, leaving the
    operator to wait out the 180s health timeout. Empty / None model_id
    is fine: that path falls back to the slot's TOML default.
    """
    sm = _get_slot_manager(request)
    body: dict[str, Any] = {}
    try:
        body = await request.json()
    except Exception:
        # POST without a body is fine — fall back to the slot's default model.
        body = {}
    model_id = body.get("model_id") if isinstance(body, dict) else None
    # Some callers post {"model": "..."} for symmetry with the slot
    # config schema. Accept both so a dashboard typo doesn't 422.
    if not model_id and isinstance(body, dict):
        model_id = body.get("model")
    if model_id:
        registry = getattr(request.app.state, "model_registry", None)
        if registry is not None and not is_resolvable(model_id, registry):
            from hal0.registry.store import ModelNotFound

            raise ModelNotFound(
                f"model {model_id!r} is not resolvable — not in the registry and "
                f"not an installed FLM model (slot {name!r} not touched)",
                details={"model_id": model_id, "slot": name},
            )
    async with record_action(
        request,
        category="slot",
        action="slot.load",
        target=name,
        message=f"load {model_id or 'default'}",
    ) as _rec:
        snap = await sm.load(name, model_id=model_id)
        _rec.after = {"model_id": model_id, "state": _state_value(snap)}
    return _slot_to_dict(snap, request)


@router.post("/{name}/unload")
async def unload_slot(name: str, request: Request) -> dict[str, object]:
    sm = _get_slot_manager(request)
    async with record_action(request, category="slot", action="slot.unload", target=name) as _rec:
        snap = await sm.unload(name)
        _rec.after = {"state": _state_value(snap)}
    return _slot_to_dict(snap, request)


@router.post("/{name}/restart")
async def restart_slot(name: str, request: Request) -> dict[str, object]:
    sm = _get_slot_manager(request)
    async with record_action(request, category="slot", action="slot.restart", target=name) as _rec:
        snap = await sm.restart(name)
        _rec.after = {"state": _state_value(snap)}
    return _slot_to_dict(snap, request)


@router.post("/{name}/swap")
async def swap_slot(name: str, request: Request) -> dict[str, object]:
    """Hot-swap a slot's model. Body: {"model_id": "..."}.

    The swap path is destructive — it unloads the live slot before
    attempting to load the new model — so we validate ``model_id``
    against the registry up-front. A bad id without this check used to
    leave the slot in ERROR after a 180s health timeout (the container
    started but had no resolvable model file); now it 404s in <10ms
    with the slot untouched.
    """
    sm = _get_slot_manager(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    model_id = body.get("model_id") if isinstance(body, dict) else None
    if not model_id:
        raise BadRequest(
            "swap requires a non-empty model_id in the request body",
            details={"slot": name},
            code="swap.missing_model",
        )
    registry = getattr(request.app.state, "model_registry", None)
    if registry is not None and not is_resolvable(model_id, registry):
        from hal0.registry.store import ModelNotFound

        raise ModelNotFound(
            f"model {model_id!r} is not resolvable — not in the registry and "
            f"not an installed FLM model (slot {name!r} not touched)",
            details={"model_id": model_id, "slot": name},
        )
    async with record_action(
        request,
        category="slot",
        action="slot.swap",
        target=name,
        message=f"swap → {model_id}",
    ) as _rec:
        snap = await sm.swap(name, model_id)
        _rec.after = {"model_id": model_id, "state": _state_value(snap)}
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
            # B13: use a custom 'degraded' event name, NOT the reserved SSE
            # 'error' name — EventSource's onmessage/onerror never fire for a
            # named 'error' frame, so the log drawer used to spin forever on
            # "waiting for log lines…". The client listens for 'degraded'.
            yield 'event: degraded\ndata: {"message":"journalctl unavailable"}\n\n'
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


# ── container image pull ───────────────────────────────────────────────────────


class _ImagePullJob:
    """Lightweight job object for a container-image pull.

    Tracks state (pulling | completed | failed), layer progress, and an
    asyncio.Event used to wake SSE subscribers on each line of output.

    Unlike the HF-model PullJob (byte-oriented), this job is layer-oriented:
    layer = layers finished, total_layers = layers discovered.
    """

    __slots__ = ("error", "image", "layer", "slot_name", "state", "total_layers")

    def __init__(self, slot_name: str, image: str) -> None:
        self.slot_name = slot_name
        self.image = image
        self.state: str = "pulling"
        self.layer: int = 0
        self.total_layers: int = 0
        self.error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "slot_name": self.slot_name,
            "image": self.image,
            "state": self.state,
            "layer": self.layer,
            "total_layers": self.total_layers,
            "error": self.error,
        }


async def _run_image_pull(job: _ImagePullJob, request: Request) -> None:
    """Run the container pull in background, updating ``job`` per line.

    Writes progress into ``job`` so the 0.5-s polling SSE loop picks it up.
    ``request`` is accepted for future use (event bus, slot invalidation)
    but not read currently — marked ARG001 to suppress the linter.
    """
    from hal0.providers.container import container_provider

    cp = container_provider()
    try:
        async for chunk in cp.pull_image_stream(job.image):
            job.state = chunk.get("state", "pulling")
            job.layer = int(chunk.get("layer", job.layer))
            job.total_layers = int(chunk.get("total_layers", job.total_layers))
            if chunk.get("error"):
                job.error = str(chunk["error"])
            if job.state in ("completed", "failed"):
                break
    except Exception as exc:
        job.state = "failed"
        job.error = str(exc)


@router.post("/{name}/pull", status_code=202)
async def pull_slot_image(
    name: str, request: Request, background: BackgroundTasks
) -> dict[str, object]:
    """Start a background container image pull for slot ``name``.

    Idempotent: if a pull is already in-flight for this slot, returns
    the existing job's snapshot rather than starting a second pull.

    Returns a job snapshot::

        {"slot_name": "...", "image": "...", "state": "pulling",
         "layer": 0, "total_layers": 0}

    Clients should open ``GET /api/slots/{name}/pull/stream`` to receive
    live layer-progress events after POSTing here.
    """
    sm = _get_slot_manager(request)
    # Validate slot exists.
    await sm.status(name)

    slot_pull_jobs: dict[str, Any] = getattr(request.app.state, "slot_pull_jobs", {})

    existing = slot_pull_jobs.get(name)
    if existing is not None and existing.state == "pulling":
        return {"resumed": True, **existing.as_dict()}

    # Resolve image from profile.
    image: str | None = None
    try:
        configs = await sm.iter_configs()
        for cfg in configs:
            if str(cfg.get("name", "")) == name:
                profile_name = str(cfg.get("profile") or "")
                if profile_name:
                    from hal0.config.loader import load_profiles_config

                    catalog = load_profiles_config()
                    prof = catalog.profile.get(profile_name)
                    if prof:
                        image = prof.image
                break
    except Exception:
        pass

    if not image:
        raise BadRequest(
            f"slot {name!r} has no container profile / image — cannot pull",
            details={"slot": name},
        )

    job = _ImagePullJob(name, image)
    if not hasattr(request.app.state, "slot_pull_jobs"):
        request.app.state.slot_pull_jobs = {}
    request.app.state.slot_pull_jobs[name] = job
    background.add_task(_run_image_pull, job, request)
    return {"resumed": False, **job.as_dict()}


@router.get("/{name}/pull/stream")
async def pull_slot_image_stream(name: str, request: Request) -> StreamingResponse:
    """SSE stream of container image-pull layer progress for slot ``name``.

    Emits one frame immediately (snapshot or terminal-already state),
    then one per layer line, and a final terminal frame on completion or
    failure. Graceful when no pull is active: emits a ``present`` or
    ``missing`` frame and closes.

    Frame shape::

        data: {"slot_name": "...", "image": "...", "state": "pulling",
               "layer": N, "total_layers": M}

    Terminal states: ``completed`` | ``failed`` | ``present`` | ``missing``.
    """

    async def _gen() -> Any:
        slot_pull_jobs: dict[str, Any] = getattr(request.app.state, "slot_pull_jobs", {})
        job = slot_pull_jobs.get(name)

        if job is None:
            # No active pull — inspect the image to surface present|missing.
            image: str | None = None
            try:
                sm = _get_slot_manager(request)
                configs = await sm.iter_configs()
                for cfg in configs:
                    if str(cfg.get("name", "")) == name:
                        profile_name = str(cfg.get("profile") or "")
                        if profile_name:
                            from hal0.config.loader import load_profiles_config

                            catalog = load_profiles_config()
                            prof = catalog.profile.get(profile_name)
                            if prof:
                                image = prof.image
                        break
            except Exception:
                pass

            if image:
                try:
                    from hal0.providers.container import container_provider

                    present = await asyncio.get_event_loop().run_in_executor(
                        None, container_provider().image_present, image
                    )
                    state = "present" if present else "missing"
                except Exception:
                    state = "missing"
            else:
                state = "missing"

            yield f"data: {json.dumps({'slot_name': name, 'image': image, 'state': state, 'layer': 0, 'total_layers': 0})}\n\n"
            return

        # Emit initial snapshot.
        yield f"data: {json.dumps(job.as_dict())}\n\n"
        last_layer = job.layer
        while job.state == "pulling":
            await asyncio.sleep(0.5)
            if job.layer != last_layer or job.state != "pulling":
                last_layer = job.layer
                yield f"data: {json.dumps(job.as_dict())}\n\n"
        # Terminal frame.
        yield f"data: {json.dumps(job.as_dict())}\n\n"

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{name}/pull/status")
async def pull_slot_image_status(name: str, request: Request) -> dict[str, object]:
    """Poll fallback for container image-pull progress (mirror of #9).

    The SSE stream (``/{name}/pull/stream``) is the live path; this is the
    one-shot poll equivalent for clients that can't hold an EventSource
    open. Returns the in-flight job snapshot when a pull is active,
    otherwise inspects the slot's image and reports ``present`` |
    ``missing`` — the same terminal vocabulary the stream's no-job branch
    emits, so a poller and a streamer converge on the same states.

    Frame shape::

        {"slot_name": "...", "image": "...", "state": "...",
         "layer": N, "total_layers": M, "error": null}

    States: ``pulling`` | ``completed`` | ``failed`` | ``present`` |
    ``missing``.
    """
    slot_pull_jobs: dict[str, Any] = getattr(request.app.state, "slot_pull_jobs", {})
    job = slot_pull_jobs.get(name)
    if job is not None:
        return job.as_dict()

    # No active pull — resolve the slot's image + inspect presence so the
    # poller gets the same present|missing terminal the SSE stream emits.
    image: str | None = None
    try:
        sm = _get_slot_manager(request)
        configs = await sm.iter_configs()
        for cfg in configs:
            if str(cfg.get("name", "")) == name:
                profile_name = str(cfg.get("profile") or "")
                if profile_name:
                    from hal0.config.loader import load_profiles_config

                    catalog = load_profiles_config()
                    prof = catalog.profile.get(profile_name)
                    if prof:
                        image = prof.image
                break
    except Exception:
        pass

    state = "missing"
    if image:
        try:
            from hal0.providers.container import container_provider

            present = await asyncio.get_event_loop().run_in_executor(
                None, container_provider().image_present, image
            )
            state = "present" if present else "missing"
        except Exception:
            state = "missing"

    return {
        "slot_name": name,
        "image": image,
        "state": state,
        "layer": 0,
        "total_layers": 0,
        "error": None,
    }
