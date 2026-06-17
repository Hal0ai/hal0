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

    def _fake_run(cmd, **kw):
        calls.append(list(cmd))

        class _R:
            returncode = 0
            stdout = ""

        return _R()

    monkeypatch.setattr(inst.subprocess, "run", _fake_run)
    monkeypatch.setattr(inst.os, "geteuid", lambda: 0)
    monkeypatch.setattr(inst, "_unit_active", lambda u: False)
    monkeypatch.setattr(inst, "_container_active", lambda: True)

    r = isolated_client.post("/api/install/services/comfyui/repair")
    assert r.status_code == 200, r.text
    assert calls, "repair made no subprocess calls"
    assert calls[0] == [inst._SYSTEMCTL, "restart", inst._COMFYUI_SLOT_UNIT]


def test_comfyui_repair_uses_sudo_when_non_root(isolated_client, monkeypatch):
    import hal0.api.routes.installer as inst

    calls = []

    def _fake_run(cmd, **kw):
        calls.append(list(cmd))

        class _R:
            returncode = 0
            stdout = ""

        return _R()

    monkeypatch.setattr(inst.subprocess, "run", _fake_run)
    monkeypatch.setattr(inst.os, "geteuid", lambda: 1001)
    monkeypatch.setattr(inst, "_unit_active", lambda u: False)
    monkeypatch.setattr(inst, "_container_active", lambda: True)

    r = isolated_client.post("/api/install/services/comfyui/repair")
    assert r.status_code == 200, r.text
    assert calls[0] == ["sudo", "-n", inst._SYSTEMCTL, "restart", inst._COMFYUI_SLOT_UNIT]


def test_comfyui_repair_not_blocked_by_unknown_unit_check(isolated_client, monkeypatch):
    """Ensure comfyui repair returns 200, not 400 'unit not repairable'."""
    import hal0.api.routes.installer as inst

    calls = []

    def _fake_run(cmd, **kw):
        calls.append(list(cmd))

        class _R:
            returncode = 0
            stdout = ""

        return _R()

    monkeypatch.setattr(inst.subprocess, "run", _fake_run)
    monkeypatch.setattr(inst.os, "geteuid", lambda: 0)
    monkeypatch.setattr(inst, "_unit_active", lambda u: False)
    monkeypatch.setattr(inst, "_container_active", lambda: True)

    r = isolated_client.post("/api/install/services/comfyui/repair")
    assert r.status_code != 400, (
        "comfyui repair returned 400 — it must not be gated by the systemd allowlist"
    )
