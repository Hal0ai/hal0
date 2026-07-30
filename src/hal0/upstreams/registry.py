"""UpstreamRegistry — registry of HTTP upstream targets.

An Upstream is one routing target that speaks OpenAI-compatible HTTP (or
close enough for the Dispatcher's forwarding layer).

Two kinds:
  - "slot"    — local inference container managed by SlotManager.
                Eligible for on-demand warmup.
  - "remote"  — external HTTP endpoint (OpenRouter, Anthropic, OpenAI, custom).
                Lifecycle is owned elsewhere.

Loaded from /etc/hal0/upstreams.toml plus auto-registered slots.  The TOML
wins for any slot that's explicitly listed; missing slots get auto-populated
from configured slot names and their ports.

Port target: haloai lib/upstreams.py (737 lines).
Adds: adaptive cold-boot timeout (PLAN.md §5 Tier 1).
See PLAN.md §3 and §5 Tier 1 ("cold-boot health probe … exponential backoff").
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from hal0.config import paths as _paths
from hal0.errors import Hal0Error
from hal0.upstreams.filters import ModelFilters

if TYPE_CHECKING:
    from hal0.config.schema import UpstreamEntry

log = structlog.get_logger(__name__)


# ── Constants ─────────────────────────────────────────────────────────────────

_DEFAULT_TIMEOUT_SECONDS = 300.0

# TIER1: adaptive cold-boot backoff replacing the old hardcoded 2s timeout
# in haloai lib/upstreams.py:500-520. Probe interval steps (seconds) and the
# total grace cap. Both can be overridden per-slot from hardware.json (see
# UpstreamRegistry._load_slot_overrides). Jitter is ±25% of each step.
TIER1_BACKOFF_STEPS: tuple[float, ...] = (0.5, 1.0, 2.0, 5.0, 10.0)
TIER1_BACKOFF_JITTER_FRAC: float = 0.25
TIER1_TOTAL_GRACE_S: float = 180.0


# ── Errors ────────────────────────────────────────────────────────────────────


class UpstreamError(Hal0Error):
    """Base class for upstream-registry errors."""

    code = "system.upstream_error"
    status = 500


class UpstreamNotFound(UpstreamError, KeyError):
    code = "system.upstream_not_found"
    status = 404


class UpstreamAlreadyExists(UpstreamError):
    code = "system.upstream_already_exists"
    status = 409


class UpstreamProtected(UpstreamError):
    """Raised when mutating a slot-backed upstream or the reserved name."""

    code = "system.upstream_protected"
    status = 400


# "hal0" is reserved from the reactive CRUD surface (POST/DELETE
# /api/upstreams) — it's the cache key the /v1/models aggregator and the
# dashboard's synthetic slot tile use for the direct-read model catalogue
# (see hal0.api._fetch_hal0_composite_models), so a same-named upstream
# created through the API would collide with it. Operators can still author
# an explicit ``hal0`` row in upstreams.toml (loaded at hydrate time,
# bypassing this CRUD-only guard) to point it at a real remote.
RESERVED_UPSTREAM_NAME = "hal0"


# ── Upstream dataclass ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Upstream:
    """One routing target.  Frozen — mutable state lives in UpstreamRegistry caches.

    Mirrors haloai lib/upstreams.py::Upstream, adapted for hal0 naming and
    the hal0.config.paths path resolver.
    """

    name: str
    """Unique name within this registry, e.g. "primary" or "openrouter"."""

    kind: str
    """Target kind: "slot" | "remote"."""

    url: str
    """Base URL, e.g. "http://127.0.0.1:8081/v1" or "https://openrouter.ai/api/v1"."""

    auth_style: str = "bearer"
    """How to present the API key: "bearer" | "anthropic" | "google_query" | "header" | "none"."""

    auth_header: str = ""
    """Custom header name when auth_style == "header"."""

    auth_value_env: str = ""
    """Environment variable holding the API key credential.  Never stored in TOML."""

    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    """Total request timeout for this upstream.  0 means use the global default."""

    slot_name: str | None = None
    """Set when kind == "slot".  Must match a configured slot name."""

    warmup_strategy: str = "none"
    """On-demand warmup policy: "none" | "ondemand" | "always"."""

    health_path: str = "/health"
    """Path for health checks, relative to url."""

    ttl_warmup_seconds: float = TIER1_TOTAL_GRACE_S
    """Total warmup grace period (TIER1: defaults to 180s, was 30s in haloai)."""

    advertise_models: bool = True
    """Whether to include this upstream's /v1/models in aggregated model list."""

    backoff_steps: tuple[float, ...] = field(default_factory=lambda: TIER1_BACKOFF_STEPS)
    """TIER1: per-slot probe interval steps.  Override via hardware.json."""

    enabled: bool = True
    """When False the dispatcher and /v1/models skip this upstream entirely;
    config and credentials are retained (kill-switch, not deletion)."""

    model_filters: ModelFilters | None = None
    """Optional /v1/models advertising filters.  Dispatch is unfiltered."""

    hal0_peer: bool | None = None
    """Is this upstream another hal0 (issue #1425)?

    ``True``/``False`` are an explicit operator declaration; ``None`` (the
    default) auto-derives from the URL host — see
    :func:`hal0.upstreams.peers.is_hal0_peer`. Only peers are asked for
    hal0-internal API paths; a third-party OpenAI-compatible provider never
    is.
    """


# ── Config-layer ↔ runtime conversion ─────────────────────────────────────────


def _filters_to_runtime(value: Any) -> ModelFilters | None:
    """Normalise any model_filters representation to a runtime ModelFilters.

    Accepts UpstreamModelFilters (pydantic), plain dicts, ModelFilters, or
    None. Empty filters collapse to None (no-filter fast path).
    """
    if value is None:
        return None
    if isinstance(value, ModelFilters):
        return None if value.is_empty() else value
    if not isinstance(value, dict):
        value = value.model_dump()
    mf = ModelFilters.from_lists(
        models=value.get("models"),
        include=value.get("include"),
        exclude=value.get("exclude"),
    )
    return None if mf.is_empty() else mf


def _filters_to_config(value: Any) -> dict[str, list[str]] | None:
    """Normalise any model_filters representation to entry-dump shape."""
    mf = _filters_to_runtime(value)
    if mf is None:
        return None
    return {"models": list(mf.models), "include": list(mf.include), "exclude": list(mf.exclude)}


def _fields_for_entry(fields: dict[str, Any]) -> dict[str, Any]:
    out = dict(fields)
    if "model_filters" in out:
        out["model_filters"] = _filters_to_config(out["model_filters"])
    return out


def _fields_for_dataclass(fields: dict[str, Any]) -> dict[str, Any]:
    out = dict(fields)
    if "model_filters" in out:
        out["model_filters"] = _filters_to_runtime(out["model_filters"])
    return out


def upstream_from_entry(entry: UpstreamEntry) -> Upstream:
    """Build the runtime Upstream for a validated UpstreamEntry."""
    return Upstream(
        name=entry.name,
        kind=entry.kind,
        url=entry.url,
        auth_style=entry.auth_style,
        auth_header=entry.auth_header,
        auth_value_env=entry.auth_value_env,
        timeout_seconds=entry.timeout_seconds,
        slot_name=entry.slot_name,
        warmup_strategy=entry.warmup_strategy,
        advertise_models=entry.advertise_models,
        enabled=entry.enabled,
        model_filters=_filters_to_runtime(entry.model_filters),
        hal0_peer=entry.hal0_peer,
    )


# ── Warmup state ──────────────────────────────────────────────────────────────


@dataclass
class _SlotStats:
    """Per-slot runtime counters surfaced to the dispatcher / dashboard.

    # TIER2: tps is recomputed on each request; clamped at 0 to avoid the
    # haloai bug where a process restart reset the histogram counter and the
    # delta went negative.  When we detect a counter reset we log a warning.
    """

    tps: float = 0.0
    """Tokens per second (clamped >= 0)."""

    last_request_at: float = 0.0
    last_counter: int = 0


# ── UpstreamRegistry ──────────────────────────────────────────────────────────


class UpstreamRegistry:
    """Registry of all routing targets (slots + remote upstreams).

    Loaded at startup from /etc/hal0/upstreams.toml (or whatever
    `hal0.config.paths.etc()` resolves to) and kept in memory.  Supports
    dynamic registration for auto-discovered slots.

    The class is intentionally synchronous for CRUD operations; the
    HTTP-touching methods (test/fetch_models/warmup) are async.
    """

    def __init__(self) -> None:
        self._upstreams: dict[str, Upstream] = {}
        self._warmup_locks: dict[str, asyncio.Lock] = {}
        self._slot_stats: dict[str, _SlotStats] = {}
        # TIER1: per-slot backoff overrides loaded from hardware.json
        self._slot_overrides: dict[str, dict[str, Any]] = {}
        self._client: httpx.AsyncClient | None = None
        # Serialises every load→mutate→save of upstreams.toml so concurrent
        # reactive writes can't lose updates (the atomic rename in
        # write_toml_atomic prevents torn files, not lost updates).
        self._persist_lock = threading.Lock()

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def list(self) -> list[Upstream]:
        """Return all registered upstreams (snapshot)."""
        return list(self._upstreams.values())

    def get(self, name: str) -> Upstream | None:
        """Return an upstream by name, or None if not found."""
        return self._upstreams.get(name)

    def add(self, upstream: Upstream) -> None:
        """Register a new upstream.

        Raises UpstreamAlreadyExists if `upstream.name` is already present.
        """
        if upstream.name in self._upstreams:
            raise UpstreamAlreadyExists(
                f"upstream {upstream.name!r} already exists",
                {"name": upstream.name},
            )
        self._upstreams[upstream.name] = upstream
        log.info("upstream.add", name=upstream.name, kind=upstream.kind, url=upstream.url)

    def upsert(self, upstream: Upstream) -> None:
        """Add or replace an upstream by name (no error on collision)."""
        self._upstreams[upstream.name] = upstream

    def remove(self, name: str) -> bool:
        """Remove an upstream by name.  Returns True if it was present."""
        existed = name in self._upstreams
        self._upstreams.pop(name, None)
        self._warmup_locks.pop(name, None)
        self._slot_stats.pop(name, None)
        if existed:
            log.info("upstream.remove", name=name)
        return existed

    def update(self, name: str, **patch: Any) -> Upstream:
        """Merge `patch` into the named upstream and return the new value."""
        cur = self._upstreams.get(name)
        if cur is None:
            raise UpstreamNotFound(f"upstream {name!r} not found", {"name": name})
        merged = replace(cur, **patch)
        self._upstreams[name] = merged
        return merged

    def set_advertise(self, name: str, value: bool, *, persist: bool = True) -> Upstream:
        """Flip an upstream's ``advertise_models`` flag and persist atomically.

        Thin wrapper over :meth:`apply_persistent_patch` — see there for the
        persistence contract.
        """
        return self.apply_persistent_patch(name, {"advertise_models": value}, persist=persist)

    def apply_persistent_patch(
        self, name: str, fields: dict[str, Any], *, persist: bool = True
    ) -> Upstream:
        """Merge ``fields`` into the named upstream and persist atomically.

        Reads ``upstreams.toml``, applies ``fields`` to the matching entry
        (re-validated through :class:`~hal0.config.schema.UpstreamEntry`),
        and writes the file back via
        :func:`hal0.config.loader.save_upstreams_config` (``write_toml_atomic``
        gives a crash-safe rename). Only after the save succeeds is the
        in-memory :class:`Upstream` updated — a failed write leaves memory
        consistent with disk (TOML stays canonical).

        ``fields`` uses config-layer values; ``model_filters`` may be an
        :class:`~hal0.config.schema.UpstreamModelFilters`, a plain dict, or
        ``None`` (clears filters). An empty filters object also clears.

        Auto-registered upstreams (slot-backed entries, which aren't
        authored in ``upstreams.toml``) have no on-disk row to patch — for
        those the in-memory value is updated and the on-disk config is
        left untouched; the value resets on restart.

        Args:
            name: Upstream name.
            fields: Field-name → new-value mapping.
            persist: When ``True`` (default), atomically write
                ``upstreams.toml``. Set to ``False`` for tests that don't
                want filesystem side-effects.

        Returns the new in-memory :class:`Upstream`.

        Raises:
            UpstreamNotFound: If ``name`` is not registered.
            ValidationError: If the patched entry fails schema validation.
        """
        cur = self._upstreams.get(name)
        if cur is None:
            raise UpstreamNotFound(f"upstream {name!r} not found", {"name": name})

        if persist:
            # Local import: hal0.config.loader imports from this module's
            # sibling modules, so a top-level import here would risk a
            # circular import in some test configurations.
            from hal0.config.loader import (
                load_upstreams_config,
                save_upstreams_config,
            )
            from hal0.config.schema import UpstreamEntry

            with self._persist_lock:
                cfg = load_upstreams_config()
                for i, entry in enumerate(cfg.upstream):
                    if entry.name == name:
                        data = entry.model_dump()
                        data.update(_fields_for_entry(fields))
                        cfg.upstream[i] = UpstreamEntry.model_validate(data)
                        save_upstreams_config(cfg)
                        break
                # else: auto-registered upstream, not on disk — in-memory only.

        merged = replace(cur, **_fields_for_dataclass(fields))
        self._upstreams[name] = merged
        log.info(
            "upstream.patch",
            name=name,
            fields=sorted(fields),
            persisted=persist,
        )
        return merged

    def create_persistent(self, entry: UpstreamEntry, *, persist: bool = True) -> Upstream:
        """Append a new upstream to ``upstreams.toml`` and register it.

        Guards: the reserved name and ``kind="slot"`` entries are rejected —
        slot upstreams are owned by SlotManager, not this surface.

        Raises:
            UpstreamProtected: Reserved name or slot kind.
            UpstreamAlreadyExists: Name collision in registry or TOML.
        """
        if entry.name == RESERVED_UPSTREAM_NAME:
            raise UpstreamProtected(
                f"upstream name {entry.name!r} is reserved", {"name": entry.name}
            )
        if entry.kind == "slot":
            raise UpstreamProtected(
                "slot upstreams are managed by the slot lifecycle, not created here",
                {"name": entry.name},
            )
        if entry.name in self._upstreams:
            raise UpstreamAlreadyExists(
                f"upstream {entry.name!r} already exists", {"name": entry.name}
            )

        if persist:
            from hal0.config.loader import (
                load_upstreams_config,
                save_upstreams_config,
            )

            with self._persist_lock:
                cfg = load_upstreams_config()
                if any(e.name == entry.name for e in cfg.upstream):
                    raise UpstreamAlreadyExists(
                        f"upstream {entry.name!r} already exists in upstreams.toml",
                        {"name": entry.name},
                    )
                cfg.upstream.append(entry)
                save_upstreams_config(cfg)

        upstream = upstream_from_entry(entry)
        self.add(upstream)
        return upstream

    def remove_persistent(self, name: str, *, persist: bool = True) -> bool:
        """Delete an upstream from ``upstreams.toml`` and the registry.

        Credentials referenced via ``auth_value_env`` are deliberately left
        in ``api.env`` — deleting an upstream must never destroy a secret.

        Returns True when a TOML row was removed (False for auto-registered
        upstreams that only existed in memory).

        Raises:
            UpstreamNotFound: If ``name`` is not registered.
            UpstreamProtected: Reserved name or slot-backed upstream.
        """
        cur = self._upstreams.get(name)
        if cur is None:
            raise UpstreamNotFound(f"upstream {name!r} not found", {"name": name})
        # Reserved name, slot-kind, AND container-backed remotes (kind="remote"
        # with slot_name set) are all owned by the slot lifecycle.
        if name == RESERVED_UPSTREAM_NAME or cur.kind == "slot" or cur.slot_name:
            raise UpstreamProtected(
                f"upstream {name!r} is protected and cannot be deleted", {"name": name}
            )

        removed_from_toml = False
        if persist:
            from hal0.config.loader import (
                load_upstreams_config,
                save_upstreams_config,
            )

            with self._persist_lock:
                cfg = load_upstreams_config()
                kept = [e for e in cfg.upstream if e.name != name]
                if len(kept) != len(cfg.upstream):
                    cfg.upstream = kept
                    save_upstreams_config(cfg)
                    removed_from_toml = True

        self.remove(name)
        return removed_from_toml

    def in_priority_order(self) -> list[Upstream]:
        """Sort order for dispatcher fallback: slots first, then remotes."""
        rank = {"slot": 0, "remote": 1}
        return sorted(
            self._upstreams.values(),
            key=lambda u: (rank.get(u.kind, 99), u.name),
        )

    def from_slot(self, slot_name: str) -> Upstream | None:
        """Look up by slot_name (kind=slot)."""
        for u in self._upstreams.values():
            if u.kind == "slot" and u.slot_name == slot_name:
                return u
        return None

    # ── hardware.json per-slot overrides ──────────────────────────────────────

    def load_slot_overrides(self, hardware_json: Path | None = None) -> None:
        """Read per-slot backoff overrides from hardware.json.

        # TIER1: PLAN §5 Tier 1 says "total grace 180s per slot, exposed via
        # hardware.json per-slot override". The expected layout is:
        #     {"slots": {"<name>": {"warmup_grace_s": 240,
        #                            "backoff_steps": [0.5, 1, 2]}}}
        # Missing file is fine — the registry uses the global TIER1 defaults.
        """
        path = hardware_json or _paths.hardware_json()
        try:
            raw = path.read_text()
        except OSError:
            self._slot_overrides = {}
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            log.warning(
                "upstream.hardware_json_parse_fail",
                path=str(path),
                error=str(exc),
            )
            self._slot_overrides = {}
            return
        slots = data.get("slots") or {}
        if not isinstance(slots, dict):
            self._slot_overrides = {}
            return
        self._slot_overrides = {
            name: dict(cfg) for name, cfg in slots.items() if isinstance(cfg, dict)
        }

    def _effective_backoff_steps(self, u: Upstream) -> tuple[float, ...]:
        """Return the (possibly per-slot-overridden) backoff steps for this upstream."""
        if u.kind != "slot" or u.slot_name is None:
            return u.backoff_steps
        override = self._slot_overrides.get(u.slot_name, {})
        steps = override.get("backoff_steps")
        if isinstance(steps, list) and steps:
            try:
                return tuple(float(s) for s in steps)
            except (TypeError, ValueError):
                log.warning(
                    "upstream.bad_backoff_override",
                    slot=u.slot_name,
                    steps=steps,
                )
        return u.backoff_steps

    def _effective_total_grace_s(self, u: Upstream) -> float:
        """Return the (possibly per-slot-overridden) warmup grace cap."""
        if u.kind == "slot" and u.slot_name is not None:
            override = self._slot_overrides.get(u.slot_name, {})
            grace = override.get("warmup_grace_s")
            if isinstance(grace, (int, float)) and grace > 0:
                return float(grace)
        return u.ttl_warmup_seconds

    # ── Auth headers ──────────────────────────────────────────────────────────

    def auth_headers(self, u: Upstream) -> dict[str, str]:
        """Build outbound auth headers honouring `u.auth_style`."""
        style = (u.auth_style or "bearer").lower()
        key = os.environ.get(u.auth_value_env, "") if u.auth_value_env else ""

        if style == "none":
            return {}
        if style == "bearer":
            if not key:
                return {}
            header = u.auth_header or "Authorization"
            if header.lower() == "authorization" and not key.lower().startswith("bearer "):
                return {header: f"Bearer {key}"}
            return {header: key}
        if style == "anthropic":
            out: dict[str, str] = {"anthropic-version": "2023-06-01"}
            if key:
                out["x-api-key"] = key
            return out
        if style == "google_query":
            # Google keys ride as ?key=… on the URL — emit no header here.
            return {}
        if style == "header":
            if u.auth_header and key:
                return {u.auth_header: key}
            return {}
        log.debug("upstream.unknown_auth_style", name=u.name, style=style)
        return {}

    # ── HTTP client ───────────────────────────────────────────────────────────

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=10.0),
                limits=httpx.Limits(max_connections=64, max_keepalive_connections=16),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    # ── Health + warmup ───────────────────────────────────────────────────────

    async def health(self, u: Upstream) -> bool:
        """Return True if a GET on the health path returns 2xx within the probe timeout."""
        url = u.url.rstrip("/")
        if u.health_path:
            base = url[:-3] if url.endswith("/v1") else url
            target = base + u.health_path
        else:
            target = url
        try:
            c = self._get_client()
            r = await c.get(
                target,
                timeout=httpx.Timeout(connect=2.0, read=3.0, write=2.0, pool=2.0),
            )
            return 200 <= r.status_code < 300
        except (httpx.HTTPError, OSError) as exc:
            log.debug("upstream.health_fail", name=u.name, url=target, error=str(exc))
            return False

    def _get_warmup_lock(self, slot_name: str) -> asyncio.Lock:
        lock = self._warmup_locks.get(slot_name)
        if lock is None:
            lock = asyncio.Lock()
            self._warmup_locks[slot_name] = lock
        return lock

    async def warmup(self, u: Upstream) -> bool:
        """Bring a slot upstream to a healthy state, with TIER1 adaptive backoff.

        Returns True when /health responds 2xx within the total grace window.
        Returns False if:
            - kind != "slot" or no slot_name configured
            - warmup_strategy == "none" (just probe once and return)
            - total grace elapses without a healthy response

        TIER1: probe interval follows TIER1_BACKOFF_STEPS with ±25% jitter,
        total grace is `_effective_total_grace_s(u)` (default 180s), and the
        per-slot override comes from hardware.json. This replaces the haloai
        hardcoded 2s timeout at lib/upstreams.py:500-520.
        """
        if u.kind != "slot" or not u.slot_name:
            return False
        if u.warmup_strategy == "none":
            return await self.health(u)

        lock = self._get_warmup_lock(u.slot_name)
        async with lock:
            # Re-check inside the lock — another waiter may have warmed it.
            if await self.health(u):
                return True

            steps = self._effective_backoff_steps(u)
            total_grace = self._effective_total_grace_s(u)
            deadline = time.monotonic() + total_grace
            attempt = 0
            log.info(
                "upstream.warmup_start",
                name=u.name,
                slot=u.slot_name,
                grace_s=total_grace,
                steps=list(steps),
            )

            while True:
                # TIER1 — exponential step with jitter, capped at the last step.
                base_step = steps[min(attempt, len(steps) - 1)]
                jitter = base_step * TIER1_BACKOFF_JITTER_FRAC
                delay = max(0.0, base_step + random.uniform(-jitter, jitter))

                # Don't oversleep past the grace deadline.
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    log.warning(
                        "upstream.warmup_timeout",
                        name=u.name,
                        slot=u.slot_name,
                        grace_s=total_grace,
                        attempts=attempt + 1,
                    )
                    return False
                delay = min(delay, remaining)
                await asyncio.sleep(delay)

                if await self.health(u):
                    log.info(
                        "upstream.warmup_ready",
                        name=u.name,
                        slot=u.slot_name,
                        attempts=attempt + 1,
                    )
                    return True

                attempt += 1
                if time.monotonic() >= deadline:
                    log.warning(
                        "upstream.warmup_timeout",
                        name=u.name,
                        slot=u.slot_name,
                        grace_s=total_grace,
                        attempts=attempt,
                    )
                    return False

    # ── Tier 2 stats ──────────────────────────────────────────────────────────

    def record_tokens(self, slot_name: str, token_counter: int, now: float | None = None) -> float:
        """Update the slot's tps from a monotonic token counter.

        # TIER2: clamps the computed tps at 0 and logs a warning when the
        # counter resets (haloai lib/slots.py:240-346 — process restart could
        # send tps negative). Returns the new (clamped) tps.
        """
        ts = time.monotonic() if now is None else now
        stats = self._slot_stats.get(slot_name)
        if stats is None:
            stats = _SlotStats(last_counter=token_counter, last_request_at=ts)
            self._slot_stats[slot_name] = stats
            return 0.0

        delta_tokens = token_counter - stats.last_counter
        delta_t = ts - stats.last_request_at

        if delta_tokens < 0:
            # TIER2 — counter went backwards: process restart or rollover.
            log.warning(
                "upstream.tps_counter_reset",
                slot=slot_name,
                previous=stats.last_counter,
                current=token_counter,
            )
            stats.tps = 0.0
        elif delta_t <= 0:
            # Same instant or clock skew — keep the previous tps but clamp >= 0.
            stats.tps = max(0.0, stats.tps)
        else:
            stats.tps = max(0.0, delta_tokens / delta_t)  # TIER2

        stats.last_counter = token_counter
        stats.last_request_at = ts
        return stats.tps

    def get_tps(self, slot_name: str) -> float:
        """Return the most recently computed tps for a slot, or 0.0 if unknown."""
        stats = self._slot_stats.get(slot_name)
        return stats.tps if stats else 0.0

    # ── HTTP-touching ops ─────────────────────────────────────────────────────

    async def test(self, name: str) -> dict[str, Any]:
        """Probe an upstream's `/models` with its configured auth.

        Returns {ok, status, latency_ms, models_count?, error?}.
        Raises UpstreamNotFound if `name` is not registered.
        """
        u = self._upstreams.get(name)
        if u is None:
            raise UpstreamNotFound(f"upstream {name!r} not found", {"name": name})

        if u.auth_value_env and not os.environ.get(u.auth_value_env):
            return {
                "ok": False,
                "error": f"env var ${u.auth_value_env} is not set",
                "latency_ms": 0.0,
            }

        target = u.url.rstrip("/") + "/models"
        headers = self.auth_headers(u)
        started = time.monotonic()
        try:
            c = self._get_client()
            resp = await c.get(
                target,
                headers=headers,
                timeout=httpx.Timeout(connect=3.0, read=8.0, write=3.0, pool=3.0),
            )
        except httpx.TimeoutException:
            return {
                "ok": False,
                "error": f"timeout contacting {target}",
                "latency_ms": round((time.monotonic() - started) * 1000, 1),
            }
        except (httpx.HTTPError, OSError) as exc:
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "latency_ms": round((time.monotonic() - started) * 1000, 1),
            }

        latency_ms = round((time.monotonic() - started) * 1000, 1)
        if resp.status_code != 200:
            return {
                "ok": False,
                "status": resp.status_code,
                "body_excerpt": resp.text[:300],
                "latency_ms": latency_ms,
            }
        try:
            body = resp.json()
        except ValueError:
            return {"ok": True, "status": 200, "models_count": None, "latency_ms": latency_ms}
        if isinstance(body, dict):
            data = body.get("data")
            count = len(data) if isinstance(data, list) else None
        elif isinstance(body, list):
            count = len(body)
        else:
            count = None
        return {"ok": True, "status": 200, "models_count": count, "latency_ms": latency_ms}

    async def fetch_models(self, name: str) -> list[str]:
        """Return the list of model ids advertised by an upstream's /v1/models.

        Empty list on any failure. Does not raise (the dispatcher needs a
        forgiving aggregation).
        """
        u = self._upstreams.get(name)
        if u is None:
            return []
        target = u.url.rstrip("/") + "/models"
        try:
            c = self._get_client()
            resp = await c.get(
                target,
                headers=self.auth_headers(u),
                timeout=httpx.Timeout(connect=3.0, read=5.0, write=3.0, pool=3.0),
            )
            if resp.status_code != 200:
                log.debug(
                    "upstream.fetch_models_non_200",
                    name=name,
                    status=resp.status_code,
                )
                return []
            body = resp.json()
        except (httpx.HTTPError, OSError, ValueError) as exc:
            log.debug("upstream.fetch_models_fail", name=name, error=str(exc))
            return []

        if isinstance(body, dict):
            data = body.get("data", [])
        elif isinstance(body, list):
            data = body
        else:
            return []
        out: list[str] = []
        for item in data if isinstance(data, list) else []:
            if isinstance(item, dict):
                mid = item.get("id")
                if isinstance(mid, str) and mid:
                    out.append(mid)
            elif isinstance(item, str):
                out.append(item)
        return out


__all__ = [
    "TIER1_BACKOFF_JITTER_FRAC",
    "TIER1_BACKOFF_STEPS",
    "TIER1_TOTAL_GRACE_S",
    "Upstream",
    "UpstreamAlreadyExists",
    "UpstreamError",
    "UpstreamNotFound",
    "UpstreamRegistry",
]
