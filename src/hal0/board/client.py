"""BoardClient — the one facade over the Hermes kanban plugin.

The board's HTTP routes, MCP tools and chat tools all call this class and
nothing else, so the Hermes path table lives in exactly one place. Every method
is a one-line forward through :meth:`HermesKanbanClient.request_json`; paths
are relative to ``/api/plugins/kanban`` (the client prepends the prefix).

Conventions shared by every method:

* ``board`` (keyword) selects a Hermes board and goes into ``params`` as
  ``?board=`` only when not ``None``.
* ``agent_id`` (keyword) is passed through to ``request_json`` for the outbound
  ``X-hal0-Agent`` audit header.
* Read-query knobs (``tenant``, ``include_archived``, ``workflow_template_id``,
  ``tail``, ``max_dispatch``, ``delete``) go into ``params`` only when not
  ``None`` (so ``include_archived`` defaults to None and Hermes's own
  ``include_archived_by_default`` applies). Booleans (``include_archived``,
  ``delete``) are sent as the strings ``"true"`` / ``"false"``; integers (``tail``, ``max_dispatch``) as
  ``str(int)``.

``resolve_board_client`` lazily builds and caches the facade on ``app.state``,
raising :class:`BoardNotInstalled` on a box without Hermes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from hal0.board import BoardNotInstalled, HermesKanbanClient


def _bool(value: bool) -> str:
    return "true" if value else "false"


def _params(board: str | None, **knobs: str | None) -> dict[str, str]:
    """Query params: ``board`` plus every knob that is not ``None``."""
    params = {k: v for k, v in knobs.items() if v is not None}
    if board is not None:
        params["board"] = board
    return params


class BoardClient:
    """Facade over the Hermes kanban plugin (see the module docstring)."""

    def __init__(self, kanban: HermesKanbanClient) -> None:
        self._kanban = kanban

    # ── reads ────────────────────────────────────────────────────────────

    async def get_board(
        self,
        *,
        tenant: str | None = None,
        include_archived: bool | None = None,
        workflow_template_id: str | None = None,
        board: str | None = None,
        agent_id: str | None = None,
    ) -> Any:
        return await self._kanban.request_json(
            "GET",
            "/board",
            params=_params(
                board,
                tenant=tenant,
                include_archived=None if include_archived is None else _bool(include_archived),
                workflow_template_id=workflow_template_id,
            ),
            agent_id=agent_id,
        )

    async def get_task(
        self, task_id: str, *, board: str | None = None, agent_id: str | None = None
    ) -> Any:
        return await self._kanban.request_json(
            "GET", f"/tasks/{task_id}", params=_params(board), agent_id=agent_id
        )

    async def get_task_log(
        self,
        task_id: str,
        *,
        tail: int | None = None,
        board: str | None = None,
        agent_id: str | None = None,
    ) -> Any:
        return await self._kanban.request_json(
            "GET",
            f"/tasks/{task_id}/log",
            params=_params(board, tail=None if tail is None else str(tail)),
            agent_id=agent_id,
        )

    async def list_boards(
        self,
        *,
        include_archived: bool | None = None,
        board: str | None = None,
        agent_id: str | None = None,
    ) -> Any:
        return await self._kanban.request_json(
            "GET",
            "/boards",
            params=_params(
                board,
                include_archived=None if include_archived is None else _bool(include_archived),
            ),
            agent_id=agent_id,
        )

    async def list_profiles(self, *, board: str | None = None, agent_id: str | None = None) -> Any:
        return await self._get("/profiles", board, agent_id)

    async def list_assignees(self, *, board: str | None = None, agent_id: str | None = None) -> Any:
        return await self._get("/assignees", board, agent_id)

    async def get_stats(self, *, board: str | None = None, agent_id: str | None = None) -> Any:
        return await self._get("/stats", board, agent_id)

    async def get_diagnostics(
        self, *, board: str | None = None, agent_id: str | None = None
    ) -> Any:
        return await self._get("/diagnostics", board, agent_id)

    async def list_active_workers(
        self, *, board: str | None = None, agent_id: str | None = None
    ) -> Any:
        return await self._get("/workers/active", board, agent_id)

    async def get_run(
        self, run_id: str, *, board: str | None = None, agent_id: str | None = None
    ) -> Any:
        return await self._get(f"/runs/{run_id}", board, agent_id)

    async def get_config(self, *, board: str | None = None, agent_id: str | None = None) -> Any:
        return await self._get("/config", board, agent_id)

    async def get_orchestration(
        self, *, board: str | None = None, agent_id: str | None = None
    ) -> Any:
        return await self._get("/orchestration", board, agent_id)

    # ── mutations ────────────────────────────────────────────────────────

    async def create_task(
        self, body: Any, *, board: str | None = None, agent_id: str | None = None
    ) -> Any:
        return await self._send("POST", "/tasks", body, board, agent_id)

    async def update_task(
        self, task_id: str, body: Any, *, board: str | None = None, agent_id: str | None = None
    ) -> Any:
        return await self._send("PATCH", f"/tasks/{task_id}", body, board, agent_id)

    async def delete_task(
        self, task_id: str, *, board: str | None = None, agent_id: str | None = None
    ) -> Any:
        return await self._send("DELETE", f"/tasks/{task_id}", None, board, agent_id)

    async def comment_task(
        self, task_id: str, body: Any, *, board: str | None = None, agent_id: str | None = None
    ) -> Any:
        return await self._send("POST", f"/tasks/{task_id}/comments", body, board, agent_id)

    async def bulk_tasks(
        self, body: Any, *, board: str | None = None, agent_id: str | None = None
    ) -> Any:
        return await self._send("POST", "/tasks/bulk", body, board, agent_id)

    async def reassign_task(
        self, task_id: str, body: Any, *, board: str | None = None, agent_id: str | None = None
    ) -> Any:
        return await self._send("POST", f"/tasks/{task_id}/reassign", body, board, agent_id)

    async def specify_task(
        self, task_id: str, body: Any, *, board: str | None = None, agent_id: str | None = None
    ) -> Any:
        return await self._send("POST", f"/tasks/{task_id}/specify", body, board, agent_id)

    async def decompose_task(
        self, task_id: str, body: Any, *, board: str | None = None, agent_id: str | None = None
    ) -> Any:
        return await self._send("POST", f"/tasks/{task_id}/decompose", body, board, agent_id)

    async def reclaim_task(
        self, task_id: str, body: Any, *, board: str | None = None, agent_id: str | None = None
    ) -> Any:
        return await self._send("POST", f"/tasks/{task_id}/reclaim", body, board, agent_id)

    async def add_link(
        self, body: Any, *, board: str | None = None, agent_id: str | None = None
    ) -> Any:
        return await self._send("POST", "/links", body, board, agent_id)

    async def remove_link(
        self, body: Any, *, board: str | None = None, agent_id: str | None = None
    ) -> Any:
        """``DELETE /links``; Hermes reads ``parent_id`` and ``child_id`` as query params.

        ``body`` must carry both keys (a missing key raises ``KeyError``); no JSON body is sent.
        """
        return await self._kanban.request_json(
            "DELETE",
            "/links",
            params=_params(board, parent_id=body["parent_id"], child_id=body["child_id"]),
            agent_id=agent_id,
        )

    async def dispatch_nudge(
        self,
        *,
        max_dispatch: int | None = None,
        board: str | None = None,
        agent_id: str | None = None,
    ) -> Any:
        return await self._kanban.request_json(
            "POST",
            "/dispatch",
            params=_params(board, max=None if max_dispatch is None else str(max_dispatch)),
            agent_id=agent_id,
        )

    async def create_board(
        self, body: Any, *, board: str | None = None, agent_id: str | None = None
    ) -> Any:
        return await self._send("POST", "/boards", body, board, agent_id)

    async def update_board(
        self, slug: str, body: Any, *, board: str | None = None, agent_id: str | None = None
    ) -> Any:
        return await self._send("PATCH", f"/boards/{slug}", body, board, agent_id)

    async def delete_board(
        self,
        slug: str,
        *,
        delete: bool,
        board: str | None = None,
        agent_id: str | None = None,
    ) -> Any:
        return await self._kanban.request_json(
            "DELETE",
            f"/boards/{slug}",
            params=_params(board, delete=_bool(delete)),
            agent_id=agent_id,
        )

    async def switch_board(
        self, slug: str, *, board: str | None = None, agent_id: str | None = None
    ) -> Any:
        return await self._send("POST", f"/boards/{slug}/switch", None, board, agent_id)

    async def update_profile(
        self, name: str, body: Any, *, board: str | None = None, agent_id: str | None = None
    ) -> Any:
        return await self._send("PATCH", f"/profiles/{name}", body, board, agent_id)

    async def update_orchestration(
        self, body: Any, *, board: str | None = None, agent_id: str | None = None
    ) -> Any:
        return await self._send("PUT", "/orchestration", body, board, agent_id)

    # ── forwards ─────────────────────────────────────────────────────────

    async def _get(self, path: str, board: str | None, agent_id: str | None) -> Any:
        return await self._kanban.request_json(
            "GET", path, params=_params(board), agent_id=agent_id
        )

    async def _send(
        self, method: str, path: str, body: Any, board: str | None, agent_id: str | None
    ) -> Any:
        return await self._kanban.request_json(
            method, path, params=_params(board), json_body=body, agent_id=agent_id
        )


def resolve_board_client(
    app: Any, *, installed_check: Callable[[], bool] | None = None
) -> BoardClient:
    """Return the app's cached :class:`BoardClient`, building it on first use.

    Checks :func:`hal0.board.auth.hermes_installed`; only a True result is
    cached (``app.state.board_installed``). While Hermes is absent every call
    re-probes and raises :class:`BoardNotInstalled`, so an install done while
    hal0-api runs is noticed. ``installed_check`` lets tests inject the probe.
    """
    state = app.state
    cached = getattr(state, "board_client", None)
    if cached is not None:
        return cached
    installed = getattr(state, "board_installed", None)
    if installed is None:
        if installed_check is None:
            from hal0.board.auth import hermes_installed as installed_check
        installed = bool(installed_check())
        if not installed:
            raise BoardNotInstalled("the Hermes agent is not installed on this box")
        state.board_installed = True
    kanban = getattr(state, "hermes_kanban", None)
    if kanban is None:
        kanban = state.hermes_kanban = HermesKanbanClient.from_env()
    client = state.board_client = BoardClient(kanban)
    return client


__all__ = ["BoardClient", "resolve_board_client"]
