# W3 — API Router Surface (hal0 backend)

Slice: HTTP API layer + the spec node `P3-routers: Thin the Mega-Routers Into Request→Service→Envelope Shells` (`docs/rework/hal0-specs/spec-p3-routers.md`, community 139).

Graph already oriented via `graphify explain ENDPOINTS`, `create_app`, `v1.py`, `models.py`, `slots.py`, `mcp.py`, `board.py`, `realtime.py`, `chat_proxy.py`, `agents.py`, `P3-routers`, plus `graphify path create_app() v1.py` and `graphify query` for router-registration patterns. Raw-file greps limited to line-anchored confirmation of degree/LOC/decorator counts.

## Findings

1. **`ENDPOINTS` is a frontend node, not a backend god.** Degree 136 at `ui/src/api/endpoints.ts:8` (community 0). It is the single URL-constants table consumed by ~30 React hooks (`useBoard.ts`, `useSlots.ts`, `useModels.ts`, etc.). Coupling smell: changing a route in the backend forces edits here, but there is no schema link — drift is silent.

2. **`create_app()` is the real backend god.** Degree 104 at `src/hal0/api/__init__.py:1400` (community 141). It owns: `FastAPI(...)`, `lifespan()`, all 5 `AuthEnforcementMiddleware` indirect wiring, and **50 `app.include_router(...)` calls** (confirmed by `grep -c app.include_router src/hal0/api/__init__.py` = 50, spanning L1441–L1686). Every endpoint surface in the system flows through this one function.

3. **`api/__init__.py` is itself the largest "router" — 1892 LOC.** Bigger than any single route module. It is a composite of: imports, `lifespan()`, `create_app()`, `_build()`, `_run_chat()`, `_make_client()`, `_build_app()` test helpers, the audit/redact helpers, plus inline router wiring. The 50 include_router calls form the de-facto "routing registry."

4. **Mega-routers per graph (ranked by graph degree + LOC).** Both signals disagree on which is "worst" — graph degree tracks business-logic coupling; LOC tracks request-handler volume.

   | File | LOC | graph degree | handlers | community | spec note |
   |---|---:|---:|---:|---:|---|
   | `src/hal0/api/__init__.py` | **1892** | 104 | n/a (composer) | 141 | NOT in P3 spec — should be |
   | `src/hal0/api/routes/v1.py` | 1685 | 42 | 10 | 38 | top handler by edge weight |
   | `src/hal0/api/routes/slots.py` | 1328 | 43 | 26 | 23 | P3 lists 1888 LOC as-built (1.2) |
   | `src/hal0/api/routes/memory.py` | 1205 | — | 14 | — | not called out in P3 — underspecified |
   | `src/hal0/api/routes/models.py` | 1145 | 23 | 21 | 126 | P3 lists 2267 LOC as-built (1.1) |
   | `src/hal0/api/routes/updater.py` | 977 | — | — | 44 | not in P3 |
   | `src/hal0/api/routes/comfyui.py` | 933 | — | — | — | not in P3 |
   | `src/hal0/api/routes/mcp.py` | 893 | 24 | 10 | 134 | P3 §4 (auto-gen admin) |
   | `src/hal0/api/agents/chat_proxy.py` | 572 | 18 | — | 270 | WebSocket proxy to hermes; split across `api/agents/` and `api/routes/agents.py` |
   | `src/hal0/api/routes/board.py` | 473 | 40 | **32** | 104 | DENSE — 14.7 LOC/handler; P3 gap |
   | `src/hal0/api/routes/realtime.py` | 106 | 5 | — | **762** | isolated community; no cross-refs |

   Discrepancy vs spec: P3 spec claims `models.py` = 2267 LOC and `slots.py` = 1888 LOC. Current tree is 1145 / 1328. Either the spec measures a different commit (likely `main`) or the spec is stale; either way, both files remain mega-routers in `rework/descar`.

5. **`routes/__init__.py` is empty** (verified by `Read` — file exists, zero bytes). No barrel; no semantic grouping. The 50 include_router calls in `api/__init__.py` import each route module directly. The package is a flat bag of 48 modules — `routes/_memory_subgraph.py`, `routes/activity.py`, … `routes/v1.py` — without any sub-package hierarchy.

6. **`realtime.py` is in its own community (762).** Five edges, no coupling to other route modules, no shared types with `v1.py`. Risk: Realtime drift from the rest of the `/v1` surface (spec: `spec-hp-realtime` notes it is brand new).

7. **`chat_proxy.py` lives under `src/hal0/api/agents/`, not `routes/`.** `src/hal0/api/agents/chat_proxy.py` (572 LOC, degree 18, community 270) wraps Hermes WebSocket + session REST. But a parallel `src/hal0/api/routes/agents.py` (338 LOC, degree 9) also exists for install/uninstall/list. **Two roots for one product surface** — fragmenting the "agents" API across the tree.

8. **MCP admin is hand-maintained.** Per spec §4 / §1.6: `mcp/admin.py` (1684 LOC on spec) maintains `_REST_MAP` + `_PATH_ARGS` by hand; P3 plans auto-generation from manifest. Current `mcp.py` route file is 893 LOC / 10 handlers — confirms the surface is large but linear, not graph-dense.

9. **`v1.py` is the proxy/projection megabyte.** Degree 42, 10 visible route decorators, but its inner surface (`chat_completions`, `embeddings`, `audio_speech`, `audio_transcriptions`, `images_generations`, `_dispatch_and_forward`, `_dispatch_via_npu_trio`, `_forward_multipart`, `_aggregate_models`, `_ensure_backend_for_model`, `_instrument_streaming_throughput`, `_record_nonstreaming_throughput`, `_seed_tts_defaults`) is the OpenAI-compatible surface — must remain backwards-compatible per spec. Path: `create_app() → __init__.py → hal0_chat_slot_alias_map() → _rewrite_chat_slot_alias() → v1.py` (4 hops, BFS-confirmed).

10. **Auth/middleware is centralized.** `AuthEnforcementMiddleware` lives in `src/hal0/api/middleware/` (3 files: `__init__.py`, `error_codes.py`, `request_id.py`, `log_scrub.py`). Per-handler `Depends(...)` is the only auth seam — fine, but means a per-route exemption (spec §1.7 `security/exposure.py` 276 LOC, **READ-ONLY**) is the auth carve-out path.

## Risks / Smells

- **Single-file god at `api/__init__.py`** — 1892 LOC, 104 graph edges, 50 router mounts. Every refactor of the route surface touches this file. It is the largest target the P3 spec missed.
- **`board.py` density** — 32 handlers in 473 LOC. Highest handler-per-LOC of any route file. Likely to grow as board features compound.
- **`memory.py` not addressed by spec** — 1205 LOC, 14 handlers, business logic (memory subgraph + admin). P3 spec enumerates `models.py`, `slots.py`, `mcp/admin.py`, `chat_templates.py`, `security/exposure.py`, `api/__init__.py`-as-`create_app`, but skips `memory.py` and `memory_admin.py`.
- **Two roots for agents API** — `api/agents/chat_proxy.py` + `api/routes/agents.py`. Onboarding confusion; cross-imports likely hidden.
- **Empty `routes/__init__.py`** — no namespace grouping; flat bag of 48 modules.
- **`ENDPOINTS` ↔ backend drift** — frontend URL table has no schema link to backend mounts. Renaming a route silently breaks the UI.
- **`realtime.py` isolation** — community 762 (its own); zero coupling; high drift risk vs the rest of `/v1`.
- **`models.py` LOC discrepancy** — spec says 2267, tree says 1145. Spec is for a different revision (likely `main`); rework branch has not yet hit mega-router status for `models.py`, but is approaching it.

## Recommendations (concrete, cross-referenced to P3 spec)

1. **Add `api/__init__.py` to the P3 spec as file 1.0.** Split into `api/app.py` (FastAPI + middleware + lifespan) and `api/routes/__init__.py` (the 50-line mount registry). Net: kills the god node at degree 104.

2. **Extraction order (per P3 §5.1, de-risk easy first):**
   1. `v1.py` (1685 LOC, 10 handlers) — extract OpenAI-compat shim into `services/chat_compat.py`; route becomes 30-LOC envelope.
   2. `slots.py` (1328 LOC, 26 handlers) — per spec, split into `slots/{crud,state,metrics}.py` routes + `slots_service.py`.
   3. `models.py` (1145 LOC, 21 handlers) — pull-list, scan, hf into `models_services/`; keep create/update/delete as envelopes.
   4. `mcp.py` admin section — auto-generate per spec §4.

3. **Add `memory.py` and `memory_admin.py` to the spec.** 1205 + 674 LOC, both target-rich for service-layer extraction (memory subgraph is independent of HTTP shape).

4. **Reunify the agents API.** Move `src/hal0/api/agents/chat_proxy.py` under `routes/agents/` and merge with `routes/agents.py`. Single import root.

5. **Populate `api/routes/__init__.py` with a typed mount list.** Function `def mount(app: FastAPI) -> None:` that does the 50 `include_router` calls, imported by `api/app.py`. Today the file is 0 bytes — perfect seed.

6. **Type the auth seam.** `security/exposure.py` (spec §1.7, READ-ONLY 276 LOC) becomes the single source for per-route auth flags. Replace ad-hoc `Depends(...)` chains with one exposure decorator.

7. **Snapshot-test envelope shape (spec §7.4)** across `v1`, `slots`, `models`, `mcp` *before* extracting, so the rewrite is mechanical.

## Cross-references

- P3 spec: `docs/rework/hal0-specs/spec-p3-routers.md` — community 139, degree 12. Sub-nodes at L29 (1.1 models), L65 (1.2 slots), L134 (1.5 chat_templates), L143 (1.6 mcp/admin), L156 (1.7 exposure), L236 (3.1 service protocols), L439 (3.3 typed-error migration), L447 (4 MCP admin auto-gen), L506 (5.1 service extraction order), L535 (5.3 typed-error migration).
- Rework board entry: P3-routers → ✔ (tracked on `REWORK_BOARD.md`).
- create_app() wiring: `src/hal0/api/__init__.py` L862 (`lifespan()`), L1400 (`create_app()`), L1441–L1686 (50 include_router calls).
- Path confirmation: `create_app() → __init__.py → hal0_chat_slot_alias_map() → _rewrite_chat_slot_alias() → v1.py` (4 hops).
