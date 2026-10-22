# ADR-0035: Hermes profiles are routed to as pinned `/v1` upstreams

## Status

**ACCEPTED (2026-10-22).** This record amends ADR-0033. hal0 still does not
proxy Hermes's dashboard chat. It adds routing targets for Hermes's own
OpenAI-compatible API, so that Open WebUI (OWUI) and any other `/v1` client
can reach a Hermes profile.

## Context

OWUI is the chat front door on a hal0 box, and it talks only to hal0's `/v1`.
Before this change every OWUI turn landed on a slot model, so nothing in OWUI
could reach hal0's memory, the hal0-admin tools, owner approvals or Hermes's
own toolsets. These facts decide the shape of the fix.

Verified against hermes-agent d923a5eb487a17c0a68801bba9ef0f56c81040a8.

Hermes line numbers below are in that tree (hermes-agent 0.22.2), which is the
only ref in `VETTED_HERMES_REFS` (`src/hal0/agents/hermes_provision.py:920`).
`tests/agents/test_hermes_pin_guard.py` fails when the vetted refs and the
line above disagree.

1. **hal0 provisions the gateway `api_server` platform on every box.**
   `HAL0_MANAGED_PLATFORMS` is `("api_server",)`
   (`src/hal0/agents/hermes_platforms.py:121`).
2. **Hermes publishes an OpenAI-compatible API for external UIs.**
   - It binds `127.0.0.1:8642` by default (`gateway/platforms/api_server.py:216-217`).
   - Host and port resolve from config
     `gateway.platforms.api_server.{host,port}`, then `API_SERVER_HOST` and
     `API_SERVER_PORT`, then the defaults (`api_server.py:230`).
   - The advertised endpoints are `GET /health`, `GET /health/detailed`,
     `GET /v1/models` and `POST /v1/chat/completions` (`api_server.py:80-82`).
   - `GET /health` is unauthenticated (handler at `api_server.py:2425`);
     `GET /v1/models` is bearer-authenticated (`api_server.py:2474-2475`).
   - Chat completions live in `gateway/platforms/api_server_openai_routes.py:636`
     and stream SSE when `stream` is truthy, with string booleans coerced
     (`api_server_openai_routes.py:656`, `:745`).
3. **One gateway serves many profiles.**
   - With `multiplex_profiles` on (`gateway/config.py:612`, precedence at
     `:767-775`), the gateway serves the default profile plus every live named
     profile under `profiles/` that is not tombstoned, parked or standalone
     (`hermes_cli/profiles.py:1135-1160`).
   - Named profiles are reached at `/p/<profile>/<same path>`. The middleware
     answers 404 for an unserved prefix and enters the profile's runtime scope
     (`api_server.py:1693-1716`; ingress route at `:4492`).
   - Under a prefix `GET /v1/models` advertises the profile name as the model
     id; the default profile advertises its configured
     `model_name` / `API_SERVER_MODEL_NAME`, else `hermes-agent`
     (`api_server.py:1198-1200`, `:1458-1468`, `:2480`).
4. **Per-profile keys fail closed.** A named profile does not inherit the
   default key. It reads `API_SERVER_KEY` from its own secret scope and gets an
   empty key when none is usable (`api_server.py:1535-1549`), and the bearer
   check then answers 401 (`api_server.py:1559-1575`). The scope is built from
   the profile home's `.env` each time a request enters the profile, so a key
   written to that file needs no gateway restart
   (`gateway/run.py:1772-1782`, `:1788-1818`). A last duplicate key wins
   (`agent/secret_scope.py:390-420`); dotenv parsing follows
   `agent/secret_scope.py:257-290`; a key must pass `has_usable_secret`
   (`hermes_cli/auth.py:307-314`).
5. **hal0-api can read the keys Hermes honours once they sit in Hermes's
   `.env` files.** Provisioning keeps the default key in hal0's root-only
   vault (`hermes_provision.HERMES_SECRETS_ENV`), which reaches the gateway
   as process environment through a systemd drop-in; hal0-api runs as `hal0`
   and cannot read it there. Hermes loads `$HERMES_HOME/.env` over the
   process environment at gateway start (`hermes_cli/env_loader.py:506`,
   `override=True`), so the same value in that file changes nothing for the
   gateway and is readable by hal0-api, which runs as the gateway's user.
   Each named profile's key lives in `profiles/<p>/.env`.
6. **OWUI reaches the host only through the bridge address**
   (`packaging/systemd/hal0-openwebui.service:70`, `host.docker.internal`),
   so it cannot reach a loopback-only Hermes. Hermes stays loopback-only
   (ADR-0004).
7. **hal0's `/v1` is a dispatcher over an `UpstreamRegistry`.** `kind="remote"`
   entries forward with a bearer key read from the process environment
   (`src/hal0/upstreams/registry.py:605-620`, `auth_headers`), and `/v1/models`
   aggregates every advertising upstream
   (`src/hal0/api/routes/v1.py:952`, `_aggregate_models`). OWUI already points
   at hal0's `/v1` (`src/hal0/openwebui/env_writer.py:173`). Health probing
   strips a trailing `/v1` from the url before appending `health_path`
   (`registry.py:674`), so the default `/health` lands on Hermes's
   unauthenticated route.
8. **hal0's admin MCP server gates server-side and does not block.** A gated
   tool returns `{"status": "pending_approval", "approval_id": ...}` at once
   (`src/hal0/mcp/admin.py:2165`), and the operator approves in the dashboard
   inbox. A Hermes turn never stalls on an approval.
9. **A model id that no upstream or slot owns falls to the `agent` slot.**
   Rule 9 of capability resolution is that fallback
   (`src/hal0/dispatcher/_capability_resolve.py:248`). Without a guard, a
   Hermes upstream that went offline would make its picker entry silently
   answer from a local model.
10. **There is no in-process agent state machine to hook.** Enable, disable and
    reprovision are root CLI paths running outside hal0-api, so the places hal0
    can reconcile are boot, the component converge pass, and an endpoint the CLI
    can call.

## Decision

1. **One pinned `remote` upstream per keyed profile.**
   - `hal0.agents.hermes_upstreams.reconcile_hermes_upstreams` registers
     `hermes` for the default profile and `hermes.<profile>` for each named
     one, with urls `http://<host>:<port>/v1` and `.../p/<profile>/v1`.
   - Each entry is `kind="remote"` with `auth_style="bearer"`,
     `managed_by="agent:hermes"`, `pinned=True`, `timeout_seconds=600`, and as
     `owned_models` exactly the id Hermes derives for the profile
     (for the default, the configured `model_name` / `API_SERVER_MODEL_NAME` when
     set, else `hermes-agent`; for a named profile, its name; fact 3).
   - Operator-authored names in `upstreams.toml` win: the reconciler skips and
     logs them.
2. **Ownership follows hal0's knowledge; liveness only decides success.**
   - The reconciler registers the default profile and every directory under
     `profiles/` that has a usable key, whether or not the gateway answers. A
     gateway that is down, restarting or answering 5xx keeps every entry, so
     its ids fail fast (decision 4) instead of falling to the `agent` slot.
   - A named profile is dropped only when the gateway's default `/health`
     answers 200 and its `/p/<name>/health` answers exactly 404. hal0 so asks
     Hermes which profiles it serves and does not re-implement tombstone,
     parked or standalone rules. Multiplexing off makes every live probe 404,
     and only `hermes` stays.
   - The endpoint follows Hermes's own precedence (fact 2).
   - One owner per model id. When two profiles derive the same id (the
     default profile configured with `model_name: coder` next to
     `profiles/coder`), the first keeps it, `default` always first, and the
     other is left out and reported under `model_id_collisions`, instead of a
     second pinned owner that is advertised but never reached.
3. **Keys are read from Hermes's `.env` files and never copied.**
   - A key reaches the registry through hal0-api's own process environment,
     as `HAL0_HERMES_API_KEY__<PROFILE>`, refreshed on every reconcile. It is
     never written to disk, to `api.env`, to the OWUI container or to the vault.
   - `ensure_profile_key` writes `API_SERVER_KEY` into a **named** profile's
     `.env` only when that file has no usable key, atomically, keeping its mode
     and, as root, its owner (a new file is owned like its directory, so the
     gateway can read it). It never rotates a present value. Fact 4 means no
     gateway restart follows.
   - `ensure_default_key` **mirrors** the default key into `$HERMES_HOME/.env`
     when neither config `api_server.key` nor that file holds a usable one:
     at provisioning, with the value `ensure_gateway_api_server_key` has just
     minted for the vault, and on a root `hal0 agent reprovision hermes
     --repair`, from the vault. It never mints a key of its own, because a
     fresh value would desynchronise hal0 from the running gateway until its
     next restart (fact 5). A non-root pass that finds no readable default
     key leaves `hermes` unregistered and says so: the `profile_api_keys`
     phase warns, and the `hermes-api` doctor row names the root repair.
4. **Pinned upstreams own their ids, and an owned id fails fast.** Only an id in
   a pinned upstream's `owned_models` is owned
   (`resolution_path="pinned:<name>"`). Other ids Hermes advertises, such as
   its gateway-wide `model_routes` aliases (`api_server.py:2485-2488`), go into
   the models cache and resolve by the normal ladder, slot and registry first.
   So an alias equal to a slot model id never takes that slot over and never
   loops hal0's own inference back into Hermes. An owned id never falls
   through to the `agent` slot (fact 9), and ownership is decided before
   container-slot preemption, so a loaded container slot that advertises an
   owned id does not take the request either:
   - upstream offline: 502 `dispatch.upstream_unavailable`, "...which is offline";
   - upstream disabled: "...which is disabled";
   - upstream live but not advertising the id: "...which is up but does not
     advertise it".
   A cold model cache is fetched once, single-flighted, before deciding. In
   `/v1/chat/completions` an owned id counts as remote: the slot-alias rewrite
   and `_ensure_backend_for_model` are skipped, so no thinking injection, no
   OmniRouter tool loop and no slot wake runs for it.
5. **Streams get per-upstream stall bounds.** `Upstream.stream_idle_timeout_s`
   and `stream_total_timeout_s` override the dispatcher's global 300 s and 900 s.
   Hermes upstreams use 600 s idle and 1800 s total, because a long tool run can
   leave gaps between deltas.
6. **Managed entries are read-only on the providers page.** They serialize
   `managed_by`, `pinned` and `owned_models`. A `PATCH`, a `DELETE` and a
   `POST /api/providers/{name}/credentials` are refused with 400
   `upstream.protected`, the last before anything is written to `api.env`.
   The page lists them with a "managed by agent:hermes" badge and their owned
   ids, and shows no delete, toggle, filter or key controls for them. The new `Upstream` fields are
   dataclass-only and never round-trip through TOML.
7. **When it reconciles, and who writes the keys.** Every hal0-api entry
   point goes through `sync_hermes_upstreams`: the key sweep
   (`ensure_profile_keys`) first, then the reconcile, so a profile created
   while hal0-api runs gets its key on the next pass instead of after a
   restart. A failed sweep is logged and the reconcile still runs.
   - At hal0-api boot (phase `hermes_upstreams`, after the dispatcher), so
     every hal0-api start, including the restart that ends `hal0 update`,
     delivers the keys.
   - Every 120 s after boot (`HERMES_RECONCILE_INTERVAL_S`), so a gateway that
     was still starting at boot, or a profile added later, is picked up.
     Errors are logged and the loop keeps running.
   - After the `hermes` component converge job ends `converged`, `stale` or
     `upgraded`; the job attaches `hermes_upstreams` to its result.
   - On `POST /api/agents/hermes/reconcile-upstreams` (404
     `agent.not_installed` when Hermes is absent). `hal0 agent bootstrap
     hermes` and `reprovision` call it best-effort.
   - Provisioning: `gateway_secrets_wire` mirrors the default key it mints
     (decision 3), and the `profile_api_keys` phase right after it runs the
     sweep, so `hal0 agent reprovision hermes` writes keys too.
8. **Two Hermes headers pass through.** `X-Hermes-Session-Id` and
   `X-Hermes-Session-Key` join the forwardable request headers
   (`api_server_openai_routes.py:679-690`). The allowlist is global, so every
   upstream receives them when a client sends them. They are protocol
   selectors, not credentials. OWUI sends neither, and resends history each
   turn.
9. **Visibility.** The Services page's OWUI card shows an "Agents" wired chip
   (`wired.agents`, from the server-side classifier). `hal0 doctor` has a
   `hermes-api` row, "Hermes API upstreams". Green on that row means the API
   path answers, not that Hermes has its tools (see Consequences).

### The ADR-0033 boundary

ADR-0033 deleted hal0's proxy of Hermes's *dashboard chat*: WebSocket events,
PTY and session RPC. That stays deleted. This record routes to Hermes's
*OpenAI API*, which Hermes publishes for external UIs (`api_server.py:80-82`).
hal0 holds no session state, no WebSocket, no embed token and no chat UI of
its own for Hermes.

## Rejected

- **Widen Hermes's bind, or publish 8642.** OWUI could then reach Hermes
  directly, but it opens a listener that ADR-0004 keeps closed, and the
  per-profile keys would guard it alone.
- **Point OWUI directly at 8642.** OWUI cannot reach loopback (fact 6). Even if
  it could, other OpenAI clients of hal0 would get nothing, and OWUI would hold
  Hermes keys.
- **OWUI-side MCP tool servers.** ADR-0015's `exposure.openwebui` stays 501.
  OWUI would still have no Hermes memory, approvals or skills.
- **A hal0 chat proxy for Hermes.** It would reopen ADR-0033: session state,
  streaming and a UI hal0 would have to maintain.
- **Mirror the key into `api.env`.** It copies a secret to a second place, and a
  non-root repair cannot write there. A live check showed it is unnecessary,
  because hal0-api reads the `.env` files Hermes reads.
- **One key shared across profiles.** Hermes isolates each profile's scope
  (fact 4). A shared key would let one profile's credential open another.

## Consequences

- OWUI's picker, and `/v1/models` for every client, lists `hermes-agent` and
  one model per served profile next to the slot models. Choosing a slot model
  behaves exactly as before.
- **Dependency on #2891.** The API path works when the gateway runs without the
  `mcp` and `web` extras, but the profiles then have no hal0 tools, and the
  doctor row says only that the API answers.
- **Streams can run for 30 minutes.** A Hermes turn can hold a hal0-api
  connection for up to 1800 s, against 900 s for a slot. A few concurrent long
  turns share the dispatcher's connection pool with slot traffic.
- **Task calls cost agent turns.** OWUI sends titles, tags and follow-ups to
  the chat model unless told otherwise. hal0 sets no `TASK_MODEL*` env, because
  it cannot know which slot model is loaded. The OWUI guide tells the operator
  to set a slot model as the task model.
- `/v1/responses` is not available through hal0, which mounts no such route.
- **Existing boxes need one root step for `hermes-agent`.** A box provisioned
  before this decision holds its default key only in the root-only vault, so
  hal0-api cannot read it and `hermes` stays unregistered until
  `sudo hal0 agent reprovision hermes --repair` mirrors it into
  `$HERMES_HOME/.env` (fact 5). `hal0 doctor` says so on the `hermes-api`
  row. The named profiles need no step: hal0-api writes their keys and
  registers them at its first start after the update, and the periodic pass,
  the reconcile route, `hal0 agent reprovision hermes` and the `hermes`
  converge job do the same. A box installed after this decision gets the
  mirror at provisioning.
- **The test suite pins `HERMES_HOME`.** App boot runs the key sweep, so the
  shared isolation fixture points `HERMES_HOME` under `tmp_path`; a suite run
  on a box with a live Hermes tree never writes to it.
- **Every `hal0-brain` turn is an agent turn.** It is the resident steward with
  the admin tools, so it is exposed like any other served profile. Managed
  entries are read-only (decision 6), so hal0 has no per-entry switch. The knob
  is Hermes-side: park the profile (`hermes -p <profile> gateway stop`) or set
  `gateway.standalone: true` in its config. Either drops it from
  `profiles_to_serve` (`hermes_cli/profiles.py:1158-1159`), and the next
  reconcile that sees the gateway up and the profile's prefix answer 404
  removes its upstream. The default profile cannot be hidden this way:
  it is always served first, `gateway.standalone` is ignored on it, and a
  `gateway.parked` marker on it is ignored with a warning
  (`hermes_cli/profiles.py:1064-1067`, `:1151-1156`), so `hermes-agent` stays.
- **A Hermes pin bump is guarded.** Changing `VETTED_HERMES_REFS` fails
  `test_adr_0035_verified_ref_matches_vetted_pin` until the citations above are
  re-verified and the "Verified against" line is updated.

## References

- ADR-0033 (amended here), ADR-0034, ADR-0004, ADR-0015.
- #2891 (gateway runs without the `mcp` and `web` extras).
- NousResearch/hermes-agent `d923a5eb` (v0.22.2): `gateway/platforms/api_server.py`,
  `gateway/platforms/api_server_openai_routes.py`, `gateway/run.py`,
  `gateway/config.py`, `agent/secret_scope.py`, `hermes_cli/profiles.py`,
  `hermes_cli/auth.py`.
