"""Where a pull's file name comes from does not change the rule it is held to.

A file name sent with the request was already screened
(``check_body_hf_coordinates``). One read from the stored registry row, or from
the curated catalogue, is now screened the same way, and the destination the
pull derives is required to lie inside the store.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from hal0.registry import pull_jobs
from hal0.registry.pull import PullInvalidSource, make_job, run_pull
from hal0.registry.store import ModelRegistry


def _request(row: Any) -> Any:
    registry = SimpleNamespace(get=lambda _id: row)
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(model_registry=registry)))


def _row(filename: str) -> Any:
    return SimpleNamespace(hf_repo="org/name", hf_filename=filename)


@pytest.mark.parametrize("filename", ["model-Q4_K_M.gguf", "Q4_K_M/model-00001-of-00002.gguf"])
def test_stored_file_name_inside_the_repo_resolves(filename: str) -> None:
    assert pull_jobs.resolve_pull_source(_request(_row(filename)), "m") == ("org/name", filename)


@pytest.mark.parametrize(
    "filename", ["/model.gguf", "a/../../model.gguf", "a\\model.gguf", "./m.gguf"]
)
def test_stored_file_name_is_held_to_the_request_rule(filename: str) -> None:
    with pytest.raises(PullInvalidSource) as excinfo:
        pull_jobs.resolve_pull_source(_request(_row(filename)), "m")
    assert excinfo.value.details["field"] == "hf_filename"


def test_curated_file_name_is_held_to_the_request_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    curated = SimpleNamespace(hf_repo="org/name", hf_file="a/../../model.gguf")
    monkeypatch.setattr(pull_jobs, "get_curated", lambda _id: curated)
    with pytest.raises(PullInvalidSource):
        pull_jobs.resolve_pull_source(_request(SimpleNamespace(hf_repo="", hf_filename="")), "m")


@pytest.mark.parametrize("field", ["mmproj_file", "draft_file"])
def test_curated_companion_names_are_held_to_the_request_rule(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    curated = SimpleNamespace(
        hf_repo="org/name", hf_file="model.gguf", mmproj_file="", draft_file="", draft_repo=""
    )
    setattr(curated, field, "a/../../companion.gguf")
    monkeypatch.setattr(pull_jobs, "get_curated", lambda _id: curated)
    request = _request(SimpleNamespace(hf_repo="", hf_filename=""))
    with pytest.raises(PullInvalidSource):
        pull_jobs.resolve_pull_source_with_body(request, "m", None)


def test_curated_companion_names_inside_the_repo_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    curated = SimpleNamespace(
        hf_repo="org/name",
        hf_file="model.gguf",
        mmproj_file="mmproj/mmproj-f16.gguf",
        draft_file="",
        draft_repo="",
    )
    monkeypatch.setattr(pull_jobs, "get_curated", lambda _id: curated)
    request = _request(SimpleNamespace(hf_repo="", hf_filename=""))
    source = pull_jobs.resolve_pull_source_with_body(request, "m", None)
    assert source[2] == "mmproj/mmproj-f16.gguf"


@pytest.mark.asyncio
async def test_derived_destination_must_lie_inside_the_store(
    tmp_hal0_home: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second barrier: a pull whose derived destination is not under the
    store fails before anything is downloaded, and nothing is written."""
    store_root = tmp_path / "store"
    store_root.mkdir()
    monkeypatch.setenv("HAL0_MODEL_STORE", str(store_root))
    # <store>/m/a/../../../model.gguf is one level above the store.
    name = "a/../../../model.gguf"
    rev = "0123456789abcdef0123456789abcdef01234567"
    body = b"x" * 64
    downloads: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        # A source that lists the file, so the pull reaches the point where it
        # derives the destination. A real repository cannot list such a name.
        path = req.url.path
        if path.endswith("/revision/main"):
            return httpx.Response(200, json={"sha": rev})
        if f"/tree/{rev}" in path:
            lfs = {"size": len(body), "oid": hashlib.sha256(body).hexdigest()}
            return httpx.Response(
                200, json=[{"path": name, "type": "file", "size": 134, "lfs": lfs}]
            )
        downloads.append(path)
        return httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})

    job = make_job("m")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await run_pull(
            job,
            hf_repo="org/name",
            hf_file=name,
            registry=ModelRegistry(),
            client=client,
            extra_files=[],
        )
    finally:
        await client.aclose()
    assert job.state == "failed", job.error
    assert job.error_code == "store.path_escape", job.error
    assert downloads == []
    assert [p for p in tmp_path.rglob("*.gguf") if p.is_file()] == []
