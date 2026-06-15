"""In-process orchestration for first-run setup (design D3, spec §6.6).

Lifted out of the ``POST /api/install/apply`` route so the same algorithm
runs in-process at install time (api not up yet) and behind the HTTP route
post-install. Deps are injected so there is no hidden ``app.state`` coupling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hal0.config import paths
from hal0.config.schema import HardwareInfo
from hal0.install.profile_derive import derive_device, derive_profile
from hal0.registry.curated import get_curated
from hal0.registry.pull import get_job, make_job


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


# ── Helpers ────────────────────────────────────────────────────────────────────


def _build_slot_cfg(*, slot, model_id, device, profile, port, context_size=4096):
    """Podman-aware slot config dict (device+profile, NOT backend — #807)."""
    return {
        "name": slot,
        "port": port,
        "device": device,
        "profile": profile,
        "enabled": True,
        "model": {"default": model_id, "context_size": context_size},
    }


def _ensure_registry_entry(registry, model_id) -> None:
    """No-op shim if the registry already knows the id; create a stub otherwise.

    Mirrors installer.py's ``_ensure_registry_entry``; the real registry object
    exposes the same surface. A plain dict (tests) is tolerated.
    """
    if hasattr(registry, "ensure"):
        registry.ensure(model_id)


def _sentinel_path() -> Path:
    """`/var/lib/hal0/.first_run_done` — identical to installer.py's sentinel."""
    return paths.var_lib() / ".first_run_done"


def mark_first_run_done() -> None:
    p = _sentinel_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text("")
    tmp.replace(p)  # atomic


def install_extension(ext_id: str) -> ExtensionOutcome:
    """Install + wire one extension. Delegates to extensions.install_extension
    (Task 1.2); imported lazily to avoid a cycle."""
    from hal0.install.extensions import install_extension as _do

    return _do(ext_id)


def _install_extensions(extensions: dict) -> list[ExtensionOutcome]:
    outs: list[ExtensionOutcome] = []
    for ext_id, enabled in extensions.items():
        if not enabled:
            continue
        try:
            outs.append(install_extension(ext_id))
        except Exception as exc:  # best-effort
            outs.append(ExtensionOutcome(ext_id=ext_id, error=str(exc)))
    return outs


# ── Core orchestration ─────────────────────────────────────────────────────────


async def apply_setup(
    selections: Selections,
    *,
    hardware: HardwareInfo,
    slot_manager,
    registry,
    jobs: dict,
    hf_token: str | None = None,
    write_sentinel: bool = True,
) -> SetupResult:
    """Create the chosen slots OFFLINE, plan their pulls, install extensions,
    and (optionally) write the first-run sentinel. Best-effort, non-aborting
    per item (ADR-0010): a bad row is reported with ``skipped``/``error`` and
    the walk continues. Does NOT run pulls — see ``SetupResult.pulls``."""
    slot_outcomes: list[SlotOutcome] = []
    model_ids: list[str] = []
    pulls: list[PullPlan] = []

    for s in selections.slots:
        rec = SlotOutcome(slot=s.slot_name, model_id=s.model_id)
        device = s.device or derive_device(s.capability, hardware, npu_opt_in=selections.npu_opt_in)
        if device is None:
            rec.skipped = "not_applicable_on_this_hardware"
            slot_outcomes.append(rec)
            continue
        profile = s.profile or derive_profile(s.capability, device)
        rec.device, rec.profile = device, profile

        curated = get_curated(s.model_id)
        if curated is None:
            rec.skipped = "needs_upstream_routing"
            slot_outcomes.append(rec)
            continue

        _ensure_registry_entry(registry, s.model_id)
        ctx = int(curated.context_length or 0) or 4096
        cfg = _build_slot_cfg(
            slot=s.slot_name,
            model_id=s.model_id,
            device=device,
            profile=profile,
            port=s.port,
            context_size=ctx,
        )
        try:
            await slot_manager.create(s.slot_name, cfg)
            rec.created = True
        except Exception as exc:  # best-effort
            rec.error = str(exc)
            slot_outcomes.append(rec)
            continue

        existing = get_job(jobs, s.model_id)
        if existing is not None and getattr(existing, "state", None) in ("queued", "running"):
            job = existing
        else:
            job = make_job(s.model_id)
            jobs[s.model_id] = job
            pulls.append(
                PullPlan(
                    model_id=s.model_id,
                    job=job,
                    kwargs=dict(
                        hf_repo=curated.hf_repo,
                        hf_file=curated.hf_file,
                        registry=registry,
                        hf_token=hf_token,
                        comfyui_subdir=curated.comfyui_subdir or None,
                        capability=s.capability,
                    ),
                )
            )
        rec.pull_job_id = job.job_id
        model_ids.append(s.model_id)
        slot_outcomes.append(rec)

    ext_outcomes = _install_extensions(selections.extensions)

    if write_sentinel:
        mark_first_run_done()

    return SetupResult(
        slots=slot_outcomes,
        extensions=ext_outcomes,
        model_ids=model_ids,
        pulls=pulls,
    )
