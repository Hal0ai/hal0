"""Per-upstream model-advertising filters.

Implements docs/superpowers/specs/2026-07-06-upstream-model-filters.md:
an operator curates which of an upstream's models appear in the aggregated
``/v1/models`` catalog. Dispatch is deliberately unfiltered — an excluded
model stays reachable by explicit name.

Semantics
    A model id is advertised when
      (1) ``models`` and ``include`` are both empty (no include filter), OR
          the id is an exact member of ``models``, OR it matches at least
          one ``include`` glob;
    AND
      (2) it does not match any ``exclude`` glob.
    Exclude always overrides include.

Globs use :func:`fnmatch.fnmatchcase` (``*`` and ``?``; no regex).

This module must stay import-light (stdlib only) — it is shared by the
``/v1/models`` handler and the config layer without risking circular imports.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatchcase

__all__ = ["ModelFilters", "apply_filters", "is_advertised"]


@dataclass(frozen=True)
class ModelFilters:
    """Immutable runtime form of an [upstream.model_filters] table."""

    models: tuple[str, ...] = field(default_factory=tuple)
    """Exact model ids to allowlist (OR'd with `include`)."""

    include: tuple[str, ...] = field(default_factory=tuple)
    """Globs to include; empty together with `models` means include-all."""

    exclude: tuple[str, ...] = field(default_factory=tuple)
    """Globs to exclude; always wins over models/include."""

    @classmethod
    def from_lists(
        cls,
        models: Sequence[str] | None = None,
        include: Sequence[str] | None = None,
        exclude: Sequence[str] | None = None,
    ) -> ModelFilters:
        """Build from plain lists (e.g. a parsed UpstreamModelFilters dump)."""
        clean = lambda xs: tuple(s.strip() for s in (xs or ()) if s and s.strip())  # noqa: E731
        return cls(models=clean(models), include=clean(include), exclude=clean(exclude))

    def is_empty(self) -> bool:
        return not (self.models or self.include or self.exclude)


def is_advertised(model_id: str, filters: ModelFilters | None) -> bool:
    """Return True when `model_id` should appear in /v1/models."""
    if filters is None or filters.is_empty():
        return True
    if any(fnmatchcase(model_id, pat) for pat in filters.exclude):
        return False
    if not filters.models and not filters.include:
        return True
    if model_id in filters.models:
        return True
    return any(fnmatchcase(model_id, pat) for pat in filters.include)


def apply_filters(model_ids: Iterable[str], filters: ModelFilters | None) -> list[str]:
    """Filter an iterable of model ids, preserving order."""
    return [m for m in model_ids if is_advertised(m, filters)]
