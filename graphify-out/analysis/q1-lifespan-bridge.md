# Q1 — Why does `lifespan()` have high betweenness centrality?

**Graph node:** `lifespan()` — `src/hal0/api/__init__.py:862`, community 145, **degree 33** (from `graphify explain`).

## Findings

### 1. `lifespan` IS the boot bridge — this is legitimate, not accidental
- Single `async def lifespan(app: FastAPI) -> AsyncIterator[None]` (L862) is the **only** FastAPI startup/shutdown hook in the app. It is wired into the `FastAPI(title=..., lifespan=lifespan, ...)` constructor at L1405 inside `create_app()`.
- Graphify shows **33 outbound edges** spanning ≥14 communities (45 upstreams, 28 dispatcher, 91 audit, 149 ports, 159 personas, 186 slots/identity, 106 NPU trio, 110 hardware, 273 events, 325 seam, 377 omni_router, 233 config, 282 models, 145 itself).
- Highest-impact wiring steps (every one runs inside the single lifespan body, every one is an `app.state.*` assignment consumed by route handlers):
  - L865–866 `UpstreamRegistry()` + `_hydrate_upstreams` → reads `upstreams.toml` into in-process registry
  - L867–868 `ModelRegistry()`, `HardwareProbe()`
  - L873 `load_hal0_config()` (caches `Hal0Config` on `app.state.hal0_config`)
  - L886 `scan_and_register(model_registry, hal0_cfg.models)` — gated by `auto_scan_on_start`
  - L903 `model_cache: dict[str, list[str]] = {}` — the shared `/v1/models` cache
  - L931–935 `AuditStore(...)` + `init_schema()` + `prune()` (durable mirror)
  - L952 `EventBus(sink=_audit_sink ...)` — wired as the durable audit forwarder
  - L963–967 `SlotIdentityStore()`, `PortAuthority(pool=..., reserved={8080:"api"})`
  - L974 `SlotManager(event_bus=..., upstreams_registry=..., identity_store=..., port_authority=...)`
  - L981 `Dispatcher(upstream_registry=..., model_registry=..., cached_models=lambda name:..., fetch_models=_fetch_and_cache, slot_manager=...)`
  - L997, L1005, L1014 `slot_manager.reconcile_unconfigured_slots()` / `reconcile_npu_trio_slots()` / `fold_identity()` (rework §11.1/§11.2)
  - L1024 `slot_manager.start_idle_monitor(...)`
  - L1035, L1038 `_prime_hal0_composite_cache(...)`, `_seed_multiplex_models(...)`
  - L1045 `slot_manager.reconcile_container_upstreams()` (#732)
  - L1064 `app.state.hardware_stats = HardwareStats()`
  - L1069, L1076 `app.state.model_pull_jobs`, `app.state.model_pull_tasks`
  - L1081 `app.state.shutting_down = asyncio.Event()`
  - L1086, L1095 `sweep_orphaned_partials()`, `sweep_pull_jobs()`
  - L1108 `_auto_resume_interrupted_pulls(app)` (#1225)
  - L1129 `app.state.hermes_kanban = HermesKanbanClient.from_env()`
  - L1137 `register as _register_hermes_executor` (KB-5 bridge, optional)
  - L1141 `await event_bus.emit("system.restart", ...)`
  - L1168–1191 `seed_default_personas` + `mark_home_managed_if_owned` — idempotent persona seeding
  - L1200–1206 `seed_static_slots` — idempotent static slot TOML copy (gap-fill for `hal0 update`)
  - L1213–1218 `CapabilityOrchestrator(slot_manager=..., registry=...)` + `initialize_if_missing()`
  - L1265–1269 `MetricsService(slot_manager=..., registry=...)` + `seam` exposed on `app.state.metrics_seam`
  - L1288–1292 `asyncio.create_task(_refresh_model_cache_on_ready(...))` (background refresh)
  - L1307 `slot_manager.arbiter.run_idle_loop()` (GpuArbiter D6 idle-restore)
  - L1337 `OmniRouter(slot_manager=..., http_client=..., api_base_url=...)`
  - L1365 `NpuTrioRouter(slot_manager=...)`
  - L1376–1382 `AsyncExitStack()` opens MCP session managers, refresh task, gpu arbiter idle task, `metrics_service.start()` — yielding inside the stack; `push_async_callback` orders shutdown.
  - **Shutdown (L1384–1397, `finally`):** `_shutdown_pull_jobs(app)` → `omni_router_client.aclose()` → `slot_manager.stop_idle_monitor()` → `dispatcher.aclose()` → `comfyui.aclose_client()`.

### 2. Every subsystem needs a single hand-off point — that hand-off is `lifespan`
- The graph's betweenness of 0.102 reflects the structural fact that there is exactly **one** place where the in-process wiring for every community converges. Removing any of these would not move the wiring to another node — there is no alternative. Routes reach into `app.state`; nothing else constructs `SlotManager`, `Dispatcher`, `EventBus`, `AuditStore`, `MetricsService`, etc. outside this function.
- `graphify path "lifespan" "seed_static_slots"` confirms 1-hop (`indirect_call`). Same for the other seeds. This is by construction: they are imported lazily inside `lifespan` so they can degrade without blocking startup.

### 3. The bridge's centrality is legitimate — but the file is showing strain
- The function has grown from a normal lifespan to a ~540-line orchestrator. The 33 outbound edges and 14+ communities crossed are proportional to that growth.
- The ordering constraints hidden in the body (SlotManager must precede Dispatcher so `forward()` can flip SERVING; EventBus must precede SlotManager; `CapabilityOrchestrator` must come AFTER `slot_manager` + `registry`; `MetricsService` last so its `start()` migrations apply to a fully-wired world) are non-obvious. They are not encoded anywhere — they live only as comment-ordering.
- Every init step is wrapped in `try/except` + `log.warning(..._failed, error=str(exc))`. This is intentional ("must never block startup") but it means a partial boot is a common steady state — `audit_store` may be `None`, `omni_router` may be `None`, `npu_trio_router` may be `None`, the gpu arbiter task may be `None`, the identity store may be `None`. Routes handle this by checking for `None`, but the contract is implicit and only lives at construction sites.
- `app.state` carries ~25+ attributes by the end. They become the cross-community handshake bus. There is no schema; names are bare strings.

### 4. The 33 edges are not 33 *equivalent* responsibilities
The graph's uniform "degree = 33" obscures three very different wiring patterns:

| Pattern | Examples | Implication |
|---|---|---|
| **Hard dependency** (downstream broken if absent) | `SlotManager`, `Dispatcher`, `EventBus`, `model_cache`, `HardwareStats`, `AuditStore` | Ordering is load-bearing |
| **Soft dependency** (degrades to None on failure) | `SlotIdentityStore`, `PortAuthority`, `OmniRouter`, `NpuTrioRouter`, `_register_hermes_executor`, `mark_home_managed_if_owned`, `seed_default_personas`, `seed_static_slots`, gpu arbiter, pull sweeps | One bug ≠ outage, but ≠ boot either |
| **Background task** (async task created, cancelled on exit) | `_refresh_model_cache_on_ready`, `slot_manager.arbiter.run_idle_loop()`, `metrics_service.start()` | Lifetime owned by `AsyncExitStack` |

The graph treats these the same. The betweenness number conflates them.

## Risks / Smells

1. **Hidden init ordering.** L974 SlotManager before L981 Dispatcher; L952 EventBus before L974 SlotManager; L1213 CapabilityOrchestrator after registry ready. Encoded only in source order + comments. Refactor risk: any reorder silently breaks dispatch.
2. **Inconsistent error policy.** Some failures set state to `None` (omni_router, npu_trio_router, identity_store, port_authority, audit_store), some log+continue (auto_scan, sweeps, seeding). One canon would help.
3. **`app.state` sprawl.** ~25+ bag attributes with bare string names. The cross-community handshake bus is invisible to typecheckers and discoverable only by `grep`.
4. **Lifespan as god function.** A single function is responsible for: reading config, scanning models, building durable stores, building in-memory caches, reconciling persisted state, starting background tasks, attaching routers/MCP/board bridges, and ordering shutdown. Anything that touches boot must land here.
5. **Defensive layering is unobservable.** Every `try/except` log is best-effort; the function may complete with several subsystems missing. There is no healthcheck that surfaces "I booted with degraded X".

## Recommendations

1. **Split lifespan into named phases.** Keep `lifespan()` as a thin orchestrator that calls pure constructors + side-effect starters in a fixed order:
   - `boot_config()` → `hal0_cfg`
   - `boot_persistence()` → `(audit_store, identity_store, port_authority)`
   - `boot_runtime()` → `(event_bus, slot_manager, dispatcher, metrics_service)`
   - `boot_reconcile()` → reconciliation passes
   - `boot_seeds()` → persona + static slot seeds
   - `boot_attachments()` → OmniRouter / NpuTrioRouter / Hermes executor / MCP managers
   - `boot_tasks()` → refresh task, gpu arbiter idle loop, metrics start
   - `lifespan()` → AsyncExitStack wiring + finally-order shutdown.
   Each becomes a single community-scoped module, e.g. `hal0.api.boot.runtime`. Order is now an explicit list, not free-form lines.
2. **Encode boot policy in a small dataclass**, e.g. `BootReport { audit: bool, identity: bool, omni: bool, npu: bool, arbiter: bool, ... }`. Surface it on `app.state.boot_report` so `/api/health` and dashboards can render degraded-state explicitly instead of probing each `app.state.X is None`.
3. **Type the app-state bag.** A single `AppState` TypedDict (or `dataclass(slots=True)` attached in `create_app`) gives typecheckers the chance to catch typos in route handlers. ~25 strings → 25 typed slots.
4. **Stop importing inside `lifespan`.** Top-level imports for `seed_default_personas`, `seed_static_slots`, `OmniRouter`, `NpuTrioRouter`, `HermesKanbanClient`, `HardwareStats`, `MetricsService`, `CapabilityOrchestrator` would lose nothing — they are stable. Lazy imports here are pay-as-you-go but obscure the dependency graph from anyone reading the function top-to-bottom. (Cycle-avoidance is the only valid reason; none of these appear to be cyclic.)
5. **Document the ordering contract.** The file currently has 5+ multi-line comments explaining init order ("SlotManager owns slot state. Built before Dispatcher..."). That knowledge belongs in a single docstring at the top of `lifespan()` (or in `boot/__init__.py`) listing the load-bearing ordering invariants.

## Verdict

Centrality = **legitimate**. Betweenness of 0.102 is correct: this is the single, mandated FastAPI lifespan hook and by design every subsystem converges through it. The smell is not the wiring count — it is the file growing into a 540-line script that combines config load, persistence bootstrap, runtime construction, reconciliation, seeding, attachment, background tasks, and shutdown ordering in one place. Refactor target: extract phases, type the state bag, surface a `BootReport`.