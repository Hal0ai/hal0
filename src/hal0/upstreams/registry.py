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

import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field, replace
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from hal0.config import paths as _paths
from hal0.errors import Hal0Error
from hal0.http_client import async_client
from hal0.upstreams.catalog import UpstreamModel
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


class UpstreamAuthUnconfigured(UpstreamError):
    """An upstream declares an auth style that needs a credential it lacks.

    Before #1513 this was silent: :meth:`UpstreamRegistry.auth_headers`
    returned ``{}`` when the env var behind ``auth_value_env`` was unset, so
    the request went out unauthenticated and the *remote* reported the
    problem — as a 401 the operator had to go and read — while the dashboard
    showed a green "key set" chip (``auth_key_present`` is env-var presence,
    not presentability). Failing here names the upstream and the variable, at
    the point the credential was supposed to be attached.

    502 rather than 500: the call never left hal0, and the fault is
    configuration of a remote dependency, not an internal error.
    """

    code = "system.upstream_auth_unconfigured"
    status = 502


# "hal0" is reserved from the reactive CRUD surface (POST/DELETE
# /api/upstreams) — it's the cache key the /v1/models aggregator and the
# dashboard's synthetic slot tile use for the direct-read model catalogue
# (see hal0.api._fetch_hal0_composite_models), so a same-named upstream
# created through the API would collide with it. Operators can still author
# an explicit ``hal0`` row in upstreams.toml (loaded at hydrate time,
# bypassing this CRUD-only guard) to point it at a real remote.
RESERVED_UPSTREAM_NAME = "hal0"


# ── Upstream dataclass ────────────────────────────────────────────────────────


#: Auth styles :func:`build_auth_headers` knows how to render.
KNOWN_AUTH_STYLES = frozenset({"bearer", "anthropic", "header", "none"})

#: Upper bound on a probed ``/models`` body; a hostile endpoint cannot make the
#: dialog's server-side fetch buffer more than this.
PROBE_MAX_BODY_BYTES = 2 * 1024 * 1024
_PROBE_ERROR_EXCERPT = 300


class ProbeUrlError(ValueError):
    """An unusable probe URL. The message is fixed text and never echoes the URL or key."""


def _decode_capped(raw: bytes, encoding: str) -> bytes | None:
    """Decode a gzip/deflate body without ever holding more than the cap.

    ``None`` means the decoded body exceeds :data:`PROBE_MAX_BODY_BYTES` or the
    encoding is unsupported/corrupt.
    """
    import zlib

    enc = encoding.strip().lower()
    if enc in ("", "identity"):
        return raw
    if enc not in ("gzip", "x-gzip", "deflate"):
        return None
    attempts = (zlib.MAX_WBITS | 16,) if enc != "deflate" else (zlib.MAX_WBITS, -zlib.MAX_WBITS)
    for wbits in attempts:
        d = zlib.decompressobj(wbits)
        try:
            out = d.decompress(raw, PROBE_MAX_BODY_BYTES + 1)
        except zlib.error:
            continue
        if len(out) > PROBE_MAX_BODY_BYTES:
            return None
        # A stream that did not reach its end is truncated; data after the end
        # is ignored.
        if d.eof:
            return out
    return None


def build_auth_headers(style: str, header_name: str, key: str) -> dict[str, str]:
    """Render outbound auth headers for ``style`` from an in-hand ``key``.

    Pure: reads no environment. Shared by :meth:`UpstreamRegistry.auth_headers`
    (key from ``$auth_value_env``) and :meth:`UpstreamRegistry.probe` (key in
    transit). An empty ``key`` means "no credential header". Unknown styles
    yield ``{}``.
    """
    style = (style or "bearer").lower()
    if style == "bearer":
        if not key:
            return {}
        header = header_name or "Authorization"
        if header.lower() == "authorization" and not key.lower().startswith("bearer "):
            return {header: f"Bearer {key}"}
        return {header: key}
    if style == "anthropic":
        out: dict[str, str] = {"anthropic-version": "2023-06-01"}
        if key:
            out["x-api-key"] = key
        return out
    if style == "header":
        if header_name and key:
            return {header_name: key}
        return {}
    return {}


def normalise_key(style: str, header_name: str, key: str) -> tuple[str, str] | None:
    """Split an operator-supplied ``key`` into ``(value to send or store, bare token)``.

    A leading ``Bearer `` is stripped only where hal0 itself prepends one: the
    ``bearer`` style on the ``Authorization`` header. Everywhere else the value
    is kept verbatim (an operator may need ``X-Authorization: Bearer ...``), but
    the bare token is still returned so callers can redact it. ``None`` when the
    token is not printable ASCII without whitespace; one space after a leading
    ``Bearer`` is allowed in the verbatim styles. ``("", "")`` means no key.
    """
    stripped = key.strip()
    bare = stripped
    if stripped.lower().startswith("bearer "):
        bare = stripped[7:].strip()
    if not (bare.isascii() and all(0x21 <= ord(ch) <= 0x7E for ch in bare)):
        return None
    adds_prefix = (style or "bearer").lower() == "bearer" and (
        (header_name or "Authorization").lower() == "authorization"
    )
    if adds_prefix or bare == stripped:
        return bare, bare
    if stripped[7:] != bare:  # exactly one space after the prefix
        return None
    return stripped, bare


def _redact_secret(text: str, key: str) -> str:
    """Replace every occurrence of ``key`` (and its URL-encoded form) with ``***``.

    An empty key redacts nothing (it means "no auth header"); a short key is
    redacted like any other.
    """
    if not key:
        return text
    from urllib.parse import quote

    text = text.replace(key, "***")
    encoded = quote(key, safe="")
    if encoded != key:
        text = re.sub(re.escape(encoded), "***", text, flags=re.IGNORECASE)
    return text


def _parse_json(text: str) -> Any:
    return json.loads(text)


def _probe_transport_error(exc: BaseException) -> str:
    """A short, fixed-vocabulary description; never ``str(exc)`` (it can carry URL/headers)."""
    if isinstance(exc, httpx.TimeoutException):
        return "timeout contacting provider"
    cause = exc.__cause__ or exc.__context__
    if isinstance(exc, ConnectionRefusedError) or isinstance(cause, ConnectionRefusedError):
        return "connection refused"
    if isinstance(exc, httpx.ConnectError):
        return "connection failed"
    return f"request failed ({type(exc).__name__})"


def _probe_url(url: str) -> str:
    """Validate a probe target and return its ``/models`` URL; ``ProbeUrlError`` if unusable.

    Only http/https with a host, a valid port and no embedded credentials.
    Loopback and LAN hosts stay allowed (Ollama, a peer hal0). The message
    never echoes the URL.
    """
    from urllib.parse import urlsplit

    try:
        parts = urlsplit((url or "").strip())
        host = parts.hostname
        parts.port  # noqa: B018 - raises ValueError on a non-numeric/out-of-range port
    except ValueError:
        raise ProbeUrlError("url is not a valid http(s) URL") from None
    if parts.scheme not in ("http", "https") or not host:
        raise ProbeUrlError("url must be an http:// or https:// URL with a host")
    if parts.username or parts.password:
        raise ProbeUrlError("url must not contain embedded credentials")
    return url.strip().rstrip("/") + "/models"


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
    """How to present the API key: "bearer" | "anthropic" | "header" | "none"."""

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

    catalog_id: str | None = None
    """Provider preset this row was created from, if any."""

    curated: bool = False
    """When True only the enabled rows in ``models`` are served."""

    budget_monthly_usd: float | None = None
    """Optional monthly spend ceiling in USD."""

    models: tuple[UpstreamModel, ...] = ()
    """Per-model rows (``[[upstream.models]]``)."""

    managed_by: str | None = None
    """Owner tag for agent-managed entries (e.g. ``"agent:hermes"``); ``None`` = operator-authored.
    Dataclass-only: dynamic entries never round-trip TOML."""

    pinned: bool = False
    """Model-id routing is pinned to this upstream's ``owned_models``.
    Dataclass-only: dynamic entries never round-trip TOML."""

    owned_models: tuple[str, ...] = ()
    """Model ids this upstream owns when ``pinned``.
    Dataclass-only: dynamic entries never round-trip TOML."""

    stream_idle_timeout_s: float | None = None
    """Max seconds between streamed chunks; ``None`` = global default.
    Dataclass-only: dynamic entries never round-trip TOML."""

    stream_total_timeout_s: float | None = None
    """Max total seconds for one streamed response; ``None`` = global default.
    Dataclass-only: dynamic entries never round-trip TOML."""


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


_MODEL_FIELDS = tuple(f.name for f in dataclass_fields(UpstreamModel))


def _models_to_runtime(value: Any) -> tuple[UpstreamModel, ...]:
    """Normalise per-model rows (entries, dicts, or dataclasses) to runtime form."""
    out: list[UpstreamModel] = []
    for m in value or ():
        if isinstance(m, UpstreamModel):
            out.append(m)
            continue
        data = m if isinstance(m, dict) else m.model_dump()
        out.append(UpstreamModel(**{k: data[k] for k in _MODEL_FIELDS if k in data}))
    return tuple(out)


def _models_to_config(value: Any) -> list[dict[str, Any]]:
    """Normalise per-model rows to entry-dump shape (list of dicts)."""
    return [asdict(m) for m in _models_to_runtime(value)]


def _fields_for_entry(fields: dict[str, Any]) -> dict[str, Any]:
    out = dict(fields)
    if "model_filters" in out:
        out["model_filters"] = _filters_to_config(out["model_filters"])
    if "models" in out:
        out["models"] = _models_to_config(out["models"])
    return out


def _fields_for_dataclass(fields: dict[str, Any]) -> dict[str, Any]:
    out = dict(fields)
    if "model_filters" in out:
        out["model_filters"] = _filters_to_runtime(out["model_filters"])
    if "models" in out:
        out["models"] = _models_to_runtime(out["models"])
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
        catalog_id=entry.catalog_id,
        curated=entry.curated,
        budget_monthly_usd=entry.budget_monthly_usd,
        models=_models_to_runtime(entry.models),
    )


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

    #: Auth styles that cannot do their job without a credential. A missing
    #: key for one of these is a configuration error, not "no header today"
    #: (#1513).
    _CREDENTIALED_STYLES = frozenset({"bearer", "anthropic", "header"})

    def auth_headers(self, u: Upstream) -> dict[str, str]:
        """Build outbound auth headers honouring `u.auth_style`.

        Raises :class:`UpstreamAuthUnconfigured` when the style needs a
        credential and ``$auth_value_env`` is unset or blank. It used to
        return ``{}`` and let the request go out naked (#1513) — a silent
        downgrade from "authenticated" to "not", which is the failure mode the
        dashboard's "key set" chip was actively contradicting. A blank value
        counts as missing: ``KEY=`` is the shape left behind by clearing a
        secret, not a credential.

        ``none`` — and any upstream that declares no ``auth_value_env`` at
        all, which is every local slot upstream — is unaffected.
        """
        style = (u.auth_style or "bearer").lower()
        key = os.environ.get(u.auth_value_env, "").strip() if u.auth_value_env else ""

        if style == "none":
            return {}
        if style in self._CREDENTIALED_STYLES and u.auth_value_env and not key:
            raise UpstreamAuthUnconfigured(
                f"upstream {u.name!r} uses auth_style {style!r} but "
                f"${u.auth_value_env} is unset — the request would be sent "
                "unauthenticated. Set it via Settings ▸ Secrets or in "
                "/etc/hal0/api.env, then restart hal0-api.",
                details={"name": u.name, "auth_style": style, "env": u.auth_value_env},
            )
        if style not in KNOWN_AUTH_STYLES:
            log.debug("upstream.unknown_auth_style", name=u.name, style=style)
        return build_auth_headers(style, u.auth_header, key)

    # ── HTTP client ───────────────────────────────────────────────────────────

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            # Probes local slots and remote providers alike: async_client
            # keeps the loopback ones off the proxy environment (#2585).
            self._client = async_client(
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

    async def probe(
        self, *, url: str, auth_style: str, auth_header: str = "", key: str = ""
    ) -> dict[str, Any]:
        """Fetch ``{url}/models`` with a transient key, before anything is saved.

        Returns ``{ok, status, latency_ms, payload, headers, error}``. The key
        lives only in this call: it is never stored, logged, put in
        ``os.environ`` or returned. Every provider-derived string (error text,
        response headers, the decoded body) has the key replaced with ``***``.
        Redirects are not followed and the body read stops at
        :data:`PROBE_MAX_BODY_BYTES`. Raises :class:`ProbeUrlError` for an unusable URL (before the key is bound);
        any other failure is returned as ``ok: false`` with fixed-vocabulary text.
        """
        target = _probe_url(url)
        # Past this point the key is bound; nothing may escape carrying it in a
        # frame, so every failure becomes a fixed-vocabulary ``ok: false``.
        started = time.monotonic()
        try:
            return await self._probe_run(target, auth_style, auth_header, key)
        except Exception as exc:
            return {
                "ok": False,
                "status": None,
                "latency_ms": round((time.monotonic() - started) * 1000, 1),
                "payload": None,
                "headers": {},
                "error": _probe_transport_error(exc),
            }

    async def _probe_run(
        self, target: str, auth_style: str, auth_header: str, key: str
    ) -> dict[str, Any]:
        style = (auth_style or "bearer").lower()
        stripped = key.strip()
        normal = normalise_key(style, auth_header, key)
        if normal is not None:
            sent, token = normal
        else:  # refused input still goes out as given; keep the bare token redacted too
            sent = token = stripped
            if stripped.lower().startswith("bearer "):
                token = stripped[7:].strip()
        headers = build_auth_headers(style, auth_header, sent)
        headers["Accept-Encoding"] = "identity"
        secrets = {s for s in (key, stripped, sent, token) if s}

        def scrub(text: str) -> str:
            for secret in secrets:
                text = _redact_secret(text, secret)
            return text

        def fail(
            status: int | None, error: str, started: float, resp_headers: dict[str, str] | None
        ) -> dict[str, Any]:
            return {
                "ok": False,
                "status": status,
                "latency_ms": round((time.monotonic() - started) * 1000, 1),
                "payload": None,
                "headers": resp_headers or {},
                "error": scrub(error)[:_PROBE_ERROR_EXCERPT],
            }

        started = time.monotonic()
        try:
            async with (
                async_client(
                    timeout=httpx.Timeout(connect=3.0, read=8.0, write=3.0, pool=3.0),
                    follow_redirects=False,
                ) as c,
                c.stream("GET", target, headers=headers) as resp,
            ):
                resp_headers = {k.lower(): scrub(v) for k, v in resp.headers.items()}
                chunks: list[bytes] = []
                size = 0
                too_large = False
                # Raw (still-encoded) bytes: the cap bounds memory before any
                # decompression happens.
                async for chunk in resp.aiter_raw():
                    size += len(chunk)
                    if size > PROBE_MAX_BODY_BYTES:
                        too_large = True
                        break
                    chunks.append(chunk)
                status = resp.status_code
                reason = resp.reason_phrase
        except Exception as exc:
            return fail(None, _probe_transport_error(exc), started, None)

        if too_large:
            return fail(status, "response too large", started, resp_headers)
        decoded = _decode_capped(b"".join(chunks), resp_headers.get("content-encoding", ""))
        if decoded is None:
            return fail(
                status, "response too large or in an unsupported encoding", started, resp_headers
            )
        text = scrub(decoded.decode("utf-8", errors="replace"))
        if status != 200:
            excerpt = text[:_PROBE_ERROR_EXCERPT].strip()
            message = f"HTTP {status} {reason}".strip()
            if excerpt:
                message = f"{message}: {excerpt}"
            return fail(status, message, started, resp_headers)
        try:
            payload = _parse_json(text)
        except ValueError:
            return fail(status, "response was not JSON", started, resp_headers)
        log.debug("upstream.probe_ok", host=target.split("/")[2], status=status)
        return {
            "ok": True,
            "status": status,
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
            "payload": payload,
            "headers": resp_headers,
            "error": None,
        }

    async def fetch_model_payload(self, name: str) -> Any | None:
        """Return the decoded JSON body of an upstream's ``/models``.

        ``None`` on any failure: unknown upstream, unconfigured credential,
        transport error, non-2xx status, or a body that is not JSON. Does not
        raise.
        """
        u = self._upstreams.get(name)
        if u is None:
            return None
        target = u.url.rstrip("/") + "/models"
        try:
            # #1513: auth_headers now raises on an unconfigured credential
            # rather than silently probing naked. Model prefetch is
            # best-effort — an upstream that cannot authenticate drops out of
            # the advertised list instead of taking the whole fan-out down.
            headers = self.auth_headers(u)
        except UpstreamAuthUnconfigured as exc:
            log.warning("upstream.fetch_models_auth_unconfigured", name=name, detail=str(exc))
            return None
        try:
            c = self._get_client()
            resp = await c.get(
                target,
                headers=headers,
                timeout=httpx.Timeout(connect=3.0, read=5.0, write=3.0, pool=3.0),
            )
            if not (200 <= resp.status_code < 300):
                log.debug(
                    "upstream.fetch_models_bad_status",
                    name=name,
                    status=resp.status_code,
                )
                return None
            return resp.json()
        except (httpx.HTTPError, OSError, ValueError) as exc:
            log.debug("upstream.fetch_models_fail", name=name, error=str(exc))
            return None

    async def fetch_models(self, name: str) -> list[str]:
        """Return the list of model ids advertised by an upstream's /v1/models.

        Empty list on any failure. Does not raise (the dispatcher needs a
        forgiving aggregation).
        """
        body = await self.fetch_model_payload(name)
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
