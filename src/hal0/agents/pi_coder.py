"""pi-coder driver (ADR-0004 §6).

Shim around ``installer/agents/pi-coder.sh``. Responsibilities:

- Invoke the installer script (track-latest of ``badlogic/pi-mono`` +
  ``pi-mcp-adapter``).
- Write the pi-mcp-adapter config pointing at hal0's two MCP servers
  (``/mcp/admin`` and ``/mcp/memory``) with the active hal0 Bearer
  token.
- Leave ``pi-memory-md`` upstream extension alone. CONTEXT.md is
  explicit: that's project-scoped markdown memory, different scope from
  hal0's cross-app memory MCP. They coexist.

Idempotency: the shell script is responsible for re-running cleanly;
this Python wrapper only writes the adapter config (which is also an
idempotent operation — overwrite-in-place via tmp+rename).
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 — required for shim
from pathlib import Path

from hal0.agents.manager import AgentDriver, AgentError, installer_script_path
from hal0.config import paths as _paths

# MCP endpoints. Both ride the existing hal0-api process per ADR-0004 §4
# (admin) and ADR-0005 (memory). 127.0.0.1 is intentional: the bundled
# agent runs on the same box as the API; LAN-exposed MCP is Phase 9
# (`MCP client side of hal0`).
_HAL0_API_BASE_DEFAULT = "http://127.0.0.1:8080"
_MCP_ADMIN_PATH = "/mcp/admin"
_MCP_MEMORY_PATH = "/mcp/memory"


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
            # Script consults this for adapter config + pi base-url
            # wiring. Empty = "the dev install has auth-disabled, skip
            # the Authorization header" — the script handles that
            # branch.
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

        # Adapter config — written by Python so we have a single source
        # of truth for the JSON shape (the shell script can't easily
        # serialise nested JSON without jq, and jq isn't a hard dep).
        self._write_adapter_config(bearer_token=bearer_token)

    def uninstall(self) -> None:
        # pi-mono lives in the user's profile (npm / cargo / however
        # the upstream landed it). We don't yank the upstream binary —
        # the operator may want it stand-alone. We do nuke the adapter
        # config so a stale Bearer token doesn't leak.
        cfg = self._adapter_config_path()
        if cfg.exists():
            cfg.unlink()

    def status(self) -> str:
        """Coarse status: adapter config present + readable."""
        return "installed" if self._adapter_config_path().exists() else "broken"

    # ── Internals ───────────────────────────────────────────────────────

    def _data_dir(self) -> Path:
        return _paths.var_lib() / "agents" / self.name

    def _adapter_config_path(self) -> Path:
        # pi-mcp-adapter is a proxy-tool MCP routing layer (ADR-0004 §6,
        # "~200 tokens per dispatch instead of dumping the full tool
        # catalog"). Config lives in the per-agent data dir so a
        # ``hal0 agent uninstall`` cleans it up.
        return self._data_dir() / "pi-mcp-adapter.json"

    def _write_adapter_config(self, *, bearer_token: str | None) -> None:
        """Atomic write of the adapter JSON. Overwriting is the
        idempotent path."""
        api_base = _api_base()
        servers: dict[str, dict[str, object]] = {
            "hal0-admin": {
                "url": f"{api_base}{_MCP_ADMIN_PATH}",
            },
            "hal0-memory": {
                "url": f"{api_base}{_MCP_MEMORY_PATH}",
            },
        }
        if bearer_token:
            # Same Authorization header the dashboard would send
            # (ADR-0001 Bearer token, reused — no new credential type).
            for srv in servers.values():
                srv["headers"] = {"Authorization": f"Bearer {bearer_token}"}

        payload = {"version": 1, "servers": servers}
        cfg = self._adapter_config_path()
        cfg.parent.mkdir(parents=True, exist_ok=True)
        tmp = cfg.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(cfg)
