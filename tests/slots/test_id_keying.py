"""SlotManager + SlotIdentityStore/PortAuthority wiring (rework §11.1/§11.2).

Exercises the *injected* stores path: a bare ``SlotManager()`` keeps the legacy
name-keyed/TOML-port behaviour (covered by the other slots tests), while an
injected identity store + port authority activate opaque ids, single-authority
port allocation, the boot fold, and rename-as-relabel — all without podman/
systemd (the manager never spawns a container in these paths).

``asyncio_mode = "auto"`` (pyproject) auto-collects the plain ``async def``
tests, so no per-test marker is needed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hal0.ports.authority import PortAuthority
from hal0.slots.identity import SlotIdentityStore
from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotConfigError, SlotNotFound, SlotState


@pytest.fixture
def hal0_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))
    return tmp_path


def _manager(tmp_path: Path) -> SlotManager:
    db = tmp_path / "hal0.db"
    identity = SlotIdentityStore(db_path=db)
    authority = PortAuthority(pool=(8081, 8200), db_path=db)
    return SlotManager(identity_store=identity, port_authority=authority)


async def _create(sm: SlotManager, name: str, **over):
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


async def test_create_assigns_id_and_acquires_port(hal0_home: Path) -> None:
    sm = _manager(hal0_home)
    snap = await _create(sm, "chat", port=8081)
    assert snap.slot_id is not None and snap.slot_id >= 1
    # id surfaces additively on the API dict.
    assert snap.as_dict()["id"] == snap.slot_id
    # the port is now owned by the authority, bound to this slot's id.
    held = sm._port_authority.held_by(snap.slot_id)
    assert held == snap.port


async def test_second_slot_never_double_claims_same_port(hal0_home: Path) -> None:
    sm = _manager(hal0_home)
    a = await _create(sm, "chat", port=8081)
    # both request 8081; the authority must hand the second a different port.
    b = await _create(sm, "agent", port=8081)
    assert a.port == 8081
    assert b.port != a.port
    assert sm._port_authority.held_by(b.slot_id) == b.port


async def test_delete_releases_port_and_id(hal0_home: Path) -> None:
    sm = _manager(hal0_home)
    snap = await _create(sm, "chat", port=8081)
    slot_id = snap.slot_id
    await sm.delete("chat", force=True)
    # identity row gone; port claim released (re-grantable).
    assert sm._identity.get_by_name("chat") is None
    assert sm._port_authority.held_by(slot_id) is None
    assert sm._port_authority.is_free(8081, include_listeners=False)


async def test_rename_is_pure_relabel_preserving_id(hal0_home: Path) -> None:
    sm = _manager(hal0_home)
    snap = await _create(sm, "chat", port=8081)
    original_id = snap.slot_id
    renamed = await sm.rename("chat", "chat-primary")
    assert renamed.name == "chat-primary"
    assert renamed.slot_id == original_id  # id survives the relabel
    # old name no longer resolves; new one does, same id.
    assert sm._identity.get_by_name("chat") is None
    row = sm._identity.get_by_name("chat-primary")
    assert row is not None and row.id == original_id
    # on-disk TOML moved under the new name.
    assert (await sm.get_config("chat-primary"))["name"] == "chat-primary"


async def test_rename_rejects_running_slot(hal0_home: Path) -> None:
    sm = _manager(hal0_home)
    await _create(sm, "chat", port=8081)
    # force a non-OFFLINE state without spawning a container.
    await sm._transition("chat", SlotState.READY, model_id="some-model", port=8081, force=True)
    with pytest.raises(SlotConfigError):
        await sm.rename("chat", "chat-primary")


async def test_rename_rejects_name_collision(hal0_home: Path) -> None:
    sm = _manager(hal0_home)
    await _create(sm, "chat", port=8081)
    await _create(sm, "agent", port=8082)
    with pytest.raises(SlotConfigError):
        await sm.rename("chat", "agent")


async def test_slot_id_to_name_roundtrip(hal0_home: Path) -> None:
    sm = _manager(hal0_home)
    snap = await _create(sm, "chat", port=8081)
    assert sm.slot_id_to_name(snap.slot_id) == "chat"
    with pytest.raises(SlotNotFound):
        sm.slot_id_to_name(999999)


async def test_fold_identity_populates_rows_and_claims(hal0_home: Path) -> None:
    # Create slots via a *bare* manager (no stores) so nothing is folded yet,
    # then fold with a wired manager sharing the same HAL0_HOME.
    bare = SlotManager()
    await _create(bare, "chat", port=8081)
    await _create(bare, "agent", port=8082)

    sm = _manager(hal0_home)
    assert sm._identity.get_by_name("chat") is None  # not folded yet
    folded = await sm.fold_identity()
    assert folded == 2
    chat = sm._identity.get_by_name("chat")
    agent = sm._identity.get_by_name("agent")
    assert chat is not None and agent is not None
    assert sm._port_authority.held_by(chat.id) == 8081
    assert sm._port_authority.held_by(agent.id) == 8082
    # idempotent: a second fold changes nothing.
    assert await sm.fold_identity() == 2


async def test_bare_manager_unchanged(hal0_home: Path) -> None:
    """No injected stores → no id, no authority (legacy behaviour intact)."""
    sm = SlotManager()
    snap = await _create(sm, "chat", port=8081)
    assert snap.slot_id is None
    assert "id" not in snap.as_dict()
    assert snap.port == 8081
