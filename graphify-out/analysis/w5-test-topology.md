# w5 — Test Topology

Slice of the hal0 graphify knowledge graph. Goal: map what the test subgraph
reveals about coverage shape — heavily-tested subsystems, src god nodes that
lack a paired test community, and the test-only fake/stub/mock ecosystem.

All claims cite community numbers, file:line, edge counts, or node degrees
extracted from `graphify-out/graph.json` (26,181 nodes / 49,958 edges, built
from commit 270a35ae) and the per-community headers in
`graphify-out/GRAPH_REPORT.md` (1013 communities).

## Headline numbers

- **1013** communities total (graphify-out/GRAPH_REPORT.md L1173 onwards).
- **~50** communities whose name is a `test_*.py` (pytest files).
- **~40** communities whose name is a `Fake*` / `Stub*` / `Mock*` / `_Recording*`
  / `TestClient` / `_Fake*` / `_Stub*` test double.
- **~70** communities named after a `*.spec.ts` Playwright e2e spec (UI side).
- The test/fake/spec communities are **NOT clustered together** — the
  community detector groups them by what they exercise, so `test_board_chat.py`
  sits inside the `BoardStore` cluster (community 54) and `test_updater.py`
  inside the updater cluster (community 15). This means **community membership
  itself encodes the test↔src pairing**, and a coverage gap shows up as a
  god/hub node with no `test_*` neighbor in the same community.

## Findings

### (a) Heavily-tested subsystems

| Subsystem | Test communities | Evidence |
|---|---|---|
| **Updater** | 4+ dedicated tests + idempotency | `test_updater.py` (15), `test_updater_routes.py` (66), `test_hermes_provision.py` (60), `test_hermes_provision_idempotency.py` (169), `_hermes_fakes.py` (701) for installs. `apply_update`/`prepare_update`/`commit_update`/`rollback` paths each have multiple `test_apply_*` cases (community 15 nodes at L397/413/448/483/523/622/752/787/834). |
| **Probe / hardware detection** | Mirrors src 1:1 | `test_probe.py` (24) sits in the same community as `probe.py` (20) → exact 1:1 pairing. `HardwareInfo` (26) co-located. |
| **Slots** | 10+ test files across slots/ | `test_manager.py` (community 2, same as `SlotManager` src), `test_gpu_arbiter.py` (29, same as GpuArbiter), `test_config_drift_aliases.py`, `test_fail_watcher.py`, `test_fail_watcher_warming.py`, `test_pressure_eviction.py`, `test_pulling_serving_idle.py`, `test_slots_image_pull.py` (172), `test_unit_files.py` (179), `test_npu_swap_status.py` (115). Almost every community in the slots subtree has a paired test. |
| **Dispatcher / router** | 5 test files | `test_router.py` (28), `test_capability_wake_on_evict.py`, `test_arbiter_dispatch.py` (78), `test_serving_integration.py` (58), `test_npu_swap_status.py` (115), `test_rerank_path_routing.py` (307). All share communities with their src. |
| **Board / board_chat** | 4+ test files | `test_board_chat.py` (54, ~150 lines of `_make_app`, `_Recorder`, `_StubLLM`), `test_board_chat_tool_use_e2e.py` (131, `_RestRecorder` + `_StubLLM`), `test_board_chat_admin_tools.py` (289), `test_board_store.py` (5, same community as `BoardStore`), `test_board_routes.py` (290), `test_hermes_executor.py` (271), `test_brain_injection.py` (257), `test_brain_self_auth.py` (381). |
| **Memory** | 6+ dedicated tests | `test_hindsight_provider.py` (184), `test_memory_graph_route.py` (205, has `StubWrapper` + `_RetryWrapper` + `_HindsightyWrapper`), `test_memory_recall_tool.py`, `test_recall_route.py`, `test_provider_contract.py` (all using `FakeMemoryProvider` community 156), `test_chat_proxy_auth.py` (187), `test_notes.py` (158), `test_agent_uninstall_memory.py` (175), `test_memory_hindsight_plugin.py` (14), `test_subgraph_ego_returns_connected_slice` at L186 (329), `test_agent_memory_stats_endpoint.py` (398, has `_FakeWrapper`). |
| **Container provider / runtime plan** | Mirrored | `test_container.py` (17) sits in the same community as `resolve_profile_flags()` (also 17) and shares with `ContainerProvider` (16) via edges. `test_runtime_launch_plan.py` (88). 16+ `test_*` methods enumerated (L245/L332/L349/L367/L385/L403/L421/L470/L497/L517/L596/L611/L734/L929/L1069/L1079). |
| **Models / registry** | 4 tests | `test_models_crud.py` (65), `test_models_routes.py` (97), `test_model_store.py` (185), `test_migrate_model_layout.py` (27), `test_pull_routes.py` (103), `test_registry_import.py` (74). `SqliteModelRegistry` (21) gets hammered. |
| **Profiles** | 3 tests | `test_profiles_crud.py` (117), `test_profile_derive.py` (102), plus embedded tests in `test_container.py`. |
| **Auth / KB-1** | 4 tests | `test_auth_core.py` (119), `test_chat_proxy_auth.py` (187), `test_openrouter_auth_loopback.py` (202), `test_kb1_hardening_tail.py` (167). Cluster 687 is the spec for KB-1. |
| **MCP / agents** | 5 tests | `test_mcp_routes.py` (69), `test_plugin_manifest_proxy.py` (30), `test_hermes_wrapper.py` (114), `test_admin.py` (56), `test_personas.py` (170). |
| **ComfyUI proxy** | 1 focused test | `test_comfyui_proxy.py` (129) for routes; provider-side behavior is covered indirectly. |
| **UI e2e** | ~70 spec files | Spread across communities 120 (auth/setup), 150 (memory/board/login), 180 (board CRUD), 453 (footer/inference/activity-log), 475 (slot-drawer/profile), 745 (board-chat-v3 + wsHarness), 851 (board-chat-tool-use-v3), 985/986/987/1027 (v3 slot specs), 1057 (npu-occupancy-v3), 984 (comfyui-arbiter-v3), 849 (dashboard-redesign-v3), 814 (footer-update-chip/models-catalog-controls), 896 (slot-create-default-v3). Fixtures in `apiMock.ts` (180), `mock-data.ts` (180/475), `wsHarness.ts` (745), `sseHarness.ts` (453), `mock.ts` (199). |

### (b) Coverage gaps — god/hub src nodes that LACK a matching test community

These are nodes that appear in the top god/hub ranking (high degree = many
callers) but whose community does NOT contain a `test_*` node neighbor.

| God / hub node | Degree | Community | Test neighbor? | Gap |
|---|---|---|---|---|
| **`FLMProvider`** (src/hal0/providers/flm.py L335) | high hub, community 18 | 18 | None named `test_flm*.py` | Provider unit tests absent; only `test_container.py` (17) and `test_runtime_launch_plan.py` (88) cover container-shaped outputs. |
| **`StackApplyEngine`** (src/hal0/stacks/apply.py L118) | hub, community 35 | 35 | None named `test_stacks*.py` | Stack apply logic appears untested at the unit level. The community contains only `RecordingOrchestrator` and `FakeSnap` from `tests/stacks/conftest.py` (105) but no `test_*` file in community 35 itself. |
| **`v1.py`** routes (src/hal0/api/routes/v1.py L1, deg 42) | 42 | 38 | None named `test_v1*.py` | No dedicated `test_v1_routes.py`. Coverage comes transitively from `test_board_chat.py` (54), `test_chat_normalization.py` (226), `test_v1_npu_trio_routing.py` (197) — but `images_generations`, `audio_speech`, `audio_transcriptions`, `embeddings`, `_seed_tts_defaults`, `_tts_slot_config`, `_dispatch_via_npu_trio`, `_normalize_chat_body`, `_rewrite_chat_slot_alias`, `_forward_multipart` are not directly named in a test community. |
| **`ENDPOINTS`** (ui/src/api/endpoints.ts L8, deg 136) | 136 | 0 | None | Frontend constants file; no UI test directly covers it (covered transitively by the 70+ spec files that import it). Low priority since it's a string table, but worth noting. |
| **`connect()`** (src/hal0/db/connection.py L78, deg 114) | 114 | 13 | None named | DB connection helper exercised by 94+ callers via INFERRED edges but no focused test community. Implicit testing through DB-using tests. |
| **`UpstreamRegistry`** (src/hal0/upstreams/registry.py L239) | hub, community 45 | 45 | None | No dedicated `test_upstreams*.py`. `FakeUpstreamRegistry` exists in 28, 307, 523, 802 but the real class is community 45-orphan. |
| **`AgentManager`** (src/hal0/agents/manager.py, community 43) | hub | 43 | Indirect only | `_FakeAgentManager` (678), `FakeDelegateRunner` (499), `_fake_bundled_agent_manager` (978), `_delegate_fakes.py` (1022) — but no `test_agent_manager.py`. Manager flows are exercised through `test_hermes_provision*` and `test_agents_*`. |
| **`MemoryConfig`** (src/hal0/config related, community 203) | hub | 203 | None | No test community named `test_memory_config*.py`. Covered transitively. |
| **`ComfyUIProvider`** (src/hal0/providers/comfyui.py, community 41) | hub | 41 | `test_comfyui_proxy.py` (129) covers routes only | Provider logic not unit-tested — only the proxy route is exercised via e2e (comfyui-arbiter-v3.spec.ts at 984 and imagegen-v2.spec.ts at 985). |
| **`cli.py`** (src/hal0/cli/cli.py L1, community 34) | hub | 34 | `test_agent_shim.py` (42), `test_registry_import.py` (74) — partial | CLI surface large; only fragments tested. |
| **`AgentMCPClient`** (src/hal0/agents/..., community 48) | hub | 48 | `test_mcp_routes.py` (69) only | MCP client class itself lacks direct unit coverage. |
| **`SlotIdentityStore`** (src/hal0/slots/..., community 186) | moderate | 186 | None | Co-located with `test_model_store.py` (185) and other tests, but no `test_slot_identity*.py`. |
| **`chat.py`** (src/hal0/brain/chat.py, community 46) | hub | 46 | `test_brain_injection.py` (257), `test_brain_self_auth.py` (381), `test_hermes_executor.py` (271), `test_board_chat*` | Reasonable coverage via brain/* tests but no `test_chat.py` directly in community 46. |
| **`MigrateState`** (src/hal0/config/migrations, community 67) | moderate | 67 | `test_migrate_model_layout.py` (27) only | Migration state machine is mostly implicit. |
| **`schema.py`** (src/hal0/config/schema.py L1, community 51, contains `Hal0Config` deg huge) | hub | 51 | `test_container.py` (17), `test_profile_derive.py` (102), `test_unit_files.py` (179) exercise fragments | Schema-level tests thin; many sub-models (`Hal0Config`, `BrainChatConfig`) lack direct contracts. |
| **`probe.py`** vs **`HardwareInfo`** | 40 + 30 | 20 / 26 | `test_probe.py` (24) covers probe but community 26 (HardwareInfo) has no dedicated test | `HardwareInfo` shape tests absent. |
| **`Dispatcher`** (community 28) | hub | 28 | `test_router.py` (28) co-located ✓ | OK — slot 28 has its test. |

**Summary of coverage gaps**: the slots/dispatcher/updater/memory/board_chat
"hot core" is well-paired (test community shares the src community). The
gap zones are: `v1.py` routes (no direct unit tests), `FLMProvider` /
`ComfyUIProvider` provider logic, `StackApplyEngine`, `UpstreamRegistry`,
`MemoryConfig`, the `schema.py` config surface, and the `connect()` DB helper.

### (c) Test-only fakes / stubs — what they mock

Identified by community name starting with `Fake` / `Stub` / `Mock` /
`TestClient` / `_Recording` / `_Fake` / `_Stub`. Many of these are
purpose-built for one test file.

| Community | Name | File:line | Mocks |
|---|---|---|---|
| 29 | **FakeManager** | tests/slots/test_gpu_arbiter.py L87 | `GpuArbiter` — drives `GpuImageMode`/`GpuInferenceMode`/`ArbiterPinned` for 18+ `test_*` arbiter scenarios (L322/L397/L448 etc). Degree 50 → hub of GPU arbiter tests. |
| 55 | **StubWrapper** | (matched community) | Wraps a client contract; details above (community 55). |
| 58 | `_RecordingSlotManager` | tests/dispatcher/test_serving_integration.py L39 | `SlotManager` — records calls so `test_serving_integration` can assert slot dispatch behavior end-to-end. |
| 70 | `_FakeWrapper` | tests/agents/test_agent_memory_stats_endpoint.py L31 | Memory wrapper fake returning canned `list_items` payloads; covers stats endpoint. |
| 78 | `_ArbiterSlotManager` | tests/dispatcher/test_arbiter_dispatch.py L52 | `SlotManager` specialized for arbiter-dispatch tests (community 78). |
| 90 | **FakeWsServer** | (ui/test ws fixture) | In-process WS server for UI e2e streaming tests. |
| 107 | **FakeContainerProvider** | tests/golden_paths/conftest.py L49 | `ContainerProvider` for golden-path integration tests. |
| 113 | **FakeSlotManager** | tests/omni_router/conftest.py L25 | `SlotManager` minimal stand-in for omni_router dispatch tests (used by `make_slot` fixture). |
| 156 | **FakeMemoryProvider** | tests/memory/fakes.py L23 | `MemoryProvider` contract — implements `add/list_items/search/delete/graph_status/set_graph_enabled/set_rerank_enabled`. Consumed by `test_provider_contract.py`, `test_recall_route.py`, `test_memory_recall_tool.py`. |
| 171 / 216 / 345 / 400 / 771 | **TestClient** (FastAPI) | multiple | FastAPI test client wrappers per suite. 5 separate communities suggests distinct test-app bootstraps. |
| 180 | **apiMock.ts** | ui/tests/e2e/fixtures/apiMock.ts L1 | Frontend API mock hub. Joined with `mock-data.ts` (180) for board fixtures — community 180 is the board-e2e center. |
| 199 | **mock.ts** | ui/tests/e2e/fixtures/mock.ts | General UI mock fixture. |
| 205 | **StubWrapper** (memory_graph_route) | tests/api/test_memory_graph_route.py L31 | Wraps `MemoryClient` returning canned `graph_status`; subclasses `_RetryWrapper` and `_HindsightyWrapper` to test retry/hindsight paths. |
| 228 / 492 / 885 | **FakeSlotManager** | multiple sites | Same name, distinct fixtures per test area (confirms a recurring test pattern across 4+ suites). |
| 307 | **FakeUpstreamRegistry** | tests/dispatcher/test_rerank_path_routing.py L33 | `UpstreamRegistry` for rerank/route tests. |
| 405 | **FakeContainerProvider** | tests/golden_paths/conftest.py L49 | Same name, second copy — golden-paths uses its own. |
| 499 | **FakeDelegateRunner** | (tests/agents) | Mock delegate runner for agent delegations. |
| 518 | **_FakeHttpClient** | (tests/agents) | HTTP client mock. |
| 523 | **FakeUpstreamRegistry** | tests/api/test_model_cache_refresh.py (community 523) | Second copy — model-cache-refresh uses its own. |
| 532 | **FakeLocalBackend** | (tests/) | Backend fake for local-only tests. |
| 556 | **_StubSlotManager** | (tests/) | Stub variant of SlotManager (vs full Fake). |
| 586 | **FakeDockerBackend** | (tests/) | Docker backend fake. |
| 678 | **_FakeAgentManager** | (tests/agents) | AgentManager fake. |
| 681 | **FakeBackendResult** | (tests/) | Canned backend result. |
| 701 | **_hermes_fakes.py** | tests/agents/_hermes_fakes.py L1 | Hermes-specific fakes: `install_io()`, `fake_hermes_run()`, `sandbox_hermes_paths()`. Hub for hermes install/provision tests. |
| 758 | **_RecordingJob** | (tests/) | Records job lifecycle. |
| 770 | **_FakeSM** | (tests/) | Abbreviated SlotManager fake. |
| 802 | **FakeUpstreams** | (tests/) | Multi-upstream fake. |
| 886 | **_FakeResponse** | (tests/) | HTTP response fake. |
| 889 | **test_paths_stub.py** | tests/ | Path-stubbing test fixture. |
| 978 | **_fake_bundled_agent_manager** | (tests/) | Bundled-agent-manager fake. |
| 1022 | **_delegate_fakes.py** | (tests/) | Delegate-task fakes (paired with skill name "δ-harness: `delegate_task` coverage"). |

**Pattern**: `FakeSlotManager` appears as 4+ separate community roots (113,
228, 492, 885) — strong signal that the codebase has no shared
`tests/_fakes/slot_manager.py` and each suite reimplements it locally. Same
for `FakeContainerProvider` (107, 405) and `FakeUpstreamRegistry` (307,
523). This is duplication smell, not a coverage gap.

## Risks / Smells

1. **Duplicated fakes (≈5× `FakeSlotManager`, 2× `FakeContainerProvider`,
   2× `FakeUpstreamRegistry`)**: each test suite reimplements its own
   minimal stand-in. The `FakeManager` (community 29, deg 50) is the most
   connected fake — if its interface drifts from real `GpuArbiter`, 18+
   tests silently rot. A shared `tests/_fakes/` module would centralize
   the contract.

2. **`v1.py` route surface uncovered directly**: 42-edge community 38 has
   no `test_v1*.py`. All `images_generations`/`audio_*`/`embeddings`/
   `_normalize_chat_body`/`_dispatch_via_npu_trio` paths are exercised
   transitively via board_chat and chat_normalization tests — fine for
   happy paths, but a regression in a route-only behavior could slip
   through.

3. **Provider unit tests missing**: `FLMProvider` (18), `ComfyUIProvider`
   (41) — only their proxy routes are tested. Provider-level contracts
   (not just render output) are tested transitively through
   `test_container.py`.

4. **Schema surface thin**: `schema.py` (51) is enormous and contains
   `Hal0Config`, `ProfileConfig`, `BrainChatConfig` etc. Tests touch
   fragments via `test_container.py` / `test_profile_derive.py` /
   `test_unit_files.py` but no whole-config fixture tests (e.g.
   `test_hal0_config_load_defaults`).

5. **`UpstreamRegistry` (45) orphan**: hub node with no test community.
   `FakeUpstreamRegistry` exists in 28/307/523/802 but the real class is
   community-45-isolated.

6. **`probe.py` (20) vs `HardwareInfo` (26) split**: probe.py tests exist
   (24) but `HardwareInfo` shape is community 26 without a dedicated
   test community — likely tested transitively.

7. **UI test count dominates**: ~70 e2e specs vs ~50 pytest files. UI
   spec communities are organized by *feature area* (board 180,
   slot-drawer 475, memory 150, footer/inference 453) but multiple v3
   specs scatter across 814/849/851/896/985/986/987/1027/1057. Naming
   fragmentation risk.

## Recommendations

1. **Consolidate `FakeSlotManager` / `FakeContainerProvider` /
   `FakeUpstreamRegistry`** into `tests/_fakes/` (one module per real
   class). The 4+ duplicates are byte-level rewrites with the same
   intent. **Estimated payoff**: remove ~80-150 lines of duplication,
   give fake interface a single canonical contract so breakage surfaces
   in CI, not silently.

2. **Add `tests/api/test_v1_routes.py`** to community 38: at minimum
   cover `images_generations`, `audio_speech`, `audio_transcriptions`,
   `embeddings`, `_normalize_chat_body`, `_dispatch_via_npu_trio` with
   `_RecordingSlotManager` + `_FakeWrapper`. This fills the largest
   coverage gap on a high-degree hub.

3. **Add provider-level unit tests** for `FLMProvider` (18) and
   `ComfyUIProvider` (41) — currently the proxy routes are tested but
   the providers themselves only via `test_container.py` rendering
   assertions.

4. **Add `tests/stacks/test_stack_apply_engine.py`** in community 35.
   `StackApplyEngine` has zero `test_*` neighbors.

5. **Add a shared `tests/_fakes/upstream_registry.py`** — `FakeUpstreamRegistry`
   exists 2× (28, 523) and 802 has `FakeUpstreams`. Same intent, three
   files.

6. **Schema regression test**: one `tests/config/test_hal0_config_defaults.py`
   that round-trips a default `Hal0Config` to TOML and back. Catches
   `extra="forbid"` and field-deprecation regressions (per P3-schema
   spec, community 540).

7. **Track `_RecordingSlotManager` as a high-risk test double** (deg 8
   neighbors). It records slot-manager calls for serving-integration
   tests — if real `SlotManager` interface evolves, every test that
   uses it must be updated in lockstep.

8. **Consider splitting `test_board_chat.py`** — community 54 has
   `_make_app`, `_Recorder`, `_StubLLM`, `_tool_call_response`,
   `_final_response` co-located. If board_chat grows, this community
   becomes a god-test.