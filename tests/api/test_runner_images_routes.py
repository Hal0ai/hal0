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


def test_sync_route_populates_catalogue(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    store.upsert(
        RunnerImage(
            id="hal0ai/hal0-toolbox-cpu", image="ghcr.io/hal0ai/hal0-toolbox-cpu", tag="latest"
        )
    )

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


# ── row enrichment: is_default / in_use_by (runner-image-catalogue v2) ──────


def _row(image: str, tag: str, image_id: str = "row"):
    from hal0.registry.runner_image import RunnerImage

    return RunnerImage(id=image_id, image=image, tag=tag)


class TestEnrichRow:
    """Pure-function contract for the catalogue v2 row enrichment."""

    def test_release_default_match_any_tag(self) -> None:
        from hal0.api.routes.runner_images import enrich_row

        row = enrich_row(
            _row("ghcr.io/hal0ai/hal0-combined", "0777"),
            defaults={"rocmfpx": ("ghcr.io/hal0ai/hal0-combined:0824", "release")},
            slot_usage={},
        )
        # "any tag": the family default's REPO matching row.image is enough.
        assert row["is_default"] == {"family": "rocmfpx", "source": "release"}

    def test_override_source_reported(self) -> None:
        from hal0.api.routes.runner_images import enrich_row

        row = enrich_row(
            _row("ghcr.io/hal0ai/hal0-other", "1"),
            defaults={"rocmfpx": ("ghcr.io/hal0ai/hal0-other:1", "override")},
            slot_usage={},
        )
        assert row["is_default"] == {"family": "rocmfpx", "source": "override"}

    def test_no_family_match_is_null(self) -> None:
        from hal0.api.routes.runner_images import enrich_row

        row = enrich_row(
            _row("ghcr.io/hal0ai/hal0-unrelated", "1"),
            defaults={"rocmfpx": ("ghcr.io/hal0ai/hal0-combined:0824", "release")},
            slot_usage={},
        )
        assert row["is_default"] is None

    def test_in_use_by_matches_exact_image_tag(self) -> None:
        from hal0.api.routes.runner_images import enrich_row

        row = enrich_row(
            _row("ghcr.io/hal0ai/hal0-combined", "0824"),
            defaults={},
            slot_usage={
                "agent": "ghcr.io/hal0ai/hal0-combined:0824",
                "utility": "ghcr.io/hal0ai/hal0-combined:0824",
                "npu": "ghcr.io/hal0ai/hal0-toolbox-flm:0.9.44",
                "old": "ghcr.io/hal0ai/hal0-combined:0822",  # other tag: no match
            },
        )
        assert row["in_use_by"] == ["agent", "utility"]
        assert row["is_default"] is None


@pytest.fixture
def isolated_client(tmp_hal0_home: str):
    """TestClient built AFTER HAL0_HOME isolation (settings writes land in tmp).

    Same idiom as tests/api/test_settings_routes.py — the shared ``client``
    fixture builds the app before the per-test HAL0_HOME monkeypatch.
    """
    from fastapi import FastAPI

    from hal0.api import create_app

    app: FastAPI = create_app()
    with TestClient(app) as c:
        yield c


class TestRunnerImageRouteEnrichment:
    """GET /api/runner-images rows carry the frozen catalogue-v2 contract."""

    def _seed_combined(self, client: TestClient):
        from hal0.registry.runner_image import RunnerImage

        return client.app.state.runner_image_registry.upsert(
            RunnerImage(
                id="rocmfpx-combined",
                image="ghcr.io/hal0ai/hal0-combined",
                tag="0824",
                available_tags=["0824", "0822"],
            )
        )

    def test_release_default_and_contract_fields(self, isolated_client: TestClient) -> None:
        """The baked rocmfpx default is hal0-combined:0824 (#2041), so the
        combined row reports source=release with no override configured."""
        self._seed_combined(isolated_client)
        rows = isolated_client.get("/api/runner-images").json()["images"]
        row = next(r for r in rows if r["id"] == "rocmfpx-combined")
        assert row["available_tags"] == ["0824", "0822"]
        assert row["is_default"] == {"family": "rocmfpx", "source": "release"}
        assert row["in_use_by"] == []

    def test_override_set_and_null_clear_via_settings_put(
        self, isolated_client: TestClient
    ) -> None:
        """[slots].default_images written/cleared through PUT /api/settings:
        set → source=override; explicit null value → override removed."""
        from hal0.registry.runner_image import RunnerImage

        isolated_client.app.state.runner_image_registry.upsert(
            RunnerImage(id="other", image="ghcr.io/hal0ai/hal0-other", tag="1")
        )

        r = isolated_client.put(
            "/api/settings",
            json={"slots": {"default_images": {"cpu": "ghcr.io/hal0ai/hal0-other:1"}}},
        )
        assert r.status_code == 200, r.text
        assert r.json()["slots"]["default_images"] == {"cpu": "ghcr.io/hal0ai/hal0-other:1"}

        rows = isolated_client.get("/api/runner-images").json()["images"]
        row = next(r for r in rows if r["id"] == "other")
        assert row["is_default"] == {"family": "cpu", "source": "override"}

        # CLEAR: explicit null value removes the key (deep-merge has no
        # delete idiom; the SlotsConfig validator drops null-valued keys).
        r = isolated_client.put("/api/settings", json={"slots": {"default_images": {"cpu": None}}})
        assert r.status_code == 200, r.text
        assert r.json()["slots"]["default_images"] == {}

        rows = isolated_client.get("/api/runner-images").json()["images"]
        row = next(r for r in rows if r["id"] == "other")
        assert row["is_default"] is None

    def test_in_use_by_lists_slots_resolving_to_the_row(
        self, isolated_client: TestClient, tmp_hal0_home: str
    ) -> None:
        """A slot whose resolved config references image:tag shows up in the
        row's in_use_by (resolved-config path — no rendered units in tests)."""
        from pathlib import Path

        self._seed_combined(isolated_client)
        slots_dir = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
        slots_dir.mkdir(parents=True, exist_ok=True)
        (slots_dir / "agent.toml").write_text(
            'name = "agent"\nport = 8081\nimage_pin = "ghcr.io/hal0ai/hal0-combined:0824"\n',
            encoding="utf-8",
        )

        rows = isolated_client.get("/api/runner-images").json()["images"]
        row = next(r for r in rows if r["id"] == "rocmfpx-combined")
        assert row["in_use_by"] == ["agent"]
