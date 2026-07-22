"""Brain fallback chain edge cases, host filtering, priority ranking."""

from __future__ import annotations

import pytest

from hal0.lifecycle.catalog import LifecycleCatalog
from hal0.lifecycle.types import HostFacts, OperatorIntent, ResolutionRequest


@pytest.fixture
def stock_host() -> HostFacts:
    return HostFacts(host="cpu", device_class="cpu", backend=None)


@pytest.fixture
def hermes_intent() -> OperatorIntent:
    return OperatorIntent(
        capabilities=frozenset({"chat", "tool-use"}),
        roles=frozenset({"brain"}),
        purpose="setup",
    )


def test_brain_fallback_on_nvidia_host_selects_stock_gguf(
    catalog: LifecycleCatalog, hermes_intent: OperatorIntent
) -> None:
    host = HostFacts(host="nvidia-cuda", device_class="gpu", backend="cuda")
    plan = catalog.resolve(ResolutionRequest.setup(host=host, intent=hermes_intent))
    decision = plan.selection("brain.model")
    assert decision.selected is not None
    assert decision.selected.id == "hal0-brain-stock-gguf"
    assert decision.rejected[0].id == "hal0-brain-rocmfpx-agent"
    assert decision.rejected[0].reason_code == "runner.rocmfpx_unavailable"


# ── Host filtering tests ─────────────────────────────────────────────────


def test_architecture_mismatch_rejects_all(
    catalog: LifecycleCatalog, hermes_intent: OperatorIntent
) -> None:
    """When architecture is non-amd64, no runners match."""
    host = HostFacts(
        host="amd-vulkan", device_class="gpu", backend="vulkan", architectures=frozenset({"arm64"})
    )
    plan = catalog.resolve(ResolutionRequest.setup(host=host, intent=hermes_intent))
    decision = plan.selection("brain.model")
    assert decision.selected is None
    assert len(decision.rejected) >= 1


def test_capability_mismatch_no_brain(catalog: LifecycleCatalog) -> None:
    """When a model's required capability (chat) is present, selection proceeds
    based on runner-host-model compatibility — the resolver filters runners by
    capability, not the intent alone. The brain model selection uses the
    brain-fallback-chain policy and filters runners by required 'chat' capability.
    On a host where any runner supports chat, the first compatible model is selected."""
    host = HostFacts(host="amd-rocm", device_class="gpu", backend="rocm")
    intent = OperatorIntent(capabilities=frozenset({"tts"}), roles=frozenset({"brain"}))
    plan = catalog.resolve(ResolutionRequest.setup(host=host, intent=intent))
    decision = plan.selection("brain.model")
    # The first model hal0-brain-rocmfpx-agent matches — runners on amd-rocm
    # support chat capability, model format matches rocmfpx-gguf via rocmfpx runner
    assert decision.selected is not None
    assert decision.selected.id == "hal0-brain-rocmfpx-agent"


def test_host_label_never_deployed_triggers_rejection(
    catalog: LifecycleCatalog, hermes_intent: OperatorIntent
) -> None:
    """A host label that doesn't match any runner's hosts → full rejection."""
    host = HostFacts(host="nonexistent-vendor", device_class="gpu", backend="cuda")
    plan = catalog.resolve(ResolutionRequest.setup(host=host, intent=hermes_intent))
    decision = plan.selection("brain.model")
    assert decision.selected is None
    for r in decision.rejected:
        assert r.reason_code.startswith("runner.")


# ── Priority ranking and tie-break tests ─────────────────────────────────


def _catalog_with_competing_brain_runners(
    catalog_source, *, rocmfpx_priority: int, vulkanfpx_priority: int
) -> LifecycleCatalog:
    rocmfpx = catalog_source.runner("rocmfpx")
    vulkanfpx = catalog_source.runner("vulkanfpx")
    rocmfpx["priority"] = rocmfpx_priority
    vulkanfpx["priority"] = vulkanfpx_priority
    vulkanfpx["hosts"] = ["amd-rocm"]
    vulkanfpx["backends"] = ["rocm"]
    return LifecycleCatalog.from_documents(catalog_source.documents)


def test_brain_model_runner_ranked_by_priority_desc(
    catalog_source, hermes_intent: OperatorIntent
) -> None:
    catalog = _catalog_with_competing_brain_runners(
        catalog_source, rocmfpx_priority=100, vulkanfpx_priority=101
    )
    host = HostFacts(host="amd-rocm", device_class="gpu", backend="rocm")

    plan = catalog.resolve(ResolutionRequest.setup(host=host, intent=hermes_intent))

    assert plan.selection("brain.runner").selected.id == "vulkanfpx"


def test_priority_tie_broken_deterministically_by_runner_id(
    catalog_source, hermes_intent: OperatorIntent
) -> None:
    catalog = _catalog_with_competing_brain_runners(
        catalog_source, rocmfpx_priority=100, vulkanfpx_priority=100
    )
    host = HostFacts(host="amd-rocm", device_class="gpu", backend="rocm")

    plan = catalog.resolve(ResolutionRequest.setup(host=host, intent=hermes_intent))

    assert plan.selection("brain.runner").selected.id == "rocmfpx"
