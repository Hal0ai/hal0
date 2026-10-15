"""The kokoro-v1 pull lays down the bundle the runner loads (#2739).

The kokoro runner globs ``kokoro-*.onnx`` and ``voices*.bin`` under
``--model_path``. Before #2739 the capability-grouped pull renamed the ONNX
file ``model.gguf`` and never fetched the voices pack, so the pulled copy was
dead weight and every tts start re-downloaded ~354 MB from GitHub.
"""

from __future__ import annotations

import fnmatch
import hashlib
from pathlib import Path

import httpx
import pytest

from hal0.config.store import capability_model_dir
from hal0.db import repository
from hal0.db.connection import connect
from hal0.providers.kokoro import check_kokoro_bundle
from hal0.registry import pull
from hal0.registry.curated import get_curated
from hal0.registry.pull import make_job, run_pull
from hal0.registry.store import ModelRegistry

_REPO = "mikkoph/kokoro-onnx"
_ONNX = "kokoro-v1.0.onnx"
_VOICES = "voices-v1.0.bin"
_ONNX_BYTES = b"onnx-model-bytes" * 64
_VOICES_BYTES = b"voices-pack" * 32


def _transport(seen: list[str] | None = None) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if seen is not None:
            seen.append(path)
        if path.endswith(f"/resolve/main/{_ONNX}"):
            body = _ONNX_BYTES
        elif path.endswith(f"/resolve/main/{_VOICES}"):
            body = _VOICES_BYTES
        else:
            return httpx.Response(404, content=b"")
        return httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})

    return httpx.MockTransport(handler)


async def _pull(registry: ModelRegistry, **kwargs) -> pull.PullJob:
    job = make_job("kokoro-v1")
    client = httpx.AsyncClient(transport=_transport())
    try:
        await run_pull(
            job,
            hf_repo=_REPO,
            hf_file=_ONNX,
            registry=registry,
            client=client,
            **kwargs,
        )
    finally:
        await client.aclose()
    assert job.state == "completed", f"got {job.state}: {job.error}"
    return job


def test_curated_row_names_the_files_the_runner_globs() -> None:
    row = get_curated("kokoro-v1")
    assert row is not None
    assert row.hf_repo == _REPO
    assert fnmatch.fnmatch(row.hf_file, "kokoro-*.onnx")
    assert row.extra_files == [_VOICES]
    assert all(fnmatch.fnmatch(f, "voices*.bin") for f in row.extra_files)


def test_non_gguf_capability_pull_keeps_its_upstream_name(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pull, "_pull_root", lambda: tmp_path)
    dest = pull._final_path_for_entry("kokoro-v1", _ONNX, None, "tts")
    assert dest == tmp_path / "tts" / "kokoro-v1" / _ONNX
    # GGUF still lands as the canonical model.gguf.
    gguf = pull._final_path_for_entry("m", "dir/Q4.gguf", None, "chat")
    assert gguf.name == "model.gguf"


@pytest.mark.asyncio
async def test_kokoro_pull_installs_the_runner_bundle(tmp_hal0_home: str) -> None:
    registry = ModelRegistry()
    job = await _pull(registry, capability="tts")

    bundle = capability_model_dir("tts", "kokoro-v1")
    assert Path(job.path) == bundle / _ONNX  # type: ignore[arg-type]
    assert (bundle / _ONNX).read_bytes() == _ONNX_BYTES
    assert (bundle / _VOICES).read_bytes() == _VOICES_BYTES
    assert not (bundle / "model.gguf").exists()
    assert [f.kind for f in job.files] == ["model", "sidecar"]
    assert job.bytes_downloaded == len(_ONNX_BYTES) + len(_VOICES_BYTES)

    # The directory the pull wrote is exactly what the provider launches.
    check_kokoro_bundle(bundle)
    assert registry.get("kokoro-v1").path == str(bundle / _ONNX)

    db = registry.db_path
    assert db is not None
    if db is not None:
        with connect(db) as conn:
            rows = {r["rel"]: r for r in repository.list_model_files(conn, "kokoro-v1")}
        assert set(rows) == {_ONNX, _VOICES}
        assert rows[_VOICES]["role"] == "sidecar"


@pytest.mark.asyncio
async def test_repull_replaces_a_pre_fix_mislabeled_install(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A box that pulled before #2739 has ``model.gguf`` and no voices pack.

    Re-pulling installs the real names, removes the mislabeled copy, and
    re-points the store blob at the live file so hardlink dedup still works.
    """
    registry = ModelRegistry()
    # Reproduce the pre-fix layout: canonical model.gguf, no sidecar.
    with monkeypatch.context() as m:
        m.setattr(pull, "_canonical_name", lambda _f: "model.gguf")
        await _pull(registry, capability="tts", extra_files=[])
    bundle = capability_model_dir("tts", "kokoro-v1")
    assert (bundle / "model.gguf").is_file()
    with pytest.raises(Exception, match="hal0 model pull kokoro-v1"):
        check_kokoro_bundle(bundle)

    await _pull(registry, capability="tts")

    assert not (bundle / "model.gguf").exists()
    check_kokoro_bundle(bundle)
    assert registry.get("kokoro-v1").path == str(bundle / _ONNX)
    db = getattr(registry, "db_path", None)
    if db is not None:
        sha = hashlib.sha256(_ONNX_BYTES).hexdigest()
        with connect(db) as conn:
            blob = repository.get_blob(conn, sha)
        assert blob is not None
        assert blob["blob_path"] == str(bundle / _ONNX)


@pytest.mark.asyncio
async def test_in_place_update_of_a_mislabeled_row_lands_the_real_name(
    tmp_hal0_home: str,
) -> None:
    """``hal0 model update kokoro-v1`` pins dest to the row's old model.gguf."""
    registry = ModelRegistry()
    bundle = capability_model_dir("tts", "kokoro-v1")
    bundle.mkdir(parents=True)
    (bundle / "model.gguf").write_bytes(_ONNX_BYTES)

    await _pull(registry, dest_override=str(bundle / "model.gguf"))

    assert not (bundle / "model.gguf").exists()
    check_kokoro_bundle(bundle)


@pytest.mark.asyncio
async def test_explicit_empty_extra_files_pulls_only_the_main_file(tmp_hal0_home: str) -> None:
    registry = ModelRegistry()
    job = await _pull(registry, capability="tts", extra_files=[])
    assert [f.kind for f in job.files] == ["model"]
    assert not (capability_model_dir("tts", "kokoro-v1") / _VOICES).exists()


@pytest.mark.asyncio
async def test_extras_only_follow_the_curated_repo(tmp_hal0_home: str) -> None:
    """Another repo under the same id gets exactly the file the operator named."""
    seen: list[str] = []
    job = make_job("kokoro-v1")
    client = httpx.AsyncClient(transport=_transport(seen))
    try:
        await run_pull(
            job,
            hf_repo="someone/else",
            hf_file=_ONNX,
            registry=ModelRegistry(),
            client=client,
            capability="tts",
        )
    finally:
        await client.aclose()
    assert job.state == "completed", f"got {job.state}: {job.error}"
    assert [f.kind for f in job.files] == ["model"]
    assert not any(p.endswith(_VOICES) for p in seen)


@pytest.mark.asyncio
async def test_an_unrelated_model_gguf_is_left_alone(tmp_hal0_home: str) -> None:
    """Only the old copy of THIS model is removed, never some other file."""
    bundle = capability_model_dir("tts", "kokoro-v1")
    bundle.mkdir(parents=True)
    (bundle / "model.gguf").write_bytes(b"something else entirely")

    await _pull(ModelRegistry(), capability="tts")

    assert (bundle / "model.gguf").read_bytes() == b"something else entirely"
