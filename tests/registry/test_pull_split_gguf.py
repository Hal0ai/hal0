"""Split-GGUF pulls: one ``hf_file`` (the first part) pulls the whole set.

llama.cpp opens a split model from its first part and finds parts 2..N by
the ``-0000i-of-0000N`` name in the same directory, so the pull has to land
every part there under names that keep that pattern — including the
capability-grouped layout, which otherwise renames a GGUF to ``model.gguf``.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from hal0.db import repository
from hal0.db.connection import connect
from hal0.registry import pull
from hal0.registry.pull import PullInvalidSource, _register_pulled, make_job, run_pull
from hal0.registry.store import ModelRegistry

_DIR = "UD-Q4_K_XL"
_STEM = "Nemo-UD-Q4_K_XL"
_PARTS = [f"{_DIR}/{_STEM}-{i:05d}-of-00003.gguf" for i in (1, 2, 3)]
_MMPROJ = "mmproj-BF16.gguf"


_REV = "0123456789abcdef0123456789abcdef01234567"


def _transport(
    bodies: dict[str, bytes],
    seen: list[str],
    *,
    fail: set[str] | None = None,
    listed: set[str] | None = None,
    on_get: Any = None,
) -> httpx.MockTransport:
    """Stub HF: revision resolve, the tree at that revision, and downloads.

    ``seen`` records each downloaded rel. ``fail`` rels answer 500 (a
    transient error the pull keeps its staging for); ``listed`` limits which
    files the tree reports (default: every body).
    """
    names = set(bodies) if listed is None else listed

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path.endswith("/revision/main"):
            return httpx.Response(200, json={"sha": _REV})
        if f"/tree/{_REV}" in path:
            rows = [
                {
                    "path": rel,
                    "type": "file",
                    "size": 134,
                    "lfs": {"size": len(body), "oid": hashlib.sha256(body).hexdigest()},
                }
                for rel, body in bodies.items()
                if rel in names
            ]
            return httpx.Response(200, json=rows)
        assert f"/resolve/{_REV}/" in path, f"download not pinned to the revision: {path}"
        rel = path.split(f"/resolve/{_REV}/", 1)[-1]
        if on_get is not None:
            on_get(rel)
        seen.append(rel)
        if fail and rel in fail:
            return httpx.Response(500, content=b"")
        body = bodies.get(rel)
        if body is None:
            return httpx.Response(404, content=b"")
        return httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})

    return httpx.MockTransport(handler)


def _bodies() -> dict[str, bytes]:
    out = {rel: bytes([i]) * (1000 + i) for i, rel in enumerate(_PARTS, 1)}
    out[_MMPROJ] = b"m" * 300
    return out


async def _pull(
    registry: ModelRegistry, transport: httpx.MockTransport, *, hf_file: str = _PARTS[0], **kw: Any
) -> Any:
    job = make_job("nemo")
    client = httpx.AsyncClient(transport=transport)
    try:
        await run_pull(
            job,
            hf_repo="unsloth/Nemo-GGUF",
            hf_file=hf_file,
            registry=registry,
            client=client,
            extra_files=[],
            **kw,
        )
    finally:
        await client.aclose()
    return job


def test_shard_set_derives_every_part_from_the_first() -> None:
    assert pull._shard_set(_PARTS[0]) == _PARTS
    assert pull._shard_set("plain-Q4_K_M.gguf") == []


def test_shard_set_refuses_a_later_part() -> None:
    with pytest.raises(PullInvalidSource):
        pull._shard_set(_PARTS[1])


def test_shard_dests_keep_the_split_pattern(tmp_path: Path) -> None:
    canonical = tmp_path / "model.gguf"
    assert [p.name for p in pull._shard_dests(canonical, 3)] == [
        "model-00001-of-00003.gguf",
        "model-00002-of-00003.gguf",
        "model-00003-of-00003.gguf",
    ]
    upstream = tmp_path / f"{_STEM}-00001-of-00003.gguf"
    assert pull._shard_dests(upstream, 3)[2].name == f"{_STEM}-00003-of-00003.gguf"


@pytest.mark.asyncio
async def test_split_pull_lands_every_part_and_the_projector(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HAL0_MODEL_STORE", str(tmp_path))
    bodies = _bodies()
    seen: list[str] = []
    registry = ModelRegistry()
    totals: list[int] = []
    job_box: list[Any] = []

    def on_get(rel: str) -> None:
        if job_box:
            totals.append(job_box[0].bytes_total)

    real_make_job = pull.make_job

    def capture_job(model_id: str) -> Any:
        job = real_make_job(model_id)
        job_box.append(job)
        return job

    monkeypatch.setattr(sys.modules[__name__], "make_job", capture_job)
    job = await _pull(
        registry,
        _transport(bodies, seen, on_get=on_get),
        capability="chat",
        mmproj_file=_MMPROJ,
    )

    assert job.state == "completed", f"got {job.state}: {job.error}"
    assert seen == [*_PARTS, _MMPROJ]
    # The whole set's size is the total from the first request on, so
    # progress never reaches 100% after part 1 and never moves backwards.
    everything = sum(len(b) for b in bodies.values())
    assert totals == [everything] * 4

    model_dir = tmp_path / "chat" / "nemo"
    finals = [model_dir / f"model-{i:05d}-of-00003.gguf" for i in (1, 2, 3)]
    for rel, final in zip(_PARTS, finals, strict=True):
        assert final.read_bytes() == bodies[rel]
    assert not (model_dir / "model.gguf").exists()
    assert (model_dir / _MMPROJ).read_bytes() == bodies[_MMPROJ]
    assert not pull._split_staging_dir("nemo").exists()

    parts_bytes = sum(len(bodies[rel]) for rel in _PARTS)
    assert job.path == str(finals[0])
    assert job.bytes_downloaded == everything
    assert [f["kind"] for f in job.as_dict()["files"]] == ["shard", "shard", "shard", "mmproj"]

    entry = registry.get("nemo")
    assert entry.path == str(finals[0])
    assert entry.size_bytes == parts_bytes
    assert entry.mmproj == str(model_dir / _MMPROJ)
    assert entry.hf_filename == _PARTS[0]
    assert entry.metadata["shards"] == [str(p) for p in finals]
    assert entry.metadata["sha256"] == hashlib.sha256(bodies[_PARTS[0]]).hexdigest()

    with connect(registry.db_path) as conn:
        rows = {r["rel"]: r for r in repository.list_model_files(conn, "nemo")}
    assert {rel: (rows[rel]["role"], rows[rel]["shard_index"]) for rel in _PARTS} == {
        _PARTS[0]: ("shard", 1),
        _PARTS[1]: ("shard", 2),
        _PARTS[2]: ("shard", 3),
    }
    assert rows[_MMPROJ]["role"] == "mmproj"


@pytest.mark.asyncio
async def test_split_pull_without_capability_keeps_upstream_names(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HAL0_MODEL_STORE", str(tmp_path))
    bodies = _bodies()
    job = await _pull(ModelRegistry(), _transport(bodies, []))

    assert job.state == "completed", f"got {job.state}: {job.error}"
    for rel in _PARTS:
        assert (tmp_path / "nemo" / rel).read_bytes() == bodies[rel]


@pytest.mark.asyncio
async def test_failed_part_leaves_the_installed_set_whole_and_the_retry_resumes(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A re-pull that fails on part 3 must not mix new and old parts, and the
    next attempt fetches only what is missing."""
    monkeypatch.setenv("HAL0_MODEL_STORE", str(tmp_path))
    registry = ModelRegistry()
    old = {rel: b"o" * 900 for rel in _PARTS}
    assert (await _pull(registry, _transport(old, []), capability="chat")).state == "completed"
    model_dir = tmp_path / "chat" / "nemo"
    finals = [model_dir / f"model-{i:05d}-of-00003.gguf" for i in (1, 2, 3)]

    new = _bodies()
    del new[_MMPROJ]
    seen: list[str] = []
    job = await _pull(registry, _transport(new, seen, fail={_PARTS[2]}), capability="chat")
    assert job.state == "failed"
    assert [f.read_bytes() for f in finals] == [b"o" * 900] * 3

    seen.clear()
    job = await _pull(registry, _transport(new, seen), capability="chat")
    assert job.state == "completed", f"got {job.state}: {job.error}"
    assert seen == [_PARTS[2]]
    assert [f.read_bytes() for f in finals] == [new[rel] for rel in _PARTS]


@pytest.mark.asyncio
async def test_cancel_discards_staged_parts(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HAL0_MODEL_STORE", str(tmp_path))
    job_box: list[Any] = []

    def on_get(rel: str) -> None:
        if rel == _PARTS[1]:
            job_box[0].cancel_requested = True

    real_make_job = pull.make_job

    def capture_job(model_id: str) -> Any:
        job = real_make_job(model_id)
        job_box.append(job)
        return job

    monkeypatch.setattr(sys.modules[__name__], "make_job", capture_job)
    job = await _pull(ModelRegistry(), _transport(_bodies(), [], on_get=on_get), capability="chat")
    assert job.state == "cancelled"
    assert not pull._split_staging_dir("nemo").exists()


@pytest.mark.asyncio
async def test_missing_part_in_the_tree_fails_before_downloading(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HAL0_MODEL_STORE", str(tmp_path))
    seen: list[str] = []
    bodies = _bodies()
    job = await _pull(
        ModelRegistry(), _transport(bodies, seen, listed={_PARTS[0], _PARTS[1]}), capability="chat"
    )
    assert job.state == "failed"
    assert _PARTS[2] in (job.error or "")
    assert seen == []


@pytest.mark.asyncio
async def test_switching_quant_drops_the_old_set(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Re-pulling the id with a 2-part quant removes the 3-part set's rows and
    files; the names the new set reuses keep the new bytes."""
    monkeypatch.setenv("HAL0_MODEL_STORE", str(tmp_path))
    registry = ModelRegistry()
    assert (
        await _pull(registry, _transport(_bodies(), []), capability="chat")
    ).state == "completed"

    two = [f"Q8_0/Nemo-Q8_0-{i:05d}-of-00002.gguf" for i in (1, 2)]
    bodies = {rel: bytes([9 + i]) * 700 for i, rel in enumerate(two)}
    job = await _pull(registry, _transport(bodies, []), hf_file=two[0], capability="chat")
    assert job.state == "completed", f"got {job.state}: {job.error}"

    model_dir = tmp_path / "chat" / "nemo"
    assert sorted(p.name for p in model_dir.glob("model-*.gguf")) == [
        "model-00001-of-00002.gguf",
        "model-00002-of-00002.gguf",
    ]
    with connect(registry.db_path) as conn:
        rels = {r["rel"] for r in repository.list_model_files(conn, "nemo")}
    assert rels == set(two)


def test_single_file_repull_clears_a_stale_shard_list(tmp_hal0_home: str, tmp_path: Path) -> None:
    registry = ModelRegistry()
    first = tmp_path / "model-00001-of-00002.gguf"
    common = {
        "model_id": "nemo",
        "sha256": "abc",
        "hf_repo": "unsloth/Nemo-GGUF",
        "capability": "chat",
    }
    _register_pulled(
        registry,
        path=str(first),
        size_bytes=10,
        hf_filename="Nemo-00001-of-00002.gguf",
        shards=[str(first), str(tmp_path / "model-00002-of-00002.gguf")],
        **common,
    )
    assert len(registry.get("nemo").metadata["shards"]) == 2

    _register_pulled(
        registry,
        path=str(tmp_path / "model.gguf"),
        size_bytes=5,
        hf_filename="Nemo-Q4_K_M.gguf",
        **common,
    )
    assert "shards" not in registry.get("nemo").metadata
