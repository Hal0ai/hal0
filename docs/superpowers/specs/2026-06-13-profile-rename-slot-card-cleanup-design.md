# Profile rename + explicit backend + slot-card/section cleanup

**Date:** 2026-06-13
**Branch:** `fix/profile-rename-slot-cards` (off `origin/main` @ cb9ab16)
**Status:** Design — awaiting review

## Problem

Three coupled issues on the Slots dashboard:

1. **Redundant profile names.** Seed profile slugs repeat the backend that the
   card already shows next to them: the bottom-left chip renders `gpu-rocm` /
   `gpu-vulkan` / `cpu` / `npu`, immediately followed by a profile slug that
   restates it — `gpu-rocm` + `moe-rocmfp4`, `gpu-vulkan` + `vulkan-std`,
   `cpu` + `kokoro-cpu`.

2. **ROCm/Vulkan is implicit.** A profile carries the ROCm-vs-Vulkan choice
   only inside its `image` tag (`…:rocm-7.2.4-rocmfp4-server` vs
   `…:vulkan-radv-server`). The slot's backend is then derived by a string
   sniff — `(image||"").includes("vulkan") ? "gpu-vulkan" : "gpu-rocm"`
   (`ui/src/dash/slot-modals.jsx:156`). The C7 comment already flags this as an
   "interim heuristic." There is no `backend` field on `ProfileConfig`.

3. **Old/duplicate slot cards.** `/api/slots` merges real slots
   (`kind:"local"`) with **synthetic upstream pseudo-entries**
   (`src/hal0/slot_view/__init__.py:533-549`). The `hal0` entry has
   `kind:"slot"`, `type:null`, no `profile`/`runtime`, and a hard-coded
   `backend:"vulkan"`. With no `runtime`, the grid renders it through the
   **legacy non-container branch** (`ui/src/dash/slots.jsx:286-303`) — a
   stale-style card wedged between real cards and the FLM section. Separately,
   the serializer emits `group:null`, `device:null`, `device_class:null` for
   every real slot, so the section/grouping logic (`s.group`,
   `s.device === "npu"`) silently fails.

## Decisions (locked with user)

- Lowercase slugs + pretty display map (validator stays `^[a-z0-9][a-z0-9_-]{0,31}$`).
- Rename the existing 6 seeds only; do not scaffold Embed/Rank/STT/CPU yet.
- Explicit `backend` field on `ProfileConfig`.
- Dev-only: no alias/migration layer — recreate CT105 slots against new names.
- Fresh branch off main; include the full card/section cleanup in this pass.

## Design

### 1. Seed rename

| Current slug        | New slug   | Display   | backend | device_class | mtp   |
|---------------------|------------|-----------|---------|--------------|-------|
| `moe-rocmfp4`       | `rocm`     | ROCm      | rocm    | gpu          | false |
| `dense-mtp-rocmfp4` | `rocm-mtp` | ROCm-MTP  | rocm    | gpu          | true  |
| `vulkan-std`        | `vulkan`   | Vulkan    | vulkan  | gpu          | false |
| `flm-npu`           | `flm`      | FLM       | null    | npu          | false |
| `kokoro-cpu`        | `tts`      | TTS       | null    | cpu          | false |
| `comfyui`           | `comfyui`  | ComfyUI   | null    | img          | false |

Images and flags are unchanged. The namesake GPU profiles (`rocm`, `vulkan`)
remain broad-compatibility "std" templates carrying the must-have defaults
(`-fa on`, `--no-mmap`, batch sizing); MTP/FP4 specialization lives in the
`-mtp` variant.

### 2. Explicit backend field

Add to `ProfileConfig` (`src/hal0/config/schema.py`):

```python
backend: Literal["rocm", "vulkan"] | None = Field(
    default=None,
    description=(
        "GPU runtime this profile targets. Authoritative source for the "
        "ROCm-vs-Vulkan choice (replaces sniffing the image tag). None for "
        "non-GPU profiles (npu/cpu/img), where device_class drives display."
    ),
)
```

Set `backend` on the three GPU entries in `SEED_PROFILES` and `profiles.toml`;
leave it absent (→ `None`) on `flm`/`tts`/`comfyui`. `device_class` keeps its
existing role (gpu/cpu/npu/img). Backend is the *GPU runtime selector*;
device_class is the *silicon class* — orthogonal, no field is overloaded.

### 3. Backend becomes profile-authoritative

- **UI create/edit:** delete the `image.includes("vulkan")` heuristic in
  `slot-modals.jsx`; read `profile.backend` from the profiles query instead.
- **Serializer:** in `serialize_slot` (`slot_view/__init__.py:139`), lift the
  resolved profile's `backend` and `device_class` to the top level so the slot
  payload always carries both (today only `backend` is populated, via
  metadata). One source of truth for the card.

### 4. Card display (`ui/src/dash/slots.jsx`)

- Bottom-left chip text becomes the **pretty profile name**
  (`ROCm`/`Vulkan`/`ROCm-MTP`/`FLM`/`TTS`/`ComfyUI`) via a display map shared
  with `profiles.jsx`. Drop the `gpu-rocm` device-tag string.
- Chip **color** keyed off `backend` (`rocm`/`vulkan`), falling back to
  `device_class` (`npu`/`cpu`/`img`) when backend is null. Reuse existing
  `dev-*` classes.
- The container image-tag chip (`slots.jsx:257-285`) stays as the secondary
  detail; the profile-name chip is the primary identity.

### 5. Kill the old cards

- **Filter synthetic entries** out of the SlotCard grid **UI-side**: the grid
  renders only `kind === "local"` slots; `_synthetic`/`kind:"slot"` phantoms
  like `hal0` are excluded from the card map. The `/api/slots` contract is
  left intact (synthetic upstream visibility is a real feature consumed
  elsewhere) — this is purely a render filter, not an API change.
- **Fix grouping:** derive the section from `device_class` (now always
  emitted) rather than the null `group`/`device`:
  gpu → Chat, npu → NPU/FLM stack, embedding/reranking/tts → Capabilities,
  img → Image-Gen tab.
- **Remove the dead legacy branch:** all slots are `runtime:"container"`, so
  the non-container render path (`slots.jsx:286-303`) is dead for real slots —
  delete it, collapsing the card to the single container path.

### 6. Dev-only cleanup on CT105

Rebuild `ui/dist` and recreate the 6 slots pointing at the new profile names
(`scripts/deploy.sh` / `make deploy` to rebuild the bundle; bare `git reset`
leaves the dashboard bundle stale).

## Touch-points

- `src/hal0/config/schema.py` — `ProfileConfig.backend`; rename
  `SEED_PROFILES` keys; `DEVICE_DEFAULT_PROFILES` values; seed `backend`.
- `installer/etc-hal0/profiles.toml` — rename sections; add `backend`.
- `src/hal0/slot_view/__init__.py` — lift `backend`/`device_class` from the
  resolved profile (API unchanged otherwise; synthetic entries stay in the
  payload, filtered UI-side).
- `ui/src/dash/slot-modals.jsx` — drop image-sniff; read `profile.backend`.
- `ui/src/dash/slots.jsx` — chip text/color; device_class-driven grouping;
  remove legacy branch; filter synthetic.
- `ui/src/dash/profiles.jsx` — `PROFILE_INTENT` keys; shared pretty-name map.
- Tests: `tests/config/test_profiles.py`, `tests/profiles/test_catalog.py`,
  `tests/api/test_profiles_*`, ui e2e specs referencing old slug names.

## Out of scope (YAGNI)

- New Embed/Rank/STT/general-CPU seed profiles (add when those slots are
  containerized).
- Any alias/back-compat layer for old profile names (dev-only, recreate slots).
- Flag/perf retuning of the renamed profiles.

## Testing

- Schema: `backend` validates/serializes; seed catalog matches `profiles.toml`
  (the existing seed-drift guard).
- Profiles route: rename reflected in `/api/profiles`; seeds still immutable.
- Serializer: `device_class`/`backend` present; synthetic entries excluded.
- UI: card renders pretty name + backend color; sections group by device_class;
  no legacy-branch card; phantom `hal0` gone. Update e2e selectors.
