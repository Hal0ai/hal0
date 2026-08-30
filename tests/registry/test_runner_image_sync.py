"""Tests for hal0.registry.runner_image_sync — GHCR probe + images.json merge.

Uses ``httpx.MockTransport`` (house pattern, see tests/registry/test_pull.py)
to stub both ghcr.io and raw.githubusercontent.com without touching the
network.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from hal0.registry.runner_image_store import RunnerImageStore
from hal0.registry.runner_image_sync import (
    IMAGES_JSON_URL,
    fetch_images_json,
    sync_runner_images,
)

_IMAGES_JSON = {
    "schema": "hal0.runner-images.v1",
    "images": [
        {
            "id": "cpu",
            "image": "ghcr.io/hal0ai/hal0-toolbox-cpu",
            "tag": "latest",
            "manifest_key": "toolbox-cpu",
            "ownership": "owned",
            "publish": "ci",
            "build": {"context": ".", "dockerfile": "Dockerfile.cpu"},
            "notes": "CPU-only toolbox image.",
        },
        {
            # no "id" — exercises the repo-path fallback
            "image": "ghcr.io/hal0ai/hal0-toolbox-flm",
            "tag": "v2",
            "manifest_key": "toolbox-flm",
            "ownership": "referenced",
            "publish": "external",
            "notes": "FLM/NPU toolbox, mirrored from upstream.",
        },
    ],
}

#: config 345 + layers 6000 + 6000 — what a size probe should sum to.
_MANIFEST_BODY = {
    "schemaVersion": 2,
    "config": {"size": 345},
    "layers": [{"size": 6000}, {"size": 6000}],
}
_MANIFEST_SIZE = 12345


def _route_handler(
    *,
    images_json: dict | None,
    ghcr_fail: set[str] | None = None,
    manifest_body: dict | None = None,
    child_manifests: dict[str, dict] | None = None,
    tags: list[str] | None = None,
    tags_fail: set[str] | None = None,
):
    """MockTransport handler for raw.githubusercontent.com + ghcr.io.

    ``manifest_body`` overrides the manifest returned for tag refs;
    ``child_manifests`` maps digest refs to bodies (multi-arch children).
    ``tags`` overrides the ``tags/list`` payload (default ``["latest"]``);
    ``tags_fail`` names repos whose ``tags/list`` call 500s (the manifest
    probe still succeeds — exercises the degrade-to-empty path).
    """
    ghcr_fail = ghcr_fail or set()
    child_manifests = child_manifests or {}
    tags = tags if tags is not None else ["latest"]
    tags_fail = tags_fail or set()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == IMAGES_JSON_URL:
            if images_json is None:
                return httpx.Response(404, content=b"not found")
            return httpx.Response(200, json=images_json)
        if url.startswith("https://ghcr.io/token"):
            scope = request.url.params.get("scope", "")
            repo = scope.split(":")[1] if ":" in scope else ""
            if repo in ghcr_fail:
                return httpx.Response(500, content=b"nope")
            return httpx.Response(200, json={"token": f"tok-{repo}"})
        if "/manifests/" in url:
            repo, _, ref = url.split("https://ghcr.io/v2/")[1].partition("/manifests/")
            if repo in ghcr_fail:
                return httpx.Response(500, content=b"nope")
            if ref in child_manifests:
                return httpx.Response(200, json=child_manifests[ref])
            return httpx.Response(
                200,
                headers={"docker-content-digest": f"sha256:{repo.replace('/', '-')}-{ref}"},
                json=manifest_body if manifest_body is not None else _MANIFEST_BODY,
            )
        if "/tags/list" in url:
            repo = url.split("https://ghcr.io/v2/")[1].split("/tags/list")[0]
            if repo in tags_fail:
                return httpx.Response(500, content=b"nope")
            return httpx.Response(200, json={"tags": tags})
        return httpx.Response(404, content=b"unrouted: " + url.encode())

    return handler


@pytest.mark.asyncio
async def test_fetch_images_json_parses_entries() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(_route_handler(images_json=_IMAGES_JSON))
    )
    try:
        entries, err = await fetch_images_json(client)
    finally:
        await client.aclose()
    assert err is None
    assert len(entries) == 2
    assert entries[0]["manifest_key"] == "toolbox-cpu"


@pytest.mark.asyncio
async def test_fetch_images_json_degrades_on_404() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(_route_handler(images_json=None)))
    try:
        entries, err = await fetch_images_json(client)
    finally:
        await client.aclose()
    assert entries == []
    assert err is not None


@pytest.mark.asyncio
async def test_fetch_images_json_degrades_on_malformed_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json{{{")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        entries, err = await fetch_images_json(client)
    finally:
        await client.aclose()
    assert entries == []
    assert err is not None


@pytest.mark.asyncio
async def test_sync_merges_ghcr_and_images_json(tmp_path: Path) -> None:
    store = RunnerImageStore(db_path=tmp_path / "hal0.db")
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(_route_handler(images_json=_IMAGES_JSON))
    )
    try:
        result = await sync_runner_images(store, client=client)
    finally:
        await client.aclose()

    assert result.images_json_ok is True
    assert result.probe_errors == {}
    assert len(result.images) == 2

    cpu = store.get("cpu")  # images.json "id" short name wins as row id
    assert cpu is not None
    assert cpu.image == "ghcr.io/hal0ai/hal0-toolbox-cpu"
    assert cpu.digest == "sha256:hal0ai-hal0-toolbox-cpu-latest"
    assert cpu.size_bytes == _MANIFEST_SIZE  # config + layer blob sizes, not doc length
    assert cpu.ownership == "owned"
    assert cpu.publish == "ci"
    assert cpu.notes == "CPU-only toolbox image."
    assert cpu.build == {"context": ".", "dockerfile": "Dockerfile.cpu"}

    flm = store.get("hal0ai/hal0-toolbox-flm")  # no "id" — repo-path fallback
    assert flm is not None
    assert flm.tag == "v2"
    assert flm.ownership == "referenced"


@pytest.mark.asyncio
async def test_sync_degrades_gracefully_on_malformed_images_json(tmp_path: Path) -> None:
    """A malformed images.json must not crash the sync — it just yields no rows."""
    store = RunnerImageStore(db_path=tmp_path / "hal0.db")
    client = httpx.AsyncClient(transport=httpx.MockTransport(_route_handler(images_json=None)))
    try:
        result = await sync_runner_images(store, client=client)
    finally:
        await client.aclose()

    assert result.images_json_ok is False
    assert result.images_json_error is not None
    assert result.images == []
    # No packages to probe (images.json is the source of which packages to
    # probe — see module docstring), so nothing raised and the store stays
    # empty rather than the sync blowing up.
    assert store.list() == []


@pytest.mark.asyncio
async def test_sync_one_bad_package_does_not_abort_the_rest(tmp_path: Path) -> None:
    store = RunnerImageStore(db_path=tmp_path / "hal0.db")
    handler = _route_handler(images_json=_IMAGES_JSON, ghcr_fail={"hal0ai/hal0-toolbox-flm"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await sync_runner_images(store, client=client)
    finally:
        await client.aclose()

    assert "hal0ai/hal0-toolbox-flm" in result.probe_errors
    assert store.get("cpu") is not None
    assert store.get("hal0ai/hal0-toolbox-flm") is None


@pytest.mark.asyncio
async def test_sync_multiarch_index_size_summed_from_child(tmp_path: Path) -> None:
    """An OCI index resolves size via its linux/amd64 child manifest."""
    index = {
        "schemaVersion": 2,
        "manifests": [
            {"digest": "sha256:attest", "platform": {"os": "unknown", "architecture": "unknown"}},
            {"digest": "sha256:amd64child", "platform": {"os": "linux", "architecture": "amd64"}},
        ],
    }
    child = {"schemaVersion": 2, "config": {"size": 100}, "layers": [{"size": 900}]}
    store = RunnerImageStore(db_path=tmp_path / "hal0.db")
    handler = _route_handler(
        images_json=_IMAGES_JSON,
        manifest_body=index,
        child_manifests={"sha256:amd64child": child},
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await sync_runner_images(store, client=client)
    finally:
        await client.aclose()

    assert result.probe_errors == {}
    cpu = store.get("cpu")
    assert cpu is not None
    assert cpu.size_bytes == 1000
    # digest stays the top-level (index) one — what a pull-by-digest resolves
    assert cpu.digest == "sha256:hal0ai-hal0-toolbox-cpu-latest"


@pytest.mark.asyncio
async def test_sync_two_entries_sharing_one_repo_stay_distinct(tmp_path: Path) -> None:
    """rocmfpx/rocmfpx-hy3 pattern: one GHCR repo, two tags, two rows."""
    images_json = {
        "schema": "hal0.runner-images.v1",
        "images": [
            {"id": "rocmfpx", "image": "ghcr.io/hal0ai/hal0-rocmfpx", "tag": "c077206"},
            {"id": "rocmfpx-hy3", "image": "ghcr.io/hal0ai/hal0-rocmfpx", "tag": "ifp2-hyv3"},
        ],
    }
    store = RunnerImageStore(db_path=tmp_path / "hal0.db")
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(_route_handler(images_json=images_json))
    )
    try:
        result = await sync_runner_images(store, client=client)
    finally:
        await client.aclose()

    assert result.probe_errors == {}
    assert len(result.images) == 2
    base = store.get("rocmfpx")
    hy3 = store.get("rocmfpx-hy3")
    assert base is not None and base.tag == "c077206"
    assert hy3 is not None and hy3.tag == "ifp2-hyv3"
    assert base.image == hy3.image == "ghcr.io/hal0ai/hal0-rocmfpx"


@pytest.mark.asyncio
async def test_sync_prunes_stale_rows_but_keeps_downloaded(tmp_path: Path) -> None:
    """Rows delisted from images.json vanish on the next good sync — unless
    a local pull landed, in which case the row survives."""
    from hal0.registry.runner_image import RunnerImage

    store = RunnerImageStore(db_path=tmp_path / "hal0.db")
    # Stale pre-rename row (old repo-path id for what is now id "cpu"):
    store.upsert(RunnerImage(id="hal0ai/hal0-toolbox-cpu", image="ghcr.io/hal0ai/hal0-toolbox-cpu"))
    # Delisted but locally downloaded — must survive the prune:
    store.upsert(
        RunnerImage(
            id="hal0ai/old-downloaded",
            image="ghcr.io/hal0ai/old-downloaded",
            local_path="/var/lib/hal0/runner-images/old-downloaded.json",
        )
    )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(_route_handler(images_json=_IMAGES_JSON))
    )
    try:
        await sync_runner_images(store, client=client)
    finally:
        await client.aclose()

    ids = {i.id for i in store.list()}
    assert ids == {"cpu", "hal0ai/hal0-toolbox-flm", "hal0ai/old-downloaded"}


# ── tag tracking (runner-image-catalogue v2) ────────────────────────────────


@pytest.mark.asyncio
async def test_pinned_entry_keeps_tag_but_gains_available_tags(tmp_path: Path) -> None:
    """A pinned images.json entry keeps its pin as the headline ``tag`` but
    still stores the full newest-first ``available_tags`` list."""
    store = RunnerImageStore(db_path=tmp_path / "hal0.db")
    handler = _route_handler(images_json=_IMAGES_JSON, tags=["0822", "latest", "0824"])
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await sync_runner_images(store, client=client)
    finally:
        await client.aclose()

    assert result.probe_errors == {}
    cpu = store.get("cpu")
    assert cpu is not None
    assert cpu.tag == "latest"  # the images.json pin stays the headline
    assert cpu.available_tags == ["0824", "0822", "latest"]


@pytest.mark.asyncio
async def test_unpinned_entry_headline_is_newest_tag(tmp_path: Path) -> None:
    """No ``tag`` pin → the headline is the newest sorted tag, not ``latest``."""
    images_json = {
        "schema": "hal0.runner-images.v1",
        "images": [
            {"id": "combined", "image": "ghcr.io/hal0ai/hal0-combined"},
        ],
    }
    store = RunnerImageStore(db_path=tmp_path / "hal0.db")
    handler = _route_handler(images_json=images_json, tags=["0822", "latest", "0824"])
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await sync_runner_images(store, client=client)
    finally:
        await client.aclose()

    assert result.probe_errors == {}
    row = store.get("combined")
    assert row is not None
    assert row.tag == "0824"
    assert row.available_tags == ["0824", "0822", "latest"]


@pytest.mark.asyncio
async def test_tags_list_failure_degrades_to_empty_available_tags(tmp_path: Path) -> None:
    """A failing ``tags/list`` must not fail the row: the pinned headline
    still resolves and ``available_tags`` degrades to ``[]``."""
    store = RunnerImageStore(db_path=tmp_path / "hal0.db")
    handler = _route_handler(images_json=_IMAGES_JSON, tags_fail={"hal0ai/hal0-toolbox-cpu"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await sync_runner_images(store, client=client)
    finally:
        await client.aclose()

    assert "cpu" not in result.probe_errors
    cpu = store.get("cpu")
    assert cpu is not None
    assert cpu.tag == "latest"
    assert cpu.available_tags == []


@pytest.mark.asyncio
async def test_unpinned_entry_tags_list_failure_falls_back_to_latest(tmp_path: Path) -> None:
    """No pin AND no tag list → the headline falls back to ``latest``."""
    images_json = {
        "schema": "hal0.runner-images.v1",
        "images": [
            {"id": "combined", "image": "ghcr.io/hal0ai/hal0-combined"},
        ],
    }
    store = RunnerImageStore(db_path=tmp_path / "hal0.db")
    handler = _route_handler(images_json=images_json, tags_fail={"hal0ai/hal0-combined"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await sync_runner_images(store, client=client)
    finally:
        await client.aclose()

    assert result.probe_errors == {}
    row = store.get("combined")
    assert row is not None
    assert row.tag == "latest"
    assert row.available_tags == []


@pytest.mark.asyncio
async def test_failed_images_json_never_prunes(tmp_path: Path) -> None:
    from hal0.registry.runner_image import RunnerImage

    store = RunnerImageStore(db_path=tmp_path / "hal0.db")
    store.upsert(RunnerImage(id="cpu", image="ghcr.io/hal0ai/hal0-toolbox-cpu"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(_route_handler(images_json=None)))
    try:
        result = await sync_runner_images(store, client=client)
    finally:
        await client.aclose()

    assert result.images_json_ok is False
    assert store.get("cpu") is not None


# ── noise-tag filtering (cosign signatures + per-commit CI tags) ────────────


@pytest.mark.asyncio
async def test_sync_excludes_cosign_and_ci_commit_tags_from_available_tags(
    tmp_path: Path,
) -> None:
    """CI-built families (vulkan/rocm toolboxes) push a cosign ``.sig``
    artifact and a per-commit ``sha-<7hex>`` tag alongside every real
    build. Neither should ever reach the catalogue's ``available_tags`` —
    that's the tag-picker flood + false "newer" candidate observed live
    against ghcr.io/hal0ai packages."""
    store = RunnerImageStore(db_path=tmp_path / "hal0.db")
    noisy_tags = [
        "latest",
        "0824",
        "sha256-" + "a" * 64 + ".sig",
        "sha256-" + "b" * 64 + ".att",
        "sha-abc1234",
        "sha-" + "c" * 40,
    ]
    handler = _route_handler(images_json=_IMAGES_JSON, tags=noisy_tags)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await sync_runner_images(store, client=client)
    finally:
        await client.aclose()

    assert result.probe_errors == {}
    cpu = store.get("cpu")
    assert cpu is not None
    assert cpu.available_tags == ["0824", "latest"]
    for noise in noisy_tags[2:]:
        assert noise not in cpu.available_tags


@pytest.mark.asyncio
async def test_sync_unpinned_headline_never_resolves_to_a_noise_tag(tmp_path: Path) -> None:
    """The unpinned-headline fallback (newest of ``available_tags``) must
    never land on a cosign/CI tag even when GHCR lists it as the most
    recently pushed tag in registry order."""
    images_json = {
        "schema": "hal0.runner-images.v1",
        "images": [
            {"id": "combined", "image": "ghcr.io/hal0ai/hal0-combined"},
        ],
    }
    store = RunnerImageStore(db_path=tmp_path / "hal0.db")
    handler = _route_handler(
        images_json=images_json,
        tags=["sha-abc1234", "main", "sha256-" + "d" * 64 + ".sig"],
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await sync_runner_images(store, client=client)
    finally:
        await client.aclose()

    assert result.probe_errors == {}
    row = store.get("combined")
    assert row is not None
    assert row.tag == "main"
    assert row.available_tags == ["main"]


# ── per-tag digest resolution (runner-image-catalogue v3) ────────────────────


@pytest.mark.asyncio
async def test_probe_resolves_digest_per_tag(tmp_path: Path) -> None:
    """probe_ghcr_package resolves a digest for every catalogued tag."""
    from hal0.registry.runner_image_sync import probe_ghcr_package

    handler = _route_handler(images_json=_IMAGES_JSON, tags=["0826", "0824"])
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        image = await probe_ghcr_package("hal0ai/hal0-toolbox-cpu", client=client)
    finally:
        await client.aclose()

    assert [t.tag for t in image.tags] == ["0826", "0824"]
    assert image.tags[0].digest == "sha256:hal0ai-hal0-toolbox-cpu-0826"
    assert image.tags[1].digest == "sha256:hal0ai-hal0-toolbox-cpu-0824"
    assert image.digest == "sha256:hal0ai-hal0-toolbox-cpu-0826"  # headline unchanged


@pytest.mark.asyncio
async def test_per_tag_probe_failure_degrades_to_none(tmp_path: Path) -> None:
    """When a per-tag manifest probe fails, that tag's digest is None but the row survives."""

    from hal0.registry.runner_image_sync import probe_ghcr_package

    def handler_with_failing_tag(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == IMAGES_JSON_URL:
            return httpx.Response(200, json=_IMAGES_JSON)
        if url.startswith("https://ghcr.io/token"):
            scope = request.url.params.get("scope", "")
            repo = scope.split(":")[1] if ":" in scope else ""
            return httpx.Response(200, json={"token": f"tok-{repo}"})
        if "/manifests/" in url:
            repo, _, ref = url.split("https://ghcr.io/v2/")[1].partition("/manifests/")
            # Fail on 0824 manifest probe, succeed on 0826
            if ref == "0824":
                return httpx.Response(500, content=b"manifest probe failed")
            return httpx.Response(
                200,
                headers={"docker-content-digest": f"sha256:{repo.replace('/', '-')}-{ref}"},
                json=_MANIFEST_BODY,
            )
        if "/tags/list" in url:
            return httpx.Response(200, json={"tags": ["0826", "0824"]})
        return httpx.Response(404, content=b"unrouted: " + url.encode())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler_with_failing_tag))
    try:
        image = await probe_ghcr_package("hal0ai/hal0-toolbox-cpu", client=client)
    finally:
        await client.aclose()

    assert [t.tag for t in image.tags] == ["0826", "0824"]
    assert image.tags[0].digest == "sha256:hal0ai-hal0-toolbox-cpu-0826"  # success
    assert image.tags[1].digest is None  # failed, degraded to None
    assert image.tags[1].tag == "0824"  # row survives
    assert image.digest == "sha256:hal0ai-hal0-toolbox-cpu-0826"


@pytest.mark.asyncio
async def test_sync_persists_tags(tmp_path: Path) -> None:
    """sync_runner_images calls store.set_tags after upsert."""
    store = RunnerImageStore(db_path=tmp_path / "hal0.db")
    handler = _route_handler(images_json=_IMAGES_JSON, tags=["0826", "0824"])
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await sync_runner_images(store, client=client)
    finally:
        await client.aclose()

    assert result.probe_errors == {}
    cpu = store.get("cpu")
    assert cpu is not None
    # After sync, the tags should be persisted and loaded back.
    # CPU has pinned tag "latest" (from images.json), which is not in available
    # tags ["0826", "0824"], so it gets prepended. Expected: ["latest", "0826", "0824"].
    assert len(cpu.tags) == 3
    assert [t.tag for t in cpu.tags] == ["latest", "0826", "0824"]
    assert cpu.tags[0].digest == "sha256:hal0ai-hal0-toolbox-cpu-latest"
    assert cpu.tags[1].digest == "sha256:hal0ai-hal0-toolbox-cpu-0826"
    assert cpu.tags[2].digest == "sha256:hal0ai-hal0-toolbox-cpu-0824"
