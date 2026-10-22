"""Hermes command approvals: the operator setting behind Hermes's ``approvals.mode``.

Hermes Agent (0.19.0+, re-verified at the pinned v0.22.4 ``4d043038``) gates
a dangerous terminal command or an ``execute_code`` script behind an
approval. ``approvals.mode`` decides who answers first. Upstream accepts
``manual``, ``smart`` and ``off`` (``hermes_cli/approval_mode.py:16``); hal0
offers the first two, and ``off`` is reachable only through overrides.yaml:

* ``smart``  — an auxiliary model call answers APPROVE / DENY / ESCALATE.
  On hal0 that call resolves to the main provider (the hal0 gateway), so the
  judge is a local slot model. Anything but APPROVE, and any failure of the
  call, falls back to asking a human.
* ``manual`` — a human approves every flagged command.

This is NOT hal0's MCP tool approval queue (``/api/agent/approvals``,
``hal0 agent approvals``), which gates hal0 MCP tool calls an agent makes.
The two layers are independent.

Where the setting lives
-----------------------
``[hermes].command_approvals`` in ``/etc/hal0/agents/hermes.toml`` (the
per-agent seed TOML). That directory is root:root (``install/perms.py``) and
the agent unit runs as ``hal0`` with ``NoNewPrivileges=yes``, so the agent
cannot write the file on disk or reach the ``hal0-agentenv`` sudo seam that
hal0-api uses to write it. Same reasoning as the terminal-tool marker
(:data:`hal0.agents.hermes_provision.TERMINAL_STATE_PATH`): agent-controlled
state must never present itself to a later provision as the operator's
decision. ``hal0.toml`` was rejected as the home because the agent unit can
write it (``ReadWritePaths=/etc/hal0``) and the generic ``config_write`` MCP
tool reaches it.

Precedence
----------
``/etc/hal0/agents/hermes/overrides.yaml`` deep-merges last at provisioning
(:func:`hal0.agents.hermes_provision._merge_config_yaml_layers`), so an
``approvals.mode`` there wins over this setting. :func:`status` reports that
instead of claiming the setting is in effect.

Known limits
------------
The setting is not a boundary against an agent with the terminal tool on:

* Hermes reads ``approvals.mode`` from ``$HERMES_HOME/config.yaml``, which
  the ``hal0`` user owns (``install/perms.py``) and the agent runs as. With a
  shell it can rewrite that key directly (e.g. to ``off``), no API involved;
  ADR-0024 does not change that. The seed TOML keeps the operator's choice,
  re-provisioning restores it, and :func:`status` / ``hal0 doctor all``
  report the drift (``in_sync``).
* Agents still authenticate with the box admin key (ADR-0024), so an agent
  that can make arbitrary HTTP calls can call the ADMIN route that changes
  the setting, the same gap that lets it approve its own MCP calls. The
  admin MCP server never exposes the route as a tool
  (``EXCLUDED_TOOLS["hermes_command_approvals"]``), and the route refuses a
  request that identifies itself as an agent.
"""

from __future__ import annotations

import asyncio
import subprocess
import tomllib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
from pydantic import ValidationError

from hal0.agents import hermes_provision as hp
from hal0.config.schema import HERMES_COMMAND_APPROVALS_MODES, HermesAgentSettings
from hal0.errors import Hal0Error

log = structlog.get_logger(__name__)
audit_log = structlog.get_logger("hal0.agents.audit")

AGENT_ID = "hermes"
SETTING_TABLE = "hermes"
SETTING_KEY = "command_approvals"
#: Hermes config key the setting renders to.
HERMES_CONFIG_KEY = "approvals.mode"
MODES: tuple[str, ...] = HERMES_COMMAND_APPROVALS_MODES

#: ``Hal0Error.code`` the restart seam raises when ``systemctl restart`` times
#: out (hal0.api.agents.restart). Kept literal so this module stays free of
#: hal0.api imports.
RESTART_TIMEOUT_CODE = "agent.restart_timeout"

# One apply at a time per process: two concurrent PUTs must not interleave
# their snapshot / write / rollback steps.
_APPLY_LOCK = asyncio.Lock()


class CommandApprovalsInvalid(Hal0Error):
    code = "agent.command_approvals_invalid"
    status = 400


class CommandApprovalsApplyFailed(Hal0Error):
    code = "agent.command_approvals_apply_failed"
    status = 500


class CommandApprovalsUnavailable(Hal0Error):
    code = "agent.command_approvals_unavailable"
    status = 409


def default_mode() -> str:
    """The mode an unset or unreadable setting falls back to."""
    return hp.HAL0_APPROVALS_MODE


def _seed_path() -> Path:
    # Late attribute lookups keep the hermes_provision paths the single
    # authority (and the seam tests monkeypatch).
    return hp.INSTALL_SEED_PATH


def _overrides_path() -> Path:
    return hp.OVERRIDES_PATH


def _config_path() -> Path:
    return Path(hp.HERMES_HOME_DEFAULT) / "config.yaml"


@dataclass(frozen=True)
class SettingRead:
    """What the seed TOML says. ``stored`` is False when the key is absent."""

    mode: str
    stored: bool
    invalid_value: str | None = None
    #: The seed TOML exists but could not be read or parsed, so whatever the
    #: operator stored there is unknown and the default is in use.
    unreadable: bool = False


def validate_mode(value: Any) -> str:
    """Return ``value`` if it is exactly ``smart`` or ``manual``; raise otherwise."""
    try:
        return HermesAgentSettings(command_approvals=value).command_approvals
    except ValidationError as exc:
        raise CommandApprovalsInvalid(
            f"Hermes command approvals must be one of {', '.join(MODES)}; got {value!r}",
            details={"mode": value, "allowed": list(MODES)},
        ) from exc


def _read_seed(path: Path) -> dict[str, Any] | None:
    """Parse the seed TOML. ``{}`` when absent; ``None`` when unreadable."""
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None


def read_setting(path: Path | None = None) -> SettingRead:
    """The operator's stored setting, read leniently.

    An absent key, an unreadable file or an invalid value all yield the
    default. An invalid value is reported in ``invalid_value`` and an
    unreadable or unparseable seed in ``unreadable`` (both logged), so the
    dashboard and CLI can say so instead of showing "smart (default)" over
    an operator's ``manual``. Never raises: provisioning must not abort over
    this one key.
    """
    seed_path = path or _seed_path()
    data = _read_seed(seed_path)
    if data is None:
        log.warning(
            "hermes.command_approvals.seed_unreadable",
            path=str(seed_path),
            fallback=default_mode(),
        )
        return SettingRead(mode=default_mode(), stored=False, unreadable=True)
    table = data.get(SETTING_TABLE)
    if not isinstance(table, dict) or SETTING_KEY not in table:
        return SettingRead(mode=default_mode(), stored=False)
    raw = table.get(SETTING_KEY)
    try:
        return SettingRead(mode=validate_mode(raw), stored=True)
    except CommandApprovalsInvalid:
        log.warning(
            "hermes.command_approvals.invalid_setting",
            value=str(raw),
            fallback=default_mode(),
            path=str(seed_path),
        )
        return SettingRead(mode=default_mode(), stored=False, invalid_value=str(raw))


def _approvals_mode_in_yaml(path: Path) -> str | None:
    """``approvals.mode`` as a string from a YAML file, or ``None`` if unset."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    data = hp._parse_yaml_config(text)
    approvals = (data or {}).get("approvals")
    if not isinstance(approvals, dict) or "mode" not in approvals:
        return None
    value = approvals.get("mode")
    # YAML 1.1 reads a bare `off` as False; Hermes normalises it back to "off".
    if value is False:
        return "off"
    return None if value is None else str(value)


def read_override(path: Path | None = None) -> str | None:
    """``approvals.mode`` from overrides.yaml (any value, e.g. ``off``), or ``None``."""
    return _approvals_mode_in_yaml(path or _overrides_path())


def read_rendered(path: Path | None = None) -> str | None:
    """``approvals.mode`` as Hermes's config.yaml currently holds it, or ``None``."""
    return _approvals_mode_in_yaml(path or _config_path())


def status() -> dict[str, Any]:
    """The setting, what is in effect, and why.

    * ``mode``      — the operator setting (default when unset/invalid).
    * ``effective`` — what Hermes is configured to use: the overrides.yaml
      value when one is set, else ``mode``.
    * ``source``    — ``overrides`` | ``setting`` | ``default``.
    * ``rendered``  — the value in Hermes's config.yaml right now (``None``
      when Hermes is not provisioned); ``in_sync`` compares it to
      ``effective`` so drift (a hand or agent edit) is visible.
    """
    setting = read_setting()
    override = read_override()
    rendered = read_rendered()
    effective = override if override is not None else setting.mode
    if override is not None:
        source = "overrides"
    elif setting.stored:
        source = "setting"
    else:
        source = "default"
    return {
        "agent_id": AGENT_ID,
        "mode": setting.mode,
        "effective": effective,
        "source": source,
        "default": default_mode(),
        "allowed": list(MODES),
        "invalid_value": setting.invalid_value,
        "seed_unreadable": setting.unreadable,
        "seed_path": str(_seed_path()),
        "overrides_path": str(_overrides_path()),
        "rendered": rendered,
        "in_sync": None if rendered is None else rendered == effective,
    }


def _render_seed_body(seed: dict[str, Any], mode: str) -> str:
    import tomli_w

    merged = dict(seed)
    table = dict(merged.get(SETTING_TABLE) or {})
    table[SETTING_KEY] = mode
    merged[SETTING_TABLE] = table
    return tomli_w.dumps(merged)


def _write_seed(body: str) -> None:
    hp.write_seed_toml_body(body)


def _config_set(mode: str) -> list[str]:
    """``hermes config set approvals.mode <mode>``; returns errors (empty = ok)."""
    hermes_bin = hp._hermes_bin(Path(hp.HERMES_VENV_DEFAULT))
    if not hermes_bin.exists():
        return [f"hermes binary not found at {hermes_bin}"]
    _applied, errors = hp._apply_config_set(
        [(HERMES_CONFIG_KEY, mode)],
        hermes_bin=hermes_bin,
        hermes_home=Path(hp.HERMES_HOME_DEFAULT),
        run=subprocess.run,
    )
    return errors


def _restore_config(text: str) -> None:
    hp._atomic_write(_config_path(), text)


def _merge_overrides() -> None:
    hp._merge_config_yaml_layers(_config_path(), list_keys={}, overrides_path=_overrides_path())


async def apply_mode(
    mode: Any,
    *,
    actor: str,
    unit_active: Callable[[], Awaitable[bool | None]],
    restart: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Persist ``mode``, re-render Hermes's config and restart the agent.

    All or nothing. Steps:

    1. Validate (exactly ``smart`` | ``manual``).
    2. Persist to the seed TOML (via the ``hal0-agentenv`` seam when
       unprivileged). A failure here changes nothing.
    3. If overrides.yaml sets ``approvals.mode`` it wins, so Hermes's
       config is left alone and nothing restarts; the response says so.
       If Hermes's config is not rendered yet, or already holds ``mode``,
       nothing else happens. (No seed TOML at all means Hermes is not
       installed: refused with :class:`CommandApprovalsUnavailable`.)
    4. ``hermes config set approvals.mode``, re-merge overrides.yaml on top
       (as provisioning does), and read it back.
    5. Restart ``hal0-agent@hermes`` when it is running. A stopped agent
       picks the value up when it next starts; when liveness is unknown the
       response says so (``restart: "unknown"``) instead of claiming it is
       stopped.

    A failure in 4 or 5 restores the previous seed TOML and config.yaml and
    raises :class:`CommandApprovalsApplyFailed`. The exception is a restart
    TIMEOUT: the unit may still be starting on the new config, so the new
    state is kept and the response reports ``restart: "timeout"``.

    ``restart`` must raise :class:`~hal0.errors.Hal0Error` on failure.
    """
    mode = validate_mode(mode)
    async with _APPLY_LOCK:
        seed_path = _seed_path()
        if not seed_path.exists():
            # No seed = Hermes was never installed. Creating one holding only
            # [hermes] would fail AgentConfig validation ([agent] is required)
            # and break the MCP allow-list loader the day Hermes is installed.
            raise CommandApprovalsUnavailable(
                f"Hermes is not installed ({seed_path} does not exist). "
                "Install it first: sudo hal0 agent install hermes",
                details={"path": str(seed_path)},
            )
        try:
            seed_before_text = seed_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise CommandApprovalsApplyFailed(
                f"could not read {seed_path}: {exc}", details={"path": str(seed_path)}
            ) from exc
        seed = _read_seed(seed_path)
        if seed is None:
            # Never rewrite a seed we cannot parse: it carries the MCP
            # allow-list, and a fresh body would silently drop it.
            raise CommandApprovalsApplyFailed(
                f"{seed_path} is not valid TOML; refusing to rewrite it. "
                "Fix it by hand or run `hal0 agent reprovision hermes --repair`.",
                details={"path": str(seed_path)},
            )

        before = status()
        config_path = _config_path()
        installed = config_path.is_file()
        # Snapshot config.yaml BEFORE any write, so a read failure here
        # changes nothing (rather than leaving a new seed with no rollback).
        config_before_text = ""
        if installed:
            try:
                config_before_text = config_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise CommandApprovalsApplyFailed(
                    f"could not read {config_path}: {exc}. Nothing was changed.",
                    details={"path": str(config_path), "error": str(exc)},
                ) from exc
        override = before["source"] == "overrides"
        noop = (
            before["mode"] == mode
            and before["invalid_value"] is None
            and before["source"] != "default"
            and (override or not installed or before["in_sync"])
        )
        if noop:
            return {
                **before,
                "changed": False,
                "restart": "not-needed",
                "message": f"Hermes command approvals already {mode}.",
            }

        audit_log.info(
            "agent.command_approvals.set",
            agent_id=AGENT_ID,
            actor=actor,
            previous=before["mode"],
            mode=mode,
        )
        try:
            await asyncio.to_thread(_write_seed, _render_seed_body(seed, mode))
        except (OSError, subprocess.SubprocessError) as exc:
            raise CommandApprovalsApplyFailed(
                f"could not save the setting to {seed_path}: {exc}. Nothing was changed.",
                details={"path": str(seed_path), "error": str(exc)},
            ) from exc

        if override:
            return {
                **status(),
                "changed": True,
                "restart": "skipped-overridden",
                "message": (
                    f"Saved {mode}, but {before['overrides_path']} sets approvals.mode to "
                    f"{before['effective']!r}, which wins. Hermes was not changed."
                ),
            }
        if not installed:
            return {
                **status(),
                "changed": True,
                "restart": "skipped-not-installed",
                "message": f"Saved {mode}. Hermes is not provisioned yet; it applies at install.",
            }

        if read_rendered() == mode:
            # Hermes already runs with this value (e.g. recording the default
            # explicitly): saved, nothing to re-render or restart.
            return {
                **status(),
                "changed": True,
                "restart": "not-needed",
                "message": f"Saved {mode}. Hermes already uses it; no restart needed.",
            }

        async def _rollback(reason: str) -> list[str]:
            errors: list[str] = []
            try:
                await asyncio.to_thread(_restore_config, config_before_text)
            except OSError as exc:
                errors.append(f"config.yaml restore: {exc}")
            try:
                await asyncio.to_thread(_write_seed, seed_before_text)
            except (OSError, subprocess.SubprocessError) as exc:
                errors.append(f"seed TOML restore: {exc}")
            audit_log.warning(
                "agent.command_approvals.rolled_back",
                agent_id=AGENT_ID,
                actor=actor,
                mode=mode,
                reason=reason,
                rollback_errors=errors,
            )
            return errors

        set_errors = await asyncio.to_thread(_config_set, mode)
        if not set_errors:
            # Re-merge overrides.yaml on top, exactly as provisioning does after
            # its own `config set` pass: an operator override that appeared
            # since `before` was read must still win.
            try:
                await asyncio.to_thread(_merge_overrides)
            except Exception as exc:  # YAML error, OSError: roll back below
                set_errors = [f"overrides re-merge: {exc}"]
        expected = read_override() or mode
        rendered = read_rendered()
        if set_errors or rendered != expected:
            reason = "; ".join(set_errors) or f"config.yaml reads back {rendered!r}"
            rollback_errors = await _rollback(reason)
            raise CommandApprovalsApplyFailed(
                f"could not write approvals.mode to Hermes's config ({reason}). "
                + (
                    "The previous setting was restored."
                    if not rollback_errors
                    else "Restoring the previous setting also failed: " + "; ".join(rollback_errors)
                ),
                details={"mode": mode, "errors": set_errors, "rollback_errors": rollback_errors},
            )

        active = await unit_active()
        if active is None:
            # Liveness is unobservable (no systemd, a wedged bus, a probe
            # timeout). Never treat unknown as "not running".
            return {
                **status(),
                "changed": True,
                "restart": "unknown",
                "message": (
                    f"Hermes command approvals set to {mode}, but hal0 could not tell "
                    "whether Hermes is running. Restart it to apply: "
                    "sudo systemctl restart hal0-agent@hermes"
                ),
            }
        if active is False:
            return {
                **status(),
                "changed": True,
                "restart": "skipped-inactive",
                "message": (
                    f"Hermes command approvals set to {mode}. Hermes is not running; "
                    "it uses the new mode when it next starts."
                ),
            }

        try:
            await restart()
        except Hal0Error as exc:
            if getattr(exc, "code", None) == RESTART_TIMEOUT_CODE:
                # The unit may still be coming up on the NEW config. Rolling
                # the files back now could leave Hermes running the new mode
                # while the setting and GET say the old one, so keep the new
                # state (it is consistent on disk) and say the outcome is
                # unknown.
                audit_log.warning(
                    "agent.command_approvals.restart_timeout",
                    agent_id=AGENT_ID,
                    actor=actor,
                    mode=mode,
                )
                return {
                    **status(),
                    "changed": True,
                    "restart": "timeout",
                    "message": (
                        f"Hermes command approvals set to {mode}, but the Hermes restart "
                        "did not finish in time, so whether it is running is unknown. "
                        "Check `hal0 agent status hermes`."
                    ),
                }
            rollback_errors = await _rollback(f"restart failed: {exc}")
            raise CommandApprovalsApplyFailed(
                f"Hermes restart failed ({exc}). "
                + (
                    "The previous setting and config were restored; check "
                    "`hal0 agent status` because the agent may be stopped."
                    if not rollback_errors
                    else "Restoring the previous setting also failed: " + "; ".join(rollback_errors)
                ),
                details={"mode": mode, "rollback_errors": rollback_errors},
            ) from exc

        return {
            **status(),
            "changed": True,
            "restart": "restarted",
            "message": f"Hermes command approvals set to {mode}. Hermes restarted.",
        }


__all__ = [
    "AGENT_ID",
    "MODES",
    "CommandApprovalsApplyFailed",
    "CommandApprovalsInvalid",
    "CommandApprovalsUnavailable",
    "SettingRead",
    "apply_mode",
    "default_mode",
    "read_override",
    "read_rendered",
    "read_setting",
    "status",
    "validate_mode",
]
