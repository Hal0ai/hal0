# Lane: mcp (read-only)

hal0 bundles two MCP servers — `hal0-admin` (~180 tools) and `hal0-memory` (~26). Validate the
protocol, the tool inventory, and a couple of genuinely read-only tool calls.

## Getting connected

Discover the mount paths rather than assuming them: `hal0 mcp list` / `hal0 mcp status <server>`
on the box, plus the OpenAPI route list. Note that in rc.4 the URL the CLI advertised was not
the URL that worked (`/mcp/admin` → 405; the real endpoint was `/mcp/admin/mcp`) — **check
whether the advertised URL is directly usable, because that is itself a check.**

Speak MCP streamable-HTTP JSON-RPC with curl from the box (`127.0.0.1`), not from the LAN: the
`/mcp/*` mount enforces a localhost-only DNS-rebinding floor and answers LAN requests with
421 (see `known-issues.yaml: mcp-lan-421-invalid-host`).

* `POST initialize` with `protocolVersion` and `capabilities {}`
* header `Accept: application/json, text/event-stream` — responses may be SSE-framed
* carry the `Mcp-Session-Id` from the initialize response
* `notifications/initialized`, then `tools/list`

## Checks

1. Protocol handshake completes; session id is honoured.
2. Tool counts match what the docs and `hal0 mcp` claim, per server. A drift in count between
   releases is worth reporting even if nothing is broken — it usually means a tool was added or
   lost unintentionally.
3. Tool schemas are structurally valid (name, description, non-empty inputSchema).
4. Call 1–2 clearly read-only tools per server (a status/list/recall tool — nothing that
   creates, mutates, or deletes) and judge the result.
5. **Error protocol.** Call a tool with a deliberately wrong argument. The JSON-RPC response
   must carry `isError: true` — rc.4 returned `isError: false` on argument errors, which makes
   every client treat a failure as a success.
6. `serverInfo` reports the hal0 version, not the FastMCP library version (rc.4 reported
   FastMCP 1.28.1).
7. Latency: note anything that takes more than a couple of seconds for a list call.

## Carry-forward

The MCP surface is how other agents consume hal0, so *silent* protocol wrongness here is worse
than elsewhere — a client cannot tell it is being lied to. Weight `isError`, schema validity,
and session handling as `major` by default.
