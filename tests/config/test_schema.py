"""Unit tests for hal0.config.schema pydantic models.

Each validator gets exercised on both valid and invalid input.  PLAN.md
§5 Tier 1 promises that ``backend = "vukan"`` raises with a helpful
message and the field path — these tests pin that contract.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hal0.config.schema import (
    CURRENT_SCHEMA_VERSION,
    DispatcherConfig,
    GPUInfo,
    Hal0Config,
    HardwareInfo,
    MetaConfig,
    ModelConfig,
    ProviderEntry,
    ProvidersConfig,
    SlotConfig,
    SlotsConfig,
    TelemetryConfig,
    UpstreamEntry,
    UpstreamsConfig,
)

# ── SlotConfig ────────────────────────────────────────────────────────────────


class TestSlotConfig:
    def test_minimum_valid(self) -> None:
        s = SlotConfig(name="primary", port=8081)
        assert s.name == "primary"
        assert s.port == 8081
        assert s.backend == "vulkan"
        # PR-10 (ADR-0008 §2): provider defaults to "lemonade".
        assert s.provider == "lemonade"
        assert s.enabled is True
        assert isinstance(s.model, ModelConfig)

    def test_invalid_backend_raises_with_field_path(self) -> None:
        """PLAN.md §5 Tier 1: backend = 'vukan' must surface field path."""
        with pytest.raises(ValidationError) as ei:
            SlotConfig(name="primary", port=8081, backend="vukan")
        msg = str(ei.value)
        assert "backend" in msg
        assert "vukan" in msg

    def test_invalid_provider_raises(self) -> None:
        with pytest.raises(ValidationError) as ei:
            SlotConfig(name="primary", port=8081, provider="ollama")
        assert "provider" in str(ei.value)
        assert "ollama" in str(ei.value)

    def test_port_below_range_raises(self) -> None:
        with pytest.raises(ValidationError):
            SlotConfig(name="primary", port=22)

    def test_port_above_range_raises(self) -> None:
        with pytest.raises(ValidationError):
            SlotConfig(name="primary", port=9000)

    def test_port_in_range_ok(self) -> None:
        SlotConfig(name="x", port=8081)
        SlotConfig(name="x", port=8099)

    def test_empty_name_raises(self) -> None:
        with pytest.raises(ValidationError) as ei:
            SlotConfig(name="", port=8081)
        assert "name" in str(ei.value)

    def test_name_uppercase_raises(self) -> None:
        with pytest.raises(ValidationError):
            SlotConfig(name="Primary", port=8081)

    def test_name_starts_with_dash_raises(self) -> None:
        with pytest.raises(ValidationError):
            SlotConfig(name="-bad", port=8081)

    def test_name_too_long_raises(self) -> None:
        with pytest.raises(ValidationError):
            SlotConfig(name="a" * 33, port=8081)

    def test_name_allows_alnum_dash_underscore(self) -> None:
        SlotConfig(name="primary", port=8081)
        SlotConfig(name="my-slot", port=8081)
        SlotConfig(name="my_slot", port=8081)
        SlotConfig(name="slot1", port=8081)

    def test_all_valid_backends(self) -> None:
        for b in ("vulkan", "rocm", "flm", "moonshine", "kokoro", "cpu"):
            SlotConfig(name="x", port=8081, backend=b)

    def test_all_valid_providers(self) -> None:
        for p in ("llama-server", "flm", "moonshine", "kokoro"):
            SlotConfig(name="x", port=8081, provider=p)

    def test_workers_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            SlotConfig(name="x", port=8081, workers=0)

    def test_idle_timeout_nonnegative(self) -> None:
        SlotConfig(name="x", port=8081, idle_timeout_s=0)
        with pytest.raises(ValidationError):
            SlotConfig(name="x", port=8081, idle_timeout_s=-1)

    def test_extra_allow_keeps_unknown_keys(self) -> None:
        """extra='allow' on SlotConfig keeps forward-compat keys."""
        s = SlotConfig.model_validate({"name": "x", "port": 8081, "future_field": "foo"})
        # Unknown top-level keys are kept on the model when extra="allow".
        assert s.model_dump().get("future_field") == "foo"


# ── ModelConfig ───────────────────────────────────────────────────────────────


class TestModelConfig:
    def test_defaults(self) -> None:
        m = ModelConfig()
        assert m.default == ""
        assert m.context_size == 4096
        assert m.n_gpu_layers == -1

    def test_context_size_below_minimum_raises(self) -> None:
        with pytest.raises(ValidationError):
            ModelConfig(context_size=0)

    def test_context_size_minimum_ok(self) -> None:
        ModelConfig(context_size=128)

    def test_negative_rope_freq_base_raises(self) -> None:
        with pytest.raises(ValidationError):
            ModelConfig(rope_freq_base=-1.0)


# ── ProviderEntry / ProvidersConfig ──────────────────────────────────────────


class TestProviderEntry:
    def test_requires_catalog_id(self) -> None:
        with pytest.raises(ValidationError):
            ProviderEntry()  # type: ignore[call-arg]

    def test_empty_catalog_id_raises(self) -> None:
        with pytest.raises(ValidationError) as ei:
            ProviderEntry(catalog_id="")
        assert "catalog_id" in str(ei.value)

    def test_valid(self) -> None:
        p = ProviderEntry(catalog_id="openrouter", name="OpenRouter")
        assert p.catalog_id == "openrouter"
        assert p.enabled is True


class TestProvidersConfig:
    def test_default_empty(self) -> None:
        c = ProvidersConfig()
        assert c.provider == []

    def test_round_trip(self) -> None:
        c = ProvidersConfig(provider=[ProviderEntry(catalog_id="x")])
        d = c.model_dump()
        c2 = ProvidersConfig.model_validate(d)
        assert c2.provider[0].catalog_id == "x"


# ── UpstreamEntry / UpstreamsConfig ──────────────────────────────────────────


class TestUpstreamEntry:
    def test_requires_name_and_url(self) -> None:
        with pytest.raises(ValidationError):
            UpstreamEntry()  # type: ignore[call-arg]

    def test_empty_name_raises(self) -> None:
        with pytest.raises(ValidationError) as ei:
            UpstreamEntry(name="", url="http://x")
        assert "name" in str(ei.value)

    def test_empty_url_raises(self) -> None:
        with pytest.raises(ValidationError) as ei:
            UpstreamEntry(name="x", url="")
        assert "url" in str(ei.value)

    def test_invalid_kind_raises(self) -> None:
        with pytest.raises(ValidationError) as ei:
            UpstreamEntry(name="x", url="http://x", kind="invalid")
        assert "kind" in str(ei.value)

    def test_invalid_auth_style_raises(self) -> None:
        with pytest.raises(ValidationError) as ei:
            UpstreamEntry(name="x", url="http://x", auth_style="basic")
        assert "auth_style" in str(ei.value)

    def test_invalid_warmup_raises(self) -> None:
        with pytest.raises(ValidationError) as ei:
            UpstreamEntry(name="x", url="http://x", warmup_strategy="weird")
        assert "warmup_strategy" in str(ei.value)

    def test_slot_kind_requires_slot_name(self) -> None:
        with pytest.raises(ValidationError) as ei:
            UpstreamEntry(name="x", url="http://x", kind="slot", slot_name=None)
        assert "slot_name" in str(ei.value)

    def test_slot_kind_with_slot_name_ok(self) -> None:
        UpstreamEntry(name="x", url="http://x", kind="slot", slot_name="primary")

    def test_remote_kind_no_slot_name_ok(self) -> None:
        UpstreamEntry(name="x", url="http://x", kind="remote")

    def test_timeout_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            UpstreamEntry(name="x", url="http://x", timeout_seconds=0.0)
        with pytest.raises(ValidationError):
            UpstreamEntry(name="x", url="http://x", timeout_seconds=-1.0)


class TestUpstreamsConfig:
    def test_duplicate_upstream_names_raise(self) -> None:
        with pytest.raises(ValidationError) as ei:
            UpstreamsConfig(
                upstream=[
                    UpstreamEntry(name="dup", url="http://a"),
                    UpstreamEntry(name="dup", url="http://b"),
                ]
            )
        assert "dup" in str(ei.value)


# ── HardwareInfo ──────────────────────────────────────────────────────────────


class TestHardwareInfo:
    def test_defaults(self) -> None:
        h = HardwareInfo()
        assert h.cpu_cores == 0
        assert h.ram_mb == 0
        assert h.gpus == []
        assert h.npu.present is False

    def test_gpu_info_defaults(self) -> None:
        g = GPUInfo()
        assert g.vendor == ""
        assert g.compute_capable is False
        assert g.vulkan_capable is False

    def test_negative_ram_raises(self) -> None:
        with pytest.raises(ValidationError):
            HardwareInfo(ram_mb=-1)

    def test_round_trip(self) -> None:
        h = HardwareInfo(
            cpu_model="Ryzen",
            cpu_cores=16,
            cpu_threads=32,
            ram_mb=131072,
            gpus=[GPUInfo(vendor="nvidia", name="RTX 4080", vram_mb=16384)],
        )
        d = h.model_dump()
        h2 = HardwareInfo.model_validate(d)
        assert h2.cpu_cores == 16
        assert h2.gpus[0].vendor == "nvidia"


# ── Hal0Config (top-level) ────────────────────────────────────────────────────


class TestHal0Config:
    def test_defaults_load_clean(self) -> None:
        c = Hal0Config()
        assert c.meta.schema_version == CURRENT_SCHEMA_VERSION
        assert c.dispatcher.prefetch_timeout_s == 8.0
        assert c.telemetry.enabled is False
        assert c.telemetry.channel == "stable"

    def test_invalid_channel_raises(self) -> None:
        with pytest.raises(ValidationError) as ei:
            TelemetryConfig(channel="beta")
        assert "channel" in str(ei.value)

    def test_schema_version_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            MetaConfig(schema_version=0)

    def test_dispatcher_timeout_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            DispatcherConfig(prefetch_timeout_s=0.0)
        with pytest.raises(ValidationError):
            DispatcherConfig(prefetch_timeout_s=-1.0)

    def test_dispatcher_parallel_cap_min_1(self) -> None:
        with pytest.raises(ValidationError):
            DispatcherConfig(prefetch_parallel_cap=0)

    def test_slot_port_range_end_lt_start_raises(self) -> None:
        with pytest.raises(ValidationError) as ei:
            SlotsConfig(port_range_start=8090, port_range_end=8085)
        assert "port_range_end" in str(ei.value)

    def test_extra_allow_keeps_unknown_keys(self) -> None:
        c = Hal0Config.model_validate({"future_section": {"foo": 1}})
        # extra='allow' keeps the unknown table.
        assert c.model_dump().get("future_section") == {"foo": 1}
