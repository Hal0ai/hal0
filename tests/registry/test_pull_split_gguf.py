"""Split-GGUF pulls: one ``hf_file`` (the first part) pulls the whole set.

llama.cpp opens a split model from its first part and finds parts 2..N by
the ``-0000i-of-0000N`` name in the same directory, so the pull has to land
every part there under names that keep that pattern — including the
capability-grouped layout, which otherwise renames a GGUF to ``model.gguf``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

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


def _transport(bodies: dict[str, bytes], seen: list[str]) -> httpx.MockTransport:
    """Serve ``bodies`` keyed by repo-relative filename; record each request."""

    def handler(req: httpx.Request) -> httpx.Response:
        rel = req.url.path.split("/resolve/main/", 1)[-1]
        seen.append(rel)
        body = bodies.get(rel)
        if body is None:
            return httpx.Response(404, content=b"")
        return httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})

    return httpx.MockTransport(handler)


def _bodies() -> dict[str, bytes]:
    out = {rel: bytes([i]) * (1000 + i) for i, rel in enumerate(_PARTS, 1)}
    out[_MMPROJ] = b"m" * 300
    return out


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
    monkeypatch.setattr(pull, "_pull_root", lambda: tmp_path)
    bodies = _bodies()
    seen: list[str] = []
    job = make_job("nemo")
    registry = ModelRegistry()
    client = httpx.AsyncClient(transport=_transport(bodies, seen))
    try:
        await run_pull(
            job,
            hf_repo="unsloth/Nemo-GGUF",
            hf_file=_PARTS[0],
            registry=registry,
            client=client,
            capability="chat",
            mmproj_file=_MMPROJ,
            extra_files=[],
        )
    finally:
        await client.aclose()

    assert job.state == "completed", f"got {job.state}: {job.error}"
    assert seen == [*_PARTS, _MMPROJ]

    model_dir = tmp_path / "chat" / "nemo"
    finals = [model_dir / f"model-{i:05d}-of-00003.gguf" for i in (1, 2, 3)]
    for rel, final in zip(_PARTS, finals, strict=True):
        assert final.read_bytes() == bodies[rel]
    assert not (model_dir / "model.gguf").exists()
    assert (model_dir / _MMPROJ).read_bytes() == bodies[_MMPROJ]

    parts_bytes = sum(len(bodies[rel]) for rel in _PARTS)
    assert job.path == str(finals[0])
    assert job.bytes_downloaded == parts_bytes + len(bodies[_MMPROJ])
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
    monkeypatch.setattr(pull, "_pull_root", lambda: tmp_path)
    bodies = _bodies()
    job = make_job("nemo")
    client = httpx.AsyncClient(transport=_transport(bodies, []))
    try:
        await run_pull(
            job,
            hf_repo="unsloth/Nemo-GGUF",
            hf_file=_PARTS[0],
            registry=ModelRegistry(),
            client=client,
            extra_files=[],
        )
    finally:
        await client.aclose()

    assert job.state == "completed", f"got {job.state}: {job.error}"
    for rel in _PARTS:
        assert (tmp_path / "nemo" / rel).read_bytes() == bodies[rel]


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
