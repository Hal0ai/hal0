"""#732: upstream-registry restart drop — reconciliation + idempotent load.

Per-slot ``kind="remote"`` upstreams live only in the in-memory
``UpstreamRegistry`` and die with the api process, while the podman
containers (and their loaded models) survive a restart. Pre-fix, every
``systemctl restart hal0-api`` left "ready" slots unroutable
(``dispatch.no_route``) until an operator ran an unload+load sweep.

Restart simulation: a second SlotManager + fresh registry over the same
HAL0 home — state.json and the (fake) container survive, the registry
starts empty, exactly like a new api process.
"""

from __future__ import annotations

from pathlib import Path

from hal0.slots.manager import SlotManager
from hal0.upstreams.registry import UpstreamRegistry

from .conftest import FakeContainerProvider


def _write_trio_shadow(slot_root: Path) -> None:
    (slot_root / "embed.toml").write_text(
        "\n".join(
            [
                'name = "embed"',
                "port = 8082",
                'device = "npu"',
                'type = "embedding"',
                'backend = "flm"',
                "enabled = true",
                "[model]",
                'default = "embed-gemma:300m"',
                "",
            ]
        ),
        encoding="utf-8",
    )


class TestReconcileContainerUpstreams:
    async def test_restores_upstream_for_running_container(
        self, slot_root: Path, container_stub: FakeContainerProvider
    ) -> None:
        reg1 = UpstreamRegistry()
        await SlotManager(upstreams_registry=reg1).load("chat")
        assert reg1.get("chat") is not None  # sanity: load registers

        reg2 = UpstreamRegistry()
        restored = await SlotManager(upstreams_registry=reg2).reconcile_container_upstreams()

        assert restored == ["chat"]
        up = reg2.get("chat")
        assert up is not None
        assert up.kind == "remote"
        assert up.slot_name == "chat"
        assert ":8081" in up.url

    async def test_skips_container_dead_while_api_down(
        self, slot_root: Path, container_stub: FakeContainerProvider
    ) -> None:
        await SlotManager(upstreams_registry=UpstreamRegistry()).load("chat")
        # Unit stopped out-of-band while the api was down — state.json
        # still says ready, but a dead upstream must not be registered.
        container_stub.active.discard("chat")

        reg2 = UpstreamRegistry()
        restored = await SlotManager(upstreams_registry=reg2).reconcile_container_upstreams()

        assert restored == []
        assert reg2.get("chat") is None

    async def test_skips_never_loaded_slot(
        self, slot_root: Path, container_stub: FakeContainerProvider
    ) -> None:
        reg = UpstreamRegistry()
        restored = await SlotManager(upstreams_registry=reg).reconcile_container_upstreams()
        assert restored == []
        assert reg.get("chat") is None

    async def test_skips_trio_shadow(
        self, slot_root: Path, container_stub: FakeContainerProvider
    ) -> None:
        _write_trio_shadow(slot_root)
        sm1 = SlotManager(upstreams_registry=UpstreamRegistry())
        await sm1.load("chat")
        await sm1.load("embed")  # shadow: marked READY, no container of its own

        reg2 = UpstreamRegistry()
        restored = await SlotManager(upstreams_registry=reg2).reconcile_container_upstreams()

        assert restored == ["chat"]
        assert reg2.get("embed") is None


class TestReconcileAdoptsOfflineButActive:
    """Startup reconcile must adopt a running container whose state.json is
    stale-OFFLINE — otherwise the slot stays unrouted AND the dashboard
    reports it "offline" over a live, serving container (the slot-status
    coherence bug). Covers a unit started out-of-band, or a state.json that
    never recorded READY before the api restarted.
    """

    async def test_adopts_offline_but_active_slot(
        self, slot_root: Path, container_stub: FakeContainerProvider
    ) -> None:
        # Container is running but no transition ever marked it READY, so
        # _current_state("chat") is OFFLINE (no state.json).
        container_stub.active.add("chat")

        reg = UpstreamRegistry()
        sm = SlotManager(upstreams_registry=reg)
        assert sm._current_state("chat").value == "offline"  # precondition

        restored = await sm.reconcile_container_upstreams()

        # Active container is adopted + routed, not skipped for reading OFFLINE.
        assert restored == ["chat"]
        up = reg.get("chat")
        assert up is not None
        assert up.kind == "remote"
        assert ":8081" in up.url
        # FSM state reconciled to READY by the reconcile pass itself, so
        # /api/status stops emitting "offline" over the running container.
        assert sm._current_state("chat").value == "ready"

    async def test_still_skips_offline_and_inactive(
        self, slot_root: Path, container_stub: FakeContainerProvider
    ) -> None:
        # OFFLINE state.json + dead unit → nothing to adopt, no route.
        reg = UpstreamRegistry()
        sm = SlotManager(upstreams_registry=reg)
        restored = await sm.reconcile_container_upstreams()
        assert restored == []
        assert reg.get("chat") is None
        assert sm._current_state("chat").value == "offline"


class TestIdempotentLoadReregisters:
    async def test_load_on_ready_slot_restores_upstream(
        self, slot_root: Path, container_stub: FakeContainerProvider
    ) -> None:
        await SlotManager(upstreams_registry=UpstreamRegistry()).load("chat")

        reg2 = UpstreamRegistry()
        sm2 = SlotManager(upstreams_registry=reg2)
        calls_before = len(container_stub.load_calls)
        snap = await sm2.load("chat")

        assert snap.state.value == "ready"
        # Still no extra spawn — the short-circuit stays a short-circuit…
        assert len(container_stub.load_calls) == calls_before
        # …but the route comes back.
        up = reg2.get("chat")
        assert up is not None
        assert up.kind == "remote"
        assert ":8081" in up.url

    async def test_load_on_ready_trio_shadow_does_not_register(
        self, slot_root: Path, container_stub: FakeContainerProvider
    ) -> None:
        _write_trio_shadow(slot_root)
        sm1 = SlotManager(upstreams_registry=UpstreamRegistry())
        await sm1.load("embed")

        reg2 = UpstreamRegistry()
        sm2 = SlotManager(upstreams_registry=reg2)
        await sm2.load("embed")
        assert reg2.get("embed") is None
