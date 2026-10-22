# SPDX-License-Identifier: MIT
# Copyright (c) 2025 Nous Research
"""Frozen copy of the Hermes kanban plugin's run-tracking surface.

Adapter lane: **hal0-hermes-kanban** (the board executor bridge, KB-5 —
:mod:`hal0.board.hermes_executor`). ``HermesBoardExecutor`` dispatches ONE
hal0 board attempt to a Hermes worker and then inspects / cancels / reconciles
that external run through this surface, entirely separate from the
``gateway/platforms/api_server.py`` ``/v1/runs`` family already frozen in
``api_surface.py`` (that family belongs to the executor's OpenAI-compatible
run/session surface, not the kanban plugin's per-task worker ledger).

Schema + route table + the dashboard session-token injection formats are
copied verbatim from the reviewed official pin
``4d04303818b61bf3aa09278af48356315515965b`` (Hermes v0.22.4):
``hermes_cli/kanban_db.py:776-982`` (schema),
``plugins/kanban/dashboard/plugin_api.py:936-985`` (routes, mounted at
``/api/plugins/kanban`` by the generic ``/api/plugins/{plugin['name']}``
plugin-router mount, ``hermes_cli/web_server_dashboard.py:1185-1189``) and
``hermes_cli/web_server_dashboard.py:139, :172`` (token injection).

KNOWN GAP (recorded, not silently accepted — see the R5 hermes-bump runbook
and §7 "Two validation re-runs, now unblocked" / Phase 6 HP-executor first
live contact, never exercised): the pinned upstream kanban plugin does NOT
expose a bare ``POST /runs`` creation route, and its terminal-state route is
named ``terminate`` — but ``HermesBoardExecutor.dispatch()``
(``src/hal0/board/hermes_executor.py:310-313``) POSTs directly to
``WORKER_BASE_PATH`` (i.e. ``POST /api/plugins/kanban/runs``) to create a run,
and ``.cancel()`` (``:352-354``) POSTs to
``{WORKER_BASE_PATH}/{run_id}/cancel``. Neither route exists in this frozen
surface; the closest analogues are ``POST /dispatch`` (batch auto-dispatch,
different request/response shape) and ``POST /runs/{run_id}/terminate``. This
fixture freezes what upstream ACTUALLY ships today (so a Hermes bump is
caught here first); it deliberately does NOT freeze hal0's assumed-but-
unverified ``POST /runs`` / ``.../cancel`` shape, since that would pin a
contract that was never observed against a live Hermes. Phase 6 must
either confirm a real creation/cancel route exists (and this fixture gets
extended) or the executor bridge needs a follow-up fix — that decision
belongs to the HP-executor lane, not drift-watch.
"""

from __future__ import annotations

# --- Kanban DB schema (hermes_cli/kanban_db.py CREATE TABLE statements) -----
# The "7-table roster" R5 §7 refers to. A table appearing/disappearing here
# means the kanban_db_init provisioning phase (hal0.agents.hermes_provision.
# _phase_kanban_db_init) and/or the board executor's assumptions about run
# state need review.
KANBAN_DB_TABLES = frozenset(
    {
        "tasks",
        "task_links",
        "task_comments",
        "task_events",
        "task_runs",
        "task_attachments",
        "kanban_notify_subs",
    }
)

# --- Route table (plugins/kanban/dashboard/plugin_api.py) -------------------
# (method, path) pairs for the run-ledger subset HermesBoardExecutor reads
# from today (`inspect`/`reconcile`), relative to the plugin's own router —
# the mount prefix is frozen separately as KANBAN_PLUGIN_MOUNT_PREFIX below.
KANBAN_RUNS_ROUTES = (
    ("get", "/runs/{run_id}"),
    ("get", "/runs/{run_id}/inspect"),
    ("post", "/runs/{run_id}/terminate"),
)

# hermes_cli/web_server_dashboard.py:1185-1189, the generic plugin-router mount:
#   app.include_router(router, prefix=f"/api/plugins/{plugin['name']}")
# The kanban plugin registers as "kanban", giving the base hal0's
# WORKER_BASE_PATH ("/api/plugins/kanban/runs") depends on.
KANBAN_PLUGIN_MOUNT_PREFIX = "/api/plugins/kanban"

# --- Dashboard session-token injection (hermes_cli/web_server_dashboard.py) --
# Only emitted on the UNGATED (auth_required=False) bootstrap branch; the
# gated branch instead ships window.__HERMES_AUTH_REQUIRED__=true and expects
# cookie auth via /api/auth/me. hal0's harvest regex
# (hal0.board.hermes_executor._TOKEN_RE / hal0.board._TOKEN_RE) assumes the
# ungated shape — it deliberately matches only the loopback, no-prior-auth
# deployment hal0 targets. The gate turns on for a non-loopback bind OR a
# non-loopback dashboard.public_url (see config_defaults.py in this package).
#
# Two forms exist at 0.22.2 and 0.22.4:
# * the SPA index (web_server_dashboard.py:172), an f-string with literal quotes;
# * the headless ``serve`` token page (web_server_dashboard.py:139), which
#   renders the token with ``json.dumps`` — a JSON string, so also
#   double-quoted for the url-safe tokens Hermes mints
#   (``secrets.token_urlsafe(32)``, web_server.py:235).
# Both are withheld when the auth gate is on, and also on the listener the
# gateway itself hosts (``withhold_session_token``, web_server_dashboard.py:134,
# :171); ``hermes dashboard``, which hal0 runs, injects it.
SESSION_TOKEN_INJECTION_TEMPLATE = 'window.__HERMES_SESSION_TOKEN__="{token}";'
SESSION_TOKEN_HEADLESS_TEMPLATE = (
    "<!doctype html><html><head><script>"
    "window.__HERMES_SESSION_TOKEN__={token_json};"
    "window.__HERMES_AUTH_REQUIRED__=false;"
    "</script></head><body>{msg}</body></html>"
)
