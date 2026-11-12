"""install.sh puts a new install's memory engine on the configured embedder (#3518).

The installer started a new engine on the base unit, which names no embedder,
so every new store was built with the engine default whatever
``[memory.models]`` said. ``hal0.memory.first_start`` now runs before the
engine's first start. These are static checks over the script text plus a
driven copy of the two shell pieces, the pattern the other installer tests use
(there is no systemd harness for install.sh).

Also here: bank seeding goes through ``hal0 memory bank seed`` (#3557), which
names the banks from the layout record and ``[memory]`` and talks to the
engine directly, so it neither names a bank in shell nor waits for hal0-api.
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest

INSTALL_SH = Path(__file__).resolve().parents[2] / "installer" / "install.sh"
_TEXT = INSTALL_SH.read_text(encoding="utf-8")

_STEP = '"${VENV_DIR}/bin/python" -I -m hal0.memory.first_start'
_UNIT_INSTALL = 'install -m644 "${HINDSIGHT_UNIT_SRC}" /etc/systemd/system/hindsight-api.service'
_FIRST_START = "systemctl enable --now hindsight-api"
_MIGRATIONS = "from hal0.updater.updater import run_post_activation_migrations"
_CONFIG_SEED = 'HAL0_TOML="${ETC_DIR}/hal0.toml"'


def test_the_step_is_called_exactly_once() -> None:
    assert _TEXT.count(_STEP) == 1


def test_step_runs_after_the_config_exists_and_before_the_first_engine_start() -> None:
    config = _TEXT.index(_CONFIG_SEED)
    migrations = _TEXT.index(_MIGRATIONS)
    unit = _TEXT.index(_UNIT_INSTALL)
    step = _TEXT.index(_STEP)
    start = _TEXT.index(_FIRST_START)

    assert config < migrations < unit < step < start, (
        "hal0.memory.first_start must run once hal0.toml is seeded and migrated and the "
        "engine's unit file is installed, and before 'systemctl enable --now hindsight-api': "
        "an engine started first builds its store with the engine default (#3518)"
    )


def test_nothing_starts_the_engine_before_the_step() -> None:
    before = _TEXT[: _TEXT.index(_STEP)]
    for verb in ("start", "restart", "enable --now", "enable"):
        assert f"systemctl {verb} hindsight-api" not in before, (
            f"'systemctl {verb} hindsight-api' runs before hal0.memory.first_start"
        )
    assert _TEXT.count(_FIRST_START) == 1


def test_the_installer_reloads_units_after_the_step() -> None:
    """The drop-in the step writes is read only at unit load."""
    step = _TEXT.index(_STEP)
    start = _TEXT.index(_FIRST_START)
    assert "systemctl daemon-reload" in _TEXT[step:start]


def test_the_step_cannot_abort_the_install() -> None:
    """Under ``set -e`` a bare non-zero exit would end the whole install."""
    line = next(ln for ln in _TEXT.splitlines() if _STEP in ln)
    assert line.rstrip().endswith("|| hs_first_rc=$?")


def test_the_step_is_inside_the_memory_engine_block() -> None:
    """Skipped with the engine (HAL0_SKIP_HINDSIGHT=1) and without its venv."""
    gate = _TEXT.index(
        'if [[ "${HAL0_SKIP_HINDSIGHT:-0}" -ne 1 && -f "${HINDSIGHT_UNIT_SRC}" ]]; then'
    )
    venv_gate = _TEXT.index('if [[ -x "${HS_DIR}/.venv/bin/hindsight-api" ]]; then', gate)
    assert gate < venv_gate < _TEXT.index(_STEP)


# ── the exit-code handling, driven ────────────────────────────────────────────


def _step_block() -> str:
    start = _TEXT.index("hs_first_rc=0")
    end = _TEXT.index("esac", start) + len("esac")
    return _TEXT[start:end]


@pytest.mark.parametrize(
    ("rc", "warned"),
    [
        (0, ""),
        (3, "could not start on the configured embedder"),
        (5, "did not start"),
        (6, "could not download its models"),
        (4, "could not be set up before its first start"),
        (1, "could not be set up before its first start"),
    ],
)
def test_step_exit_codes_warn_and_never_abort(tmp_path: Path, rc: int, warned: str) -> None:
    python = tmp_path / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(f"#!/usr/bin/env bash\necho 'memory embedder: line'\nexit {rc}\n")
    python.chmod(python.stat().st_mode | stat.S_IEXEC)
    script = tmp_path / "drive.sh"
    script.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        'warn() { echo "WARN $*"; }\n'
        f'VENV_DIR="{tmp_path}/venv"\nHAL0_BIN=hal0\n'
        f"{_step_block()}\n"
        "echo REACHED\n",
        encoding="utf-8",
    )
    res = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, timeout=30, check=False
    )

    assert res.returncode == 0, res.stderr
    assert "memory embedder: line" in res.stdout and "REACHED" in res.stdout
    if warned:
        assert "WARN the memory engine" in res.stdout and warned in res.stdout
    else:
        assert "WARN" not in res.stdout
    if rc != 0:
        # Every not-healthy exit names both places to look.
        assert "journalctl -u hindsight-api" in res.stdout and "hal0 doctor all" in res.stdout
    if rc == 6:
        assert "when the model host is reachable" in res.stdout
        assert "engine default" not in res.stdout
    if rc == 5:
        # The generic line: no blame on the embedder, and both downloads named.
        assert "reranker" in res.stdout and "journalctl -u hindsight-api" in res.stdout
        assert "doctor" in res.stdout and "engine default" not in res.stdout


# ── the installer's own start cannot abort the install ────────────────────────

_RESET = "systemctl reset-failed hindsight-api 2>/dev/null || true"


def test_reset_failed_runs_between_the_step_and_a_tolerant_enable() -> None:
    """A unit that reached its start limit is refused by ``enable --now``."""
    step = _TEXT.index(_STEP)
    reset = _TEXT.index(_RESET)
    start = _TEXT.index(_FIRST_START)
    assert step < reset < start
    assert _TEXT[start:].startswith(_FIRST_START + " \\\n")
    assert _TEXT[start:].splitlines()[1].lstrip().startswith("|| warn ")


@pytest.mark.parametrize("enable_rc", [0, 1])
def test_a_failing_enable_warns_and_the_install_goes_on(tmp_path: Path, enable_rc: int) -> None:
    start = _TEXT.index(_RESET)
    end = _TEXT.index("\n", _TEXT.index("|| warn", _TEXT.index(_FIRST_START)))
    log = tmp_path / "systemctl.log"
    systemctl = tmp_path / "systemctl"
    systemctl.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$*" >> "{log}"\n'
        f'[[ "$1" == enable ]] && exit {enable_rc}\n'
        '[[ "$1" == reset-failed ]] && exit 1\n'
        "exit 0\n"
    )
    systemctl.chmod(systemctl.stat().st_mode | stat.S_IEXEC)
    script = tmp_path / "drive.sh"
    script.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        'warn() { echo "WARN $*"; }\nHAL0_BIN=hal0\n'
        f"{_TEXT[start:end]}\n"
        "echo REACHED\n",
        encoding="utf-8",
    )
    res = subprocess.run(
        ["bash", str(script)],
        env={"PATH": f"{tmp_path}:/usr/bin:/bin", "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert res.returncode == 0, res.stderr
    assert "REACHED" in res.stdout
    assert ("WARN could not enable and start the memory engine" in res.stdout) == bool(enable_rc)
    assert log.read_text().splitlines() == [
        "reset-failed hindsight-api",
        "enable --now hindsight-api",
    ]


def test_the_generic_not_healthy_line_names_the_downloads_and_both_checks() -> None:
    start = _TEXT.index('warn "hindsight-api not healthy yet')
    block = _TEXT[start : _TEXT.index("\n            fi", start)]
    assert "embedding and reranker models" in block
    assert "journalctl -u hindsight-api" in block and "doctor all" in block


def test_the_failed_enable_line_names_both_checks() -> None:
    line = next(
        ln for ln in _TEXT.splitlines() if "could not enable and start the memory engine" in ln
    )
    assert "journalctl -u hindsight-api" in line and "doctor all" in line


# ── the installer seeds through hal0, never names a bank (#3557) ──────────────


def _seed_block() -> str:
    start = _TEXT.index('"${HAL0_BIN}" memory bank seed')
    return _TEXT[start : _TEXT.index("\n                esac", start)]


def test_seeding_runs_the_cli_with_the_installers_paths() -> None:
    """ADR-0058 decision 7: the banks come from the layout and [memory], so a
    two-bank install has hal0:shared and hal0:private and no stray bank."""
    assert _TEXT.count('"${HAL0_BIN}" memory bank seed') == 1
    line_start = _TEXT.rindex("\n", 0, _TEXT.index('"${HAL0_BIN}" memory bank seed'))
    block = _TEXT[line_start : line_start + 400]
    assert 'HAL0_HOME="${HAL0_HOME_FOR_OWUI}"' in block
    assert '--config "${HAL0_TOML}" --state-dir "${VAR_DIR}/state"' in block


def test_seeding_names_no_bank_and_posts_no_manifest() -> None:
    assert "/api/memory/banks/" not in _TEXT
    assert "private:hermes" not in _TEXT
    assert "retain_mission" not in _TEXT
    assert "seed_target_main" not in _TEXT
    assert "wait_memory_provider_ready" not in _TEXT


def test_seeding_failure_warns_and_the_install_carries_on() -> None:
    block = _seed_block()
    assert "|| seed_rc=$?" in block
    assert '*) warn "memory bank seeding incomplete' in block


def test_seeding_says_seeded_only_on_exit_0() -> None:
    """Exit 3 (nothing to seed) and 4 (deferred) are not reported as a seed."""
    from hal0.cli.memory_bank_commands import SEED_EXIT_DEFERRED, SEED_EXIT_NOTHING

    block = _seed_block()
    assert '0) info "seeded the memory banks' in block
    assert f'{SEED_EXIT_NOTHING}) info "memory banks already set up' in block
    assert f'{SEED_EXIT_DEFERRED}) info "memory bank seeding deferred' in block
    assert block.count("seeded the memory banks") == 1


def test_seeding_runs_after_the_engine_answers_and_after_the_layout_is_recorded() -> None:
    seed = _TEXT.index('"${HAL0_BIN}" memory bank seed')
    assert _TEXT.index('curl -fsS "http://127.0.0.1:9177/health"') < seed
    # run_post_activation_migrations writes memory-layout.json.
    assert _TEXT.index(_MIGRATIONS) < seed
