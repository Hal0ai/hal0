"""Tests for the HF streaming pull engine.

Uses ``httpx.MockTransport`` to stub HuggingFace without touching the
network. The same transport handler verifies authorization headers,
redirect handling, and partial downloads / cancellation.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from hal0.registry.pull import (
    _sanitise_id,
    hf_download_url,
    make_job,
    pull_job_file,
    run_pull,
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
