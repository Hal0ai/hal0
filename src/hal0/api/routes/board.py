"""Operator Board proxy — ``/api/board/*`` → Hermes kanban plugin.

The FROZEN FE↔BE contract (SPEC §4). Every path forwards to
``{HERMES_DASHBOARD_BASE_URL}/api/plugins/kanban/<upstream>``. No kanban data
lives in hal0 — Hermes owns the DB; hal0-api is a thin AUDITED proxy.

Shape (mirrors :mod:`hal0.api.routes.memory_admin`):

* **Reads** (``GET``) are a table-driven allowlisted passthrough through
  :meth:`HermesKanbanClient.request_json` — method/path/query/body forward
  verbatim, response returned verbatim. NOT audited.
* **Mutations** are EXPLICIT handlers each wrapped in
  :func:`hal0.api._audit.record_action` ``(category="board", action="board.<noun>.<verb>")``
  setting ``rec.after`` to the upstream result, so the slots-page ActivityLog
  records every board write with the actor derived from ``X-hal0-Agent``.
* ``?board=<slug>`` threads through every task/board-scoped call verbatim
  (the proxy forwards the whole query string, so ``board`` rides along with
  no special-casing).
* ``WS /events`` proxies the upstream kanban events WS (reuses the
  chat_proxy ``_proxy_ws`` shape).
* ``POST /chat`` (SSE) is the hal0-native orchestrator — see
  :mod:`hal0.api.routes.board_chat`.

Auth: the browser's ``Authorization`` / ``Cookie`` / ``X-Hermes-Session-Token``
are NEVER forwarded — the client injects the server-resolved Hermes session
bearer itself (SPEC §2.G). ``X-hal0-Agent`` from the inbound request is
threaded to the client for audit attribution.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request, WebSocket

from hal0.api._audit import record_action
from hal0.board import BoardUnreachable, HermesKanbanClient
from hal0.errors import BadRequest

router = APIRouter()


# ── client + agent resolution ──────────────────────────────────────────────


def _client(request: Request) -> HermesKanbanClient:
    """Resolve the app-state kanban client, or 503 if unwired."""
    client = getattr(request.app.state, "hermes_kanban", None)
    if client is None:
        raise BoardUnreachable("operator board backend is not configured on this hal0 instance")
    return client


def _inbound_agent(request: Request) -> str | None:
    """Pass through the inbound ``X-hal0-Agent`` so audit + upstream agree."""
    return request.headers.get("X-hal0-Agent")


def _query(request: Request) -> dict[str, str] | None:
    """The inbound query string forwarded verbatim (carries ``?board=`` etc)."""
    return dict(request.query_params) or None


async def _read_body(request: Request) -> Any | None:
    raw = await request.body()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise BadRequest("request body must be valid JSON", code="board.invalid_body") from exc


async def _forward(
    request: Request,
    method: str,
    path: str,
    *,
    json_body: Any | None = None,
) -> Any:
    """Forward to the kanban client, threading query + agent verbatim."""
    client = _client(request)
    return await client.request_json(
        method,
        path,
        params=_query(request),
        json_body=json_body,
        agent_id=_inbound_agent(request),
    )


# ── explicit audited mutations (SPEC §4 audited rows) ───────────────────────
#
# Each sets rec.after = upstream result so the audit row proves the write
# landed. record_action derives the actor from X-hal0-Agent (mcp:<agent>) or
# falls back to "dashboard".


@router.post("/tasks")
async def create_task(request: Request) -> Any:
    body = await _read_body(request)
    async with record_action(
        request, category="board", action="board.task.create", target=None
    ) as rec:
        result = await _forward(request, "POST", "/tasks", json_body=body)
        rec.after = result
        # A created task may carry a no-dispatcher warning — keep it visible.
        task = result.get("task") if isinstance(result, dict) else None
        if isinstance(task, dict):
            rec.target = task.get("id")
    return result


@router.patch("/tasks/{task_id}")
async def update_task(request: Request, task_id: str) -> Any:
    body = await _read_body(request)
    async with record_action(
        request, category="board", action="board.task.update", target=task_id
    ) as rec:
        result = await _forward(request, "PATCH", f"/tasks/{task_id}", json_body=body)
        rec.after = result
    return result


@router.delete("/tasks/{task_id}")
async def delete_task(request: Request, task_id: str) -> Any:
    async with record_action(
        request, category="board", action="board.task.delete", target=task_id
    ) as rec:
        result = await _forward(request, "DELETE", f"/tasks/{task_id}")
        rec.after = result
    return result


@router.post("/tasks/{task_id}/comments")
async def comment_task(request: Request, task_id: str) -> Any:
    body = await _read_body(request)
    async with record_action(
        request, category="board", action="board.task.comment", target=task_id
    ) as rec:
        result = await _forward(request, "POST", f"/tasks/{task_id}/comments", json_body=body)
        rec.after = result
    return result


@router.post("/tasks/bulk")
async def bulk_tasks(request: Request) -> Any:
    body = await _read_body(request)
    async with record_action(
        request, category="board", action="board.task.bulk", target=None
    ) as rec:
        result = await _forward(request, "POST", "/tasks/bulk", json_body=body)
        rec.after = result
        if isinstance(body, dict) and isinstance(body.get("ids"), list):
            rec.target = ",".join(str(i) for i in body["ids"])
    return result


@router.post("/tasks/{task_id}/reassign")
async def reassign_task(request: Request, task_id: str) -> Any:
    body = await _read_body(request)
    async with record_action(
        request, category="board", action="board.task.reassign", target=task_id
    ) as rec:
        result = await _forward(request, "POST", f"/tasks/{task_id}/reassign", json_body=body)
        rec.after = result
    return result


@router.post("/tasks/{task_id}/specify")
async def specify_task(request: Request, task_id: str) -> Any:
    body = await _read_body(request)
    async with record_action(
        request, category="board", action="board.task.specify", target=task_id
    ) as rec:
        result = await _forward(request, "POST", f"/tasks/{task_id}/specify", json_body=body)
        rec.after = result
    return result


@router.post("/tasks/{task_id}/decompose")
async def decompose_task(request: Request, task_id: str) -> Any:
    body = await _read_body(request)
    async with record_action(
        request, category="board", action="board.task.decompose", target=task_id
    ) as rec:
        result = await _forward(request, "POST", f"/tasks/{task_id}/decompose", json_body=body)
        rec.after = result
    return result


@router.post("/tasks/{task_id}/reclaim")
async def reclaim_task(request: Request, task_id: str) -> Any:
    body = await _read_body(request)
    async with record_action(
        request, category="board", action="board.task.reclaim", target=task_id
    ) as rec:
        result = await _forward(request, "POST", f"/tasks/{task_id}/reclaim", json_body=body)
        rec.after = result
    return result


@router.post("/links")
async def add_link(request: Request) -> Any:
    body = await _read_body(request)
    target = None
    if isinstance(body, dict):
        target = f"{body.get('parent_id')}->{body.get('child_id')}"
    async with record_action(
        request, category="board", action="board.link.add", target=target
    ) as rec:
        result = await _forward(request, "POST", "/links", json_body=body)
        rec.after = result
    return result


@router.delete("/links")
async def remove_link(request: Request) -> Any:
    # DELETE /links takes parent_id/child_id as QUERY params (SPEC §4) —
    # they ride along in the forwarded query string.
    qp = request.query_params
    target = f"{qp.get('parent_id')}->{qp.get('child_id')}"
    async with record_action(
        request, category="board", action="board.link.remove", target=target
    ) as rec:
        result = await _forward(request, "DELETE", "/links")
        rec.after = result
    return result


@router.post("/dispatch")
async def dispatch_nudge(request: Request) -> Any:
    async with record_action(
        request, category="board", action="board.dispatch.nudge", target=None
    ) as rec:
        result = await _forward(request, "POST", "/dispatch")
        rec.after = result
    return result


@router.post("/boards")
async def create_board(request: Request) -> Any:
    body = await _read_body(request)
    target = body.get("slug") if isinstance(body, dict) else None
    async with record_action(
        request, category="board", action="board.board.create", target=target
    ) as rec:
        result = await _forward(request, "POST", "/boards", json_body=body)
        rec.after = result
    return result


@router.patch("/boards/{slug}")
async def update_board(request: Request, slug: str) -> Any:
    body = await _read_body(request)
    async with record_action(
        request, category="board", action="board.board.update", target=slug
    ) as rec:
        result = await _forward(request, "PATCH", f"/boards/{slug}", json_body=body)
        rec.after = result
    return result


@router.delete("/boards/{slug}")
async def delete_board(request: Request, slug: str) -> Any:
    async with record_action(
        request, category="board", action="board.board.delete", target=slug
    ) as rec:
        result = await _forward(request, "DELETE", f"/boards/{slug}")
        rec.after = result
    return result


@router.post("/boards/{slug}/switch")
async def switch_board(request: Request, slug: str) -> Any:
    body = await _read_body(request)
    async with record_action(
        request, category="board", action="board.board.switch", target=slug
    ) as rec:
        result = await _forward(request, "POST", f"/boards/{slug}/switch", json_body=body)
        rec.after = result
    return result


@router.patch("/profiles/{name}")
async def update_profile(request: Request, name: str) -> Any:
    body = await _read_body(request)
    async with record_action(
        request, category="board", action="board.profile.update", target=name
    ) as rec:
        result = await _forward(request, "PATCH", f"/profiles/{name}", json_body=body)
        rec.after = result
    return result


@router.put("/orchestration")
async def update_orchestration(request: Request) -> Any:
    body = await _read_body(request)
    async with record_action(
        request, category="board", action="board.orchestration.update", target=None
    ) as rec:
        result = await _forward(request, "PUT", "/orchestration", json_body=body)
        rec.after = result
    return result


# ── allowlisted read passthrough table (NOT audited) ────────────────────────
#
# (hal0 method, hal0 path under /api/board, upstream sub-path template).
# Path params are substituted from request.path_params; query + body forward
# verbatim. Mutations are NOT in this table — they are the explicit audited
# handlers above. The full set is the SPEC §4 read rows.

_READS: tuple[tuple[str, str, str], ...] = (
    ("GET", "/board", "/board"),
    ("GET", "/tasks/{task_id}", "/tasks/{task_id}"),
    ("GET", "/tasks/{task_id}/log", "/tasks/{task_id}/log"),
    ("GET", "/boards", "/boards"),
    ("GET", "/profiles", "/profiles"),
    ("GET", "/assignees", "/assignees"),
    ("GET", "/stats", "/stats"),
    ("GET", "/diagnostics", "/diagnostics"),
    ("GET", "/workers/active", "/workers/active"),
    ("GET", "/runs/{run_id}", "/runs/{run_id}"),
    ("GET", "/config", "/config"),
    ("GET", "/orchestration", "/orchestration"),
)


def _make_read_handler(template: str):
    async def handler(request: Request) -> Any:
        upstream = template.format(**request.path_params) if request.path_params else template
        return await _forward(request, "GET", upstream)

    return handler


for _method, _path, _template in _READS:
    router.add_api_route(
        _path,
        _make_read_handler(_template),
        methods=[_method],
        name=f"board_get_{_template.strip('/').replace('/', '_').replace('{', '').replace('}', '')}",
    )


# ── live events WS proxy (NOT audited) ──────────────────────────────────────


@router.websocket("/events")
async def board_events_ws(websocket: WebSocket) -> None:
    """Proxy the browser WS to the upstream kanban events WS.

    Reuses the chat_proxy bidi pump shape. The browser passes
    ``since`` / ``board`` / ``tenant``; the upstream ``?token=`` is supplied
    server-side from the Hermes session resolver (browsers can't set
    ``Authorization`` on a WS upgrade — SPEC §2.C). On upstream failure the
    browser WS is closed with 1011 so its retry logic kicks in.
    """
    from hal0.api.routes.board_ws import proxy_board_events

    await websocket.accept()
    await proxy_board_events(websocket)


# ── chat orchestrator (SSE, audited per tool call) ──────────────────────────


@router.post("/chat")
async def board_chat(request: Request):
    """hal0-native board orchestrator. SSE stream. SPEC §2.D / §4.

    Delegates to :mod:`hal0.api.routes.board_chat` so the transport stays
    swappable (future: route to the Hermes agent via chat_proxy without
    changing this contract).
    """
    from hal0.api.routes.board_chat import run_board_chat

    return await run_board_chat(request)


__all__ = ["router"]
