# FLM 1.0 Rework Plan — Bring FastFlowLM Inline with hal0 v1.0

> **Date:** 2026-07-22 · **Source:** user directives + codebase investigation + `spec-hw-slot-ownership.md` + `spec-flags-ownership.md`
>
> FLM = FastFlowLM, a lightweight (~17 MB) NPU-first runtime from AMD (github.com/FastFlowLM/FastFlowLM).
> Unlike llama-server inference slots, **FLM runs ONE process simultaneously serving THREE slots**:
> Chat, STT (ASR), and Embed. This trio model is intentionally different from inference slots
> and follows its own architecture. The rework respects this difference.

---

## 1. Architecture — FLM ≠ Inference Slot

| Axis | Inference Slot (llama-server) | FLM (NPU) |
|------|------------------------------|-----------|
| **Process model** | One process per slot | One process for Chat + STT + Embed |
| **Models** | GGUF files in model store | FLM-native tags pulled via `flm pull` |
| **Runner image** | `rocmfpx` / `vulkanfpx` / `cpu` | `ghcr.io/hal0ai/hal0-toolbox-flm:0.9.44` |
| **Dispatch** | `/v1/chat/completions` via dispatcher | Chat via dispatcher; STT/Embed via `NpuTrioRouter` (direct to container) |
| **Slot edit** | Device · NGL · Threads · Binary grid | NPU toggles: Chat/ASR/Embed + model selection |
| **Model page** | Registry GGUF rows | FLM tag catalog (host `flm list -j`) |
| **Profile** | Device-agnostic logical tune | Single `profile.flm` — no flags (FLM doesn't use them) |

---

## 2. Phase 1 — Data/config alignment (S risk, mechanical)

### 2.1 Strip `device_class` from FLM profile

FLM's profile in `seed_profiles.toml` still carries `device_class = "npu"` — every other profile was stripped
in the 16-profile rework (commits `9a0798f9` / `6c4476e9`). FLM was missed.

| File | Change |
|------|--------|
| `src/hal0/config/data/seed_profiles.toml` | Remove `device_class = "npu"` from `[profile.flm]` |
| `tests/slots/test_seed_profiles.py` | Add FLM to `test_no_device_class_in_seed_profiles` (assert `device_class is None`) |

**Rationale:** The device-class association comes from `RUNNER_IMAGES["flm"].device_class = "npu"` + the slot's `device = "npu"`. The profile should be device-agnostic like every other profile.

---

### 2.2 Populate FLM HW grid fields

FLM's static seed TOML was NOT updated in the seeded-profile rework (commit `e73f6399` touched 8 other seeds).
It needs the HW grid fields populated with NPU-appropriate defaults.

| File | Change |
|------|--------|
| `installer/etc-hal0/slots/flm.toml` | Add after `profile = "flm"`:
| | `n_gpu_layers = 0` (NPU doesn't use GPU layers)
| | `threads = 0` (unset — runtime default)
| | `binary = ""` (derive from `RUNNER_IMAGES["flm"]`)
| `tests/slots/test_slot_schema.py` | Add FLM to seed validation (assert `SlotConfig.model_validate` passes) |

---

### 2.3 FLM image resolution cleanup

FLM's container image is `ghcr.io/hal0ai/hal0-toolbox-flm:0.9.44` (confirmed latest on GitHub Packages).
The resolution chain is: `slot.image_pin` → `HAL0_TOOLBOX_IMAGE_FLM` env → manifest digest → default.

| File | Change |
|------|--------|
| `src/hal0/runners/__init__.py` | No change — `_FLM_IMAGE` = `"ghcr.io/hal0ai/hal0-toolbox-flm:0.9.44"` already correct |
| `src/hal0/providers/flm.py` | Sunset-stamp the env-var fallback: `# HAL0-SUNSET: v1.1.0 — HAL0_TOOLBOX_IMAGE_FLM env override` |
| `ARCHITECTURE.md` | Note FLM image resolution as canonical |

**Rationale:** `0.9.44` is already in code. No version bump needed. The env-var fallback is kept for operational flexibility but marked for future removal.

---

## 3. Phase 2 — Models page: FLM tab + icons + 3-dot menu (M risk, UI + API)

### 3.1 API: FLM model catalog endpoint (verify existing)

The endpoint `GET /api/slots/flm/models` already returns:
```json
{"models": [{"model": "qwen3:0.6b", "installed": false, "capabilities": ["chat"], "family": "qwen3"}]}
```

**No API changes needed** — the endpoint is fully functional. It returns ALL known FLM models
(installed + available), probed from the host `flm list -j` (primary) with container-exec fallback
(`podman exec hal0-slot-flm flm list --json`).

---

### 3.2 UI: Add "NPU / FLM" tab to models page

**File:** `ui/src/dash/models.jsx`

Add a **fourth tab** to the existing three-tab bar:

| Tab | ID | Content |
|-----|-----|-----|
| Inference Models | `inference` | Existing (unchanged) |
| Image / ComfyUI | `image` | Existing (unchanged) |
| **NPU / FLM** | **`npu`** | **NEW — FLM models only** |
| Upstream Models | `upstream` | Existing (unchanged) |

**FLM tab behavior:**
- Fetches from `GET /api/slots/flm/models` (returns all FLM models, installed + available)
- Groups into **Installed** (top) and **Available** (below) sections
- Text search across `model` (tag) and `family` fields
- Filter chips: Chat, Embed, STT (from `capabilities` array)

---

### 3.3 UI: Download icon + checkmark icon per row

Update **ALL tabs** (not just FLM) to use a consistent icon scheme:

| State | Icon | Behavior |
|-------|------|----------|
| **Installed** | ✅ green checkmark | Click → opens edit drawer for that model |
| **Not installed** | ⬇️ download icon (muted/blue) | Click → triggers pull/install |

**FLM-specific pull flow:**
- Click download → `POST /api/models/pull { source: "flm", tag: "qwen3:0.6b" }`
- hal0 spawns host `flm pull <tag>` (existing `flm_pull_command()` machinery)
- SSE progress updates in DownloadsPane (existing `usePullsList()` widget)
- On completion: row flips to ✅ checkmark

---

### 3.4 UI: 3-dot icon on every model row → quick settings

Add a `•••` (three-dot vertical) icon at the **right edge of every model row** across ALL tabs.

**Dropdown menu (model-type-aware):**

| Action | Inference Models | FLM Models | ComfyUI Models |
|--------|-----------------|------------|----------------|
| **Edit model settings** | Opens ModelDrawer for this model ID | Opens NPU model settings (see §4) | Opens ComfyUI settings |
| **Assign to slot** | Jumps to slot assign picker with this model pre-selected | Jumps to FLM slot with this model pre-selected | (same) |
| **Set as default** | `POST /api/models/{id}/default` | N/A (FLM models are per-slot) | N/A |
| **Duplicate** | Opens DuplicateModelDialog | N/A | N/A |
| **Delete/Remove** | `DELETE /api/models/{id}` | `DELETE /api/models/{id}?source=flm` → host `flm remove <tag>` | (same) |

**Implementation notes:**
- Use the existing `ModelDrawer` component for Inference models
- For FLM models, the "Edit model settings" option opens the NPU slot settings drawer (since FLM model settings = per-slot NPU config)
- 3-dot icon: use `•••` from the existing icon set or `MoreVertical` / `EllipsisVertical` from lucide-react if available

---

## 4. Phase 3 — NPU slot edit: unique fields (M risk, UI + API)

### 4.1 Current state — what already exists

The NPU drawer in `ui/src/dash/slot-modals.jsx` already has:
- Chat toggle + model dropdown (selectable)
- ASR toggle (fixed model: `whisper-v3`)
- Embed toggle (fixed model: `embed-gemma`)
- Pull-on-select for uninstalled chat models

This is good groundwork but needs expansion per user requirements.

---

### 4.2 Rework: Model selection for ALL three sub-slots

**Target:** The operator can choose the model for Chat, STT, and Embed independently.

| Sub-slot | Toggle | Model picker | Default |
|----------|--------|-------------|---------|
| **Chat** | `PillToggle` | `<select>` from `flmModels.filter(chat-capable)` | Unchanged: seeded from slot config |
| **STT (ASR)** | `PillToggle` | **NEW:** `<select>` from `flmModels.filter(stt-capable)` | `whisper-v3` |
| **Embed** | `PillToggle` | **NEW:** `<select>` from `flmModels.filter(embed-capable)` | `embed-gemma` |

**API contract change:** `NpuConfig` needs per-role model fields:

```python
class NpuConfig(BaseModel):
    chat: bool = True
    asr: bool = False
    embed: bool = False
    chat_model: str | None = None   # NEW — flm serve <tag> positional
    asr_model: str | None = None    # NEW — flm serve --asr-model <tag>
    embed_model: str | None = None  # NEW — flm serve --embed-model <tag>
```

**Container spec change** (`src/hal0/providers/flm.py`):
- `flm serve <chat_model>` (was: `flm serve <tag>`)
- `--asr 1 --asr-model <asr_model>` (was: `--asr 1` only)
- `--embed 1 --embed-model <embed_model>` (was: `--embed 1` only)

---

### 4.3 Slot edit layout

**NPU pane** in the slot drawer (when `device === "npu"`):

```
┌─ NPU ────────────────────────────────────────────────┐
│                                                       │
│  ○ Chat      [qwen3:4b                    ▾]   ✅    │
│  ○ STT       [whisper-v3                  ▾]   ✅    │
│  ○ Embed     [embed-gemma                 ▾]   ✅    │
│                                                       │
│  Image:  ghcr.io/hal0ai/hal0-toolbox-flm:0.9.44      │
│  Port:   8088                                         │
│  Context: 16384                                       │
│                                                       │
│  [Save NPU Configuration]                             │
└───────────────────────────────────────────────────────┘
```

- ○ = `PillToggle` on/off
- ▾ = `<select>` dropdown populated from `flmModels` filtered by capability
- ✅ = installed indicator next to selected model
- Image, Port, Context = read-only (set from slot config, not changed here)

---

## 5. Phase 4 — Code quality & docs (S risk, zero behavior change)

### 5.1 Sunset stamps

| File | Marker | Item |
|------|--------|------|
| `src/hal0/providers/flm.py` | `HAL0-SUNSET: v1.1.0` | `HAL0_TOOLBOX_IMAGE_FLM` env-var fallback |
| `src/hal0/providers/flm.py` | `HAL0-SUNSET: v1.1.0` | `_DEFAULT_FLM_IMAGE` back-compat alias |
| `ARCHITECTURE.md` | — | Document FLM's trio dispatch model as intentional architecture |

### 5.2 Test coverage

| Test file | What |
|-----------|------|
| `tests/slots/test_seed_profiles.py` | FLM profile: no `device_class`, no hardware flags |
| `tests/slots/test_slot_schema.py` | FLM seed validates against `SlotConfig` |
| `tests/providers/test_flm_provider.py` | `image_ref()`: `image_pin` wins; empty resolves to runner default |
| `tests/api/test_slots_flm.py` | `GET /api/slots/flm/models` returns installed + available |
| New e2e: `flm-models-tab.spec.ts` | FLM tab renders, download/checkmark icons correct, 3-dot menu opens |

---

## 6. Implementation Order

| Phase | Tasks | Risk | Est. |
|-------|-------|------|------|
| **Phase 1** | Strip FLM `device_class`, populate HW grid, image cleanup | 🟢 Low | ~30 min |
| **Phase 2** | Models page: FLM tab, icons, 3-dot menu | 🟡 Medium | ~2 hrs |
| **Phase 3** | NPU slot edit: per-role model pickers, schema change | 🟡 Medium | ~1.5 hrs |
| **Phase 4** | Sunset stamps, docs, tests | 🟢 Low | ~30 min |

---

## 7. What's Intentionally Different (Not a Bug)

These are architectural differences between FLM and inference slots — do NOT "align" them:

| Difference | Why |
|------------|-----|
| **Single process, three roles** | FLM's trio model. Chat/STT/Embed share one NPU process. |
| **No GGUF files** | FLM uses proprietary optimized model kernels, not GGUF. |
| **STT/Embed bypass dispatcher** | `NpuTrioRouter` talks directly to the FLM container — the dispatcher only knows about the chat anchor. This is correct architecture. |
| **No `n_gpu_layers` / `threads` semantics** | NPU doesn't use GPU layers or CPU threads in llama.cpp's sense. |
| **No `flags` / `extra_args`** | FLM doesn't take llama-server command-line flags. |
| **Profile has no flags** | `profile.flm.flags = ""` is correct — FLM has no device-agnostic tune knobs. |
| **Manifest pin active** | FLM has `manifest_key="flm"` while GPU runners have `None`. FLM images DO get manifest digest pinning — intentional per `spec-hw-slot-ownership.md` GPU exclusion. |

---

## 8. Deferred (Post-v1.0.0)

| Item | Reason |
|------|--------|
| FLM model registry in SQLite | FLM models have different lifecycle (host binary, not file-store). Needs `source` column. |
| Multi-process FLM dispatcher consolidation | Requires FLM upstream changes. Current single-process trio model is correct for v1.0. |
| `HAL0_TOOLBOX_IMAGE_FLM` removal | Needs deprecation window + box notification. Sunset v1.1.0. |
