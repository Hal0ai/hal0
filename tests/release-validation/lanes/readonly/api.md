# Lane: api (read-only)

Probe the REST surface from the workstation with curl against `$API` (from `CONTEXT.md`).
**GET only.** No POST/PUT/DELETE.

## Checks

1. **Route inventory.** `GET $API/api/openapi.json` first — it is the source of truth for what
   exists in this build. Record the **full sorted path list** in your report, not just the count:
   a count-only comparison cannot tell an added route from a removed one. Save it as a lane
   artifact in the run directory (`<box>-api-openapi-paths.txt`) and diff it against the previous
   release's artifact. A route that vanished between releases is a finding.
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
6. **Self-consistency, elsewhere.** slot `container_health` / `image_status` vs the slot's
   reported state (the rc.4-era `health_ok` field is gone; the field is `container_health` now).
   Specifically — regression `image-status-wrong-podman-store`: for any slot with
   `container_status: running`, `image_status` must not read `missing` and `actual_image` must
   not be null. If it does, cross-check `podman image inspect` in the store the containers
   actually run from (root's, on a standard install) and note which user the API's probe
   executes as — the rc.6 defect was a wrong-store probe, self-contradicted by
   `/api/system-info` in the same second.

   Since #1939 the field has a fifth member, `unknown`, and the two failures are **different
   findings**. `missing` on a running slot is the wrong-store defect above. `unknown` means the
   API is telling you honestly that it could not read the image store at all — the
   `hal0-podman-ro` seam failed (wrapper rc 66), its sudoers grant is absent, or the probe
   timed out. That is not a self-contradiction and must not be filed as one; it is a **seam
   finding**. Follow it up rather than passing it: grep the journal for
   `podman_ro.image_present_unanswered`, which carries a `reason=` naming which case fired
   (`grant-denied` / `podman-failed` / `podman-absent` / `invalid-argument` / `seam-error`),
   or for `slot_view.image_probe_failed` (`reason=probe-error` when the probe raised before
   reaching the seam, `reason=probe-timeout` when the whole slot probe blew its deadline
   first), and cross-check `hal0 doctor seams`. Every
   `unknown` has exactly one of those two lines behind it; an `unknown` with neither is itself
   a finding. A whole fleet reading `unknown` is a broken install
   even though no field is lying. Conversely, `unknown` where the seam is demonstrably healthy
   is itself a defect — the probe should have gotten an answer.
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
