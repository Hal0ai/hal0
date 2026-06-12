"""``hal0-agent`` CLI shim — invoked by ``hal0-agent@<id>.service``.

This is a deliberately tiny entry point that:

1. Resolves the agent type from ``/etc/hal0/agents/<id>.toml`` (or a
   hardcoded fallback when the id is a known builtin like ``hermes``).
2. Translates ``serve`` / ``stop`` / ``status`` / ``reprovision`` into
   the right upstream invocation for that agent type.
3. Emits ``sd_notify`` (``READY=1`` / ``WATCHDOG=1`` / ``STOPPING=1``)
   so the systemd template unit's ``Type=notify`` + ``WatchdogSec=``
   work without pulling ``systemd-python`` into hal0's wheel.

**Why this shim exists vs. running ``hermes`` directly from the unit:**

* Hermes upstream's ``hermes dashboard`` invocation needs hal0-specific
  env (HAL0_AGENT_ID, HERMES_HOME, dist path) plus a "wait for the
  /api/events socket to come up THEN sd_notify READY" handshake. The
  unit can't express that.
* The agent-id parameterisation (``%i`` in the template) needs to drive
  the resolution of which upstream binary to run. ``hermes`` today,
  ``pi-coder`` for v0.4. Keeping that map in the shim lets us add new
  agent types without editing the template unit.
* ``mcp serve`` mode in Hermes upstream is a query-only MCP server
  with NO event stream — picking it kills the chat surface on day 1
  (see DA-sec-ops MUST-FIX #1 in the v0.3 research bundle, and the
  ExecStart-mode comment in ``hal0-agent@.service``).

The shim is intentionally pure-stdlib + ``subprocess`` so it stays
robust against hal0 wheel-build/import drift. It does NOT import
``hal0.api``, ``fastapi``, or anything that could fail to load when
``hal0-agent@hermes.service`` starts before ``hal0-api.service``.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

# ----------------------------------------------------------------------------
# Configuration discovery
# ----------------------------------------------------------------------------

# Per-agent TOML — overrides the builtin fallback. Optional.
# Schema (all keys optional except ``type``):
#
#     type   = "hermes"                    # required; selects invocation
#     home   = "/var/lib/hal0/agents/<id>" # default: derived from id
#     venv   = "/var/lib/hal0/venvs/<id>"  # default: derived from id
#     port   = 9119                        # default: hermes upstream's 9119
#     host   = "127.0.0.1"                 # default: loopback only
_AGENTS_CONF_DIR = Path(os.environ.get("HAL0_AGENTS_CONF_DIR", "/etc/hal0/agents"))

# Builtin fallback for known agent ids — keyed off the id, NOT the type.
# Lets ``hal0-agent@hermes.service`` work even before ``/etc/hal0/agents/
# hermes.toml`` is dropped (first-boot ordering: bootstrap installs the
# unit + enables it, then ``hermes_provision`` may write the toml).
_BUILTIN_AGENT_TYPES: dict[str, str] = {
    "hermes": "hermes",
}


@dataclass(frozen=True)
class AgentConfig:
    """Resolved agent invocation parameters."""

    agent_id: str
    agent_type: str
    home: Path
    venv: Path
    host: str
    port: int

    @property
    def hermes_bin(self) -> Path:
        """Path to the ``hermes`` console script inside the agent's venv."""

        return self.venv / "bin" / "hermes"

    @property
    def status_url(self) -> str:
        """URL the shim polls to confirm the agent's HTTP surface is up."""

        # Use the UNAUTHENTICATED ``/health`` liveness route. The Hermes
        # dashboard auth-gates every ``/api/*`` endpoint (it exposes API
        # keys), so ``/api/health`` returns 401 — which the old poll treated
        # as "not ready", so the Type=notify start never got READY=1 and the
        # service crash-looped on the 120s start timeout. ``/health`` (and
        # ``/healthz``) return 200 without auth — verified live (hermes 0.14.0).
        return f"http://{self.host}:{self.port}/health"


def _load_agent_config(agent_id: str) -> AgentConfig:
    """Resolve ``agent_id`` to a fully-populated :class:`AgentConfig`.

    Reads ``/etc/hal0/agents/<id>.toml`` if it exists, then falls back
    to the builtin map. Raises :class:`SystemExit` (via :func:`die`) if
    the agent id isn't recognised and no TOML exists.
    """

    conf_path = _AGENTS_CONF_DIR / f"{agent_id}.toml"
    data: dict[str, object] = {}
    if conf_path.exists():
        try:
            data = tomllib.loads(conf_path.read_text())
        except (OSError, tomllib.TOMLDecodeError) as exc:
            _die(f"failed to parse {conf_path}: {exc}")

    agent_type = str(data.get("type") or _BUILTIN_AGENT_TYPES.get(agent_id) or "")
    if not agent_type:
        _die(
            f"unknown agent id '{agent_id}' — drop /etc/hal0/agents/{agent_id}.toml "
            f"with a 'type = ...' field, or use a builtin id "
            f"({', '.join(sorted(_BUILTIN_AGENT_TYPES))})"
        )

    home = Path(str(data.get("home") or f"/var/lib/hal0/.{agent_id}"))
    venv = Path(str(data.get("venv") or f"/var/lib/hal0/venvs/{agent_id}"))
    host = str(data.get("host") or "127.0.0.1")
    # tomllib parses integers as int already; coerce via str→int to satisfy
    # mypy's strict object→int handling on the dict.get fallback path.
    port_raw = data.get("port") or 9119
    port = int(str(port_raw))

    return AgentConfig(
        agent_id=agent_id,
        agent_type=agent_type,
        home=home,
        venv=venv,
        host=host,
        port=port,
    )


# ----------------------------------------------------------------------------
# sd_notify (pure-stdlib — no systemd-python dependency)
# ----------------------------------------------------------------------------


def _sd_notify(state: str) -> bool:
    """Send a single sd_notify state line to ``$NOTIFY_SOCKET``.

    Returns ``True`` on success, ``False`` when the env var is unset
    (e.g. running the shim outside systemd). Failure to write is
    swallowed — the shim never crashes for a notify miss.
    """

    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False
    # Abstract-namespace sockets start with '@' which datagram socket
    # APIs encode as a leading NUL byte. Translate per sd_notify(3).
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC)
    except OSError:
        return False
    try:
        sock.sendto(state.encode("utf-8"), addr)
    except OSError:
        return False
    finally:
        sock.close()
    return True


# ----------------------------------------------------------------------------
# HTTP readiness probe (also pure-stdlib)
# ----------------------------------------------------------------------------


def _is_ready(cfg: AgentConfig, *, timeout: float = 1.0) -> bool:
    """True iff the agent's HTTP surface is up.

    A 2xx from the unauthenticated health route is the happy path. We also
    treat an auth challenge (401/403) as "up": an HTTP error response proves
    the socket is open and serving, so the shim should sd_notify READY rather
    than time out if a future hermes release auth-gates the health route too.
    """

    try:
        with urllib.request.urlopen(cfg.status_url, timeout=timeout) as resp:
            status: int = resp.status
            return 200 <= status < 300
    except urllib.error.HTTPError as exc:
        # Server responded (gated/errored) → it IS reachable.
        return exc.code in (401, 403)
    except (urllib.error.URLError, OSError):
        return False


# ----------------------------------------------------------------------------
# Hermes invocation
# ----------------------------------------------------------------------------


def _build_hermes_argv(cfg: AgentConfig) -> list[str]:
    """Build the argv that boots Hermes with ``/api/events`` + ``/api/pty``.

    The chosen subcommand is ``hermes dashboard --tui`` per ``hermes_cli/
    main.py:14050-14102`` and ``hermes_cli/main.py:10930-10939``:

    * ``cmd_dashboard`` is the ONLY subcommand that imports + calls
      ``hermes_cli.web_server.start_server``, which is where the
      ``/api/pty``, ``/api/events``, and ``/api/ws`` routes are mounted
      (``hermes_cli/web_server.py:3585`` ``/api/pty``,
      ``hermes_cli/web_server.py:3763`` ``/api/events``,
      ``hermes_cli/web_server.py:3704`` ``/api/ws``).
    * ``--tui`` (alias env: ``HERMES_DASHBOARD_TUI=1``) flips on the
      embedded chat PTY — without it the dashboard runs but the chat
      tab is hidden and ``/api/pty`` refuses upgrades. hal0's chat
      surface needs both.
    * ``--skip-build`` keeps the unit start fast and works without npm
      on the production box — the wheel ships ``hermes_cli/web_dist/``
      pre-built.
    * ``--no-open`` because we're running headless (no browser open).
    * ``--host 127.0.0.1`` — hal0-api proxies the WS; binding to all
      interfaces would let any LAN host reach the agent's PTY without
      the hal0-api Origin/HMAC checks (DA-sec-ops #2).
    * Explicitly **NOT** ``hermes mcp serve``: that subcommand boots an
      MCP query server with NO ``message.delta`` / ``tool.start/complete``
      / ``approval.request`` event stream, so PR-9/PR-10's chat surface
      would have nothing to render (DA-sec-ops MUST-FIX #1).
    """

    return [
        str(cfg.hermes_bin),
        "dashboard",
        "--host",
        cfg.host,
        "--port",
        str(cfg.port),
        "--tui",
        "--no-open",
        "--skip-build",
    ]


def _build_hermes_env(cfg: AgentConfig) -> dict[str, str]:
    """Build the child env for Hermes — inherits parent + adds hal0 keys."""

    env = dict(os.environ)
    env["HAL0_AGENT_ID"] = cfg.agent_id
    env["HERMES_HOME"] = str(cfg.home)
    # Mirror ``--tui`` so any nested hermes-cli invocation (e.g. an
    # in-process subprocess.run) sees the same flag without parsing argv.
    env["HERMES_DASHBOARD_TUI"] = "1"
    # Drop NOTIFY_SOCKET from the child — the shim owns sd_notify,
    # and a misbehaving Hermes plugin shouldn't be able to send
    # READY=1 / STOPPING=1 to systemd on our behalf.
    env.pop("NOTIFY_SOCKET", None)
    return env


# ----------------------------------------------------------------------------
# Subcommands
# ----------------------------------------------------------------------------

# How long to wait for the child to become reachable before bailing.
# Longer than the default 60s WatchdogSec on first boot (model
# warm-up + provider plugin discovery can dominate), but capped so a
# truly-wedged hermes doesn't keep the unit in ``activating`` forever.
_READY_TIMEOUT_S = 90.0
# Watchdog ping interval — half of the unit's WatchdogSec (60s) so a
# single miss doesn't trip the watchdog.
_WATCHDOG_INTERVAL_S = 25.0
# SIGTERM → SIGKILL escalation window.
_STOP_GRACE_S = 10.0


def cmd_serve(cfg: AgentConfig) -> int:
    """Launch the agent process, then sd_notify READY once it's reachable.

    Returns the agent process's exit code. Watchdog pings continue for
    as long as the child is alive AND the health URL responds — a hung
    agent stops getting pinged and systemd restarts it per the unit's
    ``Restart=on-failure`` policy.
    """

    if cfg.agent_type != "hermes":
        _die(f"agent type '{cfg.agent_type}' not supported by this shim yet")
    if not cfg.hermes_bin.exists():
        _die(
            f"hermes binary not found at {cfg.hermes_bin} — run 'hal0 agent bootstrap hermes' first"
        )

    argv = _build_hermes_argv(cfg)
    env = _build_hermes_env(cfg)

    # Don't ``exec`` — we need a parent process to drive sd_notify
    # READY/WATCHDOG. Forward signals into the child via the SIGTERM
    # handler below.
    child = subprocess.Popen(
        argv,
        env=env,
        cwd=str(cfg.home if cfg.home.exists() else Path.cwd()),
        stdin=subprocess.DEVNULL,
    )

    def _forward_signal(signum: int, _frame: object) -> None:
        with contextlib.suppress(ProcessLookupError):
            child.send_signal(signum)

    signal.signal(signal.SIGTERM, _forward_signal)
    signal.signal(signal.SIGINT, _forward_signal)
    # SIGHUP = persona swap / config reload — Hermes upstream re-reads
    # overrides.yaml on SIGHUP. Pass through unchanged.
    signal.signal(signal.SIGHUP, _forward_signal)

    # Block until /api/health responds OR the child exits OR we time out.
    ready = _wait_for_ready(child, cfg, _READY_TIMEOUT_S)
    if not ready:
        # Child either crashed or never opened the socket. Don't sd_notify
        # READY — systemd will mark us failed and trigger Restart.
        rc = child.wait() if child.poll() is None else (child.returncode or 1)
        return rc or 1

    _sd_notify("READY=1\nSTATUS=hermes dashboard reachable\n")

    # Heartbeat loop. ``WATCHDOG=1`` while the child is alive AND
    # reachable; on either failure we let the loop exit and systemd
    # observe via WatchdogSec.
    while child.poll() is None:
        if _is_ready(cfg):
            _sd_notify("WATCHDOG=1\n")
        time.sleep(_WATCHDOG_INTERVAL_S)

    _sd_notify("STOPPING=1\n")
    return child.returncode or 0


def _wait_for_ready(
    child: subprocess.Popen[bytes],
    cfg: AgentConfig,
    timeout: float,
) -> bool:
    """Poll the agent's health URL until 2xx, child-exit, or timeout."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if child.poll() is not None:
            return False
        if _is_ready(cfg):
            return True
        time.sleep(0.5)
    return False


def cmd_stop(cfg: AgentConfig) -> int:
    """SIGTERM any running ``hermes dashboard`` PTY for this agent id.

    Invoked by ``ExecStop=`` in the template unit AND usable standalone
    (``hal0-agent hermes stop``). The shim doesn't track PIDs — it
    scans for the dashboard cmdline + the matching ``HAL0_AGENT_ID``
    env, mirroring what ``hermes dashboard --stop`` does upstream.

    For Type=notify units, systemd already SIGTERMs the main pid on
    its own; this command is the manual fallback + the basis for
    ``status``.
    """

    needle = str(cfg.hermes_bin)
    pids = _find_child_pids(needle, cfg.agent_id)
    if not pids:
        # Already stopped — exit 0 so retries are idempotent.
        return 0

    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue

    # Give the child up to _STOP_GRACE_S to exit cleanly, then SIGKILL.
    deadline = time.monotonic() + _STOP_GRACE_S
    while time.monotonic() < deadline:
        pids = _find_child_pids(needle, cfg.agent_id)
        if not pids:
            return 0
        time.sleep(0.25)

    # Escalate.
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            continue
    return 0


def _find_child_pids(needle: str, agent_id: str) -> list[int]:
    """Scan /proc for processes matching ``needle`` AND ``agent_id``.

    Pure-stdlib so we don't drag psutil into the shim's blast radius.
    Returns an empty list when running on a non-Linux host (no /proc).
    """

    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return []

    matches: list[int] = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (
                (entry / "cmdline")
                .read_bytes()
                .replace(b"\0", b" ")
                .decode("utf-8", errors="replace")
            )
        except OSError:
            continue
        if needle not in cmdline:
            continue
        try:
            environ = (entry / "environ").read_bytes()
        except OSError:
            continue
        if f"HAL0_AGENT_ID={agent_id}\0".encode() not in environ + b"\0":
            continue
        matches.append(int(entry.name))
    return matches


def cmd_status(cfg: AgentConfig) -> int:
    """Print short status — exit 0 iff health URL responds."""

    if _is_ready(cfg):
        print(f"{cfg.agent_id}: reachable at {cfg.status_url}")
        return 0
    print(
        f"{cfg.agent_id}: NOT reachable at {cfg.status_url}",
        file=sys.stderr,
    )
    return 1


def cmd_reprovision(cfg: AgentConfig) -> int:
    """Re-run the hal0 bootstrap pipeline for this agent.

    Today this delegates to ``hal0 agent bootstrap <type> --repair``
    (the only re-entry point in the current CLI). When PR-3 lands an
    explicit ``reprovision`` subcommand, swap the argv to point at it.
    """

    hal0_bin = shutil.which("hal0") or "/usr/local/bin/hal0"
    argv = [hal0_bin, "agent", "bootstrap", cfg.agent_type, "--repair"]
    return subprocess.call(argv)


def _render_live_context(*, hermes_home: Path) -> dict[str, object]:
    """Indirection so tests can patch; lazy import keeps shim startup light."""
    from hal0.agents.hermes_provision import render_live_context

    return render_live_context(hermes_home=hermes_home)


def cmd_render_context(cfg: AgentConfig) -> int:
    """Re-probe live hal0 state and (re)write STATE.md + HERMES.md.

    Wired as ``ExecStartPre`` on hal0-agent@hermes.service (non-fatal) and
    spawned detached after a model/slot change. Render is best-effort:
    a daemon-unreachable read leaves last-good files and still exits 0 so
    it never blocks the service from starting.
    """
    if cfg.agent_type != "hermes":
        _die(f"agent type '{cfg.agent_type}' not supported by this shim yet")
    try:
        result = _render_live_context(hermes_home=cfg.home)
    except Exception as exc:  # never block service start
        print(f"hal0-agent: render-context failed (non-fatal): {exc}", file=sys.stderr)
        return 0
    state = "degraded" if result.get("degraded") else "ok"
    print(
        f"hal0-agent: render-context {state} "
        f"(state_written={result.get('state_written')}, "
        f"hermes_written={result.get('hermes_written')})"
    )
    return 0


# ----------------------------------------------------------------------------
# Argv parsing
# ----------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hal0-agent",
        description=(
            "Wrap a bundled hal0 agent process with sd_notify, watchdog, "
            "and graceful stop. Invoked by hal0-agent@<id>.service."
        ),
    )
    parser.add_argument(
        "agent_id",
        help="Agent instance id (e.g. 'hermes'). Matches %%i in the systemd unit.",
    )
    parser.add_argument(
        "subcommand",
        choices=["serve", "stop", "status", "reprovision", "render-context"],
        help="What to do with the agent.",
    )
    return parser


def _die(msg: str, *, exit_code: int = 1) -> None:
    """Print ``msg`` to stderr and exit non-zero. Never returns."""

    print(f"hal0-agent: {msg}", file=sys.stderr)
    raise SystemExit(exit_code)


_DISPATCH: dict[str, Callable[[AgentConfig], int]] = {
    "serve": cmd_serve,
    "stop": cmd_stop,
    "status": cmd_status,
    "reprovision": cmd_reprovision,
    "render-context": cmd_render_context,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    cfg = _load_agent_config(args.agent_id)
    return _DISPATCH[args.subcommand](cfg)


if __name__ == "__main__":  # pragma: no cover - exercised via entrypoint
    raise SystemExit(main())
