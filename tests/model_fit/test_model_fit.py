from __future__ import annotations

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
    profile = catalog.resolve("rocm")

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
    profile = ProfileCatalog().resolve("rocm")

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
    profile = ProfileCatalog().resolve("tts")

    fit = evaluate_model_fit(
        model_id="qwen3-4b",
        slot_type="llm",
        device="cpu",
        profile=profile,
        capabilities=["chat"],
    )

    assert fit.status == "blocked"
    assert "profile.unsupported_slot_type" in fit.reasons


def test_blocks_npu_profile_device_mismatch(tmp_hal0_home: str) -> None:
    profile = ProfileCatalog().resolve("flm")

    fit = evaluate_model_fit(
        model_id="gemma3:1b",
        slot_type="llm",
        device="gpu-rocm",
        profile=profile,
        capabilities=["chat"],
    )

    assert fit.status == "blocked"
    assert "profile.device_class_mismatch" in fit.reasons


def test_degrades_gpu_cpu_profile_mismatch(tmp_hal0_home: str) -> None:
    catalog = ProfileCatalog()
    profile = catalog.create(
        "cpu-llama",
        ProfileConfig(
            image="ghcr.io/x/llama-cpu:z",
            flags="",
            device_class="cpu",
        ),
    )

    fit = evaluate_model_fit(
        model_id="qwen3-4b",
        slot_type="llm",
        device="gpu-rocm",
        profile=profile,
        capabilities=["chat"],
    )

    assert fit.status == "degraded"
    assert fit.allowed is True
    assert fit.reasons == ("profile.device_class_mismatch",)
