"""Unit tests for hal0.registry.store.ModelRegistry.

Covers:
  * CRUD round-trips with atomic TOML on disk.
  * Typed errors: ModelNotFound, ModelAlreadyExists.
  * Concurrent reads + writes don't corrupt the file.
  * mtime cache invalidation: out-of-band edits show up on next access.
  * Parse failures on disk keep the prior cache view (Tier 1).
"""

from __future__ import annotations

import threading
import time
import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from hal0.registry.model import Model, ModelCapabilities, ModelDefaults

# ML-1: `ModelRegistry` now names the SQLite-backed store. This file tests
# the TOML-specific implementation itself (atomic write, mtime cache,
# corrupt-file tolerance, directory fsync) under its own name,
# `TomlModelRegistry`, aliased here so the rest of the file is unchanged.
from hal0.registry.store import (
    ModelAlreadyExists,
    ModelNotFound,
    RegistryError,
)
from hal0.registry.store import TomlModelRegistry as ModelRegistry


@pytest.fixture
def reg(tmp_path: Path) -> ModelRegistry:
    """Fresh ModelRegistry in a tmp dir."""
    return ModelRegistry(registry_dir=tmp_path / "registry")


def _model(model_id: str = "qwen3-4b", path: str = "/models/qwen3.gguf", **kw) -> Model:
    return Model(id=model_id, path=path, **kw)


# ── reads on empty registry ──────────────────────────────────────────────────


class TestEmptyRegistry:
    def test_list_empty(self, reg: ModelRegistry) -> None:
        assert reg.list() == []

    def test_get_unknown_raises_model_not_found(self, reg: ModelRegistry) -> None:
        with pytest.raises(ModelNotFound):
            reg.get("nope")

    def test_has_returns_false(self, reg: ModelRegistry) -> None:
        assert reg.has("nope") is False

    def test_remove_unknown_returns_false(self, reg: ModelRegistry) -> None:
        assert reg.remove("nope") is False

    def test_route_for_unknown_returns_none(self, reg: ModelRegistry) -> None:
        assert reg.route_for("nope") is None


# ── add ──────────────────────────────────────────────────────────────────────


class TestAdd:
    def test_add_then_get(self, reg: ModelRegistry) -> None:
        m = _model("qwen3-4b", name="Qwen3 4B")
        reg.add(m)
        got = reg.get("qwen3-4b")
        assert got.id == "qwen3-4b"
        assert got.name == "Qwen3 4B"

    def test_add_persists_to_disk(self, reg: ModelRegistry) -> None:
        reg.add(_model("a"))
        assert reg.registry_file.exists()
        with open(reg.registry_file, "rb") as f:
            raw = tomllib.load(f)
        assert "models" in raw
        assert "a" in raw["models"]

    def test_add_duplicate_raises(self, reg: ModelRegistry) -> None:
        reg.add(_model("a"))
        with pytest.raises(ModelAlreadyExists):
            reg.add(_model("a"))

    def test_add_writes_atomically(self, reg: ModelRegistry, tmp_path: Path) -> None:
        """A failure during write leaves the prior file intact."""
        reg.add(_model("a"))
        # Snapshot what's on disk.
        snapshot = reg.registry_file.read_bytes()

        with (
            patch("hal0.registry.store.tomli_w.dump", side_effect=OSError("disk full")),
            pytest.raises(OSError),
        ):
            reg.add(_model("b"))

        # Disk unchanged.
        assert reg.registry_file.read_bytes() == snapshot

        # No leftover tmpfiles in the registry dir.
        tmpfiles = list(reg.registry_dir.glob(f".{reg.registry_file.name}.*.tmp"))
        assert tmpfiles == []


# ── remove ───────────────────────────────────────────────────────────────────


class TestRemove:
    def test_remove_existing_returns_true(self, reg: ModelRegistry) -> None:
        reg.add(_model("a"))
        assert reg.remove("a") is True
        with pytest.raises(ModelNotFound):
            reg.get("a")

    def test_remove_persists(self, reg: ModelRegistry) -> None:
        reg.add(_model("a"))
        reg.add(_model("b"))
        reg.remove("a")
        with open(reg.registry_file, "rb") as f:
            raw = tomllib.load(f)
        assert "a" not in raw["models"]
        assert "b" in raw["models"]


# ── update ───────────────────────────────────────────────────────────────────


class TestUpdate:
    def test_update_existing(self, reg: ModelRegistry) -> None:
        reg.add(_model("a", name="A"))
        new = reg.update("a", {"name": "Aprime"})
        assert new.name == "Aprime"
        assert reg.get("a").name == "Aprime"

    def test_update_missing_raises(self, reg: ModelRegistry) -> None:
        with pytest.raises(ModelNotFound):
            reg.update("missing", {"name": "x"})

    def test_update_with_non_dict_raises(self, reg: ModelRegistry) -> None:
        reg.add(_model("a"))
        with pytest.raises(RegistryError):
            reg.update("a", "not a dict")  # type: ignore[arg-type]

    def test_update_cannot_change_id(self, reg: ModelRegistry) -> None:
        reg.add(_model("a"))
        # id key in updates is ignored — the model stays at "a".
        reg.update("a", {"id": "b", "name": "renamed"})
        with pytest.raises(ModelNotFound):
            reg.get("b")
        assert reg.get("a").name == "renamed"

    def test_update_validation_failure_raises(self, reg: ModelRegistry) -> None:
        reg.add(_model("a"))
        # empty path is rejected by the Model.path_nonempty validator.
        with pytest.raises(RegistryError):
            reg.update("a", {"path": ""})


# ── nested-table merge semantics (#1413) ─────────────────────────────────────
#
# `defaults` / `capability_flags` merge ONE level deep: an absent sub-key keeps
# the stored value, an explicit ``None`` clears it, and ``{"defaults": None}``
# still drops the whole table. Every other field stays a flat overwrite.


class TestNestedTableMerge:
    def test_partial_defaults_patch_preserves_siblings(self, reg: ModelRegistry) -> None:
        reg.add(
            _model(
                "a",
                defaults=ModelDefaults(
                    context_size=8192,
                    chat_template="minicpm5-1b",
                    mtp=True,
                    jinja=False,
                ),
            )
        )
        new = reg.update("a", {"defaults": {"extra_args": "--temp 0.5"}})
        assert new.defaults is not None
        assert new.defaults.extra_args == "--temp 0.5"
        assert new.defaults.context_size == 8192
        assert new.defaults.chat_template == "minicpm5-1b"
        assert new.defaults.mtp is True
        assert new.defaults.jinja is False

    def test_explicit_none_sub_key_clears_only_that_key(self, reg: ModelRegistry) -> None:
        reg.add(_model("a", defaults=ModelDefaults(context_size=8192, extra_args="-b 512")))
        new = reg.update("a", {"defaults": {"context_size": None}})
        assert new.defaults is not None
        assert new.defaults.context_size is None
        assert new.defaults.extra_args == "-b 512"

    def test_defaults_none_drops_the_whole_table(self, reg: ModelRegistry) -> None:
        reg.add(_model("a", defaults=ModelDefaults(context_size=8192, extra_args="-b 512")))
        new = reg.update("a", {"defaults": None})
        assert new.defaults is None

    def test_empty_defaults_patch_is_a_no_op(self, reg: ModelRegistry) -> None:
        reg.add(_model("a", defaults=ModelDefaults(context_size=8192)))
        new = reg.update("a", {"defaults": {}})
        assert new.defaults is not None
        assert new.defaults.context_size == 8192

    def test_capability_flags_merge_one_level(self, reg: ModelRegistry) -> None:
        reg.add(_model("a", capability_flags=ModelCapabilities(tool_calling=True)))
        new = reg.update("a", {"capability_flags": {}})
        assert new.capability_flags.tool_calling is True
        cleared = reg.update("a", {"capability_flags": {"tool_calling": None}})
        assert cleared.capability_flags.tool_calling is None

    def test_defaults_patch_does_not_disturb_top_level_fields(self, reg: ModelRegistry) -> None:
        reg.add(_model("a", name="A", defaults=ModelDefaults(context_size=4096)))
        new = reg.update("a", {"defaults": {"jinja": True}})
        assert new.name == "A"
        assert new.defaults is not None
        assert new.defaults.context_size == 4096
        assert new.defaults.jinja is True


# ── list ─────────────────────────────────────────────────────────────────────


class TestList:
    def test_list_returns_all_sorted(self, reg: ModelRegistry) -> None:
        reg.add(_model("c"))
        reg.add(_model("a"))
        reg.add(_model("b"))
        ids = [m.id for m in reg.list()]
        assert ids == ["a", "b", "c"]


# ── mtime cache invalidation ─────────────────────────────────────────────────


class TestMtimeCache:
    def test_invalidates_when_file_mtime_advances(self, reg: ModelRegistry) -> None:
        reg.add(_model("a"))
        assert reg.has("a")

        # Build a second registry against the same file — same on-disk
        # state, but a fresh in-memory cache.
        reg2 = ModelRegistry(registry_dir=reg.registry_dir)
        assert reg2.has("a")

        # Out-of-band: write to the same file directly through reg, and
        # bump mtime to ensure stat() returns a new value.
        reg.add(_model("b"))
        future = time.time() + 5
        import os as _os

        _os.utime(reg.registry_file, (future, future))

        # reg2 sees the new entry on next access.
        assert reg2.has("b")

    def test_reload_invalidates_cache(self, reg: ModelRegistry) -> None:
        reg.add(_model("a"))
        # Force the cache to populate.
        reg.list()
        # Pretend nothing changed but call reload explicitly.
        reg.reload()
        # Next access still works.
        assert reg.has("a")

    def test_corrupt_file_keeps_stale_cache_warns(
        self, reg: ModelRegistry, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Tier 1: parse failure logs a warning but doesn't blank the cache."""
        reg.add(_model("a"))
        assert reg.has("a")

        # Corrupt the file on disk.
        reg.registry_file.write_text("garbage = = =\n")
        # Bump mtime so the cache notices.
        future = time.time() + 5
        import os as _os

        _os.utime(reg.registry_file, (future, future))

        # Cache view stays alive.
        with caplog.at_level("WARNING"):
            assert reg.has("a")
        # We logged at WARN about the parse failure.
        assert any("registry parse failed" in rec.message for rec in caplog.records)


# ── route_for ────────────────────────────────────────────────────────────────


class TestRouteFor:
    def test_returns_upstream_url_from_metadata(self, reg: ModelRegistry) -> None:
        reg.add(_model("a", metadata={"upstream_url": "http://127.0.0.1:8081"}))
        assert reg.route_for("a") == "http://127.0.0.1:8081"

    def test_returns_none_when_url_missing(self, reg: ModelRegistry) -> None:
        reg.add(_model("a"))
        assert reg.route_for("a") is None

    def test_returns_none_when_model_missing(self, reg: ModelRegistry) -> None:
        assert reg.route_for("ghost") is None


# ── concurrency ──────────────────────────────────────────────────────────────


class TestConcurrency:
    def test_concurrent_writes_do_not_corrupt_file(self, reg: ModelRegistry) -> None:
        """N threads each add a unique model; final file has all of them."""
        n_writers = 16
        per_writer = 5
        barrier = threading.Barrier(n_writers)

        def writer(start: int) -> None:
            barrier.wait()  # release all threads roughly at once
            for i in range(per_writer):
                reg.add(_model(f"m-{start}-{i}", path=f"/p/{start}-{i}"))

        threads = [threading.Thread(target=writer, args=(w,)) for w in range(n_writers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        models = reg.list()
        assert len(models) == n_writers * per_writer
        ids = {m.id for m in models}
        for w in range(n_writers):
            for i in range(per_writer):
                assert f"m-{w}-{i}" in ids

        # File parses cleanly.
        with open(reg.registry_file, "rb") as f:
            raw = tomllib.load(f)
        assert len(raw["models"]) == n_writers * per_writer

    def test_concurrent_reads_during_writes(self, reg: ModelRegistry) -> None:
        """Readers running concurrently with writers see consistent snapshots."""
        n_models = 30
        stop = threading.Event()

        def writer() -> None:
            for i in range(n_models):
                reg.add(_model(f"x-{i:03d}", path=f"/p/{i}"))
            stop.set()

        errors: list[Exception] = []

        def reader() -> None:
            try:
                while not stop.is_set():
                    # Each call returns a coherent view; entries should
                    # never be malformed even if writes are mid-flight.
                    for m in reg.list():
                        assert m.id.startswith("x-")
                        assert m.path.startswith("/p/")
            except Exception as exc:
                errors.append(exc)

        w = threading.Thread(target=writer)
        readers = [threading.Thread(target=reader) for _ in range(4)]
        w.start()
        for r in readers:
            r.start()
        w.join()
        for r in readers:
            r.join()

        assert errors == []
        assert len(reg.list()) == n_models


# ── HAL0_HOME default resolution ─────────────────────────────────────────────


class TestDefaultRegistryDir:
    def test_uses_paths_registry_dir_when_no_override(self, tmp_hal0_home: str) -> None:
        """No override → registry_dir comes from paths.registry_dir()."""
        from hal0.config import paths as _paths

        reg = ModelRegistry()
        assert reg.registry_dir == _paths.registry_dir()

    def test_override_wins(self, tmp_path: Path) -> None:
        reg = ModelRegistry(registry_dir=tmp_path / "custom")
        assert reg.registry_dir == tmp_path / "custom"


# ── pre-existing on-disk corruption surface ──────────────────────────────────


class TestPreexistingCorruption:
    def test_initial_load_with_corrupt_file_returns_empty(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A corrupt registry on first access logs WARN and returns []."""
        rdir = tmp_path / "registry"
        rdir.mkdir()
        (rdir / "registry.toml").write_text("not valid = = toml\n")

        reg = ModelRegistry(registry_dir=rdir)
        with caplog.at_level("WARNING"):
            assert reg.list() == []
        assert any("registry parse failed" in rec.message for rec in caplog.records)

    def test_entry_not_a_table_is_skipped(self, tmp_path: Path) -> None:
        rdir = tmp_path / "registry"
        rdir.mkdir()
        # `models.a` is a string, not a table — should be skipped, not crash.
        (rdir / "registry.toml").write_text('[models]\na = "not a table"\n')

        reg = ModelRegistry(registry_dir=rdir)
        assert reg.list() == []


# ── MR-13: atomic writes fsync the parent directory ──────────────────────────


class TestParentDirFsync:
    def test_add_fsyncs_registry_dir(
        self, reg: ModelRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """add() fsyncs the parent dir after os.replace so the rename is durable."""
        import hal0.registry.store as store_mod

        calls: list[Path] = []
        real = store_mod._fsync_dir

        def _spy(dir_path: Path) -> None:
            calls.append(dir_path)
            real(dir_path)

        monkeypatch.setattr(store_mod, "_fsync_dir", _spy)

        reg.add(_model("a"))

        assert calls == [reg.registry_file.parent]

    def test_add_survives_dir_fsync_failure(
        self, reg: ModelRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed directory fsync is best-effort — the write still lands."""
        import hal0.registry.store as store_mod

        real_open = store_mod.os.open

        def _flaky_open(path, flags, *args, **kwargs):  # type: ignore[no-untyped-def]
            if flags & getattr(store_mod.os, "O_DIRECTORY", 0):
                raise OSError("no O_DIRECTORY here")
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(store_mod.os, "open", _flaky_open)

        reg.add(_model("a", name="Alpha"))

        assert reg.registry_file.exists()
        assert reg.get("a").name == "Alpha"
