# A1 — Backend Architecture & Seams

**Summary.** The backend spine (`api → dispatcher → slots → providers/lemonade → upstreams → capabilities → registry`) is layered with *mostly clean, one-directional* dependencies — `slots/manager.py` even documents "this module does NOT import from `hal0.dispatcher`" (`src/hal0/slots/manager.py:20`), so the Dispatcher↔SlotManager relationship is a clean injected seam, **not** a cycle. The real problems are concentrated in two oversized hubs (`slots/manager.py`, 2060 lines / `dispatcher/router.py`, 50 KB), a god *module* of wiring glue (`api/__init__.py`, 1596 lines), routing logic smeared across three places, and a handful of leaky private-member reaches across module boundaries.

> Note on the graph: `graphify explain` anchors `SlotManager`/`ModelRegistry`/`UpstreamRegistry` to `api/__init__.py` (degree 17–24) because that is where they are *imported and wired* in `lifespan()`. Their real homes are `slots/manager.py`, `registry/store.py`, `upstreams/registry.py`. The high degree is a faithful signal that **`api/__init__.py` is the integration god-module**, not that those classes are themselves hubs.

---

## 1. The spine, layer by layer

| Layer | Home | Role | Talks to |
|---|---|---|---|
| API/wiring | `src/hal0/api/__init__.py` | app factory, `lifespan()`, slot-alias/model-view glue | everything |
| Dispatch | `src/hal0/dispatcher/router.py:327` `Dispatcher` | registry-aware request router (read-only on slots) | UpstreamRegistry, ModelRegistry, SlotManager (injected) |
| Slot lifecycle | `src/hal0/slots/manager.py:182` `SlotManager` | FSM, spawn, idle, CRUD, NPU exclusivity, routing | providers (lazy import) |
| Backend abstraction | `src/hal0/providers/base.py:104` `Provider` ABC + 7 impls | start/health/infer per backend | LemonadeClient |
| Lemonade transport | `src/hal0/lemonade/client.py:117` `LemonadeClient` | HTTP/WS to lemond | — |
| Upstreams | `src/hal0/upstreams/registry.py:147` `UpstreamRegistry` | in-memory upstream table + warmup/health/tps | httpx only |
| Capabilities | `src/hal0/capabilities/orchestrator.py:192` | "thin overlay" mapping capability children → slots | SlotManager, ModelRegistry, LemonadeClient |
| Registry | `src/hal0/registry/store.py:77` `ModelRegistry` | model-id → upstream bindings (TOML, mtime-cached) | — |

Dependency direction is correct: lower layers (`registry`, `upstreams`, `lemonade`) have no upward imports; `slots` imports `providers` only via a lazy in-function import (`src/hal0/slots/manager.py:580`); `dispatcher` imports `SlotManager` only under `TYPE_CHECKING` (`src/hal0/dispatcher/router.py:55`) and receives it by constructor injection (`src/hal0/dispatcher/router.py:359`).

---

## 2. God objects / oversized hubs

### 2.1 `SlotManager` — the central god object (degree 24 in graph)
`src/hal0/slots/manager.py:182`, ~50 methods, file is 2060 lines. One class owns at least seven distinct responsibilities:
- FSM transitions + broadcast: `_transition` (`:301`), `_broadcast` (`:424`), `state_stream` (`:441`)
- fail-watching: `_fail_watch_loop` (`:504`), `_update_fail_watcher` (`:464`)
- lifecycle: `load`/`unload`/`restart`/`swap`/`start`/`recover_evicted_slot` (`:593`–`:803`)
- process spawn/terminate: `_spawn_locked` (`:1215`), `terminate` (`:1268`)
- config CRUD: `create`/`delete`/`update_config`/`reconcile_unconfigured_slots` (`:1319`–`:1508`)
- request routing: `route_for_request` (`:1064`), `default_slot_for` (`:1039`)
- idle monitor + capacity + model-pull + NPU exclusivity: `_idle_monitor_loop` (`:1864`), `_needs_pull` (`:1706`), `_check_npu_exclusivity` (`:1617`)

**DEEPEN:** extract the FSM/broadcast/fail-watch cluster (`:301`–`:564`) into a `SlotStateMachine` collaborator, and the idle-monitor loop (`:1824`–`:1896`) into the existing `IdleDriver` it already cooperates with. The lifecycle methods stay on the manager. This splits the 99 KB file along its natural seams without changing the public surface callers depend on.

### 2.2 `Dispatcher` — one 780-line class
`src/hal0/dispatcher/router.py:327` runs to ~`:1113` — a single class holding the 4-tier resolution ladder, the streaming/plain forward paths, header filtering, single-flight prefetch, and eviction recovery. Module-level helpers (`_remap_model` `:1114`, `_resolve_target_url` `:129`, `_filter_response_headers` `:1159`) are already extracted; the class body is still the hub.

### 2.3 `api/__init__.py` — god *module* of glue
1596 lines of free functions that fuse three layers: slot views (`hal0_slot_alias_models` `:313`, `hal0_llm_slot_views` `:433`, `hal0_chat_slot_alias_map` `:398`), upstream hydration (`_autoregister_slot_upstreams` `:500`, `_hydrate_upstreams` `:647`, `_seed_multiplex_models` `:613`), and lemonade lifecycle (`_start_lemonade_idle_driver` `:751`, `_start_lemonade_metrics_shim` `:685`). `lifespan()` (`:808`) is ~370 lines.

**DEEPEN:** the `hal0_*_slot_*` view/alias functions (`:276`–`:500`) read `SlotManager` + `ModelRegistry` to compute composite model lists; they belong to a `slots`-owned (or new `views`) module behind a clean `slot_model_views(slot_manager, registry)` seam, not in the API package root. Likewise `_autoregister_slot_upstreams`/`_hydrate_upstreams` are **upstream wiring that lives in the API god-module instead of `upstreams/`** (see §4).

---

## 3. Leaky abstractions / private-member reaches

- **Dispatcher reaches into `SlotManager._current_state()`** (private) at `src/hal0/dispatcher/router.py:669` and `:704`. The seam is otherwise clean DI; this one private reach is the leak. **DEEPEN:** add a public `SlotManager.state(name) -> SlotState` accessor and have the Dispatcher consume that.
- **`CapabilityOrchestrator` imports `LemonadeClient` internals directly** — `flm_args_from_lemond_config`, `flm_args_set_payload` (`src/hal0/capabilities/orchestrator.py:47`) — to read/write lemond `flm_args` for the NPU trio (`:478`–`:503`). A capability-layer module mutating lemond's process args is a transport-detail leak two layers down. **DEEPEN:** fold the FLM-trio `flm_args` read/modify/write into a `LemonadeClient.set_flm_modality(child, enable)` method so the orchestrator drives it through one verb.
- **`route_for_request` on `SlotManager`** (`src/hal0/slots/manager.py:1064`) duplicates routing concern that the `Dispatcher` (`router.py`) also owns — see §5.

---

## 4. `upstreams/` is hydrated from the wrong layer

`UpstreamRegistry` (`src/hal0/upstreams/registry.py:147`) is a clean in-memory store (add/upsert/health/warmup/tps), with no upward deps. But the logic that *populates* it from slot configs lives in `api/__init__.py`: `_autoregister_slot_upstreams` (`src/hal0/api/__init__.py:500`) and `_hydrate_upstreams` (`:647`). So the `upstreams` layer cannot own its own hydration; the API package does. **DEEPEN:** move hydration into an `UpstreamRegistry.hydrate_from_slots(slot_manager)` method (or a small `upstreams/hydrate.py`), leaving `api/__init__.py` to call one verb.

---

## 5. "Should be one" — routing is split three ways

The single concern *"given a request, which upstream/slot serves it?"* is implemented in three places:
1. `Dispatcher` resolution ladder — registry → passthrough → cold-cache prefetch → legacy fallback (`src/hal0/dispatcher/router.py:327`+, documented `:8`–`:24`).
2. `SlotManager.route_for_request` (`src/hal0/slots/manager.py:1064`) and `default_slot_for` (`:1039`).
3. Legacy heuristic fallback `proxy.resolve_slot` (`src/hal0/dispatcher/proxy.py`, invoked from the Dispatcher's tier-4).

**DEEPEN:** consolidate behind the `Dispatcher` as the single routing authority; demote `SlotManager.route_for_request` to a thin delegate or remove it, and schedule the `proxy.resolve_slot` legacy tier for deletion (its own docstring marks it "Kept until v0.2", `router.py:21`). One routing seam, one place to reason about resolution order.

## 6. "Should be one" — the FLM/NPU trio path

NPU-trio dispatch is spread across `dispatcher/flm_trio.py` (`FLMTrioRouter`, owns its own `LemonadeClient` `:127`), the discriminator `v1._is_npu_trio_request` (gates on the slot record `type` field), and `CapabilityOrchestrator._apply_npu_trio_modality` (`orchestrator.py:682`) which writes the `device=npu` slot record + lemond `flm_args`. The trio is one feature implemented as a triangle of capabilities↔slots↔dispatcher side-effects. This is the most fragile coupling in the spine (matches auto-memory `hal0_npu_flm_trio_asr_embed_serving`). Lower-priority to refactor, but worth a single owning module long-term.

---

## Cross-cutting seams (for other agents)

- **→ API/routes agent (v1.py):** the slot-alias/model-view functions (`api/__init__.py:276`–`:500`) and `_is_npu_trio_request` gating are consumed by the request handlers. If routes are being mapped, that glue is the contract surface between this spine and the HTTP layer.
- **→ Providers agent:** `Provider` ABC (`providers/base.py:104`) with 7 impls is the real backend-abstraction seam; `SlotManager` reaches it only via lazy `lemonade_provider()` (`providers/__init__.py:69`). Worth confirming whether non-lemonade providers (comfyui/kokoro/moonshine, 1800+ lines combined) are still wired or dead under the "Lemonade-only v0.2" claim (`manager.py:1`).
- **→ Registry/model-store agent:** `ModelRegistry` (`registry/store.py:77`) auto-regenerates the lemonade catalog on mutation via `_notify_change` (`:287`) — see auto-memory `hal0_server_models_autoregen_on_mutation`. That on-change hook is a seam into `lemonade/server_models_gen.py`.
- **→ Lemonade/transport agent:** `LemonadeClient` is owned by both `flm_trio.py` (`:127`) and the capabilities orchestrator (`:47`); reconcile who instantiates vs. injects it.
- **→ Config agent:** `CapabilityOrchestrator` and `SlotManager` both write slot TOML (`config/loader.write_toml_atomic`); drift between `capabilities.toml` and `slots/*.toml` is a known reconcile hazard (auto-memory `hal0_orchestrator_drift_bug`).
