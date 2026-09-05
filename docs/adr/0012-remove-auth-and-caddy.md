# ADR-0012: Remove auth and Caddy entirely

## Status

**ACCEPTED (2026-05-23).** Shipped for v0.3. Reconstructed here against
the current state; the original write-up lived in the gitignored
`docs/internal/adr/0012-remove-auth-and-caddy.md` (see `ARCHITECTURE.md`
"Decision records").

## Context

An earlier decision moved auth into FastAPI and reduced the bundled
Caddy reverse proxy to a TLS-only terminator: a fresh install locked
behind a password, a one-time-password lockfile minted at install, and a
session-cookie/Bearer-token auth surface gating the dashboard and
`/v1/*`.

In practice, every real hal0 deployment sits behind an existing upstream
proxy the operator already runs (Traefik, Cloudflare Tunnel, nginx). The
bundled Caddy was a middleman with its own failure mode
(`hal0-caddy.service` could fail independently of `hal0-api`) and its own
cert story. The first-run password step gated nobody on a trusted LAN,
and on a hostile network the real auth was the upstream proxy's anyway.
The auth surface it protected cost roughly 6,000 lines of code and tests
across `src/hal0/auth/`, `src/hal0/api/auth/`,
`src/hal0/api/middleware/auth.py`, `src/hal0/api/routes/auth.py`, Caddy's
packaging, and the corresponding test suites.

## Decision

**Remove the entire FastAPI auth surface and the bundled Caddy reverse
proxy. Document upstream-proxy patterns as the recommended way to add
auth and TLS on a hostile network.** `hal0-api` binds `0.0.0.0:8080` with
no authentication of its own
(`ARCHITECTURE.md` "The dedicated auth packages ... were removed").

Operators who need a password, TLS, or origin restriction put a real
reverse proxy in front and own auth at the edge — `docs/operate/auth.mdx`
documents the Traefik / nginx / Cloudflare Tunnel patterns.

What stayed: `installer/uninstall.sh` still tears down
`hal0-caddy.service` and the first-run lockfile so pre-v0.3 installs
clean up correctly. The MCP mount's identity middleware
(`src/hal0/api/mcp_mount.py`) was kept but repointed — it no longer
enforces a Bearer token; it reads the caller's identity from an explicit
`X-hal0-Agent` header instead, which is what routes a write into the
right `private:<agent_id>` memory namespace.

## Consequences

- ~6,000 fewer lines of auth code, tests, and packaging to maintain; one
  fewer systemd unit (`hal0-caddy.service` gone).
- First-run UX is "open the dashboard," not "find the OTP in the install
  log, paste it into a wizard, set a password."
- There is no password-protected dashboard and no Bearer-token store:
  anyone who can reach `:8080` can drive every admin endpoint and every
  `/v1/*` call. Operators on an untrusted or multi-tenant network **must**
  add an upstream proxy — the installer offers no "secure by default"
  fallback, and the docs are loud about that (`docs/operate/auth.mdx`,
  `docs/concepts/security.mdx`).
- `X-hal0-Agent` is self-asserted and unauthenticated — hal0 has no
  credential to check it against post this ADR. It is a namespace-routing
  signal between cooperating local agents, not a security boundary
  against a hostile LAN caller (`docs/concepts/security.mdx`).
- The subsequent OpenRouter OAuth callback work had to explicitly reckon
  with this LAN-trust posture rather than assume Bearer auth was still
  available (ADR-0020).

## References

- `ARCHITECTURE.md` — "The dedicated auth packages ... were removed:
  hal0-api binds `0.0.0.0:8080` open, and LAN trust plus an upstream
  reverse proxy own authentication."
- `src/hal0/api/mcp_mount.py` — `X-hal0-Agent` identity header, replacing
  the retired Bearer-token MCP middleware
- `docs/operate/auth.mdx` — upstream reverse-proxy patterns
- `docs/concepts/security.mdx` — the LAN-trust boundary, spelled out for
  operators
- ADR-0020 — the OpenRouter OAuth callback design that had to work within
  this ADR's no-inbound-auth posture
