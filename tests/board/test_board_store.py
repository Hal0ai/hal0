"""BoardStore — hal0-owned board repository (KB-4).

Covers CRUD against the frozen contract shapes, per-card revision / ETag
concurrency (KB-6), the events feed the WS streams, and the first-boot import
(Hermes present / absent / partial).

Run targeted:
    PYTHONPATH=src .venv/bin/python -m pytest tests/board/test_board_store.py -q
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from hal0.board.store import DEFAULT_BOARD_SLUG, VISIBLE_STATUSES, BoardStore
from hal0.errors import Conflict, NotFound


@pytest.fixture
def store(tmp_path: Path) -> BoardStore:
    s = BoardStore(tmp_path / "board.db")
    asyncio.run(s.ensure_initialized(None))  # Hermes absent -> empty default board
    return s


# ── first-boot: Hermes absent ───────────────────────────────────────────────


def test_empty_board_seeds_default(store: BoardStore) -> None:
    boards = store.list_boards()
    assert [b["slug"] for b in boards] == [DEFAULT_BOARD_SLUG]
    assert boards[0]["current"] is True


def test_empty_board_returns_all_visible_lanes(store: BoardStore) -> None:
    lanes = store.get_board()["lanes"]
    assert list(lanes.keys()) == list(VISIBLE_STATUSES)
    assert all(v == [] for v in lanes.values())


def test_include_archived_adds_lane(store: BoardStore) -> None:
    lanes = store.get_board(include_archived=True)["lanes"]
    assert "archived" in lanes
    assert store.get_board(include_archived=False)["lanes"].get("archived") is None


def test_ensure_initialized_idempotent(store: BoardStore) -> None:
    asyncio.run(store.ensure_initialized(None))
    assert len(store.list_boards()) == 1


# ── card CRUD ────────────────────────────────────────────────────────────────


def test_create_task_returns_task_envelope(store: BoardStore) -> None:
    res = store.create_task({"title": "hi", "status": "todo", "assignee": "admin-agent"})
    task = res["task"]
    assert task["title"] == "hi"
    assert task["status"] == "todo"
    assert task["assignee"] == "admin-agent"
    assert task["profile"] == "admin-agent"  # normaliser alias
    assert task["revision"] == 1
    assert task["deps"] == {"parents": [], "children": []}


def test_create_defaults_status_triage(store: BoardStore) -> None:
    assert store.create_task({"title": "x"})["task"]["status"] == "triage"


def test_board_buckets_by_status(store: BoardStore) -> None:
    store.create_task({"title": "a", "status": "todo"})
    store.create_task({"title": "b", "status": "ready"})
    lanes = store.get_board()["lanes"]
    assert len(lanes["todo"]) == 1
    assert len(lanes["ready"]) == 1


def test_get_task_not_found(store: BoardStore) -> None:
    with pytest.raises(NotFound):
        store.get_task("t_missing")


def test_update_task_moves_lane(store: BoardStore) -> None:
    tid = store.create_task({"title": "a", "status": "todo"})["task"]["id"]
    updated = store.update_task(tid, {"status": "done"})
    assert updated["status"] == "done"
    assert updated["revision"] == 2


def test_update_invalid_status_rejected(store: BoardStore) -> None:
    tid = store.create_task({"title": "a"})["task"]["id"]
    with pytest.raises(Conflict):
        store.update_task(tid, {"status": "nonsense"})


def test_delete_task(store: BoardStore) -> None:
    tid = store.create_task({"title": "a"})["task"]["id"]
    store.delete_task(tid)
    with pytest.raises(NotFound):
        store.get_task(tid)


def test_comment_appends(store: BoardStore) -> None:
    tid = store.create_task({"title": "a"})["task"]["id"]
    store.comment_task(tid, {"author": "op", "body": "lgtm"})
    task = store.get_task(tid)
    assert task["comment_count"] == 1
    assert task["comments"][0]["body"] == "lgtm"


def test_bulk_update(store: BoardStore) -> None:
    a = store.create_task({"title": "a", "status": "todo"})["task"]["id"]
    b = store.create_task({"title": "b", "status": "todo"})["task"]["id"]
    res = store.bulk_update({"ids": [a, b, "t_ghost"], "status": "ready"})
    assert res["updated"] == 2
    assert store.get_task(a)["status"] == "ready"


# ── links / deps ─────────────────────────────────────────────────────────────


def test_links_and_dep_count(store: BoardStore) -> None:
    parent = store.create_task({"title": "p"})["task"]["id"]
    c1 = store.create_task({"title": "c1"})["task"]["id"]
    c2 = store.create_task({"title": "c2", "status": "done"})["task"]["id"]
    store.add_link({"parent_id": parent, "child_id": c1})
    store.add_link({"parent_id": parent, "child_id": c2})
    task = store.get_task(parent)
    assert sorted(task["deps"]["children"]) == sorted([c1, c2])
    assert task["dep_count"] == "1/2"  # c2 is done
    assert store.get_task(c1)["deps"]["parents"] == [parent]


def test_remove_link(store: BoardStore) -> None:
    parent = store.create_task({"title": "p"})["task"]["id"]
    child = store.create_task({"title": "c"})["task"]["id"]
    store.add_link({"parent_id": parent, "child_id": child})
    store.remove_link(parent, child)
    assert store.get_task(parent)["deps"]["children"] == []


def test_add_link_missing_card(store: BoardStore) -> None:
    parent = store.create_task({"title": "p"})["task"]["id"]
    with pytest.raises(NotFound):
        store.add_link({"parent_id": parent, "child_id": "t_ghost"})


# ── ETag / revision concurrency (KB-6) ───────────────────────────────────────


def test_update_matching_revision_ok(store: BoardStore) -> None:
    tid = store.create_task({"title": "a"})["task"]["id"]
    updated = store.update_task(tid, {"title": "b"}, if_revision=1)
    assert updated["title"] == "b"
    assert updated["revision"] == 2


def test_update_stale_revision_conflicts(store: BoardStore) -> None:
    tid = store.create_task({"title": "a"})["task"]["id"]
    store.update_task(tid, {"title": "b"})  # revision now 2
    with pytest.raises(Conflict) as exc:
        store.update_task(tid, {"title": "c"}, if_revision=1)
    assert exc.value.code == "board.stale_write"
    assert exc.value.status == 409


def test_delete_stale_revision_conflicts(store: BoardStore) -> None:
    tid = store.create_task({"title": "a"})["task"]["id"]
    store.update_task(tid, {"title": "b"})
    with pytest.raises(Conflict):
        store.delete_task(tid, if_revision=1)


# ── boards / orchestration ───────────────────────────────────────────────────


def test_create_and_switch_board(store: BoardStore) -> None:
    store.create_board({"slug": "proj-x", "name": "Project X"})
    slugs = {b["slug"] for b in store.list_boards()}
    assert {"default", "proj-x"} <= slugs
    store.switch_board("proj-x")
    current = [b for b in store.list_boards() if b["current"]]
    assert len(current) == 1 and current[0]["slug"] == "proj-x"


def test_delete_current_board_repoints_current(store: BoardStore) -> None:
    store.create_board({"slug": "proj-x"})
    store.switch_board("proj-x")
    store.delete_board("proj-x")
    current = [b for b in store.list_boards() if b["current"]]
    assert current and current[0]["slug"] == "default"


def test_duplicate_board_conflicts(store: BoardStore) -> None:
    with pytest.raises(Conflict):
        store.create_board({"slug": "default"})


def test_update_orchestration_knobs(store: BoardStore) -> None:
    out = store.update_orchestration(
        {"auto_decompose": True, "orchestrator_profile": "admin-agent"}
    )
    assert out["auto_decompose"] is True
    assert out["orchestrator_profile"] == "admin-agent"
    # config knobs stay read-only / untouched
    assert out["tick_interval"] == 5


def test_profiles_and_assignees_derived(store: BoardStore) -> None:
    store.create_task({"title": "a", "assignee": "admin-agent"})
    store.create_task({"title": "b", "assignee": "admin-agent"})
    store.create_task({"title": "c", "assignee": "mem-agent"})
    profiles = {p["id"]: p["count"] for p in store.list_profiles()}
    assert profiles["admin-agent"] == 2
    assert profiles["mem-agent"] == 1
    assignees = {a["id"] for a in store.list_assignees()}
    assert {"admin-agent", "mem-agent"} <= assignees


def test_stats_excludes_archived_from_total(store: BoardStore) -> None:
    store.create_task({"title": "a", "status": "todo"})
    store.create_task({"title": "b", "status": "archived"})
    stats = store.stats()
    assert stats["total"] == 1
    assert stats["by_status"]["archived"] == 1


def test_workers_active_from_running(store: BoardStore) -> None:
    store.create_task({"title": "a", "status": "running", "assignee": "admin-agent"})
    workers = store.workers_active()
    assert workers == [{"id": "admin-agent", "status": "active", "claimed": 1}]


# ── events feed (WS) ─────────────────────────────────────────────────────────


def test_events_since_streams_mutations(store: BoardStore) -> None:
    tid = store.create_task({"title": "a"})["task"]["id"]
    store.update_task(tid, {"status": "ready"})
    events, cursor = store.events_since(0)
    kinds = [e["kind"] for e in events]
    assert kinds == ["created", "updated"]
    assert cursor == events[-1]["id"]
    # A resume from the latest cursor yields nothing new.
    tail, cur2 = store.events_since(cursor)
    assert tail == []
    assert cur2 == cursor


def test_events_board_filter(store: BoardStore) -> None:
    store.create_board({"slug": "other"})
    store.create_task({"title": "a"})  # default board
    store.create_task({"title": "b", "board": "other"})
    events, _ = store.events_since(0, board="other")
    assert all(e["board"] == "other" for e in events)
    assert len(events) == 1


# ── first-boot import: Hermes present + partial ──────────────────────────────


class _FakeClient:
    """Stub HermesKanbanClient.request_json for import tests."""

    def __init__(self, responses: dict[tuple[str, str], object], fail: set[str] | None = None):
        self._responses = responses
        self._fail = fail or set()

    async def request_json(self, method, path, *, params=None, **_):
        if path in self._fail:
            raise RuntimeError("hermes unreachable")
        key = (method, path)
        if key == ("GET", "/board"):
            slug = (params or {}).get("board")
            return self._responses.get(("GET", f"/board:{slug}"), {"lanes": {}})
        return self._responses.get(key, {})


def test_import_from_hermes_present(tmp_path: Path) -> None:
    client = _FakeClient(
        {
            ("GET", "/boards"): [
                {"slug": "ops", "name": "Ops", "icon": "▣", "desc": "d"},
            ],
            ("GET", "/board:ops"): {
                "lanes": {
                    "todo": [{"id": "t_imported", "title": "carried over", "status": "todo"}],
                    "done": [],
                }
            },
        }
    )
    s = BoardStore(tmp_path / "board.db")
    asyncio.run(s.ensure_initialized(client))
    boards = {b["slug"] for b in s.list_boards()}
    assert "ops" in boards
    lanes = s.get_board(board="ops")["lanes"]
    assert lanes["todo"][0]["id"] == "t_imported"


def test_import_does_not_rerun_when_not_empty(tmp_path: Path) -> None:
    s = BoardStore(tmp_path / "board.db")
    asyncio.run(s.ensure_initialized(None))  # seeds default, non-empty now
    client = _FakeClient({("GET", "/boards"): [{"slug": "ops", "name": "Ops"}]})
    asyncio.run(s.ensure_initialized(client))  # must NOT import
    assert {b["slug"] for b in s.list_boards()} == {DEFAULT_BOARD_SLUG}


def test_import_partial_board_fetch_failure(tmp_path: Path) -> None:
    """A board whose /board fetch fails still imports as an empty board — the
    import degrades gracefully rather than aborting the whole seed."""
    client = _FakeClient(
        {("GET", "/boards"): [{"slug": "ops", "name": "Ops"}]},
        fail={"/board"},
    )
    s = BoardStore(tmp_path / "board.db")
    asyncio.run(s.ensure_initialized(client))
    assert {b["slug"] for b in s.list_boards()} == {"ops"}
    assert all(v == [] for v in s.get_board(board="ops")["lanes"].values())


def test_import_unreachable_falls_back_to_empty(tmp_path: Path) -> None:
    client = _FakeClient({}, fail={"/boards"})
    s = BoardStore(tmp_path / "board.db")
    asyncio.run(s.ensure_initialized(client))
    assert {b["slug"] for b in s.list_boards()} == {DEFAULT_BOARD_SLUG}
