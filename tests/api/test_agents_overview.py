"""GET /api/agents/{name}/overview: one payload for the Hermes and Pi cards.

The route gathers live inputs (systemd units, the Hermes API server, the
release tree, provision.json, the doctor checks, approvals, memory, the Pi npm
tree) and feeds the pure builders in :mod:`hal0.agents.overview`. Every
external call is a module attribute of :mod:`hal0.api.agents.overview`, so the
fixtures below replace each one and nothing here touches systemd, the network
or the real Hermes tree.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.agents.hermes_release import Pin, Release
from hal0.agents.manager import AgentRecord
from hal0.api.agents import overview as overview_route
from hal0.api.middleware import error_codes
from hal0.api.routes import agents as agents_routes

PIN_REF = "a" * 40
INSTALLED_AT = "2026-10-22T00:00:00Z"


class _FakeManager:
    def __init__(self, records: list[AgentRecord]) -> None:
        self._records = records

    def list(self) -> list[AgentRecord]:
        return self._records


def _record(name: str) -> AgentRecord:
    return AgentRecord(
        name=name,
        installed_at=INSTALLED_AT,
        status="installed",
        data_dir=f"/var/lib/hal0/agents/{name}",
        config_path=f"/etc/hal0/agents/{name}.toml",
    )


def _fake_npm_tree(tmp_path: Path, pi_v: str = "0.84.4", adapter_v: str = "2.31.0") -> Path:
    """An npm global tree with ``bin/pi`` symlinked into the Pi package."""
    root = tmp_path / "lib" / "node_modules"
    pkg = root / "@earendil-works" / "pi-coding-agent"
    (pkg / "dist" / "bundle").mkdir(parents=True)
    (pkg / "package.json").write_text(
        json.dumps({"name": "@earendil-works/pi-coding-agent", "version": pi_v})
    )
    (pkg / "dist" / "bundle" / "cli.js").write_text("")
    (root / "pi-mcp-adapter").mkdir()
    (root / "pi-mcp-adapter" / "package.json").write_text(
        json.dumps({"name": "pi-mcp-adapter", "version": adapter_v})
    )
    link = tmp_path / "bin" / "pi"
    link.parent.mkdir()
    link.symlink_to(pkg / "dist" / "bundle" / "cli.js")
    return link


def _build_client(
    monkeypatch: pytest.MonkeyPatch, records: list[AgentRecord]
) -> Iterator[TestClient]:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(overview_route.router, prefix="/api/agents", tags=["agents"])
    monkeypatch.setattr(agents_routes, "_manager", lambda: _FakeManager(records))
    with TestClient(app) as client:
        yield client


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """A hermetic Hermes home, no desktop URL, and an empty route cache."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    monkeypatch.delenv("HAL0_HERMES_PUBLIC_URL", raising=False)
    overview_route._cache.clear()
    yield
    overview_route._cache.clear()


@pytest.fixture
def probe_calls() -> dict[str, int]:
    return {"gateway_alive": 0}


def _patch_hermes_ready(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, probe_calls: dict[str, int]
) -> None:
    """Every Hermes seam answers as a healthy, provisioned, converged box."""

    async def _active(agent_id: str) -> bool | None:
        assert agent_id == "hermes"
        return True

    async def _unit_state(unit: str) -> dict[str, str | None]:
        assert unit == "hermes-gateway.service"
        return {
            "active_state": "active",
            "sub_state": "running",
            "unit_file_state": "enabled",
            "since": None,
        }

    def _gateway_alive(home: Path, *, probe: Callable[[str], int] | None = None) -> bool:
        probe_calls["gateway_alive"] += 1
        return True

    async def _memory(agent_id: str, request: Any) -> dict[str, Any]:
        return {
            "agent_id": agent_id,
            "available": True,
            "writes": 3,
            "reads": 0,
            "last_write": "2026-10-22T20:00:00Z",
        }

    provision = tmp_path / "provision.json"
    provision.write_text(
        json.dumps(
            {
                "hal0_version": "1.6.0",
                "hermes_version": PIN_REF,
                "completed_at": "2026-10-22T19:00:00Z",
                "phases": {"install": {"status": "ok", "at": "2026-10-22T19:00:00Z"}},
            }
        )
    )
    live = Release(
        id="20261022T120000Z-aaaaaaa",
        path=tmp_path / "releases" / "20261022T120000Z-aaaaaaa",
        data={"ref": PIN_REF, "describe": "v0.22.4", "python_minor": "3.14"},
    )
    attrs: dict[str, Any] = {
        "unit_is_active": _active,
        "unit_state": _unit_state,
        "gateway_alive": _gateway_alive,
        "served_profiles": lambda home, *, probe=None: ["default", "coder"],
        "component_status_snapshot": lambda: {
            "components": [
                {"id": "openwebui", "installed": "1", "pinned": "2", "status": "pending"},
                {"id": "hermes", "installed": PIN_REF, "pinned": PIN_REF, "status": "converged"},
            ],
            "pending": 1,
        },
        "live_release": lambda layout: live,
        "_previous_release": lambda layout: None,
        "read_pin": lambda: Pin(url="https://example.invalid/hermes.git", extras=(), ref=PIN_REF),
        "check_hermes_sqlite": lambda: {"status": "ok", "detail": "Python 3.14.0, SQLite 3.51.3"},
        "check_hermes_plugins": lambda: {"status": "ok", "detail": "plugins match"},
        "check_hermes_ownership": lambda **kw: [
            {"path": "/h", "label": "HERMES_HOME tree", "status": "ok", "detail": "owned by hal0"}
        ],
        "check_hermes_gateway_unit": lambda: {
            "status": "ok",
            "detail": "hermes-gateway.service installed",
        },
        "command_status": lambda: {
            "mode": "smart",
            "effective": "smart",
            "source": "setting",
            "in_sync": True,
        },
        "_queue": lambda request: SimpleNamespace(list_pending=lambda: []),
        "get_agent_memory_stats": _memory,
        "configured_platform_names": lambda *a, **kw: ["api_server", "telegram"],
        "provision_path": lambda: provision,
    }
    for attr, value in attrs.items():
        monkeypatch.setattr(overview_route, attr, value)


@pytest.fixture
def client_ready(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, probe_calls: dict[str, int]
) -> Iterator[TestClient]:
    _patch_hermes_ready(monkeypatch, tmp_path, probe_calls)
    yield from _build_client(monkeypatch, [_record("hermes")])


@pytest.fixture
def client_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, probe_calls: dict[str, int]
) -> Iterator[TestClient]:
    _patch_hermes_ready(monkeypatch, tmp_path, probe_calls)
    monkeypatch.setattr(overview_route, "pi_script", lambda: tmp_path / "missing" / "pi.sh")
    yield from _build_client(monkeypatch, [])


@pytest.fixture
def client_nosystemd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, probe_calls: dict[str, int]
) -> Iterator[TestClient]:
    """No systemd: every unit probe answers "unknown"."""
    _patch_hermes_ready(monkeypatch, tmp_path, probe_calls)

    async def _none(agent_id: str) -> None:
        return None

    async def _unknown(unit: str) -> dict[str, str | None]:
        return {
            "active_state": "unknown",
            "sub_state": "unknown",
            "unit_file_state": "unknown",
            "since": None,
        }

    monkeypatch.setattr(overview_route, "unit_is_active", _none)
    monkeypatch.setattr(overview_route, "unit_state", _unknown)
    yield from _build_client(monkeypatch, [_record("hermes")])


@pytest.fixture
def client_pi(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    link = _fake_npm_tree(tmp_path)
    script = tmp_path / "pi.sh"
    script.write_text(
        'PI_PKG="@earendil-works/pi-coding-agent@1.2.0"\nADAPTER_PKG="pi-mcp-adapter@5.3.0"\n'
    )
    # A home that does not exist is unreadable even to root, which a literal
    # ``/root`` is not.
    marker = tmp_path / "profile.json"
    marker.write_text(
        json.dumps(
            {"version": 1, "home": str(tmp_path / "no-such-home"), "profiled_at": "2026-10-22"}
        )
    )
    monkeypatch.setattr(overview_route, "pi_binary", lambda: link)
    monkeypatch.setattr(overview_route, "pi_script", lambda: script)
    monkeypatch.setattr(overview_route, "pi_marker_path", lambda: marker)
    monkeypatch.setattr(overview_route, "probe_status", lambda url, *, timeout=3.0: 0)
    yield from _build_client(monkeypatch, [_record("pi")])


@pytest.fixture
def client_pi_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    """No agents installed; the Pi installer script still pins its versions."""
    script = tmp_path / "pi.sh"
    script.write_text(
        'PI_PKG="@earendil-works/pi-coding-agent@1.2.0"\nADAPTER_PKG="pi-mcp-adapter@5.3.0"\n'
    )
    monkeypatch.setattr(overview_route, "pi_script", lambda: script)
    yield from _build_client(monkeypatch, [])


def test_overview_hermes_ready_shape(client_ready):
    body = client_ready.get("/api/agents/hermes/overview").json()
    assert body["phase"] == "ready" and body["degraded"] == []
    assert body["version"]["update_available"] is False
    assert body["units"] == {"agent": "active", "gateway": "active"}
    assert body["api_server"] == {
        "endpoint": "127.0.0.1:8642",
        "healthy": True,
        "profiles": ["default", "coder"],
    }
    assert body["platforms"] == ["api_server", "telegram"]
    assert body["links"] == {"desktop": None} and body["job"] is None
    assert body["approvals"] == {
        "mode": "smart",
        "effective": "smart",
        "in_sync": True,
        "pending": 0,
    }
    assert body["memory"] == {"available": True, "writes": 3, "last_write": "2026-10-22T20:00:00Z"}


def test_overview_hermes_not_installed_returns_pin_only(client_empty):
    body = client_empty.get("/api/agents/hermes/overview").json()
    assert body == {
        "name": "hermes",
        "kind": "daemon",
        "installed": False,
        "phase": "not_installed",
        "pin": {"ref": "a" * 40, "describe": None},
        "commands": {"install": "sudo hal0 agent install hermes"},
    }


def test_overview_degrades_without_systemd(client_nosystemd):
    body = client_nosystemd.get("/api/agents/hermes/overview").json()
    assert (
        body["units"] == {"agent": "unknown", "gateway": "unknown"} and body["phase"] == "unknown"
    )


def test_overview_unknown_agent_404(client_ready):
    assert client_ready.get("/api/agents/nope/overview").status_code == 404


def test_overview_cached_until_refresh(client_ready, probe_calls):
    client_ready.get("/api/agents/hermes/overview")
    client_ready.get("/api/agents/hermes/overview")
    assert probe_calls["gateway_alive"] == 1
    client_ready.get("/api/agents/hermes/overview?refresh=1")
    assert probe_calls["gateway_alive"] == 2


def test_overview_pi_installed_behind_pin(client_pi):
    body = client_pi.get("/api/agents/pi/overview").json()
    assert body["kind"] == "cli" and body["phase"] == "installed"
    assert body["version"] == {
        "installed": {"pi": "0.84.4", "adapter": "2.31.0"},
        "pin": {"pi": "1.2.0", "adapter": "5.3.0"},
        "update_available": True,
    }
    assert body["commands"]["upgrade"] == "sudo hal0 agent upgrade pi"
    assert (
        body["profile"]["home_readable"] is False and body["profile"]["checks"][0]["id"] == "binary"
    )
    assert body["memory"] == {
        "mcp_entry": None,
        "hindsight_config": None,
        "hindsight_api_reachable": False,
    }


def test_overview_desktop_link_from_env(client_ready, monkeypatch):
    monkeypatch.setenv("HAL0_HERMES_PUBLIC_URL", "https://h.example/")
    body = client_ready.get("/api/agents/hermes/overview?refresh=1").json()
    assert body["links"]["desktop"] == "https://h.example"


def test_overview_pi_not_installed_returns_pin_only(client_pi_empty):
    body = client_pi_empty.get("/api/agents/pi/overview").json()
    assert body == {
        "name": "pi",
        "kind": "cli",
        "installed": False,
        "phase": "not_installed",
        "version": {
            "installed": None,
            "pin": {"pi": "1.2.0", "adapter": "5.3.0"},
            "update_available": False,
        },
        "commands": {"install": "sudo hal0 agent install pi"},
    }


def test_overview_survives_a_failing_probe(client_ready, monkeypatch):
    def _boom(home, *, probe=None):
        raise RuntimeError("gateway probe exploded")

    monkeypatch.setattr(overview_route, "gateway_alive", _boom)
    resp = client_ready.get("/api/agents/hermes/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["api_server"] == {"endpoint": None, "healthy": False, "profiles": []}
    assert body["phase"] == "degraded"
    assert [r["reason"] for r in body["degraded"]] == ["api_unhealthy"]


def test_overview_missing_gateway_unit_is_absent(client_ready, monkeypatch):
    async def _not_loaded(unit: str) -> dict[str, str | None]:
        return {
            "active_state": "inactive",
            "sub_state": "dead",
            "unit_file_state": "unknown",
            "since": None,
        }

    monkeypatch.setattr(overview_route, "unit_state", _not_loaded)
    body = client_ready.get("/api/agents/hermes/overview").json()
    assert body["units"]["gateway"] == "absent"


def test_overview_job_is_the_newest_active_hermes_job(client_ready):
    client_ready.app.state.update_jobs = {
        "old": {"id": "old", "state": "running", "phase": "converge:hermes", "created_at": 1.0},
        "new": {"id": "new", "state": "queued", "phase": "repair:hermes", "created_at": 2.0},
        "done": {"id": "done", "state": "applied", "phase": "converge:hermes", "created_at": 3.0},
        "other": {
            "id": "other",
            "state": "running",
            "phase": "converge:openwebui",
            "created_at": 4.0,
        },
    }
    body = client_ready.get("/api/agents/hermes/overview").json()
    assert body["job"] == {"id": "new", "kind": "repair", "state": "queued"}
    assert body["phase"] == "working"


def _provision_and_build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, report_at: str, built_at: str | None
) -> None:
    """A provision report whose install phase failed at ``report_at``, and a live
    release built at ``built_at`` (no ``built_at`` key when ``None``)."""
    (tmp_path / "provision.json").write_text(
        json.dumps(
            {
                "hal0_version": "1.6.0",
                "hermes_version": PIN_REF,
                "completed_at": None,
                "phases": {"install": {"status": "fail", "at": report_at}},
            }
        )
    )
    data: dict[str, Any] = {"ref": PIN_REF, "describe": "v0.22.4", "python_minor": "3.14"}
    if built_at is not None:
        data["built_at"] = built_at
    live = Release(id="20261022T021958Z-aaaaaaa", path=tmp_path / "releases" / "r", data=data)
    monkeypatch.setattr(overview_route, "live_release", lambda layout: live)


def test_overview_provision_report_older_than_the_live_release_is_stale(
    client_ready, monkeypatch, tmp_path
):
    _provision_and_build(
        monkeypatch,
        tmp_path,
        report_at="2026-10-21T23:49:54Z",
        built_at="2026-10-22T02:19:58Z",
    )
    body = client_ready.get("/api/agents/hermes/overview").json()
    assert body["phase"] == "ready" and body["degraded"] == []
    provision = body["provision"]
    assert provision["stale"] is True and provision["fail"] == 1
    assert provision["reported_at"] == "2026-10-21T23:49:54Z"
    assert provision["live_built_at"] == "2026-10-22T02:19:58Z"


@pytest.mark.parametrize(
    ("report_at", "built_at"),
    [
        ("2026-10-22T03:00:00Z", "2026-10-22T02:19:58Z"),  # the report is newer than the build
        ("2026-10-21T23:49:54Z", "not-a-time"),  # an unparseable build time is not stale
        ("2026-10-21T23:49:54Z", None),  # a release with no build time is not stale
    ],
)
def test_overview_provision_report_not_older_than_the_build_still_degrades(
    client_ready, monkeypatch, tmp_path, report_at, built_at
):
    _provision_and_build(monkeypatch, tmp_path, report_at=report_at, built_at=built_at)
    body = client_ready.get("/api/agents/hermes/overview").json()
    assert body["provision"]["stale"] is False
    assert [r["reason"] for r in body["degraded"]] == ["provision_failed"]
    assert body["phase"] == "degraded"


def test_overview_route_does_not_import_hal0_cli():
    """The API layer never imports ``hal0.cli`` (see ``hal0.health_report``).

    A fresh interpreter, because this process has imported the CLI for other tests.
    """
    code = (
        "import sys, hal0.api.agents.overview; "
        "print('\\n'.join(sorted(m for m in sys.modules if m.startswith('hal0.cli'))))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    loaded = [line for line in proc.stdout.splitlines() if line.startswith("hal0.cli")]
    assert loaded == []
