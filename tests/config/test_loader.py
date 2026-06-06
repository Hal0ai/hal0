"""Unit tests for hal0.config.loader.

Covers:
  * Round-trip load/save for hal0.toml and slots/<name>.toml.
  * Atomic write semantics — interrupt mid-write leaves the original
    intact (Tier 1 fix, see PLAN.md §5).
  * Schema validation surfaces field-path errors at load time.
  * Migration framework: identity v1 + chain runner.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from hal0.config import paths
from hal0.config.loader import (
    ConfigNotFound,
    ConfigParseError,
    list_slots,
    load_hal0_config,
    load_hardware_info,
    load_providers_config,
    load_slot_config,
    load_upstreams_config,
    save_hal0_config,
    save_hardware_info,
    save_providers_config,
    save_slot_config,
    save_upstreams_config,
    write_toml_atomic,
)
from hal0.config.migrations import (
    MIGRATIONS,
    MigrationError,
    latest_version,
    run_migrations,
)
from hal0.config.schema import (
    CURRENT_SCHEMA_VERSION,
    GPUInfo,
    Hal0Config,
    HardwareInfo,
    MetaConfig,
    NPUInfo,
    ProviderEntry,
    ProvidersConfig,
    SlotConfig,
    UpstreamEntry,
    UpstreamsConfig,
)

# ── write_toml_atomic ─────────────────────────────────────────────────────────


class TestWriteTomlAtomic:
    def test_writes_file_with_content(self, tmp_path: Path) -> None:
        target = tmp_path / "out.toml"
        write_toml_atomic(target, {"a": 1, "section": {"b": "two"}})
        assert target.exists()
        with open(target, "rb") as f:
            data = tomllib.load(f)
        assert data == {"a": 1, "section": {"b": "two"}}

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "deep" / "out.toml"
        write_toml_atomic(target, {"a": 1})
        assert target.exists()

    def test_overwrites_existing_file_atomically(self, tmp_path: Path) -> None:
        target = tmp_path / "out.toml"
        write_toml_atomic(target, {"a": 1})
        write_toml_atomic(target, {"a": 2})
        with open(target, "rb") as f:
            data = tomllib.load(f)
        assert data == {"a": 2}

    def test_interrupted_write_leaves_original_intact(self, tmp_path: Path) -> None:
        """Tier 1: a mid-write crash leaves the prior file untouched."""
        target = tmp_path / "out.toml"
        write_toml_atomic(target, {"a": "original"})
        original_mtime = target.stat().st_mtime

        # Inject a failure inside the os.fdopen+dump block by patching
        # tomli_w.dump to raise.  os.replace() is never reached, so the
        # original file is untouched.
        with (
            patch("hal0.config.loader.tomli_w.dump", side_effect=OSError("disk full")),
            pytest.raises(OSError, match="disk full"),
        ):
            write_toml_atomic(target, {"a": "new"})

        # Original content survives.
        with open(target, "rb") as f:
            data = tomllib.load(f)
        assert data == {"a": "original"}

        # No leftover tmpfiles.
        tmpfiles = list(target.parent.glob(f".{target.name}.*.tmp"))
        assert tmpfiles == []

        # mtime unchanged (no rename happened).
        assert target.stat().st_mtime == original_mtime

    def test_interrupted_rename_cleans_up_tmpfile(self, tmp_path: Path) -> None:
        """If os.replace fails the tmpfile is unlinked in the finally."""
        target = tmp_path / "out.toml"
        write_toml_atomic(target, {"a": "original"})

        with (
            patch("hal0.config.loader.os.replace", side_effect=OSError("nope")),
            pytest.raises(OSError, match="nope"),
        ):
            write_toml_atomic(target, {"a": "new"})

        # Original survives, tmpfile cleaned up.
        with open(target, "rb") as f:
            assert tomllib.load(f) == {"a": "original"}
        tmpfiles = list(target.parent.glob(f".{target.name}.*.tmp"))
        assert tmpfiles == []


# ── hal0.toml round-trip ─────────────────────────────────────────────────────


class TestHal0ConfigRoundTrip:
    def test_load_default_when_file_missing(self, tmp_hal0_home: str) -> None:
        cfg = load_hal0_config()
        assert isinstance(cfg, Hal0Config)
        assert cfg.meta.schema_version == CURRENT_SCHEMA_VERSION
        assert cfg.dispatcher.prefetch_timeout_s == 8.0

    def test_save_then_load(self, tmp_hal0_home: str) -> None:
        original = Hal0Config()
        original.dispatcher.prefetch_timeout_s = 12.5
        original.telemetry.enabled = True
        original.telemetry.channel = "nightly"

        save_hal0_config(original)
        assert paths.hal0_toml().exists()

        loaded = load_hal0_config()
        assert loaded.dispatcher.prefetch_timeout_s == 12.5
        assert loaded.telemetry.enabled is True
        assert loaded.telemetry.channel == "nightly"

    def test_load_with_invalid_toml_raises(self, tmp_hal0_home: str) -> None:
        paths.hal0_toml().parent.mkdir(parents=True, exist_ok=True)
        paths.hal0_toml().write_text("this is = not valid !! toml\n")
        with pytest.raises(ConfigParseError):
            load_hal0_config()

    def test_load_with_invalid_field_value_raises(self, tmp_hal0_home: str) -> None:
        paths.hal0_toml().parent.mkdir(parents=True, exist_ok=True)
        paths.hal0_toml().write_text('[telemetry]\nchannel = "beta"\n')
        with pytest.raises(ConfigParseError) as ei:
            load_hal0_config()
        assert "channel" in str(ei.value)

    def test_load_with_explicit_path(self, tmp_path: Path) -> None:
        custom = tmp_path / "custom.toml"
        save_hal0_config(Hal0Config(), path=custom)
        loaded = load_hal0_config(path=custom)
        assert isinstance(loaded, Hal0Config)


# ── slot config round-trip ───────────────────────────────────────────────────


class TestSlotConfigRoundTrip:
    def test_save_then_load(self, tmp_hal0_home: str) -> None:
        cfg = SlotConfig(
            name="primary",
            port=8081,
            backend="vulkan",
            provider="llama-server",
        )
        cfg.model.default = "qwen3-4b"
        save_slot_config(cfg)

        loaded = load_slot_config("primary")
        assert loaded.name == "primary"
        assert loaded.port == 8081
        assert loaded.backend == "vulkan"
        assert loaded.model.default == "qwen3-4b"

    def test_load_missing_slot_raises(self, tmp_hal0_home: str) -> None:
        with pytest.raises(ConfigNotFound):
            load_slot_config("ghost")

    def test_load_with_invalid_backend_raises_with_field_path(self, tmp_hal0_home: str) -> None:
        """PLAN.md §5 Tier 1: backend = 'vukan' must raise with field path."""
        paths.slots_config_dir().mkdir(parents=True, exist_ok=True)
        (paths.slots_config_dir() / "broken.toml").write_text(
            '[slot]\nname = "broken"\nport = 8081\nbackend = "vukan"\n'
        )
        with pytest.raises(ConfigParseError) as ei:
            load_slot_config("broken")
        assert "backend" in str(ei.value)
        assert "vukan" in str(ei.value)

    def test_list_slots_empty(self, tmp_hal0_home: str) -> None:
        assert list_slots() == []

    def test_list_slots_returns_stems_sorted(self, tmp_hal0_home: str) -> None:
        for name in ("primary", "embed", "stt"):
            cfg = SlotConfig(name=name, port=8081)
            save_slot_config(cfg)
        assert list_slots() == ["embed", "primary", "stt"]

    def test_slot_toml_on_disk_has_nested_sections(self, tmp_hal0_home: str) -> None:
        """On-disk shape uses [slot] / [model] sections (haloai-compatible)."""
        cfg = SlotConfig(name="primary", port=8081)
        save_slot_config(cfg)
        target = paths.slots_config_dir() / "primary.toml"
        with open(target, "rb") as f:
            data = tomllib.load(f)
        assert "slot" in data
        assert data["slot"]["name"] == "primary"
        assert data["slot"]["port"] == 8081
        assert "model" in data

    def test_load_preserves_extra_sections(self, tmp_hal0_home: str) -> None:
        """Unknown top-level sections round-trip via `extra` so hand-edits survive."""
        paths.slots_config_dir().mkdir(parents=True, exist_ok=True)
        (paths.slots_config_dir() / "primary.toml").write_text(
            "[slot]\n"
            'name = "primary"\n'
            "port = 8081\n"
            "[defaults]\n"
            "threads = 12\n"
            'extra_args = "--foo"\n'
        )
        cfg = load_slot_config("primary")
        assert cfg.extra.get("defaults", {}).get("threads") == 12

        # Round-trip preserves the [defaults] section.
        save_slot_config(cfg)
        with open(paths.slots_config_dir() / "primary.toml", "rb") as f:
            data = tomllib.load(f)
        assert data["defaults"]["threads"] == 12

    # ── Phase 1 A3: [server].extra_args ──────────────────────────────────

    def test_server_extra_args_missing_defaults_to_none(self, tmp_hal0_home: str) -> None:
        """A slot TOML without a [server] table loads with server.extra_args = None."""
        paths.slots_config_dir().mkdir(parents=True, exist_ok=True)
        (paths.slots_config_dir() / "primary.toml").write_text(
            '[slot]\nname = "primary"\nport = 8081\n'
        )
        cfg = load_slot_config("primary")
        assert cfg.server.extra_args is None

    def test_server_extra_args_loads_from_toml(self, tmp_hal0_home: str) -> None:
        """`[server].extra_args = "..."` populates the typed ServerConfig field."""
        paths.slots_config_dir().mkdir(parents=True, exist_ok=True)
        (paths.slots_config_dir() / "primary.toml").write_text(
            "[slot]\n"
            'name = "primary"\n'
            "port = 8081\n"
            "[server]\n"
            'extra_args = "--lora /tmp/lora.gguf"\n'
        )
        cfg = load_slot_config("primary")
        assert cfg.server.extra_args == "--lora /tmp/lora.gguf"

    def test_server_extra_args_round_trips(self, tmp_hal0_home: str) -> None:
        """save → load → save preserves [server].extra_args on disk."""
        cfg = SlotConfig(name="primary", port=8081)
        cfg.server.extra_args = "--rope-freq-base 500000"
        save_slot_config(cfg)

        loaded = load_slot_config("primary")
        assert loaded.server.extra_args == "--rope-freq-base 500000"

        # Disk shape is a proper [server] table.
        with open(paths.slots_config_dir() / "primary.toml", "rb") as f:
            data = tomllib.load(f)
        assert data["server"]["extra_args"] == "--rope-freq-base 500000"

    def test_unset_server_extra_args_does_not_write_empty_table(self, tmp_hal0_home: str) -> None:
        """A default ServerConfig (all None) elides the [server] table on disk."""
        cfg = SlotConfig(name="primary", port=8081)
        save_slot_config(cfg)
        with open(paths.slots_config_dir() / "primary.toml", "rb") as f:
            data = tomllib.load(f)
        # No stray empty [server] table.
        assert "server" not in data


# ── providers.toml round-trip ────────────────────────────────────────────────


class TestProvidersRoundTrip:
    def test_load_default_when_missing(self, tmp_hal0_home: str) -> None:
        cfg = load_providers_config()
        assert isinstance(cfg, ProvidersConfig)
        assert cfg.provider == []

    def test_save_then_load(self, tmp_hal0_home: str) -> None:
        cfg = ProvidersConfig(provider=[ProviderEntry(catalog_id="openrouter", name="OpenRouter")])
        save_providers_config(cfg)
        loaded = load_providers_config()
        assert len(loaded.provider) == 1
        assert loaded.provider[0].catalog_id == "openrouter"


# ── upstreams.toml round-trip ────────────────────────────────────────────────


class TestUpstreamsRoundTrip:
    def test_save_then_load(self, tmp_hal0_home: str) -> None:
        cfg = UpstreamsConfig(
            upstream=[
                UpstreamEntry(
                    name="local", url="http://127.0.0.1:8081", kind="slot", slot_name="primary"
                ),
            ]
        )
        save_upstreams_config(cfg)
        loaded = load_upstreams_config()
        assert len(loaded.upstream) == 1
        assert loaded.upstream[0].kind == "slot"
        assert loaded.upstream[0].slot_name == "primary"

    def test_invalid_slot_kind_without_slot_name_raises_at_load(self, tmp_hal0_home: str) -> None:
        path = paths.etc() / "upstreams.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('[[upstream]]\nname = "broken"\nkind = "slot"\nurl = "http://x"\n')
        with pytest.raises(ConfigParseError) as ei:
            load_upstreams_config()
        assert "slot_name" in str(ei.value)


# ── hardware.json round-trip ─────────────────────────────────────────────────


class TestHardwareJsonRoundTrip:
    def test_load_default_when_missing(self, tmp_hal0_home: str) -> None:
        h = load_hardware_info()
        assert isinstance(h, HardwareInfo)
        assert h.gpus == []

    def test_save_then_load(self, tmp_hal0_home: str) -> None:
        h = HardwareInfo(
            cpu_model="AMD Ryzen 9 7950X",
            cpu_cores=16,
            cpu_threads=32,
            ram_mb=131072,
            gpus=[GPUInfo(vendor="nvidia", name="RTX 4080", vram_mb=16384)],
        )
        save_hardware_info(h)
        assert paths.hardware_json().exists()

        loaded = load_hardware_info()
        assert loaded.cpu_cores == 16
        assert len(loaded.gpus) == 1
        assert loaded.gpus[0].name == "RTX 4080"

    def test_cgroup_max_mb_round_trips(self, tmp_hal0_home: str) -> None:
        """The cgroup cap is part of the probe snapshot (#372) — make
        sure it round-trips through the JSON file so the dashboard can
        see it after a probe + restart."""
        h = HardwareInfo(
            cpu_model="AMD Ryzen",
            cpu_cores=16,
            cpu_threads=32,
            ram_mb=96 * 1024,
            unified_memory_mb=96 * 1024,
            gpus=[],
            npu=NPUInfo(present=False),
            cgroup_max_mb=80 * 1024,
        )
        save_hardware_info(h)
        loaded = load_hardware_info()
        assert loaded.cgroup_max_mb == 80 * 1024

    def test_load_with_invalid_json_raises(self, tmp_hal0_home: str) -> None:
        paths.hardware_json().parent.mkdir(parents=True, exist_ok=True)
        paths.hardware_json().write_text("{not valid json")
        with pytest.raises(ConfigParseError):
            load_hardware_info()


# ── Migration framework ──────────────────────────────────────────────────────


class TestMigrations:
    def test_v1_registered(self) -> None:
        assert 1 in MIGRATIONS

    def test_latest_version_at_least_1(self) -> None:
        assert latest_version() >= 1

    def test_run_migrations_identity_for_v1(self) -> None:
        data = {"meta": {"schema_version": 1}, "telemetry": {"enabled": True}}
        out, version = run_migrations(data)
        assert version == latest_version()
        assert out["meta"]["schema_version"] == version
        assert out["telemetry"]["enabled"] is True

    def test_run_migrations_unversioned_input_stamps_v1(self) -> None:
        """A v0/unversioned dict gets stamped as v1."""
        data = {"telemetry": {"enabled": False}}
        out, version = run_migrations(data, target_version=1)
        assert version == 1
        assert out["meta"]["schema_version"] == 1
        assert out["telemetry"]["enabled"] is False

    def test_run_migrations_rejects_downgrade(self) -> None:
        data = {"meta": {"schema_version": 5}}
        with pytest.raises(MigrationError):
            run_migrations(data, target_version=1)

    def test_run_migrations_missing_step_raises(self) -> None:
        # Ask for a version higher than what's registered.
        data = {"meta": {"schema_version": 1}}
        impossible = latest_version() + 5
        with pytest.raises(MigrationError) as ei:
            run_migrations(data, target_version=impossible)
        assert "missing migration" in str(ei.value)

    def test_run_migrations_does_not_mutate_input(self) -> None:
        data = {"meta": {"schema_version": 1}, "telemetry": {"enabled": True}}
        original = {"meta": {"schema_version": 1}, "telemetry": {"enabled": True}}
        run_migrations(data)
        assert data == original

    def test_run_migrations_chained(self) -> None:
        """Stub: register a fake v2 migration, run 1 → 2, verify chain."""
        from hal0.config import migrations as m

        sentinel_calls: list[int] = []

        def fake_v2(d: dict) -> dict:
            sentinel_calls.append(2)
            d = dict(d)
            d["v2_field"] = "added"
            return d

        # Inject manually (not via @register) so we don't pollute the
        # production MIGRATIONS dict permanently.
        m.MIGRATIONS[2] = fake_v2
        try:
            data = {"meta": {"schema_version": 1}}
            out, version = run_migrations(data, target_version=2)
            assert version == 2
            assert out["meta"]["schema_version"] == 2
            assert out["v2_field"] == "added"
            assert sentinel_calls == [2]
        finally:
            del m.MIGRATIONS[2]


# ── HAL0_HOME isolation ──────────────────────────────────────────────────────


class TestHAL0HomeIsolation:
    def test_loaders_respect_hal0_home(self, tmp_hal0_home: str) -> None:
        """All loaders write under HAL0_HOME when set."""
        save_hal0_config(Hal0Config())
        save_slot_config(SlotConfig(name="primary", port=8081))
        save_providers_config(ProvidersConfig())
        save_upstreams_config(UpstreamsConfig())
        save_hardware_info(HardwareInfo())

        home = Path(tmp_hal0_home)
        assert str(home) in str(paths.hal0_toml())
        assert paths.hal0_toml().exists()
        assert (paths.slots_config_dir() / "primary.toml").exists()
        assert (paths.etc() / "providers.toml").exists()
        assert (paths.etc() / "upstreams.toml").exists()
        assert paths.hardware_json().exists()


# ── Direct meta-overrides ────────────────────────────────────────────────────


class TestMetaConfigVersion:
    def test_persisted_schema_version_loads_back(self, tmp_hal0_home: str) -> None:
        cfg = Hal0Config()
        cfg.meta = MetaConfig(schema_version=1)
        save_hal0_config(cfg)
        loaded = load_hal0_config()
        assert loaded.meta.schema_version == 1


# ── manifest.json (toolbox image pinning) ────────────────────────────────────


class TestManifestLoader:
    """Covers the toolbox-image manifest reader used at slot-spawn time.

    The manifest's digests are refreshed by `scripts/update-toolbox-digests.sh`
    before a release. The runtime reads it through `load_manifest` /
    `manifest_image_ref` to decide whether to pin pulls by digest or fall
    back to :v1 tags.
    """

    def _write_manifest(self, home: str, payload: dict[str, object]) -> None:
        import json

        manifest_dir = Path(home) / "etc" / "hal0"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "manifest.json").write_text(json.dumps(payload))

    def test_load_manifest_missing_returns_empty(self, tmp_hal0_home: str) -> None:
        from hal0.config.loader import load_manifest

        assert load_manifest() == {}

    def test_load_manifest_round_trip(self, tmp_hal0_home: str) -> None:
        from hal0.config.loader import load_manifest

        payload = {
            "_schema": "hal0.manifest.v1",
            "toolbox_images": {
                "vulkan": {"tag": "ghcr.io/o/hal0-toolbox-vulkan:v1", "digest": "sha256:abc"},
            },
        }
        self._write_manifest(tmp_hal0_home, payload)
        loaded = load_manifest()
        assert loaded["toolbox_images"]["vulkan"]["digest"] == "sha256:abc"

    def test_manifest_image_ref_digest_pinned(self, tmp_hal0_home: str) -> None:
        from hal0.config.loader import manifest_image_ref

        payload = {
            "toolbox_images": {
                "vulkan": {
                    "tag": "ghcr.io/o/hal0-toolbox-vulkan:v1",
                    "digest": "sha256:deadbeef",
                }
            }
        }
        self._write_manifest(tmp_hal0_home, payload)
        ref = manifest_image_ref("vulkan")
        assert ref == "ghcr.io/o/hal0-toolbox-vulkan@sha256:deadbeef"

    def test_manifest_image_ref_falls_back_to_tag(self, tmp_hal0_home: str) -> None:
        from hal0.config.loader import manifest_image_ref

        payload = {
            "toolbox_images": {
                "vulkan": {"tag": "ghcr.io/o/hal0-toolbox-vulkan:v1", "digest": None},
            }
        }
        self._write_manifest(tmp_hal0_home, payload)
        assert manifest_image_ref("vulkan") == "ghcr.io/o/hal0-toolbox-vulkan:v1"

    def test_manifest_image_ref_missing_returns_none(self, tmp_hal0_home: str) -> None:
        from hal0.config.loader import manifest_image_ref

        self._write_manifest(tmp_hal0_home, {"toolbox_images": {}})
        assert manifest_image_ref("flm") is None

    def test_manifest_parse_error_raises(self, tmp_hal0_home: str) -> None:
        from hal0.config.loader import load_manifest

        manifest_dir = Path(tmp_hal0_home) / "etc" / "hal0"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "manifest.json").write_text("{not json")
        with pytest.raises(ConfigParseError):
            load_manifest()
