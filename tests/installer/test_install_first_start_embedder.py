"""install.sh puts a new install's memory engine on the configured embedder (#3518).

The installer started a new engine on the base unit, which names no embedder,
so every new store was built with the engine default whatever
``[memory.models]`` said. ``hal0.memory.first_start`` now runs before the
engine's first start. These are static checks over the script text plus a
driven copy of the two shell pieces, the pattern the other installer tests use
(there is no systemd harness for install.sh).

Also here: bank seeding waits for hal0-api to pick the engine up. hal0-api
starts before the engine on a new install and seeding raced its 30 s re-probe,
which is what "memory bank seeding incomplete" was on every new box.
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


# ── bank seeding waits for hal0-api to have the engine ────────────────────────


def _extract(func_name: str) -> str:
    start = _TEXT.index(f"{func_name}()")
    end = _TEXT.index("\n}\n", start) + 3
    return _TEXT[start:end]


def _drive_wait(tmp_path: Path, curl_body: str, timeout: int) -> subprocess.CompletedProcess[str]:
    curl = tmp_path / "curl"
    curl.write_text(f"#!/usr/bin/env bash\n{curl_body}\n", encoding="utf-8")
    curl.chmod(curl.stat().st_mode | stat.S_IEXEC)
    script = tmp_path / "drive.sh"
    script.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\nHAL0_PORT=8080\n"
        f"{_extract('wait_memory_provider_ready')}\n"
        f"wait_memory_provider_ready {timeout}\n",
        encoding="utf-8",
    )
    return subprocess.run(
        ["bash", str(script)],
        env={"PATH": f"{tmp_path}:/usr/bin:/bin", "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_seed_wait_returns_once_hal0_api_has_the_engine(tmp_path: Path) -> None:
    """The fallback provider answers ``engine: null`` until the re-probe swaps it."""
    count = tmp_path / "n"
    body = (
        f'n=$(cat "{count}" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "{count}"\n'
        '[[ "$*" == *"/api/memory/engine"* ]] || exit 2\n'
        "if [ $n -ge 3 ]; then\n"
        """  echo '{"enabled":true,"engine":"hindsight","reachable":true}'\n"""
        "else\n"
        """  echo '{"enabled":true,"engine":null,"reachable":false}'\n"""
        "fi"
    )
    res = _drive_wait(tmp_path, body, 10)

    assert res.returncode == 0, res.stderr
    assert int(count.read_text()) == 3


def test_seed_wait_accepts_spaced_json(tmp_path: Path) -> None:
    res = _drive_wait(tmp_path, """echo '{"enabled": true, "engine": "hindsight"}'""", 5)
    assert res.returncode == 0, res.stderr


def test_seed_wait_times_out_when_hal0_api_is_down(tmp_path: Path) -> None:
    res = _drive_wait(tmp_path, "exit 7", 1)
    assert res.returncode == 1


def test_seeding_waits_for_the_provider_first() -> None:
    wait = _TEXT.index("wait_memory_provider_ready 45")
    seed = _TEXT.index("if _seed_bank shared ")
    assert _TEXT.index('curl -fsS "http://127.0.0.1:9177/health"') < wait < seed
    # A timeout warns and seeding is still attempted.
    assert "|| warn" in _TEXT[wait:seed]


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


# ── the bank manifests have two copies; they must not drift ───────────────────


def test_seed_manifests_match_hal0_memory_bank_seed() -> None:
    """install.sh keeps shell literals so seeding works when the Python side is broken."""
    import json
    import re

    from hal0.memory.bank_seed import SEED_BANKS

    found = dict(re.findall(r"_seed_bank (\S+) '(\{.*?\})' ?[\\;]", _TEXT))
    assert set(found) == set(SEED_BANKS)
    for bank, body in found.items():
        assert json.loads(body) == SEED_BANKS[bank], bank
