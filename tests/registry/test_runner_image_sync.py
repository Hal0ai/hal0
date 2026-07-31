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
            "image": "ghcr.io/hal0ai/hal0-toolbox-cpu",
            "tag": "latest",
            "manifest_key": "toolbox-cpu",
            "ownership": "owned",
            "publish": "ci",
            "build": {"context": ".", "dockerfile": "Dockerfile.cpu"},
            "notes": "CPU-only toolbox image.",
        },
        {
            "image": "ghcr.io/hal0ai/hal0-toolbox-flm",
            "tag": "v2",
            "manifest_key": "toolbox-flm",
            "ownership": "referenced",
            "publish": "external",
            "notes": "FLM/NPU toolbox, mirrored from upstream.",
        },
    ],
}


def _route_handler(*, images_json: dict | None, ghcr_fail: set[str] | None = None):
    ghcr_fail = ghcr_fail or set()

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
            repo = url.split("https://ghcr.io/v2/")[1].split("/manifests/")[0]
            if repo in ghcr_fail:
                return httpx.Response(500, content=b"nope")
            return httpx.Response(
                200,
                headers={
                    "docker-content-digest": f"sha256:{repo.replace('/', '-')}",
                    "content-length": "12345",
                },
            )
        if "/tags/list" in url:
            return httpx.Response(200, json={"tags": ["latest"]})
        return httpx.Response(404, content=b"unrouted: " + url.encode())

    return handler


@pytest.mark.asyncio
async def test_fetch_images_json_parses_entries() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(_route_handler(images_json=_IMAGES_JSON)))
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
    client = httpx.AsyncClient(transport=httpx.MockTransport(_route_handler(images_json=_IMAGES_JSON)))
    try:
        result = await sync_runner_images(store, client=client)
    finally:
        await client.aclose()

    assert result.images_json_ok is True
    assert result.probe_errors == {}
    assert len(result.images) == 2

    cpu = store.get("hal0ai/hal0-toolbox-cpu")
    assert cpu is not None
    assert cpu.digest == "sha256:hal0ai-hal0-toolbox-cpu"
    assert cpu.size_bytes == 12345
    assert cpu.ownership == "owned"
    assert cpu.publish == "ci"
    assert cpu.notes == "CPU-only toolbox image."
    assert cpu.build == {"context": ".", "dockerfile": "Dockerfile.cpu"}

    flm = store.get("hal0ai/hal0-toolbox-flm")
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
    assert store.get("hal0ai/hal0-toolbox-cpu") is not None
    assert store.get("hal0ai/hal0-toolbox-flm") is None
