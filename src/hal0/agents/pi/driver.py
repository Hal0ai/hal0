"""pi-Agent driver (ADR-0004 §6).

Pi is an open-source coding agent from Earendil Works
(``badlogic/pi-mono``). It ships as a single ``pi`` binary installed
via npm/cargo and is configured via ``~/.pi/agent/``.

This driver's responsibilities:

1. Probe whether ``pi`` is on PATH and the hal0-provider extension
   is deployed (``_probe_pi_installed``).
2. On the API/dashboard install path, deploy the hal0-provider
   extension into ``~/.pi/agent/extensions/hal0-provider/`` and
   write a pi ``settings.json`` that makes hal0 the default
   provider. Provisioning (``npm install -g pi`` or equivalent)
   lives in the bootstrap pipeline, run in the foreground by
   ``hal0 agent install pi``.
3. On uninstall, remove the hal0-provider extension directory.

Unlike Hermes, pi is NOT a daemon — it's a CLI/TUI tool invoked
interactively by the operator. There is no systemd unit, no TCP
health-check probe. Status reflects the on-disk truth: binary
present + extension deployed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404 — required for shim
from pathlib import Path
from typing import Any

from hal0.agents.manager import AgentDriver, AgentError
from hal0.config import paths as _paths

# The pi binary name. Expected on PATH (global npm install or cargo install).
_PI_BIN = "pi"

# Extension source (vendored in the hal0 repo).
_EXTENSION_SRC = Path(__file__).resolve().parent / "plugins" / "hal0-provider"
_THEME_SRC = Path(__file__).resolve().parent / "themes" / "hal0.json"

# Extension destination in pi's config tree.
_PI_AGENT_DIR = Path.home() / ".pi" / "agent"
_EXTENSION_DST = _PI_AGENT_DIR / "extensions" / "hal0-provider"
_THEME_DST = _PI_AGENT_DIR / "themes" / "hal0.json"
_SETTINGS_FILE = _PI_AGENT_DIR / "settings.json"


def _probe_pi_binary() -> bool:
    """Return True iff ``pi`` is on PATH."""
    return shutil.which(_PI_BIN) is not None


def _probe_extension_deployed() -> bool:
    """Return True iff the hal0-provider extension is deployed."""
    return (_EXTENSION_DST / "index.ts").exists()


class PiAgentDriver(AgentDriver):
    """Driver for the pi coding agent."""

    name = "pi"

    def __init__(self, *, runner: object | None = None) -> None:
        self._runner = runner if runner is not None else subprocess

    # ── AgentDriver protocol ────────────────────────────────────────────

    def install(self, *, bearer_token: str | None = None) -> None:
        if not _probe_pi_binary():
            raise AgentError(
                "pi is not installed — the `pi` binary was not found on "
                "PATH. Run `hal0 agent install pi` on the host: it installs "
                "the node.js toolchain and provisions pi via npm."
            )

        # Deploy the hal0-provider extension.
        _EXTENSION_DST.parent.mkdir(parents=True, exist_ok=True)
        if _EXTENSION_DST.exists():
            shutil.rmtree(_EXTENSION_DST)
        shutil.copytree(str(_EXTENSION_SRC), str(_EXTENSION_DST))

        # Deploy the hal0 theme.
        if _THEME_SRC.exists():
            _THEME_DST.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(_THEME_SRC), str(_THEME_DST))

        # Write pi settings — idempotent upsert of hal0 as default.
        self._write_pi_settings(bearer_token=bearer_token)

    def uninstall(self) -> None:
        # Remove the extension directory.
        if _EXTENSION_DST.exists():
            shutil.rmtree(_EXTENSION_DST)

        # Revert pi settings: remove hal0 provider defaults, restore
        # built-in defaults if hal0 was the active provider.
        self._revert_pi_settings()

    def status(self) -> str:
        """Return ``"installed"`` when pi binary + extension are both present."""
        if _probe_pi_binary() and _probe_extension_deployed():
            return "installed"
        return "broken"

    # ── Internals ───────────────────────────────────────────────────────

    def _data_dir(self) -> Path:
        return _paths.var_lib() / "agents" / self.name

    def _write_pi_settings(self, *, bearer_token: str | None) -> None:
        """Upsert hal0 into pi's settings.json as the default provider/model.

        Preserves all other existing settings. Only overwrites the
        ``defaultProvider`` and ``defaultModel`` keys. If the settings
        file doesn't exist, creates a minimal one.

        The ``HAL0_API_KEY`` env var is set in the pi agent's env file
        (same pattern as Hermes). Pi's ``$HAL0_API_KEY`` syntax in the
        provider config resolves it at runtime.
        """
        api_base = os.environ.get("HAL0_API_URL", "http://127.0.0.1:8080").rstrip("/")

        existing: dict[str, Any] = {}
        if _SETTINGS_FILE.exists():
            try:
                existing = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = {}

        existing["defaultProvider"] = "hal0"
        existing["defaultModel"] = "agent"
        existing["theme"] = "hal0"

        _SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        tmp.replace(_SETTINGS_FILE)

        # Write the env file for the agent wrapper.
        env_dir = _paths.etc() / "agents"
        env_dir.mkdir(parents=True, exist_ok=True)
        env_file = env_dir / "pi.env"
        lines = [
            "# hal0 — pi Agent env (managed by hal0; safe to edit)",
            f"HAL0_API_URL={api_base}",
        ]
        if bearer_token:
            lines.append(f"HAL0_BEARER_TOKEN={bearer_token}")
        tmp_env = env_file.with_suffix(".tmp")
        tmp_env.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp_env.replace(env_file)

    def _revert_pi_settings(self) -> None:
        """Remove hal0-specific defaults from pi settings.json.

        Restores built-in defaults: openrouter / deepseek/deepseek-v4-pro.
        Only touches the file if hal0 is the current default provider.
        """
        if not _SETTINGS_FILE.exists():
            return
        try:
            settings = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(settings, dict):
            return

        if settings.get("defaultProvider") == "hal0":
            settings["defaultProvider"] = "openrouter"
            settings["defaultModel"] = "deepseek/deepseek-v4-pro"

        tmp = _SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        tmp.replace(_SETTINGS_FILE)
