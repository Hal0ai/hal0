"""Task 3.5: ComfyUI model selection helpers.

Public API:
    auto_selections() -> list[ModelVariant]
        Returns the default variant for every capability, in CAPABILITIES order.

    variant_for(capability_id, family) -> ModelVariant
        Looks up the variant with the given family within the capability.
        Raises KeyError for unknown capability or unknown family.
"""

from __future__ import annotations

from hal0.comfyui.capabilities import CAPABILITIES, ModelVariant, default_variant


def auto_selections() -> list[ModelVariant]:
    """Return the default ModelVariant for every capability in CAPABILITIES order."""
    return [default_variant(cap) for cap in CAPABILITIES.values()]


def variant_for(capability_id: str, family: str) -> ModelVariant:
    """Return the ModelVariant with *family* from the named capability.

    Raises:
        KeyError: if *capability_id* is not in CAPABILITIES, or if no
                  alternative in that capability has the given *family*.
    """
    cap = CAPABILITIES[capability_id]  # raises KeyError if unknown capability
    for v in cap.alternatives:
        if v.family == family:
            return v
    raise KeyError(f"{capability_id!r} has no variant with family {family!r}")
