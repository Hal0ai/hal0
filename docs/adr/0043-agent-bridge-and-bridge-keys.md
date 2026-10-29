# ADR-0043: Agent bridge and bridge keys

## Status

**ACCEPTED (2026-10-28).** An operator decision.

It amends ADR-0004: hal0 bundles a third MCP server, `hal0-agents`, next to
`hal0-admin` and `hal0-memory`. It implements the first item of ADR-0024
(a key tier accepted on `/mcp/*` and refused on the REST surface) for remote
agents, and the third item (the admin MCP server calls REST as itself) for
bridge principals only. The rest of ADR-0024 stays open; see "Relationship to
other records".

The first change under this record ships the keys, the store, the credential
tier, the REST and CLI surface, the config section and a doctor row. The
second ships Decisions 8 and 9, the `hal0-agents` server and its run tools;
what it settled along the way is under "Implementation decisions (second
change)". The teach tools (Decision 10) land in a later change on this ADR;
see Consequences.

## Context

A coding agent running somewhere else (Claude Code on a workstation, Codex, Pi,
a CI job, a second hal0) needs to use the Hermes agent a hal0 box runs in two
ways. It hands Hermes a task that needs the box (a GPU benchmark, an operation
on that host, an action with credentials that live only there) and gets the
outcome back in its own session. And it teaches Hermes: durable facts for its
memory namespace, new or revised skills, a read-only view of what Hermes can
do now.

hal0 already owns every part of that except the front door:

- It provisions Hermes's API server and reads every profile's
  `API_SERVER_KEY` into hal0-api's process environment as
  `HAL0_HERMES_API_KEY__<PROFILE>` (ADR-0035, decision 3;
  `src/hal0/agents/hermes_upstreams.py`, `reconcile_hermes_upstreams`).
- It serves `hal0-admin` and `hal0-memory` over Streamable HTTP, with an
  approval inbox for gated calls (ADR-0004). Servers are built once per
  process (`BoundFastMCP`, `src/hal0/mcp/_binding.py:59`) and mounted by
  `mount_mcp_servers` (`src/hal0/api/mcp_mount.py`); the bundled set is
  `BUNDLED_SERVER_IDS = {"hal0-admin", "hal0-memory"}`
  (`src/hal0/mcp/installed.py:56`).
- It keeps the agent's credentials in a root-only vault (ADR-0002) and shows
  agent lifecycle in the dock (ADR-0042).

What it cannot do is admit a second caller of its own:

1. **There is no credential for a remote agent.** The auth tiers are
   `anon | client | admin` (`Tier` in `src/hal0/api/auth.py`). A bearer is
   compared with `hmac.compare_digest` (same file) against two values,
   `HAL0_ADMIN_KEY` and `HAL0_CLIENT_KEY` (`_KEY_ENV` in
   `src/hal0/service_identity.py`). No store holds more than one key per tier.
   The agent tier ADR-0024 proposes was never built.
2. **The MCP mounts are classified for the box's own agents.** `/mcp/memory`
   is CLIENT and every other `/mcp` path is ADMIN (the `/mcp` rows of `RULES`
   in `src/hal0/security/exposure.py`); anything unclassified is ADMIN
   (anti-scar rule 8, `CONTRIBUTING.md`). A remote agent that wants memory or
   the admin tools has to be handed the box's own key.
3. **Caller identity is a claim.** `X-hal0-Agent` is read from the request
   header (`_AGENT_HEADER` in `src/hal0/api/mcp_mount.py`) and nothing binds
   it to a credential, so any holder of the client key can write into any
   agent's `private:<agent>` memory.
4. **hal0's one SQLite file is world-readable.** `hal0.db` is mode `0644`
   (`src/hal0/install/perms.py:548-552`). A table of key hashes, and later of
   run input and output, does not belong in it.
5. **"Peer" already means two other things.** `hal0 agent peers` lists memory
   identity cards (`src/hal0/cli/agent_commands.py:1987`), and
   `[upstreams.<name>].hal0_peer` marks a hal0-to-hal0 upstream
   (`src/hal0/config/schema.py:1270`).

Hermes's own API, as of hermes-agent 0.22.4 (the pin; the same release
ADR-0035 was verified against), shapes the answer:

- A named profile answers only to its own `API_SERVER_KEY`, and run controls
  are scoped by a hash of the profile and that key, so a second caller using
  the same key sees and can stop the first caller's runs.
- A run that needs an approval or an answer raises an event on a server-sent
  event stream. A caller that is not reading the stream leaves the run
  waiting, and nothing reaches hal0's approval inbox.
- Memory and skill writes are not on Hermes's API at all. Those paths exist
  only on the box, through hal0.

## Decision

1. **A bridge client is a named remote agent the operator admits.** hal0
   mints it a key `hal0bk_<name>.<token>`, where `<token>` is
   `secrets.token_urlsafe(32)` and `<name>` matches
   `^[a-z0-9][a-z0-9_-]{0,31}$` (`src/hal0/bridge/keys.py`). The name is
   embedded so the auth path looks the client row up directly; the separator is
   a literal `.` because the urlsafe alphabet never produces one. hal0 stores
   only the sha256 of the whole key. The plaintext is shown once, when the
   client is added or its key rotated, and cannot be recovered. Because a
   client's memory identity is its bare name, the names hal0's own callers and
   namespaces use are reserved: `hermes`, `pi`, `admin`, `anonymous`, `shared`,
   every name starting `hal0`, and every name starting `hermes__`
   (`RESERVED_CLIENT_NAMES` and `RESERVED_CLIENT_PREFIXES` in
   `src/hal0/bridge/keys.py`).
2. **The store is `bridge.db`, a separate SQLite file.**
   `/var/lib/hal0/bridge.db`, `hal0:hal0` mode `0600`, with the `-wal` and
   `-shm` siblings listed as permission rows in `install/perms.py` so
   `hal0 doctor perms` audits them. The schema is
   `src/hal0/bridge/migrations/*.sql`, applied by `hal0.db.migrate.migrate`
   (the runner takes a migrations directory, so it drives a second database
   file). The `clients` table is `name` (primary key), `key_hash` (unique),
   `created_at`, `created_by`, `revoked_at`, `last_seen_at`, `note` and
   `scopes`. Revocation is immediate: the check is one indexed lookup per
   request, with no cache. The lookup matches the sha256 of the presented key
   against the indexed `key_hash` instead of comparing keys in constant time.
   That is safe: a timing difference could only tell an observer how the
   digest of a key it chose compares with the stored digests, and getting from
   a digest back to a key is a preimage search. The check is a read:
   `last_seen_at` is stamped at most once a minute. A revoked client keeps its
   row, so its name cannot be
   reused and its past runs stay attributable. Machine-minted credentials are
   machine state, which is SQLite, not TOML (anti-scar rule 7).
3. **`bridge` is a credential tier, and the bridge always asks for it.**
   `Tier` gains `"bridge"`. `resolve_principal` returns
   `Principal(tier="bridge", name=<client>)` for a bearer that starts with
   `hal0bk_` and whose hash matches an unrevoked row; other bearers are never
   hashed against the store. The exposure table gains `AuthClass.BRIDGE`, used
   by `/mcp/agents` and `GET /api/bridge/me` and nothing else. A BRIDGE route
   admits a `bridge` or an `admin` principal, always: with `[security]
   require_auth` off, and from loopback. This is the precedent of ADR-0040
   (the ComfyUI gate's own credential) applied to a surface that must not
   open as a side effect of the trusted-LAN default. A `bridge` principal is
   refused everywhere except the scope-gated `/mcp/memory` and `/mcp/admin`
   mounts (Decision 4) and the OPEN routes, which admit anyone. The key is
   verified once per request, by the auth middleware, which stores the
   principal it judged on the request (`BRIDGE_PRINCIPAL_SCOPE_KEY`); what runs
   behind it (the MCP identity resolvers, `GET /api/bridge/me`) reads that
   principal (`judged_principal` in `src/hal0/api/auth.py`) instead of
   verifying again. A bridge-shaped key the middleware did not admit, because
   it did not verify, the store could not be read or the bridge is disabled, is
   anonymous to them: never the `X-hal0-Agent` header's claim and never the
   service identity. Only the re-check of an open stream or socket verifies
   again, live, so revoking a key ends what it opened.
4. **Scopes decide which MCP servers a key opens.** A client carries a subset
   of `{agents, memory, admin}`. `agents` is held by every client; the default
   is `agents,memory`; `admin` is granted only when asked for.

   | Server | Accepts a bridge key | Identity and effect |
   |---|---|---|
   | `/mcp/agents` | always | the client's runs; an admin sees all |
   | `/mcp/memory` | scope `memory` | identity is the client name from the key; `X-hal0-Agent` is ignored, so `X-hal0-Private: 1` lands in `private:<client>` and nowhere else |
   | `/mcp/admin` | scope `admin` | the admin server calls REST with hal0's own service identity (`service_key(prefer="admin")`) and audits the call as `bridge:<client>`, instead of forwarding the caller's bearer; gated tools still go to the approval inbox and `EXCLUDED_TOOLS` (`hal0.mcp.admin`) stay hidden |

   A bridge key is refused on every non-OPEN `/api/*` and `/v1/*` route,
   whatever its scope; OPEN routes such as `GET /api/health` and
   `GET /v1/models` admit it as they admit a caller with no key. The one
   exception is `GET /api/bridge/me`, which accepts the bridge
   key itself and returns the caller's name and its created and last-seen
   times, so onboarding is verifiable with one `curl`.
5. **The operator surface is one CLI over one REST group.** REST, all ADMIN:
   `GET` and `POST /api/bridge/clients`, `DELETE /api/bridge/clients/{name}`
   (revoke), `POST /api/bridge/clients/{name}/rotate`, and
   `PATCH /api/bridge/clients/{name}` (scopes). The CLI goes through REST, so
   there is one writer:
   `hal0 bridge add <name> [--note TEXT] [--scope agents|memory|admin]...`,
   `list`, `revoke <name>`, `rotate <name>` and `scopes <name> <scope>...`.
   `add` and `rotate` print the key once and one onboarding line per granted
   scope: `claude mcp add --transport http <server> <url> --header
   "Authorization: Bearer <key>"`, with `hal0-agents` at `/mcp/agents/mcp`,
   `hal0-memory` at `/mcp/memory/mcp` and `hal0-admin` at `/mcp/admin/mcp`
   (one table, `SCOPE_SERVERS` in `src/hal0/bridge/keys.py`, maps each scope to
   its server and mount). The lines are pasted on another machine, so their
   base is `[network].public_url` when set, else the address the request
   reached hal0-api at, with a loopback host (the CLI calls the API on
   `127.0.0.1`) replaced by the box's first LAN address on the API port.
6. **`[bridge]` in `hal0.toml` holds the limits, and the bridge is on by
   default.** `BridgeConfig` (`extra="forbid"`) has `enabled = true`,
   `max_concurrent_runs_per_client = 2`, `run_retention_days = 7` and
   `clarify_timeout_s = 900`. The default is on because the surface is inert
   until the operator adds a client: with no client rows nothing except the
   admin key can authenticate, so onboarding stays one command with no
   restart. `enabled = false` switches every bridge key off: none
   authenticates anywhere, the scoped MCP mounts included, and hal0-api never
   creates `bridge.db` on a request's behalf. It also leaves the MCP server
   unmounted (404); answers adding, rotating and re-scoping a client and
   `GET /api/bridge/me` with 404 `bridge.disabled`, ahead of body validation;
   makes those `hal0 bridge` verbs say why; and makes the doctor row pass as
   "disabled". Listing and revoking clients keep working while the bridge is
   off, over the `bridge.db` that already exists, so an operator can end a key
   before turning the bridge back on. `enabled` is read when hal0-api starts
   and recorded on the auth module (`set_bridge_enabled`), the one value the
   gate and the routes consult; the other three apply to the next request that
   uses them.
7. **`hal0 doctor all` carries a `bridge-store` row.** It reports whether
   `bridge.db` is present, owner-only (`0600`, owned by the service user),
   migrated to the schema this build ships, and how many clients it holds.
   hal0-api creates the file when it starts with the bridge on, so a missing
   file while hal0-api answers is a warning (that open failed and no key can
   verify), and with hal0-api down it passes as not created yet.
   It opens the file read-only and never creates, migrates or re-permissions
   `bridge.db`; SQLite may leave the `-wal` and `-shm` siblings of a database
   it reads, and those have permission rows too. The row is advisory: a broken
   store only stops bridge keys from authenticating.
8. **`hal0-agents` is the bridge's MCP server (implemented in the second change).**
   It is mounted at `/mcp/agents` (client URL `/mcp/agents/mcp`), built once
   like the other two, and added to `BUNDLED_SERVER_IDS`. Hermes's keys stay in
   hal0-api's process environment; the server forwards with the keys hal0
   already holds and never hands one to a caller.
9. **Run tools (implemented in the second change).** Agents are listed with their
   kind and health; a run is started on a Hermes profile (`hermes` is the
   default profile, `hermes/<profile>` a named one), waited for, polled,
   steered, stopped and listed; its events are replayed from a ledger in
   `bridge.db`. The calls below are decided now so the first change does not
   close them off:
   - There is **no approve tool**. A gated action needs a human click
     (ADR-0004). An `approval.request` from a run is mirrored into the existing
     inbox, which grants `once` or denies; `session` and `always` are
     Hermes-side allowlist decisions an operator makes in Hermes.
   - A **clarification question goes to the delegating client**, not the
     inbox: the question is for the agent that asked. The client answers it
     with a tool, and a run whose question stays unanswered for
     `[bridge].clarify_timeout_s` is stopped with "clarification unanswered".
   - **Session keys and idempotency keys are namespaced per client** by hal0
     (`hal0-bridge:<client>:<key>`), so one client cannot bind another's
     conversation.
   - The wait tool defaults to 120 seconds and caps at 600. A run that is
     silent for a minute is not a failed run: the local model can take 40
     seconds to the first token.
   - Concurrency is limited per client by `max_concurrent_runs_per_client`
     before the run is posted.
10. **Teach tools (a later change on this ADR).** Memory add and search write
    to the agent's own namespace (`private:hermes` or
    `private:hermes__<profile>`, or the shared one when asked for), forced by
    `MemoryDispatcher.scoped` (`src/hal0/dispatcher/memory_dispatcher.py:107`)
    and tagged with the client that wrote them. Skills are listed from hal0's
    shipped mirror (read-only) and from the drop-in directory
    `/var/lib/hal0/skills`; writes and deletes touch only the drop-in
    directory, validate the skill's frontmatter, refuse symlinks and path
    components, cap size, and are **gated**: they go to the approval inbox, and
    repeated pushes of one skill collapse into one row. A capabilities tool
    gives a read-only, sanitised view of what Hermes can do now.

### Implementation decisions (second change)

The second change (PR 2) implements Decisions 8 and 9. These calls were made
while building it and reviewing it:

- **The event cap counts rows.** The ledger keeps a run's newest 500 event
  rows, not the events within 500 sequence numbers of its newest: Hermes
  numbers every `message.delta`, while the watcher stores about 2 KiB of
  deltas as one `message.text` row, so a window of sequence numbers kept only
  the last ~500 deltas of text. A run's whole output stays in `runs.output`,
  and `agent_run_status(run_id, offset=…)` and
  `GET /api/bridge/runs/{run_id}?offset=` read it in 16 KiB windows
  (`output_offset`, `output_total` and `next_offset`, in UTF-8 bytes). A view
  whose output is cut names the next window in `more`.
- **`unknown` is a terminal status.** A run that Hermes answers 404 for, that
  Hermes reports as `unknown`, or that the watcher cannot reach Hermes about
  for `give_up_s` (1800 s) ends as `unknown`: the ledger will hear nothing
  more, so it no longer counts against the concurrency limit.
- **Giving up is measured from the last contact with Hermes**, meaning the
  last event, or the last stream that ended normally. A connection that stays
  open and quiet is not a failure, so a slow model never ends a watch;
  connection failures and refusals do not count as contact.
- **Hermes's 401, 403 and 5xx answers are 502 to the client**
  (`bridge.agent_refused`). They mean hal0's key or Hermes itself failed, not
  the client's request. Other 4xx answers pass through with Hermes's status,
  and Hermes's error code is in the message.
- **The inbox row is withdrawn and filed again as the run moves.** The inbox
  keeps one row per run. A request that a newer approval or a clarification
  supersedes, that Hermes settles first, or that the run outlives is
  withdrawn. A decision that does not reach Hermes leaves the run
  `waiting_for_approval`, and the request is filed again unless Hermes
  answered that it is no longer pending (409 `approval_not_pending` or
  `stale_generation`): the operator must always have a row to answer.
- **`bridge.disabled` and `bridge.unavailable` are different states.**
  `bridge.disabled` (404) is a bridge switched off at boot: the REST routes
  answer it and `/mcp/agents` is not mounted. `bridge.unavailable` (503) is a
  bridge that is on but whose run service is not running (not published yet,
  or its boot phase failed). The run routes and the `hal0-agents` tools both
  answer it.
- **The `/mcp/*` host allowlist is seeded from the box's own names.** The MCP
  SDK's DNS-rebinding guard matches the `Host` header exactly, and its
  localhost-only default refused a remote client calling the box by its
  address. The default now carries `localhost`, `127.0.0.1`, every detected
  LAN address, the hostname, its mDNS `.local` name and the
  `[network].public_url` host, each bare and with the API port, on top of
  `HAL0_MCP_ALLOWED_HOSTS`. This loosens nothing: a rebinding attack sends a
  name the attacker controls, which is not among them.
- **Bus events carry no run text.** `activity.db` is `0644`, while run text
  belongs in the `0600` ledger. A bus message about a run names only
  `<client> → <agent>` (and the status when the run ends); the event's data
  carries the run id.

## Rejected

- **Point the remote agent at Hermes's API on port 8642.** It works from the
  LAN today and is rejected. The key is one shared secret per profile and is
  the key hal0 itself uses, which ADR-0035 keeps out of every file hal0 writes,
  so handing it out copies it to another machine, and revoking one caller
  becomes a rotation for the whole gateway. Two callers with one key see and
  stop each other's runs. A run waiting on an approval or an answer is stuck
  unless someone watches its event stream. Teaching is not on that API. And
  nothing would appear in the dashboard, the dock or `hal0 doctor`.
- **Hermes's A2A plugin as the first step.** It is the right answer for agent
  frameworks calling Hermes and for Hermes-to-Hermes across machines, and the
  wrong first step here: inbound tasks are framed as untrusted peer input,
  remote callers cannot run slash commands, it has no memory or skill write
  path, and it opens a second port with a token scheme hal0 does not manage.
  If cross-framework peers appear, a later ADR can have hal0 provision the A2A
  platform the way it provisions `api_server`
  (`HAL0_MANAGED_PLATFORMS`, ADR-0035). Nothing here blocks that.
- **The word "peer".** It already names `hal0 agent peers` identity cards and
  `[upstreams].hal0_peer`, and a third meaning would make "peer" ambiguous in
  the CLI, the docs and the logs. The new thing is a **bridge client**, and the
  group is `hal0 bridge`.
- **Tables in `hal0.db`.** The file is `0644`. Key hashes and run text need
  `0600`, and widening one file's mode for one table would narrow every
  other table's protection.
- **Keys in `hal0.toml` or `api.env`.** Machine-minted credentials are machine
  state (anti-scar rule 7), and adding, revoking and rotating a client are row
  writes that must take effect on the next request, not edits to a config file.
- **A helper that prints `claude mcp add` lines from the box's admin or client
  key.** It makes onboarding a copy-paste, and it hands every remote agent the
  one key that opens everything, with no way to revoke one without rotating
  all. The same one-command onboarding is `hal0 bridge add`, with a key that
  opens only what its scopes name.
- **Trusting `X-hal0-Agent` for bridge clients.** It is a claim, not a
  credential (Context, point 3). A bridge principal's identity is the name in
  its key; the header is ignored for it.

## Consequences

- A box with no bridge clients is unchanged: the new tier needs a row in
  `bridge.db` to match, and the one lookup runs only for a bearer that starts
  with `hal0bk_`. Turning the bridge off needs a hal0-api restart.
- `bridge.db` is box state. It is preserved across updates like the rest of
  `/var/lib/hal0`, and a lost file means every bridge key stops working and
  each client is added again.
- Code that switches on `Tier` has to handle `"bridge"`; the auth matrix tests
  (`tests/api/test_auth_bridge_tier.py`) cover every scope set against
  `/mcp/agents`, `/mcp/memory`, `/mcp/admin`, `/api/*` and `/v1/*`, with
  `require_auth` on and off, from loopback and from a LAN peer.
- **A bridge key is as powerful as asking Hermes to do something, for every
  profile hal0 holds a key for. With the `admin` scope it is the agent-tier
  key ADR-0024 describes: the admin MCP server's tools, autonomous reads and
  writes included, and no REST.** Treat it as an operator credential.
  `SECURITY.md` says so. Leave `admin` off unless the remote agent operates the
  box. A key does not expire; `hal0 bridge revoke` ends it at once.
- Memory written through the bridge is the first memory whose namespace is
  bound to a credential: a client can no longer write into another agent's
  private bank by naming it in a header.
- The first change added no `hal0-agents` server: the lines `hal0 bridge add`
  printed for it pointed at a path that was not mounted, and
  `GET /api/bridge/me` was what proved a key worked. The second change landed
  the server and its run tools, the run ledger, the watcher, the
  `agent-bridge` doctor row and `hal0 bridge runs`. The teach tools and the
  shipped skill that tells Hermes how to answer a delegated run land in a
  third, and the dashboard card and the guide follow. Each applies the
  decisions above and records anything it learns as an amendment here.
- Hermes's own approvals still apply to what a delegated run does. hal0 does
  not rewrite a run's input: it is work the operator delegated, not untrusted
  web content.
- After the bridge is in use, an operator who bound Hermes's API server to the
  LAN for other callers can return it to loopback: Open WebUI's upstream path
  and the bridge both go through hal0-api.

## Relationship to other records

- **ADR-0004.** Amended: its "One MCP tool catalog, mapped onto existing
  routes" bullet describes the bundled `hal0-admin` and `hal0-memory`
  servers, and there is now a third, `hal0-agents`, bundled like the other two
  and not uninstallable. The rule that a tool ships only if it maps to an
  existing `/api/*` route holds for the admin server; the agents server maps
  onto Hermes's API and the memory dispatcher instead.
- **ADR-0024 (proposed, not implemented).** Item 1, a key tier accepted on
  `/mcp/*` and refused on the REST surface, is implemented here for remote
  agents: the `bridge` tier. Item 3, the admin MCP server calling REST as
  itself with an audit header, is implemented here for bridge principals only;
  the box's own agents still have their bearer forwarded. Item 2 (approve and
  deny need a person) is tracked as #3133 and item 4 (the agent-UID split) as
  #3134, both open. A bridge key cannot reach `/api/agent/approvals`, which is
  `/api/*`, but this record does not close the hole ADR-0024 describes for a
  caller that already holds the admin key.
- **ADR-0035.** The profile keys hal0 already holds are what the bridge
  forwards with. No new copy of a Hermes credential is written anywhere.
- **ADR-0040.** The precedent for a credential of a surface's own that is
  required even when `require_auth` is off, and for keeping a service's
  credential away from the admin key.

## References

- `src/hal0/bridge/keys.py` (key format, name rule, scopes),
  `src/hal0/bridge/store.py` (`BridgeStore`), `src/hal0/bridge/migrations/`,
  `src/hal0/config/paths.py` (`bridge_db`), `src/hal0/config/schema.py`
  (`BridgeConfig`), `src/hal0/install/perms.py` (the `bridge.db` rows),
  `src/hal0/cli/doctor_all.py` (`check_bridge_store`).
- `docs/reference/config-schema.mdx` (`[bridge]`),
  `docs/reference/cli-operator.mdx` (`hal0 bridge`),
  `docs/guides/connect-mcp.mdx`, `SECURITY.md` ("Bundled agent trust
  boundary").
- ADR-0002, ADR-0004, ADR-0024, ADR-0035, ADR-0040, ADR-0042.
