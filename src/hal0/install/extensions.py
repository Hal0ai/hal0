"""The first-run Extensions registry (spec §6.4). A growing, grouped list of
Apps and Agents the user can enable; each one is auto-wired into hal0 at
install time. Today's installer enables OpenWebUI + Hermes unconditionally —
this makes them (and future entries) a selectable, wired set."""

from __future__ import annotations

import shutil
import subprocess
import time
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
]
_BY_ID = {e.id: e for e in EXTENSIONS}

# Extension id -> the bundled-agent id ``hal0 agent install`` actually
# recognises (``hal0.agents.manager.BUNDLED_AGENTS``). Empty today — every
# extension id in :data:`EXTENSIONS` matches its bundled-agent id 1:1 (the
# "pi" -> "pi-coder" divergence was removed with the pi-coder driver, refs
# P1-drivers). Kept as a seam rather than deleted outright so a future
# agent whose setup-screen label diverges from its driver's canonical id
# has somewhere to add the translation without re-threading
# :func:`install_extension`.
_AGENT_ID_ALIASES: dict[str, str] = {}


def list_extensions(kind: str | None = None) -> list[Extension]:
    return [e for e in EXTENSIONS if kind is None or e.kind == kind]


def get_extension(ext_id: str) -> Extension | None:
    return _BY_ID.get(ext_id)


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def _run_ok(cmd: list[str]) -> bool:
    """Best-effort subprocess run — ``True`` on rc=0, never raises.

    Mirrors installer/install.sh's ``|| true`` quiesce calls (e.g. the
    OpenWebUI runtime-guard fallback) so the Python wiring can reproduce the
    same "don't fail the flow" posture without a bare ``except``.
    """
    try:
        return subprocess.run(cmd, check=False).returncode == 0
    except OSError:
        return False


def _podman_usable() -> bool:
    """``command -v podman && podman info`` — same check install.sh gates
    the OpenWebUI unit on (installer/install.sh ~1497), since the unit is a
    plain ``podman run`` ExecStart and would restart-loop with 203/EXEC
    without a working runtime."""
    if shutil.which("podman") is None:
        return False
    return _run_ok(["podman", "info"])


def _docker_present() -> bool:
    """``command -v docker`` — used only to make the OpenWebUI skip reason
    honest (see :func:`install_openwebui`); it never makes docker a usable
    runtime for the companion.

    The packaged ``hal0-openwebui.service`` hardcodes ``/usr/bin/podman`` in
    every ExecStart* line (unlike the per-slot ContainerProvider unit, which
    is runtime-templated). Force-starting it on a docker-only host would
    restart-loop with 203/EXEC on the missing podman binary, so the
    companion genuinely requires podman today — this only distinguishes
    "docker is present but podman is required" from "no runtime at all" in
    the skip outcome, so a docker-only operator isn't told there's no
    runtime when there's one hal0 just can't use for this unit yet."""
    return shutil.which("docker") is not None


def _wait_active(unit: str, timeout: float = 15.0) -> bool:
    """Poll ``systemctl is-active --quiet <unit>`` — Python port of
    install.sh's ``wait_active`` bash helper."""
    if shutil.which("systemctl") is None:
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _run_ok(["systemctl", "is-active", "--quiet", unit]):
            return True
        time.sleep(0.5)
    return False


def install_openwebui() -> ExtensionOutcome:
    """Enable ``hal0-openwebui`` with the same runtime guard as
    installer/install.sh's inline block: without a usable podman the unit
    would restart-loop (203/EXEC), so quiesce it instead of enabling.

    This is the ONE place both the install-time path (``apply_setup`` →
    :func:`install_extension`) and the deferred ``hal0 app install
    openwebui`` verb call into, so "skip now, install later" is lossless
    (issue #1102 / decision Q9).
    """
    unit = "hal0-openwebui.service"
    if not _podman_usable():
        # A prior install (or an upgrade where openwebui was enabled) may
        # have left the unit restart-looping with no runtime. Quiesce it so
        # status reflects reality (inactive, not failed/looping) — mirrors
        # install.sh's fallback branch.
        _run_ok(["systemctl", "disable", "--now", unit])
        _run_ok(["systemctl", "reset-failed", unit])
        # The packaged unit is podman-only (hardcoded /usr/bin/podman); a
        # docker-only host isn't "no container runtime" — it's "the wrong
        # one for this companion". Surface that distinction instead of
        # silently implying docker is a viable fallback here (it is for
        # slots, via ContainerProvider, but not for this unit).
        if _docker_present():
            return ExtensionOutcome(ext_id="openwebui", skipped="docker_present_podman_required")
        return ExtensionOutcome(ext_id="openwebui", skipped="no_container_runtime")

    if not _run_ok(["systemctl", "enable", "--now", unit]):
        return ExtensionOutcome(ext_id="openwebui", error=f"systemctl enable --now {unit} failed")

    if not _wait_active(unit, timeout=30):
        # Slow first boot (image pull / sqlite init) isn't fatal — the unit
        # is enabled and will keep coming up; only the confirmation lagged.
        return ExtensionOutcome(ext_id="openwebui", installed=True, error="not_active_yet")

    return ExtensionOutcome(ext_id="openwebui", installed=True)


def install_extension(ext_id: str) -> ExtensionOutcome:
    """Install + wire one extension. Apps enable their systemd unit; agents
    go through ``hal0 agent install <id>`` (which performs the wiring —
    base_url routing, creds, and — for hermes — the Telegram/Discord
    gateway — that install.sh does today)."""
    ext = get_extension(ext_id)
    if ext is None:
        return ExtensionOutcome(ext_id=ext_id, skipped="unknown_extension")
    try:
        if ext.id == "openwebui":
            return install_openwebui()
        elif ext.kind == "agent":
            agent_id = _AGENT_ID_ALIASES.get(ext.id, ext.id)
            _run(["hal0", "agent", "install", agent_id])
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
