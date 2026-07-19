# §4.4 addendum — MCP admin route-map autogen (the 3 gaps)

Settles the three gaps that must be pinned before the `build_admin_route_map(app)`
autogen lane (spec-p3-routers.final.md step 20) can build. Ratification level:
**orchestrator/technical** — all three resolutions are the security-conservative
default; none is a product call. Flagged inline if any warrants a human veto.

Context: today `mcp/admin.py` hand-maintains `_REST_MAP` (tool_name → (method,
path)) + `_PATH_ARGS`, guarded by `tests/mcp/test_route_sync.py` (every mapped
target resolves to a live route) + `_validate_catalog` (import-time). Autogen
derives the route→tool scaffolding from `app.routes`; the **security overlay
stays hand-authored**. The three gaps are about keeping that split safe.

---

## Gap 1 — deny-by-default for unclassified auto-added routes

**Problem.** Under autogen every route lands in the generated map. If a newly
added route auto-became an exposed MCP tool, adding a route would silently widen
the agent-reachable surface — the opposite of deny-by-default.

**Resolution (RATIFIED).** Autogen produces only the route→tool *scaffolding*
(name, method, path, param shape). **Exposure is NEVER auto-derived.** A route
with no entry in the hand-authored classification overlay
(`AUTONOMOUS_WRITE_TOOLS` / `GATED_TOOLS` / `PROBE_TOOLS` / read-classified) is:
- **hidden** from `tools/list` (not an MCP tool),
- **not fatal** (import + boot succeed),
- surfaced in a **CI report** — a new `test_unclassified_routes` (or a
  `_validate_catalog` section) lists every generated route lacking a
  classification, so a human classifies it deliberately.

Net: new routes are invisible to agents until a human opts them in. Deny-by-default
preserved; the security overlay remains the single source of exposure truth.

## Gap 2 — transport exclusions (PATCH + stream/WS predicate)

**Problem.** SSE/streaming/WebSocket routes (log tails, pull-progress streams,
`events`, board WS) are not request→response tools; auto-generating tools for
them is meaningless and risky. Also PATCH was unsupported (§4.1).

**Resolution (RATIFIED).**
- **Add PATCH** to the supported verb set.
- Add an **exclusion predicate** that skips a route from tool generation when ANY
  of: (a) its response class is streaming (`StreamingResponse` /
  `EventSourceResponse` / WS), (b) its path matches a stream marker
  (`/logs`, `*/stream`, `/events`, `*/ws`, board WS, pull-progress streams), or
  (c) it is in an explicit `EXCLUDED_ROUTES` set for known non-tool endpoints.
  Excluded routes never become MCP tools and are NOT counted as "unclassified"
  in the Gap-1 report (they're deliberately non-tool).

## Gap 3 — re-key overlays on route-id, not tool-name

**Problem.** Redaction/wrap/description/param-hint overlays are keyed on tool
*names* today. Autogen derives tool names from routes, so names can shift —
name-keyed overlays would silently detach.

**Resolution (RATIFIED).**
- The overlay (redaction, response-wrap, description, param-hints, annotations)
  re-keys on **`route_id = "<METHOD>:<path-template>"`** (the stable identity),
  not the tool name.
- Keep a hand-authored **`TOOL_NAME_ALIASES`** map (route_id → stable tool name)
  so `tools/list` names never churn — agent chat caches tool schemas, so a
  silent rename is a break. Collisions are explicit (e.g. `slot_edit` +
  `model_assign` both → `PUT /api/slots/{name}/config`: two tool names, one
  route_id, via aliases).
- The spec's "86-entry" figure is **stale** — the count is *derived* from the
  live route set (currently ~72); the addendum asserts no fixed number.

---

## Build shape (unblocked once ratified)
1. Interim: keep `test_route_sync.py` green while introducing the generator.
2. `build_admin_route_map(app)` walks `app.routes`, skips `/mcp` `/docs` `/redoc`
   `/openapi.json` `/dashboard-plugins` + SPA catch-all + the Gap-2 excludes;
   per route computes `route_id`, extracts `{placeholder}` → `_PATH_ARGS`.
3. `_REST_MAP`/`_PATH_ARGS` become lazy from a stashed app ref
   (`install_admin_route_map(app)` in lifespan + a test helper).
4. Overlay re-keys on `route_id`; `TOOL_NAME_ALIASES` preserves names.
5. `_validate_catalog` updated; new `test_admin_route_map` + extend
   `test_route_sync`/`test_validate_catalog`; the Gap-1 unclassified-routes report.

**Sequencing:** interim sync test → generator + lifespan install + alias table →
overlay re-key → Gap-1 report. Ships in v1.0.0 or as a fast-follow (the current
hand-maintained map is correct; this removes the drift-by-hand hazard).
