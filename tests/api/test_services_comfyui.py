"""TDD — Task 3.4: ComfyUI installer services step + repair.

Assertions:
  (a) GET /api/install/services includes a comfyui entry.
  (b) repair path for comfyui restarts the slot-managed img unit.
"""

from __future__ import annotations


def test_services_includes_comfyui(isolated_client, monkeypatch):
    import hal0.api.routes.installer as inst

    monkeypatch.setattr(inst, "_unit_active", lambda u: False)
    r = isolated_client.get("/api/install/services")
    assert r.status_code == 200, r.text
    services = r.json()["services"]
    units = [s.get("unit") or s.get("id") or "" for s in services]
    assert any("comfyui" in u for u in units), (
        f"comfyui not in services response; got units: {units}"
    )


def test_comfyui_repair_restarts_img_slot_unit(isolated_client, monkeypatch):
    import hal0.api.routes.installer as inst

    calls = []
    monkeypatch.setattr(inst, "_seam_restart", lambda unit: calls.append(unit))
    monkeypatch.setattr(inst.os, "geteuid", lambda: 0)
    monkeypatch.setattr(inst, "_unit_active", lambda u: False)
    monkeypatch.setattr(inst, "_container_active", lambda: True)

    r = isolated_client.post("/api/install/services/comfyui/repair")
    assert r.status_code == 200, r.text
    # comfyui maps to the seeded img slot unit, restarted through the seam
    # (P3-perms: bare systemctl under User=hal0 dies on polkit).
    assert calls == [inst._COMFYUI_SLOT_UNIT]


def test_comfyui_repair_not_blocked_by_unknown_unit_check(isolated_client, monkeypatch):
    """Ensure comfyui repair returns 200, not 400 'unit not repairable'."""
    import hal0.api.routes.installer as inst

    monkeypatch.setattr(inst, "_seam_restart", lambda unit: None)
    monkeypatch.setattr(inst.os, "geteuid", lambda: 0)
    monkeypatch.setattr(inst, "_unit_active", lambda u: False)
    monkeypatch.setattr(inst, "_container_active", lambda: True)

    r = isolated_client.post("/api/install/services/comfyui/repair")
    assert r.status_code != 400, (
        "comfyui repair returned 400 — it must not be gated by the systemd allowlist"
    )
