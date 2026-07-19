"""Board executor dispatch seam (KB-5) — interface, registry, no-op default.

Run targeted:
    PYTHONPATH=src .venv/bin/python -m pytest tests/board/test_board_dispatch.py -q
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from hal0.board import dispatch as seam
from hal0.board.dispatch import (
    AttemptHandle,
    BoardExecutor,
    NoopExecutor,
    clear_executors,
    dispatch,
    register_executor,
)
from hal0.board.store import BoardStore


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_executors()
    yield
    clear_executors()


def test_empty_registry_dispatches_nothing() -> None:
    result = dispatch("t_1", "hermes")
    assert result.dispatched is False
    assert result.handle is None
    assert "no executor" in (result.reason or "")


def test_noop_executor_conforms_to_protocol() -> None:
    assert isinstance(NoopExecutor(), BoardExecutor)


def test_registered_executor_dispatches_and_writes_back() -> None:
    register_executor("hermes", NoopExecutor())
    seen: list[AttemptHandle] = []
    result = dispatch("t_1", "hermes", writeback=seen.append)
    assert result.dispatched is True
    assert result.handle is not None
    assert result.handle.card_id == "t_1"
    assert result.handle.status == "skipped"
    assert seen and seen[0].card_id == "t_1"


def test_attempt_handle_with_status_is_immutable() -> None:
    h = AttemptHandle(card_id="t_1", attempt_id="a1", target="hermes")
    h2 = h.with_status("running", run_id="r9")
    assert h.status == "pending" and h.run_id is None  # original untouched
    assert h2.status == "running" and h2.run_id == "r9"


def test_explicit_attempt_id_overrides_executor() -> None:
    register_executor("hermes", NoopExecutor())
    result = dispatch("t_1", "hermes", attempt_id="pinned")
    assert result.handle is not None
    assert result.handle.attempt_id == "pinned"


# ── store nudge consults the seam ────────────────────────────────────────────


def _store(tmp_path: Path) -> BoardStore:
    s = BoardStore(tmp_path / "board.db")
    asyncio.run(s.ensure_initialized(None))
    return s


def test_nudge_zero_without_executor(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_task({"title": "a", "status": "ready"})
    assert store.dispatch_nudge() == {"dispatched": 0}


def test_nudge_dispatches_ready_cards_with_executor(tmp_path: Path) -> None:
    store = _store(tmp_path)
    a = store.create_task({"title": "a", "status": "ready"})["task"]["id"]
    store.create_task({"title": "b", "status": "todo"})  # not ready -> not dispatched
    register_executor(BoardStore.DISPATCH_TARGET, NoopExecutor())
    out = store.dispatch_nudge()
    assert out == {"dispatched": 1}
    # writeback recorded a run + a dispatched event on the ready card
    runs = store.get_task_log(a)
    assert runs and runs[-1]["state"] == "skipped"
    events = [e["kind"] for e in store.get_task(a)["events"]]
    assert "dispatched" in events


def test_nudge_respects_max(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for i in range(3):
        store.create_task({"title": f"c{i}", "status": "ready"})
    register_executor(BoardStore.DISPATCH_TARGET, NoopExecutor())
    assert store.dispatch_nudge(max_dispatch=2) == {"dispatched": 2}


def test_executor_may_not_reshape_canonical_state(tmp_path: Path) -> None:
    """The writeback appends runs/events only — the card's lane is unchanged by
    a dispatch (design: executor may report, not change canonical state)."""
    store = _store(tmp_path)
    a = store.create_task({"title": "a", "status": "ready"})["task"]["id"]
    register_executor(BoardStore.DISPATCH_TARGET, NoopExecutor())
    store.dispatch_nudge()
    assert store.get_task(a)["status"] == "ready"  # lane untouched


def test_seam_module_exports() -> None:
    assert hasattr(seam, "register_executor")
    assert hasattr(seam, "BoardExecutor")
