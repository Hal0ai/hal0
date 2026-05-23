"""Typed dataclasses for bundle manifests.

Bundle manifests on disk are JSON files that combine two concerns:

1. A Lemonade-compatible ``collection.omni`` shape — a list of
   pre-registered ``model_name`` entries that Lemonade can consume as a
   single user-facing kit (see memory ``hal0_lemonade_omni_pattern``).
2. hal0-specific tier metadata — minimum RAM, the slot assignment for
   each model (which slot id receives which model), whether the FLM NPU
   trio should be shown / opt-in, and human-readable display fields.

Two dataclasses model this:

  - :class:`Bundle` is the in-memory shape consumed by the API + picker.
    It carries the hal0-specific fields (``name``, ``min_ram_gb``,
    ``primary``, ``coder``, ``aux``, ``npu_trio_shown``,
    ``npu_trio_optin``, ``display_*``) along with a reference to the
    backing :class:`BundleManifest`.
  - :class:`BundleManifest` is the full on-disk JSON shape. It nests a
    ``collection.omni`` block under ``omni`` plus a ``hal0`` block with
    the tier metadata.

The split is deliberate: future v0.3 work may publish bundles through
Lemonade's native collection endpoints without needing to re-derive the
hal0 metadata. Until then, both halves of the JSON travel together.

Serialisation is JSON via :func:`bundle_to_dict` / :func:`bundle_from_dict`.
We don't pull in pydantic for this because the manifests are a small,
static product surface and the surrounding code already standardises on
plain dataclasses for typed shapes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Fixed values from plan §8 / ADR-0010. Bumping these requires a v0.3 PR.
SCHEMA_VERSION: int = 1


@dataclass(frozen=True)
class ModelEntry:
    """One model assignment inside a bundle.

    ``slot`` is the hal0 slot id (``chat.primary``, ``chat.coder``,
    ``embed``, ``rerank``, ``stt``, ``tts``, ``img``, plus the FLM trio
    slots ``agent`` / ``stt-npu`` / ``embed-npu`` when present).
    ``model_name`` is the Lemonade registry id, matching what
    ``server_models.json`` advertises after ``hal0 registry sync``.
    ``size_gb`` is the on-disk pull size; informational only — the actual
    download honours whatever Lemonade resolves at pull time.
    ``lru`` marks a slot as eligible for LRU eviction (Pro/Max secondary
    models). The default is False for primary/aux entries.
    """

    slot: str
    model_name: str
    size_gb: float
    lru: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot,
            "model_name": self.model_name,
            "size_gb": self.size_gb,
            "lru": self.lru,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelEntry:
        return cls(
            slot=str(data["slot"]),
            model_name=str(data["model_name"]),
            size_gb=float(data["size_gb"]),
            lru=bool(data.get("lru", False)),
        )


@dataclass(frozen=True)
class Bundle:
    """The hal0 bundle metadata block.

    Maps 1:1 onto a row of the plan §8.2 table. Fields:

    - ``name`` is the canonical bundle identifier (``hal0-Lite``,
      ``hal0-Default``, ``hal0-Pro``, ``hal0-Max``, ``LMX-Omni-52B-Halo``).
      Used as the URL slug (lowercased) and the registry path.
    - ``min_ram_gb`` is the unified-RAM floor the picker filters on. A
      tier with ``min_ram_gb > host_ram_gb`` is rendered greyed-out with
      a tooltip explaining why.
    - ``primary``, ``coder`` and ``aux`` describe the slot assignments;
      ``coder`` is None for tiers that don't ship one.
    - ``npu_trio_shown`` toggles the picker's visibility of the FLM trio
      checkbox. ``npu_trio_optin`` is the default value of that
      checkbox when shown — for v0.2 both Pro and Max are
      ``shown=True / optin=False`` so the user must tick the box.
    - ``display_label`` is the user-facing card title;
      ``display_subtitle`` is the short tagline.
    - ``vendor`` distinguishes the four hal0-curated tiers from the
      LMX-Omni AMD-curated kit so the UI can group them under separate
      headings (plan §8.1 ASCII mockup).
    """

    name: str
    min_ram_gb: int
    primary: ModelEntry | None
    coder: ModelEntry | None
    aux: tuple[ModelEntry, ...]
    npu_trio_shown: bool
    npu_trio_optin: bool
    display_label: str
    display_subtitle: str
    vendor: str  # "hal0" or "amd"

    @property
    def slug(self) -> str:
        """URL-safe lowercase identifier used by the REST surface."""

        return self.name.lower()

    @property
    def total_size_gb(self) -> float:
        """Sum of declared model sizes for the install-time download estimate."""

        total = 0.0
        if self.primary is not None:
            total += self.primary.size_gb
        if self.coder is not None:
            total += self.coder.size_gb
        for entry in self.aux:
            total += entry.size_gb
        return total

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "min_ram_gb": self.min_ram_gb,
            "primary": self.primary.to_dict() if self.primary is not None else None,
            "coder": self.coder.to_dict() if self.coder is not None else None,
            "aux": [entry.to_dict() for entry in self.aux],
            "npu_trio_shown": self.npu_trio_shown,
            "npu_trio_optin": self.npu_trio_optin,
            "display_label": self.display_label,
            "display_subtitle": self.display_subtitle,
            "vendor": self.vendor,
            "total_size_gb": self.total_size_gb,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Bundle:
        primary_raw = data.get("primary")
        coder_raw = data.get("coder")
        return cls(
            name=str(data["name"]),
            min_ram_gb=int(data["min_ram_gb"]),
            primary=ModelEntry.from_dict(primary_raw) if primary_raw else None,
            coder=ModelEntry.from_dict(coder_raw) if coder_raw else None,
            aux=tuple(ModelEntry.from_dict(entry) for entry in data.get("aux", [])),
            npu_trio_shown=bool(data.get("npu_trio_shown", False)),
            npu_trio_optin=bool(data.get("npu_trio_optin", False)),
            display_label=str(data.get("display_label", data["name"])),
            display_subtitle=str(data.get("display_subtitle", "")),
            vendor=str(data.get("vendor", "hal0")),
        )


@dataclass(frozen=True)
class BundleManifest:
    """The full on-disk bundle JSON shape.

    The ``omni`` block mirrors the Lemonade ``collection.omni`` manifest
    (kind + name + members[]), and the ``hal0`` block carries the
    :class:`Bundle` metadata. ``schema_version`` lets future formats
    add fields without breaking older installs.
    """

    schema_version: int
    bundle: Bundle
    omni: dict[str, Any]
    # Free-form additional fields preserved on round-trip (e.g. release
    # notes, deprecated-by markers in future revisions).
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "schema_version": self.schema_version,
            "hal0": self.bundle.to_dict(),
            "omni": self.omni,
        }
        if self.extra:
            out["extra"] = dict(self.extra)
        return out

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BundleManifest:
        if "hal0" not in data:
            raise ValueError("bundle manifest missing required 'hal0' block")
        if "omni" not in data:
            raise ValueError("bundle manifest missing required 'omni' block")
        return cls(
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
            bundle=Bundle.from_dict(data["hal0"]),
            omni=dict(data["omni"]),
            extra=dict(data.get("extra", {})),
        )

    @classmethod
    def from_json(cls, raw: str) -> BundleManifest:
        return cls.from_dict(json.loads(raw))

    @classmethod
    def from_path(cls, path: Path) -> BundleManifest:
        return cls.from_json(path.read_text(encoding="utf-8"))


__all__ = [
    "SCHEMA_VERSION",
    "Bundle",
    "BundleManifest",
    "ModelEntry",
]
