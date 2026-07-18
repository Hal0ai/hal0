"""Operator Board routes — ``/api/board/*`` backed by the hal0 SQLite store.

hal0 OWNS the operator board (rework R4 §Agents-and-brain). These routes read
and write :class:`hal0.board.store.BoardStore` (``db/migrations/005_board.sql``)
instead of proxying to the Hermes kanban plugin — Hermes is now an OPTIONAL
executor, and the board works with it absent.

The FE↔BE wire contract (``ui/CONTRACTS.md`` "Operator Board", SPEC §4) is
FROZEN: paths, methods, payload shapes, status codes, and WS events are
unchanged. Only the implementation moved from a proxy forward to a local store;
response fields may be added (additive) but never removed or reshaped.

Shape (unchanged from the proxy era):

* **Reads** (``GET``) are NOT audited — they call the store directly.
* **Mutations** are EXPLICIT handlers each wrapped in
  :func:`hal0.api._audit.record_action` ``(category="board", action="board.<noun>.<verb>")``
  with ``rec.after`` set to the store result, so the slots-page ActivityLog
  records every board write with the actor derived from ``X-hal0-Agent``.
* ``?board=<slug>`` threads through every task/board-scoped call (omit ⇒ the
  current board).
* ``WS /events`` streams the local ``card_event`` feed (see
  :mod:`hal0.api.routes.board_ws`) so every mutation — operator's, the agent
  chat's, a worker's — reflects live on the board through the one transport.
* ``POST /chat`` (SSE) is the hal0-native orchestrator — see
  :mod:`hal0.api.routes.board_chat` (a separate lane; unchanged here).

First-boot import: the first request per process runs
:meth:`BoardStore.ensure_initialized`, which imports the live Hermes board when
present + the store is empty, else seeds a clean empty board.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request, WebSocket

from hal0.api._audit import record_action
from hal0.board.store import BoardStore
from hal0.errors import BadRequest

router = APIRouter()


# ── store resolution + request helpers ──────────────────────────────────────


async def _store(request: Request) -> BoardStore:
    """Resolve (and lazily construct) the app-state board store.

    The store is created once per process and cached on ``app.state``; the
    first access runs the idempotent first-boot import against the optional
    Hermes kanban client (``app.state.hermes_kanban``), so a fresh box imports a
    live Hermes board when present and seeds a clean empty board otherwise.
    """
    store = getattr(request.app.state, "board_store", None)
    if store is None:
        store = BoardStore()
        request.app.state.board_store = store
    if not store.initialized:
        client = getattr(request.app.state, "hermes_kanban", None)
        await store.ensure_initialized(client)
    return store


def _board(request: Request) -> str | None:
    return request.query_params.get("board")


def _truthy(value: str | None) -> bool:
    return value is not None and value.lower() in ("1", "true", "yes", "on")


async def _read_body(request: Request) -> Any | None:
    raw = await request.body()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise BadRequest("request body must be valid JSON", code="board.invalid_body") from exc


# ── audited mutations (SPEC §4 audited rows) ────────────────────────────────
#
# Each sets rec.after = store result so the audit row proves the write landed.
# record_action derives the actor from X-hal0-Agent (mcp:<agent>) or falls back
# to "dashboard"; a store error inside the block is recorded outcome=error and
# re-raised (surfaced through the JSON-error envelope).


@router.post("/tasks")
async def create_task(request: Request) -> Any:
    body = await _read_body(request)
    store = await _store(request)
    async with record_action(
        request, category="board", action="board.task.create", target=None
    ) as rec:
        result = store.create_task(body or {}, board=_board(request))
        rec.after = result
        task = result.get("task") if isinstance(result, dict) else None
        if isinstance(task, dict):
            rec.target = task.get("id")
    return result


@router.patch("/tasks/{task_id}")
async def update_task(request: Request, task_id: str) -> Any:
    body = await _read_body(request)
    store = await _store(request)
    async with record_action(
        request, category="board", action="board.task.update", target=task_id
    ) as rec:
        result = store.update_task(task_id, body or {})
        rec.after = result
    return result


@router.delete("/tasks/{task_id}")
async def delete_task(request: Request, task_id: str) -> Any:
    store = await _store(request)
    async with record_action(
        request, category="board", action="board.task.delete", target=task_id
    ) as rec:
        result = store.delete_task(task_id)
        rec.after = result
    return result


@router.post("/tasks/{task_id}/comments")
async def comment_task(request: Request, task_id: str) -> Any:
    body = await _read_body(request)
    store = await _store(request)
    async with record_action(
        request, category="board", action="board.task.comment", target=task_id
    ) as rec:
        result = store.comment_task(task_id, body or {})
        rec.after = result
    return result


@router.post("/tasks/bulk")
async def bulk_tasks(request: Request) -> Any:
    body = await _read_body(request)
    store = await _store(request)
    async with record_action(
        request, category="board", action="board.task.bulk", target=None
    ) as rec:
        result = store.bulk_update(body or {})
        rec.after = result
        if isinstance(body, dict) and isinstance(body.get("ids"), list):
            rec.target = ",".join(str(i) for i in body["ids"])
    return result


@router.post("/tasks/{task_id}/reassign")
async def reassign_task(request: Request, task_id: str) -> Any:
    body = await _read_body(request)
    store = await _store(request)
    async with record_action(
        request, category="board", action="board.task.reassign", target=task_id
    ) as rec:
        result = store.reassign(task_id, body or {})
        rec.after = result
    return result


@router.post("/tasks/{task_id}/specify")
async def specify_task(request: Request, task_id: str) -> Any:
    body = await _read_body(request)
    store = await _store(request)
    async with record_action(
        request, category="board", action="board.task.specify", target=task_id
    ) as rec:
        result = store.specify(task_id, body or {})
        rec.after = result
    return result


@router.post("/tasks/{task_id}/decompose")
async def decompose_task(request: Request, task_id: str) -> Any:
    body = await _read_body(request)
    store = await _store(request)
    async with record_action(
        request, category="board", action="board.task.decompose", target=task_id
    ) as rec:
        result = store.decompose(task_id, body or {})
        rec.after = result
    return result


@router.post("/tasks/{task_id}/reclaim")
async def reclaim_task(request: Request, task_id: str) -> Any:
    body = await _read_body(request)
    store = await _store(request)
    async with record_action(
        request, category="board", action="board.task.reclaim", target=task_id
    ) as rec:
        result = store.reclaim(task_id, body or {})
        rec.after = result
    return result


@router.post("/links")
async def add_link(request: Request) -> Any:
    body = await _read_body(request)
    store = await _store(request)
    target = None
    if isinstance(body, dict):
        target = f"{body.get('parent_id')}->{body.get('child_id')}"
    async with record_action(
        request, category="board", action="board.link.add", target=target
    ) as rec:
        result = store.add_link(body or {})
        rec.after = result
    return result


@router.delete("/links")
async def remove_link(request: Request) -> Any:
    # DELETE /links takes parent_id/child_id as QUERY params (SPEC §4).
    qp = request.query_params
    parent_id = qp.get("parent_id")
    child_id = qp.get("child_id")
    target = f"{parent_id}->{child_id}"
    store = await _store(request)
    async with record_action(
        request, category="board", action="board.link.remove", target=target
    ) as rec:
        result = store.remove_link(parent_id or "", child_id or "")
        rec.after = result
    return result


@router.post("/dispatch")
async def dispatch_nudge(request: Request) -> Any:
    store = await _store(request)
    max_raw = request.query_params.get("max")
    max_dispatch = int(max_raw) if max_raw and max_raw.isdigit() else None
    async with record_action(
        request, category="board", action="board.dispatch.nudge", target=None
    ) as rec:
        result = store.dispatch_nudge(max_dispatch=max_dispatch)
        rec.after = result
    return result


@router.post("/boards")
async def create_board(request: Request) -> Any:
    body = await _read_body(request)
    store = await _store(request)
    target = body.get("slug") if isinstance(body, dict) else None
    async with record_action(
        request, category="board", action="board.board.create", target=target
    ) as rec:
        result = store.create_board(body or {})
        rec.after = result
    return result


@router.patch("/boards/{slug}")
async def update_board(request: Request, slug: str) -> Any:
    body = await _read_body(request)
    store = await _store(request)
    async with record_action(
        request, category="board", action="board.board.update", target=slug
    ) as rec:
        result = store.update_board(slug, body or {})
        rec.after = result
    return result


@router.delete("/boards/{slug}")
async def delete_board(request: Request, slug: str) -> Any:
    store = await _store(request)
    async with record_action(
        request, category="board", action="board.board.delete", target=slug
    ) as rec:
        result = store.delete_board(slug)
        rec.after = result
    return result


@router.post("/boards/{slug}/switch")
async def switch_board(request: Request, slug: str) -> Any:
    store = await _store(request)
    async with record_action(
        request, category="board", action="board.board.switch", target=slug
    ) as rec:
        result = store.switch_board(slug)
        rec.after = result
    return result


@router.patch("/profiles/{name}")
async def update_profile(request: Request, name: str) -> Any:
    body = await _read_body(request)
    store = await _store(request)
    async with record_action(
        request, category="board", action="board.profile.update", target=name
    ) as rec:
        result = store.update_profile(name, body or {})
        rec.after = result
    return result


@router.put("/orchestration")
async def update_orchestration(request: Request) -> Any:
    body = await _read_body(request)
    store = await _store(request)
    async with record_action(
        request, category="board", action="board.orchestration.update", target=None
    ) as rec:
        result = store.update_orchestration(body or {})
        rec.after = result
    return result


# ── reads (NOT audited) — store-backed ──────────────────────────────────────


@router.get("/board")
async def get_board(request: Request) -> Any:
    store = await _store(request)
    return store.get_board(
        board=_board(request),
        include_archived=_truthy(request.query_params.get("include_archived")),
    )


@router.get("/tasks/{task_id}")
async def get_task(request: Request, task_id: str) -> Any:
    store = await _store(request)
    return store.get_task(task_id)


@router.get("/tasks/{task_id}/log")
async def get_task_log(request: Request, task_id: str) -> Any:
    store = await _store(request)
    tail_raw = request.query_params.get("tail")
    tail = int(tail_raw) if tail_raw and tail_raw.isdigit() else None
    return store.get_task_log(task_id, tail=tail)


@router.get("/boards")
async def list_boards(request: Request) -> Any:
    store = await _store(request)
    return store.list_boards()


@router.get("/profiles")
async def list_profiles(request: Request) -> Any:
    store = await _store(request)
    return store.list_profiles()


@router.get("/assignees")
async def list_assignees(request: Request) -> Any:
    store = await _store(request)
    return store.list_assignees(board=_board(request))


@router.get("/stats")
async def board_stats(request: Request) -> Any:
    store = await _store(request)
    return store.stats(board=_board(request))


@router.get("/diagnostics")
async def board_diagnostics(request: Request) -> Any:
    store = await _store(request)
    return store.diagnostics()


@router.get("/workers/active")
async def workers_active(request: Request) -> Any:
    store = await _store(request)
    return store.workers_active()


@router.get("/runs/{run_id}")
async def get_run(request: Request, run_id: str) -> Any:
    store = await _store(request)
    return store.get_run(run_id)


@router.get("/config")
async def get_config(request: Request) -> Any:
    store = await _store(request)
    return store.get_config()


@router.get("/orchestration")
async def get_orchestration(request: Request) -> Any:
    store = await _store(request)
    return store.get_orchestration()


# ── live events WS (NOT audited) ────────────────────────────────────────────


@router.websocket("/events")
async def board_events_ws(websocket: WebSocket) -> None:
    """Stream the local ``card_event`` feed to the browser.

    The browser passes ``since`` (last cursor) / ``board``; the bridge polls the
    store and pushes ``{"events": [...], "cursor": N}`` frames — the same frozen
    frame shape the Hermes proxy relayed, now sourced from hal0's own store.
    """
    from hal0.api.routes.board_ws import proxy_board_events

    await websocket.accept()
    await proxy_board_events(websocket)


# ── chat orchestrator (SSE, audited per tool call) ──────────────────────────


@router.post("/chat")
async def board_chat(request: Request):
    """hal0-native board orchestrator. SSE stream. SPEC §2.D / §4.

    Delegates to :mod:`hal0.api.routes.board_chat` (a separate lane; unchanged).
    """
    from hal0.api.routes.board_chat import run_board_chat

    return await run_board_chat(request)


__all__ = ["router"]
