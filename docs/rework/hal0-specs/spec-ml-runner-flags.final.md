I have a complete, verified map. Here is the implementation-ready spec.

---

# ML-4 (runner-image registry) + ML-5 (flag resolution) — implementation-ready spec

Repo `/home/mint/hal0` @ `rework/descar`. All file:line refs below verified against code. Plan refs: §7.1a (flags), §7.1b (runners), §8.2 (SQLite schema the record lands on).

---

## PART 1 — CURRENT STATE MAP (verified)

### 1.1 The 7 argv segments (single source)
`providers/container.py:599` `_llama_argv_segments()` returns the ordered labelled list — this is the sole argv authority; both launch and preview consume it.

Segments, low→high precedence (last-wins via `resolve_argv`):
```
base < profile < model_defaults < chat_template < mmproj < slot_overrides < extra_args
```
- `base` (`:631`): `--host 0.0.0.0 --port --model --alias --ctx-size`
- `profile` (`:643`): `shlex.split(profile_flags)` — the MTP-expanded profile flag string
- `model_defaults` (`:648-655`): `defaults.extra_args` + `-ngl` from `defaults.n_gpu_layers`; **`FAMILY_DEFAULTS` is prepended into this segment's `extra_args`** at `container.py:951-958` (`family_flags(...)`)
- `chat_template` (`:657`), `mmproj` (`:658`)
- `slot_overrides` (`:664-674`): `-ngl` from `[model].n_gpu_layers`, `--parallel`/`--kv-unified`
- `extra_args` (`:676`): `[server].extra_args`

`_llama_launch_plan()` (`:689`) calls `resolve_argv(segments).argv` (`:734`). `_render_unit()` scalar shim (`:546`) also flows here.
`slots/argv.py`: `resolve_argv` (`:215`), `normalize_argv` (`:179`), `merge_flags` (`:256`), `FLAG_ALIASES` (`:47`), `APPEND_FLAGS` (`:68`). **`-ngl`, `-fa`, `--jinja` are all deduped scalars/bools; there is NO `--no-jinja` negation handling** — a `--no-jinja` token would dedup under its own literal key, never cancelling a `--jinja` from an earlier segment. This is the structural reason `*-nojinja` clone profiles exist.

### 1.2 SEED_PROFILES + resolvers (config/schema.py)
- `SEED_PROFILES` dict `:927-1201` — 23 entries. Every GPU entry carries `image` (all `DEFAULT_ROCMFPX_IMAGE` except `cuda`, `cpu-llm`, `flm`, `tts`, `tts-qwen3`, `comfyui`), `flags`, `mtp`, `device_class`, `backend`, `intent`, `quant`.
- `resolve_profile_flags()` `:1664` — appends MTP bundle via `merge_flags(build_mtp_flag_bundle(backend), base)` when effective mtp true.
- `family_flags()` `:1259` → `FAMILY_DEFAULTS` `:1230` (`gemma` only), keyed by `model_family()` `:1248` **filename/id token scan** (`_KNOWN_FAMILIES` `:1245`) — NOT registry architecture.
- `PROFILE_BENCH` `:1214`, `ProfileConfig` `:1274` (`image` required + nonempty validator `:1345`), `ProfilesConfig` `:1353`.
- `load_profiles_config()` overlay `loader.py:427-501`: seeds are virtual, overlaid from `SEED_PROFILES` on every load (`:469`); custom-only persisted (`:501`).

### 1.3 MTP spread (6 sites confirmed)
1. `build_mtp_flag_bundle()` `schema.py:805` (+ `_MTP_DRAFT_DEVICE` `:802`, `MTP_FLAG_BUNDLE` `:835`)
2. `resolve_profile_flags()` mtp expansion `schema.py:1686-1704`
3. `ProfileConfig.mtp` field `schema.py:1296`
4. `_effective_mtp()` `container.py:212` (slot→profile.mtp AND model-eligible)
5. `model_is_mtp_eligible()` `model_meta/__init__.py:620` + **`_MTP_NAME_RE` name-regex** `:617`
6. `SlotConfig.mtp` `schema.py:379`; plus swap-guard `manager._defuse_stale_mtp_on_swap` `:2522`.

### 1.4 Image resolution — 3 inconsistent chains (confirmed)
- **llama** (`container.py:125` `_resolve_image_ref`): slot `image` → `slot["slot"]["image"]` → `profile.image` → `resolve_default_image(backend, device_class)` (`schema.py:890`, HW-gated: cuda→`FALLBACK_CUDA_IMAGE`, cpu→`FALLBACK_VULKAN_IMAGE`, else `DEFAULT_ROCMFPX_IMAGE`). Honors `slot.image`.
- **FLM** (`flm.py:430`): `os.environ["HAL0_TOOLBOX_IMAGE_FLM"] or _DEFAULT_FLM_IMAGE` — **ignores `slot.image` and profile.image entirely**. FLM image **triple-pinned**: seed `schema.py:1148`, `flm.py:47`, `manifest.json` + `capabilities/catalog.py:142` (a 4th).
- **kokoro/qwen3tts**: `container_spec` uses `image=profile.image` directly (`kokoro.py:163`, `qwen3tts.py:179`); their `image_ref()` methods (`kokoro.py:112`, `qwen3tts.py:127`) are **dead code** (never called). `_DEFAULT_KOKORO_IMAGE` `kokoro.py:45`, `_DEFAULT_QWEN3TTS_IMAGE` `qwen3tts.py:54`.
- **comfyui** (`comfyui.py:159`): slot `image` → env → **`manifest_image_ref("comfyui")`** → `_HAL0_COMFYUI_IMAGE` `:53`. Only provider reading the manifest.

Constants to absorb: `DEFAULT_ROCMFPX_IMAGE`, `STALE_ROCMFPX_IMAGE_REFS`, `FALLBACK_VULKAN_IMAGE`, `FALLBACK_CUDA_IMAGE` (schema.py); `_DEFAULT_FLM_IMAGE`, `_DEFAULT_KOKORO_IMAGE`, `_DEFAULT_QWEN3TTS_IMAGE`, `_HAL0_COMFYUI_IMAGE`, `_FLM_TOOLBOX_IMAGE` (catalog.py:142); manifest `toolbox_images` keys: `vulkan, rocm, flm, moonshine, kokoro, comfyui, qwen3tts`.

### 1.5 `_runtime_family` sniff → target lookup
`profiles/__init__.py:106` `_runtime_family(name, profile)` string-sniffs `profile.image` + name literals → one of `flm|qwen3tts|kokoro|comfyui|llama-server`. Consumed by `_resolve_item` `:260`, surfaces on `ResolvedProfile.runtime_family` `:59`. `container.py:796` `_profile_runtime_family` re-resolves it via catalog; `_spec_provider_for` `:758` dispatches on it. `_supported_slot_types()` `:125` maps family→slot types.

### 1.6 `_apply_preferred_profile` (manager.py)
`_preferred_profile_for` `:2429` (reads `defaults.profile`), `_profile_fits_slot` `:2443` (device/type/backend coherence), `_apply_preferred_profile` `:2482`. Called at `:1325` (swap) and `:1945` (create). `ModelDefaults.profile` field `registry/model.py:56`.

---

## PART 2 — ML-4: `hal0/runners/` runner-image registry

### 2.1 New package `src/hal0/runners/__init__.py`
Define one frozen registry keyed by runner key:

```python
@dataclass(frozen=True, slots=True)
class RunnerSupports:
    mtp: bool = False
    jinja: bool = False
    mmproj: bool = False

@dataclass(frozen=True, slots=True)
class Runner:
    key: str
    image: str                      # canonical ghcr ref (tag form; digest via manifest pin)
    runtime_family: RuntimeFamily   # llama-server|flm|kokoro|qwen3tts|comfyui  (import the Literal from profiles or move it here — see §4.4)
    supports: RunnerSupports
    device_class: str               # gpu|cpu|npu|img
    backend: str | None = None      # rocm|vulkan|cuda|None — the vendor lane
    manifest_key: str | None = None # key into manifest.json toolbox_images for digest pin

RUNNER_IMAGES: dict[str, Runner] = {
    "rocmfpx":   Runner("rocmfpx", DEFAULT_ROCMFPX_IMAGE, "llama-server",
                        RunnerSupports(mtp=True, jinja=True, mmproj=True), "gpu", "rocm", "rocm"),
    "vulkanfpx": Runner("vulkanfpx", DEFAULT_ROCMFPX_IMAGE, "llama-server",
                        RunnerSupports(mtp=True, jinja=True, mmproj=True), "gpu", "vulkan", "rocm"),
    "cuda":      Runner("cuda", FALLBACK_CUDA_IMAGE, "llama-server",
                        RunnerSupports(mtp=False, jinja=True, mmproj=True), "gpu", "cuda"),
    "cpu":       Runner("cpu", FALLBACK_VULKAN_IMAGE, "llama-server",
                        RunnerSupports(mtp=False, jinja=True, mmproj=True), "cpu", None),
    "flm":       Runner("flm", "ghcr.io/hal0ai/hal0-toolbox-flm:0.9.44", "flm",
                        RunnerSupports(), "npu", None, "flm"),
    "kokoro":    Runner("kokoro", "ghcr.io/hal0ai/hal0-toolbox-kokoro:v1", "kokoro",
                        RunnerSupports(), "cpu", None, "kokoro"),
    "qwen3tts":  Runner("qwen3tts", "ghcr.io/hal0ai/hal0-toolbox-qwen3tts:v1", "qwen3tts",
                        RunnerSupports(), "gpu", "rocm", "qwen3tts"),
    "comfyui":   Runner("comfyui", _HAL0_COMFYUI_IMAGE, "comfyui",
                        RunnerSupports(mmproj=False), "img", None, "comfyui"),
}
```

The image constants themselves stay defined once (in `runners/` — moved out of schema.py/providers, or kept in schema.py and imported — see §4.1). `DEFAULT_ROCMFPX_IMAGE` bump = one edit.

Public API in `runners/__init__.py`:
- `get_runner(key: str) -> Runner` (raises `NotFound`)
- `resolve_runner_image(runner: Runner) -> str` — env override (`HAL0_TOOLBOX_IMAGE_<KEY>`) → `manifest_image_ref(runner.manifest_key)` digest pin → `runner.image`. **This unifies the 3 chains into ONE resolver** honoring both env AND manifest for every runner.
- `runner_for_backend(backend, device_class) -> Runner` — replaces `resolve_default_image` HW-gate logic (cuda→cuda, cpu→cpu, else rocmfpx/vulkanfpx by backend). `resolve_default_image` becomes a thin shim returning `resolve_runner_image(runner_for_backend(...))` for back-compat.
- `STALE_RUNNER_IMAGE_REFS` = the old `STALE_ROCMFPX_IMAGE_REFS` frozenset (move here; updater imports from new home).

### 2.2 `_runtime_family` becomes a lookup, not a sniff
- Add optional `Model.preferred_runner` (see §2.4) and/or `ProfileConfig.runner` key. `ResolvedProfile.runtime_family` should resolve via `RUNNER_IMAGES[runner_key].runtime_family` when a runner key is known, falling back to the current `_runtime_family()` sniff only for legacy custom profiles with no runner key.
- `profiles/__init__.py:106` `_runtime_family` — keep as the **legacy fallback** but gate it behind "no runner key present." Long-term (P3-schema) profiles lose `image` entirely and always carry a runner key.
- `container.py:796` `_profile_runtime_family` and `_spec_provider_for` `:758` are unchanged in shape — they already consume `runtime_family`; the value just becomes lookup-sourced.

### 2.3 Runner image resolution replaces `_resolve_image_ref`
Rewrite `container.py:125` `_resolve_image_ref` precedence to:
`slot.image` (verbatim string override, kept) → **model.preferred_runner → profile.runner** → `runner_for_backend(backend, device_class)`. Each runner key resolves through `resolve_runner_image()` (env + manifest + default). Drop the direct `DEFAULT_ROCMFPX_IMAGE` fallthrough; it now comes from the `cpu`/`rocmfpx` runner defaults.
Do the same collapse for FLM/kokoro/qwen3tts: their `container_spec` should call `resolve_runner_image(get_runner(family_key))` instead of `profile.image` / env-only. **This fixes FLM ignoring slot.image and the dead kokoro image_ref** — one resolver, one precedence, all families.

### 2.4 `Model.preferred_runner` + `_apply_preferred_runner`
- Add `preferred_runner: str | None` to `Model` (`registry/model.py:68`) — a key into `RUNNER_IMAGES`. (The plan §8.2 already reserves the `preferred_runner` column; ML-1 lands it relationally — coordinate, don't duplicate.)
- Add `_apply_preferred_runner(slot_name, model_id)` in `slots/manager.py` beside `_apply_preferred_profile` (`:2482`), plus `_preferred_runner_for(model_id)` beside `:2429`. Fit-check analog of `_profile_fits_slot` (`:2443`): a runner is adoptable only if `runner.device_class` matches the slot device and `runner.backend` (if set) matches slot backend. Wire calls at the same two sites (`:1325` swap, `:1945` create). Persist to slot TOML `image`/`runner` under `slot_write_lock()` exactly as `_apply_preferred_profile` does.
- Updater `retag_stale_slot_images` (`updater.py:1171`) keeps working — point its imports at `STALE_RUNNER_IMAGE_REFS` + `runner_for_backend` (was `resolve_default_image`).

---

## PART 3 — ML-5: flag resolution

### 3.1 Capability flags on the model, delete name-regex + clones
- Add to `ModelDefaults` (`registry/model.py:22`): `mtp: bool | None = None`, `jinja: bool | None = None` (tri-state; plan §8.2 has them as nullable columns). Keep `profile`, `extra_args`, `n_gpu_layers`, `chat_template`.
- **Delete** `model_is_mtp_eligible`'s name-regex path (`model_meta/__init__.py:617` `_MTP_NAME_RE`, `:636-637`). Eligibility becomes: `defaults.mtp` explicit → that; else registry `mtp` tag (keep the tag check `:631-635`); no filename sniffing.
- **Delete** the clone seed profiles: `rocm-dense-nojinja` (`schema.py:1007`), `vulkan-dense-nojinja` (`:1019`), `rocm-dense-small` (`:1030`). `--jinja` and MTP become model-driven, so these variants are redundant.
- `--jinja` handling: emit `--jinja` iff effective `jinja` capability is true (default true for llama-server runners via `runner.supports.jinja`, suppressible by `defaults.jinja=false`). Since there is no negation flag, jinja must be **injected into the profile/model segment conditionally**, not removed post-hoc — add a `jinja` capability segment computed in `_resolve_llama_scalars`. Remove `--jinja` from the seed profile `flags` strings (it becomes a capability, not a tune) as profiles shrink.

### 3.2 Profiles become pure tunes (lose image + mtp)
- Strip `image` and `mtp` from `ProfileConfig` semantics: profiles keep only hardware/backend tunes (`-dev`, `-b/-ub`, `--threads`, `-fa`, KV-quant, `-ngl`, `--no-mmap`, `--metrics`, `--no-webui`, etc.). `image` moves to the runner registry; `mtp` moves to model capability.
- Transitional: `ProfileConfig.image` currently has a **required + nonempty validator** (`schema.py:1288`, `:1345`). To drop it you must relax the field to optional (or default from the runner) — coordinate with P3-schema, which externalizes SEED_PROFILES to `share/*.toml` (see Part 5). Recommend: make `image` optional now, resolve it from the runner at read time, delete it from seeds in P3-schema.
- `resolve_profile_flags()` (`:1664`) stops appending the MTP bundle based on `profile.mtp`; the MTP bundle append moves to `_resolve_llama_scalars` gated on the effective **model** capability + `runner.supports.mtp`. `_effective_mtp` (`container.py:212`) precedence becomes: slot.mtp → model.defaults.mtp → (registry mtp tag AND runner.supports.mtp).

### 3.3 Precedence (low→high), plan §7.1a
```
runner image  <  profile tune  <  arch defaults (FAMILY_DEFAULTS, keyed by registry architecture)
              <  per-model metadata (mtp/jinja/mmproj/extra_args)
              <  slot instance overrides (port/ctx/parallel/vision/[server].extra_args ALWAYS wins)
```
Map onto the existing 7 segments (`_llama_argv_segments`):
- `runner image`: not an argv segment — selects image + `supports` gates (mtp/jinja/mmproj capability visibility).
- `profile tune` → `profile` segment
- `arch defaults` → the `FAMILY_DEFAULTS` prepend into `model_defaults` (`container.py:951`); **re-key `family_flags` off registry `architecture` not filename** (`model_family` `schema.py:1248`). Requires `Model.architecture` (plan §8.2 column) — coordinate with ML-1.
- `per-model metadata` → `model_defaults` segment (+ new mtp/jinja capability injection)
- `slot overrides` → `slot_overrides` + `extra_args` segments (unchanged, still win)

The `resolve_argv` last-wins engine (`slots/argv.py`) needs **no change** — only the segment contents/order shift.

---

## PART 4 — FILES TO ADD / TOUCH

### 4.1 Add
- `src/hal0/runners/__init__.py` — `Runner`, `RunnerSupports`, `RUNNER_IMAGES`, `get_runner`, `resolve_runner_image`, `runner_for_backend`, `STALE_RUNNER_IMAGE_REFS`. (Move image constants here, or keep in schema.py and import — decide with P3-schema; simplest first step: define here, have schema.py re-export for back-compat.)
- `tests/runners/test_registry.py`, `tests/runners/test_resolve_image.py` (see Part 6).
- (P3-schema-coordinated) `share/profiles.toml` for externalized seeds — **do not** own this in ML-5; P3-schema does. ML-5 only removes `image`/`mtp` semantics.

### 4.2 Touch — ML-4
- `config/schema.py`: image consts (`:851`,`:865`,`:886-887`), `resolve_default_image` (`:890`) → shim over runners; `_runtime_family` sniff stays as fallback.
- `profiles/__init__.py`: `_runtime_family` (`:106`) → runner-key lookup w/ sniff fallback; `ResolvedProfile` gains `runner` key; `RuntimeFamily`/`SlotType` Literals may move to `runners/`.
- `providers/container.py`: `_resolve_image_ref` (`:125`), `_profile_image_and_flags` (`:177`), `_profile_runtime_family` (`:796`).
- `providers/flm.py` (`:430`,`:47`), `kokoro.py` (`:112`,`:45`,`:163`), `qwen3tts.py` (`:127`,`:54`,`:179`), `comfyui.py` (`:159`,`:53`) — route image through `resolve_runner_image`.
- `capabilities/catalog.py:142` — drop `_FLM_TOOLBOX_IMAGE`, import runner.
- `registry/model.py`: add `Model.preferred_runner`.
- `slots/manager.py`: `_apply_preferred_runner` + `_preferred_runner_for` + fit-check; wire at `:1325`,`:1945`.
- `updater/updater.py`: `retag_stale_slot_images` (`:1171`,`:1203`,`:1251`,`:1288`) + FLM version derivation (`api/routes/updater.py:393-456`) → runner registry.

### 4.3 Touch — ML-5
- `registry/model.py`: `ModelDefaults.mtp`, `ModelDefaults.jinja`.
- `model_meta/__init__.py`: delete `_MTP_NAME_RE` (`:617`) + name path in `model_is_mtp_eligible` (`:636`).
- `config/schema.py`: delete `rocm-dense-nojinja`/`vulkan-dense-nojinja`/`rocm-dense-small` seeds; strip `--jinja` from seed flag strings; `resolve_profile_flags` (`:1664`) stops profile-mtp append; `family_flags`/`model_family` re-key to architecture.
- `providers/container.py`: `_effective_mtp` (`:212`) precedence; `_resolve_llama_scalars` (`:860`) — add jinja/mtp capability injection gated by `runner.supports`; `_llama_argv_segments` (`:599`) — optional new capability sub-segment or fold into `model_defaults`.
- `slots/manager.py`: `_defuse_stale_mtp_on_swap` (`:2522`) — read `defaults.mtp` not just name.

### 4.4 Stays behind interfaces (blast-radius control)
- `resolve_argv`/`normalize_argv`/`merge_flags` (`slots/argv.py`) — **unchanged**.
- `resolve_default_image` name kept as shim (many test + updater imports).
- `_spec_provider_for` dispatch shape unchanged (still on `runtime_family`).
- `SlotConfig.image` string override — unchanged, still highest image precedence.

---

## PART 5 — P3-SCHEMA + ML-1 OVERLAP (coordinate, don't collide)

- **SEED_PROFILES → `share/*.toml`** is owned by **P3-schema** (tracker line 109; plan §163-164, §691-698). ML-5's job is to *change the shape* (remove `image`, `mtp`, the 3 clones, `--jinja` from flags); P3-schema *moves the data out of schema.py*. Sequence: ML-5 shape change first (or same PR-family), P3-schema externalization second. Flag this in both lanes' PR descriptions — editing `SEED_PROFILES` in two lanes will conflict.
- **`Model.preferred_runner`, `mtp`, `jinja`, `architecture`** are the **same columns ML-1 (SQLite pilot) ships** (plan §8.2 CREATE TABLE model: `preferred_runner`, `mtp INTEGER`, `jinja INTEGER`, `architecture`). Land them as pydantic `Model`/`ModelDefaults` fields once; ML-1 maps them to columns. Do **not** add them twice. The metadata record is explicitly identical across 7.1a/b/c (plan §326-327).
- **`RUNNER_IMAGES` is a code registry** (`hal0/runners/`), NOT a DB table — confirmed by plan (§8.2 has no runner table; `preferred_runner` is a TEXT key into the code registry). Keep it in code.
- **`STALE_ROCMFPX_IMAGE_REFS`** move touches `updater/updater.py` + `tests/updater/test_image_retag.py` — same-lane.

---

## PART 6 — TESTS

Existing tests that will break / must update (verified present):
- `tests/slots/test_argv.py` — `test_resolve_argv_equivalent_argv_to_normalize` (parity pin); update segment expectations if a jinja/mtp capability sub-segment is added.
- `tests/config/test_mtp_override.py`, `tests/slots/test_mtp_defuse.py` — MTP now model-capability driven; drop name-regex cases.
- `tests/config/test_profiles.py`, `tests/config/test_schema_seeds_d1.py`, `tests/profiles/test_catalog.py`, `tests/api/test_profiles_crud.py`/`_route.py` — seed count drops by 3 (nojinja/small removed); `image`/`mtp` semantics change.
- `tests/providers/test_image_resolution.py`, `tests/providers/test_container.py`, `tests/config/test_default_image_gate.py` — image precedence now runner-sourced.
- `tests/updater/test_image_retag.py` — `STALE_RUNNER_IMAGE_REFS` rename.
- `tests/slots/test_model_preferred_profile.py` — add a sibling `test_model_preferred_runner.py`.

New tests:
- `runners/`: registry completeness (every `runtime_family` value is a real family; every `manifest_key` exists in `manifest.json`); `resolve_runner_image` precedence (env > manifest digest > default); `runner_for_backend` HW-gate parity with old `resolve_default_image`.
- ML-5: `defaults.jinja=false` suppresses `--jinja`; `defaults.mtp` tri-state beats tag; `runner.supports.mtp=false` gates MTP off even for eligible model; precedence chain end-to-end (runner<profile<family<model<slot).
- `_apply_preferred_runner` fit-check: wrong device_class/backend rejected, compatible adopted + persisted.

---

## PART 7 — RISKS

1. **`ProfileConfig.image` required-validator (`schema.py:1345`).** Dropping `image` from profiles breaks the nonempty validator + every custom profile on disk that carries `image`. Mitigate: make `image` optional, resolve from runner at read, keep round-tripping custom `image` until P3-schema migration.
2. **Three seed removals change seed count** — many tests assert exact seed sets; `load_profiles_config` overlay (`loader.py:469`) re-applies seeds every load, so a stale on-disk clone would silently vanish. Confirm no live slot pins `rocm-dense-nojinja`/`-small` before deleting (updater migration or `_resolve_profile_or_base` fallback `container.py:90` already handles missing → base).
3. **FLM image un-pinning.** FLM is quadruple-pinned (seed, flm.py, manifest, catalog.py); the FLM version-derivation route (`api/routes/updater.py:399`) parses the tag. Collapse to one source carefully — the version regex expects `:0.9.44`.
4. **Manifest for llama runners.** Today only comfyui reads the manifest; unifying means rocmfpx/flm/kokoro/qwen3tts now honor manifest digests. Verify `manifest.json` has all keys (it does: vulkan/rocm/flm/moonshine/kokoro/comfyui/qwen3tts) and that digest-pinning the big rocmfpx image doesn't regress the env-override dev path.
5. **`family_flags` re-key to architecture** depends on `Model.architecture` existing (ML-1 column). Until then, keep filename keying as fallback or the gemma f16-KV fix silently stops firing.
6. **Launch/preview parity** (`_resolve_slot_argv` `container.py:1620` vs launch `container_spec`) — both go through `_resolve_llama_scalars`; any new jinja/mtp capability injection MUST be inside that shared resolver, or preview drifts from launch.
7. **kokoro/qwen3tts dead `image_ref`** — safe to delete, but grep for external callers first (none in src, confirmed).

---

Everything above is grounded in the current `rework/descar` tree; the `resolve_argv` engine and `_spec_provider_for` dispatch need no changes — ML-4/ML-5 are additive registry + capability work plus deletions, landing behind the existing `ModelRegistry`/`ProfileCatalog`/provider interfaces.