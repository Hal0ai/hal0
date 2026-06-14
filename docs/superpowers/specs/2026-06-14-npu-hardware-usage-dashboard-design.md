# NPU Hardware-Usage Dashboard Pane — Design

**Date:** 2026-06-14
**Status:** Draft (awaiting review)
**Author:** Claude (brainstorming session)
**Depends on:** [FLM Universal Install + Toolbox Hardening](./2026-06-14-flm-universal-install-toolbox-hardening-design.md) (Spec A — provides a working `xrt-smi` for the real column map)

---

## 1. Why this exists

The dashboard has an honest, useful **iGPU memory map** (`memory-map.jsx`) because the iGPU's GTT is a fixed, partitionable pool — "what fits alongside what" is a real bin-packing question against a hard ceiling. Users asked whether we can build the same for the **NPU**.

The honest answer, after hardware investigation:
- The NPU has **no dedicated memory** — it shares the 128 GiB unified pool (XDNA2 is a spatial-dataflow accelerator: a 4×8 array of AIE-ML tiles streaming weights/activations from system DRAM). So a second "VRAM bar" would be a lie.
- The NPU's binding constraint is **compute (columns) + the single FLM process**, not memory.
- Memory is **already represented**: `memory-map.jsx` draws a purple NPU segment from `npu_status.model_mb`, and there's already an `NpuFlmStack` pane.

So the gap worth filling is a **compute + capacity** view, presented honestly. This spec adds a dedicated NPU pane that shows **headroom and live activity, co-equal** (the chosen framing).

---

## 2. The data reality (what we can honestly show)

Verified on CT105 (kernel 7.0.6, XDNA2 "RyzenAI-npu5", `amdxdna` 0.7.0). Both an on-box probe and official-docs research agree:

| Signal | Available? | Source / note |
|---|---|---|
| Model RAM (`model_mb`) | ✅ wired | already in `memory-map` via `_npu_status()` |
| Loaded / idle / active | ✅ free | slot state + `/sys/class/accel/accel0/device/power/runtime_status` |
| Duty-cycle % (coarse "busy") | ✅ free | `runtime_active_time` delta ÷ wall delta; ~0 ms, no sudo |
| Throughput (tok/s) | ✅ serving | FLM/slot `usage` — the real "how hard" signal |
| Columns **allocated** N/8 | ⚠ needs Spec A | `xrt-smi examine -r aie-partitions`; **allocation-time, static — NOT live utilization**; container exec, cache it |
| True util %, power, temp, clock | ❌ none | no sensors on this part; `xrt-smi` Estimated Power is Windows-only; arrives on **Linux 7.1** via `npu_tops_curr`/PMF |

### Honesty rules (load-bearing)
- The AIE grid shows **allocated** columns, labelled as such — *not* utilized. Column allocation is bookkeeping done by the amdxdna Resource Solver at hardware-context creation; there is no per-column busy meter on Linux today.
- **No** fake busy-%, power, temp, or clock gauges. "Live activity" = duty-cycle (real, coarse) + tok/s (real).
- Footer notes the true-utilization path (Linux 7.1 + `npu_tops_curr`) as a labeled future, with a TODO to switch when CT105 is on 7.1+.

---

## 3. Goals / Non-goals

### Goals
- **G1.** A dedicated, co-equal NPU pane answering both "can another workload fit alongside?" (headroom) and "is it working, how hard?" (live activity).
- **G2.** Reuse existing patterns: accordion (Inference | Image Gen | NPU), device palette (`--dev-npu` #c896ff), `_npu_status()` telemetry block, 2.5 s poll.
- **G3.** Absorb the existing `NpuFlmStack` trio controls — one NPU surface, not two.
- **G4.** Degrade gracefully: when `xrt-smi` can't run (pre-Spec-A), the grid greys but every free signal stays live. Pane never blanks.

### Non-goals
- A second "NPU VRAM" bar (dishonest — no dedicated pool).
- True utilization %, power, thermal (not available on this kernel).
- Changing FLM serving (trio = one process, asr/embed model fixed by build flags — unchanged).
- Per-column live heat/utilization (allocation only).

---

## 4. Design

### 4.1 Placement
New **`NpuPane`** = the third accordion section on the slots page, parallel to Inference and Image Gen (single-open mutual exclusion preserved). It **absorbs** `NpuFlmStack`: the FLM trio controls (chat model picker, asr/embed toggles, master load/unload) move into the pane's lower half.

### 4.2 Layout
**Collapsed strip:** `NPU ● active · 3/8 cols · duty 35% · 41 tok/s · 7.2 GB ▾`

**Expanded — hardware header (new):**
- **Hero: 4×8 AIE grid.** 8 columns × 4 rows; allocated columns lit `--dev-npu`, free columns dim. Caption: "AIE array · 4×8 · *allocated* (not utilized)" and "headroom: N / 8 free". (Allocation is per-column, so a column lights whole.)
- **Stat cluster:** tri-state dot (idle/loaded/active) + "1 process · 3 roles"; **duty-cycle** bar (labelled "runtime_active_time — real, coarse"); **throughput** bar (tok/s, "serving layer"); **Model RAM** and **Unified free** tiles.

**Expanded — absorbed FLM trio:** chat (model picker) · asr (on/off) · embed (on/off) + master load/unload, as today.

**Footer:** `RyzenAI-npu5 · amdxdna 0.7.0 · fw 1.1.2.65` + "ⓘ true util % & power arrive with Linux 7.1 (box on 7.0.6)".

**States:**
- **Idle (loaded, no traffic):** grid shows allocated columns dim, duty 0%, 0 tok/s, amber dot.
- **Degraded (`xrt-smi` unavailable):** grid greys → "column map unavailable — needs flm image rebuild"; duty/tok-s/RAM/state stay live.

### 4.3 Data flow
- **Extend `_npu_status()`** in `src/hal0/api/routes/hardware.py` (served by `/api/stats/hardware`, already consumed by the frontend) to add:
  ```
  npu_status: {
    ok, model_mb,                      // existing
    state: "absent|idle|loaded|active",
    duty_pct: number|null,             // runtime_active_time delta
    tok_s: number|null,                // from serving/slot metrics
    columns: { allocated: number, total: 8 } | null,  // null when xrt-smi unavailable
  }
  ```
- **Cadence:** `state`, `duty_pct`, `tok_s`, `model_mb` on the existing 2.5 s poll (all ~0 ms). **`columns`** comes from an `xrt-smi` container exec (~hundreds of ms) → **cached**, refreshed on slot load/unload + a slow periodic (~30 s). Never on the 2.5 s path. (Reuse the existing SWR/snapshot cache pattern.)
- **duty_pct math:** sample `runtime_active_time` (ms) and wall clock at each poll; `duty = clamp(Δactive / Δwall, 0, 1)`. `runtime_status` gives the active/suspended state cheaply.
- **columns source:** `xrt-smi examine -r aie-partitions -f JSON` exec'd into the live FLM container (which already has `/dev/accel/accel0` + apparmor=unconfined + XRT on path post-Spec-A). Parse partition column span → `allocated`; `total` = 8.

### 4.4 Frontend
- New `ui/src/dash/npu-pane.jsx` (`NpuPane`) — absorbs `NpuFlmStack` from `slots.jsx`.
- New `AieGrid` subcomponent (4×8, allocated/free/degraded states).
- Accordion wiring in the slots-page container (alongside `inference-pane.jsx` / `comfyui-pane.jsx`).
- Consume extended `npu_status` via the existing `useStatsHardware()` hook (2.5 s).
- Colors from `dashboard.css` device palette (`--dev-npu` #c896ff); reuse `engine-panes.css` accent + `.subcard` styling.

---

## 5. What changes (file map)
- `src/hal0/api/routes/hardware.py` — extend `_npu_status()` (state/duty/tok_s/columns) + cached `xrt-smi` column reader.
- `src/hal0/slots/capacity.py` — if tok/s per-slot lands here, expose it.
- `ui/src/dash/npu-pane.jsx` (new) + `AieGrid`; remove `NpuFlmStack` from `ui/src/dash/slots.jsx`.
- Slots-page accordion container — register the NPU pane.
- `ui/src/dashboard.css` / `engine-panes.css` — minor grid styles (palette already exists).
- e2e: new `ui/tests/e2e/specs/npu-pane-*.spec.ts`.

> **Collision note:** as of 2026-06-14 the `refactor/runtime-launch-plan` branch has heavy uncommitted edits to `slots.jsx`, `engine-panes.css`, `inference-pane.jsx`, `dashboard.css`. **Sequence Spec B implementation after that branch lands**, or coordinate via `wip` + worktree to avoid conflicts on these exact files.

---

## 6. Risks / caveats
- **"Allocated ≠ utilized" misread.** Mitigation: explicit label + caption + footer note. Do not animate the grid as if it were live load.
- **`xrt-smi` exec cost / flakiness.** Mitigation: cache + slow refresh; never block the 2.5 s poll; degrade to "unavailable" cleanly.
- **Dependency on Spec A.** Without the image rebuild, `columns` is always null (degraded grid). The pane still ships value (duty + tok/s + RAM + state). Decide at planning whether to ship degraded-first or gate on Spec A.
- **tok/s availability when idle/asr/embed.** tok/s is chat-path; asr/embed throughput may not map cleanly — show tok/s for chat, fall back to "active request present" as the binary busy signal.

---

## 7. Testing
- **Backend:** unit-test extended `_npu_status()` shapes for absent/idle/loaded/active; duty math from synthetic `runtime_active_time` deltas; `columns` parser against captured `xrt-smi … -f JSON` fixtures (loaded + idle); cache refresh-on-load behavior.
- **Frontend:** `AieGrid` renders allocated/free/degraded; pane collapsed/expanded/idle/degraded snapshots; absorbed trio controls still drive load/unload/model swap.
- **e2e:** mock extended `npu_status` via the γ-suite forced-mock; verify co-equal accordion behavior + degraded state.
- **Live (CT105):** with FLM loaded, grid shows real allocated columns matching `xrt-smi`; duty/tok-s move under inference.

---

## 8. Open questions (resolve in planning)
- Ship Spec B degraded-first (free signals now) or gate on Spec A's image rebuild for the real grid?
- Does per-slot tok/s already exist in `capacity.py`/metrics, or is new plumbing needed?
- Exact home of the `xrt-smi` exec+cache (reuse ComfyUI status aggregator pattern, or a dedicated NPU reader)?
- Sequencing vs the `refactor/runtime-launch-plan` UI changes (file collisions above).
