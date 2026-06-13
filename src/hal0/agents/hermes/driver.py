"""Hermes-Agent driver (ADR-0004 §6).

Hermes is user-owned upstream — and crucially, the user cannot PR
upstream NousResearch/hermes-agent. So hal0-awareness lives on the
hal0 side, in a hal0-owned wrapper script (``hal0-hermes``) that
sources ``/etc/hal0/agents/hermes.env`` and ``exec``s the upstream
``hermes`` binary.

This driver's responsibilities:

1. Probe whether Hermes is provisioned in the hal0-managed venv
   (``_probe_hermes_provisioned``) and whether the ``hal0-hermes``
   wrapper is installed and functional (``--hal0-ready`` returns 0).
2. On the API/dashboard install path, register an already-provisioned
   agent by writing the canonical env file at
   ``/etc/hal0/agents/hermes.env`` that the wrapper sources on every
   hermes invocation. Provisioning itself (venv create + pip install
   hermes-agent + ``/usr/local/bin/hermes`` shim) lives in the
   bootstrap pipeline (:mod:`hal0.agents.hermes_provision`), run in
   the foreground by ``hal0 agent install hermes``.

The pre-install gate shifted (see :func:`_probe_hermes_provisioned`):
the old gate ran ``shutil.which("hermes")`` against the daemon's
minimal PATH, which a ``pipx``/``pip --user`` install can never
satisfy — so the recommended remedy looped forever. The gate now
keys off the concrete managed-venv artifacts instead.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess  # nosec B404 — required for shim
from pathlib import Path
from typing import Any

from hal0.agents.manager import (
    AgentDriver,
    HermesUpstreamMissingError,
)
from hal0.config import paths as _paths

# NOTE: provisioning (venv create + pip install hermes-agent + wrapper
# shim) moved wholesale into the bootstrap pipeline
# (``hal0.agents.hermes_provision``), driven by the foreground CLI
# ``hal0 agent install hermes`` (which first ensures the OS toolchain
# via ``installer/agents/hermes-prereqs.sh``). This driver no longer
# shells out to a per-agent installer script; on the API path it only
# registers an already-provisioned agent (writes the env file).


# The wrapper binary name. Installed by the bootstrap pipeline's
# install phase to /usr/local/bin. Sourcing the env file + exec'ing
# the venv ``hermes`` is the whole job.
_WRAPPER_BIN_NAME = "hal0-hermes"
_WRAPPER_READY_FLAG = "--hal0-ready"

# Managed-venv layout — keep in sync with
# ``hal0.agents.hermes_provision`` (BootstrapState.venv default +
# HERMES_CLI_INSTALL_PATH). Mirrored here as plain constants so the
# probe stays cheap and doesn't import the heavy provisioning module
# at driver-import time.
_MANAGED_VENV = Path("/var/lib/hal0/venvs/hermes")
_MANAGED_VENV_HERMES = _MANAGED_VENV / "bin" / "hermes"
_HERMES_CLI_SHIM = Path("/usr/local/bin/hermes")


def _probe_hermes_provisioned() -> bool:
    """Return True iff Hermes is provisioned in the hal0-managed venv.

    This is the **pre-register** gate for the API/dashboard install
    path. Upstream Hermes no longer lives wherever the operator's
    interactive ``pipx``/``pip`` happened to drop it (a location a
    systemd daemon's minimal PATH can't see, and the ``hal0`` agent
    user can't read under 0700 ``/root``). Instead ``hal0 agent
    install hermes`` provisions it into ``/var/lib/hal0/venvs/hermes``
    (world-traversable, runnable by the ``hal0`` user) with a thin
    ``/usr/local/bin/hermes`` shim on the system PATH.

    We check the concrete artifacts the bootstrap install phase
    writes — the venv interpreter's ``hermes`` entry point and the
    canonical CLI shim — rather than ``shutil.which`` against the
    daemon's incidental PATH (the bug that made the old gate
    unsatisfiable for a pipx install).
    """
    return _MANAGED_VENV_HERMES.exists() or _HERMES_CLI_SHIM.exists()


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


def _probe_systemd_unit_active(unit: str) -> bool:
    """Return True iff ``systemctl is-active <unit>`` exits 0.

    Non-blocking: uses a 2-second subprocess timeout. Returns False on any
    error (no systemctl on PATH, timeout, permission denied) so callers
    fall through to the next probe rather than raising.
    """
    if shutil.which("systemctl") is None:
        return False
    try:
        result = subprocess.run(  # nosec B603 — known-safe argv
            ["systemctl", "is-active", unit],
            check=False,
            capture_output=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _probe_tcp_port(host: str, port: int, *, timeout: float = 1.0) -> bool:
    """Return True iff a TCP connection to ``host:port`` succeeds within ``timeout`` seconds."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class HermesDriver(AgentDriver):
    """Driver for the Hermes-Agent bundled agent."""

    name = "hermes"

    def __init__(self, *, runner: object | None = None, prober: object | None = None) -> None:
        # ``runner`` parallels :class:`PiCoderDriver` — tests inject a
        # fake subprocess. ``prober`` lets tests force the upstream
        # pre-install gate without needing a real ``hermes`` on PATH.
        self._runner = runner if runner is not None else subprocess
        self._prober = prober if prober is not None else _probe_hermes_provisioned

    # ── AgentDriver protocol ────────────────────────────────────────────

    def install(self, *, bearer_token: str | None = None) -> None:
        # API/dashboard install is the THIN path: it registers an
        # already-provisioned Hermes by writing the env file the
        # wrapper sources. It does NOT provision — creating the venv +
        # pip-installing hermes-agent is a multi-minute job that can't
        # run inside a single HTTP request, so provisioning lives in
        # the foreground CLI (`hal0 agent install hermes`, which runs
        # the bootstrap pipeline). If the managed venv isn't there yet,
        # point the operator at the CLI rather than looping forever on
        # a pipx hint the daemon's minimal PATH can never satisfy.
        if not self._prober():
            raise HermesUpstreamMissingError(
                "Hermes is not provisioned — the managed venv at "
                f"{_MANAGED_VENV} does not exist. Run `hal0 agent install "
                "hermes` on the host: it installs the python/venv/pipx "
                "toolchain, creates the venv, and provisions Hermes into "
                "it. (The hal0 daemon can't run the multi-minute "
                "provisioning over HTTP, so the dashboard/API install only "
                "registers an already-provisioned agent.)"
            )

        data_dir = self._data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)

        # Write the env file the wrapper sources on every hermes
        # invocation. Single source of truth for the API URL + Bearer
        # the agent uses to reach hal0.
        self._write_env_file(bearer_token=bearer_token)

    def uninstall(self) -> None:
        # Order matters (#348 + #349): we MUST read provision.json
        # BEFORE the manager strips the state dir. The manager calls
        # ``driver.uninstall()`` first specifically so the driver can
        # consume bookkeeping that is about to be deleted.
        #
        # Two driver-specific artifacts live OUTSIDE the manager's
        # seed + data + state triad and so will leak if we don't
        # clean them here:
        #
        #   * the dedicated Python venv at ``/var/lib/hal0/venvs/<name>/``
        #     (built by ``hermes_provision._install_venv``) — full
        #     interpreter + site-packages, ~hundreds of MiB.
        #   * the operator-facing docs the ``context_link`` phase
        #     renders into ``/etc/hal0/`` (HERMES.md + AGENTS.md).
        #
        # Both gaps are recorded in ``provision.json`` so we don't
        # hardcode paths — if a future phase adds another file the
        # inverse stays correct as long as it stamps a ``"path"``
        # entry. Each removal is idempotent: a missing path is a
        # no-op, never an error, so a re-run / partial-uninstall is
        # safe.
        provision = self._load_provision()
        if provision is not None:
            self._remove_context_link_outputs(provision)
            self._remove_venv(provision)

        env_file = self._env_file_path()
        if env_file.exists():
            env_file.unlink()

    def status(self) -> str:
        """Return ``"installed"`` when Hermes is running/reachable, ``"broken"`` otherwise.

        Priority order (cheapest first):

        1. ``systemctl is-active hal0-agent@hermes.service`` — a single
           dbus round-trip, ~0 ms when the unit is active.
        2. TCP connect to ``127.0.0.1:9119`` (1 s timeout) — catches the
           case where the agent runs outside systemd (e.g. dev mode).
        3. Env-file presence — the "installed but not yet started" case.

        Only returns ``"broken"`` when all three signals say the agent is not
        reachable *and* the env file (our install-time artefact) is absent.
        This avoids the false-negative where the unit is active/running but
        ``/etc/hal0/agents/hermes.env`` was never written (e.g. the wrapper
        installed without going through :meth:`install`).
        """
        # 1. systemctl probe — fast, no network.
        if _probe_systemd_unit_active("hal0-agent@hermes.service"):
            return "installed"

        # 2. Socket probe — catches out-of-systemd invocations.
        if _probe_tcp_port("127.0.0.1", 9119, timeout=1.0):
            return "installed"

        # 3. Env-file fallback — installed but service hasn't started yet.
        if self._env_file_path().exists():
            return "installed"

        return "broken"

    # ── Internals ───────────────────────────────────────────────────────

    def _data_dir(self) -> Path:
        return _paths.var_lib() / "agents" / self.name

    def _env_file_path(self) -> Path:
        # ADR-0004 §6: "writes /etc/hal0/agents/hermes.env". Lives in
        # /etc so the user/admin can tweak it without disturbing
        # /var/lib state, same posture as openwebui.env. The wrapper
        # sources this file on every hermes invocation.
        return _paths.etc() / "agents" / "hermes.env"

    def _provision_state_path(self) -> Path:
        """Return the path to ``provision.json`` for this agent.

        Mirrors :meth:`AgentManager._state_dir` so test harnesses that
        route ``$HAL0_HOME`` through ``_paths.var_lib()`` see the same
        state file the manager would clean up. Production
        ``hermes_provision`` hardcodes ``/var/lib/hal0/state/agents/hermes``
        (see ``_DEFAULT_STATE_ROOT``); under HAL0_HOME both resolutions
        agree because ``_paths.var_lib()`` returns ``/var/lib/hal0``
        when HAL0_HOME is unset.
        """
        return _paths.var_lib() / "state" / "agents" / self.name / "provision.json"

    def _load_provision(self) -> dict[str, Any] | None:
        """Best-effort load of the bootstrap checkpoint.

        Returns ``None`` on any failure (missing file, unreadable JSON,
        unexpected shape) — the driver's uninstall remains a no-op
        rather than failing the whole teardown over bookkeeping we
        can't parse. The manager will still strip the state dir after
        we return, so a half-loaded provision.json doesn't strand
        artifacts indefinitely.
        """
        path = self._provision_state_path()
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        return data

    def _remove_context_link_outputs(self, provision: dict[str, Any]) -> None:
        """Unlink every file the ``context_link`` phase rendered (#349).

        Reads ``phases.context_link.details.rendered.<name>.path`` —
        currently HERMES.md + AGENTS.md (written to /etc/hal0/) plus
        SOUL.md (written under HERMES_HOME, which the manager will
        rmtree anyway, but removing it here first is harmless and
        keeps the inverse symmetrical to the install).

        Idempotent: a missing path or a non-file (e.g. directory)
        produces no error, just a skip. Symlinks created in
        ``details.links`` aren't tracked here — those all live under
        HERMES_HOME (data_dir) and get cleaned by the manager's
        ``shutil.rmtree``.
        """
        phases = provision.get("phases")
        if not isinstance(phases, dict):
            return
        context_link = phases.get("context_link")
        if not isinstance(context_link, dict):
            return
        details = context_link.get("details")
        if not isinstance(details, dict):
            return
        rendered = details.get("rendered")
        if not isinstance(rendered, dict):
            return
        for entry in rendered.values():
            if not isinstance(entry, dict):
                continue
            raw_path = entry.get("path")
            if not isinstance(raw_path, str) or not raw_path:
                continue
            target = Path(raw_path)
            try:
                # ``missing_ok=True`` covers the "already gone" branch
                # without a separate exists() check. OSError catches
                # the "exists but is a directory" branch (unlink would
                # raise IsADirectoryError) — we skip rather than
                # rmtree because the manager owns directory teardown
                # and we don't want this driver hook to delete a tree
                # an operator hand-placed at a recorded path.
                target.unlink(missing_ok=True)
            except OSError:
                continue

    def _remove_venv(self, provision: dict[str, Any]) -> None:
        """Remove the per-agent Python venv recorded in provision.json (#348).

        Reads top-level ``venv`` from the checkpoint — that field is
        the canonical record of where ``hermes_provision._install_venv``
        actually built the venv on this host, including any operator
        override. Production default is
        ``/var/lib/hal0/venvs/<name>/``.

        Idempotent: a missing venv directory is a no-op. We require
        the recorded path to be a directory before recursing — a
        symlink-to-elsewhere or a stale file at the recorded location
        is left alone (the operator can investigate; we don't want
        an aggressive rmtree following an unexpected target).
        """
        raw_venv = provision.get("venv")
        if not isinstance(raw_venv, str) or not raw_venv:
            return
        venv = Path(raw_venv)
        # Resolve through symlinks via ``is_dir()`` which follows
        # them; if the recorded path is a symlink-to-dir we still
        # want to rmtree the target tree (it's our venv, just a
        # weird mount layout). ``shutil.rmtree`` against a non-dir
        # raises; the explicit guard turns that into the no-op
        # contract the issue requires.
        if not venv.is_dir():
            return
        try:
            shutil.rmtree(venv)
        except OSError:
            # Best-effort. A permissions failure or a concurrent
            # writer won't abort the rest of the uninstall — the
            # manager's seed + data + state cleanup still runs.
            return

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
