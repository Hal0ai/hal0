"""Hermes-Agent driver (ADR-0004 §6).

Hermes is user-owned upstream — and crucially, the user cannot PR
upstream NousResearch/hermes-agent. So hal0-awareness lives on the
hal0 side, in a hal0-owned wrapper script (``hal0-hermes``) that
sources ``/etc/hal0/agents/hermes.env`` and ``exec``s the upstream
``hermes`` binary.

This driver's responsibilities:

1. Probe whether the ``hal0-hermes`` wrapper is installed and
   functional (``--hal0-ready`` sentinel returns 0).
2. Shell out to ``installer/agents/hermes-agent.sh`` to install the
   wrapper if the user invokes this path manually.
3. Write the canonical env file at ``/etc/hal0/agents/hermes.env``
   that the wrapper sources on every hermes invocation.

The "hal0-awareness" probe shifted: previously it checked the upstream
binary for a ``--hal0-config`` flag that was never going to ship.
Now it checks that the hal0-owned wrapper is on PATH and answers the
``--hal0-ready`` probe. The shell script
``installer/agents/hermes-agent.sh`` mirrors this gate at the shell
level so a curl|bash invocation also short-circuits cleanly.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 — required for shim
from pathlib import Path

from hal0.agents.manager import (
    AgentDriver,
    AgentError,
    HermesUpstreamMissingError,
)
from hal0.config import paths as _paths

# Hermes's installer script lives at ``installer/agents/hermes-agent.sh``
# (not ``hermes.sh``) — kept as-is to avoid breaking the curl|bash
# invocation contract the dashboard uses. We resolve it ourselves
# rather than via :func:`installer_script_path`, which assumes
# ``{name}.sh``.
_INSTALLER_SCRIPT_REL = "installer/agents/hermes-agent.sh"


def _installer_script_path() -> Path:
    # parents[0]=agents, [1]=hal0, [2]=src, [3]=repo root — same
    # resolution shape as :func:`installer_script_path` in the manager.
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / _INSTALLER_SCRIPT_REL


# The wrapper binary name. Installed by installer/agents/hermes-agent.sh
# to /usr/local/bin (root) or ~/.local/bin (user). Sourcing the env
# file + exec'ing upstream `hermes` is the whole job.
_WRAPPER_BIN_NAME = "hal0-hermes"
_WRAPPER_READY_FLAG = "--hal0-ready"
_UPSTREAM_BIN_NAME = "hermes"


def _probe_hermes_upstream() -> bool:
    """Return True iff upstream ``hermes`` is on PATH.

    This is the **pre-install** gate — the wrapper's whole job is to
    source the env file and exec upstream ``hermes``, so installing
    the wrapper without upstream Hermes is pointless. Mirrors the
    `command -v hermes` check at the top of
    ``installer/agents/hermes-agent.sh``.
    """
    return shutil.which(_UPSTREAM_BIN_NAME) is not None


def _probe_wrapper_installed() -> bool:
    """Return True iff the hal0-hermes wrapper is installed and functional.

    This is the **post-install** health check — used by
    :meth:`HermesDriver.status` and the dashboard's installed-agent row
    to surface whether the wrapper is wired correctly. NOT used to gate
    ``install()``; that gates on :func:`_probe_hermes_upstream` because
    the installer's job *is* to put the wrapper on PATH.

    Two cheap checks, AND-ed:

    1. ``shutil.which("hal0-hermes")`` resolves (wrapper is on PATH).
    2. ``hal0-hermes --hal0-ready`` returns rc 0 (wrapper is readable,
       executable, and not corrupted).
    """
    bin_path = shutil.which(_WRAPPER_BIN_NAME)
    if bin_path is None:
        return False

    try:
        result = subprocess.run(  # nosec B603 — known-safe argv
            [bin_path, _WRAPPER_READY_FLAG],
            check=False,
            capture_output=True,
            timeout=5,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False

    return result.returncode == 0


class HermesDriver(AgentDriver):
    """Driver for the Hermes-Agent bundled agent."""

    name = "hermes"

    def __init__(self, *, runner: object | None = None, prober: object | None = None) -> None:
        # ``runner`` parallels :class:`PiCoderDriver` — tests inject a
        # fake subprocess. ``prober`` lets tests force the upstream
        # pre-install gate without needing a real ``hermes`` on PATH.
        self._runner = runner if runner is not None else subprocess
        self._prober = prober if prober is not None else _probe_hermes_upstream

    # ── AgentDriver protocol ────────────────────────────────────────────

    def install(self, *, bearer_token: str | None = None) -> None:
        # Pre-flight: upstream ``hermes`` must be on PATH. The wrapper
        # we're about to install just sources the env file and execs
        # upstream hermes — installing it without the binary is
        # pointless. Mirror of `command -v hermes` in
        # installer/agents/hermes-agent.sh.
        if not self._prober():
            raise HermesUpstreamMissingError(
                "Upstream `hermes` binary not found on PATH. Install it "
                "first: `pipx install hermes-agent` (recommended) or "
                "`pip install --user hermes-agent`. Then retry "
                "`hal0 agent install hermes`."
            )

        script = _installer_script_path()
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

        # Write the env file the wrapper sources on every hermes
        # invocation. Single source of truth for the API URL + Bearer
        # the agent uses to reach hal0.
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
        # /var/lib state, same posture as openwebui.env. The wrapper
        # sources this file on every hermes invocation.
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
