"""One-time conversion of pre-catalog upstream rows into curated, per-model rows.

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

# HAL0-SUNSET: v2.0.0  — one-time conversion of pre-catalog upstream rows; delete the module, its boot phase and its tests once every supported box has run it.

from __future__ import annotations

import asyncio
import os
import stat
import tomllib
from collections.abc import MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
from pydantic import ValidationError

from hal0.config import paths
from hal0.upstreams.catalog import canonical_id, enabled_ids, participates, reseed_curated_cache
from hal0.upstreams.discovery import DiscoveredModel, DiscoveryCache, parse_models_payload
from hal0.upstreams.filters import is_advertised
from hal0.upstreams.presets import get_preset
from hal0.upstreams.registry import Upstream, UpstreamRegistry

log = structlog.get_logger(__name__)

RETIRED_SUFFIX = ".retired"
BACKUP_SUFFIX = ".pre-convert"
_LOG_ID_CAP = 20


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
) -> tuple[list[dict[str, Any]], list[str]]:
    """Per-model rows for the patch, plus the reason class of each id left out.

    A ``providers.toml`` pick beats the old filters. An id the row schema would
    reject (blank, duplicate) is skipped rather than failing the whole row.
    """
    from hal0.config.schema import UpstreamModelEntry

    out: list[dict[str, Any]] = []
    skipped: list[str] = []
    seen: set[str] = set()
    for m in discovered:
        enabled = m.id in picked if picked else is_advertised(m.id, row.model_filters)
        data = {
            "id": m.id,
            "enabled": enabled,
            "type": m.type,
            "context": m.context,
            "price_in": m.price_in,
            "price_out": m.price_out,
            "price_unit": m.price_unit,
        }
        if m.id in seen:
            skipped.append("duplicate_id")
            continue
        try:
            UpstreamModelEntry.model_validate(data)
        except ValidationError as exc:
            skipped.append(exc.errors()[0]["type"])
            continue
        seen.add(m.id)
        out.append(data)
    return out, skipped


def _backup_upstreams(src: Path) -> None:
    """Copy ``src`` to ``<src>.pre-convert`` with the same mode; never overwrite."""
    if not src.exists():
        return  # rows live in memory only; there is nothing on disk to protect
    dest = src.with_name(src.name + BACKUP_SUFFIX)
    data = src.read_bytes()
    mode = stat.S_IMODE(src.stat().st_mode)
    try:
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    except FileExistsError:
        return
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    os.chmod(dest, mode)


def _warn_ambiguous_ids(registry: UpstreamRegistry) -> None:
    """Warn about bare ids enabled on several curated providers (they answer 400)."""
    owners: dict[str, list[str]] = {}
    for u in registry.list():
        if participates(u):
            for mid in enabled_ids(u):
                owners.setdefault(mid, []).append(u.name)
    clashes = sorted(mid for mid, names in owners.items() if len(names) > 1)
    for mid in clashes[:_LOG_ID_CAP]:
        log.warning(
            "upstreams.convert.ambiguous_id",
            model=mid,
            use=sorted(canonical_id(n, mid) for n in owners[mid]),
            hint="a bare id enabled on several providers needs the <provider>/<id> form",
        )
    if len(clashes) > _LOG_ID_CAP:
        log.warning(
            "upstreams.convert.ambiguous_ids_more",
            more=len(clashes) - _LOG_ID_CAP,
            message=f"and {len(clashes) - _LOG_ID_CAP} more",
        )


def _upstreams_all_curated(names: set[str]) -> bool:
    """On disk, are the rows ``names`` refers to all curated? ``False`` if the file won't parse."""
    from hal0.config.loader import load_upstreams_config

    try:
        cfg = load_upstreams_config()
    except Exception as exc:
        log.warning("upstreams.convert.providers_toml_kept", reason=type(exc).__name__)
        return False
    return all(e.curated for e in cfg.upstream if e.name in names)


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
    backup_ok: bool | None = None  # decided at the first write of the run
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
        rows, skipped = _model_rows(current, discovered, picked)
        if skipped:
            log.warning(
                "upstreams.convert.skipped_models",
                name=name,
                count=len(skipped),
                reasons=sorted(set(skipped)),
            )
        if not rows:
            deferred.append(name)
            log.info("upstreams.convert.deferred", name=name, reason="no_valid_models")
            continue
        if backup_ok is None:
            try:
                _backup_upstreams(paths.etc() / "upstreams.toml")
                backup_ok = True
            except OSError as exc:
                backup_ok = False
                log.warning("upstreams.convert.backup_failed", error=type(exc).__name__)
        if not backup_ok:
            deferred.append(name)
            log.info("upstreams.convert.deferred", name=name, reason="no_backup")
            continue
        try:
            registry.apply_persistent_patch(
                name, {"models": rows, "curated": True, "model_filters": None}
            )
        except Exception as exc:
            deferred.append(name)
            fields: dict[str, Any] = {"error": type(exc).__name__}
            if isinstance(exc, ValidationError):
                fields["locations"] = sorted(
                    {".".join(str(p) for p in e["loc"]) for e in exc.errors()}
                )[:_LOG_ID_CAP]
            log.warning("upstreams.convert.deferred", name=name, **fields)
            continue
        converted.append(name)
        if picked and selections is not None:
            folded += selections.entries.get(name, 0)
        if model_cache is not None:
            reseed_curated_cache(registry, model_cache, name)
        if discovery is not None:
            discovery.put(name, discovered)
        disabled = sum(1 for r in rows if not r["enabled"])
        if disabled:
            log.warning(
                "upstreams.convert.disabled_ids",
                name=name,
                disabled=disabled,
                hint="these ids routed before conversion and return 404 until enabled",
            )
        unmatched = sorted(picked - {m.id for m in discovered}) if picked else []
        log.info(
            "upstreams.convert.converted",
            name=name,
            models=len(rows),
            enabled=len(rows) - disabled,
            providers_toml=bool(picked),
            unmatched_picks=unmatched[:_LOG_ID_CAP],
        )

    if converted:
        _warn_ambiguous_ids(registry)

    if selections is not None and providers_toml is not None:
        waiting = ({u.name for u in candidates} & selections.names) - set(converted) - settled
        # The registry can hold slot and agent rows even when upstreams.toml failed to
        # parse, so ask the file itself: keep providers.toml unless every row it names is curated.
        if not waiting and _upstreams_all_curated(selections.names):
            _retire(providers_toml)

    return ConvertResult(tuple(converted), tuple(deferred), folded)


__all__ = ["ConvertResult", "convert_legacy_upstreams"]
