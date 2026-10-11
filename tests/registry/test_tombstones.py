"""Tests for hal0.registry.tombstones — the ignored-model-paths store (ADR-0026)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.config import paths
from hal0.registry import tombstones


@pytest.fixture(autouse=True)
def _home(tmp_hal0_home: str) -> None:
    """Every test writes the tombstone file under an isolated HAL0_HOME."""


def test_load_is_empty_when_file_absent() -> None:
    assert tombstones.load() == set()


def test_add_persists_resolved_paths(tmp_path: Path) -> None:
    real = tmp_path / "models" / "a.gguf"
    real.parent.mkdir()
    real.write_bytes(b"x")
    link = tmp_path / "link.gguf"
    link.symlink_to(real)

    added = tombstones.add([link])

    assert added == [str(real)]
    assert tombstones.load() == {str(real)}
    assert paths.model_tombstones_path().is_file()


def test_add_is_idempotent(tmp_path: Path) -> None:
    f = tmp_path / "a.gguf"
    assert tombstones.add([f]) == [str(f)]
    assert tombstones.add([f]) == []
    assert tombstones.load() == {str(f)}


def test_is_tombstoned_matches_path_and_descendants(tmp_path: Path) -> None:
    d = tmp_path / "flm-model"
    entries = {str(d), str(tmp_path / "x.gguf")}
    assert tombstones.is_tombstoned(tmp_path / "x.gguf", entries)
    assert tombstones.is_tombstoned(d / "weights" / "w.q4nx", entries)
    assert not tombstones.is_tombstoned(tmp_path / "y.gguf", entries)
    assert not tombstones.is_tombstoned(tmp_path / "x.gguf", set())


def test_clear_drops_entry_and_entries_below(tmp_path: Path) -> None:
    d = tmp_path / "dir"
    keep = tmp_path / "other.gguf"
    tombstones.add([d / "a.gguf", d / "sub" / "b.gguf", keep])

    removed = tombstones.clear(d)

    assert removed == sorted([str(d / "a.gguf"), str(d / "sub" / "b.gguf")])
    assert tombstones.load() == {str(keep)}


def test_clear_matches_symlinked_form(tmp_path: Path) -> None:
    real = tmp_path / "real.gguf"
    real.write_bytes(b"x")
    link = tmp_path / "via-link.gguf"
    link.symlink_to(real)
    tombstones.add([real])

    assert tombstones.clear(link) == [str(real)]
    assert tombstones.load() == set()


def test_corrupt_file_reads_as_empty_and_is_overwritten(tmp_path: Path) -> None:
    target = paths.model_tombstones_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{not json", encoding="utf-8")

    assert tombstones.load() == set()
    tombstones.add([tmp_path / "a.gguf"])
    assert tombstones.load() == {str(tmp_path / "a.gguf")}
