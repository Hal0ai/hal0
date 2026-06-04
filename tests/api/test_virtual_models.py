"""GET /v1/models advertises hal0/* virtual names with context_length.

Verifies that the list_models handler appends rows for each canonical
virtual name (hal0/primary, hal0/npu, hal0/utility) when a slot is
loaded, and that each row carries a context_length field (required by
Hermes' custom provider for correct context-window sizing).
"""

from __future__ import annotations

from hal0.normalize import resolver as R


def _views():
    return [
        R.SlotView(
            name="primary", role=None, device="gpu-vulkan", model_id="big", context_length=65536
        )
    ]


def test_virtual_names_present_with_context_length(client, monkeypatch):
    async def fake_views(request):
        return _views()

    monkeypatch.setattr("hal0.api.routes.v1._normalize_slot_views", fake_views)
    monkeypatch.setattr("hal0.api.routes.v1._normalize_loaded_models", lambda request: {"big"})

    data = client.get("/v1/models").json()["data"]
    by_id = {row["id"]: row for row in data}
    assert "hal0/primary" in by_id
    row = by_id["hal0/primary"]
    assert row["context_length"] == 65536  # FIRST key in Hermes' context precedence
    assert row["_hal0"]["virtual"] is True
    assert row["_hal0"]["resolves_to"] == "big"
    assert row["_hal0"]["device"] == "gpu-vulkan"


def test_virtual_rows_do_not_duplicate(client, monkeypatch):
    async def fake_views(request):
        return _views()

    monkeypatch.setattr("hal0.api.routes.v1._normalize_slot_views", fake_views)
    monkeypatch.setattr("hal0.api.routes.v1._normalize_loaded_models", lambda request: {"big"})

    ids = [r["id"] for r in client.get("/v1/models").json()["data"]]
    assert ids.count("hal0/primary") == 1


def test_all_canonical_virtual_names_advertised_with_fallback(client, monkeypatch):
    async def fake_views(request):
        return _views()

    monkeypatch.setattr("hal0.api.routes.v1._normalize_slot_views", fake_views)
    monkeypatch.setattr("hal0.api.routes.v1._normalize_loaded_models", lambda request: {"big"})

    payload = client.get("/v1/models").json()
    assert payload["object"] == "list"
    by_id = {r["id"]: r for r in payload["data"]}
    # all three appear; npu/utility fall back to the primary's model on a primary-only box
    assert {"hal0/primary", "hal0/npu", "hal0/utility"}.issubset(by_id)
    assert by_id["hal0/npu"]["_hal0"]["resolves_to"] == "big"
    assert by_id["hal0/utility"]["_hal0"]["resolves_to"] == "big"
    # every virtual row carries a context_length (mandatory for Hermes)
    for vid in ("hal0/primary", "hal0/npu", "hal0/utility"):
        assert by_id[vid]["context_length"] == 65536
