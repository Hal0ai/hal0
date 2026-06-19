"""AIE column-allocation probe for the live FLM/NPU container — NPU occupancy.

The NPU-occupancy dashboard card needs the *honest* AIE column allocation
for each FLM/NPU slot: which of the Strix Halo XDNA NPU's 8 columns a
loaded slot actually owns. That allocation is only knowable by exec'ing
``xrt-smi`` *inside* the running FLM container (the host does not see the
AIE partition table). The probe is therefore an expensive ``podman exec``
round-trip, so results are cached with a short TTL.

Design mirrors :func:`hal0.slots.capacity._container_cgroup_mem_bytes`:

  - Resolve the container runtime (podman → docker) and ``exec`` into the
    slot container ``hal0-slot-<name>``.
  - Run the **unwrapped** ``xrt-smi`` binary
    (``/opt/xilinx/xrt/bin/unwrapped/xrt-smi``) — the wrapped launcher is
    a Python shim that sources ``setup.sh`` and breaks under a bare
    ``exec``.
  - Fail-soft on every error path (no runtime, missing container, old
    image without xrt-smi, non-zero exit, timeout, empty/garbage output):
    return ``None`` so the route can degrade to the single-tenant
    "all 8 columns" binary fallback.

Cache: module-level dict keyed by container name, TTL
:data:`_COL_CACHE_TTL_S` seconds, with a monotonic clock seam
(:data:`_now`) tests can monkeypatch. Loads/unloads invalidate via
:func:`invalidate_columns_cache`; otherwise the TTL drives periodic
re-probing.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

# Unwrapped xrt-smi binary inside the FLM container. The wrapped path
# (/opt/xilinx/xrt/bin/xrt-smi) is a Python launcher that needs setup.sh
# sourced; under a bare `podman exec` it fails, so we call the real ELF.
_XRT_SMI_BIN = "/opt/xilinx/xrt/bin/unwrapped/xrt-smi"

# AIE-partition examine probe, run through `sh -c` inside the container.
#
# We cannot stream JSON via `-o /dev/stdout`: the live xrt-smi build rejects it
# ("output file '/dev/stdout' already exists", exit 1), and with `--force` it
# interleaves its human-readable console report onto stdout *alongside* the
# JSON, so the captured payload no longer parses. The robust path is the /tmp
# dance — write JSON to a private per-exec temp file (``$$`` is the in-container
# shell PID, unique even under concurrent probes), ``cat`` it back (JSON only),
# then remove it. A failed probe propagates a non-zero exit so the returncode
# check in :func:`read_aie_columns` degrades to None.
_XRT_SMI_SH = (
    'f="/tmp/hal0-aie-$$.json"; '
    '"' + _XRT_SMI_BIN + '" examine -r aie-partitions -f JSON -o "$f" --force '
    '>/dev/null 2>&1 || { rm -f "$f"; exit 1; }; '
    'cat "$f"; rm -f "$f"'
)

# Exec timeout — xrt-smi examine is sub-second when healthy.
_EXEC_TIMEOUT_S = 2.0

# ── cache (TTL, monotonic clock seam) ────────────────────────────────────────
# Module attr so tests can monkeypatch the clock: ``npu_columns._now``.
_now = time.monotonic
_COL_CACHE_TTL_S = 30.0
# container_name -> (probed_at_monotonic, value-or-None)
_COL_CACHE: dict[str, tuple[float, dict[str, Any] | None]] = {}


def _container_runtime() -> str | None:
    """Resolve the podman/docker binary, or ``None`` when neither exists.

    Mirrors :func:`hal0.providers.container._container_runtime` but returns
    ``None`` (fail-soft) instead of raising — a missing runtime just means
    "can't probe columns", which degrades cleanly.
    """
    import os
    import shutil

    override = os.environ.get("HAL0_CONTAINER_RUNTIME")
    if override:
        return override
    for candidate in ("podman", "docker"):
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _parse_aie_partitions(payload: str) -> dict[str, Any] | None:
    """Parse ``xrt-smi examine -r aie-partitions -f JSON`` output.

    Contract: ``devices[0].aie_partitions.partitions[]`` where each
    partition carries ``start_col``, ``num_cols``, ``partition_index`` and
    ``hw_contexts[]``. Returns::

        {"partitions": [{"start_col": 0, "num_cols": 8, "contexts": N}],
         "total": <sum of num_cols, capped at 8>}

    Defensive: any missing/garbage key → ``None`` so the caller degrades.
    """
    if not payload or not payload.strip():
        return None
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    devices = data.get("devices")
    if not isinstance(devices, list) or not devices:
        return None
    dev0 = devices[0]
    if not isinstance(dev0, dict):
        return None
    aie = dev0.get("aie_partitions")
    if not isinstance(aie, dict):
        return None
    raw_parts = aie.get("partitions")
    if not isinstance(raw_parts, list):
        return None

    parts: list[dict[str, Any]] = []
    total = 0
    for p in raw_parts:
        if not isinstance(p, dict):
            return None
        if "start_col" not in p or "num_cols" not in p:
            return None
        try:
            start_col = int(p["start_col"])
            num_cols = int(p["num_cols"])
        except (TypeError, ValueError):
            return None
        if num_cols <= 0 or start_col < 0:
            return None
        ctx = p.get("hw_contexts")
        contexts = len(ctx) if isinstance(ctx, list) else 0
        parts.append(
            {
                "start_col": start_col,
                "num_cols": num_cols,
                "contexts": contexts,
            }
        )
        total += num_cols

    if not parts:
        return None
    return {"partitions": parts, "total": min(total, 8)}


async def read_aie_columns(container_name: str) -> dict[str, Any] | None:
    """Probe live AIE column allocation inside *container_name*.

    Execs ``xrt-smi examine -r aie-partitions`` inside the named container
    via the resolved runtime. Returns the parsed partition dict (see
    :func:`_parse_aie_partitions`) or ``None`` on any failure — no runtime,
    missing container, old image without xrt-smi, non-zero exit, timeout,
    empty stdout, or unparseable JSON.

    Uncached — see :func:`cached_aie_columns` for the TTL wrapper.
    """
    runtime = _container_runtime()
    if runtime is None:
        return None

    try:
        proc = await asyncio.create_subprocess_exec(
            runtime,
            "exec",
            container_name,
            "sh",
            "-c",
            _XRT_SMI_SH,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, _err = await asyncio.wait_for(proc.communicate(), timeout=_EXEC_TIMEOUT_S)
    except (TimeoutError, FileNotFoundError, OSError):
        return None

    if proc.returncode != 0:
        return None

    payload = out.decode("utf-8", errors="replace") if out else ""
    if not payload.strip():
        return None

    return _parse_aie_partitions(payload)


async def cached_aie_columns(container_name: str) -> dict[str, Any] | None:
    """Return AIE columns for *container_name*, cached for the TTL window.

    On a cache hit within :data:`_COL_CACHE_TTL_S` returns the stored value
    (including a cached ``None`` — a recent failed probe is not re-attempted
    until the TTL lapses, so a degraded slot doesn't hammer ``podman exec``
    on every poll). Otherwise re-probes via :func:`read_aie_columns` and
    stores the result.
    """
    cached = _COL_CACHE.get(container_name)
    if cached is not None:
        probed_at, value = cached
        if (_now() - probed_at) < _COL_CACHE_TTL_S:
            return value

    value = await read_aie_columns(container_name)
    _COL_CACHE[container_name] = (_now(), value)
    return value


def invalidate_columns_cache(container_name: str | None = None) -> None:
    """Drop cached column data so the next read re-probes.

    With *container_name* clears just that entry; with ``None`` clears the
    whole cache. Called on slot load/unload (allocation changed) and by
    tests for isolation.
    """
    if container_name is None:
        _COL_CACHE.clear()
    else:
        _COL_CACHE.pop(container_name, None)


__all__ = [
    "cached_aie_columns",
    "invalidate_columns_cache",
    "read_aie_columns",
]
