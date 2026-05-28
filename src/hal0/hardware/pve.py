"""Proxmox host-pressure probe.

Optional integration: on Strix Halo (and any other AMD-APU /
single-physical-pool box) deployed as an LXC, the LXC's view of memory
is just its cgroup share. Other tenants and the host kernel itself draw
from the same physical DIMMs the GPU's GTT lives in — competing for the
unified pool with no in-LXC visibility.

When the operator configures ``/etc/hal0/proxmox.json`` with a
read-only API token, we pull cluster/resources and surface:

  - ``host_mem_total_mb`` / ``host_mem_used_mb`` — physical host RAM
  - ``tenants`` — every running LXC/VM with its actual + max memory

…so the dashboard's unified-memory bar can show "Proxmox Host" pressure
honestly instead of pretending only this LXC's bytes exist.

If the config file is missing, every API caller treats it as
"not configured" and skips the segment — non-Proxmox deployments stay
out of the way.

Port target: haloai/lib/hardware.py:_pve_status (~2026-04-30).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import ssl
import tempfile
import urllib.request
from enum import StrEnum
from pathlib import Path
from typing import Any

import structlog

from hal0.config import paths

log = structlog.get_logger(__name__)


def pve_config_path() -> Path:
    """Return /etc/hal0/proxmox.json (or HAL0_HOME-rooted equivalent)."""
    return paths.etc() / "proxmox.json"


def _load_pve_config() -> dict[str, Any] | None:
    """Read /etc/hal0/proxmox.json and return the parsed dict, or None.

    Returns None for: file missing, unreadable, malformed JSON, or
    missing required keys. Non-Proxmox deployments stay silent.
    """
    target = pve_config_path()
    try:
        raw = json.loads(target.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        log.warning("pve.config_unreadable", path=str(target), error=str(exc))
        return None

    try:
        proxmox = raw["proxmox"]
        auth = raw["auth"]
        cfg = {
            "host": proxmox["host"],
            "port": int(proxmox.get("port", 8006)),
            "verify_ssl": bool(proxmox.get("verify_ssl", False)),
            "user": auth["user"],
            "token_name": auth["token_name"],
            "token_value": auth["token_value"],
        }
    except (KeyError, TypeError, ValueError) as exc:
        log.warning("pve.config_invalid", path=str(target), error=str(exc))
        return None
    return cfg


def save_pve_config(payload: dict[str, Any]) -> None:
    """Atomically write /etc/hal0/proxmox.json from a flat payload.

    Accepts the same flat shape we accept from the API
    ({host, port, user, token_name, token_value, verify_ssl}) and writes
    the nested {proxmox, auth} structure haloai's _load_pve_config
    expects, so the two configs stay byte-compatible.
    """
    nested = {
        "proxmox": {
            "host": str(payload["host"]),
            "port": int(payload.get("port", 8006)),
            "verify_ssl": bool(payload.get("verify_ssl", False)),
            "service": "PVE",
        },
        "auth": {
            "user": str(payload["user"]),
            "token_name": str(payload["token_name"]),
            "token_value": str(payload["token_value"]),
        },
    }
    target = pve_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_str = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    tmp = Path(tmp_str)
    try:
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(nested, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            with contextlib.suppress(OSError):
                os.close(fd)
            raise
        # Token value is sensitive — 0600 keeps it out of the world-read
        # path even if /etc/hal0 is more permissive.
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
        tmp = None  # type: ignore[assignment]
    finally:
        if tmp is not None:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
    # Drop the cache so the next status call observes the new creds.
    invalidate_pve_cache()


def delete_pve_config() -> bool:
    """Remove /etc/hal0/proxmox.json. Returns True iff the file existed."""
    target = pve_config_path()
    existed = target.exists()
    if existed:
        target.unlink()
    invalidate_pve_cache()
    return existed


# ── HTTP fetch ───────────────────────────────────────────────────────────────


def _fetch_pve_resources(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Blocking GET /api2/json/cluster/resources. Run via to_thread."""
    url = f"https://{cfg['host']}:{cfg['port']}/api2/json/cluster/resources"
    header = f"PVEAPIToken={cfg['user']}!{cfg['token_name']}={cfg['token_value']}"
    ctx: ssl.SSLContext | None = None
    if not cfg["verify_ssl"]:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"Authorization": header})
    with urllib.request.urlopen(req, timeout=3.0, context=ctx) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body.get("data", []) or []


def _summarise(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Reduce /cluster/resources entries to the dashboard's flat shape."""
    node = next((e for e in entries if e.get("type") == "node"), None) or {}
    tenants: list[dict[str, Any]] = []
    for e in entries:
        t = e.get("type")
        if t not in ("lxc", "qemu"):
            continue
        tenants.append(
            {
                "type": t,
                "vmid": e.get("vmid"),
                "name": e.get("name"),
                "status": e.get("status"),
                "maxmem_mb": round((e.get("maxmem") or 0) / (1024 * 1024), 1),
                "mem_mb": round((e.get("mem") or 0) / (1024 * 1024), 1),
                "maxcpu": e.get("maxcpu"),
                "cpu_pct": round((e.get("cpu") or 0) * 100, 1),
                "node": e.get("node"),
            }
        )
    tenants.sort(key=lambda t: (t["status"] != "running", -t["maxmem_mb"]))

    running = [t for t in tenants if t["status"] == "running"]
    allocated_mb = sum(t["maxmem_mb"] for t in running)
    host_max = node.get("maxmem") or 0
    host_used = node.get("mem") or 0
    return {
        "configured": True,
        "ok": True,
        "node": node.get("node"),
        "host_mem_total_mb": round(host_max / (1024 * 1024), 1),
        "host_mem_used_mb": round(host_used / (1024 * 1024), 1),
        "host_mem_free_mb": round(max(0, host_max - host_used) / (1024 * 1024), 1),
        "host_cpu_pct": round((node.get("cpu") or 0) * 100, 1),
        "host_cpu_count": node.get("maxcpu"),
        "host_uptime_s": node.get("uptime"),
        "tenants_running": len(running),
        "tenants_total": len(tenants),
        "tenants_allocated_mb": round(allocated_mb, 1),
        "tenants": tenants,
    }


# ── Auto-detection (used when no proxmox.json is configured) ───────────────


class PveDetectionState(StrEnum):
    """Confidence that the current host is a Proxmox-managed LXC.

    Used only when /etc/hal0/proxmox.json is missing — the UI surfaces a
    non-blocking nudge in the DETECTED / UNCERTAIN cases.
    """

    NOT_DETECTED = "not_detected"
    UNCERTAIN = "uncertain"
    DETECTED = "detected"


# Module-level Paths so tests can monkeypatch the read sources.
_PROC_VERSION = Path("/proc/version")
_PROC_1_CGROUP = Path("/proc/1/cgroup")


def _has_pve_kernel() -> bool:
    try:
        return "-pve" in _PROC_VERSION.read_text(errors="ignore")
    except OSError:
        return False


def _is_lxc_init() -> bool:
    try:
        text = _PROC_1_CGROUP.read_text(errors="ignore")
    except OSError:
        return False
    # Proxmox LXCs put init under lxc.payload.<vmid>/… or /lxc/<vmid>/…
    return "lxc.payload" in text or "/lxc/" in text


def detect_proxmox_host() -> PveDetectionState:
    """Best-effort detection of whether hal0 is running inside a Proxmox LXC.

    Signals (each independent, none requires shelling out):
      - /proc/version contains '-pve'   (strong)
      - /proc/1/cgroup is lxc-shaped    (medium)

    Both signals present → DETECTED. Either signal alone → UNCERTAIN. Neither → NOT_DETECTED.
    Never raises; unreadable inputs collapse to NOT_DETECTED.
    """
    pve_kernel = _has_pve_kernel()
    lxc_init = _is_lxc_init()
    if pve_kernel and lxc_init:
        return PveDetectionState.DETECTED
    if pve_kernel or lxc_init:
        return PveDetectionState.UNCERTAIN
    return PveDetectionState.NOT_DETECTED


# ── Slim projection + transition detection ──────────────────────────────────

# Fields the dashboard's /api/stats/hardware doesn't read — keep them in
# the full shape (returned by /api/settings/proxmox GET) but strip from
# the slim shape so the 2.5 s-poll payload doesn't grow with cluster size.
_SLIM_DROP_KEYS = (
    "tenants",
    "host_cpu_count",
    "host_uptime_s",
    "tenants_allocated_mb",
)


def project_slim(full: dict[str, Any]) -> dict[str, Any]:
    """Strip per-tenant + unused-scalar fields from a full pve_status dict.

    /api/stats/hardware is polled every 2.5 s; the full tenants[] array
    grows O(cluster_size). The Settings card still gets the full shape
    via /api/settings/proxmox where the cadence is on-demand.
    """
    if not full.get("configured"):
        return full  # {"configured": false} — nothing to strip
    return {k: v for k, v in full.items() if k not in _SLIM_DROP_KEYS}


# Tracks the most recent ``ok`` observation so the caller (hardware route)
# can emit a one-shot event on the ok→broken / broken→ok transitions.
# None means "never observed" (process start, or configuration was just
# removed) — the next observation primes the value but emits nothing.
_prev_ok: bool | None = None


def pop_transition(current: dict[str, Any]) -> str | None:
    """Return ``"became_broken"`` / ``"recovered"`` / ``None``.

    Pass the most recent ``pve_status()`` result. Updates the stored
    state — call this exactly once per fresh observation. Returns a
    transition tag only when ``ok`` flipped; otherwise ``None``.

    Unconfigured deployments reset the stored state so a later
    configure-then-fail doesn't fire a spurious "recovered" event
    pulled from a much earlier session.
    """
    global _prev_ok
    if not current.get("configured"):
        _prev_ok = None
        return None
    cur_ok = bool(current.get("ok"))
    prev = _prev_ok
    _prev_ok = cur_ok
    if prev is None:
        return None  # first observation: prime, don't emit
    if prev and not cur_ok:
        return "became_broken"
    if not prev and cur_ok:
        return "recovered"
    return None


# ── Cached async wrapper ─────────────────────────────────────────────────────

_TTL_S = 30.0
_cache: dict[str, Any] | None = None
_cache_at: float = 0.0
_lock = asyncio.Lock()


def invalidate_pve_cache() -> None:
    """Drop the cached pve_status result so the next call re-fetches."""
    global _cache, _cache_at
    _cache = None
    _cache_at = 0.0


async def pve_status() -> dict[str, Any]:
    """Return the current Proxmox host status. Cached 30 s, single-flight.

    Always returns a dict — never raises — so the API route can merge it
    unconditionally. Shape:

      - config missing:  ``{"configured": false}``
      - fetch failed:    ``{"configured": true, "ok": false, "error": ...}``
      - fetch succeeded: full summary (see _summarise)
    """
    global _cache, _cache_at
    loop = asyncio.get_event_loop()
    now = loop.time()
    if _cache is not None and (now - _cache_at) < _TTL_S:
        return _cache

    async with _lock:
        now = loop.time()
        if _cache is not None and (now - _cache_at) < _TTL_S:
            return _cache

        cfg = _load_pve_config()
        if cfg is None:
            result: dict[str, Any] = {"configured": False}
            _cache = result
            _cache_at = now
            return result

        try:
            entries = await asyncio.to_thread(_fetch_pve_resources, cfg)
        except Exception as exc:
            result = {
                "configured": True,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
            _cache = result
            _cache_at = now
            return result

        result = _summarise(entries)
        _cache = result
        _cache_at = now
        return result


async def pve_test(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a candidate config WITHOUT writing it or touching the cache.

    Used by ``POST /api/settings/proxmox/test`` so operators can verify
    credentials in the UI before saving.
    """
    cfg = {
        "host": str(payload.get("host", "")),
        "port": int(payload.get("port", 8006)),
        "verify_ssl": bool(payload.get("verify_ssl", False)),
        "user": str(payload.get("user", "")),
        "token_name": str(payload.get("token_name", "")),
        "token_value": str(payload.get("token_value", "")),
    }
    missing = [k for k in ("host", "user", "token_name", "token_value") if not cfg[k]]
    if missing:
        return {"ok": False, "error": f"missing required fields: {', '.join(missing)}"}
    try:
        entries = await asyncio.to_thread(_fetch_pve_resources, cfg)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    summary = _summarise(entries)
    return {
        "ok": True,
        "node": summary["node"],
        "host_mem_total_mb": summary["host_mem_total_mb"],
        "tenants_total": summary["tenants_total"],
    }


__all__ = [
    "PveDetectionState",
    "delete_pve_config",
    "detect_proxmox_host",
    "invalidate_pve_cache",
    "pve_config_path",
    "pve_status",
    "pve_test",
    "save_pve_config",
]
