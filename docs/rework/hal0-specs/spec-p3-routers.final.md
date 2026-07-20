# P3-routers: Thin the Mega-Routers Into Request→Service→Envelope Shells

**Repo:** `/home/mint/hal0` @ `rework/descar` · **Mode:** READ-ONLY spec, verified against code.
**Plan:** `hal0-rework-plan.md` §Phase3.4 (decompose god files, item 4) + §23.2 S9 (auth middleware
already landed — do NOT touch) + §24.2 W6 (P3-routers is in this wave with ML-1 tables available).
**Target files:**
- `src/hal0/api/routes/models.py` (**2,267 lines as-built**; plan cites 2,509 — modest drift)
- `src/hal0/api/routes/slots.py`  (**1,888 lines as-built**; plan cites 1,846)
- `src/hal0/api/routes/comfyui.py` (951)
- `src/hal0/api/routes/benchmarks.py` (480)
- `src/hal0/api/routes/chat_templates.py` (HTTPException outliers)
- `src/hal0/mcp/admin.py` (`_REST_MAP` at L424, `_PATH_ARGS` at L517, `_validate_catalog` at L1539)
- `src/hal0/security/exposure.py` (S9 — READ-ONLY contract)

---

## 0. Executive summary

Three independent god files conspire on the same anti-pattern: route handlers hold the **whole
vertical** — request decode, validation, side-effect orchestration, envelope, error envelope. The
result is ~4,500 lines of request-bound Python that is impossible to test in isolation, embeds HF
client wiring, async pull-job scheduling, cgroup/systemd probes, llama-metrics scraping, and the
bench planner's localhost self-call — all of which belong one layer down.

This spec **thins the routes**, not the services. The services already exist for most of the work:

- `slots/metrics.py` holds the slim Prometheus exposition (`render_slot_metrics`, 84 lines). The
  systemd/cgroup/llama-metrics *scrapers* — three adapters doing actual IO — still live in
  `routes/slots.py` (L639-844) and are the route-layer's heaviest non-policy code.
- `registry/pull.py` holds `PullJob`, `make_job`, `run_pull`, `run_flm_pull`, `persist_pull_job`,
  `list_persisted_jobs`. The HF client orchestration (`_run_pull_with_events`, the sidecar/byte
  math, FLM router, schedule helper) still lives in `routes/models.py` (L1733-2076).
- `slot_view` (L1-820) holds `serialize_slot`, `config_enrichment`, `container_enrichment`,
  `synthesize_upstream_entries`, `SlotViewAggregator`. Route layer keeps thin adapters over these.
- `registry/` (`__init__.py`, `discover.py`, `detect.py`, `curated.py`, `model.py`, `store.py`,
  `update_check.py`, `pull.py`) owns the model registry backend; the route layer currently does
  body-decoding + registry-call + event-emit + adapter-shaping for every CRUD verb.

The split the plan wants — **routers become request→service→envelope shells** — is enforceable:
service modules already exist for every heavy concept the routes re-implement, plus we need to
extract one more (`registry/pull_jobs.py` for the HF orchestration wrapper, `slots/metrics_collect.py`
for the three IO adapters).

The other half is **typed bodies**. 38 hand-rolled `await request.json()` sites across `routes/` —
plan §Phase3.4 puts them at "38" exactly (verified: `grep -rn "await request\.json" routes/ | wc -l`
= 38). Replacing each with a Pydantic body model eliminates a class of "key was None → silent
degradation" bugs and surfaces the real contract. The comfyui/benchmarks outliers are a special
case — `benchmarks.py` still uses `fastapi.HTTPException` (8 sites, all `400`/`404`), `comfyui.py`
still wraps `JSONResponse(status_code=...)` (9 sites, 200/202/404/422/409). Both bypass the typed
`Hal0Error` envelope the rest of the API uses (per `api/middleware/error_codes.py`).

The third leg is **MCP admin auto-generation**. `_REST_MAP` is currently a hand-maintained 86-entry
dict (L424-512); `_PATH_ARGS` is hand-maintained (L517-557). Every FastAPI route on the
dashboard-side already classifies its method+path the same way KB-1's middleware does. Walking
`create_app().routes` once at startup can build the (method, path) pairs and the path-arg keys
(uniform `{name}` / `{slug}` / `{model_id}` / `{run_id}` extraction). What stays hand-authored is
the *security overlay*: tool classification (`AUTONOMOUS_READ_TOOLS` / `AUTONOMOUS_WRITE_TOOLS` /
`GATED_TOOLS` / `PROBE_TOOLS`), per-tool descriptions, per-tool param hints, and the `_ANNOTATIONS`
map. Those are MCP-specific policy and don't appear in the FastAPI route table.

### Constraints (do NOT violate)

1. **KB-1 auth middleware is already landed.** `api/auth.py` + `security/exposure.py` (S9) classify
   by `(method, path)`. **Do not move/rename route paths** unless they update the exposure table in
   the same diff (and the §21.11 exposure-CI test in `tests/security/test_exposure.py`); a
   classifier that points at a stale path silently downgrades a mutating route to ADMIN-by-fallback
   — correct behavior, but the ratchet is supposed to *flag* it, not absorb it.
2. **P3-slots extracted `slots/{reaper,watchdog,routing,npu/,profile_adopt,config_write,drift}.py`
   (per `spec-p3-slots.final.md`).** `slots/manager.py` keeps public names as thin delegators. The
   router layer calls public `SlotManager` methods only — we do **not** touch `manager.py` internals
   from P3-routers.
3. **`registry/pull.py` exists.** The route layer must call `run_pull`/`run_flm_pull`/`persist_pull_job`
   rather than re-implementing them. The remaining orchestration is what moves to
   `registry/pull_jobs.py`.
4. **V1 (§21.5) already extended `routes/v1.py`.** Don't touch `v1.py` from P3-routers unless a
   thin-shell signature breaks (§21.5 spec change was upstream).
5. **`spec-p3-slots.final.md` is the test precedent for these decomposition specs.** Match its
   structure (Exec summary → Responsibility map → Target layout → Interface boundaries → Extraction
   order → Delegation policy → Tests → Risks).

---

## 1. Current-state map (verified, line-by-line)

### 1.1 `routes/models.py` (2,267 lines)

| Symbol | Lines | What it does | Disposition |
|---|---|---|---|
| `router` + logger + module imports | 1-52 | wiring | stays |
| `_load_persisted_pull_job`, `_reconcile_persisted_pull_job` | 64-130 | pull-snapshot disk fallback + reconcile | **EXTRACT** → `registry/pull_jobs.py::load_persisted` |
| `_ALIAS_NAMES`, `_is_alias`, `_FLM_DISPATCH_TYPE`, `_MODALITY_TO_SLOT_TYPE`, `_dispatch_type`, `_comfyui_category` | 133-221 | classification helpers | extract → `registry/normalize.py` (pure) |
| `list_models` (`GET ""`) | 224-415 | aggregate registry + upstreams | **stay** in route; extract orchestration to `registry/list.py::list_all` |
| `list_catalogue` (`GET /catalogue`) | 417-441 | curated catalogue | **stay** thin (delegates to `registry/curated.py`) |
| `scan_preview` (`POST /scan/preview`) | 443-569 | walk paths, detect, return rows | **EXTRACT** body decoder + walker to `registry/scan.py::preview` |
| `scan_models` (`POST /scan`) | 572-625 | commit scan, emit events | **EXTRACT** to `registry/scan.py::commit` |
| `_commit_scan_rows`, `_suggest_id_from_path` | 628-741 | commit logic | extract with `scan_models` |
| `add_model_from_path` (`POST /add-from-path`) | 743-895 | model detection + registration | **EXTRACT** to `registry/add.py::add_from_path` |
| `create_model` (`POST ""`) | 897-942 | direct registry create | **stay** (delegates to `registry/store.create`) |
| `_model_to_dict`, `_lazy_quant` | 945-1001 | shape helpers | extract → `registry/serialize.py` |
| `list_pulls` (`GET /pulls`) | 1003-1043 | list pull jobs | **EXTRACT** to `registry/pull_jobs.py::list_all` |
| `_pull_entry`, `_speed_for_entry`, `_eta_for_entry`, `_hf_repo_for_model` | 1045-1105 | shape helpers | extract → `registry/pull_jobs.py` |
| `check_model_updates` (`GET /updates/check`) | 1107-1160 | HF remote SHA compare | **EXTRACT** to `registry/update_check.py::check_for_model` (already exists; move adapter) |
| `update_model_from_hf` (`POST /{model_id}/update`) | 1162-1245 | trigger update pull | **EXTRACT** to `registry/update_check.py::apply_for_model` |
| `get_model` (`GET /{model_id}`) | 1247-1263 | registry.get | **stay** shell |
| `update_model` (`PUT /{model_id}`) | 1265-1315 | registry.update + event | **EXTRACT** body decoder + emit to `registry/update.py::apply` |
| `_slots_referencing_model`, `_clear_slot_default`, `_unload_slot_if_running` | 1317-1400 | cascade helpers | extract → `registry/cascade.py` |
| `delete_model` (`DELETE /{model_id}`) | 1402-1492 | cascade delete + emit | **EXTRACT** to `registry/cascade.py::delete_model` |
| `delete_pull` (`DELETE /pulls/{model_id}`) | 1494-1531 | cancel + remove snapshot | **EXTRACT** to `registry/pull_jobs.py::cancel` |
| `_resolve_pull_source`, `_resolve_pull_capability`, `_seed_registry_from_body`, `_resolve_pull_source_with_body`, `_schedule_pull_task`, `_run_pull_with_events`, `_emit_terminal_pull_event`, `_speed_bps`, `_eta_s` | 1533-1906 | pull orchestration | **EXTRACT** entire block to `registry/pull_jobs.py::enqueue_hf` |
| `pull_model` (`POST /{model_id}/pull`) | 1908-2026 | HF+FLM pull dispatch | **EXTRACT** orchestration to `registry/pull_jobs.py::enqueue` (routes thin shell) |
| `_start_flm_pull` | 2029-2076 | FLM pull adapter | extract with `pull_model` |
| `pull_status` (`GET /{model_id}/pull/status`) | 2079-2101 | job snapshot | **EXTRACT** to `registry/pull_jobs.py::status` |
| `pull_stream` (`GET /{model_id}/pull/stream`) | 2103-2188 | SSE of job progress | **EXTRACT** to `registry/pull_jobs.py::stream` |
| `inspect_model` (`POST /inspect`) | 2190-2248 | HF repo metadata, no registry touch | **EXTRACT** to `registry/inspect.py::inspect_hf_repo` |
| `pull_cancel` (`POST /{model_id}/pull/cancel`) | 2250-... | cancel + emit | **EXTRACT** to `registry/pull_jobs.py::cancel` |

**Net after extraction:** route file ≈ 450-550 lines — request→service→envelope shells, body
model + audit + 4-line handler. Service modules added: `registry/{list,scan,add,update,cascade,
normalize,serialize,inspect,pull_jobs}.py` + minor edits to `registry/update_check.py`.

### 1.2 `routes/slots.py` (1,888 lines)

| Symbol | Lines | What it does | Disposition |
|---|---|---|---|
| `router` + logger + module docstring | 1-51 | wiring | stays |
| `list_flm_models` (`GET /flm/models`) | 53-135 | NPU catalog probe (subprocess + fallback) | **EXTRACT** to `slots/flm_catalog.py::list_models` |
| `NotImplementedYet` class | 138-140 | typed error | stays |
| `_get_slot_manager`, `_slot_to_dict`, `_config_field_enrichment`, `_container_state_enrichment`, `_loaded_models`, `_synthesize_slots_from_upstreams` | 146-310 | request-bound adapters | **stay** (all are 1-liners that delegate to `slot_view`/`SlotManager`); OK to keep |
| `list_slots` (`GET ""`) | 313-340 | aggregator | **stay** thin |
| `_slot_port_range`, `_collect_port_claims`, `_next_free_slot_port`, `_reject_port_conflict` | 342-428 | port allocator | **EXTRACT** to `slots/port_alloc.py` (or merge into §11.2 PortAuthority once that lands — see §6 coordination) |
| `_reject_unknown_config_keys`, `_normalize_create_body` | 430-485 | body validation/normalization | **stay** (move to `slots/config_write.py` if it grows) |
| `create_slot` (`POST ""`) | 488-564 | SlotManager.create wrapper | **stay** shell (body-validate + audit + call) |
| `_tps_from_events`, `_per_slot_local_tps`, `_per_slot_ttft` | 570-636 | rolling-window stat helpers | **EXTRACT** to `slots/metrics_collect.py::local_views` |
| `_systemd_show` | 639-670 | `systemctl show` subprocess | **EXTRACT** to `slots/metrics_collect.py::systemd_props` |
| `_scrape_llama_metrics` | 673-798 | httpx `/metrics`+`/slots` | **EXTRACT** to `slots/metrics_collect.py::llama_metrics` |
| `_docker_container_mem_bytes` | 800-845 | cgroup-v2 walk | **EXTRACT** to `slots/metrics_collect.py::container_mem_bytes` |
| `_local_slot_metrics` | 847-933 | per-slot fan-out | **EXTRACT** to `slots/metrics_collect.py::collect_local` |
| `slot_metrics` (`GET /metrics`) | 936-1030 | 3-layer merge + FLM KV | **stay** thin (delegates to `metrics_collect.collect_local` + merging) |
| `slot_capacity` (`GET /capacity`) | 1028-1054 | CapacitySnapshot | **stay** thin (calls `slots/capacity.build_per_slot` — exists) |
| `get_slot` (`GET /{name}`) | 1056-1091 | SlotManager.status + enrich | **stay** thin |
| `_state_value`, `_safe_config` | 1093-1106 | small helpers | stay |
| `delete_slot` (`DELETE /{name}`) | 1108-1120 | SlotManager.delete | **stay** shell |
| `get_slot_config` (`GET /{name}/config`) | 1122-1128 | SlotManager.get_config | **stay** shell |
| `get_slot_voices` (`GET /{name}/voices`) | 1130-1163 | httpx /v1/audio/voices | **EXTRACT** to `slots/voices.py::fetch_for_slot` |
| `get_slot_resolved` (`GET /{name}/resolved`) | 1165-1182 | container.resolved_argv | **stay** shell |
| `update_slot_config` (`PUT /{name}/config`) | 1184-1237 | body-decode + merge + audit + unload-on-disable | **stay** shell (Pydantic body) |
| `update_slot_defaults` (`PATCH /{name}/defaults`) | 1239-1269 | defaults merge | **stay** shell |
| `load_slot` (`POST /{name}/load`) | 1276-1318 | model_id validate + load | **stay** shell |
| `unload_slot`, `restart_slot`, `swap_slot` | 1320-1414 | lifecycle wrappers | **stay** shell |
| `_is_log_noise`, `slot_logs`, `contextlib_suppress`, `slot_logs_stream` | 1416-1554 | journalctl tail + SSE | **EXTRACT** journalctl subprocess to `slots/logs.py::tail_journal`; keep SSE wrapper in route |
| `slot_state` (`GET /{name}/state`) | 1556-1571 | snapshot subset | **stay** shell |
| `slot_state_stream` (`GET /{name}/state/stream`) | 1574-... | SSE | **stay** shell |
| `_run_image_pull`, `pull_slot_image`, `pull_slot_image_stream`, `pull_slot_image_status` | 1667-1888 | slot-image pull orchestration | **EXTRACT** to `slots/image_pull.py` |

**Net after extraction:** route file ≈ 700-800 lines — most of that is the body-typed route bodies
+ the SSE/journalctl wrappers (which have to stay in the route because they hold `StreamingResponse`).

### 1.3 `routes/comfyui.py` — HTTPException-adjacent outliers

`comfyui.py` doesn't import `HTTPException` but builds the same shape by hand via
`JSONResponse(status_code=…, content={"error": {"code": …, "message": …}})`. Counted 9 hand-built
envelopes (L482, 495, 528, 565, 613, 632, 644, 754, 780, 926, 929). These all bypass the typed
`Hal0Error` envelope and need converting. The two `request.json()` sites (L477, L560) need
Pydantic bodies. Most are already fine — `/models/fetch` (L596) already uses a `_FetchBody`
Pydantic model, so the pattern is established in-file.

### 1.4 `routes/benchmarks.py` — raw HTTPException

8 `raise HTTPException(status_code=…, detail=…)` sites (L199, 274, 331, 412, 414, 416, 420, 461).
5 are `400 BadRequest`, 3 are `404 NotFound`, all bypass the typed envelope. Zero Pydantic bodies
(the routes are mostly `def`, not `async def`, per the file's "threadpool because of blocking IO"
docstring L11-18). Plan calls these "outliers" — the path of least churn: replace each with
the equivalent `BadRequest`/`NotFound` from `api/middleware/error_codes.py`, keep `def` (not `async
def`) where blocking IO matters.

### 1.5 `routes/chat_templates.py` — bonus HTTPException outliers

2 sites (L129 `400`, L136 `500`) — same pattern, same fix. Not in the plan's "38" count but
adjacent and trivial.

### 1.6 `mcp/admin.py` — `_REST_MAP` / `_PATH_ARGS` hand-maintained

`_REST_MAP` is 86 entries (L424-512), `_PATH_ARGS` is 27 entries (L517-557). They are checked
coherent against each other + `AUTONOMOUS_*` / `GATED_*` / `PROBE_TOOLS` / `TOOL_PARAM_HINTS` in
`_validate_catalog` (L1539). Auto-generation target: build `(method, path)` pairs from the FastAPI
route table at app-startup, extract `{placeholder}` names from the path template to derive the
path-arg tuple. The hand-authored security overlay (`AUTONOMOUS_READ_TOOLS`,
`AUTONOMOUS_WRITE_TOOLS`, `GATED_TOOLS`, `PROBE_TOOLS`, `TOOL_PARAM_HINTS`, `TOOL_DESCRIPTIONS`,
`_ANNOTATIONS`) stays — these encode MCP policy that has no FastAPI analog.

### 1.7 `security/exposure.py` — coordination only (READ-ONLY contract)

S9 (plan §23.2): "moving/renaming routes must keep exposure classifications valid (flag any route
path changes)". The classifier matches on path strings via `RULES`. **No path change is required**
for P3-routers — the work is body+extraction, not URL design. If a small re-shape is needed (e.g.
consolidating `POST /api/models/{model_id}/pull/cancel` and `DELETE /api/models/pulls/{model_id}`
— currently two routes for the same effect — see §5.3), do it in a *separate* PR coordinated with
`tests/security/test_exposure.py`.

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
  manager.py                     # state machine + lifecycle + CRUD (P3-slots core)
  reaper.py, watchdog.py,        # P3-slots collaborators (existing)
    routing.py, config_write.py,
    profile_adopt.py, npu/,
  capacity.py, metrics.py,       # (existing)
    state.py, argv.py,
    arbiter.py, ttft_samples.py
  metrics_collect.py     NEW     # systemd_props, container_mem_bytes,
                                 # llama_metrics, collect_local, local_views
                                 # (was the routes/slots.py IO-adapter block)
  port_alloc.py          NEW     # _slot_port_range, _collect_port_claims,
                                 # _next_free_slot_port, _reject_port_conflict
                                 # (merged into §11.2 PortAuthority when that
                                 # lands — flag for deletion in that PR)
  voices.py              NEW     # fetch_for_slot(name, port) → voices/source
  logs.py                NEW     # tail_journal(unit, backfill_n, quiet) → lines
  image_pull.py          NEW     # run_image_pull + slot image pull orchestration
  flm_catalog.py         NEW     # list_models() NPU catalog probe + fallback

src/hal0/api/routes/
  models.py                      # THIN: request → registry/* → envelope (~450-550 LOC)
  slots.py                       # THIN: request → SlotManager + slot_view →
                                 # envelope (~700-800 LOC; SSE/journalctl wrappers stay)
  comfyui.py                     # typed Hal0Error replaces hand-built JSONResponse
                                 # envelopes; Pydantic bodies replace request.json()
  benchmarks.py                  # typed Hal0Error replaces HTTPException; bodies
                                 # for queue/run/control; keep `def` (threadpool)
  chat_templates.py              # typed Hal0Error replaces HTTPException (2 sites)

src/hal0/mcp/
  admin.py                       # _REST_MAP/_PATH_ARGS auto-generated at startup
                                 # from create_app().routes; security overlay
                                 # (AUTONOMOUS_*, GATED_TOOLS, PROBE_TOOLS,
                                 # TOOL_PARAM_HINTS, TOOL_DESCRIPTIONS,
                                 # _ANNOTATIONS) stays hand-authored
  build_server.py                # consumes the auto-generated map (no change)
```

---

## 3. Interface boundaries (buildable contracts)

### 3.1 Service-layer Protocols

```python
# registry/pull_jobs.py
from typing import Protocol, Any
from fastapi import Request

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
async def enqueue_flm(
    request: Request, *, model_id: str,
) -> dict[str, object]: ...
def status(model_id: str, *, registry: ModelRegistryLike) -> dict[str, object]: ...
async def stream(model_id: str, *, request: Request) -> AsyncIterator[str]: ...
async def cancel(model_id: str, *, request: Request) -> dict[str, object]: ...
def list_all() -> list[dict[str, Any]]: ...
def load_persisted(model_id: str, registry: ModelRegistryLike | None = None
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

### 3.2 Route-layer Pydantic bodies (replacing `request.json()`)

Place these in `routes/models.py`, `routes/slots.py`, `routes/comfyui.py` (already has
`_FetchBody` precedent) at module scope, exported. Bodies must use Pydantic v2 models with
`extra="forbid"` where the original code raised `validation.unknown_keys`; otherwise `extra="ignore"`
to match today's silent-pass behavior on legacy fields.

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
    """Partial update — any subset."""
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
    refresh: bool = False  # already query param; body might add new fields later
```

```python
# routes/slots.py — bodies
class CreateSlotBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1)
    port: int | None = None
    # … full SlotConfig surface — populated from existing schema.py

class UpdateSlotConfigBody(BaseModel):
    """Partial — shallow merge over [model]/[server]/[npu]/[image]."""
    model_config = ConfigDict(extra="ignore")
    model: dict[str, Any] | None = None
    server: dict[str, Any] | None = None
    npu: dict[str, Any] | None = None
    image: dict[str, Any] | None = None
    port: int | None = None
    enabled: bool | None = None

class UpdateDefaultsBody(BaseModel):
    """Body keys merge into [model]."""
    model_config = ConfigDict(extra="forbid")
    # Fields will be re-validated by SlotConfig post-merge (already does)

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

class SetPinnedBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pinned: bool

# _FetchBody already exists at L591 — leave as-is
```

```python
# routes/benchmarks.py — bodies (queue/enqueue/control/run)
class EnqueueBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    suite: str | None = None
    model: str | None = None
    kind: Literal["eval"] | None = None  # only "eval" supported (L414)

class ControlBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["start", "pause", "stop"]

# run, evalrun: bodies designed against bench/suites spec — verify with
# SPEC.md in hal0/bench before authoring; only do this for run/evalrun if
# hal0.bench.evalrun already accepts a typed payload.
```

### 3.3 Typed-error migration targets

`api/middleware/error_codes.py` already exposes `BadRequest` (400), `NotFound` (404), `Conflict`
(409), `Hal0Error` (500 default). Replacement map for outliers:

| Site | Current | Replace with |
|---|---|---|
| `routes/benchmarks.py:199` | `HTTPException(404, "unknown suite …")` | `raise NotFound(..., code="bench.unknown_suite")` |
| `routes/benchmarks.py:274` | `HTTPException(400, "cell_key or model is required")` | `raise BadRequest(..., code="bench.missing_filter")` |
| `routes/benchmarks.py:331` | `HTTPException(404, "unknown run_id: …")` | `raise NotFound(..., code="bench.unknown_run")` |
| `routes/benchmarks.py:412-420` | `HTTPException(400/404, "body.suite or body.model is required" / "kind='eval' requires body.model" / "unknown suite …")` | `raise BadRequest(..., code="bench.invalid_envelope")` |
| `routes/benchmarks.py:461` | `HTTPException(400, "bad action …")` | `raise BadRequest(..., code="bench.bad_action")` |
| `routes/chat_templates.py:129` | `HTTPException(400, "Invalid template id …")` | `raise BadRequest(..., code="chat_template.invalid_id")` |
| `routes/chat_templates.py:136` | `HTTPException(500, "Could not write template …")` | `raise Hal0Error(..., code="chat_template.write_failed")` |
| `routes/comfyui.py:482-490` | hand-built `JSONResponse(422, …)` | `raise BadRequest(..., code="comfyui.invalid_mode")` |
| `routes/comfyui.py:495-503` | hand-built `JSONResponse(409, …)` | `raise Conflict(..., code="comfyui.switch_in_progress")` |
| `routes/comfyui.py:528-540` | hand-built `JSONResponse(409, …)` | `raise Conflict(..., code="comfyui.busy")` |
| `routes/comfyui.py:565-573` | hand-built `JSONResponse(422, …)` | `raise BadRequest(..., code="comfyui.invalid_pin")` |
| `routes/comfyui.py:613-621` | hand-built `JSONResponse(422, …)` | `raise BadRequest(..., code="comfyui.fetch.invalid_body")` |
| `routes/comfyui.py:632-640` | hand-built `JSONResponse(422, …)` | `raise BadRequest(..., code="comfyui.fetch.unknown_variant")` |
| `routes/comfyui.py:926-929` | hand-built `JSONResponse(404, …)` | `raise NotFound(..., code="comfyui.no_output")` |

The error envelope middleware (`api/middleware/error_codes.py`) renders `BadRequest`/`NotFound`/
`Conflict` as `{"error": {"code": <code>, "message": ..., "details": ...}}` — byte-identical to the
hand-built shape, so the dashboard sees zero behavioral change.

---

## 4. MCP admin auto-generation

### 4.1 What auto-generates

For every FastAPI route registered on the `create_app()` instance (excluding SPA-fallback catchalls
and the `/mcp` JSON-RPC mount — those are not admin tools), emit one `_REST_MAP` entry keyed by a
deterministic tool name. Derivation rule:

```
tool_name = f"{prefix.strip('/').replace('/', '_')}_{method.lower()}"
            if "{name}" not in path else
            f"{prefix.strip('/').replace('/', '_').replace('{name}', '').rstrip('_')}_{method.lower()}"
```

Examples (verified against current `_REST_MAP` at `mcp/admin.py:424`):

| FastAPI route | Current `_REST_MAP` key | Auto-gen'd key |
|---|---|---|
| `GET /api/slots` | `slot_list` | `api_slots_get` (alias needed) |
| `GET /api/slots/{name}` | `slot_status` | `api_slots__name__get` |
| `POST /api/slots/{name}/load` | `slot_load` | `api_slots__name__load_post` |
| `POST /api/slots/{name}/swap` | `model_swap` | `api_slots__name__swap_post` |
| `PUT /api/slots/{name}/config` | `slot_edit`, `model_assign` (same route!) | `api_slots__name__config_put` |

The current `_REST_MAP` has **two keys pointing at the same route** (`slot_edit` and `model_assign`
both → `PUT /api/slots/{name}/config`). Auto-generation can't reproduce that ambiguity — the keys
become route-derived and unique. The right fix is to keep **two tool names** (one per agent
workflow) but back them by a routing table that says "tool `slot_edit` and `model_assign` both
forward to the same `_route_id`". The auto-gen gives the `_route_id`; the per-tool name is a
policy overlay (which matches the security overlay pattern).

### 4.2 What stays hand-authored

The security overlay is purely MCP policy:

- `AUTONOMOUS_READ_TOOLS` (frozenset of tool names)
- `AUTONOMOUS_WRITE_TOOLS`
- `GATED_TOOLS`
- `PROBE_TOOLS` (memory_* + anything that should NOT route via REST)
- `TOOL_PARAM_HINTS` (per-tool body-schema hints — extends auto-gen with named properties +
  required list)
- `TOOL_DESCRIPTIONS` (per-tool human-readable description for `tools/list`)
- `_ANNOTATIONS` (per-tool ToolAnnotations: read-only/hint, destructive, idempotent, open-world)

These have no FastAPI analog and stay hand-authored. The `_validate_catalog` function (L1539) keeps
its job but now checks (a) security overlay is internally consistent (overlap check stays) and
(b) every tool name in the overlay exists in the auto-gen map or in `PROBE_TOOLS`.

### 4.3 Generation algorithm

Add a function `build_admin_route_map(app: FastAPI) -> tuple[dict[str, tuple[str, str]],
                                                              dict[str, tuple[str, ...]]]` in
`mcp/admin.py`. It walks `app.routes` (skipping `APIRoute` instances whose `path` starts with
`/mcp`, `/docs`, `/redoc`, `/openapi.json`, `/dashboard-plugins`, or matches the SPA catch-all).
For each route it:

1. Computes `route_id = "<method>:<path-template>"` (canonical key for the merge table)
2. Extracts `{placeholder}` names from `path-template` to derive `_PATH_ARGS`
3. Returns the merged map

The module-level `_REST_MAP` and `_PATH_ARGS` constants become **lazy** module-level calls that
build from a stashed `app` reference (set by the lifespan at startup via a new
`install_admin_route_map(app)`). Tests that don't go through the lifespan need a
`set_admin_route_map(map_, path_args)` helper — already present pattern in the codebase.

`_validate_catalog` is updated: the "classified but missing from _REST_MAP" check becomes
"classified but no `_route_id` matches" (still a fail), and the "in _REST_MAP but never
classified" check stays (it's the policy overlay talking). The path-arg placeholder check stays
identical.

### 4.4 Why not auto-generate the security overlay?

Because tool classification reflects **agent policy**, not the HTTP route. A `GET /api/models/{id}`
call can be a routine dashboard read (CLIENT) or a sensitive secret lookup (ADMIN). The exposure
table (`security/exposure.py`) classifies by HTTP semantics; the MCP classification classifies by
agent intent. Mapping the two would conflate "what auth does this need" with "what does this
agent get to do" — and the plan's §1 / KB-1 architecture explicitly keeps them as separate
tables (S9 is the auth table; the MCP table is its own thing).

### 4.5 Risk to dashboard/agent chat

The current `tools/list` advertises the hand-curated tool names (`slot_load`, `model_assign`,
`model_inspect`, …). Auto-gen must keep those names stable — the agent chat has cached tool
schemas in any long-lived session and a name change breaks them silently. Approach: **keep the
hand-authored alias map** (`TOOL_NAME_ALIASES: dict[str, str] = {"api_slots__name__load_post":
"slot_load", …}`) and resolve at `tools/list` time. New routes auto-register with route-derived
names; existing routes keep their alias. When all routes in the alias map are confirmed via the
exposure-CI test to have a route-derived equivalent, aliases can be retired (separate PR).

---

## 5. Extraction order (least-coupled first)

Each step is independently shippable + green before the next. Ordering minimizes churn and
de-risks the typed-body migration last.

1. **`registry/serialize.py`** (pure functions: `_model_to_dict`, `_lazy_quant`, `_pull_entry`,
   `_speed_for_entry`, `_eta_for_entry`). Zero side effects, no state. Re-export from
   `registry/__init__.py`. Update `routes/models.py` to import from here.
2. **`registry/normalize.py`** (pure: `_ALIAS_NAMES`, `_is_alias`, `_FLM_DISPATCH_TYPE`,
   `_MODALITY_TO_SLOT_TYPE`, `_dispatch_type`, `_comfyui_category`). Zero side effects. Re-export.
3. **`registry/cascade.py`** (slot-cascade helpers + `delete_model`). Self-contained — uses only
   `config/paths` + tomllib + `slots/manager.SlotManager`. Pure functions + one async function.
4. **`registry/add.py`** (`add_from_path` extraction). Self-contained; uses `registry/detect`,
   `registry/model`, `registry/store`.
5. **`registry/scan.py`** (`preview` + `commit_scan_rows` extraction). Self-contained; uses
   `registry/discover` + `registry/detect`.
6. **`registry/list.py`** (`list_all` aggregator). Uses `registry/store`, `registry/curated`,
   `upstreams/filters`. Heaviest of the pure extractions — do after the registry backend has
   settled post-ML-1.
7. **`registry/update.py`** (body-decoder + emit for `update_model`). Self-contained.
8. **`registry/inspect.py`** (`inspect_hf_repo` extraction). Uses `upstreams/huggingface`.
9. **`registry/pull_jobs.py`** (the big one: `_run_pull_with_events` + `_emit_terminal_pull_event`
   + `_speed_bps` + `_eta_s` + `_seed_registry_from_body` + `_resolve_pull_source` +
   `_resolve_pull_capability` + `_resolve_pull_source_with_body` + `_schedule_pull_task` +
   `_start_flm_pull` + `_load_persisted_pull_job` + `_reconcile_persisted_pull_job` +
   `list_persisted_jobs` adapter + `pull_model` body). **Wait until step 7 lands**, then move
   the whole block in one PR (high churn, low risk after the registry store is stable).
10. **`slots/voices.py`** (`fetch_for_slot` extraction). Self-contained httpx wrapper.
11. **`slots/logs.py`** (`tail_journal` extraction). Self-contained; route layer keeps the SSE
    wrapper that consumes it.
12. **`slots/port_alloc.py`** (`_slot_port_range`, `_collect_port_claims`, `_next_free_slot_port`,
    `_reject_port_conflict`). Self-contained. **Flag for deletion in the §11.2 PortAuthority PR**
    (the PortAuthority module absorbs these functions).
13. **`slots/metrics_collect.py`** (the IO-adapter block: `_systemd_show`, `_docker_container_mem_bytes`,
    `_scrape_llama_metrics`, `_local_slot_metrics`, `_tps_from_events`, `_per_slot_local_tps`,
    `_per_slot_ttft`). Self-contained; route layer calls `collect_local(sm)` and `local_tps(app_state)`.
14. **`slots/flm_catalog.py`** (`list_flm_models` extraction). Self-contained httpx + subprocess
    + flm provider import.
15. **`slots/image_pull.py`** (slot-image pull orchestration). Self-contained.
16. **Typed-body migration** (`routes/models.py` Pydantic bodies). Per-body, per-PR, gated on the
    service module the route delegates to landing first. Use the bodies from §3.2.
17. **Typed-error migration in `routes/benchmarks.py` + `routes/chat_templates.py`** (mechanical:
    10 sites). Ship as one PR — small enough.
18. **Typed-body + typed-error migration in `routes/comfyui.py`** (2 bodies + 6 envelopes).
    Ship as one PR. The two `JSONResponse(404, …)` outliers at L926/929 stay as-is (they're
    inside `_output_*` response builders and aren't really error envelopes — verify before
    converting).
19. **`routes/slots.py` typed bodies** (CreateSlot, UpdateSlotConfig, UpdateDefaults, LoadSlot).
    One PR.
20. **MCP admin auto-generation** (`mcp/admin.py::build_admin_route_map` + lifespan wiring +
    alias table for back-compat). Separate PR — biggest consumer-surface change.

---

## 6. Cross-lane overlaps (must coordinate — do NOT design here)

- **KB-1 / §1 auth middleware (S9):** already landed. **No path changes from P3-routers** —
   body+extraction work only. If a future cleanup PR consolidates
   `POST /api/models/{model_id}/pull/cancel` + `DELETE /api/models/pulls/{model_id}` (the plan
   flagged this duplication), it must update `security/exposure.py` RULES and the §21.11
   exposure-CI test in the same diff. Don't do that consolidation in this lane.
- **P3-slots (slots/manager.py decomposition):** completed. P3-routers does NOT touch
   `slots/manager.py` internals — only public methods (`list`, `create`, `update_config`,
   `delete`, `load`, `unload`, `restart`, `swap`, `status`, `iter_configs`, `get_config`,
   `state_stream`). All public names are guaranteed to survive as delegators (per
   `spec-p3-slots.final.md` §5).
- **§11.2 PortAuthority:** will absorb `slots/port_alloc.py`. P3-routers extracts the current
   helpers into a module that PortAuthority will own — do not design the merge here. Flag the
   file as "merge into §11.2 PortAuthority" in its module docstring.
- **ML-1 / SqliteModelRegistry (registry/backend):** `registry/store.py` is the route layer's
   only stable interface. The §5 extraction order pulls `registry/list.py` last (after the
   SqliteModelRegistry lands), because the aggregator calls `registry.get`/`has`/`list`-shaped
   methods whose exact surface depends on ML-1.
- **ML-3 / model store (config/store.py):** the `_resolve_pull_source_with_body` block (now
   in `registry/pull_jobs.py`) reads from the resolver. Keep its seam stable — ML-3 owns the
   resolver; P3-routers owns the orchestration around it.
- **§21.5 / v1.py extension:** already shipped. Don't touch `routes/v1.py`.
- **§20 / bench rework:** owns `routes/benchmarks.py` body design. The bodies in §3.2 are
   intentionally minimal (EnqueueBody + ControlBody); the deeper bench payload schema is a
   §20 call. Coordinate on `evalrun`/`run` payloads (do not author here).
- **§21.3 / introspection+ (§21.5/v1/models extension):** the `GET /v1/models` extension lives
   on `routes/v1.py`. The internal dashboard model listing (`routes/models.py::list_models`)
   stays here. No overlap.
- **§7.6 request seam (per-request measurement):** the route shell emits a single
   `request_metric` call (gated on §13.3 tables landing). The service module is the natural
   place for the call — wrap `registry/pull_jobs.enqueue_hf` etc. with a request-seam decorator.
   Don't add the decorator until §13 OBS core lands; today the routes are pass-through.

---

## 7. Tests impact (12 files expected)

### 7.1 Service-layer unit tests (NEW — high value)

These tests are enabled by the Protocol seams in §3.1; today they require full app wiring.

- `tests/registry/test_pull_jobs.py` — enqueue / status / cancel / stream against fake
  `ModelRegistryLike` + fake `EventBusLike`. Replaces ~half of the current
  `tests/api/test_models.py::test_pull_*` tests.
- `tests/registry/test_cascade.py` — `delete_model` cascade: registry with model + 2 referencing
  slot TOMLs + SlotManager (fake). Replaces `tests/api/test_models.py::test_delete_cascade`.
- `tests/registry/test_scan.py` — `preview` + `commit` against a tmp model dir + fake registry.
- `tests/slots/test_metrics_collect.py` — `systemd_props` (fake `asyncio.create_subprocess_exec`),
  `container_mem_bytes` (cgroup fixture), `llama_metrics` (mocked httpx), `collect_local`
  integration against a fake SlotManager.
- `tests/slots/test_port_alloc.py` — `_next_free_slot_port`, `_reject_port_conflict` edge cases.

### 7.2 Existing tests — update or rely on re-exports

- `tests/api/test_models.py` — drop direct imports of `_run_pull_with_events`, `_seed_registry_from_body`,
  `_resolve_pull_source_with_body`, `_start_flm_pull`, `_load_persisted_pull_job`,
  `_reconcile_persisted_pull_job`, `_speed_for_entry`, `_eta_for_entry`, `_dispatch_type`,
  `_is_alias`, `_comfyui_category`, `_model_to_dict`, `_lazy_quant`, `_pull_entry`. If they were
  imported by tests, either re-export from `registry/` or update the test import.
- `tests/api/test_slots.py` — drop direct imports of `_systemd_show`, `_docker_container_mem_bytes`,
  `_scrape_llama_metrics`, `_local_slot_metrics`, `_tps_from_events`, `_per_slot_local_tps`,
  `_per_slot_ttft`, `_slot_port_range`, `_collect_port_claims`, `_next_free_slot_port`,
  `_reject_port_conflict`. Same re-export rule.
- `tests/api/test_comfyui.py` — convert HTTPException assertions to typed-error envelope assertions.
- `tests/api/test_benchmarks.py` — convert HTTPException assertions to typed-error envelope.
- `tests/api/test_chat_templates.py` — convert HTTPException assertions to typed-error envelope.

### 7.3 MCP admin tests (NEW)

- `tests/mcp/test_admin_route_map.py` — build a fake `FastAPI` with a representative route set,
  call `build_admin_route_map(app)`, assert:
  - every (method, path-template) from the test app appears in `_REST_MAP` (or in PROBE_TOOLS)
  - every `_PATH_ARGS[key]` matches the `{placeholder}` set extracted from the template
  - the alias map covers every old tool name (`slot_load`, `model_assign`, etc.)
- `tests/mcp/test_validate_catalog.py` — confirm `TOOL_DESCRIPTIONS` is consistent with the
  alias map + `AUTONOMOUS_*`/`GATED_*`/`PROBE_TOOLS` sets.

### 7.4 Exposure-CI compatibility

`tests/security/test_exposure.py` walks `create_app().routes` and asserts each is classified.
Auto-generation changes nothing in the route table (we add helpers, not routes) — the exposure
table is unaffected. Confirm with a dry-run before merge.

---

## 8. Risks

1. **`registry/pull_jobs.py` is the largest single extraction** (~370 lines from `routes/models.py`).
   It mutates `request.app.state.model_pull_jobs` (a `dict[str, PullJob]`) and emits via
   `request.app.state.events`. The Protocol in §3.1 keeps it unit-testable without a live FastAPI
   app, but the integration test (`tests/api/test_models.py::test_pull_full_lifecycle`) is the
   real gate — make sure it stays green throughout step 9.
2. **`slots/metrics_collect.py` extraction breaks monkeypatching.** Today several tests patch
   `routes.slots._systemd_show` and friends. Re-export them as `_systemd_show = systemd_props` in
   `routes/slots.py` (preserve the underscored name) so monkeypatching still works. Apply the
   same pattern to any other helper moved off-route.
3. **Pydantic body rejection changes the wire contract.** `extra="forbid"` on `CreateModelBody`
   rejects keys the legacy `dict.get(...)` path silently swallowed. Today the dashboard sends
   ~12 keys; confirm via `tests/api/test_models.py::test_create_with_legacy_fields` that the
   forbidden set is empty. If not, ship `extra="ignore"` for that body + a follow-up issue to
   narrow.
4. **Typed-error envelope shape must be byte-identical to the hand-built JSONResponse.** The
   error_codes middleware produces `{"error": {"code": …, "message": …, "details": …}}`; the
   comfyui hand-built envelopes already match. Verify with snapshot test on
   `tests/api/test_comfyui.py` before/after each conversion.
5. **MCP tool-name back-compat.** Auto-gen changes how routes surface in `tools/list`. The alias
   map preserves names, but the JSON-schema for body fields is auto-derived from FastAPI's own
   Pydantic models — which today is richer than the hand-curated `TOOL_PARAM_HINTS` (it now
   reflects the real body schema instead of the human-curated subset). Agent chats that consumed
   `required: ["hf_repo"]` will now see the real required list. Mitigation: ship `TOOL_PARAM_HINTS`
   as an explicit override — anything in `TOOL_PARAM_HINTS[tool]` wins over the auto-derived schema.
6. **`slots/port_alloc.py` is throwaway.** The §11.2 PortAuthority PR will absorb it. Don't invest
   in its API surface — keep the names + signatures as-is.
7. **Pydantic v2 vs. v1 mismatch.** Plan §2 narrows to Pydantic v2 (per `pydantic-settings` already
   imported in `api/auth.py`). All bodies use `model_config = ConfigDict(extra="forbid")` + v2
   field types. Don't use v1 `Config` class — caught the deprecated import in the existing
   `_FetchBody` at `routes/comfyui.py:586`.
8. **Existing body parsers do partial validation that the Pydantic body may double-do.** Example:
   `_reject_unknown_config_keys` in `routes/slots.py:430` validates `SlotConfig` sub-tables;
   `UpdateSlotConfigBody` should not re-validate those — keep it as `extra="ignore"` and let
   `_reject_unknown_config_keys` continue to do its job.
9. **The 2 hand-built `JSONResponse(404)` at `routes/comfyui.py:926/929`** look like error
   envelopes but are inside `_output_image`/`_output_metadata` helpers — converting them to
   `NotFound` might break the streaming response path. **Verify before converting** (read the
   helper bodies during step 18).

---

## 9. Definition of done (per §24.5 of the rework plan)

- `routes/models.py` ≤ 550 lines, `routes/slots.py` ≤ 800 lines (current 2,267 + 1,888).
- `await request.json()` count = 0 in `routes/` (was 38).
- `HTTPException` count = 0 in `routes/` (was 10 across benchmarks + chat_templates).
- Hand-built `JSONResponse(status_code=…)` for error envelopes = 0 in `routes/comfyui.py`
  (was 6 with status_code ∈ {404, 409, 422}).
- `mcp/admin.py::_REST_MAP` + `_PATH_ARGS` regenerated at startup from `create_app().routes`
  via `build_admin_route_map(app)`; the hand-authored security overlay stays.
- All existing `routes/{models,slots,comfyui,benchmarks,chat_templates}.py` callers keep the
  same JSON wire shape (verified by snapshot tests on the 38+ affected endpoints).
- `security/exposure.py` RULES unchanged (no path renames); `tests/security/test_exposure.py`
  green.
- New service-module unit tests (`tests/registry/test_pull_jobs.py`,
  `tests/slots/test_metrics_collect.py`, `tests/slots/test_port_alloc.py`,
  `tests/registry/test_cascade.py`, `tests/mcp/test_admin_route_map.py`) ship with the PRs that
  add the corresponding service modules.
- Public re-exports from `registry/__init__.py` and `slots/__init__.py` for every helper that
  moved off `routes/` (per the spec-p3-slots delegation pattern) — grep-verify every external
  caller.
- Tracker row flipped + changelog line; surface-impacts (`hal0-rework-surface-impacts.md`)
  addressed; cross-lane seam S9 (security/exposure.py) unchanged; merge to `rework/descar`;
  `check-sunset` green + scar baseline ↓/neutral; CI green on push.

---

## 10. Out of scope (explicit non-goals)

- **`routes/v1.py`** — owned by §21.5; already extended.
- **`slots/manager.py` internals** — owned by P3-slots; do NOT touch.
- **`security/exposure.py`** — owned by KB-1; do NOT touch.
- **`registry/store.py` (SqliteModelRegistry)** — owned by ML-1; the Protocol seam in §3.1
  makes the aggregator resilient to ML-1 changes but the route-layer doesn't depend on
  ML-1 landing.
- **Bench payload schemas beyond `EnqueueBody`/`ControlBody`** — owned by §20 bench rework.
- **Pydantic body schemas for `routes/auth.py`, `routes/installer.py`, `routes/updater.py`,
  `routes/proxmox.py`, `routes/memory.py`, `routes/profiles.py`, `routes/config.py`,
  `routes/dashboard_layout.py`, `routes/services.py`, `routes/backends.py`,
  `routes/settings.py`, `routes/capabilities.py`, `routes/stacks.py`** — these are part of
  the 38 `request.json()` sites but are **not** the route-file god files targeted by this
  spec. P3-routers thins only `models.py` + `slots.py` + the comfyui/benchmarks outliers;
  the rest are typed-body migrations for a future lane (or in-place when each lane lands —
  e.g. installer/installer overhaul already touches `installer.py`).
- **§13 OBS metrics request-seam wiring** — separate lane; touch only when §13.3 tables land.
