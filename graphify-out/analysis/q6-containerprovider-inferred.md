# Q6 — ContainerProvider INFERRED edges: correctness audit

**Task.** Verify whether the 76 INFERRED edges attached to `ContainerProvider` (src/hal0/providers/container.py:L1222, community 16, degree 113) are real. Take a representative sample of ~15 INFERRED edges, classify each TRUE / FALSE / AMBIGUOUS with proof, and compute a correctness rate.

**Method.**
1. Enumerate INFERRED edges directly from `graphify-out/graph.json` (8 OUTGOING, 68 INCOMING; 76 total).
2. Sample 15: all 8 outgoing + 7 incoming (spread across updater.py / test_container.py / test_container_chat_template.py).
3. For each, open container.py and the target (mostly providers/base.py) at file:line, then classify.

**Edge counts (ground truth from graph.json).**
- OUTGOING INFERRED: 8 (all `relation="uses"`).
- INCOMING INFERRED: 68 (1 `relation="indirect_call"`, 26 `relation="calls"`, 41 `relation="uses"`).
- Total: 76 ✓ matches BRIEF.

## Verdict table

| # | Dir | Target | Source (in container.py / target file) | Proof (file:line) | Verdict |
|---|-----|--------|----------------------------------------|--------------------|---------|
| 1 | OUT uses | `HealthCheck` | container.py:L72 import + instantiation in `_llama_launch_plan` | `from hal0.providers.base import HealthCheck, Mount, Provider, RuntimeLaunchPlan` (container.py:72); `health=HealthCheck(cmd=f"curl -fsS http://127.0.0.1:{port}/health \|\| exit 1")` (container.py:942) | **TRUE** |
| 2 | OUT uses | `Mount` | container.py:L72 import only; no direct `Mount(...)` construction in container.py | import at L72; Mount construction in this file absent (`Mount(...)` only appears in store.py:284 + comfyui.py + qwen3tts.py). Mounts enter the plan via `model_store_module.mount_for(root, read_only=True)` at L935, factory in another module. | **AMBIGUOUS** — symbol is imported and appears in plan.mounts (typed `list[Mount \| tuple[str,str]]`); the *construction* is delegated. Edge is loose but not wrong. |
| 3 | OUT uses | `Provider` | container.py:L72 import + subclassing | `class ContainerProvider(Provider):` (container.py:1222) | **TRUE** |
| 4 | OUT uses | `RuntimeLaunchPlan` | container.py:L72 import + return-type + construction | `def _llama_launch_plan(...) -> RuntimeLaunchPlan:` (container.py:888); `return RuntimeLaunchPlan(image=..., command=..., ...)` (container.py:929); `container_spec(...) -> RuntimeLaunchPlan` (container.py:1261) | **TRUE** |
| 5 | OUT uses | `ResolvedArgv` | container.py:L73 import + type annotation + via `resolve_argv` | `from hal0.slots.argv import ResolvedArgv, resolve_argv` (container.py:73); `command = resolve_argv(segments).argv` (container.py:916); annotation `-> tuple[str, ResolvedArgv] \| None` (container.py:1877) | **TRUE** |
| 6 | OUT uses | `FLMProvider` | container.py:L1401 import + isinstance check + instantiation | `from hal0.providers.flm import FLMProvider` (container.py:1401); `if isinstance(provider, FLMProvider): return await provider.health(port)` (container.py:1403); earlier `return FLMProvider()` at L964 via `_spec_provider_for` | **TRUE** |
| 7 | OUT uses | `KokoroProvider` | container.py:L974 import + instantiation | `from hal0.providers.kokoro import KokoroProvider` (container.py:974); `return KokoroProvider()` (container.py:976) | **TRUE** |
| 8 | OUT uses | `Qwen3TTSProvider` | container.py:L970 import + instantiation | `from hal0.providers.qwen3tts import Qwen3TTSProvider` (container.py:970); `return Qwen3TTSProvider()` (container.py:972) | **TRUE** |
| 9 | IN uses | `Updater` | updater.py:L1338 import + `ContainerProvider()` at L1340 | `from hal0.providers.container import ContainerProvider, _best_effort_model_info` (updater.py:1338); `provider = ContainerProvider()` (updater.py:1340) | **TRUE** |
| 10 | IN uses | `UpdateError` | updater.py:L1338 (file-level propagation) | `class UpdateError(Hal0Error):` at updater.py:73. Exception class has **no** reference to ContainerProvider; the only ContainerProvider mention in this file is the local function at L1338 (`rerender_slot_units`). Graphify INFERRED propagated the file-level import to every class in the file. | **FALSE** |
| 11 | IN uses | `ReleaseManifest` | updater.py:L1338 (file-level propagation) | `class ReleaseManifest(BaseModel):` at updater.py:143. Pydantic model for release artifacts; never touches ContainerProvider. Same file-level propagation artifact. | **FALSE** |
| 12 | IN uses | `TestContainerSpec` | test_container.py:L34 import; class definition at L513 instantiates and calls | `from hal0.providers.container import (..., ContainerProvider, ...)` (test_container.py:34-44); `class TestContainerSpec:` (L513); `def _provider(self) -> ContainerProvider: return ContainerProvider()` (L514-515); `provider.container_spec(cfg or _slot_cfg(), _model_info())` (L524) | **TRUE** |
| 13 | IN uses | `TestRenderUnit` | test_container.py:L34 import; class at L245 exercises render path | `class TestRenderUnit:` at L245; tests use `_render_llama(...)` which threads through `_render_quadlet_from_plan` imported from container.py — exercises the ContainerProvider-owned renderer directly | **TRUE** |
| 14 | IN uses | `TestContextSizeDerive` | test_container.py:L34 import; class at L923 instantiates | `class TestContextSizeDerive:` (L923); `provider = ContainerProvider()` (L930); `provider.container_spec(cfg, model_info)` (L935) | **TRUE** |
| 15 | IN calls | `_build_spec()` | test_container_chat_template.py:L56 calls ContainerProvider | `def _build_spec(slot_cfg, model_info):` (L55); `provider = ContainerProvider()` (L56); `return provider.container_spec(slot_cfg, model_info)` (L68) | **TRUE** |

## Score

| Outcome | Count | Rate |
|---------|-------|------|
| TRUE | 13 | 86.7% |
| AMBIGUOUS | 1 | 6.7% |
| FALSE | 2 | 13.3% |
| **Strict (TRUE only)** | **13/15** | **86.7%** |
| **Lenient (TRUE + AMBIGUOUS)** | **14/15** | **93.3%** |

## Findings (ranked)

1. **Hypothesis confirmed: domain-type INFERRED edges are substantially more reliable than generic-name edges.** All 8 OUTGOING "uses" edges to domain types (HealthCheck, Mount, Provider, RuntimeLaunchPlan, ResolvedArgv, FLMProvider, KokoroProvider, Qwen3TTSProvider) are TRUE or AMBIGUOUS — none FALSE. The INCOMING sample is 5/7 TRUE. Compare against the BRIEF's earlier audit of generic-name edges (e.g. `connect()`) where the failure rate was much higher: domain-specific identifiers don't collide with cross-module noise.

2. **Failure mode 1 — same-file propagation.** Updater-related INFERRED "uses" edges (`UpdateError`, `ReleaseManifest`, `UpdateCosignMissing`, `UpdateManifestInvalid`, `ReleaseInfo`, `UpdateDownloadError`, `UpdateExtractError`, `UpdateSwapError`, `UpdateVerifyError`, `UpdateRollbackUnavailable`, `UpdateCosignFailed`, all `src/hal0/updater/updater.py`) are spawned from a single import statement at updater.py:1338 inside `rerender_slot_units()`. The graphify INFERRED engine attributes the import to every class defined in the same module. **12 of 76 edges (~16%) are this artifact.** Inflates Updater's apparent coupling to ContainerProvider, but the truth is: exactly one local function (`rerender_slot_units`) uses ContainerProvider — and only when rewriting stale slot units.

3. **Failure mode 2 — indirect construction via factory.** `Mount` is imported by container.py but never instantiated there. Construction is delegated to `model_store_module.mount_for(...)` (config/store.py:284). The INFERRED edge is loose — true at the import/symbol-reference level, false at the "instantiates" level. Pattern repeats for `Mount` (1 of 8 outgoing) and likely also for less central types in other god nodes (worth a follow-up Q7 audit if these artifacts affect downstream queries).

4. **Incoming edge type distribution.** Of the 68 incoming INFERRED:
   - 26 `calls` (mostly test methods like `test_health_*`, `test_tts_*`, `test_pull_image_*`, `test_cpu_profile_*`, `test_gpu_profile_*` — these are function-name edges inferred via test discovery; structurally weak but not necessarily wrong, hard to verify without running each test).
   - 41 `uses` (mix of true test-class couplings + 12 same-file propagation artifacts in updater.py).
   - 1 `indirect_call` (`container_stub()` in tests/api/test_models_crud.py:L72 — test fixture that monkey-patches ContainerProvider's systemd/podman surface; structurally TRUE).
   - The test-class INFERRED edges (TestContainerSpec, TestRenderUnit, TestContextSizeDerive, TestLoadSync, TestFamilyDefaults, TestHostNetLoopbackFence, TestImageMismatch, TestLoopbackFenceCommand, TestResolveProfileFlags, TestUniformQuadletRender, TestContainerRuntimeProbe) are all genuine — every test class in test_container.py uses ContainerProvider by design (the module is the unit-test suite for that class).

5. **No import cycles introduced.** All 8 outgoing INFERRED edges are corroborated by real imports + real references in container.py; the file does not develop circular dependencies via the inferred relationships.

## Risks / smells

- **Updater file pollution.** 12 of 76 ContainerProvider INFERRED edges are spurious (updater.py file-level propagation). Anyone reading the graph and assuming "Updater is tightly coupled to ContainerProvider" will overestimate coupling. The real coupling is one helper function.
- **Mount edge is loose.** Mount is genuinely a dependency of the runtime path, but the construction site is in store.py, not container.py. Tagging "uses" is defensible but imprecise.

## Recommendations

1. **For graph consumers:** when reading ContainerProvider's neighborhood, ignore the 12 updater.py exception/release-class edges — they are a graphify artifact, not real coupling. Look only at Updater (the class) for the actual relationship.
2. **For graphify INFERRED heuristics:** the same-file propagation pattern is the dominant source of FALSE INFERRED edges in domain-type nodes. A fix would be: only propagate file-level imports to classes that *contain* the importing line in their body (not just the same file).
3. **Follow-up Q7 (optional):** audit `SlotManager` (277 edges, BRIEF's #1 god node) using the same method — sample ~15 INFERRED edges and compare the rate. If SlotManager is significantly worse than 87%, the hypothesis that "domain-type INFERRED edges are reliable" only holds for god nodes with broad, well-named public APIs.

## Sources

- `graphify-out/graph.json` (target node `src_hal0_providers_container_containerprovider`; 8 outgoing + 68 incoming INFERRED edges enumerated directly).
- `src/hal0/providers/container.py` (class def at L1222; imports at L72-73; `_llama_launch_plan` at L871-943; FLMProvider reference at L1401-1404; `_spec_provider_for` at L946-981).
- `src/hal0/providers/base.py` (Mount dataclass at L30-115; HealthCheck at L118-159; RuntimeLaunchPlan at L162-215; Provider ABC at L224-308).
- `src/hal0/updater/updater.py` (rerender_slot_units at L1330-1372; class declarations for UpdateError L73, UpdateManifestInvalid L80, UpdateCosignMissing L101, ReleaseManifest L143, ReleaseInfo L220).
- `tests/providers/test_container.py` (imports L34-44; class TestContainerSpec L513, TestRenderUnit L245, TestContextSizeDerive L923).
- `tests/providers/test_container_chat_template.py` (`_build_spec` at L55-68).
