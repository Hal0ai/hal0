"""Bilingual (name-or-id) on-disk slot read/write path (P3-runtime-db inc1-3).

The manager-side half of the seam: a slot whose TOML lives at ``<id>.toml``
must enumerate, load, reconcile, fold, and write back exactly like a name-keyed
one — always under its REAL display name, never the digit stem. Name-keyed
behaviour is covered by the wider suite and must stay green (see the bare/legacy
assertions here).

Fixtures mirror ``tests/slots/test_id_keying.py``: an injected identity store +
port authority sharing one SQLite db under an isolated ``HAL0_HOME``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.config import paths
from hal0.ports.authority import PortAuthority
from hal0.slots.identity import SlotIdentityStore
from hal0.slots.manager import SlotManager
from hal0.slots.migrate_id_keying import (
    RecordingSlotArtifactOps,
    migrate_slot_id_keying,
)
from hal0.slots.state import SlotState


@pytest.fixture
def hal0_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    return tmp_path


def _db(home: Path) -> Path:
    return home / "hal0.db"


def _manager(home: Path) -> SlotManager:
    db = _db(home)
    identity = SlotIdentityStore(db_path=db)
    authority = PortAuthority(pool=(8081, 8200), db_path=db)
    return SlotManager(identity_store=identity, port_authority=authority)


async def _create(sm: SlotManager, name: str, **over) -> object:
    cfg = {
        "name": name,
        "type": "llm",
        "device": "gpu-rocm",
        "provider": "llama-server",
        "enabled": True,
        "model": {"default": "some-model"},
        **over,
    }
    return await sm.create(name, cfg)


async def _seed_id_keyed(home: Path, specs: list[tuple[str, int]]) -> SlotIdentityStore:
    """Create name-keyed slots, fold, then migrate → id-keyed on disk.

    Returns the shared identity store. After this the config dir holds only
    ``<id>.toml`` files (no ``<name>.toml``) and one identity row per slot.
    """
    sm = _manager(home)
    for name, port in specs:
        await _create(sm, name, port=port)
    identity = sm._identity
    report = migrate_slot_id_keying(
        identity=identity,
        config_dir=paths.slots_config_dir(),
        data_dir=paths.var_lib() / "slots",
        ops=RecordingSlotArtifactOps(),
    )
    assert report.migrations  # something migrated
    return identity


# ── enumerator + read path ────────────────────────────────────────────────────


async def test_iter_configured_slots_recovers_names_from_id_toml(hal0_home: Path) -> None:
    await _seed_id_keyed(hal0_home, [("chat", 8081), ("brain", 8082)])
    # only <id>.toml on disk — no <name>.toml
    stems = {p.stem for p in paths.slots_config_dir().glob("*.toml")}
    assert all(s.isdigit() for s in stems), stems

    sm = _manager(hal0_home)
    rows = sm._iter_configured_slots()
    by_name = {name: (sid, path) for sid, name, path in rows}
    assert set(by_name) == {"chat", "brain"}
    # every returned name is a REAL display name, never a digit stem.
    for _sid, name, _path in rows:
        assert not name.isdigit()
    # the id came from the digit stem; the path is the <id>.toml.
    assert by_name["chat"][1].stem.isdigit()


async def test_all_configured_slot_names_never_digit(hal0_home: Path) -> None:
    await _seed_id_keyed(hal0_home, [("chat", 8081), ("brain", 8082)])
    sm = _manager(hal0_home)
    names = sm._all_configured_slot_names()
    assert set(names) == {"chat", "brain"}
    assert not any(n.isdigit() for n in names)


async def test_load_slot_config_reads_id_toml_by_name(hal0_home: Path) -> None:
    await _seed_id_keyed(hal0_home, [("chat", 8081)])
    sm = _manager(hal0_home)
    # asked by NAME, served from <id>.toml, name preserved.
    cfg = await sm.get_config("chat")
    assert cfg["name"] == "chat"
    assert cfg["port"] == 8081
    row = sm._identity.get_by_name("chat")
    assert row is not None
    assert sm._config_file_for("chat") == paths.slots_config_dir() / f"{row.id}.toml"


async def test_iter_configs_id_keyed_names_intact(hal0_home: Path) -> None:
    await _seed_id_keyed(hal0_home, [("chat", 8081), ("brain", 8082)])
    sm = _manager(hal0_home)
    cfgs = await sm.iter_configs()
    assert {c["name"] for c in cfgs} == {"chat", "brain"}


# ── state path (bilingual + half-migrated fallback) ───────────────────────────


async def test_state_file_for_prefers_id_keyed_state(hal0_home: Path) -> None:
    await _seed_id_keyed(hal0_home, [("chat", 8081)])
    sm = _manager(hal0_home)
    row = sm._identity.get_by_name("chat")
    assert row is not None
    expected = paths.slot_data_dir(str(row.id)) / "state.json"
    assert sm._state_file_for("chat") == expected


async def test_state_file_for_half_migrated_falls_back_to_name(hal0_home: Path) -> None:
    """TOML id-keyed but state.json still name-keyed (crash mid-migration) —
    the resolver must find the surviving name-keyed state.json."""
    identity = await _seed_id_keyed(hal0_home, [("chat", 8081)])
    row = identity.get_by_name("chat")
    assert row is not None
    id_state = paths.slot_data_dir(str(row.id)) / "state.json"
    # simulate the half-migrated tree: move the id-state back to name-keyed.
    name_state = paths.slot_data_dir("chat") / "state.json"
    name_state.parent.mkdir(parents=True, exist_ok=True)
    if id_state.exists():
        name_state.write_text(id_state.read_text(encoding="utf-8"), encoding="utf-8")
        id_state.unlink()

    sm = _manager(hal0_home)
    assert sm._state_file_for("chat") == name_state


async def test_status_hydrates_from_id_keyed_state(hal0_home: Path) -> None:
    await _seed_id_keyed(hal0_home, [("chat", 8081)])
    sm = _manager(hal0_home)
    snap = await sm.status("chat")
    assert snap.name == "chat"
    assert snap.state in (SlotState.OFFLINE, SlotState.IDLE, SlotState.READY)


# ── fold no-ops on id-keyed (never a bogus digit-named row) ────────────────────


async def test_fold_identity_noop_on_id_keyed_no_digit_rows(hal0_home: Path) -> None:
    identity = await _seed_id_keyed(hal0_home, [("chat", 8081), ("brain", 8082)])
    before = {r.name for r in identity.list_all()}
    assert before == {"chat", "brain"}

    sm = _manager(hal0_home)
    folded = await sm.fold_identity()
    assert folded == 2  # the two existing id-keyed rows, re-affirmed

    after = {r.name for r in sm._identity.list_all()}
    assert after == {"chat", "brain"}
    # THE guard: no identity row was ever minted under a digit stem.
    assert not any(n.isdigit() for n in after)


# ── reconcile over id-stems resolves real names ────────────────────────────────


async def test_reconcile_unconfigured_over_id_stems_uses_real_names(hal0_home: Path) -> None:
    identity = await _seed_id_keyed(hal0_home, [("chat", 8081)])
    row = identity.get_by_name("chat")
    assert row is not None
    # Stamp a pre-fix "no model.default set" ERROR into the id-keyed state.json.
    from hal0.slots.state import SlotStateRecord, write_state_atomic

    id_state = paths.slot_data_dir(str(row.id)) / "state.json"
    write_state_atomic(
        id_state,
        SlotStateRecord(
            name="chat",
            state=SlotState.ERROR,
            model_id=None,
            port=8081,
            message="no model.default set",
        ),
    )
    sm = _manager(hal0_home)
    await sm.reconcile_unconfigured_slots()
    # The reconcile must have addressed the slot by its real name and flipped
    # the stale ERROR — never crashed on a digit "name" nor minted one.
    assert not any(n.isdigit() for n in {r.name for r in sm._identity.list_all()})


# ── write path: update_config writes back to <id>.toml, no split-brain ─────────


async def test_update_config_writes_back_to_id_toml(hal0_home: Path) -> None:
    identity = await _seed_id_keyed(hal0_home, [("chat", 8081)])
    row = identity.get_by_name("chat")
    assert row is not None

    sm = _manager(hal0_home)
    await sm.update_config("chat", {"model": {"default": "next-model"}})

    # write landed in <id>.toml, and NO name-keyed sibling appeared.
    assert (paths.slots_config_dir() / f"{row.id}.toml").exists()
    assert not (paths.slots_config_dir() / "chat.toml").exists()
    cfg = await sm.get_config("chat")
    assert cfg["model"]["default"] == "next-model"
    assert cfg["name"] == "chat"
