"""``/api/agents/{agent_id}/command-approvals`` — Hermes's approvals.mode setting.

Covers the route contract (shape, validation, apply path, errors), its auth
class, the refusal of agent-identified callers, and that the admin MCP server
never exposes it as a tool. The ADR-0024 gap (an agent's Bearer token is the
box admin key) is pinned as a strict xfail, like
``tests/api/test_agent_self_approval.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0 import service_identity
from hal0.agents import hermes_command_approvals as cmd
from hal0.agents import hermes_provision as hp
from hal0.api import auth as auth_mod
from hal0.api.agents import command_approvals as routes
from hal0.api.agents import restart as restart_seam
from hal0.api.middleware import error_codes
from hal0.errors import Hal0Error
from hal0.security.exposure import AuthClass, classify

PATH = "/api/agents/hermes/command-approvals"
ROUTE_TEMPLATE = "/api/agents/{agent_id}/command-approvals"


@pytest.fixture
def box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seed = tmp_path / "hermes.toml"
    seed.write_text('[agent]\nname = "hermes"\n')
    home = tmp_path / "home"
    home.mkdir()
    config = home / "config.yaml"
    config.write_text("approvals:\n  mode: smart\n")
    monkeypatch.setattr(hp, "INSTALL_SEED_PATH", seed)
    monkeypatch.setattr(hp, "OVERRIDES_PATH", tmp_path / "overrides.yaml")
    monkeypatch.setattr(hp, "HERMES_HOME_DEFAULT", home)
    monkeypatch.setattr(cmd, "_write_seed", lambda body: seed.write_text(body))

    def _config_set(mode: str) -> list[str]:
        config.write_text(yaml.safe_dump({"approvals": {"mode": mode}}))
        return []

    monkeypatch.setattr(cmd, "_config_set", _config_set)

    calls: dict[str, Any] = {"restarts": [], "active": True, "fail": False}

    async def _is_active(agent_id: str) -> bool | None:
        return calls["active"]

    async def _restart(agent_id: str, *, actor: str) -> dict[str, str]:
        calls["restarts"].append((agent_id, actor))
        if calls["fail"]:
            raise Hal0Error("systemctl restart failed (rc=1)", code="agent.restart_failed")
        return {"status": "restarted"}

    monkeypatch.setattr(restart_seam, "unit_is_active", _is_active)
    monkeypatch.setattr(restart_seam, "restart_agent_unit", _restart)
    return {"seed": seed, "config": config, "calls": calls}


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(routes.router, prefix="/api/agents")
    with TestClient(app) as c:
        yield c


def test_get_shape(box: dict[str, Any], client: TestClient) -> None:
    res = client.get(PATH)
    assert res.status_code == 200
    body = res.json()
    assert {"mode", "effective", "source"} <= set(body)
    assert (body["mode"], body["effective"], body["source"]) == ("smart", "smart", "default")
    assert body["allowed"] == ["smart", "manual"]


def test_put_applies_and_restarts(box: dict[str, Any], client: TestClient) -> None:
    res = client.put(PATH, json={"mode": "manual"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["mode"], body["effective"], body["source"], body["restart"]) == (
        "manual",
        "manual",
        "setting",
        "restarted",
    )
    assert box["calls"]["restarts"] == [("hermes", "hal0-dashboard")]
    assert 'command_approvals = "manual"' in box["seed"].read_text()
    assert client.get(PATH).json()["mode"] == "manual"


def test_put_reports_override(box: dict[str, Any], client: TestClient, tmp_path: Path) -> None:
    (tmp_path / "overrides.yaml").write_text("approvals:\n  mode: smart\n")
    body = client.put(PATH, json={"mode": "manual"}).json()
    assert (body["mode"], body["effective"], body["source"]) == ("manual", "smart", "overrides")
    assert body["restart"] == "skipped-overridden"
    assert box["calls"]["restarts"] == []


@pytest.mark.parametrize("payload", [{"mode": "off"}, {"mode": None}, {"mode": "Manual"}])
def test_put_rejects_invalid_mode(box: dict[str, Any], client: TestClient, payload: Any) -> None:
    res = client.put(PATH, json=payload)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "agent.command_approvals_invalid"
    assert "command_approvals" not in box["seed"].read_text()


@pytest.mark.parametrize("payload", [{}, ["manual"], "manual"])
def test_put_rejects_malformed_body(box: dict[str, Any], client: TestClient, payload: Any) -> None:
    res = client.put(PATH, json=payload)
    assert res.status_code == 400


def test_unknown_agent_is_404(box: dict[str, Any], client: TestClient) -> None:
    assert client.get("/api/agents/pi/command-approvals").status_code == 404
    assert (
        client.put("/api/agents/pi/command-approvals", json={"mode": "manual"}).status_code == 404
    )


def test_apply_failure_is_reported_and_rolled_back(box: dict[str, Any], client: TestClient) -> None:
    box["calls"]["fail"] = True
    before = box["seed"].read_text()
    res = client.put(PATH, json={"mode": "manual"})
    assert res.status_code == 500
    err = res.json()["error"]
    assert err["code"] == "agent.command_approvals_apply_failed"
    assert "restart failed" in err["message"]
    assert box["seed"].read_text() == before
    assert yaml.safe_load(box["config"].read_text())["approvals"]["mode"] == "smart"


# ── agent self-write ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("actor", ["hermes", "pi", "hal0-brain", "anything-else"])
def test_put_refuses_an_agent_identified_caller(
    box: dict[str, Any], client: TestClient, actor: str
) -> None:
    res = client.put(PATH, json={"mode": "manual"}, headers={"X-hal0-Agent": actor})
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "agent.command_approvals_agent_refused"
    assert "command_approvals" not in box["seed"].read_text()
    assert box["calls"]["restarts"] == []


@pytest.mark.parametrize("actor", ["hal0-cli", "hal0-dashboard"])
def test_put_accepts_operator_surfaces(box: dict[str, Any], client: TestClient, actor: str) -> None:
    res = client.put(PATH, json={"mode": "manual"}, headers={"X-hal0-Agent": actor})
    assert res.status_code == 200
    assert box["calls"]["restarts"] == [("hermes", actor)]


def test_admin_mcp_never_exposes_the_route() -> None:
    from hal0.api import create_app
    from hal0.mcp import admin

    create_app()  # installs the live route map
    route_ids = {f"GET:{ROUTE_TEMPLATE}", f"PUT:{ROUTE_TEMPLATE}"}
    assert route_ids <= set(admin._UNCLASSIFIED_ROUTES)  # live, and hidden
    assert not route_ids & set(admin.TOOL_NAME_ALIASES)
    assert "hermes_command_approvals" in admin.EXCLUDED_TOOLS
    assert not [
        t for t in admin.TOOL_DESCRIPTIONS if "approvals_mode" in t or "command_approv" in t
    ]


# ── auth ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def keyed_box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("HAL0_AGENT_SECRET_PATH", str(tmp_path / "secret.bin"))
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "hal0_home"))
    monkeypatch.setenv("HAL0_ADMIN_KEY", "box-admin-key")
    monkeypatch.setenv("HAL0_CLIENT_KEY", "box-client-key")
    auth_mod._require_auth_cache = None
    yield


def _scope(method: str, key: str | None) -> dict[str, object]:
    headers = [(b"authorization", f"Bearer {key}".encode())] if key else []
    return {"type": "http", "method": method, "path": PATH, "headers": headers, "query_string": b""}


@pytest.mark.parametrize("method", ["GET", "PUT"])
def test_route_is_admin_class(method: str) -> None:
    assert classify(method, PATH) is AuthClass.ADMIN


def test_client_key_and_anonymous_are_refused(keyed_box: None) -> None:
    for key, status in (("box-client-key", 403), (None, 401)):
        principal = auth_mod.resolve_principal(_scope("PUT", key))
        allowed, got, _ = auth_mod._decide(classify("PUT", PATH), principal)
        assert (allowed, got) == (False, status)


def test_admin_key_is_accepted(keyed_box: None) -> None:
    principal = auth_mod.resolve_principal(_scope("PUT", "box-admin-key"))
    assert auth_mod._decide(classify("PUT", PATH), principal)[0] is True


@pytest.mark.xfail(
    strict=True,
    reason=(
        "ADR-0024 (v1.5): agents still hold the box admin key, so an agent's own "
        "credential clears this ADMIN route. With the terminal tool on, a "
        "prompt-injected agent could switch its command approvals."
    ),
)
def test_agent_credential_cannot_switch_command_approvals(keyed_box: None) -> None:
    agent_key = service_identity.service_key(prefer="admin")
    assert agent_key is not None
    principal = auth_mod.resolve_principal(_scope("PUT", agent_key))
    allowed, _, _ = auth_mod._decide(classify("PUT", PATH), principal)
    assert allowed is False


def test_put_without_hermes_installed_is_409(box: dict[str, Any], client: TestClient) -> None:
    box["seed"].unlink()
    res = client.put(PATH, json={"mode": "manual"})
    assert res.status_code == 409
    assert res.json()["error"]["code"] == "agent.command_approvals_unavailable"


def test_route_agent_id_matches_the_module() -> None:
    assert routes._AGENT_ID == cmd.AGENT_ID


def test_unknown_liveness_reports_restart_unknown(box: dict[str, Any], client: TestClient) -> None:
    box["calls"]["active"] = None
    body = client.put(PATH, json={"mode": "manual"}).json()
    assert body["restart"] == "unknown" and box["calls"]["restarts"] == []


def test_restart_timeout_is_200_with_unknown_outcome(
    box: dict[str, Any], client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _timeout(agent_id: str, *, actor: str) -> dict[str, str]:
        raise Hal0Error("systemctl restart timed out", code="agent.restart_timeout")

    monkeypatch.setattr(restart_seam, "restart_agent_unit", _timeout)
    res = client.put(PATH, json={"mode": "manual"})
    assert res.status_code == 200
    assert res.json()["restart"] == "timeout"
    assert 'command_approvals = "manual"' in box["seed"].read_text()


def test_restart_timeout_code_matches_the_seam() -> None:
    import inspect

    assert f'code="{cmd.RESTART_TIMEOUT_CODE}"' in inspect.getsource(restart_seam)
