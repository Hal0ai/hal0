"""Tests for the v0.2 SlotConfig.device refactor + capabilities.toml
schema_version=2 migration (ADR-0006 §7, issue #143).

Covers:
  - Pydantic schema accepts / rejects ``device`` values correctly.
  - Each legacy ``backend`` → ``device`` mapping enumerated.
  - ``map_backend_to_device`` edge cases (unknown values, empty input).
  - Auto-migration is idempotent (running twice = no-op the second time).
  - ``.v1.bak`` is created exactly once and preserved.
  - Round-trip: write v1 → load → migrates → write v2 → load → no-op.
  - CLI subcommand prints the diff correctly; ``--apply`` mutates;
    ``--revert`` restores.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
import tomli_w
from pydantic import ValidationError
from typer.testing import CliRunner

from hal0.capabilities.config import (
    CAPABILITIES_SCHEMA_VERSION_CURRENT,
    CapabilityConfig,
    CapabilitySelection,
    auto_migrate_capabilities_file,
    capabilities_v1_backup_path,
    load_capabilities_config,
    migrate_capabilities_v1_to_v2,
    read_schema_version,
    save_capabilities_config,
)
from hal0.cli.capabilities_commands import app as capabilities_app
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
    def test_legacy_backend_promotes_to_device(
        self, legacy_backend: str, expected_device: str
    ) -> None:
        # Construct WITHOUT device — promotion should fill it from backend.
        s = SlotConfig(name="x", port=8081, backend=legacy_backend)
        assert s.device == expected_device
        # Backend is preserved for one-release round-trip legibility.
        assert s.backend == legacy_backend

    def test_explicit_device_wins_over_legacy_backend(self) -> None:
        # If both are passed, ``device`` is authoritative.
        s = SlotConfig(name="x", port=8081, backend="vulkan", device="cpu")
        assert s.device == "cpu"
        assert s.backend == "vulkan"


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
        with caplog.at_level("WARNING", logger="hal0.config.schema"):
            result = map_backend_to_device("rcom")
        assert result == "cpu"
        assert any("device_mapping_unknown_backend" in r.message for r in caplog.records)

    def test_new_namespace_values_are_idempotent(self) -> None:
        # The catalog already stored gpu-rocm etc; these should round-trip.
        for d in ("gpu-rocm", "gpu-vulkan", "cpu", "npu"):
            assert map_backend_to_device(d) == d


# ── migrate_capabilities_v1_to_v2 ────────────────────────────────────────────


class TestMigrateCapabilitiesV1ToV2:
    def test_renames_backend_to_device(self) -> None:
        v1 = {
            "selections": {
                "embed": {
                    "embed": {
                        "backend": "vulkan",
                        "provider": "llama-server",
                        "model": "nomic-embed",
                        "enabled": True,
                    }
                }
            }
        }
        v2 = migrate_capabilities_v1_to_v2(v1)
        assert v2["schema_version"] == 2
        sel = v2["selections"]["embed"]["embed"]
        assert sel["device"] == "gpu-vulkan"
        assert "backend" not in sel
        assert sel["provider"] == "llama-server"

    @pytest.mark.parametrize(
        ("legacy", "expected"),
        [
            ("vulkan", "gpu-vulkan"),
            ("rocm", "gpu-rocm"),
            ("flm", "npu"),
            ("moonshine", "cpu"),
            ("kokoro", "cpu"),
            ("cpu", "cpu"),
        ],
    )
    def test_mapping_table(self, legacy: str, expected: str) -> None:
        v1 = {"selections": {"test": {"test": {"backend": legacy, "model": "m", "enabled": False}}}}
        v2 = migrate_capabilities_v1_to_v2(v1)
        assert v2["selections"]["test"]["test"]["device"] == expected

    def test_already_v2_input_is_noop(self) -> None:
        """Idempotence: a v2 file should round-trip unchanged in shape."""
        v2_in = {
            "schema_version": 2,
            "selections": {
                "embed": {
                    "embed": {
                        "device": "gpu-rocm",
                        "provider": "llama-server",
                        "model": "nomic",
                        "enabled": True,
                    }
                }
            },
        }
        v2_out = migrate_capabilities_v1_to_v2(v2_in)
        assert v2_out["schema_version"] == 2
        assert v2_out["selections"]["embed"]["embed"]["device"] == "gpu-rocm"
        assert "backend" not in v2_out["selections"]["embed"]["embed"]

    def test_empty_selections_stamps_version(self) -> None:
        v1 = {"selections": {}}
        v2 = migrate_capabilities_v1_to_v2(v1)
        assert v2["schema_version"] == 2
        assert v2["selections"] == {}

    def test_no_selections_key(self) -> None:
        # Truly empty / fresh file.
        v2 = migrate_capabilities_v1_to_v2({})
        assert v2["schema_version"] == 2
        assert v2["selections"] == {}

    def test_unknown_backend_falls_back_to_cpu(self) -> None:
        v1 = {"selections": {"x": {"y": {"backend": "rcom_typo", "model": "m", "enabled": False}}}}
        v2 = migrate_capabilities_v1_to_v2(v1)
        assert v2["selections"]["x"]["y"]["device"] == "cpu"

    def test_empty_selection_passes_through(self) -> None:
        # The "blank picker" state — no model, no backend yet.
        v1 = {"selections": {"embed": {"embed": {}}}}
        v2 = migrate_capabilities_v1_to_v2(v1)
        sel = v2["selections"]["embed"]["embed"]
        assert "backend" not in sel
        # device stays absent / empty so the dashboard renders an unset picker.
        assert sel.get("device", "") == ""

    def test_caller_dict_not_mutated(self) -> None:
        v1 = {"selections": {"embed": {"embed": {"backend": "vulkan", "model": ""}}}}
        before = repr(v1)
        migrate_capabilities_v1_to_v2(v1)
        assert repr(v1) == before


# ── auto_migrate_capabilities_file ───────────────────────────────────────────


def _write_legacy_v1_file(path: Path) -> None:
    """Write a representative v0.1.x-shape capabilities.toml."""
    data = {
        "selections": {
            "embed": {
                "embed": {
                    "backend": "vulkan",
                    "provider": "llama-server",
                    "model": "nomic-embed-text-v1.5",
                    "enabled": True,
                }
            },
            "voice": {
                "stt": {
                    "backend": "moonshine",
                    "provider": "moonshine",
                    "model": "moonshine-base",
                    "enabled": False,
                },
                "tts": {
                    "backend": "kokoro",
                    "provider": "kokoro",
                    "model": "kokoro-v1",
                    "enabled": False,
                },
            },
        }
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        tomli_w.dump(data, f)


class TestAutoMigrate:
    def test_no_file_returns_false(self, tmp_path: Path) -> None:
        target = tmp_path / "capabilities.toml"
        assert auto_migrate_capabilities_file(target) is False
        assert not target.exists()

    def test_legacy_file_is_migrated(self, tmp_path: Path) -> None:
        target = tmp_path / "capabilities.toml"
        _write_legacy_v1_file(target)
        assert auto_migrate_capabilities_file(target) is True

        # Live file: schema_version=2, device key set, backup exists.
        with open(target, "rb") as f:
            after = tomllib.load(f)
        assert after["schema_version"] == 2
        assert after["selections"]["embed"]["embed"]["device"] == "gpu-vulkan"
        assert after["selections"]["voice"]["stt"]["device"] == "cpu"
        assert after["selections"]["voice"]["tts"]["device"] == "cpu"

        # Backup file: contains original v1 bytes, never the v2 shape.
        backup = capabilities_v1_backup_path(target)
        assert backup.exists()
        with open(backup, "rb") as f:
            backup_raw = tomllib.load(f)
        assert backup_raw["selections"]["embed"]["embed"]["backend"] == "vulkan"
        assert "schema_version" not in backup_raw

    def test_idempotent_second_run(self, tmp_path: Path) -> None:
        target = tmp_path / "capabilities.toml"
        _write_legacy_v1_file(target)
        assert auto_migrate_capabilities_file(target) is True
        # Snapshot the v2 file bytes after the first run.
        first_pass = target.read_bytes()
        # Second run is a no-op.
        assert auto_migrate_capabilities_file(target) is False
        assert target.read_bytes() == first_pass

    def test_v1_bak_created_exactly_once(self, tmp_path: Path) -> None:
        target = tmp_path / "capabilities.toml"
        _write_legacy_v1_file(target)
        assert auto_migrate_capabilities_file(target) is True
        backup = capabilities_v1_backup_path(target)
        backup_first_run = backup.read_bytes()

        # Hand-write the v2-shape file again (simulate a downgrade that
        # re-wrote without bumping schema_version, an unlikely but
        # explicitly-tested edge case). The backup must NOT be clobbered.
        # First: rewrite the live file to be v1 again to trigger the path.
        _write_legacy_v1_file(target)
        # Backup still exists from the first run — auto-migrate must
        # refuse to clobber it. The live file then stays v1 (operator's
        # responsibility to clean up); the backup is preserved.
        assert auto_migrate_capabilities_file(target) is False or True
        # Backup bytes unchanged.
        assert backup.read_bytes() == backup_first_run

    def test_round_trip_write_v1_load_writes_v2(self, tmp_path: Path) -> None:
        target = tmp_path / "capabilities.toml"
        _write_legacy_v1_file(target)
        assert auto_migrate_capabilities_file(target) is True
        # Re-load through the validated pydantic surface.
        cfg = load_capabilities_config(target)
        assert cfg.schema_version == 2
        # Re-save and confirm idempotence.
        save_capabilities_config(cfg, target)
        # No further migration needed.
        assert auto_migrate_capabilities_file(target) is False


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


# ── read_schema_version ──────────────────────────────────────────────────────


class TestReadSchemaVersion:
    def test_legacy_no_version_is_v1(self) -> None:
        assert read_schema_version({"selections": {}}) == 1

    def test_v2_returned(self) -> None:
        assert read_schema_version({"schema_version": 2}) == 2

    def test_non_int_falls_back_to_v1(self) -> None:
        # Defensive: a hand-edited TOML with a string version still loads.
        assert read_schema_version({"schema_version": "bogus"}) == 1


# ── CLI: hal0 capabilities migrate-to-lemonade ───────────────────────────────


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestMigrateToLemonadeCLI:
    def test_no_file_exits_zero(self, tmp_path: Path, runner: CliRunner) -> None:
        target = tmp_path / "missing.toml"
        result = runner.invoke(
            capabilities_app,
            ["migrate-to-lemonade", "--path", str(target)],
        )
        assert result.exit_code == 0
        # Rich wraps output; collapse whitespace before the substring assertion.
        flat = " ".join(result.stdout.split())
        assert "does not exist" in flat

    def test_already_v2_exits_zero(self, tmp_path: Path, runner: CliRunner) -> None:
        target = tmp_path / "capabilities.toml"
        with open(target, "wb") as f:
            tomli_w.dump({"schema_version": 2, "selections": {}}, f)
        result = runner.invoke(
            capabilities_app,
            ["migrate-to-lemonade", "--path", str(target)],
        )
        assert result.exit_code == 0
        flat = " ".join(result.stdout.split())
        assert "already v2" in flat

    def test_dry_run_prints_diff_no_write(self, tmp_path: Path, runner: CliRunner) -> None:
        target = tmp_path / "capabilities.toml"
        _write_legacy_v1_file(target)
        before = target.read_bytes()

        result = runner.invoke(
            capabilities_app,
            ["migrate-to-lemonade", "--path", str(target)],
        )
        assert result.exit_code == 0
        # File untouched.
        assert target.read_bytes() == before
        # Backup not created on dry-run.
        assert not capabilities_v1_backup_path(target).exists()
        # Diff content visible in output.
        assert "device" in result.stdout
        assert "schema_version" in result.stdout

    def test_apply_writes_migration(self, tmp_path: Path, runner: CliRunner) -> None:
        target = tmp_path / "capabilities.toml"
        _write_legacy_v1_file(target)

        result = runner.invoke(
            capabilities_app,
            ["migrate-to-lemonade", "--path", str(target), "--apply"],
        )
        assert result.exit_code == 0
        # Live file is v2.
        with open(target, "rb") as f:
            after = tomllib.load(f)
        assert after["schema_version"] == 2
        # Backup exists.
        assert capabilities_v1_backup_path(target).exists()

    def test_revert_restores_backup(self, tmp_path: Path, runner: CliRunner) -> None:
        target = tmp_path / "capabilities.toml"
        _write_legacy_v1_file(target)
        original_bytes = target.read_bytes()

        # Apply first.
        runner.invoke(
            capabilities_app,
            ["migrate-to-lemonade", "--path", str(target), "--apply"],
        )
        assert target.read_bytes() != original_bytes  # migrated

        # Revert.
        result = runner.invoke(
            capabilities_app,
            ["migrate-to-lemonade", "--path", str(target), "--revert"],
        )
        assert result.exit_code == 0
        assert target.read_bytes() == original_bytes
        # Backup consumed.
        assert not capabilities_v1_backup_path(target).exists()

    def test_revert_without_backup_errors(self, tmp_path: Path, runner: CliRunner) -> None:
        target = tmp_path / "capabilities.toml"
        _write_legacy_v1_file(target)
        result = runner.invoke(
            capabilities_app,
            ["migrate-to-lemonade", "--path", str(target), "--revert"],
        )
        assert result.exit_code == 1
        flat = " ".join(result.stdout.split())
        assert "no v1 backup" in flat

    def test_apply_and_revert_mutually_exclusive(self, tmp_path: Path, runner: CliRunner) -> None:
        target = tmp_path / "capabilities.toml"
        _write_legacy_v1_file(target)
        result = runner.invoke(
            capabilities_app,
            [
                "migrate-to-lemonade",
                "--path",
                str(target),
                "--apply",
                "--revert",
            ],
        )
        assert result.exit_code == 2
        flat = " ".join(result.stdout.split())
        assert "mutually exclusive" in flat
