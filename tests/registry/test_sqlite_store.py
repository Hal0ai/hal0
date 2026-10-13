"""Unit tests for hal0.registry.sqlite_store.SqliteModelRegistry.

CRUD/error/route_for/on_change coverage, the nested-table merge of
``update()``, lossless round-trips and concurrency. `ModelRegistry` (the
public name from hal0.registry.store) IS `SqliteModelRegistry`; a few tests
import `SqliteModelRegistry` by name to make the intent explicit.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from hal0.registry.model import Model, ModelCapabilities
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


# ── merge semantics (#1413) ──────────────────────────────────────────────────
#
# ``capability_flags`` is the one nested table `update()` merges ONE level
# deep. The file facts (chat_template, tokenizer_repo, mtp, jinja) are plain
# top-level fields (ADR-0029 §6): an absent key keeps the stored value, an
# explicit ``None`` clears it. Every other field stays a flat overwrite.


class TestMergeSemantics:
    def test_patching_one_fact_preserves_the_others(self, reg: ModelRegistry) -> None:
        reg.add(
            _model(
                "a",
                tokenizer_repo="Qwen/Qwen3-4B",
                chat_template="minicpm5-1b",
                mtp=True,
                jinja=False,
            )
        )
        new = reg.update("a", {"jinja": True})
        assert new.jinja is True
        assert new.tokenizer_repo == "Qwen/Qwen3-4B"
        assert new.chat_template == "minicpm5-1b"
        assert new.mtp is True

    def test_explicit_none_clears_only_that_fact(self, reg: ModelRegistry) -> None:
        reg.add(_model("a", chat_template="minicpm5-1b", mtp=True))
        new = reg.update("a", {"chat_template": None})
        assert new.chat_template is None
        assert new.mtp is True

    def test_a_defaults_patch_is_refused(self, reg: ModelRegistry) -> None:
        """ADR-0029 §6: the nested ``defaults`` table is gone; the merge
        re-validates through ``Model`` so a stale-shape patch never lands."""
        reg.add(_model("a", chat_template="minicpm5-1b"))
        with pytest.raises(RegistryError):
            reg.update("a", {"defaults": {"chat_template": "chatml"}})
        with pytest.raises(RegistryError):
            reg.update("a", {"defaults": None})
        assert reg.get("a").chat_template == "minicpm5-1b"

    def test_model_validate_refuses_a_defaults_table(self) -> None:
        with pytest.raises(ValueError, match="defaults"):
            Model.model_validate({"id": "a", "path": "/m/a.gguf", "defaults": {"mtp": True}})
        with pytest.raises(ValueError, match="defaults"):
            Model.model_validate({"id": "a", "path": "/m/a.gguf", "defaults": None})

    def test_capability_flags_merge_one_level(self, reg: ModelRegistry) -> None:
        reg.add(_model("a", capability_flags=ModelCapabilities(tool_calling=True)))
        new = reg.update("a", {"capability_flags": {}})
        assert new.capability_flags.tool_calling is True
        cleared = reg.update("a", {"capability_flags": {"tool_calling": None}})
        assert cleared.capability_flags.tool_calling is None

    def test_fact_patch_does_not_disturb_top_level_fields(self, reg: ModelRegistry) -> None:
        reg.add(_model("a", name="A", chat_template="minicpm5-1b"))
        new = reg.update("a", {"jinja": True})
        assert new.name == "A"
        assert new.chat_template == "minicpm5-1b"
        assert new.jinja is True


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


# ── file-fact round-trip (mirrors test_schema_migration.py) ─────────────────


class TestModelFactsRoundTrip:
    def test_full_facts_round_trip(self, reg: ModelRegistry) -> None:
        reg.add(
            _model(
                "a",
                chat_template="minicpm5-1b",
                tokenizer_repo="Qwen/Qwen3-4B",
                mtp=True,
                jinja=False,
            )
        )
        got = reg.get("a")
        assert got.chat_template == "minicpm5-1b"
        assert got.tokenizer_repo == "Qwen/Qwen3-4B"
        assert got.mtp is True
        assert got.jinja is False

    def test_unset_facts_stay_none(self, reg: ModelRegistry) -> None:
        reg.add(_model("a"))
        got = reg.get("a")
        assert (got.chat_template, got.tokenizer_repo, got.mtp, got.jinja) == (
            None,
            None,
            None,
            None,
        )

    def test_falsy_but_set_facts_are_not_dropped(self, reg: ModelRegistry) -> None:
        """Regression: a falsy-but-set fact (mtp=False, jinja=False) is a real
        value, not 'unset'."""
        reg.add(_model("a", mtp=False, jinja=False))
        got = reg.get("a")
        assert got.mtp is False
        assert got.jinja is False

    def test_tokenizer_repo_round_trips_add_get_update(self, reg: ModelRegistry) -> None:
        """tokenizer_repo has no column: it rides the ``extra`` blob. It must
        survive add -> get, a sparse update of a sibling fact, an overwrite,
        and an explicit clear — without leaking into Model.metadata."""
        reg.add(
            _model(
                "a",
                tokenizer_repo="Qwen/Qwen3-4B",
                metadata={"discovered": True},
            )
        )
        got = reg.get("a")
        assert got.tokenizer_repo == "Qwen/Qwen3-4B"
        assert "_tokenizer_repo" not in got.metadata
        assert got.metadata.get("discovered") is True

        # A sibling-only patch keeps it (and it persists across instances).
        reg.update("a", {"jinja": False})
        again = reg.get("a")
        assert again.tokenizer_repo == "Qwen/Qwen3-4B"
        assert again.jinja is False

        reg.update("a", {"tokenizer_repo": "meta-llama/Llama-3.1-8B"})
        assert reg.get("a").tokenizer_repo == "meta-llama/Llama-3.1-8B"

        cleared = reg.update("a", {"tokenizer_repo": None})
        assert cleared.tokenizer_repo is None
        assert cleared.jinja is False
        assert "_tokenizer_repo" not in cleared.metadata

    def test_unset_tokenizer_repo_stamps_nothing(self, reg: ModelRegistry) -> None:
        reg.add(_model("a", mtp=True))
        got = reg.get("a")
        assert got.mtp is True
        assert got.tokenizer_repo is None


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
