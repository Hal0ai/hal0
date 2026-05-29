# ADR 0020 — Localhost-callback-only OAuth PKCE (for OpenRouter BYOK)

- **Status:** Accepted
- **Date:** 2026-05-29
- **Drivers:** OpenRouter integration Phase 0 (V1 OpenRouter as Hermes
  upstream); DA must-fix #4 (`openrouter-research-2026-05-28/notes/da-or.md`)
- **Related:** ADR-0012 (removed auth + Caddy); ADR-0019 (v0.3 Hermes
  integration); future ADR-0021 (OpenRouter as registered upstream)

## Context

hal0-api binds `0.0.0.0:8080` with no Bearer authentication (ADR-0012).
The LAN-trust posture works because every privileged surface (slot
config, hermes restart, hermes-admin MCP) is gated either by being
loopback-only or by being deliberately operator-aware ("you can break it
from the LAN — that's your network").

OpenRouter's BYOK + delegate-routing UX requires OAuth 2.0 PKCE: the
hal0 dashboard launches an authorize URL, the user signs in at
`openrouter.ai`, OR redirects back to a callback URL on hal0 with an
authorization code, hal0 exchanges the code for a refresh token + access
token, persists them, and uses them on subsequent requests.

The callback URL is the new attack surface. If hal0 advertises a
callback at `http://<lan-ip>:8080/api/openrouter/auth/callback`, any LAN
host can:

- Race a real OAuth redirect to inject a code from an attacker's OR
  session.
- DoS the callback with bogus codes to fill logs.
- (Mitigated by PKCE) replay a code without the verifier still fails.

Even with PKCE, the LAN-trust threat model strains. ADR-0012's posture
is "every privileged surface is operator-aware" — adding a
credential-storage surface that LAN hosts can poke isn't operator-aware.

## Decision

The OAuth PKCE callback URL is constrained to
`http://127.0.0.1:<port>/api/openrouter/auth/callback`.

Concretely:

- hal0-api keeps binding `0.0.0.0:8080` for the existing dashboard + tool
  surfaces.
- The callback route `/api/openrouter/auth/callback` is registered, but
  a per-route guard rejects every request whose `request.client.host` is
  not loopback (`127.0.0.1`, `::1`, or the literal `localhost`).
- The authorize URL passed to `openrouter.ai` uses
  `redirect_uri=http://127.0.0.1:8080/api/openrouter/auth/callback`.
- To complete the flow, the user must be ON the hal0 host (typing into a
  browser tab there) or have an SSH tunnel forwarding
  `127.0.0.1:8080` to their laptop's `127.0.0.1:8080`.

## Consequences

**Wins:**

- ADR-0012's LAN-trust model holds; no new attack surface visible from
  the LAN.
- Refresh-token storage at
  `/var/lib/hal0/agents/{id}/personas/{pid}/openrouter.toml` (chmod
  `0600`) is the only credential at rest — same protection as
  `runtime.json` from v0.3.
- PKCE + localhost-only = belt-and-suspenders against code-injection
  races.
- No reverse-proxy / Caddy dependency reintroduced (ADR-0012 §"What was
  deleted").

**Trade-offs:**

- Users connecting to hal0 from a remote browser must SSH-tunnel
  `:8080` to complete the OAuth handshake (one-time per persona /
  account).
- Some friction on first-time setup; documented in the operator manual
  + onboarding tour.
- A future dashboard hosted at `hal0.thinmint.dev` (Traefik vhost)
  cannot complete the flow without either (a) dual-binding the callback
  to the public URL with a separate auth model OR (b) running the flow
  from the LXC host's local browser.

**Deferred to v0.4 (if there's real demand):**

- Dual-bind callback: public URL + Bearer token + nonce + Origin
  allowlist.
- This explicitly re-opens ADR-0012 and requires a new ADR.

## Alternatives considered

1. **Drop OAuth, demand the user paste their OpenRouter API key
   directly.**
   Simpler. No callback. Key lives at
   `/var/lib/hal0/agents/{id}/personas/{pid}/openrouter.env` (chmod
   `0600`). Loses OR's PKCE-delegate flow (where end-users authorize OR
   access without exposing their downstream provider keys to hal0).
   User experience: paste a key VS click a button. The button is the
   OR-UX-aligned choice. **Rejected** for v0.3+; revisit if operator
   feedback shows the localhost-tunnel friction is meaningful.

2. **Public callback + Bearer + Origin allowlist.**
   Reverts ADR-0012's posture entirely. **Rejected** for v0.3+;
   possible v0.5+ if hal0 grows hosted-dashboard demand.

3. **Bind callback to LAN, accept the risk.**
   **Rejected** — even with PKCE, the credential-storage surface is
   operator-aware in a way LAN hosts aren't trusted to be.

## Implementation pointer

V1 (the OpenRouter-as-Hermes-upstream PR) will wire the actual callback
flow into `src/hal0/api/openrouter/` (NEW module). A minimal route
skeleton lands in this PR (`status=501`) so the URL is registered and
the loopback guard is enforced from day 1.

## Quotes

ADR-0012 §"Context" — "Every operator who has run it for real puts it
behind an existing upstream proxy [...] The bundled Caddy was middleman
[...] doubled the failure surface."

`openrouter-research-2026-05-28/PLANNING.md` §5 Q1 — "localhost-only
first; new ADR-0020 documents the constraint."
