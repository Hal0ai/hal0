"""Make a persona edit reach the running Hermes (#2921, #2850).

The persona prompt lives in Hermes's ``config.yaml`` as
``agent.system_prompt`` (:func:`hermes_provision._persona_prompt_layer`), and
Hermes reads that file at process start. Editing a persona TOML or moving
``active.txt`` therefore changes nothing for a running agent until the active
persona is rendered into ``config.yaml`` again and the consumers restart.

:func:`refresh_persona_prompt` does both through the mechanisms the Hermes
command-approvals setting already uses: the YAML layer merge the provisioner
applies (:func:`hermes_provision._merge_config_yaml_layers`, with the
operator's ``overrides.yaml`` still winning), then
:func:`hermes_provision.try_restart_hermes_consumers` (the ``hal0-systemctl``
seam, ``sudo -n`` for a non-root caller). Hermes code is imported lazily so
importing :mod:`hal0.api` never loads it (golden path GP15).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

#: Statuses :func:`refresh_persona_prompt` reports, in ``status``.
APPLIED = "applied"
UNCHANGED = "unchanged"
OVERRIDDEN = "overridden"
NOT_INSTALLED = "not-installed"
FAILED = "failed"
RESTART_FAILED = "restart-failed"

_REPAIR_HINT = "sudo hal0 agent reprovision hermes --repair"


def _result(status: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"status": status, "message": message, **extra}


def _overridden(overrides_path: Path) -> bool:
    """True when the operator's overrides.yaml pins ``agent.system_prompt``."""
    import yaml

    if not overrides_path.is_file():
        return False
    loaded = yaml.safe_load(overrides_path.read_text(encoding="utf-8")) or {}
    agent = loaded.get("agent") if isinstance(loaded, dict) else None
    return isinstance(agent, dict) and "system_prompt" in agent


def refresh_persona_prompt(
    root: Path,
    *,
    hermes_home: Path | str | None = None,
    restart: bool = True,
) -> dict[str, Any]:
    """Render the active persona's prompt into Hermes's config.yaml and apply it.

    ``root`` is the personas store the caller edited, so the render reads the
    same files the edit wrote. Never raises: every failure comes back as a
    ``status`` plus a ``message`` that says what did and did not happen.

    Returns ``{"status", "message", "changed", "restarted", "failed_units"}``
    where ``status`` is one of ``applied`` (written, consumers restarted),
    ``unchanged`` (config already had this prompt), ``overridden``
    (overrides.yaml wins, nothing written), ``not-installed`` (no config.yaml
    yet), ``restart-failed`` (written, a restart failed) or ``failed``
    (config.yaml could not be written).
    """
    from hal0.agents import hermes_provision as hp
    from hal0.agents import personas as personas_mod

    home = Path(hermes_home) if hermes_home is not None else hp.HERMES_HOME_DEFAULT
    config = home / "config.yaml"
    base: dict[str, Any] = {"changed": False, "restarted": False, "failed_units": []}
    if not config.is_file():
        return _result(
            NOT_INSTALLED,
            "Saved. Hermes is not provisioned yet; the persona prompt applies at install.",
            **base,
        )
    try:
        if _overridden(hp.OVERRIDES_PATH):
            return _result(
                OVERRIDDEN,
                f"Saved, but {hp.OVERRIDES_PATH} sets agent.system_prompt, which wins. "
                "Hermes was not changed.",
                **base,
            )
        prompt, _name = hp._active_persona_render(
            hp.BootstrapState(hermes_home=str(home)), personas_root=root
        )
        names = [
            *hp._SEED_PERSONA_DISPLAY_NAMES,
            *(p.display_name for p in personas_mod.list_personas(root=root)),
        ]
        changed = hp._merge_config_yaml_layers(
            config,
            list_keys=hp._persona_prompt_layer(prompt),
            overrides_path=hp.OVERRIDES_PATH,
            persona_names=names,
        )
    except Exception as exc:  # I/O, YAML or permission errors: report, do not 500.
        log.warning("persona.live_render_failed", error=str(exc), error_type=type(exc).__name__)
        return _result(
            FAILED,
            f"Saved, but could not write Hermes's config.yaml: {exc}. "
            f"The new prompt is not live; run `{_REPAIR_HINT}` to apply it.",
            **base,
        )

    if not changed:
        return _result(
            UNCHANGED,
            "Saved. Hermes's config.yaml already carries this prompt; nothing to restart.",
            **base,
        )
    base["changed"] = True
    if not restart:
        return _result(
            APPLIED,
            "Persona prompt written to Hermes's config.yaml. Restart Hermes to apply it.",
            **base,
        )
    failed = hp.try_restart_hermes_consumers()
    if failed:
        return _result(
            RESTART_FAILED,
            "Persona prompt written to Hermes's config.yaml, but "
            f"{', '.join(failed)} could not be restarted, so the running agent still "
            "uses the old prompt until it is restarted.",
            **{**base, "failed_units": failed},
        )
    return _result(
        APPLIED,
        "Persona prompt written to Hermes's config.yaml; the Hermes agent and gateway "
        "were restarted (a stopped unit stays stopped) and new sessions use it.",
        **{**base, "restarted": True},
    )
