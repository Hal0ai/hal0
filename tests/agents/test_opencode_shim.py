"""Unit tests for hal0.agents.opencode.OpenCodeDriver.

Asserts the shim invokes ``installer/agents/opencode.sh`` with the right
argv/env and writes ``~/.config/opencode/opencode.json`` wiring hal0 as an
OpenAI-compatible provider plus the hindsight-backed ``hal0-memory`` MCP
mount. Subprocess is faked so the suite stays hermetic (no npm / network).

opencode's config tree is resolved from ``Path.home()`` per call (see
driver.py) — the ``oc_home`` fixture redirects ``HOME`` to a temp dir so
these tests never touch the real operator home.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hal0.agents.opencode import OpenCodeDriver

# ── Fake subprocess ──────────────────────────────────────────────────────────


class _FakeCompleted:
    returncode = 0


class _FakeRunner:
    """Replaces ``subprocess`` for the driver. Records every ``run()`` call
    so tests can assert on argv + env without spawning a real shell."""

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
def oc_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect Path.home() to a temp dir for the duration of the test."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def driver(tmp_hal0_home: str, oc_home: Path) -> OpenCodeDriver:
    """Driver with the default subprocess module — overridden per-test via
    ``driver._runner = ...`` when subprocess assertions matter.

    ``tmp_hal0_home`` (autouse via the project conftest) routes
    ``/var/lib/hal0`` + ``/etc/hal0`` under tmp_path; ``oc_home`` does the
    same for opencode's ``~/.config/opencode`` tree.
    """
    return OpenCodeDriver()


def _config_path(oc_home: Path) -> Path:
    return oc_home / ".config" / "opencode" / "opencode.json"


# ── install: subprocess + env ────────────────────────────────────────────────


def test_install_invokes_installer_script_with_correct_argv(driver: OpenCodeDriver) -> None:
    runner = _FakeRunner()
    driver._runner = runner  # type: ignore[assignment]

    driver.install(bearer_token="hal0_tok_xyz")

    bash_calls = [c for c in runner.calls if c["argv"][0] == "bash"]
    assert len(bash_calls) == 1
    argv = bash_calls[0]["argv"]
    assert argv[1].endswith("/installer/agents/opencode.sh")
    env = bash_calls[0]["env"]
    assert env["HAL0_BEARER_TOKEN"] == "hal0_tok_xyz"
    assert env["HAL0_AGENT_DATA_DIR"].endswith("/agents/opencode")
    assert env["HAL0_API_URL"].startswith("http")


def test_install_writes_provider_and_memory_mcp(driver: OpenCodeDriver, oc_home: Path) -> None:
    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token="hal0_tok_xyz")

    cfg_path = _config_path(oc_home)
    assert cfg_path.exists()
    cfg = json.loads(cfg_path.read_text())

    # hal0 provider (OpenAI-compatible) with the slot virtuals + default model.
    assert cfg["model"] == "hal0/agent"
    prov = cfg["provider"]["hal0"]
    assert prov["npm"] == "@ai-sdk/openai-compatible"
    assert prov["options"]["baseURL"].endswith("/v1")
    assert prov["options"]["apiKey"] == "hal0_tok_xyz"
    assert "agent" in prov["models"] and "code" in prov["models"]

    # hindsight-backed memory MCP, agent-scoped, bearer wired when present.
    mem = cfg["mcp"]["hal0-memory"]
    assert mem["type"] == "remote"
    assert mem["url"].endswith("/mcp/memory/mcp")
    assert mem["headers"]["X-hal0-Agent"] == "opencode"
    assert mem["headers"]["Authorization"] == "Bearer hal0_tok_xyz"


def test_install_dev_mode_uses_sentinel_key_and_no_auth_header(
    driver: OpenCodeDriver, oc_home: Path
) -> None:
    """No bearer token (auth-disabled dev install) → sentinel api key and no
    Authorization header on the memory mount."""
    driver._runner = _FakeRunner()  # type: ignore[assignment]
    driver.install(bearer_token=None)

    cfg = json.loads(_config_path(oc_home).read_text())
    assert cfg["provider"]["hal0"]["options"]["apiKey"] == "hal0-local"
    assert "Authorization" not in cfg["mcp"]["hal0-memory"]["headers"]


def test_install_propagates_installer_failure(driver: OpenCodeDriver) -> None:
    from hal0.agents.manager import AgentError

    driver._runner = _FakeRunner(fail=True)  # type: ignore[assignment]
    with pytest.raises(AgentError):
        driver.install(bearer_token="tok")


def test_status_and_uninstall_roundtrip(driver: OpenCodeDriver, oc_home: Path) -> None:
    driver._runner = _FakeRunner()  # type: ignore[assignment]
    assert driver.status() == "broken"  # nothing written yet

    driver.install(bearer_token="tok")
    assert driver.status() == "installed"
    assert _config_path(oc_home).exists()

    driver.uninstall()
    assert not _config_path(oc_home).exists()
    assert driver.status() == "broken"


def test_registered_as_bundled_agent() -> None:
    from hal0.agents.manager import BUNDLED_AGENTS, _driver_for

    assert "opencode" in BUNDLED_AGENTS
    drv = _driver_for("opencode")
    assert drv.name == "opencode"
