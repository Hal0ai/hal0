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
    body = resp.json()
    assert body["images"] == []
    # ``families`` always covers every hal0.runners.RUNNER_IMAGES key, even
    # with an empty catalogue — it's the launch-truth summary, not a view
    # over catalogued rows.
    assert body["families"]
    assert {f["family"] for f in body["families"]} >= {"rocmfpx", "cpu"}


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


# ── restart-affected-slots (#2096 page-side workaround, task 12) ────────────


def test_restart_affected_names_slots(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "hal0.api.routes.runner_images._slot_image_usage",
        lambda: {"brain": "ghcr.io/x/a:0826", "ops": "ghcr.io/x/a:0826", "flm": "other:1"},
    )
    restarted: list[str] = []

    async def _fake_restart(name: str, request) -> None:
        restarted.append(name)

    monkeypatch.setattr("hal0.api.routes.runner_images._restart_slot", _fake_restart)
    res = client.post("/api/runner-images/restart-affected", json={"ref": "ghcr.io/x/a:0826"})
    assert res.status_code == 202
    assert res.json()["restarted"] == ["brain", "ops"]
    assert restarted == ["brain", "ops"]


def test_restart_affected_rejects_bad_ref(client: TestClient) -> None:
    res = client.post("/api/runner-images/restart-affected", json={"ref": "bad ref"})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "runner_image.ref_invalid"


def test_restart_affected_empty_when_no_slot_matches(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "hal0.api.routes.runner_images._slot_image_usage",
        lambda: {"brain": "ghcr.io/x/a:0826"},
    )
    res = client.post("/api/runner-images/restart-affected", json={"ref": "ghcr.io/x/nomatch:1"})
    assert res.status_code == 202
    assert res.json()["restarted"] == []


def test_restart_affected_is_fail_soft_per_slot(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing slot restart is logged and skipped, not a 500 for the batch."""
    monkeypatch.setattr(
        "hal0.api.routes.runner_images._slot_image_usage",
        lambda: {"brain": "ghcr.io/x/a:0826", "ops": "ghcr.io/x/a:0826"},
    )

    async def _flaky_restart(name: str, request) -> None:
        if name == "brain":
            raise RuntimeError("boom")

    monkeypatch.setattr("hal0.api.routes.runner_images._restart_slot", _flaky_restart)
    res = client.post("/api/runner-images/restart-affected", json={"ref": "ghcr.io/x/a:0826"})
    assert res.status_code == 202
    assert res.json()["restarted"] == ["ops"]


def test_restart_affected_delegates_to_slot_manager_restart(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_restart_slot`` uses the exact same service call as
    ``POST /api/slots/{name}/restart`` — ``SlotManager.restart``."""
    monkeypatch.setattr(
        "hal0.api.routes.runner_images._slot_image_usage",
        lambda: {"brain": "ghcr.io/x/a:0826"},
    )
    calls: list[str] = []

    async def _fake_restart(name: str):
        calls.append(name)
        return None

    monkeypatch.setattr(client.app.state.slot_manager, "restart", _fake_restart)
    res = client.post("/api/runner-images/restart-affected", json={"ref": "ghcr.io/x/a:0826"})
    assert res.status_code == 202
    assert res.json()["restarted"] == ["brain"]
    assert calls == ["brain"]


def test_restart_affected_clears_snapshot_cache_even_on_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cache invalidation must run even when ``sm.restart`` raises — mirrors
    slots.py's ``@_invalidates_snapshot`` decorator's try/finally semantics
    (fix round 1: the cache clear used to sit after the ``record_action``
    block, so a raising restart left ``/api/slots`` serving a stale
    pre-restart snapshot for the rest of its TTL)."""
    monkeypatch.setattr(
        "hal0.api.routes.runner_images._slot_image_usage",
        lambda: {"brain": "ghcr.io/x/a:0826"},
    )

    async def _boom(name: str):
        raise RuntimeError("boom")

    monkeypatch.setattr(client.app.state.slot_manager, "restart", _boom)
    client.app.state._slots_snapshot_cache = {"stale": "snapshot"}

    res = client.post("/api/runner-images/restart-affected", json={"ref": "ghcr.io/x/a:0826"})
    assert res.status_code == 202
    assert res.json()["restarted"] == []  # the raising restart is fail-soft-skipped
    assert client.app.state._slots_snapshot_cache is None


def test_restart_affected_records_after_state_on_success(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Batch-restart audit rows match per-slot ones: ``rec.after`` is set from
    ``_state_value(snap)`` exactly as slots.py's own ``restart_slot`` route
    does (fix round 1 audit-parity finding)."""
    from types import SimpleNamespace

    monkeypatch.setattr(
        "hal0.api.routes.runner_images._slot_image_usage",
        lambda: {"brain": "ghcr.io/x/a:0826"},
    )

    async def _fake_restart(name: str):
        return SimpleNamespace(state=SimpleNamespace(value="running"))

    monkeypatch.setattr(client.app.state.slot_manager, "restart", _fake_restart)
    res = client.post("/api/runner-images/restart-affected", json={"ref": "ghcr.io/x/a:0826"})
    assert res.status_code == 202
    assert res.json()["restarted"] == ["brain"]

    activity = client.get(
        "/api/activity", params={"category": "slot", "action": "slot.restart"}
    ).json()
    rec = next(r for r in activity["records"] if r["target"] == "brain")
    assert rec["after"] == {"state": "running"}
    assert rec["outcome"] == "ok"


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
            local=None,
        )
        # "any tag": the family default's REPO matching row.image is enough.
        assert row["is_default"] == {"family": "rocmfpx", "source": "release"}

    def test_override_source_reported(self) -> None:
        from hal0.api.routes.runner_images import enrich_row

        row = enrich_row(
            _row("ghcr.io/hal0ai/hal0-other", "1"),
            defaults={"rocmfpx": ("ghcr.io/hal0ai/hal0-other:1", "override")},
            slot_usage={},
            local=None,
        )
        assert row["is_default"] == {"family": "rocmfpx", "source": "override"}

    def test_no_family_match_is_null(self) -> None:
        from hal0.api.routes.runner_images import enrich_row

        row = enrich_row(
            _row("ghcr.io/hal0ai/hal0-unrelated", "1"),
            defaults={"rocmfpx": ("ghcr.io/hal0ai/hal0-combined:0824", "release")},
            slot_usage={},
            local=None,
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
            local=None,
        )
        assert row["in_use_by"] == ["agent", "utility"]
        assert row["is_default"] is None


# ── per-tag pull (catalogue v2 follow-up to #2043/#2044) ────────────────────


class TestPerTagPull:
    """POST /{id}/pull?tag=… pulls that catalogued tag, not just the headline.

    #2043 scoped the UI per-tag Pull out because ``runner_pull_jobs.enqueue``
    resolved ``image:tag`` exclusively from the row's headline tag. The route
    now accepts an optional ``tag`` restricted to the row's ``available_tags``
    (headline always allowed); omitting it keeps today's headline behaviour.
    """

    IMAGE_ID = "hal0ai/hal0-combined"

    def _seed(self, client: TestClient) -> None:
        from hal0.registry.runner_image import RunnerImage

        client.app.state.runner_image_registry.upsert(
            RunnerImage(
                id=self.IMAGE_ID,
                image="ghcr.io/hal0ai/hal0-combined",
                tag="0824",
                available_tags=["0824", "0822"],
            )
        )

    def _stub_provider(self, pulled: list[str]) -> None:
        class _RecordingProvider:
            async def pull_image_stream(self, image: str):
                pulled.append(image)
                yield {"state": "completed", "layer": 1, "total_layers": 1}

        runner_pull_jobs.provider_factory = lambda: _RecordingProvider()

    def _wait_terminal(self, client: TestClient, params: dict[str, str] | None = None) -> dict:
        import time

        for _ in range(200):
            resp = client.get(f"/api/runner-images/{self.IMAGE_ID}/pull/status", params=params)
            if resp.status_code == 200 and resp.json()["state"] in (
                "completed",
                "failed",
                "cancelled",
            ):
                return resp.json()
            time.sleep(0.01)
        raise AssertionError("pull job never reached a terminal state")

    def test_explicit_tag_pulls_that_ref(self, client: TestClient) -> None:
        self._seed(client)
        pulled: list[str] = []
        self._stub_provider(pulled)

        resp = client.post(f"/api/runner-images/{self.IMAGE_ID}/pull", params={"tag": "0822"})
        assert resp.status_code == 202
        body = resp.json()
        assert body["tag"] == "0822"
        assert body["image_ref"] == "ghcr.io/hal0ai/hal0-combined:0822"
        assert body["id"]

        status = self._wait_terminal(client)
        assert status["state"] == "completed"
        assert status["tag"] == "0822"
        assert status["image_ref"] == "ghcr.io/hal0ai/hal0-combined:0822"
        assert pulled == ["ghcr.io/hal0ai/hal0-combined:0822"]

    def test_no_tag_keeps_headline_behaviour(self, client: TestClient) -> None:
        self._seed(client)
        pulled: list[str] = []
        self._stub_provider(pulled)

        resp = client.post(f"/api/runner-images/{self.IMAGE_ID}/pull")
        assert resp.status_code == 202
        body = resp.json()
        assert body["tag"] == "0824"
        assert body["image_ref"] == "ghcr.io/hal0ai/hal0-combined:0824"
        self._wait_terminal(client)
        assert pulled == ["ghcr.io/hal0ai/hal0-combined:0824"]

    def test_unknown_tag_404s_with_available_tags(self, client: TestClient) -> None:
        self._seed(client)
        resp = client.post(f"/api/runner-images/{self.IMAGE_ID}/pull", params={"tag": "9999"})
        assert resp.status_code == 404
        err = resp.json()["error"]
        assert err["code"] == "runner_image.tag_not_available"
        assert err["details"]["tag"] == "9999"
        assert err["details"]["available_tags"] == ["0824", "0822"]

    def test_headline_tag_allowed_even_when_probe_failed(self, client: TestClient) -> None:
        """available_tags may be [] on probe failure — the headline stays pullable."""
        from hal0.registry.runner_image import RunnerImage

        client.app.state.runner_image_registry.upsert(
            RunnerImage(
                id=self.IMAGE_ID,
                image="ghcr.io/hal0ai/hal0-combined",
                tag="0824",
                available_tags=[],
            )
        )
        pulled: list[str] = []
        self._stub_provider(pulled)
        resp = client.post(f"/api/runner-images/{self.IMAGE_ID}/pull", params={"tag": "0824"})
        assert resp.status_code == 202
        assert resp.json()["tag"] == "0824"

    def test_inflight_other_tag_conflicts_409(self, client: TestClient) -> None:
        from hal0.registry.runner_pull import make_job

        self._seed(client)
        job = make_job(self.IMAGE_ID, "ghcr.io/hal0ai/hal0-combined:0824", tag="0824")
        client.app.state.runner_image_pull_jobs[self.IMAGE_ID] = job

        resp = client.post(f"/api/runner-images/{self.IMAGE_ID}/pull", params={"tag": "0822"})
        assert resp.status_code == 409
        err = resp.json()["error"]
        assert err["code"] == "runner_image.pull_conflict"
        assert err["details"]["in_flight_tag"] == "0824"

    def test_inflight_same_tag_resumes(self, client: TestClient) -> None:
        from hal0.registry.runner_pull import make_job

        self._seed(client)
        job = make_job(self.IMAGE_ID, "ghcr.io/hal0ai/hal0-combined:0824", tag="0824")
        client.app.state.runner_image_pull_jobs[self.IMAGE_ID] = job

        resp = client.post(f"/api/runner-images/{self.IMAGE_ID}/pull", params={"tag": "0824"})
        assert resp.status_code == 202
        body = resp.json()
        assert body["resumed"] is True
        assert body["tag"] == "0824"
        assert body["id"] == job.job_id

    def test_status_for_previous_tag_survives_new_pull(self, client: TestClient) -> None:
        """A terminal tag-A job stays reachable via ?tag=A after tag B's pull
        claims the single in-memory slot — snapshots persist per (image, tag),
        so starting a new tag's pull can't orphan the old tag's result."""
        self._seed(client)
        pulled: list[str] = []
        self._stub_provider(pulled)

        first = client.post(f"/api/runner-images/{self.IMAGE_ID}/pull", params={"tag": "0822"})
        assert first.status_code == 202
        done = self._wait_terminal(client, params={"tag": "0822"})
        assert done["state"] == "completed"

        second = client.post(f"/api/runner-images/{self.IMAGE_ID}/pull", params={"tag": "0824"})
        assert second.status_code == 202

        old = client.get(f"/api/runner-images/{self.IMAGE_ID}/pull/status", params={"tag": "0822"})
        assert old.status_code == 200
        body = old.json()
        assert body["tag"] == "0822"
        assert body["state"] == "completed"
        assert body["image_ref"] == "ghcr.io/hal0ai/hal0-combined:0822"

    @pytest.mark.parametrize(
        "bad",
        [
            "../../etc/passwd",
            "a/b",
            "..",
            ".hidden",
            "-leading",
            "we@ird",
            "a" * 129,
        ],
    )
    def test_malformed_tag_refused_before_any_path_use(self, client: TestClient, bad: str) -> None:
        """Tags feed the snapshot filename (<id>@<tag>.json) — anything
        outside strict OCI tag syntax is a typed 400 on both the pull and
        status routes, before any filesystem path is built (CodeQL
        py/path-injection on #2048)."""
        self._seed(client)

        pull = client.post(f"/api/runner-images/{self.IMAGE_ID}/pull", params={"tag": bad})
        assert pull.status_code == 400
        assert pull.json()["error"]["code"] == "runner_image.tag_invalid"

        status = client.get(f"/api/runner-images/{self.IMAGE_ID}/pull/status", params={"tag": bad})
        assert status.status_code == 400
        assert status.json()["error"]["code"] == "runner_image.tag_invalid"

    def test_status_tag_filter(self, client: TestClient) -> None:
        self._seed(client)
        pulled: list[str] = []
        self._stub_provider(pulled)
        client.post(f"/api/runner-images/{self.IMAGE_ID}/pull", params={"tag": "0822"})
        status = self._wait_terminal(client, params={"tag": "0822"})
        assert status["tag"] == "0822"

        miss = client.get(f"/api/runner-images/{self.IMAGE_ID}/pull/status", params={"tag": "0824"})
        assert miss.status_code == 404
        assert miss.json()["error"]["code"] == "runner_image.pull_job_not_found"


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


# ── store-truth enrichment: store_state / per-tag downloaded / badges ───────
#
# runner-image-catalogue v3 (task 5): rows gain store_state ("present" /
# "missing" / "unknown"), a per-tag ``downloaded`` flag, ``store_context``,
# and validated/candidate/deprecated ``badges`` — sourced from
# ``hal0.providers.podman_introspect.images_digests()`` (monkeypatched at
# the route module's imported name, its patch point) and the
# ``hal0.config.schema`` image-ref sets.


class TestStoreStateEnrichment:
    DIGEST_A = "sha256:" + "a" * 64
    DIGEST_B = "sha256:" + "b" * 64

    def _seed(self, client: TestClient, **overrides):
        from hal0.registry.runner_image import RunnerImage, RunnerImageTag

        image = RunnerImage(
            id="x",
            image="ghcr.io/x/a",
            tag="0826",
            available_tags=["0826", "0824"],
            **overrides,
        )
        store = client.app.state.runner_image_registry
        store.upsert(image)
        store.set_tags(
            "x",
            [
                RunnerImageTag(tag="0826", digest=self.DIGEST_A),
                RunnerImageTag(tag="0824", digest=self.DIGEST_B),
            ],
        )
        return image

    def test_store_state_present_by_digest(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from hal0.providers import podman_introspect as pi

        self._seed(client)
        monkeypatch.setattr(
            "hal0.api.routes.runner_images.images_digests",
            lambda: pi.LocalImagesDigests(
                refs={"docker.io/y/alias:zz": self.DIGEST_A}, context="rootful"
            ),
        )

        rows = client.get("/api/runner-images").json()["images"]
        row = next(r for r in rows if r["id"] == "x")
        assert row["store_state"] == "present"  # digest match, name irrelevant
        assert row["store_context"] == "rootful"
        assert row["downloaded"] is True
        assert row["tags"][0]["downloaded"] is True
        assert row["tags"][1]["downloaded"] is False

    def test_store_state_missing_when_absent_from_store(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from hal0.providers import podman_introspect as pi

        self._seed(client)
        monkeypatch.setattr(
            "hal0.api.routes.runner_images.images_digests",
            lambda: pi.LocalImagesDigests(refs={}, context="rootful"),
        )

        rows = client.get("/api/runner-images").json()["images"]
        row = next(r for r in rows if r["id"] == "x")
        assert row["store_state"] == "missing"
        assert row["downloaded"] is False
        assert row["tags"][0]["downloaded"] is False
        assert row["tags"][1]["downloaded"] is False

    def test_store_state_unknown_falls_back_to_marker(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._seed(client, local_path="/var/lib/hal0/images/x")
        monkeypatch.setattr("hal0.api.routes.runner_images.images_digests", lambda: None)

        rows = client.get("/api/runner-images").json()["images"]
        row = next(r for r in rows if r["id"] == "x")  # x has local_path set
        assert row["store_state"] == "unknown"
        assert row["store_context"] is None
        assert row["downloaded"] is True  # marker honoured only here
        assert row["tags"][0]["downloaded"] is None
        assert row["tags"][1]["downloaded"] is None

    def test_downloaded_route_filters_by_store_truth(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from hal0.providers import podman_introspect as pi

        self._seed(client)
        monkeypatch.setattr(
            "hal0.api.routes.runner_images.images_digests",
            lambda: pi.LocalImagesDigests(refs={"ghcr.io/x/a:0826": None}, context="rootful"),
        )

        rows = client.get("/api/runner-images/downloaded").json()["images"]
        assert [r["id"] for r in rows] == ["x"]
        assert rows[0]["store_state"] == "present"

    def test_validated_badge(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        from hal0.registry.runner_image import RunnerImage, RunnerImageTag

        monkeypatch.setattr("hal0.api.routes.runner_images.images_digests", lambda: None)
        store = client.app.state.runner_image_registry
        store.upsert(RunnerImage(id="combined", image="ghcr.io/hal0ai/hal0-combined", tag="0826"))
        store.set_tags("combined", [RunnerImageTag(tag="0826")])

        rows = client.get("/api/runner-images").json()["images"]
        row = next(r for r in rows if r["id"] == "combined")
        assert row["badges"]["0826"] == "validated"

    def test_deprecated_badge(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        from hal0.registry.runner_image import RunnerImage, RunnerImageTag

        monkeypatch.setattr("hal0.api.routes.runner_images.images_digests", lambda: None)
        store = client.app.state.runner_image_registry
        store.upsert(
            RunnerImage(id="stale-combined", image="ghcr.io/hal0ai/hal0-combined", tag="0824")
        )
        store.set_tags("stale-combined", [RunnerImageTag(tag="0824")])

        rows = client.get("/api/runner-images").json()["images"]
        row = next(r for r in rows if r["id"] == "stale-combined")
        assert row["badges"]["0824"] == "deprecated"

    def test_badges_missing_when_no_match(self, client: TestClient) -> None:
        rows_before = self._seed(client)
        del rows_before  # seed just for a row with no badge-set membership
        rows = client.get("/api/runner-images").json()["images"]
        row = next(r for r in rows if r["id"] == "x")
        assert row["badges"] == {}

    def test_promptforge_import_error_degrades_gracefully(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """DEFAULT_PROMPTFORGE_IMAGE now exists (landed with #2129/#1946),
        but ``_tag_badges``'s fail-soft ImportError branch is still real
        production code (guards against a future rename/removal of the
        constant) and must stay exercised: simulate the absence via
        monkeypatch rather than relying on the branch predating #2129."""
        import hal0.config.schema as schema_mod

        monkeypatch.delattr(schema_mod, "DEFAULT_PROMPTFORGE_IMAGE", raising=False)
        self._seed(client)
        monkeypatch.setattr("hal0.api.routes.runner_images.images_digests", lambda: None)

        resp = client.get("/api/runner-images")
        assert resp.status_code == 200

    def test_promptforge_candidate_badge_when_catalogued(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With DEFAULT_PROMPTFORGE_IMAGE now a real constant (#2129), a
        catalogued row matching its ref gets the "candidate" badge — the
        happy path the ImportError branch above used to make unreachable."""
        from hal0.config.schema import DEFAULT_PROMPTFORGE_IMAGE
        from hal0.registry.runner_image import RunnerImage, RunnerImageTag

        image, _, tag = DEFAULT_PROMPTFORGE_IMAGE.rpartition(":")
        monkeypatch.setattr("hal0.api.routes.runner_images.images_digests", lambda: None)
        store = client.app.state.runner_image_registry
        store.upsert(RunnerImage(id="promptforge", image=image, tag=tag))
        store.set_tags("promptforge", [RunnerImageTag(tag=tag)])

        rows = client.get("/api/runner-images").json()["images"]
        row = next(r for r in rows if r["id"] == "promptforge")
        assert row["badges"][tag] == "candidate"

    def test_get_runner_image_detail_carries_store_state(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from hal0.providers import podman_introspect as pi

        self._seed(client)
        monkeypatch.setattr(
            "hal0.api.routes.runner_images.images_digests",
            lambda: pi.LocalImagesDigests(refs={}, context="rootless"),
        )

        detail = client.get("/api/runner-images/x").json()
        assert detail["store_state"] == "missing"
        assert detail["store_context"] == "rootless"

    def test_sync_route_rows_carry_store_state(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("hal0.api.routes.runner_images.images_digests", lambda: None)
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
        assert body["images"][0]["store_state"] == "unknown"
        assert "families" in body


# ── launch-truth families payload (runner-image-catalogue v3, task 9) ───────
#
# GET /api/runner-images gains a top-level ``families`` list: per
# hal0.runners.RUNNER_IMAGES key, the effective ref, its source tier
# (override → env → manifest → release), store state, the newest
# release-shaped tag catalogued for that repo, and an update marker.


class TestFamiliesPayload:
    DIGEST_A = "sha256:" + "a" * 64  # tag 0826 — newest
    DIGEST_B = "sha256:" + "b" * 64  # tag 0824 — effective (override)

    def _seed_repo_x(self, client: TestClient) -> None:
        from hal0.registry.runner_image import RunnerImage, RunnerImageTag

        store = client.app.state.runner_image_registry
        store.upsert(
            RunnerImage(id="x", image="ghcr.io/x/a", tag="0826", available_tags=["0826", "0824"])
        )
        store.set_tags(
            "x",
            [
                RunnerImageTag(tag="0826", digest=self.DIGEST_A),
                RunnerImageTag(tag="0824", digest=self.DIGEST_B),
            ],
        )

    def test_families_payload(
        self, isolated_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from hal0.providers import podman_introspect as pi

        self._seed_repo_x(isolated_client)
        r = isolated_client.put(
            "/api/settings",
            json={"slots": {"default_images": {"rocmfpx": "ghcr.io/x/a:0824"}}},
        )
        assert r.status_code == 200, r.text
        monkeypatch.setattr(
            "hal0.api.routes.runner_images.images_digests",
            lambda: pi.LocalImagesDigests(
                refs={"ghcr.io/x/a:0824": self.DIGEST_B}, context="rootful"
            ),
        )

        body = isolated_client.get("/api/runner-images").json()
        fam = next(f for f in body["families"] if f["family"] == "rocmfpx")
        assert fam["effective_ref"] == "ghcr.io/x/a:0824"
        assert fam["source"] == "override"
        assert fam["store_state"] == "present"
        assert fam["newest_release"] == {"tag": "0826", "digest": self.DIGEST_A}
        assert fam["update_available"] is True

    def test_families_env_source(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAL0_TOOLBOX_IMAGE_ROCMFPX", "ghcr.io/x/a:custom")

        body = client.get("/api/runner-images").json()
        fam = next(f for f in body["families"] if f["family"] == "rocmfpx")
        assert fam["source"] == "env"
        assert fam["effective_ref"].endswith(":custom")

    def test_families_release_source_no_row_no_update(self, client: TestClient) -> None:
        """No override/env, no manifest pin, and no catalogue row for the
        release ref: newest_release is null and update_available is False —
        never a crash on an unmatched repo."""
        body = client.get("/api/runner-images").json()
        fam = next(f for f in body["families"] if f["family"] == "rocmfpx")
        assert fam["source"] == "release"
        assert fam["newest_release"] is None
        assert fam["update_available"] is False
        assert fam["slots"] == []
        assert fam["pinned_slots"] == []

    def test_families_digest_pinned_ref_present_and_update_available(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A manifest-tier effective ref pinned by digest (``repo@sha256:…``
        — what ``manifest_image_ref`` returns with a real digest, and what a
        digest-form override/env value would also look like) must resolve
        store presence and ``update_available`` via digest match, not the
        tag-lookup path — ``ref.rpartition(":")`` would otherwise split
        inside the digest and produce hex garbage as a fake "tag" (review
        finding #1, fix round 1). ``flm`` is the family exercised: it has a
        real ``manifest_key`` (unlike rocmfpx/vulkanfpx/cuda/cpu, which are
        deliberately unwired — see the ``hal0.runners`` module docstring)."""
        from hal0.providers import podman_introspect as pi

        self._seed_repo_x(client)  # repo ghcr.io/x/a, tags 0826/DIGEST_A, 0824/DIGEST_B
        pinned_ref = f"ghcr.io/x/a@{self.DIGEST_B}"
        monkeypatch.setattr(
            "hal0.config.loader.manifest_image_ref",
            lambda key: pinned_ref if key == "flm" else None,
        )
        monkeypatch.setattr(
            "hal0.api.routes.runner_images.images_digests",
            lambda: pi.LocalImagesDigests(
                refs={"docker.io/y/alias:zz": self.DIGEST_B}, context="rootful"
            ),
        )

        body = client.get("/api/runner-images").json()
        fam = next(f for f in body["families"] if f["family"] == "flm")
        assert fam["source"] == "manifest"
        assert fam["effective_ref"] == pinned_ref
        assert fam["store_state"] == "present"  # digest match, name irrelevant
        assert fam["newest_release"] == {"tag": "0826", "digest": self.DIGEST_A}
        assert fam["update_available"] is True

    def test_families_store_state_unknown_when_store_unreadable(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._seed_repo_x(client)
        monkeypatch.setenv("HAL0_TOOLBOX_IMAGE_ROCMFPX", "ghcr.io/x/a:0824")
        monkeypatch.setattr("hal0.api.routes.runner_images.images_digests", lambda: None)

        body = client.get("/api/runner-images").json()
        fam = next(f for f in body["families"] if f["family"] == "rocmfpx")
        assert fam["store_state"] == "unknown"

    def test_families_no_update_when_digests_match(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Effective ref already pinned to the newest catalogued digest —
        update_available is False."""
        from hal0.providers import podman_introspect as pi

        self._seed_repo_x(client)
        monkeypatch.setenv("HAL0_TOOLBOX_IMAGE_ROCMFPX", "ghcr.io/x/a:0826")
        monkeypatch.setattr(
            "hal0.api.routes.runner_images.images_digests",
            lambda: pi.LocalImagesDigests(
                refs={"ghcr.io/x/a:0826": self.DIGEST_A}, context="rootful"
            ),
        )

        body = client.get("/api/runner-images").json()
        fam = next(f for f in body["families"] if f["family"] == "rocmfpx")
        assert fam["newest_release"] == {"tag": "0826", "digest": self.DIGEST_A}
        assert fam["update_available"] is False

    def test_families_slots_and_pinned_slots(
        self, isolated_client: TestClient, tmp_hal0_home: str
    ) -> None:
        """A slot resolving to the exact effective ref lands in ``slots``; a
        slot pinned to a different tag of the same repo lands in
        ``pinned_slots`` instead."""
        from pathlib import Path

        self._seed_repo_x(isolated_client)
        r = isolated_client.put(
            "/api/settings",
            json={"slots": {"default_images": {"rocmfpx": "ghcr.io/x/a:0824"}}},
        )
        assert r.status_code == 200, r.text

        slots_dir = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
        slots_dir.mkdir(parents=True, exist_ok=True)
        (slots_dir / "agent.toml").write_text(
            'name = "agent"\nport = 8081\nimage_pin = "ghcr.io/x/a:0824"\n',
            encoding="utf-8",
        )
        (slots_dir / "utility.toml").write_text(
            'name = "utility"\nport = 8082\nimage_pin = "ghcr.io/x/a:0826"\n',
            encoding="utf-8",
        )

        body = isolated_client.get("/api/runner-images").json()
        fam = next(f for f in body["families"] if f["family"] == "rocmfpx")
        assert fam["slots"] == ["agent"]
        assert fam["pinned_slots"] == ["utility"]

    def test_families_payload_never_500s_on_bad_default_images(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ``load_hal0_config`` blow-up must degrade families to release
        defaults, not 500 the whole route (fail-soft contract)."""

        def _boom():
            raise RuntimeError("config corrupt")

        monkeypatch.setattr("hal0.config.loader.load_hal0_config", _boom)

        resp = client.get("/api/runner-images")
        assert resp.status_code == 200
        assert resp.json()["families"]


# ── canonical family keys (runner-image-catalogue v3, task 11) ──────────────
#
# ``vulkanfpx`` shares DEFAULT_ROCMFPX_IMAGE with ``rocmfpx`` — one lever
# (rocmfpx) governs the shared image lineage; vulkanfpx never emits its own
# families row or defaults entry, and an override written under the alias
# key folds into the canonical family.


class TestCanonicalFamilyFold:
    def test_vulkanfpx_absent_from_families(self, client: TestClient) -> None:
        body = client.get("/api/runner-images").json()
        fams = [f["family"] for f in body["families"]]
        assert "vulkanfpx" not in fams
        assert "rocmfpx" in fams

    def test_alias_override_key_applies_to_rocmfpx_family_row(
        self, isolated_client: TestClient
    ) -> None:
        """An override written under the alias key ``vulkanfpx`` applies to
        the canonical ``rocmfpx`` family row — both keys never produce two
        strip rows for the same image lineage."""
        r = isolated_client.put(
            "/api/settings",
            json={"slots": {"default_images": {"vulkanfpx": "ghcr.io/x/a:0999"}}},
        )
        assert r.status_code == 200, r.text

        body = isolated_client.get("/api/runner-images").json()
        fams = [f["family"] for f in body["families"]]
        assert "vulkanfpx" not in fams
        rocm = next(f for f in body["families"] if f["family"] == "rocmfpx")
        assert rocm["effective_ref"] == "ghcr.io/x/a:0999"
        assert rocm["source"] == "override"

    def test_canonical_key_wins_when_both_present(self, isolated_client: TestClient) -> None:
        """When both the exact canonical key and the alias key are set, the
        canonical key wins (``folded.setdefault`` semantics)."""
        r = isolated_client.put(
            "/api/settings",
            json={
                "slots": {
                    "default_images": {
                        "rocmfpx": "ghcr.io/x/a:canonical",
                        "vulkanfpx": "ghcr.io/x/a:alias",
                    }
                }
            },
        )
        assert r.status_code == 200, r.text

        body = isolated_client.get("/api/runner-images").json()
        rocm = next(f for f in body["families"] if f["family"] == "rocmfpx")
        assert rocm["effective_ref"] == "ghcr.io/x/a:canonical"

    def test_canonical_key_wins_regardless_of_map_iteration_order(
        self, isolated_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """fix round 1: the canonical key must win even when the alias key
        iterates FIRST (realistic hand-edited toml order) — a single
        ``setdefault`` pass over ``overrides.items()`` would let whichever
        key happened to iterate first win instead of the canonical one."""
        from types import SimpleNamespace

        monkeypatch.setattr(
            "hal0.config.loader.load_hal0_config",
            lambda: SimpleNamespace(
                slots=SimpleNamespace(
                    default_images={
                        "vulkanfpx": "ghcr.io/x/a:alias",
                        "rocmfpx": "ghcr.io/x/a:canonical",
                    }
                )
            ),
        )

        body = isolated_client.get("/api/runner-images").json()
        rocm = next(f for f in body["families"] if f["family"] == "rocmfpx")
        assert rocm["effective_ref"] == "ghcr.io/x/a:canonical"
