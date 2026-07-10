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

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hal0.api.routes import models as models_route

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
    # The background task must receive the pinned destination + coords.
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
