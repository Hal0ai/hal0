from __future__ import annotations

from typing import Any

import pytest

from hal0.capabilities.catalog import models_for_capability
from hal0.capabilities.orchestrator import CapabilityOrchestrator
from hal0.errors import BadRequest
from hal0.model_fit import ModelFit


class FakeSlotManager:
    pass


def _orch() -> CapabilityOrchestrator:
    return CapabilityOrchestrator(slot_manager=FakeSlotManager())  # type: ignore[arg-type]


def test_validate_model_fit_blocks_wrong_model_class(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_models_for_capability(capability, registry=None):
        assert capability == "tts"
        return [
            {
                "id": "chat-model",
                "capabilities": ["chat"],
                "backends": [{"id": "cpu"}],
            }
        ]

    monkeypatch.setattr(
        "hal0.capabilities.orchestrator.models_for_capability",
        fake_models_for_capability,
    )

    with pytest.raises(BadRequest) as exc:
        _orch()._validate_model_in_catalog("voice", "tts", "chat-model", "cpu")

    assert exc.value.code == "capability.illegal_model_fit"
    assert exc.value.details["fit_reasons"][0] == "model.slot_type_mismatch"
    assert exc.value.details["slot_type"] == "tts"


def test_validate_model_fit_blocks_profile_unsupported_slot_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_models_for_capability(capability, registry=None):
        assert capability == "rerank"
        return [
            {
                "id": "reranker",
                "capabilities": ["rerank"],
                "backends": [{"id": "npu"}],
            }
        ]

    monkeypatch.setattr(
        "hal0.capabilities.orchestrator.models_for_capability",
        fake_models_for_capability,
    )

    with pytest.raises(BadRequest) as exc:
        _orch()._validate_model_in_catalog("embed", "rerank", "reranker", "npu")

    assert exc.value.code == "capability.illegal_model_fit"
    assert exc.value.details["profile"] == "flm"
    assert exc.value.details["fit_reasons"][0] == "profile.unsupported_slot_type"


def test_validate_model_fit_allows_npu_embedding(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_models_for_capability(capability, registry=None):
        assert capability == "embed"
        return [
            {
                "id": "embedder",
                "capabilities": ["embed"],
                "backends": [{"id": "npu"}],
            }
        ]

    monkeypatch.setattr(
        "hal0.capabilities.orchestrator.models_for_capability",
        fake_models_for_capability,
    )

    _orch()._validate_model_in_catalog("embed", "embed", "embedder", "npu")


def test_validate_model_fit_passes_registry_for_registry_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeRegistry:
        def has(self, model_id: str) -> bool:
            return model_id == "local-embedder"

    seen: dict[str, Any] = {}

    def fake_models_for_capability(capability, registry=None):
        return [
            {
                "id": "local-embedder",
                "capabilities": ["embed"],
                "backends": [{"id": "gpu-vulkan"}],
            }
        ]

    def fake_evaluate_model_fit(**kwargs):
        seen.update(kwargs)
        return ModelFit("allowed")

    monkeypatch.setattr(
        "hal0.capabilities.orchestrator.models_for_capability",
        fake_models_for_capability,
    )
    monkeypatch.setattr(
        "hal0.capabilities.orchestrator.evaluate_model_fit",
        fake_evaluate_model_fit,
    )

    orch = CapabilityOrchestrator(
        slot_manager=FakeSlotManager(),  # type: ignore[arg-type]
        registry=FakeRegistry(),  # type: ignore[arg-type]
    )
    orch._validate_model_in_catalog("embed", "embed", "local-embedder", "gpu-vulkan")

    assert seen["registry"] is orch._registry


def test_models_for_capability_filters_backends_blocked_by_model_fit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_flat_rows_for_capability(capability, *, registry=None):
        assert capability == "rerank"
        return [
            {
                "id": "reranker",
                "backend": "npu",
                "provider": "flm",
                "size_gb": 1.0,
                "capabilities": ["rerank"],
                "downloaded": True,
                "pullable": True,
            },
            {
                "id": "reranker",
                "backend": "gpu-vulkan",
                "provider": "llama-server",
                "size_gb": 1.0,
                "capabilities": ["rerank"],
                "downloaded": True,
                "pullable": True,
            },
        ]

    monkeypatch.setattr(
        "hal0.capabilities.catalog._flat_rows_for_capability",
        fake_flat_rows_for_capability,
    )

    rows = models_for_capability("rerank")

    assert len(rows) == 1
    assert rows[0]["id"] == "reranker"
    assert [backend["id"] for backend in rows[0]["backends"]] == ["gpu-vulkan"]
    assert rows[0]["backends"][0]["fit_status"] == "allowed"
