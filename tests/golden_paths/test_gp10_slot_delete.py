"""Golden path #10 — slot delete with unit, state, and port cleanup.

REWORK.md §Golden-path verification, scenario 10. Driven through the public
route surface:

    POST   /api/slots                 create + claim a port
    POST   /api/slots/{name}/load     bring the slot to READY (fake container)
    DELETE /api/slots/{name}          delete
    GET    /api/slots/{name}          state gone (404)
    GET    /api/slots/{name}/config   config gone (404)
    GET    /api/ports                 port claim released

Contract pinned (durable across SLOT increment B): deleting a slot releases
its port claim, erases its state + config, invokes container/unit teardown,
and is idempotent — a second delete is a clean 404 no-op per the current API
contract.

Intent-boundary assertion: the real ``systemctl stop`` + ``podman rm`` +
Quadlet unit-file removal is deploy-only. Here we assert the INTENT — that
delete drove the container-provider ``unload_sync`` (the manager stops a
running slot before deleting it) — via the recorded fake dispatch. The real
half is covered by the halo143 acceptance runbook (slot-teardown step).
"""

from __future__ import annotations

from .conftest import FakeContainerProvider, make_create_body


def _has_claim(client, slot_id: int) -> bool:
    report = client.get("/api/ports").json()
    return any(c.get("slot_id") == slot_id for c in (report.get("authority_claims") or []))


def test_delete_cleans_up_unit_state_and_port(
    fake_container: FakeContainerProvider,
    client_factory,
) -> None:
    with client_factory() as client:
        created = client.post("/api/slots", json=make_create_body("gp10"))
        assert created.status_code == 201, created.text
        slot_id = created.json()["id"]

        # Load it so there is a running unit + container to tear down.
        loaded = client.post("/api/slots/gp10/load")
        assert loaded.status_code == 200, loaded.text
        assert loaded.json()["state"] == "ready"
        assert "gp10" in fake_container.active
        unloads_before = len(fake_container.unload_calls)

        deleted = client.delete("/api/slots/gp10")
        assert deleted.status_code == 200, deleted.text
        assert deleted.json() == {"name": "gp10", "deleted": True, "forced": False}

        # Intent boundary: delete stopped the running slot first — the
        # container-provider teardown (unload_sync) was dispatched. The real
        # systemctl-stop/podman-rm half is halo143-runbook territory.
        assert len(fake_container.unload_calls) == unloads_before + 1
        assert "gp10" not in fake_container.active

        # Port claim released — no authority claim for this identity remains.
        assert not _has_claim(client, slot_id)

        # State + config are gone.
        assert client.get("/api/slots/gp10").status_code == 404
        assert client.get("/api/slots/gp10/config").status_code == 404
        # And the stable-id lookup no longer resolves the deleted identity.
        assert client.get(f"/api/slots/by-id/{slot_id}").status_code == 404


def test_delete_is_idempotent(
    fake_container: FakeContainerProvider,
    client_factory,
) -> None:
    with client_factory() as client:
        client.post("/api/slots", json=make_create_body("gp10")).raise_for_status()

        first = client.delete("/api/slots/gp10")
        assert first.status_code == 200, first.text

        # Second delete is a clean 404 no-op per the current API contract.
        second = client.delete("/api/slots/gp10")
        assert second.status_code == 404, second.text
        assert second.json()["error"]["code"] == "slot.not_found"
