# ADR-0033: Agent chat is Hermes's own dashboard; hal0 does not proxy it

## Status

**ACCEPTED (2026-10-20, #2817).** Operator decision of 2026-10-20: delete
the chat proxy rather than repair it. This record amends ADR-0004, whose
"Sandboxed sibling unit" bullet said the browser reaches the agent through
`src/hal0/api/agents/chat_proxy.py`. The rest of ADR-0004 stands.

## Context

ADR-0004 runs Hermes as a loopback-only sibling unit
(`hal0-agent@hermes.service`) and had hal0-api bridge the browser to it: a
WebSocket and REST proxy mounted at `/api/agents/{agent_id}/` (`/events`,
`/submit`, `/session/handshake`, `/session/create`, `/session/resume`,
`/session/history`). The proxy authenticated to Hermes with an "embed token"
that `hal0 agent bootstrap hermes` wrote to `$HERMES_HOME/runtime.json`.

Its only client was `ui/src/dash/agents/chat/use-hermes-session.js`. The
v0.4 dashboard rework deleted it (#439, `40452fc8`), and nothing in hal0 has
called the proxy since. The dashboard's agent-chat slide-out talks to the
hal0-brain steward at `/api/brain/chat` instead.

The proxy also cannot work against the pinned hermes-agent
(`3ef6bbd201263d354fd83ec55b3c306ded2eb72a`, `VETTED_HERMES_REFS` in
`src/hal0/agents/hermes_provision.py`). It fails in three independent ways.
The line numbers below are in `hermes_cli/web_server.py` at that commit.

1. **Wrong credential.** hal0 sent its own random token as
   `Authorization: Bearer`. On a loopback bind, which is how hal0 runs it,
   Hermes's WebSockets accept only `?token=<_SESSION_TOKEN>`
   (`_ws_auth_reason`, `:16517`, comparing at `:16596`). A gated (public)
   bind takes `?ticket=` or `?internal=` instead and rejects `?token=`
   (`:16527-16590`). Neither path reads a Bearer header. Hermes generates
   `_SESSION_TOKEN` fresh at every start unless
   `HERMES_DASHBOARD_SESSION_TOKEN` is set (`:281`), and hal0 never set it.
   So Hermes refused the upgrade and hal0's sockets closed with 1011 before
   any traffic. A validation run on nightly 1.5.0-nightly.20261020175722
   reproduced exactly that.
2. **Missing channel.** `/api/events` (`:17738`) also requires a
   `?channel=<id>` (`_channel_or_close_code`, `:16876`) and closes with 4400
   without one (`:17754`). hal0 never sent it.
3. **REST methods that do not exist.** The `session/*` routes posted
   JSON-RPC envelopes to `/api/session.create`, `/api/session.resume` and
   `/api/session.history`. Hermes has no such routes: `session.create` is a
   JSON-RPC method of its TUI gateway, reached over `/api/ws`, and its REST
   session API is `/api/sessions…` (`:4216` onwards).

Hermes's dashboard has its own Chat tab (`/api/pty`, `/api/ws`,
`/api/events`), which is how operators chat with the agent today. The hal0
dashboard links to it when `HAL0_HERMES_PUBLIC_URL` is set
(`src/hal0/api/routes/config.py`).

## Decision

**Chat with a bundled agent is the agent's own dashboard. hal0 does not
proxy it.** hal0 keeps supervising, wiring and gating the agent as ADR-0004
describes, and does not put its own surface between the browser and the
agent's chat.

Removed in #2817:

- `src/hal0/api/agents/chat_proxy.py` and its router mount in
  `src/hal0/api/__init__.py`: the six routes above, the `tool.progress`
  coalescer and the proxy's own cookie re-check loop.
- The `runtime.json` embed token: its writer in the `install_artifacts`
  phase of `hermes_provision.py` (the phase still writes the seed TOML and
  the driver env file), and its row in `hal0 doctor perms`.
- `check_ws_origin_and_cookie` and `require_browser_auth` from
  `src/hal0/api/agents/_auth.py`, which only the proxy called.
- The `HAL0_HERMES_RUNTIME_JSON`, `HAL0_HERMES_HOST` and `HAL0_HERMES_PORT`
  environment variables, which only the proxy read.
- The proxy's tests, and the chat-proxy cases of the key-rotation tests.

Kept, because other surfaces use them:

- The session cookie (`hal0_session`), its secret and `POST /api/auth/login`.
  The dashboard signs in with them.
- The Origin allowlist and its same-origin fallback (`allowed_origins`,
  `_is_same_origin`), which the enforcement middleware applies to
  WebSocket upgrades and state-changing requests.
- `hal0.api.live_sockets` and the middleware's `guard_websocket`,
  which close the board feed and `/v1/realtime` sockets on a key rotation
  (#2801, #2812).

A host that ran an earlier bootstrap keeps its `runtime.json`. Nothing reads
it, and uninstalling the agent removes it with the rest of `$HERMES_HOME`,
so there is no cleanup step: a one-time sweep would be a compatibility path
with nothing to protect.

A hal0-native chat composer, if one is ever wanted, is a separate decision
and gets its own record. It would start from Hermes's real session
contract, not from this proxy.

## Consequences

- The `/api/agents/{id}/events`, `/submit` and `/session/*` routes return
  404. They ship in a minor release under `### Breaking`: no client used
  them and none of the proxied routes could work.
  `/session/handshake` only minted the cookie `POST /api/auth/login` mints.
- hal0 has two WebSockets left, the board feed and `/v1/realtime`. Both are
  re-checked only when a key rotates through the running API. The proxy was
  the only socket that also re-checked its cookie on an interval, so an
  expired session no longer closes any open socket. The next upgrade is
  refused instead.
- Chat needs `HAL0_HERMES_PUBLIC_URL` (or another route to Hermes's
  loopback dashboard) to be reachable from a browser. That was already true:
  the proxy never worked against the pinned Hermes.

## References

- #2817 (this removal), #439 (the client's removal), #2801 and its fix
  #2812 (closing open WebSockets on a key rotation)
- ADR-0004 (bundled agents), amended here
- `src/hal0/api/agents/_auth.py`, `src/hal0/api/live_sockets.py`,
  `src/hal0/api/auth.py`
- `docs/operate/auth.mdx`, "WebSocket close code 4401"
- NousResearch/hermes-agent `3ef6bbd2`, `hermes_cli/web_server.py`
