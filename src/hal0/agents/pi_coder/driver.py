"""pi-coder driver.

pi (upstream `earendil-works/pi`, formerly `badlogic/pi-mono`) is a
third-party CLI/TUI coding agent. hal0's shim wires it to run against
hal0 out of the box:

1. Installer shell script (``installer/agents/pi-coder.sh``) — invokes
   the installer script (track-latest of ``@earendil-works/pi-coding-agent``
   + ``pi-mcp-adapter``).
2. Model provider — deploys the ``hal0-provider`` extension
   (``plugins/hal0-provider/``), which auto-discovers active hal0 slots
   from ``/v1/models`` and registers them as an OpenAI-compatible pi
   provider. Set as the default provider/model in ``settings.json``.
3. Memory — deploys the ``hal0-memory`` extension
   (``plugins/hal0-memory/``), a native REST tool extension against
   hal0-api's ``/api/memory/*`` surface with dual private/shared banks.
   This supersedes routing memory through the generic pi-mcp-adapter
   MCP proxy — only ``hal0-admin`` rides that path now.
4. Delegation — best-effort ``pi install npm:pi-subagents`` so the LLM
   can delegate to focused child agents (scout/planner/worker/reviewer/
   oracle/etc). Subagents inherit pi's default model (hal0/agent) unless
   overridden, so no remote-provider config is required out of the box.
5. Theme — deploys the ``hal0`` theme and sets it as default.

``pi-memory-md`` (upstream's own project-scoped markdown memory) is left
untouched — different scope from hal0's memory MCP/REST surface
(CONTEXT.md "memory").

Unlike Hermes, pi is NOT a daemon — it's a CLI/TUI tool invoked
interactively by the operator. There is no systemd unit, no TCP
health-check probe. Status reflects on-disk truth: adapter config +
hal0-provider extension present.

Idempotency: every write here is an atomic tmp+rename overwrite, safe
to re-run.

pi's own config tree (``~/.pi/agent/``) is resolved from ``Path.home()``
per call, NOT cached at import time — tests rely on this to redirect
writes under a temp ``HOME`` via ``monkeypatch.setenv``.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess  # nosec B404 — required for shim
from pathlib import Path
from typing import Any

from hal0.agents.manager import AgentDriver, AgentError, installer_script_path
from hal0.config import paths as _paths

# MCP endpoints. Both ride the existing hal0-api process
# (admin and memory). 127.0.0.1 is intentional: the bundled
# agent runs on the same box as the API; LAN-exposed MCP is Phase 9
# ("MCP client side of hal0"). Memory is NOT wired here — the
# hal0-memory extension talks to the REST surface directly.
_HAL0_API_BASE_DEFAULT = "http://127.0.0.1:8080"
_MCP_ADMIN_PATH = "/mcp/admin"

# Vendored extension + theme sources, deployed into pi's config tree at
# install time. These live inside the hal0 repo, not the operator's
# home — safe as module-level constants.
_PLUGINS_DIR = Path(__file__).resolve().parent / "plugins"
_PROVIDER_EXT_SRC = _PLUGINS_DIR / "hal0-provider"
_MEMORY_EXT_SRC = _PLUGINS_DIR / "hal0-memory"
_THEME_SRC = Path(__file__).resolve().parent / "themes" / "hal0.json"

# Package added for subagent delegation. Best-effort:
# needs npm + network, and is an enhancement layered on top of the core
# provider/memory/theme wiring, not a hard requirement.
_SUBAGENTS_PACKAGE = "npm:pi-subagents"

# Upstream's own built-in defaults, restored on uninstall iff hal0 was
# the active default (mirrors the pre-hal0 factory settings).
_UPSTREAM_DEFAULT_PROVIDER = "openrouter"
_UPSTREAM_DEFAULT_MODEL = "deepseek/deepseek-v4-pro"


def _api_base() -> str:
    """Honour HAL0_API_URL the same way the CLI does."""
    return os.environ.get("HAL0_API_URL", _HAL0_API_BASE_DEFAULT).rstrip("/")


class PiCoderDriver(AgentDriver):
    """Driver for the pi-coder bundled agent."""

    name = "pi-coder"

    def __init__(self, *, runner: object | None = None) -> None:
        # Tests inject a fake subprocess module to assert correct argv +
        # avoid spawning real shells. Default = real subprocess module.
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

        # The shell script needs the data dir to exist + know where to
        # drop the adapter config. We export both so the script is
        # tomli-w-free POSIX shell.
        env = os.environ.copy()
        data_dir = self._data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        env["HAL0_AGENT_DATA_DIR"] = str(data_dir)
        env["HAL0_API_URL"] = _api_base()
        if bearer_token:
            # Script consults this for adapter config wiring. Empty =
            # "the dev install has auth-disabled, skip the Authorization
            # header" — the script handles that branch.
            env["HAL0_BEARER_TOKEN"] = bearer_token

        try:
            self._runner.run(  # type: ignore[attr-defined]
                ["bash", str(script)],
                env=env,
                check=True,
            )
        except Exception as exc:  # subprocess.CalledProcessError or others
            raise AgentError(
                f"pi-coder install failed ({type(exc).__name__}: {exc}). "
                "Upstream pi-mono may have shipped a breaking change — the "
                "nightly smoke test exists to catch this; check "
                "https://github.com/Hal0ai/hal0/actions for the latest run."
            ) from exc

        # Model provider (autodiscovers hal0 slots) + memory (shared
        # banks) extensions, and the hal0 theme.
        self._deploy_extension(_PROVIDER_EXT_SRC, self._provider_ext_dst())
        self._deploy_extension(_MEMORY_EXT_SRC, self._memory_ext_dst())
        self._deploy_theme()

        # Default provider/model/theme — a single source-of-truth JSON
        # upsert so we have full control over the shape (the shell
        # script can't easily serialise nested JSON without jq, and jq
        # isn't a hard dep).
        self._write_pi_settings()

        # Adapter config for hal0-admin (slot/model/hardware skills).
        # Memory rides the native hal0-memory extension now, not this
        # generic MCP proxy.
        self._write_adapter_config(bearer_token=bearer_token)

        # Delegation. Best-effort: a failure here (offline box, upstream
        # hiccup) shouldn't block the install — the core wiring above
        # already works without it.
        self._install_subagents()

    def uninstall(self) -> None:
        # Adapter config — pi-coder's own data, always safe to remove.
        cfg = self._adapter_config_path()
        if cfg.exists():
            cfg.unlink()

        # Extensions + theme deployed at install time.
        for dst in (self._provider_ext_dst(), self._memory_ext_dst()):
            if dst.exists():
                shutil.rmtree(dst)
        theme_dst = self._theme_dst()
        if theme_dst.exists():
            theme_dst.unlink()

        # Revert pi settings: remove hal0 provider/theme defaults,
        # restore upstream's built-in defaults if hal0 was active.
        self._revert_pi_settings()

        # Best-effort: pi may not be on PATH anymore, or the package may
        # already be gone. Either way, uninstall must not raise.
        with contextlib.suppress(Exception):
            self._runner.run(  # type: ignore[attr-defined]
                ["pi", "remove", _SUBAGENTS_PACKAGE],
                check=False,
            )

    def status(self) -> str:
        """Return ``"installed"`` when the adapter config + hal0-provider
        extension are both present."""
        if self._adapter_config_path().exists() and self._provider_extension_deployed():
            return "installed"
        return "broken"

    # ── pi config-tree path helpers ──────────────────────────────────────
    #
    # Resolved from Path.home() on every call (not cached at import time)
    # so tests can redirect writes under a temp HOME via monkeypatch.

    def _pi_agent_dir(self) -> Path:
        return Path.home() / ".pi" / "agent"

    def _provider_ext_dst(self) -> Path:
        return self._pi_agent_dir() / "extensions" / "hal0-provider"

    def _memory_ext_dst(self) -> Path:
        return self._pi_agent_dir() / "extensions" / "hal0-memory"

    def _theme_dst(self) -> Path:
        return self._pi_agent_dir() / "themes" / "hal0.json"

    def _settings_file(self) -> Path:
        return self._pi_agent_dir() / "settings.json"

    def _provider_extension_deployed(self) -> bool:
        return (self._provider_ext_dst() / "index.ts").exists()

    # ── Internals ───────────────────────────────────────────────────────

    def _data_dir(self) -> Path:
        return _paths.var_lib() / "agents" / self.name

    def _adapter_config_path(self) -> Path:
        # pi-mcp-adapter is a proxy-tool MCP routing layer
        # ("~200 tokens per dispatch instead of dumping the full tool
        # catalog"). Config lives in the per-agent data dir so a
        # ``hal0 agent uninstall`` cleans it up.
        return self._data_dir() / "pi-mcp-adapter.json"

    def _write_adapter_config(self, *, bearer_token: str | None) -> None:
        """Atomic write of the adapter JSON. Overwriting is the
        idempotent path. Only wires hal0-admin — memory rides the
        native hal0-memory extension instead of this generic proxy."""
        api_base = _api_base()
        servers: dict[str, dict[str, object]] = {
            "hal0-admin": {
                "url": f"{api_base}{_MCP_ADMIN_PATH}",
            },
        }
        if bearer_token:
            # Same Authorization header the dashboard would send
            # (Bearer token, reused — no new credential type).
            for srv in servers.values():
                srv["headers"] = {"Authorization": f"Bearer {bearer_token}"}

        payload = {"version": 1, "servers": servers}
        cfg = self._adapter_config_path()
        cfg.parent.mkdir(parents=True, exist_ok=True)
        tmp = cfg.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(cfg)

    def _deploy_extension(self, src: Path, dst: Path) -> None:
        if not src.exists():
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(str(src), str(dst))

    def _deploy_theme(self) -> None:
        if not _THEME_SRC.exists():
            return
        theme_dst = self._theme_dst()
        theme_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(_THEME_SRC), str(theme_dst))

    def _write_pi_settings(self) -> None:
        """Upsert hal0 into pi's settings.json as the default
        provider/model/theme. Preserves all other existing settings
        (packages, subagent overrides, etc). Creates a minimal file if
        none exists yet."""
        settings_file = self._settings_file()
        existing: dict[str, Any] = {}
        if settings_file.exists():
            try:
                existing = json.loads(settings_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = {}

        existing["defaultProvider"] = "hal0"
        existing["defaultModel"] = "agent"
        existing["theme"] = "hal0"

        settings_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = settings_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        tmp.replace(settings_file)

    def _revert_pi_settings(self) -> None:
        """Remove hal0-specific defaults from pi settings.json, restoring
        upstream's built-in defaults. Only touches the file if hal0 is
        the current default provider (don't clobber an operator's own
        later override)."""
        settings_file = self._settings_file()
        if not settings_file.exists():
            return
        try:
            settings = json.loads(settings_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(settings, dict):
            return

        if settings.get("defaultProvider") == "hal0":
            settings["defaultProvider"] = _UPSTREAM_DEFAULT_PROVIDER
            settings["defaultModel"] = _UPSTREAM_DEFAULT_MODEL
        if settings.get("theme") == "hal0":
            settings.pop("theme", None)

        tmp = settings_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        tmp.replace(settings_file)

    def _install_subagents(self) -> None:
        """Best-effort ``pi install npm:pi-subagents`` for delegation.

        Subagents inherit pi's default model (hal0/agent, set above)
        unless the operator configures an override, so this needs no
        further wiring. A failure (offline box, upstream hiccup) is
        swallowed — the core provider/memory/theme wiring already works
        without it.
        """
        with contextlib.suppress(Exception):
            self._runner.run(  # type: ignore[attr-defined]
                ["pi", "install", _SUBAGENTS_PACKAGE],
                check=True,
            )
