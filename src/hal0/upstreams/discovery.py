"""Model discovery: parse a provider's ``/models`` payload, cache the result.

Pure parsing plus an in-memory TTL cache. This feeds the UI only; request
routing never depends on it. Field precedence is provider payload, then the
preset row with the same id, then a guess from the id.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

from hal0.upstreams.presets import Preset

DISCOVERY_TTL_S = 21600

_PER_MTOK = 1_000_000


@dataclass(frozen=True)
class DiscoveredModel:
    id: str
    type: str
    type_source: str  # "provider" | "preset" | "guess"
    context: int | None = None
    price_in: float | None = None
    price_out: float | None = None
    price_unit: str = "Mtok"
    recommended: bool = False


# First match wins.
_TYPE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("rerank", ("rerank",)),
    ("embed", ("embed", "bge-", "e5-")),
    ("stt", ("whisper", "transcribe", "asr", "stt")),
    ("tts", ("tts", "speech", "voice")),
    ("image", ("image", "dall-e", "flux", "sdxl")),
)


def guess_type(model_id: str) -> str:
    """Guess a model kind from its id; ``chat`` when nothing matches."""
    lowered = model_id.lower()
    for kind, needles in _TYPE_RULES:
        if any(n in lowered for n in needles):
            return kind
    return "chat"


def _context(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _price_per_mtok(value: Any) -> float | None:
    """USD-per-token (string or number) to USD per million tokens."""
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return None
    try:
        per_token = float(value)
    except (ValueError, OverflowError):
        return None
    if not math.isfinite(per_token) or per_token < 0:
        return None
    per_mtok = per_token * _PER_MTOK
    return per_mtok if math.isfinite(per_mtok) else None


def _entry_id(entry: Any) -> str | None:
    if isinstance(entry, str):
        return entry or None
    if isinstance(entry, dict):
        mid = entry.get("id")
        if isinstance(mid, str) and mid:
            return mid
    return None


def _entries(body: Any) -> list[Any]:
    data = body.get("data") if isinstance(body, dict) else body
    return data if isinstance(data, list) else []


def parse_models_payload(body: Any, preset: Preset | None) -> list[DiscoveredModel]:
    """Parse a ``/models`` body into models. Never raises; skips duplicate ids."""
    rows = {m.id: m for m in preset.models} if preset is not None else {}
    seen: set[str] = set()
    out: list[DiscoveredModel] = []
    for entry in _entries(body):
        mid = _entry_id(entry)
        if mid is None or mid in seen:
            continue
        seen.add(mid)
        raw: dict[str, Any] = entry if isinstance(entry, dict) else {}
        pricing = raw.get("pricing")
        pricing = pricing if isinstance(pricing, dict) else {}
        row = rows.get(mid)

        context = _context(raw.get("context_length"))
        price_in = _price_per_mtok(pricing.get("prompt"))
        price_out = _price_per_mtok(pricing.get("completion"))
        if row is not None:
            context = context if context is not None else row.context
            price_in = price_in if price_in is not None else row.price_in
            price_out = price_out if price_out is not None else row.price_out
            kind, source = row.type, "preset"
        else:
            kind, source = guess_type(mid), "guess"
        out.append(
            DiscoveredModel(
                id=mid,
                type=kind,
                type_source=source,
                context=context,
                price_in=price_in,
                price_out=price_out,
                price_unit=row.price_unit if row is not None else "Mtok",
                recommended=row.recommended if row is not None else False,
            )
        )
    return out


class DiscoveryCache:
    """In-memory per-upstream cache of discovered models (single event loop)."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[list[DiscoveredModel], float]] = {}

    def get(self, name: str) -> tuple[list[DiscoveredModel], float] | None:
        return self._entries.get(name)

    def put(self, name: str, models: list[DiscoveredModel], *, now: float | None = None) -> None:
        self._entries[name] = (models, time.time() if now is None else now)

    def is_stale(
        self, name: str, *, now: float | None = None, ttl_s: float = DISCOVERY_TTL_S
    ) -> bool:
        entry = self._entries.get(name)
        if entry is None:
            return True
        current = time.time() if now is None else now
        return current - entry[1] > ttl_s

    def drop(self, name: str) -> None:
        self._entries.pop(name, None)


__all__ = [
    "DISCOVERY_TTL_S",
    "DiscoveredModel",
    "DiscoveryCache",
    "guess_type",
    "parse_models_payload",
]
