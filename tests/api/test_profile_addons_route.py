"""GET /api/profile-addons — the dashboard's import catalog over HTTP.

The outbound fetch is switched off or replaced in every test; none opens a
socket.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.profiles import addon_catalog
from hal0.profiles.addons import ADDON_PROFILES, build_envelopes, build_index


@pytest.fixture
def client(tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv(addon_catalog.INDEX_URL_ENV, "off")
    addon_catalog.clear_cache()
    app: FastAPI = create_app()
    with TestClient(app) as c:
        yield c
    addon_catalog.clear_cache()


def test_catalog_lists_the_bundled_addons_when_the_fetch_is_off(client: TestClient) -> None:
    r = client.get("/api/profile-addons")

    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "bundled"
    assert body["url"] is None
    assert [row["name"] for row in body["addons"]] == sorted(ADDON_PROFILES)
    assert all(row["blocked"] is None for row in body["addons"])


def test_catalog_row_imports_through_the_import_route(client: TestClient) -> None:
    row = next(
        r for r in client.get("/api/profile-addons").json()["addons"] if r["name"] == "dense"
    )
    assert row["installed"] is False

    r = client.post("/api/profiles/import", json={"envelope": row["envelope"], "name": "dense"})
    assert r.status_code == 201

    after = next(
        r for r in client.get("/api/profile-addons").json()["addons"] if r["name"] == "dense"
    )
    assert after["installed"] is True


def test_catalog_serves_the_published_index_and_refreshes_on_request(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = "https://catalog.example/profiles/index.json"
    envelopes = build_envelopes()
    index = build_index(envelopes)
    index["addons"] = [r for r in index["addons"] if r["name"] == "moe"]
    calls: list[str] = []

    async def fetch(target: str) -> bytes:
        calls.append(target)
        if target == url:
            return json.dumps(index).encode()
        return json.dumps(envelopes["moe"]).encode()

    monkeypatch.setenv(addon_catalog.INDEX_URL_ENV, url)
    monkeypatch.setattr(addon_catalog, "_http_fetch", fetch)

    body = client.get("/api/profile-addons").json()
    assert body["source"] == "remote"
    assert [row["name"] for row in body["addons"]] == ["moe"]
    assert len(calls) == 2

    client.get("/api/profile-addons")
    assert len(calls) == 2
    # A refresh straight after a fetch is served from the copy just read.
    client.get("/api/profile-addons?refresh=true")
    assert len(calls) == 2
    monkeypatch.setattr(addon_catalog, "MIN_REFRESH_INTERVAL_S", 0.0)
    client.get("/api/profile-addons?refresh=true")
    assert len(calls) == 4


def test_a_profile_named_catalog_is_still_reachable(client: TestClient) -> None:
    """The catalog has its own prefix so it cannot shadow a profile name."""
    assert (
        client.post("/api/profiles", json={"name": "catalog", "flags": "-fa on"}).status_code == 201
    )
    assert client.get("/api/profiles/catalog").json()["name"] == "catalog"
