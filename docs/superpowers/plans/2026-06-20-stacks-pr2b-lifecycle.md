# Stacks — PR-2b: Lifecycle Convergence — Implementation Plan

> **Status: Implemented / Shipped (v0.8.x).** `StackApplyEngine.converge()` (SlotManager load/swap/unload + capability-child routing) landed in `src/hal0/stacks/apply.py`. (#925) Plan retained for historical context.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add async `StackApplyEngine.converge(stack)` — drive the existing `SlotManager` to load/swap the stack's primary slots to their assigned models, route the stack's capability-child rows through the existing `CapabilityOrchestrator.apply`, and unload running slots NOT in the stack (declarative replace). Returns a per-slot `ConvergeReport`; individual failures are recorded, never unwound.

**Architecture:** `converge` reads one `SlotManager.list()` snapshot, then in three passes: (1) per primary entry decide load/swap/skip from current state+model; (2) per enabled capability row call `orchestrator.apply(group, child, …)`; (3) unload any dispatchable slot not touched by (1)/(2). `SlotManager`/orchestrator are injected deps (real instances in production via PR-4; recording fakes in tests — the pattern the orchestrator tests already use). Phase-A config (PR-2a) is unchanged; `converge` is the runtime half that PR-4's REST `apply` will call after `apply_config`.

**Tech Stack:** Python 3.12, asyncio, pytest (`asyncio_mode = "auto"` — async tests need no decorator). No new dependencies.

**Stacked on:** PR #923 (`feat/stacks-apply-engine`) → which is stacked on #921. This branch is `feat/stacks-lifecycle`; its PR targets `feat/stacks-apply-engine` until the lower PRs merge, then retarget.

## Global Constraints

- **Scope of THIS PR (2b):** the runtime convergence layer ONLY — `converge()` + its three helpers + `ConvergeReport`, plus injected `slot_manager`/`orchestrator` deps. NO config writing (that is PR-2a's `apply_config`, untouched), NO REST/MCP (PR-4), NO export/import (PR-3), NO drift changes.
- **Reuse, don't reinvent:** call the EXISTING async `SlotManager.load(slot, model_id=…)` / `swap(slot, new_model_id)` / `unload(slot)` / `list()`, and `CapabilityOrchestrator.apply(slot, child, partial)`. Do not re-implement lifecycle or touch `container_provider`.
- **Injected deps:** `StackApplyEngine.__init__` gains `slot_manager=None` and `orchestrator=None` (keyword, default None so PR-2a's `StackApplyEngine()` callers and tests keep working). `converge` raises `RuntimeError` if `slot_manager`/`orchestrator` is missing.
- **Primary-slot decision (from one `list()` snapshot):** dispatchable = {READY, SERVING, IDLE}; transitional = {PULLING, STARTING, WARMING, UNLOADING}; needs-load = {OFFLINE, ERROR} or no snapshot. If transitional → skip (don't fight an in-flight transition). If dispatchable and `model_id` differs → `swap`. If dispatchable and model matches → skip. Else → `load`.
- **Capability routing:** only ENABLED rows are applied (a stack lists the children it wants on; omitted/disabled children are turned off by the unload sweep). Map `child → (group, child)` via the hardcoded reverse of the orchestrator's `_CHILD_TO_SLOT` (hardcoded, NOT imported, to avoid the capabilities import cycle that `hal0.slot_config` also avoids — keep in sync with `hal0.capabilities.orchestrator`).
- **Declarative unload:** "touched" slots = primary slots with a model ∪ underlying slot-names of enabled capability rows. Unload any slot whose pre-converge snapshot state is dispatchable and whose name is NOT touched.
- **Failure handling (spec §5 Phase-B):** wrap each lifecycle/orchestrator call in try/except; record `(slot_or_action, message)` in `report.errors` and continue. A per-slot failure never aborts the rest of convergence and never unwinds committed config.
- **Test runner:** `cd /home/halo/dev/wt/stacks-lifecycle && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest <path> -q`. `asyncio_mode="auto"` is set in pyproject — async test functions run without a decorator.
- **Conventions:** test files `tests/stacks/test_*.py`; classes `Test<Feature>`; functions `test_<behavior>`; plain `assert`; shared fakes in `tests/stacks/conftest.py`.

## File Structure

| File | Responsibility | Action |
| --- | --- | --- |
| `tests/stacks/conftest.py` | `RecordingSlotManager`, `RecordingOrchestrator`, `FakeSnap` — record async lifecycle/apply calls; configurable pre-state. | Create |
| `src/hal0/stacks/apply.py` | `ConvergeReport`; injected `slot_manager`/`orchestrator`; `converge()` + `_converge_primary` (T1), `_converge_capabilities` + child maps (T2), `_converge_unload` (T3). | Modify |
| `tests/stacks/test_converge_primary.py` | primary load/swap/skip/transitional/error tests. | Create (T1) |
| `tests/stacks/test_converge_capabilities.py` | capability routing tests. | Create (T2) |
| `tests/stacks/test_converge_unload.py` | declarative unload-sweep tests. | Create (T3) |

---

## Task 1: Recording fakes + `converge()` primary pass

**Files:**
- Create: `tests/stacks/conftest.py`
- Modify: `src/hal0/stacks/apply.py`
- Test: `tests/stacks/test_converge_primary.py`

**Interfaces:**
- Consumes: `hal0.slots.state.SlotState` (the enum); `hal0.config.schema.StackConfig`/`StackSlotEntry` (PR-1); the PR-2a `StackApplyEngine`.
- Produces:
  - `tests/stacks/conftest.py`: `FakeSnap(name, state, model_id)`; `RecordingSlotManager(snapshots=None)` with async `list()/load/swap/unload` recording into `.calls: list[tuple[str,str,str|None]]`; `RecordingOrchestrator()` with async `apply(slot, child, partial)` recording into `.calls: list[tuple[str,str,dict]]`.
  - `apply.py`: `ConvergeReport` dataclass (`loaded`, `swapped`, `skipped`, `unloaded`, `capabilities_applied: list[str]`, `errors: list[tuple[str,str]]`); `StackApplyEngine.__init__(..., slot_manager=None, orchestrator=None)`; `async converge(stack) -> ConvergeReport`; `async _converge_primary(entry, snap, report)`.

- [ ] **Step 1: Write the failing test + shared fakes**

Create `tests/stacks/conftest.py`:

```python
"""Shared recording fakes for the Stacks convergence tests.

Mirrors the FakeSlotManager pattern used in tests/capabilities: async methods
that record their calls without touching systemd/containers, so converge()'s
decision logic can be asserted by inspecting the call list.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hal0.slots.state import SlotState


@dataclass
class FakeSnap:
    """A minimal Slot snapshot: just the fields converge() reads."""

    name: str
    state: SlotState
    model_id: str | None = None


class RecordingSlotManager:
    """Records load/swap/unload/list calls; serves a configurable pre-state."""

    def __init__(self, snapshots: list[FakeSnap] | None = None) -> None:
        self.calls: list[tuple[str, str, str | None]] = []
        self._snapshots = list(snapshots or [])

    async def list(self) -> list[FakeSnap]:
        self.calls.append(("list", "", None))
        return list(self._snapshots)

    async def load(self, slot_name: str, model_id: str | None = None) -> FakeSnap:
        self.calls.append(("load", slot_name, model_id))
        return FakeSnap(slot_name, SlotState.READY, model_id)

    async def swap(self, slot_name: str, new_model_id: str) -> FakeSnap:
        self.calls.append(("swap", slot_name, new_model_id))
        return FakeSnap(slot_name, SlotState.READY, new_model_id)

    async def unload(self, slot_name: str) -> FakeSnap:
        self.calls.append(("unload", slot_name, None))
        return FakeSnap(slot_name, SlotState.OFFLINE, None)


class RecordingOrchestrator:
    """Records capability apply() calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def apply(self, slot: str, child: str, partial: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((slot, child, dict(partial)))
        return {"slot": slot, "child": child, "status": "ready"}
```

Create `tests/stacks/test_converge_primary.py`:

```python
"""Tests for converge() primary-slot pass (load/swap/skip/transitional/error).

Targeted file run:
    cd /home/halo/dev/wt/stacks-lifecycle && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_converge_primary.py -q
"""

from __future__ import annotations

import pytest

from hal0.config.schema import StackConfig, StackSlotEntry
from hal0.slots.state import SlotState
from hal0.stacks.apply import ConvergeReport, StackApplyEngine
from tests.stacks.conftest import FakeSnap, RecordingOrchestrator, RecordingSlotManager


def _engine(sm: RecordingSlotManager) -> StackApplyEngine:
    return StackApplyEngine(slot_manager=sm, orchestrator=RecordingOrchestrator())


def _stack(*entries: StackSlotEntry) -> StackConfig:
    return StackConfig(name="S", slots=list(entries))


class TestPrimaryConverge:
    async def test_offline_slot_is_loaded(self) -> None:
        sm = RecordingSlotManager([FakeSnap("agent", SlotState.OFFLINE, None)])
        report = await _engine(sm).converge(_stack(StackSlotEntry(slot="agent", model="ace-saber")))
        assert ("load", "agent", "ace-saber") in sm.calls
        assert report.loaded == ["agent"]

    async def test_missing_snapshot_is_loaded(self) -> None:
        sm = RecordingSlotManager([])  # agent not configured/known yet
        report = await _engine(sm).converge(_stack(StackSlotEntry(slot="agent", model="ace-saber")))
        assert ("load", "agent", "ace-saber") in sm.calls
        assert report.loaded == ["agent"]

    async def test_dispatchable_different_model_is_swapped(self) -> None:
        sm = RecordingSlotManager([FakeSnap("agent", SlotState.READY, "old-model")])
        report = await _engine(sm).converge(_stack(StackSlotEntry(slot="agent", model="ace-saber")))
        assert ("swap", "agent", "ace-saber") in sm.calls
        assert report.swapped == ["agent"]
        assert not [c for c in sm.calls if c[0] == "load"]

    async def test_dispatchable_same_model_is_skipped(self) -> None:
        sm = RecordingSlotManager([FakeSnap("agent", SlotState.READY, "ace-saber")])
        report = await _engine(sm).converge(_stack(StackSlotEntry(slot="agent", model="ace-saber")))
        assert report.skipped == ["agent"]
        assert not [c for c in sm.calls if c[0] in ("load", "swap")]

    async def test_transitional_slot_is_skipped(self) -> None:
        sm = RecordingSlotManager([FakeSnap("agent", SlotState.WARMING, "ace-saber")])
        report = await _engine(sm).converge(_stack(StackSlotEntry(slot="agent", model="ace-saber")))
        assert report.skipped == ["agent"]
        assert not [c for c in sm.calls if c[0] in ("load", "swap")]

    async def test_entry_without_model_is_ignored(self) -> None:
        sm = RecordingSlotManager([])
        report = await _engine(sm).converge(_stack(StackSlotEntry(slot="stt")))  # no model → capability-only
        assert report.loaded == [] and report.swapped == [] and report.skipped == []

    async def test_load_failure_is_recorded_not_raised(self) -> None:
        class Boom(RecordingSlotManager):
            async def load(self, slot_name, model_id=None):
                raise RuntimeError("spawn failed")

        sm = Boom([FakeSnap("agent", SlotState.OFFLINE, None)])
        report = await _engine(sm).converge(_stack(StackSlotEntry(slot="agent", model="ace-saber")))
        assert report.errors == [("agent", "spawn failed")]
        assert report.loaded == []

    async def test_converge_requires_slot_manager(self) -> None:
        with pytest.raises(RuntimeError):
            await StackApplyEngine().converge(_stack(StackSlotEntry(slot="agent", model="m")))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/halo/dev/wt/stacks-lifecycle && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_converge_primary.py -q`
Expected: FAIL with `ImportError: cannot import name 'ConvergeReport' from 'hal0.stacks.apply'`

- [ ] **Step 3a: Extend the engine imports + constructor**

In `src/hal0/stacks/apply.py`, add to the existing imports (top of file):

```python
from hal0.slots.state import SlotState
```

Replace the existing `__init__` signature/body:

```python
    def __init__(
        self,
        *,
        slots_dir: Path | None = None,
        store: SlotConfigStore | None = None,
    ) -> None:
        self._slots_dir = Path(slots_dir) if slots_dir else None
        self._store = store or SlotConfigStore(slots_dir=slots_dir)
```

with:

```python
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
```

(`Any` is already imported via `from typing import Any`.)

- [ ] **Step 3b: Add `ConvergeReport`, dispatch-state sets, and `converge()` + `_converge_primary`**

In `src/hal0/stacks/apply.py`, add after the `StackChangePlan` dataclass (before the `StackApplyEngine` class):

```python
# Slot states by convergence intent.
_DISPATCHABLE = frozenset({SlotState.READY, SlotState.SERVING, SlotState.IDLE})
_TRANSITIONAL = frozenset(
    {SlotState.PULLING, SlotState.STARTING, SlotState.WARMING, SlotState.UNLOADING}
)


@dataclass
class ConvergeReport:
    """What converge() did, per slot. Failures are recorded, not raised."""

    loaded: list[str]
    swapped: list[str]
    skipped: list[str]
    unloaded: list[str]
    capabilities_applied: list[str]
    errors: list[tuple[str, str]]
```

Then add these methods to `StackApplyEngine` (after the drift methods from PR-2a, at the end of the class):

```python
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

        # Pass 2 — capability children (Task 2)

        # Pass 3 — unload sweep (Task 3)

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/halo/dev/wt/stacks-lifecycle && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_converge_primary.py -q`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
cd /home/halo/dev/wt/stacks-lifecycle
git add tests/stacks/conftest.py src/hal0/stacks/apply.py tests/stacks/test_converge_primary.py
git commit -m "feat(stacks): converge() primary-slot pass (load/swap/skip) + ConvergeReport"
```

---

## Task 2: Capability-child routing pass

**Files:**
- Modify: `src/hal0/stacks/apply.py`
- Test: `tests/stacks/test_converge_capabilities.py`

**Interfaces:**
- Consumes: `RecordingOrchestrator` (T1 conftest); the `converge()` Pass-2 seam (T1).
- Produces: child→group / child→slot-name maps; `async _converge_capabilities(stack, touched, report)`; updates `touched` with enabled rows' underlying slot-names.

- [ ] **Step 1: Write the failing test**

Create `tests/stacks/test_converge_capabilities.py`:

```python
"""Tests for converge() capability-child routing pass.

Targeted file run:
    cd /home/halo/dev/wt/stacks-lifecycle && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_converge_capabilities.py -q
"""

from __future__ import annotations

from hal0.config.schema import StackCapabilityRow, StackConfig, StackSlotEntry
from hal0.stacks.apply import StackApplyEngine
from tests.stacks.conftest import RecordingOrchestrator, RecordingSlotManager


def _engine(orch: RecordingOrchestrator) -> StackApplyEngine:
    return StackApplyEngine(slot_manager=RecordingSlotManager([]), orchestrator=orch)


def _row(child: str, **kw: object) -> StackCapabilityRow:
    base = {"child": child, "device": "npu", "provider": "flm", "model": "bge-m3", "enabled": True}
    base.update(kw)
    return StackCapabilityRow(**base)


class TestCapabilityRouting:
    async def test_embed_row_routes_to_embed_group(self) -> None:
        orch = RecordingOrchestrator()
        stack = StackConfig(name="S", slots=[StackSlotEntry(slot="embed", capabilities=[_row("embed")])])
        report = await _engine(orch).converge(stack)
        assert orch.calls == [("embed", "embed", {"device": "npu", "provider": "flm", "model": "bge-m3", "enabled": True})]
        assert report.capabilities_applied == ["embed/embed"]

    async def test_rerank_routes_to_embed_group(self) -> None:
        orch = RecordingOrchestrator()
        stack = StackConfig(name="S", slots=[StackSlotEntry(slot="rerank", capabilities=[_row("rerank")])])
        await _engine(orch).converge(stack)
        assert orch.calls[0][0] == "embed" and orch.calls[0][1] == "rerank"

    async def test_stt_and_tts_route_to_voice_group(self) -> None:
        orch = RecordingOrchestrator()
        stack = StackConfig(
            name="S",
            slots=[StackSlotEntry(slot="voice", capabilities=[_row("stt"), _row("tts")])],
        )
        await _engine(orch).converge(stack)
        groups = {(c[0], c[1]) for c in orch.calls}
        assert ("voice", "stt") in groups and ("voice", "tts") in groups

    async def test_disabled_row_is_not_applied(self) -> None:
        orch = RecordingOrchestrator()
        stack = StackConfig(name="S", slots=[StackSlotEntry(slot="embed", capabilities=[_row("embed", enabled=False)])])
        report = await _engine(orch).converge(stack)
        assert orch.calls == []
        assert report.capabilities_applied == []

    async def test_unknown_child_is_recorded_as_error(self) -> None:
        orch = RecordingOrchestrator()
        # `child` has no schema validator (any string is accepted), so an
        # unmapped child constructs fine and must be reported, not applied.
        bad = StackCapabilityRow(child="nope", device="npu", provider="flm", model="m")
        stack = StackConfig(name="S", slots=[StackSlotEntry(slot="x", capabilities=[bad])])
        report = await _engine(orch).converge(stack)
        assert orch.calls == []
        assert report.errors and report.errors[0][0] == "capability:nope"

    async def test_apply_failure_is_recorded(self) -> None:
        class Boom(RecordingOrchestrator):
            async def apply(self, slot, child, partial):
                raise RuntimeError("orch down")

        orch = Boom()
        stack = StackConfig(name="S", slots=[StackSlotEntry(slot="embed", capabilities=[_row("embed")])])
        report = await _engine(orch).converge(stack)
        assert report.errors == [("embed/embed", "orch down")]
        assert report.capabilities_applied == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/halo/dev/wt/stacks-lifecycle && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_converge_capabilities.py -q`
Expected: FAIL (capability rows produce no `orch.calls` yet — the Pass-2 seam is still a comment).

- [ ] **Step 3a: Add the child maps**

In `src/hal0/stacks/apply.py`, add after the `_DISPATCHABLE`/`_TRANSITIONAL` sets:

```python
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
```

- [ ] **Step 3b: Wire Pass 2 into `converge` + add `_converge_capabilities`**

In `converge()`, replace the line `        # Pass 2 — capability children (Task 2)` with:

```python
        # Pass 2 — capability children (enabled rows only).
        await self._converge_capabilities(stack, touched, report)
```

Add the helper to `StackApplyEngine` (after `_converge_primary`):

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/halo/dev/wt/stacks-lifecycle && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_converge_capabilities.py -q`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
cd /home/halo/dev/wt/stacks-lifecycle
git add src/hal0/stacks/apply.py tests/stacks/test_converge_capabilities.py
git commit -m "feat(stacks): converge() capability-child routing via orchestrator"
```

---

## Task 3: Declarative unload sweep

**Files:**
- Modify: `src/hal0/stacks/apply.py`
- Test: `tests/stacks/test_converge_unload.py`

**Interfaces:**
- Consumes: the `converge()` Pass-3 seam + `touched` set + `snapshots`; `_DISPATCHABLE` (T1).
- Produces: `async _converge_unload(snapshots, touched, report)` — unload dispatchable slots not in `touched`.

- [ ] **Step 1: Write the failing test**

Create `tests/stacks/test_converge_unload.py`:

```python
"""Tests for converge() declarative unload sweep.

Targeted file run:
    cd /home/halo/dev/wt/stacks-lifecycle && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_converge_unload.py -q
"""

from __future__ import annotations

from hal0.config.schema import StackCapabilityRow, StackConfig, StackSlotEntry
from hal0.slots.state import SlotState
from hal0.stacks.apply import StackApplyEngine
from tests.stacks.conftest import FakeSnap, RecordingOrchestrator, RecordingSlotManager


def _engine(sm: RecordingSlotManager) -> StackApplyEngine:
    return StackApplyEngine(slot_manager=sm, orchestrator=RecordingOrchestrator())


class TestUnloadSweep:
    async def test_running_slot_not_in_stack_is_unloaded(self) -> None:
        sm = RecordingSlotManager(
            [FakeSnap("agent", SlotState.READY, "ace-saber"), FakeSnap("img", SlotState.READY, "flux")]
        )
        stack = StackConfig(name="S", slots=[StackSlotEntry(slot="agent", model="ace-saber")])
        report = await _engine(sm).converge(stack)
        assert ("unload", "img", None) in sm.calls
        assert report.unloaded == ["img"]

    async def test_stack_primary_slot_is_not_unloaded(self) -> None:
        sm = RecordingSlotManager([FakeSnap("agent", SlotState.READY, "ace-saber")])
        stack = StackConfig(name="S", slots=[StackSlotEntry(slot="agent", model="ace-saber")])
        report = await _engine(sm).converge(stack)
        assert report.unloaded == []
        assert not [c for c in sm.calls if c[0] == "unload"]

    async def test_enabled_capability_slot_is_not_unloaded(self) -> None:
        # embed system slot is running; stack enables embed → must NOT be swept.
        sm = RecordingSlotManager([FakeSnap("embed", SlotState.READY, "bge-m3")])
        stack = StackConfig(
            name="S",
            slots=[
                StackSlotEntry(
                    slot="embed",
                    capabilities=[StackCapabilityRow(child="embed", device="npu", provider="flm", model="bge-m3")],
                )
            ],
        )
        report = await _engine(sm).converge(stack)
        assert report.unloaded == []

    async def test_offline_slot_not_in_stack_is_left_alone(self) -> None:
        sm = RecordingSlotManager([FakeSnap("img", SlotState.OFFLINE, None)])
        stack = StackConfig(name="S", slots=[StackSlotEntry(slot="agent", model="ace-saber")])
        report = await _engine(sm).converge(stack)
        assert not [c for c in sm.calls if c[0] == "unload"]
        assert report.unloaded == []

    async def test_unload_failure_is_recorded(self) -> None:
        class Boom(RecordingSlotManager):
            async def unload(self, slot_name):
                raise RuntimeError("stop failed")

        sm = Boom([FakeSnap("img", SlotState.READY, "flux")])
        stack = StackConfig(name="S", slots=[StackSlotEntry(slot="agent", model="ace-saber")])
        report = await _engine(sm).converge(stack)
        assert report.errors == [("img", "stop failed")]
        assert report.unloaded == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/halo/dev/wt/stacks-lifecycle && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_converge_unload.py -q`
Expected: FAIL (`test_running_slot_not_in_stack_is_unloaded` — `img` is never unloaded; the Pass-3 seam is still a comment).

- [ ] **Step 3: Wire Pass 3 into `converge` + add `_converge_unload`**

In `converge()`, replace the line `        # Pass 3 — unload sweep (Task 3)` with:

```python
        # Pass 3 — unload dispatchable slots the stack doesn't touch.
        await self._converge_unload(snapshots, touched, report)
```

Add the helper to `StackApplyEngine` (after `_converge_capabilities`):

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/halo/dev/wt/stacks-lifecycle && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_converge_unload.py -q`
Expected: PASS (5 tests)

- [ ] **Step 5: Run the full PR-2b set + a regression sweep**

Run: `cd /home/halo/dev/wt/stacks-lifecycle && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks -q`
Expected: PASS (PR-1 + PR-2a + the 19 new PR-2b converge tests).

Run: `cd /home/halo/dev/wt/stacks-lifecycle && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/slot_config tests/config -q`
Expected: PASS — confirms PR-2a's reused config path is untouched.

- [ ] **Step 6: Commit**

```bash
cd /home/halo/dev/wt/stacks-lifecycle
git add src/hal0/stacks/apply.py tests/stacks/test_converge_unload.py
git commit -m "feat(stacks): converge() declarative unload sweep"
```

---

## PR-2b Done — Definition of Done

- `converge()` loads offline/missing primary slots, swaps dispatchable ones with a different model, skips matches + transitional, and records per-slot failures without raising.
- Enabled capability rows route to `orchestrator.apply(group, child, …)` via the child maps; disabled rows skipped.
- Dispatchable slots not in the stack (and not capability-touched) are unloaded; offline/transitional left alone.
- ~19 new tests pass; full `tests/stacks` green; `tests/slot_config` + `tests/config` unregressed.
- No config writes, REST, MCP, or drift changes (those are PR-2a/PR-3/PR-4).

## Next — PR-4 (REST + MCP) will wire the whole apply

`POST /api/stacks/{slug}/apply?dry_run=true` → `plan()` (diff preview). `POST …/apply` → `apply_config(plan)` then `converge(stack)` then `record_active(plan, applied_at=…)`, injecting `app.state.slot_manager` + `app.state.capability_orchestrator`. Also the follow-ups parked from PR-2a (catalog `Protocol` typing, narrow `except` to `NotFound`, `apply_config→record_active` docstring contract).
