"""Registry for hal0-hosted, user-installed MCP servers (issue #305).

Bundled MCP servers (hal0-admin, hal0-memory) are baked into the
orchestrator at start-up via :mod:`hal0.api.mcp_mount`; this module
covers the other half — user-installed MCP servers that the dashboard's
`/agents/mcp` page can install / uninstall / configure at runtime.

Scope (v0.3 alpha)
------------------

* Persist installed-server records as one TOML file per server under
  ``/etc/hal0/mcp-servers/<id>.toml`` — mirrors the slot/upstream/agent
  layout so users get a single mental model.
* Provide list / add / remove / patch helpers the FastAPI route layer
  calls through.
* No process supervision yet — that's deferred to a follow-up
  bootstrap-path effort. Installed servers report
  ``state="stopped"`` in :func:`hal0.api.routes.mcp.list_servers` until
  the supervisor ships.

The on-disk schema is intentionally small + permissive: the dashboard's
preview shape (manifest fetch in :mod:`hal0.mcp.manifest`) is the
source-of-truth for tool counts + descriptions, this file just records
what the operator chose to install + their per-server env overrides.
"""

from __future__ import annotations

import contextlib
import fcntl
import ipaddress
import os
import re
import tomllib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import structlog
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from pydantic_core import PydanticCustomError

from hal0.config import paths as cfg_paths
from hal0.config.loader import write_toml_atomic
from hal0.config.schema import ToolPolicy
from hal0.errors import BadRequest, Conflict, NotFound

log = structlog.get_logger(__name__)


# Bundled mounts that can't be uninstalled via this registry. The FastAPI
# route layer guards against deletion + the registry itself never lists
# them; this set is exposed for the route's bundled-rejection branch.
BUNDLED_SERVER_IDS = frozenset({"hal0-admin", "hal0-memory"})


# ── Schema ──────────────────────────────────────────────────────────────────

#: Grammar for a ``[secrets]`` reference — the name of a key in
#: ``/etc/hal0/api.env``. Mirrors ``routes/secrets.py:65`` (the canonical
#: enforcement point for names actually stored there); duplicated here
#: rather than imported so this domain module doesn't reach up into the
#: API route layer for a one-line regex.
_SECRET_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def is_loopback_destination(host: str) -> bool:
    """``localhost`` or any loopback IP literal (``127.0.0.0/8``, ``::1``).

    The #2304 exemption for plaintext ``http://``. It only holds if the
    request really goes direct, so :mod:`hal0.mcp.probe` ignores
    environment proxies for these hosts.
    """
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _insecure_http_message(server_id: str, host: str, keys: list[str]) -> str:
    """Operator-facing refusal for the #2304 plaintext-header gate.

    Names the header *keys* and the host — never a value.
    """
    return (
        f"MCP server {server_id!r}: header value(s) {keys} from [secrets]/[env] "
        f"would be sent in clear text to non-loopback host {host!r}"
    )


def _validation_reason(exc: ValidationError) -> str:
    """Operator-facing summary of a record's validation failure.

    Built from each error's ``loc``/``msg`` only. ``str(exc)`` would append
    pydantic's ``input_value=...``, which for a record is the whole TOML
    dict — ``[env]`` literals included — and this string lands in the
    journal and in a 400 response body.
    """
    parts = []
    for err in exc.errors(include_url=False, include_context=False, include_input=False):
        loc = ".".join(str(x) for x in err["loc"])
        parts.append(f"{loc}: {err['msg']}" if loc else err["msg"])
    return "; ".join(parts)


class ExposureConfig(BaseModel):
    """``[exposure]`` — which consumers a user-installed server is joined to.

    ADR-0015 §Decision 1/3/4: ``hermes`` and ``brain`` are honoured today
    (:mod:`hal0.mcp.hermes_join`); ``openwebui``/``opencode`` have no join
    mechanism in this codebase and setting either raises
    ``501 mcp.exposure_unsupported`` at the route layer rather than being
    silently ignored — the field still round-trips so a future join can
    read an operator's already-expressed intent.
    """

    model_config = {"extra": "forbid"}

    hermes: bool = Field(default=False)
    brain: bool = Field(default=False)
    openwebui: bool = Field(default=False)
    opencode: bool = Field(default=False)


class InstalledServer(BaseModel):
    """One user-installed MCP server's on-disk record.

    The shape is intentionally close to the prototype's catalog row so
    the dashboard can render an installed entry alongside a catalog one
    without a translation layer.
    """

    # A record's input carries [env] literals; keep them out of every
    # ValidationError rendering (see _validation_reason, #2304).
    model_config = {"hide_input_in_errors": True}

    id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    description: str = Field(default="")
    spec: str = Field(..., min_length=1)
    """Install spec — ``oci://``, ``npm:``, ``uvx:``, ``git+https://``, or
    a manifest URL. Stored verbatim so a future re-install / upgrade
    path can rerun the resolver."""
    transport: str = Field(default="stdio")
    """``stdio``, ``streamable-http``, or ``sse`` (ADR-0015). ``stdio``
    covers the most common path (npx/uvx packages) but has no
    supervisor yet — see ADR-0015's "Deferred: stdio supervisor"."""
    command: str = Field(default="")
    """Stdio launch command (e.g. ``npx``, ``uvx``). Unused until the
    stdio supervisor ships (ADR-0015); recorded now so a record doesn't
    need a schema migration when it does."""
    args: list[str] = Field(default_factory=list)
    """Stdio launch args. Same deferred-use note as ``command``."""
    url: str = Field(default="")
    """Connect URL for ``streamable-http``/``sse`` transports. Empty for
    ``stdio`` records."""
    tools: int = Field(default=0, ge=0, le=4096)
    resources: int = Field(default=0, ge=0)
    prompts: int = Field(default=0, ge=0)
    env: dict[str, str] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)
    """``env var name`` → ``key in /etc/hal0/api.env``. A *reference*,
    never a literal value (ADR-0015 §Decision 1) — resolved at call/probe
    time via ``os.environ``, which hal0-api keeps in lockstep with
    ``api.env`` on every secrets write (``routes/secrets.py``)."""
    tool_policy: ToolPolicy = Field(default_factory=ToolPolicy)
    """Reuses :class:`hal0.config.schema.ToolPolicy` verbatim — same
    allow/gated/blocked disjointness, same empty-by-default posture.
    Serialised under the TOML ``[tools]`` table (see
    :meth:`to_toml_dict`); named ``tool_policy`` on the model, not
    ``tools``, because the pre-existing ``tools`` field above is the
    advertised tool *count* every existing on-disk record already
    carries."""
    exposure: ExposureConfig = Field(default_factory=ExposureConfig)
    enabled: bool = Field(default=True)
    installed_at: str = Field(default="")
    """ISO-8601 UTC timestamp set on first write."""
    source_url: str | None = Field(default=None)
    """The manifest URL the install was resolved against, when applicable."""
    author: str = Field(default="user")
    verified: bool = Field(default=False)
    allow_insecure_http: bool = Field(default=False)
    """Explicit opt-in to send ``[secrets]``/``[env]`` header values over
    plaintext ``http://`` to a non-loopback host (#2304). Off by default;
    when set, :mod:`hal0.mcp.hermes_join` logs a warning on every render."""

    @field_validator("secrets")
    @classmethod
    def _secrets_reference_valid_names(cls, v: dict[str, str]) -> dict[str, str]:
        """Both sides of a ``[secrets]`` entry must be env-var-shaped.

        The left side becomes a literal env var name in the eventual
        process/header env; the right side must match the grammar
        ``/api/secrets`` enforces for names it will actually store
        (``routes/secrets.py:63``) — a mismatched reference here would
        otherwise resolve to ``None`` silently at call time.
        """
        bad = {k: val for k, val in v.items() if not _SECRET_KEY_RE.match(val)}
        if bad:
            raise ValueError(
                f"secrets values must reference a valid api.env key name "
                f"(^[A-Z][A-Z0-9_]{{0,63}}$): {sorted(bad)}"
            )
        return v

    @model_validator(mode="after")
    def _header_values_need_tls(self) -> InstalledServer:
        """Refuse a record that would send header values in clear text (#2304).

        In practice this fires on load: ``POST /install`` never writes header
        values, and ``[secrets]`` is set by editing the TOML. Such a record
        fails to load (``list_installed`` skips it with
        ``hal0.mcp.installed.bad_record``; ``get_installed`` returns
        ``mcp.record_malformed``), so PATCH and ``/test`` cannot reach it
        either — the message names the only ways out. Hermes keeps an entry
        it already joined for the server until the next registry mutation
        runs :func:`hal0.mcp.hermes_join.sync_exposure`.
        """
        exposure = None if self.allow_insecure_http else self.plaintext_header_exposure()
        if exposure is not None:
            record_path = _registry_dir() / f"{self.id}.toml"
            raise PydanticCustomError(
                "mcp_insecure_url",
                "{reason}",
                {
                    "reason": (
                        f"{_insecure_http_message(self.id, *exposure)}; the record is "
                        f"not loaded. Edit {record_path} to use an https:// url or a "
                        f"loopback host, set allow_insecure_http = true, or DELETE "
                        f"the server. Hermes keeps any entry it already joined for "
                        f"this server until the next MCP change that re-syncs it "
                        f"(install, uninstall, or a tools/exposure/enabled PATCH)."
                    )
                },
            )
        return self

    def plaintext_header_exposure(self) -> tuple[str, list[str]] | None:
        """``(host, header keys)`` when header values would cross a network
        in clear text, else ``None``. Ignores ``allow_insecure_http``.

        :func:`hal0.mcp.probe.build_headers` turns every ``[secrets]`` entry
        and every ``[env]`` literal into a request header, sent to ``url``
        by both the probe and Hermes (via :mod:`hal0.mcp.hermes_join`). That
        is only safe over ``https://`` or to a loopback host. An ``[env]``
        key with an empty value carries nothing and is not counted.
        """
        if self.transport not in ("streamable-http", "sse") or not self.url:
            return None
        keys = sorted(set(self.secrets) | {k for k, v in self.env.items() if v})
        if not keys:
            return None
        try:
            parts = urlsplit(self.url)
            host = parts.hostname or ""
        except ValueError:
            return (self.url, keys)
        if parts.scheme.lower() == "https" or is_loopback_destination(host):
            return None
        return (host or self.url, keys)

    def to_toml_dict(self) -> dict[str, Any]:
        """Serialise to a tomli_w-compatible dict (drops None values).

        ``tool_policy`` round-trips under the on-disk ``[tools]`` key —
        the model attribute is named differently only to avoid colliding
        with the pre-existing ``tools`` int-count field (see its
        docstring); the TOML shape matches ADR-0015's schema example
        exactly (``[tools]`` with ``allow``/``gated``/``blocked``).
        """
        d = self.model_dump(mode="python")
        tool_policy = d.pop("tool_policy")
        d["tools_count"] = d.pop("tools")
        d["tools"] = tool_policy
        return {k: v for k, v in d.items() if v is not None}

    @classmethod
    def from_toml_dict(cls, raw: dict[str, Any]) -> InstalledServer:
        """Inverse of :meth:`to_toml_dict` — undoes the ``tools`` rename.

        Tolerates the pre-#N on-disk shape (``tools`` as a bare int,
        no ``[tools]`` table) so every existing record still validates:
        that shape has no ``tools_count`` key, so ``tools`` is left as
        the int count and ``tool_policy`` falls back to its empty default.
        """
        raw = dict(raw)
        if isinstance(raw.get("tools"), dict):
            raw["tool_policy"] = raw.pop("tools")
            if "tools_count" in raw:
                raw["tools"] = raw.pop("tools_count")
            else:
                raw["tools"] = 0
        return cls.model_validate(raw)


# ── Registry surface ────────────────────────────────────────────────────────


def _registry_dir() -> Path:
    """Return ``/etc/hal0/mcp-servers/`` (or the HAL0_HOME-rooted equiv).

    Created on first write — list operations tolerate the directory not
    existing yet, so a fresh install reports zero installed servers
    without an error.
    """
    return cfg_paths.etc() / "mcp-servers"


def _registry_path(server_id: str) -> Path:
    """Resolve one server's record path, refusing anything outside the registry.

    THE single place a caller-supplied id becomes a filesystem path, so the
    barrier belongs here rather than at each entry point: :func:`_registry_lock`
    reaches this before ``patch_config``'s own :func:`get_installed` has
    validated anything, and it opens the returned path's sibling ``.lock`` with
    mode ``"w"`` — a truncating create. :func:`_validate_id`'s charset already
    makes traversal unrepresentable; the containment check states that as a
    property of the resolved path rather than of the id spelling, and is
    written as realpath-then-prefix-compare so the taint analysers reading this
    file can see the barrier too.
    """
    _validate_id(server_id)
    root = os.path.realpath(_registry_dir())
    candidate = os.path.realpath(os.path.join(root, f"{server_id}.toml"))
    if not candidate.startswith(root + os.sep) or os.path.dirname(candidate) != root:
        raise BadRequest(
            "server id does not resolve inside the MCP registry",
            code="mcp.id_invalid",
            details={"id": server_id},
        )
    return Path(candidate)


# Restrictive perms: TOML files contain the per-server ``env`` block, which
# is the canonical home for API keys for community MCP servers. Default
# umask (022) would leave them world-readable; we narrow both the directory
# and individual files explicitly after write.
_REGISTRY_DIR_MODE = 0o700
_REGISTRY_FILE_MODE = 0o600


def _harden_registry_perms(path: Path) -> None:
    """Tighten perms on the registry dir + a single record file.

    Called immediately after :func:`write_toml_atomic` so a record's env
    block (API keys) isn't world-readable even briefly. Uses chmod rather
    than passing a mode to mkdir because write_toml_atomic also creates
    the directory under default umask.
    """
    parent = path.parent
    with contextlib.suppress(OSError):
        parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(parent, _REGISTRY_DIR_MODE)
    with contextlib.suppress(OSError):
        os.chmod(path, _REGISTRY_FILE_MODE)


_ID_OK = set("abcdefghijklmnopqrstuvwxyz0123456789-_")


def _validate_id(server_id: str) -> None:
    """Enforce a tight id charset.

    The id becomes a filename + a URL path segment; restricting it
    saves us from quoting both call sites. Bundled-server ids are
    rejected up-front so an install can't shadow ``hal0-admin``.
    """
    if not server_id:
        raise BadRequest("server id is required", code="mcp.id_required")
    if len(server_id) > 64:
        raise BadRequest("server id too long (max 64)", code="mcp.id_too_long")
    bad = [c for c in server_id if c not in _ID_OK]
    if bad:
        raise BadRequest(
            "server id may contain only [a-z0-9_-]",
            code="mcp.id_invalid",
            details={"id": server_id, "bad_chars": sorted(set(bad))},
        )
    if server_id in BUNDLED_SERVER_IDS:
        raise Conflict(
            f"server id {server_id!r} is reserved for a bundled server",
            code="mcp.id_reserved",
        )


def list_installed() -> list[InstalledServer]:
    """Return every installed-server record, sorted by id.

    Missing dir → empty list. A malformed record is logged + skipped
    rather than crashing the dashboard — operators see the bad row
    via the journal, the rest of the page keeps working.
    """
    root = _registry_dir()
    if not root.exists():
        return []
    rows: list[InstalledServer] = []
    for p in sorted(root.glob("*.toml")):
        try:
            with p.open("rb") as f:
                raw = tomllib.load(f)
            rows.append(InstalledServer.from_toml_dict(raw))
            _warned_bad_records.pop(str(p), None)
        except (OSError, tomllib.TOMLDecodeError, ValidationError) as exc:
            _warn_bad_record(p, exc)
    return rows


#: ``path -> st_mtime_ns`` of malformed records already reported, so the
#: dashboard's polling of :func:`list_installed` logs each edit once.
_warned_bad_records: dict[str, int] = {}


def _warn_bad_record(path: Path, exc: Exception) -> None:
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = -1
    if _warned_bad_records.get(str(path)) == mtime:
        return
    _warned_bad_records[str(path)] = mtime
    error = _validation_reason(exc) if isinstance(exc, ValidationError) else str(exc)
    log.warning("hal0.mcp.installed.bad_record", path=str(path), error=error)


def get_installed(server_id: str) -> InstalledServer:
    """Return one installed-server record. Raises :class:`NotFound`."""
    _validate_id(server_id)
    path = _registry_path(server_id)
    if not path.exists():
        raise NotFound(
            f"MCP server {server_id!r} not installed",
            code="mcp.not_found",
            details={"server_id": server_id},
        )
    try:
        with path.open("rb") as f:
            raw = tomllib.load(f)
        return InstalledServer.from_toml_dict(raw)
    except (OSError, tomllib.TOMLDecodeError, ValidationError) as exc:
        raise BadRequest(
            f"installed-server record at {path} is malformed",
            code="mcp.record_malformed",
            details={
                "server_id": server_id,
                "reason": _validation_reason(exc) if isinstance(exc, ValidationError) else str(exc),
            },
        ) from exc


def _require_tls_for_header_values(record: InstalledServer) -> None:
    """Raise ``400 mcp.insecure_url`` for a record failing the #2304 gate.

    The model validator covers construction and load; this covers records
    built with ``model_copy`` (no validators), i.e. :func:`patch_config`.
    """
    exposure = None if record.allow_insecure_http else record.plaintext_header_exposure()
    if exposure is not None:
        host, keys = exposure
        raise BadRequest(
            f"{_insecure_http_message(record.id, host, keys)}; use an https:// url "
            f"or a loopback host, or set allow_insecure_http = true",
            code="mcp.insecure_url",
            details={"server_id": record.id, "host": host, "header_keys": keys},
        )


def install(record: InstalledServer) -> InstalledServer:
    """Write a new installed-server record. Raises :class:`Conflict` on dup.

    The caller is expected to have populated the record from a
    manifest fetch (:mod:`hal0.mcp.manifest`) or a hand-rolled spec —
    we don't re-resolve here, that's a separate route concern.
    """
    _validate_id(record.id)
    _require_tls_for_header_values(record)
    path = _registry_path(record.id)
    if path.exists():
        raise Conflict(
            f"MCP server {record.id!r} is already installed",
            code="mcp.already_installed",
            details={"server_id": record.id},
        )
    # Stamp installed_at if the caller didn't pre-fill it.
    if not record.installed_at:
        record = record.model_copy(update={"installed_at": datetime.now(tz=UTC).isoformat()})
    write_toml_atomic(path, record.to_toml_dict())
    _harden_registry_perms(path)
    log.info(
        "hal0.mcp.installed.added",
        server_id=record.id,
        spec=record.spec,
        transport=record.transport,
    )
    return record


def uninstall(server_id: str) -> None:
    """Remove the installed-server record. Raises :class:`NotFound`.

    Bundled servers can't be uninstalled — :func:`_validate_id` rejects
    those ids before the disk lookup. Tolerates the file disappearing
    between the existence check and the unlink (race) — the operation
    is still considered successful.
    """
    _validate_id(server_id)
    path = _registry_path(server_id)
    if not path.exists():
        raise NotFound(
            f"MCP server {server_id!r} not installed",
            code="mcp.not_found",
            details={"server_id": server_id},
        )
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
    log.info("hal0.mcp.installed.removed", server_id=server_id)


@contextlib.contextmanager
def _registry_lock(server_id: str) -> Iterator[None]:
    """Advisory exclusive lock serializing read-modify-write on one server's
    registry record (#382).

    Two concurrent ``patch_config`` calls would otherwise interleave
    read -> modify -> write and clobber each other's update. The lock is
    held on a sibling ``<record>.lock`` file for the duration of the RMW.
    """
    target = _registry_path(server_id)
    lock_path = target.parent / f"{target.name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def patch_config(
    server_id: str,
    *,
    env: dict[str, str] | None = None,
    secrets: dict[str, str] | None = None,
    tool_policy: ToolPolicy | None = None,
    exposure: ExposureConfig | None = None,
    enabled: bool | None = None,
) -> InstalledServer:
    """Merge env / secrets / tools / exposure / enabled overrides into a record.

    Every dict/model field replaces wholesale when supplied — a TOML
    file holds a single per-server block for each, so the route layer
    always sends the full intended set, not a delta (matches the
    pre-existing ``env`` contract). ``enabled`` flips the flag without
    touching anything else. Callers that also need the Hermes exposure
    join re-run (``tool_policy``/``exposure``/``enabled`` changes) are
    responsible for calling :mod:`hal0.mcp.hermes_join` themselves —
    this function only owns the on-disk record.
    """
    with _registry_lock(server_id):
        record = get_installed(server_id)
        updates: dict[str, Any] = {}
        if env is not None:
            # Coerce values to strings — pydantic validates the type but the
            # FastAPI body is permissive (numbers, bools, etc).
            updates["env"] = {k: str(v) for k, v in env.items()}
        if secrets is not None:
            updates["secrets"] = {k: str(v) for k, v in secrets.items()}
        if tool_policy is not None:
            updates["tool_policy"] = tool_policy
        if exposure is not None:
            updates["exposure"] = exposure
        if enabled is not None:
            updates["enabled"] = bool(enabled)
        if not updates:
            return record
        next_record = record.model_copy(update=updates)
        _require_tls_for_header_values(next_record)
        target_path = _registry_path(server_id)
        write_toml_atomic(target_path, next_record.to_toml_dict())
        _harden_registry_perms(target_path)
    log.info(
        "hal0.mcp.installed.patched",
        server_id=server_id,
        fields=sorted(updates.keys()),
    )
    return next_record


def list_enabled_exposed(*, target: str) -> list[InstalledServer]:
    """Installed, enabled records with ``exposure.<target>`` set.

    ``target`` is ``"hermes"`` or ``"brain"`` — the two joins ADR-0015
    wires. Used by :mod:`hal0.mcp.hermes_join` to compute its desired
    set without duplicating the enabled+exposure filter at each call site.
    """
    return [r for r in list_installed() if r.enabled and bool(getattr(r.exposure, target, False))]


__all__ = [
    "BUNDLED_SERVER_IDS",
    "ExposureConfig",
    "InstalledServer",
    "get_installed",
    "install",
    "is_loopback_destination",
    "list_enabled_exposed",
    "list_installed",
    "patch_config",
    "uninstall",
]
