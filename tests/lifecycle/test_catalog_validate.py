from __future__ import annotations

import pytest

from hal0.lifecycle.catalog import CatalogError, LifecycleCatalog


def test_bundled_catalog_is_valid(catalog: LifecycleCatalog) -> None:
    assert catalog.validate().errors == ()


def test_catalog_rejects_mutable_runner_image(catalog_source) -> None:
    catalog_source.runner("cpu")["package"] = "ghcr.io/hal0ai/cpu:latest"
    with pytest.raises(CatalogError, match="immutable digest"):
        LifecycleCatalog.from_documents(catalog_source.documents).validate()


def test_catalog_has_one_deterministic_default_per_host(catalog: LifecycleCatalog) -> None:
    report = catalog.validate()
    assert report.errors == ()
    assert catalog.default_runner(host="amd-vulkan", capability="chat").id


def test_rocmfpx_model_cannot_use_stock_llama(catalog: LifecycleCatalog) -> None:
    decision = catalog.compatibility(model="hal0-brain-rocmfpx-agent", runner="vulkan")
    assert decision.compatible is False
    assert decision.reason_code == "model_format.unsupported"


def test_catalog_reports_cross_reference_errors_in_stable_order(catalog_source) -> None:
    catalog_source.documents["profiles"]["profiles"][0]["runner_policy"] = "missing-policy"
    catalog_source.documents["bootstrap"]["bootstrap"]["initial_slots"].append(
        {
            "name": "search",
            "role": "search",
            "profile": None,
            "enabled": False,
            "model_policy": None,
            "ready_without_model": False,
        }
    )
    report = LifecycleCatalog.from_documents(catalog_source.documents).validate()
    assert report.errors == tuple(sorted(report.errors))
    assert any("missing-policy" in error for error in report.errors)
    assert any("initial slots" in error for error in report.errors)


def test_deprecated_runner_replacement_must_exist(catalog_source) -> None:
    runner = catalog_source.runner("cpu")
    runner["deprecated"] = True
    runner["replacement"] = "gone"
    report = LifecycleCatalog.from_documents(catalog_source.documents).validate()
    assert any("replacement" in error and "gone" in error for error in report.errors)


def test_every_supported_host_capability_has_a_default(catalog_source) -> None:
    catalog_source.runner("cpu")["default_for"] = []
    report = LifecycleCatalog.from_documents(catalog_source.documents).validate()
    assert "supported scope 'cpu/chat' has no default runner" in report.errors


def test_bootstrap_slot_profile_must_exist(catalog_source) -> None:
    catalog_source.documents["bootstrap"]["bootstrap"]["initial_slots"][0]["profile"] = "gone"
    report = LifecycleCatalog.from_documents(catalog_source.documents).validate()
    assert "bootstrap slot 'agent' references missing profile 'gone'" in report.errors


def test_unknown_authored_field_is_refused(catalog_source) -> None:
    catalog_source.documents["models"]["future_required_field"] = True
    with pytest.raises(CatalogError, match="unknown fields"):
        LifecycleCatalog.from_documents(catalog_source.documents)


def test_model_file_format_must_match_declared_model_format(catalog_source) -> None:
    catalog_source.documents["models"]["models"][0]["files"][0]["format"] = "stock-gguf"
    report = LifecycleCatalog.from_documents(catalog_source.documents).validate()
    assert any("file format" in error for error in report.errors)
