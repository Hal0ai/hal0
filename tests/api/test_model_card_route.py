"""GET /api/models/{id}/card — model card fetch + cache route."""

from __future__ import annotations

import pytest

import hal0.registry.cards as cards

CARD = "# Test model\n\nHello.\n"


@pytest.fixture
def _fake_hf(monkeypatch):
    calls: list[str] = []

    async def fake_fetch(hf_repo, *, hf_token=None, client=None):
        calls.append(hf_repo)
        return CARD

    monkeypatch.setattr(cards, "fetch_card", fake_fetch)
    return calls


def _register(client, mid: str, *, hf: bool = True):
    body = {"id": mid, "name": mid, "path": f"/tmp/{mid}/model.gguf"}
    if hf:
        body["hf_repo"] = f"org/{mid}"
        body["hf_filename"] = "model.gguf"
    assert client.post("/api/models", json=body).status_code == 201


def test_card_fetches_then_serves_from_cache(isolated_client, _fake_hf):
    _register(isolated_client, "m1")
    res = isolated_client.get("/api/models/m1/card")
    assert res.status_code == 200
    body = res.json()
    assert body["markdown"] == CARD
    assert body["cached"] is False
    assert body["hf_repo"] == "org/m1"

    res = isolated_client.get("/api/models/m1/card")
    assert res.json()["cached"] is True
    assert _fake_hf == ["org/m1"]

    res = isolated_client.get("/api/models/m1/card?refresh=true")
    assert res.json()["cached"] is False
    assert _fake_hf == ["org/m1", "org/m1"]


def test_card_unknown_model_404(isolated_client):
    res = isolated_client.get("/api/models/nope/card")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "model.not_found"


def test_card_without_hf_repo_404(isolated_client, _fake_hf):
    _register(isolated_client, "scanned", hf=False)
    res = isolated_client.get("/api/models/scanned/card")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "model.card_no_source"
    assert _fake_hf == []


def test_delete_model_drops_cached_card(isolated_client, _fake_hf):
    _register(isolated_client, "m1")
    isolated_client.get("/api/models/m1/card")
    assert cards.card_path("m1").exists()
    assert isolated_client.delete("/api/models/m1").status_code == 200
    assert not cards.card_path("m1").exists()
