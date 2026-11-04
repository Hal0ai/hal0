"""A fresh non-dev installer config must bypass upgrade-only v4 defaults."""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

from hal0.config.schema import CURRENT_SCHEMA_VERSION

INSTALL = Path(__file__).resolve().parents[2] / "installer" / "install.sh"


def test_fresh_seed_keeps_shared_memory_defaults_after_post_activation(tmp_path: Path) -> None:
    source = INSTALL.read_text(encoding="utf-8")
    seed = source.rsplit('HAL0_TOML="${ETC_DIR}/hal0.toml"', 1)[1].split(
        "# Make the config world-readable", 1
    )[0]
    etc = tmp_path / "etc" / "hal0"
    etc.mkdir(parents=True)
    env = {**os.environ, "HAL0_HOME": str(tmp_path), "ETC_DIR": str(etc), "PY": sys.executable}
    result = subprocess.run(
        ["bash", "-c", 'HAL0_TOML="${ETC_DIR}/hal0.toml"\ninfo() { :; }\n' + seed],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    config_file = etc / "hal0.toml"
    with config_file.open("rb") as fh:
        assert tomllib.load(fh)["meta"]["schema_version"] == CURRENT_SCHEMA_VERSION

    # This is the installer's post-activation entrypoint with host actions off.
    code = """\
from hal0.updater.updater import run_post_activation_migrations
run_post_activation_migrations(
    skip_catalogue_sync=True, repair_hermes_venv=False,
    upgrade_memory_engine_venv=False, converge_companions=False,
    converge_podman_forward=False, fetch_model_bundles=False,
)
from hal0.config.loader import load_hal0_config
cfg = load_hal0_config()
assert cfg.memory.graph.extraction_slot == 'memory'
assert cfg.memory.priority.enabled is True
"""
    migrated = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=False
    )
    assert migrated.returncode == 0, migrated.stderr
