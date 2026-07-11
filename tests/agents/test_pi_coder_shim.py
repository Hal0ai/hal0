"""Unit tests for hal0.agents.pi_coder.PiCoderDriver.

Asserts the shim invokes the installer script with correct argv, deploys
the hal0-provider (model autodiscovery) + hal0-memory (shared banks)
extensions and the hal0 theme into pi's config tree, upserts
settings.json, writes a hal0-admin-only adapter config, and best-effort
installs pi-subagents for delegation. Subprocess is faked so the test
suite stays hermetic (no npm / cargo / network).

pi's config tree is resolved from ``Path.home()`` per call (see
driver.py) — the ``pi_home`` fixture redirects ``HOME`` to a temp dir so
these tests never touch the real operator home.
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

    def __init__(self, *, fail: bool = False, fail_argv0: str | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._fail = fail
        self._fail_argv0 = fail_argv0

    def run(
        self,
        argv: list[str],
        *,
        env: dict[str, str] | None = None,
        check: bool = False,
    ) -> _FakeCompleted:
        self.calls.append({"argv": list(argv), "env": dict(env or {}), "check": check})
        if self._fail or (self._fail_argv0 and argv and argv[0] == self._fail_argv0):
            raise RuntimeError("fake subprocess failure")
        return _FakeCompleted()


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def pi_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect Path.home() to a temp dir for the duration of the test.

    driver.py resolves ``~/.pi/agent/...`` fresh on every call (not
    cached at import time) specifically so this works.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def driver(tmp_hal0_home: str, pi_home: Path) -> PiCoderDriver:
    """Driver with the default subprocess module — overridden per-test
    via ``driver._runner = ...`` when subprocess assertions matter.

    ``tmp_hal0_home`` autouse'd through the project conftest routes
    ``/var/lib/hal0`` and ``/etc/hal0`` under tmp_path so the adapter
    config writes don't escape the sandbox. ``pi_home`` does the same
    for pi's own ``~/.pi/agent`` tree.
    """
    return PiCoderDriver()


def _pi_agent_dir(pi_home: Path) -> Path:
    return pi_home / ".pi" / "agent"


# ── install: subprocess + env ────────────────────────────────────────────────


def test_install_invokes_installer_script_with_correct_argv(
    driver: PiCoderDriver,
    tmp_hal0_home: str,
) -> None:
    runner = _FakeRunner()
    driver._runner = runner  # type: ignore[assignment]

    driver.install(bearer_token="hal0_tok_xyz")

    bash_calls = [c for c in runner.calls if c["argv"][0] == "bash"]
    assert len(bash_calls) == 1
    argv = bash_calls[0]["argv"]
    assert argv[1].endswith("/installer/agents/pi-coder.sh")
    # Token + data dir surfaced via env so the POSIX script doesn't
    # have to parse argv flags.
    env = bash_calls[0]["env"]
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
    assert cfg["servers"]["hal0-admin"]["url"].endswith("/mcp/admin")
    # Authorization header populated when a token was passed.
    assert cfg["servers"]["hal0-admin"]["headers"]["Authorization"] == "Bearer hal0_tok_xyz"


def test_install_adapter_config_no_longer_wires_memory(
    driver: PiCoderDriver,
    tmp_hal0_home: str,
) -> None:
    """Memory rides the native hal0-memory extension now — the generic
    MCP proxy only wires hal0-admin."""
    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token="tok")

    cfg_path = (
        Path(tmp_hal0_home) / "var-lib" / "hal0" / "agents" / "pi-coder" / "pi-mcp-adapter.json"
    )
    cfg = json.loads(cfg_path.read_text())
    assert "hal0-memory" not in cfg["servers"]


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


# ── install: extensions + theme + settings ───────────────────────────────────


def test_install_deploys_provider_and_memory_extensions(
    driver: PiCoderDriver,
    pi_home: Path,
) -> None:
    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token=None)

    agent_dir = _pi_agent_dir(pi_home)
    assert (agent_dir / "extensions" / "hal0-provider" / "index.ts").exists()
    assert (agent_dir / "extensions" / "hal0-memory" / "index.ts").exists()


def test_install_deploys_hal0_theme(driver: PiCoderDriver, pi_home: Path) -> None:
    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token=None)

    theme_path = _pi_agent_dir(pi_home) / "themes" / "hal0.json"
    assert theme_path.exists()
    theme = json.loads(theme_path.read_text())
    assert theme["name"] == "hal0"


def test_install_sets_default_provider_model_and_theme(
    driver: PiCoderDriver,
    pi_home: Path,
) -> None:
    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token=None)

    settings = json.loads((_pi_agent_dir(pi_home) / "settings.json").read_text())
    assert settings["defaultProvider"] == "hal0"
    assert settings["defaultModel"] == "agent"
    assert settings["theme"] == "hal0"


def test_install_preserves_existing_settings(driver: PiCoderDriver, pi_home: Path) -> None:
    """Upsert must not clobber unrelated operator settings (e.g. their
    own subagent overrides or other packages)."""
    settings_file = _pi_agent_dir(pi_home) / "settings.json"
    settings_file.parent.mkdir(parents=True)
    settings_file.write_text(json.dumps({"editorPaddingX": 1, "packages": ["npm:some-other"]}))

    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token=None)

    settings = json.loads(settings_file.read_text())
    assert settings["editorPaddingX"] == 1
    assert settings["packages"] == ["npm:some-other"]
    assert settings["defaultProvider"] == "hal0"


def test_install_runs_pi_install_subagents(driver: PiCoderDriver, tmp_hal0_home: str) -> None:
    runner = _FakeRunner()
    driver._runner = runner  # type: ignore[assignment]
    driver.install(bearer_token=None)

    pi_calls = [c for c in runner.calls if c["argv"][:2] == ["pi", "install"]]
    assert pi_calls == [{"argv": ["pi", "install", "npm:pi-subagents"], "env": {}, "check": True}]


def test_install_subagents_failure_is_non_fatal(driver: PiCoderDriver, tmp_hal0_home: str) -> None:
    """npm/network hiccups installing pi-subagents must not fail the
    whole install — theme/provider/memory wiring already succeeded."""
    driver._runner = _FakeRunner(fail_argv0="pi")  # type: ignore[assignment]
    driver.install(bearer_token=None)  # must not raise


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


# ── uninstall ─────────────────────────────────────────────────────────────────


def test_uninstall_removes_adapter_config(driver: PiCoderDriver, tmp_hal0_home: str) -> None:
    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token="tok")
    cfg_path = (
        Path(tmp_hal0_home) / "var-lib" / "hal0" / "agents" / "pi-coder" / "pi-mcp-adapter.json"
    )
    assert cfg_path.exists()

    driver.uninstall()
    assert not cfg_path.exists()


def test_uninstall_removes_extensions_and_theme(driver: PiCoderDriver, pi_home: Path) -> None:
    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token="tok")

    driver.uninstall()

    agent_dir = _pi_agent_dir(pi_home)
    assert not (agent_dir / "extensions" / "hal0-provider").exists()
    assert not (agent_dir / "extensions" / "hal0-memory").exists()
    assert not (agent_dir / "themes" / "hal0.json").exists()


def test_uninstall_reverts_settings_when_hal0_was_active(
    driver: PiCoderDriver, pi_home: Path
) -> None:
    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token="tok")

    driver.uninstall()

    settings = json.loads((_pi_agent_dir(pi_home) / "settings.json").read_text())
    assert settings["defaultProvider"] == "openrouter"
    assert settings["defaultModel"] == "deepseek/deepseek-v4-pro"
    assert "theme" not in settings


def test_uninstall_does_not_revert_operator_override(driver: PiCoderDriver, pi_home: Path) -> None:
    """If the operator switched pi to some other provider after install,
    uninstall must not stomp their choice."""
    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token="tok")

    settings_file = _pi_agent_dir(pi_home) / "settings.json"
    settings = json.loads(settings_file.read_text())
    settings["defaultProvider"] = "anthropic"
    settings["defaultModel"] = "claude-sonnet-5"
    settings_file.write_text(json.dumps(settings))

    driver.uninstall()

    settings = json.loads(settings_file.read_text())
    assert settings["defaultProvider"] == "anthropic"
    assert settings["defaultModel"] == "claude-sonnet-5"


def test_uninstall_is_best_effort_on_subprocess_failure(
    driver: PiCoderDriver, tmp_hal0_home: str
) -> None:
    """``pi remove npm:pi-subagents`` failing (pi already gone, offline
    box) must not stop the rest of the teardown."""
    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token="tok")

    driver._runner = _FakeRunner(fail=True)  # type: ignore[assignment]
    driver.uninstall()  # must not raise

    cfg_path = (
        Path(tmp_hal0_home) / "var-lib" / "hal0" / "agents" / "pi-coder" / "pi-mcp-adapter.json"
    )
    assert not cfg_path.exists()


# ── status ────────────────────────────────────────────────────────────────────


def test_status_reflects_adapter_and_extension_presence(
    driver: PiCoderDriver, tmp_hal0_home: str
) -> None:
    assert driver.status() == "broken"  # no install yet
    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token="tok")
    assert driver.status() == "installed"
    driver.uninstall()
    assert driver.status() == "broken"
