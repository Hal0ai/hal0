"""Unit tests for hal0.agents.pi_coder.PiCoderDriver.

Asserts the shim invokes the installer script with correct argv +
writes the adapter config to the right paths. Subprocess is faked so
the test suite stays hermetic (no npm / cargo / network).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hal0.agents.pi_coder import PiCoderDriver

# ── Fake subprocess ──────────────────────────────────────────────────────────


class _FakeCompleted:
    returncode = 0


class _FakeRunner:
    """Replaces ``subprocess`` for the driver. Records every ``run()``
    call so tests can assert on argv + env without spawning a real
    shell."""

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


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def driver(tmp_hal0_home: str) -> PiCoderDriver:
    """Driver with the default subprocess module — overridden per-test
    via ``driver._runner = ...`` when subprocess assertions matter.

    ``tmp_hal0_home`` autouse'd through the project conftest routes
    ``/var/lib/hal0`` and ``/etc/hal0`` under tmp_path so the adapter
    config writes don't escape the sandbox.
    """
    return PiCoderDriver()


# ── install: subprocess + env ────────────────────────────────────────────────


def test_install_invokes_installer_script_with_correct_argv(
    driver: PiCoderDriver,
    tmp_hal0_home: str,
) -> None:
    runner = _FakeRunner()
    driver._runner = runner  # type: ignore[assignment]

    driver.install(bearer_token="hal0_tok_xyz")

    assert len(runner.calls) == 1
    argv = runner.calls[0]["argv"]
    # bash + path to installer/agents/pi-coder.sh
    assert argv[0] == "bash"
    assert argv[1].endswith("/installer/agents/pi-coder.sh")
    # Token + data dir surfaced via env so the POSIX script doesn't
    # have to parse argv flags.
    env = runner.calls[0]["env"]
    assert env["HAL0_BEARER_TOKEN"] == "hal0_tok_xyz"
    assert env["HAL0_AGENT_DATA_DIR"].endswith("/agents/pi-coder")
    assert env["HAL0_API_URL"].startswith("http")


def test_install_writes_adapter_config_with_bearer_header(
    driver: PiCoderDriver,
    tmp_hal0_home: str,
) -> None:
    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token="hal0_tok_xyz")

    cfg_path = (
        Path(tmp_hal0_home) / "var-lib" / "hal0" / "agents" / "pi-coder" / "pi-mcp-adapter.json"
    )
    assert cfg_path.exists()
    cfg = json.loads(cfg_path.read_text())
    assert cfg["version"] == 1
    assert "hal0-admin" in cfg["servers"]
    assert "hal0-memory" in cfg["servers"]
    assert cfg["servers"]["hal0-admin"]["url"].endswith("/mcp/admin")
    assert cfg["servers"]["hal0-memory"]["url"].endswith("/mcp/memory")
    # Authorization header populated when a token was passed.
    assert cfg["servers"]["hal0-admin"]["headers"]["Authorization"] == "Bearer hal0_tok_xyz"


def test_install_writes_adapter_config_without_auth_when_no_token(
    driver: PiCoderDriver,
    tmp_hal0_home: str,
) -> None:
    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token=None)

    cfg_path = (
        Path(tmp_hal0_home) / "var-lib" / "hal0" / "agents" / "pi-coder" / "pi-mcp-adapter.json"
    )
    cfg = json.loads(cfg_path.read_text())
    # No headers key when no token — matches the auth-disabled dev
    # install branch.
    assert "headers" not in cfg["servers"]["hal0-admin"]


# ── install: idempotency ─────────────────────────────────────────────────────


def test_install_rerun_is_idempotent(driver: PiCoderDriver, tmp_hal0_home: str) -> None:
    """Calling install() twice should overwrite the adapter config
    cleanly; no side effects on the FS layout."""
    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token="tok-1")
    cfg_path = (
        Path(tmp_hal0_home) / "var-lib" / "hal0" / "agents" / "pi-coder" / "pi-mcp-adapter.json"
    )

    # Different runner instance — first run wasn't memoised.
    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token="tok-2")
    cfg = json.loads(cfg_path.read_text())
    assert cfg["servers"]["hal0-admin"]["headers"]["Authorization"] == "Bearer tok-2"
    # File was rewritten (atomic replace), not appended/duplicated.
    assert cfg_path.stat().st_size > 0


# ── install: subprocess failure surfaces as AgentError ───────────────────────


def test_install_subprocess_failure_raises_agent_error(driver: PiCoderDriver) -> None:
    from hal0.agents.manager import AgentError

    driver._runner = _FakeRunner(fail=True)  # type: ignore[assignment]
    with pytest.raises(AgentError, match="pi-coder install failed"):
        driver.install(bearer_token="tok")


# ── uninstall removes adapter config ─────────────────────────────────────────


def test_uninstall_removes_adapter_config(driver: PiCoderDriver, tmp_hal0_home: str) -> None:
    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token="tok")
    cfg_path = (
        Path(tmp_hal0_home) / "var-lib" / "hal0" / "agents" / "pi-coder" / "pi-mcp-adapter.json"
    )
    assert cfg_path.exists()

    driver.uninstall()
    assert not cfg_path.exists()


def test_status_reflects_adapter_config_presence(driver: PiCoderDriver, tmp_hal0_home: str) -> None:
    assert driver.status() == "broken"  # no install yet
    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token="tok")
    assert driver.status() == "installed"
    driver.uninstall()
    assert driver.status() == "broken"
