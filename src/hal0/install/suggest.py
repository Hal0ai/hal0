"""Hardware-driven curated-model suggestions per capability (spec §6.5).

Generalizes ``hardware/recommend.py`` (single primary pick) into a ranked
list per capability, for the ``hal0 setup`` slot steps. Picks come only from
``registry.curated.CURATED_MODELS`` so they validate against the registry as
soon as downloaded — we never invent model names.
"""

from __future__ import annotations

from dataclasses import dataclass

from hal0.config.schema import HardwareInfo
from hal0.install.profile_derive import derive_device, derive_profile
from hal0.registry.curated import CURATED_MODELS, CuratedModel


@dataclass(frozen=True)
class Suggestion:
    model_id: str
    display_name: str
    size_gb: float
    vram_gb_min: float
    context_length: int
    device: str | None
    profile: str | None
    capability: str
    bundle_only: bool
    recommended: bool = False


#: Which curated ``capability`` values satisfy a slot capability. "coder"
#: falls back to general chat models so a coder slot is never empty.
_CAP_MATCH: dict[str, tuple[str, ...]] = {
    "chat": ("chat",),
    "coder": ("coder", "chat"),
    "embed": ("embed",),
    "stt": ("asr",),
    "tts": ("tts",),
}


def _ram_gb(hw: HardwareInfo) -> float:
    return (hw.unified_memory_mb or hw.ram_mb) / 1024


def _is_coder(m: CuratedModel) -> bool:
    return m.capability == "coder" or "coder" in m.tags or "coder" in m.id.lower()


def suggest_models(
    capability: str, hw: HardwareInfo, *, limit: int = 3, prefer_coder: bool = False
) -> list[Suggestion]:
    """Return up to ``limit`` curated picks for ``capability`` that fit the
    detected RAM, largest-first, with exactly one marked ``recommended``."""
    wanted = _CAP_MATCH.get(capability, (capability,))
    ram = _ram_gb(hw)
    device = derive_device(capability, hw, npu_opt_in=True)
    profile = derive_profile(capability, device) if device else None

    cands = [
        m
        for m in CURATED_MODELS
        if not m.bundle_only and m.capability in wanted and m.vram_gb_min <= ram + 0.01
    ]
    if capability == "coder" and prefer_coder:
        cands.sort(key=lambda m: (not _is_coder(m), -m.vram_gb_min))
    else:
        cands.sort(key=lambda m: -m.vram_gb_min)  # largest-that-fits first

    picks = cands[:limit]
    return [
        Suggestion(
            model_id=m.id,
            display_name=m.display_name,
            size_gb=m.size_gb,
            vram_gb_min=m.vram_gb_min,
            context_length=m.context_length,
            device=device,
            profile=profile,
            capability=m.capability,
            bundle_only=m.bundle_only,
            recommended=(i == 0),
        )
        for i, m in enumerate(picks)
    ]


__all__ = ["Suggestion", "suggest_models"]
