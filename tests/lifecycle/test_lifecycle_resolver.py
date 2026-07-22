"""Resolution engine tests: fresh install, setup, compare, host filtering, priority ranking."""

from __future__ import annotations

import pytest

from hal0.lifecycle.catalog import CatalogError, LifecycleCatalog
from hal0.lifecycle.types import (
    HostFacts,
    InstalledState,
    OperatorIntent,
    ResolutionPlan,
    ResolutionRequest,
    SlotState,
    UpdatePlan,
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


# ── Fresh-install tests ──────────────────────────────────────────────────


def test_fresh_install_plans_only_agent_and_default_runner(
    catalog: LifecycleCatalog, amd_host: HostFacts
) -> None:
    plan = catalog.resolve(ResolutionRequest.fresh_install(host=amd_host))
    assert [op.resource.id for op in plan.operations if op.kind == "slot.ensure"] == ["agent"]
    assert [op.kind for op in plan.operations] == ["slot.ensure"]
    assert plan.selection("agent.runner").selected is not None
    assert plan.selection("agent.runner").selected.id == "vulkan"
    assert not [op for op in plan.operations if op.kind == "model.pull"]


def test_fresh_install_on_cpu_has_no_model_pulls(catalog: LifecycleCatalog) -> None:
    host = HostFacts(host="cpu", device_class="cpu", backend=None)
    plan = catalog.resolve(ResolutionRequest.fresh_install(host=host))
    assert not [op for op in plan.operations if op.kind == "model.pull"]


# ── Brain fallback tests ─────────────────────────────────────────────────


def test_brain_fallback_prefers_hal0_stock_before_minicpm(
    catalog: LifecycleCatalog, stock_host: HostFacts, hermes_intent: OperatorIntent
) -> None:
    plan = catalog.resolve(ResolutionRequest.setup(host=stock_host, intent=hermes_intent))
    decision = plan.selection("brain.model")
    assert decision.selected is not None
    assert decision.selected.id == "hal0-brain-stock-gguf"
    assert decision.rejected[0].reason_code == "runner.rocmfpx_unavailable"
    assert decision.rejected[0].id == "hal0-brain-rocmfpx-agent"


def test_resolve_model_selection_on_amd_host(
    catalog: LifecycleCatalog, hermes_intent: OperatorIntent
) -> None:
    host = HostFacts(host="amd-rocm", device_class="gpu", backend="rocm")
    plan = catalog.resolve(ResolutionRequest.setup(host=host, intent=hermes_intent))
    decision = plan.selection("brain.model")
    assert decision.selected is not None
    assert decision.selected.id == "hal0-brain-rocmfpx-agent"
    assert not decision.rejected


def test_brain_fallback_rejects_with_clear_chain(
    catalog: LifecycleCatalog, stock_host: HostFacts, hermes_intent: OperatorIntent
) -> None:
    plan = catalog.resolve(ResolutionRequest.setup(host=stock_host, intent=hermes_intent))
    decision = plan.selection("brain.model")
    assert decision.selected is not None
    assert decision.selected.id == "hal0-brain-stock-gguf"
    rejected_ids = [r.id for r in decision.rejected]
    assert "hal0-brain-rocmfpx-agent" in rejected_ids


# ── Host filtering tests ─────────────────────────────────────────────────


def test_host_facts_mismatched_architecture_excluded(
    catalog: LifecycleCatalog, hermes_intent: OperatorIntent
) -> None:
    """A host with no matching architecture should find no runners."""
    host = HostFacts(
        host="amd-vulkan", device_class="gpu", backend="vulkan", architectures=frozenset({"arm64"})
    )
    plan = catalog.resolve(ResolutionRequest.setup(host=host, intent=hermes_intent))
    decision = plan.selection("brain.model")
    assert decision.selected is None


def test_backend_mismatch_cannot_select_default_rocm_runner(
    catalog: LifecycleCatalog,
) -> None:
    host = HostFacts(host="amd-rocm", device_class="gpu", backend="cuda")
    with pytest.raises(CatalogError, match="no default runner"):
        catalog.resolve(ResolutionRequest.fresh_install(host=host))


def test_backend_mismatch_cannot_select_brain_rocm_runner(
    catalog: LifecycleCatalog, hermes_intent: OperatorIntent
) -> None:
    host = HostFacts(host="amd-rocm", device_class="gpu", backend="cuda")
    plan = catalog.resolve(ResolutionRequest.setup(host=host, intent=hermes_intent))
    assert plan.selection("brain.model").selected is None


def test_device_class_mismatch_cannot_select_default_gpu_runner(
    catalog: LifecycleCatalog,
) -> None:
    host = HostFacts(host="amd-rocm", device_class="cpu", backend="rocm")
    with pytest.raises(CatalogError, match="no default runner"):
        catalog.resolve(ResolutionRequest.fresh_install(host=host))


def test_device_class_mismatch_cannot_select_brain_gpu_runner(
    catalog: LifecycleCatalog, hermes_intent: OperatorIntent
) -> None:
    host = HostFacts(host="amd-rocm", device_class="cpu", backend="rocm")
    plan = catalog.resolve(ResolutionRequest.setup(host=host, intent=hermes_intent))
    assert plan.selection("brain.model").selected is None


def test_consistent_rocm_host_resolves_default_and_brain(
    catalog: LifecycleCatalog, hermes_intent: OperatorIntent
) -> None:
    host = HostFacts(host="amd-rocm", device_class="gpu", backend="rocm")

    fresh = catalog.resolve(ResolutionRequest.fresh_install(host=host))
    assert fresh.selection("agent.runner").selected.id == "rocmfpx"

    setup = catalog.resolve(ResolutionRequest.setup(host=host, intent=hermes_intent))
    assert setup.selection("brain.model").selected.id == "hal0-brain-rocmfpx-agent"
    assert setup.selection("brain.runner").selected.id == "rocmfpx"


# ── Compare tests ────────────────────────────────────────────────────────


def test_existing_slot_pin_is_never_changed(
    catalog: LifecycleCatalog, installed_with_custom_pin: InstalledState
) -> None:
    plan = catalog.compare(installed_with_custom_pin)
    assert not [op for op in plan.operations if op.kind == "slot.runner.set"]


def test_compare_plans_missing_initial_slot(catalog: LifecycleCatalog) -> None:
    """When the bundled initial slot (agent) is missing, compare plans slot.ensure."""
    installed = InstalledState(slots=(), runners=frozenset())
    plan = catalog.compare(installed)
    assert isinstance(plan, UpdatePlan)
    assert [op.kind for op in plan.operations] == ["slot.ensure"]
    assert plan.operations[0].resource is not None
    assert plan.operations[0].resource.id == "agent"


def test_compare_preserves_operator_runner_pin(
    catalog: LifecycleCatalog,
) -> None:
    """When the agent slot exists with a pinned runner, compare does not change it."""
    installed = InstalledState(
        slots=(
            SlotState(name="agent", role="agent", profile="agent", runner="rocmfpx", enabled=True),
        ),
        runners=frozenset({"rocmfpx"}),
    )
    plan = catalog.compare(installed)
    assert not [op for op in plan.operations if op.kind == "slot.runner.set"]


# ── Determinism / serialization tests ────────────────────────────────────


def test_resolution_plan_is_deterministic_same_input(
    catalog: LifecycleCatalog, hermes_intent: OperatorIntent
) -> None:
    host = HostFacts(host="amd-vulkan", device_class="gpu", backend="vulkan")
    plan1 = catalog.resolve(ResolutionRequest.setup(host=host, intent=hermes_intent))
    plan2 = catalog.resolve(ResolutionRequest.setup(host=host, intent=hermes_intent))
    assert plan1.model_dump_json() == plan2.model_dump_json()


def test_resolution_plan_is_deterministic_reordered_candidates(
    catalog: LifecycleCatalog,
) -> None:
    """Determinism must hold regardless of input ordering — plan JSON is stable."""
    host = HostFacts(host="cpu", device_class="cpu", backend=None)
    intent1 = OperatorIntent(
        capabilities=frozenset({"chat", "tool-use"}),
        roles=frozenset({"brain"}),
    )
    intent2 = OperatorIntent(
        capabilities=frozenset({"tool-use", "chat"}),
        roles=frozenset({"brain"}),
    )
    plan1 = catalog.resolve(ResolutionRequest.setup(host=host, intent=intent1))
    plan2 = catalog.resolve(ResolutionRequest.setup(host=host, intent=intent2))
    assert plan1.model_dump_json() == plan2.model_dump_json()


def test_resolution_plan_json_serialization_round_trip(
    catalog: LifecycleCatalog, hermes_intent: OperatorIntent
) -> None:
    host = HostFacts(host="amd-rocm", device_class="gpu", backend="rocm")
    plan = catalog.resolve(ResolutionRequest.setup(host=host, intent=hermes_intent))
    assert ResolutionPlan.model_validate_json(plan.model_dump_json()) == plan
