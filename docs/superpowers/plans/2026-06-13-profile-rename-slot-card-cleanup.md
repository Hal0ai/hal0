# Profile rename + explicit backend + slot-card cleanup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:test-driven-development per task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Rename the 6 seed profiles to drop redundant backend tokens, make ROCm/Vulkan an explicit `backend` field on profiles, and clean up the slot-card UI (pretty profile-name chip colored by backend, device_class-driven grouping, no synthetic phantom cards, no dead legacy branch).

**Architecture:** Backend defines the data contract (profile `backend` + serialized `device_class`/`backend` on each slot); the UI consumes it. Two parallel workstreams split on the `/api/profiles` + `/api/slots` contract below.

**Tech Stack:** Python 3.13 / Pydantic v2 / pytest (backend); React (no JSX build framework — plain `.jsx` via Vite) / Playwright e2e (frontend).

---

## Locked API contract (the interface between workstreams)

`/api/profiles` items gain a `backend` key: `"rocm" | "vulkan" | null`.
`/api/slots` items already expose `backend`; they additionally gain a
non-null `device_class` (`"gpu" | "cpu" | "npu" | "img"`) lifted from the
resolved profile.

New profile slugs + display names (shared pretty-name map):

| slug | display | backend | device_class | mtp |
|---|---|---|---|---|
| `rocm` | ROCm | rocm | gpu | false |
| `rocm-mtp` | ROCm-MTP | rocm | gpu | true |
| `vulkan` | Vulkan | vulkan | gpu | false |
| `flm` | FLM | null | npu | false |
| `tts` | TTS | null | cpu | false |
| `comfyui` | ComfyUI | null | img | false |

---

## WORKSTREAM A — Backend / contract (owner: orchestrator)

### Task A1: `backend` field on ProfileConfig + seed rename

**Files:** Modify `src/hal0/config/schema.py` (`ProfileConfig` ~739, `SEED_PROFILES` ~688, `DEVICE_DEFAULT_PROFILES` ~731). Test: `tests/config/test_profiles.py`.

- [ ] Write failing test: a `ProfileConfig` accepts `backend="rocm"`, defaults to `None`, rejects `backend="cuda"`; `SEED_PROFILES` has keys `{rocm, rocm-mtp, vulkan, flm, tts, comfyui}` with the backend values from the table.
- [ ] Run → FAIL.
- [ ] Add `backend: Literal["rocm","vulkan"] | None = Field(default=None, …)` to `ProfileConfig`. Rename `SEED_PROFILES` keys and add `"backend"` to each (rocm/rocm-mtp/vulkan → their backend; flm/tts/comfyui omit or `None`). Update `DEVICE_DEFAULT_PROFILES`: `gpu-rocm→"rocm"`, `gpu-vulkan→"vulkan"`, `cpu→"tts"`, `npu→"flm"`.
- [ ] Run → PASS. Commit.

### Task A2: ResolvedProfile + ProfilePatch + route bodies carry backend

**Files:** Modify `src/hal0/profiles/__init__.py` (`ResolvedProfile`, `to_dict`, `ProfilePatch`, `_runtime_family`, `_supported_slot_types`, `_resolve_item`), `src/hal0/api/routes/profiles.py` (`ProfileBody`, `ProfileUpdateBody`, create/update). Test: `tests/profiles/test_catalog.py`, `tests/api/test_profiles_*`.

- [ ] Write failing test: `ProfileCatalog().resolve("rocm").backend == "rocm"`; `.resolve("flm").backend is None`; `to_dict()` includes `"backend"`.
- [ ] Run → FAIL.
- [ ] Add `backend: str | None = None` to `ResolvedProfile` + `to_dict`; thread `backend=profile.backend` in `_resolve_item`. Add `backend` to `ProfilePatch` and the route `ProfileBody`/`ProfileUpdateBody` + create/update calls. Update name literals in `_runtime_family` (`"flm-npu"→"flm"`, `"kokoro-cpu"→"tts"`) — keep the device_class/image fallbacks.
- [ ] Run → PASS. Commit.

### Task A3: profiles.toml seed file matches SEED_PROFILES

**Files:** Modify `installer/etc-hal0/profiles.toml`. Test: the existing seed-drift guard (find with `grep -rn "SEED_PROFILES" tests/`).

- [ ] Write/confirm failing drift test: `profiles.toml` parses to the same names+fields as `SEED_PROFILES`.
- [ ] Run → FAIL.
- [ ] Rewrite `profiles.toml`: rename the 6 `[profile.<name>]` sections, add `backend = "rocm"|"vulkan"` to the GPU ones.
- [ ] Run → PASS. Commit.

### Task A4: serializer lifts device_class (+ keeps backend) from resolved profile

**Files:** Modify `src/hal0/slot_view/__init__.py` (`serialize_slot` ~139-157). Test: `tests/` for slot_view (find with `grep -rln "serialize_slot\|device_class" tests/`).

- [ ] Write failing test: `serialize_slot` output for a slot with `profile="rocm"` has `device_class == "gpu"` and `backend == "rocm"`.
- [ ] Run → FAIL.
- [ ] In `serialize_slot`, resolve the slot's profile via `ProfileCatalog` (guard missing/None profile) and set `base["device_class"]` (and `base["backend"]` if absent) from it. Avoid import cycles (local import like the existing `_is_alias` pattern).
- [ ] Run → PASS. Commit.

### Task A5: full backend test sweep

- [ ] `cd /home/halo/dev/hal0-prof && python -m pytest tests/config tests/profiles tests/api/test_profiles_route.py tests/api/test_profiles_crud.py -q` → green. Fix fallout (other tests referencing old slug names — `grep -rln "moe-rocmfp4\|vulkan-std\|kokoro-cpu\|flm-npu\|dense-mtp-rocmfp4" tests/`). Commit.

---

## WORKSTREAM B — Frontend / UI (owner: spawned agent)

Build against the locked contract above. All paths under `ui/`.

### Task B1: shared pretty-name map + profiles.jsx

**Files:** Modify `ui/src/dash/profiles.jsx` (`PROFILE_INTENT` ~12, `profileIntent`). Optionally add `ui/src/dash/profile-names.js` exporting `prettyProfile(slug)`.

- [ ] Add a `PROFILE_DISPLAY` map: `{rocm:"ROCm", "rocm-mtp":"ROCm-MTP", vulkan:"Vulkan", flm:"FLM", tts:"TTS", comfyui:"ComfyUI"}` with a `prettyProfile(slug)` helper (fallback: title-case the slug). Re-key `PROFILE_INTENT` to the new slugs. Export the helper for reuse by slots.jsx.
- [ ] Verify `npm run build` succeeds.

### Task B2: drop image-sniff, read profile.backend (create modal)

**Files:** Modify `ui/src/dash/slot-modals.jsx` (~144-157).

- [ ] Replace the `device` derivation IIFE: look up the selected profile in `allProfiles`; set `device` from its `backend`/`device_class` — `backend==="vulkan"→"gpu-vulkan"`, `backend==="rocm"→"gpu-rocm"`, else map device_class (`npu→"npu"`, `cpu→"cpu"`, `img→"gpu-rocm"`). Remove the `image.includes("vulkan")` line entirely.
- [ ] Verify build.

### Task B3: card chip = pretty profile name, colored by backend

**Files:** Modify `ui/src/dash/slots.jsx` (SlotCard chip region ~242-303).

- [ ] Render the bottom-left identity chip as `prettyProfile(slot.profile)` with class `dev-{slot.backend || slot.device_class}` (reuse existing `dev-rocm`/`dev-vulkan`/`dev-cpu`/`dev-npu`/`dev-img` colors). Keep the container image-tag chip as secondary detail. Drop the `gpu-rocm` device-tag text chip.
- [ ] Verify build.

### Task B4: device_class grouping + filter synthetic + remove legacy branch

**Files:** Modify `ui/src/dash/slots.jsx` (grouping ~777-782, render ~1067-1091, legacy branch ~286-303).

- [ ] Card grid renders only `kind === "local"` slots (filter out `_synthetic`/`kind:"slot"` so the `hal0` phantom disappears).
- [ ] Derive sections from `device_class` (gpu→Chat, embedding/reranking/tts→Capabilities, npu→NPU/FLM stack, img→Image-Gen tab) instead of null `s.group`/`s.device === "npu"`. Use `s.device_class` (and `type` for the capabilities split).
- [ ] Remove the dead non-container branch (`!isContainer` path) now that all real slots are containers.
- [ ] Verify build.

### Task B5: e2e + selector fixups

**Files:** `ui/tests/e2e/specs/profiles-*.spec.ts`, `slot-drawer-profile-v3.spec.ts`, any spec referencing old slugs.

- [ ] `grep -rln "moe-rocmfp4\|vulkan-std\|kokoro-cpu\|flm-npu\|dense-mtp-rocmfp4" ui/` → update to new slugs / display names. Keep tests green where runnable.

---

## INTEGRATION (orchestrator, after both workstreams)

### Task I1: full build + test
- [ ] `cd ui && npm run build` → succeeds. `cd .. && python -m pytest -q -k "profile or slot_view or slots"` → green.

### Task I2: push + PR
- [ ] Commit any integration fixes. `git push -u origin fix/profile-rename-slot-cards`. Open PR with summary + the design/plan links.

### Task I3: deploy + recreate slots + verify (CT105, dev-only)
- [ ] Coordinate via `wip hal0 status` first. Deploy via `scripts/deploy.sh`/`make deploy` (rebuilds `ui/dist`). Recreate the 6 slots against new profile names. Verify `/api/slots` shows non-null `device_class`, no `kind:"slot"` phantom in the card grid, and the dashboard renders pretty-name chips with correct colors + no old-style cards.

---

## Self-review notes
- Spec coverage: rename (A1/A3/B1), explicit backend (A1/A2/A4/B2), card display (B3), kill old cards (B4), dev-only deploy (I3) — all mapped.
- Type consistency: `backend ∈ {rocm,vulkan,null}` everywhere; `device_class ∈ {gpu,cpu,npu,img}`; pretty-name keyed on slug.
- The synthetic filter is UI-side only (B4); `/api/slots` payload unchanged (per spec).
