# Memory-aware GPU coexistence for ComfyUI on hal0

**Date:** 2026-06-17
**Status:** Design — pending implementation
**Area:** GPU arbiter (`src/hal0/slots/arbiter.py`), ComfyUI gate
(`installer/comfyui/custom_nodes/hal0_gpu_gate.py`), ComfyUI routes
(`src/hal0/api/routes/comfyui.py`), dashboard ComfyUI pane
(`ui/src/dash/comfyui-pane.jsx`).

## Problem

The hal0 GPU arbiter treats the Strix Halo iGPU as **exclusive**: it is either
in `inference` mode (LLM slots) or `generation` mode (ComfyUI). `hal0_gpu_gate.py`
(a ComfyUI custom node) 403-blocks `POST /prompt` whenever
`/api/comfyui/status.mode == "inference"`, with the message *"The GPU is in
inference mode (LLM slots loaded). Flip the Image Gen switch in the hal0
dashboard, then queue again."*

Two things are now wrong:

1. **The switch it names no longer exists.** The V2 "Render hero" pane redesign
   (PR #878) dropped the inference⇄generation toggle. The backend switchover
   endpoint (`POST /api/comfyui/switchover`) and the `useComfyuiSwitchover` hook
   still exist, but there is no UI control — so the gate points users at a
   non-existent switch.
2. **The exclusivity is overly conservative.** Measured on CT105: GTT ceiling is
   now **96 GiB** (the 80→96 reboot landed), the LLM stack is ~33 GiB when fully
   loaded (`agent` chadrock-35B ≈ 18 GiB + `chat` qwopus-27B ≈ 15 GiB), and most
   renders are 8–30 GiB (SDXL ~8, Wan 2.2 14B fp8 ~30; only Qwen-Image bf16 is
   heavy at ~47 GiB). So a render and the LLM stack usually **fit together**. The
   gate blocks on the `mode` flag, not on real memory pressure — and right now,
   with the LLM slots idle-unloaded (GTT 0 used), renders are blocked even though
   the GPU is empty.

The real cost of running both at once is **shared-iGPU compute contention**
(both run slower), not OOM — and that cost is accepted.

## Goal

Let ComfyUI renders **coexist with inference when they fit in the GTT budget**,
keeping inference always-available, and **auto-switch (evict LLMs) only when a
render genuinely needs the whole GPU** — evicting `agent` last.

## Design

### GTT partition (inference-priority)

Partition the GTT ceiling into three zones:

```
│◄──── LLM reserve R ────►│◄──── render envelope G ────►│ margin │
   kept free for the LLM        renders coexist here          safety
   stack (agent + chat)         without evicting anyone        headroom
```

- **R** (`HAL0_GPU_LLM_RESERVE_GB`) — GTT held free so the LLM stack can load /
  stay loaded on demand. Default: derived from the resident LLM-group slots
  (`agent` + `chat` footprints); fallback constant if it can't be derived.
- **margin** (`HAL0_GPU_GTT_MARGIN_GB`, default 6) — headroom for
  activation/decode spikes and allocator overhead.
- **G** = `gtt_ceil − R − margin` — the render envelope (~57 GiB at
  ceil 96, R 33, margin 6).

A render is **admitted to coexist** iff its estimated footprint ≤ G. The gate is
**admission control**: by capping coexisting renders at G it keeps R free for
the LLM stack. This is independent of whether the LLM slots are currently loaded —
R is always held in reserve, so inference can load at any time ("leave inference
active").

### Decision outcomes

On `POST /prompt` (and `POST /api/prompt`):

| Estimated render footprint | Decision | Action |
|---|---|---|
| ≤ G | `coexist` | Allow. Render runs alongside inference; no eviction. |
| G < fp ≤ ceil − margin | `needs_exclusive` | Evict the minimum LLM slots (agent last) to grow the render's available GTT until it fits; then allow. |
| > ceil − margin | `wont_fit` | Block with an honest "this workflow exceeds total GPU memory" message. |

### Memory-safety property (why this can't crash inference)

GTT is **hard-capped at `ceil`** by the amdgpu driver, and loaded LLM weights are
already resident. If a render's *actual* peak overshoots our estimate and tries
to exceed available GTT, the **render's allocation fails** (ComfyUI errors on that
job) — it cannot evict or corrupt an already-loaded LLM. The worst case is a
failed render, never a crashed `agent`. Footprint estimation therefore only needs
to be *good enough to avoid pointless failed renders and unnecessary switches*; it
is not a safety-critical bound. The reserve R + conservative estimate + margin
keep failures rare.

### Footprint estimation

Estimation lives in **hal0-api** (testable, not in the stdlib-only gate). Given
the workflow's API-format prompt JSON:

1. Walk nodes; for each model-loading `class_type`
   (`CheckpointLoaderSimple`, `UNETLoader`/`UNETLoaderGGUF`, `CLIPLoader`/
   `DualCLIPLoader`, `VAELoader`, `LoraLoader*`, video loaders, etc.), read the
   referenced model filename input.
2. Map each filename to its on-disk size under
   `/mnt/ai-models/comfyui/models/<subdir>/`.
3. Sum unique files (dedupe shared encoders/VAEs), apply a **peak multiplier**
   (`HAL0_GPU_RENDER_PEAK_FACTOR`, default ~1.3; a higher factor for
   video-latent workflows detected by video loader/sampler node types).

Unknown/unmappable models contribute a conservative default rather than zero, so
an unrecognised workflow biases toward `needs_exclusive` (safe), not `coexist`.

A node-type → loader-input mapping table lives in one module
(`comfyui_footprint.py`) so it is easy to extend as new model families land.

### Eviction policy — agent last

When a render is `needs_exclusive`, free the **minimum** LLM-group slots needed,
in ascending priority, recomputing available GTT after each eviction and stopping
as soon as the render fits:

```
evict order:  utility / embed / stt / tts / rerank  →  chat  →  agent  (LAST)
```

- A render that fits after evicting only `chat` keeps `agent` resident (partial
  coexistence — the operator/Hermes brain stays up).
- `agent` is evicted only when a render needs nearly the entire GTT (true
  exclusive mode). This reinforces `agent`'s existing reserved + non-deletable
  slot status.
- Order is configurable via `HAL0_GPU_EVICT_PRIORITY` (a slot-name list); `agent`
  is pinned last regardless, as a hard invariant in code.

### Components & changes

1. **`src/hal0/slots/comfyui_footprint.py`** (new) — pure functions:
   `estimate_footprint(prompt, model_dir, peak_factor) → GiB` and the
   loader-node mapping table.
2. **`src/hal0/slots/arbiter.py`** — add a budget/admission API:
   `admit_render(footprint) → AdmitDecision{coexist | needs_exclusive(evict_plan)
   | wont_fit}` using the R/G/margin partition and the eviction-priority list;
   add an incremental "evict-to-fit, agent last" helper. The hard
   `GpuMode.LLM + img group → GpuInferenceMode` block is relaxed: img dispatch is
   allowed when the render fits the envelope.
3. **`src/hal0/api/routes/comfyui.py`** — new `POST /api/comfyui/admit` (body:
   prompt JSON) → `{decision, footprint_gb, envelope_gb, free_gb, evict_plan}`.
   `/api/comfyui/status` gains a `gpu_budget` block
   `{gtt_ceil_gb, reserve_gb, envelope_gb, free_gb}` for the dashboard.
4. **`installer/comfyui/custom_nodes/hal0_gpu_gate.py`** — rewrite: instead of
   blocking on `mode == "inference"`, POST the intercepted prompt to
   `/api/comfyui/admit`. `coexist` → pass through. `needs_exclusive` → 403 with a
   message that names the **real** path ("this render needs the full GPU; hal0 is
   freeing inference slots (agent kept last) — re-queue in a moment") and
   fire-and-forget triggers the eviction/switch via hal0-api. `wont_fit` → 403
   honest error. Stays **fail-open** (allow) if hal0-api is unreachable.
5. **`ui/src/dash/comfyui-pane.jsx`** — surface the `gpu_budget` (a small
   reserve/envelope/free readout) and restore a mode affordance:
   - "Render here" / queue actions in the pane do **auto-switch-then-queue** for
     the `needs_exclusive` case using the existing `useComfyuiSwitchover` hook,
     with the blast-radius confirm.
   - For coexisting renders, no switch — just queue.
6. **Config** (documented in the installer env template):
   `HAL0_GPU_LLM_RESERVE_GB`, `HAL0_GPU_GTT_MARGIN_GB`,
   `HAL0_GPU_RENDER_PEAK_FACTOR`, `HAL0_GPU_EVICT_PRIORITY`.

### Out of scope (YAGNI)

- Hard GTT cgroup limits on the ComfyUI container (the hard ceiling + admission
  control already make the failure mode safe).
- Per-frame/dynamic re-estimation during a render.
- Reserving GTT for LLM slots that are not in the resident inference set.
- Changing compute scheduling / perf levels (contention is accepted).

## Testing

- **`tests/slots/test_comfyui_footprint.py`** — estimation over representative
  prompts (SDXL, Qwen-Image bf16, Wan 2.2 video): unique-file dedupe, peak
  multiplier, unknown-model conservative default.
- **`tests/slots/test_arbiter_admit.py`** — `admit_render` across the three
  outcomes at varied ceil/reserve/used; **evict-to-fit ordering puts `agent`
  last**; stops as soon as the render fits; `wont_fit` when nothing frees enough.
- **`tests/comfyui/test_hal0_gpu_gate.py`** (extend) — `should_admit` decision
  wiring: coexist passes, needs_exclusive 403s with the switch hint, fail-open on
  unreachable API.
- **`tests/api/test_comfyui_*.py`** — `/admit` response shape; `status.gpu_budget`.
- **UI e2e** — pane renders the budget readout; queue triggers switchover only on
  `needs_exclusive`.
- CT105 live verification: coexist a small render with `agent` loaded; confirm a
  heavy render evicts `chat` first and keeps `agent`.

## Rollout

- Default `HAL0_GPU_LLM_RESERVE_GB` conservatively (≈ measured agent+chat) so
  coexistence is opt-in-safe out of the box.
- The gate stays fail-open, so a bad deploy degrades to "allow renders," never to
  "ComfyUI bricked."
