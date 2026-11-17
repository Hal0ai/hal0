"""One-time conversion of legacy upstream rows into curated, per-model rows.

An install that predates per-model rows has remote upstreams with no model
rows (*uncurated*): they pass requests through and advertise whatever their
``model_filters`` allow. Once such a provider can be reached, this module lists
its models, writes one row per model (enabled when the old filters advertised
it) and flips the row to ``curated``, clearing the filters in the same write.

The conversion is deliberately timid. A provider that is unreachable or lists
nothing is *deferred*: its row is left byte-for-byte as it was and the next
boot tries again. Each row is one atomic persistent write, so a failure leaves
that row exactly as it was and never stops the others.

It also retires the old ``providers.toml`` store, whose per-provider model
selection is folded into the matching rows. The file is renamed, never deleted.
"""

from __future__ import annotations

import asyncio
import os
import tomllib
from collections.abc import MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from hal0.upstreams.catalog import reseed_curated_cache
from hal0.upstreams.discovery import DiscoveredModel, DiscoveryCache, parse_models_payload
from hal0.upstreams.filters import is_advertised
from hal0.upstreams.presets import get_preset
from hal0.upstreams.registry import Upstream, UpstreamRegistry

log = structlog.get_logger(__name__)

RETIRED_SUFFIX = ".retired"


@dataclass(frozen=True)
class ConvertResult:
    converted: tuple[str, ...]
    deferred: tuple[str, ...]
    """Provider unreachable, listed nothing, or the write failed; retried next boot."""
    folded_provider_rows: int
    """``providers.toml`` entries whose selection was applied to a converted row."""


@dataclass(frozen=True)
class _Selections:
    """What the old ``providers.toml`` store said, keyed by the row it names."""

    models: dict[str, frozenset[str]]
    """Row name -> models the operator picked (entries with an empty pick are absent)."""
    entries: dict[str, int]
    """Row name -> number of ``[[provider]]`` entries naming it."""

    @property
    def names(self) -> set[str]:
        return set(self.entries)


def _is_candidate(u: Upstream) -> bool:
    return u.kind == "remote" and not u.slot_name and not u.managed_by and not u.curated


def _read_selections(path: Path | None) -> _Selections | None:
    """Parse ``providers.toml``; ``None`` when absent or unreadable (file is left alone)."""
    if path is None or not path.is_file():
        return None
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        log.warning("upstreams.convert.providers_toml_unreadable", error=type(exc).__name__)
        return None
    items = raw.get("provider")
    if not isinstance(items, list):
        items = []
    models: dict[str, set[str]] = {}
    entries: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            name = item.get("catalog_id")
        if not isinstance(name, str) or not name.strip():
            continue
        name = name.strip()
        entries[name] = entries.get(name, 0) + 1
        picked = item.get("models")
        ids = {m for m in picked if isinstance(m, str) and m} if isinstance(picked, list) else set()
        if ids:
            models.setdefault(name, set()).update(ids)
    return _Selections({k: frozenset(v) for k, v in models.items()}, entries)


def _model_rows(
    row: Upstream, discovered: list[DiscoveredModel], picked: frozenset[str] | None
) -> list[dict[str, Any]]:
    """Per-model rows for the patch. A ``providers.toml`` pick beats the old filters."""
    out: list[dict[str, Any]] = []
    for m in discovered:
        enabled = m.id in picked if picked else is_advertised(m.id, row.model_filters)
        out.append(
            {
                "id": m.id,
                "enabled": enabled,
                "type": m.type,
                "context": m.context,
                "price_in": m.price_in,
                "price_out": m.price_out,
                "price_unit": m.price_unit,
            }
        )
    return out


def _retire(path: Path) -> None:
    target = path.with_name(path.name + RETIRED_SUFFIX)
    if target.exists():
        log.warning("upstreams.convert.providers_toml_retire_skipped", reason="retired_file_exists")
        return
    try:
        os.rename(path, target)
    except OSError as exc:
        log.warning("upstreams.convert.providers_toml_retire_failed", error=type(exc).__name__)
        return
    log.info("upstreams.convert.providers_toml_retired")


async def convert_legacy_upstreams(
    registry: UpstreamRegistry,
    *,
    providers_toml: Path | None,
    model_cache: MutableMapping[str, list[str]] | None = None,
    discovery: DiscoveryCache | None = None,
) -> ConvertResult:
    """Convert every reachable uncurated remote row; defer the rest.

    ``model_cache`` and ``discovery`` are the live dispatcher cache and the
    discovery cache: when given, a converted row's cache entry is reseeded from
    its enabled rows and its discovered models are stored, so routing leaves
    passthrough without a restart.
    """
    candidates = [u for u in registry.list() if _is_candidate(u)]
    selections = _read_selections(providers_toml)

    fetched = await asyncio.gather(
        *(registry.fetch_model_payload(u.name) for u in candidates), return_exceptions=True
    )

    converted: list[str] = []
    deferred: list[str] = []
    settled: set[str] = set()  # rows that need no further word from providers.toml
    folded = 0
    for original, payload in zip(candidates, fetched, strict=True):
        name = original.name
        current = registry.get(name)
        if current is None or not _is_candidate(current):
            settled.add(name)  # changed under us (curated, removed): not ours to touch
            continue
        if isinstance(payload, BaseException):
            payload = None
        discovered = parse_models_payload(payload, get_preset(current.catalog_id or ""))
        if not discovered:
            deferred.append(name)
            log.info("upstreams.convert.deferred", name=name, reason="no_models")
            continue
        picked = selections.models.get(name) if selections is not None else None
        rows = _model_rows(current, discovered, picked)
        try:
            registry.apply_persistent_patch(
                name, {"models": rows, "curated": True, "model_filters": None}
            )
        except Exception as exc:
            deferred.append(name)
            log.warning("upstreams.convert.deferred", name=name, error=type(exc).__name__)
            continue
        converted.append(name)
        if picked and selections is not None:
            folded += selections.entries.get(name, 0)
        if model_cache is not None:
            reseed_curated_cache(registry, model_cache, name)
        if discovery is not None:
            discovery.put(name, discovered)
        log.info(
            "upstreams.convert.converted",
            name=name,
            models=len(rows),
            enabled=sum(1 for r in rows if r["enabled"]),
            providers_toml=bool(picked),
            unmatched_picks=len(picked - {m.id for m in discovered}) if picked else 0,
        )

    if selections is not None and providers_toml is not None:
        waiting = ({u.name for u in candidates} & selections.names) - set(converted) - settled
        # An empty registry may just mean upstreams.toml failed to load; keep the file then.
        if not waiting and (registry.list() or not selections.entries):
            _retire(providers_toml)

    return ConvertResult(tuple(converted), tuple(deferred), folded)


__all__ = ["ConvertResult", "convert_legacy_upstreams"]
