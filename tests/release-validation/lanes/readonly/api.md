# Lane: api (read-only)

Probe the REST surface from the workstation with curl against `$API` (from `CONTEXT.md`).
**GET only.** No POST/PUT/DELETE.

## Checks

1. **Route inventory.** `GET $API/api/openapi.json` first — it is the source of truth for what
   exists in this build. Record the **full sorted path list** in your report, not just the count:
   a count-only comparison cannot tell an added route from a removed one. Diff it against the
   list in the previous release's report. A route that vanished between releases is a finding.
2. **Core surface.** Walk at minimum: `/health`, `/api/status`, `/api/slots`,
   `/api/slots/{name}`, `/api/slots/{name}/config`, `/api/models`, `/api/settings`,
   `/api/capabilities`, `/api/hardware`, `/api/profiles`, `/api/activity`, `/api/services`,
   `/api/memory`, `/api/memory/engine`, `/api/memory/banks`, `/api/bench`, `/api/upstreams`,
   `/api/agents`, `/api/apps`, `/api/auth/status`, `/api/board`, `/api/update/status`,
   `/v1/models`. For each: well-formed JSON, no 5xx, no stack traces, values that make sense for
   this box.
3. **Version consistency.** The version string must be identical and correct everywhere it
   appears: `/api/status`, `/api/settings`, the UI-facing settings payload, `hal0 --version`.
   Note the exact form (PEP 440 `1.0.0rc5` vs tag form `1.0.0-rc.5`) and whether the two forms
   are used consistently.
4. **Context window, all four surfaces at once** — regression `ctx-advertised-vs-resolved`
   (#1788) and `slot-detail-ctx-max-raw` (#1835). For every slot, compare `ctx_max` from the
   LIST route, `ctx_max` from the DETAIL route, `context_length` in `/v1/models`, and the
   `--ctx-size` in the same detail payload's `resolved_command`. rc.5 fixed two of the four and
   a two-surface check scored it "fixed". `/api/slots/{name}/config` legitimately reports the
   raw TOML ceiling (see `known-issues.yaml`) — exclude it from the equality test but record it.
5. **Every advertised id must be resolvable** — regression `v1-models-never-evicts` (#1837).
   Every id in `/v1/models` must be either a registered model in `/api/models` or a configured
   slot name in `/api/slots`. One `comm -23` over the two sorted sets. Run it late in the run,
   after a stateful lane has created and deleted a slot — that is when a ghost appears.
6. **Self-consistency, elsewhere.** slot `health_ok` / `image_status` vs the slot's reported
   state (rc.4 had a ready+healthy slot advertising `health_ok=false, image_status=missing`).
7. **Error shape.** Probe malformed requests: unknown slot name, bad query params, unknown model
   in a GET. Expect clean structured 4xx JSON. A 200 with zeroed data for a nonexistent resource
   is a finding.
8. **Nonexistent-bank sweep.** Do not check only `/stats`. Walk EVERY bank sub-resource with an
   invented bank id — `memories, tags, entities, entities/graph, documents, directives,
   mental-models, operations, graph, graph/subgraph, config, stats/timeseries, stats, profile,
   export` — and record the status per route. rc.4 filed `/stats` alone and the fix did not
   generalise; rc.5 found twelve more returning 200 with an empty payload indistinguishable from
   a real, merely-empty bank.
9. **Catch-all shadowing.** Confirm the SPA catch-all does not serve HTML 200 at API paths, and
   **follow the redirects** (`curl -sL`): `/health`, `/openapi.json`, `/docs` 307 to their
   `/api/` forms, and the target must itself resolve 200. A 307 alone proves nothing.
10. **Auth posture.** Confirm the observed anonymous access matches what `/api/auth/status`
    claims. On an auth-enabled box, confirm anonymous requests are actually rejected.
11. **Headers.** Record the response headers verbatim (CORS, security headers, server banner) so
    a silent change in exposure posture shows up in next release's diff.

## Carry-forward

Add a check here whenever an API defect is found by another lane or by a user. This lane is the
cheapest place to catch a regression, so it should grow every release.
