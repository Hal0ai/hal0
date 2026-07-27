"""Mount-level regression for issue #1302 — the ``/mcp/memory`` bulk-delete gate.

``mcp__hal0-admin__memory_delete`` has always gated ``len(ids) > 1``
through the approval queue. The standalone ``/mcp/memory`` mount ran the
same tool against the same provider with **no** gate, so an agent holding
only the narrow memory surface could bulk-delete without an operator ever
seeing a prompt — the gate was one mount away from being a no-op.

These tests pin the wiring, not just the dispatcher: ``mount_mcp_servers``
must hand the process-wide queue to the memory server, and must NOT hand
it to the dispatcher the admin server calls (double-gating there would
re-enqueue an already-approved call on every approval, a livelock).
"""

from __future__ import annotations

from typing import Any

import pytest

from hal0.api import mcp_mount
from hal0.mcp import memory
from hal0.mcp.approval_queue import ApprovalQueue


class _RecordingProvider:
    """Minimal memory provider — records deletes so we can prove none ran."""

    def __init__(self) -> None:
        self.delete_calls: list[dict[str, Any]] = []

    async def delete(
        self,
        *,
        ids: list[str],
        client_id: str | None = None,
        dataset: str | list[str] | None = None,
    ) -> dict[str, Any]:
        self.delete_calls.append({"ids": ids, "client_id": client_id, "dataset": dataset})
        return {"deleted": len(ids)}


@pytest.mark.asyncio
async def test_mounted_memory_server_gates_bulk_delete(monkeypatch: Any) -> None:
    """The server ``mount_mcp_servers`` builds must carry the queue."""
    provider = _RecordingProvider()
    queue = ApprovalQueue()
    built: dict[str, Any] = {}

    real_build = memory.build_server

    def _spy(**kwargs: Any) -> Any:
        built.update(kwargs)
        return real_build(**kwargs)

    monkeypatch.setattr(memory, "build_server", _spy)

    app = _mount(queue=queue, provider=provider, monkeypatch=monkeypatch)
    assert app is not None

    assert built.get("approval_queue") is queue, (
        "mount_mcp_servers must arm the memory server's approval gate — "
        "without the queue, /mcp/memory bypasses the admin bulk-delete gate"
    )

    # And the armed server actually gates end-to-end.
    dispatcher = memory.make_dispatcher(
        provider,
        client_id_resolver=lambda: "narrow-agent",
        private_resolver=lambda: False,
        approval_queue=queue,
    )
    out = await dispatcher("memory_delete", {"ids": ["a", "b", "c"]})
    assert out["status"] == "pending_approval"
    assert provider.delete_calls == []


def test_admin_memory_dispatcher_is_not_double_gated(monkeypatch: Any) -> None:
    """Admin gates first; its in-process memory callable must not re-gate.

    If it did, approving a bulk delete would run an executor that enqueues
    a fresh approval instead of deleting — the call could never complete.
    """
    provider = _RecordingProvider()
    queue = ApprovalQueue()
    admin_kwargs: dict[str, Any] = {}

    from hal0.mcp import admin as admin_mod

    real_build = admin_mod.build_server

    def _spy(**kwargs: Any) -> Any:
        admin_kwargs.update(kwargs)
        return real_build(**kwargs)

    monkeypatch.setattr(admin_mod, "build_server", _spy)

    sentinel = object()
    _mount(queue=queue, provider=provider, monkeypatch=monkeypatch, memory_dispatcher=sentinel)

    # The admin server receives the plain dispatcher it was given — the
    # gate lives in admin.dispatch, above this callable.
    assert admin_kwargs.get("memory_dispatcher") is sentinel
    assert admin_kwargs.get("approval_queue") is queue


def _mount(
    *,
    queue: ApprovalQueue,
    provider: Any,
    monkeypatch: Any,
    memory_dispatcher: Any = None,
) -> Any:
    """Run ``mount_mcp_servers`` against a bare app, skipping route-map install.

    The route-map autogen walks the live ``/api/*`` table, which needs a
    full ``create_app``; these tests only care about the MCP wiring, so we
    stub it out.
    """
    from fastapi import FastAPI

    from hal0.mcp import admin as admin_mod

    monkeypatch.setattr(admin_mod, "install_admin_route_map", lambda _app: None)

    app = FastAPI()
    mcp_mount.mount_mcp_servers(
        app,
        approval_queue=queue,
        memory_provider=provider,
        memory_dispatcher=memory_dispatcher,
    )
    return app
