"""Route -> :class:`AuthClass` classification table (KB-1 / §1, seam S9).

Single source of truth for "how much auth does this endpoint need". Three
consumers bind to :func:`classify` / :data:`RULES` (hal0-rework-plan.md
§23.2 S9): the runtime enforcement middleware (:mod:`hal0.api.auth`), the
``tests/security/test_exposure.py`` exposure-CI ratchet (§21.11), and —
later — the §22 Settings Security page.

Design (full rationale: ``hal0-specs/spec-kb1-auth.md``):

- **OPEN** — no auth, ever. Kept deliberately tiny and enumerated exactly:
  the ``/v1/models`` SDK probe, the Prometheus scrape endpoint, the
  liveness checks hit by the installer/systemd watchdog before any
  credential exists, the dashboard's network-shape bootstrap endpoint,
  the login/status endpoints themselves, and the static SPA shell (which
  carries no server data — it's the same HTML/JS bundle for every
  visitor).
- **BOOTSTRAP** — open *only* until an admin key is configured (the
  installer surface). Once ``HAL0_ADMIN_KEY`` is set, the enforcement
  middleware treats BOOTSTRAP the same as ADMIN. See ``spec-kb1-auth.md``
  risk #3 for why this three-state (unconfigured / configured / dev-open)
  design is required to avoid a first-run chicken-egg lockout.
- **CLIENT** — the inference surface (``/v1/*`` writer routes) plus a
  short, explicit list of genuinely read-only introspection ``GET``s
  (models/slots listing, stats, hardware, meta/enums, backends, npu,
  ports, profiles, hf search).
- **ADMIN** — everything else, including every mutating route and every
  route that can return secrets/config. **Unclassified paths fall back to
  ADMIN** — deny-by-default. A newly added router is locked out until a
  rule is added here (that's the whole point of the ratchet).

Ordering matters: rules are evaluated first-match-wins, so more specific
rules (exact paths, narrow method sets) must precede the broader prefix
rules that share their namespace (e.g. the ``/api/config/urls`` OPEN
exact-match must precede the ``/api/config`` ADMIN prefix rule).

A handful of routes aren't named anywhere in the KB-1 spec's explicit
OPEN/CLIENT bucket lists (``/api/status``, JSON ``/api/metrics``,
``/api/features``, ``/api/logs``, ``/api/events``, ``/api/activity``,
``/api/journal``, the dashboard-plugin manifest). Per "when unsure, choose
the more restrictive class", those default to ADMIN except where reasoned
analogy to an explicitly-CLIENT bucket is very close (``/api/status``
mirrors the CLIENT ``slots list`` / ``hardware`` bucket; JSON
``/api/metrics`` mirrors the OPEN ``/api/metrics/prometheus`` sibling).
See the KB-1 delivery report for the full "UNSURE — please review" list.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

Matcher = Callable[[str], bool]


class AuthClass(Enum):
    """The four classes a route can resolve to. See module docstring."""

    OPEN = "open"
    CLIENT = "client"
    ADMIN = "admin"
    BOOTSTRAP = "bootstrap"


def _exact(path: str) -> Matcher:
    """Match ``path`` only (no trailing-slash / sub-path variants)."""

    def _match(candidate: str) -> bool:
        return candidate == path

    # Introspectable pattern metadata -- lets GET /api/auth/exposure
    # (routes/auth.py) serialize RULES without duplicating the literal
    # path strings a second time. Read-only attributes on the closure;
    # never consulted by match()/applies() itself.
    _match.pattern = path  # type: ignore[attr-defined]
    _match.kind = "exact"  # type: ignore[attr-defined]
    return _match


def _prefix(prefix: str) -> Matcher:
    """Match ``prefix`` itself or any ``prefix/...`` sub-path.

    Boundary-safe: ``_prefix("/api/meta")`` does NOT match
    ``/api/metrics`` (a real near-miss in this codebase's route table —
    ``/api/meta/enums`` vs. ``/api/metrics``/``/api/metrics/prometheus``).
    """

    def _match(candidate: str) -> bool:
        return candidate == prefix or candidate.startswith(prefix + "/")

    _match.pattern = prefix  # type: ignore[attr-defined]
    _match.kind = "prefix"  # type: ignore[attr-defined]
    return _match


def _outside_api_v1_mcp(candidate: str) -> bool:
    """True for any path NOT under ``/api``, ``/v1``, or ``/mcp``.

    This is the static-SPA / docs-helper catch-all: it mirrors the exact
    same "not /api, not /v1" test ``_mount_dashboard``'s own SPA-fallback
    route already applies (``api/__init__.py::_spa``), so classification
    agrees with the app's existing public/private boundary instead of
    inventing a second one. Covers ``/``, ``/assets/*``, ``/brand/*``,
    ``/favicon.svg``, the Hermes dashboard-plugin static asset proxy
    (``/dashboard-plugins/...``), and any other bare SPA client-route.
    """
    return not (
        candidate.startswith("/api") or candidate.startswith("/v1") or candidate.startswith("/mcp")
    )


_outside_api_v1_mcp.pattern = None  # type: ignore[attr-defined]
_outside_api_v1_mcp.kind = "catchall"  # type: ignore[attr-defined]


@dataclass(frozen=True)
class _Rule:
    label: str
    match: Matcher
    auth_class: AuthClass
    methods: frozenset[str] | None = None  # None == any method

    def applies(self, method: str, path: str) -> bool:
        if self.methods is not None and method.upper() not in self.methods:
            return False
        return self.match(path)


_GET: frozenset[str] = frozenset({"GET", "HEAD"})
_POST: frozenset[str] = frozenset({"POST"})
_PUT: frozenset[str] = frozenset({"PUT"})

# Ordered, first-match-wins. See the module docstring for the full design
# rationale and hal0-rework-plan.md §23.5 for the architecture this
# implements. Keep additions grouped by router/prefix with a one-line
# reason a human can audit; don't reorder rules across namespaces.
RULES: tuple[_Rule, ...] = (
    # ── OPEN: the tiny, explicit allowlist (spec-kb1-auth.md) ──────────
    _Rule("v1 models probe (list)", _exact("/v1/models"), AuthClass.OPEN, _GET),
    _Rule("v1 models probe (by id)", _prefix("/v1/models"), AuthClass.OPEN, _GET),
    _Rule("prometheus scrape", _exact("/api/metrics/prometheus"), AuthClass.OPEN, _GET),
    _Rule("liveness (shallow)", _exact("/api/health"), AuthClass.OPEN, _GET),
    _Rule("liveness (deep)", _exact("/api/health/system"), AuthClass.OPEN, _GET),
    _Rule("dashboard bootstrap urls", _exact("/api/config/urls"), AuthClass.OPEN, _GET),
    _Rule("login", _exact("/api/auth/login"), AuthClass.OPEN, _POST),
    _Rule("auth status", _exact("/api/auth/status"), AuthClass.OPEN, _GET),
    # Clearing your own HttpOnly session cookie is harmless and must work at
    # any posture (the cookie is the only session end the browser has).
    _Rule("logout", _exact("/api/auth/logout"), AuthClass.OPEN, _POST),
    # Persist the enforcement toggle. ADMIN: only the operator may flip the
    # posture once auth is on (while it's off the middleware isn't enforcing,
    # so the first enable rides through — the intended "turn it on" path).
    _Rule("auth require toggle", _exact("/api/auth/require"), AuthClass.ADMIN, _PUT),
    # Rotate the admin/client box key — destructive, writes /etc/hal0/api.env.
    # ADMIN: only the operator may mint a new key. Same posture as the require
    # toggle (rides through while auth is OFF; admin-only once it's ON). Rate-
    # limited like login (routes/auth.py reuses app.state.login_limiter).
    _Rule("auth key rotate", _exact("/api/auth/rotate"), AuthClass.ADMIN, _POST),
    # Serializes RULES + OPEN_ALLOWLIST (the whole deny-by-default table) for
    # the Settings ▸ Security page's live exposure table (ExposureTable.jsx
    # stub-with-reason). ADMIN: the rule table is itself a map of the API's
    # full auth posture — same "surface map is sensitive-ish" reasoning this
    # module already applies to /api/docs and /api/openapi.json below.
    _Rule("auth exposure table (GET)", _exact("/api/auth/exposure"), AuthClass.ADMIN, _GET),
    # ── BOOTSTRAP: installer, open only until an admin key exists ──────
    _Rule("installer", _prefix("/api/install"), AuthClass.BOOTSTRAP, None),
    # ── explicit ADMIN for FastAPI's own docs/meta routes ──────────────
    # (Explicit, not fallback — these must not trip the "unclassified"
    # ratchet, and the KB-1 OPEN allowlist is meant to stay exactly the
    # 8 routes above; the API surface map is itself sensitive-ish info.)
    _Rule("openapi docs ui", _exact("/api/docs"), AuthClass.ADMIN, _GET),
    _Rule("openapi redoc ui", _exact("/api/redoc"), AuthClass.ADMIN, _GET),
    _Rule("openapi schema", _exact("/api/openapi.json"), AuthClass.ADMIN, _GET),
    _Rule("swagger oauth2 redirect", _exact("/docs/oauth2-redirect"), AuthClass.ADMIN, _GET),
    # ── CLIENT: /v1 inference + writer surface (rest of /v1/*) ─────────
    # Realtime WS (HP-realtime inc-1): a spoken inference surface, CLIENT like
    # chat. Pinned by name before the generic /v1 rule so the Settings Security
    # page + exposure CI resolve it to an explicit "realtime" rule (the generic
    # /v1 prefix below would already classify it CLIENT; this documents intent).
    _Rule("realtime ws (inference)", _prefix("/v1/realtime"), AuthClass.CLIENT, None),
    _Rule("v1 inference/writer", _prefix("/v1"), AuthClass.CLIENT, None),
    # ── CLIENT: explicit read-only introspection GETs ──────────────────
    _Rule("models list/introspection (GET)", _prefix("/api/models"), AuthClass.CLIENT, _GET),
    # Duplicate a registry row (UI-API-1 item 3) — a write that mints a new
    # model. Explicitly ADMIN; the generic "models mutations" rule below would
    # already catch it, but pinning it by name documents the intent and gives
    # the exposure test a stable target.
    _Rule(
        "model duplicate (POST)",
        _prefix("/api/models"),
        AuthClass.ADMIN,
        _POST,
    ),
    # Set/clear a model's per-type default marker (POST /api/models/{id}/default)
    # — a registry mutation. Explicitly ADMIN, matching every other model
    # mutation; the generic "models mutations" rule below would already catch it,
    # but pinning it by name documents the intent (same pattern as duplicate).
    _Rule(
        "model set-default (POST)",
        _prefix("/api/models"),
        AuthClass.ADMIN,
        _POST,
    ),
    _Rule("models mutations", _prefix("/api/models"), AuthClass.ADMIN, None),
    _Rule("slots list (GET, exact)", _exact("/api/slots"), AuthClass.CLIENT, _GET),
    _Rule("slots (everything else)", _prefix("/api/slots"), AuthClass.ADMIN, None),
    _Rule("hf search (GET)", _prefix("/api/hf"), AuthClass.CLIENT, _GET),
    _Rule("hf (other methods)", _prefix("/api/hf"), AuthClass.ADMIN, None),
    _Rule("hardware (GET)", _prefix("/api/hardware"), AuthClass.CLIENT, _GET),
    _Rule("hardware (mutations)", _prefix("/api/hardware"), AuthClass.ADMIN, None),
    _Rule("stats (GET)", _prefix("/api/stats"), AuthClass.CLIENT, _GET),
    _Rule("stats (other)", _prefix("/api/stats"), AuthClass.ADMIN, None),
    _Rule("meta enums (GET)", _prefix("/api/meta"), AuthClass.CLIENT, _GET),
    _Rule("meta (other)", _prefix("/api/meta"), AuthClass.ADMIN, None),
    _Rule("backends (GET)", _prefix("/api/backends"), AuthClass.CLIENT, _GET),
    _Rule("backends (mutations)", _prefix("/api/backends"), AuthClass.ADMIN, None),
    _Rule("npu (GET)", _prefix("/api/npu"), AuthClass.CLIENT, _GET),
    _Rule("npu (other)", _prefix("/api/npu"), AuthClass.ADMIN, None),
    _Rule("ports (GET)", _prefix("/api/ports"), AuthClass.CLIENT, _GET),
    _Rule("ports (other)", _prefix("/api/ports"), AuthClass.ADMIN, None),
    _Rule("profiles (GET)", _prefix("/api/profiles"), AuthClass.CLIENT, _GET),
    _Rule("profiles (mutations)", _prefix("/api/profiles"), AuthClass.ADMIN, None),
    # ── judgment calls: not named in either explicit spec bucket -------
    # (analogy-classified; see module docstring + KB-1 report UNSURE list)
    _Rule("status summary (GET)", _exact("/api/status"), AuthClass.CLIENT, _GET),
    # OBS-1 (§13/§21.3): read-only fleet metrics over the SQLite tables.
    _Rule("system stats (GET)", _exact("/api/system-stats"), AuthClass.CLIENT, _GET),
    _Rule("system info (GET)", _exact("/api/system-info"), AuthClass.CLIENT, _GET),
    _Rule("metrics json (GET)", _exact("/api/metrics"), AuthClass.CLIENT, _GET),
    _Rule("features (GET)", _exact("/api/features"), AuthClass.CLIENT, _GET),
    _Rule("features (mutations)", _prefix("/api/features"), AuthClass.ADMIN, None),
    # Doctor verdict feed (D6 diagnostics panel) — a GET, but classified
    # ADMIN rather than CLIENT: it aggregates + re-surfaces details from
    # ADMIN-only subsystems (capability slots, memory engine, services
    # health), same "when unsure, more restrictive" default this module's
    # docstring names. Mirrors `hal0 doctor verify --json` (operator-only).
    _Rule("doctor verdict feed (GET)", _exact("/api/doctor"), AuthClass.ADMIN, _GET),
    # ── ADMIN: everything mutating / config / secret ────────────────────
    _Rule("comfyui", _prefix("/api/comfyui"), AuthClass.ADMIN, None),
    _Rule("services", _prefix("/api/services"), AuthClass.ADMIN, None),
    _Rule("settings", _prefix("/api/settings"), AuthClass.ADMIN, None),
    _Rule("secrets", _prefix("/api/secrets"), AuthClass.ADMIN, None),
    _Rule("memory", _prefix("/api/memory"), AuthClass.ADMIN, None),
    _Rule("board", _prefix("/api/board"), AuthClass.ADMIN, None),
    # hal0-brain steward chat (R4 §G): primary /api/brain/chat surface, sibling
    # of the /api/board/chat alias. Admin-only — it drives the full platform
    # admin tool surface (slot mutations, model pulls, board writes).
    _Rule("brain (hal0-brain steward chat)", _prefix("/api/brain"), AuthClass.ADMIN, None),
    _Rule("providers", _prefix("/api/providers"), AuthClass.ADMIN, None),
    _Rule("upstreams", _prefix("/api/upstreams"), AuthClass.ADMIN, None),
    _Rule("updates", _prefix("/api/updates"), AuthClass.ADMIN, None),
    _Rule("capabilities", _prefix("/api/capabilities"), AuthClass.ADMIN, None),
    _Rule("stacks", _prefix("/api/stacks"), AuthClass.ADMIN, None),
    _Rule("benchmarks", _prefix("/api/benchmarks"), AuthClass.ADMIN, None),
    _Rule("chat-templates", _prefix("/api/chat-templates"), AuthClass.ADMIN, None),
    _Rule("agent approvals", _prefix("/api/agent/approvals"), AuthClass.ADMIN, None),
    _Rule(
        "agents (lifecycle/personas/restart/chat-proxy)",
        _prefix("/api/agents"),
        AuthClass.ADMIN,
        None,
    ),
    _Rule("mcp introspection", _prefix("/api/mcp"), AuthClass.ADMIN, None),
    _Rule("images cache", _prefix("/api/images"), AuthClass.ADMIN, None),
    _Rule("user layout", _prefix("/api/user"), AuthClass.ADMIN, None),
    _Rule("config (rest, may hold secrets)", _prefix("/api/config"), AuthClass.ADMIN, None),
    _Rule(
        "openrouter oauth (already loopback-gated)",
        _prefix("/api/openrouter"),
        AuthClass.ADMIN,
        None,
    ),
    _Rule("dashboard plugin manifest", _exact("/api/dashboard/plugins"), AuthClass.ADMIN, _GET),
    _Rule("logs", _prefix("/api/logs"), AuthClass.ADMIN, None),
    _Rule("events", _prefix("/api/events"), AuthClass.ADMIN, None),
    _Rule("activity", _prefix("/api/activity"), AuthClass.ADMIN, None),
    _Rule("journal", _prefix("/api/journal"), AuthClass.ADMIN, None),
    # ── mcp raw JSON-RPC mounts (admin + memory tool servers) ──────────
    _Rule("mcp tool-server mounts", _prefix("/mcp"), AuthClass.ADMIN, None),
    # ── static SPA / assets / favicon / plugin-asset proxy ─────────────
    _Rule("static SPA shell", _outside_api_v1_mcp, AuthClass.OPEN, _GET),
)


def match_rule(method: str, path: str) -> _Rule | None:
    """Return the first :class:`_Rule` matching ``(method, path)``, or ``None``.

    ``None`` means the path fell all the way through the table without an
    explicit classification — the deny-by-default ratchet. Callers that
    only need the resolved class should use :func:`classify`; the exposure
    CI test uses this to distinguish "explicitly ADMIN" from "ADMIN by
    silent fallback" (the latter should never happen for a route that
    already exists in the app).
    """
    normalized = path if path == "/" else path.rstrip("/") or "/"
    for rule in RULES:
        if rule.applies(method, normalized):
            return rule
    return None


def classify(method: str, path: str) -> AuthClass:
    """Classify ``(method, path)`` against :data:`RULES`.

    First-match-wins; an unmatched path denies-by-default to
    :data:`AuthClass.ADMIN` (deny-by-default ratchet — S9): a newly added
    route stays locked out until someone adds an explicit rule above.
    """
    rule = match_rule(method, path)
    return rule.auth_class if rule is not None else AuthClass.ADMIN


# The exact OPEN allowlist tests/security/test_exposure.py asserts against
# (as (method, path) pairs, using FastAPI's own path *templates* — e.g.
# ``/v1/models/{model_id}`` — the same identity a route has regardless of
# which concrete id gets substituted at request time). Kept here, next to
# RULES, so a widening PR has to touch both the rule and this allowlist in
# the same diff.
OPEN_ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/v1/models"),
        ("GET", "/v1/models/{model_id:path}"),
        ("GET", "/api/metrics/prometheus"),
        ("GET", "/api/health"),
        ("GET", "/api/health/system"),
        ("GET", "/api/config/urls"),
        ("POST", "/api/auth/login"),
        ("GET", "/api/auth/status"),
        ("POST", "/api/auth/logout"),
        # Hermes dashboard-plugin static asset proxy (kanban plugin JS/CSS
        # bundles) -- not under /api or /v1, so it hits the same
        # not-api/v1/mcp catch-all the SPA shell itself uses. No secrets:
        # SRI-verified static assets, traversal-guarded (manifest_proxy.py).
        ("GET", "/dashboard-plugins/{plugin_name}/{file_path:path}"),
    }
)


__all__ = [
    "OPEN_ALLOWLIST",
    "RULES",
    "AuthClass",
    "classify",
    "match_rule",
]
