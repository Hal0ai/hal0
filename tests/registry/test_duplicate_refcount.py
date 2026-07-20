"""services.models_service.duplicate_model — refcount-reusing row duplication.

Deliverable 3 (UI-API-1 item 3): a duplicate is a new registry row referencing
the SAME weights (no byte copy). When the source is a pulled model carrying
``model_file`` rows, those are replicated under the new id and each shared
blob's ``store_blob.refcount`` is bumped — exactly the accounting a same-sha
pull performs — so a later delete of either row never orphans bytes the other
still uses.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.db import repository
from hal0.db.connection import connect, tx
from hal0.errors import BadRequest
from hal0.registry.model import Model
from hal0.registry.sqlite_store import SqliteModelRegistry
from hal0.registry.store import ModelAlreadyExists, ModelNotFound
from hal0.services.models_service import duplicate_model


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "hal0.db"


@pytest.fixture
def registry(db_path: Path) -> SqliteModelRegistry:
    return SqliteModelRegistry(db_path=db_path)


def _seed_pulled_model(registry: SqliteModelRegistry, db_path: Path, tmp_path: Path) -> str:
    """Register a model with one LFS ``model_file`` row + its ``store_blob``."""
    blob = tmp_path / "weights.gguf"
    blob.write_bytes(b"\x00" * 32)
    registry.add(
        Model(
            id="src",
            path=str(blob),
            name="Source",
            capabilities=["chat"],
            backends=["vulkan"],
        )
    )
    with connect(db_path) as conn, tx(conn):
        repository.insert_blob(
            conn, sha256="sha-weights", size_bytes=32, blob_path=str(blob), refcount=1
        )
        repository.insert_model_file(
            conn,
            model_id="src",
            rel="weights.gguf",
            dest=str(blob),
            size_bytes=32,
            sha256="sha-weights",
            lfs=True,
            role="model",
        )
    return "src"


def test_duplicate_replicates_model_files_and_bumps_refcount(
    registry: SqliteModelRegistry, db_path: Path, tmp_path: Path
) -> None:
    src = _seed_pulled_model(registry, db_path, tmp_path)

    out = duplicate_model(registry, source_id=src, new_id="copy")

    assert out["id"] == "copy"
    assert out["files_refcounted"] == 1
    # New row shares the source's weights path (no byte copy).
    assert registry.get("copy").path == registry.get(src).path

    with connect(db_path) as conn:
        copy_files = repository.list_model_files(conn, "copy")
        assert [f["rel"] for f in copy_files] == ["weights.gguf"]
        assert copy_files[0]["sha256"] == "sha-weights"
        # Refcount bumped from 1 -> 2: both rows now reference the blob.
        blob = repository.get_blob(conn, "sha-weights")
        assert blob["refcount"] == 2


def test_duplicate_hand_registered_model_has_no_files_to_refcount(
    registry: SqliteModelRegistry, tmp_path: Path
) -> None:
    """A hand-registered single-file model carries no ``model_file`` rows — the
    duplicate just shares ``path`` and reports zero refcounted files."""
    f = tmp_path / "m.gguf"
    f.write_bytes(b"\x00")
    registry.add(Model(id="hand", path=str(f)))
    out = duplicate_model(registry, source_id="hand", new_id="hand-copy")
    assert out["files_refcounted"] == 0
    assert registry.get("hand-copy").path == str(f)


def test_duplicate_with_profile_stamps_flags(registry: SqliteModelRegistry, tmp_path: Path) -> None:
    from hal0.profiles import ProfileCatalog

    f = tmp_path / "p.gguf"
    f.write_bytes(b"\x00")
    registry.add(Model(id="p", path=str(f)))
    resolved = ProfileCatalog().resolve("cpu-chat")
    out = duplicate_model(registry, source_id="p", new_id="p-copy", profile="cpu-chat")
    assert out["defaults"]["profile"] == "cpu-chat"
    assert out["defaults"]["extra_args"] == resolved.flags
    # Source untouched.
    assert registry.get("p").defaults is None


def test_duplicate_unknown_source_raises(registry: SqliteModelRegistry) -> None:
    with pytest.raises(ModelNotFound):
        duplicate_model(registry, source_id="nope", new_id="x")


def test_duplicate_conflicting_new_id_raises(registry: SqliteModelRegistry, tmp_path: Path) -> None:
    f = tmp_path / "c.gguf"
    f.write_bytes(b"\x00")
    registry.add(Model(id="a", path=str(f)))
    registry.add(Model(id="b", path=str(f)))
    with pytest.raises(ModelAlreadyExists):
        duplicate_model(registry, source_id="a", new_id="b")


def test_duplicate_same_id_raises(registry: SqliteModelRegistry, tmp_path: Path) -> None:
    f = tmp_path / "s.gguf"
    f.write_bytes(b"\x00")
    registry.add(Model(id="s", path=str(f)))
    with pytest.raises(BadRequest):
        duplicate_model(registry, source_id="s", new_id="s")
