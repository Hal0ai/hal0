"""Seed a new box's default memory banks once the engine is there (#3518).

``install.sh`` seeds its default banks right after the memory engine first
answers ``/health``. When the engine is not healthy at install time (its
models could not be downloaded, say) that step is skipped, and nothing seeded
the banks afterwards: they were only created empty, with no retain mission, on
the first write.

hal0-api already notices the engine becoming available: a boot that found no
engine re-probes it and swaps it in (``SelfHealingMemoryProvider``).
:func:`seed_missing_banks` runs as one of that swap's heal hooks, with no
scheduler of its own.

That hook fires on every box whose hal0-api boots before its engine, not only
on a new one, so it writes only where there is nothing to disturb:

* A bank is seeded when it does not exist, or when it exists with no
  documents and no facts and its configuration sets no ``retain_mission``.
* A bank that holds anything is left alone whatever its configuration. An
  operator's bank is never reconfigured behind their back.
* Nothing is seeded while ``hal0 memory embedder switch`` is running or
  unfinished (:func:`switch_in_progress`); the hook reports "not done" and
  the heal loop runs it again once the switch has finished.

Banks are addressed the way hal0 addresses them, through the provider and the
store's bank layout (ADR-0058 decision 7, :mod:`hal0.memory.bank_layout`):

* two-bank: the platform bank (``[memory] bank``, ``hal0:shared`` by
  default) with the ``shared`` manifest, and the private bank
  (``[memory] private_bank``, ``hal0:private``) with the private one;
* the old layout: its platform bank alone. A store on its old layout is
  folded into the two banks by ``hal0 memory migrate fold-in``, not seeded
  into new ones, and its per-agent private banks are left as they are.

The manifests are the ones ``install.sh`` posts, keyed here by hal0 namespace.
The installer keeps them as shell literals so its own seeding works when the
Python side is broken, and ``tests/installer/test_install_first_start_embedder.py``
holds the two copies in lockstep.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

_SHARED = "shared"
_PRIVATE_HERMES = "private:hermes"

#: hal0 namespace -> the bank-template manifest a new box's bank starts with.
SEED_BANKS: dict[str, dict[str, Any]] = {
    _SHARED: {
        "version": "1",
        "bank": {
            "retain_mission": (
                "Extract technical decisions and rationale, gotchas and fixes, PRs and status "
                "changes, conventions, commands, endpoints, flags, incidents and resolutions, "
                "and cross-session coordination facts. Ignore routine edits, transient state, "
                "secrets, and anything already in git."
            ),
            "enable_observations": True,
            "disposition_skepticism": 4,
            "disposition_literalism": 4,
            "disposition_empathy": 1,
        },
    },
    _PRIVATE_HERMES: {
        "version": "1",
        "bank": {
            "retain_mission": (
                "This agent private working notes, scratch decisions, and per-task state. "
                "Never store shared facts here."
            ),
            "disposition_skepticism": 4,
            "disposition_literalism": 4,
            "disposition_empathy": 2,
        },
    },
}

SEEDED = "seeded"
SKIPPED = "skipped"
FAILED = "failed"


def seed_targets(provider: Any) -> dict[str, dict[str, Any]]:
    """Engine bank id -> manifest, for the banks this box's provider really uses.

    Two-bank: the platform and the private bank. Legacy: the platform bank
    only. Empty when the provider has no platform bank to name (it is not
    the Hindsight provider).
    """
    platform = getattr(provider, "platform_bank", None)
    if not isinstance(platform, str) or not platform:
        return {}
    targets = {platform: SEED_BANKS[_SHARED]}
    private = getattr(provider, "private_bank", None)
    if getattr(provider, "two_bank", False) is True and isinstance(private, str) and private:
        targets[private] = SEED_BANKS[_PRIVATE_HERMES]
    return targets


async def _get(client: Any, path: str, **kwargs: Any) -> Any | None:
    """A GET's JSON, or ``None`` when the engine says the bank is not there."""
    try:
        return await client.request_json("GET", path, **kwargs)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return None
        raise


async def _wants_seeding(client: Any, bank: str) -> bool:
    """True for a bank that is missing, or is empty and has no retain mission."""
    stats = await _get(client, f"/v1/default/banks/{bank}/stats", params={"refresh": "true"})
    if stats is None:
        return True
    if not isinstance(stats, dict):
        return False
    held = (stats.get("total_documents"), stats.get("total_nodes"))
    if any(not isinstance(count, int) or count != 0 for count in held):
        # Something is stored, or the counts cannot be read: leave it alone.
        return False
    config = await _get(client, f"/v1/default/banks/{bank}/config")
    if config is None:
        return True
    overrides = config.get("overrides") if isinstance(config, dict) else None
    return isinstance(overrides, dict) and not overrides.get("retain_mission")


async def seed_missing_banks(client: Any, targets: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Import each manifest in ``targets`` into a bank that :func:`_wants_seeding`.

    ``client`` is the Hindsight client (``request_json``) and ``targets`` comes
    from :func:`seed_targets`. Returns ``{bank: "seeded" | "skipped" |
    "failed"}``. Logs once, at info, when it seeded anything. Never raises.
    """
    out: dict[str, str] = {}
    for bank, manifest in targets.items():
        try:
            if not await _wants_seeding(client, bank):
                out[bank] = SKIPPED
                continue
            await client.request_json(
                "POST", f"/v1/default/banks/{bank}/import", json_body=manifest
            )
            out[bank] = SEEDED
        except Exception as exc:  # a heal hook must not take hal0-api down
            log.warning("hal0.memory.bank_seed_failed", bank=bank, error=str(exc))
            out[bank] = FAILED
    seeded = [bank for bank, status in out.items() if status == SEEDED]
    if seeded:
        log.info("hal0.memory.banks_seeded", banks=seeded)
    return out


def switch_in_progress() -> str | None:
    """Why the engine's banks must not be written by anything but the switch now, else ``None``.

    ``hal0 memory embedder switch`` running or unfinished, its write gate set,
    or the engine isolated on its private port. Never raises: a check that
    cannot be made reads as "in progress", so nothing is written on a guess.
    """
    try:
        from hal0.memory import embedder_switch

        return embedder_switch.blocking_reason()
    except Exception as exc:
        return f"whether an embedder switch is running could not be checked ({exc})"


def heal_hook(provider: Any) -> Any:
    """A heal hook for ``provider``: seed the banks, ``False`` to be run again on a failure."""

    async def _seed() -> bool:
        held = switch_in_progress()
        if held is not None:
            # The engine can be back at its normal address a moment before an
            # embedder switch lifts the write gate, and a bank that is half
            # way through its swap looks missing: seeding it then would put a
            # new bank where the switch is about to rename the rebuilt one.
            # Not done yet: the heal loop runs this again.
            log.info("hal0.memory.bank_seed_deferred", reason=held)
            return False
        client = getattr(provider, "hindsight_client", None)
        targets = seed_targets(provider)
        if client is None or not targets:
            return True  # healed onto something that is not Hindsight: nothing to seed
        return FAILED not in (await seed_missing_banks(client, targets)).values()

    return _seed


__all__ = ["SEED_BANKS", "heal_hook", "seed_missing_banks", "seed_targets", "switch_in_progress"]
