"""Seed the two default memory banks once the engine is there (#3518).

``install.sh`` seeds ``shared`` and ``private:hermes`` right after the memory
engine first answers ``/health``. When the engine is not healthy at install
time (its models could not be downloaded, say) that step is skipped, and
nothing seeded the banks afterwards: they were only created empty, with no
retain mission, on the first write.

hal0-api already notices the engine becoming available: a boot that found no
engine re-probes it and swaps it in (``SelfHealingMemoryProvider``).
:func:`seed_missing_banks` runs as one of that swap's heal hooks, so the banks
are seeded as soon as the engine is up, with no scheduler of its own.

It only fills what is missing. A bank whose configuration already carries a
``retain_mission`` override is left exactly as it is, so an operator's edits
are never written over and a second run does nothing.

The manifests are the ones ``install.sh`` posts; the installer keeps them as
shell literals so its own seeding works when the Python side is broken, and
``tests/installer/test_install_first_start_embedder.py`` holds the two copies
in lockstep.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

log = structlog.get_logger(__name__)

SEED_BANKS: dict[str, dict[str, Any]] = {
    "shared": {
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
    "private:hermes": {
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
PRESENT = "present"
FAILED = "failed"


async def _is_seeded(client: Any, bank: str) -> bool:
    """True when the bank's own configuration already sets a retain mission."""
    try:
        payload = await client.request_json("GET", f"/v1/default/banks/{bank}/config")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            return False
        raise
    overrides = payload.get("overrides") if isinstance(payload, dict) else None
    return isinstance(overrides, dict) and bool(overrides.get("retain_mission"))


async def seed_missing_banks(client: Any) -> dict[str, str]:
    """Import each :data:`SEED_BANKS` manifest into a bank that has no retain mission.

    ``client`` is the Hindsight client (``request_json``). Returns
    ``{bank: "seeded" | "present" | "failed"}``. Never raises.
    """
    out: dict[str, str] = {}
    for bank, manifest in SEED_BANKS.items():
        try:
            if await _is_seeded(client, bank):
                out[bank] = PRESENT
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


def heal_hook(provider: Any) -> Any:
    """A heal hook for ``provider``: seed the banks, ``False`` to be run again on a failure."""

    async def _seed() -> bool:
        client = getattr(provider, "hindsight_client", None)
        if client is None:
            return True  # healed onto something that is not Hindsight: nothing to seed
        return FAILED not in (await seed_missing_banks(client)).values()

    return _seed


__all__ = ["SEED_BANKS", "heal_hook", "seed_missing_banks"]
