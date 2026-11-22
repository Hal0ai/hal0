# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Nous Research
"""Frozen copy of the Hermes OpenAI-compatible API-server HTTP surface.

Adapter lanes:
* **hal0-hermes-executor** — dispatches one immutable hal0 attempt, then
  inspects / cancels / reconciles its external run and streams heartbeats.
  Consumes the ``/v1/runs`` family and the ``/api/sessions`` family.
* **hal0-hermes-automation** — schedules agent work through the authenticated
  Jobs API (never ``jobs.json`` / internal state). Consumes the ``/api/jobs``
  family and the managed-cron fire webhook ``/api/cron/fire``.

Route table + security constants copied verbatim from the reviewed official pin
``caafe5389ae585ef77387a962aee05b1bcf17029`` (Hermes v0.23.3). The routes are
the rows of ``APIServerAdapter._http_route_table()``
(``gateway/platforms/api_server.py:1565-1607``), which since 0.22 also pulls
in the run routes from ``gateway/platforms/api_server_runs.py:285-293`` and
the room-member routes from ``gateway/platforms/api_server_room_grants.py:85-91``.
``connect()`` registers every row twice, natively and under ``/p/{profile}``
(``api_server.py:3941-3943``). It then adds one catch-all,
``* /p/{profile}/{tail}`` (``api_server.py:3946``, ``_handle_profile_ingress``,
``:1540-1546``; already there at v0.22.4, ``api_server.py:4478``): a served
profile's inbound-port platform (Twilio, LINE and the like) answering on this
listener, authenticated by that platform adapter's own verifier, 404 for a
profile with no such adapter. It is not a row of the table, so this bump
freezes it below as ``PROFILE_INGRESS_ROUTE``.

SECURITY (board fold, lxc105): ``DEFAULT_HOST`` is loopback, the server refuses
to start without ``API_SERVER_KEY`` and rejects placeholder / <16-char keys via
``hermes_cli.auth.has_usable_secret`` (``api_server.py:3874-3903``). These are
frozen so a Hermes bump that weakened them fails the contract suite before any
adapter ships.
"""

from __future__ import annotations

# --- Security defaults (gateway/platforms/api_server.py:170-171) ------------
# Loopback bind default. A bump to "0.0.0.0" must trip the contract suite.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8642

# connect() calls _api_key_passes_startup_guard(): an empty key refuses to
# start (even on a loopback bind), and a key is additionally checked with
# has_usable_secret(key, min_length=16) — placeholder or <16-char keys refuse.
# Since 0.22 a failed import of has_usable_secret also refuses (fail closed).
API_SERVER_KEY_MIN_LENGTH = 16
API_SERVER_KEY_REQUIRED_TO_START = True

# hermes_cli.auth._PLACEHOLDER_SECRET_VALUES (auth.py:280-282) — the
# placeholder denylist an API_SERVER_KEY is checked against. Frozen so it
# cannot silently shrink to {}.
PLACEHOLDER_SECRET_VALUES = frozenset(
    {
        "*",
        "**",
        "***",
        "changeme",
        "your_api_key",
        "your_api_key_here",
        "your-api-key",
        "placeholder",
        "example",
        "dummy",
        "null",
        "none",
    }
)

# --- Route table -------------------------------------------------------------
# (method, path) pairs, in registration order.

# Executor-bridge dispatch / inspect / cancel / reconcile surface
# (api_server_runs.py:285-293). ``/steer`` is new since 0.19.0; ``/clarify`` and
# ``/resolve-unknown`` are new since 0.22.2. For a room-member grant ``/clarify``
# needs the ``approve`` permission and ``/resolve-unknown`` the ``stop`` one
# (api_server_runs.py:409-410).
RUN_ROUTES = (
    ("post", "/v1/runs"),
    ("get", "/v1/runs/{run_id}"),
    ("get", "/v1/runs/{run_id}/events"),
    ("post", "/v1/runs/{run_id}/approval"),
    ("post", "/v1/runs/{run_id}/clarify"),
    ("post", "/v1/runs/{run_id}/steer"),
    ("post", "/v1/runs/{run_id}/resolve-unknown"),
    ("post", "/v1/runs/{run_id}/stop"),
)

# Executor-bridge session-control surface (create/list/inspect/fork/chat).
# ``/model`` (per-session model lock) is new since 0.19.0.
SESSION_ROUTES = (
    ("get", "/api/sessions"),
    ("post", "/api/sessions"),
    ("get", "/api/sessions/{session_id}"),
    ("patch", "/api/sessions/{session_id}"),
    ("delete", "/api/sessions/{session_id}"),
    ("get", "/api/sessions/{session_id}/messages"),
    ("post", "/api/sessions/{session_id}/fork"),
    ("post", "/api/sessions/{session_id}/chat"),
    ("post", "/api/sessions/{session_id}/chat/stream"),
    ("post", "/api/sessions/{session_id}/model"),
)

# Provider / chat-completions transport surface.
CHAT_ROUTES = (
    ("post", "/v1/chat/completions"),
    ("post", "/v1/responses"),
    ("get", "/v1/responses/{response_id}"),
    ("delete", "/v1/responses/{response_id}"),
)

# Automation-lane authenticated Jobs API + managed-cron fire webhook. The
# ``/api/cron/fire`` row is only registered when the cron module imports
# (api_server.py:1604-1606).
JOB_ROUTES = (
    ("get", "/api/jobs"),
    ("post", "/api/jobs"),
    ("get", "/api/jobs/{job_id}"),
    ("patch", "/api/jobs/{job_id}"),
    ("delete", "/api/jobs/{job_id}"),
    ("post", "/api/jobs/{job_id}/pause"),
    ("post", "/api/jobs/{job_id}/resume"),
    ("post", "/api/jobs/{job_id}/run"),
    ("post", "/api/cron/fire"),
)

# Generic platform event-callback ingress, added in 0.19.0. Authenticated by the
# target platform adapter's own verifier, NOT API_SERVER_KEY. Frozen so the
# security checklist sees any change to this unauthenticated-by-key surface.
PLATFORM_CALLBACK_ROUTES = (("post", "/api/platforms/{platform}/events"),)

# Routes hal0 does not consume but that widen the surface the loopback bind and
# API_SERVER_KEY protect. The routes new since 0.19.0 that v0.23.3 still serves
# are listed here or in the tables above (RUN_ROUTES ``/steer``, SESSION_ROUTES
# ``/model``). The room-member routes authenticate with a ``HermesRoom`` grant
# token (api_server_room_grants.py:100-102), not API_SERVER_KEY.
OTHER_ROUTES = (
    ("get", "/health"),
    ("get", "/health/detailed"),
    ("get", "/v1/health"),
    ("get", "/v1/models"),
    ("get", "/api/model/options"),
    ("get", "/v1/capabilities"),
    ("get", "/v1/skills"),
    ("get", "/v1/toolsets"),
    ("post", "/v1/room-members/invitations"),
    ("get", "/v1/room-members/capabilities"),
    ("post", "/v1/room-members/grants/refresh"),
    ("post", "/v1/room-members/grants/revoke"),
    ("post", "/v1/room-members/runs/close"),
)

# The (method, path) pairs new at 0.22.2 relative to 0.19.0 (3ef6bbd2): the
# "11 routes added" of the #2615 review, kept so the next bump
# can diff against a named list.
ADDED_SINCE_0_19_0 = frozenset(
    {
        ("get", "/api/model/options"),
        ("get", "/v1/artifacts/download/{artifact_id}"),
        ("get", "/v1/browser-control/ws"),
        ("get", "/v1/room-members/capabilities"),
        ("post", "/api/sessions/{session_id}/model"),
        ("post", "/v1/artifacts/upload"),
        ("post", "/v1/browser-control/register"),
        ("post", "/v1/room-members/grants/refresh"),
        ("post", "/v1/room-members/grants/revoke"),
        ("post", "/v1/room-members/invitations"),
        ("post", "/v1/runs/{run_id}/steer"),
    }
)

# The (method, path) pairs new at v0.22.4 relative to v0.22.2 (d923a5eb). No
# row was removed.
ADDED_SINCE_0_22_2 = frozenset(
    {
        ("post", "/v1/runs/{run_id}/clarify"),
        ("post", "/v1/runs/{run_id}/resolve-unknown"),
    }
)

# The (method, path) pairs new at v0.23.3 relative to v0.22.4 (4d043038).
ADDED_SINCE_0_22_4 = frozenset({("post", "/v1/room-members/runs/close")})

# The (method, path) pairs v0.22.4 served and v0.23.3 does not: browser control
# (v0.23.0 removed the built-in browser tools, upstream #143146) and the two
# artifact-transfer routes. hal0 never called any of the four. They are four of
# the eleven rows in ``ADDED_SINCE_0_19_0``.
REMOVED_SINCE_0_22_4 = frozenset(
    {
        ("get", "/v1/artifacts/download/{artifact_id}"),
        ("get", "/v1/browser-control/ws"),
        ("post", "/v1/artifacts/upload"),
        ("post", "/v1/browser-control/register"),
    }
)

# Registered after every row above, so a native route always wins
# (api_server.py:3944-3946). Any method.
PROFILE_INGRESS_ROUTE = ("*", "/p/{profile}/{tail:.*}")

# Since 0.19.0 every route above is also mounted under this prefix (same
# handlers; the profile-prefix middleware scopes config to that profile).
PROFILE_ROUTE_PREFIX = "/p/{profile}"
