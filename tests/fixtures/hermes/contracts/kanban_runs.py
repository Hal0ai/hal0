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

Schema + route table + the dashboard session-token injection format are
copied verbatim from the reviewed official pin
``9de9c25f620ff7f1ce0fd5457d596052d5159596`` (Hermes v2026.7.7.2 / 0.18.2):
``hermes_cli/kanban_db.py`` (schema) and
``plugins/kanban/dashboard/plugin_api.py`` (routes, mounted at
``/api/plugins/kanban`` by ``hermes_cli/web_server.py``'s generic
``/api/plugins/{plugin['name']}`` plugin-router mount).

KNOWN GAP (recorded, not silently accepted — see the R5 hermes-bump runbook
and §7 "Two validation re-runs, now unblocked" / Phase 6 HP-executor first
live contact, never exercised): the pinned upstream kanban plugin does NOT
expose a bare ``POST /runs`` creation route, and its terminal-state route is
named ``terminate`` — but ``HermesBoardExecutor.dispatch()``
(``src/hal0/board/hermes_executor.py:308-312``) POSTs directly to
``WORKER_BASE_PATH`` (i.e. ``POST /api/plugins/kanban/runs``) to create a run,
and ``.cancel()`` (``:350-352``) POSTs to
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

# hermes_cli/web_server.py's generic plugin-router mount:
#   app.include_router(router, prefix=f"/api/plugins/{plugin['name']}")
# The kanban plugin registers as "kanban", giving the base hal0's
# WORKER_BASE_PATH ("/api/plugins/kanban/runs") depends on.
KANBAN_PLUGIN_MOUNT_PREFIX = "/api/plugins/kanban"

# --- Dashboard session-token injection (hermes_cli/web_server.py) -----------
# Only emitted on the UNGATED (auth_required=False) bootstrap branch; the
# gated branch instead ships window.__HERMES_AUTH_REQUIRED__=true and expects
# cookie auth via /api/auth/me. hal0's harvest regex
# (hal0.board.hermes_executor._TOKEN_RE / hal0.board._TOKEN_RE) assumes the
# ungated shape — it deliberately matches only the loopback, no-prior-auth
# deployment hal0 targets.
SESSION_TOKEN_INJECTION_TEMPLATE = 'window.__HERMES_SESSION_TOKEN__="{token}";'
