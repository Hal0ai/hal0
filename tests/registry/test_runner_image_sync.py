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
    provenance_from_labels,
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
            "runtime_family": "llama-server",
            "supported_backends": ["cpu"],
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
    assert cpu.runtime_family == "llama-server"
    assert cpu.supported_backends == ["cpu"]

    flm = store.get("hal0ai/hal0-toolbox-flm")  # no "id" — repo-path fallback
    assert flm is not None
    assert flm.tag == "v2"
    assert flm.ownership == "referenced"
    # Entry carries no runtime metadata — reads back as "not declared".
    assert flm.runtime_family is None
    assert flm.supported_backends == []


@pytest.mark.asyncio
async def test_malformed_runtime_metadata_degrades_to_not_declared(tmp_path: Path) -> None:
    """Bad runtime_family/supported_backends shapes never fail the row.

    Same fail-soft discipline as every other images.json field: a non-string
    runtime_family is dropped; a non-list supported_backends (or one whose
    members aren't non-empty strings) degrades to [].
    """
    images_json = {
        "schema": "hal0.runner-images.v1",
        "images": [
            {
                "id": "cpu",
                "image": "ghcr.io/hal0ai/hal0-toolbox-cpu",
                "tag": "latest",
                "runtime_family": 42,
                "supported_backends": "rocm,vulkan",
            },
            {
                "id": "flm",
                "image": "ghcr.io/hal0ai/hal0-toolbox-flm",
                "tag": "v2",
                "runtime_family": "flm",
                "supported_backends": [None, "", "  ", "npu"],
            },
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
    cpu = store.get("cpu")
    assert cpu is not None
    assert cpu.runtime_family is None
    assert cpu.supported_backends == []
    flm = store.get("flm")
    assert flm is not None
    assert flm.runtime_family == "flm"
    assert flm.supported_backends == ["npu"]  # junk members filtered, order kept


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


@pytest.mark.asyncio
async def test_probe_fetches_headline_manifest_exactly_once(tmp_path: Path) -> None:
    """Regression: :func:`probe_ghcr_package` must not double-fetch the
    headline tag's manifest — once for the top-level digest/size
    resolution, once more inside the per-tag ``available_tags`` loop. The
    loop's ``t == resolved_tag`` branch reuses the already-fetched result
    instead of re-probing (see the "Reuse the headline manifest result"
    comment in the module) — this pins that at the request-count level,
    not just the output shape ``test_probe_resolves_digest_per_tag`` already
    covers, so a regression that re-fetches the same ref (same output,
    doubled GHCR traffic) would still be caught here."""
    from hal0.registry.runner_image_sync import probe_ghcr_package

    manifest_refs: list[str] = []
    base_handler = _route_handler(images_json=_IMAGES_JSON, tags=["0826", "0824"])

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/manifests/" in url:
            _, _, ref = url.partition("/manifests/")
            manifest_refs.append(ref)
        return base_handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await probe_ghcr_package("hal0ai/hal0-toolbox-cpu", client=client, tag="0826")
    finally:
        await client.aclose()

    # "0826" is both the pinned headline AND a member of available_tags —
    # exactly the case the reuse branch exists for.
    assert manifest_refs.count("0826") == 1
    assert manifest_refs.count("0824") == 1


@pytest.mark.asyncio
async def test_second_sync_probe_failure_retains_existing_tag_rows(tmp_path: Path) -> None:
    """Regression: a package that synced successfully once, then FAILS its
    probe on a second sync run (e.g. a transient GHCR 500 on the token
    endpoint), must not lose its previously-persisted ``runner_image_tag``
    rows. ``sync_runner_images``'s per-package loop ``continue``s straight
    past ``store.upsert``/``store.set_tags`` on a probe exception — this
    pins that the existing tag rows survive that skip untouched, store-side,
    not just that the id stays in ``probe_errors``."""
    store = RunnerImageStore(db_path=tmp_path / "hal0.db")
    good_handler = _route_handler(images_json=_IMAGES_JSON, tags=["0826", "0824"])
    client = httpx.AsyncClient(transport=httpx.MockTransport(good_handler))
    try:
        result = await sync_runner_images(store, client=client)
    finally:
        await client.aclose()

    assert result.probe_errors == {}
    cpu_before = store.get("cpu")
    assert cpu_before is not None
    assert [t.tag for t in cpu_before.tags] == ["latest", "0826", "0824"]

    failing_handler = _route_handler(
        images_json=_IMAGES_JSON,
        ghcr_fail={"hal0ai/hal0-toolbox-cpu"},
        tags=["0826", "0824"],
    )
    client2 = httpx.AsyncClient(transport=httpx.MockTransport(failing_handler))
    try:
        result2 = await sync_runner_images(store, client=client2)
    finally:
        await client2.aclose()

    assert "cpu" in result2.probe_errors
    cpu_after = store.get("cpu")
    assert cpu_after is not None
    assert [(t.tag, t.digest) for t in cpu_after.tags] == [
        (t.tag, t.digest) for t in cpu_before.tags
    ]


@pytest.mark.asyncio
async def test_sync_result_rows_carry_fresh_tags_on_first_sync(tmp_path: Path) -> None:
    """The SyncResult rows returned by sync_runner_images must already carry
    the freshly-probed tags — not the tag table's state from BEFORE this
    sync's ``store.set_tags`` call.

    ``store.upsert`` reads the tag table for the row it returns before the
    sync loop calls ``store.set_tags`` with this run's probed tags; naively
    appending ``store.upsert(image)``'s return value straight to
    ``result.images`` carries the PREVIOUS sync's tags — empty on a first
    sync against a brand-new store, since there is no previous sync at all.
    That means the /sync response (and its ``families`` payload, which is
    built from these rows) would lag by one full sync. This asserts the
    fix: on a first sync (empty store), the rows in ``result.images``
    already carry the tags this very probe just resolved.
    """
    store = RunnerImageStore(db_path=tmp_path / "hal0.db")
    handler = _route_handler(images_json=_IMAGES_JSON, tags=["0826", "0824"])
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await sync_runner_images(store, client=client)
    finally:
        await client.aclose()

    assert result.probe_errors == {}
    cpu = next(img for img in result.images if img.id == "cpu")
    # Same expectation as test_sync_persists_tags's store-reload check above,
    # but asserted straight off the SyncResult row — no re-fetch from the
    # store in between — so a regression back to the pre-fix ordering (which
    # would leave this empty on a first sync) is caught here.
    assert len(cpu.tags) == 3
    assert [t.tag for t in cpu.tags] == ["latest", "0826", "0824"]
    assert cpu.tags[0].digest == "sha256:hal0ai-hal0-toolbox-cpu-latest"
    assert cpu.tags[1].digest == "sha256:hal0ai-hal0-toolbox-cpu-0826"
    assert cpu.tags[2].digest == "sha256:hal0ai-hal0-toolbox-cpu-0824"


# ── build provenance (h0/runner-provenance: config-blob OCI labels) ─────────


_ROCMFPX_LABELS = {
    "org.opencontainers.image.source": "https://github.com/hal0ai/ROCmFPX.git",
    "org.opencontainers.image.revision": "0a59adde1c5b2f3a4d6e7f8091a2b3c4d5e6f708",
    "dev.hal0.runner.patches": "hip-graphs,fp4-mma,tunable-ops,rocm-7-abi",
}

_UPSTREAM_LABELS = {
    "org.opencontainers.image.source": "https://github.com/ggml-org/llama.cpp.git",
    "org.opencontainers.image.revision": "c841aee0d5b9e2f1a3c4d5e6f708192a3b4c5d6e",
    "dev.hal0.runner.patches": "",
}


def test_provenance_from_labels_extracts_all_three() -> None:
    prov = provenance_from_labels(_ROCMFPX_LABELS)
    assert prov == {
        "source_repo": "https://github.com/hal0ai/ROCmFPX.git",
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
    store = RunnerImageStore(db_path=tmp_path / "hal0.db")
    handler = _provenance_handler(
        labels_by_ref={"latest": _ROCMFPX_LABELS, "0824": _UPSTREAM_LABELS},
        tags=["0824", "latest"],
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await sync_runner_images(store, client=client)
    finally:
        await client.aclose()

    assert result.probe_errors == {}
    cpu = store.get("cpu")
    assert cpu is not None
    by_tag = {t.tag: t.provenance for t in cpu.tags}
    assert by_tag["latest"] == {
        "source_repo": "https://github.com/hal0ai/ROCmFPX.git",
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
    store = RunnerImageStore(db_path=tmp_path / "hal0.db")
    handler = _provenance_handler(labels_by_ref={}, blob_fail=True, tags=["latest"])
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await sync_runner_images(store, client=client)
    finally:
        await client.aclose()

    assert result.probe_errors == {}
    cpu = store.get("cpu")
    assert cpu is not None
    assert cpu.digest == "sha256:hal0ai-hal0-toolbox-cpu-latest"
    assert all(t.provenance is None for t in cpu.tags)


@pytest.mark.asyncio
async def test_unlabelled_config_blob_reads_as_none_provenance(tmp_path: Path) -> None:
    """A reachable blob with no provenance labels reads back exactly like an
    unprobed tag — None, never an all-None placeholder dict."""
    store = RunnerImageStore(db_path=tmp_path / "hal0.db")
    handler = _provenance_handler(labels_by_ref={}, tags=["latest"])
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await sync_runner_images(store, client=client)
    finally:
        await client.aclose()

    assert result.probe_errors == {}
    cpu = store.get("cpu")
    assert cpu is not None
    assert all(t.provenance is None for t in cpu.tags)
