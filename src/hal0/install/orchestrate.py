"""In-process orchestration for first-run setup (design D3, spec §6.6).

Lifted out of the ``POST /api/install/apply`` route so the same algorithm
runs in-process at install time (api not up yet) and behind the HTTP route
post-install. Deps are injected so there is no hidden ``app.state`` coupling.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hal0.config import paths
from hal0.config.schema import HardwareInfo
from hal0.install.profile_derive import derive_device, derive_profile
from hal0.registry.curated import get_curated
from hal0.registry.pull import get_job, make_job

log = logging.getLogger(__name__)

# Mirrors ``cli.setup_plan.MIN_FREE_GIB`` — duplicated (not imported) so this
# install-time validation path stays independent of the CLI module (isolation
# guardrail: setup_plan.py is owned by a sibling workstream).
MIN_FREE_GIB = 10.0


@dataclass(frozen=True)
class SlotSelection:
    """One slot the user chose to provision.

    ``model_id`` may be ``None`` (or empty) to provision an *empty scaffold*
    slot: the device/profile/port structure is created but ``model.default`` is
    left unset for the operator to fill in later (pick-free — we never choose a
    model for them). A falsy ``model_id`` means no curated lookup and no pull.
    """

    capability: str  # "chat" | "coder" | "embed" | "rerank" | "stt" | "tts" | "vision" | "img"
    slot_name: str
    port: int
    model_id: str | None = None  # None/"" → scaffold an empty (modelless) slot
    device: str | None = None  # explicit override; None → derive from hw
    profile: str | None = None  # explicit override; None → derive from device


@dataclass(frozen=True)
class Selections:
    """The full set of first-run choices to apply."""

    storage_dir: str
    slots: list[SlotSelection]
    extensions: dict[str, bool]  # extension id -> enabled
    npu_opt_in: bool = False
    # Task 3.5: ComfyUI default capability selections recorded at install time.
    # These are NOT pulled at install — the operator triggers pulls later via
    # POST /api/comfyui/models/fetch.  Stored as (capability_id, family) pairs
    # so they survive serialisation without depending on capabilities.py here.
    # NOTE: install-selection integration friction — Selections models LLM slots
    # (chat/coder), not image-gen pickers; this field is a lightweight sidecar
    # rather than a first-class slot because ComfyUI picks are capability/family
    # pairs (no port, no model_id from the LLM registry).  A future refactor
    # could lift ComfyUI picks into a dedicated setup phase.
    comfyui_defaults: tuple[tuple[str, str], ...] = ()


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
    (``background.add_task`` for the route; ``await`` with progress for the TUI).

    ``slot_names`` are the slots created DISABLED for this model (WS-E, #1108).
    The caller MUST drive each plan through :func:`run_pull_and_activate` so the
    slot(s) flip ``enabled=True`` only after the bytes land — never before the
    model exists on disk (the start-before-model race). A FAILED pull leaves the
    slot(s) disabled and marked (``[meta].pull_failed``), so a half-provisioned
    box parks the slot instead of crash-looping a container against a missing
    model.
    """

    model_id: str
    job: Any  # registry.pull.PullJob
    kwargs: dict[str, Any]
    slot_names: list[str] = field(default_factory=list)


@dataclass
class SetupResult:
    slots: list[SlotOutcome]
    extensions: list[ExtensionOutcome]
    model_ids: list[str]
    pulls: list[PullPlan] = field(default_factory=list)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _build_slot_cfg(*, slot, model_id, device, profile, port, context_size=4096, enabled=False):
    """Podman-aware slot config dict (device+profile, NOT backend — #807).

    Born DISABLED by default (WS-E, #1108): the guided apply path creates the
    slot, THEN queues the pull, and only flips ``enabled=True`` once the pull
    completes (:func:`run_pull_and_activate`). Seeding ``enabled=True`` here
    would let the slot start before its model exists on disk.
    """
    return {
        "name": slot,
        "port": port,
        "device": device,
        "profile": profile,
        "enabled": enabled,
        "model": {"default": model_id, "context_size": context_size},
    }


# ── Context budget (WS-E / #1108) ───────────────────────────────────────────────

# Conservative KV-cache cost per context token (bytes): fp16 K+V for a mid-size
# dense GGUF (~32 layers, GQA 8 kv-heads, head_dim 128):
#   2 (K+V) * 32 * 8 * 128 * 2 bytes ≈ 128 KiB / token.
# Over-estimating KV clamps HARDER → strictly safer against first-warm OOM. MoE/
# MTP primaries have a far smaller hybrid KV, so this only ever under-fits (more
# headroom), never OOMs.
_KV_BYTES_PER_TOKEN = 128 * 1024
# Server + activation/compute-buffer headroom held back off the memory pool
# before any of it funds KV cache.
_RUNTIME_RESERVE_GB = 1.0
# Never clamp below this — a sub-2K window slot is useless.
_MIN_CONTEXT = 2048
# Round the derived budget down to a whole multiple of this (llama.cpp likes
# power-of-two-ish windows; keeps the number legible in the TOML).
_CTX_ALIGN = 1024
_BYTES_PER_GB = 1024**3


def _memory_budget_gb(hw: HardwareInfo) -> float:
    """GB of host memory available to load a model (weights + KV).

    Derived from the authoritative ``hardware.json`` facts (#1097), NOT a Strix
    constant. Mirrors ``hardware.recommend._vram_budget_gb`` but ALSO honours a
    cgroup memory cap (#372) so an LXC/container-capped host — the box that
    produced the ``oom`` artifact — clamps against its real ceiling:

      * AMD UMA (Strix Halo): the model pool is host RAM shared via GTT, so a
        cgroup cap binds it. Half the pool — leave RAM for the OS + the rest of
        the stack (OpenWebUI, FastAPI, ...).
      * discrete GPU: dedicated VRAM, which a host cgroup does not cap.
      * CPU-only / GPU with no usable VRAM: half of MemAvailable, itself capped
        by any cgroup limit.
    """
    if hw.gpus:
        g = hw.gpus[0]
        if g.vendor == "amd" and hw.unified_memory_mb >= hw.ram_mb * 0.95:
            pool_gb = hw.unified_memory_mb / 1024
            if hw.cgroup_max_mb:
                pool_gb = min(pool_gb, hw.cgroup_max_mb / 1024)
            return max(pool_gb * 0.5, 0.0)
        if g.vram_mb > 0:
            return g.vram_mb / 1024
    avail_gb = max(hw.ram_available_mb / 1024, 1.0)
    if hw.cgroup_max_mb:
        avail_gb = min(avail_gb, hw.cgroup_max_mb / 1024)
    return max(avail_gb * 0.5, 0.0)


def _clamp_context_size(requested: int, hw: HardwareInfo, *, weights_gb: float = 0.0) -> int:
    """Clamp a requested context window to what host memory can actually fund.

    Model weights + a runtime reserve come off the memory budget first; the
    remainder funds the KV cache at a conservative bytes-per-token rate. Returns
    ``min(requested, budget)`` floored at ``_MIN_CONTEXT``. A falsy/zero
    ``requested`` means "use the whole budget". This replaces blindly trusting
    ``curated.context_length`` (which is the model's *native* window, not a
    hardware budget — the first-warm OOM, #1108).
    """
    budget_gb = _memory_budget_gb(hw)
    kv_gb = budget_gb - max(weights_gb, 0.0) - _RUNTIME_RESERVE_GB
    if kv_gb <= 0:
        return _MIN_CONTEXT
    budget_tokens = int(kv_gb * _BYTES_PER_GB / _KV_BYTES_PER_TOKEN)
    budget_tokens = (budget_tokens // _CTX_ALIGN) * _CTX_ALIGN
    budget_tokens = max(budget_tokens, _MIN_CONTEXT)
    if not requested or requested <= 0:
        return budget_tokens
    return max(min(int(requested), budget_tokens), _MIN_CONTEXT)


# ── Enable-on-pull-success (WS-E / #1108) ───────────────────────────────────────


async def _set_slot_enabled(slot_manager, slot_name: str, enabled: bool, *, failed: bool) -> None:
    """Best-effort flip of a slot's ``enabled`` flag after its pull settles.

    Non-aborting per ADR-0010: a failed config rewrite must not crash the pull
    driver. On a FAILED pull we also stamp ``[meta].pull_failed`` so the parked
    slot is clearly marked (the dashboard / ``hal0 doctor`` can surface it).
    """
    updates: dict[str, Any] = {"enabled": enabled}
    if failed:
        updates["meta"] = {"pull_failed": True}
    # Activation is best-effort — the model still downloaded; the operator can
    # enable the slot by hand. Never let this abort the pull driver (ADR-0010).
    with contextlib.suppress(Exception):
        await slot_manager.update_config(slot_name, updates)


async def run_pull_and_activate(plan: PullPlan, *, slot_manager) -> None:
    """Run one planned pull, then activate its slot(s) — WS-E (#1108).

    On SUCCESS: flip every ``plan.slot_names`` slot to ``enabled=True`` (the
    model now exists on disk, so a start is safe). On FAILURE (``run_pull``
    raised, or the job settled ``state == "failed"``): leave the slot(s)
    disabled and mark them, so nothing crash-loops against a missing model.

    Re-raises the original exception after marking, so callers that inspect
    ``gather(..., return_exceptions=True)`` results still see the failure.
    """
    from hal0.registry.pull import run_pull

    try:
        await run_pull(plan.job, **plan.kwargs)
    except BaseException:
        for name in plan.slot_names:
            await _set_slot_enabled(slot_manager, name, False, failed=True)
        raise
    if getattr(plan.job, "state", None) == "failed":
        for name in plan.slot_names:
            await _set_slot_enabled(slot_manager, name, False, failed=True)
        return
    for name in plan.slot_names:
        await _set_slot_enabled(slot_manager, name, True, failed=False)


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


def _nearest_existing_ancestor(path: str) -> Path | None:
    """Walk up from *path* to the nearest ancestor that actually exists.

    A chosen store dir usually doesn't exist yet at persist time, so free-space
    and writability checks need to land on the mount it *will* be created on.
    Mirrors the ancestor-walk in ``cli.setup_plan._free_space_gib``. Returns
    ``None`` if no ancestor can be resolved (e.g. a bare relative fragment).
    """
    p = Path(path)
    seen: set[Path] = set()
    while p not in seen:
        seen.add(p)
        if p.exists():
            return p
        if p.parent == p:
            return None
        p = p.parent
    return None


def _free_space_gib(path: str) -> float | None:
    """Available GiB on the mount containing *path*.

    Duplicated from (not imported off) ``cli.setup_plan._free_space_gib`` per
    the isolation guardrail that keeps this install-time path independent of
    the CLI module. Returns ``None`` if no ancestor can be stat'd.
    """
    ancestor = _nearest_existing_ancestor(path)
    if ancestor is None:
        return None
    try:
        return shutil.disk_usage(ancestor).free / (1024**3)
    except OSError:
        return None


def _is_root_fs(ancestor: Path) -> bool:
    """True if *ancestor* lives on the same filesystem as ``/``.

    Compares ``st_dev`` (same pattern as ``registry.model_store``'s same-fs
    bind-mount check) rather than string-prefix matching ``/`` so bind mounts
    and nested mount points are handled correctly. Fails closed (``False``) on
    a stat error — a validation warning is best-effort, never fatal.
    """
    try:
        return ancestor.stat().st_dev == Path("/").stat().st_dev
    except OSError:
        return False


def _is_writable(ancestor: Path) -> bool:
    return os.access(ancestor, os.W_OK)


def _validate_store_mount(storage_dir: str) -> None:
    """Best-effort writability + free-space check on the chosen store mount.

    Warns (logs only — never raises) when the mount is unwritable, sits on
    the root filesystem, or is short on space (issue #1100 / decision Q4).
    Non-fatal: a bad pick is surfaced to the operator via logs/``hal0
    doctor`` rather than aborting the setup walk (ADR-0010).
    """
    ancestor = _nearest_existing_ancestor(storage_dir)
    if ancestor is None:
        log.warning(
            "model store %s: could not resolve an existing ancestor to validate", storage_dir
        )
        return

    if not _is_writable(ancestor):
        log.warning("model store %s is not writable (checked %s)", storage_dir, ancestor)

    if _is_root_fs(ancestor):
        log.warning(
            "model store %s is on the root filesystem — model + FLM/NPU weights "
            "will consume root-FS space; consider a dedicated mount",
            storage_dir,
        )

    free_gib = _free_space_gib(storage_dir)
    if free_gib is None:
        log.warning("model store %s: could not determine free space", storage_dir)
    elif free_gib < MIN_FREE_GIB:
        log.warning(
            "model store %s has only %.1f GiB free (< %.0f GiB threshold)",
            storage_dir,
            free_gib,
            MIN_FREE_GIB,
        )


def _colocated_flm_store(store_dir: str) -> str:
    """Compute the FLM (NPU) store path co-located under the chosen model store.

    Mirrors the convention documented on ``ModelsConfig.flm_store`` and used by
    ``installer/install.sh`` (``<store>/flm/models``) so NPU weights land on
    the same mount as the rest of the model store rather than stranding on the
    root FS.
    """
    return str(Path(store_dir) / "flm" / "models")


def _persist_store_dir(storage_dir: str) -> None:
    """Persist a chosen model-store directory to ``[models].store`` AND
    co-locate ``[models].flm_store`` alongside it in hal0.toml (issue #1100,
    decision Q4 — extends #1095's WS-A store threading).

    ``Selections.storage_dir`` (WS-A, issue #1095) is the operator's pull
    destination pick from ``/apply``, ``/apply-selections`` and
    ``hal0 setup --storage-dir``. The pull engine resolves its destination from
    ``[models].store`` *lazily at pull time*
    (``registry.pull._pull_root`` → ``ModelsConfig.effective_store``), so
    writing the pick here — before the planned pulls run — is what makes a
    custom store actually land pulls in that directory rather than silently
    no-op'ing the field. ``[models].flm_store`` is resolved the same way
    (``config.paths.flm_models_dir``), so NPU weights need the same treatment
    or they strand under the default ``/var/lib/hal0`` cache on the root FS.

    Also runs a non-fatal writability + free-space check on the chosen mount
    (``_validate_store_mount``), warning on an unwritable dir, a root-FS pick,
    or low free space — never blocking the walk.

    Best-effort per ADR-0010: an empty or relative value is ignored (the pull
    engine keeps its default store), and a config-write failure is swallowed so
    a bad storage pick never aborts the slot/extension walk. Idempotent —
    skips the rewrite when both ``[models].store`` and ``[models].flm_store``
    already point at the chosen (co-located) paths.
    """
    s = (storage_dir or "").strip()
    if not s or not Path(s).is_absolute():
        return

    _validate_store_mount(s)
    flm_target = _colocated_flm_store(s)

    try:
        from hal0.config.loader import load_hal0_config, save_hal0_config

        cfg = load_hal0_config()
        store_ok = (cfg.models.store or "").strip() == s
        flm_ok = (cfg.models.flm_store or "").strip() == flm_target
        if store_ok and flm_ok:
            return
        cfg.models.store = s
        cfg.models.flm_store = flm_target
        save_hal0_config(cfg)
    except Exception:
        # Config persistence is best-effort: pulls fall back to the default
        # store and the rest of setup must proceed regardless.
        pass


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
    # WS-E (#1108): map model_id → the plan WE created this run, so two slots
    # sharing one model_id (e.g. chat + coder on the same pick) both get
    # enabled off the single pull rather than the second slot being stranded
    # disabled.
    plans_by_model: dict[str, PullPlan] = {}

    # Honour the operator's chosen store BEFORE planning pulls: the pull engine
    # reads ``[models].store`` lazily at pull time, so persisting it here is
    # what threads ``storage_dir`` all the way to the pull destination (#1095).
    _persist_store_dir(selections.storage_dir)

    for s in selections.slots:
        rec = SlotOutcome(slot=s.slot_name, model_id=s.model_id or "")
        device = s.device or derive_device(s.capability, hardware, npu_opt_in=selections.npu_opt_in)
        if device is None:
            rec.skipped = "not_applicable_on_this_hardware"
            slot_outcomes.append(rec)
            continue
        profile = s.profile or derive_profile(s.capability, device)
        rec.device, rec.profile = device, profile

        # Empty scaffold: create the slot structure with no model default and
        # no pull. The operator assigns a model later (pick-free).
        if not s.model_id:
            cfg = _build_slot_cfg(
                slot=s.slot_name,
                model_id="",
                device=device,
                profile=profile,
                port=s.port,
            )
            try:
                await slot_manager.create(s.slot_name, cfg)
                rec.created = True
            except Exception as exc:  # best-effort
                rec.error = str(exc)
            slot_outcomes.append(rec)
            continue

        curated = get_curated(s.model_id)
        if curated is None:
            rec.skipped = "needs_upstream_routing"
            slot_outcomes.append(rec)
            continue

        _ensure_registry_entry(registry, s.model_id)
        # WS-E (#1108): clamp the native curated window to a VRAM/RAM-aware
        # budget derived from hardware.json (#1097), not the raw
        # curated.context_length that produced the first-warm ``oom``. Model
        # weights come off the budget first; the remainder funds the KV cache.
        ctx = _clamp_context_size(
            int(curated.context_length or 0),
            hardware,
            weights_gb=float(getattr(curated, "size_gb", 0.0) or 0.0),
        )
        # Born DISABLED — flipped to enabled only after the pull completes.
        cfg = _build_slot_cfg(
            slot=s.slot_name,
            model_id=s.model_id,
            device=device,
            profile=profile,
            port=s.port,
            context_size=ctx,
            enabled=False,
        )
        try:
            await slot_manager.create(s.slot_name, cfg)
            rec.created = True
        except Exception as exc:  # best-effort
            rec.error = str(exc)
            slot_outcomes.append(rec)
            continue

        existing = get_job(jobs, s.model_id)
        own_plan = plans_by_model.get(s.model_id)
        if own_plan is not None:
            # Second slot for a model WE already planned this run — ride the
            # same pull so it, too, gets enabled on success.
            own_plan.slot_names.append(s.slot_name)
            job = own_plan.job
        elif existing is not None and getattr(existing, "state", None) in ("queued", "running"):
            # A pull for this model is already in flight from elsewhere; don't
            # double-run it. The slot stays disabled (safer than the old
            # enabled-before-model default) for the operator to enable once the
            # in-flight pull lands.
            job = existing
        else:
            job = make_job(s.model_id)
            jobs[s.model_id] = job
            plan = PullPlan(
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
                slot_names=[s.slot_name],
            )
            plans_by_model[s.model_id] = plan
            pulls.append(plan)
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
