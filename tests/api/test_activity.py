"""Tests for the durable /api/activity surface.

The lifespan wires ``app.state.audit`` (an AuditStore) and mirrors every
EventBus emit into it, so a freshly-booted client already carries the
``system.restart`` boot event. We seed extra rows directly through the store
to exercise the filter + export + epoch contract.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient


def _seed(client: TestClient, **kw) -> int:
    """Record one row through the live store (async helper run to completion)."""
    return asyncio.run(client.app.state.audit.record(**kw))


def test_activity_returns_records_envelope_with_epoch(client: TestClient) -> None:
    r = client.get("/api/activity")
    assert r.status_code == 200
    body = r.json()
    assert "records" in body and "next_since" in body and "epoch" in body
    assert isinstance(body["epoch"], str) and body["epoch"]
    # The boot system.restart event was mirrored into the durable store.
    actions = {rec["action"] for rec in body["records"]}
    assert "system.restart" in actions


def test_activity_filters_by_severity(client: TestClient) -> None:
    _seed(
        client,
        kind="action",
        category="slot",
        action="slot.load",
        target="npu",
        actor="dashboard",
        severity="error",
        outcome="error",
        message="OOM",
        error="OOM",
    )
    r = client.get("/api/activity", params={"severity": "error"})
    assert r.status_code == 200
    recs = r.json()["records"]
    assert recs and all(x["severity"] == "error" for x in recs)
    assert any(x["target"] == "npu" for x in recs)


def test_activity_filters_by_category_and_kind(client: TestClient) -> None:
    _seed(
        client,
        kind="action",
        category="profile",
        action="profile.create",
        target="p1",
        actor="dashboard",
        severity="ok",
        outcome="ok",
        message="created p1",
    )
    r = client.get("/api/activity", params={"category": "profile", "kind": "action"})
    recs = r.json()["records"]
    assert recs and all(x["category"] == "profile" for x in recs)


def test_activity_rejects_invalid_severity(client: TestClient) -> None:
    r = client.get("/api/activity", params={"severity": "bogus"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "activity.invalid_query"


def test_activity_since_cursor_advances(client: TestClient) -> None:
    first = client.get("/api/activity").json()
    nxt = first["next_since"]
    _seed(
        client,
        kind="action",
        category="model",
        action="model.delete",
        target="qwen",
        actor="cli",
        severity="ok",
        outcome="ok",
        message="deleted qwen",
    )
    r = client.get("/api/activity", params={"since": nxt})
    recs = r.json()["records"]
    assert [x["action"] for x in recs] == ["model.delete"]


def test_activity_export_csv(client: TestClient) -> None:
    _seed(
        client,
        kind="action",
        category="slot",
        action="slot.restart",
        target="chat",
        actor="dashboard",
        severity="ok",
        outcome="ok",
        message="restarted chat",
    )
    r = client.get("/api/activity/export", params={"fmt": "csv"})
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "attachment" in r.headers.get("content-disposition", "")
    assert r.text.splitlines()[0].startswith("id,ts,kind,category,action")
    assert "slot.restart" in r.text


def test_activity_export_json(client: TestClient) -> None:
    r = client.get("/api/activity/export", params={"fmt": "json"})
    assert r.status_code == 200
    assert "application/json" in r.headers["content-type"]


def test_activity_epoch_is_stable_within_process(client: TestClient) -> None:
    a = client.get("/api/activity").json()["epoch"]
    b = client.get("/api/activity").json()["epoch"]
    assert a == b


def test_activity_stream_backfill_respects_limit(client: TestClient) -> None:
    """`limit` caps the SSE connect backfill to the newest N matching rows.

    The dashboard pane keeps a 200-row ring; without the cap the stream
    replayed the full 1000-row durable backlog one frame at a time and the
    slots page churned through hundreds of frames it immediately discarded.

    Called directly (not through TestClient streaming — the live tail is an
    infinite generator) with a fake already-disconnected request, so the
    generator drains the backfill and exits at the first tail poll.
    """
    import json as _json

    from hal0.api.routes.activity import stream_activity

    for i in range(30):
        _seed(
            client,
            kind="event",
            category="slot",
            action="slot.state",
            message=f"seed {i}",
        )

    class _DisconnectedRequest:
        def __init__(self, app) -> None:
            self.app = app

        async def is_disconnected(self) -> bool:
            return True

    async def _drain() -> list[dict]:
        resp = await stream_activity(
            _DisconnectedRequest(client.app),  # type: ignore[arg-type]
            since=0,
            category=None,
            action=None,
            severity=None,
            outcome=None,
            actor=None,
            kind=None,
            search=None,
            limit=10,
        )
        frames: list[dict] = []
        async for chunk in resp.body_iterator:
            text = chunk.decode() if isinstance(chunk, bytes) else chunk
            for line in text.splitlines():
                if line.startswith("data: "):
                    frames.append(_json.loads(line[len("data: ") :]))
        return frames

    frames = asyncio.run(_drain())
    assert len(frames) == 10
    # The newest 10 seeds, replayed oldest→newest.
    messages = [f["record"]["message"] for f in frames]
    assert messages == [f"seed {i}" for i in range(20, 30)]
