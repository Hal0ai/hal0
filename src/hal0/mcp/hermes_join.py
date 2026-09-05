"""The Hermes/brain exposure join for user-installed MCP servers (ADR-0015).

`src/hal0/mcp/installed.py` persists the operator's install/tool/exposure
choices; this module is what makes an `exposure.hermes` (or `.brain`) flag
actually reach the agent. It has three jobs, run together as
:func:`sync_exposure` after every registry mutation that can change the
desired set (install, uninstall, and the ``PATCH /tools``/``/exposure``/
``/config`` routes):

1. Recompute the desired ``mcp_servers`` set for Hermes's main config and
   the hal0-brain profile config, and additively apply / surgically remove
   entries via :func:`hal0.agents.hermes_provision.apply_mcp_server_entries`
   / :func:`~hal0.agents.hermes_provision.apply_brain_profile_mcp_entries` —
   the two functions ADR-0015 added specifically so this module doesn't
   need a second "how to edit Hermes config" mechanism.
2. Mirror each exposed record's ``[tools]`` policy into the seed TOML at
   ``/etc/hal0/agents/hermes.toml`` so
   :meth:`hal0.agents.mcp_client.AgentMCPClient.classify` governs
   user-installed tools on the same axes as the two builtins.
3. Re-probe (via :mod:`hal0.mcp.probe`) exactly the servers whose desired
   membership changed, so a bad URL surfaces at the mutation that exposed
   it rather than at an agent's first turn.

Removal ownership: an id is only ever a removal candidate for step 1 when
it appears in the on-disk ownership manifest
(``/var/lib/hal0/mcp/hermes-managed.json`` — the ids this module wrote on
its own previous run). This is the format-agnostic equivalent of the
sentinel-comment idea in the field study's design note: YAML comments do
not survive the full parse/dump cycle hal0 already uses for
``config.yaml`` (see ADR-0015 "Rejected"), but a small side-car manifest
gives the identical guarantee — hal0 only ever deletes an entry it knows
it wrote, never an operator's hand-added block, which was never in the
manifest to begin with.

Every step here is best-effort: a failure is folded into the returned
report's ``errors`` rather than raised. The registry write that triggered
a sync has already succeeded by the time this runs (see call sites in
``routes/mcp.py``); the join self-heals on the next mutation or the next
``hal0 agent reprovision hermes --repair``.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from typing import Any

import structlog

from hal0.mcp.installed import InstalledServer, list_enabled_exposed
from hal0.mcp.probe import build_headers

log = structlog.get_logger(__name__)

#: hal0's two-consumer scope for v1 (ADR-0015 §Decision 3/4). ``openwebui``
#: and ``opencode`` have no join mechanism and are rejected at the route
#: layer with ``mcp.exposure_unsupported`` before this module ever runs.
JOIN_TARGETS = ("hermes", "brain")

#: Ownership manifest — the ids this module wrote into Hermes's
#: ``config.yaml``/seed TOML on its own last run, per target. Only ids
#: recorded here are ever candidates for removal (see module docstring).
_MANIFEST_PATH = Path("/var/lib/hal0/mcp/hermes-managed.json")

#: Agent identity tag applied to every user-installed server's headers —
#: mirrors the tag `_build_config_overlay` applies to the two builtins
#: (`hermes_provision.py:2017`) so audit rows attribute calls the same way.
_AGENT_ID = "hermes"


def _load_manifest() -> dict[str, list[str]]:
    if not _MANIFEST_PATH.exists():
        return {t: [] for t in JOIN_TARGETS}
    try:
        raw = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {t: [] for t in JOIN_TARGETS}
    return {t: sorted({str(x) for x in raw.get(t, [])}) for t in JOIN_TARGETS}


def _write_manifest(manifest: dict[str, list[str]]) -> None:
    with contextlib.suppress(OSError):
        _MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(_MANIFEST_PATH.parent, 0o700)
    tmp = _MANIFEST_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(_MANIFEST_PATH)
    with contextlib.suppress(OSError):
        os.chmod(_MANIFEST_PATH, 0o600)


def _desired_entries(target: str) -> dict[str, dict[str, Any]]:
    """Desired ``mcp_servers`` entries for ``target`` from the registry.

    Only ``streamable-http``/``sse`` records qualify — ``stdio`` has no
    supervisor yet (ADR-0015 "Deferred"), so a stdio record's
    ``exposure.hermes``/``.brain`` is rejected before it ever reaches
    here (see ``routes/mcp.py``'s ``PATCH /exposure`` handler).
    """
    entries: dict[str, dict[str, Any]] = {}
    for record in list_enabled_exposed(target=target):
        if record.transport not in ("streamable-http", "sse"):
            continue
        if not record.url:
            continue
        entries[record.id] = {
            "type": "sse" if record.transport == "sse" else "http",
            "url": record.url,
            "timeout": 60,
            "headers": build_headers(record, agent_id=_AGENT_ID),
        }
    return entries


def _seed_tools_block(records_by_id: dict[str, InstalledServer]) -> dict[str, Any]:
    """Build the ``[mcp.servers.<id>]`` seed-TOML mirror for classify().

    Shape matches ``hermes_provision._builtin_mcp_seed_servers`` (
    ``builtin: False`` distinguishes these from the two hal0-owned
    entries) plus the record's ``tool_policy`` under ``tools`` — the exact
    key :class:`hal0.config.schema.MCPServerConfig` reads as its
    ``ToolPolicy``.
    """
    return {
        sid: {
            "builtin": False,
            "enabled": True,
            "tools": record.tool_policy.model_dump(mode="python"),
        }
        for sid, record in records_by_id.items()
    }


def sync_exposure(*, only_server_id: str | None = None) -> dict[str, Any]:
    """Recompute + apply the Hermes/brain join for every exposed server.

    Called after any registry mutation that can change the desired set.
    ``only_server_id`` narrows the post-sync re-probe to one server (the
    one that just changed) — the sync itself always recomputes the full
    desired set, since an uninstall/disable can change what should be
    *removed* for servers other than the one just mutated.

    Returns a report dict merging both targets' results plus a `probe`
    key for the re-probed server(s); never raises — every failure is
    folded into `errors`.
    """
    from hal0.agents import hermes_provision

    manifest = _load_manifest()
    report: dict[str, Any] = {"hermes": {}, "brain": {}, "errors": []}

    all_records = {r.id: r for r in list_enabled_exposed(target="hermes")}
    all_records.update({r.id: r for r in list_enabled_exposed(target="brain")})

    for target in JOIN_TARGETS:
        desired = _desired_entries(target)
        previously_owned = set(manifest.get(target, []))
        remove_ids = sorted(previously_owned - set(desired))
        try:
            if target == "hermes":
                result = hermes_provision.apply_mcp_server_entries(desired, remove_ids=remove_ids)
            else:
                result = hermes_provision.apply_brain_profile_mcp_entries(
                    desired, remove_ids=remove_ids
                )
        except Exception as exc:
            log.warning("hal0.mcp.hermes_join.apply_failed", target=target, error=str(exc))
            result = {"errors": [str(exc)]}
            report["errors"].append(f"{target}: {exc}")
        report[target] = result
        manifest[target] = sorted(desired.keys())

    _write_manifest(manifest)

    # Seed TOML mirror (classify() source) always reflects hermes-exposed
    # records — brain has no separate policy axis, it shares the same
    # per-server ToolPolicy.
    hermes_desired_records = {
        sid: rec for sid, rec in all_records.items() if sid in _desired_entries("hermes")
    }
    try:
        _mirror_seed_toml(hermes_desired_records, previously_owned=set(manifest.get("hermes", [])))
    except Exception as exc:
        log.warning("hal0.mcp.hermes_join.seed_mirror_failed", error=str(exc))
        report["errors"].append(f"seed_toml: {exc}")

    changed_ids = set(report["hermes"].get("removed", [])) | set(report["brain"].get("removed", []))
    if only_server_id:
        changed_ids.add(only_server_id)
    probes: dict[str, Any] = {}
    for sid in sorted(changed_ids):
        record = all_records.get(sid)
        if record is None or record.transport not in ("streamable-http", "sse") or not record.url:
            continue
        with contextlib.suppress(Exception):
            from hal0.mcp.probe import probe_installed_server_sync

            probes[sid] = probe_installed_server_sync(record)
    report["probe"] = probes
    return report


def _mirror_seed_toml(
    records_by_id: dict[str, InstalledServer], *, previously_owned: set[str]
) -> None:
    """Merge/prune the ``[mcp.servers.<id>]`` seed-TOML mirror in place.

    Uses the same merge-never-clobber write hal0's own seed-TOML writer
    uses for the two builtin blocks — never a wholesale rewrite of
    ``/etc/hal0/agents/hermes.toml`` (``hermes_provision._write_seed_toml``,
    ADR-0015 §Decision 2 step 4).
    """
    import tomllib

    import tomli_w

    from hal0.agents.hermes_provision import INSTALL_SEED_PATH

    path = INSTALL_SEED_PATH
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            existing = {}

    mcp = dict(existing.get("mcp") or {})
    servers = dict(mcp.get("servers") or {})

    for sid in previously_owned - set(records_by_id):
        entry = servers.get(sid)
        if isinstance(entry, dict) and entry.get("builtin") is False:
            servers.pop(sid, None)

    servers.update(_seed_tools_block(records_by_id))

    mcp["servers"] = servers
    merged = dict(existing)
    merged["mcp"] = mcp

    from hal0.agents.hermes_provision import _atomic_write

    _atomic_write(path, tomli_w.dumps(merged))
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


__all__ = ["JOIN_TARGETS", "sync_exposure"]
