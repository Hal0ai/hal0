"""#1412 — the SINGLE-FILE pull path must do the same blob/file accounting
the fileset path does.

``run_pull``'s fileset branch calls ``_register_blob_after_install`` per file
and writes one ``model_file`` row per installed file. The single-file /
mmproj-pair branch — the one Add-by-HF-coords and
``POST /api/models/{id}/pull`` with ``hf_repo``+``hf_filename`` actually take
— went straight to ``_register_pulled`` and wrote neither. On a box whose
models all came through that path ``store_blob`` and ``model_file`` were
empty, so refcounting, hardlink dedup, and the store GC were all inert.
"""

from __future__ import annotations

import hashlib

import httpx
import pytest

from hal0.db import repository
from hal0.db.connection import connect, db_path
from hal0.registry import gc
from hal0.registry.pull import make_job, run_pull
from hal0.registry.sqlite_store import SqliteModelRegistry

# ── helpers (mirrors tests/registry/test_pull.py) ────────────────────────────


def _payload(size: int = 2048) -> bytes:
    return b"GGUF" + b"\x00" * 4 + b"a" * (size - 8)


def _multi_handler(bodies: dict[str, bytes]) -> httpx.MockTransport:
    """Serve a distinct body per HF filename (main model + mmproj sidecar)."""

    def handler(req: httpx.Request) -> httpx.Response:
        body = bodies.get(req.url.path.rsplit("/", 1)[-1])
        if body is None:
            return httpx.Response(404, content=b"")
        return httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})

    return httpx.MockTransport(handler)


def _registry() -> SqliteModelRegistry:
    """A SQLite registry on the SAME db ``connect()`` opens by default, so the
    pull's blob bookkeeping and the assertions below see one database."""
    return SqliteModelRegistry(db_path=db_path())


async def _pull(
    model_id: str,
    filename: str,
    bodies: dict[str, bytes],
    *,
    registry: SqliteModelRegistry,
    mmproj_file: str | None = None,
):
    job = make_job(model_id)
    client = httpx.AsyncClient(transport=_multi_handler(bodies))
    try:
        await run_pull(
            job,
            hf_repo="Qwen/Qwen3-4B-Instruct-GGUF",
            hf_file=filename,
            registry=registry,
            client=client,
            mmproj_file=mmproj_file,
        )
    finally:
        await client.aclose()
    assert job.state == "completed", f"got {job.state}: {job.error}"
    return job


# ── the regression ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_file_pull_writes_store_blob_and_model_file(tmp_hal0_home: str) -> None:
    """A plain HF-coords pull registers its blob (refcount 1) and its file row."""
    body = _payload(4096)
    digest = hashlib.sha256(body).hexdigest()
    registry = _registry()

    job = await _pull("qwen3-4b", "qwen3-4b.gguf", {"qwen3-4b.gguf": body}, registry=registry)

    with connect() as conn:
        blob = repository.get_blob(conn, digest)
        assert blob is not None, "pull completion must register a store_blob row (#1412)"
        assert blob["refcount"] == 1
        assert blob["blob_path"] == job.path
        assert blob["size_bytes"] == len(body)

        files = repository.list_model_files(conn, "qwen3-4b")
        assert [f["rel"] for f in files] == ["qwen3-4b.gguf"]
        assert files[0]["dest"] == job.path
        assert files[0]["sha256"] == digest
        assert files[0]["role"] == "model"
        assert files[0]["lfs"] == 1


@pytest.mark.asyncio
async def test_single_file_pull_registers_mmproj_sidecar_too(tmp_hal0_home: str) -> None:
    """The WS-11 mmproj sidecar is a second installed file — it gets its own
    blob + ``model_file`` row, exactly like a fileset's mmproj entry."""
    main = _payload(4096)
    mm = _payload(2048) + b"m"
    registry = _registry()

    await _pull(
        "vision-4b",
        "vision-4b.gguf",
        {"vision-4b.gguf": main, "mmproj-vision-4b.gguf": mm},
        registry=registry,
        mmproj_file="mmproj-vision-4b.gguf",
    )

    with connect() as conn:
        files = {f["rel"]: f for f in repository.list_model_files(conn, "vision-4b")}
        assert set(files) == {"vision-4b.gguf", "mmproj-vision-4b.gguf"}
        assert files["mmproj-vision-4b.gguf"]["role"] == "mmproj"
        assert repository.get_blob(conn, hashlib.sha256(main).hexdigest()) is not None
        assert repository.get_blob(conn, hashlib.sha256(mm).hexdigest()) is not None


@pytest.mark.asyncio
async def test_gc_sees_single_file_pulled_bytes_as_tracked(tmp_hal0_home: str) -> None:
    """The store GC's fs-walk must recognise freshly pulled bytes as tracked.

    Before #1412 the pulled file had no ``store_blob`` and no ``model_file``
    row, so ``reconcile_store_tree`` classified a perfectly live model as
    *bare bytes* — reap-eligible the moment anyone ran GC with
    ``dry_run=False``.
    """
    body = _payload(4096)
    registry = _registry()

    await _pull("gc-model", "gc-model.gguf", {"gc-model.gguf": body}, registry=registry)

    report = gc.reconcile_store_tree(dry_run=True)
    assert report.orphans_found == 0, (
        "a freshly pulled model must not look like untracked bare bytes to GC"
    )
    assert report.errors == []


@pytest.mark.asyncio
async def test_repull_same_bytes_does_not_inflate_refcount(tmp_hal0_home: str) -> None:
    """Refcount counts *referents*, not pull attempts. Re-pulling the same
    model must leave the blob at one reference — otherwise the count drifts
    upward forever and GC can never reclaim the bytes after a real delete."""
    body = _payload(4096)
    digest = hashlib.sha256(body).hexdigest()
    registry = _registry()

    await _pull("repull", "repull.gguf", {"repull.gguf": body}, registry=registry)
    await _pull("repull", "repull.gguf", {"repull.gguf": body}, registry=registry)

    with connect() as conn:
        blob = repository.get_blob(conn, digest)
        assert blob is not None
        assert blob["refcount"] == 1
        assert len(repository.list_model_files(conn, "repull")) == 1
