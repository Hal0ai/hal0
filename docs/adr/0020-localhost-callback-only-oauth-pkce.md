# ADR-0020: Localhost-callback-only OAuth PKCE (OpenRouter BYOK)

## Status

**ACCEPTED (2026-05-29).** The loopback guard shipped as a Phase 0
scaffold; the PKCE code-for-token exchange itself is still pending (see
Status below). Reconstructed here against the current state; the
original write-up lived in the gitignored
`docs/internal/adr/0020-localhost-callback-only-oauth-pkce.md` (see
`ARCHITECTURE.md` "Decision records").

## Context

`hal0-api` binds `0.0.0.0:8080` with no Bearer authentication (ADR-0012).
That LAN-trust posture holds because every privileged surface is either
loopback-only or deliberately operator-aware.

OpenRouter's bring-your-own-key delegate-routing flow needs OAuth 2.0
PKCE: the dashboard opens an authorize URL, the user signs in at
OpenRouter, OpenRouter redirects back to a callback URL on hal0 with an
authorization code, and hal0 exchanges the code for tokens. A callback
URL reachable from the LAN would let any LAN host race a real redirect
with an attacker-supplied code, or spam the endpoint — PKCE mitigates
replay, but a credential-issuing endpoint that any LAN host can poke
still strains ADR-0012's "every privileged surface is operator-aware"
posture.

## Decision

**The OAuth PKCE callback is constrained to
`http://127.0.0.1:<port>/api/openrouter/auth/callback` — loopback only,
enforced per-route rather than by a global middleware** (a global guard
would force allowlisting the rest of the deliberately open-LAN API).

- `is_loopback_host()` / `require_loopback()`
  (`src/hal0/api/openrouter/_loopback.py`) accept only `127.0.0.1`,
  `::1`, and the literal `localhost` — a strict allowlist, not a
  `127.0.0.0/8` CIDR test, so a spoofed header can't widen it.
- A non-loopback caller gets `403 loopback_required`, naming this ADR and
  telling the operator to complete the flow on the hal0 host or over an
  SSH tunnel to `127.0.0.1:8080`. 403, not 404: the callback URL's
  existence is not a secret worth hiding.
- The authorize URL's `redirect_uri` is the loopback address; completing
  the flow from a remote browser requires being physically at the hal0
  host or SSH-tunnelling the port.

## Consequences

- ADR-0012's LAN-trust model holds — no new attack surface is visible
  from the LAN.
- Remote-browser operators pay a one-time SSH-tunnel setup cost to link
  an OpenRouter account.
- A future publicly-hosted dashboard cannot complete this flow without
  either dual-binding the callback behind its own auth model or running
  the flow from the host's local browser — explicitly deferred, and
  re-opens ADR-0012 if pursued.

## Status of the implementation

As of this writing, `GET /api/openrouter/auth/callback`
(`src/hal0/api/openrouter/auth.py`) is a Phase 0 scaffold: the route is
registered and the loopback guard is enforced, but the handler returns
`501` — the PKCE code-for-token exchange and refresh-token persistence
are still pending a follow-up PR. The loopback constraint this ADR
decides is already enforced against every request, including in the
current 501 state.

## References

- `src/hal0/api/openrouter/_loopback.py` — `is_loopback_host`,
  `require_loopback`
- `src/hal0/api/openrouter/auth.py` — the Phase 0 callback route
- ADR-0012 — the no-inbound-auth, LAN-trust posture this ADR works within
