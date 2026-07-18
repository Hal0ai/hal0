I have a complete picture. Here is the implementation-ready spec.

---

# P3-schema — `config/schema.py` decomposition: implementation-ready spec

Repo `/home/mint/hal0` @ `rework/descar`. Target file `src/hal0/config/schema.py` (3086 lines, verified — not 3045). Plan refs: `hal0-rework-plan.md` §7.1a/§11.3, tracker line 109; risks PLAN.md §17.

## 0. Current-state map (verified line ranges)

| Block | Lines | Kind | Move? |
|---|---|---|---|
| Shared constants / enums (`_VALID_BACKENDS`, `DeviceLiteral`, `_VALID_PROVIDERS`, port bounds, `_ENV_VAR_NAME_RE`, schema-version consts) | 48–114 | code | stay |
| `ModelConfig`, `NpuConfig`, `ImageGenConfig`, `ServerConfig` | 125–287 | leaf models | stay |
| **`SlotConfig`** (24 fields + 4 `mode="before"` hoists + `model_serializer` tuck + 4 validators) | **290–753** | god model | split (Part B) |
| `ProviderEntry`, `ProvidersConfig` | 759–793 | models | stay |
| `_MTP_DRAFT_DEVICE`, `build_mtp_flag_bundle`, `MTP_FLAG_BUNDLE` | 802–835 | derivation | **stay** |
| `DEFAULT_ROCMFPX_IMAGE`, `STALE_ROCMFPX_IMAGE_REFS`, `FALLBACK_*_IMAGE`, `resolve_default_image` | 851–912 | **image pins** | **→ ML-runner registry (§7.1b), NOT this lane** |
| **`SEED_PROFILES`** (21 profiles, not "25" — verified) | **927–1201** | data | externalize |
| `PROFILE_BENCH` | 1214–1221 | data | externalize |
| `FAMILY_DEFAULTS`, `_KNOWN_FAMILIES`, `model_family`, `family_flags` | 1230–1262 | data+derivation | data→TOML, fns stay |
| `DEVICE_DEFAULT_PROFILES` (alias of `model_meta.DEVICE_TO_DEFAULT_PROFILE`) | 1271 | alias | stay |
| `ProfileConfig`, `ProfilesConfig` | 1274–1366 | models | stay |
| Stack schema-version consts, `StackModelMeta`, `StackCapabilityRow`, `StackSlotEntry`, `StackConfig`, `StacksConfig` | 1376–1547 | models | stay |
| `_embed_rerank_rows`, **`SEED_STACKS`** (3 stacks) | 1564–1661 | data | externalize |
| `resolve_profile_flags`, `resolve_chat_template` | 1664–1720 | derivation | **stay** |
| Upstreams / Hardware / Hal0Config sub-models (Meta/Slots/Dispatcher/Telemetry/Memory*/Honcho*/Models/Activity/BrainChat/Agent*) | 1726–3029 | models | stay |
| `__all__` | 3032–3085 | — | maintain |

Verified counts: **21** seed profiles (the docstring "25" is stale), **3** seed stacks. Class count ≈ 46 pydantic models.

**Consumers of the data being moved** (grep-verified):
- `SEED_PROFILES`: `config/loader.py` (overlay, L469), `profiles/__init__.py` (`ProfileCatalog._guard_custom`, `.seed`), `updater/updater.py` (retag/prune L1003–1050), `api/routes/profiles.py`, `install/profile_derive.py`, `capabilities/profile_fit.py`, `providers/container.py`.
- `SEED_STACKS`: `loader.py` (L521), `stacks/__init__.py` (L135/L139), `api/routes/installer.py`, `api/routes/stacks.py`.
- `PROFILE_BENCH`: `profiles/__init__.py` L261.
- `FAMILY_DEFAULTS`/`family_flags`: `providers/container.py` L951.
- `DEFAULT_ROCMFPX_IMAGE`/`STALE_ROCMFPX_IMAGE_REFS`/`resolve_default_image`: `providers/container.py`, `updater/updater.py` (retag engine).
- `DEVICE_DEFAULT_PROFILES`: `install/profile_derive.py`, `capabilities/profile_fit.py`.

The virtual-seed overlay contract is load-bearing: `load_profiles_config` overwrites on-disk seed keys from code every load (loader.py L459–471); `save_profiles_config` strips seed keys before write (L498–501); `ProfileCatalog._guard_custom` blocks editing/deleting seeds. Externalization must preserve this exactly — the TOML becomes the *code-side source*, not operator config.

---

## Part A — Externalize SEED_PROFILES / SEED_STACKS / bench / family-defaults → shipped TOML

### A.1 Where the shipped data lives (packaging decision)

`pyproject.toml` uses hatchling with `packages = ["src/hal0"]` (L91) — **only files under `src/hal0/` ship in the wheel**. `paths.usr_lib()` → `/usr/lib/hal0/current` is the code root, not a wheel-readable path. There is no `share/` today.

**Recommendation (buildable, zero packaging change): package-internal data dir, read via `importlib.resources`.**
Put the TOML at `src/hal0/config/data/` and load with `importlib.resources.files("hal0.config.data")`. This ships automatically in the wheel (it's under `src/hal0`), works from an editable checkout and an installed release identically, and needs no `force-include`.

The plan text says "shipped TOML under `share/`" (§11.3) — that is the *conceptual* home. If you want the literal `share/hal0/` FHS path (so operators can browse/PR profiles out-of-band, which §11.3's "central git-backed profile source" wants later), the alternative is:

```toml
# pyproject.toml — only if you insist on repo-root share/
[tool.hatch.build.targets.wheel.force-include]
"share/hal0" = "hal0/_data"   # ships share/hal0/*.toml as hal0/_data/*.toml
```
…then read via `importlib.resources.files("hal0._data")`. **Pick the package-internal option** unless §11.3's git-source work is being pulled forward; it's strictly less machinery. This spec assumes `src/hal0/config/data/`.

### A.2 New data files

```
src/hal0/config/data/
  seed_profiles.toml     # the 21 SEED_PROFILES entries
  seed_stacks.toml       # the 3 SEED_STACKS entries
  profile_bench.toml     # PROFILE_BENCH hero metrics
  family_defaults.toml   # FAMILY_DEFAULTS arch-quirk flags
```

**`seed_profiles.toml`** — one `[profile.<name>]` table per seed, keys = current dict keys (`flags`, `mtp`, `device_class`, `backend`, `intent`, `quant`, and — *transitionally* — `image`). Preserve the extensive per-profile comments as TOML `#` comments (they are operational knowledge: iSWA/KV rationale, `-ub` sizing for embed/rerank, CPU `--no-mmap` exception). Example:
```toml
[profile.rocm-dense]
image = "@DEFAULT_ROCMFPX_IMAGE"   # sentinel; resolved by loader (see A.4) — do NOT hardcode a digest here
flags = "-ngl 999 -fa on -dev ROCm0 -b 512 -ub 512 --parallel 1 --threads 16 --no-mmap --jinja --metrics --no-webui --ctx-checkpoints 0 --checkpoint-every-n-tokens -1"
mtp = true
device_class = "gpu"
backend = "rocm"
intent = "ROCmFPX · DENSE · MTP (sustained-decode)"
quant = "ROCmFP4"
```

**Image-pin handling is the ML-runner handshake (see §H).** Do **not** copy the resolved digest `ghcr.io/hal0ai/hal0-rocmfpx:c077206` into TOML — that would fork the pin from the runner registry. Two options, gated on ML-runner sequencing:
- If P3 lands **before** ML-runner §7.1b: keep `image` in the TOML as a **sentinel string** (`"@DEFAULT_ROCMFPX_IMAGE"`, `"@FALLBACK_VULKAN_IMAGE"`, `"@FALLBACK_CUDA_IMAGE"`) that the loader substitutes from the still-in-`schema.py` constants. Literal image refs (`comfyui` digest, `flm:0.9.44`, `kokoro:v1`, `qwen3tts:v1`, `llama.cpp:server-cuda`) stay literal.
- If P3 lands **after/with** ML-runner: drop `image` and `mtp` from the TOML entirely (§7.1a removes both from profiles), and the loader synthesizes `image` from `runner_images` + `mtp` from the model record.

**`seed_stacks.toml`** — `[stack.<slug>]` tables with nested `slots`, embedded `profiles`/`models` maps. The `_embed_rerank_rows(device)` helper (loader.py-side derivation) can either (a) stay in Python and be applied when constructing `StackConfig` from the raw TOML, or (b) be expanded inline in TOML. Recommend **(a)** — keep `_embed_rerank_rows` in `config/seeds.py` and reference it, so the two hardcoded capability model ids (`qwen3-embedding-0-6b-q8-0`, `bge-reranker-v2-m3-q4_k_m`) live in one place; expand into TOML only if you want the stacks to be 100% data.

### A.3 New loader module: `src/hal0/config/seeds.py`

Owns reading + validating + caching the shipped data. Public surface replaces the schema.py module-level constants:

```python
# src/hal0/config/seeds.py
from functools import lru_cache
import tomllib
from importlib.resources import files
from hal0.config.schema import ProfileConfig, StackConfig  # models stay in schema.py

@lru_cache(maxsize=1)
def seed_profiles() -> dict[str, dict]: ...      # raw dicts (image sentinels resolved)
@lru_cache(maxsize=1)
def seed_stacks() -> dict[str, StackConfig]: ...
@lru_cache(maxsize=1)
def profile_bench() -> dict[str, dict[str, float]]: ...
@lru_cache(maxsize=1)
def family_defaults() -> dict[str, str]: ...
```
- Reads via `files("hal0.config.data").joinpath("seed_profiles.toml").read_text()`.
- Resolves `@`-sentinel image strings against `schema.DEFAULT_ROCMFPX_IMAGE` / `FALLBACK_*` (transition path).
- `lru_cache` gives the current module-constant O(1) semantics (loaded once). Add a `reset_cache()` for tests.
- **Fail-fast**: validate each seed through `ProfileConfig.model_validate` at first access; a malformed shipped TOML is a build/packaging bug and should raise loudly at import-adjacent time (add a startup self-check + a unit test that loads all four files).

### A.4 schema.py backward-compat shims (avoid touching 15+ call sites at once)

Keep the old names importable so the externalization is a **mechanical, low-risk** first PR. In schema.py:
```python
from hal0.config import seeds as _seeds
SEED_PROFILES = _seeds.seed_profiles()      # module-level, preserves `from ...schema import SEED_PROFILES`
SEED_STACKS   = _seeds.seed_stacks()
PROFILE_BENCH = _seeds.profile_bench()
FAMILY_DEFAULTS = _seeds.family_defaults()
```
**Circular-import guard**: `seeds.py` imports `ProfileConfig`/`StackConfig` from `schema.py`, and `schema.py` calls `seeds.seed_profiles()` at module level. Break by having `schema.py` define the models FIRST, then import `seeds` at the bottom (after all class defs), or make the schema-side names lazy (`__getattr__` module hook) so `seeds` is imported only on first access. Recommend the **bottom-of-module import** — simplest, and `seeds.seed_profiles()` only needs `ProfileConfig` which is defined by then. Verify no other module imports `seeds` before `schema` finishes (loader imports schema, not seeds directly).

Later PRs migrate consumers to `from hal0.config.seeds import seed_profiles` and drop the shims; not required for the first landing.

### A.5 What stays in Python (derivation logic)

`build_mtp_flag_bundle`, `MTP_FLAG_BUNDLE`, `_MTP_DRAFT_DEVICE`, `resolve_profile_flags`, `resolve_chat_template`, `model_family`, `family_flags`, `_KNOWN_FAMILIES`, `DEVICE_DEFAULT_PROFILES` alias. These are logic/aliases, not data — leave in schema.py (or move `resolve_profile_flags` + MTP bundle to a new `config/profile_flags.py` if you want schema.py to be pure models; optional, not required).

---

## Part B — SlotConfig split (recommendation: **partial split, sequenced after P2-device**)

The god model is 24 fields (290–753). Field grouping:

| Group | Fields |
|---|---|
| **Identity/placement** | `name`, `port`, `device`, `gpu_index`, `enabled`, `profile` |
| **Deprecated (past-sunset)** | `backend`, `provider`, `runtime`, `workers` |
| **Runtime/flags/ctx** | `enable_thinking`, `mtp`, `parallel`, `chat_template`, `vision`, `idle_timeout_s` |
| **TTS request defaults** | `default_voice`, `default_speed`, `default_response_format` |
| **Nested tables** | `model` (ModelConfig), `server` (ServerConfig), `npu` (NpuConfig), `image_gen` (ImageGenConfig) |
| **Catch-all** | `extra` |

**Recommendation: do a nested `SlotRuntime` sub-model, but land it as the *last* P3 PR, after the deprecated-field deletions (Part D) and after/with P2-device.** Rationale and the "why not fully now":

- **`cfg.backend` has 34 read-sites and `cfg.provider` 29** (grep-verified, filtered). P2-device is *already* deleting the backend translators (tracker: "delete 4 backend translators, 43 sites"). Splitting `backend`/`provider` out **before** P2-device removes them is throwaway churn — you'd move fields P2 is about to delete. **Sequence: P2-device deletes `backend`; Part D deletes `provider`/`runtime`/`workers`; then the split operates on the clean 6+6 field set.**
- The loader round-trips SlotConfig **flat** (`_flatten_slot_toml`/`_unflatten_slot_toml`, loader.py L189–355): `[slot]` scalars hoist to top level, sibling tables (`[server]`, `[npu]`, `[image]`, `[model]`) land in `extra` and are promoted by the 4 `mode="before"` hoist validators, then re-parked by the `model_serializer` tuck. A `SlotRuntime` sub-model must use the **same hoist/tuck pattern** (add `_hoist_runtime_from_extra` + tuck) OR — cleaner — keep the runtime fields **top-level-flat** and split *only logically* via a `runtime` `@property` view that returns a `SlotRuntime` computed model. The flat wire format (`slots/<name>.toml` has bare `mtp = true`, `parallel = 4` under no table) must not change or every existing slot TOML breaks.

**Concrete recommendation — Option B2 (view-based split, keeps flat wire format):**
1. After Part D + P2-device, the flat field set is: identity (`name`,`port`,`device`,`gpu_index`,`enabled`,`profile`) + runtime (`enable_thinking`,`mtp`,`parallel`,`chat_template`,`vision`,`idle_timeout_s`) + tts trio + nested tables + `extra`.
2. Introduce a frozen `SlotRuntime` pydantic model (or dataclass) with the flags/ctx fields, exposed as `SlotConfig.runtime` computed property. Providers that build argv (`providers/container.py`) consume `cfg.runtime`; nothing changes on disk.
3. This gets the **navigability/testability** win (a clear "these fields drive launch flags" seam for the ML-runner precedence chain) without a risky wire-format migration.

**Do a physical nested split (Option B1) only if** you also accept a slot-TOML migration (add a `[runtime]` table, hoist/tuck it, and a one-time on-load promotion of legacy flat keys). Given P3's risk budget and that the model layer is being reworked wholesale by ML-runner, B1's migration cost isn't justified. **Document B2 as the chosen path; note B1 as rejected-for-now with the wire-format-migration reason.**

Either way, the ML-runner handshake removes `mtp` from the *profile* and moves the *authoritative* mtp/jinja to the model record (§7.1a) — `SlotConfig.mtp` remains as the per-slot **override** (tri-state `None`=auto), which is correct and stays. Don't delete `SlotConfig.mtp`.

---

## Part C — `extra="forbid"` on leaf models + escape-hatch table

Current policy audit (verified):

| Model | Current | Target | Rationale |
|---|---|---|---|
| `NpuConfig`, `ImageGenConfig`, `ProfileConfig`, `ProfilesConfig`, `StackModelMeta`, `StackCapabilityRow`, `StackSlotEntry`, `StackConfig`, `StacksConfig`, `UpstreamModelFilters` | **forbid** | forbid | already correct |
| `MemoryGraphConfig`, `MemoryEmbeddingConfig`, `HonchoLLMFeatureConfig`, `HonchoLLMConfig`, `HonchoConfig` | **ignore** | ignore | intentional: silently drop retired cognee/route keys on load (documented in-model) |
| `ModelConfig` | allow | **allow (keep)** | `extra` field is the documented provider-passthrough escape hatch (`[model].extra` → backend verbatim) |
| `ServerConfig` | allow | **forbid** + keep `extra_args`/`env` | no legitimate unknown `[server]` keys; typos should fail. **Escape hatch = the existing `extra_args` freeform string** |
| `SlotConfig` | allow | **allow (keep)** | round-trip fidelity for future fields + provider knobs is load-bearing (hoist/tuck relies on `extra`); this is THE escape hatch model |
| `Hal0Config` | allow | **allow (keep)** | forward-compat: newer hal0 writing a future `[paths]` table must survive an older reader (documented L3016) |
| `HardwareInfo`, `GPUInfo`, `NPUInfo` | allow | **allow (keep)** | additive probe facts from newer probes must round-trip on older readers |
| `ProviderEntry`, `ProvidersConfig`, `UpstreamEntry`, `UpstreamsConfig` | allow | **forbid** (entries) / keep allow (containers) | `ProviderEntry`/`UpstreamEntry` should reject typo'd keys (Tier-1 goal); the list-container models can stay allow |
| `MetaConfig`, `SlotsConfig`, `DispatcherConfig`, `TelemetryConfig`, `MemoryConfig`, `ModelsConfig`, `ActivityConfig`, `BrainChatConfig`, `AgentConfig`, `AgentMetadataConfig`, `AgentMCPConfig`, `MCPServerConfig`, `AgentAuthConfig`, `ToolPolicy` | allow | **forbid** for the leaf tunables (Meta/Slots/Dispatcher/Telemetry/Activity/BrainChat/ToolPolicy/AgentAuth); **keep allow** for the forward-compat containers (`Hal0Config` only) | the whole PLAN §5 Tier-1 promise ("`backend = "vukan"` raises with field path") only holds if leaf tables forbid extras |

**Escape-hatch table (canonical, ship as a docstring block + a test):**

| Model | Policy | Escape hatch |
|---|---|---|
| `Hal0Config` | allow | top-level forward-compat (future tables) |
| `SlotConfig` | allow | `extra` + hoist/tuck round-trip |
| `ModelConfig` | allow | `[model].extra` provider passthrough |
| `HardwareInfo`/`GPUInfo`/`NPUInfo` | allow | additive probe facts |
| `ServerConfig` | forbid | `extra_args` (freeform CLI), `env` (dict) |
| memory/honcho models | ignore | deliberate drop of retired keys |
| everything else | **forbid** | none — typos fail at load |

Add a **single meta-test** that iterates every model class in schema.py and asserts its `model_config["extra"]` matches this table (locks the policy against drift).

---

## Part D — Past-sunset deprecation fields

| Field | Location | Doc says | Owner | Action |
|---|---|---|---|---|
| `SlotConfig.backend` + `_VALID_BACKENDS` + `backend_valid` + `_promote_backend_to_device` | 309–317, 64, 732–737, 628–667 | "v0.2; removed v0.3" | **P2-device** | **Do NOT touch in P3** — note the collision. P2-device deletes it + the 4 translators. P3's SlotConfig split waits for this. |
| `SlotConfig.provider` + `_VALID_PROVIDERS` + `provider_valid` | 340–347, 79, 748–753 | "round-trips for back-compat + UI labels only" | **P3 (this lane)** | Delete field + validator + `_VALID_PROVIDERS`. 29 read-sites — most are `provider_valid`/`providers`-package false-positives; audit the real `cfg.provider` reads (UI label surface, `StackSlotEntry.provider`) and replace with derived runtime_family. Coordinate: `StackSlotEntry.provider`/`StackCapabilityRow.provider` are transport fields, keep. |
| `SlotConfig.runtime` (`Literal["container"]`) | 352–360 | "kept one release" | **P3** | Delete — only value is `container`; 1 read-site. |
| `SlotConfig.workers` | 467–477 | "DEPRECATED / inert... does nothing" | **P3** | Delete — 0 read-sites (grep-verified). Pure removal. |
| `ModelConfig.rope_freq_base` | 152–161 | "DEPRECATED (accepted, ignored)" | **P3** | Delete — launch path no longer emits it. |
| `ModelsConfig.pull_root` | 2835–2844 | "superseded by store; will be removed" | keep | Still the `store`-empty fallback (`effective_store`/`scan_roots`). NOT past-sunset. Leave. |
| `MemoryConfig.engine` cognee value | 2648–2687 | "DEPRECATED, resolves to hindsight" | keep | Still accepted; leave. |

For the removed `SlotConfig` fields: since `extra="allow"` stays on SlotConfig, a legacy slot TOML with `provider = "..."`/`workers = 1`/`runtime = "container"` on disk **round-trips harmlessly through `extra`** rather than erroring — the deletion is safe without a migration. Add a `mode="before"` cleaner that drops these three keys (so they don't get re-written on save), or leave them in `extra` (lossless). Recommend a small before-validator that pops them + logs once, matching the `_promote_backend_to_device` precedent.

---

## Part E — No-op migration framework

`src/hal0/config/migrations/` (`__init__.py` 182 lines + `v1.py` identity). It's a real, working forward-migration runner (`run_migrations`, `MIGRATIONS` registry, `@register`) — but the only registered migration is the v1 identity no-op. **P1-migfw owns the disposition** (tracker L67: "Delete/repurpose identity no-op config-migration framework… repurpose for SQLite migrations").

**P3's stance:** do not delete it in this lane. P3 only touches the schema-version *constants* that live in schema.py:
- `CURRENT_SCHEMA_VERSION` (102), `CAPABILITIES_SCHEMA_VERSION_LEGACY/CURRENT` (113–114), `STACK_SCHEMA_VERSION_CURRENT` (1376), `PROFILE_SCHEMA_VERSION_CURRENT` (1380), `AGENT_CONFIG_SCHEMA_VERSION` (2280).

These stay (they're referenced by `MetaConfig`, `StackConfig`, `AgentConfig` defaults + the runner). **Coordination note for P1-migfw:** if P1 repurposes the framework for SQLite (§7.5), the TOML-era `run_migrations`/`meta.schema_version` machinery either (a) gets deleted once SQLite is the config store, or (b) stays for the remaining file-based configs (agents, slots). P3 leaves the constants intact so P1 can decide. **No P3 edit to `config/migrations/`.**

---

## Files to add / touch

**Add:**
- `src/hal0/config/data/seed_profiles.toml`
- `src/hal0/config/data/seed_stacks.toml`
- `src/hal0/config/data/profile_bench.toml`
- `src/hal0/config/data/family_defaults.toml`
- `src/hal0/config/seeds.py` (loader + `lru_cache` + `reset_cache`)

**Touch:**
- `src/hal0/config/schema.py` — remove the 4 data blocks (~400 lines); add `seeds` bottom-import shims; convert leaf models to `extra="forbid"` (Part C); delete `provider`/`runtime`/`workers`/`rope_freq_base` + validators + `_VALID_PROVIDERS` (Part D); add drop-legacy-keys before-validator; (final PR) `SlotRuntime` view (Part B). Update `__all__`.
- `pyproject.toml` — no change (package-internal data) OR add `force-include` (share/ alternative).
- `installer/etc-hal0/profiles.toml` — plan §1076 says "delete `installer/etc-hal0/profiles.toml` + its prune dance" once `SEED_PROFILES` is the one catalog. That's an installer-lane cleanup; P3 can leave the operator-comment file (it's harmless — seeds are virtual) or delete it and update `install.sh`/`updater.retag`. **Coordinate with installer lane.**
- Consumer imports (later PR, optional): `loader.py`, `profiles/__init__.py`, `updater/updater.py`, `install/profile_derive.py`, `capabilities/profile_fit.py`, `stacks/__init__.py` — migrate to `from hal0.config.seeds import ...`.

---

## Tests

**Existing to update:** `tests/config/test_schema.py` (484L), `test_schema_seeds_d1.py` (138L), `test_profiles.py` (446L), `test_stacks_schema.py` (122L), `test_schema_npu.py`, `test_mtp_override.py`, `test_default_image_gate.py`, `test_profile_derivation_parity.py`, `tests/updater/test_seed_profiles_migration.py`, `tests/profiles/test_catalog.py`, `tests/stacks/*`.

**New:**
1. `tests/config/test_seeds_data.py` — loads all four TOMLs, asserts 21 profiles / 3 stacks, every entry validates through its model, image sentinels resolve, `PROFILE_BENCH` keys ⊆ profile names.
2. **Parity test** — assert `seeds.seed_profiles()` == the pre-refactor hardcoded dict (snapshot the old dict as a fixture; guarantees byte-identical migration incl. every flag string). Same for stacks/bench/family.
3. `test_extra_policy_lock.py` — iterate all schema models, assert `model_config["extra"]` matches the Part C table.
4. `test_deprecated_fields_removed.py` — assert `provider`/`runtime`/`workers`/`rope_freq_base` not in model fields; assert a legacy slot TOML carrying them still loads (round-trips via `extra`) and re-saves without them.
5. `test_slot_runtime_view.py` (final PR) — `cfg.runtime` returns the flag/ctx subset; flat wire format unchanged (load→dump→load identity on real `installer/etc-hal0/slots/*.toml`).
6. Round-trip regression: load every `installer/etc-hal0/slots/*.toml` (8 files) through load→save→load, assert equality (locks the hoist/tuck + deprecated-key handling).

---

## Risks

- **R1 — image-pin fork (highest).** Copying `DEFAULT_ROCMFPX_IMAGE` digest into TOML duplicates the pin the ML-runner registry (§7.1b) is about to own → two sources of truth, silent drift on the next image bump. **Mitigation: sentinel strings resolved from the single constant (A.2); never a literal digest for the rocmfpx lanes.** See §H.
- **R2 — circular import** (`schema ↔ seeds`). Mitigation: bottom-of-module import; test that `import hal0.config.schema` and `import hal0.config.seeds` both succeed cold.
- **R3 — seed comment/ordering loss.** The per-profile comments are operational knowledge (KV/iSWA/`-ub` sizing). `tomli_w` writes no comments, but these files are **hand-authored + shipped**, never re-serialized, so comments survive. The parity test (T2) guards flag-string fidelity.
- **R4 — `extra="forbid"` breaks a real config.** Some installs may have stray keys in `[dispatcher]`/`[telemetry]`. Mitigation: forbid only leaf tunables, keep `Hal0Config` top-level `allow`; ship in a minor with a CHANGELOG note; the load path already raises `ConfigParseError` with the field path (Tier-1 intent).
- **R5 — SlotConfig split churn** collides with P2-device (`backend`) + ML-runner (`mtp`/`image`). Mitigation: **sequence** — split is the last P3 PR, after Part D + P2-device land.
- **R6 — wheel packaging misses `.toml`.** Under `src/hal0/` hatchling includes non-`.py` files by default; add T1 as a smoke test run against a built wheel (`python -m build` + import from the installed wheel) in CI.

---

## §H — Handshake with ML-runner-flags lane

Both lanes touch `SEED_PROFILES`. Ownership split, to avoid conflicting edits:

- **ML-runner-flags owns:** removing `image` and `mtp` **from profiles** (§7.1a: "profiles… lose `image` + `mtp`"); the `RUNNER_IMAGES` registry (§7.1b) that **absorbs** `DEFAULT_ROCMFPX_IMAGE`, `FALLBACK_VULKAN_IMAGE`, `FALLBACK_CUDA_IMAGE`, `resolve_default_image`, `STALE_ROCMFPX_IMAGE_REFS`, and the FLM/kokoro/qwen3tts/comfyui image pins; moving authoritative `mtp`/`jinja` onto the model record (`ModelDefaults.mtp/.jinja`), which deletes the name-regex MTP sniffing and the `*-nojinja`/`*-small` profile clones.
- **P3-schema (this lane) owns:** the **data-externalization mechanism** — `config/data/*.toml` + `config/seeds.py` + the virtual-overlay contract + `SlotConfig`/`SlotRuntime` split + `extra="forbid"` + non-backend deprecation removals.

**Interface contract (the actual handshake):**
1. **Image pins are NOT re-homed into P3's TOML.** They stay in `schema.py` (transition) then move to the runner registry (ML-runner). P3's `seed_profiles.toml` references them via `@`-sentinels resolved in `seeds.py`. When ML-runner lands `RUNNER_IMAGES`, `seeds.py`'s resolver switches from `schema.DEFAULT_ROCMFPX_IMAGE` to `runners.RUNNER_IMAGES[...]` — a one-line change in one place.
2. **Sequencing:** if ML-runner lands first, P3 authors `seed_profiles.toml` **without** `image`/`mtp` (they come from runner+model records) and drops the `*-nojinja`/`*-small` clones ML-runner already deleted — the externalized file is simply the reduced profile set. If P3 lands first, it ships the full 21-profile set with sentinels/`mtp`, and ML-runner later edits **the TOML** (data edit, not code) to strip `image`/`mtp` + remove the clone rows — which is exactly the win §11.3 wants ("flag-tuning becomes a data edit, not a release").
3. **Shared contract to freeze now:** the seed-profile **key set** and the `resolve_profile_flags(profile, mtp_override)` signature. ML-runner changes *what feeds* `mtp_override` (model record vs profile), not the resolver interface. P3 keeps `resolve_profile_flags` + `build_mtp_flag_bundle` in Python unchanged so ML-runner can re-wire callers without a merge conflict in the data files.

**Recommended global order:** P2-device (delete `backend`) → P3 Part A (externalize, low-risk, mechanical) → P3 Parts C/D (forbid + deprecation removals) → ML-runner §7.1a/b (image/mtp out of profiles, runner registry) → P3 Part B (SlotConfig/SlotRuntime split, on the now-clean field set). This orders the near-pure-deletion and mechanical-move work first and defers the one invasive change (the split) until its neighbors have removed the fields it would otherwise have to carry.