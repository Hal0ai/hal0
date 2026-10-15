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
2. **Tool counts, four surfaces — and the docs by NAME SET, not count.** Compare `tools/list`,
   `hal0 mcp list`, `GET /api/mcp/servers`, and the shipped docs. A mismatch between the REST
   view and the MCP view means the registry and the served surface have diverged. For the docs,
   regex the backticked tool names out of `docs/reference/mcp-tools.mdx`'s table rows and `comm`
   them against the served set — a raw count hides an 89-tool gap (rc.6: the page claimed "every
   tool" while naming 91 of 180, including 21 undocumented GATED/destructive tools). Also verify
   the connect guide's URLs are directly usable: every URL in `connect-mcp.mdx`'s Connect
   section must end in `/mcp` and answer an initialize POST with 200 on loopback (regression
   `mcp-docs-connect-url-and-tool-tables`).
3. Tool schemas are structurally valid (name, description, non-empty inputSchema).
4. **Annotations.** Every tool in `tools/list` must carry `annotations`, and `destructiveHint`
   must be `true` for the delete family (`model_delete`, `slot_delete`, …) and `false` for
   load/unload. Agents route safety decisions off these hints, so a lost or flipped annotation
   is silently dangerous. rc.5 baseline: admin 180/180 annotated, 106 `readOnlyHint: true`,
   13 `destructiveHint: true`.
5. **Declared capabilities are implemented.** For each capability advertised in the initialize
   response (prompts, resources), call the matching list method and require a real result rather
   than `-32601 Method not found`.
6. **Session scoping.** Replay a session id obtained from `/mcp/admin/mcp` against
   `/mcp/memory/mcp` and require rejection (rc.5 correctly returns `-32600 Session not found`).
   A cross-mount session leak is a security finding. Also send a `tools/list` with NO session id
   at all and require rejection (`-32600 Missing session ID`).
7. Call 1–2 clearly read-only tools per server (a status/list/recall tool — nothing that
   creates, mutates, or deletes) and judge the result.
8. **Error protocol.** Call a tool with (a) a missing required arg, (b) a wrong-typed
   required value, and (c) **unwrapped arguments** — the tool's fields passed flat with no inner
   `args` object. All three must return `isError: true`; (c) pins the required args-wrapper
   contract, which confused this lane's own first probe in rc.6. Then call one with an
   **unknown extra key** and record what happens: the inner `args` object is deliberately open
   (`known-issues.yaml: mcp-args-additional-properties-open`), so an ignored unknown key is
   expected — read that entry's `still_report_if` before filing anything.
9. `serverInfo` reports the hal0 version, not the FastMCP library version (rc.4 reported
   FastMCP 1.28.1).
10. Latency: note anything that takes more than a couple of seconds for a list call.
11. **isError vs REST parity, specifically on a resource in a NON-healthy lifecycle state** —
    regression `mcp-status-tools-iserror-on-error-state-resource`. Find (or induce) a slot in
    `state: error`, then call `slot_status`/`slot_by_name`/`slot_by_id` (or the equivalent
    status/show tool for any resource) against it and compare `isError` to the matching REST
    route's HTTP status for the SAME resource (`GET /api/slots/<n>` etc). `isError: true` for a
    resource read that the REST surface answers 200 for is a lying tool result, even though the
    JSON body inside is correct — it hides real data behind a false failure flag. This is a
    standing check now, not a one-off: the collision is keyed to a literal top-level `status`
    field name colliding with the wrapper's own error sentinel, so re-run it on whichever
    status/show tools exist each release, not just the ones this run happened to catch.

12. **tool_details vs tools/list, by name.** Compare the `tool_details` names in `/api/mcp/servers`
   against the `tools/list` name sets of each server, not only the counts (182 admin + 26 memory on
   1.4.0).
13. **Type errors are type errors.** Call a tool with a wrong-typed required arg
   (`{"args":{"name":123}}`) and assert the error is a type/validation error, not a coercion into
   `slot.not_found`. Remember args must be wrapped in `args`.

14. **Gated tools validate args before queuing approval.** Call `slot_logs` (and one other gated tool) with a wrong-typed arg and confirm the error comes back before an approval is queued.
15. **Annotation counts.** Count `readOnlyHint: true` and `destructiveHint: true` in `tools/list` against the rc.5 baseline (108 and 13 at last count) and report the delta.
16. **Docs at the release tag.** Compare `docs/reference/mcp-tools.mdx` names against the tool list at the exact release tag, not the working tree HEAD.
17. **`slot_list` count.** `count` should equal the number of `kind=="local"` rows in `/api/slots`; the synthetic `hal0` row makes it +1 (#2697, regression `mcp-slot-list-counts-synthetic-hal0`). Every REAL slot entry must have `id` and `state`; the synthetic row has both null by design (`slot-list-rest-includes-synthetic-hal0`). Non-string path args being stringified is `mcp-admin-path-args-are-stringified`: record `known`.

18. **Gated tools reject malformed calls before queueing.** For every tool in `GATED_TOOLS` call with empty args and require `isError: true mcp.missing_arg`, not `pending_approval` (#2722). Then deny or cancel every approval you queued (`POST /api/agent/approvals/<id>/deny`) and assert `GET /api/agent/approvals` is empty: the inbox is shared state.

19. **Docs names are a subset of served names**, not only a superset: a renamed or removed tool still named in `mcp-tools.mdx` is a docs defect.

20. **`slot_list` count and marker.** `count` equals the number of `kind: local` rows in `/api/slots`, or the composite `hal0` row is marked; count = real + 1 with no marker is #2697 (known, record it; #2688 also dropped `_synthetic`).

## Carry-forward

The MCP surface is how other agents consume hal0, so *silent* protocol wrongness here is worse
than elsewhere — a client cannot tell it is being lied to. Weight `isError`, schema validity,
and session handling as `major` by default.
