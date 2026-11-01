# ADR-0046: Approval decisions need a session or root

## Status

**ACCEPTED on the operator's merge of the change that adds this record (#3133).**
Implements item 2 of ADR-0024 ahead of the rest of that record, which stays
PROPOSED. This reverses the answer ADR-0024 recorded on 2026-10-08 ("ship item 2
on its own first? no"), for the reasons under "What this is worth". It is a
decision for the operator to take with those limits in view, which is why the
change is a manual merge.

## Context

`POST /api/agent/approvals/{id}/approve` and `/deny` checked the ADMIN tier and
nothing else. The bundled agent holds a literal copy of the admin key when the
box has one (`HAL0_MCP_TOKEN`, `service_key(prefer="admin")`), so a prompt-injected
agent with the terminal toolset could approve its own queued call with one
`curl`. The admin MCP server hides the tools (`EXCLUDED_TOOLS`), but the route
behind them did not know the difference.

Three facts shape every option:

- `hal0-api` and the agent run as the same user, `hal0`. Anything `hal0` can read,
  the agent can present: `api.env`, `/proc/<pid>/environ`, and the session secret
  `/var/lib/hal0/agents/secret.bin` (ADR-0002, Bypasses A and B).
- Loopback origin proves nothing. The agent is on loopback too.
- The installer deliberately mints no admin key (`installer/install.sh`, "Client
  key"), so the default box has none. Its agent holds the client key, and nothing
  on the box can sign in.

## Decision

Both routes run `hal0.api.auth.approval_decision_refusal` at every posture, inside
the audit block, so a refused attempt leaves an error row:

| Caller | Box with an admin key | Box with no admin key |
| --- | --- | --- |
| Valid session cookie | decides | decides |
| Any `Authorization: Bearer` or `?api_key=`, valid or not | `403 auth.forbidden` | `403 auth.forbidden` |
| No credential | `401 auth.required` | decides |

`GET /api/agent/approvals` and `/events` keep their ordinary ADMIN posture.

### The CLI asks the kernel

`hal0 agent approvals approve|deny` no longer uses REST. hal0-api listens on
`/var/lib/hal0/approvals.sock` (mode `0600`, `hal0.mcp.approval_socket`), reads
`SO_PEERCRED` on each connection and refuses any peer whose UID is not 0, before
reading its request. The audit actor is `cli:root`. The command refuses to run
unless it is root, and has no remote form.

Rejected:

- **A token or key on disk.** A credential `hal0` can read is what this exists to
  refuse.
- **A root-held signing key, with hal0-api verifying.** It needs a provisioning
  step and a migration, and the public half would have to live where `hal0` cannot
  replace it. `/etc/hal0` is `hal0:hal0 2775`.
- **Running the CLI through a sudo wrapper.** That grants `hal0` itself the very
  path being closed.

The socket lives under `var_lib()` and not `/run/hal0` because only `hal0-agent@`
declares that runtime directory, and an update does not rewrite the API unit.

A same-UID process can unlink the socket and listen in its place. That can make
the CLI report a false outcome or fail, but it cannot produce a decision, since
only hal0-api holds the queue.

### The conflict on a box with `require_auth` off

These two wants conflict, and only the first can be fully kept on a box with an
admin key:

1. The agent on the box must not decide.
2. The operator on the box must decide without signing in, which is what "auth
   off" has always meant over loopback.

An anonymous browser and an anonymous `curl` from the agent look identical, so
**a box with an admin key asks for sign-in to decide, even over loopback and with
`require_auth` off.** That is the one new friction here. The dashboard's global
mutation handler answers the `401` with its sign-in drawer and retries, so the
operator is asked for the key once and the rest of the dashboard stays open as
before.

**A box with no admin key has no gate.** Nothing there can sign in, so refusing an
anonymous caller would strand the dashboard, and keyless is open by design
(#1822). An agent on that box is refused when it sends the client key it was given,
but it can decide by sending no key at all. The fix for that box is to configure an
admin key.

## What this is worth

Less than it looks. It closes the one-`curl` path and turns a self-approval from
something an agent can stumble into with its provisioned credential into something
it has to set out to do. It is not a boundary, and it does not make the inbox the
real gate ADR-0004 promises. A process running as `hal0` can still:

1. **Trade the admin key for a session.** `POST /api/auth/login` accepts the key
   the agent holds and returns a valid cookie. One extra `curl`.
2. **Sign its own session.** `secret.bin` is `0600` owned by `hal0`, the account the
   agent runs as, and a valid signature is all `verify_session_cookie` checks. This
   needs no key at all.
3. **Skip the queue.** The queue gates MCP tool calls only. The REST route behind a
   gated tool (`DELETE /api/slots/{name}` behind `slot_delete`) checks the tier and
   accepts the admin key directly, so no approval is ever asked for.
4. **Decide anonymously** on a box with no admin key, as above.
5. **Reach the socket as root** through the root-equivalence SECURITY.md describes,
   or attach to hal0-api with `ptrace` where Yama allows it.

Items 1 to 3 and 5 close only with ADR-0024 items 1 and 4: the agent stops holding
the admin key, and stops being the same UID. `tests/api/test_agent_self_approval.py`
pins 1, 2 and 4 as known gaps, so that work has to rewrite them.

## Consequences

- Scripts and agents that approve or deny with a key get `403`. Nothing shipped
  does. The release-validation lanes that cleaned up with `POST .../deny` now use
  `sudo hal0 agent approvals deny`.
- Deciding from the CLI needs `sudo`. Off the box, use the dashboard.
- `AuthPrincipal.source` is no longer purely diagnostic: this check reads it.
- `hal0 agent approvals approve` now exits non-zero when the approved call failed;
  it used to print "Approved" either way.

## References

- ADR-0002 (agent credential isolation), ADR-0004 (bundled agents)
- ADR-0024 (agent credential tier), item 2
- ADR-0043 (agent bridge): bridge keys were already refused on these routes
- #3133, #3134, #1822
