"""Operator Board routes — ``/api/board/*``, a live proxy to the Hermes kanban.

The Operator Board IS the Hermes kanban (ADR-0046): these routes forward to the
kanban plugin through the :class:`hal0.board.client.BoardClient` facade and add
only what hal0 owns — the audit trail and the error envelope. hal0 keeps no
board state of its own.

The FE↔BE wire contract (``ui/CONTRACTS.md`` "Operator Board") is unchanged:
paths, methods and status codes stay put.

* **Reads** (``GET``) are NOT audited. They are generated from
  :data:`_READ_ROWS`; query params the facade accepts are passed through.
* **Mutations** are explicit handlers each wrapped in
  :func:`hal0.api._audit.record_action` ``(category="board", action="board.<noun>.<verb>")``
  with ``rec.after`` set to the upstream result; the actor comes from
  ``X-hal0-Agent`` and the same header is forwarded upstream.
* ``?board=<slug>`` threads through every task/board-scoped call.
* Facade errors (``BoardNotInstalled`` / ``BoardNoToken`` / ``BoardUnreachable``
  / ``BoardUpstreamError``) are logged once in :func:`_call` and re-raised for
  the JSON-error envelope.
* ``WS /events`` delegates to :mod:`hal0.api.routes.board_ws`.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from fastapi import APIRouter, Request, WebSocket

from hal0.api._audit import record_action
from hal0.board.client import BoardClient, resolve_board_client
from hal0.board.legacy import run_once
from hal0.errors import BadRequest, Hal0Error

log = structlog.get_logger(__name__)

router = APIRouter()


# ── request helpers ─────────────────────────────────────────────────────────


async def _client(request: Request) -> BoardClient:
    """Run the one-shot legacy converter, then resolve the app's facade."""
    await run_once(request.app)
    return resolve_board_client(request.app)


async def _call(request: Request, make: Callable[[BoardClient], Awaitable[Any]]) -> Any:
    """Resolve the facade, run ``make(client)``, log + re-raise any ``Hal0Error``."""
    try:
        return await make(await _client(request))
    except Hal0Error as exc:
        log.warning("board.upstream", code=exc.code, status=exc.status, path=request.url.path)
        raise


def _board(request: Request) -> str | None:
    return request.query_params.get("board")


def _agent(request: Request) -> str | None:
    return request.headers.get("X-hal0-Agent") or None


def _truthy(value: str | None) -> bool | None:
    if value is None:
        return None
    return value.lower() in ("1", "true", "yes", "on")


def _int_query(request: Request, name: str, *, minimum: int = 0) -> int | None:
    raw = request.query_params.get(name)
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise BadRequest(
            f"query param {name!r} must be an integer", code="board.invalid_query"
        ) from exc
    if value < minimum:
        raise BadRequest(f"query param {name!r} must be >= {minimum}", code="board.invalid_query")
    return value


async def _read_body(request: Request) -> Any | None:
    raw = await request.body()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise BadRequest("request body must be valid JSON", code="board.invalid_body") from exc


def _task_id(result: Any) -> str | None:
    task = result.get("task") if isinstance(result, dict) else None
    return task.get("id") if isinstance(task, dict) else None


# ── audited mutations ───────────────────────────────────────────────────────
#
# Each sets rec.after = the upstream result so the audit row proves the write
# landed. A facade error inside the block is recorded outcome=error and
# re-raised. The body is parsed first so a malformed one is a 400 with no
# upstream call.


@router.post("/tasks")
async def create_task(request: Request) -> Any:
    body = await _read_body(request)
    async with record_action(
        request, category="board", action="board.task.create", target=None
    ) as rec:
        result = await _call(
            request,
            lambda c: c.create_task(body or {}, board=_board(request), agent_id=_agent(request)),
        )
        rec.after = result
        rec.target = _task_id(result)
    return result


@router.patch("/tasks/{task_id}")
async def update_task(request: Request, task_id: str) -> Any:
    body = await _read_body(request)
    async with record_action(
        request, category="board", action="board.task.update", target=task_id
    ) as rec:
        result = await _call(
            request,
            lambda c: c.update_task(
                task_id, body or {}, board=_board(request), agent_id=_agent(request)
            ),
        )
        rec.after = result
    return result


@router.delete("/tasks/{task_id}")
async def delete_task(request: Request, task_id: str) -> Any:
    async with record_action(
        request, category="board", action="board.task.delete", target=task_id
    ) as rec:
        result = await _call(
            request,
            lambda c: c.delete_task(task_id, board=_board(request), agent_id=_agent(request)),
        )
        rec.after = result
    return result


@router.post("/tasks/{task_id}/comments")
async def comment_task(request: Request, task_id: str) -> Any:
    body = await _read_body(request)
    async with record_action(
        request, category="board", action="board.task.comment", target=task_id
    ) as rec:
        result = await _call(
            request,
            lambda c: c.comment_task(
                task_id, body or {}, board=_board(request), agent_id=_agent(request)
            ),
        )
        rec.after = result
    return result


@router.post("/tasks/bulk")
async def bulk_tasks(request: Request) -> Any:
    body = await _read_body(request)
    target = None
    if isinstance(body, dict) and isinstance(body.get("ids"), list):
        target = ",".join(str(i) for i in body["ids"])
    async with record_action(
        request, category="board", action="board.task.bulk", target=target
    ) as rec:
        result = await _call(
            request,
            lambda c: c.bulk_tasks(body or {}, board=_board(request), agent_id=_agent(request)),
        )
        rec.after = result
    return result


@router.post("/tasks/{task_id}/reassign")
async def reassign_task(request: Request, task_id: str) -> Any:
    body = await _read_body(request)
    async with record_action(
        request, category="board", action="board.task.reassign", target=task_id
    ) as rec:
        result = await _call(
            request,
            lambda c: c.reassign_task(
                task_id, body or {}, board=_board(request), agent_id=_agent(request)
            ),
        )
        rec.after = result
    return result


@router.post("/tasks/{task_id}/specify")
async def specify_task(request: Request, task_id: str) -> Any:
    body = await _read_body(request)
    async with record_action(
        request, category="board", action="board.task.specify", target=task_id
    ) as rec:
        result = await _call(
            request,
            lambda c: c.specify_task(
                task_id, body or {}, board=_board(request), agent_id=_agent(request)
            ),
        )
        rec.after = result
    return result


@router.post("/tasks/{task_id}/decompose")
async def decompose_task(request: Request, task_id: str) -> Any:
    body = await _read_body(request)
    async with record_action(
        request, category="board", action="board.task.decompose", target=task_id
    ) as rec:
        result = await _call(
            request,
            lambda c: c.decompose_task(
                task_id, body or {}, board=_board(request), agent_id=_agent(request)
            ),
        )
        rec.after = result
    return result


@router.post("/tasks/{task_id}/reclaim")
async def reclaim_task(request: Request, task_id: str) -> Any:
    body = await _read_body(request)
    async with record_action(
        request, category="board", action="board.task.reclaim", target=task_id
    ) as rec:
        result = await _call(
            request,
            lambda c: c.reclaim_task(
                task_id, body or {}, board=_board(request), agent_id=_agent(request)
            ),
        )
        rec.after = result
    return result


def _link_target(body: Any) -> str | None:
    if isinstance(body, dict):
        return f"{body.get('parent_id')}->{body.get('child_id')}"
    return None


@router.post("/links")
async def add_link(request: Request) -> Any:
    body = await _read_body(request)
    async with record_action(
        request, category="board", action="board.link.add", target=_link_target(body)
    ) as rec:
        result = await _call(
            request,
            lambda c: c.add_link(body or {}, board=_board(request), agent_id=_agent(request)),
        )
        rec.after = result
    return result


@router.delete("/links")
async def remove_link(request: Request) -> Any:
    # Hermes reads the ids from the query; a JSON body is a fallback only when
    # neither id is in the query.
    qp = request.query_params
    parent_id, child_id = qp.get("parent_id"), qp.get("child_id")
    if parent_id is None and child_id is None:
        body = await _read_body(request)
        if isinstance(body, dict):
            parent_id, child_id = body.get("parent_id"), body.get("child_id")
    if not parent_id or not child_id:
        raise BadRequest("parent_id and child_id are required", code="board.invalid_query")
    body = {"parent_id": parent_id, "child_id": child_id}
    async with record_action(
        request, category="board", action="board.link.remove", target=_link_target(body)
    ) as rec:
        result = await _call(
            request,
            lambda c: c.remove_link(body, board=_board(request), agent_id=_agent(request)),
        )
        rec.after = result
    return result


@router.post("/dispatch")
async def dispatch_nudge(request: Request) -> Any:
    max_dispatch = _int_query(request, "max")
    async with record_action(
        request, category="board", action="board.dispatch.nudge", target=None
    ) as rec:
        result = await _call(
            request,
            lambda c: c.dispatch_nudge(
                max_dispatch=max_dispatch, board=_board(request), agent_id=_agent(request)
            ),
        )
        rec.after = result
    return result


@router.post("/boards")
async def create_board(request: Request) -> Any:
    body = await _read_body(request)
    target = body.get("slug") if isinstance(body, dict) else None
    async with record_action(
        request, category="board", action="board.board.create", target=target
    ) as rec:
        result = await _call(
            request,
            lambda c: c.create_board(body or {}, board=_board(request), agent_id=_agent(request)),
        )
        rec.after = result
    return result


@router.patch("/boards/{slug}")
async def update_board(request: Request, slug: str) -> Any:
    body = await _read_body(request)
    async with record_action(
        request, category="board", action="board.board.update", target=slug
    ) as rec:
        result = await _call(
            request,
            lambda c: c.update_board(
                slug, body or {}, board=_board(request), agent_id=_agent(request)
            ),
        )
        rec.after = result
    return result


@router.delete("/boards/{slug}")
async def delete_board(request: Request, slug: str) -> Any:
    delete = bool(_truthy(request.query_params.get("delete")))
    async with record_action(
        request, category="board", action="board.board.delete", target=slug
    ) as rec:
        result = await _call(
            request,
            lambda c: c.delete_board(
                slug, delete=delete, board=_board(request), agent_id=_agent(request)
            ),
        )
        rec.after = result
    return result


@router.post("/boards/{slug}/switch")
async def switch_board(request: Request, slug: str) -> Any:
    async with record_action(
        request, category="board", action="board.board.switch", target=slug
    ) as rec:
        result = await _call(
            request,
            lambda c: c.switch_board(slug, board=_board(request), agent_id=_agent(request)),
        )
        rec.after = result
    return result


@router.patch("/profiles/{name}")
async def update_profile(request: Request, name: str) -> Any:
    body = await _read_body(request)
    async with record_action(
        request, category="board", action="board.profile.update", target=name
    ) as rec:
        result = await _call(
            request,
            lambda c: c.update_profile(
                name, body or {}, board=_board(request), agent_id=_agent(request)
            ),
        )
        rec.after = result
    return result


@router.put("/orchestration")
async def update_orchestration(request: Request) -> Any:
    body = await _read_body(request)
    async with record_action(
        request, category="board", action="board.orchestration.update", target=None
    ) as rec:
        result = await _call(
            request,
            lambda c: c.update_orchestration(
                body or {}, board=_board(request), agent_id=_agent(request)
            ),
        )
        rec.after = result
    return result


# ── reads (NOT audited) — table-driven ──────────────────────────────────────

#: ``(router_path, facade_method_name)``. Path params are passed positionally in
#: path order; query params the facade method accepts are passed as keywords.
_READ_ROWS: tuple[tuple[str, str], ...] = (
    ("/board", "get_board"),
    ("/tasks/{task_id}", "get_task"),
    ("/tasks/{task_id}/log", "get_task_log"),
    ("/boards", "list_boards"),
    ("/profiles", "list_profiles"),
    ("/assignees", "list_assignees"),
    ("/stats", "get_stats"),
    ("/diagnostics", "get_diagnostics"),
    ("/workers/active", "list_active_workers"),
    ("/runs/{run_id}", "get_run"),
    ("/config", "get_config"),
    ("/orchestration", "get_orchestration"),
)

_QUERY_KEYS = ("tenant", "include_archived", "workflow_template_id", "board", "tail")


def _query_kwargs(request: Request, accepted: frozenset[str]) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    for key in _QUERY_KEYS:
        if key not in accepted or key not in request.query_params:
            continue
        if key == "include_archived":
            kwargs[key] = _truthy(request.query_params[key])
        elif key == "tail":
            kwargs[key] = _int_query(request, key, minimum=1)
        else:
            kwargs[key] = request.query_params[key]
    return kwargs


def _make_read(method_name: str) -> Callable[[Request], Awaitable[Any]]:
    accepted = frozenset(inspect.signature(getattr(BoardClient, method_name)).parameters)

    async def handler(request: Request) -> Any:
        kwargs = _query_kwargs(request, accepted)
        args = list(request.path_params.values())
        agent_id = _agent(request)
        return await _call(
            request,
            lambda c: getattr(c, method_name)(*args, agent_id=agent_id, **kwargs),
        )

    handler.__name__ = method_name
    return handler


for _path, _method in _READ_ROWS:
    router.add_api_route(_path, _make_read(_method), methods=["GET"], name=_method)


# ── live events WS (NOT audited) ────────────────────────────────────────────


@router.websocket("/events")
async def board_events_ws(websocket: WebSocket) -> None:
    """Stream board events to the browser (see :mod:`hal0.api.routes.board_ws`)."""
    from hal0.api.routes.board_ws import proxy_board_events

    await websocket.accept()
    await proxy_board_events(websocket)


__all__ = ["router"]
