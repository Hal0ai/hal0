# ADR 0012 — Remove auth and Caddy entirely

- **Status:** Accepted
- **Date proposed:** 2026-05-23
- **Date accepted:** 2026-05-23
- **Implementing PRs:** #254 (Caddy removed), #255 (frontend auth UI removed),
  #256 (backend auth modules + tests + dependency arrays removed), #266
  (installer first-run-lock + OTP + password prompt removed)
- **Supersedes:** ADR-0001 (collapse edge auth into FastAPI)
- **Closed on land:** the ADR-0001 close-out branches
  `feat/adr-0001-a-password-auth`, `feat/adr-0001-b-caddy-reduction`,
  `docs/adr-0001-c-housekeeping`, `docs/adr-0001-close-out-2026-05-21`
  (never pushed to origin; superseded as obsolete)

## Context

ADR-0001 (2026-05-21) moved auth *into* FastAPI and reduced Caddy to a
TLS-only terminator. The end state of that ADR was: a fresh install
locked behind a password, a one-time-password lockfile minted during
install, a session-cookie + bearer-token auth surface, OpenAI-compatible
`/v1/*` calls gated, and Caddy still in the installer as the default
front door.

That landed. It worked. It was also more surface than a homelab
appliance actually needs.

The hal0 deployment topology in practice:

- Every operator who has run it for real puts it behind an existing
  upstream proxy — Traefik on the homelab gateway, Cloudflare Tunnel for
  external access, nginx in a few one-off cases.
- The bundled Caddy was middleman: `:443 → 127.0.0.1:8080`. Doubled the
  failure surface (`hal0-caddy.service` could fail to start
  independently of `hal0-api`) and doubled the cert story (Caddy's
  internal CA vs whatever the upstream proxy used).
- The first-run wizard's password step was friction that didn't pay for
  itself — it gated nobody on a trusted LAN, and on a hostile network
  the *real* auth was the upstream proxy's basic-auth or OIDC anyway.
- ADR-0001's collapsed surface still cost ~5,500 lines of code +
  tests across `src/hal0/auth/`, `src/hal0/api/auth/`,
  `src/hal0/api/middleware/auth.py`, `src/hal0/api/routes/auth.py`, and
  the corresponding test suite — code that mostly existed to keep the
  no-op-for-most-users password flow correct.

## Decision

**Remove the entire FastAPI auth surface and the bundled Caddy
reverse proxy. Document upstream-proxy patterns as the recommended way
to add auth + TLS back on hostile networks.**

This is a harder direction than ADR-0001 took. The trade is:

- Operator on a trusted LAN: zero login friction, no password to
  rotate, no token to mint.
- Operator on a hostile network: hal0 doesn't pretend to have an answer.
  Put a real reverse proxy in front, set its auth, done. The
  `docs/operate/auth.mdx` page documents Traefik / nginx / Cloudflare
  Tunnel patterns for this.

### What was deleted

- `src/hal0/api/auth/` — 4 files, 712 lines (first-run lockfile,
  password hash + verify, OTP rate-limiter, package init)
- `src/hal0/auth/` — 3 files, 646 lines (token store, password helpers,
  package init exposing `auth_enabled()`)
- `src/hal0/api/middleware/auth.py` — 508 lines (`require_token`,
  `require_writer`, `require_admin` deps + `AuthIdentity` resolver +
  session cookie + Bearer + forwarded-email paths)
- `src/hal0/api/routes/auth.py` — 33 KB (`/api/auth/status`, `/login`,
  `/logout`, `/password`, `/me`, `/tokens`, `/tokens/{id}/rotate`)
- `ui/src/api/hooks/useAuth.ts` — 58 lines
- `ui/src/dash/settings.jsx::AuthSection` — token reveal + rotate +
  allowed-origins panel
- `tests/api/test_auth_*` — 5 files, ~2,200 lines
- `tests/auth/` — 3 files, ~330 lines
- `tests/api/test_install_routes.py`, `tests/api/test_no_public_paths.py`
  — regression tests for moot architecture
- `packaging/caddy/Caddyfile.template` + `packaging/systemd/hal0-caddy.service`
- ~135 lines of `install_caddy_tls()` + `--no-tls` flag handling from
  `installer/install.sh`
- ~110 lines of first-run-lockfile + OTP minting + password-claim
  banner from `installer/install.sh`

Total: ~6,000 lines removed across backend, frontend, tests, installer,
and packaging.

### What stays

- `installer/uninstall.sh` still tears down `hal0-caddy.service` and
  removes `/var/lib/hal0/.first-run.lock` on uninstall so existing
  v0.1.x / v0.2.x installs get cleaned up properly. Both files
  short-circuit if the artifact isn't present.
- `MCPAuthMiddleware` (in `src/hal0/api/mcp_mount.py`) is kept as a
  thin identity-stashing middleware — no longer enforces a bearer
  but parses the Authorization header (if any) so the caller's identity
  can land in the Cognee `private:<client_id>` namespace. The
  v0.3 Hermes bootstrap work tracks renaming this to
  `MCPIdentityMiddleware` and switching to an explicit `X-hal0-Agent`
  header.
- The bcrypt + PyJWT + argon2-cffi Python deps. Still small, still
  paid for by other features (cosign signing chains, deps of deps).
  Slice E (out of scope here) can audit + drop later.

### What is no longer possible

- There is no password-protected dashboard. Anyone who can reach
  `:8080` can drive every admin endpoint.
- There is no Bearer-token store. Anyone who can reach the API can
  call every `/v1/*` and `/api/*` endpoint without credentials.
- There is no first-run wizard claim flow. A fresh install is
  immediately usable by anyone on the network.

Operators who need any of those properties must put a reverse proxy
in front and own auth at the edge. `docs/operate/auth.mdx` documents
three concrete patterns:

1. **Traefik** with basic-auth middleware on the hal0 router
2. **nginx** with `auth_basic` on the location block
3. **Cloudflare Tunnel** with Cloudflare Access policies

## Why now, not next minor

This is a v0.3 cut. hal0 is pre-1.0 by explicit policy
(see `PLAN.md` §1) — minor bumps may carry breaking changes. The
parallel session that wrote `PLAN.md` v0.3 stream 4 ("Admin / auth
simplification") already framed this as one of the five v0.3 streams,
so users on the v0.3.x track will encounter the change naturally
through the upgrade notes.

## Consequences

### Positive

- ~6,000 fewer lines of code and tests to maintain.
- One fewer systemd unit (`hal0-caddy.service` gone).
- One fewer Python package graph (`hal0.auth` + `hal0.api.auth` gone).
- One fewer init-time decision tree (`HAL0_AUTH_ENABLED` /
  `HAL0_AUTH_DISABLED` env vars gone — they don't exist anymore).
- First-run UX is "click around the dashboard," not "find the OTP
  from the install log, paste it into the wizard, set a password."
- Test suite collection is faster — `pytest --collect-only` no longer
  walks 5 + 3 auth test modules.

### Negative

- Operators on a multi-tenant network MUST add an upstream proxy.
  The installer no longer offers any kind of "secure by default"
  fallback — and the docs need to be loud about that. We accept this.
- The v0.3 Hermes bootstrap plan + ADR-0011 + GitHub issues #237,
  #240, #243, #246 assumed bearer-token auth between Hermes and hal0.
  Those need follow-up updates to switch to an explicit
  `X-hal0-Agent` identity header for the Cognee `private:<id>`
  namespace source. Tracked in the v0.3 stream-4 follow-up.
- Any user with a hal0 v0.2.x install upgrading to v0.3 will lose
  whatever password + tokens they had. Documented in
  `docs/v0.3-upgrade.md` (Slice D follow-up — outside this PR's
  scope).

### Neutral

- The `cosign` keyless verification chain in `installer/install.sh`
  and the bcrypt + PyJWT + argon2 deps are unchanged. They serve
  different purposes (release verification, future use) and were not
  the load-bearing surface that ADR-0001 set up.

## Notes / appendix

- The originally-planned `Child A` password auth + `Child B` Caddy
  reduction branches (started in late April / early May, pre-v0.2)
  were superseded by this ADR. Their code was never merged. The
  diff against current `main` would have re-introduced the very
  modules this ADR deletes; rebasing them would have been wasted
  effort. They are documented as obsolete in `feedback-caddy-reduction-divergence`
  (auto-memory) and closed without merging.
- The `MCPAuthMiddleware → MCPIdentityMiddleware` rename + the
  Hermes bootstrap header-source swap are deferred to the v0.3
  Hermes-bootstrap work. They are mechanical edits that depend on
  this PR landing first to avoid a confused intermediate state where
  bearer-token auth and identity-header auth both exist.
