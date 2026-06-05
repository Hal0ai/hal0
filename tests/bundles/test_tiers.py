"""Tests for hal0.bundles.tiers — locked bundle list + plan §8.2 contents.

The picker is shape-driven by these manifests; drift between the plan
table and the on-disk JSON would surface as confused operators (clicking
a tier and getting a different stack). These tests assert the exact
values from plan §8.2 / ADR-0010 so a future edit to one without the
other fails fast.
"""

from __future__ import annotations

import pytest

from hal0.bundles import tiers as bundle_tiers


def setup_function(_):
    bundle_tiers.reset_cache()


def test_bundles_locked_list_order():
    assert bundle_tiers.BUNDLES == (
        "hal0-Lite",
        "hal0-Default",
        "hal0-Pro",
        "hal0-Max",
        "LMX-Omni-52B-Halo",
    )


def test_load_all_bundles_returns_five_manifests():
    manifests = bundle_tiers.load_all_bundles()
    assert [m.bundle.name for m in manifests] == list(bundle_tiers.BUNDLES)


def test_load_bundle_rejects_unknown_name():
    with pytest.raises(ValueError):
        bundle_tiers.load_bundle("hal0-Gigantic")


def test_hal0_lite_matches_plan_table():
    m = bundle_tiers.load_bundle("hal0-Lite").bundle
    assert m.min_ram_gb == 16
    assert m.primary is not None
    assert m.primary.model_name == "qwen3.5-0.8b"
    assert m.primary.size_gb == pytest.approx(1.0)
    assert m.coder is None
    assert m.aux == ()
    assert m.npu_trio_shown is False
    assert m.vendor == "hal0"


def test_hal0_default_matches_plan_table():
    m = bundle_tiers.load_bundle("hal0-Default").bundle
    assert m.min_ram_gb == 32
    assert m.primary is not None and m.primary.model_name == "qwen3.5-9b"
    assert m.primary.size_gb == pytest.approx(6.9)
    assert m.coder is None
    aux_models = {entry.model_name for entry in m.aux}
    assert aux_models == {"nomic-embed-text-v1.5-q8_0", "Whisper-Tiny", "kokoro-v1"}
    assert m.npu_trio_shown is False


def test_hal0_pro_matches_plan_table():
    m = bundle_tiers.load_bundle("hal0-Pro").bundle
    assert m.min_ram_gb == 64
    assert m.primary is not None and m.primary.model_name == "Qwen3.6-27B-MTP-GGUF"
    assert m.primary.size_gb == pytest.approx(18.8)
    assert m.coder is not None and m.coder.model_name == "Qwen3-Coder-30B-A3B-Instruct-GGUF"
    assert m.coder.size_gb == pytest.approx(18.6)
    assert m.coder.lru is True
    aux_models = {entry.model_name for entry in m.aux}
    # +bge-reranker, +whisper-base, +sd-turbo (plus carryover embed +
    # kokoro:cpu — the "+" notation in plan §8.2 inherits Default's aux).
    assert "bge-reranker-v2-m3-q4_k_m" in aux_models
    assert "Whisper-Base" in aux_models
    assert "SD-Turbo-GGUF" in aux_models
    assert m.npu_trio_shown is True
    assert m.npu_trio_optin is False  # opt-in means user must tick the box


def test_hal0_max_matches_plan_table():
    m = bundle_tiers.load_bundle("hal0-Max").bundle
    assert m.min_ram_gb == 100
    assert m.primary is not None and m.primary.model_name == "Qwen3.6-35B-A3B-MTP-GGUF"
    assert m.primary.size_gb == pytest.approx(23.8)
    assert m.coder is not None and m.coder.model_name == "qwen3-coder-next"
    assert m.coder.size_gb == pytest.approx(48.0)
    assert m.coder.lru is True
    aux_models = {entry.model_name for entry in m.aux}
    assert "Whisper-Large-v3-Turbo" in aux_models
    assert "Flux-2-Klein-9B-GGUF" in aux_models
    assert m.npu_trio_shown is True


def test_lmx_kit_matches_plan_table():
    m = bundle_tiers.load_bundle("LMX-Omni-52B-Halo").bundle
    assert m.min_ram_gb == 100
    assert m.primary is not None and m.primary.model_name == "Qwen3.6-35B-A3B-MTP-GGUF"
    assert m.coder is None  # vendor kit doesn't ship a coder
    aux_models = {entry.model_name for entry in m.aux}
    assert aux_models == {"Whisper-Large-v3-Turbo", "kokoro-v1", "Flux-2-Klein-9B-GGUF"}
    assert m.vendor == "amd"
    assert m.npu_trio_shown is False  # kit doesn't expose the toggle


def test_list_bundle_summaries_matches_load_all_bundles():
    summaries = bundle_tiers.list_bundle_summaries()
    full = bundle_tiers.load_all_bundles()
    assert [s.name for s in summaries] == [m.bundle.name for m in full]


def test_intree_fallback_when_runtime_dir_missing(tmp_path, monkeypatch):
    """The dev install (no /var/lib/hal0/.../omni/) still serves manifests
    out of the in-tree installer/manifests/omni/ directory."""

    bundle_tiers.reset_cache()
    monkeypatch.delenv("HAL0_BUNDLES_DIR", raising=False)
    # Force HAL0_HOME at a fresh empty dir so the runtime path doesn't
    # exist — this is what triggers the fallback.
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    manifests = bundle_tiers.load_all_bundles()
    assert len(manifests) == 5


def test_override_dir_short_circuits_search(tmp_path, monkeypatch):
    bundle_tiers.reset_cache()
    # Write a minimal manifest for one tier in the override dir; we
    # expect the loader to use it without falling back.
    monkeypatch.setenv("HAL0_BUNDLES_DIR", str(tmp_path))
    payload = (
        '{"schema_version": 1, "hal0": {"name": "hal0-Lite", "min_ram_gb": 16, '
        '"primary": null, "coder": null, "aux": [], "npu_trio_shown": false, '
        '"npu_trio_optin": false, "display_label": "L", "display_subtitle": "", '
        '"vendor": "hal0"}, "omni": {"kind": "collection.omni", "name": "hal0-Lite", "members": []}}'
    )
    (tmp_path / "hal0-lite.json").write_text(payload, encoding="utf-8")
    m = bundle_tiers.load_bundle("hal0-Lite")
    assert m.bundle.primary is None  # came from the override, not the in-tree manifest
