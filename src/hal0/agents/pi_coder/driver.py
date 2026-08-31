"""pi driver (spec 2026-08-31 pi-agent-installable, D1-D6).

pi (upstream `earendil-works/pi`, formerly `badlogic/pi-mono`) is a
third-party CLI/TUI coding agent (``kind="cli"`` — no daemon, no
systemd unit, no TCP health-check probe; status reflects on-disk
truth). hal0's shim wires it to run against hal0 with a deliberately
minimal profile (spec D2):

1. Installer shell script (``installer/agents/pi.sh``) — installs the
   pinned ``@earendil-works/pi-coding-agent`` CLI + ``pi-mcp-adapter``.
2. Model provider — deploys the ``hal0-provider`` extension
   (``plugins/hal0-provider/``), which auto-discovers active hal0 slots
   from ``/v1/models`` and registers them as an OpenAI-compatible pi
   provider. Set as the default provider/model in ``settings.json``.
3. Memory — two wires (spec D3/D4), not one:
   a. ``hal0-memory`` MCP server upserted into pi's global
      ``~/.pi/agent/mcp.json`` override file (consumed by the pinned
      ``pi-mcp-adapter`` package), pointed at hal0-api's memory MCP
      mount.
   b. The ``hindsight`` extension (``plugins/hindsight/``), a vendored
      wrapper around ``@vectorize-io/hindsight-coding-agents`` pinned
      in its own ``package.json`` — resolved via ``npm install`` inside
      the *deployed* copy at install time. ``~/.hindsight/coding-agent.json``
      is seeded iff absent (shared state with other harnesses on the
      box; never overwritten once an operator or another agent has
      written it).
4. Theme — deploys the ``hal0`` theme and sets it as default.

``pi-memory-md`` (upstream's own project-scoped markdown memory) is left
untouched — different scope from hal0's memory MCP/REST surface
(CONTEXT.md "memory").

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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hal0.agents.manager import AgentDriver, AgentError, installer_script_path
from hal0.config import paths as _paths

# MCP endpoints. Both ride the existing hal0-api process per ADR-0004 §4
# (admin) and ADR-0005 (memory). 127.0.0.1 is intentional: the bundled
# agent runs on the same box as the API; LAN-exposed MCP is Phase 9
# ("MCP client side of hal0"). The memory server is mounted at
# ``/mcp/memory`` (src/hal0/api/mcp_mount.py) but the FastMCP
# streamable-HTTP transport it serves lives one level deeper, at
# ``<mount>/mcp`` — Hermes wires the identical server the same way
# (``hermes_provision.py:1424``: "http://127.0.0.1:8080/mcp/memory/mcp").
_HAL0_API_BASE_DEFAULT = "http://127.0.0.1:8080"
_MCP_MEMORY_PATH = "/mcp/memory/mcp"

# Vendored extension + theme sources, deployed into pi's config tree at
# install time. These live inside the hal0 repo, not the operator's
# home — safe as module-level constants.
_PLUGINS_DIR = Path(__file__).resolve().parent / "plugins"
_PROVIDER_EXT_SRC = _PLUGINS_DIR / "hal0-provider"
_HINDSIGHT_EXT_SRC = _PLUGINS_DIR / "hindsight"
_THEME_SRC = Path(__file__).resolve().parent / "themes" / "hal0.json"

# pi-mcp-adapter pin (spec D2) — the generic MCP proxy layer used for
# hal0-memory (and anything else the operator points it at). Bump in
# lockstep with installer/agents/pi.sh and scripts/smoke-pi.sh.
_ADAPTER_PIN = "npm:pi-mcp-adapter@2.31.0"

# Packages hal0 manages in pi's settings.json ``packages`` array.
# Dedup'd against operator entries on every install; removed (only
# these entries) on uninstall.
#
# The deployed extensions must NOT be listed here: pi (verified on
# 0.84.4, CT105 acceptance) auto-discovers every directory under
# ``~/.pi/agent/extensions/``, and an explicit ``extensions/<name>``
# packages entry loads the same extension a second time — the duplicate
# ``registerTool`` calls then fail the whole extension with
# "Tool ... conflicts with .../extensions/hindsight/index.ts".
_MANAGED_PACKAGES = (_ADAPTER_PIN,)

# Older installs (and the pre-fix revision of this driver) wrote these
# double-loading entries; strip them on install and uninstall.
_LEGACY_PACKAGE_ENTRIES = ("extensions/hal0-provider", "extensions/hindsight")

# Hindsight's self-hosted server, colocated on the box (spec D4).
_HINDSIGHT_API_URL = "http://127.0.0.1:9177"

# Upstream's own built-in defaults, restored on uninstall iff hal0 was
# the active default (mirrors the pre-hal0 factory settings).
_UPSTREAM_DEFAULT_PROVIDER = "openrouter"
_UPSTREAM_DEFAULT_MODEL = "deepseek/deepseek-v4-pro"

# Marker stamped into the (world-readable) data dir at install time. pi's
# profile lands in the INVOKING user's 0700 home (~root/.pi on a stock
# box), which the hal0-api daemon (User=hal0) can never read — the marker
# is what lets the daemon's status()/list() answer without that access.
_PROFILE_MARKER = "profile.json"


def _api_base() -> str:
    """Honour HAL0_API_URL the same way the CLI does."""
    return os.environ.get("HAL0_API_URL", _HAL0_API_BASE_DEFAULT).rstrip("/")


def _pi_binary_on_path() -> bool:
    """Module-level seam so tests can monkeypatch the PATH probe."""
    return shutil.which("pi") is not None


class PiDriver(AgentDriver):
    """Driver for the pi bundled agent."""

    name = "pi"

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

        # The shell script needs the data dir to exist. We export it +
        # the API URL so the script is tomli-w-free POSIX shell.
        env = os.environ.copy()
        data_dir = self._data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        env["HAL0_AGENT_DATA_DIR"] = str(data_dir)
        env["HAL0_API_URL"] = _api_base()
        if bearer_token:
            # Passed through for any manual/operator invocation of the
            # script that wants it; installer/agents/pi.sh itself no
            # longer consults it — the driver owns all token wiring
            # (see _write_mcp_config / _resolve_service_token below).
            env["HAL0_BEARER_TOKEN"] = bearer_token

        try:
            self._runner.run(  # type: ignore[attr-defined]
                ["bash", str(script)],
                env=env,
                check=True,
            )
        except Exception as exc:  # subprocess.CalledProcessError or others
            raise AgentError(
                f"pi install failed ({type(exc).__name__}: {exc}). "
                "installer/agents/pi.sh pins exact upstream versions (see its "
                "header for the pin policy) — this is not a track-latest "
                "surface, so a failure here usually means the pinned "
                "package itself is broken or unreachable, not a new "
                "upstream release. Check npm registry status / connectivity "
                "before bumping the pin."
            ) from exc

        # Model provider (autodiscovers hal0 slots) + hindsight memory
        # extensions, and the hal0 theme.
        self._deploy_extension(_PROVIDER_EXT_SRC, self._provider_ext_dst())
        self._deploy_extension(_HINDSIGHT_EXT_SRC, self._hindsight_ext_dst())
        self._npm_install_hindsight_ext()
        self._deploy_theme()

        # Default provider/model/theme/packages — a single
        # source-of-truth JSON upsert so we have full control over the
        # shape (the shell script can't easily serialise nested JSON
        # without jq, and jq isn't a hard dep).
        self._write_pi_settings()

        # hal0-memory MCP server (rides the pinned pi-mcp-adapter).
        self._write_mcp_config(bearer_token=bearer_token)

        # Hindsight client config — seeded iff absent (spec D4).
        self._write_hindsight_config()

        # Last: stamp the daemon-readable marker (see _write_profile_marker).
        # Ordering matters — the marker asserts the profile above landed.
        self._write_profile_marker()

    def uninstall(self) -> None:
        # Best-effort: run the uninstall companion installer/agents/pi.sh
        # wrote into the data dir at install time (npm uninstall -g of
        # both packages). Must happen BEFORE the config teardown below —
        # the manager rmtree's the data dir (and this companion script
        # with it) right after this method returns, so this is the only
        # chance to invoke it. A missing/failing companion (e.g. a
        # half-uninstalled agent, or npm gone from PATH) must not block
        # the rest of uninstall — the on-disk config truth still has to
        # come clean.
        with contextlib.suppress(Exception):
            self._runner.run(  # type: ignore[attr-defined]
                ["sh", str(self._data_dir() / "uninstall.sh")],
                check=False,
            )

        # Extensions + theme deployed at install time.
        for dst in (self._provider_ext_dst(), self._hindsight_ext_dst()):
            if dst.exists():
                shutil.rmtree(dst)
        theme_dst = self._theme_dst()
        if theme_dst.exists():
            theme_dst.unlink()

        # Revert pi settings: remove hal0 provider/theme defaults +
        # managed packages, restore upstream's built-in defaults if
        # hal0 was active.
        self._revert_pi_settings()

        # Drop the hal0-memory MCP server, preserving every other
        # server the operator configured.
        self._remove_mcp_config()

        # NOTE: ~/.hindsight/coding-agent.json is intentionally left
        # untouched — it's shared state with other harnesses on the box,
        # not exclusively hal0's to remove.

        # Marker last: its absence is what flips status() to "broken",
        # so it only disappears once the teardown above ran. The manager
        # rmtree's the whole data dir right after, but a driver-only
        # uninstall (tests, partial recovery) must converge too.
        self._profile_marker_path().unlink(missing_ok=True)

    def status(self) -> str:
        """Return ``"installed"`` when the profile marker is present, the
        ``pi`` binary is on PATH, and — where the profiled home is
        readable — the deployed files are all still there.

        The marker indirection exists because status() runs in two
        privilege contexts: the root CLI, and the hal0-api daemon
        (``User=hal0``), whose ``Path.home()`` is /var/lib/hal0 and who
        cannot read the operator's 0700 home. The marker (in the
        world-readable data dir) plus the PATH probe is the daemon-side
        contract; the deep file check only runs when the profiled home is
        actually readable (see :meth:`_write_profile_marker`)."""
        marker = self._profile_marker_path()
        if not marker.exists() or not _pi_binary_on_path():
            return "broken"
        try:
            home = Path(json.loads(marker.read_text(encoding="utf-8"))["home"])
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            return "broken"
        if not os.access(home, os.R_OK | os.X_OK):
            return "installed"  # daemon context — marker + binary is the contract
        agent_dir = home / ".pi" / "agent"
        if (
            (agent_dir / "extensions" / "hal0-provider" / "index.ts").exists()
            and (agent_dir / "extensions" / "hindsight" / "index.ts").exists()
            and (agent_dir / "themes" / "hal0.json").exists()
        ):
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

    def _hindsight_ext_dst(self) -> Path:
        return self._pi_agent_dir() / "extensions" / "hindsight"

    def _theme_dst(self) -> Path:
        return self._pi_agent_dir() / "themes" / "hal0.json"

    def _settings_file(self) -> Path:
        return self._pi_agent_dir() / "settings.json"

    def _mcp_config_path(self) -> Path:
        return self._pi_agent_dir() / "mcp.json"

    def _hindsight_client_config(self) -> Path:
        return Path.home() / ".hindsight" / "coding-agent.json"

    def _provider_extension_deployed(self) -> bool:
        return (self._provider_ext_dst() / "index.ts").exists()

    def _hindsight_extension_deployed(self) -> bool:
        return (self._hindsight_ext_dst() / "index.ts").exists()

    # ── Internals ───────────────────────────────────────────────────────

    def _data_dir(self) -> Path:
        return _paths.var_lib() / "agents" / self.name

    def _profile_marker_path(self) -> Path:
        return self._data_dir() / _PROFILE_MARKER

    def _write_profile_marker(self) -> None:
        """Stamp the data dir with where the profile was written.

        The data dir is world-readable while the profiled home (~root on
        a stock box) is 0700 — this marker is what lets the hal0-api
        daemon (``User=hal0``) answer :meth:`status` without read access
        to the operator's home. Holds no secret: just the home path and
        a timestamp."""
        payload = {
            "version": 1,
            "home": str(Path.home()),
            "profiled_at": datetime.now(UTC).isoformat(),
        }
        marker = self._profile_marker_path()
        marker.parent.mkdir(parents=True, exist_ok=True)
        tmp = marker.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(marker)

    def _npm_install_hindsight_ext(self) -> None:
        """Resolve the pinned @vectorize-io/hindsight-coding-agents dependency
        inside the DEPLOYED extension copy. Deep-imports in index.ts ride the
        package's ./dist/* export map (internal API — hence the exact pin in
        the vendored package.json)."""
        self._runner.run(  # type: ignore[attr-defined]
            ["npm", "install", "--omit=dev"],
            cwd=str(self._hindsight_ext_dst()),
            check=True,
        )

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
        provider/model/theme, plus the managed packages. Preserves all
        other existing settings (operator packages, subagent overrides,
        etc). Creates a minimal file if none exists yet."""
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
        packages = [
            p
            for p in existing.get("packages", [])
            if isinstance(p, str) and p not in _LEGACY_PACKAGE_ENTRIES
        ]
        for managed in _MANAGED_PACKAGES:
            if managed not in packages:
                packages.append(managed)
        existing["packages"] = packages

        settings_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = settings_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        tmp.replace(settings_file)

    def _revert_pi_settings(self) -> None:
        """Remove hal0-specific defaults + managed packages from pi
        settings.json, restoring upstream's built-in defaults. Only
        touches provider/model if hal0 is the current default (don't
        clobber an operator's own later override)."""
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
        packages = settings.get("packages")
        if isinstance(packages, list):
            settings["packages"] = [
                p
                for p in packages
                if p not in _MANAGED_PACKAGES and p not in _LEGACY_PACKAGE_ENTRIES
            ]

        tmp = settings_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
        tmp.replace(settings_file)

    def _resolve_service_token(self) -> str | None:
        """Best-effort self-resolve the box service key when the caller
        (``POST /api/agents/install``) didn't pass one — the production
        path currently never does (src/hal0/api/routes/agents.py). Same
        helper Hermes provisioning uses to wire its own MCP servers
        (``hermes_provision.py`` around ``_probe_mcp_server`` / the
        overlay-build ``bearer = service_key(prefer="admin")`` call).
        Tolerates absence — dev boxes with auth disabled, a missing key
        file — by returning ``None``; the caller then writes the MCP
        entry without an Authorization header rather than failing the
        install."""
        try:
            from hal0.service_identity import service_key

            return service_key(prefer="admin")
        except Exception:
            return None

    def _write_mcp_config(self, *, bearer_token: str | None) -> None:
        """Upsert the hal0-memory MCP server into ~/.pi/agent/mcp.json
        (pi-mcp-adapter's Pi-global override file). Preserves every other
        server the operator configured.

        Identity always rides the ``X-hal0-Agent`` header (mirrors
        ``hermes_provision.py``'s MCP wiring and the ``_AGENT_HEADER`` the
        memory mount reads — src/hal0/api/mcp_mount.py:44-46), independent
        of whether a bearer token is available. When ``bearer_token`` is
        falsy, best-effort self-resolve the box service token via
        :meth:`_resolve_service_token` before giving up on
        Authorization.

        The file may hold a bearer token, so the tmp file is created
        with mode 0600 before the atomic rename — never world-readable,
        even momentarily.
        """
        path = self._mcp_config_path()
        existing: dict[str, Any] = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = {}
        servers = existing.setdefault("mcpServers", {})
        token = bearer_token or self._resolve_service_token()
        headers: dict[str, str] = {"X-hal0-Agent": self.name}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        entry: dict[str, Any] = {"url": f"{_api_base()}{_MCP_MEMORY_PATH}", "headers": headers}
        servers["hal0-memory"] = entry
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, (json.dumps(existing, indent=2) + "\n").encode("utf-8"))
        finally:
            os.close(fd)
        tmp.replace(path)

    def _remove_mcp_config(self) -> None:
        """Drop the hal0-memory entry from ~/.pi/agent/mcp.json,
        preserving every other server. Tolerates a missing or corrupt
        file (nothing to do)."""
        path = self._mcp_config_path()
        if not path.exists():
            return
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(existing, dict):
            return
        servers = existing.get("mcpServers")
        if isinstance(servers, dict):
            servers.pop("hal0-memory", None)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)

    def _write_hindsight_config(self) -> None:
        """Seed ~/.hindsight/coding-agent.json iff absent — an existing
        file is operator/agent-shared state (other harnesses read it
        too) and is never overwritten. Banks: coding-agent::{gitProject};
        the box repo checkout maps into the shared hal0-mono bank (spec
        bank guardrail)."""
        path = self._hindsight_client_config()
        if path.exists():
            return
        payload = {
            "serverMode": "self-hosted",
            "apiUrl": _HINDSIGHT_API_URL,
            "mapPathToBank": {"/home/halo/dev/hal0": "coding-agent::hal0-mono"},
            "retainTags": ["project:{gitProject}"],
            "retainMetadata": {"repo": "{gitProject}"},
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
