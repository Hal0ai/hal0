"""Unit tests for hal0.registry.sqlite_store.SqliteModelRegistry (ML-1).

Mirrors the CRUD/error/route_for/on_change coverage in test_store.py
against the SQLite-backed implementation, plus lossless-round-trip and
concurrency checks specific to the SQLite substrate. `ModelRegistry`
(the public, drop-in name from hal0.registry.store) is used directly in
most tests here — it IS `SqliteModelRegistry` post-ML-1 — with a few
tests importing `SqliteModelRegistry` by name to make the intent explicit.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from hal0.registry.model import Model, ModelDefaults
from hal0.registry.sqlite_store import SqliteModelRegistry
from hal0.registry.store import (
    ModelAlreadyExists,
    ModelNotFound,
    ModelRegistry,
    RegistryError,
)


@pytest.fixture
def reg(tmp_path: Path) -> ModelRegistry:
    """Fresh SqliteModelRegistry in a tmp dir (registry_dir isolates the DB)."""
    return ModelRegistry(registry_dir=tmp_path / "registry")


def _model(model_id: str = "qwen3-4b", path: str = "/models/qwen3.gguf", **kw) -> Model:
    return Model(id=model_id, path=path, **kw)


def test_model_registry_is_sqlite_backed() -> None:
    """The drop-in cutover point: `ModelRegistry` IS `SqliteModelRegistry`."""
    assert ModelRegistry is SqliteModelRegistry


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

    def test_add_persists_across_instances(self, reg: ModelRegistry, tmp_path: Path) -> None:
        reg.add(_model("a"))
        reg2 = ModelRegistry(registry_dir=tmp_path / "registry")
        assert reg2.has("a")

    def test_add_duplicate_raises(self, reg: ModelRegistry) -> None:
        reg.add(_model("a"))
        with pytest.raises(ModelAlreadyExists):
            reg.add(_model("a"))

    def test_add_duplicate_leaves_original_row_intact(self, reg: ModelRegistry) -> None:
        reg.add(_model("a", name="Original"))
        with pytest.raises(ModelAlreadyExists):
            reg.add(_model("a", name="Clobbered"))
        assert reg.get("a").name == "Original"

    def test_add_persists_backends(self, reg: ModelRegistry) -> None:
        reg.add(_model("a", backends=["vulkan", "rocm", "cpu"]))
        got = reg.get("a")
        assert got.backends == ["vulkan", "rocm", "cpu"]


# ── remove ───────────────────────────────────────────────────────────────────


class TestRemove:
    def test_remove_existing_returns_true(self, reg: ModelRegistry) -> None:
        reg.add(_model("a"))
        assert reg.remove("a") is True
        with pytest.raises(ModelNotFound):
            reg.get("a")

    def test_remove_leaves_others_intact(self, reg: ModelRegistry) -> None:
        reg.add(_model("a"))
        reg.add(_model("b"))
        reg.remove("a")
        assert not reg.has("a")
        assert reg.has("b")

    def test_remove_cascades_backend_rows(self, reg: ModelRegistry) -> None:
        """ON DELETE CASCADE must actually fire (foreign_keys=ON on every
        connection) — otherwise model_backend rows leak, and a later add()
        of the same id would silently resurrect stale backends."""
        reg.add(_model("a", backends=["vulkan"]))
        reg.remove("a")
        reg.add(_model("a", backends=["rocm"]))
        assert reg.get("a").backends == ["rocm"]


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
        reg.update("a", {"id": "b", "name": "renamed"})
        with pytest.raises(ModelNotFound):
            reg.get("b")
        assert reg.get("a").name == "renamed"

    def test_update_validation_failure_raises(self, reg: ModelRegistry) -> None:
        reg.add(_model("a"))
        with pytest.raises(RegistryError):
            reg.update("a", {"path": ""})

    def test_update_replaces_backend_rows(self, reg: ModelRegistry) -> None:
        reg.add(_model("a", backends=["vulkan", "cpu"]))
        updated = reg.update("a", {"backends": ["rocm"]})
        assert updated.backends == ["rocm"]
        assert reg.get("a").backends == ["rocm"]

    def test_update_preserves_created_at(self, reg: ModelRegistry, tmp_path: Path) -> None:
        """created_at must not advance on update — only updated_at does."""
        reg.add(_model("a"))
        db_path = reg.db_path
        from hal0.db.connection import connect

        with connect(db_path) as conn:
            (created_before,) = conn.execute(
                "SELECT created_at FROM model WHERE id = 'a'"
            ).fetchone()
        reg.update("a", {"name": "renamed"})
        with connect(db_path) as conn:
            (created_after,) = conn.execute(
                "SELECT created_at FROM model WHERE id = 'a'"
            ).fetchone()
        assert created_before == created_after


# ── list ─────────────────────────────────────────────────────────────────────


class TestList:
    def test_list_returns_all_sorted(self, reg: ModelRegistry) -> None:
        reg.add(_model("c"))
        reg.add(_model("a"))
        reg.add(_model("b"))
        ids = [m.id for m in reg.list()]
        assert ids == ["a", "b", "c"]


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

    def test_upstream_url_survives_alongside_other_metadata(self, reg: ModelRegistry) -> None:
        """upstream_url must ride in `extra`, not get accidentally routed
        into the dedicated context_length column."""
        reg.add(
            _model(
                "a",
                metadata={"upstream_url": "http://x", "context_length": 8192, "custom": "v"},
            )
        )
        got = reg.get("a")
        assert got.metadata == {
            "upstream_url": "http://x",
            "context_length": 8192,
            "custom": "v",
        }


# ── reload / on_change ───────────────────────────────────────────────────────


class TestReloadAndOnChange:
    def test_reload_is_a_noop_that_does_not_raise(self, reg: ModelRegistry) -> None:
        reg.add(_model("a"))
        reg.reload()
        assert reg.has("a")

    def test_on_change_fires_after_add_update_remove(self, reg: ModelRegistry) -> None:
        calls: list[str] = []
        reg.on_change = lambda: calls.append("x")
        reg.add(_model("a"))
        reg.update("a", {"name": "renamed"})
        reg.remove("a")
        assert calls == ["x", "x", "x"]

    def test_on_change_failure_does_not_break_write(self, reg: ModelRegistry) -> None:
        def boom() -> None:
            raise RuntimeError("regen failed")

        reg.on_change = boom
        reg.add(_model("a"))
        assert reg.has("a")

    def test_no_hook_is_a_noop(self, reg: ModelRegistry) -> None:
        reg.add(_model("a"))
        assert reg.has("a")


# ── ModelDefaults round-trip (mirrors test_schema_migration.py) ─────────────


class TestModelDefaultsRoundTrip:
    def test_full_defaults_round_trip(self, reg: ModelRegistry) -> None:
        reg.add(
            _model(
                "a",
                defaults=ModelDefaults(
                    context_size=8192,
                    n_gpu_layers=-1,
                    rope_freq_base=1000000.0,
                    extra_args="--threads 8",
                ),
            )
        )
        got = reg.get("a")
        assert got.defaults is not None
        assert got.defaults.context_size == 8192
        assert got.defaults.n_gpu_layers == -1
        assert got.defaults.rope_freq_base == 1000000.0
        assert got.defaults.extra_args == "--threads 8"

    def test_defaults_none_stays_none(self, reg: ModelRegistry) -> None:
        reg.add(_model("a", defaults=None))
        assert reg.get("a").defaults is None

    def test_empty_defaults_collapses_to_none(self, reg: ModelRegistry) -> None:
        """All-None ModelDefaults() must read back as None, not as an
        object with every field None — mirrors the TOML store's collapse
        rule (_model_to_toml)."""
        reg.add(_model("a", defaults=ModelDefaults()))
        assert reg.get("a").defaults is None

    def test_falsy_but_set_n_gpu_layers_is_not_dropped(self, reg: ModelRegistry) -> None:
        """Regression: n_gpu_layers=0 is a legitimate, meaningful value
        (CPU-only) and must not be treated as 'unset' by the has-any-
        default check."""
        reg.add(_model("a", defaults=ModelDefaults(n_gpu_layers=0)))
        got = reg.get("a")
        assert got.defaults is not None
        assert got.defaults.n_gpu_layers == 0


# ── concurrency (adversarial per the ML-1 spec's explicit call-out) ─────────


class TestConcurrency:
    def test_concurrent_adds_do_not_lose_rows(self, reg: ModelRegistry) -> None:
        """N threads each add a unique model; BEGIN IMMEDIATE must
        serialize the writes so none are lost — the SQLite analogue of
        the old sidecar-flock 'no lost update' guarantee."""
        n_writers = 12
        per_writer = 4
        barrier = threading.Barrier(n_writers)

        def writer(start: int) -> None:
            barrier.wait()
            for i in range(per_writer):
                reg.add(_model(f"m-{start}-{i}", path=f"/p/{start}-{i}"))

        threads = [threading.Thread(target=writer, args=(w,)) for w in range(n_writers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        models = reg.list()
        assert len(models) == n_writers * per_writer

    def test_two_instances_same_dir_no_lost_update(self, tmp_path: Path) -> None:
        registry_dir = tmp_path / "registry"
        reg_a = ModelRegistry(registry_dir=registry_dir)
        reg_b = ModelRegistry(registry_dir=registry_dir)
        reg_a.add(_model("base"))

        barrier = threading.Barrier(2)
        errors: list[BaseException] = []

        def add_on(r: ModelRegistry, mid: str) -> None:
            try:
                barrier.wait()
                r.add(_model(mid))
            except BaseException as exc:
                errors.append(exc)

        t_a = threading.Thread(target=add_on, args=(reg_a, "a"))
        t_b = threading.Thread(target=add_on, args=(reg_b, "b"))
        t_a.start()
        t_b.start()
        t_a.join()
        t_b.join()

        assert not errors, f"writer raised: {errors!r}"
        ids = {m.id for m in reg_a.list()}
        assert ids == {"base", "a", "b"}


# ── HAL0_HOME default resolution ─────────────────────────────────────────────


class TestDefaultRegistryDir:
    def test_uses_paths_registry_dir_when_no_override(self, tmp_hal0_home: str) -> None:
        from hal0.config import paths as _paths

        reg = ModelRegistry()
        assert reg.registry_dir == _paths.registry_dir()

    def test_override_wins(self, tmp_path: Path) -> None:
        reg = ModelRegistry(registry_dir=tmp_path / "custom")
        assert reg.registry_dir == tmp_path / "custom"

    def test_db_path_override_wins_over_registry_dir(self, tmp_path: Path) -> None:
        explicit_db = tmp_path / "elsewhere" / "custom.db"
        reg = SqliteModelRegistry(registry_dir=tmp_path / "registry", db_path=explicit_db)
        assert reg.db_path == explicit_db
