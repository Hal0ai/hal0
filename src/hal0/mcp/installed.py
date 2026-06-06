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
* No process supervision yet — that lives in a follow-up ADR (see
  ADR-0013 §6 "Bootstrap path"). Installed servers report
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
import os
import tomllib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, Field, ValidationError

from hal0.config import paths as cfg_paths
from hal0.config.loader import write_toml_atomic
from hal0.errors import BadRequest, Conflict, NotFound

log = structlog.get_logger(__name__)


# Bundled mounts that can't be uninstalled via this registry. The FastAPI
# route layer guards against deletion + the registry itself never lists
# them; this set is exposed for the route's bundled-rejection branch.
BUNDLED_SERVER_IDS = frozenset({"hal0-admin", "hal0-memory"})


# ── Schema ──────────────────────────────────────────────────────────────────


class InstalledServer(BaseModel):
    """One user-installed MCP server's on-disk record.

    The shape is intentionally close to the prototype's catalog row so
    the dashboard can render an installed entry alongside a catalog one
    without a translation layer.
    """

    id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    description: str = Field(default="")
    spec: str = Field(..., min_length=1)
    """Install spec — ``oci://``, ``npm:``, ``uvx:``, ``git+https://``, or
    a manifest URL. Stored verbatim so a future re-install / upgrade
    path can rerun the resolver."""
    transport: str = Field(default="stdio")
    """``stdio`` or ``streamable-http``. ``stdio`` covers the most
    common path (npx/uvx packages) — the supervisor follow-up will
    actually honour this field."""
    tools: int = Field(default=0, ge=0, le=4096)
    resources: int = Field(default=0, ge=0)
    prompts: int = Field(default=0, ge=0)
    env: dict[str, str] = Field(default_factory=dict)
    enabled: bool = Field(default=True)
    installed_at: str = Field(default="")
    """ISO-8601 UTC timestamp set on first write."""
    source_url: str | None = Field(default=None)
    """The manifest URL the install was resolved against, when applicable."""
    author: str = Field(default="user")
    verified: bool = Field(default=False)

    def to_toml_dict(self) -> dict[str, Any]:
        """Serialise to a tomli_w-compatible dict (drops None values)."""
        d = self.model_dump(mode="python")
        return {k: v for k, v in d.items() if v is not None}


# ── Registry surface ────────────────────────────────────────────────────────


def _registry_dir() -> Path:
    """Return ``/etc/hal0/mcp-servers/`` (or the HAL0_HOME-rooted equiv).

    Created on first write — list operations tolerate the directory not
    existing yet, so a fresh install reports zero installed servers
    without an error.
    """
    return cfg_paths.etc() / "mcp-servers"


def _registry_path(server_id: str) -> Path:
    return _registry_dir() / f"{server_id}.toml"


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
            rows.append(InstalledServer.model_validate(raw))
        except (OSError, tomllib.TOMLDecodeError, ValidationError) as exc:
            log.warning(
                "hal0.mcp.installed.bad_record",
                path=str(p),
                error=str(exc),
            )
    return rows


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
        return InstalledServer.model_validate(raw)
    except (OSError, tomllib.TOMLDecodeError, ValidationError) as exc:
        raise BadRequest(
            f"installed-server record at {path} is malformed",
            code="mcp.record_malformed",
            details={"server_id": server_id, "reason": str(exc)},
        ) from exc


def install(record: InstalledServer) -> InstalledServer:
    """Write a new installed-server record. Raises :class:`Conflict` on dup.

    The caller is expected to have populated the record from a
    manifest fetch (:mod:`hal0.mcp.manifest`) or a hand-rolled spec —
    we don't re-resolve here, that's a separate route concern.
    """
    _validate_id(record.id)
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
    enabled: bool | None = None,
) -> InstalledServer:
    """Merge env / enabled overrides into a registry record.

    Replaces the record's ``env`` dict wholesale when supplied (a TOML
    file holds a single per-server env block, so the route layer always
    sends the full intended set, not a delta). ``enabled`` flips the
    flag without touching anything else.
    """
    with _registry_lock(server_id):
        record = get_installed(server_id)
        updates: dict[str, Any] = {}
        if env is not None:
            # Coerce values to strings — pydantic validates the type but the
            # FastAPI body is permissive (numbers, bools, etc).
            updates["env"] = {k: str(v) for k, v in env.items()}
        if enabled is not None:
            updates["enabled"] = bool(enabled)
        if not updates:
            return record
        next_record = record.model_copy(update=updates)
        target_path = _registry_path(server_id)
        write_toml_atomic(target_path, next_record.to_toml_dict())
        _harden_registry_perms(target_path)
    log.info(
        "hal0.mcp.installed.patched",
        server_id=server_id,
        fields=sorted(updates.keys()),
    )
    return next_record


__all__ = [
    "BUNDLED_SERVER_IDS",
    "InstalledServer",
    "get_installed",
    "install",
    "list_installed",
    "patch_config",
    "uninstall",
]
