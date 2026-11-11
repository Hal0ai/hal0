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
        # A config save writes the new default back without anyone choosing it.
        ({"bank": "hal0:shared"}, "shared", True, "legacy_unified"),
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


@pytest.mark.parametrize("url", ["http://10.9.8.7:9177", "https://memory.example.org"])
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
    ("store", "url"), [(STORE_UNAVAILABLE, ""), (STORE_ABSENT, "http://10.9.8.7:9177")]
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
def test_an_unreadable_record_is_kept_and_read_as_legacy(record: Path, content: str) -> None:
    record.parent.mkdir(parents=True)
    record.write_text(content)
    with pytest.raises(bl.LayoutRecordError):
        bl.load_layout_record(record)
    got = bl.ensure_layout_record(
        cfg(bank="Hal0::Platform"), path=record, store=STORE_ABSENT, engine_url=""
    )
    assert (got.layout, got.source, got.persisted) == (
        bl.LAYOUT_LEGACY,
        bl.SOURCE_RECORD_UNREADABLE,
        False,
    )
    assert got.legacy_platform_bank == "Hal0::Platform"
    assert record.read_text() == content


def test_a_failed_write_still_answers(record: Path, monkeypatch) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(bl.nofollow, "write_file", boom)
    got = bl.ensure_layout_record(cfg(), path=record, store=STORE_ABSENT, engine_url="")
    assert got.two_bank and not got.persisted


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
