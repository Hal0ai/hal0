"""Task 3: the TOML→SQLite import shim derives ``provider`` for legacy
entries that only ever carried ``backends`` tags.

Mirrors the fixture idiom from ``tests/registry/test_import_export_roundtrip.py``
(``_write_registry_toml`` + ``import_toml_to_sqlite`` + ``SqliteModelRegistry``).
"""

from __future__ import annotations

from pathlib import Path

import tomli_w

from hal0.registry.import_toml import import_toml_to_sqlite
from hal0.registry.sqlite_store import SqliteModelRegistry


def _write_registry_toml(path: Path, models: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        tomli_w.dump({"models": models}, f)


def test_import_toml_derives_provider_from_tags(tmp_path: Path) -> None:
    """A legacy entry with ``backends=["kokoro"]`` and no ``provider`` key
    imports with ``provider`` derived from the tag, not left ``None``."""
    registry_dir = tmp_path / "registry"
    rfile = registry_dir / "registry.toml"
    _write_registry_toml(
        rfile,
        {
            "kokoro-tts": {
                "name": "Kokoro TTS",
                "path": "/models/kokoro.onnx",
                "size_bytes": 100,
                "backends": ["kokoro"],
            }
        },
    )

    report = import_toml_to_sqlite(registry_file=rfile, db_path=tmp_path / "hal0.db")
    assert report.imported == 1

    registry = SqliteModelRegistry(db_path=tmp_path / "hal0.db")
    model = registry.get("kokoro-tts")
    assert model.provider == "kokoro"
    # Tags themselves are still stored (vestigial lane hints — intentional).
    assert model.backends == ["kokoro"]


def test_import_toml_respects_explicit_provider(tmp_path: Path) -> None:
    """An entry that already carries an explicit ``provider`` is never
    overridden by the tag-derived guess."""
    registry_dir = tmp_path / "registry"
    rfile = registry_dir / "registry.toml"
    _write_registry_toml(
        rfile,
        {
            "explicit-model": {
                "name": "Explicit",
                "path": "/models/explicit.gguf",
                "size_bytes": 100,
                "backends": ["kokoro"],
                "provider": "llama-server",
            }
        },
    )

    report = import_toml_to_sqlite(registry_file=rfile, db_path=tmp_path / "hal0.db")
    assert report.imported == 1

    registry = SqliteModelRegistry(db_path=tmp_path / "hal0.db")
    model = registry.get("explicit-model")
    assert model.provider == "llama-server"
