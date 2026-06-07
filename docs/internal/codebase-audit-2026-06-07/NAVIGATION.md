# NAVIGATION — hal0 codebase "where to look" index

> Synthesis artifact from the 2026-06-07 six-agent audit. First stop for any
> future agent. Goal: cut token spend by pointing at the real def-home (file:line)
> AND the graphify command that surfaces it.

## How to use graphify here (read this first)

The graph lives at `graphify-out/graph.json` (built from commit `78ab1fd7`; run
`git rev-parse HEAD` to check staleness; `graphify update .` to refresh — **do not**
run update in a read-only pass).

Command quality, verified against this graph:

| Command | Verdict | Notes |
|---|---|---|
| `graphify explain "<Node>"` | **Best** — use this | Returns a clean node card (source, community, degree, neighbours). Use exact ClassName/func names. |
| `graphify path "<A>" "<B>"` | OK, but noisy | Emits `source/target match was ambiguous` warnings when names collide; the path it returns may route through a shared type (e.g. `SlotManager → SlotState ← Dispatcher`). Treat the path as a hint, not gospel. |
| `graphify query "<NL question>"` | **Avoid for routing/spine questions** | A natural-language query like *"how does request routing work"* returned `budget.py`, `Persona`, `Hal0Error` — BFS noise, not the dispatcher. Use `explain` on a known node instead. |

**CRITICAL graphify caveat (from A1):** `explain` reports the *wiring site*, not the
real home, for the big injected classes. E.g. `explain "SlotManager"` says
`Source: src/hal0/api/__init__.py L135` — that's where it is imported and wired in
`lifespan()`, **not** its definition (`src/hal0/slots/manager.py:182`). The high degree
(24) is a faithful signal that `api/__init__.py` is the integration god-module. Every
entry below pairs the graphify command with the **true def-home** so you don't get sent
to the god-module by mistake.

---

## The backend spine (top-to-bottom dependency order)

One-directional, no import cycle (`slots/manager.py:20` explicitly documents it does
NOT import `dispatcher`; `dispatcher/router.py:55` imports `SlotManager` only under
`TYPE_CHECKING` + constructor injection).

| Layer | True def-home (file:line) | graphify | Role |
|---|---|---|---|
| App factory / wiring | `src/hal0/api/__init__.py:1195` `create_app()` | `explain "create_app"` | mounts ~35 routers, `lifespan()` at `:808` (~370 lines) |
| Dispatch | `src/hal0/dispatcher/router.py:327` `Dispatcher` | `explain "Dispatcher"` (⚠ node is `api/deps.py:46` — the DI dep, not the class; go to router.py:327) | 4-tier resolution ladder + forward + recovery |
| Slot lifecycle | `src/hal0/slots/manager.py:182` `SlotManager` | `explain "SlotManager"` (⚠ reports `api/__init__.py L135`) | FSM, spawn, idle, CRUD, NPU exclusivity, routing |
| Backend abstraction | `src/hal0/providers/base.py:104` `Provider` ABC | `explain "Provider"` | start/health/infer per backend; 6 impls |
| Lemonade transport | `src/hal0/lemonade/client.py:117` `LemonadeClient` | `explain "LemonadeClient"` | HTTP/WS admin client to lemond |
| Upstreams | `src/hal0/upstreams/registry.py:147` `UpstreamRegistry` | `explain "UpstreamRegistry"` | in-memory upstream table + warmup/health/tps |
| Capabilities | `src/hal0/capabilities/orchestrator.py:192` `CapabilityOrchestrator` | `explain "CapabilityOrchestrator"` | capability-children → slots overlay |
| Registry | `src/hal0/registry/store.py:77` `ModelRegistry` | `explain "ModelRegistry"` | model-id → upstream bindings (TOML, mtime-cached); `_notify_change` (`:287`) auto-regens lemonade catalog |

### Request flow (the hot path)
- HTTP entry: `src/hal0/api/routes/v1.py` — chat/completions at `:687`, dispatch call
  at `:463` (`dispatcher.dispatch(request, body=body)`). OmniRouter consulted via
  `request.app.state.omni_router` (`v1.py:873`, optional — see backlog).
- Dispatcher ladder: `dispatcher/router.py:327`+ (documented at `:8–24`). Tier-4 legacy
  fallback `proxy.resolve_slot` invoked at `router.py:549`.
- Model remap (slot-name → real model): `dispatcher/router.py:1114` `_remap_model`.
- Lemond gateway redirect: `dispatcher/router.py:122–139` `_resolve_target_url`
  (synthetic `hal0` upstream → `LEMONADE_BASE_URL + /v1`, default `:13305`).
- Slot views/aliases consumed by the routes: `api/__init__.py:313` `hal0_slot_alias_models`,
  `:398` `hal0_chat_slot_alias_map`, `:433` `hal0_llm_slot_views` (A1 wants these
  extracted to a slots-owned `slot_model_views()` seam).

---

## Subsystem index

### API surface
- Router registration table: `api/__init__.py:1195–1463`. `explain "create_app"`.
- Error envelope (single handler, all routes): `api/middleware/error_codes.py:74`.
  Typed error hierarchy: `errors.py:38` `Hal0Error`.
- Catch-all lemond proxy (un-covered `/v1/*`): `routes/lemonade_proxy.py:318`,
  `_forward` at `:250`.
- Async jobs: updater (durable, disk-mirrored) `routes/updater.py:114`; model-pull
  (process-local, NOT mirrored) `routes/models.py:1230`.

### Auth (THREE paths — see backlog #2)
- Global gate: `src/hal0/api/deps.py` `require_token`/`require_writer`.
- Agents chat-WS HMAC + origin allowlist: `src/hal0/api/agents/_auth.py` (allowlist at
  `:61` — **ships `thinmint.dev`**).
- MCP bearer resolver: `src/hal0/api/mcp_mount.py:46,134`.

### Slots & runtime
- Manager: `slots/manager.py:182`. FSM `_transition` `:301`, spawn `_spawn_locked`
  `:1215`, routing `route_for_request` `:1064`, NPU exclusivity `_check_npu_exclusivity`
  `:1617`, idle loop `_idle_monitor_loop` `:1864`.
- Capacity (untested): `slots/capacity.py:1`. TTFT (untested): `slots/ttft_samples.py:1`.
- systemd unit renderer (orphan? see QUESTIONS): `slots/unit_template.py` — renders
  `hal0-slot@<name>.service.d/override.conf`. Base template unit removed in PR-9
  (`installer/install.sh:655`).
- Self-managed (non-lemond) providers set: `slots/state.py:124`
  `SELF_MANAGED_PROVIDERS = {kokoro, moonshine, vibevoice}`.

### Providers
- ABC + impls: `providers/base.py:104`; dict populated at `providers/__init__.py:47`
  (llama-server/moonshine/kokoro/comfyui). Hot path pulls only
  `lemonade_provider()` (`providers/__init__.py:69`); others reachable via
  `get_provider` (`v1.py:1026`, `unit_template.py:102`) + flm (`capacity.py:180`).
  Dead-code status: see QUESTIONS.

### Lemonade / FLM-NPU
- Admin client: `lemonade/client.py:117`. Catalog gen: `lemonade/server_models_gen.py`
  (triggered by `registry/store.py:287` on mutation).
- FLM trio (the triangle): `dispatcher/flm_trio.py:99` `FLMTrioRouter` (owns own
  `LemonadeClient` at `:127`), gate `v1._is_npu_trio_request`, side-effects
  `capabilities/orchestrator.py:682` `_apply_npu_trio_modality`.

### Capabilities / config
- Orchestrator: `capabilities/orchestrator.py:192`; reaches into lemond `flm_args`
  internals at `:47` (leak — A1 wants `LemonadeClient.set_flm_modality()`).
- FHS path resolver (single source): `config/paths.py`. `HAL0_HOME` = test/dev override.
- Atomic TOML writes: `config/loader.write_toml_atomic`. Both orchestrator AND manager
  write slot TOML → drift hazard (memory `hal0_orchestrator_drift_bug`).
- Pydantic schema: `config/schema.py:1` (1475 lines).
- FeatureFlags stubs (raise NotImplementedError): `config/features.py:34,48,56`.

### Events / journal (parallel ring buffers — see backlog)
- EventBus (SSE fan-out, untested): `events/__init__.py:74`.
- Lemond log ring: `journal/__init__.py`. Lemond log SSE source: `client.py:415`
  `stream_logs` (re-exposed at `/api/logs`, `/api/journal`, nuclear-evict banner).

### Memory (DARK by default — see backlog #1)
- Gate: `api/__init__.py:1486` `HAL0_MEMORY_ENABLED` (default 0).
- Cognee wrapper: `memory/cognee_wrapper.py:1` (1481 lines). No-op key sentinel at `:114`.
- pgvector degrade stub (drops writes on restart): `memory/pgvector_provider.py:1`,
  wired as fallback at `memory/__init__.py:89`.
- MCP memory dispatch adapter (untested): `dispatcher/memory_dispatcher.py:1`.

### Install / update
- Bootstrap (trust boundary): `installer/bootstrap.sh`. Setup: `installer/install.sh`
  (layout resolution `:167–193`, lemonade bootstrap `:1068–1158`, service start
  `:1589–1681`).
- Updater: `updater/updater.py:843` `Updater`, `apply()` `:935`,
  `_is_editable_install()` `:781` (the editable-no-op trap — see backlog).
- Preflight (also `hal0 doctor`): `installer/lib/preflight.sh`.
- Release-manifest version read (the LIVE one): `updater/updater.py:899` reads
  `version` from the **fetched** GH manifest — NOT the static repo `manifest.json`.

### CLI (entire surface untested — see backlog)
- `cli/update_commands.py:155`, `slot_commands.py`, `model_commands.py`,
  `agent_commands.py`, `config_commands.py`, `migrate_commands.py`,
  `registry_commands.py`, `capabilities_commands.py`.

### Agents / Hermes
- Provision monolith (3393 lines, 9 phases): `agents/hermes_provision.py:1`.
  dashboard_url fallback (**ships `hal0.thinmint.dev`**) at `:2023`.
- Jinja templates (**ship `thinmint.dev`**): `agents/hermes_templates/HERMES.md.j2:40`,
  `AGENTS.md.j2:10`.
- Manager + bundled agents: `agents/manager.py:88` (`pi_coder` still in `BUNDLED_AGENTS`,
  not picker-visible).
- API service layer (confusingly named `api/agents/`, not routes): `_auth.py`,
  `budget.py`, `chat_proxy.py`, `memory_stats.py`, `personas.py`, `restart.py`.

---

## Oversized files (start here if splitting)

| File | Lines | Natural seam |
|---|---|---|
| `agents/hermes_provision.py` | 3393 | split by phase (install/config/mcp) |
| `slots/manager.py` | 2221 | extract `SlotStateMachine` (`:301–564`) + idle loop |
| `api/routes/models.py` | 1684 | registry / pull / HF-search / scan |
| `api/routes/slots.py` | 1662 | route handlers + 7 metric helpers → `slots/metrics.py` |
| `api/__init__.py` | 1596 | app factory vs router registration vs lifespan |
| `config/schema.py` | 1475 | acceptable (schema module) |
| `api/routes/v1.py` | 1273 | routing + dispatch |
| `updater/updater.py` | 1215 | download logic could split |
| `dispatcher/router.py` | 1172 | the Dispatcher hub |
