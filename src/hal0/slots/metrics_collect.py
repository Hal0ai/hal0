"""Per-slot live-metrics collection adapters (extracted from routes/slots.py).

These are the route layer's heaviest non-policy code: three IO adapters that
shell out or scrape (``systemctl show``, cgroup-v2 ``memory.current`` via
``docker inspect`` → ``/proc/<pid>/cgroup``, and llama-server's
``/metrics``+``/slots`` HTTP endpoints), the per-slot fan-out that merges them,
and the two rolling-window views (tok/s + TTFT) computed off the dispatcher's
in-process event deques.

They moved here (P3-routers §J) so ``routes/slots.py::slot_metrics`` is a thin
request→service→envelope shell. Every function is fail-soft — any probe error
degrades to an empty/zero value rather than 500-ing the dashboard.

Interface contract:

    systemd_props(unit, *props) -> dict[str, str]
        ``systemctl show -p <prop>...`` parsed to a dict; {} on any error.
    container_mem_bytes(container_name) -> int
        cgroup-v2 ``memory.current`` for a docker container; 0 on any error.
    llama_metrics(port) -> dict[str, Any]
        Scrape llama.cpp ``/metrics``+``/slots`` on loopback; {} on failure.
    collect_local(sm) -> dict[str, dict[str, Any]]
        Per-slot mem/uptime/request-count fan-out over ``sm.list()``.
        ``sm=None`` (or a listing error) → {}.
    tps_from_events(events, window_s=5.0) -> float
        Rolling tok/s from a (ts, tokens) deque.
    local_tps(app_state, window_s=5.0) -> dict[str, float]
        Per-name tok/s from ``app_state.tps_events``.
    local_ttft(app_state) -> dict[str, dict[str, float]]
        Per-name TTFT view (latest + windowed mean) from ``app_state.ttft_events``.

The ``httpx.AsyncClient``/``asyncio.create_subprocess_exec`` calls are done via
module-global ``httpx``/``asyncio`` so tests can monkeypatch them; ``routes/slots``
re-exports the underscore-named originals so existing patch/import sites still
resolve (``hal0.metrics.sampler`` imports ``_scrape_llama_metrics`` from there).
"""

from __future__ import annotations

import asyncio
from typing import Any


def tps_from_events(events: Any, window_s: float = 5.0) -> float:
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


def local_tps(app_state: Any, window_s: float = 5.0) -> dict[str, float]:
    """Per-slot/upstream tok/s measured on this process's streaming path.

    Reads the per-name deques populated by v1._instrument_streaming_throughput.
    Empty/missing store returns an empty dict so callers can union without
    a None check.
    """
    store = getattr(app_state, "tps_events", None)
    if not store:
        return {}
    return {name: tps_from_events(events, window_s) for name, events in store.items()}


def local_ttft(app_state: Any) -> dict[str, dict[str, float]]:
    """Per-slot TTFT view — latest sample + windowed mean.

    Reads the per-name ttft_events deque populated by
    `v1._instrument_streaming_throughput` and returns a dict of
    ``{slot_name: {"ttft_seconds": latest, "ttft_avg_seconds": mean}}``.
    Slots without any in-window sample are simply absent from the
    result so the UI can render '—' rather than a misleading zero.
    """
    store = getattr(app_state, "ttft_events", None)
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


async def systemd_props(unit: str, *props: str) -> dict[str, str]:
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


async def llama_metrics(port: int) -> dict[str, Any]:
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
    # `local_tps`, which correctly decays to 0 at idle.
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


async def container_mem_bytes(container_name: str) -> int:
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


async def collect_local(sm: Any) -> dict[str, dict[str, Any]]:
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

    ``sm`` is the SlotManager (``request.app.state.slot_manager``); ``None``
    or a listing error yields an empty dict.
    """
    if sm is None:
        return {}
    try:
        slots = await sm.list()
    except Exception:
        return {}

    import time

    monotonic_now_us = int(time.clock_gettime(time.CLOCK_MONOTONIC) * 1_000_000)

    async def _one(slot: Any) -> tuple[str, dict[str, Any]]:
        scrape_port = slot.port
        unit = f"hal0-slot@{slot.name}.service"
        # Fan systemd properties + docker cgroup + llama metrics out in
        # parallel — three independent IO waits, no point serialising.
        props_task = asyncio.create_task(
            systemd_props(
                unit,
                "MemoryCurrent",
                "ActiveEnterTimestampMonotonic",
                "ActiveState",
            )
        )
        mem_task = asyncio.create_task(container_mem_bytes(f"hal0-slot-{slot.name}"))
        metrics_task = asyncio.create_task(llama_metrics(scrape_port))
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
