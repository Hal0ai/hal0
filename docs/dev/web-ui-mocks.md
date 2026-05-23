# Web UI mock harness

The hal0 dashboard v2 ships ahead of several backend surfaces (multi-modal
slots #142, metrics aggregator #145, FLM/NPU install #146, MCP server
registry #180). To keep frontend work unblocked without speculative
backend wiring, the UI carries a small runtime mock layer that
substitutes responses for whitelisted endpoints.

Slice #166 introduced the harness. Slice #14 #180 will use the same
shapes for the MCP store.

## Activation modes

| Mode | Trigger | Behaviour |
|------|---------|-----------|
| **Forced mock** | `VITE_MOCK_LEMONADE=1` at build/dev time | Every allowlisted URL returns a baked response with no network call |
| **404 fallback** | (default) | Real `fetch()` runs; on 404 for an allowlisted URL, the mock response is substituted with `console.warn` |
| **Pass-through** | (default) | Non-allowlisted URLs and non-404 responses pass through untouched |

There is **no** global `window.fetch` monkey-patch. Stores opt in by
importing `mockFetch` from `@/composables/useMock` and swapping their
`fetch()` calls.

## Allowlist (slice #166)

Each row maps a URL regex to the backend issue that will retire it.

| Issue | Pattern | Builder |
|-------|---------|---------|
| #145 | `/v1/stats` | last-request snapshot (TTFT, decode tok/s, prompt/output/input tokens) — mirrors PR-12 #179 emission shape |
| #142 | `/api/backends` | `{backends, lemonade}` envelope from ADR-0008 §5 |
| #142 | `/api/backends/:id` | per-backend snapshot with `loaded` model list |
| #180 | `/api/mcp/servers` | 8 servers (2 bundled + 4 installed + 1 failed + 1 installing + 1 stopped) |
| #180 | `/api/mcp/clients` | 3 connected clients (Claude Code, Cursor, Claude Desktop) |
| #180 | `/api/mcp/catalog` | 12-item browse catalog with categories |
| #180 | `/api/mcp/servers/:id` | per-server detail |
| #142 | `/api/capabilities/personas` | persona rollup (not covered by today's `/api/capabilities`) |

When a real endpoint lands, drop both the matcher in `MOCK_ALLOWLIST` and
the dispatch builder in `ui/src/composables/useMock.js`. Store callers
keep using `mockFetch` — the only difference is the 404 fallback never
trips.

## Store opt-in pattern

```js
// stores/backends.js
import { mockFetch } from '@/composables/useMock'

async function fetchAll() {
  const res = await mockFetch('/api/backends')
  // …consume `res` exactly like `fetch()` — same Response interface.
}
```

`mockFetch` returns a real `Response` (synthetic when substituted) so
existing parse logic stays unchanged. Add allowlist entries with care
— shapes are a contract the backend will eventually match.

## Playwright fixture parity

Playwright runs under Node + tsx without the Vite transform, so it
cannot import `useMock.js` directly (the file touches `import.meta.env`).
The shapes live in `ui/tests/e2e/fixtures/mock-data.ts` as a parallel
TS module that **must stay structurally identical** to `MOCK_DATA` in
`useMock.js`. Any PR that mutates one MUST mutate the other in the
same commit.

Helpers in `ui/tests/e2e/fixtures/apiMock.ts`:

- `mockMcpEndpoints(page)` — pre-routes `/api/mcp/{servers,clients,catalog,servers/:id}` to MOCK_DATA shapes. Useful for slice #14 specs.
- `mockV1Stats(page)` — pre-routes `/v1/stats` for the lemonade store's 5s poll.

Call these after `installDefaultMocks(page, mockState)` — Playwright
matches routes in reverse-registration order.

## Contract policy

1. Mocks are a **shape contract**. Backend implementations MUST match
   the field names and types listed in the builder functions in
   `useMock.js`.
2. Shape drift is a UI-breaking event. Coordinate with the backend
   issue owner before changing any field.
3. New mock entries require an issue number in the allowlist row. No
   un-owned mocks — every row has a retirement plan.
4. Mock data should be **minimal**, not exhaustive. Cover the fields
   the views actually consume.

## Retirement plan

| Issue | Owner | Status | Retirement step |
|-------|-------|--------|-----------------|
| #142 multi-modal slots | UI + backend | mocked | When `/api/slots` returns the multi-modal shape, drop `/api/backends/*` mocks |
| #145 metrics aggregator | backend | LANDED on main as PR-12 #179 | When `feat/dash-v2-rework` rebases on main, drop `/v1/stats` mock |
| #146 FLM install | backend | mocked | When `flm:npu` reports real `version + state`, no change to mock (already correct shape) |
| #180 MCP registry | UI slice #14 + backend | mocked | When `/api/mcp/*` ships, drop all 4 MCP entries |

## Files

- `ui/src/composables/useMock.js` — `MOCK_DATA`, `MOCK_ALLOWLIST`, `mockFetch`, `useMock`.
- `ui/tests/e2e/fixtures/mock-data.ts` — Playwright-side shape mirror.
- `ui/tests/e2e/fixtures/apiMock.ts` — `mockMcpEndpoints`, `mockV1Stats` helpers.
- `ui/src/stores/backends.js` — first consumer (refactored from inline 404 fallback).
- `ui/src/stores/lemonade.js` — `/v1/stats` poll via `mockFetch`.
