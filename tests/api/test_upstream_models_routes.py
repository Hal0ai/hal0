"""Per-model routes on upstream providers: list, enable/edit, refresh, create with rows."""

from __future__ import annotations

import os
import time
import tomllib
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from hal0.config.loader import load_upstreams_config, save_upstreams_config
from hal0.config.schema import UpstreamEntry, UpstreamModelEntry
from hal0.dispatcher.router import NoRouteFound
from hal0.upstreams.catalog import MODEL_TYPES, ModelNotEnabled, UpstreamModel
from hal0.upstreams.registry import Upstream, upstream_from_entry
from tests.dispatcher.test_router import make_request

URL = "https://mm.invalid/v1"
PAYLOAD = {
    "data": [
        {
            "id": "qwen/qwen3-coder",
            "context_length": 262144,
            "pricing": {"prompt": "0.000001", "completion": "0.000002"},
        },
        {"id": "text-embedding-x"},
        {"id": "gpt-5-mini"},
    ]
}


def _etc(client: TestClient) -> Path:
    return Path(os.environ["HAL0_HOME"]) / "etc" / "hal0"


def _toml(client: TestClient) -> dict[str, Any]:
    return tomllib.loads((_etc(client) / "upstreams.toml").read_text(encoding="utf-8"))


def _fresh(client: TestClient, payload: Any = PAYLOAD) -> dict[str, int]:
    """Empty the registry and stub the provider's /models body."""
    reg = client.app.state.upstreams
    for u in list(reg.list()):
        reg.remove(u.name)
    calls = {"n": 0}

    async def fake(name: str) -> Any:
        calls["n"] += 1
        return payload

    reg.fetch_model_payload = fake  # type: ignore[method-assign]
    return calls


def _down(client: TestClient) -> None:
    async def nothing(name: str) -> Any:
        return None

    client.app.state.upstreams.fetch_model_payload = nothing


def _add(client: TestClient, **kw: Any) -> Upstream:
    """Persist a remote row and register it live."""
    entry = UpstreamEntry(name=kw.pop("name", "mm"), kind="remote", url=URL, **kw)
    cfg = load_upstreams_config()
    cfg.upstream.append(entry)
    save_upstreams_config(cfg)
    u = upstream_from_entry(entry)
    client.app.state.upstreams.add(u)
    return u


def _create(client: TestClient, **kw: Any) -> dict[str, Any]:
    """Create through the route; curated unless the test passes ``models=None``."""
    if kw.get("models", ...) is None:
        del kw["models"]
    else:
        kw.setdefault("models", [])
    body = {"name": "mm", "url": URL, **kw}
    res = client.post("/api/upstreams", json=body)
    assert res.status_code == 201, res.text
    return res.json()


def _models(client: TestClient, name: str = "mm") -> dict[str, Any]:
    res = client.get(f"/api/upstreams/{name}/models")
    assert res.status_code == 200, res.text
    return res.json()


def _by_id(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {m["id"]: m for m in body["models"]}


def _resolve(client: TestClient, model: str):
    return client.portal.call(
        client.app.state.dispatcher.dispatch,
        make_request(),
        {"model": model, "messages": [{"role": "user", "content": "hi"}]},
    )


# ── create ────────────────────────────────────────────────────────────────────


def test_create_with_models_writes_curated_row(client: TestClient) -> None:
    _fresh(client)
    out = _create(
        client,
        catalog_id="openai",
        budget_monthly_usd=25.0,
        models=[
            {"id": "gpt-5-mini", "enabled": True, "price_in": 0.25, "price_unit": "Mtok"},
            {"id": "gpt-x", "enabled": False, "type": "embed"},
        ],
    )
    assert out["curated"] is True
    assert out["catalog_id"] == "openai"
    assert out["budget_monthly_usd"] == 25.0
    assert (out["enabled_count"], out["models_total"]) == (1, 2)
    row = _toml(client)["upstream"][0]
    assert row["curated"] is True and row["catalog_id"] == "openai"
    assert [m["id"] for m in row["models"]] == ["gpt-5-mini", "gpt-x"]
    assert client.app.state.upstream_models["mm"] == ["gpt-5-mini"]


def test_create_never_accepts_a_key(client: TestClient) -> None:
    _fresh(client)
    res = client.post("/api/upstreams", json={"name": "mm", "url": URL, "key": "sk-secret"})
    assert res.status_code in (400, 422)
    assert "sk-secret" not in res.text


def test_create_with_models_omitted_is_uncurated_and_passes_through(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fresh(client)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    out = _create(client, catalog_id="openai", budget_monthly_usd=5.0, models=None)
    assert out["curated"] is False and out["models_total"] == 0
    assert out["catalog_id"] == "openai" and out["budget_monthly_usd"] == 5.0
    row = _toml(client)["upstream"][0]
    assert row.get("curated", False) is False
    # the cache is left to the dispatcher, as for any new uncurated row
    assert "mm" not in client.app.state.upstream_models
    call = _resolve(client, "gpt-5-mini")
    assert call.upstream_name == "mm" and call.resolved_model == "gpt-5-mini"


def test_create_with_models_omitted_accepts_filters(client: TestClient) -> None:
    _fresh(client)
    out = _create(client, model_filters={"include": ["gpt-*"]}, models=None)
    assert out["curated"] is False
    assert out["model_filters"]["include"] == ["gpt-*"]


def test_create_with_empty_models_is_curated_and_routes_nothing(client: TestClient) -> None:
    _fresh(client)
    out = _create(client, models=[])
    assert out["curated"] is True and out["models_total"] == 0
    assert client.app.state.upstream_models["mm"] == []
    # discovery lists models, but none is enabled: every row is discovered-only and off
    rows = _models(client)["models"]
    assert rows and all(m["enabled"] is False for m in rows)
    with pytest.raises(NoRouteFound):
        _resolve(client, "gpt-5-mini")


def test_create_with_models_and_filters_is_400(client: TestClient) -> None:
    _fresh(client)
    res = client.post(
        "/api/upstreams",
        json={
            "name": "mm",
            "url": URL,
            "models": [{"id": "m1"}],
            "model_filters": {"include": ["gpt-*"]},
        },
    )
    assert res.status_code == 400, res.text


# ── list ──────────────────────────────────────────────────────────────────────


def test_list_merges_stored_and_discovered(client: TestClient) -> None:
    _fresh(client)
    _create(
        client,
        catalog_id="openai",
        models=[{"id": "gpt-5-mini", "type": "chat", "context": 4096, "price_in": 9.0}],
    )
    body = _models(client)
    assert body["name"] == "mm" and body["curated"] is True
    assert body["stale"] is False and isinstance(body["fetched_at"], float)
    assert body["protected"] is False
    rows = _by_id(body)
    assert set(rows) == {"qwen/qwen3-coder", "text-embedding-x", "gpt-5-mini"}
    stored = rows["gpt-5-mini"]
    assert stored["enabled"] is True and stored["discovered"] is True
    assert stored["canonical_id"] == "mm/gpt-5-mini"
    assert stored["recommended"] is True  # preset row
    assert stored["in_use_by"] == []
    # the operator's stored values win over discovery
    assert (stored["context"], stored["price_in"]) == (4096, 9.0)
    found = rows["qwen/qwen3-coder"]
    assert found["enabled"] is False and found["discovered"] is True
    assert found["context"] == 262144 and found["price_in"] == pytest.approx(1.0)
    assert rows["text-embedding-x"]["type"] == "embed"
    assert list(rows).index("gpt-5-mini") < list(rows).index("qwen/qwen3-coder")


def test_list_marks_vanished_model_not_discovered(client: TestClient) -> None:
    _fresh(client, {"data": [{"id": "still-here"}]})
    _create(client, models=[{"id": "gone"}, {"id": "still-here"}])
    rows = _by_id(_models(client))
    assert rows["gone"]["discovered"] is False
    assert rows["still-here"]["discovered"] is True


def test_list_survives_a_provider_that_is_down(client: TestClient) -> None:
    _fresh(client)
    _create(client, models=[{"id": "kept"}])
    _down(client)
    body = _models(client)
    assert body["stale"] is True and body["fetched_at"] is None
    assert [m["id"] for m in body["models"]] == ["kept"]
    # a cached discovery is still served when the refetch fails
    client.app.state.upstream_discovery.put(
        "mm", _discovered("cached-one"), now=time.time() - 10**7
    )
    body = _models(client)
    assert body["stale"] is True and body["fetched_at"] is not None
    assert {m["id"] for m in body["models"]} == {"kept", "cached-one"}


def _discovered(*ids: str):
    from hal0.upstreams.discovery import DiscoveredModel

    return [DiscoveredModel(id=i, type="chat", type_source="guess") for i in ids]


def test_list_refetches_only_when_stale(client: TestClient) -> None:
    calls = _fresh(client)
    _create(client)
    _models(client)
    _models(client)
    assert calls["n"] == 1


def test_list_unknown_upstream_is_404(client: TestClient) -> None:
    _fresh(client)
    res = client.get("/api/upstreams/nope/models")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "upstream.not_found"


# ── patch ─────────────────────────────────────────────────────────────────────


def test_patch_enable_reseeds_routing(client: TestClient) -> None:
    _fresh(client)
    _create(client)
    with pytest.raises(NoRouteFound):
        _resolve(client, "mm/qwen/qwen3-coder")
    res = client.patch("/api/upstreams/mm/models/qwen/qwen3-coder", json={"enabled": True})
    assert res.status_code == 200, res.text
    row = res.json()
    assert row["id"] == "qwen/qwen3-coder" and row["enabled"] is True
    assert row["canonical_id"] == "mm/qwen/qwen3-coder"
    assert row["context"] == 262144  # discovered values copied into the new row
    assert client.app.state.upstream_models["mm"] == ["qwen/qwen3-coder"]
    call = _resolve(client, "mm/qwen/qwen3-coder")
    assert call.upstream_name == "mm" and call.resolved_model == "qwen/qwen3-coder"
    stored = _toml(client)["upstream"][0]["models"]
    assert stored[0]["id"] == "qwen/qwen3-coder" and stored[0]["context"] == 262144


def test_patch_disable_stops_routing(client: TestClient) -> None:
    _fresh(client)
    _create(client, models=[{"id": "m1", "enabled": True}])
    assert _resolve(client, "mm/m1").upstream_name == "mm"
    res = client.patch("/api/upstreams/mm/models/m1", json={"enabled": False})
    assert res.status_code == 200 and res.json()["enabled"] is False
    assert client.app.state.upstream_models["mm"] == []
    with pytest.raises(ModelNotEnabled):
        _resolve(client, "mm/m1")
    http = client.post(
        "/v1/chat/completions",
        json={"model": "mm/m1", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert http.status_code == 404
    assert http.json()["error"]["code"] == "upstream.model_not_enabled"


def test_patch_model_id_with_slashes(client: TestClient) -> None:
    _fresh(client, {"data": [{"id": "a/models/b:free.v2"}]})
    _create(client)
    res = client.patch("/api/upstreams/mm/models/a/models/b:free.v2", json={"enabled": True})
    assert res.status_code == 200, res.text
    assert res.json()["id"] == "a/models/b:free.v2"
    assert _by_id(_models(client))["a/models/b:free.v2"]["enabled"] is True


def test_patch_model_does_not_swallow_sibling_routes(client: TestClient) -> None:
    _fresh(client)
    _create(client)
    assert client.get("/api/upstreams/mm/models").status_code == 200
    assert client.post("/api/upstreams/mm/refresh").status_code == 200
    res = client.patch("/api/upstreams/mm/models/refresh", json={"enabled": True})
    assert res.status_code == 200 and res.json()["id"] == "refresh"


def test_patch_edits_type_context_and_prices(client: TestClient) -> None:
    _fresh(client)
    _create(client, models=[{"id": "m1"}])
    res = client.patch(
        "/api/upstreams/mm/models/m1",
        json={
            "type": "embed",
            "context": 8192,
            "price_in": 0.5,
            "price_out": 0.0,
            "price_unit": "Mchar",
        },
    )
    assert res.status_code == 200, res.text
    row = res.json()
    assert (row["type"], row["context"], row["price_in"], row["price_unit"]) == (
        "embed",
        8192,
        0.5,
        "Mchar",
    )
    assert row["enabled"] is True  # untouched
    cleared = client.patch("/api/upstreams/mm/models/m1", json={"context": None})
    assert cleared.json()["context"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"type": "video"},
        {"price_unit": "per-fish"},
        {"context": 0},
        {"price_in": -1},
        {"enabled": None},
        {"nonsense": 1},
        {"curated": False},
    ],
)
def test_patch_bad_body_is_422(client: TestClient, body: dict[str, Any]) -> None:
    _fresh(client)
    _create(client, models=[{"id": "m1"}])
    res = client.patch("/api/upstreams/mm/models/m1", json=body)
    assert res.status_code == 422, res.text


def test_patch_upstream_cannot_flip_curated(client: TestClient) -> None:
    _fresh(client)
    _create(client)
    res = client.patch("/api/upstreams/mm", json={"curated": False})
    assert res.status_code == 422
    assert client.app.state.upstreams.get("mm").curated is True


def test_patch_on_agent_managed_is_409(client: TestClient) -> None:
    _fresh(client)
    client.app.state.upstreams.add(
        Upstream(name="hermes", kind="remote", url=URL, managed_by="agent:hermes")
    )
    for call in (
        lambda: client.patch("/api/upstreams/hermes/models/m1", json={"enabled": True}),
        lambda: client.post("/api/upstreams/hermes/refresh"),
    ):
        res = call()
        assert res.status_code == 409, res.text
        assert res.json()["error"]["code"] == "upstream.protected"


def test_patch_on_slot_backed_is_409(client: TestClient) -> None:
    _fresh(client)
    client.app.state.upstreams.add(
        Upstream(name="primary", kind="slot", url="http://127.0.0.1:8081/v1", slot_name="primary")
    )
    res = client.patch("/api/upstreams/primary/models/m1", json={"enabled": True})
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "upstream.protected"


def test_list_on_protected_is_read_only_live_ids(client: TestClient) -> None:
    _fresh(client)
    client.app.state.upstreams.add(
        Upstream(name="hermes", kind="remote", url=URL, managed_by="agent:hermes")
    )
    client.app.state.upstream_models["hermes"] = ["live-a", "live-b"]
    body = _models(client, "hermes")
    assert body["curated"] is False
    assert body["protected"] is True
    assert [m["id"] for m in body["models"]] == ["live-a", "live-b"]
    assert all(m["enabled"] and m["discovered"] for m in body["models"])


def test_patch_on_uncurated_is_409(client: TestClient) -> None:
    _fresh(client)
    _add(client)
    res = client.patch("/api/upstreams/mm/models/m1", json={"enabled": True})
    assert res.status_code == 409
    assert "not converted yet; refresh when the provider is reachable" in res.text


def test_model_filters_patch_on_curated_is_400(client: TestClient) -> None:
    _fresh(client)
    _create(client)
    res = client.patch("/api/upstreams/mm", json={"model_filters": {"include": ["gpt-*"]}})
    assert res.status_code == 400, res.text


def test_patch_unknown_upstream_is_404(client: TestClient) -> None:
    _fresh(client)
    res = client.patch("/api/upstreams/nope/models/m1", json={"enabled": True})
    assert res.status_code == 404


def test_patch_id_with_whitespace_is_refused(client: TestClient) -> None:
    _fresh(client)
    _create(client)
    res = client.patch("/api/upstreams/mm/models/%20padded", json={"enabled": True})
    assert res.status_code in (400, 422), res.text
    assert _toml(client)["upstream"][0].get("models", []) == []


# ── refresh ───────────────────────────────────────────────────────────────────


def test_refresh_updates_fetched_at(client: TestClient) -> None:
    calls = _fresh(client)
    _create(client)
    first = _models(client)["fetched_at"]
    cache = client.app.state.upstream_discovery
    models, _at = cache.get("mm")
    cache.put("mm", models, now=time.time() - 100)
    res = client.post("/api/upstreams/mm/refresh")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["fetched_at"] > first - 1 and body["stale"] is False
    assert time.time() - body["fetched_at"] < 50
    assert calls["n"] == 2
    assert {m["id"] for m in body["models"]} == {
        "qwen/qwen3-coder",
        "text-embedding-x",
        "gpt-5-mini",
    }


def test_refresh_on_curated_does_not_touch_the_file(client: TestClient) -> None:
    _fresh(client)
    _create(client, models=[{"id": "m1"}])
    path = _etc(client) / "upstreams.toml"
    before = path.read_bytes()
    client.post("/api/upstreams/mm/refresh")
    assert path.read_bytes() == before
    assert not path.with_name("upstreams.toml.pre-convert").exists()


def test_refresh_converts_an_uncurated_row(client: TestClient) -> None:
    _fresh(client)
    _add(client, model_filters={"include": ["gpt-*"]})
    path = _etc(client) / "upstreams.toml"
    original = path.read_bytes()
    res = client.post("/api/upstreams/mm/refresh")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["curated"] is True and body["stale"] is False
    rows = _by_id(body)
    assert rows["gpt-5-mini"]["enabled"] is True  # the old filter advertised it
    assert rows["qwen/qwen3-coder"]["enabled"] is False
    row = _toml(client)["upstream"][0]
    assert row["curated"] is True and "model_filters" not in row
    assert path.with_name("upstreams.toml.pre-convert").read_bytes() == original
    assert client.app.state.upstream_models["mm"] == ["gpt-5-mini"]
    assert client.app.state.upstreams.get("mm").curated is True


def test_refresh_of_an_unreachable_uncurated_row_leaves_it_alone(client: TestClient) -> None:
    _fresh(client)
    _add(client)
    _down(client)
    path = _etc(client) / "upstreams.toml"
    before = path.read_bytes()
    res = client.post("/api/upstreams/mm/refresh")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["curated"] is False and body["stale"] is True
    assert path.read_bytes() == before
    assert client.app.state.upstreams.get("mm").curated is False


def test_refresh_converts_only_the_named_row(client: TestClient) -> None:
    _fresh(client)
    _add(client, name="aa")
    _add(client, name="bb")
    client.post("/api/upstreams/aa/refresh")
    reg = client.app.state.upstreams
    assert reg.get("aa").curated is True and reg.get("bb").curated is False


def test_refresh_unknown_upstream_is_404(client: TestClient) -> None:
    _fresh(client)
    assert client.post("/api/upstreams/nope/refresh").status_code == 404


# ── responses never carry a key ───────────────────────────────────────────────


def test_no_response_carries_the_key(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _fresh(client)
    monkeypatch.setenv("MM_API_KEY", "sk-very-secret")
    _create(client, auth_value_env="MM_API_KEY", models=[{"id": "m1"}])
    seen = [
        _models(client),
        client.patch("/api/upstreams/mm/models/m1", json={"enabled": False}).json(),
        client.post("/api/upstreams/mm/refresh").json(),
        client.get("/api/upstreams/mm").json(),
    ]
    assert "sk-very-secret" not in repr(seen)


# ── schema carry-overs ────────────────────────────────────────────────────────


def test_model_type_sets_agree() -> None:
    from hal0.config import schema

    assert schema._VALID_MODEL_TYPES == MODEL_TYPES


@pytest.mark.parametrize("unit", ["Mtok", "Mchar", "image", "1k_search"])
def test_price_unit_accepts_the_closed_set(unit: str) -> None:
    assert UpstreamModelEntry(id="m", price_unit=unit).price_unit == unit


@pytest.mark.parametrize("unit", ["", "mtok", "per-fish", "Mtok "])
def test_price_unit_rejects_anything_else(unit: str) -> None:
    with pytest.raises(ValidationError):
        UpstreamModelEntry(id="m", price_unit=unit)


@pytest.mark.parametrize("bad", [" m", "m ", "\tm", "m\n"])
def test_model_id_rejects_outer_whitespace(bad: str) -> None:
    with pytest.raises(ValidationError):
        UpstreamModelEntry(id=bad)


# ── GET /v1/models/{id} with a slash ─────────────────────────────────────────


def test_v1_get_model_with_slashes_in_canonical_id(client: TestClient) -> None:
    _fresh(client)
    _create(client, name="openrouter", models=[{"id": "qwen/qwen3-coder"}])
    _create(client, name="mm", models=[{"id": "MiniMax-M2.5"}])
    for ref in ("mm/MiniMax-M2.5", "openrouter/qwen/qwen3-coder"):
        res = client.get(f"/v1/models/{ref}")
        assert res.status_code == 200, res.text
        assert res.json()["id"] == ref


# ── credentials normalise the key like the probe ─────────────────────────────


def test_credentials_strip_a_bearer_prefix(client: TestClient, tmp_hal0_home: str) -> None:
    _fresh(client)
    _create(client, auth_value_env="MM_API_KEY")
    res = client.post(
        "/api/providers/mm/credentials",
        json={"key": "MM_API_KEY", "value": "Bearer sk-abc123"},
    )
    assert res.status_code == 200, res.text
    assert os.environ["MM_API_KEY"] == "sk-abc123"
    assert "sk-abc123" not in res.text


@pytest.mark.parametrize("value", ["sk abc", "sk-é", "   ", "sk\tabc"])
def test_credentials_reject_what_the_probe_rejects(client: TestClient, value: str) -> None:
    _fresh(client)
    _create(client, auth_value_env="MM_API_KEY")
    res = client.post("/api/providers/mm/credentials", json={"key": "MM_API_KEY", "value": value})
    assert res.status_code == 400, res.text
    assert res.json()["error"]["code"] == "provider.credential_write_failed"
    assert "key must be printable ASCII without whitespace" in res.text


def test_credentials_keep_the_prefix_for_a_custom_header(
    client: TestClient, tmp_hal0_home: str
) -> None:
    _fresh(client)
    _create(
        client,
        auth_style="header",
        auth_header="X-Authorization",
        auth_value_env="MM_API_KEY",
    )
    res = client.post(
        "/api/providers/mm/credentials",
        json={"key": "MM_API_KEY", "value": "Bearer sk-abc123"},
    )
    assert res.status_code == 200, res.text
    assert os.environ["MM_API_KEY"] == "Bearer sk-abc123"
    bad = client.post(
        "/api/providers/mm/credentials",
        json={"key": "MM_API_KEY", "value": "Bearer two parts"},
    )
    assert bad.status_code == 400


def test_refresh_reports_a_conversion_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fresh(client)
    _add(client)
    from hal0.upstreams import convert

    async def boom(*a: Any, **k: Any) -> None:
        raise OSError("disk on fire")

    monkeypatch.setattr(convert, "convert_legacy_upstreams", boom)
    body = client.post("/api/upstreams/mm/refresh").json()
    assert body["curated"] is False
    assert "conversion failed" in body["warning"] and "OSError" in body["warning"]
    assert "disk on fire" not in body["warning"]


def test_refresh_warns_to_check_url_and_key_when_unreachable(client: TestClient) -> None:
    _fresh(client)
    _add(client)
    _down(client)
    body = client.post("/api/upstreams/mm/refresh").json()
    assert body["stale"] is True
    assert "check the url and key" in body["warning"]


def test_refresh_write_failure_blames_the_config_file_not_the_provider(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hal0.config.loader as loader

    _fresh(client)  # the provider answers fine
    _add(client)

    def boom(*a: Any, **k: Any) -> None:
        raise OSError("/secret/path disk full")

    monkeypatch.setattr(loader, "save_upstreams_config", boom)
    body = client.post("/api/upstreams/mm/refresh").json()
    assert body["curated"] is False
    assert "could not be written" in body["warning"] and "hal0 log" in body["warning"]
    assert "check the url and key" not in body["warning"]
    assert "/secret/path" not in body["warning"] and URL not in body["warning"]


@pytest.mark.parametrize(
    ("payload", "needle"),
    [
        ({"data": []}, "check the url and key"),
        ({"data": [{"id": " "}]}, "no usable ids"),
    ],
)
def test_refresh_warning_matches_what_the_provider_returned(
    client: TestClient, payload: Any, needle: str
) -> None:
    _fresh(client, payload)
    _add(client)
    assert needle in client.post("/api/upstreams/mm/refresh").json()["warning"]


def test_refresh_has_no_warning_normally(client: TestClient) -> None:
    _fresh(client)
    _create(client, models=[])
    assert client.post("/api/upstreams/mm/refresh").json()["warning"] is None
    assert _models(client)["warning"] is None


def test_overlapping_patches_of_a_discovered_only_model_keep_both(
    client: TestClient,
) -> None:
    """A write that lands during the discovery await must not be lost by the next."""
    _fresh(client)
    _create(client, models=[])
    real = client.app.state.upstreams.fetch_model_payload
    reg = client.app.state.upstreams
    fired = {"done": False}

    async def racing(name: str) -> Any:
        if not fired["done"]:
            fired["done"] = True
            # another write creates the row while the first PATCH awaits discovery
            reg.apply_persistent_patch(
                "mm", {"models": [UpstreamModel(id="gpt-5-mini", enabled=False, context=1234)]}
            )
        return await real(name)

    reg.fetch_model_payload = racing  # type: ignore[method-assign]
    client.app.state.upstream_discovery.drop("mm")
    res = client.patch("/api/upstreams/mm/models/gpt-5-mini", json={"enabled": True})
    assert res.status_code == 200, res.text
    row = _by_id(_models(client))["gpt-5-mini"]
    assert row["enabled"] is True and row["context"] == 1234


def test_presets_share_the_schema_vocabularies() -> None:
    from hal0.config import schema
    from hal0.upstreams import presets

    assert presets._MODEL_TYPES == schema._VALID_MODEL_TYPES
    assert presets._PRICE_UNITS == schema.VALID_PRICE_UNITS
