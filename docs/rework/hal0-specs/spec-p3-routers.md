# P3-routers: Thin the Mega-Routers Into Request→Service→Envelope Shells

**Repo:** `/home/mint/hal0` @ `rework/descar` · **Mode:** READ-ONLY analysis, WRITE-ready spec, line numbers verified against `e9639de1`-ish tree at `77e41b93` (see §1 for re-check).
**Plan refs:** `hal0-rework-plan.md` §Phase 3 #4 (god-files L165–170) + §23.2 seam S9 (`security/exposure.py`) + §24.2 W6 (P3-routers ships in W6 with ML-1 tables available).
**Target files:** `api/routes/models.py` · `api/routes/slots.py` · `api/routes/comfyui.py` · `api/routes/benchmarks.py` · `api/routes/chat_templates.py` · `mcp/admin.py` (`_REST_MAP`/`_PATH_ARGS`).

## 0. Executive summary

Three god-router files hold the **whole vertical** — request decode, validation, side-effect orchestration, error envelope. ~4,500 lines of request-bound Python that embeds HF client wiring, pull-job scheduling, cgroup/systemd/llama-metrics scraping, port allocation, FLM catalog probing, and the bench planner's localhost self-call — all of which belong one layer down. The **services already exist** for most of this work (`slots/metrics.py`, `slots/capacity.py`, `registry/{store,discover,detect,curated,model,pull,update_check}.py`, `slot_view/*`, `upstreams/huggingface.py`); the route layer currently re-implements them inline.

This spec **thins routes only** — does NOT touch `slots/manager.py` internals (P3-slots owns that), does NOT touch `security/exposure.py` RULES (KB-1 owns that), does NOT change route paths (the §6 coordination rule). Three changes:

1. **Extract orchestration to service modules.** Heavy blocks from `routes/models.py` (`_run_pull_with_events`, the sidecar/byte math, the FLM puller, schedule helper, the persist-snapshot fallback) move to `registry/pull_jobs.py`. Heavy blocks from `routes/slots.py` (`_systemd_show`, `_docker_container_mem_bytes`, `_scrape_llama_metrics`, `_local_slot_metrics`, the journalctl tail, the port allocator, the FLM catalog probe, the slot-image puller) move to `slots/{metrics_collect,logs,port_alloc,flm_catalog,image_pull}.py`. The route handlers become **request → service → envelope** shells, ~4-line bodies.
2. **Convert all 38 `await request.json()` sites to Pydantic v2 bodies.** `extra="forbid"` where the original code raised on unknown keys, `extra="ignore"` where the legacy dict-swallower was permissive. Bodies live at route module scope, exported; FastAPI binds them.
3. **Convert 10 `HTTPException` + 6 hand-built `JSONResponse(status_code=…)`** error envelopes across `routes/{benchmarks,chat_templates,comfyui}.py` to typed `BadRequest`/`NotFound`/`Conflict`/`Hal0Error` (already exported by `api/middleware/error_codes.py`). Middleware renders the envelope byte-identically to the hand-built shape (verified — see §3.3).

**MCP admin:** `_REST_MAP` (86 entries, L424–512) and `_PATH_ARGS` (27 entries, L517–557) become **auto-generated** at app-startup from `create_app().routes`. A `build_admin_route_map(app)` walks the route table, derives `route_id = "<method>:<path-template>"`, extracts `{placeholder}` names to build `_PATH_ARGS`. The MCP **security overlay** (`AUTONOMOUS_*`, `GATED_TOOLS`, `PROBE_TOOLS`, `TOOL_PARAM_HINTS`, `TOOL_DESCRIPTIONS`, `_ANNOTATIONS`, `TOOL_NAME_ALIASES`) stays hand-authored (no FastAPI analog). `_validate_catalog` (L1539) keeps its job but now cross-checks the auto-gen map.

**Constraints (carrier):** no path renames (`security/exposure.py` matches on path strings; a rename breaks §21.11 exposure-CI in the same PR) · no `slots/manager.py` internals (P3-slots owns the decomposition; this lane consumes it) · every existing public name re-exported so the ~60 callers stay unbroken.

**Net after landing:** `routes/models.py` ≤ 550 LOC · `routes/slots.py` ≤ 800 LOC (SSE/journalctl wrappers stay) · `request.json()` count → 0 in `routes/` · `HTTPException` count → 0 in `routes/` · hand-built `JSONResponse(status_code=…)` for errors → 0 in `routes/comfyui.py`.

---

## 1. Current-state map (verified, line-anchored)

Verified against `src/hal0/` at HEAD of `rework/descar` (commit `77e41b93`). **Re-check before editing** — files drift; the sizes/as-built lines below are stable per the plan cite but route bodies should be grep-verified on the live tree.

### 1.1 `api/routes/models.py` — **2,267 LOC as-built** (plan: 2,509)

P3-routers scope is **route handlers + their private helpers** in this file. The HF client (`upstreams/huggingface.py`), pull-job core (`registry/pull.py`), and registry backend (`registry/store.py`) already exist and stay there.

| Symbol | Lines | What it does | Disposition |
|---|---|---|---|
| `router`, `log`, imports | 1–52 | wiring | stays |
| `_load_persisted_pull_job`, `_reconcile_persisted_pull_job` | 64–131 | pull-snapshot disk fallback + reconcile | **EXTRACT** → `registry/pull_jobs.py::load_persisted` / `reconcile_persisted` |
| `_ALIAS_NAMES`, `_is_alias`, `_FLM_DISPATCH_TYPE`, `_MODALITY_TO_SLOT_TYPE`, `_dispatch_type`, `_comfyui_category` | 133–222 | classification helpers (pure) | **EXTRACT** → `registry/normalize.py` (re-export from `registry/__init__.py`) |
| `list_models` (`GET ""`) | 224–416 | registry + upstreams aggregator | **stay thin** — orchestration → `registry/list.py::list_all` |
| `list_catalogue` (`GET /catalogue`) | 418–442 | curated catalogue | **stay thin** (delegates to `registry/curated`) |
| `scan_preview` (`POST /scan/preview`) | 444–571 | walk paths, detect, return rows | **EXTRACT** body-decoder + walker → `registry/scan.py::preview` |
| `scan_models` (`POST /scan`) | 573–626 | commit scan + emit | **EXTRACT** → `registry/scan.py::commit` |
| `_commit_scan_rows`, `_suggest_id_from_path` | 628–741 | commit logic + id-heuristic | extract with `scan_models` |
| `add_model_from_path` (`POST /add-from-path`) | 744–896 | model detect + register | **EXTRACT** → `registry/add.py::add_from_path` |
| `create_model` (`POST ""`) | 898–943 | direct registry create | **stay thin** (binds `CreateModelBody`) |
| `_model_to_dict`, `_lazy_quant` | 945–1002 | shape helpers | extract → `registry/serialize.py` |
| `list_pulls` (`GET /pulls`) | 1004–1044 | list in-flight + persisted jobs | **EXTRACT** → `registry/pull_jobs.py::list_all` |
| `_pull_entry`, `_speed_for_entry`, `_eta_for_entry`, `_hf_repo_for_model` | 1046–1106 | shape helpers | extract → `registry/pull_jobs.py` |
| `check_model_updates` (`GET /updates/check`) | 1108–1161 | HF remote SHA compare | **EXTRACT** adapter → `registry/update_check.py::check_for_model` |
| `update_model_from_hf` (`POST /{model_id}/update`) | 1163–1246 | trigger update pull | **EXTRACT** → `registry/update_check.py::apply_for_model` |
| `get_model` (`GET /{model_id}`) | 1248–1264 | `registry.get` + serialize | stay thin |
| `update_model` (`PUT /{model_id}`) | 1266–1316 | registry update + emit | **EXTRACT** → `registry/update.py::apply` |
| `_slots_referencing_model`, `_clear_slot_default`, `_unload_slot_if_running` | 1318–1401 | cascade helpers | extract → `registry/cascade.py` |
| `delete_model` (`DELETE /{model_id}`) | 1403–1493 | cascade delete + emit | **EXTRACT** → `registry/cascade.py::delete_model` |
| `delete_pull` (`DELETE /pulls/{model_id}`) | 1495–1532 | cancel + remove snapshot | **EXTRACT** → `registry/pull_jobs.py::cancel` |
| `_resolve_pull_source`, `_resolve_pull_capability`, `_seed_registry_from_body`, `_resolve_pull_source_with_body`, `_schedule_pull_task`, `_run_pull_with_events`, `_emit_terminal_pull_event`, `_speed_bps`, `_eta_s` | 1534–1907 | **the orchestration block** | **EXTRACT entire block** → `registry/pull_jobs.py::enqueue_hf` (largest single extraction) |
| `pull_model` (`POST /{model_id}/pull`) | 1909–2027 | HF + FLM dispatch | **EXTRACT** → `registry/pull_jobs.py::enqueue` (binds `PullBody`) |
| `_start_flm_pull` | 2030–2077 | FLM pull adapter | extract with `pull_model` |
| `pull_status` (`GET /{model_id}/pull/status`) | 2080–2102 | job snapshot | **EXTRACT** → `registry/pull_jobs.py::status` |
| `pull_stream` (`GET /{model_id}/pull/stream`) | 2104–2189 | SSE of job progress | **EXTRACT** → `registry/pull_jobs.py::stream` (route keeps `StreamingResponse` wrapper) |
| `inspect_model` (`POST /inspect`) | 2191–2249 | HF repo metadata, no registry touch | **EXTRACT** → `registry/inspect.py::inspect_hf_repo` (binds `InspectBody`) |
| `pull_cancel` (`POST /{model_id}/pull/cancel`) | 2251–2267 | cancel + emit | **EXTRACT** → `registry/pull_jobs.py::cancel` (note: `DELETE /pulls/{model_id}` and `POST /{model_id}/pull/cancel` are duplicates — leave as-is in this lane; consolidation = §6 coordination followup) |

**Net after extraction:** `routes/models.py` ≈ 450–550 LOC. New `registry/{list,scan,add,update,cascade,normalize,serialize,inspect,pull_jobs}.py` (9 modules, ~1,150 LOC extracted).

### 1.2 `api/routes/slots.py` — **1,888 LOC as-built** (plan: 1,846)

P3-slots already extracted `slots/{reaper,watchdog,routing,config_write,profile_adopt,npu}.py` — **recheck what remains in the ROUTE layer** before extraction. The route file still owns: SSE/journalctl streaming (must stay — holds `StreamingResponse`), port allocation, FLM catalog probe, slot-image pull orchestration, and thin delegators to `SlotManager`.

| Symbol | Lines | What it does | Disposition |
|---|---|---|---|
| `router`, `log`, module docstring, early imports | 1–52 | wiring | stays |
| `list_flm_models` (`GET /flm/models`) | 54–136 | NPU catalog probe (subprocess + fallback) | **EXTRACT** → `slots/flm_catalog.py::list_models` |
| `NotImplementedYet` class | 139–141 | typed error | stays (re-export from `slots/errors.py` later) |
| `_get_slot_manager`, `_slot_to_dict`, `_config_field_enrichment`, `_container_state_enrichment`, `_loaded_models`, `_synthesize_slots_from_upstreams` | 145–311 | request-bound 1-liners over `slot_view`/`SlotManager` | **stay** (acceptable adapter layer) |
| `list_slots` (`GET ""`) | 313–341 | aggregator | **stay thin** |
| `_slot_port_range`, `_collect_port_claims`, `_next_free_slot_port`, `_reject_port_conflict` | 343–429 | port allocator | **EXTRACT** → `slots/port_alloc.py` (flag for §11.2 PortAuthority merge) |
| `_reject_unknown_config_keys`, `_normalize_create_body` | 431–486 | body validation/normalization | **stay** (move to `slots/config_write.py` if it grows — already-extracted collaborator) |
| `create_slot` (`POST ""`) | 488–565 | SlotManager.create wrapper | **stay thin** (binds `CreateSlotBody`) |
| `_tps_from_events`, `_per_slot_local_tps`, `_per_slot_ttft` | 570–637 | rolling-window stat helpers | **EXTRACT** → `slots/metrics_collect.py::local_views` |
| `_systemd_show` | 639–671 | `systemctl show` subprocess | **EXTRACT** → `slots/metrics_collect.py::systemd_props` |
| `_scrape_llama_metrics` | 673–799 | httpx `/metrics` + `/slots` | **EXTRACT** → `slots/metrics_collect.py::llama_metrics` |
| `_docker_container_mem_bytes` | 800–846 | cgroup-v2 walk | **EXTRACT** → `slots/metrics_collect.py::container_mem_bytes` |
| `_local_slot_metrics` | 847–934 | per-slot fan-out + FLM KV | **EXTRACT** → `slots/metrics_collect.py::collect_local` |
| `slot_metrics` (`GET /metrics`) | 936–1029 | 3-layer merge + FLM KV | **stay thin** (calls `metrics_collect.collect_local`) |
| `slot_capacity` (`GET /capacity`) | 1030–1055 | `CapacitySnapshot` (exists) | **stay thin** (calls `slots/capacity.build_per_slot`) |
| `get_slot` (`GET /{name}`) | 1057–1092 | SlotManager.status + enrich | **stay thin** |
| `_state_value`, `_safe_config` | 1094–1107 | small helpers | stays |
| `delete_slot` (`DELETE /{name}`) | 1109–1121 | SlotManager.delete | **stay thin** |
| `get_slot_config` (`GET /{name}/config`) | 1123–1129 | SlotManager.get_config | **stay thin** |
| `get_slot_voices` (`GET /{name}/voices`) | 1131–1164 | httpx `/v1/audio/voices` | **EXTRACT** → `slots/voices.py::fetch_for_slot` |
| `get_slot_resolved` (`GET /{name}/resolved`) | 1166–1183 | container.resolved_argv | **stay thin** |
| `update_slot_config` (`PUT /{name}/config`) | 1185–1238 | body-decode + merge + audit + unload-on-disable | **stay thin** (binds `UpdateSlotConfigBody`) |
| `update_slot_defaults` (`PATCH /{name}/defaults`) | 1240–1270 | defaults merge | **stay thin** (binds `UpdateDefaultsBody`) |
| `load_slot` (`POST /{name}/load`) | 1277–1319 | model_id validate + load | **stay thin** (binds `LoadSlotBody`) |
| `unload_slot`, `restart_slot`, `swap_slot` | 1321–1415 | lifecycle wrappers | **stay thin** |
| `_is_log_noise`, `slot_logs`, `slot_logs_stream` (SSE) | 1417–1555 | journalctl tail + SSE | **EXTRACT** journalctl subprocess → `slots/logs.py::tail_journal`; **keep SSE wrapper in route** (holds `StreamingResponse`) |
| `slot_state` (`GET /{name}/state`) | 1557–1572 | snapshot subset | **stay thin** |
| `slot_state_stream` (`GET /{name}/state/stream`) | 1574–… | SSE | **stay** (SSE wrapper) |
| `_run_image_pull`, `pull_slot_image`, `pull_slot_image_stream`, `pull_slot_image_status` | 1668–1888 | slot-image pull orchestration | **EXTRACT** → `slots/image_pull.py` |

**Net after extraction:** `routes/slots.py` ≈ 700–800 LOC (the SSE/journalctl wrappers must stay; the IO-adapter block + port allocator + FLM catalog + image-pull all move).

### 1.3 `api/routes/comfyui.py` — **951 LOC** — typed-error outliers

`comfyui.py` does not import `HTTPException` but builds the same shape by hand via `JSONResponse(status_code=…, content={"error": {"code": …, "message": …}})`. Two `request.json()` sites (L477, L560) need Pydantic bodies; six envelopes need converting. `/models/fetch` (L596) already uses `_FetchBody` (Pydantic) — pattern is established in-file.

| Site | Lines | Current | Replace with |
|---|---|---|---|
| `comfyui_switchover` body | 477 | `await request.json()` | `SwitchoverBody` (Pydantic) |
| `comfyui_models_fetch` body | 560 | `await request.json()` | `ModelsFetchBody` (Pydantic) |
| `comfyui_switchover` invalid mode | 482–491 | `JSONResponse(422, …)` | `BadRequest(…, code="comfyui.invalid_mode")` |
| `comfyui_switchover` switch in progress | 495–504 | `JSONResponse(409, …)` | `Conflict(…, code="comfyui.switch_in_progress")` |
| `comfyui_switchover` busy | 528–541 | `JSONResponse(409, …)` | `Conflict(…, code="comfyui.busy")` |
| `comfyui_pin` invalid pin | 565–574 | `JSONResponse(422, …)` | `BadRequest(…, code="comfyui.invalid_pin")` |
| `comfyui_models_fetch` invalid body | 613–622 | `JSONResponse(422, …)` | `BadRequest(…, code="comfyui.fetch.invalid_body")` |
| `comfyui_models_fetch` unknown variant | 632–641 | `JSONResponse(422, …)` | `BadRequest(…, code="comfyui.fetch.unknown_variant")` |
| `_output_image` / `_output_metadata` 404 | 926–929 | `JSONResponse(404, …)` | **verify before converting** (these live inside response builders, may be needed for streaming) |

### 1.4 `api/routes/benchmarks.py` — **480 LOC** — raw `HTTPException`

8 `raise HTTPException(…)` sites, all bypassing typed envelope. 5 are `400 BadRequest`, 3 are `404 NotFound`. Zero Pydantic bodies today. Routes are mostly `def` (not `async def`) per the file's "threadpool because of blocking IO" docstring (L11–18). **Do not switch to `async def`** during this lane.

| Site | Line | Current | Replace with |
|---|---|---|---|
| `get_run`/`list_runs` unknown suite | 199 | `HTTPException(404, "unknown suite …")` | `NotFound(…, code="bench.unknown_suite")` |
| queue/list filter validation | 274 | `HTTPException(400, "cell_key or model is required")` | `BadRequest(…, code="bench.missing_filter")` |
| `get_run` unknown run_id | 331 | `HTTPException(404, "unknown run_id: …")` | `NotFound(…, code="bench.unknown_run")` |
| enqueue validation | 412 | `HTTPException(400, "body.suite or body.model is required")` | `BadRequest(…, code="bench.invalid_envelope")` |
| enqueue unknown kind | 414 | `HTTPException(400, "unknown queue kind …")` | `BadRequest(…, code="bench.bad_kind")` |
| enqueue missing model | 416 | `HTTPException(400, "kind='eval' requires body.model")` | `BadRequest(…, code="bench.eval_needs_model")` |
| enqueue unknown suite | 420 | `HTTPException(404, "unknown suite …")` | `NotFound(…, code="bench.unknown_suite")` |
| control bad action | 461 | `HTTPException(400, "bad action …")` | `BadRequest(…, code="bench.bad_action")` |

### 1.5 `api/routes/chat_templates.py` — **141 LOC** — bonus outliers

2 `raise HTTPException(…)` sites — adjacent, trivial. Not in the plan's "38" `request.json` count but in the outliers sweep.

| Site | Line | Current | Replace with |
|---|---|---|---|
| template id validation | 129 | `HTTPException(400, "Invalid template id …")` | `BadRequest(…, code="chat_template.invalid_id")` |
| template write failure | 136 | `HTTPException(500, "Could not write template …")` | `Hal0Error(…, code="chat_template.write_failed")` |

### 1.6 `mcp/admin.py` — **1,684 LOC** — `_REST_MAP`/`_PATH_ARGS` hand-maintained

| Constant | Lines | What | Disposition |
|---|---|---|---|
| `_REST_MAP` | 424–512 | **86 entries** `(tool_name → (method, path_template))` | **AUTO-GENERATE** at startup |
| `_PATH_ARGS` | 517–557 | **27 entries** `(tool_name → tuple of path-arg names)` | **AUTO-GENERATE** (extract `{placeholder}` from `path_template`) |
| `TOOL_PARAM_HINTS` | 574 | per-tool body schema hints | stays hand-authored |
| `TOOL_DESCRIPTIONS` | — | per-tool descriptions for `tools/list` | stays hand-authored |
| `_ANNOTATIONS` | — | per-tool ToolAnnotations | stays hand-authored |
| `AUTONOMOUS_READ/WRITE_TOOLS`, `GATED_TOOLS`, `PROBE_TOOLS` | 254–370 | tool policy overlay | stays hand-authored |
| `_validate_catalog` | 1539 | consistency check | **KEEP**, adapt to auto-gen (§4.3) |
| `TOOL_NAME_ALIASES` | (NEW) | back-compat shim: route-derived name → hand-curated name | **NEW** — required for agent-chat stability (see §4.5) |

### 1.7 `security/exposure.py` — **276 LOC** — **READ-ONLY CONTRACT**

`RULES` (L125–214) classifies by **path strings** (`_exact` + `_prefix` matchers). It is the single classification source for KB-1 auth middleware (§23.5 S9). **No path changes from P3-routers** — the work is body + extraction, not URL design. If a future cleanup PR consolidates `POST /api/models/{model_id}/pull/cancel` and `DELETE /api/models/pulls/{model_id}` (currently two routes for the same effect — see §6), it MUST update `RULES` and `tests/security/test_exposure.py` in the same diff. **Do not do that consolidation in this lane.**

---

## 2. Target module layout

```
src/hal0/registry/
  __init__.py
  model.py                       # Model dataclass (existing)
  store.py                       # ModelRegistry backend (existing — ML-1 swaps)
  detect.py, discover.py         # (existing)
  curated.py                     # (existing)
  pull.py                        # PullJob + make_job + run_pull + run_flm_pull
                                 # + persist_pull_job + list_persisted_jobs (existing)
  update_check.py                # (existing — adapter lands here)
  list.py                NEW     # list_all(registry, upstreams) → List[dict]
  scan.py                NEW     # preview(paths, recursive) + commit(rows, registry, bus)
  add.py                 NEW     # add_from_path(path, labels, …) → Model
  update.py              NEW     # apply(registry, model_id, body, bus)
  cascade.py             NEW     # delete_model(registry, sm, model_id, bus)
                                 #         + slot-cascade helpers
  inspect.py             NEW     # inspect_hf_repo(repo, filename) → rows
  normalize.py           NEW     # is_alias, dispatch_type, comfyui_category,
                                 # _ALIAS_NAMES, vocab tables (pure)
  serialize.py           NEW     # model_to_dict, lazy_quant, pull_entry,
                                 # speed_for_entry, eta_for_entry (pure)
  pull_jobs.py           NEW     # enqueue/enqueue_hf/enqueue_flm + status +
                                 # stream + cancel + load_persisted +
                                 # reconcile_persisted + list_all + sidecar +
                                 # schedule helper (was the routes/models.py
                                 # orchestration block)

src/hal0/slots/
  manager.py                     # CORE: state machine + lifecycle + CRUD
                                 #           (P3-slots owns; do NOT touch internals)
  reaper.py, watchdog.py,        # P3-slots collaborators (existing)
    routing.py, config_write.py,
    profile_adopt.py, npu/,
  capacity.py, metrics.py,       # (existing)
    state.py, argv.py,
    arbiter.py, ttft_samples.py
  metrics_collect.py     NEW     # systemd_props, container_mem_bytes,
                                 # llama_metrics, collect_local, local_views
  port_alloc.py          NEW     # slot_port_range, collect_port_claims,
                                 # next_free, reject_conflict
                                 # (merged into §11.2 PortAuthority when that
                                 # lands — flag for deletion in that PR)
  voices.py              NEW     # fetch_for_slot(name, port) → voices/source
  logs.py                NEW     # tail_journal(unit, backfill_n, quiet) → lines
  image_pull.py          NEW     # run_image_pull + slot image pull orchestration
  flm_catalog.py         NEW     # list_models() NPU catalog probe + fallback

src/hal0/api/routes/
  models.py                      # THIN: request → registry/* → envelope (~450–550 LOC)
  slots.py                       # THIN: request → SlotManager + slot_view →
                                 # envelope (~700–800 LOC; SSE/journalctl wrappers stay)
  comfyui.py                     # typed Hal0Error replaces JSONResponse envelopes
                                 # (6 sites); Pydantic bodies replace request.json()
                                 # (2 sites); leave _output_* 404 alone (§1.3 caveat)
  benchmarks.py                  # typed Hal0Error replaces HTTPException (8 sites);
                                 # Pydantic bodies (EnqueueBody, ControlBody);
                                 # keep `def` (threadpool) — do NOT async-ify
  chat_templates.py              # typed Hal0Error replaces HTTPException (2 sites)

src/hal0/mcp/
  admin.py                       # _REST_MAP/_PATH_ARGS auto-generated at startup
                                 # from create_app().routes; security overlay
                                 # (AUTONOMOUS_*, GATED_TOOLS, PROBE_TOOLS,
                                 # TOOL_PARAM_HINTS, TOOL_DESCRIPTIONS,
                                 # _ANNOTATIONS, TOOL_NAME_ALIASES) stays
  build_server.py                # consumes the auto-gen map (no change)
```

---

## 3. Interface boundaries

### 3.1 Service-layer Protocols (unit-testability seams)

```python
# registry/pull_jobs.py
from typing import Protocol, Any
from fastapi import Request
from collections.abc import AsyncIterator

class EventBusLike(Protocol):
    async def emit(self, kind: str, level: str, key: str, msg: str,
                   *, data: dict[str, Any] | None = None) -> None: ...

class ModelRegistryLike(Protocol):
    def has(self, model_id: str) -> bool: ...
    def get(self, model_id: str) -> Any: ...
    def add(self, model: Any) -> None: ...
    def update(self, model_id: str, body: dict) -> Any: ...
    def remove(self, model_id: str) -> None: ...

async def enqueue_hf(
    request: Request, *, model_id: str,
    body: dict[str, Any] | None,
) -> dict[str, object]: ...
async def enqueue_flm(request: Request, *, model_id: str) -> dict[str, object]: ...
def status(model_id: str, *, registry: ModelRegistryLike) -> dict[str, object]: ...
async def stream(model_id: str, *, request: Request) -> AsyncIterator[str]: ...
async def cancel(model_id: str, *, request: Request) -> dict[str, object]: ...
def list_all() -> list[dict[str, Any]]: ...
def load_persisted(model_id: str,
                    registry: ModelRegistryLike | None = None
                    ) -> dict[str, Any] | None: ...
def reconcile_persisted(persisted: dict[str, Any],
                         registry: ModelRegistryLike | None = None) -> dict[str, Any]: ...
def schedule_pull_task(app_state: Any, model_id: str, coro: Any) -> None: ...
```

```python
# slots/metrics_collect.py
from typing import Protocol

class SlotManagerLike(Protocol):
    async def list(self) -> list[Any]: ...

async def systemd_props(unit: str, *props: str) -> dict[str, str]: ...
async def container_mem_bytes(container_name: str) -> int: ...
async def llama_metrics(port: int) -> dict[str, Any]: ...
async def collect_local(sm: SlotManagerLike) -> dict[str, dict[str, Any]]: ...
def local_tps(app_state: Any, window_s: float = 5.0) -> dict[str, float]: ...
def local_ttft(app_state: Any) -> dict[str, dict[str, float]]: ...
```

```python
# slots/port_alloc.py — flag for §11.2 PortAuthority merge
def slot_port_range(cfg: Any | None) -> tuple[int, int]: ...
def collect_port_claims(start: int, end: int,
                          slot_snapshots: list[dict] | None = None) -> list[Any]: ...
def next_free(claims: list[Any], start: int, end: int) -> int | None: ...
def reject_conflict(port: int, owner_slot: str,
                     slot_snapshots: list[dict] | None = None) -> None: ...
```

```python
# slots/voices.py
async def fetch_for_slot(name: str, port: int | None) -> dict[str, Any]:
    """Fail-soft: cold/unreachable → {"voices": [], "source": "offline"}."""
```

```python
# slots/logs.py
async def tail_journal(unit: str, backfill_n: int = 0,
                        *, quiet: bool = True) -> AsyncIterator[str]:
    """Yields journalctl lines. Used by SSE wrappers in routes/slots.py."""
```

```python
# slots/image_pull.py
@dataclass
class ImagePullJob: ...
async def run_image_pull(job: ImagePullJob) -> None: ...
def enqueue(name: str, app_state: Any) -> ImagePullJob: ...
def status(name: str) -> dict[str, object]: ...
async def stream(name: str) -> AsyncIterator[str]: ...
```

### 3.2 Route-layer Pydantic bodies (replacing `await request.json()`)

Place bodies at module scope in each route file, exported with the route handlers. Use Pydantic v2 (`from pydantic import BaseModel, Field, ConfigDict`). `extra="forbid"` where the original code raised on unknown keys; `extra="ignore"` where the legacy dict-swallower was permissive.

```python
# routes/models.py — bodies
class ScanPreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paths: list[str] = Field(min_length=1)
    recursive: bool = True

class ScanCommitBody(BaseModel):
    model_config = ConfigDict(extra="ignore")  # legacy compat
    rows: list[dict[str, Any]] | None = None

class AddFromPathBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = None
    name: str | None = None
    path: str
    labels: list[str] | None = None
    overwrite: bool = False

class CreateModelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    path: str
    name: str | None = None
    size_bytes: int | None = None
    quant: str | None = None
    capabilities: list[str] = []
    backends: list[str] = []
    metadata: dict[str, Any] = {}

class UpdateModelBody(BaseModel):
    """Partial — any subset of fields."""
    model_config = ConfigDict(extra="ignore")
    name: str | None = None
    capabilities: list[str] | None = None
    backends: list[str] | None = None
    defaults: dict[str, Any] | None = None
    license: str | None = None
    tags: list[str] | None = None
    metadata: dict[str, Any] | None = None

class InspectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hf_repo: str | None = None
    hf_url: str | None = None
    hf_filename: str | None = None

class PullBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    hf_repo: str | None = None
    hf_filename: str | None = None
    mmproj_filename: str | None = None
    labels: list[str] | None = None
    chat_template: str | None = None

class UpdateFromHfBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh: bool = False  # already query param; body adds new fields later
```

```python
# routes/slots.py — bodies
class CreateSlotBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    port: int | None = None
    # … full SlotConfig surface — populated from existing config.schema

class UpdateSlotConfigBody(BaseModel):
    """Partial — shallow merge over [model]/[server]/[npu]/[image]."""
    model_config = ConfigDict(extra="ignore")  # _reject_unknown_config_keys still validates sub-tables
    model: dict[str, Any] | None = None
    server: dict[str, Any] | None = None
    npu: dict[str, Any] | None = None
    image: dict[str, Any] | None = None
    port: int | None = None
    enabled: bool | None = None

class UpdateDefaultsBody(BaseModel):
    """Body keys merge into [model]."""
    model_config = ConfigDict(extra="forbid")
    # Re-validated by SlotConfig post-merge (already does)

class LoadSlotBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: str | None = None
    model: str | None = None  # symmetric with config schema
```

```python
# routes/comfyui.py — bodies (L477, L560)
class SwitchoverBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["generation", "inference"]
    force: bool = False
    pin: bool = False

class ModelsFetchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # _FetchBody already exists (L586/L591) — leave as-is
```

```python
# routes/benchmarks.py — bodies
class EnqueueBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    suite: str | None = None
    model: str | None = None
    kind: Literal["eval"] | None = None  # only "eval" supported (L414)

class ControlBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["start", "pause", "stop"]
```

### 3.3 Typed-error migration (replace HTTPException + hand-built JSONResponse)

`api/middleware/error_codes.py` already exposes `BadRequest` (400), `NotFound` (404), `Conflict` (409), `Hal0Error` (500 default). Middleware renders `{"error": {"code": …, "message": …, "details": …}}` — **byte-identical** to the hand-built shape; the dashboard sees zero behavioral change.

Full site-by-site map: see §1.3 (comfyui), §1.4 (benchmarks), §1.5 (chat_templates). All 10 `HTTPException` sites + all 8 typed `JSONResponse(status_code=…)` envelopes (2 of which to verify first — `comfyui.py:926/929`).

---

## 4. MCP admin auto-generation

### 4.1 What auto-generates

For every FastAPI `APIRoute` registered on `create_app().routes` (excluding `/mcp` JSON-RPC mounts, `/docs`/`/redoc`/`/openapi.json`, `/dashboard-plugins`, SPA-fallback catchalls), emit one `_REST_MAP` entry plus one `_PATH_ARGS` entry:

```python
# mcp/admin.py
def build_admin_route_map(
    app: FastAPI,
) -> tuple[dict[str, tuple[str, str]], dict[str, tuple[str, ...]]]:
    """Walk app.routes, build (method, path-template) → tool_name map.

    Tool-name derivation:
        route_id = "<method>:<path-template>"          # canonical, stable
        path_args = re.findall(r"\{([^}]+)\}", path_template)
    Returns:
        rest_map: {tool_name: (method, path_template)}
        path_args: {tool_name: tuple(path_arg_name, ...)}
    The route_id is the actual map key (stable across renames of tool_name);
    the human-facing tool_name is a derived alias (see TOOL_NAME_ALIASES).
    """
```

### 4.2 What stays hand-authored (security overlay)

- `AUTONOMOUS_READ_TOOLS`, `AUTONOMOUS_WRITE_TOOLS`, `GATED_TOOLS`, `PROBE_TOOLS` — tool policy (no FastAPI analog).
- `TOOL_PARAM_HINTS` — per-tool body-schema hints (overrides auto-gen for the hand-curated subset; anything in this dict wins over the auto-derived schema).
- `TOOL_DESCRIPTIONS` — `tools/list` human description.
- `_ANNOTATIONS` — `ToolAnnotations` (read-only, destructive, idempotent, open-world).
- `TOOL_NAME_ALIASES` (NEW) — back-compat: `{route_id: legacy_tool_name}` so the agent-chat's cached schemas don't break.

These have no FastAPI analog and stay hand-authored.

### 4.3 `_validate_catalog` adaptation

```python
def _validate_catalog() -> None:
    """Checks remain; targets update."""
    # 1. Security overlay is internally consistent (overlap check)
    # 2. Every classified tool name has a _route_id (i.e. exists in auto-gen map)
    # 3. Every _route_id-backed tool is classified (or in PROBE_TOOLS)
    # 4. _PATH_ARGS tuple matches {placeholder} extracted from path_template
```

The "classified but missing from `_REST_MAP`" check becomes "classified but no `_route_id` matches". The "unclassified but in `_REST_MAP`" check stays (it's the policy overlay talking).

### 4.4 Why not auto-generate the security overlay?

Tool classification reflects **agent policy**, not HTTP route semantics. A `GET /api/models/{id}` call can be a routine dashboard read (CLIENT) or a sensitive secret lookup (ADMIN). The exposure table (`security/exposure.py`) classifies by HTTP semantics (KB-1 §23.5 S9); the MCP table classifies by **agent intent**. Mapping the two would conflate "what auth does this need" with "what does this agent get to do" — and the plan's KB-1 architecture explicitly keeps them as separate tables.

### 4.5 Risk to agent chat (tool-name back-compat)

`tools/list` advertises the hand-curated tool names (`slot_load`, `model_assign`, `model_inspect`, …). Long-lived agent-chat sessions may have cached tool schemas — a name change breaks them silently. **Mitigation:** `TOOL_NAME_ALIASES = {"PUT:/api/slots/{name}/config": "slot_edit", "PUT:/api/slots/{name}/config": "model_assign", …}` resolves at `tools/list` time. New routes auto-register with route-derived names (e.g. `POST:/api/widgets/{id}/fetch`). Existing routes keep their alias. Retired when all aliases have route-derived equivalents verified by exposure-CI.

**Concrete collision found:** the current `_REST_MAP` has **two keys pointing at the same route** (`slot_edit` and `model_assign` both → `PUT /api/slots/{name}/config`). Auto-gen produces one `_route_id`; aliases give both names the same `_route_id` (clean — no ambiguity at dispatch time).

---

## 5. Edit plan (lanes + order)

Each step = one independently-shippable PR, green before the next. Lower-coupling first; risk-heavy last.

### 5.1 Service extraction (de-risk: easy first, dangerous last)

| Step | New module(s) | What moves from route | Verification gate |
|---|---|---|---|
| **1** | `registry/serialize.py` | `_model_to_dict`, `_lazy_quant`, `_pull_entry`, `_speed_for_entry`, `_eta_for_entry` from `models.py:945–1106` | `tests/api/test_models.py::test_*serialize*` (shape-equality snapshot) |
| **2** | `registry/normalize.py` | `_ALIAS_NAMES`, `_is_alias`, `_FLM_DISPATCH_TYPE`, `_MODALITY_TO_SLOT_TYPE`, `_dispatch_type`, `_comfyui_category` from `models.py:133–222` | `tests/api/test_models.py::test_alias_dispatch` + shape-snapshot |
| **3** | `registry/cascade.py` | `_slots_referencing_model`, `_clear_slot_default`, `_unload_slot_if_running`, `delete_model` body from `models.py:1318–1493` | `tests/api/test_models.py::test_delete_cascade` |
| **4** | `registry/add.py` | `add_model_from_path` body from `models.py:744–896` | `tests/api/test_models.py::test_add_from_path` |
| **5** | `registry/scan.py` | `scan_preview` + `scan_models` + `_commit_scan_rows` + `_suggest_id_from_path` from `models.py:444–741` | `tests/api/test_models.py::test_scan_*` |
| **6** | `registry/list.py` | `list_models` body from `models.py:224–416` (wait until ML-1 SqliteModelRegistry lands, then aggregator composition is stable) | `tests/api/test_models.py::test_list_models` |
| **7** | `registry/update.py`, `registry/inspect.py` | `update_model` body + `inspect_model` body from `models.py:1266–1316, 2191–2249` | `tests/api/test_models.py::test_update*`, `test_inspect*` |
| **8** | `registry/pull_jobs.py` | **the big one:** `_load_persisted_pull_job`, `_reconcile_persisted_pull_job`, `_resolve_pull_source`, `_resolve_pull_capability`, `_seed_registry_from_body`, `_resolve_pull_source_with_body`, `_schedule_pull_task`, `_run_pull_with_events`, `_emit_terminal_pull_event`, `_speed_bps`, `_eta_s`, `pull_model`, `_start_flm_pull`, `pull_status`, `pull_stream`, `pull_cancel`, `delete_pull`, `list_pulls` from `models.py:64–82, 1534–2267` | `tests/api/test_models.py::test_pull_full_lifecycle` |
| **9** | `slots/voices.py`, `slots/logs.py` | `get_slot_voices` body + `_is_log_noise`/`slot_logs` journalctl subprocess from `slots.py:1131–1164, 1417–1555` (SSE wrapper stays) | `tests/api/test_slots.py::test_voices_offline`, `test_logs_stream` |
| **10** | `slots/port_alloc.py` | `_slot_port_range`, `_collect_port_claims`, `_next_free_slot_port`, `_reject_port_conflict` from `slots.py:343–429` (flag for §11.2 PortAuthority merge) | `tests/api/test_slots.py::test_port_*` |
| **11** | `slots/metrics_collect.py` | `_tps_from_events`, `_per_slot_local_tps`, `_per_slot_ttft`, `_systemd_show`, `_scrape_llama_metrics`, `_docker_container_mem_bytes`, `_local_slot_metrics` from `slots.py:570–934` | `tests/slots/test_metrics_collect.py` NEW |
| **12** | `slots/flm_catalog.py`, `slots/image_pull.py` | `list_flm_models` body + `_run_image_pull`/`pull_slot_image`/`pull_slot_image_stream`/`pull_slot_image_status` from `slots.py:54–136, 1668–1888` | `tests/api/test_slots.py::test_flm_list`, `test_image_pull*` |

### 5.2 Typed-body migration (one PR per file)

| Step | File | Bodies | Constraint |
|---|---|---|---|
| **13** | `routes/models.py` | `ScanPreviewBody`, `ScanCommitBody`, `AddFromPathBody`, `CreateModelBody`, `UpdateModelBody`, `InspectBody`, `PullBody`, `UpdateFromHfBody` | Per-body, per-PR, gated on the service module the route delegates to (steps 1–8). `extra="forbid"` only where legacy `validation.unknown_keys` was active. |
| **14** | `routes/slots.py` | `CreateSlotBody`, `UpdateSlotConfigBody`, `UpdateDefaultsBody`, `LoadSlotBody` | `UpdateSlotConfigBody` keeps `extra="ignore"` — `_reject_unknown_config_keys` (L431) does sub-table validation |
| **15** | `routes/comfyui.py` | `SwitchoverBody`, `ModelsFetchBody` | `_FetchBody` exists (L586); don't touch |

### 5.3 Typed-error migration (mechanical; small batches)

| Step | File | Sites |
|---|---|---|
| **16** | `routes/benchmarks.py` + `routes/chat_templates.py` | 10 sites (8 + 2) |
| **17** | `routes/comfyui.py` | 6 `JSONResponse` envelopes (after verifying the 2 `_output_*` 404 sites at L926/929 can be safely converted) |

### 5.4 MCP admin auto-generation

| Step | Change |
|---|---|
| **18** | `mcp/admin.py::build_admin_route_map` + lifespan wiring (`install_admin_route_map(app)`) + `TOOL_NAME_ALIASES` populated with the 86 current `_REST_MAP` keys. `tests/mcp/test_admin_route_map.py` + `tests/mcp/test_validate_catalog.py` NEW. |

### 5.5 Net file sizes

| File | Before | After | Δ |
|---|---:|---:|---:|
| `routes/models.py` | 2,267 | ~450–550 | **−~1,750** |
| `routes/slots.py`  | 1,888 | ~700–800 | **−~1,100** |
| `routes/comfyui.py` | 951 | ~900 | −51 (errors → typed; bodies) |
| `routes/benchmarks.py` | 480 | ~470 | −10 (typed errors) |
| `routes/chat_templates.py` | 141 | ~140 | −1 |
| `registry/{list,scan,add,update,cascade,normalize,serialize,inspect,pull_jobs}.py` | 0 | **+~1,150** (NEW) | +1,150 |
| `slots/{metrics_collect,port_alloc,voices,logs,image_pull,flm_catalog}.py` | 0 | **+~620** (NEW) | +620 |
| `mcp/admin.py` | 1,684 | ~1,500 | −184 (auto-gen replaces hand-curated map; aliases/overlay stay) |
| **Total in scope** | **7,411** | **~4,810** | **−~2,600** extracted out of 3 god-route files |

---

## 6. Cross-lane coordination (must coordinate — do NOT design)

### 6.1 KB-1 auth (`security/exposure.py`) — S9 READ-ONLY

`security/exposure.py` matches on **path strings**. **No path changes from P3-routers** — body + extraction only. If a future cleanup PR consolidates `POST /api/models/{model_id}/pull/cancel` + `DELETE /api/models/pulls/{model_id}` (current `_REST_MAP` will flag both), it MUST update `RULES` and `tests/security/test_exposure.py` in the same diff. **Do not do that consolidation in this lane.**

### 6.2 P3-slots (`slots/manager.py`) — do NOT touch internals

P3-slots owns the `slots/manager.py` decomposition. P3-routers consumes its public methods (`list`, `create`, `update_config`, `delete`, `load`, `unload`, `restart`, `swap`, `status`, `iter_configs`, `get_config`, `state_stream`, `reconcile_unconfigured_slots`, `reconcile_npu_trio_slots`, `reconcile_container_upstreams`, `arbiter`, `start_idle_monitor`, `compute_config_drift`, `container_readiness_check`). All public names are guaranteed to survive as delegators per `spec-p3-slots.final.md` §5.

### 6.3 §11.2 PortAuthority — flag for future merge

`slots/port_alloc.py` (step 10) is **throwaway** — the §11.2 PortAuthority PR will absorb it. Don't invest in its API surface; keep the names + signatures as-is. Add a one-line module docstring: `# Extracted from routes/slots.py — to be merged into §11.2 PortAuthority.`

### 6.4 ML-1 SqliteModelRegistry (`registry/store.py`)

The §5 extraction order pulls `registry/list.py` last (step 6), AFTER the SqliteModelRegistry lands, because the aggregator calls `registry.get`/`has`/`list`-shaped methods whose exact surface depends on ML-1. The Protocol seam in §3.1 keeps the route layer resilient to ML-1 internals.

### 6.5 ML-3 model store (`config/store.py`)

`_resolve_pull_source_with_body` (now in `registry/pull_jobs.py`) reads from the resolver. Keep its seam stable — ML-3 owns the resolver; P3-routers owns the orchestration around it.

### 6.6 §7.6 request seam (per-request measurement)

The route shell emits a single `request_metric` call (gated on §13 OBS core). The service module is the natural place for the call — wrap `registry/pull_jobs.enqueue_hf` etc. with a request-seam decorator. **Don't add the decorator until §13 OBS core lands**; today the routes are pass-through.

### 6.7 §20 bench rework

Owns the deeper payload schemas for `benchmarks.evalrun` / `benchmarks.run` (file `hal0/bench/SPEC.md`). P3-routers ships minimal bodies (`EnqueueBody`, `ControlBody`) and defers the deeper bench body design. Coordinate before step 13 if §20 lands first.

### 6.8 §21.5 / `routes/v1.py` — already extended, do NOT touch

§21.5 owns `/v1/models`. No overlap with P3-routers.

---

## 7. Tests impact

### 7.1 NEW service-layer unit tests (enabled by Protocols)

- `tests/registry/test_pull_jobs.py` — enqueue / status / cancel / stream against fake `ModelRegistryLike` + fake `EventBusLike`. Replaces ~half of `tests/api/test_models.py::test_pull_*`.
- `tests/registry/test_cascade.py` — `delete_model` cascade: registry w/ model + 2 slot TOMLs + `SlotManager` (fake). Replaces `tests/api/test_models.py::test_delete_cascade`.
- `tests/registry/test_scan.py` — `preview` + `commit` against tmp model dir + fake registry.
- `tests/slots/test_metrics_collect.py` — `systemd_props` (fake `asyncio.create_subprocess_exec`), `container_mem_bytes` (cgroup fixture), `llama_metrics` (mocked httpx), `collect_local` integration w/ fake `SlotManager`.
- `tests/slots/test_port_alloc.py` — `next_free`, `reject_conflict` edge cases.
- `tests/mcp/test_admin_route_map.py` — fake `FastAPI` w/ representative route set; assert auto-gen map matches.
- `tests/mcp/test_validate_catalog.py` — assert `TOOL_DESCRIPTIONS` consistent w/ alias map + `AUTONOMOUS_*`/`GATED_*`/`PROBE_TOOLS`.

### 7.2 Existing tests — update or rely on re-exports

- `tests/api/test_models.py` — drop direct imports of `_run_pull_with_events`, `_seed_registry_from_body`, `_resolve_pull_source_with_body`, `_start_flm_pull`, `_load_persisted_pull_job`, `_reconcile_persisted_pull_job`, `_speed_for_entry`, `_eta_for_entry`, `_dispatch_type`, `_is_alias`, `_comfyui_category`, `_model_to_dict`, `_lazy_quant`, `_pull_entry`. Either re-export from `registry/` or update the test import path.
- `tests/api/test_slots.py` — drop direct imports of `_systemd_show`, `_docker_container_mem_bytes`, `_scrape_llama_metrics`, `_local_slot_metrics`, `_tps_from_events`, `_per_slot_local_tps`, `_per_slot_ttft`, `_slot_port_range`, `_collect_port_claims`, `_next_free_slot_port`, `_reject_port_conflict`. **Mitigation**: keep underscored-name aliases in `routes/slots.py` (`_systemd_show = metrics_collect.systemd_props`, …) so monkeypatching still works.
- `tests/api/test_comfyui.py` — convert HTTPException/JSONResponse assertions to typed-error envelope assertions (`{"error": {"code": …}}`).
- `tests/api/test_benchmarks.py` — same conversion.
- `tests/api/test_chat_templates.py` — same conversion.

### 7.3 Exposure-CI compatibility

`tests/security/test_exposure.py` walks `create_app().routes` and asserts each is classified. **Auto-generation changes nothing in the route table** (we add helpers, not routes) — the exposure table is unaffected. Confirm with a dry-run before step 18.

### 7.4 Snapshot tests for envelope shape

Per-route snapshot tests on the 38+ affected endpoints, captured before step 13 + before step 16/17. Envelopes must remain byte-identical (`{"error": {"code": …, "message": …, "details": …}}` for errors; body JSON shape for success).

---

## 8. Risks

1. **`registry/pull_jobs.py` is the largest single extraction** (~370 LOC). Mutates `request.app.state.model_pull_jobs` (a `dict[str, PullJob]`) and emits via `request.app.state.events`. Protocol keeps it unit-testable without a live FastAPI app, but `tests/api/test_models.py::test_pull_full_lifecycle` is the real gate — keep it green throughout step 8.
2. **Monkeypatching breaks when helpers move off-route.** Several tests patch `routes.slots._systemd_show` and friends. **Mitigation**: keep underscored-name aliases in `routes/slots.py` (e.g. `_systemd_show = metrics_collect.systemd_props`). Apply to every moved helper.
3. **Pydantic body rejection changes the wire contract.** `extra="forbid"` on `CreateModelBody` rejects keys the legacy dict-swallower silently dropped. The dashboard sends ~12 keys. **Verify** via `tests/api/test_models.py::test_create_with_legacy_fields` that the forbidden set is empty before step 13. If not, ship `extra="ignore"` for that body + a follow-up issue.
4. **Typed-error envelope shape must be byte-identical to the hand-built JSONResponse.** The error_codes middleware produces `{"error": {"code": …, "message": …, "details": …}}`; comfyui's hand-built envelopes already match this. Verify w/ snapshot test on `tests/api/test_comfyui.py` before/after step 17.
5. **MCP tool-name back-compat for agent chat.** Auto-gen changes how routes surface in `tools/list`. `TOOL_NAME_ALIASES` preserves names, but JSON-schemas may become richer (the real body schema vs. the hand-curated subset in `TOOL_PARAM_HINTS`). Mitigation: `TOOL_PARAM_HINTS[tool]` wins over the auto-derived schema.
6. **`slots/port_alloc.py` is throwaway.** Don't invest in its API surface; merge into §11.2 PortAuthority later.
7. **Pydantic v2 only.** Already on Pydantic v2 (per `pydantic-settings` imported in `api/auth.py`). Don't use v1 `Config` class — caught the deprecated import in the existing `_FetchBody` at `routes/comfyui.py:586`.
8. **Existing body parsers do partial validation that the Pydantic body may double-do.** Example: `_reject_unknown_config_keys` in `routes/slots.py:431` validates `SlotConfig` sub-tables; `UpdateSlotConfigBody` does **not** re-validate — keep `extra="ignore"` and let the existing validator continue.
9. **Hand-built `JSONResponse(404)` at `routes/comfyui.py:926/929`** look like errors but live inside `_output_image`/`_output_metadata` helpers — converting them to `NotFound` may break the streaming response path. **Verify before converting** (read the helper bodies during step 17).
10. **`_REST_MAP` collision (slot_edit vs model_assign).** Both keys → same route today. The auto-gen produces one `_route_id`; `TOOL_NAME_ALIASES` must register both names pointing to the same route so dispatch finds it (this is correct — they map to the same operation by agent convention).

---

## 9. Capped verification (what we run + how we cap)

Each PR lands with:
- `pytest tests/api/test_models.py tests/api/test_slots.py tests/api/test_comfyui.py tests/api/test_benchmarks.py tests/api/test_chat_templates.py tests/security/ tests/mcp/` — the touched-area suite (the existing ~120 fast tests; cap suite run at the touched-area subset, not the full repo).
- `pytest tests/slots/test_manager*.py tests/slots/test_*port*.py tests/slots/test_*metrics_collect*.py` — the integration gate that exercises `SlotManager` against the route layer (regression guard).
- `tests/security/test_exposure.py` — green (path names unchanged; auto-gen doesn't change routes).
- `check-sunset` — green.
- `scar_baseline.txt` — same-or-lower (this lane is net-positive LOC movement, not deletion, but the scar ratchet applies to "new hand-maintained catalogs" — auto-generated `_REST_MAP` is a net scar reduction).

No new live-LXC integration test (the `halo` LXC hasn't been stood up yet — see `hal0-rework-plan.md` §12). The `lxc105` box stays untouched. Migration verification (§23.3 P2-config 3-release window) is out of scope for this lane.

Definition of done (per `hal0-rework-plan.md` §24.5):
- `routes/models.py` ≤ 550 LOC, `routes/slots.py` ≤ 800 LOC.
- `await request.json()` count = 0 in `routes/` (was 38).
- `HTTPException` count = 0 in `routes/` (was 10 across benchmarks + chat_templates).
- Hand-built `JSONResponse(status_code=…)` for error envelopes = 0 in `routes/comfyui.py` (was 8, with status_code ∈ {404, 409, 422}; the 2 L926/929 verified-safe = 8 → 0; if the 2 stay = 6 → 0).
- `mcp/admin.py::_REST_MAP` + `_PATH_ARGS` regenerated at startup from `create_app().routes` via `build_admin_route_map(app)`; security overlay stays hand-authored.
- All existing `routes/{models,slots,comfyui,benchmarks,chat_templates}.py` callers keep the same JSON wire shape (snapshot tests on the 38+ affected endpoints).
- `security/exposure.py` RULES **unchanged** (no path renames); `tests/security/test_exposure.py` green.
- Public re-exports from `registry/__init__.py` + `slots/__init__.py` for every helper that moved off `routes/` — grep-verify every external caller.
- Tracker row flipped + changelog line; surface-impacts (`hal0-rework-surface-impacts.md`) addressed; cross-lane seam S9 (security/exposure.py) unchanged; merge to `rework/descar`.
- `check-sunset` green + scar baseline same-or-lower.

---

## 10. Out of scope (explicit non-goals)

- **`routes/v1.py`** — owned by §21.5; already extended.
- **`slots/manager.py` internals** — owned by P3-slots; do NOT touch.
- **`security/exposure.py`** — owned by KB-1; do NOT touch.
- **`registry/store.py` (SqliteModelRegistry)** — owned by ML-1; the Protocol seam in §3.1 makes the aggregator resilient to ML-1 changes.
- **Bench payload schemas beyond `EnqueueBody`/`ControlBody`** — owned by §20 bench rework.
- **Pydantic body migrations in other route files** — `routes/{auth,installer,updater,proxmox,memory,profiles,config,dashboard_layout,services,backends,settings,capabilities,stacks,board,board_chat,board_ws,agents,approvals,npu,health,events,activity,journal,logs,power,hardware,services_health,secrets,images,installer,openrouter,throughput,meta,activity}.py` are part of the broader `request.json` count but **not** the god-route files targeted by this spec. They get typed-body migrations in their owning lanes (or in-place when each lane lands — e.g. KB-1 auth owns `routes/auth.py`; installer owns `routes/installer.py`).
- **`POST /api/models/{model_id}/pull/cancel` + `DELETE /api/models/pulls/{model_id}` consolidation** — flagged in §6.1; own PR with `security/exposure.py` RULES update + `tests/security/test_exposure.py` coverage. NOT this lane.

---

## File referent list (absolute paths, for the implementer)

- Target routers: `/home/mint/hal0/src/hal0/api/routes/{models,slots,comfyui,benchmarks,chat_templates}.py`
- New service modules: `/home/mint/hal0/src/hal0/registry/{list,scan,add,update,cascade,normalize,serialize,inspect,pull_jobs}.py`
- New slots modules: `/home/mint/hal0/src/hal0/slots/{metrics_collect,port_alloc,voices,logs,image_pull,flm_catalog}.py`
- MCP: `/home/mint/hal0/src/hal0/mcp/admin.py`
- Existing collaborators (do not duplicate): `/home/mint/hal0/src/hal0/registry/{__init__,model,store,detect,discover,curated,pull,update_check}.py`, `/home/mint/hal0/src/hal0/upstreams/huggingface.py`, `/home/mint/hal0/src/hal0/slot_view/__init__.py`, `/home/mint/hal0/src/hal0/slots/{manager,capacity,metrics,reaper,watchdog,routing,config_write,profile_adopt,npu/trio,state,argv,arbiter,ttft_samples}.py`
- READ-ONLY contracts: `/home/mint/hal0/src/hal0/security/exposure.py` (S9), `/home/mint/hal0/src/hal0/api/middleware/error_codes.py` (`BadRequest`/`NotFound`/`Conflict`/`Hal0Error`), `/home/mint/hal0/src/hal0/slots/manager.py` (public methods — do not edit internals)
- Plan: `/home/mint/hal0-rework-plan.md` (§Phase 3 #4 L165–170, §23.2 S9 L1599, §23.5 L1657–1682, §24.2 W6 L1715, §24.3 spec-authoring backlog L1729)
- Existing related specs: `/home/mint/hal0-specs/spec-p3-slots.final.md` (delegation policy + re-export list), `/home/mint/hal0-specs/spec-ml1-sqlite.final.md` (registry backend timing), `/home/mint/hal0-specs/spec-kb1-auth.md` (S9 path classification)
- Tests: `/home/mint/hal0/tests/api/test_{models,slots,comfyui,benchmarks,chat_templates}.py`, `/home/mint/hal0/tests/security/test_exposure.py`, `/home/mint/hal0/tests/slots/` (35 files; see spec-p3-slots for the full list), `/home/mint/hal0/tests/mcp/` (will gain `test_admin_route_map.py` + `test_validate_catalog.py`)
