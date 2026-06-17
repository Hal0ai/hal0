# ComfyUI GPU Coexistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let ComfyUI renders coexist with LLM inference on the shared Strix Halo iGPU when they fit a GTT budget, keeping inference always-available and auto-evicting LLM slots (agent last) only when a render needs the full GPU.

**Architecture:** Partition GTT into `[LLM reserve R | render envelope G | margin]`. A pure footprint estimator reads a ComfyUI prompt's model-loader nodes and sums on-disk model sizes × a peak factor. A pure admission function (`admit_render`) maps a footprint to `coexist | needs_exclusive(evict_plan) | wont_fit`, building an incremental evict plan with `agent` pinned last. The `GpuArbiter` wires config + the slot manager into these; a new `POST /api/comfyui/admit` exposes the decision; the stdlib-only ComfyUI gate calls it on `/prompt`; the dashboard surfaces the budget and auto-switches only when needed.

**Tech Stack:** Python 3.13, FastAPI, pytest, React (preact-ish JSX dashboard), Playwright e2e, ruff.

## Global Constraints

- Python **3.13**; format with **`ruff format`** (NOT black); lint with `ruff check`. CI runs `ruff format --check src tests`.
- Run backend tests with `PYTHONPATH=src /home/halo/dev/hal0/.venv/bin/python -m pytest <path> -q` from the worktree root.
- `installer/comfyui/custom_nodes/hal0_gpu_gate.py` MUST stay **stdlib-only** (no hal0 imports, no third-party) and **fail-open** (allow the prompt) when hal0-api is unreachable.
- Commit messages: conventional commits; end every commit body with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- Config defaults (exact): `HAL0_GPU_LLM_RESERVE_GB` (default: sum of loaded llm-group slot footprints, fallback `33.0`), `HAL0_GPU_GTT_MARGIN_GB`=`6.0`, `HAL0_GPU_RENDER_PEAK_FACTOR`=`1.3`, `HAL0_GPU_RENDER_VIDEO_PEAK_FACTOR`=`1.6`, `HAL0_GPU_RENDER_UNKNOWN_MODEL_GB`=`8.0`, `HAL0_GPU_EVICT_PRIORITY`=`"stt,tts,rerank,embed,utility,chat"` (`agent` is pinned last in code, never in the list).
- **Invariant:** `agent` is always evicted last, enforced in code regardless of `HAL0_GPU_EVICT_PRIORITY`.
- ComfyUI model root on the runtime host: `/mnt/ai-models/comfyui/models`; env override `COMFYUI_MODELS_DIR` (already used by `comfyui.py:_comfyui_models_dir`).

---

### Task 1: Pure footprint estimator

**Files:**
- Create: `src/hal0/slots/comfyui_footprint.py`
- Test: `tests/slots/test_comfyui_footprint.py`

**Interfaces:**
- Produces:
  - `LOADER_MODEL_INPUTS: dict[str, tuple[tuple[str, str], ...]]` — maps a ComfyUI node `class_type` to a tuple of `(input_field, models_subdir)` pairs.
  - `VIDEO_NODE_TYPES: frozenset[str]` — node class_types that imply large video latents.
  - `iter_model_files(prompt: dict) -> list[tuple[str, str]]` — returns `(subdir, filename)` pairs referenced by loader nodes.
  - `estimate_footprint_gb(prompt: dict, model_dir: str, *, peak_factor: float = 1.3, video_peak_factor: float = 1.6, unknown_model_gb: float = 8.0) -> float` — conservative GiB estimate.

- [ ] **Step 1: Write the failing test**

```python
# tests/slots/test_comfyui_footprint.py
from __future__ import annotations

import os

from hal0.slots.comfyui_footprint import (
    estimate_footprint_gb,
    iter_model_files,
)

_GiB = 1024**3


def _write(path: str, size_bytes: int) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.truncate(size_bytes)


def test_iter_model_files_extracts_loader_inputs() -> None:
    prompt = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sdxl.safetensors"}},
        "2": {"class_type": "VAELoader", "inputs": {"vae_name": "sdxl_vae.safetensors"}},
        "3": {"class_type": "KSampler", "inputs": {"seed": 1}},
    }
    files = set(iter_model_files(prompt))
    assert ("checkpoints", "sdxl.safetensors") in files
    assert ("vae", "sdxl_vae.safetensors") in files
    # non-loader nodes contribute nothing
    assert len(files) == 2


def test_estimate_sums_unique_files_with_peak_factor(tmp_path) -> None:
    model_dir = str(tmp_path)
    _write(os.path.join(model_dir, "checkpoints", "sdxl.safetensors"), 7 * _GiB)
    _write(os.path.join(model_dir, "vae", "sdxl_vae.safetensors"), 1 * _GiB)
    prompt = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sdxl.safetensors"}},
        "2": {"class_type": "VAELoader", "inputs": {"vae_name": "sdxl_vae.safetensors"}},
    }
    # (7 + 1) GiB * 1.3 = 10.4
    est = estimate_footprint_gb(prompt, model_dir, peak_factor=1.3)
    assert abs(est - 10.4) < 0.05


def test_estimate_uses_video_peak_factor_when_video_node_present(tmp_path) -> None:
    model_dir = str(tmp_path)
    _write(os.path.join(model_dir, "diffusion_models", "wan_hi.safetensors"), 13 * _GiB)
    _write(os.path.join(model_dir, "diffusion_models", "wan_lo.safetensors"), 13 * _GiB)
    prompt = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": "wan_hi.safetensors"}},
        "2": {"class_type": "UNETLoader", "inputs": {"unet_name": "wan_lo.safetensors"}},
        "3": {"class_type": "SaveWEBM", "inputs": {}},
    }
    # 26 GiB * 1.6 = 41.6 (video factor, not 1.3)
    est = estimate_footprint_gb(prompt, model_dir, peak_factor=1.3, video_peak_factor=1.6)
    assert abs(est - 41.6) < 0.05


def test_unknown_or_missing_model_uses_conservative_default(tmp_path) -> None:
    model_dir = str(tmp_path)
    prompt = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "nope.safetensors"}},
    }
    est = estimate_footprint_gb(prompt, model_dir, peak_factor=1.0, unknown_model_gb=8.0)
    assert abs(est - 8.0) < 0.01
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src /home/halo/dev/hal0/.venv/bin/python -m pytest tests/slots/test_comfyui_footprint.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'hal0.slots.comfyui_footprint'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/hal0/slots/comfyui_footprint.py
"""Estimate a ComfyUI render's GPU memory footprint from its prompt JSON.

Pure + dependency-free: walk the API-format prompt for model-loading nodes,
map each to its on-disk model file, sum unique files, and apply a peak
multiplier (higher when video nodes imply large latent tensors). Conservative
by design — an unrecognised model contributes a default rather than zero, so an
unknown workflow biases toward "needs the whole GPU", never toward a too-rosy
coexist decision.
"""

from __future__ import annotations

import os

_GiB = 1024**3

#: class_type → ((input_field, models_subdir), ...). Covers the loaders the
#: curated hal0 workflows use; extend as new model families land.
LOADER_MODEL_INPUTS: dict[str, tuple[tuple[str, str], ...]] = {
    "CheckpointLoaderSimple": (("ckpt_name", "checkpoints"),),
    "CheckpointLoader": (("ckpt_name", "checkpoints"),),
    "UNETLoader": (("unet_name", "diffusion_models"),),
    "UNETLoaderGGUF": (("unet_name", "diffusion_models"),),
    "VAELoader": (("vae_name", "vae"),),
    "CLIPLoader": (("clip_name", "text_encoders"),),
    "CLIPLoaderGGUF": (("clip_name", "text_encoders"),),
    "DualCLIPLoader": (("clip_name1", "text_encoders"), ("clip_name2", "text_encoders")),
    "LoraLoader": (("lora_name", "loras"),),
    "LoraLoaderModelOnly": (("lora_name", "loras"),),
    "ControlNetLoader": (("control_net_name", "controlnet"),),
    "CLIPVisionLoader": (("clip_name", "clip_vision"),),
}

#: Node class_types that imply large video latents → use the video peak factor.
VIDEO_NODE_TYPES: frozenset[str] = frozenset(
    {
        "SaveWEBM",
        "VHS_VideoCombine",
        "SaveVideo",
        "WanImageToVideo",
        "WanVideoSampler",
        "LTXVConditioning",
        "EmptyHunyuanLatentVideo",
        "EmptyLTXVLatentVideo",
    }
)


def iter_model_files(prompt: dict) -> list[tuple[str, str]]:
    """Return (subdir, filename) for every model referenced by a loader node."""
    out: list[tuple[str, str]] = []
    if not isinstance(prompt, dict):
        return out
    for node in prompt.values():
        if not isinstance(node, dict):
            continue
        mapping = LOADER_MODEL_INPUTS.get(node.get("class_type", ""))
        if not mapping:
            continue
        inputs = node.get("inputs") or {}
        for field, subdir in mapping:
            name = inputs.get(field)
            if isinstance(name, str) and name:
                out.append((subdir, name))
    return out


def _has_video_node(prompt: dict) -> bool:
    return any(
        isinstance(n, dict) and n.get("class_type") in VIDEO_NODE_TYPES
        for n in prompt.values()
    )


def estimate_footprint_gb(
    prompt: dict,
    model_dir: str,
    *,
    peak_factor: float = 1.3,
    video_peak_factor: float = 1.6,
    unknown_model_gb: float = 8.0,
) -> float:
    """Conservative GiB estimate of a render's peak GPU footprint."""
    seen: set[tuple[str, str]] = set()
    raw_gb = 0.0
    for subdir, name in iter_model_files(prompt):
        key = (subdir, name)
        if key in seen:
            continue
        seen.add(key)
        path = os.path.join(model_dir, subdir, name)
        try:
            raw_gb += os.path.getsize(path) / _GiB
        except OSError:
            raw_gb += unknown_model_gb
    factor = video_peak_factor if _has_video_node(prompt) else peak_factor
    return raw_gb * factor
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src /home/halo/dev/hal0/.venv/bin/python -m pytest tests/slots/test_comfyui_footprint.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Format + commit**

```bash
cd /home/halo/dev/wt/gpu-coexist
/home/halo/dev/hal0/.venv/bin/ruff format src/hal0/slots/comfyui_footprint.py tests/slots/test_comfyui_footprint.py
git add src/hal0/slots/comfyui_footprint.py tests/slots/test_comfyui_footprint.py
git commit -m "feat(comfyui): pure render-footprint estimator from prompt loaders

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Pure admission decision (agent-last evict plan)

**Files:**
- Create: `src/hal0/slots/gpu_budget.py`
- Test: `tests/slots/test_gpu_budget.py`

**Interfaces:**
- Consumes: nothing (pure).
- Produces:
  - `@dataclass(frozen=True) class AdmitDecision: decision: str  # "coexist" | "needs_exclusive" | "wont_fit"; footprint_gb: float; envelope_gb: float; free_gb: float; evict_plan: tuple[str, ...]`
  - `order_evictions(loaded_llm_slots: Iterable[str], evict_priority: Sequence[str]) -> list[str]` — returns slot names in eviction order with `agent` ALWAYS last.
  - `admit_render(footprint_gb: float, *, gtt_ceil_gb: float, reserve_gb: float, margin_gb: float, used_non_llm_gb: float, loaded_llm_slots: Sequence[str], llm_footprints_gb: Mapping[str, float], evict_priority: Sequence[str]) -> AdmitDecision`

**Decision math:** `envelope = gtt_ceil - reserve - margin`. If `footprint <= envelope - used_non_llm`: `coexist`. Else build an evict plan by freeing loaded llm slots in `order_evictions` order, adding each freed slot's `llm_footprints_gb` back to available, stopping as soon as `footprint <= (gtt_ceil - margin - used_non_llm - remaining_llm)`. If the full plan (all llm slots incl. agent) still doesn't fit → `wont_fit`.

- [ ] **Step 1: Write the failing test**

```python
# tests/slots/test_gpu_budget.py
from __future__ import annotations

from hal0.slots.gpu_budget import AdmitDecision, admit_render, order_evictions

_LLM = {"agent": 18.0, "chat": 15.0}


def _admit(fp: float, *, used=0.0, loaded=("agent", "chat")):
    return admit_render(
        fp,
        gtt_ceil_gb=96.0,
        reserve_gb=33.0,
        margin_gb=6.0,
        used_non_llm_gb=used,
        loaded_llm_slots=loaded,
        llm_footprints_gb=_LLM,
        evict_priority=["utility", "chat"],
    )


def test_order_evictions_pins_agent_last() -> None:
    order = order_evictions(["agent", "chat", "utility"], ["utility", "chat"])
    assert order == ["utility", "chat", "agent"]


def test_small_render_coexists() -> None:
    d = _admit(8.0)
    assert d.decision == "coexist"
    assert d.evict_plan == ()
    assert abs(d.envelope_gb - 57.0) < 0.01  # 96 - 33 - 6


def test_render_just_over_envelope_evicts_chat_not_agent() -> None:
    # envelope 57; render 65 doesn't coexist. ceil-margin-remaining must cover it.
    # free chat (+15): available = 96-6-(agent 18) = 72 >= 65 → stop, agent kept.
    d = _admit(65.0)
    assert d.decision == "needs_exclusive"
    assert d.evict_plan == ("chat",)


def test_heavy_render_evicts_chat_then_agent_last() -> None:
    # render 80: after chat → 72 < 80; after agent too → 96-6 = 90 >= 80.
    d = _admit(80.0)
    assert d.decision == "needs_exclusive"
    assert d.evict_plan == ("chat", "agent")


def test_render_exceeding_total_wont_fit() -> None:
    d = _admit(95.0)  # > 96 - 6 margin
    assert d.decision == "wont_fit"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src /home/halo/dev/hal0/.venv/bin/python -m pytest tests/slots/test_gpu_budget.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'hal0.slots.gpu_budget'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/hal0/slots/gpu_budget.py
"""Pure GPU-budget admission decision for ComfyUI renders.

Partition the GTT ceiling into [LLM reserve | render envelope | margin].
A render coexists with inference when it fits the envelope; otherwise it needs
exclusive mode, freed by an incremental evict plan that frees the *minimum*
LLM slots needed and pins ``agent`` last so the operator brain survives where
possible.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

#: agent is the operator/Hermes brain — always the last slot to be evicted.
AGENT_SLOT = "agent"


@dataclass(frozen=True)
class AdmitDecision:
    decision: str  # "coexist" | "needs_exclusive" | "wont_fit"
    footprint_gb: float
    envelope_gb: float
    free_gb: float
    evict_plan: tuple[str, ...]


def order_evictions(
    loaded_llm_slots: Iterable[str], evict_priority: Sequence[str]
) -> list[str]:
    """Eviction order for the loaded llm slots, ``agent`` always last."""
    loaded = [s for s in loaded_llm_slots]
    ranked = [s for s in evict_priority if s in loaded and s != AGENT_SLOT]
    # any loaded slot not named in evict_priority falls between the list and agent
    leftover = [s for s in loaded if s not in evict_priority and s != AGENT_SLOT]
    order = ranked + leftover
    if AGENT_SLOT in loaded:
        order.append(AGENT_SLOT)
    return order


def admit_render(
    footprint_gb: float,
    *,
    gtt_ceil_gb: float,
    reserve_gb: float,
    margin_gb: float,
    used_non_llm_gb: float,
    loaded_llm_slots: Sequence[str],
    llm_footprints_gb: Mapping[str, float],
    evict_priority: Sequence[str],
) -> AdmitDecision:
    envelope = gtt_ceil_gb - reserve_gb - margin_gb
    coexist_free = envelope - used_non_llm_gb
    if footprint_gb <= coexist_free:
        return AdmitDecision("coexist", footprint_gb, envelope, coexist_free, ())

    # Need exclusive: free loaded llm slots (agent last) until the render fits
    # the hard ceiling minus margin minus whatever non-llm is already resident.
    order = order_evictions(loaded_llm_slots, evict_priority)
    remaining_llm = sum(llm_footprints_gb.get(s, 0.0) for s in loaded_llm_slots)
    plan: list[str] = []
    for slot in order:
        available = gtt_ceil_gb - margin_gb - used_non_llm_gb - remaining_llm
        if footprint_gb <= available:
            break
        plan.append(slot)
        remaining_llm -= llm_footprints_gb.get(slot, 0.0)
    available = gtt_ceil_gb - margin_gb - used_non_llm_gb - remaining_llm
    if footprint_gb <= available:
        return AdmitDecision(
            "needs_exclusive", footprint_gb, envelope, coexist_free, tuple(plan)
        )
    return AdmitDecision("wont_fit", footprint_gb, envelope, coexist_free, ())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src /home/halo/dev/hal0/.venv/bin/python -m pytest tests/slots/test_gpu_budget.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Format + commit**

```bash
cd /home/halo/dev/wt/gpu-coexist
/home/halo/dev/hal0/.venv/bin/ruff format src/hal0/slots/gpu_budget.py tests/slots/test_gpu_budget.py
git add src/hal0/slots/gpu_budget.py tests/slots/test_gpu_budget.py
git commit -m "feat(slots): pure GPU-budget admission with agent-last evict plan

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Arbiter budget wiring + evict-to-fit + relaxed dispatch guard

**Files:**
- Modify: `src/hal0/slots/arbiter.py` (add budget config to `__init__`, add `compute_budget()`, `plan_admission()`, async `evict_to_fit()`; relax the `GpuInferenceMode` raise at line ~672)
- Test: `tests/slots/test_arbiter_admit.py`

**Interfaces:**
- Consumes: `admit_render`, `AdmitDecision`, `order_evictions` (Task 2); `GpuArbiter(manager, state_path, idle_restore_minutes)`; `manager.unload(slot_name)` (async).
- Produces on `GpuArbiter`:
  - `def compute_budget(self) -> dict[str, float]` → `{"gtt_ceil_gb", "reserve_gb", "margin_gb", "envelope_gb", "free_gb"}` (reads sysfs gtt_total + env; reserve from loaded llm footprints or `HAL0_GPU_LLM_RESERVE_GB`/33.0 fallback).
  - `def plan_admission(self, footprint_gb: float) -> AdmitDecision`
  - `async def evict_to_fit(self, evict_plan: Sequence[str]) -> list[str]` → unloads each named slot via the manager, agent last (plan already ordered), returns the names actually unloaded.

**Integration notes for the implementer (read these files first):**
- The dispatch guard raising `GpuInferenceMode` is in `arbiter.py` around line 666–676 (`if st["mode"] == GpuMode.LLM.value and group == "img": raise GpuInferenceMode(...)`). Change it so it does NOT raise for img dispatch — coexistence is now allowed; the per-render budget check happens at admission time (`/admit`), not here. Keep the reverse guard (`GpuMode.IMG + group == "llm"` → `GpuImageMode`) unchanged. Add a comment pointing at this plan.
- `manager` exposes loaded slots; reuse the same source `arbiter.status()` uses to know which llm-group slots are loaded. If there is no direct accessor, read via the manager's slot list and filter by `self._slot_group(name) == "llm"` and state ready/loaded.
- GTT ceiling: read `/sys/class/drm/card*/device/mem_info_gtt_total` (bytes → GiB); fall back to `96.0` if unreadable. `used_non_llm_gb` is passed as `0.0` in v1 — a deliberate simplification (the reserve already protects the LLM budget; concurrent renders are serialized by ComfyUI's queue). Document this in the `plan_admission` docstring as an explicit v1 choice, not a gap.

- [ ] **Step 1: Write the failing test** (fake manager; exercises plan + evict order)

```python
# tests/slots/test_arbiter_admit.py
from __future__ import annotations

import asyncio

import pytest

from hal0.slots.arbiter import GpuArbiter


class _FakeSlot:
    def __init__(self, name, group, loaded):
        self.name = name
        self._group = group
        self.loaded = loaded


class _FakeManager:
    """Minimal manager: known llm slots + records unload() calls."""

    def __init__(self):
        self.unloaded: list[str] = []

    async def unload(self, slot_name: str):
        self.unloaded.append(slot_name)
        return slot_name


@pytest.fixture
def arbiter(tmp_path, monkeypatch):
    monkeypatch.setenv("HAL0_GPU_LLM_RESERVE_GB", "33")
    monkeypatch.setenv("HAL0_GPU_GTT_MARGIN_GB", "6")
    monkeypatch.setenv("HAL0_GPU_EVICT_PRIORITY", "utility,chat")
    arb = GpuArbiter(_FakeManager(), state_path=tmp_path / "gpu.json")
    # Force a deterministic budget for the test.
    monkeypatch.setattr(arb, "_gtt_ceil_gb", lambda: 96.0)
    monkeypatch.setattr(
        arb, "_loaded_llm_footprints", lambda: {"agent": 18.0, "chat": 15.0}
    )
    return arb


def test_plan_admission_coexist(arbiter):
    d = arbiter.plan_admission(8.0)
    assert d.decision == "coexist"


def test_plan_admission_evicts_chat_first(arbiter):
    d = arbiter.plan_admission(65.0)
    assert d.decision == "needs_exclusive"
    assert d.evict_plan == ("chat",)


def test_evict_to_fit_calls_unload_agent_last(arbiter):
    freed = asyncio.run(arbiter.evict_to_fit(("chat", "agent")))
    assert freed == ["chat", "agent"]
    assert arbiter._manager.unloaded == ["chat", "agent"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src /home/halo/dev/hal0/.venv/bin/python -m pytest tests/slots/test_arbiter_admit.py -q`
Expected: FAIL (`AttributeError: 'GpuArbiter' object has no attribute 'plan_admission'`)

- [ ] **Step 3: Implement** — add to `GpuArbiter` (exact code):

```python
# near the top of arbiter.py imports
import os
from collections.abc import Sequence

from hal0.slots.gpu_budget import AdmitDecision, admit_render

# inside class GpuArbiter:

def _gtt_ceil_gb(self) -> float:
    import glob

    for p in glob.glob("/sys/class/drm/card*/device/mem_info_gtt_total"):
        try:
            with open(p) as f:
                return int(f.read().strip()) / (1024**3)
        except (OSError, ValueError):
            continue
    return 96.0

def _loaded_llm_footprints(self) -> dict[str, float]:
    """name → GiB for currently-loaded llm-group slots (best-effort)."""
    out: dict[str, float] = {}
    for slot in getattr(self._manager, "list_slots", lambda: [])():
        name = getattr(slot, "name", None)
        if not name or self._slot_group(name) != "llm":
            continue
        if str(getattr(slot, "state", "")) not in ("ready", "loaded", "warming"):
            continue
        mb = getattr(slot, "memory_mb", None) or getattr(slot, "gtt_mb", None)
        out[name] = float(mb) / 1024 if mb else 0.0
    return out

def _reserve_gb(self, loaded: dict[str, float]) -> float:
    env = os.environ.get("HAL0_GPU_LLM_RESERVE_GB", "").strip()
    if env:
        try:
            return float(env)
        except ValueError:
            pass
    total = sum(loaded.values())
    return total if total > 0 else 33.0

def compute_budget(self) -> dict[str, float]:
    loaded = self._loaded_llm_footprints()
    ceil = self._gtt_ceil_gb()
    reserve = self._reserve_gb(loaded)
    margin = float(os.environ.get("HAL0_GPU_GTT_MARGIN_GB", "6") or 6)
    envelope = ceil - reserve - margin
    return {
        "gtt_ceil_gb": ceil,
        "reserve_gb": reserve,
        "margin_gb": margin,
        "envelope_gb": envelope,
        "free_gb": max(0.0, envelope),
    }

def plan_admission(self, footprint_gb: float) -> AdmitDecision:
    loaded = self._loaded_llm_footprints()
    b = self.compute_budget()
    priority = [
        s for s in os.environ.get("HAL0_GPU_EVICT_PRIORITY", "stt,tts,rerank,embed,utility,chat").split(",") if s.strip()
    ]
    return admit_render(
        footprint_gb,
        gtt_ceil_gb=b["gtt_ceil_gb"],
        reserve_gb=b["reserve_gb"],
        margin_gb=b["margin_gb"],
        used_non_llm_gb=0.0,  # v1: reserve already protects llm; live non-llm GTT not subtracted
        loaded_llm_slots=list(loaded),
        llm_footprints_gb=loaded,
        evict_priority=priority,
    )

async def evict_to_fit(self, evict_plan: Sequence[str]) -> list[str]:
    """Unload the planned slots in order (agent last, already ordered)."""
    freed: list[str] = []
    for name in evict_plan:
        try:
            await self._manager.unload(name)
            freed.append(name)
        except Exception as exc:  # fail-soft: keep going
            log.warning("gpu_arbiter.evict_failed", extra={"slot": name, "error": str(exc)})
    return freed
```

Then relax the dispatch guard (around line 666–676): delete/comment the `raise GpuInferenceMode(...)` for the `img` group so coexisting renders aren't blocked at dispatch. Add:

```python
        # Coexistence (see docs/superpowers/plans/2026-06-17-comfyui-gpu-coexistence.md):
        # img dispatch is no longer hard-blocked in llm mode — per-render GTT
        # admission happens at POST /api/comfyui/admit. Reverse guard kept.
```

If `_FakeManager` in the test lacks `list_slots`, the `getattr(..., lambda: [])` default makes `_loaded_llm_footprints` return `{}`; the test monkeypatches `_loaded_llm_footprints` directly, so it's fine.

- [ ] **Step 4: Run tests** — both new file and the existing arbiter tests (no regression):

Run: `PYTHONPATH=src /home/halo/dev/hal0/.venv/bin/python -m pytest tests/slots/test_arbiter_admit.py tests/slots/test_arbiter.py -q`
Expected: PASS (new 3 pass; existing arbiter tests still pass — if an existing test asserted `GpuInferenceMode` is raised for img dispatch, update it to assert dispatch is now allowed, citing this plan)

- [ ] **Step 5: Format + commit**

```bash
cd /home/halo/dev/wt/gpu-coexist
/home/halo/dev/hal0/.venv/bin/ruff format src/hal0/slots/arbiter.py tests/slots/test_arbiter_admit.py
git add src/hal0/slots/arbiter.py tests/slots/test_arbiter_admit.py tests/slots/test_arbiter.py
git commit -m "feat(slots): arbiter GTT-budget admission + evict-to-fit (agent last)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `/api/comfyui/admit` route + `status.gpu_budget`

**Files:**
- Modify: `src/hal0/api/routes/comfyui.py` (new `POST /admit`; add `gpu_budget` to `/status` return)
- Test: `tests/api/test_comfyui_admit.py`

**Interfaces:**
- Consumes: `estimate_footprint_gb` (Task 1), `arbiter.plan_admission` / `arbiter.compute_budget` (Task 3), existing `_comfyui_models_dir()`, `_get_arbiter(request)`.
- Produces: `POST /api/comfyui/admit` body `{"prompt": {...}}` → `200 {"decision","footprint_gb","envelope_gb","free_gb","evict_plan"}`; `/status` gains key `"gpu_budget": {gtt_ceil_gb,reserve_gb,margin_gb,envelope_gb,free_gb}` (or `null` when no arbiter).

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_comfyui_admit.py
from __future__ import annotations

from fastapi.testclient import TestClient


def test_admit_small_prompt_coexists(client: TestClient, monkeypatch, tmp_path):
    # No models on disk → unknown default 8GB → coexist under a 57 envelope.
    monkeypatch.setenv("COMFYUI_MODELS_DIR", str(tmp_path))
    body = {"prompt": {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "x.safetensors"}}}}
    resp = client.post("/api/comfyui/admit", json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["decision"] in {"coexist", "needs_exclusive", "wont_fit"}
    assert "footprint_gb" in data and "evict_plan" in data


def test_status_exposes_gpu_budget(client: TestClient):
    resp = client.get("/api/comfyui/status")
    assert resp.status_code == 200
    assert "gpu_budget" in resp.json()
```

- [ ] **Step 2: Run** → FAIL (404 on /admit; KeyError gpu_budget). Verify.

Run: `PYTHONPATH=src /home/halo/dev/hal0/.venv/bin/python -m pytest tests/api/test_comfyui_admit.py -q`

- [ ] **Step 3: Implement** in `comfyui.py`:

```python
from hal0.slots.comfyui_footprint import estimate_footprint_gb

@router.post("/admit")
async def comfyui_admit(request: Request) -> dict:
    """Decide whether a render can coexist with inference or needs eviction.

    Body: {"prompt": <ComfyUI API-format prompt>}. Returns the admission
    decision; the ComfyUI gate calls this on POST /prompt. Fail-soft: if the
    arbiter is missing we return coexist (matches the gate's fail-open).
    """
    body = await request.json()
    prompt = body.get("prompt") if isinstance(body, dict) else None
    if not isinstance(prompt, dict):
        prompt = {}
    footprint = estimate_footprint_gb(
        prompt,
        _comfyui_models_dir(),
        peak_factor=float(os.environ.get("HAL0_GPU_RENDER_PEAK_FACTOR", "1.3") or 1.3),
        video_peak_factor=float(os.environ.get("HAL0_GPU_RENDER_VIDEO_PEAK_FACTOR", "1.6") or 1.6),
        unknown_model_gb=float(os.environ.get("HAL0_GPU_RENDER_UNKNOWN_MODEL_GB", "8") or 8),
    )
    arbiter = _get_arbiter(request)
    if arbiter is None:
        return {"decision": "coexist", "footprint_gb": footprint, "envelope_gb": 0.0, "free_gb": 0.0, "evict_plan": []}
    d = arbiter.plan_admission(footprint)
    return {
        "decision": d.decision,
        "footprint_gb": round(d.footprint_gb, 2),
        "envelope_gb": round(d.envelope_gb, 2),
        "free_gb": round(d.free_gb, 2),
        "evict_plan": list(d.evict_plan),
    }
```

Add to the `/status` return dict (after `"arbiter": arbiter_block,`):

```python
        "gpu_budget": (
            {k: round(v, 2) for k, v in arbiter.compute_budget().items()}
            if arbiter is not None
            else None
        ),
```

(`arbiter` is already fetched into `arbiter_block` via `_get_arbiter`; reuse the local `arbiter` variable — if it's only available inside the try, hoist `arbiter = _get_arbiter(request)` to a local before the return.)

- [ ] **Step 4: Run** → PASS. `PYTHONPATH=src /home/halo/dev/hal0/.venv/bin/python -m pytest tests/api/test_comfyui_admit.py -q`

- [ ] **Step 5: Format + commit**

```bash
cd /home/halo/dev/wt/gpu-coexist
/home/halo/dev/hal0/.venv/bin/ruff format src/hal0/api/routes/comfyui.py tests/api/test_comfyui_admit.py
git add src/hal0/api/routes/comfyui.py tests/api/test_comfyui_admit.py
git commit -m "feat(comfyui): /admit decision route + status.gpu_budget

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Rewrite the ComfyUI gate to call `/admit`

**Files:**
- Modify: `installer/comfyui/custom_nodes/hal0_gpu_gate.py`
- Test: `tests/comfyui/test_hal0_gpu_gate.py` (extend existing)

**Interfaces:**
- Consumes: `POST {HAL0_API}/api/comfyui/admit` (Task 4).
- Produces: `decide_action(method, path, admit: dict | None) -> tuple[bool, dict | None]` → `(allow, gate_body_or_None)`; stdlib-only; fail-open.

- [ ] **Step 1: Write the failing test**

```python
# tests/comfyui/test_hal0_gpu_gate.py  (add these; keep existing should_block-era tests only if still valid — otherwise replace)
import importlib.util, pathlib
spec = importlib.util.spec_from_file_location(
    "hal0_gpu_gate",
    pathlib.Path(__file__).resolve().parents[2]
    / "installer/comfyui/custom_nodes/hal0_gpu_gate.py",
)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def test_non_prompt_path_passes():
    allow, body = gate.decide_action("GET", "/queue", None)
    assert allow and body is None


def test_coexist_passes():
    allow, body = gate.decide_action("POST", "/prompt", {"decision": "coexist"})
    assert allow and body is None


def test_needs_exclusive_blocks_with_switch_message():
    allow, body = gate.decide_action("POST", "/prompt", {"decision": "needs_exclusive", "evict_plan": ["chat"]})
    assert allow is False
    assert "full GPU" in body["error"]["message"] or "freeing inference" in body["error"]["message"]


def test_wont_fit_blocks():
    allow, body = gate.decide_action("POST", "/prompt", {"decision": "wont_fit"})
    assert allow is False


def test_admit_unreachable_fails_open():
    allow, body = gate.decide_action("POST", "/prompt", None)  # None == API unreachable
    assert allow and body is None
```

- [ ] **Step 2: Run** → FAIL (`decide_action` undefined). `PYTHONPATH=src /home/halo/dev/hal0/.venv/bin/python -m pytest tests/comfyui/test_hal0_gpu_gate.py -q`

- [ ] **Step 3: Implement** — replace the `should_block`/`GATE_BODY` decision core with `/admit`-driven logic (keep `_install`, swap the middleware to POST the prompt body to `/admit` and call `decide_action`). Key new code:

```python
HAL0_ADMIT_URL = os.environ.get(
    "HAL0_COMFYUI_ADMIT_URL", "http://127.0.0.1:8080/api/comfyui/admit"
)

_BLOCK_PATHS = frozenset({"/prompt", "/api/prompt"})


def _gate_body(decision: str) -> dict:
    if decision == "wont_fit":
        msg = "This workflow exceeds total GPU memory and cannot run."
    else:  # needs_exclusive
        msg = (
            "This render needs the full GPU. hal0 is freeing inference slots "
            "(agent kept last) — wait a moment and queue again."
        )
    return {
        "error": {"type": "hal0_gpu_gate", "message": msg, "details": f"admission={decision}", "extra_info": {}},
        "node_errors": {},
    }


def decide_action(method: str, path: str, admit: "dict | None") -> "tuple[bool, dict | None]":
    """(allow, gate_body). Pass-through unless a job submission is gated."""
    if method != "POST" or path not in _BLOCK_PATHS:
        return True, None
    if not isinstance(admit, dict):  # API unreachable / unparseable → fail-open
        return True, None
    decision = admit.get("decision")
    if decision in (None, "coexist"):
        return True, None
    return False, _gate_body(decision)


def _fetch_admit(prompt_bytes: bytes) -> "dict | None":
    try:
        req = urllib.request.Request(
            HAL0_ADMIT_URL, data=prompt_bytes, headers={"Content-Type": "application/json"}, method="POST"
        )
        with urllib.request.urlopen(req, timeout=_STATUS_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
```

Middleware body: read the request body once, wrap as `{"prompt": <parsed>}`, POST to `/admit`, then `allow, body = decide_action(...)`; on block, also fire-and-forget the switchover (`POST /api/comfyui/switchover {"mode":"generation"}`) in a thread so eviction starts. Return `web.json_response(body, status=403)` when blocked. (The implementer must re-inject the consumed body for the downstream handler when allowing — use `await request.read()` then reconstruct, following ComfyUI middleware patterns; verify on CT105.)

- [ ] **Step 4: Run** → PASS. `PYTHONPATH=src /home/halo/dev/hal0/.venv/bin/python -m pytest tests/comfyui/test_hal0_gpu_gate.py -q`

- [ ] **Step 5: Format + commit**

```bash
cd /home/halo/dev/wt/gpu-coexist
/home/halo/dev/hal0/.venv/bin/ruff format installer/comfyui/custom_nodes/hal0_gpu_gate.py tests/comfyui/test_hal0_gpu_gate.py
git add installer/comfyui/custom_nodes/hal0_gpu_gate.py tests/comfyui/test_hal0_gpu_gate.py
git commit -m "feat(comfyui): gate calls /admit — coexist passes, heavy renders switch

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Dashboard — budget readout + auto-switch only when needed

**Files:**
- Modify: `ui/src/api/hooks/useComfyui.ts` (surface `gpu_budget` in the status type)
- Modify: `ui/src/dash/comfyui-pane.jsx` (small reserve/envelope/free readout; restore a render/switch affordance that switches only on `needs_exclusive`)
- Test: `ui/tests/e2e/specs/comfyui-arbiter-v3.spec.ts` (extend)

**Interfaces:**
- Consumes: `/api/comfyui/status.gpu_budget`, existing `useComfyuiSwitchover`.

- [ ] **Step 1: Write the failing e2e test**

```ts
// add to comfyui-arbiter-v3.spec.ts, using the comfyV2Status() mock extended with gpu_budget
test('pane shows GPU budget readout', async ({ page }) => {
  await page.route('**/api/comfyui/status', (route: any) =>
    json(route, { ...comfyV2Status(), gpu_budget: { gtt_ceil_gb: 96, reserve_gb: 33, envelope_gb: 57, free_gb: 57, margin_gb: 6 } }))
  await gotoImageTab(page)
  await expect(page.locator('.comfy-v2-pane')).toContainText('57')  // envelope GB
})
```

- [ ] **Step 2: Run** → FAIL. `cd ui && npx playwright test comfyui-arbiter-v3 --project=chromium -g "budget readout"`

- [ ] **Step 3: Implement** — add `gpu_budget?: {gtt_ceil_gb;reserve_gb;envelope_gb;free_gb;margin_gb}` to the status interface in `useComfyui.ts`; render a compact readout in the ComfyUI pane (e.g., in the memory subcard: `envelope {free}/{envelope} GB · reserve {reserve}`). Restore a small "switch to generation" control that calls `useComfyuiSwitchover` ONLY for the heavy path (the coexist case needs no switch). Keep it minimal.

- [ ] **Step 4: Run** → PASS, plus `npm run build` and `npm run typecheck` clean.

- [ ] **Step 5: Commit**

```bash
cd /home/halo/dev/wt/gpu-coexist
git add ui/src/api/hooks/useComfyui.ts ui/src/dash/comfyui-pane.jsx ui/tests/e2e/specs/comfyui-arbiter-v3.spec.ts
git commit -m "feat(dash): ComfyUI GPU-budget readout + switch only when needed

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Config docs + installer env template

**Files:**
- Modify: the installer api.env template (find via `grep -rl HAL0_COMFYUI_SWITCHOVER_ENABLED installer/`) — add the new `HAL0_GPU_*` knobs with comments and defaults.
- Modify: `docs/` ComfyUI/GPU page if one exists (find via `grep -rln -i "switchover\|gpu arbiter" docs/`), documenting coexistence + the partition + agent-last eviction.

- [ ] **Step 1:** Add the documented env knobs (values verbatim from Global Constraints) to the api.env template with one-line comments.
- [ ] **Step 2:** Add a short "GPU coexistence" doc section (partition diagram, three outcomes, agent-last eviction, the memory-safety property).
- [ ] **Step 3: Commit**

```bash
cd /home/halo/dev/wt/gpu-coexist
git add -A
git commit -m "docs(comfyui): document GPU coexistence config + behavior

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Final verification (after all tasks)

- [ ] `PYTHONPATH=src /home/halo/dev/hal0/.venv/bin/python -m pytest tests/slots/test_comfyui_footprint.py tests/slots/test_gpu_budget.py tests/slots/test_arbiter_admit.py tests/api/test_comfyui_admit.py tests/comfyui/test_hal0_gpu_gate.py -q` → all pass
- [ ] `/home/halo/dev/hal0/.venv/bin/ruff format --check src tests` → clean
- [ ] `cd ui && npm run build && npm run typecheck && npx playwright test comfyui-arbiter-v3 imagegen-v2 --project=chromium` → green
- [ ] Open PR; after CI green, deploy to CT105 via `scripts/deploy.sh` AND copy the updated `hal0_gpu_gate.py` into `/mnt/ai-models/comfyui/custom_nodes/` + restart the img slot so ComfyUI reloads the gate.
- [ ] CT105 live check: a SDXL render coexists with `agent` loaded (no switch); a Qwen-Image bf16 render reports `needs_exclusive` and evicts `chat` before `agent`.

## Notes

- The gate file is deployed to CT105 at `/mnt/ai-models/comfyui/custom_nodes/hal0_gpu_gate.py` (bind-mounted; not carried by `git reset`). `scripts/deploy.sh` updates the repo + UI + hal0-api but NOT the in-container custom node — copy it manually and restart the img slot so ComfyUI re-imports it.
- Keep the backend launch route + switchover endpoints intact; this plan changes the *gate decision*, not the switchover write-path.
