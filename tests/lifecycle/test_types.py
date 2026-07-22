"""Frozen / serialization behaviour for resolution domain types."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hal0.lifecycle.types import (
    ActionRef,
    HostFacts,
    InstalledState,
    LifecycleOperation,
    OperatorIntent,
    ResolutionRequest,
    ResourceRef,
    UpdatePlan,
)


def test_action_ref_construction_and_frozen() -> None:
    ref = ActionRef(kind="slot.ensure", resource=ResourceRef(kind="slot", id="agent"))
    assert ref.kind == "slot.ensure"
    assert ref.resource is not None
    assert ref.resource.id == "agent"
    with pytest.raises(ValidationError):
        ref.kind = "other"  # type: ignore[misc]


def test_action_ref_json_round_trip() -> None:
    ref = ActionRef(kind="slot.ensure", resource=ResourceRef(kind="slot", id="agent"))
    assert ActionRef.model_validate_json(ref.model_dump_json()) == ref


def test_update_plan_construction_and_frozen() -> None:
    plan = UpdatePlan(
        operations=(),
        selections=(),
        warnings=("test warning",),
    )
    assert plan.warnings == ("test warning",)
    assert plan.download_estimate_bytes == 0
    with pytest.raises(ValidationError):
        plan.warnings = ("changed",)  # type: ignore[misc]


def test_update_plan_json_round_trip() -> None:
    plan = UpdatePlan(
        operations=(
            LifecycleOperation(
                kind="slot.ensure",
                resource=ResourceRef(kind="slot", id="agent"),
                detail="role=agent",
            ),
        ),
        selections=(),
        warnings=("example",),
    )
    assert UpdatePlan.model_validate_json(plan.model_dump_json()) == plan


def test_resolution_request_json_round_trip() -> None:
    host = HostFacts(host="amd-vulkan", device_class="gpu", backend="vulkan")
    request = ResolutionRequest.fresh_install(host=host)
    payload = request.model_dump_json()
    assert ResolutionRequest.model_validate_json(payload) == request


def test_host_facts_frozen_immutable() -> None:
    hf = HostFacts(host="cpu", device_class="cpu")
    with pytest.raises(ValidationError):
        hf.host = "changed"  # type: ignore[misc]


def test_operator_intent_frozen_immutable() -> None:
    intent = OperatorIntent(
        capabilities=frozenset({"chat"}),
        roles=frozenset({"brain"}),
    )
    with pytest.raises(ValidationError):
        intent.roles = frozenset()  # type: ignore[misc]


def test_installed_state_frozen_immutable() -> None:
    state = InstalledState()
    with pytest.raises(ValidationError):
        state.slots = ()  # type: ignore[misc]
