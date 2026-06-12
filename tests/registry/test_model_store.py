"""Unit tests for the model_store service module.

Covers:
  * describe_store_state — present/missing dirs, files vs dirs, free-bytes
    against deepest existing ancestor when target is missing.
  * build_suggestions — order, deduplication, current-path surfacing.
  * plan_migration — needed when source has data + paths differ; not
    needed for no-prior / same-path / empty / missing.
  * execute_migration — moves entries, skips dot-entries, records
    per-entry failures without aborting the whole migration.
"""

from __future__ import annotations

from pathlib import Path

from hal0.config import paths as cfg_paths
from hal0.registry.model_store import (
    build_suggestions,
    describe_store_state,
    execute_migration,
    plan_migration,
)

# ── describe_store_state ──────────────────────────────────────────────────


def test_describe_state_for_missing_path_surfaces_ancestor_free(tmp_path: Path) -> None:
    target = tmp_path / "deeply" / "nested" / "missing"
    state = describe_store_state(target)
    assert state.exists is False
    assert state.is_dir is False
    assert state.writable is False
    assert state.files_count == 0
    # Free bytes computed against tmp_path's mount, which always exists.
    assert state.free_bytes > 0


def test_describe_state_for_directory_with_files(tmp_path: Path) -> None:
    p = tmp_path / "store"
    p.mkdir()
    (p / "a.gguf").write_bytes(b"x" * 100)
    (p / "b.gguf").write_bytes(b"y" * 200)
    state = describe_store_state(p)
    assert state.exists is True
    assert state.is_dir is True
    assert state.writable is True
    assert state.files_count == 2
    assert state.size_bytes == 300


def test_describe_state_for_file_not_a_dir(tmp_path: Path) -> None:
    f = tmp_path / "blob"
    f.write_text("hi")
    state = describe_store_state(f)
    assert state.exists is True
    assert state.is_dir is False
    # files_count + size_bytes stay at zero because we only walk dirs.
    assert state.files_count == 0


# ── build_suggestions ────────────────────────────────────────────────────


def test_suggestions_includes_canonical_defaults(tmp_hal0_home: str) -> None:
    sug = build_suggestions()
    paths = [s["path"] for s in sug]
    assert "/mnt/ai-models" in paths
    assert str(cfg_paths.models_dir()) in paths


def test_suggestions_marks_current(tmp_hal0_home: str) -> None:
    current = str(cfg_paths.models_dir())
    sug = build_suggestions(current=current)
    matching = [s for s in sug if s["path"] == current]
    assert matching
    assert matching[0]["is_current"] is True


def test_suggestions_dedupes_when_current_matches_default(tmp_hal0_home: str) -> None:
    current = str(cfg_paths.models_dir())
    sug = build_suggestions(current=current)
    paths = [s["path"] for s in sug]
    assert paths.count(current) == 1


# ── plan_migration ───────────────────────────────────────────────────────


def test_plan_no_migration_when_current_unset(tmp_path: Path) -> None:
    plan = plan_migration(current=None, target=str(tmp_path / "new"))
    assert plan.needed is False
    assert plan.reason == "no_prior_store"


def test_plan_no_migration_when_paths_match(tmp_path: Path) -> None:
    target = tmp_path / "shared"
    target.mkdir()
    plan = plan_migration(current=str(target), target=str(target))
    assert plan.needed is False
    assert plan.reason == "same_path"


def test_plan_no_migration_when_source_empty(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    dst = tmp_path / "dst"
    plan = plan_migration(current=str(src), target=str(dst))
    assert plan.needed is False
    assert plan.reason == "source_empty"


def test_plan_no_migration_when_source_missing(tmp_path: Path) -> None:
    plan = plan_migration(
        current=str(tmp_path / "ghost"),
        target=str(tmp_path / "dst"),
    )
    assert plan.needed is False
    assert plan.reason == "source_missing"


def test_plan_migration_needed_when_source_has_files(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "model.gguf").write_bytes(b"x" * 50)
    dst = tmp_path / "dst"
    plan = plan_migration(current=str(src), target=str(dst))
    assert plan.needed is True
    assert plan.files_count == 1
    assert plan.size_bytes == 50
    assert plan.reason == "has_data"


# ── execute_migration ────────────────────────────────────────────────────


def test_execute_moves_entries_and_records_them(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "Qwen3-8B").mkdir()
    (src / "Qwen3-8B" / "weights.gguf").write_bytes(b"x" * 100)
    (src / "Whisper").mkdir()
    (src / "Whisper" / "w.gguf").write_bytes(b"y" * 50)
    dst = tmp_path / "dst"

    plan = plan_migration(current=str(src), target=str(dst))
    assert plan.needed
    result = execute_migration(plan)

    assert sorted(result.moved) == ["Qwen3-8B", "Whisper"]
    assert result.failed == []
    assert (dst / "Qwen3-8B" / "weights.gguf").read_bytes() == b"x" * 100
    assert not (src / "Qwen3-8B").exists()
    assert not (src / "Whisper").exists()


def test_execute_skips_dot_entries(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / ".tmp").mkdir()
    (src / ".tmp" / "stage.part").write_bytes(b"in-flight")
    (src / "model").mkdir()
    (src / "model" / "x.gguf").write_bytes(b"x")
    dst = tmp_path / "dst"

    plan = plan_migration(current=str(src), target=str(dst))
    result = execute_migration(plan)

    assert "model" in result.moved
    # .tmp left where it was — never mirrored into the new store.
    assert (src / ".tmp" / "stage.part").read_bytes() == b"in-flight"
    assert not (dst / ".tmp").exists()


def test_execute_records_failure_when_target_already_exists(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "model").mkdir()
    (src / "model" / "a.gguf").write_bytes(b"new")
    (src / "model2").mkdir()
    (src / "model2" / "b.gguf").write_bytes(b"new")
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "model").mkdir()  # collision
    (dst / "model" / "existing.gguf").write_bytes(b"old")

    plan = plan_migration(current=str(src), target=str(dst))
    result = execute_migration(plan)

    # model2 moved, model recorded as failed (target_exists).
    assert "model2" in result.moved
    assert any(row["name"] == "model" for row in result.failed)
    # Existing target file untouched.
    assert (dst / "model" / "existing.gguf").read_bytes() == b"old"
    # The source for the failed entry stays put — operator can retry.
    assert (src / "model" / "a.gguf").read_bytes() == b"new"


def test_execute_noop_when_plan_says_not_needed(tmp_path: Path) -> None:
    plan = plan_migration(current=None, target=str(tmp_path / "dst"))
    result = execute_migration(plan)
    assert result.moved == []
    assert result.failed == []
