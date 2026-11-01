"""``hal0 doctor all`` ``openwebui_mcp`` row: Open WebUI reaches hal0's MCP tools.

The row reads ``bridge.db`` (``mode=ro``), ``openwebui.env`` and the installed unit; it
never creates or writes any of them and never prints a key.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from hal0.bridge.store import BridgeStore
from hal0.cli import doctor_all as da

_URL = "http://host.docker.internal:8080"
_ADD_HOST = "  --add-host=host.docker.internal:host-gateway \\\n"


class _Box:
    """A tmp box: bridge.db, openwebui.env and the installed unit under one tree."""

    def __init__(self, root: Path) -> None:
        self.db = root / "bridge.db"
        self.env = root / "openwebui.env"
        self.unit = root / "hal0-openwebui.service"
        self.store = BridgeStore(self.db)
        self.store.open()
        self.unit.write_text(f"[Service]\nExecStart=/usr/bin/podman run \\\n{_ADD_HOST}  image\n")

    def mint(self, created_by: str = "system:openwebui", scopes=("agents", "memory")) -> str:
        _, key = self.store.add_client("openwebui", created_by, scopes=scopes)
        return key

    def render(self, key: str, scopes=("memory", "agents")) -> None:
        ids = {"memory": "hal0-memory", "agents": "hal0-agents", "admin": "hal0-admin"}
        conns = [
            {
                "type": "mcp",
                "url": f"{_URL}/mcp/{s}/mcp",
                "auth_type": "bearer",
                "key": key,
                "info": {"id": ids[s], "name": s},
                "config": {"enable": True},
            }
            for s in scopes
        ]
        self.env.write_text(
            f"OPENAI_API_BASE_URLS={_URL}/v1\nTOOL_SERVER_CONNECTIONS={json.dumps(conns)}\n"
        )

    def check(self, **kw):
        return da.check_openwebui_mcp(
            enabled=kw.pop("enabled", True),
            db_path=self.db,
            env_path=self.env,
            unit_path=kw.pop("unit_path", self.unit),
        )


@pytest.fixture
def box(tmp_path: Path) -> _Box:
    return _Box(tmp_path)


def test_not_installed_passes(box: _Box, tmp_path: Path) -> None:
    c = box.check(unit_path=tmp_path / "missing.service")
    assert (c.key, c.status, c.critical) == ("openwebui_mcp", "pass", False)
    assert "not installed" in c.detail


def test_bridge_disabled_warns(box: _Box) -> None:
    c = box.check(enabled=False)
    assert c.status == "warn" and "bridge disabled" in c.detail and "restart hal0-api" in c.detail


def test_no_client_yet_warns(box: _Box) -> None:
    c = box.check()
    assert c.status == "warn" and "no openwebui bridge client" in c.detail


def test_absent_bridge_db_warns_without_creating_it(tmp_path: Path) -> None:
    unit = tmp_path / "u.service"
    unit.write_text(_ADD_HOST)
    c = da.check_openwebui_mcp(
        enabled=True, db_path=tmp_path / "bridge.db", env_path=tmp_path / "e", unit_path=unit
    )
    assert c.status == "warn" and not (tmp_path / "bridge.db").exists()


def test_a_client_someone_else_made_warns(box: _Box) -> None:
    box.mint(created_by="api:admin")
    c = box.check()
    assert c.status == "warn" and "api:admin" in c.detail and "not by hal0" in c.detail


def test_revoked_client_warns_and_says_it_is_not_reminted(box: _Box) -> None:
    box.mint()
    box.store.revoke_client("openwebui")
    c = box.check()
    assert c.status == "warn" and "revoked" in c.detail and "does not re-mint" in c.detail


def test_client_without_env_block_fails(box: _Box) -> None:
    box.mint()
    c = box.check()
    assert c.status == "fail" and "no TOOL_SERVER_CONNECTIONS" in c.detail
    assert "hal0 bridge rotate openwebui" in c.detail


def test_env_block_that_is_not_json_fails(box: _Box) -> None:
    box.mint()
    box.env.write_text("TOOL_SERVER_CONNECTIONS=[not json\n")
    assert box.check().status == "fail"


def test_env_missing_a_scope_fails(box: _Box) -> None:
    key = box.mint()
    box.render(key, scopes=("memory",))
    c = box.check()
    assert c.status == "fail" and "hal0-agents" in c.detail


def test_env_with_a_dropped_scope_fails(box: _Box) -> None:
    key = box.mint()
    box.render(key, scopes=("memory", "agents", "admin"))
    c = box.check()
    assert c.status == "fail" and "hal0-admin" in c.detail and "no longer" in c.detail


def test_env_with_a_wrong_url_or_key_fails(box: _Box) -> None:
    key = box.mint()
    box.render("hal0bk_someoneelse.token", scopes=("memory", "agents"))
    c = box.check()
    assert c.status == "fail" and "no openwebui bridge key" in c.detail
    box.render(key)
    box.env.write_text(box.env.read_text().replace("/mcp/memory/mcp", "/mcp/admin/mcp"))
    assert "does not point at /mcp/memory/mcp" in box.check().detail


def test_unit_without_add_host_warns(box: _Box) -> None:
    box.render(box.mint())
    box.unit.write_text("[Service]\nExecStart=/usr/bin/podman run image\n")
    c = box.check()
    assert c.status == "warn" and "--add-host=host.docker.internal:host-gateway" in c.detail
    assert "#3160" in c.detail


def test_healthy_box_passes_and_prints_no_key(box: _Box) -> None:
    key = box.mint()
    box.render(key)
    c = box.check()
    assert c.status == "pass" and "memory" in c.detail and "agents" in c.detail
    assert key not in c.detail and "hal0bk_" not in c.detail


def test_admin_scope_opt_in_is_expected_in_the_env(box: _Box) -> None:
    key = box.mint(scopes=("agents", "memory", "admin"))
    box.render(key, scopes=("memory", "agents", "admin"))
    c = box.check()
    assert c.status == "pass" and "admin" in c.detail


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any file")
def test_unreadable_store_warns(box: _Box) -> None:
    box.db.chmod(0)
    try:
        c = box.check()
    finally:
        box.db.chmod(0o600)
    assert c.status == "warn" and "cannot read" in c.detail
