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

import json
import os
import shutil
import subprocess  # nosec B404 — required for shim
from pathlib import Path
from typing import Any

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
    # parents[0]=hermes, [1]=agents, [2]=hal0, [3]=src, [4]=repo root.
    # Bumped by one when driver.py moved from src/hal0/agents/hermes.py
    # to src/hal0/agents/hermes/driver.py to make room for the vendored
    # plugin tree (memory_cognee etc.).
    repo_root = Path(__file__).resolve().parents[4]
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
