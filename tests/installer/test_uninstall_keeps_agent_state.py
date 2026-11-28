"""A default uninstall keeps agent configuration and state; ``--purge`` removes it (#4009).

The uninstaller's header promised that ``/etc/hal0`` and ``/var/lib/hal0`` are
kept, but its bundled-agents step deleted ``/etc/hal0/agents`` (each agent's
policy file, the operator's override env file, the terminal-tool decision) and
``/var/lib/hal0/agents`` (the identity secret every agent key derives from, the
session secret, each agent's data directory) on every run.

An agent's upstream packages stay with that state on a default run: the
companion that removes them runs only under a confirmed ``--purge``. If a
default run removed the packages while keeping the seed and data directory,
``hal0 agent install`` on the re-install would return early ("already
present") and report an agent with nothing behind it.

These run the real ``installer/uninstall.sh`` in ``--dev`` mode against a
throwaway prefix, which needs no root and touches nothing outside the prefix.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_UNINSTALL = Path(__file__).resolve().parents[2] / "installer" / "uninstall.sh"


def _tree(prefix: Path, *, companion_exit: int = 0) -> dict[str, Path]:
    etc = prefix / "etc" / "hal0" / "agents"
    var = prefix / "var" / "lib" / "hal0" / "agents"
    (var / "pi" / "workspace").mkdir(parents=True)
    (var / "hermes").mkdir()
    etc.mkdir(parents=True)
    files = {
        "policy": etc / "hermes.toml",
        "pi_policy": etc / "pi.toml",
        "override": etc / "hermes.override.env",
        "terminal": etc / "hermes-terminal-tool",
        "identity": var / "identity.bin",
        "session": var / "secret.bin",
        "workspace_file": var / "pi" / "workspace" / "notes.md",
        "hermes_data": var / "hermes" / "state.json",
    }
    for f in files.values():
        f.write_text("operator data\n")
    # The companion records what it could still see when it ran: the agent's
    # own data directory must not have been removed underneath it.
    seen = prefix / "companion-saw"
    companion = var / "pi" / "uninstall.sh"
    companion.write_text(
        "#!/bin/sh\n"
        f'[ -f "{files["workspace_file"]}" ] && [ -f "{files["pi_policy"]}" ] '
        f'&& echo intact > "{seen}"\n'
        f"exit {companion_exit}\n"
    )
    companion.chmod(0o755)
    files["companion"] = companion
    files["seen"] = seen
    # Something the default run does remove, to prove the teardown really ran.
    venvs = prefix / "var" / "lib" / "hal0" / "venvs" / "pi"
    venvs.mkdir(parents=True)
    files["venvs"] = venvs
    return files


def _run(prefix: Path, *args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(_UNINSTALL), "--dev", *args],
        env={"PATH": "/usr/bin:/bin", "HAL0_PREFIX": str(prefix), "HOME": str(prefix)},
        cwd=prefix,
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


_KEPT = ("policy", "pi_policy", "override", "terminal", "identity", "session")
_STATE = (*_KEPT, "workspace_file", "hermes_data")


def test_default_run_keeps_agent_config_and_state(tmp_path: Path) -> None:
    files = _tree(tmp_path)
    proc = _run(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for name in _STATE:
        assert files[name].read_text() == "operator data\n", name
    assert "agent policy" in proc.stdout + proc.stderr
    assert not files["venvs"].exists()


def test_default_run_does_not_run_or_remove_the_companion(tmp_path: Path) -> None:
    """The agent's packages stay with its kept state, so a re-install works."""
    files = _tree(tmp_path)
    proc = _run(tmp_path)
    assert not files["seen"].exists()
    assert files["companion"].exists()
    assert "uninstall companion" not in proc.stdout + proc.stderr


def test_purge_runs_the_companion_with_its_directory_intact_then_removes_all(
    tmp_path: Path,
) -> None:
    files = _tree(tmp_path)
    proc = _run(tmp_path, "--purge", "--force")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert files["seen"].read_text().strip() == "intact"
    assert "Ran uninstall companion for pi" in proc.stdout
    assert "No uninstall companion for hermes" in proc.stdout
    for name in (*_STATE, "companion"):
        assert not files[name].exists(), name


def test_purge_with_a_failing_companion_still_removes_everything(tmp_path: Path) -> None:
    files = _tree(tmp_path, companion_exit=3)
    proc = _run(tmp_path, "--purge", "--force")
    assert "Companion uninstall for pi returned non-zero" in proc.stdout + proc.stderr
    for name in _STATE:
        assert not files[name].exists(), name


def test_declined_purge_keeps_agent_state_and_runs_no_companion(tmp_path: Path) -> None:
    """Anything but DELETE at the prompt must leave agents as a default run does."""
    files = _tree(tmp_path)
    proc = _run(tmp_path, "--purge", stdin="no\n")
    assert "Aborted" in proc.stdout + proc.stderr
    for name in _STATE:
        assert files[name].read_text() == "operator data\n", name
    assert not files["seen"].exists()
    assert files["companion"].exists()


def test_header_and_help_say_what_is_kept() -> None:
    text = _UNINSTALL.read_text(encoding="utf-8")
    header = text.split("set -uo pipefail")[0]
    assert "/etc/hal0/agents" in header
    assert "stay installed with that state" in header
    helped = subprocess.run(
        ["bash", str(_UNINSTALL), "--help"], capture_output=True, text=True, check=True
    ).stdout
    assert "agent policy" in helped and "identity and data" in helped
    assert "own packages stay" in helped
    default, purge = helped.split("--purge        ")[:2]
    assert "bundled agents' packages" in purge and "bundled agents' packages" not in default
