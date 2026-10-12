"""Tests for the /api/runner-images surface.

Uses the shared ``client`` fixture (TestClient with lifespan executed, so
``app.state.runner_image_registry`` etc. exist) from tests/conftest.py.
GHCR/images.json network calls are stubbed via ``httpx.MockTransport``
(house pattern, tests/registry/test_pull.py); the download job's provider
is stubbed via the ``runner_pull_jobs.provider_factory`` monkeypatch seam.

Catalogue rows are keyed by runner key (``standard``, ``cpu``, …); the
pin table (``hal0.runners.pins.runner_pins``) is monkeypatched wherever a
test needs a published pin.
"""

from __future__ import annotations

from typing import Any, ClassVar

import httpx
import pytest
from fastapi.testclient import TestClient

from hal0.registry import runner_image_sync as sync_mod
from hal0.registry import runner_pull_jobs
from hal0.runners.pins import RunnerPin, load_runner_pins

STD_REPO = "ghcr.io/hal0ai/hal0-runner-standard"
CPU_REPO = "ghcr.io/hal0ai/hal0-runner-cpu"
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64


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


def _patch_pins(monkeypatch: pytest.MonkeyPatch, **rows: RunnerPin) -> dict[str, RunnerPin]:
    """Replace ``runner_pins()`` with the shipped table plus ``rows`` (by key).

    ``Runner.image``, the ``_effective_defaults`` release tier and the pinned
    badge all read the table through ``hal0.runners.pins.runner_pins``.
    """
    table = dict(load_runner_pins())
    table.update(rows)
    monkeypatch.setattr("hal0.runners.pins.runner_pins", lambda: table)
    return table


def _mock_sync_transport(monkeypatch: pytest.MonkeyPatch, images_json: dict[str, Any]) -> None:
    """Route the sync's httpx traffic (images.json + GHCR) to canned answers."""

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


_CPU_ENTRY: dict[str, Any] = {
    "id": "cpu",
    "title": "CPU",
    "summary": "CPU-only llama.cpp server.",
    "image": CPU_REPO,
    "tag": "b11552-r1",
    "backends": ["cpu"],
    "lanes_gated": ["cpu"],
    "family": "llama-server",
    "tier": "default",
}


def test_list_runner_images_empty(client: TestClient) -> None:
    resp = client.get("/api/runner-images")
    assert resp.status_code == 200
    body = resp.json()
    assert body["images"] == []
    # ``families`` always covers every hal0.runners.RUNNER_IMAGES key, even
    # with an empty catalogue — it's the launch-truth summary, not a view
    # over catalogued rows.
    from hal0.runners import RUNNER_IMAGES

    assert {f["family"] for f in body["families"]} == set(RUNNER_IMAGES)


def test_get_unknown_runner_image_404s(client: TestClient) -> None:
    resp = client.get("/api/runner-images/cpu")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "runner_image.not_found"


def test_sync_route_populates_catalogue(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    images_json = {
        "schema": "hal0.runner-images.v2",
        "runners": [
            _CPU_ENTRY,
            # Not a runner this hal0 ships: reported under ``skipped``.
            {"id": "rocmfpx", "image": "ghcr.io/hal0ai/hal0-rocmfpx", "tag": "c077206"},
        ],
    }
    _mock_sync_transport(monkeypatch, images_json)

    resp = client.post("/api/runner-images/sync")
    assert resp.status_code == 202
    body = resp.json()
    assert body["images_json_ok"] is True
    assert body["images_json_error"] is None
    assert len(body["images"]) == 1
    assert list(body["skipped"]) == ["rocmfpx"]

    listed = client.get("/api/runner-images").json()["images"]
    assert [r["id"] for r in listed] == ["cpu"]
    assert listed[0]["image"] == CPU_REPO
    assert listed[0]["tag"] == "b11552-r1"
    assert listed[0]["digest"] == "sha256:abc"  # the registry's, the entry carried none
    # Catalogue facts flow through _image_to_dict (model_dump) untouched.
    assert listed[0]["summary"] == "CPU-only llama.cpp server."
    assert listed[0]["family"] == "llama-server"
    assert listed[0]["backends"] == ["cpu"]
    assert listed[0]["lanes_gated"] == ["cpu"]
    assert "ownership" not in listed[0]
    assert "supported_backends" not in listed[0]

    detail = client.get("/api/runner-images/cpu")
    assert detail.status_code == 200
    assert detail.json()["backends"] == ["cpu"]
    assert detail.json()["runner"] == "cpu"


def test_sync_route_reports_a_v1_catalogue_as_a_fetch_error(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hal0.registry.runner_image import RunnerImage

    client.app.state.runner_image_registry.upsert(RunnerImage(id="cpu", image=CPU_REPO, tag="b1"))
    v1 = {
        "schema": "hal0.runner-images.v1",
        "images": [{"id": "cpu", "image": "ghcr.io/hal0ai/hal0-toolbox-cpu", "tag": "latest"}],
    }
    _mock_sync_transport(monkeypatch, v1)

    resp = client.post("/api/runner-images/sync")
    assert resp.status_code == 202
    body = resp.json()
    assert body["images_json_ok"] is False
    assert "hal0.runner-images.v2" in body["images_json_error"]
    assert body["images"] == []
    assert body["skipped"] == {}
    # A failed fetch never prunes.
    assert [r["id"] for r in client.get("/api/runner-images").json()["images"]] == ["cpu"]


def test_pull_unknown_image_404s(client: TestClient) -> None:
    resp = client.post("/api/runner-images/nope/pull")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "runner_image.not_catalogued"


def test_pull_status_before_starting_404s(client: TestClient) -> None:
    resp = client.get("/api/runner-images/cpu/pull/status")
    assert resp.status_code == 404


def test_pull_lifecycle_start_status_complete(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = client.app.state.runner_image_registry
    from hal0.registry.runner_image import RunnerImage

    store.upsert(RunnerImage(id="cpu", image=CPU_REPO, tag="b11552-r1"))

    runner_pull_jobs.provider_factory = lambda: _FakeProvider(
        [
            {"state": "pulling", "layer": 1, "total_layers": 1, "line": "Pulling fs layer"},
            {"state": "completed", "layer": 1, "total_layers": 1},
        ]
    )

    resp = client.post("/api/runner-images/cpu/pull")
    assert resp.status_code == 202
    job_id = resp.json()["id"]
    assert job_id

    status = client.get("/api/runner-images/cpu/pull/status")
    assert status.status_code == 200
    assert status.json()["state"] in ("queued", "running", "completed")

    pulls = client.get("/api/runner-images/pulls/list")
    assert pulls.status_code == 200
    assert any(j["image_id"] == "cpu" for j in pulls.json())


def test_pull_cancel_unknown_job_404s(client: TestClient) -> None:
    resp = client.post("/api/runner-images/cpu/pull/cancel")
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


# ── row enrichment: is_default / in_use_by / runner copy / badges ───────────


def _row(key: str, tag: str = "b2", *, image: str | None = None, **kw: Any):
    from hal0.registry.runner_image import RunnerImage

    return RunnerImage(id=key, image=image or f"ghcr.io/hal0ai/hal0-runner-{key}", tag=tag, **kw)


class TestEnrichRow:
    """Pure-function contract for the catalogue row enrichment."""

    def test_release_default_match_any_tag(self) -> None:
        from hal0.api.routes.runner_images import enrich_row

        row = enrich_row(
            _row("standard", "b1"),
            defaults={"standard": (f"{STD_REPO}:b2", "release")},
            slot_usage={},
            local=None,
        )
        # "any tag": the runner's launch ref sharing the row's REPO is enough.
        assert row["is_default"] == {"family": "standard", "source": "release"}

    @pytest.mark.parametrize("source", ["override", "env", "release"])
    def test_launch_source_reported(self, source: str) -> None:
        from hal0.api.routes.runner_images import enrich_row

        row = enrich_row(
            _row("cpu", "b1"),
            defaults={"cpu": (f"{CPU_REPO}:b1", source)},
            slot_usage={},
            local=None,
        )
        assert row["is_default"] == {"family": "cpu", "source": source}

    def test_launch_ref_in_another_repo_is_not_the_default(self) -> None:
        from hal0.api.routes.runner_images import enrich_row

        row = enrich_row(
            _row("standard", "b1"),
            defaults={"standard": ("ghcr.io/me/my-llama:dev", "override")},
            slot_usage={},
            local=None,
        )
        assert row["is_default"] is None

    def test_rows_map_to_their_runner_by_id_not_by_repo(self) -> None:
        """A row whose repo equals ANOTHER runner's launch repo is still only
        compared with the launch ref of the runner named by its own id."""
        from hal0.api.routes.runner_images import enrich_row

        row = enrich_row(
            _row("cpu", "b1"),
            defaults={"standard": (f"{CPU_REPO}:b1", "override")},
            slot_usage={},
            local=None,
        )
        assert row["is_default"] is None

    @pytest.mark.parametrize(
        "key", ["standard", "cpu", "flm", "kokoro", "moonshine", "comfyui", "qwen3tts", "cuda"]
    )
    def test_row_carries_registry_runner_tier_and_copy(self, key: str) -> None:
        """ADR-0026: a row owned by a registry runner carries that runner's
        key, title, blurb and tier — the page groups by ``tier`` and shows the
        registry copy, never a tier of its own derivation."""
        from hal0.api.routes.runner_images import enrich_row
        from hal0.runners import RUNNER_IMAGES

        row = enrich_row(
            _row(key, title="catalogue title", summary="catalogue summary", tier="catalogue tier"),
            defaults={},
            slot_usage={},
            local=None,
        )
        runner = RUNNER_IMAGES[key]
        assert row["runner"] == key
        assert row["tier"] == runner.tier
        assert row["title"] == runner.title
        assert row["blurb"] == runner.blurb
        # The published metadata stays on the row under its own names.
        assert row["summary"] == "catalogue summary"

    def test_tier_does_not_follow_the_launch_image(self) -> None:
        """The runner's launch image (``is_default``) and the runtime tier are
        different facts: an optional runtime's image stays optional even
        though it is its own runner's launch image."""
        from hal0.api.routes.runner_images import enrich_row

        row = enrich_row(
            _row("qwen3tts", "b1"),
            defaults={"qwen3tts": ("ghcr.io/hal0ai/hal0-runner-qwen3tts:b1", "release")},
            slot_usage={},
            local=None,
        )
        assert row["is_default"] == {"family": "qwen3tts", "source": "release"}
        assert row["tier"] == "optional"

    def test_row_without_registry_runner_falls_back_to_published_copy(self) -> None:
        """A row whose id is no runner (an old row, a hand-inserted one) is not
        a runtime: no runner key, and title/blurb/tier are the catalogue's own."""
        from hal0.api.routes.runner_images import enrich_row

        row = enrich_row(
            _row("legacy", "v1", title="Legacy", summary="Old image.", tier="optional"),
            defaults={},
            slot_usage={},
            local=None,
        )
        assert row["runner"] is None
        assert row["title"] == "Legacy"
        assert row["blurb"] == "Old image."
        assert row["tier"] == "optional"

    def test_row_without_registry_runner_or_copy_is_null(self) -> None:
        from hal0.api.routes.runner_images import enrich_row

        row = enrich_row(_row("legacy", "v1"), defaults={}, slot_usage={}, local=None)
        assert row["runner"] is None
        assert row["title"] is None
        assert row["blurb"] is None
        assert row["tier"] is None

    def test_in_use_by_matches_exact_image_tag(self) -> None:
        from hal0.api.routes.runner_images import enrich_row

        row = enrich_row(
            _row("standard", "b2"),
            defaults={},
            slot_usage={
                "agent": f"{STD_REPO}:b2",
                "utility": f"{STD_REPO}:b2",
                "npu": "ghcr.io/hal0ai/hal0-runner-flm:0.9.44",
                "old": f"{STD_REPO}:b1",  # other tag: no match
            },
            local=None,
        )
        assert row["in_use_by"] == ["agent", "utility"]
        assert row["is_default"] is None

    def test_specialties_are_gone(self) -> None:
        """``specialties`` left the row and the signature with the
        promptforge runner."""
        from hal0.api.routes.runner_images import enrich_row

        row = enrich_row(_row("standard"), defaults={}, slot_usage={}, local=None)
        assert "specialties" not in row
        with pytest.raises(TypeError):
            enrich_row(  # type: ignore[call-arg]
                _row("standard"), defaults={}, slot_usage={}, local=None, specialties={}
            )

    def test_provenance_passthrough_and_headline_hoist(self) -> None:
        """Every tags[] entry carries its sync-probed provenance, and the
        HEADLINE tag's is hoisted to row["provenance"]."""
        from hal0.api.routes.runner_images import enrich_row
        from hal0.registry.runner_image import RunnerImageTag

        prov = {
            "source_repo": "https://github.com/hal0ai/llama.cpp.git",
            "revision": "0a59adde1c5b2f3a4d6e7f8091a2b3c4d5e6f708",
            "patch_count": 4,
        }
        image = _row(
            "standard",
            "b2",
            tags=[
                RunnerImageTag(tag="b2", digest="sha256:a", provenance=prov),
                RunnerImageTag(tag="b1", digest="sha256:b", provenance=None),
            ],
        )
        row = enrich_row(image, defaults={}, slot_usage={}, local=None)
        assert row["tags"][0]["provenance"] == prov
        assert row["tags"][1]["provenance"] is None
        assert row["provenance"] == prov

    def test_provenance_none_when_headline_has_no_tag_row(self) -> None:
        """Absent provenance stays null — the contract is 'render nothing',
        never a placeholder dict."""
        from hal0.api.routes.runner_images import enrich_row

        row = enrich_row(_row("standard"), defaults={}, slot_usage={}, local=None)
        assert row["provenance"] is None


class TestPinnedBadge:
    """``badges`` is ``{pin_tag: "pinned"}`` — the tag this release's pin
    table names for the row's runner, when the row's repo is the pin's repo."""

    def _enrich(self, image):
        from hal0.api.routes.runner_images import enrich_row

        return enrich_row(image, defaults={}, slot_usage={}, local=None)

    def test_pinned_tag_badge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_pins(
            monkeypatch,
            standard=RunnerPin(key="standard", image=STD_REPO, tag="b2-r1", digest=DIGEST_A),
        )
        assert self._enrich(_row("standard", "b3-r1"))["badges"] == {"b2-r1": "pinned"}

    def test_pin_without_a_tag_has_no_badge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_pins(monkeypatch, standard=RunnerPin(key="standard", image=STD_REPO))
        assert self._enrich(_row("standard", "b3-r1"))["badges"] == {}

    def test_row_in_another_repo_has_no_badge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_pins(
            monkeypatch,
            standard=RunnerPin(key="standard", image=STD_REPO, tag="b2-r1"),
        )
        row = _row("standard", "b2-r1", image="ghcr.io/me/other")
        assert self._enrich(row)["badges"] == {}

    def test_row_with_no_pin_has_no_badge(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_pins(
            monkeypatch,
            standard=RunnerPin(key="standard", image=STD_REPO, tag="b2-r1"),
        )
        assert self._enrich(_row("legacy", "b2-r1"))["badges"] == {}

    def test_unreadable_pin_table_degrades_to_no_badges(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom():
            raise RuntimeError("manifest.json unreadable")

        monkeypatch.setattr("hal0.runners.pins.runner_pins", _boom)
        assert self._enrich(_row("standard", "b2-r1"))["badges"] == {}


class TestProvenanceForRef:
    """Pure lookup the system-info route uses to stamp each runner's
    effective image with its sync-cached provenance — no live registry
    call, so anything unsynced answers None."""

    _PROV: ClassVar[dict[str, Any]] = {
        "source_repo": "https://github.com/ggml-org/llama.cpp.git",
        "revision": "c841aee",
        "patch_count": 0,
    }

    def _catalogue(self):
        from hal0.registry.runner_image import RunnerImageTag

        return [
            _row(
                "standard",
                "b2",
                tags=[
                    RunnerImageTag(tag="b2", digest="sha256:aaa", provenance=self._PROV),
                    RunnerImageTag(tag="b1", digest="sha256:bbb", provenance=None),
                ],
            )
        ]

    def test_tag_ref_matches(self) -> None:
        from hal0.api.routes.runner_images import provenance_for_ref

        assert provenance_for_ref(f"{STD_REPO}:b2", self._catalogue()) == self._PROV

    def test_digest_pinned_ref_matches_by_digest(self) -> None:
        from hal0.api.routes.runner_images import provenance_for_ref

        assert provenance_for_ref(f"{STD_REPO}@sha256:aaa", self._catalogue()) == self._PROV

    def test_tag_and_digest_ref_matches_by_digest(self) -> None:
        """The pin spelling ``repo:tag@digest`` resolves through the digest."""
        from hal0.api.routes.runner_images import provenance_for_ref

        assert provenance_for_ref(f"{STD_REPO}:b2@sha256:aaa", self._catalogue()) == self._PROV

    def test_unsynced_tag_or_unknown_repo_is_none(self) -> None:
        from hal0.api.routes.runner_images import provenance_for_ref

        cat = self._catalogue()
        assert provenance_for_ref(f"{STD_REPO}:9999", cat) is None
        assert provenance_for_ref(f"{STD_REPO}:b1", cat) is None
        assert provenance_for_ref("ghcr.io/hal0ai/other:b2", cat) is None


# ── per-tag pull (catalogue v2 follow-up to #2043/#2044) ────────────────────


class TestPerTagPull:
    """POST /{id}/pull?tag=… pulls that catalogued tag, not just the headline.

    #2043 scoped the UI per-tag Pull out because ``runner_pull_jobs.enqueue``
    resolved ``image:tag`` exclusively from the row's headline tag. The route
    now accepts an optional ``tag`` restricted to the row's ``available_tags``
    (headline always allowed); omitting it keeps today's headline behaviour.
    """

    IMAGE_ID = "standard"

    def _seed(self, client: TestClient) -> None:
        from hal0.registry.runner_image import RunnerImage

        client.app.state.runner_image_registry.upsert(
            RunnerImage(
                id=self.IMAGE_ID,
                image="ghcr.io/hal0ai/hal0-runner-standard",
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
        assert body["image_ref"] == "ghcr.io/hal0ai/hal0-runner-standard:0822"
        assert body["id"]

        status = self._wait_terminal(client)
        assert status["state"] == "completed"
        assert status["tag"] == "0822"
        assert status["image_ref"] == "ghcr.io/hal0ai/hal0-runner-standard:0822"
        assert pulled == ["ghcr.io/hal0ai/hal0-runner-standard:0822"]

    def test_no_tag_keeps_headline_behaviour(self, client: TestClient) -> None:
        self._seed(client)
        pulled: list[str] = []
        self._stub_provider(pulled)

        resp = client.post(f"/api/runner-images/{self.IMAGE_ID}/pull")
        assert resp.status_code == 202
        body = resp.json()
        assert body["tag"] == "0824"
        assert body["image_ref"] == "ghcr.io/hal0ai/hal0-runner-standard:0824"
        self._wait_terminal(client)
        assert pulled == ["ghcr.io/hal0ai/hal0-runner-standard:0824"]

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
                image="ghcr.io/hal0ai/hal0-runner-standard",
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
        job = make_job(self.IMAGE_ID, "ghcr.io/hal0ai/hal0-runner-standard:0824", tag="0824")
        client.app.state.runner_image_pull_jobs[self.IMAGE_ID] = job

        resp = client.post(f"/api/runner-images/{self.IMAGE_ID}/pull", params={"tag": "0822"})
        assert resp.status_code == 409
        err = resp.json()["error"]
        assert err["code"] == "runner_image.pull_conflict"
        assert err["details"]["in_flight_tag"] == "0824"

    def test_inflight_same_tag_resumes(self, client: TestClient) -> None:
        from hal0.registry.runner_pull import make_job

        self._seed(client)
        job = make_job(self.IMAGE_ID, "ghcr.io/hal0ai/hal0-runner-standard:0824", tag="0824")
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
        assert body["image_ref"] == "ghcr.io/hal0ai/hal0-runner-standard:0822"

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
    """GET /api/runner-images rows carry the frozen catalogue contract."""

    def _seed_standard(self, client: TestClient):
        from hal0.registry.runner_image import RunnerImage

        return client.app.state.runner_image_registry.upsert(
            RunnerImage(
                id="standard",
                image=STD_REPO,
                tag="b2",
                available_tags=["b2", "b1"],
            )
        )

    def test_release_default_and_contract_fields(self, isolated_client: TestClient) -> None:
        """With no override or env var the runner launches its release pin, so
        the standard row reports source=release."""
        self._seed_standard(isolated_client)
        rows = isolated_client.get("/api/runner-images").json()["images"]
        row = next(r for r in rows if r["id"] == "standard")
        assert row["available_tags"] == ["b2", "b1"]
        assert row["runner"] == "standard"
        assert row["is_default"] == {"family": "standard", "source": "release"}
        assert row["in_use_by"] == []

    def test_env_override_is_reported_as_env_source(
        self, isolated_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._seed_standard(isolated_client)
        monkeypatch.setenv("HAL0_RUNNER_IMAGE_STANDARD", f"{STD_REPO}:b1")
        rows = isolated_client.get("/api/runner-images").json()["images"]
        row = next(r for r in rows if r["id"] == "standard")
        assert row["is_default"] == {"family": "standard", "source": "env"}

    def test_override_set_and_null_clear_via_settings_put(
        self, isolated_client: TestClient
    ) -> None:
        """[slots].default_images written/cleared through PUT /api/settings:
        set → source=override; explicit null value → override removed, the
        runner falls back to its release pin."""
        from hal0.registry.runner_image import RunnerImage

        isolated_client.app.state.runner_image_registry.upsert(
            RunnerImage(id="cpu", image=CPU_REPO, tag="b1")
        )

        r = isolated_client.put(
            "/api/settings",
            json={"slots": {"default_images": {"cpu": f"{CPU_REPO}:b1"}}},
        )
        assert r.status_code == 200, r.text
        assert r.json()["slots"]["default_images"] == {"cpu": f"{CPU_REPO}:b1"}

        rows = isolated_client.get("/api/runner-images").json()["images"]
        row = next(r for r in rows if r["id"] == "cpu")
        assert row["is_default"] == {"family": "cpu", "source": "override"}

        # CLEAR: explicit null value removes the key (deep-merge has no
        # delete idiom; the SlotsConfig validator drops null-valued keys).
        r = isolated_client.put("/api/settings", json={"slots": {"default_images": {"cpu": None}}})
        assert r.status_code == 200, r.text
        assert r.json()["slots"]["default_images"] == {}

        rows = isolated_client.get("/api/runner-images").json()["images"]
        row = next(r for r in rows if r["id"] == "cpu")
        assert row["is_default"] == {"family": "cpu", "source": "release"}

    def test_override_to_another_repo_unmarks_the_catalogue_row(
        self, isolated_client: TestClient
    ) -> None:
        self._seed_standard(isolated_client)
        r = isolated_client.put(
            "/api/settings",
            json={"slots": {"default_images": {"standard": "ghcr.io/me/my-llama:dev"}}},
        )
        assert r.status_code == 200, r.text

        rows = isolated_client.get("/api/runner-images").json()["images"]
        row = next(r for r in rows if r["id"] == "standard")
        assert row["is_default"] is None

    @pytest.mark.parametrize("key", ["rocmfpx", "vulkanfpx", "strix", "promptforge", "bogus"])
    def test_override_for_a_removed_runner_is_rejected(
        self, isolated_client: TestClient, key: str
    ) -> None:
        """``[slots].default_images`` accepts only RUNNER_IMAGES keys; there is
        no alias folding for runners that no longer exist."""
        r = isolated_client.put(
            "/api/settings",
            json={"slots": {"default_images": {key: "ghcr.io/x/a:1"}}},
        )
        assert r.status_code >= 400
        assert r.status_code < 500

    def test_in_use_by_lists_slots_resolving_to_the_row(
        self, isolated_client: TestClient, tmp_hal0_home: str
    ) -> None:
        """A slot whose resolved config references image:tag shows up in the
        row's in_use_by (resolved-config path — no rendered units in tests)."""
        from pathlib import Path

        self._seed_standard(isolated_client)
        slots_dir = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
        slots_dir.mkdir(parents=True, exist_ok=True)
        (slots_dir / "agent.toml").write_text(
            f'name = "agent"\nport = 8081\nimage_pin = "{STD_REPO}:b2"\n',
            encoding="utf-8",
        )

        rows = isolated_client.get("/api/runner-images").json()["images"]
        row = next(r for r in rows if r["id"] == "standard")
        assert row["in_use_by"] == ["agent"]


# ── store-truth enrichment: store_state / per-tag downloaded / badges ───────
#
# Rows gain store_state ("present" / "missing" / "unknown"), a per-tag
# ``downloaded`` flag, ``store_context`` and the ``pinned`` badge — sourced
# from ``hal0.providers.podman_introspect.images_digests()`` (monkeypatched at
# the route module's imported name, its patch point) and the pin table.


class TestStoreStateEnrichment:
    DIGEST_A = DIGEST_A
    DIGEST_B = DIGEST_B

    def _seed(self, client: TestClient, **overrides):
        from hal0.registry.runner_image import RunnerImage, RunnerImageTag

        image = RunnerImage(
            id="cpu",
            image=CPU_REPO,
            tag="b2",
            available_tags=["b2", "b1"],
            **overrides,
        )
        store = client.app.state.runner_image_registry
        store.upsert(image)
        store.set_tags(
            "cpu",
            [
                RunnerImageTag(tag="b2", digest=self.DIGEST_A),
                RunnerImageTag(tag="b1", digest=self.DIGEST_B),
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
        row = next(r for r in rows if r["id"] == "cpu")
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
        row = next(r for r in rows if r["id"] == "cpu")
        assert row["store_state"] == "missing"
        assert row["downloaded"] is False
        assert row["tags"][0]["downloaded"] is False
        assert row["tags"][1]["downloaded"] is False

    def test_store_state_unknown_falls_back_to_marker(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._seed(client, local_path="/var/lib/hal0/images/cpu")
        monkeypatch.setattr("hal0.api.routes.runner_images.images_digests", lambda: None)

        rows = client.get("/api/runner-images").json()["images"]
        row = next(r for r in rows if r["id"] == "cpu")  # cpu has local_path set
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
            lambda: pi.LocalImagesDigests(refs={f"{CPU_REPO}:b2": None}, context="rootful"),
        )

        rows = client.get("/api/runner-images/downloaded").json()["images"]
        assert [r["id"] for r in rows] == ["cpu"]
        assert rows[0]["store_state"] == "present"

    def test_pinned_badge_on_the_release_pin_tag(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from hal0.registry.runner_image import RunnerImage, RunnerImageTag

        _patch_pins(
            monkeypatch,
            cpu=RunnerPin(key="cpu", image=CPU_REPO, tag="b2", digest=self.DIGEST_A),
        )
        monkeypatch.setattr("hal0.api.routes.runner_images.images_digests", lambda: None)
        store = client.app.state.runner_image_registry
        store.upsert(RunnerImage(id="cpu", image=CPU_REPO, tag="b3"))
        store.set_tags("cpu", [RunnerImageTag(tag="b3"), RunnerImageTag(tag="b2")])

        rows = client.get("/api/runner-images").json()["images"]
        row = next(r for r in rows if r["id"] == "cpu")
        assert row["badges"] == {"b2": "pinned"}

    def test_badges_empty_when_the_pin_has_no_tag(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_pins(monkeypatch, cpu=RunnerPin(key="cpu", image=CPU_REPO))
        monkeypatch.setattr("hal0.api.routes.runner_images.images_digests", lambda: None)
        self._seed(client)

        rows = client.get("/api/runner-images").json()["images"]
        row = next(r for r in rows if r["id"] == "cpu")
        assert row["badges"] == {}

    def test_no_validated_candidate_or_deprecated_badges(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only ``pinned`` exists: a tag the release does not pin carries no
        badge, however old or new it is."""
        _patch_pins(
            monkeypatch, cpu=RunnerPin(key="cpu", image=CPU_REPO, tag="b2", digest=self.DIGEST_A)
        )
        monkeypatch.setattr("hal0.api.routes.runner_images.images_digests", lambda: None)
        self._seed(client)

        rows = client.get("/api/runner-images").json()["images"]
        row = next(r for r in rows if r["id"] == "cpu")
        assert set(row["badges"].values()) <= {"pinned"}
        assert "b1" not in row["badges"]

    def test_get_runner_image_detail_carries_store_state(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from hal0.providers import podman_introspect as pi

        self._seed(client)
        monkeypatch.setattr(
            "hal0.api.routes.runner_images.images_digests",
            lambda: pi.LocalImagesDigests(refs={}, context="rootless"),
        )

        detail = client.get("/api/runner-images/cpu").json()
        assert detail["store_state"] == "missing"
        assert detail["store_context"] == "rootless"

    def test_sync_route_rows_carry_store_state(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("hal0.api.routes.runner_images.images_digests", lambda: None)
        _mock_sync_transport(
            monkeypatch, {"schema": "hal0.runner-images.v2", "runners": [_CPU_ENTRY]}
        )

        resp = client.post("/api/runner-images/sync")
        assert resp.status_code == 202
        body = resp.json()
        assert body["images"][0]["store_state"] == "unknown"
        assert "families" in body
        assert "skipped" in body


# ── launch-truth families payload ───────────────────────────────────────────
#
# GET /api/runner-images has a top-level ``families`` list: per
# hal0.runners.RUNNER_IMAGES key, the effective ref, its source tier
# (override → env → release), store state, the catalogue row's gated
# ``tag``/``digest`` (``newest_release``) and an update marker.


class TestFamiliesPayload:
    DIGEST_A = DIGEST_A  # the catalogue's gated digest (tag b2)
    DIGEST_B = DIGEST_B  # an older digest (tag b1)

    def _seed_std(self, client: TestClient) -> None:
        from hal0.registry.runner_image import RunnerImage, RunnerImageTag

        store = client.app.state.runner_image_registry
        store.upsert(
            RunnerImage(
                id="standard",
                image=STD_REPO,
                tag="b2",
                digest=self.DIGEST_A,
                available_tags=["b2", "b1"],
            )
        )
        store.set_tags(
            "standard",
            [
                RunnerImageTag(tag="b2", digest=self.DIGEST_A),
                RunnerImageTag(tag="b1", digest=self.DIGEST_B),
            ],
        )

    @staticmethod
    def _fam(body: dict[str, Any], key: str = "standard") -> dict[str, Any]:
        return next(f for f in body["families"] if f["family"] == key)

    def test_families_cover_exactly_the_registry_runners(self, client: TestClient) -> None:
        from hal0.runners import RUNNER_IMAGES

        fams = [f["family"] for f in client.get("/api/runner-images").json()["families"]]
        assert sorted(fams) == sorted(RUNNER_IMAGES)
        for gone in ("rocmfpx", "vulkanfpx", "strix", "promptforge"):
            assert gone not in fams

    def test_families_override_source(
        self, isolated_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from hal0.providers import podman_introspect as pi

        self._seed_std(isolated_client)
        r = isolated_client.put(
            "/api/settings",
            json={"slots": {"default_images": {"standard": f"{STD_REPO}:b1"}}},
        )
        assert r.status_code == 200, r.text
        monkeypatch.setattr(
            "hal0.api.routes.runner_images.images_digests",
            lambda: pi.LocalImagesDigests(
                refs={f"{STD_REPO}:b1": self.DIGEST_B}, context="rootful"
            ),
        )

        fam = self._fam(isolated_client.get("/api/runner-images").json())
        assert fam["effective_ref"] == f"{STD_REPO}:b1"
        assert fam["source"] == "override"
        assert fam["store_state"] == "present"
        assert fam["newest_release"] == {"tag": "b2", "digest": self.DIGEST_A}
        assert fam["update_available"] is True  # b1's digest differs from the gated one

    def test_families_env_source(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAL0_RUNNER_IMAGE_STANDARD", f"{STD_REPO}:custom")

        fam = self._fam(client.get("/api/runner-images").json())
        assert fam["source"] == "env"
        assert fam["effective_ref"] == f"{STD_REPO}:custom"

    def test_override_wins_over_env(
        self, isolated_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HAL0_RUNNER_IMAGE_STANDARD", f"{STD_REPO}:from-env")
        r = isolated_client.put(
            "/api/settings",
            json={"slots": {"default_images": {"standard": f"{STD_REPO}:from-override"}}},
        )
        assert r.status_code == 200, r.text

        fam = self._fam(isolated_client.get("/api/runner-images").json())
        assert fam["source"] == "override"
        assert fam["effective_ref"] == f"{STD_REPO}:from-override"

    def test_families_release_source_is_the_pin_ref(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_pins(
            monkeypatch,
            standard=RunnerPin(key="standard", image=STD_REPO, tag="b2", digest=self.DIGEST_A),
        )

        fam = self._fam(client.get("/api/runner-images").json())
        assert fam["source"] == "release"
        assert fam["effective_ref"] == f"{STD_REPO}:b2@{self.DIGEST_A}"

    def test_families_release_source_no_row_no_update(self, client: TestClient) -> None:
        """No override/env and no catalogue row for the runner: newest_release
        is null and update_available is False — never a crash on a missing row."""
        fam = self._fam(client.get("/api/runner-images").json())
        assert fam["source"] == "release"
        assert fam["newest_release"] is None
        assert fam["update_available"] is False
        assert fam["slots"] == []
        assert fam["pinned_slots"] == []

    def test_newest_release_is_the_catalogue_row_pin(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``newest_release`` is the catalogue row's own gated ``tag``/``digest``
        (not the newest of ``available_tags``)."""
        self._seed_std(client)
        _patch_pins(
            monkeypatch,
            standard=RunnerPin(key="standard", image=STD_REPO, tag="b2", digest=self.DIGEST_A),
        )

        fam = self._fam(client.get("/api/runner-images").json())
        assert fam["newest_release"] == {"tag": "b2", "digest": self.DIGEST_A}
        assert fam["update_available"] is False  # this box launches the gated digest

    def test_update_available_when_the_pin_trails_the_catalogue(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The release pin names an older digest than the catalogue now gates:
        the runner shows an update."""
        self._seed_std(client)
        _patch_pins(
            monkeypatch,
            standard=RunnerPin(key="standard", image=STD_REPO, tag="b1", digest=self.DIGEST_B),
        )

        fam = self._fam(client.get("/api/runner-images").json())
        assert fam["effective_ref"] == f"{STD_REPO}:b1@{self.DIGEST_B}"
        assert fam["newest_release"] == {"tag": "b2", "digest": self.DIGEST_A}
        assert fam["update_available"] is True

    def test_no_newest_release_when_the_effective_ref_is_in_another_repo(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A catalogue row only speaks for the runner when it is the same
        image repo the box launches (an operator's own build has no 'newer')."""
        self._seed_std(client)
        monkeypatch.setenv("HAL0_RUNNER_IMAGE_STANDARD", "ghcr.io/me/my-llama:dev")

        fam = self._fam(client.get("/api/runner-images").json())
        assert fam["source"] == "env"
        assert fam["newest_release"] is None
        assert fam["update_available"] is False

    def test_families_carry_registry_title_and_tier(self, client: TestClient) -> None:
        """ADR-0026: every family row names its runtime and tier straight from
        the registry, so the launch-image strip and the grouped list agree."""
        from hal0.runners import RUNNER_IMAGES

        body = client.get("/api/runner-images").json()
        assert body["families"]
        for fam in body["families"]:
            runner = RUNNER_IMAGES[fam["family"]]
            assert fam["title"] == runner.title
            assert fam["tier"] == runner.tier

    def test_families_digest_pinned_ref_present_and_update_available(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An effective ref pinned by digest (``repo@sha256:…`` — a digest-form
        env/override value) must resolve store presence and
        ``update_available`` via digest match, not the tag-lookup path —
        ``ref.rpartition(":")`` would otherwise split inside the digest and
        produce hex garbage as a fake "tag"."""
        from hal0.providers import podman_introspect as pi

        self._seed_std(client)  # gated b2 / DIGEST_A; b1 / DIGEST_B
        pinned_ref = f"{STD_REPO}@{self.DIGEST_B}"
        monkeypatch.setenv("HAL0_RUNNER_IMAGE_STANDARD", pinned_ref)
        monkeypatch.setattr(
            "hal0.api.routes.runner_images.images_digests",
            lambda: pi.LocalImagesDigests(
                refs={"docker.io/y/alias:zz": self.DIGEST_B}, context="rootful"
            ),
        )

        fam = self._fam(client.get("/api/runner-images").json())
        assert fam["source"] == "env"
        assert fam["effective_ref"] == pinned_ref
        assert fam["store_state"] == "present"  # digest match, name irrelevant
        assert fam["newest_release"] == {"tag": "b2", "digest": self.DIGEST_A}
        assert fam["update_available"] is True

    def test_families_store_state_unknown_when_store_unreadable(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._seed_std(client)
        monkeypatch.setenv("HAL0_RUNNER_IMAGE_STANDARD", f"{STD_REPO}:b1")
        monkeypatch.setattr("hal0.api.routes.runner_images.images_digests", lambda: None)

        fam = self._fam(client.get("/api/runner-images").json())
        assert fam["store_state"] == "unknown"

    def test_families_store_state_missing_when_ref_not_pulled(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from hal0.providers import podman_introspect as pi

        self._seed_std(client)
        monkeypatch.setenv("HAL0_RUNNER_IMAGE_STANDARD", f"{STD_REPO}:b1")
        monkeypatch.setattr(
            "hal0.api.routes.runner_images.images_digests",
            lambda: pi.LocalImagesDigests(refs={}, context="rootful"),
        )

        fam = self._fam(client.get("/api/runner-images").json())
        assert fam["store_state"] == "missing"

    def test_families_no_update_when_digests_match(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Effective ref already on the catalogue's gated digest —
        update_available is False."""
        from hal0.providers import podman_introspect as pi

        self._seed_std(client)
        monkeypatch.setenv("HAL0_RUNNER_IMAGE_STANDARD", f"{STD_REPO}:b2")
        monkeypatch.setattr(
            "hal0.api.routes.runner_images.images_digests",
            lambda: pi.LocalImagesDigests(
                refs={f"{STD_REPO}:b2": self.DIGEST_A}, context="rootful"
            ),
        )

        fam = self._fam(client.get("/api/runner-images").json())
        assert fam["newest_release"] == {"tag": "b2", "digest": self.DIGEST_A}
        assert fam["update_available"] is False

    def test_families_slots_and_pinned_slots(
        self, isolated_client: TestClient, tmp_hal0_home: str
    ) -> None:
        """A slot resolving to the exact effective ref lands in ``slots``; a
        slot pinned to a different tag of the same repo lands in
        ``pinned_slots`` instead."""
        from pathlib import Path

        self._seed_std(isolated_client)
        r = isolated_client.put(
            "/api/settings",
            json={"slots": {"default_images": {"standard": f"{STD_REPO}:b1"}}},
        )
        assert r.status_code == 200, r.text

        slots_dir = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
        slots_dir.mkdir(parents=True, exist_ok=True)
        (slots_dir / "agent.toml").write_text(
            f'name = "agent"\nport = 8081\nimage_pin = "{STD_REPO}:b1"\n',
            encoding="utf-8",
        )
        (slots_dir / "utility.toml").write_text(
            f'name = "utility"\nport = 8082\nimage_pin = "{STD_REPO}:b2"\n',
            encoding="utf-8",
        )

        fam = self._fam(isolated_client.get("/api/runner-images").json())
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
        body = resp.json()
        assert body["families"]
        assert {f["source"] for f in body["families"]} == {"release"}


# ── DELETE /{image_id:path}/tags/{tag} — disk reclaim (D2, #2106) ───────────


class TestDeleteRunnerImageTag:
    IMAGE_ID = "standard"

    def _seed(self, client: TestClient, **overrides) -> None:
        from hal0.registry.runner_image import RunnerImage, RunnerImageTag

        store = client.app.state.runner_image_registry
        kw = {"available_tags": ["0826", "0824"], **overrides}
        store.upsert(
            RunnerImage(
                id=self.IMAGE_ID, image="ghcr.io/hal0ai/hal0-runner-standard", tag="0826", **kw
            )
        )
        store.set_tags(
            self.IMAGE_ID,
            [
                RunnerImageTag(tag="0826", digest="sha256:" + "a" * 64),
                RunnerImageTag(tag="0824", digest="sha256:" + "b" * 64),
            ],
        )

    def _fake_remove_image(self, monkeypatch: pytest.MonkeyPatch, outcome: str, reason=None):
        calls: list[str] = []

        def _fake(ref: str):
            calls.append(ref)
            return (outcome, reason)

        monkeypatch.setattr("hal0.providers.podman_mutate.remove_image", _fake)
        return calls

    # ── 404s ─────────────────────────────────────────────────────────────

    def test_unknown_image_404s(self, client: TestClient) -> None:
        resp = client.delete(f"/api/runner-images/{self.IMAGE_ID}/tags/0824")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "runner_image.not_found"

    def test_unknown_tag_404s(self, client: TestClient) -> None:
        self._seed(client)
        resp = client.delete(f"/api/runner-images/{self.IMAGE_ID}/tags/9999")
        assert resp.status_code == 404
        err = resp.json()["error"]
        assert err["code"] == "runner_image.tag_not_found"
        assert err["details"] == {"image_id": self.IMAGE_ID, "tag": "9999"}

    # ── pull-race guard (fix round 1, #2106) ────────────────────────────

    def test_in_flight_pull_of_same_tag_409s_catalogue_untouched(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A running pull for THIS tag must block the delete — otherwise the
        pull can complete after ``store.remove_tag`` and re-stamp
        ``local_path``/``downloaded_at`` via ``set_local_state``, leaving the
        row ``downloaded=True`` with zero tag rows (reviewer-reproduced
        race, fix round 1)."""
        from hal0.registry.runner_pull import make_job

        self._seed(client)
        job = make_job(self.IMAGE_ID, "ghcr.io/hal0ai/hal0-runner-standard:0824", tag="0824")
        job.state = "running"
        client.app.state.runner_image_pull_jobs[self.IMAGE_ID] = job

        def _boom(ref: str):
            raise AssertionError("seam must not be reached while a pull is in flight")

        monkeypatch.setattr("hal0.providers.podman_mutate.remove_image", _boom)

        resp = client.delete(f"/api/runner-images/{self.IMAGE_ID}/tags/0824")
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "runner_image.pull_in_progress"

        detail = client.get(f"/api/runner-images/{self.IMAGE_ID}").json()
        assert any(t["tag"] == "0824" for t in detail["tags"])

    def test_queued_untagged_job_also_blocks(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An older untagged job record (``job.tag is None``) always pulls
        the headline — it must block a delete of the headline tag too."""
        from hal0.registry.runner_pull import make_job

        self._seed(client)
        job = make_job(self.IMAGE_ID, "ghcr.io/hal0ai/hal0-runner-standard:0826")
        job.state = "queued"
        client.app.state.runner_image_pull_jobs[self.IMAGE_ID] = job
        monkeypatch.setattr(
            "hal0.providers.podman_mutate.remove_image",
            lambda ref: (_ for _ in ()).throw(AssertionError("seam must not be reached")),
        )

        resp = client.delete(f"/api/runner-images/{self.IMAGE_ID}/tags/0826")
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "runner_image.pull_in_progress"

    def test_in_flight_pull_of_a_different_tag_does_not_block(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A running pull for a DIFFERENT tag of the same id must not block
        deleting an unrelated, already-downloaded tag."""
        from hal0.registry.runner_pull import make_job

        self._seed(client)
        job = make_job(self.IMAGE_ID, "ghcr.io/hal0ai/hal0-runner-standard:0826", tag="0826")
        job.state = "running"
        client.app.state.runner_image_pull_jobs[self.IMAGE_ID] = job
        monkeypatch.setattr("hal0.api.routes.runner_images._slot_image_usage", lambda: {})
        self._fake_remove_image(monkeypatch, "removed")

        resp = client.delete(f"/api/runner-images/{self.IMAGE_ID}/tags/0824")
        assert resp.status_code == 200

    def test_terminal_job_does_not_block(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A completed/failed/cancelled job record for this id is stale, not
        in-flight — it must not block the delete."""
        from hal0.registry.runner_pull import make_job

        self._seed(client)
        job = make_job(self.IMAGE_ID, "ghcr.io/hal0ai/hal0-runner-standard:0824", tag="0824")
        job.state = "completed"
        client.app.state.runner_image_pull_jobs[self.IMAGE_ID] = job
        monkeypatch.setattr("hal0.api.routes.runner_images._slot_image_usage", lambda: {})
        self._fake_remove_image(monkeypatch, "removed")

        resp = client.delete(f"/api/runner-images/{self.IMAGE_ID}/tags/0824")
        assert resp.status_code == 200

    # ── in-use guards ────────────────────────────────────────────────────

    def test_app_level_guard_409_names_slot_on_exact_ref_match(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._seed(client)
        monkeypatch.setattr(
            "hal0.api.routes.runner_images._slot_image_usage",
            lambda: {"brain": "ghcr.io/hal0ai/hal0-runner-standard:0824"},
        )

        def _boom(ref: str):
            raise AssertionError("seam must not be reached when app-level guard fires")

        monkeypatch.setattr("hal0.providers.podman_mutate.remove_image", _boom)

        resp = client.delete(f"/api/runner-images/{self.IMAGE_ID}/tags/0824")
        assert resp.status_code == 409
        err = resp.json()["error"]
        assert err["code"] == "runner_image.tag_in_use"
        assert err["details"]["slots"] == ["brain"]

    def test_app_level_guard_409_names_slot_on_digest_sibling_match(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Deleting the headline 0826 while a slot is pinned to 0824 — a
        DIFFERENT tag of the same repo that (per the seeded digests) shares
        0826's digest — is caught even though the ref strings differ."""
        self._seed(client)
        from hal0.registry.runner_image import RunnerImageTag

        client.app.state.runner_image_registry.set_tags(
            self.IMAGE_ID,
            [
                RunnerImageTag(tag="0826", digest="sha256:" + "a" * 64),
                RunnerImageTag(tag="0824", digest="sha256:" + "a" * 64),  # same digest as 0826
            ],
        )
        monkeypatch.setattr(
            "hal0.api.routes.runner_images._slot_image_usage",
            lambda: {"agent": "ghcr.io/hal0ai/hal0-runner-standard:0824"},
        )

        def _boom(ref: str):
            raise AssertionError("seam must not be reached when app-level guard fires")

        monkeypatch.setattr("hal0.providers.podman_mutate.remove_image", _boom)

        resp = client.delete(f"/api/runner-images/{self.IMAGE_ID}/tags/0826")
        assert resp.status_code == 409
        assert resp.json()["error"]["details"]["slots"] == ["agent"]

    def test_seam_level_in_use_409_names_no_slots(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The app-level guard didn't catch anything (no slot config
        matches), but the seam itself refuses (rc 67) — still 409, but
        ``slots`` is absent/empty since no slot was implicated."""
        self._seed(client)
        monkeypatch.setattr("hal0.api.routes.runner_images._slot_image_usage", lambda: {})
        self._fake_remove_image(monkeypatch, "in-use")

        resp = client.delete(f"/api/runner-images/{self.IMAGE_ID}/tags/0824")
        assert resp.status_code == 409
        err = resp.json()["error"]
        assert err["code"] == "runner_image.tag_in_use"
        assert not err["details"].get("slots")

        # catalogue untouched
        detail = client.get(f"/api/runner-images/{self.IMAGE_ID}").json()
        assert any(t["tag"] == "0824" for t in detail["tags"])

    # ── seam-unavailable ─────────────────────────────────────────────────

    def test_seam_unavailable_502_catalogue_untouched(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._seed(client)
        monkeypatch.setattr("hal0.api.routes.runner_images._slot_image_usage", lambda: {})
        self._fake_remove_image(monkeypatch, "unknown", "grant-denied")

        resp = client.delete(f"/api/runner-images/{self.IMAGE_ID}/tags/0824")
        assert resp.status_code == 502
        err = resp.json()["error"]
        assert err["code"] == "runner_image.rm_unavailable"
        assert err["details"]["reason"] == "grant-denied"

        detail = client.get(f"/api/runner-images/{self.IMAGE_ID}").json()
        assert any(t["tag"] == "0824" for t in detail["tags"])
        assert "0824" in detail["available_tags"]

    # ── happy paths ──────────────────────────────────────────────────────

    def test_removed_outcome_drops_the_tag_row(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._seed(client)
        monkeypatch.setattr("hal0.api.routes.runner_images._slot_image_usage", lambda: {})
        calls = self._fake_remove_image(monkeypatch, "removed")

        resp = client.delete(f"/api/runner-images/{self.IMAGE_ID}/tags/0824")
        assert resp.status_code == 200
        assert resp.json() == {"removed": True, "outcome": "removed"}
        assert calls == ["ghcr.io/hal0ai/hal0-runner-standard:0824"]

        detail = client.get(f"/api/runner-images/{self.IMAGE_ID}").json()
        assert all(t["tag"] != "0824" for t in detail["tags"])
        assert detail["available_tags"] == ["0826"]

    def test_missing_outcome_also_drops_the_tag_row(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """podman already had no such image (a previous manual rm, e.g.) —
        still an honest, successful reconciliation, not an error."""
        self._seed(client)
        monkeypatch.setattr("hal0.api.routes.runner_images._slot_image_usage", lambda: {})
        self._fake_remove_image(monkeypatch, "missing")

        resp = client.delete(f"/api/runner-images/{self.IMAGE_ID}/tags/0824")
        assert resp.status_code == 200
        assert resp.json() == {"removed": False, "outcome": "missing"}

        detail = client.get(f"/api/runner-images/{self.IMAGE_ID}").json()
        assert all(t["tag"] != "0824" for t in detail["tags"])

    def test_headline_tag_deletable_even_without_available_tags_entry(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The headline ``tag`` column is always a valid delete target, even
        if (unusually) it isn't also listed in available_tags."""
        from hal0.registry.runner_image import RunnerImage, RunnerImageTag

        store = client.app.state.runner_image_registry
        store.upsert(RunnerImage(id="solo", image="ghcr.io/x/solo", tag="only"))
        store.set_tags("solo", [RunnerImageTag(tag="only")])
        monkeypatch.setattr("hal0.api.routes.runner_images._slot_image_usage", lambda: {})
        self._fake_remove_image(monkeypatch, "removed")

        resp = client.delete("/api/runner-images/solo/tags/only")
        assert resp.status_code == 200
        assert resp.json()["removed"] is True

    def test_marker_unlinked_on_disk_when_last_tag_removed(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        from hal0.registry.runner_image import RunnerImage, RunnerImageTag

        marker = tmp_path / "marker.json"
        marker.write_text('{"image_id": "solo", "image": "ghcr.io/x/solo:only"}')

        store = client.app.state.runner_image_registry
        store.upsert(RunnerImage(id="solo", image="ghcr.io/x/solo", tag="only"))
        store.set_tags("solo", [RunnerImageTag(tag="only")])
        store.set_local_state("solo", local_path=str(marker), downloaded_at="t")
        assert marker.exists()

        monkeypatch.setattr("hal0.api.routes.runner_images._slot_image_usage", lambda: {})
        self._fake_remove_image(monkeypatch, "removed")

        resp = client.delete("/api/runner-images/solo/tags/only")
        assert resp.status_code == 200
        assert not marker.exists()

        detail = client.get("/api/runner-images/solo").json()
        assert detail["downloaded"] is False

    def test_marker_left_alone_when_sibling_tag_survives(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        marker = tmp_path / "marker.json"
        marker.write_text(
            f'{{"image_id": "{self.IMAGE_ID}", "image": "ghcr.io/hal0ai/hal0-runner-standard:0826"}}'
        )
        self._seed(client)
        client.app.state.runner_image_registry.set_local_state(
            self.IMAGE_ID, local_path=str(marker), downloaded_at="t"
        )
        monkeypatch.setattr("hal0.api.routes.runner_images._slot_image_usage", lambda: {})
        self._fake_remove_image(monkeypatch, "removed")

        resp = client.delete(f"/api/runner-images/{self.IMAGE_ID}/tags/0824")
        assert resp.status_code == 200
        assert marker.exists()  # still the marker for the surviving 0826 tag

    # ── audit ────────────────────────────────────────────────────────────

    def test_records_audit_action_on_success(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._seed(client)
        monkeypatch.setattr("hal0.api.routes.runner_images._slot_image_usage", lambda: {})
        self._fake_remove_image(monkeypatch, "removed")

        resp = client.delete(f"/api/runner-images/{self.IMAGE_ID}/tags/0824")
        assert resp.status_code == 200

        activity = client.get(
            "/api/activity", params={"category": "runner_image", "action": "runner_image.rm"}
        ).json()
        rec = next(
            r
            for r in activity["records"]
            if r["target"] == "ghcr.io/hal0ai/hal0-runner-standard:0824"
        )
        assert rec["outcome"] == "ok"
        assert rec["after"] == {"outcome": "removed", "catalogue_removed": True}

    def test_desync_logged_and_recorded_when_seam_removes_but_no_catalogue_row(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Fix round 1 (#2106, low finding): the seam agreeing the bytes are
        gone doesn't guarantee ``store.remove_tag`` found a matching row —
        e.g. the headline tag validated via ``image.tag`` with no
        ``runner_image_tag`` row ever synced for it. That desync must be
        both audited (``rec.after.catalogue_removed`` is ``False``) and
        logged, without turning an honest 200 into an error."""
        import logging

        from hal0.registry.runner_image import RunnerImage

        store = client.app.state.runner_image_registry
        store.upsert(RunnerImage(id="ghost", image="ghcr.io/x/ghost", tag="only"))
        # Deliberately no set_tags call — no runner_image_tag row for "only".
        monkeypatch.setattr("hal0.api.routes.runner_images._slot_image_usage", lambda: {})
        self._fake_remove_image(monkeypatch, "removed")

        with caplog.at_level(logging.WARNING, logger="hal0.api.routes.runner_images"):
            resp = client.delete("/api/runner-images/ghost/tags/only")
        assert resp.status_code == 200
        assert resp.json() == {"removed": True, "outcome": "removed"}
        assert "runner_image.rm_catalogue_desync" in caplog.text

        activity = client.get(
            "/api/activity", params={"category": "runner_image", "action": "runner_image.rm"}
        ).json()
        rec = next(r for r in activity["records"] if r["target"] == "ghcr.io/x/ghost:only")
        assert rec["after"] == {"outcome": "removed", "catalogue_removed": False}

    def test_records_audit_action_as_error_on_conflict(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._seed(client)
        monkeypatch.setattr("hal0.api.routes.runner_images._slot_image_usage", lambda: {})
        self._fake_remove_image(monkeypatch, "in-use")

        resp = client.delete(f"/api/runner-images/{self.IMAGE_ID}/tags/0824")
        assert resp.status_code == 409

        activity = client.get(
            "/api/activity", params={"category": "runner_image", "action": "runner_image.rm"}
        ).json()
        rec = next(
            r
            for r in activity["records"]
            if r["target"] == "ghcr.io/hal0ai/hal0-runner-standard:0824"
        )
        assert rec["outcome"] == "error"

    def _records(self, client: TestClient) -> list[dict[str, Any]]:
        return client.get(
            "/api/activity", params={"category": "runner_image", "action": "runner_image.rm"}
        ).json()["records"]

    def test_unknown_image_404_is_audited(self, client: TestClient) -> None:
        """A refused delete on a completely unknown id is a real user-visible
        outcome too — it must leave an audit row, not just the 404 response.
        No ``image.image`` is known yet at this point, so the target falls
        back to ``<image_id>:<tag>``."""
        resp = client.delete(f"/api/runner-images/{self.IMAGE_ID}/tags/0824")
        assert resp.status_code == 404

        rec = next(r for r in self._records(client) if r["target"] == f"{self.IMAGE_ID}:0824")
        assert rec["outcome"] == "error"

    def test_unknown_tag_404_is_audited(self, client: TestClient) -> None:
        self._seed(client)

        resp = client.delete(f"/api/runner-images/{self.IMAGE_ID}/tags/9999")
        assert resp.status_code == 404

        rec = next(r for r in self._records(client) if r["target"] == f"{self.IMAGE_ID}:9999")
        assert rec["outcome"] == "error"

    def test_app_level_in_use_guard_409_is_audited(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The pre-check application-level in-use guard (fires BEFORE the
        seam is ever called) must also leave an audit row — this used to run
        entirely outside the ``record_action`` block."""
        self._seed(client)
        monkeypatch.setattr(
            "hal0.api.routes.runner_images._slot_image_usage",
            lambda: {"brain": "ghcr.io/hal0ai/hal0-runner-standard:0824"},
        )

        def _boom(ref: str):
            raise AssertionError("seam must not be reached when app-level guard fires")

        monkeypatch.setattr("hal0.providers.podman_mutate.remove_image", _boom)

        resp = client.delete(f"/api/runner-images/{self.IMAGE_ID}/tags/0824")
        assert resp.status_code == 409

        rec = next(
            r
            for r in self._records(client)
            if r["target"] == "ghcr.io/hal0ai/hal0-runner-standard:0824"
        )
        assert rec["outcome"] == "error"

    def test_pull_in_progress_409_is_audited(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from hal0.registry.runner_pull import make_job

        self._seed(client)
        job = make_job(self.IMAGE_ID, "ghcr.io/hal0ai/hal0-runner-standard:0824", tag="0824")
        job.state = "running"
        client.app.state.runner_image_pull_jobs[self.IMAGE_ID] = job

        resp = client.delete(f"/api/runner-images/{self.IMAGE_ID}/tags/0824")
        assert resp.status_code == 409

        rec = next(r for r in self._records(client) if r["target"] == f"{self.IMAGE_ID}:0824")
        assert rec["outcome"] == "error"

    # ── route ordering ───────────────────────────────────────────────────

    def test_delete_tag_route_ordering(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET single-image still resolves to the catch-all, and DELETE
        .../tags/{tag} resolves to the new route — neither swallows the
        other regardless of registration order (#2106)."""
        self._seed(client)

        get_resp = client.get(f"/api/runner-images/{self.IMAGE_ID}")
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == self.IMAGE_ID

        monkeypatch.setattr("hal0.api.routes.runner_images._slot_image_usage", lambda: {})
        self._fake_remove_image(monkeypatch, "removed")
        delete_resp = client.delete(f"/api/runner-images/{self.IMAGE_ID}/tags/0824")
        assert delete_resp.status_code == 200
        assert delete_resp.json() == {"removed": True, "outcome": "removed"}

        # GET still resolves correctly for the id afterward too.
        get_resp2 = client.get(f"/api/runner-images/{self.IMAGE_ID}")
        assert get_resp2.status_code == 200
        assert all(t["tag"] != "0824" for t in get_resp2.json()["tags"])
