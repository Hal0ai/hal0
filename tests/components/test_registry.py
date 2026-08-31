"""Catalog shape: ids, order, and service joins stay stable."""

from __future__ import annotations

from hal0.components.registry import COMPONENTS, component_by_id
from hal0.services.registry import service_by_id


def test_catalog_ids_and_order() -> None:
    # Order is the converge order: hindsight LAST (slowest, service-stopping).
    assert [c.id for c in COMPONENTS] == ["openwebui", "runner-images", "hermes", "hindsight"]


def test_component_by_id() -> None:
    assert component_by_id("hindsight").kind == "venv"
    assert component_by_id("nope") is None


def test_service_joins_resolve() -> None:
    for comp in COMPONENTS:
        if comp.service_id is not None:
            assert service_by_id(comp.service_id) is not None, comp.id


def test_pinned_callables_return_strings() -> None:
    for comp in COMPONENTS:
        assert isinstance(comp.pinned(), str) and comp.pinned()
