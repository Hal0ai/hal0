"""Run ledger in bridge.db (ADR-0043 decision 9).

The ledger is what lets a delegated run survive a client disconnect and a
hal0-api restart: ``runs`` holds the lifecycle and result, ``run_events`` the
last 500 events of each run, replayable by ``seq``. These tests pin the scoping
by client, the event cap, and the cascade on prune.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from hal0.bridge.errors import BridgeError
from hal0.bridge.store import BridgeStore, RunRow


@pytest.fixture
def store(tmp_path: Path) -> Iterator[BridgeStore]:
    opened = BridgeStore(tmp_path / "bridge.db")
    opened.open()
    yield opened


def _row(run_id: str, *, client: str, **over: Any) -> RunRow:
    fields: dict[str, Any] = {
        "run_id": run_id,
        "client": client,
        "agent": "hermes",
        "profile": "default",
        "session_key": None,
        "status": "running",
        "created_at": 100.0,
        "updated_at": 100.0,
        "input_preview": "ping",
        "output": None,
        "error": None,
        "usage_json": None,
        "admission_id": None,
        "execution_generation": None,
        "pending_request_json": None,
        "last_seq": 0,
    }
    fields.update(over)
    return RunRow(**fields)


def test_insert_update_list_scoped(store: BridgeStore) -> None:
    store.insert_run(_row("run_1", client="a"))
    store.insert_run(_row("run_2", client="b"))
    assert [r.run_id for r in store.list_runs(client="a", status=None, limit=10)] == ["run_1"]
    assert store.update_run("run_1", status="completed", output="pong").status == "completed"
    assert store.count_live_runs("a") == 0
    assert store.count_live_runs("b") == 1


def test_list_runs_newest_first_filtered_and_limited(store: BridgeStore) -> None:
    store.insert_run(_row("run_old", client="a", created_at=10.0, status="completed"))
    store.insert_run(_row("run_mid", client="a", created_at=20.0))
    store.insert_run(_row("run_new", client="a", created_at=30.0))
    assert [r.run_id for r in store.list_runs(client="a", status=None, limit=10)] == [
        "run_new",
        "run_mid",
        "run_old",
    ]
    assert [r.run_id for r in store.list_runs(client="a", status=None, limit=2)] == [
        "run_new",
        "run_mid",
    ]
    assert [r.run_id for r in store.list_runs(client="a", status="completed", limit=10)] == [
        "run_old"
    ]
    # client=None lists every client's runs (the admin view).
    store.insert_run(_row("run_b", client="b", created_at=40.0))
    assert store.list_runs(client=None, status=None, limit=10)[0].run_id == "run_b"


def test_get_run_and_missing(store: BridgeStore) -> None:
    store.insert_run(_row("run_1", client="a", session_key="hal0-bridge:a:s1"))
    got = store.get_run("run_1")
    assert got is not None and got.session_key == "hal0-bridge:a:s1"
    assert store.get_run("run_nope") is None


def test_update_run_bumps_updated_at_and_rejects_unknown(store: BridgeStore) -> None:
    store.insert_run(_row("run_1", client="a"))
    after = store.update_run("run_1", status="waiting_for_approval", execution_generation=3)
    assert after.execution_generation == 3
    assert after.updated_at > 100.0
    with pytest.raises(BridgeError) as gone:
        store.update_run("run_nope", status="failed")
    assert gone.value.code == "bridge.run_not_found"
    with pytest.raises(ValueError, match="run_id"):
        store.update_run("run_1", run_id="run_other")
    with pytest.raises(ValueError, match="bogus"):
        store.update_run("run_1", bogus=1)


def test_as_dict_decodes_json_columns(store: BridgeStore) -> None:
    store.insert_run(
        _row(
            "run_1",
            client="a",
            usage_json='{"total_tokens": 9}',
            pending_request_json='{"kind": "clarify", "request_id": "q1"}',
        )
    )
    got = store.get_run("run_1")
    assert got is not None
    body = got.as_dict()
    assert body["usage"] == {"total_tokens": 9}
    assert body["pending"] == {"kind": "clarify", "request_id": "q1"}
    assert "usage_json" not in body and "pending_request_json" not in body
    bare = _row("run_2", client="a").as_dict()
    assert bare["usage"] is None and bare["pending"] is None


def test_events_cap_500_and_since_seq(store: BridgeStore) -> None:
    store.insert_run(_row("run_1", client="a"))
    for i in range(1, 601):
        store.append_event("run_1", i, 0.0, "message.delta", {"delta": "x"})
    evs = store.events("run_1", since_seq=0, limit=1000)
    assert len(evs) == 500 and evs[0]["seq"] == 101
    assert store.events("run_1", since_seq=598, limit=10)[0]["seq"] == 599
    assert store.events("run_1", since_seq=0, limit=3)[2]["seq"] == 103
    first = store.events("run_1", since_seq=598, limit=10)[0]
    assert first["event"] == "message.delta" and first["payload"] == {"delta": "x"}
    run = store.get_run("run_1")
    assert run is not None and run.last_seq == 600


def test_append_event_keeps_highest_last_seq_and_replaces_a_repeat(store: BridgeStore) -> None:
    store.insert_run(_row("run_1", client="a"))
    store.append_event("run_1", 5, 1.0, "tool.started", {"n": 1})
    store.append_event("run_1", 3, 2.0, "tool.started", {"n": 2})
    store.append_event("run_1", 5, 3.0, "tool.completed", {"n": 3})
    run = store.get_run("run_1")
    assert run is not None and run.last_seq == 5
    assert [(e["seq"], e["event"]) for e in store.events("run_1", since_seq=0, limit=10)] == [
        (3, "tool.started"),
        (5, "tool.completed"),
    ]


def test_append_event_for_an_unknown_run_is_run_not_found(store: BridgeStore) -> None:
    with pytest.raises(BridgeError) as gone:
        store.append_event("run_nope", 1, 0.0, "x", {})
    assert gone.value.code == "bridge.run_not_found"


def test_live_runs_excludes_terminal_and_unknown(store: BridgeStore) -> None:
    for run_id, status in [
        ("run_q", "queued"),
        ("run_r", "running"),
        ("run_w", "waiting_for_approval"),
        ("run_s", "stopping"),
        ("run_c", "completed"),
        ("run_f", "failed"),
        ("run_x", "cancelled"),
        ("run_i", "interrupted"),
        ("run_u", "unknown"),
    ]:
        store.insert_run(_row(run_id, client="a", status=status))
    assert sorted(r.run_id for r in store.live_runs()) == ["run_q", "run_r", "run_s", "run_w"]
    assert store.count_live_runs("a") == 4


def test_prune_cascades_events(store: BridgeStore) -> None:
    store.insert_run(_row("run_old", client="a", created_at=0.0))
    store.append_event("run_old", 1, 0.0, "x", {})
    store.insert_run(_row("run_new", client="a", created_at=50.0))
    store.append_event("run_new", 1, 0.0, "x", {})
    assert store.prune_runs(older_than=1.0) == 1
    assert store.events("run_old", since_seq=0, limit=5) == []
    assert store.get_run("run_old") is None
    assert len(store.events("run_new", since_seq=0, limit=5)) == 1


def test_last_events_returns_the_newest_in_order(store: BridgeStore) -> None:
    store.insert_run(_row("run_t", client="cc"))
    for seq in (1, 2, 5, 9, 12):
        store.append_event("run_t", seq, float(seq), "tool.started", {"seq": seq})
    assert [e["seq"] for e in store.last_events("run_t", limit=3)] == [5, 9, 12]
    assert [e["seq"] for e in store.last_events("run_t", limit=10)] == [1, 2, 5, 9, 12]
    assert store.last_events("run_none", limit=3) == []
