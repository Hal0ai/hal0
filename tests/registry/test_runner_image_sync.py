"""Tests for hal0.registry.runner_image_sync — GHCR probe + images.json v2 merge.

Uses ``httpx.MockTransport`` (house pattern, see tests/registry/test_pull.py)
to stub both ghcr.io and raw.githubusercontent.com without touching the
network.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import httpx
import pytest

from hal0.registry.runner_image import RunnerImage
from hal0.registry.runner_image_store import RunnerImageStore
from hal0.registry.runner_image_sync import (
    _MAX_EXTRA_TAGS,
    IMAGES_JSON_URL,
    SyncResult,
    catalogue_row,
    fetch_images_json,
    probe_ghcr_package,
    provenance_from_labels,
    sync_catalogue_blocking,
    sync_runner_images,
)

_CPU_DIGEST = "sha256:" + "a" * 64
_CPU_REPO = "hal0ai/hal0-runner-cpu"
_FLM_REPO = "hal0ai/hal0-runner-flm"

_CPU_ENTRY: dict[str, Any] = {
    "id": "cpu",
    "title": "CPU",
    "summary": "CPU-only llama.cpp server.",
    "image": "ghcr.io/hal0ai/hal0-runner-cpu",
    "tag": "b11552-r1",
    "digest": _CPU_DIGEST,
    "backends": ["cpu"],
    "lanes_gated": ["cpu"],
    "family": "llama-server",
    "tier": "default",
}

#: No digest in the entry: the probe's registry digest fills the gap.
_FLM_ENTRY: dict[str, Any] = {
    "id": "flm",
    "title": "FastFlowLM",
    "summary": "NPU runtime.",
    "image": "ghcr.io/hal0ai/hal0-runner-flm",
    "tag": "v0.9.1",
    "backends": ["npu"],
    "lanes_gated": [],
    "family": "flm",
    "tier": "default",
}

_IMAGES_JSON = {
    "schema": "hal0.runner-images.v2",
    "images": [_CPU_ENTRY, _FLM_ENTRY],
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
    manifest_fail: set[str] | None = None,
    manifest_body: dict | None = None,
    child_manifests: dict[str, dict] | None = None,
    tags: list[str] | None = None,
    tags_fail: set[str] | None = None,
):
    """MockTransport handler for raw.githubusercontent.com + ghcr.io.

    ``ghcr_fail`` names repos whose token call 500s (the whole probe fails);
    ``manifest_fail`` names repos whose manifest GETs 500s;
    ``manifest_body`` overrides the manifest returned for tag refs;
    ``child_manifests`` maps digest refs to bodies (multi-arch children).
    ``tags`` overrides the ``tags/list`` payload (default ``["latest"]``);
    ``tags_fail`` names repos whose ``tags/list`` call 500s (the manifest
    probe still succeeds — exercises the degrade-to-empty path).
    """
    ghcr_fail = ghcr_fail or set()
    manifest_fail = manifest_fail or set()
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
            if repo in ghcr_fail or repo in manifest_fail:
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


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _store(tmp_path: Path) -> RunnerImageStore:
    return RunnerImageStore(db_path=tmp_path / "hal0.db")


async def _sync(store: RunnerImageStore, handler) -> SyncResult:
    client = _client(handler)
    try:
        return await sync_runner_images(store, client=client)
    finally:
        await client.aclose()


def _cpu_row(**kw: Any) -> RunnerImage:
    base: dict[str, Any] = {
        "id": "cpu",
        "image": "ghcr.io/hal0ai/hal0-runner-cpu",
        "tag": "b11552-r1",
        "digest": _CPU_DIGEST,
    }
    base.update(kw)
    return RunnerImage(**base)


# ── fetch_images_json ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_images_json_parses_entries() -> None:
    client = _client(_route_handler(images_json=_IMAGES_JSON))
    try:
        entries, err = await fetch_images_json(client)
    finally:
        await client.aclose()
    assert err is None
    assert [e["id"] for e in entries] == ["cpu", "flm"]
    assert entries[0]["digest"] == _CPU_DIGEST


@pytest.mark.asyncio
async def test_fetch_images_json_degrades_on_404() -> None:
    client = _client(_route_handler(images_json=None))
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

    client = _client(handler)
    try:
        entries, err = await fetch_images_json(client)
    finally:
        await client.aclose()
    assert entries == []
    assert err is not None


@pytest.mark.asyncio
async def test_fetch_images_json_rejects_v1_schema() -> None:
    """A v1 payload is a fetch error, not a best-effort parse."""
    v1 = {
        "schema": "hal0.runner-images.v1",
        "images": [{"id": "cpu", "image": "ghcr.io/hal0ai/hal0-toolbox-cpu", "tag": "latest"}],
    }
    client = _client(_route_handler(images_json=v1))
    try:
        entries, err = await fetch_images_json(client)
    finally:
        await client.aclose()
    assert entries == []
    assert err is not None
    assert "hal0.runner-images.v1" in err
    assert "hal0.runner-images.v2" in err


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        ["not", "an", "object"],
        {"images": [_CPU_ENTRY]},  # no schema
        {"schema": "hal0.runner-images.v2"},  # no images array
        {"schema": "hal0.runner-images.v2", "images": {"cpu": _CPU_ENTRY}},  # images not a list
    ],
)
async def test_fetch_images_json_rejects_wrong_shapes(payload: object) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    client = _client(handler)
    try:
        entries, err = await fetch_images_json(client)
    finally:
        await client.aclose()
    assert entries == []
    assert err is not None


@pytest.mark.asyncio
async def test_fetch_images_json_drops_non_object_entries() -> None:
    payload = {"schema": "hal0.runner-images.v2", "images": [_CPU_ENTRY, "junk", 7, None]}
    client = _client(_route_handler(images_json=payload))
    try:
        entries, err = await fetch_images_json(client)
    finally:
        await client.aclose()
    assert err is None
    assert [e["id"] for e in entries] == ["cpu"]


# ── catalogue_row (pure entry → RunnerImage mapping) ────────────────────────


def test_catalogue_row_maps_every_field() -> None:
    row, reason = catalogue_row(_CPU_ENTRY)
    assert reason is None
    assert row is not None
    assert row.id == "cpu"
    assert row.image == "ghcr.io/hal0ai/hal0-runner-cpu"
    assert row.tag == "b11552-r1"
    assert row.digest == _CPU_DIGEST
    assert row.title == "CPU"
    assert row.summary == "CPU-only llama.cpp server."
    assert row.family == "llama-server"
    assert row.tier == "default"
    assert row.backends == ["cpu"]
    assert row.lanes_gated == ["cpu"]
    # Probe-owned facts stay empty until the GHCR probe fills them.
    assert row.size_bytes is None
    assert row.available_tags == []
    assert row.tags == []


def test_catalogue_row_optional_fields_default_to_absent() -> None:
    row, reason = catalogue_row({"id": "cpu", "image": "ghcr.io/x/y", "tag": "t1"})
    assert reason is None
    assert row is not None
    assert row.digest is None
    assert row.title is None
    assert row.summary is None
    assert row.family is None
    assert row.tier is None
    assert row.backends == []
    assert row.lanes_gated == []


def test_catalogue_row_strips_whitespace_and_junk_list_members() -> None:
    row, reason = catalogue_row(
        {
            "id": " cpu ",
            "image": " ghcr.io/x/y ",
            "tag": " t1 ",
            "digest": "   ",
            "title": " CPU ",
            "backends": [None, "", "  ", " cpu ", 7],
            "lanes_gated": "cpu,vulkan",  # not a list -> not declared
        }
    )
    assert reason is None
    assert row is not None
    assert (row.id, row.image, row.tag, row.title) == ("cpu", "ghcr.io/x/y", "t1", "CPU")
    assert row.digest is None  # blank string reads as absent
    assert row.backends == ["cpu"]  # junk members filtered, order kept
    assert row.lanes_gated == []


@pytest.mark.parametrize(
    ("entry", "needle"),
    [
        ({"image": "ghcr.io/x/y", "tag": "t"}, "no id"),
        ({"id": "", "image": "ghcr.io/x/y", "tag": "t"}, "no id"),
        ({"id": "rocmfpx", "image": "ghcr.io/x/y", "tag": "t"}, "not a runner"),
        ({"id": "hal0ai/hal0-toolbox-cpu", "image": "ghcr.io/x/y", "tag": "t"}, "not a runner"),
        ({"id": "cpu", "tag": "t"}, "image and tag"),
        ({"id": "cpu", "image": "ghcr.io/x/y"}, "image and tag"),
        ({"id": "cpu", "image": "ghcr.io/x/y", "tag": ""}, "image and tag"),
        ({"id": "cpu", "image": "ghcr.io/x/y:latest", "tag": "t"}, "bare repo"),
        ({"id": "cpu", "image": "ghcr.io/x/y@sha256:" + "a" * 64, "tag": "t"}, "bare repo"),
    ],
)
def test_catalogue_row_rejects_invalid_entries(entry: dict[str, Any], needle: str) -> None:
    row, reason = catalogue_row(entry)
    assert row is None
    assert reason is not None
    assert needle in reason


def test_catalogue_row_accepts_registry_port_in_host() -> None:
    """A ``host:port`` registry prefix is not a tag (only the last path segment is)."""
    row, reason = catalogue_row({"id": "cpu", "image": "localhost:5000/x/y", "tag": "t"})
    assert reason is None
    assert row is not None
    assert row.image == "localhost:5000/x/y"


# ── sync: catalogue merge ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_merges_ghcr_and_images_json(tmp_path: Path) -> None:
    store = _store(tmp_path)
    result = await _sync(store, _route_handler(images_json=_IMAGES_JSON))

    assert result.images_json_ok is True
    assert result.images_json_error is None
    assert result.probe_errors == {}
    assert result.skipped == {}
    assert sorted(i.id for i in result.images) == ["cpu", "flm"]

    cpu = store.get("cpu")
    assert cpu is not None
    assert cpu.image == "ghcr.io/hal0ai/hal0-runner-cpu"
    assert cpu.tag == "b11552-r1"
    assert cpu.size_bytes == _MANIFEST_SIZE  # config + layer blob sizes, not doc length
    assert cpu.title == "CPU"
    assert cpu.summary == "CPU-only llama.cpp server."
    assert cpu.family == "llama-server"
    assert cpu.tier == "default"
    assert cpu.backends == ["cpu"]
    assert cpu.lanes_gated == ["cpu"]

    flm = store.get("flm")
    assert flm is not None
    assert flm.tag == "v0.9.1"
    assert flm.backends == ["npu"]
    assert flm.lanes_gated == []


@pytest.mark.asyncio
async def test_catalogue_digest_wins_over_registry_digest(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The catalogue's digest is authoritative; a registry that disagrees is
    logged, never adopted. An entry with no digest takes the registry's."""
    store = _store(tmp_path)
    with caplog.at_level(logging.WARNING, logger="hal0.registry.runner_image_sync"):
        result = await _sync(store, _route_handler(images_json=_IMAGES_JSON))

    assert result.probe_errors == {}
    cpu = store.get("cpu")
    assert cpu is not None
    assert cpu.digest == _CPU_DIGEST  # not sha256:hal0ai-hal0-runner-cpu-b11552-r1
    assert next(t for t in cpu.tags if t.tag == "b11552-r1").digest == _CPU_DIGEST
    assert any("digest_mismatch" in r.getMessage() for r in caplog.records)

    flm = store.get("flm")
    assert flm is not None
    assert flm.digest == "sha256:hal0ai-hal0-runner-flm-v0.9.1"  # registry fills the gap


@pytest.mark.asyncio
async def test_matching_registry_digest_logs_no_mismatch(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    images_json = {
        "schema": "hal0.runner-images.v2",
        "images": [{**_CPU_ENTRY, "digest": "sha256:hal0ai-hal0-runner-cpu-b11552-r1"}],
    }
    with caplog.at_level(logging.WARNING, logger="hal0.registry.runner_image_sync"):
        await _sync(_store(tmp_path), _route_handler(images_json=images_json))
    assert not any("digest_mismatch" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_malformed_list_metadata_degrades_to_not_declared(tmp_path: Path) -> None:
    """Bad backends/lanes_gated shapes never fail the row: a non-list degrades
    to ``[]``; junk members are filtered, order kept."""
    images_json = {
        "schema": "hal0.runner-images.v2",
        "images": [
            {**_CPU_ENTRY, "backends": "rocm,vulkan", "lanes_gated": 42, "family": 7},
            {**_FLM_ENTRY, "backends": [None, "", "  ", "npu"]},
        ],
    }
    store = _store(tmp_path)
    result = await _sync(store, _route_handler(images_json=images_json))

    assert result.probe_errors == {}
    cpu = store.get("cpu")
    assert cpu is not None
    assert cpu.backends == []
    assert cpu.lanes_gated == []
    assert cpu.family is None
    flm = store.get("flm")
    assert flm is not None
    assert flm.backends == ["npu"]


@pytest.mark.asyncio
async def test_sync_degrades_gracefully_on_missing_images_json(tmp_path: Path) -> None:
    """An unreachable images.json must not crash the sync — it just yields no rows."""
    store = _store(tmp_path)
    result = await _sync(store, _route_handler(images_json=None))

    assert result.images_json_ok is False
    assert result.images_json_error is not None
    assert result.images == []
    assert store.list() == []


@pytest.mark.asyncio
async def test_sync_v1_payload_is_a_fetch_error_and_never_prunes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert(_cpu_row())
    v1 = {
        "schema": "hal0.runner-images.v1",
        "images": [{"id": "cpu", "image": "ghcr.io/hal0ai/hal0-toolbox-cpu", "tag": "latest"}],
    }
    result = await _sync(store, _route_handler(images_json=v1))

    assert result.images_json_ok is False
    assert result.images_json_error is not None
    assert "v1" in result.images_json_error
    assert result.images == []
    kept = store.get("cpu")
    assert kept is not None  # not pruned, not rewritten
    assert kept.image == "ghcr.io/hal0ai/hal0-runner-cpu"


@pytest.mark.asyncio
async def test_failed_images_json_never_prunes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert(_cpu_row())
    result = await _sync(store, _route_handler(images_json=None))

    assert result.images_json_ok is False
    assert store.get("cpu") is not None


# ── sync: skipped entries ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_runner_id_is_skipped_not_stored(tmp_path: Path) -> None:
    images_json = {
        "schema": "hal0.runner-images.v2",
        "images": [
            _CPU_ENTRY,
            {**_CPU_ENTRY, "id": "rocmfpx", "image": "ghcr.io/hal0ai/hal0-rocmfpx"},
            {**_CPU_ENTRY, "id": "hal0ai/hal0-toolbox-flm"},
        ],
    }
    store = _store(tmp_path)
    result = await _sync(store, _route_handler(images_json=images_json))

    assert result.images_json_ok is True
    assert set(result.skipped) == {"rocmfpx", "hal0ai/hal0-toolbox-flm"}
    assert "not a runner" in result.skipped["rocmfpx"]
    assert [i.id for i in store.list()] == ["cpu"]


@pytest.mark.asyncio
async def test_entry_without_id_is_skipped(tmp_path: Path) -> None:
    images_json = {
        "schema": "hal0.runner-images.v2",
        "images": [_CPU_ENTRY, {"image": "ghcr.io/hal0ai/hal0-runner-flm", "tag": "v1"}],
    }
    store = _store(tmp_path)
    result = await _sync(store, _route_handler(images_json=images_json))

    assert list(result.skipped) == ["?"]
    assert [i.id for i in store.list()] == ["cpu"]


@pytest.mark.asyncio
async def test_invalid_image_with_tag_is_skipped(tmp_path: Path) -> None:
    """``image`` must be a bare repo: tag and digest are separate fields."""
    images_json = {
        "schema": "hal0.runner-images.v2",
        "images": [
            _CPU_ENTRY,
            {**_FLM_ENTRY, "image": "ghcr.io/hal0ai/hal0-runner-flm:v0.9.1"},
        ],
    }
    store = _store(tmp_path)
    result = await _sync(store, _route_handler(images_json=images_json))

    assert set(result.skipped) == {"flm"}
    assert "bare repo" in result.skipped["flm"]
    assert store.get("flm") is None
    assert store.get("cpu") is not None


@pytest.mark.asyncio
async def test_entry_missing_tag_is_skipped(tmp_path: Path) -> None:
    """v2 always carries a tag; there is no newest-tag fallback."""
    entry = {k: v for k, v in _FLM_ENTRY.items() if k != "tag"}
    images_json = {"schema": "hal0.runner-images.v2", "images": [_CPU_ENTRY, entry]}
    store = _store(tmp_path)
    result = await _sync(store, _route_handler(images_json=images_json, tags=["0824"]))

    assert set(result.skipped) == {"flm"}
    assert store.get("flm") is None


# ── sync: probe failure keeps the row ───────────────────────────────────────


@pytest.mark.asyncio
async def test_probe_failure_keeps_the_catalogue_row(tmp_path: Path) -> None:
    """A failed GHCR probe records the error but never drops the row: the
    catalogue's own facts are still written, and the other runners sync."""
    store = _store(tmp_path)
    result = await _sync(store, _route_handler(images_json=_IMAGES_JSON, ghcr_fail={_FLM_REPO}))

    assert set(result.probe_errors) == {"flm"}
    assert sorted(i.id for i in result.images) == ["cpu", "flm"]
    flm = store.get("flm")
    assert flm is not None
    assert flm.image == "ghcr.io/hal0ai/hal0-runner-flm"
    assert flm.tag == "v0.9.1"
    assert flm.title == "FastFlowLM"
    assert flm.backends == ["npu"]
    assert flm.size_bytes is None  # nothing probed
    cpu = store.get("cpu")
    assert cpu is not None
    assert cpu.size_bytes == _MANIFEST_SIZE


@pytest.mark.asyncio
async def test_headline_manifest_failure_keeps_the_catalogue_row(tmp_path: Path) -> None:
    store = _store(tmp_path)
    result = await _sync(store, _route_handler(images_json=_IMAGES_JSON, manifest_fail={_CPU_REPO}))

    assert set(result.probe_errors) == {"cpu"}
    cpu = store.get("cpu")
    assert cpu is not None
    assert cpu.tag == "b11552-r1"
    assert cpu.digest == _CPU_DIGEST  # the catalogue's digest survives the failed probe
    assert store.get("flm") is not None


@pytest.mark.asyncio
async def test_probe_failure_on_a_previously_synced_row_keeps_it(tmp_path: Path) -> None:
    """A runner that synced once and then fails its probe (transient GHCR
    500) is still in the catalogue with the catalogue's current facts."""
    store = _store(tmp_path)
    first = await _sync(store, _route_handler(images_json=_IMAGES_JSON, tags=["0826", "0824"]))
    assert first.probe_errors == {}

    second = await _sync(
        store,
        _route_handler(images_json=_IMAGES_JSON, ghcr_fail={_CPU_REPO}, tags=["0826", "0824"]),
    )

    assert "cpu" in second.probe_errors
    cpu = store.get("cpu")
    assert cpu is not None
    assert cpu.tag == "b11552-r1"
    assert cpu.digest == _CPU_DIGEST
    assert cpu.title == "CPU"


@pytest.mark.asyncio
async def test_probe_failure_keeps_the_last_good_registry_facts(tmp_path: Path) -> None:
    """A failed probe refreshes only the catalogue facts: the size, tag list
    and per-tag digests the last good probe recorded survive."""
    store = _store(tmp_path)
    await _sync(store, _route_handler(images_json=_IMAGES_JSON, tags=["0826", "0824"]))
    before = store.get("cpu")
    assert before is not None and before.tags and before.available_tags

    await _sync(
        store,
        _route_handler(images_json=_IMAGES_JSON, ghcr_fail={_CPU_REPO}, tags=["0826", "0824"]),
    )
    after = store.get("cpu")
    assert after is not None
    assert after.size_bytes == before.size_bytes
    assert after.available_tags == before.available_tags
    assert [t.tag for t in after.tags] == [t.tag for t in before.tags]


@pytest.mark.asyncio
async def test_sync_keeps_local_download_state(tmp_path: Path) -> None:
    store = _store(tmp_path)
    await _sync(store, _route_handler(images_json=_IMAGES_JSON))
    store.set_local_state(
        "cpu", local_path="/var/lib/hal0/runner-images/cpu.json", downloaded_at="t"
    )
    await _sync(store, _route_handler(images_json=_IMAGES_JSON))
    cpu = store.get("cpu")
    assert cpu is not None and cpu.local_path == "/var/lib/hal0/runner-images/cpu.json"
    assert cpu.downloaded_at == "t"


# ── sync: pruning ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_prunes_every_row_not_in_the_catalogue(tmp_path: Path) -> None:
    """A good sync deletes rows the catalogue no longer lists — including a
    downloaded one: the catalogue is the list of runners hal0 can launch."""
    store = _store(tmp_path)
    store.upsert(RunnerImage(id="rocmfpx", image="ghcr.io/hal0ai/hal0-rocmfpx", tag="c077206"))
    store.upsert(
        RunnerImage(
            id="old-downloaded",
            image="ghcr.io/hal0ai/old-downloaded",
            tag="v1",
            local_path="/var/lib/hal0/runner-images/old-downloaded.json",
            downloaded_at="2026-01-01T00:00:00Z",
        )
    )
    store.set_tags("old-downloaded", [])

    await _sync(store, _route_handler(images_json=_IMAGES_JSON))

    assert {i.id for i in store.list()} == {"cpu", "flm"}
    assert store.get("old-downloaded") is None
    assert store.list_downloaded() == []


@pytest.mark.asyncio
async def test_a_skipped_entry_stops_the_prune(tmp_path: Path) -> None:
    """A catalogue with an entry this code could not read is a partial answer:
    nothing is pruned, so the row for that entry (and any other) survives."""
    store = _store(tmp_path)
    store.upsert(RunnerImage(id="flm", image="ghcr.io/hal0ai/hal0-runner-flm", tag="v0"))
    store.upsert(RunnerImage(id="rocmfpx", image="ghcr.io/hal0ai/hal0-rocmfpx", tag="c077206"))
    images_json = {
        "schema": "hal0.runner-images.v2",
        "images": [_CPU_ENTRY, {**_FLM_ENTRY, "image": "ghcr.io/hal0ai/hal0-runner-flm:bad"}],
    }
    result = await _sync(store, _route_handler(images_json=images_json))

    assert "flm" in result.skipped
    assert result.prune_skipped is True
    assert store.get("flm") is not None
    assert store.get("rocmfpx") is not None


@pytest.mark.asyncio
async def test_an_empty_catalogue_prunes_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert(_cpu_row())
    result = await _sync(
        store, _route_handler(images_json={"schema": "hal0.runner-images.v2", "images": []})
    )

    assert result.images_json_ok is True
    assert result.prune_skipped is True
    assert store.get("cpu") is not None


# ── sync: size ──────────────────────────────────────────────────────────────


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
    store = _store(tmp_path)
    result = await _sync(
        store,
        _route_handler(
            images_json=_IMAGES_JSON,
            manifest_body=index,
            child_manifests={"sha256:amd64child": child},
        ),
    )

    assert result.probe_errors == {}
    cpu = store.get("cpu")
    assert cpu is not None
    assert cpu.size_bytes == 1000
    flm = store.get("flm")
    assert flm is not None
    # digest stays the top-level (index) one — what a pull-by-digest resolves
    assert flm.digest == "sha256:hal0ai-hal0-runner-flm-v0.9.1"


# ── tag tracking ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_headline_tag_is_the_catalogue_tag_and_available_tags_are_newest_first(
    tmp_path: Path,
) -> None:
    """The catalogue's tag stays the headline however new the other tags are;
    ``available_tags`` carries the registry's tags newest-first."""
    store = _store(tmp_path)
    result = await _sync(
        store, _route_handler(images_json=_IMAGES_JSON, tags=["0822", "latest", "0824"])
    )

    assert result.probe_errors == {}
    cpu = store.get("cpu")
    assert cpu is not None
    assert cpu.tag == "b11552-r1"
    assert cpu.available_tags == ["0824", "0822", "latest"]


@pytest.mark.asyncio
async def test_tags_list_failure_degrades_to_empty_available_tags(tmp_path: Path) -> None:
    """A failing ``tags/list`` must not fail the row: the headline still
    resolves and ``available_tags`` degrades to ``[]``."""
    store = _store(tmp_path)
    result = await _sync(store, _route_handler(images_json=_IMAGES_JSON, tags_fail={_CPU_REPO}))

    assert "cpu" not in result.probe_errors
    cpu = store.get("cpu")
    assert cpu is not None
    assert cpu.tag == "b11552-r1"
    assert cpu.available_tags == []
    assert [t.tag for t in cpu.tags] == ["b11552-r1"]  # headline still probed
    assert cpu.size_bytes == _MANIFEST_SIZE


@pytest.mark.asyncio
async def test_sync_persists_tags_headline_first(tmp_path: Path) -> None:
    """The headline tag is always probed and persisted first, then the other
    tags newest-first, each with its own registry digest."""
    store = _store(tmp_path)
    result = await _sync(store, _route_handler(images_json=_IMAGES_JSON, tags=["0826", "0824"]))

    assert result.probe_errors == {}
    cpu = store.get("cpu")
    assert cpu is not None
    assert [t.tag for t in cpu.tags] == ["b11552-r1", "0826", "0824"]
    assert cpu.tags[0].digest == _CPU_DIGEST  # catalogue digest for the headline
    assert cpu.tags[1].digest == "sha256:hal0ai-hal0-runner-cpu-0826"
    assert cpu.tags[2].digest == "sha256:hal0ai-hal0-runner-cpu-0824"
    assert all(t.size_bytes == _MANIFEST_SIZE for t in cpu.tags)


@pytest.mark.asyncio
async def test_headline_in_tag_list_is_not_probed_twice(tmp_path: Path) -> None:
    """When the headline is also in ``tags/list`` it appears once in the tag
    rows, and its manifest is fetched exactly once."""
    manifest_refs: list[str] = []
    base_handler = _route_handler(images_json=_IMAGES_JSON, tags=["b11552-r1", "0824"])

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/manifests/" in url and _CPU_REPO in url:
            manifest_refs.append(url.partition("/manifests/")[2])
        return base_handler(request)

    store = _store(tmp_path)
    await _sync(store, handler)

    cpu = store.get("cpu")
    assert cpu is not None
    assert [t.tag for t in cpu.tags] == ["b11552-r1", "0824"]
    assert manifest_refs.count("b11552-r1") == 1
    assert manifest_refs.count("0824") == 1


@pytest.mark.asyncio
async def test_extra_tags_probe_is_capped_at_the_newest(tmp_path: Path) -> None:
    """A repo with a huge tag list (ggml-org/llama.cpp) costs a bounded number
    of manifest calls: the headline plus the newest ``_MAX_EXTRA_TAGS``."""
    all_tags = [str(1000 + i) for i in range(_MAX_EXTRA_TAGS + 5)]
    store = _store(tmp_path)
    await _sync(store, _route_handler(images_json=_IMAGES_JSON, tags=all_tags))

    cpu = store.get("cpu")
    assert cpu is not None
    assert len(cpu.available_tags) == len(all_tags)  # the picker still sees every tag
    newest = sorted(all_tags, key=int, reverse=True)[:_MAX_EXTRA_TAGS]
    assert [t.tag for t in cpu.tags] == ["b11552-r1", *newest]


@pytest.mark.asyncio
async def test_sync_result_rows_carry_fresh_tags_on_first_sync(tmp_path: Path) -> None:
    """The SyncResult rows must already carry the freshly-probed tags — not
    the tag table's state from BEFORE this sync's ``store.set_tags`` call
    (empty on a first sync), or the /sync response would lag by one sync."""
    store = _store(tmp_path)
    result = await _sync(store, _route_handler(images_json=_IMAGES_JSON, tags=["0826", "0824"]))

    assert result.probe_errors == {}
    cpu = next(img for img in result.images if img.id == "cpu")
    assert [t.tag for t in cpu.tags] == ["b11552-r1", "0826", "0824"]
    assert cpu.tags[0].digest == _CPU_DIGEST
    assert cpu.tags[1].digest == "sha256:hal0ai-hal0-runner-cpu-0826"
    assert cpu.tags[2].digest == "sha256:hal0ai-hal0-runner-cpu-0824"


# ── noise-tag filtering (cosign signatures + per-commit CI tags) ────────────


@pytest.mark.asyncio
async def test_sync_excludes_cosign_and_ci_commit_tags_from_available_tags(
    tmp_path: Path,
) -> None:
    """CI-built families push a cosign ``.sig`` artifact and a per-commit
    ``sha-<7hex>`` tag alongside every real build. Neither should ever reach
    the catalogue's ``available_tags`` or its probed tag rows."""
    store = _store(tmp_path)
    noisy_tags = [
        "latest",
        "0824",
        "sha256-" + "a" * 64 + ".sig",
        "sha256-" + "b" * 64 + ".att",
        "sha-abc1234",
        "sha-" + "c" * 40,
    ]
    result = await _sync(store, _route_handler(images_json=_IMAGES_JSON, tags=noisy_tags))

    assert result.probe_errors == {}
    cpu = store.get("cpu")
    assert cpu is not None
    assert cpu.available_tags == ["0824", "latest"]
    probed = {t.tag for t in cpu.tags}
    for noise in noisy_tags[2:]:
        assert noise not in cpu.available_tags
        assert noise not in probed


# ── per-tag digest resolution (probe_ghcr_package) ──────────────────────────


@pytest.mark.asyncio
async def test_probe_resolves_digest_per_tag() -> None:
    """probe_ghcr_package resolves a digest for the headline and every probed tag."""
    client = _client(_route_handler(images_json=_IMAGES_JSON, tags=["0826", "0824"]))
    try:
        image = await probe_ghcr_package(_cpu_row(digest=None), client=client)
    finally:
        await client.aclose()

    assert [t.tag for t in image.tags] == ["b11552-r1", "0826", "0824"]
    assert image.tags[1].digest == "sha256:hal0ai-hal0-runner-cpu-0826"
    assert image.tags[2].digest == "sha256:hal0ai-hal0-runner-cpu-0824"
    # No catalogue digest -> the headline takes the registry's.
    assert image.digest == "sha256:hal0ai-hal0-runner-cpu-b11552-r1"
    assert image.tags[0].digest == image.digest


@pytest.mark.asyncio
async def test_probe_keeps_catalogue_digest_on_mismatch() -> None:
    client = _client(_route_handler(images_json=_IMAGES_JSON, tags=["0826"]))
    try:
        image = await probe_ghcr_package(_cpu_row(), client=client)
    finally:
        await client.aclose()

    assert image.digest == _CPU_DIGEST
    assert image.tags[0].digest == _CPU_DIGEST
    assert image.size_bytes == _MANIFEST_SIZE
    assert image.available_tags == ["0826"]


@pytest.mark.asyncio
async def test_per_tag_probe_failure_degrades_to_none() -> None:
    """When a non-headline tag's manifest probe fails, that tag's digest is
    None but the row survives."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith("https://ghcr.io/token"):
            return httpx.Response(200, json={"token": "tok"})
        if "/manifests/" in url:
            repo, _, ref = url.split("https://ghcr.io/v2/")[1].partition("/manifests/")
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

    client = _client(handler)
    try:
        image = await probe_ghcr_package(_cpu_row(), client=client)
    finally:
        await client.aclose()

    assert [t.tag for t in image.tags] == ["b11552-r1", "0826", "0824"]
    assert image.tags[1].digest == "sha256:hal0ai-hal0-runner-cpu-0826"  # success
    assert image.tags[2].digest is None  # failed, degraded to None
    assert image.tags[2].tag == "0824"  # row survives
    assert image.digest == _CPU_DIGEST


@pytest.mark.asyncio
async def test_probe_headline_manifest_failure_raises() -> None:
    """The headline manifest is the one probe the sync cannot degrade past:
    it raises so the sync records a probe error for the row."""
    client = _client(_route_handler(images_json=_IMAGES_JSON, manifest_fail={_CPU_REPO}))
    try:
        with pytest.raises(RuntimeError, match="HTTP 500"):
            await probe_ghcr_package(_cpu_row(), client=client)
    finally:
        await client.aclose()


# ── sync_catalogue_blocking ─────────────────────────────────────────────────


def test_sync_catalogue_blocking_runs_a_sync_from_sync_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``hal0 update`` arm has no running loop: the blocking wrapper runs
    the async sync to completion against the given store."""
    real_client = httpx.AsyncClient
    handler = _route_handler(images_json=_IMAGES_JSON, tags=["0826"])
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    store = _store(tmp_path)

    result = sync_catalogue_blocking(store)

    assert result.images_json_ok is True
    assert result.probe_errors == {}
    assert sorted(i.id for i in result.images) == ["cpu", "flm"]
    cpu = store.get("cpu")
    assert cpu is not None
    assert [t.tag for t in cpu.tags] == ["b11552-r1", "0826"]


def test_sync_catalogue_blocking_reports_a_failed_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_client = httpx.AsyncClient
    handler = _route_handler(images_json=None)
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    store = _store(tmp_path)
    store.upsert(_cpu_row())

    result = sync_catalogue_blocking(store)

    assert result.images_json_ok is False
    assert result.images_json_error is not None
    assert store.get("cpu") is not None  # failed fetch never prunes


# ── build provenance (config-blob OCI labels) ───────────────────────────────


_PATCHED_LABELS = {
    "org.opencontainers.image.source": "https://github.com/hal0ai/llama.cpp.git",
    "org.opencontainers.image.revision": "0a59adde1c5b2f3a4d6e7f8091a2b3c4d5e6f708",
    "dev.hal0.runner.patches": "hip-graphs,fp4-mma,tunable-ops,rocm-7-abi",
}

_UPSTREAM_LABELS = {
    "org.opencontainers.image.source": "https://github.com/ggml-org/llama.cpp.git",
    "org.opencontainers.image.revision": "c841aee0d5b9e2f1a3c4d5e6f708192a3b4c5d6e",
    "dev.hal0.runner.patches": "",
}


def test_provenance_from_labels_extracts_all_three() -> None:
    prov = provenance_from_labels(_PATCHED_LABELS)
    assert prov == {
        "source_repo": "https://github.com/hal0ai/llama.cpp.git",
        "revision": "0a59adde1c5b2f3a4d6e7f8091a2b3c4d5e6f708",
        "patch_count": 4,
    }


def test_provenance_from_labels_empty_patches_is_zero() -> None:
    """Pristine upstream stamps an EMPTY patch list — that's patch_count 0,
    a positive claim ("no patches"), not an absent one."""
    prov = provenance_from_labels(_UPSTREAM_LABELS)
    assert prov is not None
    assert prov["patch_count"] == 0
    assert prov["source_repo"] == "https://github.com/ggml-org/llama.cpp.git"


def test_provenance_from_labels_partial_labels_keep_none_fields() -> None:
    prov = provenance_from_labels({"org.opencontainers.image.revision": "abc1234def"})
    assert prov == {"source_repo": None, "revision": "abc1234def", "patch_count": None}


def test_provenance_from_labels_absent_or_unlabelled_is_none() -> None:
    assert provenance_from_labels(None) is None
    assert provenance_from_labels("not a dict") is None
    assert provenance_from_labels({}) is None
    assert provenance_from_labels({"unrelated.label": "x"}) is None


def _provenance_handler(
    *,
    labels_by_ref: dict[str, dict[str, str]],
    blob_fail: bool = False,
    tags: list[str] | None = None,
):
    """MockTransport handler whose image manifests carry a ``config.digest``
    and whose ``/blobs/<digest>`` route serves the config blob — the shape
    the provenance probe walks (manifest → config.digest → blob GET).
    """
    tag_list = tags if tags is not None else ["latest"]

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == IMAGES_JSON_URL:
            return httpx.Response(200, json=_IMAGES_JSON)
        if url.startswith("https://ghcr.io/token"):
            return httpx.Response(200, json={"token": "tok"})
        if "/manifests/" in url:
            repo, _, ref = url.split("https://ghcr.io/v2/")[1].partition("/manifests/")
            return httpx.Response(
                200,
                headers={"docker-content-digest": f"sha256:{repo.replace('/', '-')}-{ref}"},
                json={
                    "schemaVersion": 2,
                    "config": {"size": 345, "digest": f"sha256:cfg-{ref}"},
                    "layers": [{"size": 6000}, {"size": 6000}],
                },
            )
        if "/blobs/" in url:
            if blob_fail:
                return httpx.Response(500, content=b"nope")
            _, _, blob_digest = url.partition("/blobs/")
            ref = blob_digest.removeprefix("sha256:cfg-")
            return httpx.Response(200, json={"config": {"Labels": labels_by_ref.get(ref) or {}}})
        if "/tags/list" in url:
            return httpx.Response(200, json={"tags": tag_list})
        return httpx.Response(404, content=b"unrouted: " + url.encode())

    return handler


@pytest.mark.asyncio
async def test_sync_probe_extracts_and_persists_provenance(tmp_path: Path) -> None:
    """Each tag's config blob labels land in RunnerImageTag.provenance and
    survive the store round-trip (runner_image_tag.provenance_json)."""
    store = _store(tmp_path)
    handler = _provenance_handler(
        labels_by_ref={"b11552-r1": _PATCHED_LABELS, "0824": _UPSTREAM_LABELS},
        tags=["0824"],
    )
    result = await _sync(store, handler)

    assert result.probe_errors == {}
    cpu = store.get("cpu")
    assert cpu is not None
    by_tag = {t.tag: t.provenance for t in cpu.tags}
    assert by_tag["b11552-r1"] == {
        "source_repo": "https://github.com/hal0ai/llama.cpp.git",
        "revision": "0a59adde1c5b2f3a4d6e7f8091a2b3c4d5e6f708",
        "patch_count": 4,
    }
    assert by_tag["0824"] == {
        "source_repo": "https://github.com/ggml-org/llama.cpp.git",
        "revision": "c841aee0d5b9e2f1a3c4d5e6f708192a3b4c5d6e",
        "patch_count": 0,
    }


@pytest.mark.asyncio
async def test_blob_fetch_failure_degrades_to_none_provenance(tmp_path: Path) -> None:
    """A failing config-blob GET never fails the sync — the tag row simply
    carries provenance=None, digest/size untouched."""
    store = _store(tmp_path)
    result = await _sync(
        store, _provenance_handler(labels_by_ref={}, blob_fail=True, tags=["latest"])
    )

    assert result.probe_errors == {}
    cpu = store.get("cpu")
    assert cpu is not None
    assert cpu.digest == _CPU_DIGEST
    assert cpu.size_bytes == _MANIFEST_SIZE
    assert all(t.provenance is None for t in cpu.tags)


@pytest.mark.asyncio
async def test_unlabelled_config_blob_reads_as_none_provenance(tmp_path: Path) -> None:
    """A reachable blob with no provenance labels reads back exactly like an
    unprobed tag — None, never an all-None placeholder dict."""
    store = _store(tmp_path)
    result = await _sync(store, _provenance_handler(labels_by_ref={}, tags=["latest"]))

    assert result.probe_errors == {}
    cpu = store.get("cpu")
    assert cpu is not None
    assert all(t.provenance is None for t in cpu.tags)
