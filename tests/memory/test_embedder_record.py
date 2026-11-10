"""The memory store's embedder record (#3518).

Run targeted:
    .venv/bin/python -m pytest tests/memory/test_embedder_record.py -q
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from hal0.memory import embedder_record as er

BGE = "BAAI/bge-small-en-v1.5"
HS = "hindsight-ai/hs-embed-1"


class Box:
    """The three places the record module reads, under one tmp dir."""

    def __init__(self, root: Path) -> None:
        self.record = root / "state" / "memory-embedder.json"
        self.dropin = root / "hindsight-api.service.d" / "showcase-lanes.conf"
        self.hs_dir = root / "memory" / "hindsight"

    @property
    def kw(self) -> dict[str, Path]:
        return {"path": self.record, "dropin_path": self.dropin, "hs_dir": self.hs_dir}

    def with_store(self) -> Box:
        """An engine that has started: pg0's cluster under ``$HOME/.pg0``."""
        data = self.hs_dir / ".pg0" / "instances" / "hindsight" / "data"
        data.mkdir(parents=True)
        (data / "PG_VERSION").write_text("18\n")
        return self

    def with_dropin(self, model: str) -> Box:
        self.dropin.parent.mkdir(parents=True, exist_ok=True)
        self.dropin.write_text(
            "# Managed by hal0\n[Service]\n"
            "Environment=HINDSIGHT_API_EMBEDDINGS_PROVIDER=local\n"
            f"Environment=HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL={model}\n"
            "Environment=HINDSIGHT_API_RERANKER_LOCAL_MODEL=hindsight-ai/hs-rerank-1\n"
        )
        return self


@pytest.fixture
def box(tmp_path: Path) -> Box:
    return Box(tmp_path)


# ── the engine default is the one hal0 assumes ───────────────────────────────


def test_the_engine_default_and_the_config_default_are_the_two_models_in_play() -> None:
    from hal0.config.schema import MemoryModelsConfig

    assert er.ENGINE_DEFAULT_EMBEDDER == BGE
    assert MemoryModelsConfig().embeddings_model == HS


def test_the_record_lives_in_the_state_directory(tmp_hal0_home: str) -> None:
    from hal0.config.paths import var_lib

    assert er.embedder_record_path() == var_lib() / "state" / "memory-embedder.json"
    assert str(er.embedder_record_path()).startswith(tmp_hal0_home)


# ── "existing store" versus "fresh" ──────────────────────────────────────────


def test_no_engine_home_is_not_a_store(box: Box) -> None:
    assert er.engine_store_exists(box.hs_dir) is False


def test_an_engine_home_the_installer_just_made_is_not_a_store(box: Box) -> None:
    """install.sh creates the venv, hf-cache and .cache; none of them is a store."""
    for name in (".venv", "hf-cache", ".cache"):
        (box.hs_dir / name).mkdir(parents=True)
    assert er.engine_store_exists(box.hs_dir) is False


def test_an_empty_pg0_directory_is_not_a_store(box: Box) -> None:
    (box.hs_dir / ".pg0").mkdir(parents=True)
    assert er.engine_store_exists(box.hs_dir) is False


def test_an_initialised_pg0_is_a_store(box: Box) -> None:
    box.with_store()
    assert er.engine_store_exists(box.hs_dir) is True


def test_a_pg0_holding_only_the_postgres_install_is_a_store(box: Box) -> None:
    """The engine started far enough to unpack Postgres: it has run with an embedder."""
    (box.hs_dir / ".pg0" / "installation").mkdir(parents=True)
    assert er.engine_store_exists(box.hs_dir) is True


@pytest.mark.parametrize("sibling", [".pg0.pre-1.1.0", ".pg0.snapshot-prev", ".pg0.restore-failed"])
def test_an_engine_upgrade_snapshot_or_marker_means_a_store_existed(box: Box, sibling: str) -> None:
    """A failed restore can leave these beside a missing or empty ``.pg0``."""
    box.hs_dir.mkdir(parents=True)
    (box.hs_dir / sibling).write_text("x")
    assert er.engine_store_exists(box.hs_dir) is True
    (box.hs_dir / ".pg0").mkdir()
    assert er.engine_store_exists(box.hs_dir) is True


def test_an_unlistable_engine_home_is_treated_as_a_store(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cannot rule a store out, so do not answer "fresh"."""
    box.hs_dir.mkdir(parents=True)

    def denied(_path: object) -> None:
        raise PermissionError(13, "denied")

    monkeypatch.setattr(os, "scandir", denied)
    assert er.engine_store_exists(box.hs_dir) is True


def test_the_default_engine_home_is_the_engine_upgrade_one(tmp_hal0_home: str) -> None:
    from hal0.memory.engine_upgrade import hindsight_dir

    assert er.engine_store_exists() is False
    (hindsight_dir() / ".pg0" / "instances").mkdir(parents=True)
    assert er.engine_store_exists() is True


# ── determination: each branch ───────────────────────────────────────────────


def test_dropin_naming_another_model_on_an_existing_store_is_recorded_as_mixed(box: Box) -> None:
    """(a) The drop-in's model is what the engine runs now; keep it, flag the store."""
    box.with_store().with_dropin(HS)

    record = er.resolve_embedder(HS, **box.kw)

    assert (record.model, record.source, record.mixed) == (HS, er.SOURCE_DROPIN, True)
    assert record.persisted is False


def test_dropin_model_wins_over_the_configured_one(box: Box) -> None:
    """(a) Config asking for something else does not move the engine again."""
    box.with_store().with_dropin(HS)
    assert er.resolve_embedder(BGE, **box.kw).model == HS


def test_dropin_naming_the_engine_default_is_not_mixed(box: Box) -> None:
    box.with_store().with_dropin(BGE)

    record = er.resolve_embedder(HS, **box.kw)

    assert (record.model, record.source, record.mixed) == (BGE, er.SOURCE_DROPIN, False)


def test_dropin_without_a_store_is_not_mixed(box: Box) -> None:
    """A store that does not exist yet cannot hold older vectors."""
    box.with_dropin(HS)

    record = er.resolve_embedder(HS, **box.kw)

    assert (record.model, record.source, record.mixed) == (HS, er.SOURCE_DROPIN, False)


def test_existing_store_without_a_dropin_is_the_engine_default(box: Box) -> None:
    """(b) An engine with no embedder environment embedded with its default."""
    box.with_store()

    record = er.resolve_embedder(HS, **box.kw)

    assert (record.model, record.source, record.mixed) == (BGE, er.SOURCE_ENGINE_DEFAULT, False)


def test_no_store_and_no_dropin_is_the_configured_model(box: Box) -> None:
    """(c) A fresh box: whatever ``[memory.models].embeddings_model`` says."""
    record = er.resolve_embedder("example-org/another-embedder", **box.kw)

    assert (record.model, record.source, record.mixed) == (
        "example-org/another-embedder",
        er.SOURCE_CONFIGURED,
        False,
    )


def test_a_dropin_that_does_not_name_a_model_is_ignored(box: Box) -> None:
    box.with_store()
    box.dropin.parent.mkdir(parents=True)
    box.dropin.write_text("[Service]\nEnvironment=HINDSIGHT_API_LLM_MAX_CONCURRENT=1\n")

    assert er.resolve_embedder(HS, **box.kw).source == er.SOURCE_ENGINE_DEFAULT


def test_a_dropin_naming_something_that_is_not_a_model_id_is_ignored(box: Box) -> None:
    box.with_store().with_dropin("two words")
    assert er.dropin_embeddings_model(box.dropin) is None
    assert er.resolve_embedder(HS, **box.kw).source == er.SOURCE_ENGINE_DEFAULT


# ── writing the record down ──────────────────────────────────────────────────


def test_ensure_records_an_existing_store(box: Box) -> None:
    box.with_store()

    record = er.ensure_embedder_record(HS, **box.kw)

    assert record.persisted
    on_disk = json.loads(box.record.read_text())
    assert on_disk == {
        "version": 1,
        "model": BGE,
        "source": "engine-default",
        "mixed": False,
        "recorded_at": record.recorded_at,
    }
    assert er.load_embedder_record(box.record) == record
    assert oct(box.record.stat().st_mode & 0o777) == oct(0o644)


def test_ensure_records_a_mixed_store(box: Box) -> None:
    box.with_store().with_dropin(HS)

    er.ensure_embedder_record(HS, **box.kw)

    on_disk = json.loads(box.record.read_text())
    assert (on_disk["model"], on_disk["source"], on_disk["mixed"]) == (HS, "showcase-drop-in", True)


def test_ensure_does_not_record_a_box_whose_engine_has_not_started(box: Box) -> None:
    """Nothing makes the engine use the configured model yet, so nothing is written:
    a store built under the engine default must not sit under a record saying otherwise."""
    record = er.ensure_embedder_record(HS, **box.kw)

    assert (record.model, record.persisted) == (HS, False)
    assert not box.record.exists()

    # The engine then starts on the base unit and builds its store with the default.
    box.with_store()
    assert er.ensure_embedder_record(HS, **box.kw).model == BGE


def test_persist_writes_the_configured_model_once_the_dropin_carries_it(box: Box) -> None:
    record = er.resolve_embedder(HS, **box.kw)
    box.with_dropin(HS)  # the engine environment now names it

    saved = er.persist_embedder_record(record, path=box.record)

    assert (saved.model, saved.source, saved.persisted) == (HS, er.SOURCE_CONFIGURED, True)
    assert er.load_embedder_record(box.record) == saved


def test_a_second_run_does_not_rewrite_the_record(box: Box) -> None:
    box.with_store()
    first = er.ensure_embedder_record(HS, **box.kw)
    before = (box.record.read_bytes(), box.record.stat().st_mtime_ns, box.record.stat().st_ino)

    second = er.ensure_embedder_record(HS, **box.kw)
    third = er.persist_embedder_record(
        er.EmbedderRecord(model=HS, source=er.SOURCE_CONFIGURED), path=box.record
    )

    assert second == first and third == first
    assert (
        box.record.read_bytes(),
        box.record.stat().st_mtime_ns,
        box.record.stat().st_ino,
    ) == before


def test_the_record_outlives_what_it_was_determined_from(box: Box) -> None:
    """Once written, neither a drop-in nor the config can change the answer."""
    box.with_store()
    er.ensure_embedder_record(HS, **box.kw)

    box.with_dropin(HS)

    assert er.resolve_embedder("example-org/another-embedder", **box.kw).model == BGE
    assert er.ensure_embedder_record(HS, **box.kw).model == BGE
    assert json.loads(box.record.read_text())["model"] == BGE


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "[]",
        json.dumps({"model": "two words", "source": "x", "recorded_at": "2026-01-01T00:00:00"}),
        json.dumps({"model": "../../etc/passwd", "source": "x", "recorded_at": "t"}),
        json.dumps({"model": BGE, "recorded_at": "2026-01-01T00:00:00"}),
        json.dumps({"model": BGE, "source": "engine-default"}),
    ],
)
def test_an_unusable_record_is_determined_again_from_disk(box: Box, content: str) -> None:
    box.with_store()
    box.record.parent.mkdir(parents=True)
    box.record.write_text(content)

    assert er.load_embedder_record(box.record) is None
    record = er.ensure_embedder_record(HS, **box.kw)

    assert (record.model, record.persisted) == (BGE, True)
    assert json.loads(box.record.read_text())["model"] == BGE


def test_a_failed_write_returns_the_determined_value(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    box.with_store()

    def refuse(*_a: object, **_k: object) -> None:
        raise PermissionError(13, "denied")

    monkeypatch.setattr(er.nofollow, "write_file", refuse)

    record = er.ensure_embedder_record(HS, **box.kw)

    assert (record.model, record.persisted) == (BGE, False)
    assert not box.record.exists()


def test_record_store_embedder_reads_the_configured_model_from_hal0_toml(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The update hook: an upgraded box (store, no drop-in) is recorded as the engine default."""
    from hal0.memory import extraction_env as ee
    from hal0.memory.engine_upgrade import hindsight_dir

    monkeypatch.setattr(ee, "SHOWCASE_DROP_IN_PATH", tmp_path / "absent" / "showcase-lanes.conf")
    (hindsight_dir() / ".pg0" / "instances" / "hindsight" / "data").mkdir(parents=True)

    record = er.record_store_embedder()

    assert record is not None and (record.model, record.persisted) == (BGE, True)
    assert json.loads(er.embedder_record_path().read_text())["source"] == "engine-default"


def test_record_store_embedder_survives_an_unreadable_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("hal0.toml is not TOML")

    monkeypatch.setattr("hal0.config.loader.load_hal0_config", boom)
    assert er.record_store_embedder() is None


# ── recorded against configured ──────────────────────────────────────────────


def test_status_is_clean_when_the_store_was_built_with_the_configured_model(box: Box) -> None:
    box.with_store()
    er.ensure_embedder_record(BGE, **box.kw)

    status = er.embedder_status(BGE, **box.kw)

    assert status["recorded"] == status["configured"] == BGE
    assert status["mixed"] is False and status["needs_switch"] is False
    assert status["source"] == "engine-default" and status["recorded_at"]
    assert er.embedder_notice(status) is None


def test_status_reports_a_config_that_differs_and_changes_nothing(box: Box) -> None:
    box.with_store()
    er.ensure_embedder_record(HS, **box.kw)
    before = box.record.read_bytes()

    status = er.embedder_status(HS, **box.kw)

    assert (status["recorded"], status["configured"]) == (BGE, HS)
    assert status["mixed"] is False and status["needs_switch"] is True
    notice = er.embedder_notice(status)
    assert notice is not None
    assert BGE in notice and HS in notice
    assert "hal0 memory embedder switch" in notice and "not in this release yet" in notice
    assert box.record.read_bytes() == before
    assert not box.dropin.exists()


def test_status_reports_a_mixed_store_even_when_the_models_match(box: Box) -> None:
    box.with_store().with_dropin(HS)

    status = er.embedder_status(HS, **box.kw)

    assert (status["recorded"], status["configured"]) == (HS, HS)
    assert status["mixed"] is True and status["needs_switch"] is True
    assert "two embedders" in (er.embedder_notice(status) or "")


def test_status_does_not_write_the_record(box: Box) -> None:
    box.with_store()
    status = er.embedder_status(HS, **box.kw)
    assert status["recorded"] == BGE and status["recorded_at"] is None
    assert not box.record.exists()
