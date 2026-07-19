# W1 — God-Node Coupling Analysis

**Worker:** w1 (god-node coupling)
**Graph snapshot:** `graphify-out/graph.json` @ commit `270a35ae` (26,181 nodes / 49,958 edges; 88% EXTRACTED, 12% INFERRED)
**Source:** BRIEF.md + `graphify query`/`explain`/`path` + GRAPH_REPORT.md community blocks + `spec-p3-slots.final.md`

## Headline

The graph's four "god nodes" — `SlotManager` (277), `ContainerProvider` (113), `SlotState` (105), `ApprovalQueue` (105) — collapse into three structurally distinct failure modes:

1. **True god class** — `SlotManager` (4,146→3,071 LOC, ~90 methods, 8 responsibilities). Graph fully vindicates the P3-slots decomposition.
2. **Core abstraction hub** — `SlotState`, `ContainerProvider`. Coupling is *legitimate* but creates lock-order / version-coupling risk.
3. **Coordination singleton** — `ApprovalQueue`. Coupled to MC→brain→audit→admin fan-out; risk is process-level serialisation, not module size.

## Findings (ranked by importance)

### F1. `SlotManager` is the only true god class; decomposition is fully justified

- **Degree 277** (next: ENDPOINTS 136). Lives in **Community 2** (113 nodes, cohesion **0.03** — near-random). Sits in the `slots/` package and has EXTRACTED references to **9 distinct modules** (`lifespan`, alias models, chat-slot map, llm slot views, composite cache, prime cache, model ids, seed multiplex) plus heavy INFERRED edges to the slots/* internal helpers (`_transition`, `_resolve_alias`, `_current_state`, `_load_slot_config`, etc.).
- **BFS-2 fans out to 864 nodes** including `create_app`, `UpstreamRegistry`, `Dispatcher`, `FLMProvider`, `GpuArbiter`, `SlotIdentityStore`, `HardwareStats`, `SlotConfig`, `EventBus`, `PortAuthority`, `container_enrichment`, `LoadedSlot`, `apply_setup` — i.e. every layer of hal0 from routes → config → hardware → providers → events. A change to SlotManager ripples to all of them.
- **Source confirms:** `src/hal0/slots/manager.py` = **3,071 LOC**; spec cites 4,146 (as-built) → 2,769 after rework. Eight method groups identified in `spec-p3-slots.final.md §1`: (a) core state-machine ~1,900 LOC, (b) idle/eviction → `reaper.py` ~260, (b′) fail-watchdog → `watchdog.py` ~340, (c) drift comparator ~115, (d) NPU-trio reconciler ~170, (e) model-fallback heuristics ~330, (f) config-write guard ~360, (g) profile-adopt ~140, (i) seeded routing ~250.
- **Tests around it:** `tests/slots/test_manager.py` (Community 2 itself), `tests/dispatcher/test_serving_integration.py`, `test_arbiter_dispatch.py`, `tests/slot_view/test_aggregator.py`, `tests/slots/test_pulling_serving_idle.py`, `test_model_fallback.py`, `test_slot_aliases.py`, `tests/capabilities/test_orchestrator_reconciliation.py`. 35 test files in `tests/slots/` per spec §8.
- **Cross-community leak:** `SlotManager` itself appears as a node in **at least 7 other communities** (3 SlotState, 8 SlotConfigError, 49 CapabilityOrchestrator, 115 NPU-swap, 161 GpuArbiter, 207 model-fallback, 551 build_per_slot). This is the canonical "leaky god" fingerprint: the type is so central that downstream modules declare it as a node even when their edge weight to it is small.

### F2. `SlotState` is a *legitimate* hub, not a god — but it is the shared-mutable lock surface

- **Degree 105** (Community 3, 76 nodes, cohesion 0.03). All edges from `SlotManager`, `Dispatcher`, `GpuArbiter`, `StackApplyEngine`, `UpstreamCall`, `FakeUpstreamRegistry`, `Slot`, `FakeModelRegistry`, `_RecordingSlotManager`, `UpstreamUnavailable`, `_ArbiterSlotManager`, `NoRouteFound`, `FakeSnap`, `FakeContainerProvider`, `SlotLoading`, `SlotReaper`, `FakeSlotManager` (graph shows 85+ more, all INFERRED "uses" from `state.py:51`).
- **Critical path detail:** `path SlotManager → ApprovalQueue` is **7 hops** (`SlotManager → get_slot_manager() → _state() → get_dispatcher() → Dispatcher → _run_chat() → create_app() → ApprovalQueue`). The shared intermediate `_state()` is the function in `api/__init__.py` that calls `dispatcher.state()` — it touches both SlotState (slots) and Dispatcher (routing) on every request. Any future refactor of SlotState.Record contract ripples into dispatch state, audit, and approval flow simultaneously.
- **Risk:** not size, but **lock contention**. `SlotManager._transition` is the single mutation choke-point, and SlotState.Record is read by ~20 modules on every request. The graph can't see Python locks but shows the *call-graph fan-in* that serialises on it.

### F3. `ContainerProvider` is the real "god" of the providers/ subsystem

- **Degree 113** (Community 16, 69 nodes, cohesion 0.03). Source: `src/hal0/providers/container.py:1222`. 1,967 LOC. Mixed edges: real provider polymorphism (`FLMProvider`, `KokoroProvider`, `Qwen3TTSProvider`, `Provider` base), runtime artefacts (`Mount`, `RuntimeLaunchPlan`, `HealthCheck`), and test scaffolding (`TestRenderUnit`, `TestRenderUnitFromSpec`, `TestContainerSpec`, `TestLoadSync`, `TestContextSizeDerive`).
- **Key inferred edge:** `ContainerProvider → FLMProvider` (INFERRED) shows the class *uses* the FLM provider, not inherits it — meaning there is dispatch code inside ContainerProvider that branches on backend type. The container provider is acting as both **launcher** and **dispatcher** for the podman path.
- **No rework spec found** that decomposes `providers/container.py`. The graph shows it has the same 8-responsibility fingerprint as SlotManager (spec template), but no spec node addresses it. **Gap.** Recommended extraction: split into `container/launch.py` (spawn/terminate/health probe) and `container/spec_render.py` (quadlet text render + profile flag resolution), keep ContainerProvider as thin facade. See `_resolve_llama_scalars` in Community 6 (119 nodes, cohesion 0.03) — that subtree already half-extracted; align the rest.

### F4. `ApprovalQueue` is a coordination singleton, not a god

- **Degree 105** (Community 166, smaller cohesion). Source: `src/hal0/mcp/approval_queue.py:155`, **368 LOC**. Live edges to `mcp/admin.py`, `agents/personas.py` (Persona, PersonaApproval), `audit` (record_action, AuditStore), `board/` (`HermesKanbanClient`, `test_board_chat_*`), `brain/` (`test_brain_injection`, `test_brain_resilience`, `test_brain_framing`, `test_brain_read_only`).
- **Test surface:** `tests/mcp/test_approval_queue.py` + 7 brain/board test files gate destructive calls through ApprovalQueue. The queue is **the** serialisation point for gated side-effects across the whole agent runtime — high fan-out is *correct* here.
- **Risk:** if ApprovalQueue ever needs to scale out (multiple daemons), the in-process FIFO becomes a bottleneck. The graph shows no current sharding seam. **Not a decomposition target**, but worth flagging for the agent-fleet scale lane.

### F5. Rework spec node `P3-slots: Decomposition Spec for slots/manager.py` is fully validated by the graph

- Cross-reference: spec at `docs/rework/hal0-specs/spec-p3-slots.final.md` calls for **8 responsibilities extracted** from `slots/manager.py`. The graph's Community 2 (113 nodes) shows the same fingerprint: one class with dependents across providers, dispatch, routing, UI (SlotManagerDep / useSlots), config, audit, and capability layers.
- **Spec target ~2,050 LOC** vs **current 3,071 LOC** — the P3-slots rework is **partially complete** (commit `dbc2c771` cited in REWORK_BOARD row P3-slots went 4,145→2,769, then 2,769→3,071 may indicate refactor regression or new tests added). The graph shows residual coupling because the extraction left delegators behind (per spec §5 — public names must survive). This is **expected, not a leak**, but the high edge count (277) confirms there is more low-hanging extraction available (especially (e) model-fallback heuristics and (i) seeded routing).

### F6. No import cycles; coupling is structural not cyclical

- `## Import Cycles — None detected.` (GRAPH_REPORT.md:1171). All coupling is *fan-in/out*, never mutual. Good — means every decomposition stays safe under the existing import graph.

### F7. God-node ranking diverges from community size — class size ≠ coupling

- `SlotManager` (277 edges, 113-node community) vs `ENDPOINTS` (136 edges, UI hub) vs `BoardStore` (114, Community 5, 83 nodes) vs `connect()` (114, Community 13, 67 nodes) vs `ContainerProvider` (113, Community 16, 69 nodes). The graph's top-10 mixes (a) state machine, (b) UI constants, (c) DB connection, (d) backend abstraction, (e) route registration. **Coupling is role-specific; do not bundle them into one "god fix" effort.** SlotManager alone dwarfs every other node by ~2× edges.

## Risks / Smells

- **R1 (SlotManager):** 277 edges, cohesion 0.03. ~33% extraction still pending vs spec target. Each remaining method cluster blocks independent testability of downstream modules.
- **R2 (SlotState):** call-graph shared-mutable choke-point. Lock contention risk invisible to graph but inferred from fan-in.
- **R3 (ContainerProvider):** no rework spec covers it; identical 8-responsibility fingerprint to SlotManager. **Decomposition debt.**
- **R4 (ApprovalQueue):** in-process FIFO serialises all gated agent actions. Will bottleneck at fleet scale; no sharding seam.
- **R5 (cross-community leakage):** `SlotManager` and `SlotState` appear as nodes in 7+ other communities each, indicating downstream modules are tightly bound even where their *direct* edge weight is low. Hard to refactor in isolation.
- **R6 (test fan-out asymmetry):** SlotManager has ~8 dedicated test files + indirect coverage in 5+ other suites (slot_view, dispatcher, capabilities, stacks). ContainerProvider has only 1 (`tests/providers/test_container.py`) for a 1,967-LOC class with 113 edges. **Test density ratio is ~1:5 inverse to coupling.**

## Recommendations

1. **Continue P3-slots extraction in priority order** (per spec §4, least-coupled first): (c) drift comparator → (d) npu trio → (g) profile-adopt → (i) seeded routing → (b) reaper → (b′) watchdog → (e) model-fallback → (f) config-write. Each cut should drop 30-60 edges from SlotManager and split one test file cleanly.
2. **Add a `P3-providers` spec** mirroring `P3-slots` for `providers/container.py`. Target split: `launch.py` (spawn/terminate/probe) + `spec_render.py` (quadlet text + flag resolution), keep ContainerProvider as facade. The graph's `_resolve_llama_scalars` (Community 6) subtree is the natural first extraction since it already self-contains.
3. **Bump ApprovalQueue test density before any future sharding work** — currently 1 direct test file (Community 166) for 105 edges. Add at minimum: load-shedding test, multi-process race test, FIFO-order invariant test.
4. **Document `_state()` in `api/__init__.py` as the shared `_transition → dispatch → audit → approval` fan-in point** (it sits on the 7-hop SlotManager→ApprovalQueue shortest path). Any change to its signature ripples across 4 subsystems; flag in the rework board.
5. **Add a coupling-budget gate to CI** — fail PR if any single node gains >5 net edges after the change. The graph makes this cheap (`graphify query` + diff vs `graph.json`). Cheap insurance against new god-nodes growing.
6. **Mark `_resolve_model_info` (manager.py:3400) as a shared seam with ML-store lane** in the rework board (spec §7 already calls this out). Graph shows `_resolve_model_info` is the bridge between SlotManager ↔ registry; coordinate with `spec-ml-store.final.md` to avoid double-decision.
