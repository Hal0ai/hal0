"""Tests for POST /api/models/{id}/verify-files — on-disk truth for a row (#2212).

Read-only stat of the row's model file + mmproj sidecar. Advisory: a missing or
unreadable file is a 200 with nulls, not an error; only an unknown ``model_id``
404s.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app

# ── isolated app fixture (mirrors test_models_feasibility.py:22-43) ─────────


@pytest.fixture
def crud_app(tmp_hal0_home: str) -> FastAPI:
    extra_root = Path(tmp_hal0_home) / "crud-models"
    extra_root.mkdir(parents=True)
    etc = Path(tmp_hal0_home) / "etc" / "hal0"
    etc.mkdir(parents=True, exist_ok=True)
    (etc / "hal0.toml").write_text(
        f'[models]\nroots = ["{extra_root}"]\nauto_scan_on_start = false\n',
        encoding="utf-8",
    )
    return create_app()


@pytest.fixture
def crud_client(crud_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(crud_app) as c:
        yield c


@pytest.fixture
def crud_models_root(tmp_hal0_home: str) -> Path:
    return Path(tmp_hal0_home) / "crud-models"


def _register(
    client: TestClient,
    models_root: Path,
    *,
    mid: str = "vf-test",
    nbytes: int = 64,
    mmproj: Path | None = None,
) -> Path:
    """Register a model over a real file and return that file's path."""
    fpath = models_root / f"{mid}.gguf"
    fpath.write_bytes(b"\x00" * nbytes)
    body: dict[str, object] = {"id": mid, "path": str(fpath), "size_bytes": nbytes}
    if mmproj is not None:
        body["mmproj"] = str(mmproj)
    r = client.post("/api/models", json=body)
    assert r.status_code == 201, r.text
    return fpath


# ── tests ─────────────────────────────────────────────────────────────────


def test_verify_files_exists_and_size_matches(
    crud_client: TestClient, crud_models_root: Path
) -> None:
    fpath = _register(crud_client, crud_models_root)
    r = crud_client.post("/api/models/vf-test/verify-files")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model"] == {
        "path": str(fpath),
        "exists": True,
        "size_bytes": 64,
        "size_matches": True,
    }
    # No projector paired on this row.
    assert body["mmproj"] is None


def test_verify_files_missing_file_degrades_to_nulls(
    crud_client: TestClient, crud_models_root: Path
) -> None:
    """A file that vanished under a live row is a 200, not a 404 or a 500."""
    fpath = _register(crud_client, crud_models_root, mid="vf-gone")
    fpath.unlink()
    r = crud_client.post("/api/models/vf-gone/verify-files")
    assert r.status_code == 200, r.text
    model = r.json()["model"]
    assert model["path"] == str(fpath)
    assert model["exists"] is False
    # Nothing on disk to size or compare — both null, never a guessed False.
    assert model["size_bytes"] is None
    assert model["size_matches"] is None


def test_verify_files_size_mismatch(crud_client: TestClient, crud_models_root: Path) -> None:
    """Bytes changed under the row → exists, but size_matches is False."""
    fpath = _register(crud_client, crud_models_root, mid="vf-drift")
    fpath.write_bytes(b"\x00" * 128)
    r = crud_client.post("/api/models/vf-drift/verify-files")
    assert r.status_code == 200, r.text
    model = r.json()["model"]
    assert model["exists"] is True
    assert model["size_bytes"] == 128
    assert model["size_matches"] is False


def test_verify_files_reports_mmproj_sidecar(
    crud_client: TestClient, crud_models_root: Path
) -> None:
    """A paired projector gets the same shape — minus size_matches, which has
    no stored size to compare against."""
    mm = crud_models_root / "mmproj-Q8.gguf"
    mm.write_bytes(b"\x00" * 32)
    _register(crud_client, crud_models_root, mid="vf-vision", mmproj=mm)
    r = crud_client.post("/api/models/vf-vision/verify-files")
    assert r.status_code == 200, r.text
    assert r.json()["mmproj"] == {
        "path": str(mm),
        "exists": True,
        "size_bytes": 32,
        "size_matches": None,
    }


def test_verify_files_mmproj_paired_but_absent(
    crud_client: TestClient, crud_models_root: Path
) -> None:
    """The case #2212 named: a projector paired on the row that never landed."""
    mm = crud_models_root / "mmproj-ghost.gguf"
    mm.write_bytes(b"\x00" * 32)
    _register(crud_client, crud_models_root, mid="vf-ghost", mmproj=mm)
    mm.unlink()
    r = crud_client.post("/api/models/vf-ghost/verify-files")
    assert r.status_code == 200, r.text
    sidecar = r.json()["mmproj"]
    assert sidecar["path"] == str(mm)
    assert sidecar["exists"] is False
    assert sidecar["size_bytes"] is None


def test_verify_files_unknown_model_404s(crud_client: TestClient) -> None:
    r = crud_client.post("/api/models/nope/verify-files")
    assert r.status_code == 404, r.text


def test_verify_files_get_does_not_resolve_as_a_model_id(
    crud_client: TestClient, crud_models_root: Path
) -> None:
    """The ``/feasibility`` trap (a literal single segment eaten by the
    ``/{model_id}`` catch-all) has no analogue on a two-segment path. A stray
    GET here falls through to the SPA catch-all's bare ``api/`` 404
    (api/__init__.py:2931-2934) — an empty body, NOT a misleading
    ``model.not_found`` envelope for a model called "verify-files"."""
    _register(crud_client, crud_models_root, mid="vf-method")
    r = crud_client.get("/api/models/vf-method/verify-files")
    assert r.status_code == 404
    assert "verify-files" not in r.text
