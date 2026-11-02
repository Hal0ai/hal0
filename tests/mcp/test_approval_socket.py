"""The root CLI's decision socket (ADR-0048).

These run a real unix socket, so the UID under test is the one the kernel
reports through ``SO_PEERCRED``, not a value the test supplied. The suite does
not run as root, so "the decider" is expressed through ``decider_uid``: this
process's own UID stands in for root, and any other UID stands in for the
``hal0`` account being refused.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import shutil
import socket
import stat
import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hal0.mcp import approval_socket
from hal0.mcp.approval_queue import ApprovalQueue


@pytest.fixture
def sock_path() -> Iterator[Path]:
    # Under /tmp on purpose: AF_UNIX paths stop at 108 bytes, which a pytest
    # tmp_path or a long TMPDIR can pass.
    directory = tempfile.mkdtemp(prefix="h0", dir="/tmp")
    yield Path(directory) / "a.sock"
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
async def app() -> AsyncIterator[Any]:
    yield SimpleNamespace(state=SimpleNamespace(approval_queue=ApprovalQueue()))


async def _enqueue(app: Any, calls: list[dict[str, Any]]) -> str:
    async def _exec(args: dict[str, Any]) -> dict[str, Any]:
        calls.append(args)
        return {"ok": True}

    approval_id: str = await app.state.approval_queue.enqueue(
        tool="slot_delete", args={"name": "scratch"}, client_id="hermes", executor=_exec
    )
    return approval_id


async def _send(action: str, approval_id: str, path: Path) -> dict[str, Any]:
    return await asyncio.to_thread(approval_socket.send_decision, action, approval_id, path=path)


def test_production_default_decider_is_root() -> None:
    default = inspect.signature(approval_socket.start_server).parameters["decider_uid"].default
    assert default == approval_socket.ROOT_UID == 0


async def test_decider_peer_approves_and_the_tool_runs(app: Any, sock_path: Path) -> None:
    calls: list[dict[str, Any]] = []
    aid = await _enqueue(app, calls)
    stop = await approval_socket.start_server(app, path=sock_path, decider_uid=os.getuid())
    try:
        reply = await _send("approve", aid, sock_path)
    finally:
        await stop()
    assert reply["status"] == 200
    assert reply["approval"]["state"] == "executed"
    assert calls == [{"name": "scratch"}]


async def test_decider_peer_denies_and_the_tool_does_not_run(app: Any, sock_path: Path) -> None:
    calls: list[dict[str, Any]] = []
    aid = await _enqueue(app, calls)
    stop = await approval_socket.start_server(app, path=sock_path, decider_uid=os.getuid())
    try:
        reply = await _send("deny", aid, sock_path)
    finally:
        await stop()
    assert reply["status"] == 200
    assert reply["approval"]["state"] == "denied"
    assert calls == []


@pytest.mark.parametrize("action", ["approve", "deny"])
async def test_non_root_peer_is_refused_and_nothing_is_decided(
    app: Any, sock_path: Path, action: str
) -> None:
    """The forged attempt: a well-formed request from a peer the kernel says is not root."""
    calls: list[dict[str, Any]] = []
    aid = await _enqueue(app, calls)
    stop = await approval_socket.start_server(app, path=sock_path, decider_uid=os.getuid() + 1)
    try:
        reply = await _send(action, aid, sock_path)
    finally:
        await stop()
    assert reply["status"] == 403
    assert reply["error"]["code"] == "auth.forbidden"
    assert calls == []
    assert [e["id"] for e in app.state.approval_queue.list_pending()] == [aid]


async def test_a_refused_peer_cannot_claim_root_in_its_request(app: Any, sock_path: Path) -> None:
    """Only the kernel's answer counts: the request carries no identity a peer could forge."""
    calls: list[dict[str, Any]] = []
    aid = await _enqueue(app, calls)
    stop = await approval_socket.start_server(app, path=sock_path, decider_uid=os.getuid() + 1)
    try:
        forged = f'{{"action":"approve","id":"{aid}","uid":0,"actor":"cli:root"}}\n'.encode()

        def _raw() -> bytes:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.connect(str(sock_path))
                sock.sendall(forged)
                return sock.recv(4096)

        raw = await asyncio.to_thread(_raw)
    finally:
        await stop()
    assert b"auth.forbidden" in raw
    assert calls == []


def test_unknown_peer_credentials_fail_closed() -> None:
    assert approval_socket.peer_uid(None) is None


async def test_peer_uid_reads_the_kernels_answer() -> None:
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        assert approval_socket.peer_uid(left) == os.getuid()
    finally:
        left.close()
        right.close()


async def test_platform_without_peer_credentials_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(socket, "SO_PEERCRED", raising=False)
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        assert approval_socket.peer_uid(left) is None
    finally:
        left.close()
        right.close()


async def test_unknown_id_and_second_decision_answer_like_the_routes(
    app: Any, sock_path: Path
) -> None:
    aid = await _enqueue(app, [])
    stop = await approval_socket.start_server(app, path=sock_path, decider_uid=os.getuid())
    try:
        missing = await _send("approve", "nope", sock_path)
        first = await _send("deny", aid, sock_path)
        second = await _send("approve", aid, sock_path)
    finally:
        await stop()
    assert (missing["status"], missing["error"]["code"]) == (404, "approvals.not_found")
    assert first["status"] == 200
    assert (second["status"], second["error"]["code"]) == (409, "approvals.already_resolved")


@pytest.mark.parametrize(
    "line",
    [
        b"not json\n",
        b"[]\n",
        b'{"action":"execute","id":"x"}\n',
        b'{"action":["approve"],"id":"x"}\n',
        b'{"action":"approve","id":7}\n',
        b'{"action":"approve","id":""}\n',
        b"x" * 4096 + b"\n",
    ],
)
async def test_malformed_requests_are_refused(app: Any, sock_path: Path, line: bytes) -> None:
    stop = await approval_socket.start_server(app, path=sock_path, decider_uid=os.getuid())

    def _raw() -> bytes:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.connect(str(sock_path))
            sock.sendall(line)
            return sock.recv(4096)

    try:
        raw = await asyncio.to_thread(_raw)
    finally:
        await stop()
    assert b"approvals.bad_request" in raw


async def test_decision_is_audited_as_the_root_cli(app: Any, sock_path: Path) -> None:
    from hal0.activity import AuditStore

    store = AuditStore(sock_path.parent / "audit.db")
    store.init_schema()
    app.state.audit = store
    aid = await _enqueue(app, [])
    stop = await approval_socket.start_server(app, path=sock_path, decider_uid=os.getuid())
    try:
        await _send("approve", aid, sock_path)
    finally:
        await stop()
    rows = store.query(action="approval.approve")
    assert [(r["actor"], r["target"], r["outcome"]) for r in rows] == [("cli:root", aid, "ok")]


async def test_socket_is_owner_only_and_removed_on_stop(app: Any, sock_path: Path) -> None:
    stop = await approval_socket.start_server(app, path=sock_path, decider_uid=os.getuid())
    mode = stat.S_IMODE(sock_path.stat().st_mode)
    await stop()
    assert mode == 0o600
    assert not sock_path.exists()


async def test_a_stale_socket_from_a_crash_is_replaced(app: Any, sock_path: Path) -> None:
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(sock_path))
    stale.close()
    aid = await _enqueue(app, [])
    stop = await approval_socket.start_server(app, path=sock_path, decider_uid=os.getuid())
    try:
        reply = await _send("deny", aid, sock_path)
    finally:
        await stop()
    assert reply["status"] == 200


async def test_an_unbindable_socket_never_fails_the_boot(app: Any, tmp_path: Path) -> None:
    occupied = tmp_path / "file"
    occupied.write_text("x")
    stop = await approval_socket.start_server(app, path=occupied / "a.sock")
    await stop()


async def test_a_chmod_failure_closes_the_listener_and_never_fails_the_boot(
    app: Any, sock_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_a: Any, **_k: Any) -> None:
        raise PermissionError("no chmod")

    monkeypatch.setattr(approval_socket.os, "chmod", _boom)
    stop = await approval_socket.start_server(app, path=sock_path, decider_uid=os.getuid())
    await stop()
    assert not sock_path.exists()
    with pytest.raises(approval_socket.ApprovalSocketUnavailable):
        approval_socket.send_decision("deny", "x", path=sock_path)


async def test_stop_waits_for_a_slow_handler_up_to_the_bound(
    app: Any, sock_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(approval_socket, "_STOP_TIMEOUT_S", 0.3)
    release = asyncio.Event()
    finished = asyncio.Event()

    async def _slow(_app: Any, *_a: Any) -> dict[str, Any]:
        await release.wait()
        finished.set()
        return {"status": 200}

    monkeypatch.setattr(approval_socket, "_decide", _slow)
    stop = await approval_socket.start_server(app, path=sock_path, decider_uid=os.getuid())
    request = asyncio.create_task(_send("deny", "x", sock_path))
    await asyncio.sleep(0.1)  # handler is now in flight

    loop = asyncio.get_running_loop()
    started = loop.time()
    stopper = asyncio.create_task(stop())
    await asyncio.sleep(0.1)
    assert not stopper.done()  # waiting on the handler, not returning at once
    release.set()
    await stopper
    assert finished.is_set()
    assert loop.time() - started < 0.3
    assert (await request)["status"] == 200


async def test_stop_gives_up_on_a_stuck_handler_after_the_bound(
    app: Any, sock_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(approval_socket, "_STOP_TIMEOUT_S", 0.2)
    release = asyncio.Event()

    async def _stuck(_app: Any, *_a: Any) -> dict[str, Any]:
        await release.wait()
        return {"status": 200}

    monkeypatch.setattr(approval_socket, "_decide", _stuck)
    stop = await approval_socket.start_server(app, path=sock_path, decider_uid=os.getuid())
    request = asyncio.create_task(_send("deny", "x", sock_path))
    await asyncio.sleep(0.1)
    await asyncio.wait_for(stop(), 2.0)  # returns despite the stuck handler
    release.set()
    await request


def test_client_reports_a_missing_listener(sock_path: Path) -> None:
    with pytest.raises(approval_socket.ApprovalSocketUnavailable, match="is hal0-api running"):
        approval_socket.send_decision("deny", "x", path=sock_path)


def test_hal0_api_listens_while_it_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real lifespan binds the production path, owner-only, and removes it."""
    from fastapi.testclient import TestClient

    from hal0.api import create_app
    from hal0.config import paths

    home = tempfile.mkdtemp(prefix="h0", dir="/tmp")
    monkeypatch.setenv("HAL0_HOME", home)
    try:
        target = paths.approval_socket()
        with TestClient(create_app()):
            assert stat.S_ISSOCK(target.stat().st_mode)
            assert stat.S_IMODE(target.stat().st_mode) == 0o600
            # This process is not root, so the production listener refuses it.
            if os.getuid() != approval_socket.ROOT_UID:
                reply = approval_socket.send_decision("deny", "x")
                assert reply["error"]["code"] == "auth.forbidden"
        assert not target.exists()
    finally:
        shutil.rmtree(home, ignore_errors=True)
