"""Tests for hal0.bundles.schema — dataclass round-trip + serializer."""

from __future__ import annotations

import json

import pytest

from hal0.bundles.schema import (
    SCHEMA_VERSION,
    Bundle,
    BundleManifest,
    ModelEntry,
)


def _sample_bundle() -> Bundle:
    return Bundle(
        name="hal0-Pro",
        min_ram_gb=64,
        primary=ModelEntry(
            slot="chat.primary",
            model_name="Qwen3.6-27B-MTP",
            size_gb=18.8,
        ),
        coder=ModelEntry(
            slot="chat.coder",
            model_name="Qwen3-Coder-30B-A3B",
            size_gb=18.6,
            lru=True,
        ),
        aux=(
            ModelEntry(slot="embed", model_name="nomic-v1.5", size_gb=0.3),
            ModelEntry(slot="rerank", model_name="bge-reranker-v2-m3", size_gb=0.45),
        ),
        npu_trio_shown=True,
        npu_trio_optin=False,
        display_label="hal0-Pro",
        display_subtitle="64 GB+",
        vendor="hal0",
    )


def _sample_manifest() -> BundleManifest:
    return BundleManifest(
        schema_version=SCHEMA_VERSION,
        bundle=_sample_bundle(),
        omni={
            "kind": "collection.omni",
            "name": "hal0-Pro",
            "members": [{"model_name": "Qwen3.6-27B-MTP"}],
        },
        extra={},
    )


def test_model_entry_round_trip():
    entry = ModelEntry(slot="embed", model_name="nomic-v1.5", size_gb=0.3, lru=False)
    restored = ModelEntry.from_dict(entry.to_dict())
    assert restored == entry


def test_model_entry_lru_defaults_false():
    entry = ModelEntry.from_dict({"slot": "embed", "model_name": "x", "size_gb": 1.0})
    assert entry.lru is False


def test_bundle_round_trip():
    bundle = _sample_bundle()
    restored = Bundle.from_dict(bundle.to_dict())
    assert restored == bundle


def test_bundle_total_size_sums_all_entries():
    bundle = _sample_bundle()
    expected = 18.8 + 18.6 + 0.3 + 0.45
    assert bundle.total_size_gb == pytest.approx(expected)


def test_bundle_total_size_handles_missing_primary_and_coder():
    bundle = Bundle(
        name="empty",
        min_ram_gb=8,
        primary=None,
        coder=None,
        aux=(),
        npu_trio_shown=False,
        npu_trio_optin=False,
        display_label="empty",
        display_subtitle="",
        vendor="hal0",
    )
    assert bundle.total_size_gb == 0.0


def test_bundle_slug_is_lowercase():
    bundle = _sample_bundle()
    assert bundle.slug == "hal0-pro"


def test_bundle_to_dict_includes_total_size_for_consumer():
    bundle = _sample_bundle()
    data = bundle.to_dict()
    assert "total_size_gb" in data
    assert data["total_size_gb"] == pytest.approx(bundle.total_size_gb)


def test_manifest_round_trip_via_json():
    manifest = _sample_manifest()
    restored = BundleManifest.from_json(manifest.to_json())
    assert restored == manifest


def test_manifest_to_dict_exposes_hal0_and_omni_blocks():
    manifest = _sample_manifest()
    data = manifest.to_dict()
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["hal0"]["name"] == "hal0-Pro"
    assert data["omni"]["kind"] == "collection.omni"


def test_manifest_from_dict_rejects_missing_hal0_block():
    with pytest.raises(ValueError):
        BundleManifest.from_dict({"omni": {}})


def test_manifest_from_dict_rejects_missing_omni_block():
    with pytest.raises(ValueError):
        BundleManifest.from_dict({"hal0": _sample_bundle().to_dict()})


def test_manifest_preserves_extra_block():
    manifest = BundleManifest(
        schema_version=SCHEMA_VERSION,
        bundle=_sample_bundle(),
        omni={"kind": "collection.omni", "name": "x", "members": []},
        extra={"deprecated_by": "hal0-Pro-v2"},
    )
    restored = BundleManifest.from_json(manifest.to_json())
    assert restored.extra == {"deprecated_by": "hal0-Pro-v2"}


def test_manifest_from_path(tmp_path):
    manifest = _sample_manifest()
    path = tmp_path / "hal0-Pro.json"
    path.write_text(manifest.to_json(), encoding="utf-8")
    restored = BundleManifest.from_path(path)
    assert restored == manifest


def test_manifest_json_is_pretty_by_default():
    manifest = _sample_manifest()
    rendered = manifest.to_json()
    # Pretty-printed output has newlines; flatten compact output doesn't.
    assert "\n" in rendered
    # And it parses back cleanly.
    json.loads(rendered)
