"""Route test for /api/models/catalogue.

Asserts the shape of the pullable curated catalogue (CuratedModel), with
counts that add up. The haloai upstream seed (``upstream`` key) was removed.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_catalogue_returns_split_shape(client: TestClient) -> None:
    resp = client.get("/api/models/catalogue")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert set(body.keys()) == {"pullable", "counts"}
    assert isinstance(body["pullable"], list)

    counts = body["counts"]
    assert set(counts.keys()) == {"pullable", "total"}
    assert counts["pullable"] == len(body["pullable"])
    assert counts["total"] == counts["pullable"]
    assert counts["total"] > 0


def test_catalogue_pullable_has_hf_coordinates(client: TestClient) -> None:
    body = client.get("/api/models/catalogue").json()
    assert body["pullable"], "expected at least one pullable curated entry"
    sample = body["pullable"][0]
    # CuratedModel guarantees these fields are present and non-empty.
    assert sample["hf_repo"]
    assert sample["hf_file"]
    assert sample["id"]
    assert sample["display_name"]
