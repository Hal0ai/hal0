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
):
    """MockTransport handler for raw.githubusercontent.com + ghcr.io.

    ``manifest_body`` overrides the manifest returned for tag refs;
    ``child_manifests`` maps digest refs to bodies (multi-arch children).
    """
    ghcr_fail = ghcr_fail or set()
    child_manifests = child_manifests or {}

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
            return httpx.Response(200, json={"tags": ["latest"]})
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
