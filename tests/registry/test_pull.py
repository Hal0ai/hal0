"""Tests for the HF streaming pull engine.

Uses ``httpx.MockTransport`` to stub HuggingFace without touching the
network. The same transport handler verifies authorization headers,
redirect handling, and partial downloads / cancellation.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from hal0.registry.pull import (
    _CHUNK_BYTES,
    _pull_root,
    _sanitise_id,
    _tmp_dir,
    hf_download_url,
    make_job,
    pull_job_file,
    run_pull,
    sweep_orphaned_partials,
)
from hal0.registry.store import ModelRegistry

# ── helpers ──────────────────────────────────────────────────────────────────


def _payload(size: int = 2048) -> bytes:
    """Deterministic fake-GGUF bytes so SHA-256 assertions are reproducible."""
    return (b"GGUF" + b"\x00" * 4 + os.urandom(0)) + (b"a" * (size - 8))


def _ok_handler(body: bytes, *, content_length: bool = True) -> httpx.MockTransport:
    """Mock transport that returns ``body`` with optional Content-Length."""

    def handler(req: httpx.Request) -> httpx.Response:
        headers: dict[str, str] = {}
        if content_length:
            headers["Content-Length"] = str(len(body))
        return httpx.Response(200, content=body, headers=headers)

    return httpx.MockTransport(handler)


def _status_handler(status: int) -> httpx.MockTransport:
    return httpx.MockTransport(lambda req: httpx.Response(status, content=b""))


# ── URL builder ──────────────────────────────────────────────────────────────


def test_hf_download_url_uses_resolve_main() -> None:
    """resolve/main is the LFS-aware HF path; raw/main returns text-only."""
    url = hf_download_url("Qwen/Qwen3-4B-Instruct-GGUF", "qwen3-4b.gguf")
    assert url == "https://huggingface.co/Qwen/Qwen3-4B-Instruct-GGUF/resolve/main/qwen3-4b.gguf"


def test_hf_download_url_strips_extraneous_slashes() -> None:
    assert hf_download_url("/foo/bar/", "/baz.gguf") == (
        "https://huggingface.co/foo/bar/resolve/main/baz.gguf"
    )


# ── path sanitiser ───────────────────────────────────────────────────────────


def test_sanitise_id_blocks_path_traversal() -> None:
    """'..' and '/' must be stripped so a model id can't escape the tree."""
    assert _sanitise_id("../../etc/passwd") == "etc-passwd"
    assert _sanitise_id("normal-id_v1.gguf") == "normal-id_v1.gguf"
    assert _sanitise_id("") == "model"
    assert _sanitise_id("/") == "model"


# ── run_pull: happy path ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_pull_happy_path_writes_file_and_registers(
    tmp_hal0_home: str,
) -> None:
    body = _payload(4096)
    digest = hashlib.sha256(body).hexdigest()

    job = make_job("qwen3-4b")
    registry = ModelRegistry()
    client = httpx.AsyncClient(transport=_ok_handler(body))
    try:
        await run_pull(
            job,
            hf_repo="Qwen/Qwen3-4B-Instruct-GGUF",
            hf_file="qwen3-4b.gguf",
            registry=registry,
            client=client,
        )
    finally:
        await client.aclose()

    assert job.state == "completed", f"got {job.state}: {job.error}"
    assert job.sha256 == digest
    assert job.bytes_downloaded == len(body)
    assert job.bytes_total == len(body)
    assert job.path is not None
    final = Path(job.path)
    assert final.exists()
    assert final.read_bytes() == body
    # Registry entry is now wired up.
    entry = registry.get("qwen3-4b")
    assert entry.path == str(final)
    assert entry.size_bytes == len(body)
    assert entry.hf_repo == "Qwen/Qwen3-4B-Instruct-GGUF"
    assert entry.metadata.get("sha256") == digest


# ── MR-1: run_pull persists a durable snapshot for ALL callers ────────────────


@pytest.mark.asyncio
async def test_run_pull_persists_terminal_snapshot_for_direct_callers(
    tmp_hal0_home: str,
) -> None:
    """MR-1: installer/bundle-tier pulls call ``run_pull`` directly (bypassing
    the routes/models wrappers). ``run_pull`` must itself write the durable
    pull-job snapshot so a status poll after an install-time api restart
    resolves instead of 404ing."""
    body = _payload(2048)
    job = make_job("hal0-max")
    registry = ModelRegistry()
    client = httpx.AsyncClient(transport=_ok_handler(body))
    try:
        await run_pull(
            job,
            hf_repo="Hal0ai/hal0-Max-GGUF",
            hf_file="hal0-max.gguf",
            registry=registry,
            client=client,
        )
    finally:
        await client.aclose()

    assert job.state == "completed"
    # The snapshot the /pull/status disk-fallback reads must exist and be terminal.
    snapshot = pull_job_file("hal0-max")
    assert snapshot.exists(), "run_pull must persist a durable snapshot itself (MR-1)"
    persisted = json.loads(snapshot.read_text(encoding="utf-8"))
    assert persisted["state"] == "completed"
    assert persisted["model_id"] == "hal0-max"


@pytest.mark.asyncio
async def test_run_pull_persists_failed_snapshot_on_error(tmp_hal0_home: str) -> None:
    """MR-1: a failed direct pull is also persisted terminally, so a restart
    doesn't leave the poller with no snapshot at all."""
    job = make_job("ghost")
    registry = ModelRegistry()
    client = httpx.AsyncClient(transport=_status_handler(404))
    try:
        await run_pull(
            job,
            hf_repo="Hal0ai/ghost-GGUF",
            hf_file="ghost.gguf",
            registry=registry,
            client=client,
        )
    finally:
        await client.aclose()

    assert job.state == "failed"
    snapshot = pull_job_file("ghost")
    assert snapshot.exists()
    persisted = json.loads(snapshot.read_text(encoding="utf-8"))
    assert persisted["state"] == "failed"


# ── MR-4: disk-space preflight before streaming ──────────────────────────────


@pytest.mark.asyncio
async def test_run_pull_fails_fast_when_content_length_exceeds_free_disk(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MR-4: a pull whose advertised size exceeds free disk fails fast with
    model.insufficient_disk BEFORE streaming the body (no .part, no final)."""
    body = _payload(4096)

    # Advertise a large content-length, but report almost no free space.
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"Content-Length": str(40 * 1024**3)})

    monkeypatch.setattr(
        "hal0.registry.pull.shutil.disk_usage",
        lambda _p: SimpleNamespace(total=100, used=99, free=1024),
    )

    job = make_job("too-big")
    registry = ModelRegistry()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await run_pull(
            job,
            hf_repo="Hal0ai/too-big-GGUF",
            hf_file="too-big.gguf",
            registry=registry,
            client=client,
        )
    finally:
        await client.aclose()

    assert job.state == "failed"
    assert job.error_code == "model.insufficient_disk"
    # Nothing was written: no bytes downloaded, no final path recorded.
    assert job.bytes_downloaded == 0
    assert job.path is None
    # The failed snapshot is still persisted (Wave 2 MR-1 finally).
    persisted = json.loads(pull_job_file("too-big").read_text(encoding="utf-8"))
    assert persisted["state"] == "failed"
    assert persisted["error_code"] == "model.insufficient_disk"


@pytest.mark.asyncio
async def test_run_pull_proceeds_when_disk_probe_unavailable(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MR-4: if the free-space probe raises, the pull must NOT fail on that —
    it falls through to the existing stream-until-ENOSPC behavior."""
    body = _payload(2048)

    def raise_oserror(_p: object) -> object:
        raise OSError("statvfs unavailable")

    monkeypatch.setattr("hal0.registry.pull.shutil.disk_usage", raise_oserror)

    job = make_job("probe-fails")
    registry = ModelRegistry()
    client = httpx.AsyncClient(transport=_ok_handler(body))
    try:
        await run_pull(
            job,
            hf_repo="Hal0ai/probe-GGUF",
            hf_file="probe.gguf",
            registry=registry,
            client=client,
        )
    finally:
        await client.aclose()

    assert job.state == "completed", f"probe failure must not fail the pull: {job.error}"


@pytest.mark.asyncio
async def test_run_pull_404_marks_failed(tmp_hal0_home: str) -> None:
    job = make_job("ghost-model")
    registry = ModelRegistry()
    client = httpx.AsyncClient(transport=_status_handler(404))
    try:
        await run_pull(
            job,
            hf_repo="nope/nope",
            hf_file="nope.gguf",
            registry=registry,
            client=client,
        )
    finally:
        await client.aclose()
    assert job.state == "failed"
    assert job.error_code == "model.pull_failed"
    assert "no file" in (job.error or "")


@pytest.mark.asyncio
async def test_run_pull_403_gated_repo(tmp_hal0_home: str) -> None:
    """Gated repos should surface a helpful HF_TOKEN hint."""
    job = make_job("gated-model")
    registry = ModelRegistry()
    client = httpx.AsyncClient(transport=_status_handler(403))
    try:
        await run_pull(
            job,
            hf_repo="meta-llama/something",
            hf_file="model.gguf",
            registry=registry,
            client=client,
        )
    finally:
        await client.aclose()
    assert job.state == "failed"
    assert "HF_TOKEN" in (job.error or "")


@pytest.mark.asyncio
async def test_run_pull_uses_hf_token_header(tmp_hal0_home: str) -> None:
    """When hf_token is set, an Authorization: Bearer header goes upstream."""
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers.get("authorization", "")
        return httpx.Response(200, content=b"x" * 32, headers={"Content-Length": "32"})

    job = make_job("gated2")
    registry = ModelRegistry()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await run_pull(
            job,
            hf_repo="org/foo",
            hf_file="foo.gguf",
            registry=registry,
            client=client,
            hf_token="hf_secret123",
        )
    finally:
        await client.aclose()
    assert seen["auth"] == "Bearer hf_secret123"
    assert job.state == "completed"


@pytest.mark.asyncio
async def test_run_pull_cancellation_removes_partial(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Setting cancel_requested mid-stream should drop the partial file."""
    job = make_job("cancel-me")
    registry = ModelRegistry()

    body = _payload(64 * 1024)

    async def slow_stream(req: httpx.Request) -> httpx.Response:
        # The MockTransport gives us a one-shot response, but we trigger
        # cancellation BEFORE run_pull sees the first chunk by flipping
        # the flag immediately. The first chunk read still happens, then
        # the second-chunk check sees the flag.
        return httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})

    client = httpx.AsyncClient(transport=httpx.MockTransport(slow_stream))
    # Set the cancel flag before the task even starts — the very first
    # chunk-boundary check will trip it.
    job.cancel_requested = True
    try:
        await run_pull(
            job,
            hf_repo="org/cancel",
            hf_file="cancel.gguf",
            registry=registry,
            client=client,
        )
    finally:
        await client.aclose()
    assert job.state == "cancelled"
    # No final file written.
    final_dir = Path(tmp_hal0_home) / "var-lib" / "hal0" / "models" / "cancel-me"
    if final_dir.exists():
        assert not any(final_dir.iterdir())
    # The cancel path also discards the staging .part + sidecar (MR-7).
    assert not _part_paths("cancel-me", "cancel.gguf")[0].exists()
    assert not _part_paths("cancel-me", "cancel.gguf")[1].exists()


# ── sweep_orphaned_partials: startup reaper (MR-9) ───────────────────────────


def test_sweep_orphaned_partials_reaps_old_but_keeps_fresh_and_final(
    tmp_hal0_home: str,
) -> None:
    """Aged *.part files are reaped; fresh partials and installed files survive."""
    tmp = _tmp_dir()
    tmp.mkdir(parents=True, exist_ok=True)

    # Stale partial left by a SIGKILL/OOM mid-pull — backdate its mtime 48h.
    old = tmp / "modelA.aaaa.part"
    old.write_bytes(b"partial")
    t = time.time() - 48 * 3600
    os.utime(old, (t, t))

    # An actively-growing partial from a concurrent pull — mtime is now.
    fresh = tmp / "modelB.bbbb.part"
    fresh.write_bytes(b"partial")

    # A completed install, NOT under .tmp — must never be touched.
    installed = _pull_root() / "modelC" / "model.gguf"
    installed.parent.mkdir(parents=True, exist_ok=True)
    installed.write_bytes(b"GGUF")

    removed = sweep_orphaned_partials()

    assert removed == 1
    assert not old.exists()
    assert fresh.exists()
    assert installed.exists()


def test_sweep_orphaned_partials_missing_tmp_dir_is_noop(tmp_hal0_home: str) -> None:
    """No .tmp directory present → returns 0 and never raises (fail-soft)."""
    assert not _tmp_dir().exists()
    assert sweep_orphaned_partials() == 0


def test_sweep_orphaned_partials_reaps_stale_resume_sidecars(tmp_hal0_home: str) -> None:
    """A stale .part.json resume sidecar is reaped too, not left to linger."""
    tmp = _tmp_dir()
    tmp.mkdir(parents=True, exist_ok=True)
    old = time.time() - 48 * 3600

    stale_part = tmp / "modelA.part"
    stale_part.write_bytes(b"partial")
    stale_sidecar = tmp / "modelA.part.json"
    stale_sidecar.write_text('{"bytes": 7}', encoding="utf-8")
    for p in (stale_part, stale_sidecar):
        os.utime(p, (old, old))

    # A fresh sidecar from an in-flight pull must survive.
    fresh_sidecar = tmp / "modelB.part.json"
    fresh_sidecar.write_text('{"bytes": 3}', encoding="utf-8")

    removed = sweep_orphaned_partials()

    assert removed == 2  # stale .part + its stale sidecar
    assert not stale_part.exists()
    assert not stale_sidecar.exists()
    assert fresh_sidecar.exists()


# ── MR-7: resume / partial-download support ──────────────────────────────────


def _part_paths(model_id: str, filename: str) -> tuple[Path, Path]:
    """Return the deterministic per-(model, file) ``.part`` + sidecar paths.

    Mirrors ``hal0.registry.pull._staging_paths`` — staging is keyed by BOTH
    the model id and the filename so each file of a multi-file pull (WS-11)
    resumes independently.
    """
    tmp = _tmp_dir()
    stem = f"{_sanitise_id(model_id)}--{_sanitise_id(filename)}"
    return tmp / f"{stem}.part", tmp / f"{stem}.part.json"


def _seed_partial(
    model_id: str,
    filename: str,
    url: str,
    have: int,
    total: int,
    prefix: bytes,
    *,
    etag: str | None = None,
) -> tuple[Path, Path]:
    """Pre-create a valid-looking partial + sidecar as a prior interrupted pull."""
    tmp = _tmp_dir()
    tmp.mkdir(parents=True, exist_ok=True)
    part, sidecar = _part_paths(model_id, filename)
    part.write_bytes(prefix)
    sidecar.write_text(
        json.dumps({"url": url, "etag": etag, "bytes": have, "total": total}),
        encoding="utf-8",
    )
    return part, sidecar


def _fail_midstream(body: bytes, first_n: int, exc: Exception) -> httpx.MockTransport:
    """Transport that streams ``body[:first_n]`` then raises ``exc`` mid-body."""

    async def handler(req: httpx.Request) -> httpx.Response:
        async def agen():  # type: ignore[no-untyped-def]
            yield body[:first_n]
            raise exc

        return httpx.Response(200, headers={"Content-Length": str(len(body))}, content=agen())

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_run_pull_resumes_from_partial_with_range(tmp_hal0_home: str) -> None:
    """Primary MR-7 guard: an interrupted pull resumes via a Range request on
    the NEXT run_pull and produces a byte-identical SHA-256."""
    total = _CHUNK_BYTES * 3
    body = _payload(total)
    digest = hashlib.sha256(body).hexdigest()
    registry = ModelRegistry()

    # ── Pass 1: stream the first chunk, then drop the connection mid-body. ──
    job1 = make_job("resume-me")
    client1 = httpx.AsyncClient(
        transport=_fail_midstream(body, _CHUNK_BYTES, httpx.ReadError("dropped"))
    )
    try:
        await run_pull(
            job1,
            hf_repo="Org/Resume-GGUF",
            hf_file="resume.gguf",
            registry=registry,
            client=client1,
        )
    finally:
        await client1.aclose()

    assert job1.state == "failed"
    part, sidecar = _part_paths("resume-me", "resume.gguf")
    assert part.exists(), "transient error must PRESERVE the .part for resume"
    assert sidecar.exists(), "transient error must write a resume sidecar"
    assert part.stat().st_size == _CHUNK_BYTES
    assert json.loads(sidecar.read_text(encoding="utf-8"))["bytes"] == _CHUNK_BYTES

    # ── Pass 2: fresh job, same model_id → resumes with a Range header. ──
    job2 = make_job("resume-me")
    seen: dict[str, str] = {}

    async def resume_handler(req: httpx.Request) -> httpx.Response:
        seen["range"] = req.headers.get("range", "")
        remainder = body[_CHUNK_BYTES:]
        return httpx.Response(
            206,
            headers={
                "Content-Length": str(len(remainder)),
                "Content-Range": f"bytes {_CHUNK_BYTES}-{total - 1}/{total}",
            },
            content=remainder,
        )

    client2 = httpx.AsyncClient(transport=httpx.MockTransport(resume_handler))
    try:
        await run_pull(
            job2,
            hf_repo="Org/Resume-GGUF",
            hf_file="resume.gguf",
            registry=registry,
            client=client2,
        )
    finally:
        await client2.aclose()

    assert seen["range"] == f"bytes={_CHUNK_BYTES}-"
    assert job2.state == "completed", f"got {job2.state}: {job2.error}"
    final = Path(job2.path)  # type: ignore[arg-type]
    assert final.read_bytes() == body
    assert job2.sha256 == digest, "resume must produce a byte-identical hash"
    assert job2.bytes_downloaded == total
    # Staging cleaned up on success.
    assert not part.exists()
    assert not sidecar.exists()


@pytest.mark.asyncio
async def test_run_pull_restarts_when_server_ignores_range(tmp_hal0_home: str) -> None:
    """A CDN that ignores Range (returns 200 + full body) must reset the hasher
    and re-download cleanly — no double-counted prefix, correct SHA-256."""
    total = _CHUNK_BYTES * 2
    body = _payload(total)
    digest = hashlib.sha256(body).hexdigest()
    url = hf_download_url("Org/Ignore-GGUF", "ignore.gguf")
    _seed_partial("ignore-me", "ignore.gguf", url, _CHUNK_BYTES, total, body[:_CHUNK_BYTES])

    job = make_job("ignore-me")
    registry = ModelRegistry()

    async def ignore_range_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Length": str(total)}, content=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(ignore_range_handler))
    try:
        await run_pull(
            job,
            hf_repo="Org/Ignore-GGUF",
            hf_file="ignore.gguf",
            registry=registry,
            client=client,
        )
    finally:
        await client.aclose()

    assert job.state == "completed", f"got {job.state}: {job.error}"
    assert job.sha256 == digest
    assert job.bytes_downloaded == total
    assert Path(job.path).read_bytes() == body  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_run_pull_restarts_when_object_changed(tmp_hal0_home: str) -> None:
    """A changed object (If-Range miss → 200 full body) discards the stale
    prefix and re-pulls with the correct SHA-256."""
    total = _CHUNK_BYTES * 2
    body = _payload(total)
    digest = hashlib.sha256(body).hexdigest()
    url = hf_download_url("Org/Changed-GGUF", "changed.gguf")
    _seed_partial(
        "changed-me",
        "changed.gguf",
        url,
        _CHUNK_BYTES,
        total,
        body[:_CHUNK_BYTES],
        etag='"old-etag"',
    )

    job = make_job("changed-me")
    registry = ModelRegistry()
    seen: dict[str, str] = {}

    async def changed_handler(req: httpx.Request) -> httpx.Response:
        seen["if_range"] = req.headers.get("if-range", "")
        return httpx.Response(
            200,
            headers={"Content-Length": str(total), "ETag": '"new-etag"'},
            content=body,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(changed_handler))
    try:
        await run_pull(
            job,
            hf_repo="Org/Changed-GGUF",
            hf_file="changed.gguf",
            registry=registry,
            client=client,
        )
    finally:
        await client.aclose()

    assert seen["if_range"] == '"old-etag"', "resume must send If-Range with the etag"
    assert job.state == "completed", f"got {job.state}: {job.error}"
    assert job.sha256 == digest
    assert Path(job.path).read_bytes() == body  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_run_pull_transient_error_preserves_partial(tmp_hal0_home: str) -> None:
    """A mid-stream transport error must PRESERVE the .part + sidecar so the
    next run_pull can resume (regression guard for the failure-cleanup change)."""
    total = _CHUNK_BYTES * 3
    body = _payload(total)
    job = make_job("keepme")
    registry = ModelRegistry()
    client = httpx.AsyncClient(
        transport=_fail_midstream(body, _CHUNK_BYTES, httpx.TransportError("boom"))
    )
    try:
        await run_pull(
            job,
            hf_repo="Org/Keep-GGUF",
            hf_file="keep.gguf",
            registry=registry,
            client=client,
        )
    finally:
        await client.aclose()

    assert job.state == "failed"
    part, sidecar = _part_paths("keepme", "keep.gguf")
    assert part.exists(), "transient error must preserve the .part"
    assert sidecar.exists()
    assert json.loads(sidecar.read_text(encoding="utf-8"))["bytes"] == _CHUNK_BYTES


@pytest.mark.asyncio
async def test_run_pull_permanent_error_removes_partial(tmp_hal0_home: str) -> None:
    """A permanent 4xx (404) must DISCARD any leftover .part + sidecar — a
    permanent failure is not resumable."""
    url = hf_download_url("Org/Perm-GGUF", "perm.gguf")
    total = _CHUNK_BYTES * 2
    body = _payload(total)
    part, sidecar = _seed_partial(
        "permfail", "perm.gguf", url, _CHUNK_BYTES, total, body[:_CHUNK_BYTES]
    )
    assert part.exists()

    job = make_job("permfail")
    registry = ModelRegistry()
    client = httpx.AsyncClient(transport=_status_handler(404))
    try:
        await run_pull(
            job,
            hf_repo="Org/Perm-GGUF",
            hf_file="perm.gguf",
            registry=registry,
            client=client,
        )
    finally:
        await client.aclose()

    assert job.state == "failed"
    assert not part.exists(), "permanent 4xx must discard the partial"
    assert not sidecar.exists()


@pytest.mark.asyncio
async def test_run_pull_ignores_legacy_id_keyed_partial(tmp_hal0_home: str) -> None:
    """Back-compat: a pre-multi-file ``<id>.part``/``<id>.part.json`` pair (the
    old staging key) must never crash or mis-stitch a new pull — it is simply
    ignored (and left for sweep_orphaned_partials to reap)."""
    tmp = _tmp_dir()
    tmp.mkdir(parents=True, exist_ok=True)
    legacy_part = tmp / "legacy-id.part"
    legacy_part.write_bytes(b"old-prefix")
    legacy_sidecar = tmp / "legacy-id.part.json"
    legacy_sidecar.write_text(
        json.dumps({"url": "https://old", "etag": None, "bytes": 10, "total": 20}),
        encoding="utf-8",
    )

    body = _payload(2048)
    job = make_job("legacy-id")
    registry = ModelRegistry()
    client = httpx.AsyncClient(transport=_ok_handler(body))
    try:
        await run_pull(
            job,
            hf_repo="Org/Legacy-GGUF",
            hf_file="legacy.gguf",
            registry=registry,
            client=client,
        )
    finally:
        await client.aclose()

    assert job.state == "completed", f"got {job.state}: {job.error}"
    assert job.sha256 == hashlib.sha256(body).hexdigest()
    # Old-keyed staging files were not consumed by the new pull.
    assert legacy_part.exists()
    assert legacy_sidecar.exists()


# ── WS-11: multi-file pulls (main GGUF + mmproj sidecar) ─────────────────────


_MAIN_NAME = "vision-model.gguf"
_MMPROJ_NAME = "mmproj-F16.gguf"


def _two_file_transport(
    main: bytes,
    mmproj: bytes,
    *,
    extra_headers: dict[str, dict[str, str]] | None = None,
    on_request: Any = None,
) -> httpx.MockTransport:
    """Route by URL path: serve ``main`` / ``mmproj`` for their filenames.

    ``extra_headers`` maps filename → headers merged into that file's
    response. ``on_request(req)`` (if set) runs before each response —
    used to flip cancel flags mid-job.
    """

    def handler(req: httpx.Request) -> httpx.Response:
        if on_request is not None:
            on_request(req)
        path = req.url.path
        if path.endswith(_MAIN_NAME):
            body, name = main, _MAIN_NAME
        elif path.endswith(_MMPROJ_NAME):
            body, name = mmproj, _MMPROJ_NAME
        else:
            return httpx.Response(404, content=b"")
        headers = {"Content-Length": str(len(body))}
        headers.update((extra_headers or {}).get(name, {}))
        return httpx.Response(200, content=body, headers=headers)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_run_pull_multi_file_fetches_mmproj_and_sets_registry(
    tmp_hal0_home: str,
) -> None:
    """WS-11 primary guard: a two-file pull lands the main GGUF and the mmproj
    sidecar in the same directory, sets ``model.mmproj`` on the registry row
    directly (no scan), and reports AGGREGATE bytes on the job."""
    main = _payload(4096)
    mmproj = b"m" * 1024
    job = make_job("vision-model")
    registry = ModelRegistry()
    client = httpx.AsyncClient(transport=_two_file_transport(main, mmproj))
    try:
        await run_pull(
            job,
            hf_repo="Org/Vision-GGUF",
            hf_file=_MAIN_NAME,
            registry=registry,
            client=client,
            mmproj_file=_MMPROJ_NAME,
        )
    finally:
        await client.aclose()

    assert job.state == "completed", f"got {job.state}: {job.error}"
    final = Path(job.path)  # type: ignore[arg-type]
    assert final.read_bytes() == main
    mmproj_path = final.parent / _MMPROJ_NAME
    assert mmproj_path.read_bytes() == mmproj, "mmproj must land beside the main model"

    # Aggregate progress across both files (the stable wire shape).
    assert job.bytes_downloaded == len(main) + len(mmproj)
    assert job.bytes_total == len(main) + len(mmproj)
    # Per-file manifest is exposed additively.
    snap = job.as_dict()
    assert snap["bytes_downloaded"] == len(main) + len(mmproj)
    assert [f["kind"] for f in snap["files"]] == ["model", "mmproj"]
    assert snap["files"][0]["sha256"] == hashlib.sha256(main).hexdigest()
    assert snap["files"][1]["sha256"] == hashlib.sha256(mmproj).hexdigest()
    # job.sha256 stays the MAIN file's hash (wire compat).
    assert job.sha256 == hashlib.sha256(main).hexdigest()

    # Registry row: mmproj associated directly — no directory scan needed.
    entry = registry.get("vision-model")
    assert entry.path == str(final)
    assert entry.mmproj == str(mmproj_path)
    assert entry.metadata.get("sha256") == hashlib.sha256(main).hexdigest()


@pytest.mark.asyncio
async def test_run_pull_single_file_wire_shape_unchanged(tmp_hal0_home: str) -> None:
    """Back-compat guard: a single-file job's as_dict() carries the exact
    top-level keys the UI reads, with the same values as before multi-file."""
    body = _payload(2048)
    job = make_job("plain-model")
    registry = ModelRegistry()
    client = httpx.AsyncClient(transport=_ok_handler(body))
    try:
        await run_pull(
            job,
            hf_repo="Org/Plain-GGUF",
            hf_file="plain.gguf",
            registry=registry,
            client=client,
        )
    finally:
        await client.aclose()

    snap = job.as_dict()
    for key in (
        "id",
        "model_id",
        "state",
        "bytes_downloaded",
        "bytes_total",
        "started_at",
        "finished_at",
        "error",
        "error_code",
        "sha256",
        "path",
    ):
        assert key in snap
    assert snap["state"] == "completed"
    assert snap["bytes_downloaded"] == len(body)
    assert snap["bytes_total"] == len(body)
    assert snap["sha256"] == hashlib.sha256(body).hexdigest()
    # Single-file registry row keeps mmproj unset.
    assert registry.get("plain-model").mmproj is None


@pytest.mark.asyncio
async def test_run_pull_multi_file_progress_monotonic_across_boundary(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WS-11: aggregate bytes_downloaded never decreases across the main→mmproj
    file boundary (the UI's progress bar must not jump backwards)."""
    monkeypatch.setattr("hal0.registry.pull._SSE_MIN_INTERVAL_S", 0.0)
    main = _payload(_CHUNK_BYTES * 3)
    mmproj = b"m" * (_CHUNK_BYTES * 2)
    job = make_job("mono-model")
    registry = ModelRegistry()

    samples: list[int] = []
    orig_signal = job._signal

    def sampling_signal() -> None:
        samples.append(job.bytes_downloaded)
        orig_signal()

    job._signal = sampling_signal  # type: ignore[method-assign]

    client = httpx.AsyncClient(transport=_two_file_transport(main, mmproj))
    try:
        await run_pull(
            job,
            hf_repo="Org/Mono-GGUF",
            hf_file=_MAIN_NAME,
            registry=registry,
            client=client,
            mmproj_file=_MMPROJ_NAME,
        )
    finally:
        await client.aclose()

    assert job.state == "completed", f"got {job.state}: {job.error}"
    assert samples == sorted(samples), "aggregate progress must be monotonic"
    # We actually crossed the boundary: some samples exceed the main file size.
    assert samples[-1] == len(main) + len(mmproj)
    assert any(0 < s < len(main) for s in samples)
    assert any(len(main) < s < len(main) + len(mmproj) for s in samples)


@pytest.mark.asyncio
async def test_run_pull_cancel_mid_second_file_then_repull_completes(
    tmp_hal0_home: str,
) -> None:
    """WS-11: cancelling while the mmproj streams leaves a clean state (main
    installed, mmproj staging discarded) and a follow-up pull completes with
    the full two-file result + registry association."""
    main = _payload(2048)
    mmproj = b"m" * 4096
    registry = ModelRegistry()

    # ── Pass 1: flip cancel_requested the moment the mmproj is requested. ──
    job1 = make_job("cancel2-model")

    def cancel_on_mmproj(req: httpx.Request) -> None:
        if req.url.path.endswith(_MMPROJ_NAME):
            job1.cancel_requested = True

    client1 = httpx.AsyncClient(
        transport=_two_file_transport(main, mmproj, on_request=cancel_on_mmproj)
    )
    try:
        await run_pull(
            job1,
            hf_repo="Org/Cancel2-GGUF",
            hf_file=_MAIN_NAME,
            registry=registry,
            client=client1,
            mmproj_file=_MMPROJ_NAME,
        )
    finally:
        await client1.aclose()

    assert job1.state == "cancelled"
    # Main file already installed (its download finished before the cancel)…
    main_final = _pull_root() / "cancel2-model" / _MAIN_NAME
    assert main_final.read_bytes() == main
    # …but the mmproj staging was discarded.
    mm_part, mm_sidecar = _part_paths("cancel2-model", _MMPROJ_NAME)
    assert not mm_part.exists()
    assert not mm_sidecar.exists()
    assert not (main_final.parent / _MMPROJ_NAME).exists()

    # ── Pass 2: plain re-pull finishes the pair and wires the registry. ──
    job2 = make_job("cancel2-model")
    client2 = httpx.AsyncClient(transport=_two_file_transport(main, mmproj))
    try:
        await run_pull(
            job2,
            hf_repo="Org/Cancel2-GGUF",
            hf_file=_MAIN_NAME,
            registry=registry,
            client=client2,
            mmproj_file=_MMPROJ_NAME,
        )
    finally:
        await client2.aclose()

    assert job2.state == "completed", f"got {job2.state}: {job2.error}"
    assert (main_final.parent / _MMPROJ_NAME).read_bytes() == mmproj
    entry = registry.get("cancel2-model")
    assert entry.mmproj == str(main_final.parent / _MMPROJ_NAME)


@pytest.mark.asyncio
async def test_run_pull_transient_error_mid_mmproj_resumes_second_file(
    tmp_hal0_home: str,
) -> None:
    """WS-11 + MR-7: a transport drop while streaming the mmproj preserves the
    mmproj's own .part + sidecar, and the next run_pull resumes THAT file via
    a Range request (per-file staging keys)."""
    main = _payload(_CHUNK_BYTES)
    mmproj = _payload(_CHUNK_BYTES * 3)
    registry = ModelRegistry()

    # ── Pass 1: main OK; mmproj drops after the first chunk. ──
    async def flaky_handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith(_MAIN_NAME):
            return httpx.Response(200, content=main, headers={"Content-Length": str(len(main))})

        async def agen():  # type: ignore[no-untyped-def]
            yield mmproj[:_CHUNK_BYTES]
            raise httpx.ReadError("dropped")

        return httpx.Response(200, headers={"Content-Length": str(len(mmproj))}, content=agen())

    job1 = make_job("flaky-vision")
    client1 = httpx.AsyncClient(transport=httpx.MockTransport(flaky_handler))
    try:
        await run_pull(
            job1,
            hf_repo="Org/Flaky-GGUF",
            hf_file=_MAIN_NAME,
            registry=registry,
            client=client1,
            mmproj_file=_MMPROJ_NAME,
        )
    finally:
        await client1.aclose()

    assert job1.state == "failed"
    mm_part, mm_sidecar = _part_paths("flaky-vision", _MMPROJ_NAME)
    assert mm_part.exists(), "transient mmproj error must preserve its .part"
    assert json.loads(mm_sidecar.read_text(encoding="utf-8"))["bytes"] == _CHUNK_BYTES
    # The MAIN file's staging is gone (it installed successfully).
    assert not _part_paths("flaky-vision", _MAIN_NAME)[0].exists()

    # ── Pass 2: mmproj resumes with a Range header; main re-streams fresh. ──
    seen: dict[str, str] = {}

    async def resume_handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith(_MAIN_NAME):
            return httpx.Response(200, content=main, headers={"Content-Length": str(len(main))})
        seen["range"] = req.headers.get("range", "")
        remainder = mmproj[_CHUNK_BYTES:]
        return httpx.Response(
            206,
            headers={
                "Content-Length": str(len(remainder)),
                "Content-Range": f"bytes {_CHUNK_BYTES}-{len(mmproj) - 1}/{len(mmproj)}",
            },
            content=remainder,
        )

    job2 = make_job("flaky-vision")
    client2 = httpx.AsyncClient(transport=httpx.MockTransport(resume_handler))
    try:
        await run_pull(
            job2,
            hf_repo="Org/Flaky-GGUF",
            hf_file=_MAIN_NAME,
            registry=registry,
            client=client2,
            mmproj_file=_MMPROJ_NAME,
        )
    finally:
        await client2.aclose()

    assert seen["range"] == f"bytes={_CHUNK_BYTES}-"
    assert job2.state == "completed", f"got {job2.state}: {job2.error}"
    mmproj_final = Path(job2.path).parent / _MMPROJ_NAME  # type: ignore[arg-type]
    assert mmproj_final.read_bytes() == mmproj
    assert job2.files[1].sha256 == hashlib.sha256(mmproj).hexdigest()
    assert registry.get("flaky-vision").mmproj == str(mmproj_final)


# ── WS-12: integrity verification against HF's advertised hash ───────────────


@pytest.mark.asyncio
async def test_run_pull_checksum_mismatch_fails_and_keeps_part(
    tmp_hal0_home: str,
) -> None:
    """WS-12 primary guard: when HF advertises an LFS sha256 (X-Linked-ETag)
    that doesn't match the streamed bytes, the job fails with
    ``pull.checksum_mismatch``, nothing is installed, and the .part is kept
    for diagnosis (its resume sidecar dropped)."""
    body = _payload(2048)
    wrong = "0" * 64

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=body,
            headers={"Content-Length": str(len(body)), "X-Linked-ETag": f'"{wrong}"'},
        )

    job = make_job("corrupt-model")
    registry = ModelRegistry()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await run_pull(
            job,
            hf_repo="Org/Corrupt-GGUF",
            hf_file="corrupt.gguf",
            registry=registry,
            client=client,
        )
    finally:
        await client.aclose()

    assert job.state == "failed"
    assert job.error_code == "pull.checksum_mismatch"
    assert wrong in (job.error or "")
    # Nothing installed; nothing registered.
    assert job.path is None
    final = _pull_root() / "corrupt-model" / "corrupt.gguf"
    assert not final.exists()
    assert not registry.has("corrupt-model")
    # The complete .part is preserved for diagnosis; the sidecar is dropped
    # so a retry starts clean instead of "resuming" corrupt bytes.
    part, sidecar = _part_paths("corrupt-model", "corrupt.gguf")
    assert part.exists(), "checksum mismatch must preserve the .part"
    assert part.read_bytes() == body
    assert not sidecar.exists()


@pytest.mark.asyncio
async def test_run_pull_checksum_match_completes(tmp_hal0_home: str) -> None:
    """False-positive guard: a CORRECT advertised hash verifies and completes."""
    body = _payload(2048)
    digest = hashlib.sha256(body).hexdigest()

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=body,
            headers={"Content-Length": str(len(body)), "X-Linked-ETag": f'"{digest}"'},
        )

    job = make_job("verified-model")
    registry = ModelRegistry()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await run_pull(
            job,
            hf_repo="Org/Verified-GGUF",
            hf_file="verified.gguf",
            registry=registry,
            client=client,
        )
    finally:
        await client.aclose()

    assert job.state == "completed", f"got {job.state}: {job.error}"
    assert job.sha256 == digest
    assert job.files[0].expected_sha256 == digest


@pytest.mark.asyncio
async def test_run_pull_no_advertised_hash_stays_record_only(
    tmp_hal0_home: str,
) -> None:
    """WS-12: non-LFS files (no sha256-shaped X-Linked-ETag — e.g. a git-blob
    sha1 etag) keep the historic record-only behaviour: complete + record the
    computed sha256, no comparison."""
    body = _payload(2048)
    sha1_etag = "a" * 40  # git blob etag shape, NOT a sha256

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=body,
            headers={
                "Content-Length": str(len(body)),
                "ETag": f'"{sha1_etag}"',
                "X-Linked-ETag": f'"{sha1_etag}"',
            },
        )

    job = make_job("nolfs-model")
    registry = ModelRegistry()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await run_pull(
            job,
            hf_repo="Org/NoLFS-GGUF",
            hf_file="nolfs.gguf",
            registry=registry,
            client=client,
        )
    finally:
        await client.aclose()

    assert job.state == "completed", f"got {job.state}: {job.error}"
    assert job.files[0].expected_sha256 is None, "sha1 etag must not be treated as sha256"
    assert job.sha256 == hashlib.sha256(body).hexdigest()
    assert registry.get("nolfs-model").metadata.get("sha256") == job.sha256


@pytest.mark.asyncio
async def test_run_pull_expected_hash_captured_from_redirect_hop(
    tmp_hal0_home: str,
) -> None:
    """WS-12: on the real HF flow the sha256 rides X-Linked-ETag on the 302
    from huggingface.co, not on the CDN's final 200 — the capture must look
    at ``resp.history``."""
    body = _payload(2048)
    wrong = "f" * 64

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "huggingface.co":
            return httpx.Response(
                302,
                headers={
                    "Location": "https://cdn.example/blob",
                    "X-Linked-ETag": f'"{wrong}"',
                },
            )
        return httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})

    job = make_job("redirected-model")
    registry = ModelRegistry()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    try:
        await run_pull(
            job,
            hf_repo="Org/Redirected-GGUF",
            hf_file="redirected.gguf",
            registry=registry,
            client=client,
        )
    finally:
        await client.aclose()

    assert job.state == "failed"
    assert job.error_code == "pull.checksum_mismatch"
    assert job.files[0].expected_sha256 == wrong
