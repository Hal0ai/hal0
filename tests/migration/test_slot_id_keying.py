"""One-shot M5 slot-id-keying migration (rework §3.1 / PR4).

Feeds the migrator a fixture of N name-keyed slot TOMLs + state.json files +
simulated containers/units (via a recording ops double), then asserts every
on-disk surface ends up id-keyed:

  * ``/etc/hal0/slots/<name>.toml``            → ``/etc/hal0/slots/<id>.toml``
    (an ``id`` field is inserted; the ``name`` label is preserved)
  * ``/var/lib/hal0/slots/<name>/state.json``  → ``.../<id>/state.json``
    (the ``name`` field is rewritten to the canonical label; a top-level
    ``slot_id`` field is added)
  * ``hal0-slot@<name>.service``               → ``hal0-slot@<id>.service``
  * ``hal0-slot-<name>`` (podman container)    → ``hal0-slot-<id>``

The migration is destructive but IDEMPOTENT: re-running it (including on a
half-migrated tree) converges to the same byte-identical state.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from hal0.slots.identity import SlotIdentityStore
from hal0.slots.migrate_id_keying import (
    RecordingSlotArtifactOps,
    migrate_slot_id_keying,
)

_SLOTS = {
    "chat": {"type": "llm", "device": "gpu-rocm", "port": 8081},
    "agent": {"type": "llm", "device": "gpu-rocm", "port": 8082},
    "embed": {"type": "embedding", "device": "cpu", "port": 8083},
}


@pytest.fixture
def tree(tmp_path: Path) -> tuple[Path, Path]:
    """A name-keyed on-disk slot tree: config TOMLs + state.json files."""
    config_dir = tmp_path / "etc" / "slots"
    data_dir = tmp_path / "var" / "slots"
    config_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)
    for name, spec in _SLOTS.items():
        toml = config_dir / f"{name}.toml"
        toml.write_text(
            "[slot]\n"
            f'name = "{name}"\n'
            f'type = "{spec["type"]}"\n'
            f'device = "{spec["device"]}"\n'
            f"port = {spec['port']}\n"
        )
        # Only chat + agent have a persisted state.json (embed never loaded).
        if name != "embed":
            sdir = data_dir / name
            sdir.mkdir()
            (sdir / "state.json").write_text(
                json.dumps(
                    {
                        "name": name,
                        "state": "ready",
                        "model_id": "some-model",
                        "port": spec["port"],
                        "updated_at": 1.0,
                        "message": "",
                        "extra": {"backend": "rocm"},
                    }
                )
            )
    return config_dir, data_dir


def _identity(tmp_path: Path) -> SlotIdentityStore:
    return SlotIdentityStore(db_path=tmp_path / "hal0.db")


def _snapshot(config_dir: Path, data_dir: Path) -> dict[str, str]:
    """Byte snapshot of every file under the tree (for idempotence checks)."""
    out: dict[str, str] = {}
    for base in (config_dir, data_dir):
        for p in sorted(base.rglob("*")):
            if p.is_file():
                out[str(p.relative_to(base.parent))] = p.read_text()
    return out


def test_all_surfaces_end_up_id_keyed(tmp_path: Path, tree: tuple[Path, Path]) -> None:
    config_dir, data_dir = tree
    identity = _identity(tmp_path)
    ops = RecordingSlotArtifactOps()

    report = migrate_slot_id_keying(
        identity=identity,
        config_dir=config_dir,
        data_dir=data_dir,
        ops=ops,
        seeded_names={"chat", "agent"},
    )

    assert len(report.migrations) == 3
    ids_by_name = {m.name: m.slot_id for m in report.migrations}

    # No name-keyed TOML survives; every TOML is <id>.toml with an id field.
    remaining = sorted(p.name for p in config_dir.glob("*.toml"))
    assert remaining == sorted(f"{sid}.toml" for sid in ids_by_name.values())
    for name, sid in ids_by_name.items():
        raw = tomllib.loads((config_dir / f"{sid}.toml").read_text())
        slot_tbl = raw.get("slot", raw)
        assert int(slot_tbl["id"]) == sid
        assert slot_tbl["name"] == name  # label preserved
        assert not (config_dir / f"{name}.toml").exists()

    # state.json moved under <id>/ with rewritten name + added slot_id.
    for name in ("chat", "agent"):
        sid = ids_by_name[name]
        assert not (data_dir / name).exists()
        state = json.loads((data_dir / str(sid) / "state.json").read_text())
        assert state["name"] == name
        assert int(state["slot_id"]) == sid
        assert state["extra"]["backend"] == "rocm"  # untouched fields survive
    # embed never had a state.json — none is fabricated.
    assert not (data_dir / str(ids_by_name["embed"])).exists()

    # Unit + container renames were requested for every slot, name → id.
    assert set(ops.unit_renames) == {(n, ids_by_name[n]) for n in _SLOTS}
    assert set(ops.container_renames) == {(n, ids_by_name[n]) for n in _SLOTS}

    # The identity rows exist and carry the derived taxonomy.
    chat_row = identity.get_by_name("chat")
    assert chat_row is not None and chat_row.slot_type == "llm"
    assert chat_row.is_seed is True
    embed_row = identity.get_by_name("embed")
    assert embed_row is not None and embed_row.is_seed is False


def test_migration_is_idempotent(tmp_path: Path, tree: tuple[Path, Path]) -> None:
    config_dir, data_dir = tree
    identity = _identity(tmp_path)

    first_report = migrate_slot_id_keying(
        identity=identity,
        config_dir=config_dir,
        data_dir=data_dir,
        ops=RecordingSlotArtifactOps(),
    )
    after_first = _snapshot(config_dir, data_dir)

    # Re-run twice more with a FRESH ops double each time.
    for _ in range(2):
        ops = RecordingSlotArtifactOps()
        report = migrate_slot_id_keying(
            identity=identity,
            config_dir=config_dir,
            data_dir=data_dir,
            ops=ops,
        )
        # Nothing left to migrate → all slots skipped, no unit/container churn.
        assert report.migrations == []
        assert report.skipped_ids == sorted(m.slot_id for m in first_report.migrations)
        assert ops.unit_renames == []
        assert ops.container_renames == []

    # Disk state is byte-identical to the single-run result.
    assert _snapshot(config_dir, data_dir) == after_first
    # Identity rows were not duplicated by the re-runs.
    assert len(identity.list_all()) == 3


def test_partial_state_rolls_forward(tmp_path: Path, tree: tuple[Path, Path]) -> None:
    """A crash between the TOML move and the state.json move leaves a
    half-migrated slot; the next run completes it without touching the
    already-migrated ones."""
    config_dir, data_dir = tree
    identity = _identity(tmp_path)

    # Simulate a partial migration of "chat": TOML already moved to <id>.toml,
    # but the state.json is still under the name-keyed dir.
    chat = identity.create(name="chat", slot_type="llm", device="gpu-rocm")
    (config_dir / f"{chat.id}.toml").write_text(
        f'[slot]\nname = "chat"\nid = {chat.id}\ntype = "llm"\ndevice = "gpu-rocm"\nport = 8081\n'
    )
    (config_dir / "chat.toml").unlink()
    assert (data_dir / "chat" / "state.json").exists()  # not yet moved

    ops = RecordingSlotArtifactOps()
    report = migrate_slot_id_keying(
        identity=identity,
        config_dir=config_dir,
        data_dir=data_dir,
        ops=ops,
    )

    # chat's state.json is now completed under <id>/, and the two untouched
    # slots migrate normally. chat is NOT re-created (same id reused).
    assert (data_dir / str(chat.id) / "state.json").exists()
    assert not (data_dir / "chat").exists()
    moved = json.loads((data_dir / str(chat.id) / "state.json").read_text())
    assert int(moved["slot_id"]) == chat.id
    # chat's TOML was already id-keyed, so it is reported as skipped, not
    # re-migrated; agent + embed are fresh migrations.
    assert chat.id in report.skipped_ids
    assert {m.name for m in report.migrations} == {"agent", "embed"}
    assert len(identity.list_all()) == 3  # chat reused, not duplicated
