"""Deep intent interface (REWORK.md §E) — inspect / apply / delete / subscribe.

Drives :class:`hal0.slots.interface.SlotInterface` through the SAME in-memory
``container_stub`` double the rest of the slot suite uses (tests/slots/conftest.py),
plus a real SQLite ``SlotIdentityStore`` + ``PortAuthority`` under the isolated
``tmp_hal0_home`` so the interface's id-keying and port-claim reads exercise the
production stores rather than fakes. Mirrors the golden-path philosophy: the
mechanism boundary (podman/systemd) is faked; identity + ports are real.
"""

from __future__ import annotations

import asyncio

import pytest

from hal0.config.schema import _SLOT_PORT_MAX, _SLOT_PORT_MIN
from hal0.ports.authority import PortAuthority
from hal0.slots.identity import SlotIdentityStore
from hal0.slots.interface import DesiredSlotState, SlotInterface, SlotSnapshot
from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotNotFound, SlotState


@pytest.fixture(autouse=True)
def _no_spawn_context_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """swap() fires a detached hal0-agent render-context; stub it out."""
    import hal0.agents.hermes_refresh as _hr

    monkeypatch.setattr(_hr, "spawn_context_refresh", lambda *a, **k: None)


async def _make_env(slot_root, **manager_kw) -> tuple[SlotManager, int]:
    """A manager wired with real identity + port stores and a folded ``chat`` id."""
    sm = SlotManager(
        identity_store=SlotIdentityStore(),
        port_authority=PortAuthority(pool=(_SLOT_PORT_MIN, _SLOT_PORT_MAX)),
        **manager_kw,
    )
    await sm.fold_identity()  # assigns chat's slot id + seeds its 8081 port claim
    row = sm._identity.get_by_name("chat")
    assert row is not None
    return sm, int(row.id)


# ── inspect ──────────────────────────────────────────────────────────────────


async def test_inspect_assembles_one_snapshot(slot_root, container_stub) -> None:
    sm, sid = await _make_env(slot_root)
    await sm.interface.apply(sid, DesiredSlotState(loaded=True))

    snap = await sm.interface.inspect(sid)

    assert isinstance(snap, SlotSnapshot)
    assert snap.slot_id == sid
    assert snap.name == "chat"
    assert snap.state == SlotState.READY
    # config assembled from the on-disk TOML
    assert snap.config.get("model", {}).get("default") == "qwen3-4b-q4_k_m"
    # authoritative port claim (seeded by fold_identity from the TOML port)
    assert snap.port_claim == 8081
    assert snap.port == 8081
    # model resolution stamped even on a registry miss (FLM tag + key)
    assert snap.model_id == "qwen3-4b-q4_k_m"
    assert snap.model_resolution.get("_model_key") == "qwen3-4b-q4_k_m"
    assert snap.last_failure is None


async def test_inspect_bad_id_raises(slot_root, container_stub) -> None:
    sm, _sid = await _make_env(slot_root)
    with pytest.raises(SlotNotFound):
        await sm.interface.inspect(999_999)


async def test_inspect_surfaces_last_failure_on_error(slot_root, container_stub) -> None:
    class Boom(RuntimeError):
        pass

    container_stub.fail_load = Boom("podman exploded")
    sm, sid = await _make_env(slot_root)

    with pytest.raises(Boom):
        await sm.interface.apply(sid, DesiredSlotState(loaded=True))

    snap = await sm.interface.inspect(sid)
    assert snap.state == SlotState.ERROR
    assert snap.last_failure is not None
    assert "podman exploded" in snap.last_failure


# ── apply ────────────────────────────────────────────────────────────────────


async def test_apply_loads_toward_target(slot_root, container_stub) -> None:
    sm, sid = await _make_env(slot_root)
    snap = await sm.interface.apply(sid, DesiredSlotState(loaded=True))
    assert snap.state == SlotState.READY
    assert container_stub.active == {"chat"}
    assert len(container_stub.load_calls) == 1


async def test_apply_is_idempotent(slot_root, container_stub) -> None:
    sm, sid = await _make_env(slot_root)
    await sm.interface.apply(sid, DesiredSlotState(loaded=True))
    loads_after_first = len(container_stub.load_calls)

    # Re-applying the state the slot is already in issues zero transitions.
    snap = await sm.interface.apply(sid, DesiredSlotState(loaded=True))
    assert snap.state == SlotState.READY
    assert len(container_stub.load_calls) == loads_after_first
    assert container_stub.unload_calls == []


async def test_apply_unloads_toward_offline(slot_root, container_stub) -> None:
    sm, sid = await _make_env(slot_root)
    await sm.interface.apply(sid, DesiredSlotState(loaded=True))

    snap = await sm.interface.apply(sid, DesiredSlotState(loaded=False))
    assert snap.state == SlotState.OFFLINE
    assert container_stub.active == set()
    assert len(container_stub.unload_calls) == 1

    # Idempotent in the offline direction too.
    await sm.interface.apply(sid, DesiredSlotState(loaded=False))
    assert len(container_stub.unload_calls) == 1


async def test_apply_swaps_model_on_live_slot(slot_root, container_stub) -> None:
    sm, sid = await _make_env(slot_root)
    await sm.interface.apply(sid, DesiredSlotState(loaded=True))

    snap = await sm.interface.apply(sid, DesiredSlotState(loaded=True, model="llama-3.2-3b-q4_k_m"))
    assert snap.state == SlotState.READY
    assert snap.model_id == "llama-3.2-3b-q4_k_m"
    # The last spawn carried the new model.
    _cfg, model_info = container_stub.load_calls[-1]
    assert model_info["_model_key"] == "llama-3.2-3b-q4_k_m"


async def test_apply_materializes_config_idempotently(slot_root, container_stub) -> None:
    sm, sid = await _make_env(slot_root)

    seen: list[dict] = []
    orig_update = sm.update_config

    async def _spy(name, updates):
        seen.append(updates)
        return await orig_update(name, updates)

    sm.update_config = _spy  # type: ignore[method-assign]

    desired = DesiredSlotState(loaded=False, config={"model": {"context_size": 4096}})
    await sm.interface.apply(sid, desired)
    snap = await sm.interface.inspect(sid)
    assert snap.config.get("model", {}).get("context_size") == 4096
    assert len(seen) == 1

    # Second apply of the same config is a no-op — update_config is not called.
    await sm.interface.apply(sid, desired)
    assert len(seen) == 1


# ── delete ───────────────────────────────────────────────────────────────────


async def test_delete_composes_the_delete_path(slot_root, container_stub) -> None:
    sm, sid = await _make_env(slot_root)
    # Prove the claim + identity exist first.
    assert sm._port_authority.held_by(sid) == 8081
    assert sm._identity.get_by_name("chat") is not None

    await sm.interface.delete(sid)

    # Identity row dropped, port claim released, TOML gone → id no longer resolves.
    assert sm._identity.get_by_name("chat") is None
    assert sm._port_authority.held_by(sid) is None
    assert not sm._config_file("chat").exists()
    with pytest.raises(SlotNotFound):
        await sm.interface.inspect(sid)


async def test_delete_unloads_live_slot_first(slot_root, container_stub) -> None:
    sm, sid = await _make_env(slot_root)
    await sm.interface.apply(sid, DesiredSlotState(loaded=True))
    assert container_stub.active == {"chat"}

    await sm.interface.delete(sid)
    assert container_stub.active == set()
    assert len(container_stub.unload_calls) == 1


# ── subscribe ────────────────────────────────────────────────────────────────


async def test_subscribe_streams_transitions(slot_root, container_stub) -> None:
    sm, sid = await _make_env(slot_root)

    seen: list[str] = []

    async def consumer() -> None:
        async for rec in sm.interface.subscribe():
            seen.append(rec.state.value)
            if rec.state == SlotState.READY:
                return

    task = asyncio.create_task(consumer())
    await asyncio.sleep(0)
    await sm.interface.apply(sid, DesiredSlotState(loaded=True))
    await asyncio.wait_for(task, timeout=2.0)

    # The load walked offline → starting → warming → ready; ready must appear.
    assert "ready" in seen
    assert "starting" in seen


def test_interface_property_is_cached(slot_root) -> None:
    sm = SlotManager()
    assert sm.interface is sm.interface
    assert isinstance(sm.interface, SlotInterface)
