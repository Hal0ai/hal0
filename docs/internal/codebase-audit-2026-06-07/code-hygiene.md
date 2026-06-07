# Code Hygiene Audit — 2026-06-07

Broad mechanical sweep of `src/` (183 Python files) and `ui/src/` (36 TypeScript/JSX files) for dead/stub code, duplicated utilities, TODO/FIXME markers, naming drift, oversized files, and test-coverage gaps.  The codebase is generally clean; most debt is deliberate scaffolding that hasn't been filled in yet rather than accidental rot.

---

## 1. TODO / FIXME / Stub Markers

Only two genuine markers exist in the production code — but several `NotImplementedError` stubs are live in exercisable paths:

| Location | Marker / Stub | Notes |
|---|---|---|
| `src/hal0/registry/curated.py:18` | `# TODO(stt/tts curated picks)` | STT/TTS wizard model list is intentionally empty; blocker is unresolved upstream selection. |
| `src/hal0/registry/curated.py:432` | comment documents empty list intent | Companion to the TODO above. |
| `src/hal0/config/features.py:34,48,56` | `raise NotImplementedError("Phase 1: port from /opt/haloai/lib/features.py")` | `FeatureFlags.get/set/list` are all stubs. Any code path that calls these will raise at runtime. No tests exercise the class; its only reference is the `__init__.py` docstring. |
| `src/hal0/installer/wizard.py:71,96` | `raise NotImplementedError("Phase 4: implement FirstRunWizard.state/pick_default")` | Both public methods raise. The wizard is reachable from the installer route; stub hits at runtime if the firstrun flow is triggered before Phase 4. |
| `src/hal0/providers/lemonade.py:524,538,575` | `raise NotImplementedError(...)` | Three provider methods unimplemented. |
| `src/hal0/providers/base.py:182` | `raise NotImplementedError(f"Phase 1: {type(self).__name__} must implement image_ref()")` | Abstract method with no ABC decorator — silently callable until runtime. |
| `src/hal0/api/openrouter/auth.py` | Returns HTTP 501 intentionally (Phase 0 scaffold) | Documented, acceptable — just note V1 hasn't landed yet. |

**Priority: HIGH** — `FeatureFlags` and `FirstRunWizard` stubs can raise at runtime on real user flows.

---

## 2. Dead / Effectively Unused Code

### `src/hal0/config/features.py` (56 lines)
The class is imported in `src/hal0/config/__init__.py` but every method raises `NotImplementedError`. No call site calls it; the module is a placeholder from the haloai port (Phase 1). Until Phase 1 lands it is unreachable dead weight.

### `src/hal0/memory/pgvector_provider.py` (126 lines)
Documented as a degrade-ladder stand-in ("A real pgvector backing is deferred"). It IS wired into `src/hal0/memory/__init__.py:89-100` as the fallback when Hindsight is unavailable, so it is technically live — but its in-memory `self._rows` store means any data written during the degrade is silently dropped on restart. No tests cover it.

### `src/hal0/api/openrouter/` (150 lines total)
The `GET /api/openrouter/auth/callback` route always returns 501. The `_loopback.py` guard module is real code, but the whole package will remain dead until V1 lands the PKCE exchange. Low risk since it is consciously stubbed.

### `src/hal0/agents/pi_coder.py` (wired but v0.2/v0.3 ships hermes-only)
`pi-coder` remains in `BUNDLED_AGENTS` (`src/hal0/agents/manager.py:88`) and is referenced in 14+ places across the codebase (`hermes_provision.py:1564`, `api/routes/mcp.py:530`, `api/agents/personas.py:39`, etc.) but the memory note confirms it was dropped from v0.2/v0.3 promo. The driver is live code but the agent is never actually installed in production bundles. It adds complexity without exercised value until v0.4.

### `scripts/prototype_ttft/metrics_core.py` (121 lines)
Explicitly described in its docstring as a teaching TUI mirror of `src/hal0/slots/ttft_samples.py`. The note in `ttft_samples.py` says "Keep them in sync." This is documented duplication rather than accidental dead code, but as the production path matures the script will inevitably drift.

---

## 3. Duplicated Utilities

### Byte-formatting functions (3 copies)
Three independent implementations of "bytes → human string" exist across the UI:

| Location | Name |
|---|---|
| `ui/src/api/hooks/useModels.ts:403` | `export function fmtBytes(b)` |
| `ui/src/lib/normalizeApiModel.ts:58` | `function formatSize(b)` |
| `ui/src/dash/settings.jsx:160` | `function _fmtBytes(n)` (private, unexported) |

`_fmtBytes` in `settings.jsx` is file-local and could be replaced with the exported `fmtBytes` from `useModels.ts`. `formatSize` in `normalizeApiModel.ts` serves a slightly different display contract but is functionally the same rounding. A shared `ui/src/lib/fmt.ts` would consolidate all three.

### `ConfigInvalidError` defined in two route modules
`src/hal0/api/routes/settings.py:63` and `src/hal0/api/routes/config.py:33` each define their own `class ConfigInvalidError(Hal0Error)`. They should be one class in `src/hal0/errors.py` (or one imported from the other module).

### `httpx.AsyncClient` instantiation scattered across 15+ modules
No shared HTTP client factory exists. Each call site sets its own timeouts and pool limits. The two "managed" clients (`lemonade_proxy.py:_build_client`, `manifest_proxy.py:_build_client`) have a `_get_client()` singleton pattern that other modules do not reuse. A thin `src/hal0/http_client.py` factory (or a `lemonade.client` accessor) would prevent timeout/pool inconsistencies.

### `httpx` imported lazily inside function bodies in 4 places
`src/hal0/api/routes/v1.py:1219`, `src/hal0/api/routes/slots.py:715`, `src/hal0/updater/updater.py:287+421` import `httpx` inside function bodies. This was likely done to avoid import overhead at server startup but is inconsistent with the 25+ modules that import it at top level. It should be normalised one way.

---

## 4. Naming Drift / Inconsistent Conventions

### `persona.py` vs `personas.py` in `src/hal0/agents/`
Two files live side-by-side: `persona.py` (94 lines — enum/TypedDict definitions for the dashboard) and `personas.py` (528 lines — the actual persona store). The singular/plural split is intentional (enums vs business logic) but creates confusion. The module names suggest `persona.py` is a sub-concern of `personas.py` — consider moving the enums into `personas.py` or renaming to `persona_enums.py`.

### `hermes_provision.py` (3393 lines) as a single-file module
The provision orchestrator spans 84 functions and 9 `Phase*` sections. It dwarfs every other module by 50%. This is a functional monolith: install, config rendering, MCP wiring, persona activation, and state management are all in one file. Splitting by phase (e.g. `hermes_install.py`, `hermes_config.py`, `hermes_mcp.py`) would improve navigability.

### `api/agents/` sub-package vs `api/routes/` route modules
Non-route business logic lives in `src/hal0/api/agents/` (`_auth.py`, `budget.py`, `chat_proxy.py`, `memory_stats.py`, `personas.py`, `restart.py`). The naming of this package as `api/agents` is confusing — it is not an HTTP route module (those live in `api/routes/agents.py`) but a service layer scoped to agents. Consider renaming to `api/agent_services/` or moving the logic into `agents/` (the top-level agents package).

---

## 5. Oversized Files (> 500 lines)

Files above 500 lines, ordered by size:

| File | Lines | Notes |
|---|---|---|
| `src/hal0/agents/hermes_provision.py` | 3393 | 9 conceptual phases in one file — needs splitting |
| `src/hal0/slots/manager.py` | 2221 | Slot FSM + systemd + TOML + state reconciliation all interleaved |
| `src/hal0/api/routes/models.py` | 1684 | Registry, pull, HF search, scan all in one route file |
| `src/hal0/api/routes/slots.py` | 1662 | Route handlers + 7 private metric helpers — helpers could be a `slots/metrics.py` |
| `src/hal0/api/__init__.py` | 1596 | App factory + all router registration + lifespan — app factory deserves its own `app_factory.py` |
| `src/hal0/memory/cognee_wrapper.py` | 1481 | Core cognee integration; large but reasonably cohesive |
| `src/hal0/config/schema.py` | 1475 | Pydantic schema — acceptable for a schema module |
| `src/hal0/api/routes/v1.py` | 1273 | OpenAI-compat dispatch; mixing routing + dispatch logic |
| `src/hal0/updater/updater.py` | 1215 | Updater logic; partially contains download logic that could split |
| `src/hal0/dispatcher/router.py` | 1172 | Dispatcher core; reasonably cohesive |

**Priority items**: `hermes_provision.py` (extreme) and `api/__init__.py` (app factory confusion with route registration).

---

## 6. Test-Coverage Gaps

### Entire subsystems with zero test files

| Package | Source files | Test files | Risk |
|---|---|---|---|
| `src/hal0/voice/` | 2 (`kokoro.py`, `moonshine.py`) | 0 | Providers do have tests under `tests/providers/` — this is a false gap |
| `src/hal0/events/` | 1 (`__init__.py` — `EventBus`) | 0 | EventBus is the dashboard's SSE spine; no unit tests |
| `src/hal0/cli/` (most commands) | 10 files | 3 test files (memory_graph, update, docs_parity) | CLI commands for slots, models, migrate, config, capabilities, agents, doctor, registry, main — all untested |

### Specific untested modules with meaningful logic

| File | Lines | Risk |
|---|---|---|
| `src/hal0/events/__init__.py` (EventBus) | 189 | High — SSE fan-out used by every dashboard panel |
| `src/hal0/slots/capacity.py` | 357 | Med — capacity planning used by slot/hardware routes |
| `src/hal0/slots/ttft_samples.py` | 122 | Med — TTFT aggregation used by metrics routes |
| `src/hal0/dispatcher/memory_dispatcher.py` | 107 | Med — MCP memory dispatch adapter |
| `src/hal0/api/image_cache.py` | 176 | Med — LRU eviction logic |
| `src/hal0/api/routes/health.py` | 141 | Med — /api/status + Prometheus shim |
| `src/hal0/api/routes/backends.py` | 453 | Med — NPU backend management route |
| `src/hal0/agents/hermes_refresh.py` | 32 | Low — fire-and-forget subprocess spawner |
| `src/hal0/config/features.py` | 56 | Low (stubs raise, can't be tested yet) |
| `src/hal0/memory/pgvector_provider.py` | 126 | Low (degrade stub, but in live code path) |
| `src/hal0/api/routes/lemonade_proxy.py` | — | Low — cache/single-flight logic has no dedicated tests |
| `src/hal0/providers/base.py` | — | Low — abstract base; `image_ref` stub |

### CLI test gap is the largest structural risk
Only `tests/cli/test_memory_graph_commands.py`, `test_update_commands.py`, and `test_cli_docs_parity.py` exist. The 7 command modules (`slot_commands.py`, `model_commands.py`, `agent_commands.py`, `config_commands.py`, `migrate_commands.py`, `registry_commands.py`, `capabilities_commands.py`) have zero dedicated tests. These are user-facing entry points.

---

## Cross-cutting Seams

- **`EventBus` (events/) ↔ Journal (journal/)**: Both are ring+fan-out SSE buses with nearly identical structure. `journal/__init__.py` explicitly mirrors `EventBus` shape but duplicates the bounded-ring + subscriber logic. A shared base class would eliminate the duplication — this is a seam with the architecture agent.
- **`slots/capacity.py` + `slots/ttft_samples.py` ↔ `api/routes/slots.py`**: Both utility modules are lazily imported inside route handlers via `from hal0.slots.capacity import build_per_slot`. They belong in the slots package and route file imports confirm the coupling — flagged for the slots/backend agent.
- **`scripts/prototype_ttft/metrics_core.py` ↔ `src/hal0/slots/ttft_samples.py`**: Documented parallel implementations — flagged for whoever owns the metrics evolution.
- **`hermes_provision.py` ↔ `agents/hermes/driver.py` + `agents/persona*`**: Provision is tightly coupled to persona rendering and MCP wiring; any refactoring here touches the agents integration surface.
- **`config/features.py` stubs ↔ Settings API (`api/routes/settings.py`)**: When Phase 1 lands, `FeatureFlags` will need to wire into `_settings_apply.py` — both modules currently live independently.
