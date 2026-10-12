"""ModelFit — contextual model/slot/device/profile compatibility.

``model_meta`` owns pure classification facts. ``ProfileCatalog`` owns
profile meaning. This module combines those facts into a verdict a caller
can show to an operator or use before writing slot config.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from hal0.model_meta import classify, is_resolvable
from hal0.profiles import ResolvedProfile

FitStatus = Literal["allowed", "blocked", "degraded"]

_CLASS_TO_SLOT_TYPE: dict[str, str] = {
    "chat": "llm",
    "embed": "embedding",
    "rerank": "reranking",
    "stt": "transcription",
    "tts": "tts",
    "img": "image",
}

_DEVICE_TO_PROFILE_CLASS: dict[str, str] = {
    "gpu-rocm": "gpu",
    "gpu-vulkan": "gpu",
    "cpu": "cpu",
    "npu": "npu",
}
#: Device class a single-purpose runtime family requires (llama-server runs
#: on any of them).
_FAMILY_CLASS: dict[str, str] = {"flm": "npu"}


@dataclass(frozen=True, slots=True)
class ModelFit:
    """Compatibility verdict for one model candidate."""

    status: FitStatus
    reasons: tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        return self.status in {"allowed", "degraded"}


def evaluate_model_fit(
    *,
    model_id: str,
    slot_type: str,
    device: str,
    profile: ResolvedProfile | None = None,
    registry: Any = None,
    capabilities: Any = None,
) -> ModelFit:
    """Return whether a model can run in a slot/device/profile context.

    The result is intentionally reason-bearing. Callers should use the
    stable reason strings for UI chips, logs, and tests instead of
    re-deriving compatibility from separate model/profile fields.
    """
    reasons: list[str] = []

    model_class = classify(model_id, capabilities=capabilities)
    expected_slot_type = _CLASS_TO_SLOT_TYPE.get(model_class, "llm")
    if expected_slot_type != slot_type:
        return ModelFit(
            "blocked",
            (
                "model.slot_type_mismatch",
                f"model_class={model_class}",
                f"expected_slot_type={expected_slot_type}",
            ),
        )

    if registry is not None and not is_resolvable(model_id, registry):
        return ModelFit("blocked", ("model.not_resolvable",))

    if profile is not None:
        if slot_type not in profile.supported_slot_types:
            return ModelFit(
                "blocked",
                (
                    "profile.unsupported_slot_type",
                    f"runtime_family={profile.runtime_family}",
                ),
            )

        expected_profile_class = _DEVICE_TO_PROFILE_CLASS.get(device)
        family_class = _FAMILY_CLASS.get(str(profile.runtime_family))
        if (
            expected_profile_class is not None
            and family_class is not None
            and family_class != expected_profile_class
        ):
            # A single-purpose engine (FLM on the NPU, ComfyUI on img) on the
            # wrong device routes to a different runtime family.
            return ModelFit(
                "blocked",
                (
                    "profile.device_class_mismatch",
                    f"device={device}",
                    f"runtime_family={profile.runtime_family}",
                ),
            )

    if reasons:
        return ModelFit("degraded", tuple(reasons))
    return ModelFit("allowed")


__all__ = ["FitStatus", "ModelFit", "evaluate_model_fit"]
