"""Golden path #14 — API restart without stopping running slots.

REWORK.md §Golden-path verification, scenario 14. Modelled at the interface:
an ``hal0-api`` restart is a fresh ``create_app()`` + fresh SlotManager over
the SAME persisted state (the isolated ``HAL0_HOME``: per-slot ``state.json``
plus the SQLite identity / port-claim stores). The slot's container keeps
running across the restart — expressed here by a ``fake_container`` whose
``active`` set survives both app instances.

Contract pinned (durable across SLOT increment B): on restart the manager
RECONCILES a still-running slot back to a running state from persisted truth
+ the live "is the unit active?" probe — it does NOT issue any stop/start
(load/unload) dispatch to do so. A restart must never bounce healthy slots.

Intent-boundary assertion: the "container is still running" fact is the fake
``is_active`` probe; the real half — that the slot systemd units genuinely
outlive the API process — is deploy-only and covered by the halo143
acceptance runbook (api-restart step). What is asserted at the interface is
the reconcile decision: zero new load/unload dispatches across the restart.
"""

from __future__ import annotations

from .conftest import FakeContainerProvider, make_create_body


def test_restart_reconciles_running_slot_without_bouncing_it(
    fake_container: FakeContainerProvider,
    client_factory,
) -> None:
    # --- first boot: create + load a slot to a running state ---
    with client_factory() as client:
        client.post("/api/slots", json=make_create_body("gp14")).raise_for_status()
        loaded = client.post("/api/slots/gp14/load")
        assert loaded.status_code == 200, loaded.text
        assert loaded.json()["state"] == "ready"
        slot_id = loaded.json()["id"]
        assert "gp14" in fake_container.active

    loads_at_restart = len(fake_container.load_calls)
    unloads_at_restart = len(fake_container.unload_calls)
    # The container survives the API process going down.
    assert "gp14" in fake_container.active

    # --- restart: a fresh app over the same HAL0_HOME + still-live container ---
    with client_factory() as client:
        snap = client.get("/api/slots/gp14")
        assert snap.status_code == 200, snap.text
        # Reconciled back to a dispatchable running state from persisted
        # truth + the live is_active probe.
        assert snap.json()["state"] in {"ready", "serving", "idle"}
        # Stable identity is preserved across the restart.
        assert snap.json()["id"] == slot_id

        # The decisive assertion: reconciliation issued NO stop/start. A
        # restart of a healthy box must not bounce running slots.
        assert len(fake_container.load_calls) == loads_at_restart
        assert len(fake_container.unload_calls) == unloads_at_restart


def test_restart_leaves_stopped_slot_stopped_without_starting_it(
    fake_container: FakeContainerProvider,
    client_factory,
) -> None:
    # A slot that was never loaded (no live container) must stay OFFLINE
    # across a restart — reconciliation must not spuriously START it.
    with client_factory() as client:
        client.post("/api/slots", json=make_create_body("gp14-cold")).raise_for_status()
        assert client.get("/api/slots/gp14-cold").json()["state"] == "offline"

    loads_at_restart = len(fake_container.load_calls)
    assert "gp14-cold" not in fake_container.active

    with client_factory() as client:
        snap = client.get("/api/slots/gp14-cold")
        assert snap.status_code == 200, snap.text
        assert snap.json()["state"] == "offline"
        # No start dispatched for a cold slot on restart.
        assert len(fake_container.load_calls) == loads_at_restart
