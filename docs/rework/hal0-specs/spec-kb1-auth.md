# KB-1 / §1 — Authentication (security fast-track)

Author: Opus orchestrator, 2026-07-18. Grounded in the attack-surface map (investigator run) +
`agents/_auth.py`, `config/network.py`, `api/__init__.py` read directly @ `rework/descar`.

## Problem (real, today)
Production installs bind `0.0.0.0:8080` (installer-seeds `HAL0_BIND_HOST=0.0.0.0`). ~40 mutating /
RCE-class routers carry **no auth** — installer apply, slot create/spawn, `models/{id}/pull` (pulls
arbitrary container images), updater apply, `board/chat` (drives an agentic tool-loop on the brain
slot), secrets write. `X-hal0-Agent` is a spoofable identity claim. Any LAN host = RCE. The ONLY
existing auth is `agents/_auth.py` (HMAC-SHA256 cookie + origin allowlist) protecting **only**
chat_proxy, wired imperatively at 5 call-sites.

## Design — 3-tier, deny-by-default, single classification source

### Credential model (matches §22 Settings Security page: "require API key, client key, admin key")
- **`HAL0_ADMIN_KEY`** — full access incl. all mutating/RCE/config/secrets/updater/installer.
- **`HAL0_CLIENT_KEY`** — inference + read-only introspection only (`/v1/*` writer, `GET` list/stats).
  Optional; if unset, client-tier falls back to requiring admin.
- **Browser session cookie** (existing `hal0_session` HMAC) = **admin-equivalent** (local operator
  dashboard). New `POST /api/auth/login {key}` mints it (validates against admin key); existing
  chat_proxy cookie minting keeps working. `GET /api/auth/status` reports posture (for the UI gate).
- Presentation: `Authorization: Bearer <key>` (programmatic) OR `?api_key=<key>` (WS/SSE, since
  browsers can't set headers on WS upgrades) OR the cookie (browser).

### Enforcement — pure-ASGI middleware, installed at `__init__.py:1275` (after log_scrub, before routers)
Middleware not per-route `Depends`: 60+ routers, easy to miss one — **deny-by-default middleware is
the secure choice**. Resolves an `AuthPrincipal {tier: anon|client|admin}` from cookie→bearer→
api_key, then enforces by **path classification** from ONE source of truth (below). Handles
`scope["type"]` in {`http`, `websocket`} so SSE (HTTP GET) and WS upgrades are both covered; WS
rejects pre-accept with a 403 close.

### Classification source of truth = `security/exposure.py` (doubles as §21.11 exposure-CI)
An ordered `(method_glob, path_prefix) -> AuthClass` table. Three classes:
- **OPEN** (no auth ever): `GET /v1/models`, `GET /v1/models/{id}` (SDK probe), `/api/metrics/prometheus`
  (scrapers can't auth), `GET /api/health` (liveness), `GET /api/config/urls` (dashboard bootstrap —
  network shape, no secrets), static SPA (`/`, `/assets`, `/brand`, favicon), `POST /api/auth/login`,
  `GET /api/auth/status`. **This set is small + explicit + asserted-exact in CI.**
- **CLIENT**: `/v1/*` inference/writer + read-only introspection GETs.
- **ADMIN**: everything else, incl. all mutating routers. **Unclassified path → ADMIN** (deny-by-default
  ratchet: a newly-added route is locked until explicitly classified).

`derive_allowed_origins()` (network.py) stays the CSRF/origin layer for cookie-authed browser calls.

### Rollout posture (backward-compatible, test-safe)
`HAL0_REQUIRE_AUTH` env; default derived: **enforce when `bind_host()` non-loopback OR any key set**;
loopback + no keys = **dev-open** (existing TestClient suite runs loopback/no-keys → passes unchanged;
this is the reason to derive, not hard-require). New halo LXC is LAN-bound → enforced. lxc105 untouched.
Installer generates `HAL0_ADMIN_KEY` (+ optional client key) into `/etc/hal0/api.env` on first run.

### §21.11 exposure CI (ships WITH this, cheap ratchet)
`tests/security/test_exposure.py`: walk `create_app().routes`, assert every route resolves to a class
(fail on unclassified — forces new routes to be classified), and assert the OPEN set == the intended
allowlist exactly (fail if anyone widens OPEN). Feeds D2 (metrics-auth decision) + §21.11.

## Shippable steps (each green + pushed before next)
1. `security/exposure.py` classification table + `tests/security/test_exposure.py` (ratchet). No behavior
   change yet (middleware not wired) — lands the guardrail first.
2. `api/auth.py`: `AuthPrincipal`, key resolution, `require_auth_enabled()` (posture), reuse
   `agents/_auth` cookie verify. Unit tests.
3. Wire pure-ASGI enforcement middleware in `create_app()`; `POST /api/auth/login` + `GET /api/auth/status`.
   Flip exposure test to assert-enforced. Full targeted + import-smoke.
4. WS/SSE `?api_key=` coverage (board events, logs, activity, events, journal, approvals, benchmarks, mcp).
5. (Later, P3-ui) Settings Security page reads/writes keys via api.env `[server]` endpoint (E1).

## Files
- New: `src/hal0/security/exposure.py`, `src/hal0/api/auth.py`, `src/hal0/api/routes/auth.py`,
  `tests/security/test_exposure.py`, `tests/api/test_auth_*.py`.
- Touch: `src/hal0/api/__init__.py` (mid-stack install + login router), `config/network.py` (posture
  helper if needed), installer (seed keys — coordinate, may defer to installer lane).
- Reuse: `src/hal0/api/agents/_auth.py` (cookie verify/mint — do NOT duplicate the HMAC).

## Risks
1. Breaking the 700+ test suite — mitigated by dev-open default (loopback/no-keys). Verify: run a broad
   API test slice with + without `HAL0_REQUIRE_AUTH=1`.
2. WS handling in ASGI middleware (pre-accept 403 close) is subtle — cover board events WS explicitly.
3. First-run chicken-egg: installer must be reachable to set keys. Installer is ADMIN-class → on a fresh
   LAN box with no admin key yet, posture = "keys unset" ... but bind is non-loopback → enforce → locked
   out. **Resolution:** posture enforces only when admin key IS set; non-loopback + no-admin-key = WARN
   log + open installer/settings ONLY (first-run bootstrap window), everything else still ADMIN-denied.
   I.e. three states: unconfigured-bootstrap (open installer+config), configured (enforce), dev (open).
4. chat_proxy already self-gates (origin+cookie, stricter) — exempt or layer; keep its gate as
   defense-in-depth, don't double-reject browser flow.
