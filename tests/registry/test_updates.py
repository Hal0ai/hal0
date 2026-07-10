"""Tests for HF model update detection (hal0.registry.updates).

Uses ``httpx.MockTransport`` to stub HuggingFace's ``/tree/main`` so the
sha-vs-oid comparison runs without touching the network.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from hal0.registry import updates as upd
from hal0.registry.model import Model

# A canonical 64-hex sha256 and a differing one.
SHA_A = "a" * 64
SHA_B = "b" * 64


def _model(model_id: str, *, sha: str | None, repo: str = "org/repo", filename: str = "m.gguf") -> Model:
    meta: dict[str, Any] = {}
    if sha is not None:
        meta["sha256"] = sha
    return Model(
        id=model_id,
        name=model_id,
        path=f"/var/lib/hal0/models/{model_id}/{filename}",
        hf_repo=repo,
        hf_filename=filename,
        metadata=meta,
    )


def _tree_transport(oid_by_path: dict[str, str | None]) -> httpx.MockTransport:
    """Return a transport whose ``/tree/main`` serves the given LFS oids.

    A ``None`` oid emits a non-LFS entry (no ``lfs`` block) so the "no remote
    sha" path is exercised.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/tree/main")
        entries = []
        for path, oid in oid_by_path.items():
            entry: dict[str, Any] = {"type": "file", "path": path, "size": 100}
            if oid is not None:
                entry["lfs"] = {"oid": oid, "size": 100}
            entries.append(entry)
        return httpx.Response(200, json=entries)

    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def _clear_cache() -> Any:
    upd.clear_cache()
    yield
    upd.clear_cache()


@pytest.mark.asyncio
async def test_update_available_when_remote_oid_differs() -> None:
    client = httpx.AsyncClient(transport=_tree_transport({"m.gguf": SHA_B}))
    infos = await upd.check_updates([_model("qwen", sha=SHA_A)], client=client)
    await client.aclose()
    assert len(infos) == 1
    info = infos[0]
    assert info.update_available is True
    assert info.current_sha == SHA_A
    assert info.remote_sha == SHA_B


@pytest.mark.asyncio
async def test_no_update_when_oid_matches() -> None:
    client = httpx.AsyncClient(transport=_tree_transport({"m.gguf": SHA_A}))
    infos = await upd.check_updates([_model("qwen", sha=SHA_A)], client=client)
    await client.aclose()
    assert infos[0].update_available is False
    assert infos[0].reason is None


@pytest.mark.asyncio
async def test_oid_prefixed_form_normalises() -> None:
    # HF sometimes reports "sha256:<hex>" — must compare equal to bare hex.
    client = httpx.AsyncClient(transport=_tree_transport({"m.gguf": f"sha256:{SHA_A}"}))
    infos = await upd.check_updates([_model("qwen", sha=SHA_A)], client=client)
    await client.aclose()
    assert infos[0].update_available is False


@pytest.mark.asyncio
async def test_row_without_stored_sha_is_skipped() -> None:
    # is_checkable rejects a row with no metadata.sha256 → omitted entirely.
    client = httpx.AsyncClient(transport=_tree_transport({"m.gguf": SHA_B}))
    infos = await upd.check_updates([_model("qwen", sha=None)], client=client)
    await client.aclose()
    assert infos == []


@pytest.mark.asyncio
async def test_non_lfs_remote_reports_no_remote_sha() -> None:
    client = httpx.AsyncClient(transport=_tree_transport({"m.gguf": None}))
    infos = await upd.check_updates([_model("qwen", sha=SHA_A)], client=client)
    await client.aclose()
    assert infos[0].update_available is False
    assert infos[0].reason == "no_remote_sha"


@pytest.mark.asyncio
async def test_unreachable_repo_degrades_to_unknown() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    infos = await upd.check_updates([_model("qwen", sha=SHA_A)], client=client)
    await client.aclose()
    assert infos[0].update_available is False
    assert infos[0].reason == "repo_unreachable"


@pytest.mark.asyncio
async def test_basename_fallback_matches_subdir_filename() -> None:
    # Stored coord is a bare basename; remote path carries a subdir prefix.
    client = httpx.AsyncClient(transport=_tree_transport({"gguf/m.gguf": SHA_B}))
    infos = await upd.check_updates([_model("qwen", sha=SHA_A)], client=client)
    await client.aclose()
    assert infos[0].update_available is True
    assert infos[0].remote_sha == SHA_B


@pytest.mark.asyncio
async def test_shared_repo_fetched_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=[{"type": "file", "path": "m.gguf", "lfs": {"oid": SHA_B}}])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    infos = await upd.check_updates(
        [_model("a", sha=SHA_A), _model("b", sha=SHA_A)],
        client=client,
    )
    await client.aclose()
    assert len(infos) == 2
    assert all(i.update_available for i in infos)
    # Two models, one repo → a single tree fetch thanks to the per-repo cache.
    assert calls["n"] == 1


def test_is_checkable_gates_on_coords_and_sha() -> None:
    assert upd.is_checkable(_model("ok", sha=SHA_A)) is True
    assert upd.is_checkable(_model("no-sha", sha=None)) is False
    flm = Model(id="flm", name="flm", path="/x", hf_repo="", hf_filename="", metadata={"sha256": SHA_A})
    assert upd.is_checkable(flm) is False
