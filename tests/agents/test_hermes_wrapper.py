"""Unit tests for hal0.agents.hermes.HermesDriver (wrapper pivot).

The driver no longer probes upstream `hermes-agent --help` for a
`--hal0-config` flag (which was never going to ship — the user cannot
PR upstream NousResearch/hermes-agent). Instead it probes for the
hal0-owned `hal0-hermes` wrapper that env-file-injects HAL0_* into
upstream `hermes` on every invocation.

These tests mirror the shape of ``tests/agents/test_pi_coder_shim.py``:
fake subprocess + fake prober, so the suite stays hermetic — no real
upstream binary, no real wrapper-on-PATH dependency.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hal0.agents.hermes import HermesDriver
from hal0.agents.manager import AgentError, HermesUpstreamMissingError

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


# ── Fake prober ──────────────────────────────────────────────────────────────


def _prober_ok() -> bool:
    """Prober that reports wrapper installed + functional."""
    return True


def _prober_missing() -> bool:
    """Prober that reports wrapper missing or non-functional."""
    return False


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def driver(tmp_hal0_home: str) -> HermesDriver:
    """Driver with default subprocess + a wrapper-installed prober.

    ``tmp_hal0_home`` (autouse'd via the project conftest) routes
    ``/var/lib/hal0`` and ``/etc/hal0`` under tmp_path so the env file
    writes don't escape the sandbox.

    Tests that need the wrapper-missing branch override
    ``driver._prober`` per-test.
    """
    return HermesDriver(prober=_prober_ok)


# ── install: wrapper-missing short-circuit ───────────────────────────────────


def test_install_raises_when_wrapper_missing_before_shelling_out(
    tmp_hal0_home: str,
) -> None:
    """If the hal0-hermes wrapper isn't on PATH (or its --hal0-ready
    probe fails), driver.install() raises HermesUpstreamMissingError
    BEFORE invoking the installer script. We assert this by injecting
    a runner that records calls — it should record zero."""
    runner = _FakeRunner()
    drv = HermesDriver(runner=runner, prober=_prober_missing)

    with pytest.raises(HermesUpstreamMissingError, match="hal0-hermes wrapper"):
        drv.install(bearer_token="hal0_tok_xyz")

    assert runner.calls == [], "installer script must NOT be shelled out when wrapper probe fails"


def test_install_error_message_points_to_installer_and_upstream_hermes(
    tmp_hal0_home: str,
) -> None:
    """Error message must tell the operator both how to install the
    wrapper AND that upstream `hermes` is a prerequisite. Avoids the
    'Hermes is incompatible' vague-error Slack thread."""
    drv = HermesDriver(runner=_FakeRunner(), prober=_prober_missing)
    with pytest.raises(HermesUpstreamMissingError) as exc:
        drv.install(bearer_token=None)
    msg = str(exc.value)
    assert "installer/agents/hermes-agent.sh" in msg
    assert "hal0 agent install hermes" in msg
    assert "hermes" in msg  # upstream binary mention


# ── install: happy path ──────────────────────────────────────────────────────


def test_install_shells_out_to_installer_script_when_wrapper_present(
    driver: HermesDriver,
    tmp_hal0_home: str,
) -> None:
    runner = _FakeRunner()
    driver._runner = runner  # type: ignore[assignment]

    driver.install(bearer_token="hal0_tok_xyz")

    assert len(runner.calls) == 1
    argv = runner.calls[0]["argv"]
    assert argv[0] == "bash"
    assert argv[1].endswith("/installer/agents/hermes-agent.sh")
    env = runner.calls[0]["env"]
    assert env["HAL0_BEARER_TOKEN"] == "hal0_tok_xyz"
    assert env["HAL0_AGENT_DATA_DIR"].endswith("/agents/hermes")
    assert env["HAL0_API_URL"].startswith("http")


def test_install_writes_env_file_with_expected_keys(
    driver: HermesDriver,
    tmp_hal0_home: str,
) -> None:
    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token="hal0_tok_xyz")

    env_file = Path(tmp_hal0_home) / "etc" / "hal0" / "agents" / "hermes.env"
    assert env_file.exists()
    body = env_file.read_text(encoding="utf-8")
    # The wrapper sources this file via `set -a; . "$HAL0_ENV_FILE"; set +a`,
    # so the contents must be POSIX env-file shape: KEY=VALUE per line.
    assert "HAL0_API_URL=http" in body
    assert "HAL0_MCP_ADMIN_URL=" in body
    assert "/mcp/admin" in body
    assert "HAL0_MCP_MEMORY_URL=" in body
    assert "/mcp/memory" in body
    assert "HAL0_BEARER_TOKEN=hal0_tok_xyz" in body


def test_install_omits_bearer_when_no_token_passed(
    driver: HermesDriver,
    tmp_hal0_home: str,
) -> None:
    """Auth-disabled dev installs don't surface a token — env file
    must not include the HAL0_BEARER_TOKEN line in that case."""
    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token=None)

    env_file = Path(tmp_hal0_home) / "etc" / "hal0" / "agents" / "hermes.env"
    body = env_file.read_text(encoding="utf-8")
    assert "HAL0_BEARER_TOKEN" not in body


# ── install: subprocess failure surfaces as AgentError ───────────────────────


def test_install_subprocess_failure_raises_agent_error(
    driver: HermesDriver,
    tmp_hal0_home: str,
) -> None:
    driver._runner = _FakeRunner(fail=True)  # type: ignore[assignment]
    with pytest.raises(AgentError, match="hermes-agent install failed"):
        driver.install(bearer_token="tok")


# ── install: idempotency ─────────────────────────────────────────────────────


def test_install_rerun_overwrites_env_file(
    driver: HermesDriver,
    tmp_hal0_home: str,
) -> None:
    """Re-running install with a fresh token rewrites the env file
    atomically; no append/dup behaviour."""
    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token="tok-1")
    env_file = Path(tmp_hal0_home) / "etc" / "hal0" / "agents" / "hermes.env"

    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token="tok-2")
    body = env_file.read_text(encoding="utf-8")
    assert "HAL0_BEARER_TOKEN=tok-2" in body
    assert "HAL0_BEARER_TOKEN=tok-1" not in body


# ── uninstall removes env file ───────────────────────────────────────────────


def test_uninstall_removes_env_file(driver: HermesDriver, tmp_hal0_home: str) -> None:
    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token="tok")
    env_file = Path(tmp_hal0_home) / "etc" / "hal0" / "agents" / "hermes.env"
    assert env_file.exists()

    driver.uninstall()
    assert not env_file.exists()


def test_uninstall_is_idempotent(driver: HermesDriver) -> None:
    """Calling uninstall when nothing's installed must not raise."""
    driver.uninstall()  # no env file yet — should be a no-op
    driver.uninstall()  # twice — still a no-op


# ── status reflects env file presence ────────────────────────────────────────


def test_status_returns_installed_iff_env_file_exists(
    driver: HermesDriver,
    tmp_hal0_home: str,
) -> None:
    assert driver.status() == "broken"  # no install yet

    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token="tok")
    assert driver.status() == "installed"

    driver.uninstall()
    assert driver.status() == "broken"
