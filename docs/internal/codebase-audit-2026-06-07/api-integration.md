# API & Integration Surface — Audit (A2, 2026-06-07)

hal0-api is a single FastAPI app (`src/hal0/api/__init__.py`) that mounts ~35 routers under
`/api/*`, an OpenAI-compatible `/v1/*` surface (dispatcher-owned + a Lemonade catch-all proxy),
and two FastMCP sub-apps under `/mcp/*`. Integration boundaries are clean and typed at the
*error* layer (one `Hal0Error` envelope middleware, no raw `HTTPException` in any route), but the
*success* layer is unstructured (bare `dict[str, Any]` per handler) and several integrations are
stubbed at 501 or wired to process-local state that won't survive a restart.

---

## 1. FastAPI route inventory & mounting

All routers are registered in the app factory `create_app()` →
`src/hal0/api/__init__.py:1195-1463`. Prefixes and notable mounts:

| Prefix | Router module | Notes |
|---|---|---|
| `/v1` (public) | `routes/v1.py:524,661` | `GET /v1/models`, `GET /v1/models/{id}` — **auth-free probe** for OpenAI SDK compat (`__init__.py:1195-1200`) |
| `/v1` (writer) | `routes/v1.py:686+` | chat/completions, completions, embeddings, rerankings, audio/transcriptions, audio/speech, images/generations |
| `/v1/{path:path}` | `routes/lemonade_proxy.py:318-339` | **catch-all** mounted AFTER dispatcher routers — un-covered paths (`/v1/health`, `/v1/load`, `/v1/stats`…) fall through to lemond (`__init__.py:1203-1214`) |
| `/api/install` | `routes/installer.py` | first-run wizard |
| `/api/slots` | `routes/slots.py` | slot lifecycle |
| `/api/models` | `routes/models.py` | registry CRUD + pull jobs |
| `/api/hf` | `routes/hf.py` | HuggingFace search proxy |
| `/api` | `routes/hardware.py`, `routes/providers.py`, `routes/health.py` | hardware probe, provider list, `/api/status` |
| `/api/logs`, `/api/lemonade` | `routes/logs.py`, `routes/lemonade_logs.py`, `routes/lemonade_admin.py` | log SSE + lemond admin-config proxy |
| `/api/settings`, `/api/settings/proxmox` | `routes/settings.py`, `routes/proxmox.py` | proxmox sub-router nested under settings |
| `/api/memory` | `routes/memory.py` | **gated off** by `HAL0_MEMORY_ENABLED` (`__init__.py:1486`) |
| `/api/updates` | `routes/updater.py` | async update jobs |
| `/api/capabilities`, `/api/bundles`, `/api/backends`, `/api/npu` | capability overlay routers | |
| `/api/config`, `/api/events`, `/api/journal` | `routes/config.py`, `routes/events.py`, `routes/journal.py` | public read-only (first-run before any credential) |
| `/api/images` | `routes/images.py` | generated-PNG cache |
| `/api/agents` | **7 routers stacked on one prefix** (see §6) | |
| `/api/agent/approvals` | `routes/approvals.py` | approval inbox |
| `/api/mcp` | `routes/mcp.py` | MCP introspection (several 501 stubs) |
| `/api/dashboard/plugins`, `/dashboard-plugins/...` | `api/plugins/...` (absolute paths) | Hermes plugin host proxy |
| `/mcp/admin`, `/mcp/memory` | `api/mcp_mount.py:168` | FastMCP sub-apps |

---

## 2. Error handling — consistent (envelope middleware)

A single exception handler renders the structured envelope:
`src/hal0/api/middleware/error_codes.py:74-90` catches `Hal0Error` (→ `{"error": {code, message,
details}}`, status from `exc.status`) and `:90` catches `RequestValidationError`. The typed-error
hierarchy is `src/hal0/errors.py:38` (`Hal0Error` base, subclasses `BadRequest`/`NotFound`/
`Conflict`/… with stable `code` + `status` class attrs).

**Verified consistent:** `grep -rn "HTTPException\|JSONResponse(" src/hal0/api/routes/*.py`
returns **zero hits** — no route bypasses the envelope by raising raw `fastapi.HTTPException` or
hand-rolling a `JSONResponse`. Every route raises a `Hal0Error` subclass. Dispatcher
(`dispatcher/router.py:166-232`) and each route module define their own namespaced subclasses
(`dispatch.*`, `mcp.*`, `system.*`, `slot.*`) — good practice, all flow through the one handler.

The dispatcher's `SlotLoading` (`dispatcher/router.py:216-232`) carries a `progress` block + a
`retry_after_s` that the middleware promotes to a real `Retry-After` header — a thoughtful
contract for OpenAI SDK backoff.

---

## 3. Response-shape inconsistency — success payloads are unstructured (FINDING)

While **errors** are uniformly enveloped, **success** responses are bare `dict[str, Any]` returned
per-handler with no shared envelope or response_model. Examples: `routes/models.py:131` returns
`dict[str, Any]`, `routes/v1.py` returns raw `Response`/streaming, `routes/installer.py:154`
returns ad-hoc dicts. Each handler invents its own key set (`pull_job_id` vs `job_id` vs `id` —
see §5). This asymmetry (typed errors, untyped successes) is the response-shape inconsistency: a
client cannot rely on a uniform `{data, ...}` wrapper, and there are no Pydantic `response_model`
declarations to pin the contract or drive the OpenAPI schema.

- Severity: **med** — works today, but every new route re-derives its own shape; drift is
  unbounded and untestable at the schema level.

---

## 4. Async-job polling — two stores, one missing durability (FINDING)

Two independent async-job subsystems exist; they **agree on the poll-to-terminal contract** but
**disagree on durability**:

- **Update jobs** (`routes/updater.py:8-21,103-126`): `POST /api/updates/apply` (kick) →
  `GET /api/updates/status/{job_id}`. Jobs live on `app.state.update_jobs` **and are mirrored to
  disk** at `/var/lib/hal0/update-jobs/<id>.json` (`updater.py:114-126`) precisely so an `hal0-api`
  restart mid-apply doesn't 404 the CLI's poll into a 600s timeout (#509).
- **Model-pull jobs** (`routes/models.py:1201,1348,1366,1667`): `POST /api/models/{id}/pull`
  (202) → `GET /api/models/{id}/pull/status` + `/pull/stream` (SSE) + `/pull/cancel`. These live
  **only** in process-local `app.state.model_pull_jobs` (`models.py:1230,1356,1375`). The
  `pull_status` docstring even says it is a "Mirror of the updater route shape" (`models.py:1349`),
  but the *storage* is not mirrored — an `hal0-api` restart mid-download loses the job and 404s the
  poll (`PullJobNotFound`), the exact failure the updater route was hardened against.

The installer reuses the same in-memory pull path (`routes/installer.py:460-491`) and even hands
the client `"next": "poll /api/models/{model_id}/pull/status"`, so the durability gap is shared.

- Severity: **med** — pull jobs satisfy the poll-to-terminal rule but regress on the durability
  fix already applied to updater jobs; same `make_job`/`PullJob` primitive, divergent persistence.
- Minor key drift: `pull` returns `pull_job_id` (`installer.py:490`) vs `id` (`models.py:1340`) vs
  updater's `job_id` — see §3.

---

## 5. Lemonade (lemond) gateway boundary

Two distinct paths reach lemond:

1. **Dispatcher → gateway** (`dispatcher/router.py:122-139`): the synthetic composite `hal0`
   upstream is redirected to `LEMONADE_BASE_URL + /v1` (default per `_lemonade_gateway_base()`),
   because forwarding to its own registered `:8080` URL would loop back into hal0-api
   (`_resolve_target_url`, `router.py:129-139`). Upstream **5xx bodies are passed through verbatim**
   so the client sees the real lemond error; only transport failures (ConnectError /
   RemoteProtocolError, `router.py:151-154`) become a typed `UpstreamUnavailable` (502) or trigger
   `_recover_evicted_slot` (`router.py:713`).
2. **Catch-all proxy** (`routes/lemonade_proxy.py:192-339`): for un-covered `/v1/*` admin paths.
   `_forward` (`lemonade_proxy.py:250-304`) **passes lemond's status + body through unchanged**
   (`:293-304`) and only wraps *its own* connectivity failures in a hal0-shaped envelope —
   503 when lemond is unreachable (`:274-281`), 502 on protocol error (`:283-290`). This **matches
   the dispatcher's pass-through-but-wrap-transport policy** — the two lemond boundaries are
   contract-consistent. Good.

**LemonadeClient** (`src/hal0/lemonade/client.py:117`) is the typed admin client used outside the
hot path: `load`/`unload`/`pull`/`shutdown`/`stats`/`internal_config`/`internal_set`/
`internal_cleanup_cache`/`stream_logs`. `load` raises a *blast-radius-specific* `LemonadeLoadError`
rather than the generic HTTP error (`client.py:264,299`) — deliberate, so a load failure (which can
nuke every loaded child) is distinguishable. HTTP port handling and the `:13305` gateway vs `:9000`
admin split are the recurring operational gotcha (see cross-cutting §9).

---

## 6. Agents / Hermes surface — heavy prefix overloading (FINDING)

**Seven routers** are stacked onto the single `/api/agents` prefix (`__init__.py:1359-1418`):
`agents` (lifecycle), `agents_personas`, `agents_budget`, `agents_restart`, `agents_memory_stats`,
`chat_proxy`, plus the manifest/plugin host. Backing modules: `src/hal0/api/agents/*.py`
(chat_proxy, personas, budget, memory_stats, restart, _auth) over the core
`src/hal0/agents/manager.py` + `hermes_provision.py` + `mcp_client.py`.

- The **chat proxy** (`api/agents/chat_proxy.py`) bridges the browser to the Hermes dashboard
  process on `127.0.0.1:9119` over WS, with an origin allowlist + HMAC session cookie and the
  embed token in `Authorization: Bearer` (never query string) — per the registration comment
  (`__init__.py:1408-1418`). This is the deepest external integration on the agents surface.
- **Shallow / stubbed integrations:** `api/agents/memory_stats.py` degrades to `available=false`
  when the memory wrapper is absent (`__init__.py:1398-1406`); the OpenRouter OAuth callback
  (`api/openrouter/...`, `__init__.py:1442-1450`) is a **501 scaffold** pointing at ADR-0020;
  `agents/pi_coder.py` is retained but pi-coder was dropped from v0.2 promo (per memory).
- Severity: **low-med** — the prefix overloading is intentional (dashboard nests all agent sub-views
  under one base URL) but makes the agents surface the least-cohesive router group; auth posture
  varies per sub-router (`api/agents/_auth.py` is a parallel auth path to the global `deps.py`).

---

## 7. MCP host

Two layers:
- **Hosted FastMCP servers** (`api/mcp_mount.py:168` `mount_mcp_servers`): mounts `hal0-admin`
  (`mcp/admin.py:688` `build_server`) and optionally `hal0-memory` (`mcp/memory.py`, skipped when
  `memory_provider is None`, `mcp_mount.py:180`). The admin server registers each REST-backed tool
  via FastMCP's `@tool` (`admin.py:685-737`), with gating (`is_gated`, `admin.py:442`) routing
  sensitive tools through the `ApprovalQueue` (`admin.py:457-516`) and an audit logger
  (`admin.py:421`). FastMCP's localhost-only DNS-rebinding lockdown is explicitly widened for the
  LAN (`mcp_mount.py:57-81,190`).
- **MCP introspection REST** (`routes/mcp.py`): read-only views of hosted servers / clients /
  catalog + an SSE tail of `mcp.tool.*`. **Lifecycle mutations are 501 stubs**
  (`routes/mcp.py:22,57-67,806-808`, `McpNotImplemented`, `code="mcp.not_implemented"`) — install/
  uninstall/restart/config-write pend on ADR-0013's `agents/mcp_client.py`. The introspection layer
  and the aftermarket-MCP-host vision are only partially wired.
- Severity: **med** — the `/api/mcp/{id}/{action}` supervisor surface advertises actions that 501;
  a dashboard build that wires the buttons gets typed "pending supervisor" toasts, not function.

---

## 8. Provider / driver ABC

`src/hal0/providers/base.py:104` `Provider(ABC)` is a clean stateless contract: abstract
`build_env` / `start_cmd` / `health` / `infer` / `container_spec`, plus a concrete default
`render_systemd_override` (`base.py:197-305`) that renders the docker-run drop-in from
`ContainerSpec` (`base.py:57`). Concrete providers: `llama_server.py`, `flm.py`, `moonshine.py`,
`kokoro.py`, `comfyui.py`, `lemonade.py` (`src/hal0/providers/`).

- **Half-implemented abstract:** `image_ref()` (`base.py:172-182`) raises `NotImplementedError`
  ("Phase 1: … must implement") in the base — it is documented as a required override but is **not
  marked `@abstractmethod`**, so a provider missing it fails at runtime rather than at
  instantiation. Inconsistent with the other five methods. Severity: **low**.
- `infer()` is documented as "the Dispatcher is the primary request path — this is used for direct
  provider-level tests and CLI smoke checks" (`base.py:153-159`) — i.e. a parallel, lightly-used
  path next to the real dispatcher forward. Possible shallow/duplicate surface worth confirming has
  callers. Severity: **low**.

---

## 9. Cross-cutting seams (touches other audit areas)

- **Slots ↔ upstreams ↔ dispatcher (A1/A3):** `UpstreamRegistry`
  (`src/hal0/upstreams/registry.py:147`) is hydrated from slots via
  `_autoregister_slot_upstreams()` / `_hydrate_upstreams()` (`api/__init__.py:500+`). The dispatcher
  remaps `body.model` from slot-name → real model with `_remap_model` (`dispatcher/router.py:1114`).
  **Contract risk flagged in memory but verify at slot layer:** `Slot.port` vs lemond's
  self-assigned child port (8001+) mismatch in `_autoregister_slot_upstreams`, and `state.json`
  `extra.backend` drift after a backend change — both are *pointers* to confirm in the slots/lemond
  modules, not findings I citation-confirmed here.
- **Config / env (A4):** lemond base URL via `LEMONADE_BASE_URL` env (`router.py:124`); memory gate
  via `HAL0_MEMORY_ENABLED` (`__init__.py:1486`); `MEMORY.md` notes lemond HTTP `:13305` vs WS
  `:9000` vs gateway split — config-surface owners should reconcile the hard-coded defaults.
- **Auth (A?):** global gate in `api/deps.py` (`require_token`/`require_writer`) but the agents
  group has a **parallel** `api/agents/_auth.py` (HMAC session for the chat WS) and MCP has its own
  bearer resolver (`mcp_mount.py:46,134`). Three auth code paths; reconcile for a single audit.
- **Events / journal (A?):** `/api/journal` merges `/api/events` + `/api/lemonade/logs/stream`
  (`__init__.py:1342-1350`) — the lemond log SSE is re-exposed in three places (logs, journal,
  nuclear-evict banner), all sourced from `LemonadeClient.stream_logs` (`client.py:415`).
- **Memory (A?):** every memory-touching surface (admin MCP routing, `/api/memory/*`, Hermes
  provider, per-agent memory stats) is designed to degrade to no-op/503 when
  `app.state.memory_provider is None` (`__init__.py:1474-1494`) — a clean single toggle, but means
  the whole memory integration is currently dark by default.

## Findings summary

| # | Title | Location | Sev | Kind |
|---|---|---|---|---|
| 3 | Success responses unstructured (no envelope/response_model) while errors are enveloped | routes/*.py; middleware/error_codes.py:74 | med | response-shape |
| 4 | Model-pull jobs process-local (no disk mirror) unlike updater jobs → restart 404s the poll | models.py:1230 vs updater.py:114 | med | coupling |
| 6 | 7 routers overloaded on `/api/agents`; per-sub-router auth posture varies | __init__.py:1359-1418; api/agents/_auth.py | med | seam |
| 7 | MCP lifecycle mutations are 501 stubs; supervisor surface advertises non-functional actions | routes/mcp.py:22,806 | med | dead-code |
| 8 | `Provider.image_ref` required-but-not-`@abstractmethod` (runtime fail vs instantiation fail) | providers/base.py:172 | low | seam |
| 8b | `Provider.infer` parallel lightly-used path beside dispatcher forward | providers/base.py:153 | low | deepen |
| 9 | Three independent auth code paths (deps / agents._auth / mcp bearer) | api/deps.py; api/agents/_auth.py; mcp_mount.py:46 | med | coupling |
