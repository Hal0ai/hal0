"""Register one pinned ``kind="remote"`` upstream per keyed Hermes profile.

The Hermes API server exposes each profile under ``/p/<profile>/``; this
module mirrors hal0's knowledge of the profiles (their directories and keys)
into the :class:`UpstreamRegistry` so every profile shows up as a model in
``/v1/models``. Entries are tagged ``managed_by="agent:hermes"``;
operator-authored entries are never touched.

Ownership follows hal0's knowledge, not Hermes liveness: a gateway that is
down, restarting or answering 5xx keeps every keyed profile registered (its
requests fail fast with a 502 instead of falling through to the ``agent``
slot). A named profile is dropped only when the gateway is up and its
``/p/<profile>/health`` answers exactly 404.
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


def _candidate_profiles(home: Path, probe: Callable[[str], int] | None) -> list[str]:
    """``default`` plus every profile dir, minus those definitively not served.

    A named profile is left out only when the gateway's default ``/health``
    answers 200 and the profile's ``/p/<name>/health`` answers exactly 404.
    A transport error, a 5xx or a down gateway keeps it (Ruling 8).
    """
    alive = hermes_api.gateway_alive(home, probe=probe)
    out = [hermes_api.DEFAULT_PROFILE]
    for name in hermes_api.profile_dirs(home):
        if alive and hermes_api.profile_health(home, name, probe=probe) == 404:
            continue
        out.append(name)
    return out


async def reconcile_hermes_upstreams(
    registry: UpstreamRegistry,
    *,
    home: Path | None = None,
    probe: Callable[[str], int] | None = None,
    environ: MutableMapping[str, str] = os.environ,
) -> dict[str, Any]:
    """Make the registry's Hermes-managed entries match the keyed profiles.

    The registry authenticates from the process environment (``os.environ``),
    so ``environ`` must be ``os.environ`` (the default) for requests to the
    entries to authenticate; any other mapping is only for tests inspecting
    what was set.
    """
    home = home if home is not None else hermes_api.hermes_home()
    host, port = hermes_api.api_server_endpoint(home)
    candidates = await asyncio.to_thread(_candidate_profiles, home, probe)

    registered: list[str] = []
    skipped: list[str] = []
    without_key: list[str] = []
    desired: dict[str, str] = {}  # upstream name -> env var
    claimed: dict[str, str] = {}  # env var -> the profile that owns it this run
    collisions: set[str] = set()

    for profile in candidates:
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
        if claimed.setdefault(env_name, profile) != profile:
            # ``my-profile`` and ``my_profile`` map to one env var; sharing it
            # would let one key overwrite the other and cleanup pop both.
            collisions.add(profile)
            if env_name not in _WARNED:
                _WARNED.add(env_name)
                log.warning(
                    "hermes_upstreams.env_name_collision",
                    env_name=env_name,
                    profile=profile,
                    kept=claimed[env_name],
                )
            continue
        environ[env_name] = key
        desired[name] = env_name
        upstream = build_hermes_upstream(
            profile, host=host, port=port, owned=(hermes_api.model_id_for(profile, home),)
        )
        # Replace in place: a remove-then-add would open a window in which the
        # id has no owner and falls through to the ``agent`` slot.
        registry.upsert(upstream)
        registered.append(name)

    removed: list[str] = []
    for u in registry.list():
        if u.managed_by == MANAGED_BY and u.name not in desired:
            registry.remove(u.name)
            environ.pop(u.auth_value_env, None)
            removed.append(u.name)

    # ``owned_models`` stays the derived id (Ruling 9): the live ``/v1/models``
    # ids, which include Hermes's gateway-wide ``model_routes`` aliases, reach
    # the dispatcher's models cache and resolve by the normal ladder.
    return {
        "registered": sorted(registered),
        "removed": sorted(removed),
        "skipped": sorted(skipped),
        "profiles_without_key": sorted(without_key),
        "env_name_collisions": sorted(collisions),
    }
