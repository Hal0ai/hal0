"""Turnstone bundled-agent driver (ADR-0004 §6, sibling of hermes).

Turnstone is a PyPI package (console scripts ``turnstone`` +
``turnstone-server``) installed into a managed venv, like hermes-agent.
The heavy provisioning (venv + pip install + config.toml render +
MCP/model/memory wiring) is a multi-minute foreground job that can't run
inside a single HTTP request, so it lives in the bootstrap pipeline
:mod:`hal0.agents.turnstone_provision`, driven by
``hal0 agent install turnstone``.

This driver is the THIN API/dashboard path:

1. ``install()`` registers an already-provisioned agent by writing the
   canonical driver env file (``/etc/hal0/agents/turnstone.env``) and
   refuses if the managed binary isn't present yet (points the operator
   at the CLI instead of half-wiring).
2. ``status()`` is a cheap health check: systemctl → loopback :9129 →
   env-file presence.
3. ``uninstall()`` reads ``provision.json`` for the recorded binary +
   removes the managed binary, shim, config, and SQLite DB — the
   artifacts that live OUTSIDE the manager's seed+data+state triad.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess  # nosec B404 — required for shim
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hal0.agents.manager import AgentDriver, AgentError
from hal0.config import paths as _paths

# Keep in sync with :mod:`hal0.agents.turnstone_provision`. Mirrored as plain
# constants so the driver stays cheap and doesn't import the heavy provisioner
# at driver-import time (same posture as the hermes driver). Turnstone is a
# PyPI package installed into a managed venv (like hermes-agent), NOT a Go
# binary — the console scripts live under ``<venv>/bin``.
_VENV = Path("/var/lib/hal0/venvs/turnstone")
_MANAGED_BIN = _VENV / "bin" / "turnstone"
_SERVER_BIN = _VENV / "bin" / "turnstone-server"
_CLI_SHIM = Path("/usr/local/bin/turnstone")
_SERVER_HOST = "127.0.0.1"
_SERVER_PORT = 9129
_UNIT = "hal0-agent@turnstone.service"


def _probe_provisioned() -> bool:
    """True iff turnstone is installed in the managed venv (or shimmed)."""
    return _SERVER_BIN.exists() or _MANAGED_BIN.exists() or _CLI_SHIM.exists()


def _probe_systemd_unit_active(unit: str) -> bool:
    """True iff ``systemctl is-active <unit>`` exits 0. Best-effort (2s cap)."""
    if shutil.which("systemctl") is None:
        return False
    try:
        result = subprocess.run(  # nosec B603 — fixed argv
            ["systemctl", "is-active", unit],
            check=False,
            capture_output=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _probe_tcp_port(host: str, port: int, *, timeout: float = 1.0) -> bool:
    """True iff a TCP connect to ``host:port`` succeeds within ``timeout``."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class TurnstoneDriver(AgentDriver):
    """Driver for the turnstone bundled agent."""

    name = "turnstone"

    def __init__(self, *, prober: Callable[[], bool] | None = None) -> None:
        # ``prober`` lets tests force the pre-register gate without a real
        # binary on disk (parallels HermesDriver).
        self._prober: Callable[[], bool] = prober if prober is not None else _probe_provisioned

    # ── AgentDriver protocol ────────────────────────────────────────────

    def install(self, *, bearer_token: str | None = None) -> None:
        # THIN path: register an already-provisioned turnstone by writing the
        # driver env file. It does NOT provision — pinning the Go binary +
        # rendering config is the foreground CLI's job.
        if not self._prober():
            raise AgentError(
                "turnstone is not provisioned — the managed binary at "
                f"{_MANAGED_BIN} does not exist. Run `hal0 agent install "
                "turnstone` on the host: it pins the binary and renders the "
                "config/model/MCP/memory wiring. (The hal0 daemon can't run "
                "the multi-minute provisioning over HTTP, so the dashboard/API "
                "install only registers an already-provisioned agent.)"
            )
        data_dir = self._data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        self._write_env_file(bearer_token=bearer_token)

    def uninstall(self) -> None:
        # Order matters (mirrors HermesDriver): read provision.json BEFORE the
        # manager strips the state dir, then remove the artifacts that live
        # outside the manager's seed+data+state triad — the managed binary,
        # shim, config home, and SQLite DB.
        provision = self._load_provision()
        for target in self._external_artifacts(provision):
            with __import__("contextlib").suppress(OSError):
                if target.is_dir() and not target.is_symlink():
                    shutil.rmtree(target)
                else:
                    target.unlink(missing_ok=True)

        env_file = self._env_file_path()
        if env_file.exists():
            env_file.unlink()

    def status(self) -> str:
        """``"installed"`` when turnstone is running/reachable, else ``"broken"``.

        Priority (cheapest first): systemctl unit active → loopback :9129
        connect → env-file presence (installed but not started yet).
        """
        if _probe_systemd_unit_active(_UNIT):
            return "installed"
        if _probe_tcp_port(_SERVER_HOST, _SERVER_PORT, timeout=1.0):
            return "installed"
        if self._env_file_path().exists():
            return "installed"
        return "broken"

    # ── Internals ───────────────────────────────────────────────────────

    def _data_dir(self) -> Path:
        return _paths.var_lib() / "agents" / self.name

    def _env_file_path(self) -> Path:
        # /etc so admins can tweak without disturbing /var/lib state — same
        # posture as hermes.env. The shim sources this on every invocation.
        return _paths.etc() / "agents" / f"{self.name}.env"

    def _provision_state_path(self) -> Path:
        return _paths.var_lib() / "state" / "agents" / self.name / "provision.json"

    def _load_provision(self) -> dict[str, Any] | None:
        """Best-effort load of the bootstrap checkpoint (None on any failure)."""
        path = self._provision_state_path()
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def _external_artifacts(self, provision: dict[str, Any] | None) -> list[Path]:
        """The out-of-triad paths uninstall must remove.

        The managed venv (~hundreds of MiB, like hermes's), the SQLite DB, the
        turnstone home, and the two console-script shims. Prefers the recorded
        ``db_path`` / ``turnstone_home`` from provision.json (honours an
        operator override), falling back to the module defaults so a
        missing/corrupt checkpoint still cleans the canonical locations.
        """
        prov = provision or {}
        db_path = Path(
            prov.get("db_path") or (_paths.var_lib() / "agents" / self.name / "turnstone.db")
        )
        home = Path(prov.get("turnstone_home") or (_paths.var_lib() / ".turnstone"))
        server_shim = _CLI_SHIM.with_name("turnstone-server")
        return [_CLI_SHIM, server_shim, _VENV, db_path, home]

    def _write_env_file(self, *, bearer_token: str | None) -> None:
        api_base = os.environ.get("HAL0_API_URL", "http://127.0.0.1:8080").rstrip("/")
        lines = [
            "# hal0 — turnstone driver env (managed by hal0; safe to edit)",
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
