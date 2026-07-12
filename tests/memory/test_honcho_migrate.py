"""Tests for the Hindsight <-> Honcho bidirectional migration engine."""

from __future__ import annotations

import httpx

from hal0.memory.honcho_migrate import (
    MigrateState,
    migrate_hindsight_to_honcho,
    migrate_honcho_to_hindsight,
)

WORKSPACE = "hal0"
AGENT = "hermes"
USER_PEER = "operator"


def _hal0_pages(pages_by_dataset):
    """Build a MockTransport handler serving GET /api/memory/list.

    ``pages_by_dataset``: {dataset_key: [page_dict, ...]} where dataset_key is
    "shared" or "private" (selected by the X-hal0-Private header).
    """
    calls: list[httpx.Request] = []
    cursors: dict[str, int] = {"shared": 0, "private": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/api/memory/list":
            key = "private" if request.headers.get("x-hal0-private") == "1" else "shared"
            pages = pages_by_dataset.get(key, [])
            idx = cursors[key]
            cursors[key] += 1
            if idx >= len(pages):
                return httpx.Response(200, json={"items": [], "next_cursor": None})
            return httpx.Response(200, json=pages[idx])
        return httpx.Response(404, json={"error": "unhandled"})

    return handler, calls


def _honcho_handler(recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        recorder.append(request)
        path = request.url.path
        if path.endswith("/peers") or path == "/v3/workspaces" or path.endswith("/sessions"):
            return httpx.Response(201, json={"id": "ok"})
        if path.endswith("/conclusions"):
            body = httpx_json(request)
            n = len(body.get("conclusions", []))
            return httpx.Response(201, json=[{"id": f"c{i}"} for i in range(n)])
        return httpx.Response(404, json={"error": "unhandled"})

    return handler


def httpx_json(request: httpx.Request):
    import json

    return json.loads(request.content.decode("utf-8"))


def test_forward_migration_batches_and_ensures_resources(tmp_path):
    page1 = {
        "items": [{"id": f"h{i}", "text": f"fact {i}"} for i in range(150)],
        "next_cursor": "cur1",
    }
    page2 = {
        "items": [{"id": f"h{i}", "text": f"fact {i}"} for i in range(150, 160)],
        "next_cursor": None,
    }
    hal0_handler, _hal0_calls = _hal0_pages({"shared": [page1, page2], "private": []})
    honcho_calls: list[httpx.Request] = []

    hal0_transport = httpx.MockTransport(hal0_handler)
    honcho_transport = httpx.MockTransport(_honcho_handler(honcho_calls))

    with (
        httpx.Client(transport=hal0_transport, base_url="http://127.0.0.1:8080") as hal0_client,
        httpx.Client(transport=honcho_transport, base_url="http://127.0.0.1:8000") as honcho_client,
    ):
        state = MigrateState(tmp_path / "state.json")
        report = migrate_hindsight_to_honcho(
            honcho_base="http://127.0.0.1:8000",
            workspace=WORKSPACE,
            user_peer=USER_PEER,
            agent_id=AGENT,
            datasets=["shared"],
            state=state,
            hal0_http_client=hal0_client,
            honcho_http_client=honcho_client,
        )

    assert report["shared"]["scanned"] == 160
    assert report["shared"]["migrated"] == 160
    assert report["total"]["migrated"] == 160

    # workspace/peer(x2)/session ensure calls happened.
    paths = [r.url.path for r in honcho_calls]
    assert "/v3/workspaces" in paths
    assert paths.count(f"/v3/workspaces/{WORKSPACE}/peers") == 2
    assert f"/v3/workspaces/{WORKSPACE}/sessions" in paths

    # Conclusions were sent in batches of <=100.
    conclusion_calls = [httpx_json(r) for r in honcho_calls if r.url.path.endswith("/conclusions")]
    for body in conclusion_calls:
        assert len(body["conclusions"]) <= 100
    total_conclusions = sum(len(b["conclusions"]) for b in conclusion_calls)
    assert total_conclusions == 160
    first = conclusion_calls[0]["conclusions"][0]
    # observer = user peer (not the agent): user-peer dialectic only
    # retrieves conclusions the peer itself observed.
    assert first["observer_id"] == USER_PEER
    assert first["observed_id"] == USER_PEER
    assert first["session_id"] == "migration__hindsight__shared"

    # State persisted the migrated ids.
    assert len(state.migrated_ids("shared")) == 160


def test_forward_migration_dry_run_writes_nothing(tmp_path):
    page = {"items": [{"id": "h1", "text": "fact"}], "next_cursor": None}
    hal0_handler, _ = _hal0_pages({"shared": [page], "private": []})
    honcho_calls: list[httpx.Request] = []

    hal0_transport = httpx.MockTransport(hal0_handler)
    honcho_transport = httpx.MockTransport(_honcho_handler(honcho_calls))

    with (
        httpx.Client(transport=hal0_transport, base_url="http://127.0.0.1:8080") as hal0_client,
        httpx.Client(transport=honcho_transport, base_url="http://127.0.0.1:8000") as honcho_client,
    ):
        state = MigrateState(tmp_path / "state.json")
        report = migrate_hindsight_to_honcho(
            honcho_base="http://127.0.0.1:8000",
            workspace=WORKSPACE,
            user_peer=USER_PEER,
            agent_id=AGENT,
            datasets=["shared"],
            dry_run=True,
            state=state,
            hal0_http_client=hal0_client,
            honcho_http_client=honcho_client,
        )

    assert report["shared"]["migrated"] == 1
    assert honcho_calls == []  # no honcho writes at all in dry-run
    assert state.migrated_ids("shared") == set()  # dry-run does not persist state
    assert not state.path.exists()


def test_forward_migration_resume_skips_migrated_ids(tmp_path):
    page = {
        "items": [{"id": "h1", "text": "a"}, {"id": "h2", "text": "b"}],
        "next_cursor": None,
    }
    hal0_handler, _ = _hal0_pages({"shared": [page], "private": []})
    honcho_calls: list[httpx.Request] = []

    hal0_transport = httpx.MockTransport(hal0_handler)
    honcho_transport = httpx.MockTransport(_honcho_handler(honcho_calls))

    state = MigrateState(tmp_path / "state.json")
    state.mark_migrated("shared", ["h1"])

    with (
        httpx.Client(transport=hal0_transport, base_url="http://127.0.0.1:8080") as hal0_client,
        httpx.Client(transport=honcho_transport, base_url="http://127.0.0.1:8000") as honcho_client,
    ):
        report = migrate_hindsight_to_honcho(
            honcho_base="http://127.0.0.1:8000",
            workspace=WORKSPACE,
            user_peer=USER_PEER,
            agent_id=AGENT,
            datasets=["shared"],
            resume=True,
            state=state,
            hal0_http_client=hal0_client,
            honcho_http_client=honcho_client,
        )

    assert report["shared"]["scanned"] == 2
    assert report["shared"]["migrated"] == 1
    assert report["shared"]["skipped"] == 1
    conclusion_calls = [httpx_json(r) for r in honcho_calls if r.url.path.endswith("/conclusions")]
    assert len(conclusion_calls) == 1
    assert conclusion_calls[0]["conclusions"][0]["content"] == "b"


def test_forward_migration_private_dataset_sets_private_header(tmp_path):
    page = {"items": [{"id": "p1", "text": "secret"}], "next_cursor": None}
    hal0_handler, calls = _hal0_pages({"shared": [], "private": [page]})
    honcho_calls: list[httpx.Request] = []

    hal0_transport = httpx.MockTransport(hal0_handler)
    honcho_transport = httpx.MockTransport(_honcho_handler(honcho_calls))

    with (
        httpx.Client(transport=hal0_transport, base_url="http://127.0.0.1:8080") as hal0_client,
        httpx.Client(transport=honcho_transport, base_url="http://127.0.0.1:8000") as honcho_client,
    ):
        state = MigrateState(tmp_path / "state.json")
        report = migrate_hindsight_to_honcho(
            honcho_base="http://127.0.0.1:8000",
            workspace=WORKSPACE,
            user_peer=USER_PEER,
            agent_id=AGENT,
            datasets=[f"private:{AGENT}"],
            state=state,
            hal0_http_client=hal0_client,
            honcho_http_client=honcho_client,
        )

    assert report[f"private:{AGENT}"]["migrated"] == 1
    list_calls = [r for r in calls if r.url.path == "/api/memory/list"]
    assert list_calls[0].headers.get("x-hal0-private") == "1"


def _reverse_conclusions_handler(pages, add_calls):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/conclusions/list"):
            page_num = int(request.url.params.get("page", "1"))
            idx = page_num - 1
            if idx >= len(pages):
                return httpx.Response(
                    200,
                    json={
                        "items": [],
                        "total": 0,
                        "page": page_num,
                        "pages": len(pages),
                        "size": 100,
                    },
                )
            return httpx.Response(200, json=pages[idx])
        return httpx.Response(404, json={"error": "unhandled"})

    return handler


def _hal0_add_recorder(add_calls):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/memory/add":
            add_calls.append(request)
            return httpx.Response(200, json={"id": "new-id", "timestamp": "2026-07-11T00:00:00Z"})
        return httpx.Response(404, json={"error": "unhandled"})

    return handler


def test_reverse_migration_writes_hal0_and_skips_migration_sessions(tmp_path):
    conclusions_page = {
        "items": [
            {
                "id": "concl-1",
                "content": "derived fact",
                "observer_id": "hal0",
                "observed_id": USER_PEER,
                "session_id": "chat-session-1",
                "created_at": "2026-07-11T01:00:00Z",
            },
            {
                "id": "concl-2",
                "content": "should be skipped (round trip)",
                "observer_id": AGENT,
                "observed_id": USER_PEER,
                "session_id": "migration__hindsight__shared",
                "created_at": "2026-07-11T02:00:00Z",
            },
        ],
        "total": 2,
        "page": 1,
        "pages": 1,
        "size": 100,
    }
    add_calls: list[httpx.Request] = []
    honcho_transport = httpx.MockTransport(
        _reverse_conclusions_handler([conclusions_page], add_calls)
    )
    hal0_transport = httpx.MockTransport(_hal0_add_recorder(add_calls))

    with (
        httpx.Client(transport=hal0_transport, base_url="http://127.0.0.1:8080") as hal0_client,
        httpx.Client(transport=honcho_transport, base_url="http://127.0.0.1:8000") as honcho_client,
    ):
        state = MigrateState(tmp_path / "state.json")
        report = migrate_honcho_to_hindsight(
            honcho_base="http://127.0.0.1:8000",
            workspace=WORKSPACE,
            agent_id=AGENT,
            state=state,
            hal0_http_client=hal0_client,
            honcho_http_client=honcho_client,
        )

    assert report["scanned"] == 2
    assert report["migrated"] == 1
    assert report["skipped"] == 1
    assert report["watermark"] == "2026-07-11T01:00:00Z"
    assert len(add_calls) == 1
    req = add_calls[0]
    assert req.headers.get("x-hal0-agent") == AGENT
    assert req.headers.get("x-hal0-private") == "1"
    import json

    body = json.loads(req.content.decode("utf-8"))
    assert body["text"] == "derived fact"
    assert body["tags"] == ["honcho-sync"]
    assert body["metadata"]["honcho_conclusion_id"] == "concl-1"
    assert body["document_id"] == "concl-1"
    assert state.watermark() == "2026-07-11T01:00:00Z"


def test_reverse_migration_watermark_advances_and_dedupes_next_run(tmp_path):
    page = {
        "items": [
            {
                "id": "concl-1",
                "content": "fact one",
                "session_id": "s1",
                "created_at": "2026-07-11T01:00:00Z",
            }
        ],
        "total": 1,
        "page": 1,
        "pages": 1,
        "size": 100,
    }
    add_calls: list[httpx.Request] = []
    honcho_transport = httpx.MockTransport(_reverse_conclusions_handler([page], add_calls))
    hal0_transport = httpx.MockTransport(_hal0_add_recorder(add_calls))

    state = MigrateState(tmp_path / "state.json")

    with (
        httpx.Client(transport=hal0_transport, base_url="http://127.0.0.1:8080") as hal0_client,
        httpx.Client(transport=honcho_transport, base_url="http://127.0.0.1:8000") as honcho_client,
    ):
        migrate_honcho_to_hindsight(
            honcho_base="http://127.0.0.1:8000",
            workspace=WORKSPACE,
            agent_id=AGENT,
            state=state,
            hal0_http_client=hal0_client,
            honcho_http_client=honcho_client,
        )
        state.save()

    # Second run against the same page — watermark now equals created_at,
    # so the item is skipped rather than re-added.
    state2 = MigrateState(tmp_path / "state.json")
    assert state2.watermark() == "2026-07-11T01:00:00Z"
    add_calls.clear()
    with (
        httpx.Client(transport=hal0_transport, base_url="http://127.0.0.1:8080") as hal0_client,
        httpx.Client(transport=honcho_transport, base_url="http://127.0.0.1:8000") as honcho_client,
    ):
        report2 = migrate_honcho_to_hindsight(
            honcho_base="http://127.0.0.1:8000",
            workspace=WORKSPACE,
            agent_id=AGENT,
            state=state2,
            hal0_http_client=hal0_client,
            honcho_http_client=honcho_client,
        )
    assert report2["migrated"] == 0
    assert add_calls == []


def test_state_file_round_trip(tmp_path):
    path = tmp_path / "sub" / "state.json"
    state = MigrateState(path)
    state.mark_migrated("shared", ["a", "b"])
    state.set_watermark("2026-07-11T00:00:00Z")
    state.bump_count(2)
    state.save()

    reloaded = MigrateState(path)
    assert reloaded.migrated_ids("shared") == {"a", "b"}
    assert reloaded.watermark() == "2026-07-11T00:00:00Z"
    assert reloaded.data["honcho_to_hindsight"]["count"] == 2


def test_hindsight_to_honcho_skips_derived_types(tmp_path):
    """Only raw fact types (observation/world) cross over; Hindsight's derived
    'experience' rows are skipped — Honcho's deriver builds its own."""
    import httpx

    from hal0.memory.honcho_migrate import MigrateState, migrate_hindsight_to_honcho

    items = [
        {"id": "1", "text": "fact one", "type": "observation"},
        {"id": "2", "text": "fact one | When: today", "type": "experience"},
        {"id": "3", "text": "world fact", "type": "world"},
    ]

    def hal0_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "items": items if "shared" not in str(request.url) or True else [],
                "next_cursor": None,
            },
        )

    calls = {"n": 0}

    def hal0_once(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={"items": items, "next_cursor": None})
        return httpx.Response(200, json={"items": [], "next_cursor": None})

    state = MigrateState(path=tmp_path / "state.json")
    report = migrate_hindsight_to_honcho(
        hal0_base="http://hal0",
        honcho_base="http://honcho",
        workspace="hal0",
        user_peer="alexander",
        agent_id="hermes",
        datasets=["private:hermes"],
        dry_run=True,
        state=state,
        hal0_http_client=httpx.Client(
            transport=httpx.MockTransport(hal0_once), base_url="http://hal0"
        ),
        honcho_http_client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
            base_url="http://honcho",
        ),
    )
    assert report["private:hermes"]["migrated"] == 2
    assert report["private:hermes"]["skipped"] == 1
