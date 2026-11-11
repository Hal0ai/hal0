"""The memory store's bank layout record (ADR-0058 decision 7).

Run targeted:
    .venv/bin/python -m pytest tests/memory/test_bank_layout.py -q
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hal0.config.paths import var_lib
from hal0.config.schema import MemoryConfig
from hal0.memory import bank_layout as bl
from hal0.memory.embedder_record import STORE_ABSENT, STORE_EXISTS, STORE_UNAVAILABLE


def cfg(**raw: Any) -> MemoryConfig:
    """``[memory]`` as hal0.toml would load it: only the keys given are set."""
    return MemoryConfig.model_validate(raw)


@pytest.fixture(autouse=True)
def no_engine_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    """No engine unit on the test host: the unit-without-a-home rule says
    nothing unless a test hands it evidence."""
    from hal0.memory import embedder_record

    monkeypatch.setattr(embedder_record, "engine_evidence", lambda **_kw: {})
    monkeypatch.delenv(bl.ENGINE_URL_ENV, raising=False)
    # hal0.toml as a file: tests that care hand one in (``raw_toml``); the
    # rest read the loaded model, as a box whose file cannot be read does.
    monkeypatch.setattr(bl, "_raw_memory_table", lambda: None)


def raw_toml(monkeypatch: pytest.MonkeyPatch, table: dict[str, Any]) -> MemoryConfig:
    """Make ``table`` hal0.toml's ``[memory]`` table; return it loaded."""
    monkeypatch.setattr(bl, "_raw_memory_table", lambda: dict(table))
    return MemoryConfig.model_validate(table)


def saved_by_this_version(table: dict[str, Any]) -> dict[str, Any]:
    """``[memory]`` after a config save by this version: every field written
    back (``save_hal0_config`` dumps the whole model)."""
    return MemoryConfig.model_validate(table).model_dump(mode="python", exclude_none=True)


@pytest.fixture
def record(tmp_path: Path) -> Path:
    return tmp_path / "state" / "memory-layout.json"


def test_the_record_lives_in_the_state_root() -> None:
    assert bl.layout_record_path() == var_lib() / "state" / "memory-layout.json"


# ── determination ─────────────────────────────────────────────────────────────


def test_a_store_that_does_not_exist_is_two_bank() -> None:
    got = bl.determine_layout(cfg(), store=STORE_ABSENT, engine_url="")
    assert (got.layout, got.source) == (bl.LAYOUT_TWO_BANK, bl.SOURCE_STORE_ABSENT)
    assert got.health_layout == "two_bank"


def test_an_existing_store_is_legacy_with_the_old_defaults() -> None:
    got = bl.determine_layout(cfg(), store=STORE_EXISTS, engine_url="")
    assert got.layout == bl.LAYOUT_LEGACY
    assert (got.legacy_platform_bank, got.legacy_unified) == ("shared", True)
    assert got.source == bl.SOURCE_STORE_EXISTS
    assert got.health_layout == "legacy_unified"


@pytest.mark.parametrize(
    ("raw", "bank", "unified", "health"),
    [
        ({"unified_bank": False}, "shared", False, "legacy_split"),
        (
            {"unified_bank": True, "bank": "Hal0::Platform"},
            "Hal0::Platform",
            True,
            "legacy_unified",
        ),
        # Set in the file before this version ever saved it: the operator's.
        ({"bank": "hal0:shared"}, "hal0:shared", True, "legacy_unified"),
        # Not a boolean: the old default.
        ({"unified_bank": "no"}, "shared", True, "legacy_unified"),
    ],
)
def test_the_legacy_capture_reads_the_old_config(raw, bank, unified, health) -> None:
    got = bl.determine_layout(cfg(**raw), store=STORE_EXISTS, engine_url="")
    assert (got.legacy_platform_bank, got.legacy_unified, got.health_layout) == (
        bank,
        unified,
        health,
    )


def test_no_store_but_an_old_config_key_is_legacy() -> None:
    """``unified_bank`` is a key only the old schema wrote."""
    got = bl.determine_layout(cfg(unified_bank=True), store=STORE_ABSENT, engine_url="")
    assert (got.layout, got.source) == (bl.LAYOUT_LEGACY, bl.SOURCE_LEGACY_CONFIG)


def test_an_unavailable_store_is_legacy() -> None:
    got = bl.determine_layout(cfg(), store=STORE_UNAVAILABLE, engine_url="")
    assert (got.layout, got.source) == (bl.LAYOUT_LEGACY, bl.SOURCE_STORE_UNAVAILABLE)


@pytest.mark.parametrize("url", ["http://192.0.2.10:9177", "https://memory.example.org"])
def test_an_engine_on_another_host_is_legacy(url: str) -> None:
    got = bl.determine_layout(cfg(), store=STORE_ABSENT, engine_url=url)
    assert (got.layout, got.source) == (bl.LAYOUT_LEGACY, bl.SOURCE_REMOTE_ENGINE)


@pytest.mark.parametrize("url", ["http://127.0.0.1:9177", "http://localhost:9177", ""])
def test_a_loopback_engine_is_looked_at(url: str) -> None:
    assert bl.determine_layout(cfg(), store=STORE_ABSENT, engine_url=url).two_bank


# ── the record ────────────────────────────────────────────────────────────────


def test_ensure_writes_a_new_store_as_two_bank_once(record: Path) -> None:
    first = bl.ensure_layout_record(cfg(), path=record, store=STORE_ABSENT, engine_url="")
    assert first.two_bank and first.persisted
    body = json.loads(record.read_text())
    assert body["version"] == 1 and body["layout"] == "two_bank" and body["captured"] is None
    assert body["source"] == "store-absent"
    assert record.stat().st_mode & 0o777 == 0o644

    # The engine has started since: the record, not the store, decides.
    again = bl.ensure_layout_record(cfg(), path=record, store=STORE_EXISTS, engine_url="")
    assert again == first
    assert json.loads(record.read_text()) == body


def test_ensure_writes_an_upgraded_store_as_legacy_and_never_flips_it(record: Path) -> None:
    old = cfg(unified_bank=False, bank="Hal0::Platform")
    first = bl.ensure_layout_record(old, path=record, store=STORE_EXISTS, engine_url="")
    body = json.loads(record.read_text())
    assert body["layout"] == "legacy"
    assert body["captured"] == {"platform_bank": "Hal0::Platform", "unified": False}

    # The config moved on and the store looks new: nothing changes.
    again = bl.ensure_layout_record(cfg(), path=record, store=STORE_ABSENT, engine_url="")
    assert again == first and again.legacy_platform_bank == "Hal0::Platform"


@pytest.mark.parametrize(
    ("store", "url"), [(STORE_UNAVAILABLE, ""), (STORE_ABSENT, "http://192.0.2.10:9177")]
)
def test_a_store_hal0_cannot_see_is_not_written_down(record: Path, store: str, url: str) -> None:
    got = bl.ensure_layout_record(cfg(), path=record, store=store, engine_url=url)
    assert got.layout == bl.LAYOUT_LEGACY and not got.persisted
    assert not record.exists()


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "[]",
        json.dumps({"layout": "three_bank", "recorded_at": "x"}),
        json.dumps({"layout": "legacy", "recorded_at": "x"}),
        json.dumps({"layout": "two_bank"}),
    ],
)
def test_an_unreadable_record_is_kept_and_is_an_error(record: Path, content: str) -> None:
    """Not a guess at the old layout: a two-bank box would then read
    ``shared`` for that run."""
    record.parent.mkdir(parents=True)
    record.write_text(content)
    with pytest.raises(bl.LayoutRecordError):
        bl.load_layout_record(record)
    with pytest.raises(bl.LayoutUndecided):
        bl.ensure_layout_record(cfg(), path=record, store=STORE_ABSENT, engine_url="")
    with pytest.raises(bl.LayoutRecordError):
        bl.current_layout(cfg(), path=record)
    assert record.read_text() == content


def _unwritable(monkeypatch) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(bl.nofollow, "write_file", boom)


def test_a_two_bank_decision_that_cannot_be_written_is_not_acted_on(record, monkeypatch) -> None:
    """Unwritten, it would read as the old layout once the engine has
    created a store, and the memory written meanwhile would be stranded."""
    _unwritable(monkeypatch)
    with pytest.raises(bl.LayoutRecordUnsaved, match="could not be written"):
        bl.ensure_layout_record(cfg(), path=record, store=STORE_ABSENT, engine_url="")


def test_an_old_layout_that_cannot_be_written_still_answers(record, monkeypatch) -> None:
    """The next start decides the same: the store is still there."""
    _unwritable(monkeypatch)
    got = bl.ensure_layout_record(cfg(), path=record, store=STORE_EXISTS, engine_url="")
    assert got.layout == bl.LAYOUT_LEGACY and not got.persisted


# ── the engine hal0-api uses, read from api.env (review defect 1) ─────────────


def test_the_engine_url_comes_from_api_env_without_the_api_environment(tmp_path) -> None:
    env = tmp_path / "api.env"
    env.write_text("HAL0_API_PORT=8080\nHAL0_HINDSIGHT_URL='http://192.0.2.10:9177'\n")
    assert bl.configured_engine_url(env_file=env) == ("http://192.0.2.10:9177", True)
    assert bl.configured_engine_url(env_file=tmp_path / "missing.env") == (None, True)


def test_the_environment_wins_over_api_env(tmp_path, monkeypatch) -> None:
    env = tmp_path / "api.env"
    env.write_text("HAL0_HINDSIGHT_URL=http://192.0.2.10:9177\n")
    monkeypatch.setenv(bl.ENGINE_URL_ENV, "http://127.0.0.1:9177")
    assert bl.configured_engine_url(env_file=env) == ("http://127.0.0.1:9177", True)


def test_a_remote_engine_named_only_in_api_env_is_never_two_bank(record, monkeypatch) -> None:
    """The root-run update pass: no hal0-api environment, no local store."""
    monkeypatch.setattr(bl, "configured_engine_url", lambda **_kw: ("http://192.0.2.10:9177", True))
    got = bl.ensure_layout_record(cfg(), path=record, store=STORE_ABSENT)
    assert (got.layout, got.source) == (bl.LAYOUT_LEGACY, bl.SOURCE_REMOTE_ENGINE)
    assert not record.exists()


def test_an_api_env_that_cannot_be_read_defers_the_decision(record, monkeypatch) -> None:
    monkeypatch.setattr(bl, "configured_engine_url", lambda **_kw: (None, False))
    with pytest.raises(bl.LayoutRecordUnsaved, match=r"api\.env"):
        bl.ensure_layout_record(cfg(), path=record, store=STORE_ABSENT)
    assert not record.exists()
    # An existing store is the old layout whatever the engine's address.
    assert bl.ensure_layout_record(cfg(), path=record, store=STORE_EXISTS).persisted


def test_an_unreadable_api_env_is_unknown(tmp_path) -> None:
    env = tmp_path / "api.env"
    env.write_text("HAL0_HINDSIGHT_URL=http://192.0.2.10:9177\n")
    env.chmod(0)
    try:
        import os

        if os.geteuid() == 0:
            pytest.skip("root reads a mode-0 file")
        assert bl.configured_engine_url(env_file=env) == (None, False)
    finally:
        env.chmod(0o600)


# ── an installed unit whose home is missing (review defect 3) ────────────────


def test_a_unit_without_its_home_decides_nothing(record, tmp_path, monkeypatch) -> None:
    from hal0.memory import embedder_record

    monkeypatch.setattr(
        embedder_record, "engine_evidence", lambda **_kw: {"on_disk_source": "unit"}
    )
    monkeypatch.setattr(
        "hal0.memory.engine_upgrade.hindsight_dir", lambda: tmp_path / "unmounted" / "hindsight"
    )
    got = bl.ensure_layout_record(cfg(), path=record, store=STORE_ABSENT, engine_url="")
    assert (got.layout, got.source) == (bl.LAYOUT_LEGACY, bl.SOURCE_STORE_UNAVAILABLE)
    assert not record.exists()


def test_current_layout_never_writes(record: Path, monkeypatch) -> None:
    monkeypatch.setattr(bl, "determine_layout", lambda *_a, **_k: bl.BankLayout("two_bank"))
    assert bl.current_layout(cfg(), path=record).two_bank
    assert not record.exists()


def test_record_memory_layout_reads_hal0_toml(monkeypatch) -> None:
    from types import SimpleNamespace

    from hal0.config import loader

    seen: list[Any] = []
    monkeypatch.setattr(
        loader, "load_hal0_config", lambda: SimpleNamespace(memory=cfg(unified_bank=False))
    )
    monkeypatch.setattr(bl, "ensure_layout_record", lambda memory: seen.append(memory) or "ok")
    assert bl.record_memory_layout() == "ok"
    assert seen[0].model_extra == {"unified_bank": False}

    def unreadable() -> None:
        raise PermissionError("hal0.toml")

    monkeypatch.setattr(loader, "load_hal0_config", unreadable)
    assert bl.record_memory_layout() is None


# ── what the provider is built with ──────────────────────────────────────────


def test_two_bank_takes_both_banks_from_the_config() -> None:
    layout = bl.BankLayout(bl.LAYOUT_TWO_BANK)
    assert layout.provider_kwargs(cfg()) == {
        "two_bank": True,
        "unified_bank": False,
        "bank": "hal0:shared",
        "private_bank": "hal0:private",
    }
    custom = cfg(bank="mine:shared", private_bank="mine:private")
    assert layout.provider_kwargs(custom)["bank"] == "mine:shared"
    assert layout.provider_kwargs(custom)["private_bank"] == "mine:private"


def test_legacy_reads_the_recorded_bank_unless_the_operator_moved_it() -> None:
    """ADR-0052: ``migrate platform`` then ``[memory] bank`` still moves a
    legacy store's platform bank; the two-bank default does not."""
    layout = bl.BankLayout(bl.LAYOUT_LEGACY, legacy_platform_bank="shared", legacy_unified=False)
    assert layout.provider_kwargs(cfg()) == {
        "two_bank": False,
        "unified_bank": False,
        "bank": "shared",
    }
    assert layout.provider_kwargs(cfg(bank="hal0:shared"))["bank"] == "shared"
    assert layout.provider_kwargs(cfg(bank="Hal0::Platform"))["bank"] == "Hal0::Platform"


def test_platform_bank_reads_the_layout(record: Path) -> None:
    bl.save_layout_record(
        bl.BankLayout(bl.LAYOUT_LEGACY, legacy_platform_bank="old", legacy_unified=True), record
    )
    assert bl.platform_bank(cfg(), path=record) == "old"
    bl.save_layout_record(bl.BankLayout(bl.LAYOUT_TWO_BANK), record)
    assert bl.platform_bank(cfg(), path=record) == "hal0:shared"


def test_provider_from_config_builds_from_the_record(monkeypatch) -> None:
    from hal0 import memory
    from hal0.config.schema import Hal0Config

    monkeypatch.setattr(memory, "_build_hindsight_client", lambda _cfg: object())
    monkeypatch.setattr(
        bl,
        "ensure_layout_record",
        lambda _m: bl.BankLayout(bl.LAYOUT_LEGACY, legacy_platform_bank="shared"),
    )
    legacy = memory.provider_from_config(Hal0Config())
    assert (legacy.two_bank, legacy.unified_bank, legacy.platform_bank) == (False, True, "shared")
    assert legacy.layout == "legacy_unified"

    monkeypatch.setattr(bl, "ensure_layout_record", lambda _m: bl.BankLayout(bl.LAYOUT_TWO_BANK))
    fresh = memory.provider_from_config(Hal0Config())
    assert (fresh.two_bank, fresh.platform_bank, fresh.private_bank) == (
        True,
        "hal0:shared",
        "hal0:private",
    )
    assert fresh.layout == "two_bank"


def test_provider_from_config_serves_no_bank_while_the_layout_is_undecided(monkeypatch) -> None:
    from hal0 import memory
    from hal0.config.schema import Hal0Config
    from hal0.memory.pgvector_provider import PgVectorProvider

    probed: list[Any] = []
    monkeypatch.setattr(memory, "_build_hindsight_client", lambda _cfg: probed.append(1))

    def undecided(_m: Any) -> Any:
        raise bl.LayoutRecordUnsaved("no")

    monkeypatch.setattr(bl, "ensure_layout_record", undecided)
    provider = memory.provider_from_config(Hal0Config())
    assert isinstance(provider, PgVectorProvider) and provider.degraded
    assert provider.layout_undecided == "no"
    assert probed == []


@pytest.mark.asyncio
async def test_the_undecided_fallback_refuses_writes_and_keeps_reading() -> None:
    """A write accepted there would be lost on restart (review B)."""
    provider = bl.undecided_fallback("the record cannot be written")
    with pytest.raises(bl.MemoryLayoutUndecided) as refused:
        await provider.add("a note", client_id="hermes")
    assert (refused.value.status, refused.value.code) == (503, "memory.layout_undecided")
    assert refused.value.details == {"reason": "the record cannot be written"}
    with pytest.raises(bl.MemoryLayoutUndecided):
        await provider.delete(["x"], client_id="hermes")
    assert await provider.search("q") == []
    assert (await provider.list_items())["items"] == []


@pytest.mark.asyncio
async def test_the_add_route_answers_a_typed_503_while_undecided() -> None:
    import httpx
    from fastapi import FastAPI

    from hal0.api.middleware import error_codes
    from hal0.api.routes import memory as memory_routes
    from hal0.api.routes import memory_admin

    app = FastAPI()
    error_codes.install(app)
    app.include_router(memory_routes.router, prefix="/api/memory")
    app.include_router(memory_admin.router, prefix="/api/memory")
    app.state.memory_provider = bl.undecided_fallback("the record cannot be written")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
        added = await client.post("http://t/api/memory/add", json={"text": "x"})
        engine = await client.get("http://t/api/memory/engine")
    assert added.status_code == 503
    assert added.json()["error"]["code"] == "memory.layout_undecided"
    assert engine.json()["layout_undecided"] == "the record cannot be written"


# ── a config save by this version does not move an old layout's bank (review A)


@pytest.mark.parametrize(
    ("store", "url"),
    [(STORE_EXISTS, "http://192.0.2.10:9177"), (STORE_UNAVAILABLE, "")],
)
def test_a_save_between_captures_keeps_the_old_bank(record, monkeypatch, store, url) -> None:
    """The paths that never write a record capture again at every start:
    capture, save, capture must name the same bank."""
    old = {"enabled": True, "unified_bank": True}
    first = bl.ensure_layout_record(
        raw_toml(monkeypatch, old), path=record, store=store, engine_url=url
    )
    assert first.legacy_platform_bank == "shared" and not first.persisted

    saved = saved_by_this_version(old)
    assert saved["bank"] == "hal0:shared" and "private_bank" in saved
    again = bl.ensure_layout_record(
        raw_toml(monkeypatch, saved), path=record, store=store, engine_url=url
    )
    assert again.legacy_platform_bank == "shared" and again.legacy_unified is True
    assert not record.exists()


def test_an_operator_set_two_bank_name_is_captured_as_written(monkeypatch) -> None:
    """``bank = "hal0:shared"`` with no ``private_bank`` beside it was written
    by hand (no older version wrote ``private_bank``)."""
    memory = raw_toml(monkeypatch, {"bank": "hal0:shared", "unified_bank": True})
    got = bl.determine_layout(memory, store=STORE_EXISTS, engine_url="")
    assert got.legacy_platform_bank == "hal0:shared"


def test_an_operator_bank_survives_a_save(monkeypatch) -> None:
    saved = saved_by_this_version({"bank": "Hal0::Platform", "unified_bank": True})
    got = bl.determine_layout(raw_toml(monkeypatch, saved), store=STORE_EXISTS, engine_url="")
    assert got.legacy_platform_bank == "Hal0::Platform"


def test_the_loaded_model_stands_in_for_an_unreadable_file() -> None:
    """Same rule from the model's set fields (the fixture hides the file)."""
    saved = cfg(bank="hal0:shared", private_bank="hal0:private", unified_bank=True)
    assert bl.determine_layout(saved, store=STORE_EXISTS, engine_url="").legacy_platform_bank == (
        "shared"
    )


def test_the_raw_table_reads_hal0_toml(tmp_path, monkeypatch) -> None:
    monkeypatch.undo()
    toml = tmp_path / "hal0.toml"
    toml.write_text('[memory]\nbank = "hal0:shared"\nunified_bank = true\n')
    monkeypatch.setattr("hal0.config.paths.hal0_toml", lambda: toml)
    assert bl._raw_memory_table() == {"bank": "hal0:shared", "unified_bank": True}
    toml.write_text("not = [toml")
    assert bl._raw_memory_table() is None


# ── a reader that cannot decide says so (review D) ────────────────────────────


def test_current_layout_refuses_an_undecided_two_bank(record, monkeypatch) -> None:
    monkeypatch.setattr(bl, "configured_engine_url", lambda **_kw: (None, False))
    monkeypatch.setattr("hal0.memory.embedder_record.store_state", lambda *_a, **_k: STORE_ABSENT)
    with pytest.raises(bl.LayoutUndecided, match="hal0-api starts"):
        bl.current_layout(cfg(), path=record)
    with pytest.raises(bl.LayoutUndecided):
        bl.platform_bank(cfg(), path=record)

    from hal0.memory.spec_apply import spec_targets

    monkeypatch.setattr(bl, "layout_record_path", lambda: record)
    with pytest.raises(bl.LayoutUndecided):
        spec_targets(cfg())


def test_provider_from_config_records_the_layout_before_the_engine_answers(monkeypatch) -> None:
    """A boot that finds no engine yet (a new install starts hal0-api
    before the engine) still records the layout while there is no store."""
    from hal0 import memory
    from hal0.config.schema import Hal0Config

    seen: list[Any] = []
    monkeypatch.setattr(
        bl, "ensure_layout_record", lambda m: seen.append(m) or bl.BankLayout("two_bank")
    )

    def down(_cfg: Any) -> Any:
        raise ConnectionError("engine not started yet")

    monkeypatch.setattr(memory, "_build_hindsight_client", down)
    assert memory.provider_from_config(Hal0Config()).degraded
    assert len(seen) == 1


def test_the_update_pass_records_the_layout_before_the_config_migrations(monkeypatch) -> None:
    """A config migration saves hal0.toml with the new ``[memory]`` defaults;
    the capture has to read the old ones first."""
    from hal0.updater import updater

    order: list[str] = []
    monkeypatch.setattr(bl, "record_memory_layout", lambda: order.append("layout"))

    def migrations(*_a: Any, **_k: Any) -> Any:
        order.append("config")
        raise RuntimeError("stop here")

    monkeypatch.setattr(updater, "_maybe_run_config_migrations", migrations)
    monkeypatch.setattr(
        "hal0.config.migrations.runner_system.migrate_runner_system",
        lambda **_k: type("C", (), {"backup": None})(),
    )
    with pytest.raises(RuntimeError, match="stop here"):
        updater.run_post_activation_migrations(converge_companions=False)
    assert order == ["layout", "config"]
