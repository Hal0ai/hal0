"""Resolution engine tests: fresh install, setup, compare."""

from __future__ import annotations

import pytest

from hal0.lifecycle.catalog import LifecycleCatalog
from hal0.lifecycle.types import (
    HostFacts,
    InstalledState,
    OperatorIntent,
    ResolutionRequest,
    SlotState,
)


@pytest.fixture
def amd_host() -> HostFacts:
    return HostFacts(host="amd-vulkan", device_class="gpu", backend="vulkan")


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


@pytest.fixture
def installed_with_custom_pin() -> InstalledState:
    return InstalledState(
        slots=(
            SlotState(
                name="agent",
                role="agent",
                profile="agent",
                runner="rocmfpx",
                model=None,
                enabled=True,
            ),
        ),
        runners=frozenset({"rocmfpx"}),
    )


def test_fresh_install_plans_only_agent_and_default_runner(
    catalog: LifecycleCatalog, amd_host: HostFacts
) -> None:
    plan = catalog.resolve(ResolutionRequest.fresh_install(host=amd_host))
    assert [op.resource.id for op in plan.operations if op.kind == "slot.ensure"] == ["agent"]
    assert [op.kind for op in plan.operations] == ["slot.ensure"]
    assert plan.selection("agent.runner").selected is not None
    assert plan.selection("agent.runner").selected.id == "vulkan"
    assert not [op for op in plan.operations if op.kind == "model.pull"]


def test_brain_fallback_prefers_hal0_stock_before_minicpm(
    catalog: LifecycleCatalog, stock_host: HostFacts, hermes_intent: OperatorIntent
) -> None:
    plan = catalog.resolve(ResolutionRequest.setup(host=stock_host, intent=hermes_intent))
    decision = plan.selection("brain.model")
    assert decision.selected is not None
    assert decision.selected.id == "hal0-brain-stock-gguf"
    assert decision.rejected[0].reason_code == "runner.rocmfpx_unavailable"
    assert decision.rejected[0].id == "hal0-brain-rocmfpx-agent"


def test_existing_slot_pin_is_never_changed(
    catalog: LifecycleCatalog, installed_with_custom_pin: InstalledState
) -> None:
    plan = catalog.compare(installed_with_custom_pin)
    assert not [op for op in plan.operations if op.kind == "slot.runner.set"]


def test_resolve_model_selection_on_amd_host(
    catalog: LifecycleCatalog, hermes_intent: OperatorIntent
) -> None:
    host = HostFacts(host="amd-rocm", device_class="gpu", backend="rocm")
    plan = catalog.resolve(ResolutionRequest.setup(host=host, intent=hermes_intent))
    decision = plan.selection("brain.model")
    assert decision.selected is not None
    assert decision.selected.id == "hal0-brain-rocmfpx-agent"
    assert not decision.rejected
