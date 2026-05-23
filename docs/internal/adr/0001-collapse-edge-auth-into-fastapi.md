# ADR 0001 — Collapse edge auth into FastAPI

- **Status:** Superseded by [ADR-0012](0012-remove-auth-and-caddy.md) (2026-05-23)
- **Date proposed:** 2026-05-17
- **Date accepted:** 2026-05-21
- **Date superseded:** 2026-05-23
- **Implementing PRs:** Child A (#58 — FastAPI password auth), Child B (#59 — Caddyfile reduction + --no-tls), Child C (#60 — docs pass)
- **Supersedes:** the dual-layer auth posture established by #28, #29, #36, #43, #49
- **Closed on land:** #28, #43, #51
- **Superseded by:** ADR-0012 removes the entire FastAPI auth surface
  and the bundled Caddy reverse proxy. The "FastAPI owns auth" outcome
  this ADR landed was a transitional state — operators in practice
  already front hal0 with an upstream proxy that handles auth + TLS,
  so the bundled surface was middleman. See ADR-0012 for the rationale
  and PRs #254, #255, #256, #266 for the implementation.

## Context

hal0 currently runs **two auth layers** in front of the FastAPI app:

1. **Caddy edge** — optional `basicauth` directive enabled by `hal0 install --auth=basic`, gated against a hard-coded `@public path` allowlist mirroring Python's `PUBLIC_PATHS` frozenset (`src/hal0/api/middleware/auth.py` ↔ `packaging/caddy/Caddyfile.template`).
2. **FastAPI app** — `require_token` / `require_writer` deps applied per-route (#29), backed by a JSON token store in `HAL0_HOME`.

The edge layer keeps producing the same class of bug:

| Bug | Root cause |
|-----|-----------|
| **#28** (critical) | `basicauth` evaluated before the public-paths allowlist, so the first-run wizard was 401'd before it could bootstrap. |
| **#36 / #49 coordination** | A "public" path (`/api/metrics/prometheus`) had to be removed from **both** the Python frozenset *and* the Caddyfile matcher, in a specific merge order. |
| **#51** (filed) | `PUBLIC_PATHS` is now a duplicated source of truth — every new public route requires two coordinated edits, and the drift fails open (path serves) or fails closed (path 401s) depending on which side is stale. |
| **#43** (HITL) | "How do users set their basic_auth credentials?" has no good answer because the credentials live in a *Caddyfile* that the installer renders once and then forgets. Rotation requires re-running the installer or hand-editing a templated file. |

The edge layer's only **legitimate** value is **TLS termination with auto-cert**, so a `hal0 install` on a home LAN gets HTTPS without the user understanding ACME. Auth is along for the ride — and the ride has cost more than it's earned.

## Decision

**Demote Caddy to a dumb TLS terminator + reverse proxy. Move all auth into FastAPI.**

Concretely:

### 1. Caddyfile reduction

`packaging/caddy/Caddyfile.template` collapses from ~90 lines (named matchers, `basicauth` block, `@public path` allowlist, per-path handle blocks) to ~10 lines:

```caddy
{$HAL0_PUBLIC_HOST} {
    tls {$HAL0_TLS_EMAIL}
    encode gzip zstd
    reverse_proxy 127.0.0.1:8080
}
```

No matchers. No allowlist. No auth. Caddy passes every request through to FastAPI, which decides what to do with it.

### 2. FastAPI gets password auth alongside the existing token auth

A new `hal0.api.auth.password` module adds:

- **Storage** — extend the existing auth store in `HAL0_HOME` (one file, not two) with a `password_hash: str | None` field. Bcrypt cost 12.
- **Login endpoint** — `POST /api/auth/login` accepts `{username, password}`, validates, sets a signed session cookie (HttpOnly, SameSite=Lax, Secure-when-TLS), returns user info.
- **Logout endpoint** — `POST /api/auth/logout` clears the cookie.
- **Set-password endpoint** — `POST /api/auth/password` (requires writer scope, OR allowed when no password is yet set) — accepts `{password}`, bcrypts, persists. Used by the wizard.
- **Session as token** — the session cookie validates identically to a Bearer token in the existing middleware — `require_token` / `require_writer` work unchanged. Browser UI uses the cookie; programmatic clients keep using Bearer.

### 3. PUBLIC_PATHS goes away

`PUBLIC_PATHS` frozenset is **deleted**. Every route's auth requirement is decided **in code**, by either:
- Declaring `dependencies=[Depends(require_token)]` (or `require_writer`) on the router/route, OR
- *Not* declaring it (the route is public).

Wizard endpoints (`/api/install/state`, `/api/auth/status`, `/api/auth/login`, etc.) are public by virtue of not declaring an auth dep — not by being on a magic allowlist.

### 4. First-run posture

On a fresh install with no password set, the server starts **unauthenticated**. The wizard renders a "set up password (optional)" step. The user can:

- Skip it → server stays open on the LAN (matches the `default to no auth` decision from the HITL on #43).
- Set a password → all writer routes become locked behind login, reads stay open (or also locked, depending on a single setting in the wizard).

This eliminates the installer-prompt path entirely (#43 is closed by deletion, not implementation).

### 5. Install-time flags simplify

- `--no-tls` (**new**) — skip Caddy entirely. FastAPI binds `0.0.0.0:8080`. Right path for hosts behind an existing reverse proxy (hal0-stage on `ai.thinmint.dev` behind your Traefik at 10.0.1.200).
- `--auth=basic` (**removed**) — there is no "basic auth at the edge" anymore. The installer no longer prompts for credentials.

Caddy installation remains the default for fresh installs on a public-host LAN.

### 6. Existing-install behavior

hal0 is pre-v1. Existing installs with Caddy `basicauth` lose edge auth on next install upgrade. Documented in upgrade notes; mitigation is "set a password in the wizard, or `--no-tls` and front with your own proxy." For users on hal0-stage behind your Traefik, basic_auth at Traefik is unaffected — only the hal0-side Caddy stops gating.

## Consequences

### Positive

- **One auth surface, one place to reason about it** — credentials, sessions, scopes, and public-route declarations all live in Python.
- **#28-class bugs structurally cannot recur** — there is no allowlist to mis-order.
- **#51 evaporates** — no duplication left to drift.
- **#43 closes by deletion** — credential capture is a wizard form, not an installer prompt.
- **Caddyfile becomes inspectable in 10 seconds** instead of 90 lines.
- **Programmatic clients unchanged** — Bearer token auth still works identically.

### Negative / Tradeoffs

- Existing installs lose edge auth on upgrade. Pre-v1, but documented.
- TLS-fronted but unauthenticated mode is technically possible (no password set, no `--no-tls`). Matches the "trusted LAN" default; flagged in the wizard.
- Session cookie introduces CSRF surface. Mitigated by `SameSite=Lax` + requiring `X-Requested-With` (or a CSRF token) on writer routes from cookie auth (Bearer auth bypasses, since Bearer can't be sent cross-origin by a browser).

### Risks

- **Cookie + Bearer dual-auth** must compose cleanly in the middleware. Tests need both paths.
- **First-run open mode** is intentional but easy to forget about. Wizard must surface this clearly ("Your hal0 server is currently open to anyone on the network").

## Out of scope (deferred)

- OIDC / OAuth providers.
- Multi-user accounts. v1 is a single owner password + multiple machine tokens.
- TOTP / passkeys / WebAuthn.
- Rate-limiting on login (worth doing eventually, not blocking this change).
- Migration script for existing installs to import their Caddy basic_auth credential into the new store. Manual re-entry via the wizard is acceptable for pre-v1.

## Implementation plan

Three coordinated PRs, in two waves:

**Wave 1 (additive):**
- PR A — FastAPI password auth (storage, login/logout/set-password endpoints, session cookie, dual cookie/Bearer in middleware). Lands without breaking any existing edge-auth install.

**Wave 2 (after A merges):**
- PR B — Caddyfile reduction, `--no-tls` install flag, drop `--auth=basic`, delete `PUBLIC_PATHS` frozenset. Wires the wizard's "set up password" step (UI lives in #46's wizard work already in tree).
- PR C — Docs (installer README, upgrade notes, PLAN.md), close #43 / #51, reframe #28's finding in `tests/harness/FINDINGS.md` as "fixed by architecture removal."

## Outcome

Landed across PRs #58 (Child A), #59 (Child B), and #60 (Child C):

- **Caddyfile collapsed to a TLS terminator + reverse proxy** (~42 lines: `tls`, `encode`, `reverse_proxy`, log block — no matchers, no `basicauth`, no allowlist). `packaging/caddy/Caddyfile.template`.
- **`--no-tls` install flag** added — skips the Caddy install/render entirely, FastAPI binds `0.0.0.0:8080`. Right path for hosts behind Traefik / nginx / Cloudflare Tunnel / an existing reverse proxy. `--dev` implies `--no-tls`. `--auth=basic` removed.
- **Password auth in FastAPI** — `POST /api/auth/password` (public when no password is set, writer-scoped otherwise), `POST /api/auth/login` issues a signed `hal0_session` cookie, `POST /api/auth/logout` clears it. The dashboard wizard's password-setup step drives the first-run flow.
- **`PUBLIC_PATHS` frozenset deleted** — every route's auth posture is decided in code via `dependencies=[Depends(require_token)]` (or `require_writer`), or by omission. The middleware composes Bearer + session cookie + (legacy) `X-Forwarded-Email` against the same deps. Regression test in `tests/api/test_no_public_paths.py` keeps the frozenset from coming back.
- **Findings #16 and #21 resolved by architecture removal** — credential capture lives in the wizard (not the installer), and the allowlist-vs-route drift class cannot recur because there is no allowlist. Finding #10 (the #28 critical bug) is similarly historical. See `tests/harness/FINDINGS.md` §10 / §16 / §21.
- **Issue housekeeping** — #28, #43, and #51 closed on land. Parent #54 closed with a summary.
