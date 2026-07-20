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
``9de9c25f620ff7f1ce0fd5457d596052d5159596``
(``gateway/platforms/api_server.py``, Hermes v2026.7.7.2 / 0.18.2).

SECURITY (board fold, lxc105): ``DEFAULT_HOST`` is loopback, the server refuses
to start without ``API_SERVER_KEY`` and rejects placeholder / <16-char keys via
``hermes_cli.auth.has_usable_secret``. These are frozen so a Hermes bump that
weakened them fails the contract suite before any adapter ships.
"""

from __future__ import annotations

# --- Security defaults (gateway/platforms/api_server.py) --------------------
# Loopback bind default. A bump to "0.0.0.0" must trip the contract suite.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8642

# connect() calls _api_key_passes_startup_guard(): an empty key refuses to
# start (even on a loopback bind), and a key is additionally checked with
# has_usable_secret(key, min_length=16) — placeholder or <16-char keys refuse.
API_SERVER_KEY_MIN_LENGTH = 16
API_SERVER_KEY_REQUIRED_TO_START = True

# hermes_cli.auth._PLACEHOLDER_SECRET_VALUES — the placeholder denylist an
# API_SERVER_KEY is checked against. Frozen so it cannot silently shrink to {}.
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

# --- Route table (gateway/platforms/api_server.py connect()) ----------------
# (method, path) pairs, in registration order.

# Executor-bridge dispatch / inspect / cancel / reconcile surface.
RUN_ROUTES = (
    ("post", "/v1/runs"),
    ("get", "/v1/runs/{run_id}"),
    ("get", "/v1/runs/{run_id}/events"),
    ("post", "/v1/runs/{run_id}/approval"),
    ("post", "/v1/runs/{run_id}/stop"),
)

# Executor-bridge session-control surface (create/list/inspect/fork/chat).
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
)

# Provider / chat-completions transport surface.
CHAT_ROUTES = (
    ("post", "/v1/chat/completions"),
    ("post", "/v1/responses"),
    ("get", "/v1/responses/{response_id}"),
    ("delete", "/v1/responses/{response_id}"),
)

# Automation-lane authenticated Jobs API + managed-cron fire webhook.
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
