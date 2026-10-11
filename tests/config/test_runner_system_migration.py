"""The one-shot runner-system conversion (``hal0.config.migrations.runner_system``)."""

from __future__ import annotations

import sqlite3
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
    image_is_current,
    map_runner_key,
    migrate_runner_system,
    plan_env,
    plan_hal0_toml,
    plan_profiles,
    plan_slot,
    restore_backup,
)
from hal0.runners import RUNNER_IMAGES

KNOWN = frozenset(RUNNER_IMAGES)
REPOS = frozenset(
    {f"ghcr.io/hal0ai/hal0-runner-{k}" for k in RUNNER_IMAGES if k != "cuda"}
    | {"ghcr.io/ggml-org/llama.cpp"}
)
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
        repos=REPOS,
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
        ("strix", "standard"),
        ("promptforge", "standard"),
        ("upstream", "standard"),
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
    ("ref", "current"),
    [
        (CURRENT, True),
        ("ghcr.io/hal0ai/hal0-runner-standard@sha256:" + "a" * 64, True),
        ("ghcr.io/hal0ai/hal0-runner-cpu:b1-r1@sha256:" + "a" * 64, True),
        ("ghcr.io/ggml-org/llama.cpp:server-cuda-b11541", True),
        ("ghcr.io/ggml-org/llama.cpp:server-cuda", False),
        ("ghcr.io/hal0ai/hal0-runner-standard:latest", False),
        ("ghcr.io/hal0ai/hal0-runner-standard", False),
        ("ghcr.io/hal0ai/hal0-combined:0826", False),
        ("ghcr.io/hal0ai/hal0-combined-upstream:0829", False),
        ("ghcr.io/hal0ai/hal0-rocmfpx:ade07ba", False),
        ("localhost/hal0-rocmfpx:vulkan-minicpm5", False),
        ("ghcr.io/hal0ai/hal0-strix-vulkan:0831", False),
        ("ghcr.io/hal0ai/hal0-promptforge:v2.3-qwen38", False),
        ("ghcr.io/hal0ai/hal0-toolbox-cpu:v1", False),
        ("ghcr.io/hal0ai/hal0-comfyui:latest", False),
        ("ghcr.io/hal0ai/amd-strix-halo-toolboxes:vulkan-radv-server", False),
        ("docker.io/someone/custom:1", False),
    ],
)
def test_image_is_current(ref: str, current: bool) -> None:
    assert image_is_current(ref, REPOS) is current


# ── slots ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("old", ["rocmfpx", "vulkanfpx", "strix", "promptforge"])
def test_retired_slot_runtime_becomes_standard(old: str) -> None:
    out, actions, warnings = _slot({"name": "s", "binary": old, "device": "gpu-rocm"})
    assert out is not None and out["binary"] == "standard"
    assert actions and not warnings


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
                "bogus": CURRENT,
            }
        },
        "other": {"x": 1},
    }
    actions: list[str] = []
    warnings: list[str] = []
    out = plan_hal0_toml(raw, known=KNOWN, repos=REPOS, actions=actions, warnings=warnings)
    assert out is not None
    assert out["slots"]["default_images"] == {
        "standard": CURRENT,
        "flm": "ghcr.io/hal0ai/hal0-runner-flm:0.9.44-r1",
    }
    assert out["other"] == {"x": 1}
    assert len(warnings) == 4  # duplicate standard, strix image, cpu image, bogus key
    assert plan_hal0_toml(out, known=KNOWN, repos=REPOS, actions=[], warnings=[]) is None


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
    out = plan_profiles(raw, known=KNOWN, repos=REPOS, actions=[], warnings=warnings)
    assert out is not None
    assert out["profile"]["a"] == {"runner": "standard", "flags": "-fa on"}
    assert out["profile"]["b"] == {}
    assert out["profile"]["c"] == {"runner": "cpu"}
    assert out["profile"]["d"] == {"flags": "x"}
    assert plan_profiles(out, known=KNOWN, repos=REPOS, actions=[], warnings=[]) is None


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
    out = plan_env(text, known=KNOWN, repos=REPOS, actions=[], warnings=warnings)
    assert out == (
        "# api.env\n"
        "HF_TOKEN=abc\n"
        f"HAL0_RUNNER_IMAGE_STANDARD={CURRENT}\n"
        "export HAL0_RUNNER_IMAGE_CPU=ghcr.io/hal0ai/hal0-runner-cpu:b1-r1\n"
    )
    assert len(warnings) == 2
    assert plan_env(out, known=KNOWN, repos=REPOS, actions=[], warnings=[]) is None


# ── end to end ────────────────────────────────────────────────────────────────


def _write_toml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomli_w.dumps(data), encoding="utf-8")


def _read_toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


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
    _write_toml(paths.profiles_toml(), {"profile": {"p": {"runner": "strix"}}})
    paths.api_env().write_text(
        "HAL0_TOOLBOX_IMAGE_ROCMFPX=ghcr.io/hal0ai/hal0-combined:0826\n", encoding="utf-8"
    )
    _seed_db(
        [
            ("plain", "/m/plain-Q4_K_M.gguf", "Q4_K_M", None),
            ("fpx", "/m/Qwen-ROCmFP4-STRIX.gguf", "ROCmFP4", "rocmfpx"),
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
    assert _read_toml(paths.profiles_toml())["profile"]["p"] == {"runner": "standard"}
    assert paths.api_env().read_text() == ""
    conn = sqlite3.connect(str(paths.db_path()))
    assert conn.execute("SELECT preferred_runner FROM model WHERE id='fpx'").fetchone() == (
        "standard",
    )
    conn.close()

    with tarfile.open(report.backup) as tar:
        names = tar.getnames()
    assert any(n.endswith("slots/agent.toml") for n in names)
    assert any(n.endswith("hal0.db") for n in names)


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


def test_failure_restores_every_write(old_box: dict[str, Path], monkeypatch) -> None:
    originals = {p: p.read_bytes() for p in old_box["slots"].glob("*.toml")}
    originals[old_box["hal0_toml"]] = old_box["hal0_toml"].read_bytes()
    real = rs._atomic_write
    calls = {"n": 0}

    def flaky(path: Path, data: bytes) -> None:
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("disk full")
        real(path, data)

    monkeypatch.setattr(rs, "_atomic_write", flaky)
    with pytest.raises(RunnerMigrationError, match="rolled back"):
        migrate_runner_system()
    monkeypatch.setattr(rs, "_atomic_write", real)
    for path, data in originals.items():
        assert path.read_bytes() == data, path


def test_unparseable_toml_fails_before_any_write(old_box: dict[str, Path]) -> None:
    (old_box["slots"] / "broken.toml").write_text("name = [", encoding="utf-8")
    before = (old_box["slots"] / "agent.toml").read_bytes()
    with pytest.raises(RunnerMigrationError, match=r"broken\.toml"):
        migrate_runner_system()
    assert (old_box["slots"] / "agent.toml").read_bytes() == before


def test_no_backup_no_write(old_box: dict[str, Path], monkeypatch) -> None:
    before = (old_box["slots"] / "agent.toml").read_bytes()

    def boom(*_a: Any, **_k: Any) -> Path:
        raise OSError("read-only")

    monkeypatch.setattr(rs, "_write_backup", boom)
    with pytest.raises(RunnerMigrationError, match="nothing was changed"):
        migrate_runner_system()
    assert (old_box["slots"] / "agent.toml").read_bytes() == before


def test_restore_backup_puts_the_old_files_back(old_box: dict[str, Path], tmp_path: Path) -> None:
    before = (old_box["slots"] / "agent.toml").read_bytes()
    report = migrate_runner_system()
    assert report.backup
    restored = restore_backup(report.backup)
    assert any(r.endswith("agent.toml") for r in restored)
    assert (old_box["slots"] / "agent.toml").read_bytes() == before


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


def test_cli_reports_failure(old_box: dict[str, Path]) -> None:
    from hal0.cli.runner_image_commands import app

    (old_box["slots"] / "broken.toml").write_text("name = [", encoding="utf-8")
    result = CliRunner().invoke(app, ["migrate", "--apply"])
    assert result.exit_code == 1
