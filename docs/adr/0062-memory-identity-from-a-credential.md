# ADR-0062: A memory identity comes from a credential, not from `X-hal0-Agent`

## Status

**ACCEPTED (2026-11-16).** Fixes #3569 for v1.8.0. Amends ADR-0043 (Context,
point 3, which recorded the hole, and the Consequences line that said bridge
memory was the first memory bound to a credential) and ADR-0005 (who may read
a `private:<agent>` namespace).

## Context

A memory identity does two jobs. It names the `private:<id>` namespace a
private write lands in, and it is the owner whose `visibility:private` notes a
read may see (`hal0.memory.read_scope.reflect_visibility_scope`). Outside the
bridge (ADR-0043) that identity was the `X-hal0-Agent` request header, read by
`client_id_resolver` in `src/hal0/api/mcp_mount.py` and `_agent_id` in
`src/hal0/api/routes/memory.py`, and nothing checked it. So any caller that
could reach `/mcp/memory` or `/api/memory/*` (the holder of the box client
key, or anyone at all on a box with auth off) could send
`X-hal0-Agent: hermes` and read Hermes's private notes, or any agent's. #3567
refused reserved names for bridge keys; it did not cover a name claimed
through the header.

Who sent the header, as of this change:

| Caller | Surface | Credential it carried |
|---|---|---|
| Hermes, default profile (`hermes`) | `/mcp/memory`, `/mcp/admin` via `config.yaml` | the box service key (`service_key(prefer="admin")`, which is the client key on a standard install: the installer mints no admin key) |
| Hermes, named profiles (`hermes__<name>`) and `hermes__hal0-brain` | the same | the same |
| Hermes memory provider plugin | `/api/memory/*` (REST) | none |
| Pi (`pi`) | `/mcp/memory`, `/mcp/admin` via `~/.pi/agent/mcp.json` | the box service key |
| `hal0 agent` CLI (`hermes`, `hal0-cli`) | `/api/memory/search`, `/delete` | none |
| Open WebUI, coding-agent plugins | `/mcp/*`, `/api/memory/compat` | a bridge key already; the header is ignored |

So the box key cannot tell Hermes from any other client-key holder, and the
REST plugin carries no credential at all.

The direction agreed in triage was a managed bridge key per Hermes profile.
That does not fit, for four reasons found in the code:

1. The names hal0's own agents use (`hermes`, `hermes__*`, `pi`, `hal0*`) are
   exactly the names ADR-0043 Decision 1 reserves, and the auth middleware
   refuses a bridge principal under a reserved name on every path (#3560).
   Giving Hermes a bridge key means either renaming its identity, which
   strands every private note it already owns (they are tagged
   `agent:hermes`), or carving an exception into the reserved list.
2. A bridge key is refused on every `/api/*` route but `/api/bridge/me` and
   `/api/memory/compat`. The Hermes memory provider talks to `/api/memory/*`.
3. `[bridge].enabled` can be off, and then no bridge key works at all.
4. Every bridge key carries the `agents` scope: it can start and steer Hermes
   runs. The memory identity needs none of that.

## Decision

1. **The header is a claim, the identity needs a credential.** On
   `/mcp/memory`, `/mcp/admin` (its memory tools) and `/api/memory/*`, the
   caller's memory identity is, in order:
   - the bridge client's name, when the request carries a bridge key the auth
     middleware admitted (unchanged; the header is ignored);
   - the `X-hal0-Agent` value, when the request also carries that agent's
     **identity key** in `X-hal0-Agent-Key`;
   - otherwise `anonymous`. The header is then an audit label only
     (`hal0.api._audit` still records it): shared memory is readable and
     writable, no private note is readable, and a private write is refused
     with `400 memory.namespace_invalid`.
2. **A reserved name claimed without its key is refused.** If the header names
   a reserved identity (`hal0.bridge.keys.is_reserved_name`, the one list;
   `anonymous` excepted, since it means no identity) and the key does not
   match, `/mcp/memory` and `/api/memory/*` answer `403
   bridge.client_name_reserved`, the code a bridge key under a reserved name
   gets. `/mcp/admin` is not refused, because its other tools do not depend on
   the memory identity; its memory tools act as `anonymous`.
3. **The identity key is derived, not stored.** `hal0.agent_identity`:
   `hal0ak_` + base64url(HMAC-SHA256(secret, `"hal0-agent-identity/v1\0" +
   <id>`)), with a 32-byte secret in `/var/lib/hal0/agents/identity.bin`
   (owned by the directory's owner, `hal0`; mode `0600`). Writing the key again
   always yields the same value, so provisioning, the hal0-api startup sweep
   and a repair mirror it and never rotate it. Verification is one HMAC with
   no store read. The secret is created by the service user (or by root inside
   an existing `agents/` directory, handed to that directory's owner); a
   process that may do neither sends the bare header, or, for a reserved name,
   no header at all (`identity_headers`). Deleting the file revokes every
   agent key at once; the next write of a key creates a new secret. The
   `hal0ak_` prefix joins the redactor's token prefixes. The secret is used
   only as a regular file (opened without following a link) of at least 32
   bytes, owned by root or `hal0`, with no group or other bits; anything else
   is refused and logged once as `hal0.agent_identity.secret_refused`. Only
   root or `hal0` may create it; root hands the file, and a root-owned
   `agents/` directory, to the `hal0` user and group (the Pi installer, which
   may run before Hermes, also creates `agents/` as `hal0`). The perms table
   declares the file, and the `agent-identity-secret` doctor row reports it.
   Keys exist only for reserved names
   (`is_reserved_name`): every other name is one a bridge client may hold,
   and its identity comes from its bridge key alone.
4. **hal0 writes the key for its own agents.**
   - Hermes, every profile: `X-hal0-Agent-Key` sits beside `X-hal0-Agent` on
     the `hal0-admin` and `hal0-memory` entries of the profile's
     `config.yaml`. Named profiles and `hal0-brain` get it from their existing
     wiring, which hal0-api re-runs on every start and on its periodic
     upstream reconcile. The default profile gets it from provisioning and,
     on an upgraded box, from `converge_default_profile_agent_key`, which
     hal0-api runs at startup. The provisioning overlay layers it through the
     YAML deep-merge, never through `hermes config set` argv, which any local
     user can read in `/proc`.
   - Each profile is keyed only for the identity its location implies
     (`hermes` for the default profile, `hermes__<name>` for a named one, the
     brain constant for `hal0-brain`); an entry naming any other identity is
     left without a key. A profile that uses the hal0 memory provider also
     gets `hal0: {agent_key: {agent, key}}`, and so does one opted out of
     hal0's MCP wiring (`hal0: {wire_mcp: false}`), whose own memory still
     needs it.
   - The Hermes memory provider reads the key from `hal0.agent_key`, else
     from the `hal0-memory` (or `hal0-admin`) entry of its own profile's
     `config.yaml`, using only a record that names its own agent id. It
     re-reads the file when it changes, so a key written after the gateway
     started is sent from the next call on.
   - Pi: `~/.pi/agent/mcp.json` gets the key whenever the driver writes it
     (`sudo hal0 agent install pi`, `sudo hal0 update component pi`).
   - The `hal0 agent` CLI and the provisioner's own probes send it when they
     can derive it.
   - The key is never sent to a third-party MCP server (`hal0.mcp.probe`
     builds those headers and is unchanged).

## Rejected

- **A managed bridge key per Hermes profile.** See Context, points 1 to 4.
- **Trusting the admin tier to claim any identity.** A standard install has
  no admin key; the service key Hermes presents is the client key, which other
  companions hold too.
- **Treating a reserved, unkeyed claim as `anonymous` without a 403.** Quieter
  during an upgrade, but a misconfigured or impersonating caller would write
  shared notes under no name instead of failing where someone sees it.

## Consequences

- An upgraded box converges without a repair. `hal0 update` mirrors the new
  memory provider plugin (`mirror_hermes_plugins`); the first hal0-api start
  after it writes the key into the default profile
  (`converge_default_profile_agent_key`) and every named profile
  (`wire_named_profiles`, `_phase_brain_profile_mcp_wire`), and, only on the
  boot that wrote the default profile's key, `try-restart`s the gateway and
  the agent unit so both load the new config and plugin. Between hal0-api's
  start and that restart, the old plugin's bare `X-hal0-Agent: hermes` gets
  403 on REST memory. If the restart fails (the seam refuses it), the warning
  `hermes.default_profile_agent_key_restart_failed` names the units, and a restart
  of `hermes-gateway` or `hal0 agent reprovision hermes --repair` finishes it.
- Pi needs `sudo hal0 update component pi` (Pi lives in the installing user's
  home, which hal0-api cannot write). Until then its `hal0-memory` server gets
  403 and its `hal0-admin` memory tools act as `anonymous`.
- A third-party caller that relied on a bare header for private memory loses
  it. The supported route is a bridge key (`hal0 bridge add <name>`), whose
  name is its identity. Its existing private notes stay where they are and are
  readable again by a bridge client of the same name, if that name is not
  reserved.
- Anything running as the `hal0` user can read `identity.bin`, as it can read
  `bridge.db`. This change does not narrow the same-UID risk in `SECURITY.md`.

## Relationship to other records

- ADR-0043: bridge keys stay the credential for remote agents; this record is
  the credential for hal0's own. The reserved-name list is shared.
- ADR-0005: the `private:<agent>` namespace is reached only by the identity
  this record defines. The one route that reached a Hermes profile's private
  namespace with another principal's credential, the ADR-0043 Decision 10
  teach tools (`agent_memory_add` / `agent_memory_search` on `/mcp/agents`),
  is closed by this change: they read and write the shared namespace only.
