"""The first-run Extensions registry (spec §6.4). A growing, grouped list of
Apps and Agents the user can enable; each one is auto-wired into hal0 at
install time. Today's installer enables OpenWebUI + Hermes unconditionally —
this makes them (and future entries) a selectable, wired set."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Literal

from hal0.install.orchestrate import ExtensionOutcome


@dataclass(frozen=True)
class Extension:
    id: str
    kind: Literal["app", "agent"]
    name: str
    summary: str
    default_enabled: bool


EXTENSIONS: list[Extension] = [
    Extension("openwebui", "app", "Open WebUI", "Chat web UI for your models", True),
    Extension("comfyui", "app", "ComfyUI", "Image & video generation (iGPU)", True),
    Extension("hermes", "agent", "Hermes", "Conversational agent with memory", True),
    Extension("pi", "agent", "Pi", "Coding agent", False),
]
_BY_ID = {e.id: e for e in EXTENSIONS}


def list_extensions(kind: str | None = None) -> list[Extension]:
    return [e for e in EXTENSIONS if kind is None or e.kind == kind]


def get_extension(ext_id: str) -> Extension | None:
    return _BY_ID.get(ext_id)


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def install_extension(ext_id: str) -> ExtensionOutcome:
    """Install + wire one extension. Apps enable their systemd unit; agents
    go through ``hal0 agent install <id>`` (which performs the wiring —
    base_url routing, creds — that install.sh does today)."""
    ext = get_extension(ext_id)
    if ext is None:
        return ExtensionOutcome(ext_id=ext_id, skipped="unknown_extension")
    try:
        if ext.kind == "agent":
            _run(["hal0", "agent", "install", ext.id])
        elif ext.id == "openwebui":
            _run(["systemctl", "enable", "--now", "hal0-openwebui.service"])
        elif ext.id == "comfyui":
            # ComfyUI is owned by the seeded img slot. The legacy
            # /opt/comfyui scripts remain manual operator tools only.
            _run(["systemctl", "enable", "--now", "hal0-slot@img.service"])
        return ExtensionOutcome(ext_id=ext_id, installed=True)
    except Exception as exc:  # best-effort
        return ExtensionOutcome(ext_id=ext_id, error=str(exc))


__all__ = [
    "EXTENSIONS",
    "Extension",
    "get_extension",
    "install_extension",
    "list_extensions",
]
