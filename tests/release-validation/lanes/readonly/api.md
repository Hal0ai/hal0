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
   `/api/memory`, `/api/memory/engine`, `/api/memory/banks`, `/api/benchmarks/queue`, `/api/upstreams`,
   `/api/agents`, `/api/auth/status`, `/api/board/board`, `/api/updates/components`,
   `/v1/models`. For each: well-formed JSON, no 5xx, no stack traces, values that make sense for
   this box.
3. **Version consistency.** The version string must be identical and correct everywhere it
   appears: `/api/status`, `/api/settings`, the UI-facing settings payload, `hal0 --version`.
   Note the exact form (PEP 440 `1.0.0rc5` vs tag form `1.0.0-rc.5`) and whether the two forms
   are used consistently.
4. **Context window, all four surfaces at once** — regression `ctx-advertised-vs-resolved`
   (#1788) and `slot-detail-ctx-max-raw` (#1835). For every slot, compare `ctx_effective` (`ctx_source` says profile/fallback; `ctx_max` no longer exists) from the
   LIST route, `ctx_effective` from the DETAIL route, `context_length` in `/v1/models`, and the
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
   first), and cross-check `hal0 doctor` (there is no `doctor seams` verb; confirm with `--help`). Every
   `unknown` has exactly one of those two lines behind it; an `unknown` with neither is itself
   a finding. A whole fleet reading `unknown` is a broken install
   even though no field is lying. Conversely, `unknown` where the seam is demonstrably healthy
   is itself a defect — the probe should have gotten an answer.
   * **6c. Vulkan capabilities fan-out, restored (#1948, landed as #1973).** On an AMD box
     (ct151, ct150), `GET /api/capabilities` must offer `gpu-vulkan` again as a valid device for
     LLM/llama runtimes, not just the non-llama runtimes it was never removed for — cross-check
     against `/api/hardware`'s `vulkan_capable` flag for the same box. This is confirmed
     offer-not-promise, by source (`capabilities/catalog.py`'s llama.cpp fan-out): the picker
     offers `gpu-vulkan` whenever the host advertises that backend at all (a usable render node
     exists), with NO dependency on which runner image is installed. So a box whose Standard
     runner pin is not Vulkan-gated (`manifest.json` `runners.standard.lanes_gated`) still
     shows the row — the load-time gate (`slots.md` 8d) is what refuses, not the catalog. That
     split (offered-but-refusable) is the shipped design, not a bug; only flag it if the
     capability is offered nowhere at all on a box that should support it, or if it silently
     drops to a different device with no error. Also confirm ordering: `gpu-rocm` is offered
     BEFORE `gpu-vulkan` on a kfd-present AMD box (operator ruling — ROCm keeps the longer
     validation history and MTP tuning as the default row, even though the §3-C matrix measured
     Vulkan faster on both prefill and decode); a kfd-less box shows `gpu-vulkan` with no
     `gpu-rocm` row at all.
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
12. **Capabilities backend fan-out, local vs catalog** — regression
    `capabilities-catalog-advertises-unavailable-gpu-rocm-for-registry-models`. On a box whose
    `/api/hardware` shows every GPU `compute_capable: false`, compare the per-model `backends`
    list in `GET /api/capabilities` between a CATALOG/curated row and a LOCALLY-added model of
    the same capability type (`hal0 model add <path> --id zzprobe` against any small gguf, then
    `hal0 model rm zzprobe -f` when done). A locally-added model advertising `gpu-rocm` while the
    top-level `.backends` array and every curated row correctly omit it is the finding — do not
    conflate it with the `catalogs.voice.tts` `qwen3-tts` row, which is gated on the ROCm lane
    (#2447, `known-issues: tts-catalog-lists-qwen3-on-gpu-rocm-regardless-of-host`, fixed): on a
    box without /dev/kfd plus a render node that row must be absent.
13. **Services health vs service registry** — regression `services-health-omits-hindsight`.
    `diff <(curl -s $API/api/services | jq -r '.services[].id' | sort) <(curl -s $API/api/services/health
    | jq -r '.services[].id' | sort)`. Any id present in `/api/services` but absent from
    `/api/services/health` means the dashboard footer can never reflect that service's outage —
    check this directly here rather than only inferring it from the UI lane's footer diff, so an
    API-side omission is never misdiagnosed as "frontend still hardcoded".

14. **Error-shape and synthetic-row hygiene.** A non-existent `/api/*` path must return a structured
   JSON 404, not an empty body. Rows flagged `_synthetic` (the `hal0` composite) are excluded from
   the per-slot ctx and image checks above. Record where the version is exposed (`/api/health` and
   the CLI report the BASE version on a nightly; `current/VERSION` is the truth) and judge check 3
   by that, not by the banner.
15. **Catalog rows vs advertised backends, mechanically** — regression
   `capabilities-npu-apply-accepted-dead` (#2659). `jq` every catalog row's `backends` against
   `/api/capabilities .backends` and report the count of rows naming a backend the box does not
   advertise (35 npu-only rows on ct151/ct150). Read-only: do not apply anything here, the
   capabilities lane does.

16. **Security headers baseline.** Record which of `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options` come back on `/` and `/api/status` (none on 1.4.0 nightlies). Observation, not a finding; report only a change against the previous report.
17. **Empty-body 404 sweep.** List every GET under `/api` that answers 404 with `content-length: 0`, not only unknown paths.
18. **Brief routes against the spec.** Assert every route this brief names is in `openapi.json`; a stale name is a kit defect, report it under kit feedback, not as a product finding. On a box with auth enabled (ct150 after the update, prod) an anonymous GET gets 401 `auth.required` before any body parse: use the key from `CONTEXT.md` or record `skipped`.

19. **Redaction of count keys.** `jq '[paths(type=="object" and .value=="***REDACTED***")|join(".")]'` over `/api/settings`; the only masked leaf expected is `memory.graph.extraction_max_tokens` (known-issues `settings-redacts-token-count-keys`). A new masked leaf, or a masked number on any other surface, is a finding.

20. **Missing field, not just invalid JSON.** POST `{"q":"x"}` to `/api/memory/add`, `/search`, `/recall`, `/delete`; add and recall answer 400 `validation.invalid`, search and delete answer 500 `system.internal` (#2701 scope). Any other route that 500s on a client mistake is the same class.

21. **Bank sub-resource 404 uniformity.** Walk the 15 `/api/memory/banks/nosuchbank/*` subpaths; record the error codes (memory.engine_error on 14, memory.bank_not_found on profile). Observation; report only a change.

22. **Concurrent-actor drift.** Diff `GET /api/models` ids at lane start and end and name the drift (it grew from 1 to 4 models in one run) so it is not mistaken for a defect.

## Carry-forward

Add a check here whenever an API defect is found by another lane or by a user. This lane is the
cheapest place to catch a regression, so it should grow every release.
