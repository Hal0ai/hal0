"""Tests for the v0.2 SlotConfig.device refactor + capabilities.toml
schema_version=2 migration (ADR-0006 §7, issue #143).

Covers:
  - Pydantic schema accepts / rejects ``device`` values correctly.
  - Each legacy ``backend`` → ``device`` mapping enumerated.
  - ``map_backend_to_device`` edge cases (unknown values, empty input).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from hal0.capabilities.config import (
    CAPABILITIES_SCHEMA_VERSION_CURRENT,
    CapabilityConfig,
    CapabilitySelection,
    load_capabilities_config,
    save_capabilities_config,
)
from hal0.config.schema import (
    BACKEND_TO_DEVICE,
    DEFAULT_DEVICE,
    SlotConfig,
    map_backend_to_device,
)

# ── SlotConfig.device ────────────────────────────────────────────────────────


class TestSlotConfigDevice:
    def test_default_device_is_gpu_rocm(self) -> None:
        s = SlotConfig(name="primary", port=8081)
        assert s.device == "gpu-rocm"

    def test_explicit_device_accepted(self) -> None:
        for d in ("gpu-rocm", "gpu-vulkan", "cpu", "npu"):
            s = SlotConfig(name="x", port=8081, device=d)
            assert s.device == d

    def test_invalid_device_raises_with_field_path(self) -> None:
        with pytest.raises(ValidationError) as ei:
            SlotConfig(name="primary", port=8081, device="gpu-rcom")
        msg = str(ei.value)
        assert "device" in msg
        assert "gpu-rcom" in msg

    @pytest.mark.parametrize(
        ("legacy_backend", "expected_device"),
        [
            ("vulkan", "gpu-vulkan"),
            ("rocm", "gpu-rocm"),
            ("flm", "npu"),
            ("moonshine", "cpu"),
            ("kokoro", "cpu"),
            ("cpu", "cpu"),
        ],
    )
    def test_legacy_backend_is_converted_not_promoted(
        self, legacy_backend: str, expected_device: str
    ) -> None:
        # ``backend`` is not a slot key any more (P2-device: ``device`` is the
        # sole persisted truth) and the strict schema (ADR-0029 §5) no longer
        # promotes it: the profile-centric conversion rewrites it to ``device``
        # (the same mapping), and a file still carrying it fails the load.
        with pytest.raises(ValidationError) as ei:
            SlotConfig(name="x", port=8081, backend=legacy_backend)
        assert "backend" in str(ei.value)
        assert map_backend_to_device(legacy_backend) == expected_device


# ── map_backend_to_device ────────────────────────────────────────────────────


class TestMapBackendToDevice:
    def test_empty_input_returns_default(self) -> None:
        assert map_backend_to_device("") == DEFAULT_DEVICE
        assert map_backend_to_device(None) == DEFAULT_DEVICE  # type: ignore[arg-type]

    def test_known_legacy_values(self) -> None:
        for legacy, expected in BACKEND_TO_DEVICE.items():
            assert map_backend_to_device(legacy) == expected

    def test_unknown_value_maps_to_cpu_with_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        # Edge case: operator hand-edited backend to a typo.
        # The implementation moved to hal0.model_meta (schema re-exports it);
        # the log event name is unchanged for grep continuity.
        with caplog.at_level("WARNING", logger="hal0.model_meta"):
            result = map_backend_to_device("rcom")
        assert result == "cpu"
        assert any("device_mapping_unknown_backend" in r.message for r in caplog.records)

    def test_new_namespace_values_are_idempotent(self) -> None:
        # The catalog already stored gpu-rocm etc; these should round-trip.
        for d in ("gpu-rocm", "gpu-vulkan", "cpu", "npu"):
            assert map_backend_to_device(d) == d


# ── save_capabilities_config drops legacy backend ───────────────────────────


class TestSaveCapabilitiesConfig:
    def test_save_strips_legacy_backend(self, tmp_path: Path) -> None:
        target = tmp_path / "capabilities.toml"
        cfg = CapabilityConfig(
            selections={
                "embed": {
                    "embed": CapabilitySelection(
                        device="gpu-rocm",
                        backend="rocm",  # deprecated; should NOT persist
                        provider="llama-server",
                        model="nomic",
                        enabled=True,
                    )
                }
            }
        )
        save_capabilities_config(cfg, target)
        with open(target, "rb") as f:
            raw = tomllib.load(f)
        assert raw["schema_version"] == CAPABILITIES_SCHEMA_VERSION_CURRENT
        sel = raw["selections"]["embed"]["embed"]
        assert sel["device"] == "gpu-rocm"
        assert "backend" not in sel

    def test_save_then_load_round_trip(self, tmp_path: Path) -> None:
        target = tmp_path / "capabilities.toml"
        cfg_in = CapabilityConfig(
            selections={
                "voice": {
                    "stt": CapabilitySelection(
                        device="cpu",
                        provider="moonshine",
                        model="moonshine-base",
                        enabled=True,
                    )
                }
            }
        )
        save_capabilities_config(cfg_in, target)
        cfg_out = load_capabilities_config(target)
        assert cfg_out.schema_version == CAPABILITIES_SCHEMA_VERSION_CURRENT
        sel = cfg_out.selections["voice"]["stt"]
        assert sel.device == "cpu"
        assert sel.model == "moonshine-base"


# ── CapabilitySelection legacy alias ──────────────────────────────────────────


class TestCapabilitySelectionLegacy:
    def test_legacy_backend_promotes_to_device_on_load(self) -> None:
        sel = CapabilitySelection(backend="vulkan", model="m", enabled=True)
        assert sel.device == "gpu-vulkan"

    def test_device_takes_precedence(self) -> None:
        sel = CapabilitySelection(device="cpu", backend="vulkan", model="m")
        # When device is explicitly set, it wins.
        assert sel.device == "cpu"


# ── Phase E: legacy runtime/provider markers are rejected, not migrated ───────


class TestLegacyRuntimeRejected:
    """``runtime="lemonade"`` / ``provider="lemonade"`` no longer exist.

    There are no pre-container installs to upgrade (the first release ships
    container-only), so legacy markers are a hard validation error rather
    than a silent migration — a fail-loud signal for hand-edited TOMLs.
    """

    def test_runtime_default_is_container(self) -> None:
        s = SlotConfig(name="primary", port=8081)
        assert s.runtime == "container"

    def test_legacy_runtime_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SlotConfig(name="primary", port=8081, runtime="lemonade")

    def test_runtime_rejects_unknown_values(self) -> None:
        with pytest.raises(ValidationError):
            SlotConfig(name="primary", port=8081, runtime="bogus")

    def test_legacy_provider_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SlotConfig(name="primary", port=8081, provider="lemonade")

    def test_provider_default_is_llama_server(self) -> None:
        s = SlotConfig(name="primary", port=8081)
        assert s.provider == "llama-server"

    def test_container_slot_loads_clean(self) -> None:
        s = SlotConfig(
            name="chat",
            port=8081,
            runtime="container",
            profile="vulkan",
        )
        assert s.runtime == "container"
        assert s.profile == "vulkan"
