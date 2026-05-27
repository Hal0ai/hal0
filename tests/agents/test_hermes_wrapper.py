"""Unit tests for hal0.agents.hermes.HermesDriver (wrapper pivot).

The driver no longer probes upstream `hermes-agent --help` for a
`--hal0-config` flag (which was never going to ship — the user cannot
PR upstream NousResearch/hermes-agent). Instead it ships a hal0-owned
`hal0-hermes` wrapper that env-file-injects HAL0_* into upstream
`hermes` on every invocation.

Pre-install gate (driver.install): probe upstream `hermes` is on PATH.
Installing the wrapper without upstream Hermes is pointless — the
wrapper just sources the env file and execs upstream hermes.

Post-install health (driver.status): env-file presence + (optionally
via _probe_wrapper_installed) wrapper functional.

These tests mirror the shape of ``tests/agents/test_pi_coder_shim.py``:
fake subprocess + fake prober, so the suite stays hermetic — no real
upstream binary, no real wrapper-on-PATH dependency.
"""

from __future__ import annotations

import json
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
    """Prober that reports upstream ``hermes`` on PATH."""
    return True


def _prober_missing() -> bool:
    """Prober that reports upstream ``hermes`` NOT on PATH."""
    return False


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def driver(tmp_hal0_home: str) -> HermesDriver:
    """Driver with default subprocess + an upstream-present prober.

    ``tmp_hal0_home`` (autouse'd via the project conftest) routes
    ``/var/lib/hal0`` and ``/etc/hal0`` under tmp_path so the env file
    writes don't escape the sandbox.

    Tests that need the upstream-missing branch override
    ``driver._prober`` per-test.
    """
    return HermesDriver(prober=_prober_ok)


# ── install: upstream-missing short-circuit ──────────────────────────────────


def test_install_raises_when_upstream_hermes_missing(
    tmp_hal0_home: str,
) -> None:
    """If upstream ``hermes`` isn't on PATH, driver.install() raises
    HermesUpstreamMissingError BEFORE invoking the installer script
    (no point shipping a wrapper around a missing binary). Injected
    runner records calls — it should record zero."""
    runner = _FakeRunner()
    drv = HermesDriver(runner=runner, prober=_prober_missing)

    with pytest.raises(HermesUpstreamMissingError, match="Upstream `hermes`"):
        drv.install(bearer_token="hal0_tok_xyz")

    assert runner.calls == [], "installer script must NOT shell out when upstream hermes is missing"


def test_install_error_message_points_to_pipx_install(
    tmp_hal0_home: str,
) -> None:
    """Error message must give the operator an actionable install
    command (pipx / pip). Avoids the 'Hermes is incompatible' vague
    Slack thread."""
    drv = HermesDriver(runner=_FakeRunner(), prober=_prober_missing)
    with pytest.raises(HermesUpstreamMissingError) as exc:
        drv.install(bearer_token=None)
    msg = str(exc.value)
    assert "pipx install hermes-agent" in msg or "pip install" in msg
    assert "hal0 agent install hermes" in msg


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


# ── uninstall: venv + context_link teardown (#348 / #349) ────────────────────
#
# The driver reads provision.json to discover BOTH the venv root and
# the /etc/hal0 doc paths that ``hermes_provision`` writes. These tests
# stamp a representative provision.json on disk + assert the right
# inverse happens.


def _state_dir(tmp_hal0_home: str) -> Path:
    """Return the path where hermes_provision would write its checkpoint."""
    return Path(tmp_hal0_home) / "var-lib" / "hal0" / "state" / "agents" / "hermes"


def _stamp_provision(
    tmp_hal0_home: str,
    *,
    venv: Path | None = None,
    context_paths: list[Path] | None = None,
) -> Path:
    """Stamp a minimal provision.json mirroring what ``hermes_provision`` writes.

    Returns the path to the file. Callers can selectively omit the
    venv or the context_link section to exercise edge cases.
    """
    state_dir = _state_dir(tmp_hal0_home)
    state_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "agent_id": "hermes-agent",
        "phases": {},
    }
    if venv is not None:
        payload["venv"] = str(venv)
    if context_paths is not None:
        payload["phases"]["context_link"] = {
            "status": "ok",
            "details": {
                "rendered": {p.name: {"path": str(p), "sha256": "deadbeef"} for p in context_paths},
                "links": [],
                "warnings": [],
            },
        }
    target = state_dir / "provision.json"
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return target


def test_uninstall_removes_venv_recorded_in_provision_json(
    driver: HermesDriver,
    tmp_hal0_home: str,
) -> None:
    """#348: /var/lib/hal0/venvs/<name>/ must not survive uninstall.

    The venv path lives outside the manager's seed + data + state
    triad, so the driver owns this cleanup. The path is read from
    provision.json so an operator override is honoured.
    """
    venv = Path(tmp_hal0_home) / "var-lib" / "hal0" / "venvs" / "hermes"
    venv.mkdir(parents=True)
    (venv / "bin").mkdir()
    (venv / "bin" / "python").write_text("#!/usr/bin/env python\n")
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
    _stamp_provision(tmp_hal0_home, venv=venv)

    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token="tok")  # env file present so uninstall has work
    assert venv.exists()

    driver.uninstall()
    assert not venv.exists(), "venv directory must be removed by uninstall (#348)"


def test_uninstall_is_idempotent_when_venv_already_gone(
    driver: HermesDriver,
    tmp_hal0_home: str,
) -> None:
    """#348 acceptance: missing venv must be a no-op, not an error."""
    venv = Path(tmp_hal0_home) / "var-lib" / "hal0" / "venvs" / "hermes"
    # Note: we deliberately do NOT create the venv directory; the
    # provision.json points at a path that isn't on disk.
    _stamp_provision(tmp_hal0_home, venv=venv)

    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token="tok")

    # Should not raise.
    driver.uninstall()
    assert not venv.exists()


def test_uninstall_without_provision_json_only_removes_env_file(
    driver: HermesDriver,
    tmp_hal0_home: str,
) -> None:
    """No provision.json (e.g. install failed before bootstrap stamped it) —
    driver still completes uninstall cleanly, just doesn't have a venv
    path to chase."""
    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token="tok")
    env_file = Path(tmp_hal0_home) / "etc" / "hal0" / "agents" / "hermes.env"
    assert env_file.exists()

    driver.uninstall()
    assert not env_file.exists()


def test_uninstall_removes_context_link_docs(
    driver: HermesDriver,
    tmp_hal0_home: str,
) -> None:
    """#349: /etc/hal0/AGENTS.md + HERMES.md must not survive uninstall.

    Both files are rendered by the ``context_link`` provision phase
    and recorded in ``provision.json:phases.context_link.details.rendered.*``.
    The driver reads the file list from that record rather than
    hardcoding — if a future phase adds another doc, the inverse
    stays correct automatically.
    """
    etc_hal0 = Path(tmp_hal0_home) / "etc" / "hal0"
    etc_hal0.mkdir(parents=True, exist_ok=True)
    agents_md = etc_hal0 / "AGENTS.md"
    hermes_md = etc_hal0 / "HERMES.md"
    agents_md.write_text("# Agents\n", encoding="utf-8")
    hermes_md.write_text("# Hermes\n", encoding="utf-8")
    _stamp_provision(tmp_hal0_home, context_paths=[agents_md, hermes_md])

    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token="tok")
    assert agents_md.exists() and hermes_md.exists()

    driver.uninstall()
    assert not agents_md.exists(), "/etc/hal0/AGENTS.md must be removed by uninstall (#349)"
    assert not hermes_md.exists(), "/etc/hal0/HERMES.md must be removed by uninstall (#349)"


def test_uninstall_context_link_is_idempotent_when_docs_already_gone(
    driver: HermesDriver,
    tmp_hal0_home: str,
) -> None:
    """#349 acceptance: a missing rendered file is a no-op, not an error."""
    etc_hal0 = Path(tmp_hal0_home) / "etc" / "hal0"
    etc_hal0.mkdir(parents=True, exist_ok=True)
    agents_md = etc_hal0 / "AGENTS.md"
    hermes_md = etc_hal0 / "HERMES.md"
    # No files created — the provision.json records paths that aren't on disk.
    _stamp_provision(tmp_hal0_home, context_paths=[agents_md, hermes_md])

    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token="tok")

    # Should not raise even though the recorded paths are missing.
    driver.uninstall()


def test_uninstall_skips_context_link_directory_safely(
    driver: HermesDriver,
    tmp_hal0_home: str,
) -> None:
    """A recorded ``rendered[*].path`` that is unexpectedly a directory
    (e.g. operator hand-placed a folder where a file used to live)
    must NOT be rmtree'd by the driver — leave it for the operator
    to investigate. The rest of the uninstall still runs.
    """
    etc_hal0 = Path(tmp_hal0_home) / "etc" / "hal0"
    etc_hal0.mkdir(parents=True, exist_ok=True)
    weird_dir = etc_hal0 / "AGENTS.md"
    weird_dir.mkdir()
    (weird_dir / "child").write_text("operator-placed\n")
    hermes_md = etc_hal0 / "HERMES.md"
    hermes_md.write_text("# Hermes\n", encoding="utf-8")
    _stamp_provision(tmp_hal0_home, context_paths=[weird_dir, hermes_md])

    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token="tok")

    driver.uninstall()
    # Directory survives — driver doesn't rmtree on this path.
    assert weird_dir.is_dir()
    assert (weird_dir / "child").exists()
    # The file-typed entry still gets cleaned.
    assert not hermes_md.exists()


def test_uninstall_handles_corrupt_provision_json(
    driver: HermesDriver,
    tmp_hal0_home: str,
) -> None:
    """Garbled provision.json must not break uninstall — the env file
    still gets removed and the driver returns cleanly."""
    state_dir = _state_dir(tmp_hal0_home)
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "provision.json").write_text("{ this is not valid json", encoding="utf-8")

    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token="tok")
    env_file = Path(tmp_hal0_home) / "etc" / "hal0" / "agents" / "hermes.env"
    assert env_file.exists()

    driver.uninstall()
    assert not env_file.exists()


def test_uninstall_full_teardown_via_provision_json(
    driver: HermesDriver,
    tmp_hal0_home: str,
) -> None:
    """End-to-end driver uninstall: stamp a realistic provision.json
    with BOTH venv + context_link populated, then assert every
    recorded artifact is gone post-uninstall."""
    venv = Path(tmp_hal0_home) / "var-lib" / "hal0" / "venvs" / "hermes"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").write_text("#!/usr/bin/env python\n")
    etc_hal0 = Path(tmp_hal0_home) / "etc" / "hal0"
    etc_hal0.mkdir(parents=True, exist_ok=True)
    agents_md = etc_hal0 / "AGENTS.md"
    hermes_md = etc_hal0 / "HERMES.md"
    agents_md.write_text("# Agents\n", encoding="utf-8")
    hermes_md.write_text("# Hermes\n", encoding="utf-8")
    _stamp_provision(
        tmp_hal0_home,
        venv=venv,
        context_paths=[agents_md, hermes_md],
    )

    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token="tok")
    env_file = Path(tmp_hal0_home) / "etc" / "hal0" / "agents" / "hermes.env"
    assert env_file.exists() and venv.exists() and agents_md.exists() and hermes_md.exists()

    driver.uninstall()
    assert not env_file.exists()
    assert not venv.exists()
    assert not agents_md.exists()
    assert not hermes_md.exists()
