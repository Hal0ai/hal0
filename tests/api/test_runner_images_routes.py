"""Tests for the /api/runner-images surface.

Uses the shared ``client`` fixture (TestClient with lifespan executed, so
``app.state.runner_image_registry`` etc. exist) from tests/conftest.py.
GHCR/images.json network calls are stubbed via ``httpx.MockTransport``
(house pattern, tests/registry/test_pull.py); the download job's provider
is stubbed via the ``runner_pull_jobs.provider_factory`` monkeypatch seam.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from hal0.registry import runner_image_sync as sync_mod
from hal0.registry import runner_pull_jobs


class _FakeProvider:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events

    async def pull_image_stream(self, image: str):
        for e in self._events:
            yield e


@pytest.fixture(autouse=True)
def _reset_provider_factory():
    original = runner_pull_jobs.provider_factory
    yield
    runner_pull_jobs.provider_factory = original


def test_list_runner_images_empty(client: TestClient) -> None:
    resp = client.get("/api/runner-images")
    assert resp.status_code == 200
    assert resp.json() == {"images": []}


def test_get_unknown_runner_image_404s(client: TestClient) -> None:
    resp = client.get("/api/runner-images/hal0ai/hal0-toolbox-cpu")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "runner_image.not_found"


def test_sync_route_populates_catalogue(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    images_json = {
        "schema": "hal0.runner-images.v1",
        "images": [
            {
                "image": "ghcr.io/hal0ai/hal0-toolbox-cpu",
                "tag": "latest",
                "manifest_key": "toolbox-cpu",
                "ownership": "owned",
                "publish": "ci",
                "notes": "CPU-only toolbox image.",
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == sync_mod.IMAGES_JSON_URL:
            return httpx.Response(200, json=images_json)
        if url.startswith("https://ghcr.io/token"):
            return httpx.Response(200, json={"token": "tok"})
        if "/manifests/" in url:
            return httpx.Response(
                200,
                headers={"docker-content-digest": "sha256:abc", "content-length": "42"},
            )
        return httpx.Response(404)

    real_async_client = httpx.AsyncClient

    def _mock_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(sync_mod.httpx, "AsyncClient", _mock_client)

    resp = client.post("/api/runner-images/sync")
    assert resp.status_code == 202
    body = resp.json()
    assert body["images_json_ok"] is True
    assert len(body["images"]) == 1

    listed = client.get("/api/runner-images").json()["images"]
    assert listed[0]["id"] == "hal0ai/hal0-toolbox-cpu"
    assert listed[0]["digest"] == "sha256:abc"
    assert listed[0]["notes"] == "CPU-only toolbox image."

    detail = client.get("/api/runner-images/hal0ai/hal0-toolbox-cpu")
    assert detail.status_code == 200
    assert detail.json()["ownership"] == "owned"


def test_pull_unknown_image_404s(client: TestClient) -> None:
    resp = client.post("/api/runner-images/hal0ai/nope/pull")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "runner_image.not_catalogued"


def test_pull_status_before_starting_404s(client: TestClient) -> None:
    resp = client.get("/api/runner-images/hal0ai/hal0-toolbox-cpu/pull/status")
    assert resp.status_code == 404


def test_pull_lifecycle_start_status_complete(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = client.app.state.runner_image_registry
    from hal0.registry.runner_image import RunnerImage

    store.upsert(RunnerImage(id="hal0ai/hal0-toolbox-cpu", image="ghcr.io/hal0ai/hal0-toolbox-cpu", tag="latest"))

    runner_pull_jobs.provider_factory = lambda: _FakeProvider(
        [
            {"state": "pulling", "layer": 1, "total_layers": 1, "line": "Pulling fs layer"},
            {"state": "completed", "layer": 1, "total_layers": 1},
        ]
    )

    resp = client.post("/api/runner-images/hal0ai/hal0-toolbox-cpu/pull")
    assert resp.status_code == 202
    job_id = resp.json()["id"]
    assert job_id

    status = client.get("/api/runner-images/hal0ai/hal0-toolbox-cpu/pull/status")
    assert status.status_code == 200
    assert status.json()["state"] in ("queued", "running", "completed")

    pulls = client.get("/api/runner-images/pulls/list")
    assert pulls.status_code == 200
    assert any(j["image_id"] == "hal0ai/hal0-toolbox-cpu" for j in pulls.json())


def test_pull_cancel_unknown_job_404s(client: TestClient) -> None:
    resp = client.post("/api/runner-images/hal0ai/hal0-toolbox-cpu/pull/cancel")
    assert resp.status_code == 404


def test_downloaded_list_empty_by_default(client: TestClient) -> None:
    resp = client.get("/api/runner-images/downloaded")
    assert resp.status_code == 200
    assert resp.json() == {"images": []}
