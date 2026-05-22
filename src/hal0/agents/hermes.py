"""Hermes-Agent driver (ADR-0004 §6).

Hermes is user-owned upstream. Native hal0-awareness grows on the
Hermes side rather than via a hal0-owned shim. This driver is a
one-liner: detect that upstream Hermes ships hal0-awareness, then
invoke Hermes's own install command and write
``/etc/hal0/agents/hermes.env``.

The "hal0-awareness" probe is intentionally narrow: we look for a
``--hal0-config`` flag on the Hermes binary (or an
``HERMES_HAL0_READY=1`` env hint from a marker the upstream installer
drops). Picking a probe with a concrete shape gives the user an
actionable error when it fails — vs a vague "Hermes is incompatible"
that lands them in a Slack thread.

Until Hermes ships that surface, the install path raises
:class:`HermesNotHal0AwareError` with the upstream issue link to
follow. The shell script ``installer/agents/hermes-agent.sh`` mirrors
the same gate at the shell level so a curl|bash invocation also
short-circuits cleanly.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 — required for shim
from pathlib import Path

from hal0.agents.manager import (
    AgentDriver,
    AgentError,
    HermesNotHal0AwareError,
    installer_script_path,
)
from hal0.config import paths as _paths

# Concrete probe surface — see module docstring. Both fire OR-ed; the
# upstream maintainer needs to satisfy only one to flip the gate green.
_HERMES_BIN_NAME = "hermes-agent"
_HAL0_AWARE_FLAG = "--hal0-config"
_HAL0_READY_ENV = "HERMES_HAL0_READY"


def _probe_hal0_awareness() -> bool:
    """Return True iff the local Hermes install advertises hal0-awareness.

    OR of two cheap checks:

    1. ``HERMES_HAL0_READY=1`` env var present (upstream-installer hint).
    2. ``hermes-agent --help`` mentions :data:`_HAL0_AWARE_FLAG`.

    Both are runtime-only — no network calls. Failing on the host
    (binary missing, --help non-zero) returns False so the caller raises
    a clean error rather than the probe itself blowing up.
    """
    if os.environ.get(_HAL0_READY_ENV) == "1":
        return True

    bin_path = shutil.which(_HERMES_BIN_NAME)
    if bin_path is None:
        return False

    try:
        result = subprocess.run(  # nosec B603 — known-safe argv
            [bin_path, "--help"],
            check=False,
            capture_output=True,
            timeout=5,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False

    haystack = (result.stdout or "") + (result.stderr or "")
    return _HAL0_AWARE_FLAG in haystack


class HermesDriver(AgentDriver):
    """Driver for the Hermes-Agent bundled agent."""

    name = "hermes"

    def __init__(self, *, runner: object | None = None, prober: object | None = None) -> None:
        # ``runner`` parallels :class:`PiCoderDriver` — tests inject a
        # fake subprocess. ``prober`` lets tests force the
        # hal0-awareness gate without needing a real Hermes on PATH.
        self._runner = runner if runner is not None else subprocess
        self._prober = prober if prober is not None else _probe_hal0_awareness

    # ── AgentDriver protocol ────────────────────────────────────────────

    def install(self, *, bearer_token: str | None = None) -> None:
        if not self._prober():
            raise HermesNotHal0AwareError(
                "Hermes-Agent on this host does not yet ship hal0-awareness. "
                "Either upgrade Hermes to a build that supports the "
                f"{_HAL0_AWARE_FLAG!r} flag, or export "
                f"{_HAL0_READY_ENV}=1 if you're testing an unreleased build. "
                "Track upstream progress at "
                "https://github.com/Hal0ai/hal0/issues (Phase 8 milestone)."
            )

        script = installer_script_path(self.name)
        if not script.is_file():
            raise AgentError(
                f"installer script missing at {script}. This hal0 install looks "
                "packaged without the bundled-agent scripts — reinstall hal0 "
                "from a release tarball or git clone."
            )

        data_dir = self._data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env["HAL0_AGENT_DATA_DIR"] = str(data_dir)
        env["HAL0_API_URL"] = os.environ.get("HAL0_API_URL", "http://127.0.0.1:8080")
        if bearer_token:
            env["HAL0_BEARER_TOKEN"] = bearer_token

        try:
            self._runner.run(  # type: ignore[attr-defined]
                ["bash", str(script)],
                env=env,
                check=True,
            )
        except Exception as exc:
            raise AgentError(f"hermes-agent install failed ({type(exc).__name__}: {exc}).") from exc

        # Write the env file Hermes will source on startup. Single
        # source of truth for the API URL + Bearer the agent uses to
        # reach hal0.
        self._write_env_file(bearer_token=bearer_token)

    def uninstall(self) -> None:
        env_file = self._env_file_path()
        if env_file.exists():
            env_file.unlink()

    def status(self) -> str:
        return "installed" if self._env_file_path().exists() else "broken"

    # ── Internals ───────────────────────────────────────────────────────

    def _data_dir(self) -> Path:
        return _paths.var_lib() / "agents" / self.name

    def _env_file_path(self) -> Path:
        # ADR-0004 §6: "writes /etc/hal0/agents/hermes.env". Lives in
        # /etc so the user/admin can tweak it without disturbing
        # /var/lib state, same posture as openwebui.env.
        return _paths.etc() / "agents" / "hermes.env"

    def _write_env_file(self, *, bearer_token: str | None) -> None:
        api_base = os.environ.get("HAL0_API_URL", "http://127.0.0.1:8080").rstrip("/")
        lines = [
            "# hal0 — Hermes-Agent env (managed by hal0; safe to edit)",
            f"HAL0_API_URL={api_base}",
            f"HAL0_MCP_ADMIN_URL={api_base}/mcp/admin",
            f"HAL0_MCP_MEMORY_URL={api_base}/mcp/memory",
        ]
        if bearer_token:
            lines.append(f"HAL0_BEARER_TOKEN={bearer_token}")
        env_file = self._env_file_path()
        env_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = env_file.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.replace(env_file)
