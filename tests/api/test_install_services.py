"""GET /api/install/services + repair — FirstRun v2 services step (design D5)."""

from __future__ import annotations


def test_services_reports_units(isolated_client, monkeypatch):
    import hal0.api.routes.installer as inst

    monkeypatch.setattr(inst, "_unit_active", lambda u: u == "hal0-openwebui.service")
    r = isolated_client.get("/api/install/services")
    assert r.status_code == 200, r.text
    body = r.json()
    names = {s["unit"]: s for s in body["services"]}
    assert names["hal0-openwebui.service"]["active"] is True
    assert "hermes" in names or any("hermes" in k for k in names)


def test_repair_unknown_unit_400(isolated_client):
    r = isolated_client.post("/api/install/services/bogus/repair")
    assert r.status_code == 400


def test_repair_known_unit_restarts(isolated_client, monkeypatch):
    import hal0.api.routes.installer as inst

    calls = {}

    def _fake_run(cmd, **kw):
        calls["cmd"] = cmd

        class _R:
            returncode = 0
            stdout = "active"

        return _R()

    monkeypatch.setattr(inst.subprocess, "run", _fake_run)
    monkeypatch.setattr(inst, "_unit_active", lambda u: True)
    r = isolated_client.post("/api/install/services/hal0-openwebui.service/repair")
    assert r.status_code == 200, r.text
    assert r.json()["active"] is True
    assert calls["cmd"][:2] == ["systemctl", "restart"]
