"""Does ``extraction_max_tokens`` clear every bank's retain chunk size? (#3215)

hindsight-api refuses any retain whose resolved ``retain_chunk_size`` is not
strictly below ``retain_max_completion_tokens`` — the value hal0 writes from
``[memory.graph].extraction_max_tokens``. A bank can carry per-strategy chunk
sizes far above the engine default (the Hindsight coding-agent plugin sets 12000
on its strategies), so a cap that is fine for the ``shared`` bank can silently
fail every retain into another one.

This module reads each bank's resolved config (``GET
/v1/default/banks/{id}/config``) and reports the banks the cap does not clear.
It only ever warns: the engine may be down or mid-restart, in which case the
check is skipped and says so rather than reporting a clean bill.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

#: Bound on concurrent per-bank config reads against the local engine.
_CONCURRENCY = 8

RequestJson = Callable[[str, str], Awaitable[Any]]


@dataclass(frozen=True)
class BankChunk:
    """A bank whose largest retain chunk the completion cap does not clear."""

    bank_id: str
    chunk_size: int


@dataclass(frozen=True)
class ChunkAudit:
    """Result of one pass over the engine's banks.

    ``checked`` is how many bank configs were read; ``skipped`` how many could
    not be (engine error, unexpected shape). ``conflicts`` is the warning list.
    """

    max_tokens: int
    checked: int = 0
    skipped: int = 0
    conflicts: list[BankChunk] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        """``warn`` when a bank is not cleared, ``unknown`` when nothing could be
        read, else ``ok``."""
        if self.conflicts:
            return "warn"
        if self.checked == 0:
            return "unknown"
        return "ok"

    def message(self) -> str | None:
        if not self.conflicts:
            return None
        worst = max(c.chunk_size for c in self.conflicts)
        names = ", ".join(f"{c.bank_id} ({c.chunk_size})" for c in self.conflicts)
        return (
            f"[memory.graph].extraction_max_tokens = {self.max_tokens} is not above "
            f"the retain chunk size of {len(self.conflicts)} bank(s): {names}. The "
            "engine refuses every retain into them. Raise extraction_max_tokens "
            f"above {worst} (16384 fits the plugin's 12000), then use 'Retry failed'."
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "max_tokens": self.max_tokens,
            "checked": self.checked,
            "skipped": self.skipped,
            "conflicts": [
                {"bank_id": c.bank_id, "chunk_size": c.chunk_size} for c in self.conflicts
            ],
            "message": self.message(),
        }


def _as_int(value: Any) -> int | None:
    # bool is an int subclass; a True chunk size is a malformed config, not 1.
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def largest_retain_chunk(payload: Any) -> int | None:
    """The largest ``retain_chunk_size`` a bank config can resolve to.

    Looks at the resolved ``config.retain_chunk_size``, every
    ``retain_strategies.*.retain_chunk_size`` in the resolved config, and the
    same strategies under ``overrides`` (a strategy chunk size only appears
    there on some engine versions). ``None`` when the payload carries no chunk
    size at all.
    """
    if not isinstance(payload, dict):
        return None
    sizes: list[int] = []
    for section in (payload.get("config"), payload.get("overrides")):
        if not isinstance(section, dict):
            continue
        direct = _as_int(section.get("retain_chunk_size"))
        if direct is not None:
            sizes.append(direct)
        strategies = section.get("retain_strategies")
        if isinstance(strategies, dict):
            for strategy in strategies.values():
                if isinstance(strategy, dict):
                    size = _as_int(strategy.get("retain_chunk_size"))
                    if size is not None:
                        sizes.append(size)
    return max(sizes) if sizes else None


async def audit_bank_chunks(
    request_json: RequestJson, bank_ids: list[str], max_tokens: int
) -> ChunkAudit:
    """Read every bank's resolved config and collect the ones ``max_tokens`` does not clear.

    ``request_json(method, path)`` is the Hindsight client's. Never raises: a
    bank whose config cannot be read counts as ``skipped``.
    """
    sem = asyncio.Semaphore(_CONCURRENCY)

    async def _one(bank_id: str) -> tuple[str, int | None, bool]:
        async with sem:
            try:
                payload = await request_json(
                    "GET", f"/v1/default/banks/{quote(bank_id, safe='')}/config"
                )
            except Exception:
                return bank_id, None, False
        size = largest_retain_chunk(payload)
        return bank_id, size, size is not None

    rows = await asyncio.gather(*(_one(b) for b in bank_ids))
    conflicts = [
        BankChunk(bank_id, size)
        for bank_id, size, ok in rows
        # Strictly greater: the engine's own check is `max_completion_tokens > chunk`.
        if ok and size is not None and max_tokens <= size
    ]
    checked = sum(1 for _, _, ok in rows if ok)
    return ChunkAudit(
        max_tokens=max_tokens,
        checked=checked,
        skipped=len(rows) - checked,
        conflicts=conflicts,
    )
