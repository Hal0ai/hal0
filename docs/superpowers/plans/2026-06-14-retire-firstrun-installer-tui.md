# Retire FirstRun → `hal0 setup` TUI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the web FirstRun picker with a `rich`-based `hal0 setup` TUI that reuses the existing install backend, adds a selectable Extensions step, and keeps the slot roster coherent whether run before or after `hal0-api` is up.

**Architecture:** Lift the `/api/install/apply` orchestration body into a deps-injected `install/orchestrate.apply_setup()` so it runs in-process (install time) or behind the HTTP route (post-install). A new `hal0 setup` Typer command renders a two-column rich layout (selection + always-on context pane), gates slot steps on Extension picks, and drives `apply_setup` — in-process when the API is unreachable, via `POST /api/install/apply` when it's up. Then delete the web picker and the dead v1 bundles surface.

**Tech Stack:** Python 3.11–3.14, Typer, `rich` (Layout/Table/Panel/Prompt/Live), pytest, FastAPI (existing route), bash (install.sh).

**Spec:** `docs/superpowers/specs/2026-06-14-retire-firstrun-fold-installer-tui-design.md`

**Conventions (read once):**
- Tests run with `PYTHONPATH=src pytest <path> -v` (per memory `hal0_activity_audit_store_pr795`).
- The repo uses `ruff check` **and** a separate `ruff format --check` step in CI — run both before each commit (memory `feedback_hal0_ci_ruff_format_check`).
- Slot config writes go through `hal0.slot_config.write_slot_toml` only (issue #697).
- Branch for this work: `feat/hal0-setup-tui` (create from `main`).

---

## File Structure

**New files:**
- `src/hal0/install/orchestrate.py` — `apply_setup()` + `Selections`/`SlotSelection`/`SetupResult`/`SlotOutcome`/`PullPlan` dataclasses. The single mutation entrypoint.
- `src/hal0/install/suggest.py` — `suggest_models(capability, hw, *, limit)` → ranked `Suggestion` list. Generalizes `hardware/recommend.py`.
- `src/hal0/install/extensions.py` — `Extension` dataclass, `EXTENSIONS` registry, `install_extension()`.
- `src/hal0/cli/setup_command.py` — the `hal0 setup` Typer command + step machine.
- `src/hal0/cli/setup_copy.py` — per-step context-pane copy (data only).
- `src/hal0/cli/setup_ui.py` — rich rendering helpers (two-column shell, checklist, suggestion table, live progress).
- Tests: `tests/install/test_orchestrate.py`, `tests/install/test_suggest.py`, `tests/install/test_extensions.py`, `tests/cli/test_setup_command.py`, `tests/cli/test_setup_ui.py`.

**Modified:**
- `src/hal0/api/routes/installer.py` — `install_apply` route becomes a thin wrapper over `apply_setup`.
- `src/hal0/cli/main.py` — register the `setup` command.
- `installer/install.sh` — replace models-dir prompt + single-slot probe block with `hal0 setup --auto`.

**Deleted (Phase 6):** `ui/src/dash/firstrun.jsx`, `ui/src/api/hooks/useFirstRun.ts`, `ui/src/dash/install-state-bridge.ts`, FirstRun bits of `ui/src/dash/main.jsx` + `ui/src/api/endpoints.ts` + `ui/src/dash/data.jsx`, `ui/tests/e2e/specs/firstrun-v2.spec.ts` + `firstrun-v3.spec.ts`, `src/hal0/api/routes/bundles.py`, `src/hal0/bundles/store.py`, the `/pick-default` + `/slots/{slot}/model` routes, `tests/api/test_bundles_route.py`.

---

# PHASE 0 — `apply_setup` (the importable orchestration core)

> Unblocks everything. After this, the HTTP route and the TUI both call one function.

## Task 0.1: Define the orchestration data types

**Files:**
- Create: `src/hal0/install/orchestrate.py`
- Test: `tests/install/test_orchestrate.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/install/test_orchestrate.py
from hal0.install.orchestrate import (
    Selections, SlotSelection, SlotOutcome, SetupResult, PullPlan,
)


def test_selections_roundtrip():
    sel = Selections(
        storage_dir="/var/lib/hal0/models",
        slots=[SlotSelection(capability="chat", slot_name="chat", port=8081,
                             model_id="qwen3-4b")],
        extensions={"openwebui": True, "hermes": True, "pi": False},
        npu_opt_in=False,
    )
    assert sel.slots[0].model_id == "qwen3-4b"
    assert sel.slots[0].device is None  # derived later
    assert sel.extensions["pi"] is False


def test_setup_result_shape():
    res = SetupResult(slots=[SlotOutcome(slot="chat", model_id="qwen3-4b")],
                      extensions=[], model_ids=[], pulls=[])
    assert res.slots[0].created is False
    assert res.slots[0].skipped is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/install/test_orchestrate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hal0.install.orchestrate'`

- [ ] **Step 3: Write the dataclasses**

```python
# src/hal0/install/orchestrate.py
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
    capability: str           # "chat" | "coder"
    slot_name: str            # "chat" | "coder"
    port: int
    model_id: str
    device: str | None = None   # explicit override; None → derive from hw
    profile: str | None = None  # explicit override; None → derive from device


@dataclass(frozen=True)
class Selections:
    """The full set of first-run choices to apply."""
    storage_dir: str
    slots: list[SlotSelection]
    extensions: dict[str, bool]   # extension id -> enabled
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
    job: Any           # registry.pull.PullJob
    kwargs: dict[str, Any]


@dataclass
class SetupResult:
    slots: list[SlotOutcome]
    extensions: list[ExtensionOutcome]
    model_ids: list[str]
    pulls: list[PullPlan] = field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/install/test_orchestrate.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
ruff check src/hal0/install/orchestrate.py && ruff format --check src/hal0/install/orchestrate.py
git add src/hal0/install/orchestrate.py tests/install/test_orchestrate.py
git commit -m "feat(install): orchestration data types for apply_setup"
```

## Task 0.2: Implement `apply_setup` (slot creation + pull planning)

**Files:**
- Modify: `src/hal0/install/orchestrate.py`
- Test: `tests/install/test_orchestrate.py`

- [ ] **Step 1: Write the failing test** (fakes for slot_manager + registry; monkeypatch curated + jobs)

```python
# tests/install/test_orchestrate.py  (append)
import pytest
from hal0.config.schema import HardwareInfo, GPUInfo, NPUInfo


class _FakeSlotManager:
    def __init__(self): self.created = {}
    async def create(self, name, cfg): self.created[name] = cfg; return object()


def _strix_hw():
    return HardwareInfo(
        platform="strix-halo", ram_mb=98304, ram_available_mb=90000,
        unified_memory_mb=98304,
        gpus=[GPUInfo(vendor="amd", vram_mb=512, compute_capable=True,
                      vulkan_capable=True)],
        npu=NPUInfo(present=True),
    )


@pytest.mark.asyncio
async def test_apply_setup_creates_chat_slot_and_plans_pull():
    from hal0.install import orchestrate
    sm = _FakeSlotManager()
    jobs: dict = {}
    sel = Selections(
        storage_dir="/var/lib/hal0/models",
        slots=[SlotSelection(capability="chat", slot_name="chat", port=8081,
                             model_id="qwen3-4b")],
        extensions={}, npu_opt_in=False,
    )
    res = await orchestrate.apply_setup(
        sel, hardware=_strix_hw(), slot_manager=sm, registry={}, jobs=jobs,
        write_sentinel=False,
    )
    assert sm.created["chat"]["device"] == "gpu-rocm"
    assert sm.created["chat"]["profile"] == "rocm-mtp"
    out = res.slots[0]
    assert out.created is True and out.skipped is None
    assert "qwen3-4b" in res.model_ids
    assert len(res.pulls) == 1 and res.pulls[0].model_id == "qwen3-4b"


@pytest.mark.asyncio
async def test_apply_setup_skips_uncurated_model():
    from hal0.install import orchestrate
    sel = Selections(storage_dir="/x",
                     slots=[SlotSelection("chat", "chat", 8081, "does-not-exist")],
                     extensions={}, npu_opt_in=False)
    res = await orchestrate.apply_setup(
        sel, hardware=_strix_hw(), slot_manager=_FakeSlotManager(),
        registry={}, jobs={}, write_sentinel=False)
    assert res.slots[0].skipped == "needs_upstream_routing"
    assert res.slots[0].created is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/install/test_orchestrate.py -k apply_setup -v`
Expected: FAIL — `AttributeError: module 'hal0.install.orchestrate' has no attribute 'apply_setup'`

- [ ] **Step 3: Implement `apply_setup`** (port of installer.py:527-585, deps injected)

```python
# src/hal0/install/orchestrate.py  (add imports at top)
from hal0.config.schema import HardwareInfo
from hal0.install.profile_derive import derive_device, derive_profile
from hal0.registry.curated import get_curated
from hal0.registry.pull import make_job, get_job


def _build_slot_cfg(*, slot, model_id, device, profile, port, context_size=4096):
    """Podman-aware slot config dict (device+profile, NOT backend — #807)."""
    return {
        "name": slot, "port": port, "device": device, "profile": profile,
        "enabled": True,
        "model": {"default": model_id, "context_size": context_size},
    }


def _ensure_registry_entry(registry, model_id) -> None:
    """No-op shim if the registry already knows the id; create a stub otherwise.
    Mirrors installer.py's ``_ensure_registry_entry``; the real registry object
    exposes the same surface. A plain dict (tests) is tolerated."""
    if hasattr(registry, "ensure"):
        registry.ensure(model_id)


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
        device = s.device or derive_device(s.capability, hardware,
                                           npu_opt_in=selections.npu_opt_in)
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
        cfg = _build_slot_cfg(slot=s.slot_name, model_id=s.model_id,
                              device=device, profile=profile, port=s.port,
                              context_size=ctx)
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
            pulls.append(PullPlan(model_id=s.model_id, job=job, kwargs=dict(
                hf_repo=curated.hf_repo, hf_file=curated.hf_file,
                registry=registry, hf_token=hf_token,
                comfyui_subdir=curated.comfyui_subdir or None,
                capability=s.capability,
            )))
        rec.pull_job_id = job.job_id
        model_ids.append(s.model_id)
        slot_outcomes.append(rec)

    ext_outcomes = _install_extensions(selections.extensions)  # Task 0.3

    if write_sentinel:
        from hal0.install.orchestrate import mark_first_run_done
        mark_first_run_done()

    return SetupResult(slots=slot_outcomes, extensions=ext_outcomes,
                       model_ids=model_ids, pulls=pulls)
```

Add a stub so this imports before Task 0.3:

```python
def _install_extensions(extensions: dict) -> list[ExtensionOutcome]:
    return []  # filled in Task 0.3
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/install/test_orchestrate.py -k apply_setup -v`
Expected: PASS (2 passed). If `pytest-asyncio` mode errors, add `asyncio_mode = auto` under `[tool.pytest.ini_options]` in `pyproject.toml` (it is already used by `tests/api/test_install_apply.py` — copy its marker style).

- [ ] **Step 5: Commit**

```bash
ruff check src/hal0/install/ && ruff format --check src/hal0/install/
git add src/hal0/install/orchestrate.py tests/install/test_orchestrate.py
git commit -m "feat(install): apply_setup creates slots + plans pulls (port of /apply)"
```

## Task 0.3: Extension install + sentinel helper

**Files:**
- Modify: `src/hal0/install/orchestrate.py`
- Test: `tests/install/test_orchestrate.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/install/test_orchestrate.py  (append)
def test_mark_first_run_done_writes_sentinel(tmp_path, monkeypatch):
    from hal0.install import orchestrate
    sentinel = tmp_path / ".first_run_done"
    monkeypatch.setattr(orchestrate, "_sentinel_path", lambda: sentinel)
    orchestrate.mark_first_run_done()
    assert sentinel.exists()


def test_install_extensions_dispatches(monkeypatch):
    from hal0.install import orchestrate
    calls = []
    monkeypatch.setattr(orchestrate, "install_extension",
                        lambda ext_id: calls.append(ext_id) or
                        orchestrate.ExtensionOutcome(ext_id=ext_id, installed=True))
    outs = orchestrate._install_extensions({"openwebui": True, "pi": False, "hermes": True})
    assert set(calls) == {"openwebui", "hermes"}      # only enabled
    assert all(o.installed for o in outs)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/install/test_orchestrate.py -k "sentinel or dispatches" -v`
Expected: FAIL — `AttributeError: ... 'mark_first_run_done'` / `'_sentinel_path'`

- [ ] **Step 3: Implement sentinel + extension dispatch** (replace the Task 0.2 stub)

```python
# src/hal0/install/orchestrate.py  (add)
from pathlib import Path
from hal0.config import paths as hal0_paths


def _sentinel_path() -> Path:
    """``/var/lib/hal0/.first_run_done`` (same path installer.py uses)."""
    return Path(hal0_paths.var_lib_dir()) / ".first_run_done"


def mark_first_run_done() -> None:
    p = _sentinel_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text("")
    tmp.replace(p)   # atomic


def install_extension(ext_id: str) -> "ExtensionOutcome":
    """Install + wire one extension. Delegates to extensions.install_extension
    (Task 1.2); imported lazily to avoid a cycle."""
    from hal0.install.extensions import install_extension as _do
    return _do(ext_id)
```

Replace the stub:

```python
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
```

> Note: confirm `hal0.config.paths` exposes `var_lib_dir()`. If the helper is named differently (e.g. `paths.var_lib()` or a `VAR_LIB` constant), use that — grep `src/hal0/config/paths.py` for the `/var/lib/hal0` base. installer.py's `_first_run_sentinel()` (lines ~100-103) shows the exact accessor to reuse.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/install/test_orchestrate.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
ruff check src/hal0/install/ && ruff format --check src/hal0/install/
git add src/hal0/install/orchestrate.py tests/install/test_orchestrate.py
git commit -m "feat(install): sentinel write + extension dispatch in apply_setup"
```

## Task 0.4: Rewire the HTTP route to call `apply_setup`

**Files:**
- Modify: `src/hal0/api/routes/installer.py:482-592`
- Test: `tests/api/test_install_apply.py` (existing — must stay green)

- [ ] **Step 1: Run the existing route tests to capture the baseline**

Run: `PYTHONPATH=src pytest tests/api/test_install_apply.py -v`
Expected: PASS (record the count — this is the contract we must preserve).

- [ ] **Step 2: Replace the route body with a thin wrapper**

Keep `_resolve_tier`, `_SLOT_META`, and the manifest load (the route still speaks "tier"; the TUI speaks "slots"). Convert the manifest → `Selections`, call `apply_setup` with `write_sentinel=False`, then fire the planned pulls via `background.add_task`. Replace lines 499-592 with:

```python
    try:
        body = await request.json()
    except Exception as exc:
        raise PickDefaultError(f"body must be valid JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise PickDefaultError("body must be a JSON object")
    tier = body.get("tier")
    if not isinstance(tier, str) or not tier.strip():
        raise PickDefaultError("body.tier is required (non-empty string)")
    npu_opt_in = bool(body.get("npu_opt_in", False))
    overrides = body.get("overrides") or {}
    if not isinstance(overrides, dict):
        raise PickDefaultError("body.overrides must be an object")

    canonical = _resolve_tier(tier.strip())
    bundle = bundle_tiers.load_bundle(canonical).bundle

    selections = _bundle_to_selections(bundle, overrides, npu_opt_in,
                                       storage_dir=body.get("storage_dir") or "")
    hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    result = await apply_setup(
        selections,
        hardware=request.app.state.hardware_probe.probe(),
        slot_manager=request.app.state.slot_manager,
        registry=request.app.state.model_registry,
        jobs=request.app.state.model_pull_jobs,
        hf_token=hf_token,
        write_sentinel=False,   # the dashboard still POSTs /complete explicitly
    )
    for plan in result.pulls:
        background.add_task(run_pull, plan.job, **plan.kwargs)

    return {
        "tier": canonical,
        "model_ids": result.model_ids,
        "slots": [vars(s) for s in result.slots],
        "next": "reattach /api/models/{id}/pull/stream per model_id",
    }
```

Add the manifest→selections adapter near `_SLOT_META`:

```python
def _bundle_to_selections(bundle, overrides, npu_opt_in, *, storage_dir):
    from hal0.install.orchestrate import Selections, SlotSelection
    slots = []
    for entry in [e for e in (bundle.primary, bundle.coder, *bundle.aux) if e]:
        cap, slot_name, port = _SLOT_META.get(entry.slot, (entry.slot, entry.slot, 8090))
        ov = overrides.get(slot_name) if isinstance(overrides.get(slot_name), dict) else {}
        slots.append(SlotSelection(
            capability=cap, slot_name=slot_name, port=port,
            model_id=ov.get("model_id") or entry.model_name,
            device=ov.get("device"), profile=ov.get("profile"),
        ))
    return Selections(storage_dir=storage_dir, slots=slots,
                      extensions={}, npu_opt_in=npu_opt_in)
```

Add the import at the top of `installer.py`:

```python
from hal0.install.orchestrate import apply_setup
```

- [ ] **Step 3: Run the route tests + the new orchestrate tests**

Run: `PYTHONPATH=src pytest tests/api/test_install_apply.py tests/install/test_orchestrate.py -v`
Expected: PASS — same route test count as Step 1, plus orchestrate tests. If a route test asserts on the response `slots[*]` keys, note `vars(SlotOutcome)` yields `slot/model_id/created/device/profile/pull_job_id/skipped/error` (a superset of the old dict — old keys preserved).

- [ ] **Step 4: Commit**

```bash
ruff check src/hal0/api/routes/installer.py && ruff format --check src/hal0/api/routes/installer.py
git add src/hal0/api/routes/installer.py
git commit -m "refactor(api): /install/apply delegates to install.orchestrate.apply_setup"
```

---

# PHASE 1 — `suggest.py` + `extensions.py`

## Task 1.1: `suggest_models` (hardware → ranked curated picks)

**Files:**
- Create: `src/hal0/install/suggest.py`
- Test: `tests/install/test_suggest.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/install/test_suggest.py
from hal0.install.suggest import suggest_models, Suggestion
from hal0.config.schema import HardwareInfo, GPUInfo, NPUInfo


def _hw(ram_gb, *, amd=True, npu=True, compute=True):
    return HardwareInfo(
        platform="strix-halo" if amd else "generic",
        ram_mb=int(ram_gb * 1024), ram_available_mb=int(ram_gb * 1024 * 0.9),
        unified_memory_mb=int(ram_gb * 1024) if amd else 0,
        gpus=[GPUInfo(vendor="amd" if amd else "intel", vram_mb=512,
                      compute_capable=compute, vulkan_capable=True)],
        npu=NPUInfo(present=npu),
    )


def test_chat_suggestions_fit_ram_and_rank():
    out = suggest_models("chat", _hw(96), limit=3)
    assert out and isinstance(out[0], Suggestion)
    assert all(s.vram_gb_min <= 96 for s in out)        # only fitting picks
    assert sum(1 for s in out if s.recommended) == 1     # exactly one starred
    # largest-that-fits is recommended (descending vram_gb_min order)
    assert out[0].recommended


def test_low_ram_box_excludes_big_models():
    out = suggest_models("chat", _hw(8), limit=5)
    assert all(s.vram_gb_min <= 8 for s in out)


def test_coder_capability_filters_to_coder_models():
    out = suggest_models("coder", _hw(96), limit=3)
    assert out, "expected at least one coder pick"
    assert all(s.capability in ("coder", "chat") for s in out)


def test_excludes_bundle_only_entries():
    out = suggest_models("chat", _hw(96), limit=20)
    assert all(not s.bundle_only for s in out)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/install/test_suggest.py -v`
Expected: FAIL — `ModuleNotFoundError: hal0.install.suggest`

- [ ] **Step 3: Implement `suggest.py`**

```python
# src/hal0/install/suggest.py
"""Hardware-driven curated-model suggestions per capability (spec §6.5).

Generalizes ``hardware/recommend.py`` (single primary pick) into a ranked
list per capability, for the ``hal0 setup`` slot steps. Picks come only from
``registry.curated.CURATED_MODELS`` so they validate against the registry as
soon as downloaded — we never invent model names.
"""

from __future__ import annotations

from dataclasses import dataclass

from hal0.config.schema import HardwareInfo
from hal0.install.profile_derive import derive_device, derive_profile
from hal0.registry.curated import CURATED_MODELS, CuratedModel


@dataclass(frozen=True)
class Suggestion:
    model_id: str
    display_name: str
    size_gb: float
    vram_gb_min: float
    context_length: int
    device: str | None
    profile: str | None
    capability: str
    bundle_only: bool
    recommended: bool = False


#: Which curated ``capability`` values satisfy a slot capability. "coder"
#: falls back to general chat models so a coder slot is never empty.
_CAP_MATCH: dict[str, tuple[str, ...]] = {
    "chat": ("chat",),
    "coder": ("coder", "chat"),
    "embed": ("embed",),
    "stt": ("asr",),
    "tts": ("tts",),
}


def _ram_gb(hw: HardwareInfo) -> float:
    return (hw.unified_memory_mb or hw.ram_mb) / 1024


def _is_coder(m: CuratedModel) -> bool:
    return m.capability == "coder" or "coder" in m.tags or "coder" in m.id.lower()


def suggest_models(capability: str, hw: HardwareInfo, *, limit: int = 3,
                   prefer_coder: bool = False) -> list[Suggestion]:
    """Return up to ``limit`` curated picks for ``capability`` that fit the
    detected RAM, largest-first, with exactly one marked ``recommended``."""
    wanted = _CAP_MATCH.get(capability, (capability,))
    ram = _ram_gb(hw)
    device = derive_device(capability, hw, npu_opt_in=True)
    profile = derive_profile(capability, device) if device else None

    cands = [
        m for m in CURATED_MODELS
        if not m.bundle_only
        and m.capability in wanted
        and m.vram_gb_min <= ram + 0.01
    ]
    if capability == "coder" and prefer_coder:
        cands.sort(key=lambda m: (not _is_coder(m), -m.vram_gb_min))
    else:
        cands.sort(key=lambda m: -m.vram_gb_min)   # largest-that-fits first

    picks = cands[:limit]
    return [
        Suggestion(
            model_id=m.id, display_name=m.display_name, size_gb=m.size_gb,
            vram_gb_min=m.vram_gb_min, context_length=m.context_length,
            device=device, profile=profile, capability=m.capability,
            bundle_only=m.bundle_only, recommended=(i == 0),
        )
        for i, m in enumerate(picks)
    ]


__all__ = ["Suggestion", "suggest_models"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/install/test_suggest.py -v`
Expected: PASS. If `test_coder_capability_filters` fails because no curated entry has `capability="coder"`, fall back is `chat` (allowed by the assertion). If `test_chat_suggestions_fit_ram_and_rank` finds 0 picks, verify `CURATED_MODELS` has chat entries with `vram_gb_min <= 96` (it does — e.g. `qwen3-4b` at 0.0).

- [ ] **Step 5: Commit**

```bash
ruff check src/hal0/install/suggest.py && ruff format --check src/hal0/install/suggest.py
git add src/hal0/install/suggest.py tests/install/test_suggest.py
git commit -m "feat(install): suggest_models — ranked curated picks per capability"
```

## Task 1.2: `extensions.py` registry + installer

**Files:**
- Create: `src/hal0/install/extensions.py`
- Test: `tests/install/test_extensions.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/install/test_extensions.py
import pytest
from hal0.install.extensions import (
    Extension, EXTENSIONS, list_extensions, get_extension, install_extension,
)
from hal0.install.orchestrate import ExtensionOutcome


def test_registry_has_grouped_extensions():
    apps = list_extensions(kind="app")
    agents = list_extensions(kind="agent")
    assert any(e.id == "openwebui" for e in apps)
    assert {e.id for e in agents} >= {"hermes", "pi"}
    assert get_extension("openwebui").default_enabled is True
    assert get_extension("pi").default_enabled is False


def test_get_unknown_extension_returns_none():
    assert get_extension("nope") is None


def test_install_agent_runs_hal0_agent_install(monkeypatch):
    ran = []
    monkeypatch.setattr("hal0.install.extensions._run",
                        lambda *a, **k: ran.append(a[0]))
    out = install_extension("hermes")
    assert isinstance(out, ExtensionOutcome) and out.installed is True
    assert any("agent" in c and "install" in c and "hermes" in c for c in ran)


def test_install_unknown_extension_skips():
    out = install_extension("nope")
    assert out.installed is False and out.skipped == "unknown_extension"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/install/test_extensions.py -v`
Expected: FAIL — `ModuleNotFoundError: hal0.install.extensions`

- [ ] **Step 3: Implement `extensions.py`**

```python
# src/hal0/install/extensions.py
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
    Extension("openwebui", "app", "Open WebUI",
              "Chat web UI for your models", True),
    Extension("hermes", "agent", "Hermes",
              "Conversational agent with memory", True),
    Extension("pi", "agent", "Pi",
              "Coding agent", False),
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
        return ExtensionOutcome(ext_id=ext_id, installed=True)
    except Exception as exc:  # best-effort
        return ExtensionOutcome(ext_id=ext_id, error=str(exc))


__all__ = ["Extension", "EXTENSIONS", "list_extensions", "get_extension",
           "install_extension"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/install/test_extensions.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
ruff check src/hal0/install/extensions.py && ruff format --check src/hal0/install/extensions.py
git add src/hal0/install/extensions.py tests/install/test_extensions.py
git commit -m "feat(install): Extensions registry (Apps/Agents) + install dispatch"
```

---

# PHASE 2 — `hal0 setup` skeleton (routing + `--auto`)

## Task 2.1: API-reachability probe + command skeleton

**Files:**
- Create: `src/hal0/cli/setup_command.py`
- Modify: `src/hal0/cli/main.py`
- Test: `tests/cli/test_setup_command.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/cli/test_setup_command.py
from hal0.cli.setup_command import _api_reachable, build_auto_selections
from hal0.config.schema import HardwareInfo, GPUInfo, NPUInfo


def _hw(ram_gb=96):
    return HardwareInfo(platform="strix-halo", ram_mb=ram_gb * 1024,
                        ram_available_mb=ram_gb * 1024,
                        unified_memory_mb=ram_gb * 1024,
                        gpus=[GPUInfo(vendor="amd", vram_mb=512,
                                      compute_capable=True, vulkan_capable=True)],
                        npu=NPUInfo(present=True))


def test_api_reachable_false_on_connection_error(monkeypatch):
    def boom(*a, **k): raise OSError("refused")
    monkeypatch.setattr("hal0.cli.setup_command.httpx.get", boom)
    assert _api_reachable(timeout=0.01) is False


def test_auto_selections_pick_recommended_and_default_extensions():
    sel = build_auto_selections(_hw(96), storage_dir="/var/lib/hal0/models")
    chat = next(s for s in sel.slots if s.slot_name == "chat")
    assert chat.model_id           # a recommended model id was chosen
    assert sel.extensions["openwebui"] is True
    assert sel.extensions["hermes"] is True
    assert sel.extensions["pi"] is False
    # an agent is enabled by default → agent slot is seeded
    assert any(s.slot_name == "coder" for s in sel.slots)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/cli/test_setup_command.py -v`
Expected: FAIL — `ModuleNotFoundError: hal0.cli.setup_command`

- [ ] **Step 3: Implement the skeleton**

```python
# src/hal0/cli/setup_command.py
"""`hal0 setup` — first-run configuration TUI (spec §6).

Hybrid execution: in-process ``apply_setup`` when hal0-api is unreachable
(install time), through ``POST /api/install/apply`` when it is up (so the
running service registers the new slots without a restart — roster coherence,
spec §11)."""

from __future__ import annotations

import asyncio

import httpx
import typer

from hal0.cli._shared import _api_base
from hal0.config.schema import HardwareInfo
from hal0.hardware.probe import HardwareProbe
from hal0.install.extensions import EXTENSIONS
from hal0.install.orchestrate import Selections, SlotSelection, apply_setup
from hal0.install.suggest import suggest_models

#: capability → (slot_name, port). Mirrors installer.py:_SLOT_META for the
#: two slots first-run provisions.
_SETUP_SLOTS = {"chat": ("chat", 8081), "coder": ("coder", 8082)}


def _api_reachable(timeout: float = 0.5) -> bool:
    try:
        r = httpx.get(f"{_api_base()}/api/install/state", timeout=timeout)
        return r.status_code < 500
    except Exception:
        return False


def build_auto_selections(hw: HardwareInfo, *, storage_dir: str) -> Selections:
    """Non-interactive defaults for ``--auto`` (install.sh path): recommended
    model per slot, default extension set, NPU trio on if present."""
    ext = {e.id: e.default_enabled for e in EXTENSIONS}
    slots: list[SlotSelection] = []
    # Main (chat) is always provisioned in --auto (OWUI + Hermes default on).
    chat = suggest_models("chat", hw, limit=1)
    if chat:
        name, port = _SETUP_SLOTS["chat"]
        slots.append(SlotSelection("chat", name, port, chat[0].model_id))
    # Agent slot only if an agent extension is enabled.
    if any(get_kind(eid) == "agent" and on for eid, on in ext.items()):
        coder = suggest_models("coder", hw, limit=1, prefer_coder=True)
        if coder:
            name, port = _SETUP_SLOTS["coder"]
            slots.append(SlotSelection("coder", name, port, coder[0].model_id))
    return Selections(storage_dir=storage_dir, slots=slots, extensions=ext,
                      npu_opt_in=bool(hw.npu.present))


def get_kind(ext_id: str) -> str | None:
    from hal0.install.extensions import get_extension
    e = get_extension(ext_id)
    return e.kind if e else None


app = typer.Typer(help="First-run setup")


@app.callback(invoke_without_command=True)
def setup(
    auto: bool = typer.Option(False, "--auto", help="Non-interactive; recommended defaults."),
    storage_dir: str = typer.Option("/var/lib/hal0/models", "--storage-dir"),
) -> None:
    hw = HardwareProbe().probe()
    if auto:
        sel = build_auto_selections(hw, storage_dir=storage_dir)
        asyncio.run(_run_auto(sel, hw))
        return
    from hal0.cli.setup_ui import run_interactive   # Task 3.x
    run_interactive(hw, storage_dir=storage_dir)


async def _run_auto(sel: Selections, hw: HardwareInfo) -> None:
    """In-process apply for the install.sh path (api is not up yet)."""
    from hal0.slots.manager import SlotManager
    from hal0.registry.registry import load_registry   # adjust to real loader

    sm = SlotManager.from_disk() if hasattr(SlotManager, "from_disk") else SlotManager()
    result = await apply_setup(sel, hardware=hw, slot_manager=sm,
                               registry=load_registry(), jobs={},
                               write_sentinel=True)
    for plan in result.pulls:
        from hal0.registry.pull import run_pull
        await run_pull(plan.job, **plan.kwargs)
    typer.echo(f"hal0 setup complete: {len(result.model_ids)} model(s), "
               f"{sum(1 for s in result.slots if s.created)} slot(s).")
```

> The `SlotManager`/`load_registry` construction in `_run_auto` is the one place that needs the real install-time accessors. Grep `installer/install.sh` stage 8 + `src/hal0/api/app.py` for how `app.state.slot_manager` and `app.state.model_registry` are built, and mirror that construction here. This is wired concretely in Task 5.1's integration test.

- [ ] **Step 4: Register the command in `main.py`**

In `src/hal0/cli/main.py`, near the other `app.add_typer(...)` calls (lines ~56-67):

```python
from hal0.cli import setup_command
app.add_typer(setup_command.app, name="setup", help="First-run setup")
```

- [ ] **Step 5: Run tests + smoke the command wiring**

Run: `PYTHONPATH=src pytest tests/cli/test_setup_command.py -v`
Expected: PASS
Run: `PYTHONPATH=src python -m hal0.cli.main setup --help`
Expected: prints help including `--auto` and `--storage-dir`.

- [ ] **Step 6: Commit**

```bash
ruff check src/hal0/cli/setup_command.py src/hal0/cli/main.py && ruff format --check src/hal0/cli/setup_command.py
git add src/hal0/cli/setup_command.py src/hal0/cli/main.py tests/cli/test_setup_command.py
git commit -m "feat(cli): hal0 setup skeleton — api routing + --auto selections"
```

---

# PHASE 3 — Selection steps (two-column shell + gating)

## Task 3.1: Context-pane copy + two-column layout shell

**Files:**
- Create: `src/hal0/cli/setup_copy.py`
- Create: `src/hal0/cli/setup_ui.py`
- Test: `tests/cli/test_setup_ui.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/cli/test_setup_ui.py
from rich.console import Console
from hal0.cli.setup_ui import render_shell
from hal0.cli.setup_copy import PANE_COPY


def test_pane_copy_has_every_step():
    for key in ("welcome", "storage", "extensions", "main", "agent", "npu",
                "review", "install"):
        assert key in PANE_COPY and PANE_COPY[key].body


def test_render_shell_includes_step_and_pane_text():
    con = Console(width=100, record=True)
    con.print(render_shell(step_key="extensions", left_body="PICK APPS HERE",
                           hw_footer="Strix Halo · 96GB · NPU"))
    text = con.export_text()
    assert "PICK APPS HERE" in text
    assert "one-shot" in text.lower()        # extensions pane headline copy
    assert "Strix Halo" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src pytest tests/cli/test_setup_ui.py -v`
Expected: FAIL — `ModuleNotFoundError: hal0.cli.setup_copy`

- [ ] **Step 3: Implement copy + shell**

```python
# src/hal0/cli/setup_copy.py
"""Per-step context-pane copy (spec §6.1). Data only — no logic."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PaneCopy:
    headline: str
    body: str


PANE_COPY: dict[str, PaneCopy] = {
    "welcome": PaneCopy("Welcome to hal0",
        "We detected your hardware and tuned the defaults on the left. "
        "Press Enter to continue."),
    "storage": PaneCopy("Where models live",
        "Downloaded models are stored here. Pick a disk with room — chat "
        "models run 2-30 GB each."),
    "extensions": PaneCopy("One-shot perfection",
        "Every app and agent you pick is automagically wired into the hal0 "
        "platform during install — base URLs, routing, and credentials "
        "configured for you. No glue code, no post-install fiddling."),
    "main": PaneCopy("Your Main model",
        "The primary model every app and agent routes to (hal0/primary). "
        "We recommend the largest pick that fits your memory."),
    "agent": PaneCopy("The Agent model",
        "Powers your coding/agent extensions. Pick a coder model, reuse your "
        "Main model, or skip."),
    "npu": PaneCopy("Free up your GPU",
        "Your NPU can run embeddings, speech-to-text, and text-to-speech in "
        "parallel — leaving the GPU for chat. Recommended when present."),
    "review": PaneCopy("Ready to build",
        "Here's exactly what will be created and wired. Nothing has been "
        "written yet."),
    "install": PaneCopy("Building your hal0",
        "Slots are created instantly; models download in the background — you "
        "can start chatting as soon as the Main model lands."),
}
```

```python
# src/hal0/cli/setup_ui.py
"""rich rendering for `hal0 setup` (spec §6.1): a two-column shell redrawn per
step. Left = the step body; right = the always-on context pane."""

from __future__ import annotations

from rich.layout import Layout
from rich.panel import Panel
from rich.console import Group, RenderableType
from rich.text import Text

from hal0.cli.setup_copy import PANE_COPY


def render_shell(*, step_key: str, left_body: RenderableType,
                 hw_footer: str) -> Panel:
    """Two-column Panel: left step body, right context pane + hw footer."""
    copy = PANE_COPY[step_key]
    pane = Group(
        Text(f"✦ {copy.headline}", style="bold amber1"),
        Text(""),
        Text(copy.body),
        Text(""),
        Text(f"Detected: {hw_footer}", style="dim"),
    )
    layout = Layout()
    layout.split_row(
        Layout(Panel(left_body, border_style="amber1"), ratio=3, name="step"),
        Layout(Panel(pane, border_style="dim"), ratio=2, name="pane"),
    )
    return Panel(layout, title="hal0 setup", border_style="amber1",
                 height=22)
```

> `amber1` is rich's nearest named color to the hal0 brand `#feaf00` (memory `hal0_brand_wordmark`). If you want exact brand color, use `style="#feaf00"`.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src pytest tests/cli/test_setup_ui.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
ruff check src/hal0/cli/setup_copy.py src/hal0/cli/setup_ui.py && ruff format --check src/hal0/cli/setup_copy.py src/hal0/cli/setup_ui.py
git add src/hal0/cli/setup_copy.py src/hal0/cli/setup_ui.py tests/cli/test_setup_ui.py
git commit -m "feat(cli): setup TUI two-column shell + context-pane copy"
```

## Task 3.2: Checklist + suggestion-table widgets

**Files:**
- Modify: `src/hal0/cli/setup_ui.py`
- Test: `tests/cli/test_setup_ui.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/cli/test_setup_ui.py  (append)
from hal0.cli.setup_ui import render_extension_checklist, render_suggestion_table
from hal0.install.extensions import EXTENSIONS
from hal0.install.suggest import Suggestion


def test_extension_checklist_marks_enabled():
    state = {"openwebui": True, "hermes": True, "pi": False}
    r = render_extension_checklist(EXTENSIONS, state, cursor=0)
    from rich.console import Console
    con = Console(width=80, record=True); con.print(r)
    text = con.export_text()
    assert "Open WebUI" in text and "Hermes" in text and "Pi" in text
    assert "Apps" in text and "Agents" in text


def test_suggestion_table_stars_recommended():
    sugg = [Suggestion("qwen3-4b", "Qwen3 4B", 2.4, 0.0, 32768, "gpu-rocm",
                       "rocm-mtp", "chat", False, recommended=True)]
    from rich.console import Console
    con = Console(width=80, record=True); con.print(render_suggestion_table(sugg))
    assert "Qwen3 4B" in con.export_text()
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src pytest tests/cli/test_setup_ui.py -k "checklist or suggestion" -v`
Expected: FAIL — `ImportError: cannot import name 'render_extension_checklist'`

- [ ] **Step 3: Implement the widgets**

```python
# src/hal0/cli/setup_ui.py  (append)
from rich.table import Table


def render_extension_checklist(extensions, state: dict, cursor: int) -> RenderableType:
    """Grouped Apps/Agents checklist. ``state`` maps id→bool; ``cursor`` is the
    highlighted row index across the flat ordered list."""
    rows = []
    grouped: dict[str, list] = {"app": [], "agent": []}
    for e in extensions:
        grouped[e.kind].append(e)
    flat = grouped["app"] + grouped["agent"]
    lines = []
    idx = 0
    for label, kind in (("Apps", "app"), ("Agents", "agent")):
        lines.append(Text(label, style="bold"))
        for e in grouped[kind]:
            mark = "[x]" if state.get(e.id) else "[ ]"
            arrow = "›" if idx == cursor else " "
            style = "bold amber1" if idx == cursor else ""
            lines.append(Text(f" {arrow} {mark} {e.name:<12} {e.summary}", style=style))
            idx += 1
    lines.append(Text(""))
    lines.append(Text("↑↓ move · space toggle · enter confirm", style="dim"))
    return Group(*lines)


def render_suggestion_table(suggestions) -> RenderableType:
    t = Table(expand=True)
    t.add_column(" ", width=2)
    t.add_column("Model"); t.add_column("Size", justify="right")
    t.add_column("Ctx", justify="right"); t.add_column("Backend")
    for s in suggestions:
        star = "★" if s.recommended else " "
        t.add_row(star, s.display_name, f"{s.size_gb:.1f}GB",
                  f"{s.context_length or '—'}", s.profile or "—")
    return t
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=src pytest tests/cli/test_setup_ui.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
ruff check src/hal0/cli/setup_ui.py && ruff format --check src/hal0/cli/setup_ui.py
git add src/hal0/cli/setup_ui.py tests/cli/test_setup_ui.py
git commit -m "feat(cli): extension checklist + suggestion table widgets"
```

## Task 3.3: The interactive step machine + gating

**Files:**
- Modify: `src/hal0/cli/setup_ui.py` (add `run_interactive` + `plan_steps`)
- Test: `tests/cli/test_setup_ui.py`

- [ ] **Step 1: Write the failing test** (test the *gating* pure-function, not the I/O loop)

```python
# tests/cli/test_setup_ui.py  (append)
from hal0.cli.setup_ui import plan_steps


def test_no_agent_skips_agent_step():
    steps = plan_steps(extensions={"openwebui": True, "hermes": False, "pi": False},
                       npu_present=True)
    assert "agent" not in steps
    assert "main" in steps        # OWUI on → main shown


def test_agent_on_shows_agent_and_main():
    steps = plan_steps(extensions={"openwebui": False, "hermes": True, "pi": False},
                       npu_present=True)
    assert "main" in steps and "agent" in steps   # agent routes to main too


def test_nothing_consuming_chat_hides_main():
    steps = plan_steps(extensions={"openwebui": False, "hermes": False, "pi": False},
                       npu_present=False)
    assert "main" not in steps and "agent" not in steps and "npu" not in steps


def test_no_npu_skips_npu_step():
    steps = plan_steps(extensions={"openwebui": True}, npu_present=False)
    assert "npu" not in steps
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src pytest tests/cli/test_setup_ui.py -k plan_steps -v`
Expected: FAIL — `ImportError: cannot import name 'plan_steps'`

- [ ] **Step 3: Implement `plan_steps` (gating) — spec §6.3**

```python
# src/hal0/cli/setup_ui.py  (append)
from hal0.install.extensions import get_extension


def _any_agent(extensions: dict) -> bool:
    return any(on and (get_extension(eid) and get_extension(eid).kind == "agent")
               for eid, on in extensions.items())


def plan_steps(*, extensions: dict, npu_present: bool) -> list[str]:
    """Ordered list of step keys to show, gated on extension picks (spec §6.3).
    Main shows whenever OWUI OR any agent is enabled; Agent shows iff any agent
    is enabled; NPU shows iff hardware present."""
    steps = ["welcome", "storage", "extensions"]
    needs_main = extensions.get("openwebui") or _any_agent(extensions)
    if needs_main:
        steps.append("main")
    if _any_agent(extensions):
        steps.append("agent")
    if npu_present:
        steps.append("npu")
    steps += ["review", "install"]
    return steps
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=src pytest tests/cli/test_setup_ui.py -k plan_steps -v`
Expected: PASS

- [ ] **Step 5: Implement `run_interactive` (the I/O loop)** — not unit-tested (terminal I/O); exercised by manual smoke + the Phase 5 harness.

```python
# src/hal0/cli/setup_ui.py  (append)
import asyncio
from rich.console import Console
from rich.prompt import Prompt, Confirm
from hal0.hardware.probe import HardwareInfo
from hal0.install.extensions import EXTENSIONS
from hal0.install.orchestrate import Selections, SlotSelection
from hal0.install.suggest import suggest_models

_con = Console()


def _hw_footer(hw: HardwareInfo) -> str:
    ram = int((hw.unified_memory_mb or hw.ram_mb) / 1024)
    npu = "NPU ready" if hw.npu.present else "no NPU"
    return f"{hw.platform} · {ram}GB · {npu}"


def _draw(step_key: str, left, hw: HardwareInfo) -> None:
    _con.clear()
    _con.print(render_shell(step_key=step_key, left_body=left,
                            hw_footer=_hw_footer(hw)))


def _choose_model(step_key, capability, hw, *, prefer_coder=False):
    sugg = suggest_models(capability, hw, limit=3, prefer_coder=prefer_coder)
    if not sugg:
        return None
    _draw(step_key, render_suggestion_table(sugg), hw)
    default = next((str(i + 1) for i, s in enumerate(sugg) if s.recommended), "1")
    choice = Prompt.ask("Pick a model", choices=[str(i + 1) for i in range(len(sugg))],
                        default=default)
    return sugg[int(choice) - 1]


def run_interactive(hw: HardwareInfo, *, storage_dir: str) -> None:
    # Step: welcome
    _draw("welcome", "Detected hardware shown on the right.", hw)
    Prompt.ask("Press Enter to begin", default="")
    # Step: storage
    _draw("storage", f"Default: {storage_dir}", hw)
    storage_dir = Prompt.ask("Model storage directory", default=storage_dir)
    # Step: extensions (checklist loop)
    state = {e.id: e.default_enabled for e in EXTENSIONS}
    _toggle_extensions(state, hw)
    # Gated steps
    steps = plan_steps(extensions=state, npu_present=bool(hw.npu.present))
    slots: list[SlotSelection] = []
    if "main" in steps:
        m = _choose_model("main", "chat", hw)
        if m:
            slots.append(SlotSelection("chat", "chat", 8081, m.model_id))
    if "agent" in steps:
        a = _choose_model("agent", "coder", hw, prefer_coder=True)
        if a:
            slots.append(SlotSelection("coder", "coder", 8082, a.model_id))
    npu_opt_in = False
    if "npu" in steps:
        _draw("npu", "Run embed + STT + TTS on the NPU?", hw)
        npu_opt_in = Confirm.ask("Enable NPU trio?", default=True)
    # Step: review
    sel = Selections(storage_dir=storage_dir, slots=slots, extensions=state,
                     npu_opt_in=npu_opt_in)
    _draw("review", _review_table(sel), hw)
    if not Confirm.ask("Build now?", default=True):
        _con.print("Aborted — nothing was written.")
        return
    # Step: install (Task 4.1)
    from hal0.cli.setup_install import run_install
    asyncio.run(run_install(sel, hw))


def _toggle_extensions(state: dict, hw: HardwareInfo) -> None:
    """Minimal space-to-toggle loop using readchar if available, else a
    numbered prompt fallback (works without raw-tty access, e.g. over a pipe)."""
    flat = [e for e in EXTENSIONS]
    while True:
        _draw("extensions", render_extension_checklist(EXTENSIONS, state, cursor=-1), hw)
        ans = Prompt.ask("Toggle by number (comma-separated) or Enter to confirm",
                         default="")
        if not ans.strip():
            return
        for tok in ans.split(","):
            tok = tok.strip()
            if tok.isdigit() and 1 <= int(tok) <= len(flat):
                eid = flat[int(tok) - 1].id
                state[eid] = not state[eid]


def _review_table(sel: Selections):
    t = Table(title="Will create", expand=True)
    t.add_column("Slot"); t.add_column("Model"); t.add_column("Extensions")
    enabled = ", ".join(k for k, v in sel.extensions.items() if v)
    for i, s in enumerate(sel.slots):
        t.add_row(s.slot_name, s.model_id, enabled if i == 0 else "")
    return t
```

> The `_toggle_extensions` numbered-fallback keeps the TUI usable where raw-tty isn't available. A `readchar`-based arrow/space variant can replace it later without changing `plan_steps` — keep the gating logic (the tested part) separate from input.

- [ ] **Step 6: Smoke-test the interactive path manually**

Run: `PYTHONPATH=src python -m hal0.cli.main setup`
Expected: walks welcome → storage → extensions → main → agent → npu → review; aborts cleanly at the final confirm without writing slots. (Verify no `/etc/hal0/slots/*.toml` were created when you abort.)

- [ ] **Step 7: Commit**

```bash
ruff check src/hal0/cli/setup_ui.py && ruff format --check src/hal0/cli/setup_ui.py
git add src/hal0/cli/setup_ui.py tests/cli/test_setup_ui.py
git commit -m "feat(cli): interactive step machine + extension-gated step planning"
```

---

# PHASE 4 — Install step (Live progress)

## Task 4.1: `run_install` with rich Live progress

**Files:**
- Create: `src/hal0/cli/setup_install.py`
- Test: `tests/cli/test_setup_install.py`

- [ ] **Step 1: Write the failing test** (drive with a fake apply_setup result; assert hybrid routing decision is pure)

```python
# tests/cli/test_setup_install.py
import pytest
from hal0.cli.setup_install import choose_apply_mode


def test_mode_in_process_when_api_down(monkeypatch):
    monkeypatch.setattr("hal0.cli.setup_install._api_reachable", lambda **k: False)
    assert choose_apply_mode() == "in_process"


def test_mode_api_when_up(monkeypatch):
    monkeypatch.setattr("hal0.cli.setup_install._api_reachable", lambda **k: True)
    assert choose_apply_mode() == "api"
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=src pytest tests/cli/test_setup_install.py -v`
Expected: FAIL — `ModuleNotFoundError: hal0.cli.setup_install`

- [ ] **Step 3: Implement `run_install`**

```python
# src/hal0/cli/setup_install.py
"""The install step: hybrid apply + rich Live download progress (spec §6.7)."""

from __future__ import annotations

import asyncio

import httpx
from rich.console import Console
from rich.progress import Progress, BarColumn, TextColumn, DownloadColumn

from hal0.cli._shared import _api_base
from hal0.cli.setup_command import _api_reachable
from hal0.config.schema import HardwareInfo
from hal0.install.orchestrate import Selections, apply_setup
from hal0.registry.pull import run_pull

_con = Console()


def choose_apply_mode() -> str:
    """'in_process' when hal0-api is down (install time), 'api' when up
    (post-install — go through the route so the live service registers slots
    without a restart; spec §11)."""
    return "api" if _api_reachable() else "in_process"


async def run_install(sel: Selections, hw: HardwareInfo) -> None:
    if choose_apply_mode() == "api":
        await _apply_via_api(sel)
        return
    await _apply_in_process(sel, hw)


async def _apply_in_process(sel: Selections, hw: HardwareInfo) -> None:
    from hal0.slots.manager import SlotManager
    from hal0.registry.registry import load_registry  # adjust to real loader

    sm = SlotManager.from_disk() if hasattr(SlotManager, "from_disk") else SlotManager()
    result = await apply_setup(sel, hardware=hw, slot_manager=sm,
                               registry=load_registry(), jobs={},
                               write_sentinel=True)
    with Progress(TextColumn("{task.description}"), BarColumn(),
                  DownloadColumn(), console=_con) as prog:
        async def _pull(plan):
            tid = prog.add_task(plan.model_id, total=None)
            def cb(done, total):
                prog.update(tid, completed=done, total=total)
            await run_pull(plan.job, progress_cb=cb, **plan.kwargs)
            prog.update(tid, description=f"{plan.model_id} ✓")
        await asyncio.gather(*(_pull(p) for p in result.pulls))
    _con.print("[bold green]hal0 is ready.[/] Dashboard: "
               "https://hal0.thinmint.dev")


async def _apply_via_api(sel: Selections) -> None:
    """Post-install: the API speaks 'tier'+overrides, so we translate the
    chosen slots into an overrides map against the user's tier-less selection.
    For the no-tier first-run case we POST the slots directly to /apply via the
    overrides channel using a synthetic 'hal0-Default' base, then reattach SSE."""
    payload = {
        "tier": "hal0-Default",
        "storage_dir": sel.storage_dir,
        "npu_opt_in": sel.npu_opt_in,
        "overrides": {s.slot_name: {"model_id": s.model_id} for s in sel.slots},
    }
    async with httpx.AsyncClient(base_url=_api_base(), timeout=30) as client:
        r = await client.post("/api/install/apply", json=payload)
        r.raise_for_status()
        data = r.json()
    _con.print(f"[bold green]hal0 updated.[/] {len(data.get('model_ids', []))} "
               "model(s) downloading — watch the dashboard.")
```

> `run_pull` must accept a `progress_cb(done, total)`. Check its real signature (`src/hal0/registry/pull.py:291`); if it emits progress via the `PullJob` object instead of a callback, poll `plan.job` inside the Live loop rather than passing `progress_cb`. Adjust this step to the real mechanism — the existing `FrDownloadRow` reattach logic in firstrun.jsx shows what the job exposes.
>
> **Known gap to confirm during review:** the API-up branch currently routes through the tier-based `/apply`. If Phase 6 deletes tier coupling, add a tier-less `POST /api/install/apply-selections` that accepts a `Selections` JSON and calls `apply_setup` directly — cleaner than the synthetic-tier shim above. Flagged as a decision point, not silently dropped.

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=src pytest tests/cli/test_setup_install.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
ruff check src/hal0/cli/setup_install.py && ruff format --check src/hal0/cli/setup_install.py
git add src/hal0/cli/setup_install.py tests/cli/test_setup_install.py
git commit -m "feat(cli): install step — hybrid apply + Live download progress"
```

---

# PHASE 5 — install.sh integration

## Task 5.1: Replace models-dir prompt + single-slot probe with `hal0 setup --auto`

**Files:**
- Modify: `installer/install.sh` (remove ~193-200 models-dir `read`; replace the probe block ~693-746)
- Test: extend the installer harness (`tests/harness/`) — see memory `hal0_test_harness`.

- [ ] **Step 1: Read the current probe block to preserve idempotency contracts**

Run: `sed -n '688,750p' installer/install.sh`
Expected: shows the inline-Python `HardwareProbe` + `recommend_primary_slot` block writing `chat.toml`. Note the `HAL0_NO_PROBE` guard and the "skip if chat.toml exists" idempotency.

- [ ] **Step 2: Replace the probe block** with a `hal0 setup --auto` call (keep the `HAL0_NO_PROBE`/new `HAL0_SKIP_SETUP` guards)

```sh
# installer/install.sh — replace the stage-8 probe block
if [[ "${HAL0_SKIP_SETUP:-0}" == "1" || "${HAL0_NO_PROBE:-0}" == "1" ]]; then
  ui_info "Skipping first-run setup (HAL0_SKIP_SETUP/HAL0_NO_PROBE set)."
else
  ui_step "Running first-run setup (recommended defaults)"
  # --auto is non-interactive: picks the hardware-recommended Main model,
  # the default extension set (Open WebUI + Hermes), and the NPU trio when
  # present. Writes slots OFFLINE + the first-run sentinel. Interactive
  # customization is available afterward via `hal0 setup`.
  "${VENV_BIN}/hal0" setup --auto \
      ${HAL0_MODELS_DIR:+--storage-dir "${HAL0_MODELS_DIR}"} \
      || ui_warn "first-run setup failed; run 'hal0 setup' manually after install"
fi
```

- [ ] **Step 3: Remove the now-dead models-dir prompt**

Delete the `read -r ... models directory` block (~lines 193-200) — `--storage-dir` now flows through `hal0 setup`. Keep `HAL0_MODELS_DIR` parsing (it's forwarded above).

- [ ] **Step 4: Remove the unconditional OWUI/Hermes install lines**

The extension install now flows through `apply_setup`. Delete the standalone `hal0 agent install hermes` foreground call (~line 1222) and the unconditional OpenWebUI enable — they're covered by the default extension set. (Leave the *unit file writing* in stage 7; extensions only enable/start them.)

- [ ] **Step 5: Add a harness assertion**

```bash
# tests/harness/ — add to the post-install assertions
# After a --no-start install on a probe-stubbed box:
test -f /var/lib/hal0/.first_run_done   # sentinel written by `hal0 setup --auto`
test -f /etc/hal0/slots/chat.toml       # Main slot seeded
```

- [ ] **Step 6: Run the installer harness (dev mode, no services)**

Run: `make harness` (or the harness entry from memory `hal0_test_harness`)
Expected: install completes; sentinel + `chat.toml` present; JSON report green. If `hal0 setup --auto` can't construct `SlotManager`/registry off-API, fix the `_run_auto`/`_apply_in_process` accessors (Task 2.1 / 4.1 notes) to mirror `app.py`'s construction — this is the integration point those notes flagged.

- [ ] **Step 7: Commit**

```bash
git add installer/install.sh tests/harness/
git commit -m "feat(installer): drive first-run via 'hal0 setup --auto'; drop single-slot probe"
```

---

# PHASE 6 — Demolish the web FirstRun + dead v1 bundles

## Task 6.1: Delete the frontend FirstRun surface

**Files:** see deletion list below.

- [ ] **Step 1: Delete the files**

```bash
git rm ui/src/dash/firstrun.jsx \
       ui/src/api/hooks/useFirstRun.ts \
       ui/src/dash/install-state-bridge.ts \
       ui/tests/e2e/specs/firstrun-v2.spec.ts \
       ui/tests/e2e/specs/firstrun-v3.spec.ts
```

- [ ] **Step 2: Remove FirstRun wiring from `main.jsx`**

Run: `grep -n "firstrun\|firstRun\|frStage\|FirstRun\|install-state" ui/src/dash/main.jsx`
Then delete: the `frStage` state, the auto-route effect (~199-203), the `firstRunLayout` tweak + "Jump to FirstRun" button (~395-417), the `FirstRunView` import + render branch, and the `window.__hal0UseInstallState` bridge usage (~118-119).

- [ ] **Step 3: Remove FirstRun endpoint constants + bundle fixture**

Run: `grep -n "install/apply\|install/state\|install/complete\|curated-models\|pick-default\|bundles" ui/src/api/endpoints.ts`
Delete the FirstRun endpoint constants (~283-293). In `ui/src/dash/data.jsx`, delete the `HAL0_DATA.bundles` fixture (~305-360).

- [ ] **Step 4: Build the UI to prove nothing references the deleted symbols**

Run: `cd ui && rm -rf node_modules/.vite dist && npm run build`
Expected: build succeeds (memory `feedback_hal0_ui_clean_rebuild` — wipe `.vite`+`dist` first). Fix any dangling imports the build surfaces.

- [ ] **Step 5: Run remaining e2e to confirm green**

Run: `cd ui && npm run test:e2e` (or the project's e2e command)
Expected: PASS with the two firstrun specs gone.

- [ ] **Step 6: Commit**

```bash
git add -A ui/
git commit -m "chore(ui): delete web FirstRun picker (folded into hal0 setup)"
```

## Task 6.2: Delete the dead v1 bundles backend + legacy install routes

**Files:** `src/hal0/api/routes/bundles.py`, `src/hal0/bundles/store.py`, the `/pick-default` + `/slots/{slot}/model` routes in `installer.py`, `tests/api/test_bundles_route.py`.

- [ ] **Step 1: Delete the v1 bundles route + store + its test**

```bash
git rm src/hal0/api/routes/bundles.py src/hal0/bundles/store.py tests/api/test_bundles_route.py
```

- [ ] **Step 2: Unmount the bundles router**

Run: `grep -rn "routes.bundles\|bundles.router\|import bundles" src/hal0/api/`
Delete the `app.include_router(bundles.router ...)` line + its import in `src/hal0/api/app.py` (or wherever routers mount).

- [ ] **Step 3: Delete `/pick-default` + `/slots/{slot}/model` from `installer.py`**

Run: `grep -n "pick-default\|slots/{slot}/model\|_assign_to_slot\|_validate_slot_name\|PickDefaultError" src/hal0/api/routes/installer.py`
Delete the `pick_default` handler (~595-671), the `PUT /slots/{slot}/model` handler (~674-715), and the now-unused `_assign_to_slot`/`_validate_slot_name` helpers. Keep `PickDefaultError` only if `install_apply` still raises it (it does — for body validation); otherwise rename to a generic `InstallBadRequest`.

- [ ] **Step 4: Prune the bundle-choice read from `/state`**

Run: `grep -n "bundle\|bundle_store\|read_choice\|.bundle-chosen" src/hal0/api/routes/installer.py`
Remove the `bundle` field population in `GET /state` (it referenced `bundle_store.read_choice`). Leave `first_run`/`has_models`/sentinel logic intact.

- [ ] **Step 5: Run the backend test suite for the install + bundles area**

Run: `PYTHONPATH=src pytest tests/api/test_installer_routes.py tests/api/test_install_apply.py tests/install/ -v`
Expected: PASS. Remove/adjust any `test_installer_routes.py` cases that asserted on `/pick-default` or the `bundle` field of `/state`.

- [ ] **Step 6: Grep-gate that no web FirstRun/bundles symbols remain**

Run: `grep -rn "bundle_store\|pick-default\|firstrun" src/hal0/ ui/src/ | grep -v "bundles/tiers\|bundles/schema\|bundles/eligibility\|test_"`
Expected: no hits (the only surviving `bundles/*` references are the dormant tiers/schema/eligibility kept for stacks, per spec §5).

- [ ] **Step 7: Commit**

```bash
ruff check src/hal0/ && ruff format --check src/hal0/
git add -A src/hal0/ tests/
git commit -m "chore(api): remove dead v1 bundles surface + legacy install routes"
```

---

# PHASE 7 — Sentinel banner + docs

## Task 7.1: Passive "run hal0 setup" dashboard banner (optional, spec §8)

**Files:** `ui/src/dash/main.jsx` (or the nav/banner component), `ui/src/api/hooks/useInstallState.ts` (kept).

- [ ] **Step 1: Add the banner** — when `useInstallState().first_run` is true and no slots exist, render a dismissible banner: "No models yet — run `hal0 setup` in your terminal to add them." No auto-route (that was deleted in Phase 6). Keep `useInstallState` (it reads `/api/install/state`, still valid).

- [ ] **Step 2: Build + eyeball**

Run: `cd ui && rm -rf node_modules/.vite dist && npm run build`
Expected: build succeeds; banner shows only in the first-run state.

- [ ] **Step 3: Commit**

```bash
git add -A ui/
git commit -m "feat(ui): passive first-run banner pointing at 'hal0 setup'"
```

## Task 7.2: Docs — README, PLAN, hal0-web brief

**Files:** `README.md`, `docs/` install guide, `hal0-web` CONTENT_BRIEF (memory `feedback_remind_docs_promo_after_changes`).

- [ ] **Step 1: Update install docs**

In `README.md` and the install guide: replace any "open the dashboard to finish setup / FirstRun wizard" copy with: "`curl hal0.dev/install.sh | bash` configures recommended defaults automatically; run `hal0 setup` anytime to customize models, agents, and apps." Document `--auto`, `--storage-dir`, `HAL0_SKIP_SETUP`.

- [ ] **Step 2: Note the Extensions concept + stacks-deferral**

Add a short "Extensions" subsection (Apps/Agents, auto-wired) and a one-line "Stacks (coming)" forward-pointer so the bundle-tier removal is explained.

- [ ] **Step 3: Commit + flag hal0-web**

```bash
git add README.md docs/
git commit -m "docs: hal0 setup TUI replaces web FirstRun; document Extensions"
```

Then surface to the user that `hal0-web` CONTENT_BRIEF + the Astro install page need the same messaging change (separate repo — don't edit blind; per workflow rules).

---

## Self-Review

**Spec coverage:**
- §3 decisions 1-8 → Phases 0/6 (retire scope), 5 (interactivity), 3 (rich), 3.1 (pane), 2.1/4.1 (hybrid), 1.2/3.x (extensions), 3.3 (gating), 3.x (Main naming). ✓
- §4 module map → every new file has a task; `orchestrate.py` Phase 0, `suggest.py`/`extensions.py` Phase 1, `setup_*.py` Phases 2-4. ✓
- §5 demolition → Phase 6 (frontend 6.1, backend 6.2) with grep-gate. Dormant bundle modules explicitly preserved. ✓
- §6 flow → Phase 3 steps + gating (`plan_steps`); §6.4 extensions (1.2); §6.5 suggest (1.1); §6.6 apply_setup (0.2-0.3); §6.7 Live (4.1). ✓
- §7 install.sh → Phase 5. §8 sentinel/banner → 0.3 + 7.1. §9 testing → tests in every task. §10 phases → mapped 1:1. ✓

**Placeholder scan:** No "TODO/TBD". Three explicit *review decision points* are flagged with concrete fallbacks (not placeholders): the `paths.var_lib_dir()` accessor name (0.3), the `run_pull` progress mechanism (4.1), and the API-up tier-less `/apply-selections` route (4.1). Each says exactly what to check and the default to take.

**Type consistency:** `Selections`/`SlotSelection`/`SlotOutcome`/`SetupResult`/`PullPlan`/`ExtensionOutcome` defined in Task 0.1, used consistently in 0.2-0.4, 2.1, 4.1. `Suggestion` (1.1) fields match the table renderer (3.2) and `_choose_model` (3.3). `apply_setup(selections, *, hardware, slot_manager, registry, jobs, hf_token, write_sentinel)` signature identical across 0.2, 0.4, 2.1, 4.1. `plan_steps(*, extensions, npu_present)` identical in 3.3 tests + impl. ✓

**Known integration seam (not a gap):** `_apply_in_process` constructs `SlotManager` + registry off-API; the exact accessors must mirror `src/hal0/api/app.py`. Flagged in 2.1, 4.1, and proven green by the Phase 5 harness (5.1 Step 6). This is the one spot requiring a real-source check at execution time.
