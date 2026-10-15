"""The one-shot runner-system conversion (``hal0.config.migrations.runner_system``)."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import stat
import struct
import tarfile
import tomllib
from pathlib import Path
from typing import Any

import pytest
import tomli_w
from typer.testing import CliRunner

from hal0.config import paths
from hal0.config.migrations import runner_system as rs
from hal0.config.migrations.runner_system import (
    RunnerMigrationError,
    image_is_hal0_runner,
    image_is_retired,
    map_runner_key,
    migrate_runner_system,
    plan_env,
    plan_hal0_toml,
    plan_profiles,
    plan_slot,
    restore_backup,
    restore_marker,
)
from hal0.runners import RUNNER_IMAGES

KNOWN = frozenset(RUNNER_IMAGES)
CURRENT = "ghcr.io/hal0ai/hal0-runner-standard:b11552-r1a2b3c4"


def _no_fork(_mid: str) -> str | None:
    return None


def _slot(raw: dict[str, Any], *, fork: Any = _no_fork) -> tuple[dict | None, list, list]:
    actions: list[str] = []
    warnings: list[str] = []
    out = plan_slot(
        raw,
        name="s",
        known=KNOWN,
        fork_quant_of=fork,
        actions=actions,
        warnings=warnings,
    )
    return out, actions, warnings


# ── classification ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("key", "new"),
    [
        ("rocmfpx", "standard"),
        ("vulkanfpx", "standard"),
        ("vulkan", "standard"),
        ("rocm", "standard"),
        # Retired OPTIONAL runners never become the default runner.
        ("strix", None),
        ("promptforge", None),
        ("upstream", None),
        ("standard", "standard"),
        ("cpu", "cpu"),
        ("cuda", "cuda"),
        ("comfyui", "comfyui"),
        ("nonsense", None),
    ],
)
def test_map_runner_key(key: str, new: str | None) -> None:
    assert map_runner_key(key, KNOWN) == new


@pytest.mark.parametrize(
    ("ref", "retired"),
    [
        ("ghcr.io/hal0ai/hal0-combined:0826", True),
        ("ghcr.io/hal0ai/hal0-combined-upstream:0829", True),
        ("ghcr.io/hal0ai/hal0-rocmfpx:ade07ba", True),
        ("localhost/hal0-rocmfpx:vulkan-minicpm5", True),
        ("ghcr.io/hal0ai/hal0-strix-vulkan:0831", True),
        ("ghcr.io/hal0ai/hal0-promptforge:v2.3-qwen38", True),
        ("ghcr.io/hal0ai/hal0-toolbox-cpu:v1", True),
        ("ghcr.io/hal0ai/hal0-comfyui:latest", True),
        ("ghcr.io/hal0ai/amd-strix-halo-toolboxes:vulkan-radv-server", True),
        ("docker.io/kyuz0/amd-strix-halo-comfyui:latest", True),
        ("kyuz0/amd-strix-halo-toolboxes:vulkan-radv", True),
        ("index.docker.io/kyuz0/amd-strix-halo-toolboxes:rocm", True),
        ("ghcr.io/hal0ai/hal0-runner-standard:b11552-r1@sha256:" + "a" * 64, False),
        # The floating CUDA tag the old default used is retired; a release tag
        # or a digest is a fixed image and is kept.
        ("ghcr.io/ggml-org/llama.cpp:server-cuda", True),
        ("ghcr.io/ggml-org/llama.cpp", True),
        ("ghcr.io/ggml-org/llama.cpp:server-cuda-b11541", False),
        ("ghcr.io/ggml-org/llama.cpp:server-cuda@sha256:" + "b" * 64, False),
        # Only the exact repos hal0 shipped: the same names elsewhere are the
        # operator's.
        ("docker.io/kyuz0/some-other-image:1", False),
        ("ghcr.io/someone/amd-strix-halo-toolboxes:vulkan", False),
        ("ghcr.io/someone/hal0-rocmfpx:mine", False),
        ("docker.io/someone/hal0-toolbox-cpu:v1", False),
        ("docker.io/someone/custom:1", False),
        ("localhost/my-llama:dev", False),
    ],
)
def test_image_is_retired(ref: str, retired: bool) -> None:
    assert image_is_retired(ref) is retired


@pytest.mark.parametrize(
    ("ref", "runner"),
    [
        (CURRENT, True),
        ("ghcr.io/ggml-org/llama.cpp:server-cuda-b11541", True),
        ("docker.io/someone/custom:1", False),
    ],
)
def test_image_is_hal0_runner(ref: str, runner: bool) -> None:
    assert image_is_hal0_runner(ref) is runner


# ── slots ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("old", ["rocmfpx", "vulkanfpx", "vulkan", "rocm"])
def test_retired_default_slot_runtime_becomes_standard(old: str) -> None:
    out, actions, warnings = _slot({"name": "s", "binary": old, "device": "gpu-rocm"})
    assert out is not None and out["binary"] == "standard"
    assert actions and not warnings


@pytest.mark.parametrize("old", ["strix", "promptforge", "upstream"])
def test_retired_optional_slot_runtime_is_cleared_loudly(old: str) -> None:
    out, actions, warnings = _slot({"name": "s", "binary": old, "device": "gpu-rocm"})
    assert out is not None and "binary" not in out
    assert not actions
    assert any(f"optional {old!r} runner was removed" in w for w in warnings)


def test_unknown_slot_runtime_is_cleared_loudly() -> None:
    out, _actions, warnings = _slot({"binary": "mystery"})
    assert out is not None and "binary" not in out
    assert any("mystery" in w for w in warnings)


def test_nested_slot_table_is_converted() -> None:
    raw = {"slot": {"binary": "vulkanfpx", "image": "ghcr.io/hal0ai/hal0-combined:0826"}}
    out, _a, warnings = _slot(raw)
    assert out is not None
    assert out["slot"] == {"binary": "standard"}
    assert any("hal0-combined" in w for w in warnings)


def test_retired_image_pin_is_dropped_and_current_one_kept() -> None:
    out, _a, warnings = _slot({"image_pin": "ghcr.io/hal0ai/hal0-strix-vulkan:0831"})
    assert out is not None and "image_pin" not in out
    assert warnings
    assert _slot({"image_pin": CURRENT})[0] is None


def test_custom_image_pin_is_kept_and_reported() -> None:
    kept: list[str] = []
    out = plan_slot(
        {"name": "s", "image_pin": "docker.io/someone/my-llama:dev"},
        name="s",
        known=KNOWN,
        fork_quant_of=_no_fork,
        actions=[],
        warnings=[],
        kept=kept,
    )
    assert out is None
    assert kept and "my-llama" in kept[0]


def test_image_gen_table_is_not_an_image_pin() -> None:
    """``[image]`` is the image-gen settings table (#599), never a ref."""
    assert _slot({"image": {"steps": 4}})[0] is None


def test_fork_quant_model_is_unassigned_and_the_slot_kept() -> None:
    raw = {
        "name": "agent",
        "device": "gpu-rocm",
        "binary": "standard",
        "model": {"default": "m", "context_size": 4096},
    }
    out, _a, warnings = _slot(raw, fork=lambda mid: "ROCmFP4" if mid == "m" else None)
    assert out is not None
    assert out["model"] == {"default": "", "context_size": 4096}
    assert out["device"] == "gpu-rocm" and out["binary"] == "standard"
    assert any("ROCmFP4" in w and "unassigned" in w for w in warnings)


@pytest.mark.parametrize(
    ("old", "new"),
    [("chadrock-moe", "moe"), ("chadrock-dense", "dense"), ("promptforge", "dense")],
)
def test_slot_on_a_removed_profile_moves_to_its_replacement(old: str, new: str) -> None:
    out, actions, _w = _slot({"name": "agent", "profile": old, "binary": "standard"})
    assert out is not None and out["profile"] == new
    assert any(old in a for a in actions)
    assert _slot(out)[0] is None


def test_removed_profile_definitions_are_dropped_from_profiles_toml() -> None:
    raw = {
        "profile": {
            "chadrock-moe": {"runner": "rocmfpx", "flags": "--x"},
            "promptforge": {"flags": "--y"},
            "mine": {"flags": "--z"},
        },
        "legacy_seeds_migrated": True,
    }
    warnings: list[str] = []
    out = plan_profiles(raw, known=KNOWN, actions=[], warnings=warnings)
    assert out is not None
    assert out["profile"] == {"mine": {"flags": "--z"}}
    assert out["legacy_seeds_migrated"] is True
    assert len(warnings) == 2
    assert plan_profiles(out, known=KNOWN, actions=[], warnings=[]) is None


def test_converted_slot_is_a_noop_on_rerun() -> None:
    out, _a, _w = _slot({"binary": "rocmfpx", "image_pin": "ghcr.io/hal0ai/hal0-combined:0826"})
    assert out is not None
    assert _slot(out)[0] is None


# ── hal0.toml / profiles / env ────────────────────────────────────────────────


def test_default_images_are_renamed_and_pruned() -> None:
    raw = {
        "slots": {
            "default_images": {
                "vulkanfpx": CURRENT,
                "rocmfpx": "ghcr.io/hal0ai/hal0-runner-standard:other",
                "strix": "ghcr.io/hal0ai/hal0-strix-vulkan:0831",
                "cpu": "ghcr.io/hal0ai/hal0-toolbox-cpu:v1",
                "flm": "ghcr.io/hal0ai/hal0-runner-flm:0.9.44-r1",
                "kokoro": "docker.io/someone/kokoro:dev",
                "bogus": CURRENT,
            }
        },
        "other": {"x": 1},
    }
    actions: list[str] = []
    warnings: list[str] = []
    out = plan_hal0_toml(raw, known=KNOWN, actions=actions, warnings=warnings)
    assert out is not None
    assert out["slots"]["default_images"] == {
        # rocmfpx beats its vulkanfpx alias, whatever the order.
        "standard": "ghcr.io/hal0ai/hal0-runner-standard:other",
        "flm": "ghcr.io/hal0ai/hal0-runner-flm:0.9.44-r1",
        "kokoro": "docker.io/someone/kokoro:dev",
    }
    assert out["other"] == {"x": 1}
    # duplicate standard, strix (optional runner), cpu image, bogus key
    assert len(warnings) == 4
    assert plan_hal0_toml(out, known=KNOWN, actions=[], warnings=[]) is None


@pytest.mark.parametrize("optional", ["strix", "promptforge", "upstream"])
def test_optional_runner_override_never_becomes_the_default_image(optional: str) -> None:
    """A custom image an operator set for a retired optional runner is
    dropped loudly; it must not become every default slot's image."""
    raw = {"slots": {"default_images": {optional: "docker.io/me/special-build:1"}}}
    warnings: list[str] = []
    out = plan_hal0_toml(raw, known=KNOWN, actions=[], warnings=warnings)
    assert out is not None and out["slots"]["default_images"] == {}
    assert any("special-build" in w and "was removed" in w for w in warnings)


def test_the_canonical_override_wins_over_its_alias_whatever_the_order() -> None:
    """The old schema let ``rocmfpx`` beat its ``vulkanfpx`` alias regardless
    of TOML order; the conversion keeps that precedence."""
    raw = {
        "slots": {
            "default_images": {
                "vulkanfpx": "docker.io/me/alias:1",
                "rocmfpx": "docker.io/me/canonical:1",
            }
        }
    }
    warnings: list[str] = []
    out = plan_hal0_toml(raw, known=KNOWN, actions=[], warnings=warnings)
    assert out is not None
    assert out["slots"]["default_images"] == {"standard": "docker.io/me/canonical:1"}
    assert any("alias:1" in w for w in warnings)


def test_env_canonical_override_wins_over_its_alias_whatever_the_order() -> None:
    text = (
        "HAL0_TOOLBOX_IMAGE_VULKANFPX=docker.io/me/alias:1\n"
        "HAL0_TOOLBOX_IMAGE_ROCMFPX=docker.io/me/canonical:1\n"
    )
    warnings: list[str] = []
    out = plan_env(text, known=KNOWN, actions=[], warnings=warnings)
    assert out == "HAL0_RUNNER_IMAGE_STANDARD=docker.io/me/canonical:1\n"
    assert any("alias:1" in w for w in warnings)


def test_migration_never_judges_the_operator_model_id(old_box: dict[str, Path]) -> None:
    """A plain file under a fork-sounding registry id keeps its slot."""
    conn = sqlite3.connect(str(paths.db_path()))
    conn.execute(
        "INSERT INTO model VALUES ('my-ROCmFPX', '/m/hal0-brain-sft-F16.gguf', 'F16', NULL, NULL)"
    )
    conn.commit()
    conn.close()
    _write_toml(
        old_box["slots"] / "brain.toml",
        {"name": "brain", "port": 8090, "model": {"default": "my-ROCmFPX"}},
    )
    migrate_runner_system()
    assert _read_toml(old_box["slots"] / "brain.toml")["model"]["default"] == "my-ROCmFPX"


def _gguf(tensor_types: list[int]) -> bytes:
    """A minimal GGUF v3: one string KV, then one tensor info per type."""

    def _s(text: str) -> bytes:
        raw = text.encode()
        return struct.pack("<Q", len(raw)) + raw

    out = b"GGUF" + struct.pack("<I", 3)
    out += struct.pack("<Q", len(tensor_types)) + struct.pack("<Q", 1)
    out += _s("general.architecture") + struct.pack("<I", 8) + _s("llama")
    for i, ttype in enumerate(tensor_types):
        out += _s(f"blk.{i}.weight") + struct.pack("<I", 2)
        out += struct.pack("<QQ", 64, 64) + struct.pack("<I", ttype) + struct.pack("<Q", 0)
    return out + b"\x00" * 64


def _seed_slot_on(model_id: str, path: Path | str, tmp_hal0_home_slots: Path):
    _seed_db([(model_id, str(path), "Q8_0", None)])
    _write_toml(
        tmp_hal0_home_slots / "brain.toml",
        {"name": "brain", "port": 8090, "binary": "standard", "model": {"default": model_id}},
    )


def test_migration_unassigns_a_renamed_fork_build_by_its_contents(
    tmp_hal0_home: str, tmp_path: Path
) -> None:
    """A fork file with an innocuous name is caught by its tensor types."""
    model = tmp_path / "hal0-brain-sft-q8-rocmfpx" / "model.gguf"
    model.parent.mkdir()
    model.write_bytes(_gguf([0, 12, 103]))
    _seed_slot_on("brain-sft", model, paths.slots_config_dir())

    report = migrate_runner_system()

    assert _read_toml(paths.slots_config_dir() / "brain.toml")["model"]["default"] == ""
    assert any("103" in w and "unassigned" in w for w in report.warnings)


def _set_shards(model_id: str, shards: list[Path]) -> None:
    """Record a split model's further files the way the registry does."""
    conn = sqlite3.connect(str(paths.db_path()))
    conn.execute("ALTER TABLE model ADD COLUMN extra TEXT")
    conn.execute(
        "UPDATE model SET extra = ? WHERE id = ?",
        (json.dumps({"shards": [str(p) for p in shards]}), model_id),
    )
    conn.commit()
    conn.close()


def test_migration_checks_every_shard_of_a_split_model(tmp_hal0_home: str, tmp_path: Path) -> None:
    first = tmp_path / "m-00001-of-00002.gguf"
    first.write_bytes(_gguf([0, 12]))
    second = tmp_path / "m-00002-of-00002.gguf"
    second.write_bytes(_gguf([0, 103]))
    _seed_slot_on("split", first, paths.slots_config_dir())
    _set_shards("split", [second])

    report = migrate_runner_system()

    assert _read_toml(paths.slots_config_dir() / "brain.toml")["model"]["default"] == ""
    assert any("103" in w and "unassigned" in w for w in report.warnings)


def test_migration_keeps_a_split_model_whose_shards_are_stock_or_missing(
    tmp_hal0_home: str, tmp_path: Path
) -> None:
    first = tmp_path / "m-00001-of-00003.gguf"
    first.write_bytes(_gguf([0, 12]))
    second = tmp_path / "m-00002-of-00003.gguf"
    second.write_bytes(_gguf([0, 14]))
    _seed_slot_on("split", first, paths.slots_config_dir())
    _set_shards("split", [second, tmp_path / "m-00003-of-00003.gguf"])

    migrate_runner_system()

    assert _read_toml(paths.slots_config_dir() / "brain.toml")["model"]["default"] == "split"


def test_migration_keeps_a_stock_gguf(tmp_hal0_home: str, tmp_path: Path) -> None:
    model = tmp_path / "model.gguf"
    model.write_bytes(_gguf([0, 12, 14, 39]))
    _seed_slot_on("brain-sft", model, paths.slots_config_dir())

    migrate_runner_system()

    assert _read_toml(paths.slots_config_dir() / "brain.toml")["model"]["default"] == "brain-sft"


@pytest.mark.parametrize("kind", ["missing", "truncated", "not_gguf", "directory"])
def test_migration_survives_an_unreadable_model_file(
    tmp_hal0_home: str, tmp_path: Path, kind: str
) -> None:
    """Content is judged only when readable; the name check still applies."""
    model = tmp_path / "model.gguf"
    if kind == "truncated":
        model.write_bytes(_gguf([0, 12])[:30])
    elif kind == "not_gguf":
        model.write_bytes(b"not a gguf at all" * 4)
    elif kind == "directory":
        model.mkdir()
    _seed_slot_on("brain-sft", model, paths.slots_config_dir())

    migrate_runner_system()

    assert _read_toml(paths.slots_config_dir() / "brain.toml")["model"]["default"] == "brain-sft"


def test_content_check_failure_never_fails_the_migration(
    tmp_hal0_home: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib

    # ``hal0.registry.detect`` is also a re-exported function name.
    detect = importlib.import_module("hal0.registry.detect")

    def _boom(_path: object) -> str | None:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(detect, "unsupported_gguf_contents", _boom)
    model = tmp_path / "model.gguf"
    model.write_bytes(_gguf([0, 103]))
    _seed_slot_on("brain-sft", model, paths.slots_config_dir())

    migrate_runner_system()

    assert _read_toml(paths.slots_config_dir() / "brain.toml")["model"]["default"] == "brain-sft"


def test_current_key_wins_over_a_renamed_one() -> None:
    raw = {
        "slots": {
            "default_images": {
                "rocmfpx": "docker.io/me/old:1",
                "standard": "docker.io/me/new:1",
            }
        }
    }
    warnings: list[str] = []
    out = plan_hal0_toml(raw, known=KNOWN, actions=[], warnings=warnings)
    assert out is not None
    assert out["slots"]["default_images"] == {"standard": "docker.io/me/new:1"}
    assert any("old:1" in w for w in warnings)


def test_profiles_runner_and_image() -> None:
    raw = {
        "profile": {
            "a": {"runner": "rocmfpx", "flags": "-fa on"},
            "b": {"runner": "ghost"},
            "c": {"runner": "cpu", "image": "ghcr.io/hal0ai/hal0-combined:0826"},
            "d": {"flags": "x"},
        }
    }
    warnings: list[str] = []
    out = plan_profiles(raw, known=KNOWN, actions=[], warnings=warnings)
    assert out is not None
    assert out["profile"]["a"] == {"runner": "standard", "flags": "-fa on"}
    assert out["profile"]["b"] == {}
    assert out["profile"]["c"] == {"runner": "cpu"}
    assert out["profile"]["d"] == {"flags": "x"}
    assert plan_profiles(out, known=KNOWN, actions=[], warnings=[]) is None


def test_env_overrides_are_renamed_or_dropped() -> None:
    text = (
        "# api.env\n"
        "HF_TOKEN=abc\n"
        f"HAL0_TOOLBOX_IMAGE_ROCMFPX={CURRENT}\n"
        'export HAL0_TOOLBOX_IMAGE_CPU="ghcr.io/hal0ai/hal0-runner-cpu:b1-r1"\n'
        "HAL0_TOOLBOX_IMAGE_VULKAN=ghcr.io/hal0ai/hal0-toolbox-vulkan:v1\n"
        "HAL0_TOOLBOX_IMAGE_WHATEVER=x\n"
    )
    warnings: list[str] = []
    out = plan_env(text, known=KNOWN, actions=[], warnings=warnings)
    assert out == (
        "# api.env\n"
        "HF_TOKEN=abc\n"
        f"HAL0_RUNNER_IMAGE_STANDARD={CURRENT}\n"
        'export HAL0_RUNNER_IMAGE_CPU="ghcr.io/hal0ai/hal0-runner-cpu:b1-r1"\n'
    )
    assert len(warnings) == 2
    assert plan_env(out, known=KNOWN, actions=[], warnings=[]) is None


def test_env_value_keeps_its_quoting() -> None:
    """An EnvironmentFile or a shell reads quoted and unquoted values
    differently, so the converted line keeps the operator's quoting."""
    text = "HAL0_TOOLBOX_IMAGE_ROCMFPX='docker.io/me/a:1 # not a comment'\n"
    out = plan_env(text, known=KNOWN, actions=[], warnings=[])
    assert out == "HAL0_RUNNER_IMAGE_STANDARD='docker.io/me/a:1 # not a comment'\n"


@pytest.mark.parametrize("key", ["VULKAN", "ROCM"])
def test_env_inert_default_keys_are_dropped_not_promoted(key: str) -> None:
    """vulkan/rocm overrides were inert on the release this upgrades from; a
    leftover custom value must not become every default slot's image."""
    warnings: list[str] = []
    out = plan_env(
        f"HAL0_TOOLBOX_IMAGE_{key}=docker.io/me/x:1\n",
        known=KNOWN,
        actions=[],
        warnings=warnings,
    )
    assert out == ""
    assert any("inert" in w for w in warnings)


@pytest.mark.parametrize("key", ["vulkan", "rocm"])
def test_default_images_inert_default_keys_are_dropped(key: str) -> None:
    raw = {"slots": {"default_images": {key: "docker.io/me/x:1"}}}
    warnings: list[str] = []
    out = plan_hal0_toml(raw, known=KNOWN, actions=[], warnings=warnings)
    assert out is not None and out["slots"]["default_images"] == {}
    assert any("inert" in w for w in warnings)


@pytest.mark.parametrize("old", ["vulkan", "rocm"])
def test_binary_vulkan_and_rocm_still_become_standard(old: str) -> None:
    out, _a, warnings = _slot({"binary": old})
    assert out is not None and out["binary"] == "standard"
    assert not warnings


def test_env_optional_runner_override_is_dropped() -> None:
    warnings: list[str] = []
    out = plan_env(
        "HAL0_TOOLBOX_IMAGE_STRIX=docker.io/me/strix:1\n",
        known=KNOWN,
        actions=[],
        warnings=warnings,
    )
    assert out == ""
    assert any("strix" in w and "was removed" in w for w in warnings)


def test_env_converted_lines_are_deduped() -> None:
    text = (
        "HAL0_RUNNER_IMAGE_STANDARD=docker.io/me/a:1\n"
        "HAL0_TOOLBOX_IMAGE_ROCMFPX=docker.io/me/a:1\n"
        "HAL0_TOOLBOX_IMAGE_VULKAN=docker.io/me/b:1\n"
        "HAL0_TOOLBOX_IMAGE_CPU=docker.io/me/c:1\n"
        "HAL0_TOOLBOX_IMAGE_CPU=docker.io/me/c:1\n"
    )
    actions: list[str] = []
    warnings: list[str] = []
    out = plan_env(text, known=KNOWN, actions=actions, warnings=warnings)
    assert out == (
        "HAL0_RUNNER_IMAGE_STANDARD=docker.io/me/a:1\nHAL0_RUNNER_IMAGE_CPU=docker.io/me/c:1\n"
    )
    # The identical duplicates go quietly; the conflicting VULKAN value loudly.
    assert [w for w in warnings if "b:1" in w]
    assert len(warnings) == 1
    assert plan_env(out, known=KNOWN, actions=[], warnings=[]) is None


# ── end to end ────────────────────────────────────────────────────────────────


def _write_toml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomli_w.dumps(data), encoding="utf-8")


def _read_toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _preferred_runners() -> dict[str, str | None]:
    conn = sqlite3.connect(str(paths.db_path()))
    try:
        return dict(conn.execute("SELECT id, preferred_runner FROM model").fetchall())
    finally:
        conn.close()


def _seed_db(models: list[tuple[str, str, str | None, str | None]]) -> Path:
    db = paths.db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE model (id TEXT PRIMARY KEY, path TEXT, quant TEXT, "
        "hf_filename TEXT, preferred_runner TEXT)"
    )
    conn.executemany(
        "INSERT INTO model VALUES (?, ?, ?, ?, ?)",
        [(mid, path, quant, None, runner) for mid, path, quant, runner in models],
    )
    conn.commit()
    conn.close()
    return db


@pytest.fixture
def old_box(tmp_hal0_home: str) -> dict[str, Path]:
    """An install still on the old runner system, every shape at once."""
    hal0_toml = paths.hal0_toml()
    _write_toml(
        hal0_toml, {"slots": {"default_images": {"rocmfpx": "ghcr.io/hal0ai/hal0-combined:0826"}}}
    )
    slots = paths.slots_config_dir()
    _write_toml(
        slots / "chat.toml",
        {"name": "chat", "port": 8081, "binary": "vulkanfpx", "model": {"default": "plain"}},
    )
    _write_toml(
        slots / "agent.toml",
        {
            "name": "agent",
            "port": 8082,
            "binary": "rocmfpx",
            "image_pin": "ghcr.io/hal0ai/hal0-combined:0826",
            "model": {"default": "fpx"},
        },
    )
    _write_toml(
        slots / "cpu.toml",
        {"name": "cpu", "port": 8083, "binary": "cpu", "model": {"default": "plain"}},
    )
    _write_toml(
        paths.profiles_toml(), {"profile": {"p": {"runner": "rocmfpx"}, "q": {"runner": "strix"}}}
    )
    paths.api_env().write_text(
        "HAL0_TOOLBOX_IMAGE_ROCMFPX=ghcr.io/hal0ai/hal0-combined:0826\n", encoding="utf-8"
    )
    _seed_db(
        [
            ("plain", "/m/plain-Q4_K_M.gguf", "Q4_K_M", None),
            ("fpx", "/m/Qwen-ROCmFP4-STRIX.gguf", "ROCmFP4", "rocmfpx"),
            ("forge", "/m/forge-Q4_K_M.gguf", "Q4_K_M", "promptforge"),
        ]
    )
    return {"hal0_toml": hal0_toml, "slots": slots}


def test_dry_run_writes_nothing(old_box: dict[str, Path]) -> None:
    before = {p: p.read_bytes() for p in old_box["slots"].glob("*.toml")}
    report = migrate_runner_system(dry_run=True)
    assert report.changed and report.dry_run
    assert {p: p.read_bytes() for p in old_box["slots"].glob("*.toml")} == before
    assert not (paths.var_lib() / "backups").exists()


def test_apply_converts_everything_with_a_backup(old_box: dict[str, Path]) -> None:
    report = migrate_runner_system()
    assert report.backup and Path(report.backup).is_file()

    assert _read_toml(old_box["hal0_toml"])["slots"]["default_images"] == {}
    chat = _read_toml(old_box["slots"] / "chat.toml")
    assert chat["binary"] == "standard" and chat["model"]["default"] == "plain"
    agent = _read_toml(old_box["slots"] / "agent.toml")
    assert agent["binary"] == "standard"
    assert "image_pin" not in agent
    assert agent["model"]["default"] == ""
    assert any("'fpx'" in w and "unassigned" in w for w in report.warnings)
    assert _read_toml(paths.profiles_toml())["profile"] == {"p": {"runner": "standard"}, "q": {}}
    assert paths.api_env().read_text() == ""
    assert _preferred_runners() == {"plain": None, "fpx": "standard", "forge": None}

    with tarfile.open(report.backup) as tar:
        names = set(tar.getnames())
        runners = json.loads(tar.extractfile("preferred_runner.json").read())  # type: ignore[union-attr]
    # Owned files by role, plus the old preferred runners; never the DB.
    assert names == {
        "hal0.toml",
        "slots/chat.toml",
        "slots/agent.toml",
        "profiles.toml",
        "api.env",
        "preferred_runner.json",
    }
    assert sorted(runners, key=lambda r: r["id"]) == [
        {"id": "forge", "preferred_runner": "promptforge"},
        {"id": "fpx", "preferred_runner": "rocmfpx"},
    ]


def test_backup_is_private(old_box: dict[str, Path]) -> None:
    """Each archive is 0600; a directory this call creates lands at the
    ownership table's declared shared mode (#2657), not a 0700 that locks out
    whichever of root and hal0 did not create it."""
    report = migrate_runner_system()
    assert report.backup
    backup = Path(report.backup)
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert stat.S_IMODE(backup.parent.stat().st_mode) == 0o2775


def test_existing_backups_dir_is_left_as_the_operator_set_it(old_box: dict[str, Path]) -> None:
    """A packaged box has a root-owned, group-writable backups dir that the
    hal0 service user cannot chmod (EPERM); the migration must not try."""
    backups = paths.var_lib() / "backups"
    backups.mkdir(parents=True)
    backups.chmod(0o2775)
    report = migrate_runner_system()
    assert report.backup
    assert stat.S_IMODE(backups.stat().st_mode) == 0o2775
    assert stat.S_IMODE(Path(report.backup).stat().st_mode) == 0o600


_AS_ROOT = os.geteuid() == 0


@pytest.mark.skipif(_AS_ROOT, reason="root bypasses the directory write check")
def test_unwritable_backups_dir_we_own_is_repaired(old_box: dict[str, Path]) -> None:
    """#2657: an existing backups dir the current user owns but cannot write
    (a umask-022 birth left it 2755, or worse) is made 2775 before the backup."""
    backups = paths.var_lib() / "backups"
    backups.mkdir(parents=True)
    backups.chmod(0o2555)
    report = migrate_runner_system()
    assert report.backup and Path(report.backup).is_file()
    assert stat.S_IMODE(backups.stat().st_mode) == 0o2775


@pytest.mark.skipif(_AS_ROOT, reason="root bypasses the directory write check")
def test_unwritable_backups_dir_owned_by_another_user_names_the_remedy(
    old_box: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """#2657: hal0-api (User=hal0) meeting a root:hal0 2755 backups dir cannot
    repair it. The failure names the directory, its owner and mode, and the
    one-line remedy, and still says nothing was changed."""
    backups = paths.var_lib() / "backups"
    backups.mkdir(parents=True)
    backups.chmod(0o2555)
    real_uid = os.geteuid()
    monkeypatch.setattr(rs.os, "geteuid", lambda: real_uid + 1)  # someone else owns it
    before = old_box["hal0_toml"].read_bytes()
    with pytest.raises(RunnerMigrationError) as info:
        migrate_runner_system()
    msg = str(info.value)
    assert "nothing was changed" in msg
    assert str(backups) in msg
    assert "2555" in msg
    assert f"chmod 2775 {backups}" in msg
    assert "hal0 doctor perms --fix" in msg
    assert stat.S_IMODE(backups.stat().st_mode) == 0o2555
    assert old_box["hal0_toml"].read_bytes() == before
    assert not any(backups.iterdir())


def test_backups_dir_symlink_is_refused(old_box: dict[str, Path], tmp_path: Path) -> None:
    backups = paths.var_lib() / "backups"
    real = tmp_path / "elsewhere"
    real.mkdir()
    backups.parent.mkdir(parents=True, exist_ok=True)
    backups.symlink_to(real)
    with pytest.raises(RunnerMigrationError):
        migrate_runner_system()
    assert not any(real.iterdir())


def test_converted_config_validates(old_box: dict[str, Path]) -> None:
    """The point of running before validation: the old default_images key
    fails config load, the converted file loads."""
    from hal0.config.loader import ConfigParseError, load_hal0_config, load_slot_config

    with pytest.raises(ConfigParseError):
        load_hal0_config()
    migrate_runner_system()
    load_hal0_config()
    assert load_slot_config("agent").binary == "standard"


def test_second_run_is_a_noop(old_box: dict[str, Path]) -> None:
    migrate_runner_system()
    again = migrate_runner_system()
    assert not again.changed
    assert again.backup is None


def test_failure_restores_every_write_but_keeps_hal0_toml(
    old_box: dict[str, Path], monkeypatch
) -> None:
    """hal0.toml is what stops hal0-api from starting, so it stays converted;
    every later write is rolled back."""
    originals = {p: p.read_bytes() for p in old_box["slots"].glob("*.toml")}
    originals[paths.profiles_toml()] = paths.profiles_toml().read_bytes()
    real = rs._replace_file
    calls = {"n": 0}

    def flaky(path: Path, data: bytes, **kw: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("disk full")
        real(path, data, **kw)

    monkeypatch.setattr(rs, "_replace_file", flaky)
    with pytest.raises(RunnerMigrationError, match=r"hal0\.toml stays converted"):
        migrate_runner_system()
    monkeypatch.setattr(rs, "_replace_file", real)
    for path, data in originals.items():
        assert path.read_bytes() == data, path
    assert _read_toml(old_box["hal0_toml"])["slots"]["default_images"] == {}
    assert _preferred_runners()["fpx"] == "rocmfpx"


def test_a_failed_rollback_names_the_files_and_the_backup(
    old_box: dict[str, Path], monkeypatch
) -> None:
    real = rs._replace_file
    calls = {"n": 0}

    def flaky(path: Path, data: bytes, **kw: Any) -> None:
        calls["n"] += 1
        # 1 = hal0.toml, 2 = first slot, 3 = second slot fails, 4 = the
        # rollback of the first slot fails too.
        if calls["n"] in (3, 4):
            raise OSError("disk full")
        real(path, data, **kw)

    monkeypatch.setattr(rs, "_replace_file", flaky)
    with pytest.raises(RunnerMigrationError, match="could NOT be restored") as info:
        migrate_runner_system()
    assert "--restore" in str(info.value)
    assert info.value.details["not_restored"]


def test_unparseable_file_is_skipped_and_the_rest_converted(old_box: dict[str, Path]) -> None:
    broken = old_box["slots"] / "broken.toml"
    broken.write_text("name = [", encoding="utf-8")
    report = migrate_runner_system()
    assert any("broken.toml" in w and "skipped" in w for w in report.warnings)
    assert broken.read_text() == "name = ["
    assert _read_toml(old_box["slots"] / "agent.toml")["binary"] == "standard"
    assert _read_toml(old_box["hal0_toml"])["slots"]["default_images"] == {}


def test_unparseable_hal0_toml_still_converts_the_slots(old_box: dict[str, Path]) -> None:
    old_box["hal0_toml"].write_text("[slots", encoding="utf-8")
    report = migrate_runner_system()
    assert any("hal0.toml" in w for w in report.warnings)
    assert _read_toml(old_box["slots"] / "agent.toml")["binary"] == "standard"


def test_no_backup_no_write(old_box: dict[str, Path], monkeypatch) -> None:
    before = (old_box["slots"] / "agent.toml").read_bytes()

    def boom(*_a: Any, **_k: Any) -> Path:
        raise OSError("read-only")

    monkeypatch.setattr(rs, "_write_backup", boom)
    with pytest.raises(RunnerMigrationError, match="nothing was changed"):
        migrate_runner_system()
    assert (old_box["slots"] / "agent.toml").read_bytes() == before


@pytest.fixture
def api_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rs, "_api_is_active", lambda: False)


def test_restore_backup_puts_the_old_state_back(old_box: dict[str, Path], api_down: None) -> None:
    before = {p: p.read_bytes() for p in old_box["slots"].glob("*.toml")}
    before[old_box["hal0_toml"]] = old_box["hal0_toml"].read_bytes()
    report = migrate_runner_system()
    assert report.backup
    restored = restore_backup(report.backup)
    assert any(r.endswith("agent.toml") for r in restored)
    for path, data in before.items():
        assert path.read_bytes() == data, path
    assert _preferred_runners() == {"plain": None, "fpx": "rocmfpx", "forge": "promptforge"}


def test_restore_leaves_a_marker_the_boot_conversion_honours(
    old_box: dict[str, Path], api_down: None
) -> None:
    report = migrate_runner_system()
    assert report.backup
    restore_backup(report.backup)
    assert restore_marker().is_file()

    boot = migrate_runner_system(honour_restore_marker=True)
    assert boot.skipped_for_restore and not boot.changed
    assert _read_toml(old_box["slots"] / "agent.toml")["binary"] == "rocmfpx"

    # An update (or --apply) converts again and clears the marker.
    again = migrate_runner_system()
    assert again.changed
    assert not restore_marker().exists()


def test_a_marker_from_another_version_is_ignored(
    old_box: dict[str, Path], api_down: None, monkeypatch
) -> None:
    report = migrate_runner_system()
    assert report.backup
    restore_backup(report.backup)
    assert "version=" in restore_marker().read_text()
    # A different release now runs (the downgrade happened, or a later
    # upgrade): the marker no longer holds the boot conversion back.
    monkeypatch.setattr(rs, "_running_version", lambda: "1.4.0-nightly.20990101")
    boot = migrate_runner_system(honour_restore_marker=True)
    assert not boot.skipped_for_restore
    assert _read_toml(old_box["slots"] / "agent.toml")["binary"] == "standard"


def test_restore_accepts_an_archive_owned_by_the_hal0_user(
    old_box: dict[str, Path], api_down: None, monkeypatch
) -> None:
    """hal0-api writes backups as the hal0 user; root restoring one is fine,
    because only owned members are ever written."""
    import os
    import pwd

    report = migrate_runner_system()
    assert report.backup
    owner = os.getuid()
    monkeypatch.setattr(rs.os, "getuid", lambda: owner + 4242)

    class _Pw:
        pw_uid = owner

    monkeypatch.setattr(pwd, "getpwnam", lambda name: _Pw() if name == "hal0" else None)
    assert restore_backup(report.backup)


def test_restore_refuses_while_the_api_runs(old_box: dict[str, Path], monkeypatch) -> None:
    report = migrate_runner_system()
    assert report.backup
    monkeypatch.setattr(rs, "_api_is_active", lambda: True)
    with pytest.raises(RunnerMigrationError, match="hal0-api is running"):
        restore_backup(report.backup)
    assert _read_toml(old_box["slots"] / "agent.toml")["binary"] == "standard"


def _archive(tmp_path: Path, members: dict[str, bytes], *, mode: int = 0o600) -> Path:
    target = tmp_path / "runners-evil.tar.gz"
    with tarfile.open(target, "w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    target.chmod(mode)
    return target


@pytest.mark.parametrize(
    "member",
    [
        "etc/sudoers.d/hal0",
        "/etc/passwd",
        "slots/../../etc/cron.d/x.toml",
        "slots/nested/x.toml",
        "hal0.db",
        "var/lib/hal0/hal0.db",
    ],
)
def test_restore_refuses_members_it_does_not_own(
    tmp_hal0_home: str, tmp_path: Path, api_down: None, member: str
) -> None:
    archive = _archive(tmp_path, {"hal0.toml": b"", member: b"pwned"})
    with pytest.raises(RunnerMigrationError, match="refusing"):
        restore_backup(archive)
    assert not paths.hal0_toml().exists()


def test_restore_refuses_a_writable_archive(
    tmp_hal0_home: str, tmp_path: Path, api_down: None
) -> None:
    archive = _archive(tmp_path, {"hal0.toml": b""}, mode=0o666)
    with pytest.raises(RunnerMigrationError, match="writable"):
        restore_backup(archive)


def test_restore_refuses_a_symlinked_archive(
    tmp_hal0_home: str, tmp_path: Path, api_down: None
) -> None:
    real = _archive(tmp_path, {"hal0.toml": b""})
    link = tmp_path / "link.tar.gz"
    link.symlink_to(real)
    with pytest.raises(RunnerMigrationError, match="regular file"):
        restore_backup(link)


def test_restore_replaces_a_symlink_instead_of_following_it(
    tmp_hal0_home: str, tmp_path: Path, api_down: None
) -> None:
    victim = tmp_path / "victim"
    victim.write_text("untouched")
    slots = paths.slots_config_dir()
    slots.mkdir(parents=True, exist_ok=True)
    (slots / "chat.toml").symlink_to(victim)
    archive = _archive(tmp_path, {"slots/chat.toml": b'name = "chat"\n'})
    restore_backup(archive)
    assert victim.read_text() == "untouched"
    assert not (slots / "chat.toml").is_symlink()
    assert (slots / "chat.toml").read_text() == 'name = "chat"\n'


def test_fresh_box_has_nothing_to_convert(tmp_hal0_home: str) -> None:
    report = migrate_runner_system()
    assert not report.changed
    assert report.lines()[-1].strip() == "nothing to convert"


# ── CLI ───────────────────────────────────────────────────────────────────────


def test_cli_dry_run_then_apply(old_box: dict[str, Path]) -> None:
    from hal0.cli.runner_image_commands import app

    runner = CliRunner()
    dry = runner.invoke(app, ["migrate"])
    assert dry.exit_code == 0, dry.output
    assert "dry run" in dry.output and "--apply" in dry.output
    assert _read_toml(old_box["slots"] / "agent.toml")["binary"] == "rocmfpx"

    applied = runner.invoke(app, ["migrate", "--apply"])
    assert applied.exit_code == 0, applied.output
    assert "backup:" in applied.output
    assert _read_toml(old_box["slots"] / "agent.toml")["binary"] == "standard"


def test_cli_reports_failure(old_box: dict[str, Path], monkeypatch) -> None:
    from hal0.cli.runner_image_commands import app

    def boom(*_a: Any, **_k: Any) -> Path:
        raise OSError("read-only")

    monkeypatch.setattr(rs, "_write_backup", boom)
    result = CliRunner().invoke(app, ["migrate", "--apply"])
    assert result.exit_code == 1


def test_cli_restore_refusal_exits_1(old_box: dict[str, Path], tmp_path: Path) -> None:
    from hal0.cli.runner_image_commands import app

    archive = _archive(tmp_path, {"hal0.toml": b""}, mode=0o666)
    result = CliRunner().invoke(app, ["migrate", "--restore", str(archive)])
    assert result.exit_code == 1
    assert "writable" in result.output


# ── #2694: an unassigned model is reported with its slot, model and remedy ────


def test_plan_slot_records_the_unassigned_model_with_a_remedy() -> None:
    unassigned: list[dict[str, str]] = []
    out = plan_slot(
        {"name": "brain", "model": {"default": "hal0-brain-sft-q8-rocmfpx"}},
        name="brain",
        known=KNOWN,
        fork_quant_of=lambda _mid: "ROCmFP8",
        actions=[],
        warnings=[],
        unassigned=unassigned,
    )
    assert out is not None and out["model"]["default"] == ""
    assert len(unassigned) == 1
    entry = unassigned[0]
    assert entry["slot"] == "brain"
    assert entry["model"] == "hal0-brain-sft-q8-rocmfpx"
    assert entry["quant"] == "ROCmFP8"
    assert "hal0 slot edit brain --model <id>" in entry["remedy"]
    assert "lfm2.5-2.6b" in entry["remedy"]


def test_migration_report_lists_each_unassigned_slot(tmp_hal0_home: str, tmp_path: Path) -> None:
    model = tmp_path / "hal0-brain-sft-q8-rocmfpx" / "model.gguf"
    model.parent.mkdir()
    model.write_bytes(_gguf([0, 12, 103]))
    _seed_slot_on("brain-sft", model, paths.slots_config_dir())

    report = migrate_runner_system()

    assert [(u["slot"], u["model"]) for u in report.unassigned] == [("brain", "brain-sft")]
    assert "hal0 slot edit brain --model <id>" in report.unassigned[0]["remedy"]
