"""/v1/models for curated providers (no network) and the dispatch error contract."""

from __future__ import annotations

import time
from typing import Any

from fastapi.testclient import TestClient

from hal0.upstreams.catalog import UpstreamModel
from hal0.upstreams.registry import Upstream


def _seed(client: TestClient, *upstreams: Upstream) -> dict[str, int]:
    reg = client.app.state.upstreams
    for u in list(reg.list()):
        reg.remove(u.name)
    for u in upstreams:
        reg.add(u)
    calls = {"fetch_models": 0, "fetch_model_payload": 0}

    async def fake_fetch(name: str) -> list[str]:
        calls["fetch_models"] += 1
        return []

    async def fake_payload(name: str) -> Any:
        calls["fetch_model_payload"] += 1
        if name == "plain":
            return {"data": [{"id": "gpt-x"}, {"id": "gpt-y"}]}
        return None

    reg.fetch_models = fake_fetch  # type: ignore[method-assign]
    reg.fetch_model_payload = fake_payload  # type: ignore[method-assign]
    return calls


def _curated(*rows: tuple[str, bool], **kw: Any) -> Upstream:
    return Upstream(
        name="mm",
        kind="remote",
        url="https://mm.invalid/v1",
        curated=True,
        models=tuple(UpstreamModel(id=i, enabled=on) for i, on in rows),
        **kw,
    )


def _rows(client: TestClient) -> list[dict[str, Any]]:
    resp = client.get("/v1/models")
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_curated_provider_listed_as_canonical_ids_without_network(client: TestClient) -> None:
    calls = _seed(client, _curated(("MiniMax-M2.5", True)))
    rows = [r for r in _rows(client) if r["owned_by"] == "mm"]
    assert [r["id"] for r in rows] == ["mm/MiniMax-M2.5"]
    assert calls == {"fetch_models": 0, "fetch_model_payload": 0}


def test_disabled_rows_not_listed(client: TestClient) -> None:
    _seed(client, _curated(("On", True), ("Off", False)))
    ids = [r["id"] for r in _rows(client) if r["owned_by"] == "mm"]
    assert ids == ["mm/On"]


def test_advertise_models_false_hides_the_provider(client: TestClient) -> None:
    _seed(client, _curated(("On", True), advertise_models=False))
    assert [r for r in _rows(client) if r["owned_by"] == "mm"] == []


def test_uncurated_provider_listing_unchanged(client: TestClient) -> None:
    plain = Upstream(name="plain", kind="remote", url="https://plain.invalid/v1")
    _seed(client, plain)
    rows = [r for r in _rows(client) if r["owned_by"] == "plain"]
    assert [r["id"] for r in rows] == ["gpt-x", "gpt-y"]


def test_uncurated_listing_refetches_only_when_stale(client: TestClient) -> None:
    plain = Upstream(name="plain", kind="remote", url="https://plain.invalid/v1")
    calls = _seed(client, plain)
    _rows(client)
    _rows(client)
    assert calls["fetch_model_payload"] == 1
    cache = client.app.state.upstream_discovery
    models, _at = cache.get("plain")
    cache.put("plain", models, now=time.time() - 10**7)
    _rows(client)
    assert calls["fetch_model_payload"] == 2


def test_failed_refetch_serves_the_stale_entry(client: TestClient) -> None:
    plain = Upstream(name="plain", kind="remote", url="https://plain.invalid/v1")
    _seed(client, plain)
    _rows(client)
    cache = client.app.state.upstream_discovery
    models, _at = cache.get("plain")
    cache.put("plain", models, now=time.time() - 10**7)

    async def boom(name: str) -> Any:
        return None

    client.app.state.upstreams.fetch_model_payload = boom
    rows = [r for r in _rows(client) if r["owned_by"] == "plain"]
    assert [r["id"] for r in rows] == ["gpt-x", "gpt-y"]


def test_disabled_model_request_returns_404_with_its_own_code(client: TestClient) -> None:
    _seed(client, _curated(("On", True), ("Off", False)))
    for stream in (False, True):
        resp = client.post(
            "/v1/chat/completions",
            json={
                "model": "mm/Off",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": stream,
            },
        )
        assert resp.status_code == 404, resp.text
        assert resp.json()["error"]["code"] == "upstream.model_not_enabled"
    resp = client.post("/v1/embeddings", json={"model": "mm/Off", "input": "hi"})
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "upstream.model_not_enabled"


def test_ambiguous_bare_id_request_returns_400(client: TestClient) -> None:
    second = Upstream(
        name="mm2",
        kind="remote",
        url="https://mm2.invalid/v1",
        curated=True,
        models=(UpstreamModel(id="On", enabled=True),),
    )
    _seed(client, _curated(("On", True)), second)
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "On", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "upstream.model_ambiguous"


def test_fetch_and_cache_serves_curated_enabled_ids_without_network(client: TestClient) -> None:
    import asyncio

    calls = _seed(client, _curated(("On", True), ("Off", False)))
    up = client.app.state.upstreams.get("mm")
    got = asyncio.run(client.app.state.fetch_and_cache(up))
    assert got == ["On"]
    assert client.app.state.model_cache["mm"] == ["On"]
    assert calls["fetch_models"] == 0


def test_reseed_follows_registry_changes_and_deletes() -> None:
    from hal0.upstreams.catalog import reseed_curated_cache
    from hal0.upstreams.registry import UpstreamRegistry

    reg = UpstreamRegistry()
    reg.add(_curated(("A", True), ("B", False)))
    reg.add(Upstream(name="plain", kind="remote", url="https://plain.invalid/v1"))
    cache: dict[str, list[str]] = {"plain": ["from-network"]}
    reseed_curated_cache(reg, cache)
    assert cache == {"plain": ["from-network"], "mm": ["A"]}
    reg.remove("mm")
    reg.add(_curated(("A", True), ("B", True)))
    reseed_curated_cache(reg, cache, "mm")
    assert cache["mm"] == ["A", "B"]
    reg.remove("mm")
    reseed_curated_cache(reg, cache, "mm")
    assert "mm" not in cache
    assert cache["plain"] == ["from-network"]


def test_slot_upstream_listing_follows_a_changed_payload(client: TestClient) -> None:
    slot = Upstream(name="box", kind="remote", url="http://127.0.0.1:1/v1", slot_name="box")
    _seed(client, slot)
    payload: dict[str, Any] = {"data": [{"id": "old-model"}]}

    async def live(name: str) -> list[str]:
        return [m["id"] for m in payload["data"]]

    client.app.state.upstreams.fetch_models = live
    assert "old-model" in [r["id"] for r in _rows(client) if r["owned_by"] == "box"]
    payload["data"] = [{"id": "new-model"}]
    ids = [r["id"] for r in _rows(client) if r["owned_by"] == "box"]
    assert "new-model" in ids and "old-model" not in ids


def test_agent_managed_listing_follows_a_changed_payload(client: TestClient) -> None:
    hermes = Upstream(
        name="hermes.x", kind="remote", url="http://h.invalid/v1", managed_by="agent:hermes"
    )
    _seed(client, hermes)
    payload: dict[str, Any] = {"data": [{"id": "alias-a"}]}

    async def live(name: str) -> list[str]:
        return [m["id"] for m in payload["data"]]

    client.app.state.upstreams.fetch_models = live
    assert "alias-a" in [r["id"] for r in _rows(client) if r["owned_by"] == "hermes.x"]
    payload["data"] = [{"id": "alias-b"}]
    ids = [r["id"] for r in _rows(client) if r["owned_by"] == "hermes.x"]
    assert "alias-b" in ids and "alias-a" not in ids


def test_empty_remote_answer_is_refetched_next_call(client: TestClient) -> None:
    plain = Upstream(name="plain", kind="remote", url="https://plain.invalid/v1")
    calls = _seed(client, plain)
    answers: list[Any] = [{"data": []}, {"data": [{"id": "late-model"}]}]

    async def live(name: str) -> Any:
        calls["fetch_model_payload"] += 1
        return answers.pop(0)

    client.app.state.upstreams.fetch_model_payload = live
    assert [r for r in _rows(client) if r["owned_by"] == "plain"] == []
    assert [r["id"] for r in _rows(client) if r["owned_by"] == "plain"] == ["late-model"]


def test_status_never_network_fills_a_curated_cache_entry(client: TestClient) -> None:
    calls = _seed(client, _curated(("Off", False)))
    client.app.state.model_cache.pop("mm", None)
    assert client.get("/api/status").status_code == 200
    assert client.app.state.model_cache["mm"] == []
    assert calls["fetch_models"] == 0
