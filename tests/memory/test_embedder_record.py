"""The memory store's embedder record (#3518).

Run targeted:
    .venv/bin/python -m pytest tests/memory/test_embedder_record.py -q
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from hal0.memory import embedder_record as er
from hal0.memory.embedder_record import Embedder

BGE = "BAAI/bge-small-en-v1.5"
HS = "hindsight-ai/hs-embed-1"
DEFAULT = Embedder("local", BGE)
CONFIGURED = Embedder("local", HS)  # what [memory.models] ships


def ev(
    live: dict[str, Any] | None = None,
    on_disk: dict[str, Any] | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """An ``engine_evidence()`` result. ``source`` defaults to ``unit`` when ``on_disk`` is given."""
    if on_disk is not None and source is None:
        source = "unit"
    return {"live": live, "on_disk": on_disk, "on_disk_source": source}


def env(provider: str | None = None, model: str | None = None) -> dict[str, Any]:
    """One evidence layer: the raw values, ``None`` where the environment leaves a key unset."""
    return {"provider": provider, "model": model}


NOTHING = ev()
#: A unit that is known and names no embedder: the base unit, no drop-in.
BASE_UNIT = ev(on_disk=env())


class Box:
    """The record file and the engine home, under one tmp dir."""

    def __init__(self, root: Path) -> None:
        self.record = root / "state" / "memory-embedder.json"
        self.hs_dir = root / "memory" / "hindsight"

    @property
    def kw(self) -> dict[str, Path]:
        return {"path": self.record, "hs_dir": self.hs_dir}

    def with_store(self) -> Box:
        """An engine that has started: pg0's cluster under ``$HOME/.pg0``."""
        data = self.hs_dir / ".pg0" / "instances" / "hindsight" / "data"
        data.mkdir(parents=True)
        (data / "PG_VERSION").write_text("18\n")
        return self

    def with_record(self, provider: str = "local", model: str = BGE, **extra: Any) -> Box:
        self.record.parent.mkdir(parents=True, exist_ok=True)
        self.record.write_text(
            json.dumps(
                {
                    "version": 1,
                    "provider": provider,
                    "model": model,
                    "source": "engine-process",
                    "mixed": False,
                    "recorded_at": "2026-11-10T21:00:00+00:00",
                    **extra,
                }
            )
        )
        return self


@pytest.fixture
def box(tmp_path: Path) -> Box:
    return Box(tmp_path)


# ── the two embedders in play ────────────────────────────────────────────────


def test_the_engine_default_and_the_config_default_are_the_two_embedders_in_play() -> None:
    assert er.ENGINE_DEFAULT == DEFAULT
    assert er.shipped_default_embedder() == CONFIGURED


def test_the_record_lives_in_the_state_directory(tmp_hal0_home: str) -> None:
    from hal0.config.paths import var_lib

    assert er.embedder_record_path() == var_lib() / "state" / "memory-embedder.json"
    assert str(er.embedder_record_path()).startswith(tmp_hal0_home)


# ── "existing store" versus "fresh": leans towards "exists" ──────────────────


def test_no_engine_home_is_not_a_store(box: Box) -> None:
    assert er.store_state(box.hs_dir) == er.STORE_ABSENT


def test_an_engine_home_the_installer_just_made_is_not_a_store(box: Box) -> None:
    """install.sh creates the venv, hf-cache and .cache; none of them is a store."""
    for name in (".venv", "hf-cache", ".cache"):
        (box.hs_dir / name).mkdir(parents=True)
    assert er.store_state(box.hs_dir) == er.STORE_ABSENT


def test_an_empty_pg0_with_no_engine_beside_it_is_not_a_store(box: Box) -> None:
    (box.hs_dir / ".pg0").mkdir(parents=True)
    assert er.store_state(box.hs_dir) == er.STORE_ABSENT


def test_an_empty_pg0_beside_an_engine_venv_is_a_store(box: Box) -> None:
    """An installed engine with a data directory has been started, whatever is left in it."""
    (box.hs_dir / ".pg0").mkdir(parents=True)
    (box.hs_dir / ".venv").mkdir()
    assert er.store_state(box.hs_dir) == er.STORE_EXISTS


def test_an_initialised_pg0_is_a_store(box: Box) -> None:
    box.with_store()
    assert er.store_state(box.hs_dir) == er.STORE_EXISTS


def test_a_pg0_holding_only_the_postgres_install_is_a_store(box: Box) -> None:
    (box.hs_dir / ".pg0" / "installation").mkdir(parents=True)
    assert er.store_state(box.hs_dir) == er.STORE_EXISTS


def test_a_dangling_pg0_symlink_is_a_store(box: Box) -> None:
    """The data directory is linked to a disk that is not there: not "fresh"."""
    box.hs_dir.mkdir(parents=True)
    (box.hs_dir / ".pg0").symlink_to(box.hs_dir.parent / "gone" / "pg0")
    assert not (box.hs_dir / ".pg0").exists()
    assert er.store_state(box.hs_dir) == er.STORE_EXISTS


def test_a_pg0_that_is_not_a_directory_is_a_store(box: Box) -> None:
    box.hs_dir.mkdir(parents=True)
    (box.hs_dir / ".pg0").write_text("")
    assert er.store_state(box.hs_dir) == er.STORE_EXISTS


@pytest.mark.parametrize("sibling", [".pg0.pre-1.1.0", ".pg0.snapshot-prev", ".pg0.restore-failed"])
def test_an_engine_upgrade_snapshot_or_marker_means_a_store_existed(box: Box, sibling: str) -> None:
    """A failed restore can leave these beside a missing or empty ``.pg0``."""
    box.hs_dir.mkdir(parents=True)
    (box.hs_dir / sibling).write_text("x")
    assert er.store_state(box.hs_dir) == er.STORE_EXISTS
    (box.hs_dir / ".pg0").mkdir()
    assert er.store_state(box.hs_dir) == er.STORE_EXISTS


def test_an_unlistable_engine_home_is_treated_as_a_store(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cannot rule a store out, so do not answer "fresh"."""
    box.hs_dir.mkdir(parents=True)

    def denied(_path: object) -> None:
        raise PermissionError(13, "denied")

    monkeypatch.setattr(os, "listdir", denied)
    assert er.store_state(box.hs_dir) == er.STORE_EXISTS


def test_an_engine_home_that_is_a_broken_symlink_is_unavailable(box: Box) -> None:
    box.hs_dir.parent.mkdir(parents=True)
    box.hs_dir.symlink_to(box.hs_dir.parent / "unplugged-disk" / "hindsight")
    assert er.store_state(box.hs_dir) == er.STORE_UNAVAILABLE


def test_an_engine_home_under_an_unmounted_mount_point_is_unavailable(
    box: Box, tmp_path: Path
) -> None:
    """fstab says a disk belongs above the engine home and it is not mounted:
    the empty directory underneath says nothing about the store."""
    mount_point = box.hs_dir.parent
    mount_point.mkdir(parents=True)
    fstab = tmp_path / "fstab"
    fstab.write_text(
        "# <file system> <mount point> <type> <options>\n"
        "UUID=0000-root / ext4 defaults 0 1\n"
        f"UUID=0000-data {mount_point} ext4 defaults,nofail 0 2\n"
    )
    assert er.store_state(box.hs_dir, fstab=fstab) == er.STORE_UNAVAILABLE
    (box.hs_dir / ".venv").mkdir(parents=True)
    assert er.store_state(box.hs_dir, fstab=fstab) == er.STORE_UNAVAILABLE


def test_a_mounted_mount_point_above_the_engine_home_is_looked_into(
    box: Box, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mount_point = box.hs_dir.parent
    fstab = tmp_path / "fstab"
    fstab.write_text(f"UUID=0000-data {mount_point} ext4 defaults 0 2\n")
    monkeypatch.setattr(os.path, "ismount", lambda p: str(p) == str(mount_point))
    assert er.store_state(box.hs_dir, fstab=fstab) == er.STORE_ABSENT
    box.with_store()
    assert er.store_state(box.hs_dir, fstab=fstab) == er.STORE_EXISTS


def test_the_default_engine_home_is_the_engine_upgrade_one(tmp_hal0_home: str) -> None:
    from hal0.memory.engine_upgrade import hindsight_dir

    assert er.store_state() == er.STORE_ABSENT
    (hindsight_dir() / ".pg0" / "instances").mkdir(parents=True)
    assert er.store_state() == er.STORE_EXISTS


# ── determination: the running engine first ──────────────────────────────────


def test_a_running_engine_with_no_embedder_environment_is_on_the_engine_default(box: Box) -> None:
    """The upgraded box: base unit, no drop-in, engine up. Unset means the default."""
    box.with_store()

    view = er.resolve_embedder(CONFIGURED, evidence=ev(live=env(), on_disk=env()), **box.kw)

    assert view.record.embedder == DEFAULT
    assert (view.record.source, view.record.mixed) == (er.SOURCE_PROCESS, False)
    assert view.record.conflict is None and view.conflict is None
    assert view.render == DEFAULT


def test_a_running_engine_on_another_model_over_a_store_is_mixed(box: Box) -> None:
    box.with_store()

    view = er.resolve_embedder(
        CONFIGURED, evidence=ev(live=env("local", HS), on_disk=env("local", HS)), **box.kw
    )

    assert view.record.embedder == Embedder("local", HS)
    assert (view.record.source, view.record.mixed) == (er.SOURCE_PROCESS, True)


def test_a_running_engine_wins_over_the_configured_embedder(box: Box) -> None:
    box.with_store()
    view = er.resolve_embedder(DEFAULT, evidence=ev(live=env("local", HS)), **box.kw)
    assert view.record.embedder == Embedder("local", HS)


def test_a_running_engine_with_no_store_is_not_mixed(box: Box) -> None:
    view = er.resolve_embedder(CONFIGURED, evidence=ev(live=env("local", HS)), **box.kw)
    assert (view.record.model, view.record.mixed) == (HS, False)


def test_a_remote_provider_is_a_different_embedder(box: Box) -> None:
    """Provider and model are one fact: a remote provider over a store is mixed too."""
    box.with_store()

    view = er.resolve_embedder(CONFIGURED, evidence=ev(live=env(provider="openai")), **box.kw)

    assert view.record.embedder == Embedder("openai", BGE)
    assert view.record.mixed is True


def test_live_and_unit_files_disagreeing_records_the_live_one_with_a_conflict(box: Box) -> None:
    """The engine runs X, its unit files would start it on Y: never silently Y."""
    box.with_store()
    evidence = ev(live=env(), on_disk=env("local", HS))

    view = er.ensure_embedder_record(CONFIGURED, evidence=evidence, **box.kw)

    assert view.record.embedder == DEFAULT and view.record.persisted
    assert view.record.conflict == {"on_disk": {"provider": "local", "model": HS}}
    assert json.loads(box.record.read_text())["conflict"] == view.record.conflict
    assert view.conflict == {"on_disk": {"provider": "local", "model": HS}}
    assert view.render == DEFAULT


# ── determination: the unit files when the engine is down ────────────────────


def test_unit_files_naming_an_embedder_are_believed_when_the_engine_is_down(box: Box) -> None:
    """A later-sorting drop-in, or the EnvironmentFile, already merged by the evidence."""
    box.with_store()

    view = er.resolve_embedder(
        CONFIGURED, evidence=ev(on_disk=env(model=HS), source="unit+file"), **box.kw
    )

    assert view.record.embedder == Embedder("local", HS)
    assert (view.record.source, view.record.mixed) == (er.SOURCE_UNIT, True)


def test_unit_files_naming_the_engine_default_are_not_mixed(box: Box) -> None:
    box.with_store()
    view = er.resolve_embedder(CONFIGURED, evidence=ev(on_disk=env("local", BGE)), **box.kw)
    assert (view.record.embedder, view.record.mixed) == (DEFAULT, False)


def test_hal0s_own_dropin_is_read_where_there_is_no_unit_to_ask(box: Box) -> None:
    box.with_store()

    view = er.resolve_embedder(
        CONFIGURED, evidence=ev(on_disk=env("local", HS), source="drop-in"), **box.kw
    )

    assert (view.record.source, view.record.model, view.record.mixed) == (
        er.SOURCE_DROPIN,
        HS,
        True,
    )


def test_a_dropin_without_a_store_is_not_mixed(box: Box) -> None:
    """A store that does not exist yet cannot hold older vectors."""
    view = er.resolve_embedder(
        CONFIGURED, evidence=ev(on_disk=env("local", HS), source="drop-in"), **box.kw
    )
    assert (view.record.model, view.record.mixed) == (HS, False)


# ── determination: nothing names an embedder ─────────────────────────────────


@pytest.mark.parametrize("evidence", [NOTHING, BASE_UNIT, ev(on_disk=env(), source="drop-in")])
def test_existing_store_and_nothing_naming_an_embedder_is_the_engine_default(
    box: Box, evidence: dict[str, Any]
) -> None:
    box.with_store()

    view = er.resolve_embedder(CONFIGURED, evidence=evidence, **box.kw)

    assert view.record.embedder == DEFAULT
    assert (view.record.source, view.record.mixed) == (er.SOURCE_ENGINE_DEFAULT, False)


@pytest.mark.parametrize("evidence", [NOTHING, BASE_UNIT])
def test_no_store_and_nothing_naming_an_embedder_is_the_configured_pair(
    box: Box, evidence: dict[str, Any]
) -> None:
    """A fresh box. A unit that is installed but has never started proves nothing."""
    box.hs_dir.mkdir(parents=True)  # install.sh makes the home before the unit
    wanted = Embedder("local", "example-org/another-embedder")

    view = er.resolve_embedder(wanted, evidence=evidence, **box.kw)

    assert view.record.embedder == wanted
    assert (view.record.source, view.record.mixed) == (er.SOURCE_CONFIGURED, False)
    assert view.conflict is None  # no record yet for the base unit to disagree with


def test_an_unavailable_store_is_the_engine_default_and_never_the_configured_pair(
    box: Box,
) -> None:
    box.hs_dir.parent.mkdir(parents=True)
    box.hs_dir.symlink_to(box.hs_dir.parent / "unplugged-disk" / "hindsight")

    view = er.ensure_embedder_record(CONFIGURED, evidence=NOTHING, **box.kw)

    assert view.record.embedder == DEFAULT
    assert (view.record.source, view.record.mixed, view.record.persisted) == (
        er.SOURCE_UNAVAILABLE,
        False,
        False,
    )
    assert er.persist_embedder_record(view.record, path=box.record).persisted is False
    assert not box.record.exists()
    status = er.embedder_status(CONFIGURED, evidence=NOTHING, **box.kw)
    assert status["store_unavailable"] == str(box.hs_dir)
    assert "is not available" in (er.embedder_notice(status) or "")


def test_an_installed_unit_with_no_home_directory_is_unavailable_not_fresh(box: Box) -> None:
    """The unit is there and its home is not: the home is on storage that is
    missing, however it is mounted. An update run now must not record the
    configured embedder over the store that will come back."""
    assert not box.hs_dir.exists()

    view = er.ensure_embedder_record(CONFIGURED, evidence=BASE_UNIT, **box.kw)

    assert view.store == er.STORE_UNAVAILABLE
    assert (view.record.embedder, view.record.source, view.record.persisted) == (
        DEFAULT,
        er.SOURCE_UNAVAILABLE,
        False,
    )
    assert not box.record.exists()
    # With no unit to ask there is no engine on this box at all: fresh.
    assert er.resolve_embedder(CONFIGURED, evidence=NOTHING, **box.kw).record.source == (
        er.SOURCE_CONFIGURED
    )


# ── writing the record down ──────────────────────────────────────────────────


def test_ensure_records_an_existing_store(box: Box) -> None:
    box.with_store()

    record = er.ensure_embedder_record(CONFIGURED, evidence=BASE_UNIT, **box.kw).record

    assert record.persisted
    assert json.loads(box.record.read_text()) == {
        "version": 1,
        "provider": "local",
        "model": BGE,
        "source": "engine-default",
        "mixed": False,
        "recorded_at": record.recorded_at,
    }
    assert er.load_embedder_record(box.record) == record
    assert oct(box.record.stat().st_mode & 0o777) == oct(0o644)


def test_ensure_records_a_mixed_store_with_its_provider(box: Box) -> None:
    box.with_store()

    er.ensure_embedder_record(CONFIGURED, evidence=ev(live=env("local", HS)), **box.kw)

    on_disk = json.loads(box.record.read_text())
    assert (on_disk["provider"], on_disk["model"], on_disk["source"], on_disk["mixed"]) == (
        "local",
        HS,
        "engine-process",
        True,
    )


def test_ensure_does_not_record_a_box_whose_engine_has_not_started(box: Box) -> None:
    """Nothing makes the engine use the configured pair yet, so nothing is written:
    a store built under the engine default must not sit under a record saying otherwise."""
    (box.hs_dir / ".venv").mkdir(parents=True)
    view = er.ensure_embedder_record(CONFIGURED, evidence=BASE_UNIT, **box.kw)

    assert (view.record.embedder, view.record.persisted) == (CONFIGURED, False)
    assert not box.record.exists()


def test_fresh_install_then_first_engine_start_then_update_records_bge_small(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The order install.sh really runs in: the update migrations (no engine
    yet), then the engine's first start on the base unit, then a later update."""
    from hal0.memory.engine_upgrade import hindsight_dir

    state = {"evidence": NOTHING}
    monkeypatch.setattr(er, "engine_evidence", lambda **_kw: state["evidence"])

    # 1. install.sh's migration step: no engine venv, no unit, no store.
    first = er.record_store_embedder()
    assert first is not None and (first.embedder, first.persisted) == (CONFIGURED, False)
    assert not er.embedder_record_path().exists()

    # 2. install.sh creates the venv and starts the engine on the base unit:
    #    no embedder environment, so it embeds with its default.
    (hindsight_dir() / ".venv").mkdir(parents=True)
    (hindsight_dir() / ".pg0" / "instances" / "hindsight" / "data").mkdir(parents=True)
    state["evidence"] = ev(live=env(), on_disk=env())

    # 3. The first `hal0 update` afterwards.
    recorded = er.record_store_embedder()

    assert recorded is not None and recorded.persisted
    assert (recorded.embedder, recorded.mixed) == (DEFAULT, False)
    assert json.loads(er.embedder_record_path().read_text())["model"] == BGE


def test_persist_writes_the_configured_pair_once_the_dropin_carries_it(box: Box) -> None:
    record = er.resolve_embedder(CONFIGURED, evidence=NOTHING, **box.kw).record

    saved = er.persist_embedder_record(record, path=box.record)

    assert (saved.embedder, saved.source, saved.persisted) == (
        CONFIGURED,
        er.SOURCE_CONFIGURED,
        True,
    )
    assert er.load_embedder_record(box.record) == saved


def test_a_second_run_does_not_rewrite_the_record(box: Box) -> None:
    box.with_store()
    first = er.ensure_embedder_record(CONFIGURED, evidence=BASE_UNIT, **box.kw).record
    before = (box.record.read_bytes(), box.record.stat().st_mtime_ns, box.record.stat().st_ino)

    second = er.ensure_embedder_record(CONFIGURED, evidence=BASE_UNIT, **box.kw).record
    third = er.persist_embedder_record(
        er.EmbedderRecord(provider="local", model=HS, source=er.SOURCE_CONFIGURED),
        path=box.record,
    )

    assert second == first and third == first
    assert (
        box.record.read_bytes(),
        box.record.stat().st_mtime_ns,
        box.record.stat().st_ino,
    ) == before


def test_a_failed_write_returns_the_determined_value(
    box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    box.with_store()

    def refuse(*_a: object, **_k: object) -> None:
        raise PermissionError(13, "denied")

    with monkeypatch.context() as patched:
        patched.setattr(er.nofollow, "write_file", refuse)
        record = er.ensure_embedder_record(CONFIGURED, evidence=BASE_UNIT, **box.kw).record

    assert (record.embedder, record.persisted, record.mixed) == (DEFAULT, False, False)
    assert not box.record.exists()
    # The next run determines the same thing: a missing record is not evidence of anything.
    again = er.ensure_embedder_record(CONFIGURED, evidence=BASE_UNIT, **box.kw).record
    assert (again.embedder, again.persisted, again.mixed) == (DEFAULT, True, False)


def test_record_store_embedder_reads_the_configured_pair_from_hal0_toml(
    tmp_hal0_home: str,
) -> None:
    """The update hook: an upgraded box (store, nothing naming an embedder)."""
    from hal0.memory.engine_upgrade import hindsight_dir

    (hindsight_dir() / ".pg0" / "instances" / "hindsight" / "data").mkdir(parents=True)

    record = er.record_store_embedder()

    assert record is not None and (record.embedder, record.persisted) == (DEFAULT, True)
    assert json.loads(er.embedder_record_path().read_text())["source"] == "engine-default"


def test_record_store_embedder_survives_an_unreadable_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("hal0.toml is not TOML")

    monkeypatch.setattr("hal0.config.loader.load_hal0_config", boom)
    assert er.record_store_embedder() is None


# ── a record file that cannot be used is kept, never overwritten ─────────────

_UNUSABLE = [
    "{not json",
    "[]",
    json.dumps({"provider": "local", "model": "two words", "source": "x", "recorded_at": "t"}),
    json.dumps(
        {"provider": "local", "model": "../../etc/passwd", "source": "x", "recorded_at": "t"}
    ),
    json.dumps({"provider": "local", "model": BGE, "recorded_at": "2026-01-01T00:00:00"}),
    json.dumps({"provider": "local", "model": BGE, "source": "engine-default"}),
    json.dumps({"model": BGE, "source": "engine-default", "recorded_at": "t"}),
    json.dumps({"provider": "Not A Provider", "model": BGE, "source": "x", "recorded_at": "t"}),
]


@pytest.mark.parametrize("content", _UNUSABLE)
def test_an_unusable_record_is_kept_and_the_embedder_comes_from_the_evidence(
    box: Box, content: str
) -> None:
    box.with_store()
    box.record.parent.mkdir(parents=True)
    box.record.write_text(content)

    with pytest.raises(er.EmbedderRecordError):
        er.load_embedder_record(box.record)
    view = er.ensure_embedder_record(CONFIGURED, evidence=ev(live=env(), on_disk=env()), **box.kw)

    # Not overwritten, by ensure or by a later drop-in write.
    assert er.persist_embedder_record(view.record, path=box.record).persisted is False
    assert box.record.read_text() == content
    # The engine's own embedder for this run, and no "mixed" from the file being bad.
    assert view.record_error == str(box.record)
    assert (view.record.embedder, view.record.persisted, view.record.mixed) == (
        DEFAULT,
        False,
        False,
    )
    assert view.render == DEFAULT
    status = er.embedder_status(CONFIGURED, evidence=ev(live=env(), on_disk=env()), **box.kw)
    assert status["record_error"] == str(box.record) and status["mixed"] is False
    notice = er.embedder_notice(status) or ""
    assert str(box.record) in notice and "cannot be read" in notice


def test_an_unreadable_record_is_kept_too(box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    box.with_store().with_record(model=HS)
    before = box.record.read_bytes()
    real = Path.read_text

    def denied(self: Path, *a: Any, **k: Any) -> str:
        if self == box.record:
            raise PermissionError(13, "denied")
        return real(self, *a, **k)

    with monkeypatch.context() as patched:
        patched.setattr(Path, "read_text", denied)
        view = er.ensure_embedder_record(CONFIGURED, evidence=BASE_UNIT, **box.kw)

    assert view.record_error == str(box.record)
    assert (view.record.embedder, view.record.persisted) == (DEFAULT, False)
    assert box.record.read_bytes() == before


# ── a record that exists is still checked against the engine ─────────────────


def test_the_record_outlives_what_it_was_determined_from(box: Box) -> None:
    """Once written, neither the unit files nor the config can change the record."""
    box.with_store()
    er.ensure_embedder_record(CONFIGURED, evidence=BASE_UNIT, **box.kw)
    before = box.record.read_bytes()
    later = ev(on_disk=env("local", HS))

    view = er.ensure_embedder_record(
        Embedder("local", "example-org/another-embedder"), evidence=later, **box.kw
    )

    assert view.record.embedder == DEFAULT
    assert box.record.read_bytes() == before


def test_a_record_the_running_engine_disagrees_with_changes_neither(box: Box) -> None:
    """A downgrade and re-upgrade, or a hand edit: the engine is on hs-embed-1
    under a record that says bge-small. Report it; render what runs."""
    box.with_store().with_record(model=BGE)
    before = box.record.read_bytes()
    evidence = ev(live=env("local", HS), on_disk=env("local", HS))

    view = er.ensure_embedder_record(DEFAULT, evidence=evidence, **box.kw)

    assert view.record.embedder == DEFAULT
    assert box.record.read_bytes() == before
    assert view.conflict == {
        "live": {"provider": "local", "model": HS},
        "on_disk": {"provider": "local", "model": HS},
    }
    # Rendering the record would swap the running engine's embedder.
    assert view.render == Embedder("local", HS)
    status = er.embedder_status(DEFAULT, evidence=evidence, **box.kw)
    assert status["conflict"] == view.conflict
    assert status["effective"] == {"provider": "local", "model": HS, "source": "process"}
    notice = er.embedder_notice(status) or ""
    assert f"record says {BGE}" in notice and f"running engine is on {HS}" in notice


def test_a_record_the_unit_files_disagree_with_while_the_engine_is_down(box: Box) -> None:
    """The render keeps the record: nothing is running that it could swap."""
    box.with_store().with_record(model=BGE)
    evidence = ev(on_disk=env(model=HS), source="unit+file")

    view = er.resolve_embedder(DEFAULT, evidence=evidence, **box.kw)

    assert view.conflict == {"on_disk": {"provider": "local", "model": HS}}
    assert view.render == DEFAULT
    notice = er.embedder_notice(er.embedder_status(DEFAULT, evidence=evidence, **box.kw)) or ""
    assert f"would start it on {HS}" in notice


def test_a_record_the_evidence_agrees_with_has_no_conflict(box: Box) -> None:
    box.with_store().with_record(model=HS)
    evidence = ev(live=env("local", HS), on_disk=env("local", HS))
    view = er.resolve_embedder(CONFIGURED, evidence=evidence, **box.kw)
    assert view.conflict is None and view.render == CONFIGURED


def test_with_no_evidence_the_note_taken_with_the_record_stands(box: Box) -> None:
    note = {"on_disk": {"provider": "local", "model": HS}}
    box.with_store().with_record(model=BGE, conflict=note)
    assert er.resolve_embedder(DEFAULT, evidence=NOTHING, **box.kw).conflict == note
    # Evidence that now agrees clears it.
    assert er.resolve_embedder(DEFAULT, evidence=ev(live=env()), **box.kw).conflict is None


# ── recorded against configured ──────────────────────────────────────────────


def test_status_is_clean_when_the_store_was_built_with_the_configured_embedder(box: Box) -> None:
    box.with_store()
    er.ensure_embedder_record(DEFAULT, evidence=BASE_UNIT, **box.kw)

    status = er.embedder_status(DEFAULT, evidence=BASE_UNIT, **box.kw)

    assert status["recorded"] == status["configured"] == BGE
    assert status["recorded_provider"] == status["configured_provider"] == "local"
    assert status["mixed"] is False and status["needs_switch"] is False
    assert status["source"] == "engine-default" and status["recorded_at"]
    assert status["conflict"] is None and status["record_error"] is None
    assert status["store_unavailable"] is None
    assert er.embedder_notice(status) is None


def test_status_reports_a_config_that_differs_and_changes_nothing(box: Box) -> None:
    box.with_store()
    er.ensure_embedder_record(CONFIGURED, evidence=BASE_UNIT, **box.kw)
    before = box.record.read_bytes()

    status = er.embedder_status(CONFIGURED, evidence=BASE_UNIT, **box.kw)

    assert (status["recorded"], status["configured"]) == (BGE, HS)
    assert status["mixed"] is False and status["needs_switch"] is True
    notice = er.embedder_notice(status)
    assert notice is not None
    assert BGE in notice and HS in notice
    # Names the command that moves the store to the configured embedder.
    assert f"`hal0 memory embedder switch {HS}` (a dry run; add --apply" in notice
    assert box.record.read_bytes() == before


def test_status_reports_a_provider_that_differs(box: Box) -> None:
    """Same model name, another provider: still a different embedder."""
    box.with_store()
    er.ensure_embedder_record(DEFAULT, evidence=BASE_UNIT, **box.kw)

    status = er.embedder_status(Embedder("openai", BGE), evidence=BASE_UNIT, **box.kw)

    assert (status["recorded_provider"], status["configured_provider"]) == ("local", "openai")
    assert status["recorded"] == status["configured"] == BGE
    assert status["needs_switch"] is True
    notice = er.embedder_notice(status) or ""
    assert "the openai provider" in notice and "no effect" in notice


def test_status_reports_a_mixed_store_even_when_the_embedders_match(box: Box) -> None:
    box.with_store()
    evidence = ev(live=env("local", HS), on_disk=env("local", HS))

    status = er.embedder_status(CONFIGURED, evidence=evidence, **box.kw)

    assert (status["recorded"], status["configured"]) == (HS, HS)
    assert status["mixed"] is True and status["needs_switch"] is True
    assert "two embedders" in (er.embedder_notice(status) or "")


def test_status_does_not_write_the_record(box: Box) -> None:
    box.with_store()
    status = er.embedder_status(CONFIGURED, evidence=BASE_UNIT, **box.kw)
    assert status["recorded"] == BGE and status["recorded_at"] is None
    assert not box.record.exists()
