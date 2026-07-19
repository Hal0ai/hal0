"""HP-executor — concrete Hermes :class:`BoardExecutor` at the KB-5 seam.

Recorded-fake HTTP only (``httpx.MockTransport`` — no sockets). Covers:
dispatch happy path, heartbeat/inspect, blocked/handoff surfacing, cancel,
reconcile-after-disconnect (recover + lost), inert-by-default, and the
"never mutates canonical board state" invariant (spy store).

Run targeted:
    PYTHONPATH=src .venv/bin/python -m pytest tests/board/test_hermes_executor.py -q
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from hal0.board.dispatch import (
    AttemptHandle,
    BoardExecutor,
    clear_executors,
    dispatch,
    get_executor,
)
from hal0.board.hermes_executor import (
    WORKER_BASE_PATH,
    HermesBoardExecutor,
    _HermesGateway,
    register,
)
from hal0.board.store import BoardStore


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    clear_executors()
    # Pin a session token so the gateway never tries to harvest one.
    monkeypatch.setenv("HERMES_SESSION_TOKEN", "tok-test")
    yield
    clear_executors()


# ── recorded-fake transport plumbing ─────────────────────────────────────────


def _executor(handler) -> HermesBoardExecutor:
    """Build an executor whose gateway is backed by a recorded MockTransport."""
    client = httpx.Client(base_url="http://hermes.test", transport=httpx.MockTransport(handler))
    gw = _HermesGateway(base_url="http://hermes.test", http_client=client)
    return HermesBoardExecutor(gateway=gw)


def _json(status: int, body: dict) -> httpx.Response:
    return httpx.Response(status, json=body)


# ── protocol conformance ─────────────────────────────────────────────────────


def test_conforms_to_board_executor_protocol() -> None:
    ex = _executor(lambda req: _json(200, {}))
    assert isinstance(ex, BoardExecutor)
    assert ex.target == "hermes"


# ── dispatch happy path ──────────────────────────────────────────────────────


def test_dispatch_starts_run_and_fills_correlation() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        assert req.method == "POST"
        assert req.url.path == WORKER_BASE_PATH
        # auth conventions carried outbound
        assert req.headers["X-Hermes-Session-Token"] == "tok-test"
        assert req.headers["Authorization"] == "Bearer tok-test"
        assert req.headers["X-hal0-Agent"]
        return _json(
            201,
            {"run_id": "r-1", "session_id": "s-1", "board_id": "b-1", "state": "running"},
        )

    ex = _executor(handler)
    handle = ex.dispatch("t_1", context={"summary": "do the thing"})

    assert handle.card_id == "t_1"
    assert handle.attempt_id.startswith("hp-t_1-")
    assert handle.target == "hermes"
    assert handle.executor == "hermes"
    assert handle.status == "running"
    assert handle.run_id == "r-1"
    assert handle.session_id == "s-1"
    assert handle.board_id == "b-1"
    # request body carried hal0 correlation, not a mirrored board card
    assert len(seen) == 1


def test_dispatch_unreachable_is_honest_failed_handle() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=req)

    ex = _executor(handler)
    handle = ex.dispatch("t_1", context={})
    assert handle.status == "failed"
    assert handle.detail["reason"] == "unreachable"


def test_dispatch_upstream_error_is_failed_handle() -> None:
    ex = _executor(lambda req: _json(500, {"error": "boom"}))
    handle = ex.dispatch("t_1", context={})
    assert handle.status == "failed"
    assert handle.detail["reason"] == "upstream_500"


# ── inspect / heartbeat ──────────────────────────────────────────────────────


def _running_handle() -> AttemptHandle:
    return AttemptHandle(
        card_id="t_1",
        attempt_id="hp-t_1-aaaa",
        target="hermes",
        executor="hermes",
        run_id="r-1",
        status="running",
    )


def test_inspect_reports_heartbeat() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "GET"
        assert req.url.path == f"{WORKER_BASE_PATH}/r-1"
        return _json(200, {"state": "running", "heartbeat_at": 123.0, "progress": 0.4})

    ex = _executor(handler)
    out = ex.inspect(_running_handle())
    assert out.status == "running"
    assert out.detail["heartbeat_at"] == 123.0
    assert out.detail["progress"] == 0.4


def test_inspect_transient_failure_keeps_last_known() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=req)

    ex = _executor(handler)
    out = ex.inspect(_running_handle())
    assert out.status == "running"  # unchanged


def test_inspect_terminal_handle_is_noop() -> None:
    called = False

    def handler(req: httpx.Request) -> httpx.Response:  # pragma: no cover - must not fire
        nonlocal called
        called = True
        return _json(200, {})

    ex = _executor(handler)
    done = _running_handle().with_status("done")
    assert ex.inspect(done).status == "done"
    assert called is False


# ── blocked / handoff surfacing ──────────────────────────────────────────────


def test_blocked_handoff_surfaces_on_handle_not_board() -> None:
    handoff = {"kind": "needs_approval", "prompt": "OK to deploy?"}

    def handler(req: httpx.Request) -> httpx.Response:
        return _json(200, {"state": "blocked", "handoff": handoff})

    ex = _executor(handler)
    out = ex.inspect(_running_handle())
    assert out.status == "blocked"
    assert out.detail["handoff"] == handoff


# ── cancel ───────────────────────────────────────────────────────────────────


def test_cancel_confirmed() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "POST"
        assert req.url.path == f"{WORKER_BASE_PATH}/r-1/cancel"
        return _json(200, {"state": "cancelled"})

    ex = _executor(handler)
    out = ex.cancel(_running_handle())
    assert out.status == "cancelled"


def test_cancel_unreachable_does_not_claim_success() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=req)

    ex = _executor(handler)
    out = ex.cancel(_running_handle())
    assert out.status == "running"  # NOT falsely cancelled
    assert "cancel_error" in out.detail


# ── reconcile after disconnect ───────────────────────────────────────────────


def test_reconcile_recovers_running_run() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return _json(200, {"state": "running", "heartbeat_at": 9.0})

    ex = _executor(handler)
    out = ex.reconcile(_running_handle())
    assert out.status == "running"
    assert out.detail["reconciled"] is True


def test_reconcile_recovers_completed_run() -> None:
    ex = _executor(lambda req: _json(200, {"state": "completed"}))
    out = ex.reconcile(_running_handle())
    assert out.status == "done"
    assert out.detail["reconciled"] is True


def test_reconcile_declares_lost_on_run_unknown() -> None:
    ex = _executor(lambda req: _json(404, {"error": "no such run"}))
    out = ex.reconcile(_running_handle())
    assert out.status == "lost"
    assert out.detail["reason"] == "run_unknown"


def test_reconcile_declares_lost_when_unreachable() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("gateway down", request=req)

    ex = _executor(handler)
    out = ex.reconcile(_running_handle())
    assert out.status == "lost"
    assert out.detail["reason"] == "unreachable"


def test_reconcile_lost_when_no_run_id() -> None:
    ex = _executor(lambda req: _json(200, {}))
    handle = AttemptHandle(card_id="t_1", attempt_id="a", target="hermes")
    out = ex.reconcile(handle)
    assert out.status == "lost"
    assert out.detail["reason"] == "no_run_id"


# ── inert by default ─────────────────────────────────────────────────────────


def test_register_inert_without_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HERMES_DASHBOARD_BASE_URL", raising=False)
    assert register(None) is False
    assert get_executor("hermes") is None
    # the seam then honestly reports not-dispatched
    result = dispatch("t_1", "hermes")
    assert result.dispatched is False
    assert "no executor" in (result.reason or "")


def test_register_active_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_DASHBOARD_BASE_URL", "http://127.0.0.1:9119")
    assert register(None) is True
    ex = get_executor("hermes")
    assert isinstance(ex, HermesBoardExecutor)


# ── invariant: never mutates canonical board state ───────────────────────────


class _SpyStore(BoardStore):
    """A BoardStore that records any call to a canonical-state mutator.

    lane / deps / ownership / approval mutators are wrapped to append to
    ``forbidden`` before delegating. The KB-5 writeback (append-only run/event)
    is NOT wrapped — that is the executor's only sanctioned reporting path.
    """

    def __init__(self, db_path) -> None:
        super().__init__(db_path)
        self.forbidden: list[str] = []

    def update_task(self, *a, **k):
        self.forbidden.append("update_task")
        return super().update_task(*a, **k)

    def delete_task(self, *a, **k):
        self.forbidden.append("delete_task")
        return super().delete_task(*a, **k)

    def add_link(self, *a, **k):
        self.forbidden.append("add_link")
        return super().add_link(*a, **k)

    def remove_link(self, *a, **k):
        self.forbidden.append("remove_link")
        return super().remove_link(*a, **k)

    def reassign(self, *a, **k):
        self.forbidden.append("reassign")
        return super().reassign(*a, **k)

    def reclaim(self, *a, **k):
        self.forbidden.append("reclaim")
        return super().reclaim(*a, **k)


def test_executor_never_mutates_canonical_state_via_nudge(tmp_path: Path) -> None:
    from hal0.board.dispatch import register_executor

    store = _SpyStore(tmp_path / "board.db")
    asyncio.run(store.ensure_initialized(None))
    card = store.create_task({"title": "a", "status": "ready", "assignee": "alice"})["task"]
    store.forbidden.clear()  # ignore setup writes

    ex = _executor(
        lambda req: _json(201, {"run_id": "r-9", "session_id": "s-9", "state": "running"})
    )
    register_executor(BoardStore.DISPATCH_TARGET, ex)

    out = store.dispatch_nudge()
    assert out == {"dispatched": 1}

    # No lane/deps/ownership/approval mutator was ever touched by the dispatch.
    assert store.forbidden == []
    # Card is byte-for-byte unchanged in its canonical fields.
    after = store.get_task(card["id"])
    assert after["status"] == "ready"
    assert after["assignee"] == "alice"
    assert after["deps"] == {"parents": [], "children": []}
    # The dispatch WAS recorded append-only (run + event) — the sanctioned path.
    assert any(r["state"] == "running" for r in store.get_task_log(card["id"]))
    assert "dispatched" in [e["kind"] for e in after["events"]]


def test_executor_holds_no_store_reference() -> None:
    """Structural invariant: the executor has no board-store handle at all."""
    ex = _executor(lambda req: _json(200, {}))
    for name in vars(ex):
        assert "store" not in name.lower()
