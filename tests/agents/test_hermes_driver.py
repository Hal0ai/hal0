"""Unit tests for ``hal0.agents.hermes.HermesDriver`` as it stands today.

Phase 8 closeout — these tests target the CURRENT HEAD shape of
``hermes.py`` (the ``_probe_hal0_awareness`` driver, not the in-flight
wrapper rewrite). Team Hermes is rewriting hermes.py in a parallel
worktree and will land their own ``tests/agents/test_hermes_wrapper.py``
file; once both PRs merge, that wrapper test will likely supersede or
extend the suite below.

Coverage mirrors ``tests/agents/test_pi_coder_shim.py``:

* probe returns False  → install raises HermesNotHal0AwareError.
* probe returns True   → install invokes the installer script + writes
                         ``/etc/hal0/agents/hermes.env`` with the
                         expected key set.
* install writes the env file with the Bearer when provided.
* uninstall removes the env file (idempotent on missing).
* status reads off the env file's presence.

Subprocess is faked through ``HermesDriver(runner=_FakeRunner())`` so the
suite stays hermetic — no real ``hermes-agent`` binary, no real
``bash installer/agents/hermes.sh`` invocation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hal0.agents import hermes as hermes_mod
from hal0.agents.hermes import HermesDriver
from hal0.agents.manager import AgentError, HermesNotHal0AwareError

# ── Fake subprocess (parallels test_pi_coder_shim._FakeRunner) ───────────────


class _FakeCompleted:
    returncode = 0


class _FakeRunner:
    """Replaces ``subprocess`` for the driver. Records every ``run()``
    call so tests can assert on argv + env without spawning a shell."""

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self._fail = fail

    def run(
        self,
        argv: list[str],
        *,
        env: dict[str, str] | None = None,
        check: bool = False,
    ) -> _FakeCompleted:
        self.calls.append({"argv": list(argv), "env": dict(env or {}), "check": check})
        if self._fail:
            raise RuntimeError("fake subprocess failure")
        return _FakeCompleted()


# ── Helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture
def _fake_installer_script(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Redirect ``installer_script_path("hermes")`` to a tmp script.

    The repo's installer/agents/ layout is owned by another team —
    rather than creating a real file inside the source tree (which
    would pollute the working copy + sneak into git), we patch the
    resolver to point at a tmpfile we create here. The script body is
    irrelevant because the test suite injects a :class:`_FakeRunner`
    that never actually exec'd it.
    """
    script = tmp_path / "hermes.sh"
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    monkeypatch.setattr(
        hermes_mod,
        "installer_script_path",
        lambda name: script if name == "hermes" else Path(f"/nonexistent/{name}.sh"),
    )
    return script


# ── probe gate ───────────────────────────────────────────────────────────────


def test_install_raises_when_probe_returns_false(tmp_hal0_home: str) -> None:
    """An upstream Hermes that doesn't advertise hal0-awareness must
    short-circuit BEFORE the installer script runs."""
    runner = _FakeRunner()
    driver = HermesDriver(runner=runner, prober=lambda: False)

    with pytest.raises(HermesNotHal0AwareError):
        driver.install(bearer_token="tok")

    # The subprocess must NOT have been touched on the failure path —
    # otherwise we'd be running the installer for a version of Hermes
    # that can't honour ``--hal0-config`` yet.
    assert runner.calls == []


def test_install_runs_installer_when_probe_passes(
    tmp_hal0_home: str,
    _fake_installer_script: Path,
) -> None:
    runner = _FakeRunner()
    driver = HermesDriver(runner=runner, prober=lambda: True)

    driver.install(bearer_token="hal0_tok_abc")

    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call["argv"][0] == "bash"
    assert call["argv"][1] == str(_fake_installer_script)
    # Env handed to bash carries the bearer + the data dir + the API URL.
    env = call["env"]
    assert env["HAL0_BEARER_TOKEN"] == "hal0_tok_abc"
    assert env["HAL0_AGENT_DATA_DIR"].endswith("/agents/hermes")
    assert env["HAL0_API_URL"].startswith("http")


# ── env file write ───────────────────────────────────────────────────────────


def _env_file_path(home: str) -> Path:
    return Path(home) / "etc" / "hal0" / "agents" / "hermes.env"


def test_install_writes_env_file_with_bearer(
    tmp_hal0_home: str,
    _fake_installer_script: Path,
) -> None:
    driver = HermesDriver(runner=_FakeRunner(), prober=lambda: True)
    driver.install(bearer_token="hal0_tok_xyz")

    env_path = _env_file_path(tmp_hal0_home)
    assert env_path.exists()
    content = env_path.read_text(encoding="utf-8")
    assert "HAL0_BEARER_TOKEN=hal0_tok_xyz" in content
    assert "HAL0_API_URL=" in content
    assert "HAL0_MCP_ADMIN_URL=" in content
    assert "HAL0_MCP_MEMORY_URL=" in content


def test_install_writes_env_file_without_token(
    tmp_hal0_home: str,
    _fake_installer_script: Path,
) -> None:
    driver = HermesDriver(runner=_FakeRunner(), prober=lambda: True)
    driver.install(bearer_token=None)

    content = _env_file_path(tmp_hal0_home).read_text(encoding="utf-8")
    # When no Bearer was supplied the line is omitted entirely so the
    # operator's env-var or external secret store stays authoritative.
    assert "HAL0_BEARER_TOKEN" not in content


def test_install_surfaces_subprocess_failures_as_agent_error(
    tmp_hal0_home: str,
    _fake_installer_script: Path,
) -> None:
    driver = HermesDriver(runner=_FakeRunner(fail=True), prober=lambda: True)

    with pytest.raises(AgentError, match="hermes-agent install failed"):
        driver.install(bearer_token="tok")


# ── uninstall + status ───────────────────────────────────────────────────────


def test_uninstall_removes_env_file(
    tmp_hal0_home: str,
    _fake_installer_script: Path,
) -> None:
    driver = HermesDriver(runner=_FakeRunner(), prober=lambda: True)
    driver.install(bearer_token="tok")
    env_path = _env_file_path(tmp_hal0_home)
    assert env_path.exists()

    driver.uninstall()
    assert not env_path.exists()


def test_uninstall_is_idempotent_when_env_file_missing(tmp_hal0_home: str) -> None:
    """Calling uninstall on a never-installed agent should silently
    no-op — matches the manager's idempotent uninstall posture."""
    driver = HermesDriver(runner=_FakeRunner(), prober=lambda: True)
    # Must not raise.
    driver.uninstall()
    assert not _env_file_path(tmp_hal0_home).exists()


def test_status_reflects_env_file_presence(
    tmp_hal0_home: str,
    _fake_installer_script: Path,
) -> None:
    driver = HermesDriver(runner=_FakeRunner(), prober=lambda: True)

    assert driver.status() == "broken"  # no install yet
    driver.install(bearer_token="tok")
    assert driver.status() == "installed"
    driver.uninstall()
    assert driver.status() == "broken"
