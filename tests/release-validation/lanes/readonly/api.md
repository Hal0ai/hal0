# Lane: api (read-only)

Probe the REST surface from the workstation with curl against `$API` (from `CONTEXT.md`).
**GET only.** No POST/PUT/DELETE.

## Checks

1. **Route inventory.** `GET $API/api/openapi.json` first — it is the source of truth for what
   exists in this build. Record the route count and diff it against the count in the previous
   release's report (in `reports/`). A route that vanished between releases is a finding.
2. **Core surface.** Walk at minimum: `/health`, `/api/status`, `/api/slots`,
   `/api/slots/{name}`, `/api/slots/{name}/config`, `/api/models`, `/api/settings`,
   `/api/capabilities`, `/api/profiles`, `/api/activity`, `/api/memory`, `/api/memory/engine`,
   `/api/memory/banks`, `/api/bench`, `/api/upstreams`, `/api/agents`, `/api/apps`,
   `/api/auth/status`, `/api/board`, `/api/update/status`, `/v1/models`.
   For each: well-formed JSON, no 5xx, no stack traces in the body, values that make sense for
   this box.
3. **Version consistency.** The version string must be identical and correct everywhere it
   appears: `/api/status`, `/api/settings`, the UI-facing settings payload, `hal0 --version`.
   Note the exact form (PEP 440 `1.0.0rc5` vs tag form `1.0.0-rc.5`) and whether the two forms
   are used consistently.
4. **Self-consistency.** Cross-check fields that describe the same reality:
   * slot `ctx_max` / `config.context_size` vs the runner's actual `--ctx-size` (regression
     `ctx-advertised-vs-resolved`)
   * slot `health_ok` / `image_status` vs the slot's reported state — rc.4 had a ready+healthy
     slot advertising `health_ok=false, image_status=missing`
   * `/v1/models` ids vs the refs that are actually servable
5. **Error shape.** Probe 3–4 malformed requests: unknown slot name, unknown bank id, bad query
   params, unknown model in a GET. Expect clean structured 4xx JSON. A 200 with zeroed data for
   a nonexistent resource is a finding (rc.4 `/api/memory/banks/{id}/stats` did this).
6. **Catch-all shadowing.** Confirm the SPA catch-all does not serve HTML 200 at API paths —
   rc.4 shadowed `/health`, `/openapi.json`, `/docs` (the real ones live under `/api/`).
7. **Auth posture.** Confirm the observed anonymous access matches what `/api/auth/status`
   claims. On an auth-enabled box, confirm anonymous requests are actually rejected.
8. **CORS headers** — present and sane, or absent by design? Record which.

## Carry-forward

Add a check here whenever an API defect is found by another lane or by a user. This lane is the
cheapest place to catch a regression, so it should grow every release.
