"""Tests for the model HF-update surface.

Covers:
  * ``GET /api/models/updates/check`` — verdicts, TTL cache, ?refresh=1.
  * ``/api/models`` rows carrying the merged ``update_available`` flag —
    including the self-heal recompute after an applied update changes
    the row's stored sha without a fresh check.
  * ``POST /api/models/{id}/update`` — 202 job handle with the dest
    pinned to the row's existing path, plus the 404/422 error envelopes.

The HF tree fetch is monkeypatched at the route module boundary
(``models.fetch_remote_lfs_shas``), same style as the inspect tests'
``_fetch_hf_repo`` interception.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from hal0.api.routes import models as models_route
from hal0.db import repository
from hal0.db.connection import connect, tx
from hal0.registry.pull import make_job, run_pull

SHA_OLD = "a" * 64
SHA_NEW = "b" * 64


def _register(client: TestClient, tmp_hal0_home: str, model_id: str, **overrides: Any) -> Path:
    """POST a registry row whose file exists on disk; return its path."""
    fpath = Path(tmp_hal0_home) / f"{model_id}.gguf"
    fpath.write_bytes(b"GGUF" + b"\x00" * 8)
    body: dict[str, Any] = {
        "id": model_id,
        "path": str(fpath),
        "hf_repo": f"org/{model_id}",
        "hf_filename": f"{model_id}.gguf",
        "metadata": {"sha256": SHA_OLD},
    }
    body.update(overrides)
    res = client.post("/api/models", json=body)
    assert res.status_code == 201, res.text
    return fpath


def _patch_tree_fetch(
    monkeypatch: pytest.MonkeyPatch,
    repo_files: dict[str, dict[str, str] | None],
) -> list[set[str]]:
    """Replace the route's tree fetch with a canned map; record call args."""
    calls: list[set[str]] = []

    async def fake_fetch(repos: set[str], **_kw: Any) -> dict[str, dict[str, str] | None]:
        calls.append(set(repos))
        return {r: repo_files.get(r) for r in repos}

    monkeypatch.setattr(models_route, "fetch_remote_lfs_shas", fake_fetch)
    return calls


# ── GET /api/models/updates/check ────────────────────────────────────────────


def test_check_reports_updates_and_reasons(
    client: TestClient, tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(client, tmp_hal0_home, "stale")
    _register(client, tmp_hal0_home, "fresh")
    _register(client, tmp_hal0_home, "gated")
    # No HF coords → not part of the check at all.
    _register(client, tmp_hal0_home, "handmade", hf_repo="", hf_filename="")

    _patch_tree_fetch(
        monkeypatch,
        {
            "org/stale": {"stale.gguf": SHA_NEW},
            "org/fresh": {"fresh.gguf": SHA_OLD},
            "org/gated": None,
        },
    )

    body = client.get("/api/models/updates/check").json()
    assert body["checked"] == 3
    assert body["updates_available"] == 1
    assert body["models"]["stale"]["update_available"] is True
    assert body["models"]["stale"]["remote_sha256"] == SHA_NEW
    assert body["models"]["fresh"]["update_available"] is False
    assert body["models"]["gated"]["reason"] == "repo_unreachable"
    assert "handmade" not in body["models"]


def test_check_is_ttl_cached_until_refresh(
    client: TestClient, tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(client, tmp_hal0_home, "m1")
    calls = _patch_tree_fetch(monkeypatch, {"org/m1": {"m1.gguf": SHA_OLD}})

    client.get("/api/models/updates/check")
    client.get("/api/models/updates/check")
    assert len(calls) == 1, "second call within the TTL must serve the snapshot"

    client.get("/api/models/updates/check?refresh=1")
    assert len(calls) == 2, "?refresh=1 must force a re-probe"


# ── /api/models row merge ────────────────────────────────────────────────────


def test_list_models_merges_update_available_flag(
    client: TestClient, tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(client, tmp_hal0_home, "stale")
    _register(client, tmp_hal0_home, "fresh")
    _patch_tree_fetch(
        monkeypatch,
        {"org/stale": {"stale.gguf": SHA_NEW}, "org/fresh": {"fresh.gguf": SHA_OLD}},
    )

    # Before any check ran: no flag on any row (absent, not False).
    rows = {m["id"]: m for m in client.get("/api/models").json()["models"]}
    assert "update_available" not in rows["stale"]

    client.get("/api/models/updates/check")
    rows = {m["id"]: m for m in client.get("/api/models").json()["models"]}
    assert rows["stale"]["update_available"] is True
    assert rows["fresh"]["update_available"] is False


def test_list_models_flag_self_heals_after_sha_refresh(
    client: TestClient, tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Applying an update rewrites metadata.sha256; the badge must clear on
    the next catalog poll WITHOUT waiting for the check TTL to expire."""
    _register(client, tmp_hal0_home, "stale")
    _patch_tree_fetch(monkeypatch, {"org/stale": {"stale.gguf": SHA_NEW}})
    client.get("/api/models/updates/check")

    rows = {m["id"]: m for m in client.get("/api/models").json()["models"]}
    assert rows["stale"]["update_available"] is True

    # Simulate the completed update: the pull registrar rewrote the sha.
    client.app.state.model_registry.update("stale", {"metadata": {"sha256": SHA_NEW}})
    rows = {m["id"]: m for m in client.get("/api/models").json()["models"]}
    assert rows["stale"]["update_available"] is False


# ── POST /api/models/{id}/update ─────────────────────────────────────────────


def test_update_pins_dest_to_existing_path(
    client: TestClient, tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fpath = _register(client, tmp_hal0_home, "stale")

    captured: dict[str, Any] = {}

    async def fake_run(job: Any, **kwargs: Any) -> None:
        captured.update(kwargs)
        job.state = "completed"

    monkeypatch.setattr(models_route, "_run_pull_with_events", fake_run)

    res = client.post("/api/models/stale/update")
    assert res.status_code == 202, res.text
    body = res.json()
    assert body["model_id"] == "stale"
    assert body["state"] == "queued"
    assert body["dest_path"] == str(fpath)
    assert body["update"] is True
    # The background task must receive the pinned destination + coords. The
    # route schedules it with asyncio.create_task and returns first, so under
    # TestClient it runs on the loop thread concurrently with this test.
    deadline = time.monotonic() + 3.0
    while not captured and time.monotonic() < deadline:
        time.sleep(0.01)
    assert captured["dest_override"] == str(fpath)
    assert captured["hf_repo"] == "org/stale"
    assert captured["hf_file"] == "stale.gguf"


def test_update_returns_in_flight_job_instead_of_duplicating(
    client: TestClient, tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(client, tmp_hal0_home, "stale")

    async def fake_run(job: Any, **kwargs: Any) -> None:
        # Leave the job non-terminal so the second POST sees it in flight.
        job.state = "running"

    monkeypatch.setattr(models_route, "_run_pull_with_events", fake_run)
    first = client.post("/api/models/stale/update").json()
    second = client.post("/api/models/stale/update").json()
    assert second["resumed"] is True
    assert second["id"] == first["id"]


def test_update_unknown_model_is_404(client: TestClient) -> None:
    res = client.post("/api/models/nope/update")
    assert res.status_code == 404


def test_update_without_hf_coords_is_422(client: TestClient, tmp_hal0_home: str) -> None:
    _register(client, tmp_hal0_home, "handmade", hf_repo="", hf_filename="")
    res = client.post("/api/models/handmade/update")
    assert res.status_code == 422


# ── split GGUF: the route wires shard rows in and follows a re-split ─────────

_REV = "0123456789abcdef0123456789abcdef01234567"
_REPO = "org/nemo"


def _split_parts(total: int) -> list[str]:
    return [f"Q4/Nemo-Q4-{i:05d}-of-{total:05d}.gguf" for i in range(1, total + 1)]


def _split_bodies(total: int, fill: int) -> dict[str, bytes]:
    return {rel: bytes([fill + i]) * (800 + i) for i, rel in enumerate(_split_parts(total))}


def _lfs_shas(bodies: dict[str, bytes]) -> dict[str, str]:
    return {rel: hashlib.sha256(body).hexdigest() for rel, body in bodies.items()}


def _hub(bodies: dict[str, bytes]) -> httpx.MockTransport:
    """A Hub serving one commit: revision resolve, its tree, and the files."""

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path.endswith("/revision/main"):
            return httpx.Response(200, json={"sha": _REV})
        if f"/tree/{_REV}" in path:
            return httpx.Response(
                200,
                json=[
                    {
                        "path": rel,
                        "type": "file",
                        "size": 134,
                        "lfs": {"size": len(body), "oid": hashlib.sha256(body).hexdigest()},
                    }
                    for rel, body in bodies.items()
                ],
            )
        body = bodies.get(path.split(f"/resolve/{_REV}/", 1)[-1])
        if body is None:
            return httpx.Response(404, content=b"")
        return httpx.Response(200, content=body, headers={"Content-Length": str(len(body))})

    return httpx.MockTransport(handler)


def _install_split(client: TestClient, store: Path, bodies: dict[str, bytes]) -> Path:
    """Pull ``bodies`` as the installed split set of model ``nemo``; return its dir."""

    async def go() -> Any:
        job = make_job("nemo")
        async with httpx.AsyncClient(transport=_hub(bodies)) as hub:
            await run_pull(
                job,
                hf_repo=_REPO,
                hf_file=sorted(bodies)[0],
                registry=client.app.state.model_registry,
                client=hub,
                capability="chat",
                extra_files=[],
            )
        return job

    job = asyncio.run(go())
    assert job.state == "completed", job.error
    return store / "chat" / "nemo"


def _wait_for_job(client: TestClient, model_id: str) -> Any:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        job = client.app.state.model_pull_jobs.get(model_id)
        if job is not None and job.state in ("completed", "failed", "cancelled"):
            return job
        time.sleep(0.02)
    raise AssertionError("update job did not finish")


def _run_update_against(monkeypatch: pytest.MonkeyPatch, bodies: dict[str, bytes]) -> None:
    """Route the update job through the real ``run_pull`` against a mocked Hub."""

    async def run(job: Any, *, hf_repo: str, hf_file: str, registry: Any, **kw: Any) -> None:
        async with httpx.AsyncClient(transport=_hub(bodies)) as hub:
            await run_pull(
                job,
                hf_repo=hf_repo,
                hf_file=hf_file,
                registry=registry,
                client=hub,
                dest_override=kw["dest_override"],
                extra_files=[],
            )

    monkeypatch.setattr(models_route, "_run_pull_with_events", run)


def test_check_reports_shard_changed_from_the_shard_rows(
    client: TestClient, tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route feeds ``load_shard_shas`` into the verdict: part 2 replaced
    upstream while part 1 is byte-identical is still an update (#2761)."""
    old = _split_parts(3)
    local = {rel: c * 64 for rel, c in zip(old, "bcd", strict=True)}
    _register(
        client,
        tmp_hal0_home,
        "nemo",
        hf_repo=_REPO,
        hf_filename=old[0],
        metadata={"sha256": "b" * 64},
    )
    db = client.app.state.model_registry.db_path
    with connect(db) as conn, tx(conn):
        for i, (rel, sha) in enumerate(local.items(), start=1):
            repository.upsert_model_file(
                conn, model_id="nemo", rel=rel, sha256=sha, lfs=True, role="shard", shard_index=i
            )
    _patch_tree_fetch(monkeypatch, {_REPO: {**local, old[1]: "e" * 64}})

    verdict = client.get("/api/models/updates/check").json()["models"]["nemo"]
    assert verdict["update_available"] is True
    assert verdict["reason"] == "shard_changed"
    assert verdict["changed_parts"] == [old[1]]


@pytest.mark.parametrize("new_total", [2, 4])
def test_update_follows_an_upstream_resplit(
    client: TestClient,
    tmp_hal0_home: str,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    new_total: int,
) -> None:
    """3 parts installed, upstream re-split to ``new_total``: the update pulls
    the new layout over the model and leaves exactly that set behind."""
    store = tmp_path_factory.mktemp("store")
    monkeypatch.setenv("HAL0_MODEL_STORE", str(store))
    model_dir = _install_split(client, store, _split_bodies(3, 1))
    new = _split_bodies(new_total, 50)
    _patch_tree_fetch(monkeypatch, {_REPO: _lfs_shas(new)})
    _run_update_against(monkeypatch, new)

    res = client.post("/api/models/nemo/update")
    assert res.status_code == 202, res.text
    assert res.json()["hf_file"] == _split_parts(new_total)[0]

    job = _wait_for_job(client, "nemo")
    assert job.state == "completed", job.error
    names = [f"model-{i:05d}-of-{new_total:05d}.gguf" for i in range(1, new_total + 1)]
    assert sorted(p.name for p in model_dir.glob("*.gguf")) == names
    assert [(model_dir / n).read_bytes() for n in names] == [
        new[r] for r in _split_parts(new_total)
    ]
    row = client.app.state.model_registry.get("nemo")
    assert row.hf_filename == _split_parts(new_total)[0]
    assert row.path == str(model_dir / names[0])
    with connect(client.app.state.model_registry.db_path) as conn:
        rels = {r["rel"] for r in repository.list_model_files(conn, "nemo")}
    assert rels == set(_split_parts(new_total))


def test_update_of_a_resplit_with_a_missing_part_fails_and_keeps_the_old_set(
    client: TestClient,
    tmp_hal0_home: str,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The completeness check still guards the new layout: part 4 absent
    upstream fails the job before any byte lands over the installed set."""
    store = tmp_path_factory.mktemp("store")
    monkeypatch.setenv("HAL0_MODEL_STORE", str(store))
    old = _split_bodies(3, 1)
    model_dir = _install_split(client, store, old)
    new = _split_bodies(4, 50)
    del new[_split_parts(4)[3]]
    _patch_tree_fetch(monkeypatch, {_REPO: _lfs_shas(new)})
    _run_update_against(monkeypatch, new)

    assert client.post("/api/models/nemo/update").status_code == 202
    job = _wait_for_job(client, "nemo")
    assert job.state == "failed"
    assert "split set is incomplete" in (job.error or "")
    assert [(model_dir / f"model-{i:05d}-of-00003.gguf").read_bytes() for i in (1, 2, 3)] == [
        old[r] for r in _split_parts(3)
    ]
    assert not list(model_dir.glob("*-of-00004.gguf"))


def test_update_keeps_the_stored_layout_while_upstream_still_has_it(
    client: TestClient, tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two layouts side by side is a valid repo shape: the installed one is
    re-pulled, not swapped for the other."""
    old = _split_parts(3)
    _register(client, tmp_hal0_home, "nemo", hf_repo=_REPO, hf_filename=old[0])
    _patch_tree_fetch(
        monkeypatch, {_REPO: {**_lfs_shas(_split_bodies(3, 1)), **_lfs_shas(_split_bodies(4, 9))}}
    )
    captured: dict[str, Any] = {}

    async def fake_run(job: Any, **kwargs: Any) -> None:
        captured.update(kwargs)
        job.state = "completed"

    monkeypatch.setattr(models_route, "_run_pull_with_events", fake_run)
    assert client.post("/api/models/nemo/update").status_code == 202
    deadline = time.monotonic() + 3.0
    while not captured and time.monotonic() < deadline:
        time.sleep(0.01)
    assert captured["hf_file"] == old[0]


def test_update_refuses_to_guess_between_two_new_layouts(
    client: TestClient, tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(client, tmp_hal0_home, "nemo", hf_repo=_REPO, hf_filename=_split_parts(3)[0])
    _patch_tree_fetch(
        monkeypatch, {_REPO: {**_lfs_shas(_split_bodies(2, 1)), **_lfs_shas(_split_bodies(4, 9))}}
    )
    res = client.post("/api/models/nemo/update")
    assert res.status_code == 422, res.text
