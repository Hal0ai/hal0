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


async def test_renaming_a_static_seed_tombstones_the_old_name(hal0_home: Path) -> None:
    # #1651: rename() relabelled the identity row and moved/rewrote the TOML
    # but wrote no tombstone for the vacated name. The boot-time seeding pass
    # (hal0.install.static_seeds.seed_static_slots) then sees `brain` as
    # neither known (identity now carries `steward`) nor on disk (id-keyed
    # slots carry no <name>.toml) and re-materialises a fresh seed under the
    # OLD name — the operator ends up with both `steward` and a duplicate
    # `brain` after a plain restart. Mirror delete()'s tombstone write.
    sm = _manager(hal0_home)
    await _create(sm, "brain", port=8085)
    await sm.rename("brain", "steward")

    from hal0.install.static_seeds import read_seed_tombstones, seed_static_slots

    assert "brain" in read_seed_tombstones()
    # The seeding pass must honour that tombstone, not resurrect a duplicate.
    seeded = seed_static_slots(existing_names=sm.identity_names())
    assert "brain" not in seeded
    assert sm._identity.get_by_name("brain") is None
    assert sm._identity.get_by_name("steward") is not None


async def test_renaming_into_a_tombstoned_seed_name_clears_it(hal0_home: Path) -> None:
    # Mirror hazard called out in #1651: renaming INTO a previously-deleted
    # seed name must clear that name's tombstone the same way create() does,
    # or the stale tombstone silently blocks that name from ever being
    # seeded again even though a live slot now legitimately owns it.
    sm = _manager(hal0_home)
    await _create(sm, "rerank", port=8083)
    await sm.delete("rerank", force=True)

    from hal0.install.static_seeds import read_seed_tombstones

    assert "rerank" in read_seed_tombstones()

    await _create(sm, "utility", port=8091)
    await sm.rename("utility", "rerank")
    assert "rerank" not in read_seed_tombstones()


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


# ── internal id-keying of the in-memory caches (rework §11.1, increment B) ────


async def test_inmemory_dicts_keyed_by_durable_slot_id(hal0_home: Path) -> None:
    """With an identity store wired, every per-slot cache is keyed by the
    durable ``slot_id`` — not the name."""
    sm = _manager(hal0_home)
    snap = await _create(sm, "chat", port=8081)
    await sm._transition("chat", SlotState.READY, model_id="some-model", port=8081, force=True)
    sm.bump_last_used("chat")
    assert snap.slot_id in sm._states
    assert snap.slot_id in sm._last_used
    # the name is NEVER a key of the internal caches.
    assert "chat" not in sm._states
    assert "chat" not in sm._last_used
    # the chokepoint round-trips.
    assert sm._key("chat") == snap.slot_id
    assert sm._name_for_key(snap.slot_id) == "chat"


def test_bare_manager_key_is_negative_surrogate_bijection() -> None:
    """No identity store → ``_key`` mints stable negative surrogates that can
    never alias a real (>= 1) AUTOINCREMENT id, and the bijection holds."""
    sm = SlotManager()
    k_chat = sm._key("chat")
    k_agent = sm._key("agent")
    assert k_chat < 0 and k_agent < 0
    assert k_chat != k_agent
    # stable + bijective.
    assert sm._key("chat") == k_chat
    assert sm._name_for_key(k_chat) == "chat"
    assert sm._name_for_key(k_agent) == "agent"
    # aliases collapse onto the canonical name's handle.
    assert sm._key("agent-hermes") == sm._key("agent")


async def test_surrogate_rebinds_to_durable_id_when_row_appears(hal0_home: Path) -> None:
    """A name touched BEFORE its identity row exists (the boot ordering where
    ``reconcile_unconfigured_slots`` runs before ``fold_identity``) binds a
    surrogate; the moment the row appears every cache entry is migrated
    surrogate → durable id, so nothing is orphaned."""
    sm = _manager(hal0_home)
    # No row yet → surrogate handle.
    surrogate = sm._key("chat")
    assert surrogate < 0
    # Simulate a pre-fold cache write under the surrogate.
    await sm._transition("chat", SlotState.OFFLINE, message="pre-fold", force=True)
    assert surrogate in sm._states
    # The identity row appears (create-on-demand fold).
    row = sm._ensure_identity("chat", {"name": "chat", "type": "llm", "device": "gpu-rocm"})
    assert row is not None and row.id >= 1
    # The cache entry migrated from the surrogate to the durable id.
    assert surrogate not in sm._states
    assert row.id in sm._states
    assert sm._key("chat") == row.id
    assert sm._name_for_key(row.id) == "chat"


async def test_rename_does_not_rekey_caches(hal0_home: Path) -> None:
    """The payoff of id-keying: a rename is a pure relabel — the id-keyed
    caches keep the SAME entry under the SAME handle, only the display label
    (and the name↔handle map) moves."""
    sm = _manager(hal0_home)
    snap = await _create(sm, "chat", port=8081)
    slot_id = snap.slot_id
    sm.bump_last_used("chat")
    stamp = sm._last_used[slot_id]
    await sm.rename("chat", "chat-primary")
    # Same handle, same entry — the cache was never re-keyed.
    assert sm._last_used.get(slot_id) == stamp
    assert sm._key("chat-primary") == slot_id
    # Old label no longer maps to the handle.
    assert "chat" not in sm._name_to_key
    assert sm._name_for_key(slot_id) == "chat-primary"
