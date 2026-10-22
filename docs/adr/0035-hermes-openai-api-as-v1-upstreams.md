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
     id; the default profile advertises `hermes-agent`
     (`api_server.py:1458-1468`).
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
5. **hal0-api can read the keys Hermes honours.** The default key lives in
   `$HERMES_HOME/.env`, and each named profile's key would live in
   `profiles/<p>/.env`. hal0-api runs as the same user as the gateway and can
   read them. hal0's own vault is not where the gateway reads its key from.
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

1. **One pinned `remote` upstream per served profile.**
   - `hal0.agents.hermes_upstreams.reconcile_hermes_upstreams` registers
     `hermes` for the default profile and `hermes.<profile>` for each named
     one, with urls `http://<host>:<port>/v1` and `.../p/<profile>/v1`.
   - Each entry is `kind="remote"` with `auth_style="bearer"`,
     `managed_by="agent:hermes"`, `pinned=True`, `timeout_seconds=600` and the
     ids Hermes advertises as `owned_models`. hal0 hardcodes no model id.
   - Operator-authored names in `upstreams.toml` win: the reconciler skips and
     logs them.
2. **Profiles are discovered by asking Hermes.** `hal0.agents.hermes_api`
   probes `/p/<name>/health` for each directory under `profiles/`, so hal0 does
   not re-implement tombstone, parked or standalone rules. Multiplexing off
   makes every probe 404 and only `hermes` registers. The endpoint follows
   Hermes's own precedence (fact 2).
3. **Keys are read from Hermes's `.env` files and never copied.**
   - A key reaches the registry through hal0-api's own process environment,
     as `HAL0_HERMES_API_KEY__<PROFILE>`, refreshed on every reconcile. It is
     never written to disk, to `api.env`, to the OWUI container or to the vault.
   - `ensure_profile_key` writes `API_SERVER_KEY` into a **named** profile's
     `.env` only when that file has no usable key, atomically, keeping its mode.
     It never rotates a present value and never touches the default profile,
     whose key stays with `ensure_gateway_api_server_key`. Fact 4 means no
     gateway restart follows.
4. **Pinned upstreams own their ids, and an owned id fails fast.** For a pinned
   upstream every id in its advertised set is owned
   (`resolution_path="pinned:<name>"`). An owned id never falls through to the
   `agent` slot (fact 9):
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
   `managed_by`, `pinned` and `owned_models`, and a `PATCH` or `DELETE` is
   refused with 400 `upstream.protected`. The new `Upstream` fields are
   dataclass-only and never round-trip through TOML.
7. **When it reconciles.** At hal0-api boot (phase `hermes_upstreams`, after the
   dispatcher), after the `hermes` component converge job (which first writes
   missing profile keys and attaches `hermes_upstreams` to the job result), and
   on `POST /api/agents/hermes/reconcile-upstreams` (404 `agent.not_installed`
   when Hermes is absent). `hal0 agent bootstrap hermes` and `reprovision` call
   it best-effort. Provisioning gains a `profile_api_keys` phase after
   `gateway_secrets_wire`. There is no timer: the dispatcher's own model-cache
   refresh and the fail-fast errors cover a gateway that stops between
   reconciles.
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
- **Existing boxes need no operator step.** The default profile works on the
  first hal0-api start after the update. Named profiles receive keys on the next
  `hal0 update` or `hal0 agent reprovision hermes`, and are absent from the
  picker until then.
- **Every `hal0-brain` turn is an agent turn.** It is the resident steward with
  the admin tools, so it is exposed like any other served profile. Managed
  entries are read-only (decision 6), so there is no per-entry switch to hide it.
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
