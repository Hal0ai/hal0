"""StackApplyEngine — translate a StackConfig into an atomic slot-config change.

Phase A of the Stacks apply flow (spec §5): reconcile each stack slot entry
onto its ``slots/<slot>.toml`` and assemble ONE ``hal0.slot_config.ChangeSet``
spanning every touched file, then commit it through the verified
``SlotConfigStore`` (atomic write + per-file rollback). Compute-only ``plan()``
backs the dashboard's dry-run diff preview.

Out of scope here (PR-2b): slot lifecycle convergence (load/swap/unload) and
capability-child (embed/stt/tts/rerank/vision) routing through the
CapabilityOrchestrator.
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hal0.config import paths
from hal0.config.schema import StackConfig, StackSlotEntry
from hal0.model_meta import canonical_device, device_to_legacy_backend
from hal0.slot_config import ChangeSet, FileState, SlotConfigStore
from hal0.slots.state import DISPATCHABLE_STATES, SlotState
from hal0.stacks.state import (
    StackStateRecord,
    read_stack_state,
    stack_content_hash,
    write_stack_state_atomic,
)

log = logging.getLogger(__name__)


def _read_toml_or_none(path: Path) -> dict[str, Any] | None:
    """Read a TOML file as a raw dict; ``None`` when it doesn't exist.

    Local mirror of ``hal0.slot_config._read_toml_or_none`` (kept local to
    avoid importing a sibling module's private; the body is trivial I/O).
    """
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return None


@dataclass(frozen=True)
class StackChangePlan:
    """The compute-only result of planning a stack apply.

    ``change_set`` is consumed by :meth:`StackApplyEngine.apply_config` (commit)
    and by the drift hasher; ``summary`` is human-readable diff lines for the
    dry-run preview.
    """

    stack_slug: str
    change_set: ChangeSet
    summary: list[str]


# Slot states by convergence intent.
# _DISPATCHABLE aliases the canonical ready-set (DR-8) — single source of truth.
_DISPATCHABLE = DISPATCHABLE_STATES
_TRANSITIONAL = frozenset(
    {SlotState.PULLING, SlotState.STARTING, SlotState.WARMING, SlotState.UNLOADING}
)

# Capability child → orchestrator group, and → underlying system slot name.
# Hardcoded reverse of hal0.capabilities.orchestrator._CHILD_TO_SLOT (keyed
# (group, child) → slot_name). Hardcoded, NOT imported, to keep this module
# clear of the capabilities import cycle that hal0.slot_config also avoids —
# KEEP IN SYNC with the orchestrator.
_CHILD_TO_GROUP: dict[str, str] = {
    "embed": "embed",
    "rerank": "embed",
    "stt": "voice",
    "tts": "voice",
    "img": "img",
    "vision": "vision",
}
_CHILD_TO_SLOT_NAME: dict[str, str] = {
    "embed": "embed",
    "rerank": "embed-rerank",
    "stt": "stt",
    "tts": "tts",
    "img": "img",
    "vision": "vision",
}


@dataclass
class ConvergeReport:
    """What converge() did, per slot. Failures are recorded, not raised."""

    loaded: list[str]
    swapped: list[str]
    skipped: list[str]
    unloaded: list[str]
    capabilities_applied: list[str]
    errors: list[tuple[str, str]]


class StackApplyEngine:
    """Reconcile a StackConfig onto slot TOMLs as one ChangeSet."""

    def __init__(
        self,
        *,
        slots_dir: Path | None = None,
        store: SlotConfigStore | None = None,
        slot_manager: Any = None,
        orchestrator: Any = None,
    ) -> None:
        self._slots_dir = Path(slots_dir) if slots_dir else None
        self._store = store or SlotConfigStore(slots_dir=slots_dir)
        # Runtime deps for converge() (PR-2b). Duck-typed: slot_manager needs
        # async list()/load()/swap()/unload(); orchestrator needs async
        # apply(slot, child, partial). Injected real in production (PR-4),
        # recording fakes in tests. None until converge() is called.
        self._slot_manager = slot_manager
        self._orchestrator = orchestrator

    def _slot_path(self, slot_name: str) -> Path:
        base = self._slots_dir or paths.slots_config_dir()
        return base / f"{slot_name}.toml"

    # ── plan (compute-only) ──────────────────────────────────────────────────

    def plan(self, slug: str, stack: StackConfig) -> StackChangePlan:
        """Compute the post-state for ``stack``. Writes NOTHING.

        Only entries that carry a primary ``model`` and whose slot TOML already
        exists are reconciled (slot creation is SlotManager's job, out of 2a
        scope). For every other entry ``after == before``.
        """
        befores: list[FileState] = []
        afters: list[FileState] = []
        summary: list[str] = []

        for entry in stack.slots:
            if not entry.model:
                continue
            path = self._slot_path(entry.slot)
            before = _read_toml_or_none(path)
            after = self._reconciled_stack_slot(before, entry)
            befores.append(FileState(path=path, data=before))
            afters.append(FileState(path=path, data=after))
            if before != after:
                summary.append(self._summarize(entry.slot, before, after))

        return StackChangePlan(
            stack_slug=slug,
            change_set=ChangeSet(before=tuple(befores), after=tuple(afters)),
            summary=summary,
        )

    def validate(
        self,
        stack: StackConfig,
        known_profiles: set[str],
        known_models: set[str],
    ) -> list[str]:
        """Pre-apply sanity check: flag entries whose profile/model won't resolve.

        Import-light on purpose — the caller passes the known profile-name and
        model-id sets (already loaded at the route layer). Returns one
        human-readable warning per unresolved reference so the dry-run preview
        and a real apply can flag that runtime will silently diverge from the
        stack (e.g. a slot pinned to a profile absent from the local catalog, or
        a model id not in the registry). Advisory only: apply still proceeds and
        converge records its own per-slot lifecycle errors separately.
        """
        warnings: list[str] = []
        for entry in stack.slots:
            if entry.profile and entry.profile not in known_profiles:
                warnings.append(f"{entry.slot}: profile {entry.profile!r} not found")
            if entry.model and entry.model not in known_models:
                warnings.append(f"{entry.slot}: model {entry.model!r} not in registry")
        return warnings

    def _reconciled_stack_slot(
        self, before: dict[str, Any] | None, entry: StackSlotEntry
    ) -> dict[str, Any] | None:
        """Project a stack slot entry onto the existing slot TOML dict.

        Deep-merges the nested ``[model]``/``[server]`` tables so sibling keys
        (``context_size`` etc.) survive. Writes both the v0.2 ``device`` and the
        one-release-legacy ``backend`` alias via :mod:`hal0.model_meta`. Returns
        ``before`` unchanged when the slot file is absent (creation is out of
        2a scope).
        """
        if before is None:
            return None
        after = dict(before)

        if entry.model:
            model = dict(after.get("model") or {})
            model["default"] = entry.model
            after["model"] = model
        if entry.device:
            device = canonical_device(entry.device)
            if device:
                after["device"] = device
                legacy = device_to_legacy_backend(device)
                if legacy:
                    after["backend"] = legacy
        if entry.provider:
            after["provider"] = entry.provider
        if entry.profile is not None:
            after["profile"] = entry.profile
        if entry.role is not None:
            after["role"] = entry.role
        # ``vision`` is a plain bool (no inherit) → declaratively written.
        after["vision"] = entry.vision
        if entry.mtp is not None:
            after["mtp"] = entry.mtp
        if entry.enable_thinking is not None:
            after["enable_thinking"] = entry.enable_thinking
        if entry.server_extra_args is not None:
            server = dict(after.get("server") or {})
            server["extra_args"] = entry.server_extra_args
            after["server"] = server
        return after

    @staticmethod
    def _summarize(slot: str, before: dict[str, Any] | None, after: dict[str, Any] | None) -> str:
        b_model = (before or {}).get("model", {}).get("default") if before else None
        a_model = (after or {}).get("model", {}).get("default") if after else None
        if b_model != a_model:
            return f"{slot}: model {b_model or '∅'} → {a_model or '∅'}"
        return f"{slot}: config updated"

    # ── apply (commit) ───────────────────────────────────────────────────────

    def apply_config(self, plan: StackChangePlan) -> None:
        """Commit ``plan.change_set`` to disk atomically.

        Delegates to ``SlotConfigStore.commit``: each file is written
        tmpfile+fsync+rename; a mid-set failure restores every already-written
        file to its ``before`` snapshot and re-raises — disk is never left
        half-reconciled. A no-op ChangeSet (nothing changed) writes nothing.

        SC-10: the commit runs under the store's shared advisory lock so a
        stack apply and a concurrent capability apply (which reconcile the same
        ``slots/*.toml`` surface) serialize rather than interleaving their
        writes.
        """
        with self._store.transaction():
            self._store.commit(plan.change_set)

    # ── drift / active pointer ───────────────────────────────────────────────

    def _projection_from_plan(self, plan: StackChangePlan) -> dict[str, Any]:
        """The slot→after-dict projection a plan would write (keyed by slot name)."""
        return {fs.path.stem: fs.data for fs in plan.change_set.after}

    def _projection_live(self, stack: StackConfig) -> dict[str, Any]:
        """The slot→current-disk-dict projection for a stack's primary slots."""
        out: dict[str, Any] = {}
        for entry in stack.slots:
            if not entry.model:
                continue
            out[entry.slot] = _read_toml_or_none(self._slot_path(entry.slot))
        return out

    def record_active(self, plan: StackChangePlan, *, applied_at: float) -> None:
        """Record ``plan``'s stack as active, fingerprinting what it wrote.

        Call AFTER ``apply_config`` succeeds. The hash is taken over the
        after-state projection, which equals live disk immediately post-commit
        (so ``drift_status`` reports ``clean`` until something hand-edits a slot).
        """
        record = StackStateRecord(
            active_slug=plan.stack_slug,
            content_hash=stack_content_hash(self._projection_from_plan(plan)),
            applied_at=applied_at,
        )
        write_stack_state_atomic(paths.stacks_state_path(), record)

    def drift_status(self, catalog: Any) -> dict[str, Any]:
        """Report the active stack and whether live config has drifted from it.

        ``none`` — no stack applied. ``clean`` — live slot config matches the
        applied fingerprint. ``modified`` — a slot was hand-edited since apply.
        ``catalog`` is a ``StacksCatalog`` (duck-typed: needs ``.resolve(slug)``).
        """
        record = read_stack_state(paths.stacks_state_path())
        if record is None:
            return {"active": None, "status": "none"}
        try:
            resolved = catalog.resolve(record.active_slug)
        except Exception:
            # Active stack was deleted out from under the pointer.
            return {"active": record.active_slug, "status": "modified"}
        live = self._projection_live(StackConfig(slots=list(resolved.slots)))
        status = "clean" if stack_content_hash(live) == record.content_hash else "modified"
        return {"active": record.active_slug, "status": status}

    # ── converge (Phase B — runtime lifecycle) ───────────────────────────────

    async def converge(self, stack: StackConfig) -> ConvergeReport:
        """Drive SlotManager/orchestrator so live runtime matches ``stack``.

        Three passes over one ``SlotManager.list()`` snapshot: load/swap the
        stack's primary slots, route enabled capability rows through the
        orchestrator, and unload dispatchable slots not in the stack
        (declarative replace). Per-slot failures are recorded in the report,
        never raised — a committed config (PR-2a) is never unwound by a
        lifecycle hiccup.
        """
        if self._slot_manager is None or self._orchestrator is None:
            raise RuntimeError("converge() requires slot_manager and orchestrator")

        report = ConvergeReport([], [], [], [], [], [])
        snapshots = {s.name: s for s in await self._slot_manager.list()}
        touched: set[str] = set()

        # Pass 1 — primary slots (entries carrying a model).
        for entry in stack.slots:
            if not entry.model:
                continue
            touched.add(entry.slot)
            await self._converge_primary(entry, snapshots.get(entry.slot), report)

        # Pass 2 — capability children (enabled rows only).
        await self._converge_capabilities(stack, touched, report)

        # Pass 3 — unload dispatchable slots the stack doesn't touch.
        await self._converge_unload(snapshots, touched, report)

        return report

    async def _converge_primary(
        self, entry: StackSlotEntry, snap: Any, report: ConvergeReport
    ) -> None:
        """Load / swap / skip one primary slot to match ``entry.model``."""
        try:
            if snap is not None and snap.state in _TRANSITIONAL:
                report.skipped.append(entry.slot)
                return
            if snap is None or snap.state not in _DISPATCHABLE:
                await self._slot_manager.load(entry.slot, model_id=entry.model)
                report.loaded.append(entry.slot)
            elif snap.model_id != entry.model:
                await self._slot_manager.swap(entry.slot, entry.model)
                report.swapped.append(entry.slot)
            else:
                report.skipped.append(entry.slot)
        except Exception as exc:  # per-slot failures are reported, not raised
            report.errors.append((entry.slot, str(exc)))

    async def _converge_capabilities(
        self, stack: StackConfig, touched: set[str], report: ConvergeReport
    ) -> None:
        """Route each enabled capability row through ``orchestrator.apply``.

        A stack lists the children it wants ON; disabled rows are skipped here
        and turned off by the unload sweep. Each row's underlying slot name is
        added to ``touched`` so the sweep won't unload it.
        """
        for entry in stack.slots:
            for row in entry.capabilities:
                if not row.enabled:
                    continue
                group = _CHILD_TO_GROUP.get(row.child)
                slot_name = _CHILD_TO_SLOT_NAME.get(row.child)
                if group is None or slot_name is None:
                    report.errors.append((f"capability:{row.child}", "unknown capability child"))
                    continue
                touched.add(slot_name)
                try:
                    await self._orchestrator.apply(
                        group,
                        row.child,
                        {
                            "device": row.device,
                            "provider": row.provider,
                            "model": row.model,
                            "enabled": True,
                        },
                    )
                    report.capabilities_applied.append(f"{slot_name}/{row.child}")
                except Exception as exc:  # recorded, not raised
                    report.errors.append((f"{slot_name}/{row.child}", str(exc)))

    async def _converge_unload(
        self, snapshots: dict[str, Any], touched: set[str], report: ConvergeReport
    ) -> None:
        """Unload every dispatchable slot not touched by this stack.

        Declarative replace: the snapshot is the PRE-converge state, so slots
        loaded/swapped in passes 1-2 are in ``touched`` and never swept.
        Offline/transitional slots are left alone.
        """
        for name, snap in snapshots.items():
            if name in touched or snap.state not in _DISPATCHABLE:
                continue
            try:
                await self._slot_manager.unload(name)
                report.unloaded.append(name)
            except Exception as exc:  # recorded, not raised
                report.errors.append((name, str(exc)))
