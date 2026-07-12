"""opencode bundled-agent driver (ADR-0004 §6, sibling of pi-coder).

[OpenCode](https://opencode.ai) is a terminal coding agent with native
OpenAI-compatible providers + MCP support. Unlike pi-coder — whose model
autodiscovery and memory ride vendored TypeScript extensions — opencode's
whole hal0 wiring lives in a single JSON config, so this driver only:

1. Runs ``installer/agents/opencode.sh`` (track-latest npm install of the
   ``opencode-ai`` CLI; NO version pin per ADR-0004 §3 — the nightly smoke
   catches upstream breakage).
2. Writes ``~/.config/opencode/opencode.json`` wiring:
   - **Provider** — hal0 as an ``@ai-sdk/openai-compatible`` provider at
     ``HAL0_API_URL/v1`` (the hal0-api gateway, ``discover_models``), with
     the ``hal0/*`` slot virtuals as the model picker set and ``hal0/agent``
     as the default. Live-resolves whatever slots are loaded.
   - **Memory** — the hindsight-backed ``hal0-memory`` MCP mount
     (``<api>/mcp/memory/mcp``, 5 tools: add/search/recall/list/delete),
     the same LAN-reachable surface hermes uses, scoped by the
     ``X-hal0-Agent`` header. The 30-tool *native* hindsight MCP is
     loopback-only by default (see ``/etc/hal0/mcp-servers/hindsight.toml``)
     and is intentionally NOT wired here — operators who LAN-expose it can
     add an ``mcp.hindsight`` entry by hand.

Idempotent: every write is an atomic tmp+rename overwrite. ``HAL0_API_URL``
is honoured the same way the CLI does, so a remote install (opencode in its
own container/box) points at hal0 over the LAN by exporting it.
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 — required for shim
from pathlib import Path
from typing import Any

from hal0.agents.manager import AgentDriver, AgentError, installer_script_path
from hal0.config import paths as _paths

# 127.0.0.1 mirrors pi-coder's default: the bundled-agent path runs on the
# hal0 box; remote installs override via HAL0_API_URL.
_HAL0_API_BASE_DEFAULT = "http://127.0.0.1:8080"

# hal0-api's stable slot virtuals (served on /v1 regardless of which
# physical slot is loaded). Kept as the opencode model picker set so the
# config is deterministic; the gateway live-resolves to the active slot.
_HAL0_MODELS: tuple[str, ...] = ("agent", "code", "brain", "flm", "nano", "ops", "utility")

# Dev-mode sentinel: hal0-api runs auth-disabled by default, but opencode
# still treats a provider as requiring a key, so we always write one.
_DEV_API_KEY = "hal0-local"


def _api_base() -> str:
    """Honour HAL0_API_URL the same way the CLI does."""
    return os.environ.get("HAL0_API_URL", _HAL0_API_BASE_DEFAULT).rstrip("/")


class OpenCodeDriver(AgentDriver):
    """Driver for the opencode bundled agent."""

    name = "opencode"

    def __init__(self, *, runner: object | None = None) -> None:
        # Tests inject a fake subprocess module to assert correct argv +
        # avoid spawning a real shell / npm / network.
        self._runner = runner if runner is not None else subprocess

    # ── AgentDriver protocol ────────────────────────────────────────────

    def install(self, *, bearer_token: str | None = None) -> None:
        script = installer_script_path(self.name)
        if not script.is_file():
            raise AgentError(
                f"installer script missing at {script}. This hal0 install looks "
                "packaged without the bundled-agent scripts — reinstall hal0 from "
                "a release tarball or git clone."
            )

        env = os.environ.copy()
        data_dir = self._data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        env["HAL0_AGENT_DATA_DIR"] = str(data_dir)
        env["HAL0_API_URL"] = _api_base()
        if bearer_token:
            env["HAL0_BEARER_TOKEN"] = bearer_token

        try:
            self._runner.run(  # type: ignore[attr-defined]
                ["bash", str(script)],
                env=env,
                check=True,
            )
        except Exception as exc:  # subprocess.CalledProcessError or others
            raise AgentError(
                f"opencode install failed ({type(exc).__name__}: {exc}). "
                "Upstream opencode may have shipped a breaking change — the "
                "nightly smoke test exists to catch this; check "
                "https://github.com/Hal0ai/hal0/actions for the latest run."
            ) from exc

        # Provider + memory MCP wiring — a single source-of-truth JSON so we
        # have full control over the shape.
        self._write_config(bearer_token=bearer_token)

    def uninstall(self) -> None:
        # opencode's config is hal0-authored data, always safe to remove.
        cfg = self._config_path()
        if cfg.exists():
            cfg.unlink()

    def status(self) -> str:
        """Return ``"installed"`` when the hal0 opencode config is present."""
        return "installed" if self._config_path().exists() else "broken"

    # ── config helpers ──────────────────────────────────────────────────

    def _data_dir(self) -> Path:
        return _paths.var_lib() / "agents" / self.name

    def _config_path(self) -> Path:
        # opencode reads global config from XDG ``~/.config/opencode/``.
        return Path.home() / ".config" / "opencode" / "opencode.json"

    def _write_config(self, *, bearer_token: str | None) -> None:
        """Atomic write of opencode.json — provider (hal0 slots) + the
        hindsight-backed hal0-memory MCP. Overwriting is the idempotent
        path."""
        api = _api_base()

        headers: dict[str, str] = {"X-hal0-Agent": self.name}
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"

        config: dict[str, Any] = {
            "$schema": "https://opencode.ai/config.json",
            "model": "hal0/agent",
            "provider": {
                "hal0": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "hal0 (hal0-api gateway)",
                    "options": {
                        "baseURL": f"{api}/v1",
                        "apiKey": bearer_token or _DEV_API_KEY,
                    },
                    "models": {model: {"name": model} for model in _HAL0_MODELS},
                }
            },
            "mcp": {
                "hal0-memory": {
                    "type": "remote",
                    "url": f"{api}/mcp/memory/mcp",
                    "enabled": True,
                    "headers": headers,
                    "timeout": 30000,
                }
            },
        }

        cfg = self._config_path()
        cfg.parent.mkdir(parents=True, exist_ok=True)
        tmp = cfg.with_suffix(".tmp")
        tmp.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        tmp.replace(cfg)
