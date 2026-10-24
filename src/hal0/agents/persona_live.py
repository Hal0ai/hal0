"""Make a persona edit reach the running Hermes (#2921, #2850).

The persona prompt lives in Hermes's ``config.yaml`` as
``agent.system_prompt`` (:func:`hermes_provision._persona_prompt_layer`).
Editing a persona TOML or moving ``active.txt`` changes nothing for Hermes
until the active persona is rendered into ``config.yaml`` again.

:func:`refresh_persona_prompt` does that with the YAML layer merge the
provisioner applies (:func:`hermes_provision._merge_config_yaml_layers`, with
the operator's ``overrides.yaml`` still winning). No restart is needed: Hermes
v0.22.4 re-reads ``config.yaml`` keyed on the file signature, so the atomic
rewrite is picked up by the gateway's next turn and by every new TUI session.
Open dashboard chats keep the prompt they started with. Hermes code is
imported lazily so importing :mod:`hal0.api` never loads it (golden path GP15).
"""

from __future__ import annotations

import threading
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

#: Serialises refreshes in this process. The active-persona read, the render
#: and the config write happen under it, so a refresh that started earlier can
#: never overwrite a later activation's prompt with a stale render.
_REFRESH_LOCK = threading.Lock()

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
) -> dict[str, Any]:
    """Render the active persona's prompt into Hermes's config.yaml.

    ``root`` is the personas store the caller edited, so the render reads the
    same files the edit wrote. Never raises: every failure comes back as a
    ``status`` plus a ``message`` that says what did and did not happen.

    Returns ``{"status", "message", "changed"}``
    where ``status`` is one of ``applied`` (written), ``unchanged`` (config
    already had this prompt), ``overridden`` (overrides.yaml wins, nothing
    written), ``not-installed`` (no config.yaml yet) or ``failed`` (config.yaml
    could not be written).
    """
    with _REFRESH_LOCK:
        return _refresh_locked(root, hermes_home)


def _operator_personality(config: Path) -> str:
    """The operator's ``display.personality``, or ``""``. Never raises."""
    import yaml

    try:
        loaded = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
        display = loaded.get("display") if isinstance(loaded, dict) else None
        value = display.get("personality") if isinstance(display, dict) else ""
        return str(value).strip() if value else ""
    except Exception:  # advisory only
        return ""


def _refresh_locked(root: Path, hermes_home: Path | str | None) -> dict[str, Any]:
    from hal0.agents import hermes_provision as hp
    from hal0.agents import personas as personas_mod

    home = Path(hermes_home) if hermes_home is not None else hp.HERMES_HOME_DEFAULT
    config = home / "config.yaml"
    base: dict[str, Any] = {"changed": False}
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

    # An operator-chosen display.personality is preserved on purpose; Hermes
    # overlays a built-in personality of that name on agent.system_prompt.
    personality = _operator_personality(config)
    caveat = (
        f" Note: display.personality is {personality!r}; if that names a Hermes "
        "personality it overlays this prompt."
        if personality
        else ""
    )
    if not changed:
        return _result(
            UNCHANGED,
            "Saved. Hermes's config.yaml already carries this prompt." + caveat,
            **base,
        )
    return _result(
        APPLIED,
        "Persona prompt written to Hermes's config.yaml; new sessions and the next "
        "gateway turn use it. Open dashboard chats keep the old prompt until you "
        "start a new chat." + caveat,
        changed=True,
    )
