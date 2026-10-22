"""Register one pinned ``kind="remote"`` upstream per Hermes profile the gateway serves.

The Hermes API server exposes each profile under ``/p/<profile>/``; this
module mirrors that set into hal0's :class:`UpstreamRegistry` so every
profile shows up as a model in ``/v1/models``. Entries are tagged
``managed_by="agent:hermes"``; operator-authored entries are never touched.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, MutableMapping
from pathlib import Path
from typing import Any

import structlog

from hal0.agents import hermes_api
from hal0.upstreams.registry import Upstream, UpstreamRegistry

log = structlog.get_logger(__name__)

MANAGED_BY = "agent:hermes"

_WARNED: set[str] = set()


def build_hermes_upstream(
    profile: str, *, host: str, port: int, owned: tuple[str, ...]
) -> Upstream:
    """The pinned remote :class:`Upstream` for ``profile``."""
    base = f"http://{host}:{port}"
    url = f"{base}/v1" if profile == hermes_api.DEFAULT_PROFILE else f"{base}/p/{profile}/v1"
    return Upstream(
        name=hermes_api.upstream_name_for(profile),
        kind="remote",
        url=url,
        auth_style="bearer",
        auth_value_env=hermes_api.env_key_name(profile),
        timeout_seconds=600,
        health_path="/health",
        advertise_models=True,
        hal0_peer=False,
        managed_by=MANAGED_BY,
        pinned=True,
        owned_models=owned,
        stream_idle_timeout_s=600,
        stream_total_timeout_s=1800,
    )


async def reconcile_hermes_upstreams(
    registry: UpstreamRegistry,
    *,
    home: Path | None = None,
    probe: Callable[[str], int] | None = None,
    environ: MutableMapping[str, str] = os.environ,
) -> dict[str, Any]:
    """Make the registry's Hermes-managed entries match the served profiles.

    The registry authenticates from the process environment (``os.environ``),
    so ``environ`` must be ``os.environ`` (the default) for live model ids to
    populate; any other mapping is only for tests inspecting what was set.
    """
    home = home if home is not None else hermes_api.hermes_home()
    host, port = hermes_api.api_server_endpoint(home)
    served = await asyncio.to_thread(hermes_api.served_profiles, home, probe=probe)

    registered: list[str] = []
    skipped: list[str] = []
    without_key: list[str] = []
    desired: dict[str, str] = {}  # upstream name -> env var

    for profile in served:
        key = hermes_api.api_key_for(profile, home)
        if key is None:
            without_key.append(profile)
            continue
        name = hermes_api.upstream_name_for(profile)
        env_name = hermes_api.env_key_name(profile)
        existing = registry.get(name)
        if existing is not None and existing.managed_by != MANAGED_BY:
            skipped.append(name)
            if name not in _WARNED:
                _WARNED.add(name)
                log.warning(
                    "hermes_upstreams.name_taken", name=name, managed_by=existing.managed_by
                )
            continue
        environ[env_name] = key
        desired[name] = env_name
        upstream = build_hermes_upstream(
            profile, host=host, port=port, owned=(hermes_api.model_id_for(profile),)
        )
        if existing is not None:
            registry.remove(name)
        registry.add(upstream)
        registered.append(name)

    removed: list[str] = []
    for u in registry.list():
        if u.managed_by == MANAGED_BY and u.name not in desired:
            registry.remove(u.name)
            environ.pop(u.auth_value_env, None)
            removed.append(u.name)

    for name in registered:
        ids = await registry.fetch_models(name)
        if ids:
            profile = hermes_api.profile_of_upstream(name) or hermes_api.DEFAULT_PROFILE
            owned = tuple(dict.fromkeys([hermes_api.model_id_for(profile), *ids]))
            registry.update(name, owned_models=owned)

    return {
        "registered": sorted(registered),
        "removed": sorted(removed),
        "skipped": sorted(skipped),
        "profiles_without_key": sorted(without_key),
    }
