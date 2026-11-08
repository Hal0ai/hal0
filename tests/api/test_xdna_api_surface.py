"""Tests for the xdna backend API surface: slot payload, config write,
runtime wiring (mount + env) and hardware stats probes."""

from __future__ import annotations

import pytest

from hal0.config.schema import XdnaConfig
from hal0.errors import BadRequest, Hal0Error
from hal0.hardware.stats import HardwareStats
from hal0.slot_view import _is_xdna_cfg, _npu_holder_names, xdna_enrichment
from hal0.slots.config_write import guard_and_normalize_xdna

# ── slot_view.xdna_enrichment ───────────────────────────────────────────────


def test_xdna_enrichment_skips_non_xdna_slots():
    configs = [{"name": "llama1", "binary": "llama-server"}]
    assert xdna_enrichment(configs) == {}


def test_xdna_enrichment_adds_fields_for_xdna_slot():
    configs = [
        {"name": "xd1", "binary": "xdna", "xdna": {"pmode": "balanced"}},
        {"name": "other", "binary": "xdna"},
    ]
    out = xdna_enrichment(configs)
    assert set(out.keys()) == {"xd1", "other"}
    entry = out["xd1"]
    assert entry["pmode"] == "balanced"
    assert entry["xdna_path"] is None  # no model bound / no registry
    assert entry["xdna_reason"] is None
    assert entry["npu_shared_with"] == ["other"]


def test_xdna_enrichment_null_pmode_when_unset():
    configs = [{"name": "xd1", "binary": "xdna"}]
    out = xdna_enrichment(configs)
    assert out["xd1"]["pmode"] is None


def test_npu_holder_names_includes_flm_anchor():
    configs = [
        {"name": "xd1", "binary": "xdna"},
        {
            "name": "flm1",
            "binary": "flm",
            "device": "npu",
            "type": "llm",
            "model": {"default": "some-model"},
            "npu": {"chat": True},
        },
    ]
    assert _npu_holder_names(configs, exclude="xd1") == ["flm1"]


def test_is_xdna_cfg():
    assert _is_xdna_cfg({"binary": "xdna"})
    assert not _is_xdna_cfg({"binary": "llama-server"})
    assert not _is_xdna_cfg({})


# ── config_write.guard_and_normalize_xdna ───────────────────────────────────


def test_guard_xdna_noop_without_update():
    merged = {"name": "s1", "binary": "xdna"}
    guard_and_normalize_xdna(merged, {})
    assert "xdna" not in merged


def test_guard_xdna_clears_on_null():
    merged = {"name": "s1", "binary": "xdna", "xdna": {"pmode": "turbo"}}
    guard_and_normalize_xdna(merged, {"xdna": None})
    assert "xdna" not in merged


def test_guard_xdna_rejects_non_xdna_slot():
    merged = {"name": "s1", "binary": "llama-server"}
    with pytest.raises(BadRequest) as exc:
        guard_and_normalize_xdna(merged, {"xdna": {"pmode": "balanced"}})
    assert exc.value.code == "slot.xdna_not_supported"


def test_guard_xdna_rejects_bad_pmode():
    merged = {"name": "s1", "binary": "xdna"}
    with pytest.raises(Hal0Error):
        guard_and_normalize_xdna(merged, {"xdna": {"pmode": "not-a-real-mode"}})


def test_guard_xdna_rejects_bad_env_key():
    merged = {"name": "s1", "binary": "xdna"}
    with pytest.raises(BadRequest):
        guard_and_normalize_xdna(merged, {"xdna": {"env": {"NOT_ALLOWED": "1"}}})


def test_guard_xdna_rejects_relative_host_xrt():
    merged = {"name": "s1", "binary": "xdna"}
    with pytest.raises(BadRequest):
        guard_and_normalize_xdna(merged, {"xdna": {"host_xrt": "relative/path"}})


def test_guard_xdna_accepts_and_normalizes_valid_update():
    merged = {"name": "s1", "binary": "xdna"}
    guard_and_normalize_xdna(merged, {"xdna": {"host_xrt": "/opt/xilinx/xrt"}})
    assert merged["xdna"] == {"host_xrt": "/opt/xilinx/xrt", "env": {}}


def test_xdna_config_schema_rejects_unknown_key():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        XdnaConfig.model_validate({"bogus": 1})


# ── hardware stats probes ───────────────────────────────────────────────────


def test_npu_pmode_none_without_binary(monkeypatch):
    stats = HardwareStats()
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert stats.npu_pmode() is None


def test_npu_pmode_parses_xrt_smi_output(monkeypatch):
    stats = HardwareStats()
    monkeypatch.setattr("hal0.hardware.stats.shutil.which", lambda name: "/usr/bin/xrt-smi")
    monkeypatch.setattr(
        "hal0.hardware.stats._run",
        lambda argv: (0, "Device\nPower Mode : Balanced\n", ""),
    )
    assert stats.npu_pmode() == "Balanced"


def test_npu_fclk_none_without_sysfs(monkeypatch, tmp_path):
    stats = HardwareStats()
    monkeypatch.setattr("hal0.hardware.stats.Path.glob", lambda self, pat: iter(()))
    assert stats.npu_fclk() is None


def test_snapshot_includes_npu_keys(monkeypatch):
    stats = HardwareStats()
    monkeypatch.setattr(stats, "npu_pmode", lambda: "balanced")
    monkeypatch.setattr(stats, "npu_fclk", lambda: "1800Mhz")
    snap = stats.snapshot()
    assert snap["npu_pmode"] == "balanced"
    assert snap["npu_fclk"] == "1800Mhz"
