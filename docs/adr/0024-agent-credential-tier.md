# ADR-0024: Agent credential tier — agents stop holding the box admin key

## Status

**PROPOSED (2026-10-08).** Target: v1.5, shipped together with the
auth-on-by-default change so the access tiers are rewritten once. Nothing
here is implemented. Completes the open question ADR-0002 left for its
Option B ("Until that token is scoped down to something narrower than the
full box admin key … this has to be resolved before Option B can be called
done").

## Context

### What agents hold today

Every bundled agent authenticates to hal0 with a literal copy of the box
admin key:

- Hermes: `_write_driver_env()` writes `service_key(prefer="admin")` into
  `HAL0_MCP_TOKEN` in `/etc/hal0/agents/hermes.env`
  (`src/hal0/agents/hermes_provision.py:6578`); the MCP server blocks use
  the same key (`:6470`, and the other `prefer="admin"` call sites).
- pi coder: `src/hal0/agents/pi_coder/driver.py:430`.

ADR-0002 scheduled the agent-UID split (Option B) for 1.1. It has not
landed: `installer/systemd/hal0-agent@.service:31` is still `User=hal0`,
the same user as `hal0-api`.

### The approval gate does not hold against a key holder

The owner-approval queue is the control that is meant to stop an agent
taking destructive actions on its own. Two facts make it advisory once the
agent can make an arbitrary HTTP call:

1. `POST /api/agent/approvals/{id}/approve`
   (`src/hal0/api/routes/approvals.py:111`) is an ordinary ADMIN route. It
   does not check who enqueued the item or who is approving it.
2. The admin MCP server hides the approve tool from agents
   (`src/hal0/mcp/admin.py:2953`, "an agent approving its own … queued call
   would defeat the gate"), but the agent's token *is* the admin key, so
   the REST route itself accepts it.

Today the agent's path to an arbitrary HTTP call is the terminal /
code-execution toolsets, which are an explicit opt-in, default off
(`hermes_provision.py:1554-1585`, #1882). With the terminal on, a
prompt-injected agent can approve its own gated calls.

### Why a narrower key alone is not enough

- **Same UID.** While the agent runs as `hal0`, it can read `api.env` and
  `/proc/<hal0-api pid>/environ` (ADR-0002, Bypasses A and B), so it can
  pick up the admin key regardless of what token it was given.
- **The MCP server forwards the caller's token.** `_call_rest` re-attaches
  the agent's Bearer header unchanged when it calls the REST layer
  (`src/hal0/mcp/admin.py:1640`). A token that REST rejects would break
  every admin-MCP tool, approved or not.

## Decision (proposed)

Ship these together in v1.5. Each one alone leaves the hole open.

1. **An `agent` key tier.** A per-agent key, minted at agent install,
   stored only in that agent's env file. `security/exposure.py` accepts it
   on `/mcp/*` (the tool servers), `/mcp/memory`, and `/v1/*` (CLIENT
   routes). It is refused on every other ADMIN route, the `/api/*` REST
   surface included.
2. **Approve and deny need a person.** `/api/agent/approvals/{id}/approve`
   and `/deny` accept a browser admin session or the CLI acting as root.
   They refuse a bare Bearer key of any tier. Exact mechanism (session-only
   vs. a separate operator key) is settled in implementation, with tests
   that a key holder cannot approve.
3. **The admin MCP server calls REST as itself.** After its own policy and
   approval check, it calls REST with an internal service identity and an
   `X-hal0-Agent` audit header, instead of forwarding the agent's token.
   The REST audit trail records which agent asked.
4. **The agent-UID split from ADR-0002 Option B.** Agents run as a
   dedicated user with no read access to `api.env`, no sudoers seams, and a
   narrowed `ReadWritePaths=`. Without this, items 1-3 are bypassed by
   reading the admin key from disk.

## Options considered

- **Do nothing; keep the terminal off by default.** Holds only while no
  operator turns the terminal on and no other tool grows an HTTP path.
  Rejected as the end state; acceptable as the posture until v1.5.
- **Scoped token only (items 1 and 3), no UID split.** Cheaper, but the
  agent can still read the admin key from disk. Rejected: it looks like a
  boundary and is not one.
- **Approval-route fix only (item 2), now.** Small, and closes self-approval
  by Bearer key. Not enough on its own (the agent still holds full admin
  for every other route), but it can ship ahead of the rest if v1.5 slips.

## Consequences

- Agent installs and upgrades mint and migrate a new key; the upgrade path
  rewrites every agent env file and has to be idempotent.
- Every `prefer="admin"` call site in agent provisioning moves to the agent
  tier, with tests.
- The UID split carries the ADR-0002 Option B costs: unit `User=`, chown
  migration, `/run/hal0` sharing, new `install/perms.py` rows.
- `hal0 doctor all` gains a check that no agent env file holds the admin
  key.
- Shipping with auth-on-by-default means the tier model, exposure table and
  docs change once, in one release.

## Open questions for the operator

1. If v1.5 slips, ship item 2 (approval route) on its own first?
2. One `agent` key per agent, or one shared agent key? Per-agent is
   recommended: it makes the audit trail truthful and lets one agent be
   revoked alone.

## References

- ADR-0002 (agent credential isolation), Option B and its open question
- ADR-0013 (per-agent MCP client allow-list)
- ADR-0015 (MCP supervisor and Hermes exposure join)
- `docs/operate/auth.mdx` (current tier model)
