"""Regression: GET /api/ports runs the PortAuthority reconcile pass.

``PortAuthority.reconcile_listeners()`` (ports/authority.py:375) reports
pool listeners that no live authority claim covers — its docstring says
that's so orphan listeners "show up in /api/ports". But ``list_ports()``
(routes/ports.py) only ever called ``authority.claims(live_only=True)``;
the reconcile pass was dead code, never invoked from any route. This
locks in that GET /api/ports actually runs it, and that a probe failure
in the (observational, best-effort) reconcile pass degrades gracefully
instead of 500ing the route.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import hal0.ports.authority as authority_mod


def _authority(client: TestClient) -> authority_mod.PortAuthority:
    authority = client.app.state.slot_manager._port_authority
    assert authority is not None, "test app must boot a real PortAuthority"
    return authority


def test_list_ports_runs_reconcile_listeners_pass(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /api/ports must invoke reconcile_listeners() and surface its
    output — proof the reconcile pass is actually wired in, not just
    present on the class."""
    _authority(client)
    calls: list[bool] = []
    sentinel = [{"port": 40404, "owner_kind": "listener"}]

    def _fake_reconcile(self: authority_mod.PortAuthority, *, conn=None) -> list[dict]:
        calls.append(True)
        return sentinel

    monkeypatch.setattr(authority_mod.PortAuthority, "reconcile_listeners", _fake_reconcile)

    resp = client.get("/api/ports")
    assert resp.status_code == 200
    body = resp.json()
    assert calls, "reconcile_listeners() was never invoked by GET /api/ports"
    assert sentinel[0] in body["authority_claims"]


def test_list_ports_survives_reconcile_listeners_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reconcile probe error (e.g. psutil hiccup) must not 500 the route
    or wipe the claims that DID resolve — it's observational, best-effort."""
    _authority(client)

    def _boom(self: authority_mod.PortAuthority, *, conn=None) -> list[dict]:
        raise RuntimeError("psutil exploded")

    monkeypatch.setattr(authority_mod.PortAuthority, "reconcile_listeners", _boom)

    resp = client.get("/api/ports")
    assert resp.status_code == 200
    body = resp.json()
    # Original .claims() result still present — reconcile failing must not
    # collapse authority_claims to None (that would hide the whole section).
    assert body.get("authority_claims") is not None
