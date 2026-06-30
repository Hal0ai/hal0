# Stacks — PR-2a: Config Apply + Dry-Run + Drift — Implementation Plan

> **Status: Implemented / Shipped (v0.8.x).** `StackApplyEngine` (apply_config + dry-run + drift_status) landed in `src/hal0/stacks/apply.py` + `state.py`. (#923) Plan retained for historical context.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Translate a `StackConfig` into one atomic, reversible config change over the stack's slot TOMLs — with a compute-only dry-run preview, commit via the existing `SlotConfigStore` rollback machinery, and an active-stack pointer + content-hash drift status (`clean`/`modified`/`none`).

**Architecture:** A new `StackApplyEngine` (`src/hal0/stacks/apply.py`) reconciles each stack slot entry onto its `slots/<slot>.toml` and assembles a single `ChangeSet` (the verified `FileState`/`ChangeSet` primitives from `hal0.slot_config`). `commit()`/`revert()` are reused unchanged — they operate on any `ChangeSet` with per-file rollback. Drift uses a `StackStateRecord` at `/var/lib/hal0/stacks/state.json`, mirroring the slot `write_state_atomic` pattern, plus a sha256 content hash over the canonical slot-TOML projection.

**Tech Stack:** Python 3.12, Pydantic v2, `tomllib`/`tomli_w`, pytest (`asyncio_mode = "auto"`). No new dependencies.

**Stacked on:** PR #921 (`feat/stacks-spec`). This branch is `feat/stacks-apply-engine`; its PR targets `feat/stacks-spec` until #921 merges, then retarget to `main`.

## Global Constraints

- **Scope of THIS PR (2a):** config translation + dry-run + commit + active-pointer/drift ONLY. **No slot lifecycle** (no `SlotManager.load/swap/unload`) — that is PR-2b. **No capability-child handling** (embed/stt/tts/rerank/vision rows) — those route through `orchestrator.apply` in PR-2b. 2a applies only stack slot entries that carry a primary `model` and whose `slots/<slot>.toml` already exists (slot *creation* has port/state side effects owned by `SlotManager` — out of scope).
- **Reuse, don't reinvent:** assemble `hal0.slot_config.ChangeSet` from `FileState`, and commit/revert via `hal0.slot_config.SlotConfigStore` — never hand-roll atomic writes. Device translation goes through `hal0.model_meta.canonical_device` + `device_to_legacy_backend` (write both the v0.2 `device` and the one-release-legacy `backend`).
- **Compute-only dry-run:** `plan()` writes NOTHING to disk; `before` snapshots must byte-match disk.
- **Atomic commit:** `commit()` writes `after` states with per-file rollback on partial failure (reused from `SlotConfigStore.commit`). Disk is never left half-reconciled.
- **Drift hash:** canonical serialization is `json.dumps(obj, sort_keys=True, default=str)` then sha256 hex — matching the repo convention (`slots/state.py:278`, `content_hash` in `agents/hermes_provision.py`). Keyed by slot name (portable across machines), not path.
- **Paths:** HAL0_HOME-aware via `hal0.config.paths` — never hardcode `/etc` or `/var/lib`. Slot TOMLs at `paths.slots_config_dir()/<slot>.toml`; stack state at `paths.var_lib()/"stacks"/"state.json"`.
- **Errors:** raise `hal0.errors.NotFound`/`Conflict` under `stacks.*` codes where a resource is addressed; let `OSError`/`TypeError` from the writer propagate (the REST layer in PR-4 wraps them).
- **Test runner:** `cd /home/halo/dev/wt/stacks-apply && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest <path> -q`. Filesystem isolation via the existing `tmp_hal0_home` fixture (root `tests/conftest.py`). `asyncio_mode="auto"` is set, but 2a has no async code.
- **Conventions:** test files `tests/stacks/test_*.py`; classes `Test<Feature>`; functions `test_<behavior>`; plain `assert`; seed TOMLs into `Path(home)/"etc"/"hal0"/...` before acting (per `tests/slot_config/test_store.py`).

## File Structure

| File | Responsibility | Action |
| --- | --- | --- |
| `src/hal0/stacks/apply.py` | `StackApplyEngine` — `plan()` (Stack→ChangeSet), `apply_config()` (commit), `_reconciled_stack_slot()`; `StackChangePlan` result. | Create |
| `src/hal0/stacks/state.py` | `StackStateRecord` + `write_stack_state_atomic`/`read_stack_state` + `stack_content_hash`; active-pointer read/write. | Create |
| `src/hal0/config/paths.py` | `stacks_state_path()` helper. | Modify (after `stacks_toml()`) |
| `tests/stacks/test_apply_plan.py` | dry-run / ChangeSet before-after / reconciliation tests. | Create |
| `tests/stacks/test_apply_commit.py` | commit, idempotency, rollback. | Create |
| `tests/stacks/test_drift.py` | state record round-trip, hashing, `clean`/`modified`/`none`. | Create |

---

## Task 1: Stack → ChangeSet translation + dry-run (`plan()`)

**Files:**
- Create: `src/hal0/stacks/apply.py`
- Test: `tests/stacks/test_apply_plan.py`

**Interfaces:**
- Consumes: `hal0.slot_config.{ChangeSet, FileState, SlotConfigStore}`; `hal0.config.paths.slots_config_dir`; `hal0.model_meta.{canonical_device, device_to_legacy_backend}`; `hal0.config.schema.StackConfig`/`StackSlotEntry` (PR-1).
- Produces:
  - `StackChangePlan` (frozen dataclass): `stack_slug: str`, `change_set: ChangeSet`, `summary: list[str]`.
  - `StackApplyEngine(*, slots_dir: Path | None = None, store: SlotConfigStore | None = None)` with `plan(slug: str, stack: StackConfig) -> StackChangePlan`.

- [ ] **Step 1: Write the failing test**

Create `tests/stacks/test_apply_plan.py`:

```python
"""Unit tests for StackApplyEngine.plan() — compute-only Stack→ChangeSet.

Targeted file run:
    cd /home/halo/dev/wt/stacks-apply && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_apply_plan.py -q
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from hal0.config.schema import StackConfig, StackSlotEntry
from hal0.slot_config import ChangeSet
from hal0.stacks.apply import StackApplyEngine, StackChangePlan


def _slots_dir(home: str) -> Path:
    d = Path(home) / "etc" / "hal0" / "slots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_agent_slot(home: str) -> Path:
    path = _slots_dir(home) / "agent.toml"
    path.write_text(
        "\n".join(
            [
                'name = "agent"',
                "port = 8087",
                'device = "gpu-vulkan"',
                'provider = "llama-server"',
                "enabled = true",
                'vision = false',
                "[model]",
                'default = "old-model"',
                'context_size = 8192',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _read(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _stack() -> StackConfig:
    return StackConfig(
        name="Saber",
        slots=[
            StackSlotEntry(
                slot="agent",
                model="chadrock-35b-ace-saber",
                device="gpu-rocm",
                vision=True,
            )
        ],
    )


class TestPlanComputeOnly:
    def test_plan_writes_nothing(self, tmp_hal0_home: str) -> None:
        slot_path = _write_agent_slot(tmp_hal0_home)
        before_bytes = slot_path.read_bytes()
        engine = StackApplyEngine()
        plan = engine.plan("saber", _stack())
        assert isinstance(plan, StackChangePlan)
        assert isinstance(plan.change_set, ChangeSet)
        assert slot_path.read_bytes() == before_bytes, "plan() must not touch disk"

    def test_before_matches_disk(self, tmp_hal0_home: str) -> None:
        slot_path = _write_agent_slot(tmp_hal0_home)
        plan = StackApplyEngine().plan("saber", _stack())
        by_path = {fs.path: fs.data for fs in plan.change_set.before}
        assert by_path[slot_path] == _read(slot_path)


class TestReconciliation:
    def test_after_sets_model_device_backend_vision(self, tmp_hal0_home: str) -> None:
        slot_path = _write_agent_slot(tmp_hal0_home)
        plan = StackApplyEngine().plan("saber", _stack())
        after = {fs.path: fs.data for fs in plan.change_set.after}[slot_path]
        assert after["model"]["default"] == "chadrock-35b-ace-saber"
        assert after["model"]["context_size"] == 8192, "sibling [model] keys must survive deep-merge"
        assert after["device"] == "gpu-rocm"
        assert after["backend"] == "rocm", "legacy backend alias written via model_meta"
        assert after["vision"] is True

    def test_changed_true_when_model_differs(self, tmp_hal0_home: str) -> None:
        _write_agent_slot(tmp_hal0_home)
        assert StackApplyEngine().plan("saber", _stack()).change_set.changed is True

    def test_missing_slot_file_is_skipped(self, tmp_hal0_home: str) -> None:
        # No agent.toml on disk → slot creation is out of 2a scope → after == before (None).
        _slots_dir(tmp_hal0_home)  # dir exists, file does not
        plan = StackApplyEngine().plan("saber", _stack())
        assert plan.change_set.changed is False
        assert all(fs.data is None for fs in plan.change_set.before)

    def test_summary_lists_changed_slot(self, tmp_hal0_home: str) -> None:
        _write_agent_slot(tmp_hal0_home)
        plan = StackApplyEngine().plan("saber", _stack())
        assert any("agent" in line for line in plan.summary)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/halo/dev/wt/stacks-apply && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_apply_plan.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'hal0.stacks.apply'`

- [ ] **Step 3: Implement `apply.py`**

Create `src/hal0/stacks/apply.py`:

```python
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


class StackApplyEngine:
    """Reconcile a StackConfig onto slot TOMLs as one ChangeSet."""

    def __init__(
        self,
        *,
        slots_dir: Path | None = None,
        store: SlotConfigStore | None = None,
    ) -> None:
        self._slots_dir = Path(slots_dir) if slots_dir else None
        self._store = store or SlotConfigStore(slots_dir=slots_dir)

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
    def _summarize(
        slot: str, before: dict[str, Any] | None, after: dict[str, Any] | None
    ) -> str:
        b_model = (before or {}).get("model", {}).get("default") if before else None
        a_model = (after or {}).get("model", {}).get("default") if after else None
        if b_model != a_model:
            return f"{slot}: model {b_model or '∅'} → {a_model or '∅'}"
        return f"{slot}: config updated"

    # ── apply (commit) — Task 2 ──────────────────────────────────────────────
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/halo/dev/wt/stacks-apply && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_apply_plan.py -q`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
cd /home/halo/dev/wt/stacks-apply
git add src/hal0/stacks/apply.py tests/stacks/test_apply_plan.py
git commit -m "feat(stacks): StackApplyEngine.plan() — compute-only Stack→ChangeSet"
```

---

## Task 2: Commit (`apply_config`) + rollback

**Files:**
- Modify: `src/hal0/stacks/apply.py` (replace the trailing `# ── apply (commit) — Task 2 ──` comment with the method)
- Test: `tests/stacks/test_apply_commit.py`

**Interfaces:**
- Consumes: `StackChangePlan` + `SlotConfigStore.commit` (Task 1).
- Produces: `StackApplyEngine.apply_config(plan: StackChangePlan) -> None` — commits `plan.change_set` atomically (delegates to the store's rollback-on-failure commit).

- [ ] **Step 1: Write the failing test**

Create `tests/stacks/test_apply_commit.py`:

```python
"""Unit tests for StackApplyEngine.apply_config() — atomic commit + rollback.

Targeted file run:
    cd /home/halo/dev/wt/stacks-apply && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_apply_commit.py -q
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

from hal0.config.schema import StackConfig, StackSlotEntry
from hal0.stacks.apply import StackApplyEngine


def _slots_dir(home: str) -> Path:
    d = Path(home) / "etc" / "hal0" / "slots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_slot(home: str, name: str, model: str) -> Path:
    path = _slots_dir(home) / f"{name}.toml"
    path.write_text(
        "\n".join(
            [f'name = "{name}"', "port = 8087", 'device = "gpu-vulkan"', "[model]", f'default = "{model}"', ""]
        ),
        encoding="utf-8",
    )
    return path


def _read(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _stack(*entries: StackSlotEntry) -> StackConfig:
    return StackConfig(name="S", slots=list(entries))


class TestCommit:
    def test_commit_writes_after(self, tmp_hal0_home: str) -> None:
        path = _write_slot(tmp_hal0_home, "agent", "old")
        engine = StackApplyEngine()
        plan = engine.plan("s", _stack(StackSlotEntry(slot="agent", model="new")))
        engine.apply_config(plan)
        assert _read(path)["model"]["default"] == "new"

    def test_commit_is_idempotent(self, tmp_hal0_home: str) -> None:
        _write_slot(tmp_hal0_home, "agent", "old")
        engine = StackApplyEngine()
        engine.apply_config(engine.plan("s", _stack(StackSlotEntry(slot="agent", model="new"))))
        # Re-planning against the now-applied disk yields no change.
        assert engine.plan("s", _stack(StackSlotEntry(slot="agent", model="new"))).change_set.changed is False


class TestRollback:
    def test_failed_commit_rolls_back(self, tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch) -> None:
        import hal0.slot_config as slot_config_mod

        a_path = _write_slot(tmp_hal0_home, "agent", "old-a")
        c_path = _write_slot(tmp_hal0_home, "chat", "old-c")
        a_before, c_before = _read(a_path), _read(c_path)

        engine = StackApplyEngine()
        plan = engine.plan(
            "s",
            _stack(
                StackSlotEntry(slot="agent", model="new-a"),
                StackSlotEntry(slot="chat", model="new-c"),
            ),
        )
        real_write = slot_config_mod.write_toml_atomic

        def _boom_on_chat(path: Path | str, data: dict[str, Any]) -> None:
            if Path(path).name == "chat.toml":
                raise OSError("disk full")
            real_write(path, data)

        monkeypatch.setattr(slot_config_mod, "write_toml_atomic", _boom_on_chat)
        with pytest.raises(OSError):
            engine.apply_config(plan)
        monkeypatch.setattr(slot_config_mod, "write_toml_atomic", real_write)

        assert _read(a_path) == a_before, "agent.toml must roll back to before"
        assert _read(c_path) == c_before, "chat.toml never written"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/halo/dev/wt/stacks-apply && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_apply_commit.py -q`
Expected: FAIL with `AttributeError: 'StackApplyEngine' object has no attribute 'apply_config'`

- [ ] **Step 3: Implement `apply_config`**

In `src/hal0/stacks/apply.py`, replace the trailing comment line `    # ── apply (commit) — Task 2 ──────────────────────────────────────────────` with:

```python
    # ── apply (commit) ───────────────────────────────────────────────────────

    def apply_config(self, plan: StackChangePlan) -> None:
        """Commit ``plan.change_set`` to disk atomically.

        Delegates to ``SlotConfigStore.commit``: each file is written
        tmpfile+fsync+rename; a mid-set failure restores every already-written
        file to its ``before`` snapshot and re-raises — disk is never left
        half-reconciled. A no-op ChangeSet (nothing changed) writes nothing.
        """
        self._store.commit(plan.change_set)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/halo/dev/wt/stacks-apply && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_apply_commit.py -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
cd /home/halo/dev/wt/stacks-apply
git add src/hal0/stacks/apply.py tests/stacks/test_apply_commit.py
git commit -m "feat(stacks): StackApplyEngine.apply_config() — atomic commit with rollback"
```

---

## Task 3: Active-stack pointer + drift detection

**Files:**
- Create: `src/hal0/stacks/state.py`
- Modify: `src/hal0/config/paths.py` (add `stacks_state_path()` after `stacks_toml()`)
- Modify: `src/hal0/stacks/apply.py` (add `record_active()` + `drift_status()` to the engine)
- Test: `tests/stacks/test_drift.py`

**Interfaces:**
- Consumes: `StackChangePlan` (Task 1); `hal0.config.paths.var_lib`; `hal0.stacks.StacksCatalog`/`StackConfig` (PR-1).
- Produces:
  - `paths.stacks_state_path() -> Path` → `var_lib() / "stacks" / "state.json"`.
  - `state.py`: `StackStateRecord(active_slug, content_hash, applied_at)`, `write_stack_state_atomic(path, record)`, `read_stack_state(path) -> StackStateRecord | None`, `stack_content_hash(projection: dict[str, dict | None]) -> str`.
  - `StackApplyEngine.record_active(plan, *, applied_at: float) -> None` and `drift_status(catalog) -> dict` returning `{"active": slug | None, "status": "clean" | "modified" | "none"}`.

- [ ] **Step 1: Write the failing test**

Create `tests/stacks/test_drift.py`:

```python
"""Unit tests for the active-stack pointer + drift detection.

Targeted file run:
    cd /home/halo/dev/wt/stacks-apply && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_drift.py -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.config import paths
from hal0.config.schema import StackConfig, StackSlotEntry
from hal0.stacks import StacksCatalog
from hal0.stacks.apply import StackApplyEngine
from hal0.stacks.state import (
    StackStateRecord,
    read_stack_state,
    stack_content_hash,
    write_stack_state_atomic,
)


def _slots_dir(home: str) -> Path:
    d = Path(home) / "etc" / "hal0" / "slots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_slot(home: str, name: str, model: str) -> Path:
    path = _slots_dir(home) / f"{name}.toml"
    path.write_text("\n".join([f'name = "{name}"', "port = 8087", "[model]", f'default = "{model}"', ""]), encoding="utf-8")
    return path


def _saber() -> StackConfig:
    return StackConfig(name="Saber", slots=[StackSlotEntry(slot="agent", model="ace-saber")])


class TestStateRecord:
    def test_round_trip(self, tmp_hal0_home: str) -> None:
        p = paths.stacks_state_path()
        rec = StackStateRecord(active_slug="saber", content_hash="abc123", applied_at=1.5)
        write_stack_state_atomic(p, rec)
        got = read_stack_state(p)
        assert got is not None
        assert got.active_slug == "saber"
        assert got.content_hash == "abc123"

    def test_read_missing_returns_none(self, tmp_hal0_home: str) -> None:
        assert read_stack_state(paths.stacks_state_path()) is None


class TestContentHash:
    def test_stable_and_order_independent(self) -> None:
        a = stack_content_hash({"agent": {"model": {"default": "x"}}, "chat": {"model": {"default": "y"}}})
        b = stack_content_hash({"chat": {"model": {"default": "y"}}, "agent": {"model": {"default": "x"}}})
        assert a == b, "hash must be key-order independent"

    def test_changes_with_content(self) -> None:
        assert stack_content_hash({"agent": {"model": {"default": "x"}}}) != stack_content_hash(
            {"agent": {"model": {"default": "z"}}}
        )


class TestDriftStatus:
    def test_no_pointer_is_none(self, tmp_hal0_home: str) -> None:
        catalog = StacksCatalog(path=Path(tmp_hal0_home) / "etc" / "hal0" / "stacks.toml")
        assert StackApplyEngine().drift_status(catalog) == {"active": None, "status": "none"}

    def test_clean_right_after_apply(self, tmp_hal0_home: str) -> None:
        _write_slot(tmp_hal0_home, "agent", "old")
        catalog = StacksCatalog(path=Path(tmp_hal0_home) / "etc" / "hal0" / "stacks.toml")
        catalog.create("saber", _saber())
        engine = StackApplyEngine()
        plan = engine.plan("saber", _saber())
        engine.apply_config(plan)
        engine.record_active(plan, applied_at=1.0)
        assert engine.drift_status(catalog) == {"active": "saber", "status": "clean"}

    def test_modified_after_hand_edit(self, tmp_hal0_home: str) -> None:
        slot_path = _write_slot(tmp_hal0_home, "agent", "old")
        catalog = StacksCatalog(path=Path(tmp_hal0_home) / "etc" / "hal0" / "stacks.toml")
        catalog.create("saber", _saber())
        engine = StackApplyEngine()
        plan = engine.plan("saber", _saber())
        engine.apply_config(plan)
        engine.record_active(plan, applied_at=1.0)
        # Hand-edit the slot after applying → drift.
        slot_path.write_text(slot_path.read_text() + '\nrole = "primary"\n', encoding="utf-8")
        assert engine.drift_status(catalog)["status"] == "modified"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/halo/dev/wt/stacks-apply && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_drift.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'hal0.stacks.state'`

- [ ] **Step 3a: Add the path helper**

In `src/hal0/config/paths.py`, after `stacks_toml()`, add:

```python
def stacks_state_path() -> Path:
    """Return the active-stack pointer path (/var/lib/hal0/stacks/state.json).

    Records which stack is currently applied + a content hash for drift
    detection. HAL0_HOME-aware via :func:`var_lib`.
    """
    return var_lib() / "stacks" / "state.json"
```

- [ ] **Step 3b: Create `state.py`**

Create `src/hal0/stacks/state.py`:

```python
"""Active-stack pointer + content hashing for drift detection (spec §7).

Mirrors the slot state pattern (``hal0.slots.state.write_state_atomic``): a
JSON record written tmpfile+fsync+rename so readers never see a torn file.
The content hash fingerprints the slot-TOML projection a stack applied, so a
later hand-edit can be detected as drift (``clean`` vs ``modified``).
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StackStateRecord:
    """Which stack is applied, and the hash of what it wrote."""

    active_slug: str
    content_hash: str
    applied_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_slug": self.active_slug,
            "content_hash": self.content_hash,
            "applied_at": self.applied_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StackStateRecord:
        return cls(
            active_slug=str(data.get("active_slug", "")),
            content_hash=str(data.get("content_hash", "")),
            applied_at=float(data.get("applied_at", 0.0)),
        )


def stack_content_hash(projection: dict[str, dict[str, Any] | None]) -> str:
    """sha256 over the canonical slot→TOML-dict projection.

    Canonical serialization is ``json.dumps(sort_keys=True)`` so the hash is
    independent of dict key order (repo convention: slots/state.py, content_hash
    in agents/hermes_provision.py). Keyed by slot name → portable across hosts.
    """
    payload = json.dumps(projection, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def write_stack_state_atomic(path: Path | str, record: StackStateRecord) -> None:
    """Persist the active-stack pointer atomically (tmpfile + fsync + replace)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(record.to_dict(), indent=2, sort_keys=True) + "\n"

    tmp_path: Path | None = None
    try:
        fd, tmp_str = tempfile.mkstemp(prefix=".hal0-stack-state-", suffix=".tmp", dir=path.parent)
        tmp_path = Path(tmp_str)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            with suppress(OSError):
                os.close(fd)
            raise
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            with suppress(OSError):
                tmp_path.unlink(missing_ok=True)


def read_stack_state(path: Path | str) -> StackStateRecord | None:
    """Read the active-stack pointer, or ``None`` when no stack is applied."""
    try:
        with open(path, encoding="utf-8") as f:
            return StackStateRecord.from_dict(json.load(f))
    except FileNotFoundError:
        return None
```

- [ ] **Step 3c: Add `record_active` + `drift_status` to the engine**

In `src/hal0/stacks/apply.py`, add these imports to the existing import block:

```python
from hal0.stacks.state import (
    StackStateRecord,
    read_stack_state,
    stack_content_hash,
    write_stack_state_atomic,
)
```

Then add these methods to `StackApplyEngine` (after `apply_config`):

```python
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
```

Note: `drift_status` builds a `StackConfig` from the resolved stack's `slots` only (the fields `_projection_live` needs), keeping it independent of the catalog's exact return type.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/halo/dev/wt/stacks-apply && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks/test_drift.py -q`
Expected: PASS (7 tests)

- [ ] **Step 5: Run the full PR-2a set + a regression sweep**

Run: `cd /home/halo/dev/wt/stacks-apply && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/stacks -q`
Expected: PASS (PR-1's catalog/schema/loader tests + the 16 new 2a tests).

Run: `cd /home/halo/dev/wt/stacks-apply && PYTHONPATH=src ~/dev/hal0/.venv/bin/python -m pytest tests/slot_config tests/config -q`
Expected: PASS — confirms the reused `SlotConfigStore` path and config schema are untouched.

- [ ] **Step 6: Commit**

```bash
cd /home/halo/dev/wt/stacks-apply
git add src/hal0/stacks/state.py src/hal0/config/paths.py src/hal0/stacks/apply.py tests/stacks/test_drift.py
git commit -m "feat(stacks): active-stack pointer + content-hash drift detection"
```

---

## PR-2a Done — Definition of Done

- `StackApplyEngine.plan()` produces a compute-only `ChangeSet` (writes nothing; `before` byte-matches disk).
- `apply_config()` commits atomically with rollback (reusing `SlotConfigStore.commit`); idempotent re-apply is a no-op.
- Reconciliation deep-merges `[model]`/`[server]`, writes `device` + legacy `backend`, honors declarative `vision`, and skips absent slot files.
- Active-stack pointer persists atomically; `drift_status` returns `none`/`clean`/`modified` correctly.
- 16 new tests pass; PR-1 + `tests/slot_config` + `tests/config` suites unregressed.

## Next — PR-2b (lifecycle convergence)

Separate plan, written after a focused extraction of the `SlotManager` test harness (how existing tests fake/stub container spawns). Scope: `StackApplyEngine.converge(stack)` async — `load`/`swap` each primary stack slot to its model, route capability-child rows through `CapabilityOrchestrator.apply` (NPU-trio aware), and `unload` running slots not named in the stack (declarative replace). PR-4 (REST) then wires `apply = apply_config + converge` behind the dry-run/commit endpoints.
