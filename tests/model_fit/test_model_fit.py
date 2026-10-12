from __future__ import annotations

import pytest

from hal0.config.schema import ProfileConfig
from hal0.model_fit import evaluate_model_fit
from hal0.profiles import ProfileCatalog


class FakeRegistry:
    def __init__(self, model_ids: set[str]) -> None:
        self._model_ids = model_ids

    def has(self, model_id: str) -> bool:
        return model_id in self._model_ids


def test_allows_matching_llm_gpu_profile(tmp_hal0_home: str) -> None:
    catalog = ProfileCatalog()
    profile = catalog.resolve("chat")

    fit = evaluate_model_fit(
        model_id="qwen3-4b",
        slot_type="llm",
        device="gpu-rocm",
        profile=profile,
        registry=FakeRegistry({"qwen3-4b"}),
        capabilities=["chat"],
    )

    assert fit.status == "allowed"
    assert fit.allowed is True


def test_blocks_model_slot_type_mismatch(tmp_hal0_home: str) -> None:
    profile = ProfileCatalog().resolve("chat")

    fit = evaluate_model_fit(
        model_id="nomic-embed",
        slot_type="llm",
        device="gpu-rocm",
        profile=profile,
        capabilities=["embed"],
    )

    assert fit.status == "blocked"
    assert "model.slot_type_mismatch" in fit.reasons


def test_blocks_profile_slot_type_mismatch(tmp_hal0_home: str) -> None:
    profile = ProfileCatalog().resolve("kokoro")

    fit = evaluate_model_fit(
        model_id="qwen3-4b",
        slot_type="llm",
        device="cpu",
        profile=profile,
        capabilities=["chat"],
    )

    assert fit.status == "blocked"
    assert "profile.unsupported_slot_type" in fit.reasons


def test_blocks_flm_profile_off_the_npu(tmp_hal0_home: str) -> None:
    """A profile selects no hardware (ADR-0029); the one device rule left is
    the runtime family's: FLM runs only on the NPU."""
    profile = ProfileCatalog().resolve("flm")

    fit = evaluate_model_fit(
        model_id="gemma3:1b",
        slot_type="llm",
        device="gpu-rocm",
        profile=profile,
        capabilities=["chat"],
    )

    assert fit.status == "blocked"
    assert fit.reasons[0] == "profile.device_class_mismatch"
    assert "runtime_family=flm" in fit.reasons


def test_allows_flm_profile_on_the_npu(tmp_hal0_home: str) -> None:
    fit = evaluate_model_fit(
        model_id="gemma3:1b",
        slot_type="llm",
        device="npu",
        profile=ProfileCatalog().resolve("flm"),
        capabilities=["chat"],
    )

    assert fit.status == "allowed"


@pytest.mark.parametrize("device", ["gpu-rocm", "gpu-vulkan", "cpu"])
def test_llama_profile_fits_every_llama_device(tmp_hal0_home: str, device: str) -> None:
    """A llama-server profile is device-agnostic: there is no gpu/cpu
    "degraded" verdict any more, because a profile carries no device class."""
    profile = ProfileCatalog().create("tuned", ProfileConfig(flags="-fa auto", ctx_size=8192))

    fit = evaluate_model_fit(
        model_id="qwen3-4b",
        slot_type="llm",
        device=device,
        profile=profile,
        capabilities=["chat"],
    )

    assert fit.status == "allowed"
    assert fit.reasons == ()
