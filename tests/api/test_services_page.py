"""Tests for the services management surface (GET/POST /api/services…).

Covers the registry-driven routes in ``hal0.api.routes.services`` plus the
``hal0.services`` package helpers:

  1. GET /api/services — 200, all 5 registry ids, honest shape with every
     probe/systemd source stubbed down.
  2. Registry invariants — comfyui only exposes restart; n8n exposes nothing.
  3. POST action — unknown service → 404; missing/disallowed action → 400.
  4. POST action — allowed verb runs through systemd.unit_action.
  5. mDNS — status shape; advertise writes hal0-addon-*.service files into
     HAL0_AVAHI_SERVICES_DIR and withdraw prunes them (installer-owned
     hal0.service is never touched).
  6. systemd helper — verb allow-list enforced at the execution boundary.
"""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api.middleware import error_codes
from hal0.api.routes.services import router as services_router
from hal0.services import mdns
from hal0.services import systemd as svc_systemd
from hal0.services.registry import SERVICES, service_by_id

_ROUTE = "hal0.api.routes.services"
_EXPECTED_IDS = {"openwebui", "comfyui", "hermes", "turnstone", "hindsight", "honcho", "n8n"}

_DOWN_STATE = {
    "active_state": "inactive",
    "sub_state": "dead",
    "unit_file_state": "disabled",
    "since": None,
}


@pytest.fixture
def svc_client() -> TestClient:
    """Minimal app with only the services management router mounted."""
    app = FastAPI()
    error_codes.install(app)
    app.include_router(services_router, prefix="/api/services")
    return TestClient(app, raise_server_exceptions=False)


def _stub_all_down() -> list:
    """Patchers pinning every probe/systemd source to a neutral down state."""
    return [
        patch(
            f"{_ROUTE}._probe_comfyui",
            new_callable=AsyncMock,
            return_value=(False, "unreachable", None, None),
        ),
        patch(
            f"{_ROUTE}._probe_hermes",
            new_callable=AsyncMock,
            return_value=(False, "systemd unit inactive or absent"),
        ),
        patch(
            f"{_ROUTE}._probe_openwebui",
            new_callable=AsyncMock,
            return_value=(False, "unreachable (ConnectError)"),
        ),
        # honcho (and n8n, when its probe env is set) go through the generic
        # loopback HTTP probe. Left unstubbed, this hits the REAL
        # 127.0.0.1:8000/health on any host that happens to be running a
        # honcho stack, silently flipping "up" to True and leaking host
        # state into an "everything stubbed down" assertion.
        patch(
            f"{_ROUTE}._probe_http_env",
            new_callable=AsyncMock,
            return_value=(False, "unreachable"),
        ),
        patch(
            f"{_ROUTE}.svc_systemd.unit_state",
            new_callable=AsyncMock,
            return_value=dict(_DOWN_STATE),
        ),
        patch(
            f"{_ROUTE}.svc_systemd.unit_is_active",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            f"{_ROUTE}.mdns.status",
            new_callable=AsyncMock,
            return_value={
                "available": False,
                "hostname": "testhost.local",
                "base_advertised": False,
                "advertised": [],
            },
        ),
    ]


def _services_by_id(body: dict) -> dict:
    return {s["id"]: s for s in body["services"]}


# ── 1. list shape ─────────────────────────────────────────────────────────────


def test_list_services_200_all_ids(svc_client: TestClient) -> None:
    with contextlib.ExitStack() as stack:
        for p in _stub_all_down():
            stack.enter_context(p)
        r = svc_client.get("/api/services")

    assert r.status_code == 200, r.text
    body = r.json()
    by_id = _services_by_id(body)
    assert set(by_id) == _EXPECTED_IDS
    assert body["mdns"]["hostname"] == "testhost.local"

    for svc in body["services"]:
        for key in (
            "id",
            "name",
            "description",
            "managed",
            "unit",
            "unit_state",
            "up",
            "detail",
            "stat",
            "url",
            "mdns_url",
            "actions",
            "mdns_capable",
            "hints",
        ):
            assert key in svc, f"{svc['id']} missing {key}"
        assert svc["up"] is False  # everything stubbed down → honest false

    # n8n is unmanaged: no unit, no unit_state, no actions.
    assert by_id["n8n"]["managed"] is False
    assert by_id["n8n"]["unit"] is None
    assert by_id["n8n"]["actions"] == []
    # openwebui is fully managed and carries the host:port URL fallback.
    assert by_id["openwebui"]["managed"] is True
    assert by_id["openwebui"]["url"] == "http://testserver:3001"
    # loopback-only services expose no URL without a public-URL env.
    assert by_id["hindsight"]["url"] is None


# ── 2. registry invariants ────────────────────────────────────────────────────


def test_registry_comfyui_restart_only() -> None:
    comfy = service_by_id("comfyui")
    assert comfy is not None
    assert comfy.actions == ("restart",)
    assert comfy.unit == "hal0-slot@img.service"


def test_registry_n8n_readonly() -> None:
    n8n = service_by_id("n8n")
    assert n8n is not None
    assert n8n.actions == ()
    assert n8n.unit is None


def test_registry_units_are_valid_names() -> None:
    for sdef in SERVICES:
        if sdef.unit is not None:
            assert svc_systemd.valid_unit(sdef.unit), sdef.unit


# ── 3. action validation ──────────────────────────────────────────────────────


def test_action_unknown_service_404(svc_client: TestClient) -> None:
    r = svc_client.post("/api/services/nope/action", json={"action": "restart"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "services.unknown"


def test_action_missing_action_400(svc_client: TestClient) -> None:
    r = svc_client.post("/api/services/openwebui/action", json={})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "services.action_required"


def test_action_disallowed_verb_400(svc_client: TestClient) -> None:
    # ComfyUI: stop is arbiter-owned, registry only allows restart.
    r = svc_client.post("/api/services/comfyui/action", json={"action": "stop"})
    assert r.status_code == 400
    body = r.json()["error"]
    assert body["code"] == "services.action_not_allowed"
    assert body["details"]["allowed"] == ["restart"]


def test_action_unmanaged_service_400(svc_client: TestClient) -> None:
    r = svc_client.post("/api/services/n8n/action", json={"action": "start"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "services.action_not_allowed"


# ── 4. action happy path ──────────────────────────────────────────────────────


def test_action_restart_runs_unit_action(svc_client: TestClient) -> None:
    with (
        patch(
            f"{_ROUTE}.svc_systemd.unit_action",
            new_callable=AsyncMock,
            return_value={"ok": True, "message": "restart hal0-openwebui.service: ok"},
        ) as action_mock,
        patch(
            f"{_ROUTE}.svc_systemd.unit_is_active",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        r = svc_client.post("/api/services/openwebui/action", json={"action": "restart"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {
        "id": "openwebui",
        "unit": "hal0-openwebui.service",
        "action": "restart",
        "ok": True,
        "active": True,
        "message": "restart hal0-openwebui.service: ok",
    }
    action_mock.assert_awaited_once_with("hal0-openwebui.service", "restart")


def test_action_failure_reported_honestly(svc_client: TestClient) -> None:
    with (
        patch(
            f"{_ROUTE}.svc_systemd.unit_action",
            new_callable=AsyncMock,
            return_value={"ok": False, "message": "start hindsight-api.service failed: boom"},
        ),
        patch(
            f"{_ROUTE}.svc_systemd.unit_is_active",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
        r = svc_client.post("/api/services/hindsight/action", json={"action": "start"})

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["active"] is False


# ── 5. mDNS ───────────────────────────────────────────────────────────────────


def test_mdns_advertise_and_withdraw(svc_client: TestClient, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HAL0_AVAHI_SERVICES_DIR", str(tmp_path))
    # Installer-owned base file must survive advertise/withdraw untouched.
    base = tmp_path / "hal0.service"
    base.write_text("<!-- installer-owned -->", encoding="utf-8")

    with patch(
        "hal0.services.mdns.systemd.unit_is_active",
        new_callable=AsyncMock,
        return_value=True,
    ):
        r = svc_client.post("/api/services/mdns", json={"advertise": True})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert sorted(body["advertised"]) == ["comfyui", "openwebui"]

        owui = (tmp_path / "hal0-addon-openwebui.service").read_text(encoding="utf-8")
        assert "<port>3001</port>" in owui
        assert "OpenWebUI on %h" in owui
        comfy = (tmp_path / "hal0-addon-comfyui.service").read_text(encoding="utf-8")
        assert "<port>8188</port>" in comfy

        status = svc_client.get("/api/services/mdns").json()
        assert status["available"] is True
        assert status["base_advertised"] is True
        assert sorted(status["advertised"]) == ["comfyui", "openwebui"]
        assert {a["id"] for a in status["advertisable"]} == {"comfyui", "openwebui"}

        r = svc_client.post("/api/services/mdns", json={"advertise": False})
        assert r.status_code == 200
        assert r.json()["advertised"] == []

    assert base.read_text(encoding="utf-8") == "<!-- installer-owned -->"
    assert list(tmp_path.glob("hal0-addon-*.service")) == []


def test_mdns_apply_requires_boolean(svc_client: TestClient) -> None:
    r = svc_client.post("/api/services/mdns", json={"advertise": "yes"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "services.advertise_required"


def test_mdns_hostname_env_override(monkeypatch) -> None:
    monkeypatch.setenv("HAL0_HOSTNAME", "mybox.local")
    assert mdns.mdns_hostname() == "mybox.local"
    monkeypatch.setenv("HAL0_HOSTNAME", "mybox")
    assert mdns.mdns_hostname() == "mybox.local"


# ── 6. systemd helper allow-list ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unit_action_rejects_unknown_verb() -> None:
    with pytest.raises(ValueError):
        await svc_systemd.unit_action("hal0-openwebui.service", "mask")


@pytest.mark.asyncio
async def test_unit_action_rejects_bad_unit_name() -> None:
    with pytest.raises(ValueError):
        await svc_systemd.unit_action("evil; rm -rf /", "restart")


@pytest.mark.asyncio
async def test_unit_state_fail_soft_without_systemd() -> None:
    with patch("hal0.services.systemd.shutil.which", return_value=None):
        state = await svc_systemd.unit_state("hal0-openwebui.service")
    assert state["active_state"] == "unknown"
    assert state["since"] is None
