"""In-process orchestration for first-run setup (design D3, spec §6.6).

Lifted out of the ``POST /api/install/apply`` route so the same algorithm
runs in-process at install time (api not up yet) and behind the HTTP route
post-install. Deps are injected so there is no hidden ``app.state`` coupling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SlotSelection:
    """One slot the user chose to provision."""

    capability: str  # "chat" | "coder"
    slot_name: str  # "chat" | "coder"
    port: int
    model_id: str
    device: str | None = None  # explicit override; None → derive from hw
    profile: str | None = None  # explicit override; None → derive from device


@dataclass(frozen=True)
class Selections:
    """The full set of first-run choices to apply."""

    storage_dir: str
    slots: list[SlotSelection]
    extensions: dict[str, bool]  # extension id -> enabled
    npu_opt_in: bool = False


@dataclass
class SlotOutcome:
    slot: str
    model_id: str
    created: bool = False
    device: str | None = None
    profile: str | None = None
    pull_job_id: str | None = None
    skipped: str | None = None
    error: str | None = None


@dataclass
class ExtensionOutcome:
    ext_id: str
    installed: bool = False
    skipped: str | None = None
    error: str | None = None


@dataclass
class PullPlan:
    """A registered-but-not-yet-run pull. The caller decides how to run it
    (``background.add_task`` for the route; ``await`` with progress for the TUI)."""

    model_id: str
    job: Any  # registry.pull.PullJob
    kwargs: dict[str, Any]


@dataclass
class SetupResult:
    slots: list[SlotOutcome]
    extensions: list[ExtensionOutcome]
    model_ids: list[str]
    pulls: list[PullPlan] = field(default_factory=list)
