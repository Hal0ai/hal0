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
    assert not _part_paths("cancel-me")[0].exists()
    assert not _part_paths("cancel-me")[1].exists()


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


def _part_paths(model_id: str) -> tuple[Path, Path]:
    """Return the deterministic ``.part`` and ``.part.json`` sidecar paths."""
    tmp = _tmp_dir()
    stem = _sanitise_id(model_id)
    return tmp / f"{stem}.part", tmp / f"{stem}.part.json"


def _seed_partial(
    model_id: str,
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
    part, sidecar = _part_paths(model_id)
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
    part, sidecar = _part_paths("resume-me")
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
    _seed_partial("ignore-me", url, _CHUNK_BYTES, total, body[:_CHUNK_BYTES])

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
    _seed_partial("changed-me", url, _CHUNK_BYTES, total, body[:_CHUNK_BYTES], etag='"old-etag"')

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
    part, sidecar = _part_paths("keepme")
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
    part, sidecar = _seed_partial("permfail", url, _CHUNK_BYTES, total, body[:_CHUNK_BYTES])
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
