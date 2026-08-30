"""Declarative catalog of hal0-shipped updatable components (spec §1).

One ``ComponentDef`` per component. Everything the update surface needs to
reason about a component lives here: the release-carried pin, the on-box
version probe, and the converge arm. Callables are lazy (module-local
functions doing local imports) so this stays a cheap import like
``hal0.services.registry``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ComponentDef:
    id: str
    name: str
    kind: str  # "container-unit" | "slot-images" | "venv"
    #: Join key into hal0.services.registry (dashboard rollup); None = no
    #: service row (runner-images).
    service_id: str | None
    pinned: Callable[[], str]
    installed: Callable[[], str | None]
    #: Converge arm — status-dict posture, never raises operationally.
    converge: Callable[..., dict[str, Any]]


def _openwebui_pinned() -> str:
    from hal0.openwebui.image_pin import OPENWEBUI_IMAGE_PIN

    return OPENWEBUI_IMAGE_PIN


def _openwebui_installed() -> str | None:
    from hal0.openwebui.image_pin import installed_unit_path, parse_pinned_digest

    unit = installed_unit_path()
    try:
        return parse_pinned_digest(unit.read_text(encoding="utf-8"))
    except OSError:
        return None


def _openwebui_converge(**kwargs: Any) -> dict[str, Any]:
    from hal0.components.openwebui_arm import converge_openwebui

    return converge_openwebui(**kwargs)


def _runner_images_pinned() -> str:
    from hal0.runners import RUNNER_IMAGES

    return f"{len(RUNNER_IMAGES)} images"


def _runner_images_installed() -> str | None:
    # Per-image truth lives in the converge result's detail rows; the
    # component-level cell mirrors pinned so status derives from the last
    # converge result, not a version diff.
    return _runner_images_pinned()


def _runner_images_converge(**kwargs: Any) -> dict[str, Any]:
    from hal0.components.runner_images_arm import converge_runner_images

    return converge_runner_images(**kwargs)


def _hermes_pinned() -> str:
    from hal0.agents.hermes_provision import _hermes_version_pin

    return _hermes_version_pin()


def _hermes_installed() -> str | None:
    from hal0.components.hermes_arm import installed_hermes_pin

    return installed_hermes_pin()


def _hermes_converge(**kwargs: Any) -> dict[str, Any]:
    from hal0.components.hermes_arm import converge_hermes

    return converge_hermes(**kwargs)


def _hindsight_pinned() -> str:
    from hal0.memory.engine_upgrade import HINDSIGHT_API_PIN

    return HINDSIGHT_API_PIN


def _hindsight_installed() -> str | None:
    import subprocess

    from hal0.memory.engine_upgrade import _installed_version, hindsight_dir

    return _installed_version(subprocess.run, hindsight_dir() / ".venv")


def _hindsight_converge(**kwargs: Any) -> dict[str, Any]:
    from hal0.memory.engine_upgrade import upgrade_memory_engine

    return upgrade_memory_engine(**kwargs)


#: Converge order. Hindsight LAST — slowest pass by an order of magnitude
#: and the only one that stops a companion service mid-pass (see
#: run_post_activation_migrations' ordering note for the same rule).
COMPONENTS: tuple[ComponentDef, ...] = (
    ComponentDef(
        id="openwebui",
        name="OpenWebUI",
        kind="container-unit",
        service_id="openwebui",
        pinned=_openwebui_pinned,
        installed=_openwebui_installed,
        converge=_openwebui_converge,
    ),
    ComponentDef(
        id="runner-images",
        name="Runner images",
        kind="slot-images",
        service_id=None,
        pinned=_runner_images_pinned,
        installed=_runner_images_installed,
        converge=_runner_images_converge,
    ),
    ComponentDef(
        id="hermes",
        name="Hermes",
        kind="venv",
        service_id="hermes",
        pinned=_hermes_pinned,
        installed=_hermes_installed,
        converge=_hermes_converge,
    ),
    ComponentDef(
        id="hindsight",
        name="Hindsight",
        kind="venv",
        service_id="hindsight",
        pinned=_hindsight_pinned,
        installed=_hindsight_installed,
        converge=_hindsight_converge,
    ),
)


def component_by_id(component_id: str) -> ComponentDef | None:
    for comp in COMPONENTS:
        if comp.id == component_id:
            return comp
    return None


__all__ = ["COMPONENTS", "ComponentDef", "component_by_id"]
