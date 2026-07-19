# Q3 — Why does `SlotManager` have high betweenness centrality (0.082)?

**Graph node:** `SlotManager` — `src/hal0/slots/manager.py:255` (class), **degree 277**, community 2 (113 nodes, cohesion 0.03), betweenness **0.082**.

## TL;DR

`SlotManager` is **the** domain bridge between the routing layer above and the resource layer below. `v1.py` (the public chat/images/audio/embeddings API) does not call `FLMProvider` or `GpuArbiter` or `compute_config_drift` or `EventBus` directly — every one of those is reached only *through* `SlotManager`. Conversely, `SlotManager` is the only place that *combines* config drift, GPU arbitration, container launch, event emission, identity, and runner selection. This is not a measurement artifact. It is the deliberate outcome of routing being kept thin and providers being kept stateless — the in-between object has to exist, and it has to be fat.

## Contrast with q1 / q2

| Node | Betweenness | Source of centralness | Verdict |
|---|---|---|---|
| `lifespan()` (q1) | 0.102 | FastAPI boot hook — sole construction site for every subsystem | Legitimate wiring hub |
| `create_app()` (q2) | 0.085 | `tests/conftest.py` fixture — every test reaches it via one bridge | Test-fixture artifact |
| `SlotManager` (q3) | 0.082 | **Architectural waist of the hourglass** | Real coupling, real refactor target |

q1 centralizes *construction* (one function builds everything). q2 centralizes *test access* (one fixture returns the app). q3 centralizes *coordination* (every domain operation routes through one state machine). Same graph statistic, three different causes, three different remediation paths.

## Findings (ranked)

### F1. Routing reaches every downstream resource *only via* SlotManager

Every `path` from `v1.py` to a leaf resource passes through `SlotManager` (or `SlotState`, which `SlotManager` owns and writes). All confirmed via `graphify path`:

| Source | Sink | Hops | Path (terminus into sink) |
|---|---|---|---|
| `v1.py` | `SlotManager` | 7 | `_dispatch_and_forward → Dispatcher → get_dispatcher → get_slot_manager → SlotManager` |
| `v1.py` | `compute_config_drift` | 5 | `_rewrite_chat_slot_alias → flm_id_to_tag → ._await_ready → _model_default → compute_config_drift` |
| `v1.py` | `EventBus` | 5 | `images_generations → get_curated → _auto_resume_interrupted_pulls ← lifespan → EventBus` |
| `v1.py` | `CapabilityOrchestrator` | 4 | `audio_speech → BadRequest → ._validate_model_in_catalog ← CapabilityOrchestrator` |

Routing is a thin request-shuffler; the moment a request touches slots, it must pass through the manager. The two apparent exceptions (`FLMProvider` reachable in 4 hops via `_rewrite_chat_slot_alias`) are tag-rewrite helpers at the **top of the request stack** — by the time the request hits a slot/provider, it has already been routed through the manager.

### F2. SlotManager is the only thing that composes the resource layer

Same pattern from the other direction: downstream resources do not coordinate with each other. They coordinate with `SlotManager` (or the `SlotState` it owns). Confirmed INFERRED edges from `graphify explain`:

- `SlotManager → GpuArbiter` (uses): every arbiter dispatch passes through the manager. `GpuArbiter → SlotState` is the only sibling-to-sibling edge; `SlotManager` is upstream of both.
- `SlotManager → FLMProvider` (uses): every `image_ref()`, `container_spec()`, `health()` call goes via `_spec_provider_for()` (`src/hal0/providers/container.py:946`), invoked from inside manager.py's `_load_slot_config` family.
- `SlotManager → SlotIdentityStore` (via `lifespan`): the manager is the sole consumer; nothing else queries identity.
- `SlotManager → EventBus` (via `_build_offline_deps`, manager.py internal): the manager is the only emitter of slot lifecycle events.
- `SlotManager → get_runner` (via `.create()` and `apply_preferred_runner`, manager.py:1736+): the manager selects runners; runners do not coordinate with providers.
- `SlotManager → compute_config_drift` (via `._await_ready()` → `container_provider()` → `compute_config_drift()` at `src/hal0/slots/drift.py:117`): the drift check is invoked only from inside the readiness loop.
- `SlotManager → heal_missing_llm_type` (via `._load_slot_config()` → `_flatten_slot_toml()` → `heal_missing_llm_type()` at `src/hal0/slots/_cfg_helpers.py:68`): slot-config normalization lives in the manager's load path.
- `SlotManager → SlotConfigError` (raises): 56 edges into the error type — the manager is the exception origin for nearly every slot-failure case.

### F3. SlotManager's centrality is *architectural*, not test-driven (contrast q2)

q2's `create_app()` had ~60 of 104 edges from `tests/conftest.py` fixture fan-in — a measurement artifact of one `app` fixture. `SlotManager`'s 277 edges are structurally different:

- `tests/slots/test_manager.py` sits in Community 2 *with* the manager (the slot-manager-and-its-tests cluster). It contributes direct method-call edges (`test_manager.py` exercises `.create()`, `._transition()`, `.load()`, `.delete()`) but those edges are *real* — they test the manager's contract, they don't fan out into unrelated communities the way `client` does.
- The test-double subtree (`FakeSlotManager`, `_RecordingSlotManager`, `_ArbiterSlotManager`, `_FakeSlotManager`) is 4 subtypes *all* in the SlotManager community. They count toward degree but not toward production coupling.
- Stripping the four fakes + `test_manager.py` + `conftest.py` leaves ~250 edges — still ~2× the next node (`ENDPOINTS`, 136). The architectural centrality is not the test fan-in.

### F4. SlotManager's centrality is *not* just a wiring hub (contrast q1)

q1's `lifespan()` is a single 540-line function that *constructs* everything (`app.state.X = ...`). It centralizes construction, not coordination. The 33 outbound edges from `lifespan` are 33 assignments.

`SlotManager` is the opposite: one constructor call in `lifespan` (`src/hal0/api/__init__.py:974`), but **~90 methods** that do real work. Its edges are *coordination* edges: every read/write of slot state, every transition, every alias resolution, every config load flows through one of its methods. The graph shows this in the `Slot` data class (manager.py:192, community 3, 76 nodes) which is the slot record every method returns. Every neighbor community reads `Slot` and writes via `SlotManager`. There is no "side door."

### F5. The hourglass shape, mapped

```
                          ╭─────────────────────────╮
                          │  v1.py (routes, 42 edges)│   ← top: thin, 8 method families
                          │  Dispatcher (routing)    │
                          │  RoutingHost (17 edges,  │       narrow seam: spec says
                          │   "narrow seam routing    │       "what routing needs from
                          │   needs from SlotManager")│       SlotManager"
                          ╰──────────┬──────────────╯
                                     │
                          ╌══════════╪══════════════╗
                          ║  SlotManager (WAIST)    ║   ← 277 edges, 90 methods,
                          ║  SlotState (lock surface)║     8 responsibilities
                          ║  SlotConfigError (typer) ║     (per spec-p3-slots §1)
                          ╚══════════╪══════════════╝
                                     │
   ┌─────────────┬─────────────┬─────┴─────┬──────────────┬──────────────┐
   ▼             ▼             ▼           ▼              ▼              ▼
 FLMProvider   GpuArbiter   EventBus   SlotIdentityStore get_runner  compute_config_drift
 ContainerProvider                                              heal_missing_llm_type
                                  ╰─────────────────────────╯
                                  ← bottom: providers/arbiter/config/events/identity/runners
```

**Routing above** is narrow on purpose — `RoutingHost` is explicitly documented as the seam `routing.py:215` "narrow seam routing needs from `:class:hal0.slots.manager.SlotManager`."

**Providers/arbiter/config/events below** are kept independently testable (they each have their own fakes: `FakeContainerProvider`, `FakeManager`, `FakeSnap`, `_ArbiterSlotManager`). They do not know about each other.

**The waist must exist.** It is the only object that can answer: "for this request, on this GPU, with this config drift, using this runner, emit this event, with this identity, dispatch to this provider." Remove it and routing has to learn about providers, providers have to learn about routing, and the system loses its seam.

### F6. Betweenness signature distinguishes waist-pattern from fixture-pattern

A "real" bridge has both high in-degree from above *and* high out-degree into diverse siblings below. `SlotManager` has exactly that:

- In-degree from above (routing/requests): v1.py routes via `_dispatch_and_forward` / `_dispatch_via_npu_trio` / `_rewrite_chat_slot_alias`; `Dispatcher.dispatch()` (community 28, 90 edges) reads `SlotManager.state()`; `CapabilityOrchestrator` (community 49, 54 edges) calls manager methods; `NpuTrioRouter` (community 106) and `OmniRouter` (community 377) consume it.
- Out-degree downward (resources): `GpuArbiter` (community 161), `FLMProvider` (community 18), `ContainerProvider` (community 16), `compute_config_drift` (community 133), `heal_missing_llm_type` (community 633), `SlotIdentityStore` (community 186), `get_runner` (community 63), `EventBus` (community 273), `RoutingHost` (community 162).
- Cross-community leakage: SlotManager itself appears as a node in **at least 7 other communities** (3 SlotState, 8 SlotConfigError, 49 CapabilityOrchestrator, 115 NPU-swap, 161 GpuArbiter, 207 model-fallback, 551 build_per_slot) — the canonical "leaky god" fingerprint per `w1-god-nodes.md` F1.

q2 (`create_app`) had in-degree dominated by one community (tests), low out-degree into siblings. That was fixture. SlotManager has in-degree from many communities and out-degree into many more. That is waist.

## Risks / Smells

- **R1 (waist bloat).** 3,071 LOC, ~90 methods, 8 distinct responsibilities extracted by P3-slots spec. Spec target ~2,050 LOC; ~33% extraction still pending per `w1-god-nodes.md` R1. Every remaining method cluster blocks independent testability of downstream modules.
- **R2 (lock contention at the waist).** `SlotManager._transition` is the single mutation choke-point; `SlotState.Record` is read by ~20 modules on every request. The graph can't see Python locks but shows the call-graph fan-in that serialises on it. The shared-mutable choke-point concentrates contention that no other place can absorb.
- **R3 (cross-community leakage).** `SlotManager` node appears in 7+ downstream communities. Any rename, signature change, or behavior change to its public surface (slots.manager:255 class definition) ripples to every one.
- **R4 (test density inverse to coupling).** Per `w1-god-nodes.md` R6: SlotManager has ~8 dedicated test files (`tests/slots/test_manager.py`, `test_pulling_serving_idle.py`, `test_model_fallback.py`, `test_slot_aliases.py`, `test_gpu_arbiter.py` via `FakeManager`, `tests/dispatcher/test_serving_integration.py`, `tests/slot_view/test_aggregator.py`, `tests/capabilities/test_orchestrator_reconciliation.py`). `ContainerProvider` has 1 (113 edges, 1,967 LOC). Test density ratio ~1:5 inverse to coupling — SlotManager's centralness is well-tested, but peers are not.
- **R5 (waist latency on the critical path).** Every request that hits a slot pays at least 5–7 hop shortest-path cost through the waist. The hot path is `v1.py → Dispatcher → get_slot_manager() → SlotManager.state()` (graph-confirmed 7 hops). Future request-rate work will pay this tax on every call.
- **R6 (RoutingHost stays narrow or the waist doubles).** `src/hal0/slots/routing.py:215` documents RoutingHost as "narrow seam routing needs from `:class:hal0.slots.manager.SlotManager`." That narrowness is the *only* firewall preventing routing from learning provider/arbiter/config internals directly. Any future addition of a RoutingHost method that calls a downstream resource without going through SlotManager would create a second waist.

## Recommendations

1. **Extract per `spec-p3-slots.final.md` §4 in the cited order** — (c) drift comparator → (d) npu trio → (g) profile-adopt → (i) seeded routing → (b) reaper → (b') watchdog → (e) model-fallback → (f) config-write. Each extraction drops ~30–60 edges from the manager and cleanly splits one test file. Target: bring `manager.py` under 2,100 LOC.
2. **Preserve RoutingHost as the *only* routing-side entry point.** Add a static check: `routing.py` may import from `hal0.slots.manager` only types defined in `hal0.slots.interface` (the public `SlotInterface`, `DesiredSlotState`). Anything else must go through SlotManager.
3. **Type-narrow `SlotState.Record` reads from outside `slots/`.** Today any module can read slot state directly (`SlotState` degree 105, 85+ INFERRED uses across communities). Replace external reads with `RoutingHost` / `SlotInterface` methods; the only writer remains `SlotManager._transition`. This collapses the lock surface (R2) without changing semantics.
4. **Document the waist contract.** One-page doc: "What `SlotManager` owns." List the 8 responsibilities from P3-slots §1, the public surface, and *what it does not own* (provider launch, arbiter dispatch, drift detect — those are delegated, even though every call leaves from here). Make non-ownership explicit so future contributors don't add responsibilities.
5. **Add a coupling-budget gate to CI** — same as `w1-god-nodes.md` R5: fail PR if any single node gains >5 net edges after the change. The graph makes this cheap (`graphify query` + diff vs `graph.json`). Cheap insurance against new waists growing.
6. **Mark `_resolve_model_info` (manager.py:3400) as a shared seam with the ML-store lane** per `w1-god-nodes.md` R7 — it is one of the few manager methods that bridges to the registry/store layer. Coordinate with `spec-ml-store.final.md` to avoid double-decision.
7. **Don't strip any edge after extraction without confirming no reroute.** Each P3 extraction must show (a) the manager's degree drops, (b) the new module's `SlotManager → new_module` edges ≤ a small budget, (c) all routing-layer `path v1.py X` shortest-paths lengthen by exactly the extraction hops. If (c) fails, the waist has been *moved*, not split — restart the extraction.

## Verdict

Betweenness = **0.082, architectural**. Neither a measurement artifact (q2) nor a wiring-script fan-in (q1). SlotManager is the waist of the hourglass: routing above stays thin because RoutingHost is a documented narrow seam; providers/arbiter/config/events below stay independent because they only know SlotManager. The seam between thin and fat *must* be fat. Refactor target: extract the 8 P3-slots responsibilities to shrink the waist, but keep the position — moving it (e.g. putting state in the Dispatcher, or routing in the manager) would create two waists and double the coupling tax.

## Evidence anchors

- `src/hal0/slots/manager.py:255` — `class SlotManager:` (graph node anchor)
- `src/hal0/slots/routing.py:215` — `class RoutingHost(Protocol)` with "narrow seam routing needs from `:class:hal0.slots.manager.SlotManager`" rationale_for
- `src/hal0/slots/state.py:51` — `class SlotState` (the shared-mutable lock surface, degree 105)
- `src/hal0/slots/state.py:206` — `class SlotConfigError` (the error type SlotManager raises, degree 56)
- `src/hal0/slots/drift.py:117` — `def compute_config_drift()` (drift comparator, community 133)
- `src/hal0/slots/_cfg_helpers.py:68` — `def heal_missing_llm_type()` (config-normalization, community 633)
- `src/hal0/slots/arbiter.py:237` — `class GpuArbiter` (degree 62, community 161)
- `src/hal0/providers/flm.py:335` — `class FLMProvider` (degree 70, community 18)
- `src/hal0/providers/container.py:1222` — `class ContainerProvider` (degree 113, community 16; spawned via `_spec_provider_for()` at line 946)
- `src/hal0/runners/__init__.py:186` — `def get_runner()` (degree 26, community 63)
- `src/hal0/events/__init__.py:96` — `class EventBus` (degree 26, community 273)
- `src/hal0/slots/identity.py:84` — `class SlotIdentityStore` (degree 62, community 186)
- `src/hal0/capabilities/orchestrator.py:165` — `class CapabilityOrchestrator` (degree 54, community 49)
- `src/hal0/api/routes/v1.py:1` — routes module (degree 42, community 38)
- `src/hal0/api/__init__.py:862` — `lifespan` (q1) constructs SlotManager at L974
- `tests/slots/test_manager.py:1`, `test_pulling_serving_idle.py:1`, `test_model_fallback.py:1`, `test_slot_aliases.py:1`, `tests/slots/test_gpu_arbiter.py:87` (`FakeManager`), `tests/capabilities/test_orchestrator_reconciliation.py:1` — six direct test files
- `graphify-out/analysis/w1-god-nodes.md` F1 (SlotManager 277 edges, 113-node community, 8-responsibility fingerprint), F5 (P3-slots spec validation), R1 (33% extraction pending), R6 (test density ratio 1:5)
- `graphify-out/analysis/q1-lifespan-bridge.md` — q1 contrast (wiring hub)
- `graphify-out/analysis/q2-create-app-bridge.md` — q2 contrast (test-fixture hub)
