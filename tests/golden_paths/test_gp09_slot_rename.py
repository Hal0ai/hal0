"""Golden path #9 — slot rename without broken references.

REWORK.md §Golden-path verification, scenario 9. Driven entirely through
the public route surface:

    POST /api/slots                    create + claim a port
    POST /api/slots/{name}/rename      the increment-A rename surface
    GET  /api/slots/by-id/{id}         stable-identity lookup
    GET  /api/slots/{name}             name-keyed lookup
    GET  /api/slots/{name}/config      config resolvability
    GET  /api/ports                    the global port-claim map

The contract these assertions pin (durable across SLOT increment B, which
rewrites the name-keyed internals): a rename is a pure relabel of a STABLE
identity. The opaque ``id`` and the port claim are preserved, config/state
stay resolvable under the new name, the old name is fully freed (404 +
reusable), and no dangling reference to the old name survives.

Deploy-only remainder: the live ``hal0-slot@<name>`` systemd unit + podman
container are still name-keyed, so renaming a RUNNING unit is deploy-only —
rename is refused unless the slot is OFFLINE. The real half is covered by
the halo143 acceptance runbook (slot-rename step). Here the slot is created
but never loaded, so it is OFFLINE and the interface contract is fully
exercisable without a container runtime.
"""

from __future__ import annotations

from .conftest import FakeContainerProvider, make_create_body


def _port_claim_for(client, slot_id: int) -> dict | None:
    """Return the PortAuthority claim row owning ``slot_id`` (public view)."""
    report = client.get("/api/ports").json()
    claims = report.get("authority_claims") or []
    for claim in claims:
        if claim.get("slot_id") == slot_id:
            return claim
    return None


def test_rename_preserves_identity_port_and_config(
    fake_container: FakeContainerProvider,
    client_factory,
) -> None:
    with client_factory() as client:
        created = client.post("/api/slots", json=make_create_body("gp9-old"))
        assert created.status_code == 201, created.text
        body = created.json()
        slot_id = body["id"]
        port = body["port"]
        assert isinstance(slot_id, int)
        assert isinstance(port, int) and port > 0

        # The port claim is recorded under the identity before the rename.
        claim = _port_claim_for(client, slot_id)
        assert claim is not None, "expected an authority port claim for the new slot"
        assert claim["port"] == port

        # Rename via the increment-A surface (slot is OFFLINE → allowed).
        renamed = client.post("/api/slots/gp9-old/rename", json={"new_name": "gp9-new"})
        assert renamed.status_code == 200, renamed.text
        rbody = renamed.json()

        # Same identity, same port — a rename never re-keys either.
        assert rbody["name"] == "gp9-new"
        assert rbody["id"] == slot_id
        assert rbody["port"] == port

        # The stable-id lookup now resolves to the NEW name — no dangling ref.
        by_id = client.get(f"/api/slots/by-id/{slot_id}")
        assert by_id.status_code == 200, by_id.text
        assert by_id.json()["name"] == "gp9-new"

        # The port claim still belongs to the same identity at the same port.
        claim_after = _port_claim_for(client, slot_id)
        assert claim_after is not None, "port claim must survive a rename"
        assert claim_after["port"] == port

        # Config + state stay resolvable under the new name.
        cfg = client.get("/api/slots/gp9-new/config")
        assert cfg.status_code == 200, cfg.text
        assert cfg.json()["name"] == "gp9-new"
        state = client.get("/api/slots/gp9-new")
        assert state.status_code == 200, state.text


def test_rename_frees_old_name_for_reuse(
    fake_container: FakeContainerProvider,
    client_factory,
) -> None:
    with client_factory() as client:
        client.post("/api/slots", json=make_create_body("gp9-old")).raise_for_status()
        client.post("/api/slots/gp9-old/rename", json={"new_name": "gp9-new"}).raise_for_status()

        # The old name is fully freed: name-keyed lookups 404 cleanly...
        gone = client.get("/api/slots/gp9-old")
        assert gone.status_code == 404, gone.text
        assert gone.json()["error"]["code"] == "slot.not_found"
        assert client.get("/api/slots/gp9-old/config").status_code == 404

        # ...and the freed name can be claimed again by a brand-new slot.
        recreated = client.post("/api/slots", json=make_create_body("gp9-old"))
        assert recreated.status_code == 201, recreated.text
        # The reused name is a DISTINCT identity from the renamed original.
        assert recreated.json()["id"] != client.get("/api/slots/gp9-new").json()["id"]
