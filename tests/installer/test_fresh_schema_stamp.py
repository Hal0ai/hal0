"""A fresh non-dev installer config must bypass upgrade-only v4 defaults.

Two things are proved here:

1. The fresh seed is stamped with the CURRENT schema version, so the
   post-activation migration does not re-apply the upgrade-only v4
   defaults (``extraction_slot = "utility"``, priority off).
2. The stamp is produced by the VENV interpreter, not the base
   ``${PY}``. hal0 is pip-installed into the venv only, so a base
   interpreter without hal0 importable cannot answer the stamp. The
   seed must still work when ``${PY}`` is a python that cannot import
   hal0 — otherwise it writes an empty ``schema_version`` and leaves a
   /etc/hal0/hal0.toml that tomllib cannot parse, which the API then
   silently discards in favour of defaults.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import tomllib
import venv
from pathlib import Path

from hal0.config.schema import CURRENT_SCHEMA_VERSION

INSTALL = Path(__file__).resolve().parents[2] / "installer" / "install.sh"


def _seed_block() -> str:
    source = INSTALL.read_text(encoding="utf-8")
    return source.rsplit('HAL0_TOML="${ETC_DIR}/hal0.toml"', 1)[1].split(
        "# Make the config world-readable", 1
    )[0]


def _base_python_without_hal0(tmp_path: Path) -> str:
    """A python that cannot import hal0 — the stock-box case."""
    # An empty venv has no site-packages and therefore no hal0.
    bare = tmp_path / "bare-python"
    venv.EnvBuilder(with_pip=False, system_site_packages=False).create(bare)
    python = bare / "bin" / "python"
    assert python.exists(), python
    probe = subprocess.run(
        [str(python), "-I", "-c", "import hal0"], capture_output=True, text=True, check=False
    )
    assert probe.returncode != 0, "the bare interpreter must not be able to import hal0"
    return str(python)


def _venv_python_with_hal0(tmp_path: Path) -> str:
    """A python that CAN import hal0 — the venv the installer created."""
    target = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True, system_site_packages=False).create(target)
    python = target / "bin" / "python"
    subprocess.run(
        [str(python), "-m", "pip", "install", "--quiet", "-e", str(INSTALL.parents[1])],
        capture_output=True,
        text=True,
        check=True,
    )
    probe = subprocess.run(
        [str(python), "-I", "-c", "import hal0.config.schema"], capture_output=True, text=True
    )
    assert probe.returncode == 0, probe.stderr
    return str(python)


def test_fresh_seed_uses_venv_interpreter_not_base_python(tmp_path: Path) -> None:
    """The stamp must not depend on hal0 being importable by ${PY}."""
    etc = tmp_path / "etc" / "hal0"
    etc.mkdir(parents=True)
    base_python = _base_python_without_hal0(tmp_path)
    _venv_python_with_hal0(tmp_path)
    env = {
        **os.environ,
        "HAL0_HOME": str(tmp_path),
        "ETC_DIR": str(etc),
        "PY": base_python,
        "VENV_DIR": str(tmp_path / "venv"),
    }
    result = subprocess.run(
        ["bash", "-c", 'HAL0_TOML="${ETC_DIR}/hal0.toml"\ninfo() { :; }\n' + _seed_block()],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    config_file = etc / "hal0.toml"
    with config_file.open("rb") as fh:
        # The whole file must parse — an empty schema_version is a
        # TOMLDecodeError, not a wrong number.
        config = tomllib.load(fh)
    assert config["meta"]["schema_version"] == CURRENT_SCHEMA_VERSION


def test_fresh_seed_keeps_shared_memory_defaults_after_post_activation(tmp_path: Path) -> None:
    etc = tmp_path / "etc" / "hal0"
    etc.mkdir(parents=True)
    venv_python = _venv_python_with_hal0(tmp_path)
    env = {
        **os.environ,
        "HAL0_HOME": str(tmp_path),
        "ETC_DIR": str(etc),
        "PY": sys.executable,
        "VENV_DIR": str(tmp_path / "venv"),
    }
    result = subprocess.run(
        ["bash", "-c", 'HAL0_TOML="${ETC_DIR}/hal0.toml"\ninfo() { :; }\n' + _seed_block()],
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
    code = textwrap.dedent(
        """\
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
    )
    migrated = subprocess.run(
        [venv_python, "-c", code], env=env, capture_output=True, text=True, check=False
    )
    assert migrated.returncode == 0, migrated.stderr
