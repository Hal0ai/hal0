"""Brain fallback chain edge cases."""

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


def test_brain_fallback_rejects_with_clear_chain(
    catalog: LifecycleCatalog, stock_host: HostFacts, hermes_intent: OperatorIntent
) -> None:
    plan = catalog.resolve(ResolutionRequest.setup(host=stock_host, intent=hermes_intent))
    decision = plan.selection("brain.model")
    assert decision.selected is not None
    assert decision.selected.id == "hal0-brain-stock-gguf"
    rejected_ids = [r.id for r in decision.rejected]
    assert "hal0-brain-rocmfpx-agent" in rejected_ids


def test_fresh_install_on_cpu_has_no_model_pulls(
    catalog: LifecycleCatalog
) -> None:
    host = HostFacts(host="cpu", device_class="cpu", backend=None)
    plan = catalog.resolve(ResolutionRequest.fresh_install(host=host))
    assert not [op for op in plan.operations if op.kind == "model.pull"]


def test_resolution_plan_is_deterministic(
    catalog: LifecycleCatalog, hermes_intent: OperatorIntent
) -> None:
    host = HostFacts(host="amd-vulkan", device_class="gpu", backend="vulkan")
    plan1 = catalog.resolve(ResolutionRequest.setup(host=host, intent=hermes_intent))
    plan2 = catalog.resolve(ResolutionRequest.setup(host=host, intent=hermes_intent))
    assert plan1.model_dump_json() == plan2.model_dump_json()
