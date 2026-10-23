"""GET /api/agents/{name}/overview: one payload for the Hermes and Pi cards.

The route gathers live inputs (systemd units, the Hermes API server, the
release tree, provision.json, the doctor checks, approvals, memory, the Pi npm
tree) and feeds the pure builders in :mod:`hal0.agents.overview`. Every
external call is a module attribute of :mod:`hal0.api.agents.overview`, so the
fixtures below replace each one and nothing here touches systemd, the network
or the real Hermes tree.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
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


def _reset_cache() -> None:
    overview_route._cache.clear()
    overview_route._inflight.clear()
    overview_route._invalidated.clear()
    overview_route._forget_platforms()


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """A hermetic Hermes home, no desktop URL, and an empty route cache."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    monkeypatch.delenv("HAL0_HERMES_PUBLIC_URL", raising=False)
    _reset_cache()
    yield
    _reset_cache()


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

    async def _reachable(agent_id: str, request: Any) -> bool:
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
        "profile_dirs": lambda home: ["coder"],
        "profile_health": lambda home, name, *, probe=None: 200,
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
        "memory_reachable": _reachable,
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
    assert body["units"] == {"agent": "active", "gateway": "active", "gateway_enabled": True}
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
    assert body["units"] == {"agent": "unknown", "gateway": "unknown", "gateway_enabled": None}
    assert body["phase"] == "unknown"


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
    assert body["api_server"] == {"endpoint": "127.0.0.1:8642", "healthy": False, "profiles": []}
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
    assert body["units"]["gateway"] == "absent" and body["units"]["gateway_enabled"] is None
    assert body["degraded"] == []  # the gateway_unit doctor row covers a missing unit


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


def test_overview_probes_the_gateway_and_every_profile_at_once(client_ready, monkeypatch):
    """Three probes must be in flight together to pass the barrier; sequential ones time out."""
    barrier = threading.Barrier(3, timeout=5)

    def alive(home, *, probe=None):
        barrier.wait()
        return True

    def health(home, name, *, probe=None):
        barrier.wait()
        return 200 if name == "coder" else 404

    monkeypatch.setattr(overview_route, "profile_dirs", lambda home: ["coder", "ghost"])
    monkeypatch.setattr(overview_route, "gateway_alive", alive)
    monkeypatch.setattr(overview_route, "profile_health", health)
    body = client_ready.get("/api/agents/hermes/overview").json()
    assert body["api_server"]["healthy"] is True
    assert body["api_server"]["profiles"] == ["default", "coder"]


@pytest.mark.parametrize(
    ("active_state", "expected"),
    [
        ("active", "active"),
        ("activating", "active"),
        ("reloading", "active"),
        ("refreshing", "active"),
        ("maintenance", "active"),
        ("deactivating", "inactive"),
        ("failed", "failed"),
    ],
)
def test_overview_gateway_transition_states_are_not_down(
    client_ready, monkeypatch, active_state, expected
):
    async def _state(unit: str) -> dict[str, str | None]:
        return {
            "active_state": active_state,
            "sub_state": "start",
            "unit_file_state": "enabled",
            "since": None,
        }

    monkeypatch.setattr(overview_route, "unit_state", _state)
    body = client_ready.get("/api/agents/hermes/overview").json()
    assert body["units"]["gateway"] == expected


def test_overview_rejected_builder_input_degrades_one_block(client_ready, monkeypatch, tmp_path):
    """A release.json or provision.json of an unexpected shape must not 500."""
    bad = Release(id="r", path=tmp_path / "r", data={"ref": PIN_REF, "extras": 5})
    monkeypatch.setattr(overview_route, "live_release", lambda layout: bad)
    (tmp_path / "provision.json").write_text(json.dumps({"phases": 5}))
    resp = client_ready.get("/api/agents/hermes/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"]["live"] is None and body["version"]["update_available"] is False
    assert body["version"]["pin"] == {"ref": PIN_REF, "describe": None}
    assert body["provision"]["present"] is False and body["provision"]["stale"] is False
    assert body["units"] == {"agent": "active", "gateway": "active", "gateway_enabled": True}


class _CountingManager(_FakeManager):
    """Signals each ``list()`` call, which a request makes just before it joins a build."""

    def __init__(self, records: list[AgentRecord]) -> None:
        super().__init__(records)
        self.calls = 0
        self.second_call = threading.Event()

    def list(self) -> list[AgentRecord]:
        self.calls += 1
        if self.calls >= 2:
            self.second_call.set()
        return super().list()


def test_overview_concurrent_misses_share_one_build(client_ready, monkeypatch, probe_calls):
    manager = _CountingManager([_record("hermes")])
    monkeypatch.setattr(agents_routes, "_manager", lambda: manager)
    entered, release = threading.Event(), threading.Event()

    def slow_alive(home, *, probe=None):
        probe_calls["gateway_alive"] += 1
        entered.set()
        assert release.wait(10)
        return True

    monkeypatch.setattr(overview_route, "gateway_alive", slow_alive)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(client_ready.get, "/api/agents/hermes/overview")
        assert entered.wait(10)
        second = pool.submit(client_ready.get, "/api/agents/hermes/overview")
        assert manager.second_call.wait(10)
        time.sleep(0.2)  # the second request has joined the running build by now
        release.set()
        bodies = [f.result(timeout=20).json() for f in (first, second)]
    assert probe_calls["gateway_alive"] == 1
    assert bodies[0] == bodies[1]


def test_overview_late_build_does_not_overwrite_a_refresh(client_ready, monkeypatch, probe_calls):
    manager = _CountingManager([_record("hermes")])
    monkeypatch.setattr(agents_routes, "_manager", lambda: manager)
    entered, release = threading.Event(), threading.Event()

    def alive(home, *, probe=None):
        probe_calls["gateway_alive"] += 1
        if probe_calls["gateway_alive"] == 1:  # the first build stalls
            entered.set()
            assert release.wait(10)
        return True

    platform_calls = []

    def platforms(*args, **kwargs):
        platform_calls.append(1)
        return ["old"] if len(platform_calls) == 1 else ["fresh"]

    monkeypatch.setattr(overview_route, "gateway_alive", alive)
    monkeypatch.setattr(overview_route, "configured_platform_names", platforms)
    with ThreadPoolExecutor(2) as pool:
        slow = pool.submit(client_ready.get, "/api/agents/hermes/overview")
        assert entered.wait(10)
        refreshed = pool.submit(client_ready.get, "/api/agents/hermes/overview?refresh=1")
        assert refreshed.result(timeout=20).json()["platforms"] == ["fresh"]
        release.set()
        assert slow.result(timeout=20).json()["platforms"] == ["old"]
    # The stalled build finished last; the cache still holds the refresh.
    assert client_ready.get("/api/agents/hermes/overview").json()["platforms"] == ["fresh"]
    assert probe_calls["gateway_alive"] == 2


def test_overview_cache_expires_after_the_ttl(client_ready, monkeypatch, probe_calls):
    clock = [1000.0]
    monkeypatch.setattr(overview_route, "_now", lambda: clock[0])
    client_ready.get("/api/agents/hermes/overview")
    clock[0] += overview_route._CACHE_TTL_S - 0.1
    client_ready.get("/api/agents/hermes/overview")
    assert probe_calls["gateway_alive"] == 1
    clock[0] += 0.2
    client_ready.get("/api/agents/hermes/overview")
    assert probe_calls["gateway_alive"] == 2


def test_overview_uninstall_drops_the_cache_entry(client_ready, monkeypatch):
    records = [_record("hermes")]
    monkeypatch.setattr(agents_routes, "_manager", lambda: _FakeManager(records))
    assert client_ready.get("/api/agents/hermes/overview").json()["installed"] is True
    assert "hermes" in overview_route._cache
    records.clear()
    body = client_ready.get("/api/agents/hermes/overview").json()
    assert body["installed"] is False and body["phase"] == "not_installed"
    assert "hermes" not in overview_route._cache


def test_overview_live_fields_are_current_on_a_cache_hit(client_ready, monkeypatch, probe_calls):
    """An approval or a unit change inside the cache window shows without a refresh.

    (A Hermes job starting or ending is a cache miss; see the job test below.)
    """
    first = client_ready.get("/api/agents/hermes/overview").json()
    assert first["job"] is None and first["phase"] == "ready"
    assert first["approvals"]["pending"] == 0

    pending = [{"id": "a"}, {"id": "b"}]
    monkeypatch.setattr(
        overview_route, "_queue", lambda request: SimpleNamespace(list_pending=lambda: pending)
    )

    async def _inactive(agent_id: str) -> bool:
        return False

    monkeypatch.setattr(overview_route, "unit_is_active", _inactive)
    after = client_ready.get("/api/agents/hermes/overview").json()
    assert after["approvals"]["pending"] == 2
    assert after["phase"] == "down" and after["unit_active"] is False
    assert after["units"] == {"agent": "inactive", "gateway": "active", "gateway_enabled": True}
    assert probe_calls["gateway_alive"] == 1  # a cache hit


def _gateway_unit_state(monkeypatch, active_state, unit_file_state, sub_state="dead"):
    async def _state(unit: str) -> dict[str, str | None]:
        return {
            "active_state": active_state,
            "sub_state": sub_state,
            "unit_file_state": unit_file_state,
            "since": None,
        }

    monkeypatch.setattr(overview_route, "unit_state", _state)


@pytest.mark.parametrize("unit_file_state", ["disabled", "masked", "masked-runtime"])
def test_overview_gateway_the_operator_turned_off_is_not_degraded(
    client_ready, monkeypatch, unit_file_state
):
    _gateway_unit_state(monkeypatch, "inactive", unit_file_state)
    monkeypatch.setattr(overview_route, "gateway_alive", lambda home, *, probe=None: False)
    body = client_ready.get("/api/agents/hermes/overview").json()
    assert body["units"]["gateway"] == "inactive" and body["units"]["gateway_enabled"] is False
    assert body["degraded"] == [] and body["phase"] == "ready"


def test_overview_gateway_that_should_run_but_is_stopped_is_degraded(client_ready, monkeypatch):
    _gateway_unit_state(monkeypatch, "failed", "enabled")
    body = client_ready.get("/api/agents/hermes/overview").json()
    assert body["units"]["gateway_enabled"] is True
    assert [r["reason"] for r in body["degraded"]] == ["gateway_down"]


@pytest.mark.parametrize(
    ("active_state", "sub_state"),
    [
        ("activating", "start"),
        ("activating", "start-post"),
        ("reloading", "reload"),
        ("refreshing", "running"),
        ("maintenance", "dead"),
    ],
)
def test_overview_api_not_answering_while_the_gateway_settles_is_not_degraded(
    client_ready, monkeypatch, active_state, sub_state
):
    _gateway_unit_state(monkeypatch, active_state, "enabled", sub_state)
    monkeypatch.setattr(overview_route, "gateway_alive", lambda home, *, probe=None: False)
    body = client_ready.get("/api/agents/hermes/overview").json()
    assert body["units"]["gateway"] == "active" and body["degraded"] == []


def test_overview_api_not_answering_from_an_active_gateway_is_degraded(client_ready, monkeypatch):
    _gateway_unit_state(monkeypatch, "active", "enabled")
    monkeypatch.setattr(overview_route, "gateway_alive", lambda home, *, probe=None: False)
    body = client_ready.get("/api/agents/hermes/overview").json()
    assert [r["reason"] for r in body["degraded"]] == ["api_unhealthy"]


def test_overview_stalled_memory_and_unreachable_engine_is_unavailable(client_ready, monkeypatch):
    async def _never(agent_id: str, request: Any) -> dict[str, Any]:
        await asyncio.sleep(30)  # far past the budget: only a timeout can end the wait
        return {"available": True, "writes": 99, "last_write": "late"}

    async def _down(agent_id: str, request: Any) -> bool:
        raise ConnectionError("engine down")

    monkeypatch.setattr(overview_route, "get_agent_memory_stats", _never)
    monkeypatch.setattr(overview_route, "memory_reachable", _down)
    monkeypatch.setattr(overview_route, "_MEMORY_TIMEOUT_S", 0.05)
    resp = client_ready.get("/api/agents/hermes/overview")
    assert resp.status_code == 200
    assert resp.json()["memory"] == {"available": False, "writes": None, "last_write": None}


def test_overview_slow_memory_count_keeps_memory_available(client_ready, monkeypatch):
    """#2957: a large bank's slow count is not an outage; the reach check decides."""

    async def _slow_count(agent_id: str, request: Any) -> dict[str, Any]:
        await asyncio.sleep(30)
        return {"available": True, "writes": 500, "last_write": "late"}

    monkeypatch.setattr(overview_route, "get_agent_memory_stats", _slow_count)
    monkeypatch.setattr(overview_route, "_MEMORY_TIMEOUT_S", 0.05)
    resp = client_ready.get("/api/agents/hermes/overview")
    assert resp.json()["memory"] == {"available": True, "writes": None, "last_write": None}


def test_overview_memory_availability_ignores_a_slow_filtered_walk(client_ready, monkeypatch):
    """The reach check, not any bank walk, decides: only the count is slow."""
    walked: list[str] = []

    async def _walk(agent_id: str, request: Any) -> dict[str, Any]:
        walked.append(agent_id)
        await asyncio.sleep(30)
        return {"available": True, "writes": 1, "last_write": None}

    monkeypatch.setattr(overview_route, "get_agent_memory_stats", _walk)
    monkeypatch.setattr(overview_route, "_MEMORY_TIMEOUT_S", 0.05)
    assert client_ready.get("/api/agents/hermes/overview").json()["memory"]["available"] is True
    assert walked == ["hermes"]


def test_overview_a_job_starting_or_ending_is_a_cache_miss(client_ready, probe_calls):
    client_ready.get("/api/agents/hermes/overview")
    client_ready.get("/api/agents/hermes/overview")
    assert probe_calls["gateway_alive"] == 1

    jobs = {"j": {"id": "j", "state": "running", "phase": "converge:hermes", "created_at": 1.0}}
    client_ready.app.state.update_jobs = jobs
    assert client_ready.get("/api/agents/hermes/overview").json()["phase"] == "working"
    assert probe_calls["gateway_alive"] == 2
    client_ready.get("/api/agents/hermes/overview")  # same job: cached
    assert probe_calls["gateway_alive"] == 2

    jobs["j"]["state"] = "applied"
    assert client_ready.get("/api/agents/hermes/overview").json()["phase"] == "ready"
    assert probe_calls["gateway_alive"] == 3
    client_ready.get("/api/agents/hermes/overview")
    assert probe_calls["gateway_alive"] == 3


def test_overview_invalidate_drops_the_cache_entry(client_ready, probe_calls):
    client_ready.get("/api/agents/hermes/overview")
    overview_route.invalidate("hermes")
    assert "hermes" not in overview_route._cache
    client_ready.get("/api/agents/hermes/overview")
    assert probe_calls["gateway_alive"] == 2


def test_overview_a_build_running_during_invalidate_stores_nothing(
    client_ready, monkeypatch, probe_calls
):
    entered, release = threading.Event(), threading.Event()

    def alive(home, *, probe=None):
        probe_calls["gateway_alive"] += 1
        if probe_calls["gateway_alive"] == 1:
            entered.set()
            assert release.wait(10)
        return True

    monkeypatch.setattr(overview_route, "gateway_alive", alive)
    with ThreadPoolExecutor(1) as pool:
        stalled = pool.submit(client_ready.get, "/api/agents/hermes/overview")
        assert entered.wait(10)
        overview_route.invalidate("hermes")
        release.set()
        assert stalled.result(timeout=20).status_code == 200
    assert "hermes" not in overview_route._cache
    client_ready.get("/api/agents/hermes/overview")
    assert probe_calls["gateway_alive"] == 2


def test_overview_cache_age_counts_from_when_the_build_finished(
    client_ready, monkeypatch, probe_calls
):
    clock = [1000.0]
    monkeypatch.setattr(overview_route, "_now", lambda: clock[0])

    def slow_alive(home, *, probe=None):
        probe_calls["gateway_alive"] += 1
        clock[0] += 20.0  # the build takes 20 s
        return True

    monkeypatch.setattr(overview_route, "gateway_alive", slow_alive)
    client_ready.get("/api/agents/hermes/overview")
    clock[0] += 25.0  # 45 s after the build began, 25 s after it finished
    client_ready.get("/api/agents/hermes/overview")
    assert probe_calls["gateway_alive"] == 1
    clock[0] += 6.0  # 31 s after it finished
    client_ready.get("/api/agents/hermes/overview")
    assert probe_calls["gateway_alive"] == 2


def test_overview_generated_at_is_the_build_start_wall_clock(client_ready):
    before = datetime.now(UTC)
    first = client_ready.get("/api/agents/hermes/overview").json()["generated_at"]
    after = datetime.now(UTC)
    assert first.endswith("Z")
    assert before.replace(microsecond=0) <= datetime.fromisoformat(first) <= after
    assert client_ready.get("/api/agents/hermes/overview").json()["generated_at"] == first
    time.sleep(0.01)
    assert client_ready.get("/api/agents/hermes/overview?refresh=1").json()["generated_at"] != first


@pytest.mark.parametrize("sub_state", ["auto-restart", "auto-restart-queued"])
def test_overview_crash_looping_gateway_reads_down_not_settling(
    client_ready, monkeypatch, sub_state
):
    """Restart=always with no start limit never reaches `failed`: it sits in activating/auto-restart."""
    _gateway_unit_state(monkeypatch, "activating", "enabled", sub_state)
    monkeypatch.setattr(overview_route, "gateway_alive", lambda home, *, probe=None: False)
    body = client_ready.get("/api/agents/hermes/overview").json()
    assert body["units"]["gateway"] == "failed"
    assert body["phase"] == "degraded"
    (reason,) = body["degraded"]
    assert reason["reason"] == "gateway_down" and reason["fix"] == "restart_gateway"
    assert "crash-looping (auto-restart)" in reason["detail"]


def test_overview_unreadable_gateway_unit_row_offers_no_fix(client_ready, monkeypatch):
    monkeypatch.setattr(
        overview_route,
        "check_hermes_gateway_unit",
        lambda: {"status": "unreadable", "detail": "cannot read the Hermes home"},
    )
    body = client_ready.get("/api/agents/hermes/overview").json()
    (reason,) = body["degraded"]
    assert reason["reason"] == "doctor_drift" and reason["fix"] == "none"
    assert reason["command"] is None
    assert {
        "id": "gateway_unit",
        "status": "unreadable",
        "detail": "cannot read the Hermes home",
    } in body["doctor"]


# ── a build that takes too long ──────────────────────────────────────────────


def _hang_gather(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    """The Hermes gather never finishes (a wedged ``sudo``, a hung ``systemctl``)."""

    async def _hang(request: Any) -> dict[str, Any]:
        calls.append("gather")
        await asyncio.sleep(60)
        raise AssertionError("the build timeout did not cancel the gather")

    monkeypatch.setattr(overview_route, "_gather_hermes", _hang)
    monkeypatch.setattr(overview_route, "_BUILD_TIMEOUT_S", 0.05)


def test_overview_build_timeout_answers_a_neutral_partial_overview(client_ready, monkeypatch):
    calls: list[str] = []
    _hang_gather(monkeypatch, calls)

    r = client_ready.get("/api/agents/hermes/overview")

    assert r.status_code == 200
    body = r.json()
    assert body["partial"] is True
    assert body["phase"] == "unknown"
    assert body["installed"] is True and body["status"] == "installed"
    assert body["name"] == "hermes" and body["kind"] == "daemon"
    assert body["generated_at"].endswith("Z")
    # The live half is still answered, from the unit probe and the queue.
    assert body["unit_active"] is True
    assert body["units"] == {"agent": "active", "gateway": "unknown", "gateway_enabled": None}
    assert body["approvals"] == {"mode": None, "effective": None, "in_sync": None, "pending": 0}
    assert body["job"] is None
    # Nothing the slow half would have said is claimed.
    assert body["api_server"] == {"endpoint": None, "healthy": None, "profiles": []}
    assert body["platforms"] == [] and body["doctor"] == [] and body["degraded"] == []
    assert body["version"]["live"] is None and body["version"]["update_available"] is False
    assert body["provision"]["present"] is False
    assert body["memory"] == {"available": False, "writes": None, "last_write": None}
    assert calls == ["gather"]


def test_overview_build_timeout_is_cached_for_the_normal_ttl(client_ready, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(overview_route, "_now", lambda: clock[0])
    calls: list[str] = []
    _hang_gather(monkeypatch, calls)

    first = client_ready.get("/api/agents/hermes/overview").json()
    clock[0] += overview_route._CACHE_TTL_S - 0.1
    second = client_ready.get("/api/agents/hermes/overview").json()

    assert calls == ["gather"]  # the next poll did not stall again
    assert second == first
    clock[0] += 0.2
    assert client_ready.get("/api/agents/hermes/overview").json()["partial"] is True
    assert calls == ["gather", "gather"]


def test_overview_refresh_after_a_timeout_builds_the_real_thing(
    client_ready, monkeypatch, probe_calls
):
    real_gather = overview_route._gather_hermes
    _hang_gather(monkeypatch, [])
    assert client_ready.get("/api/agents/hermes/overview").json()["partial"] is True

    monkeypatch.setattr(overview_route, "_gather_hermes", real_gather)
    body = client_ready.get("/api/agents/hermes/overview?refresh=1").json()

    assert "partial" not in body and body["phase"] == "ready"
    assert body["platforms"] == ["api_server", "telegram"]


def test_overview_a_normal_build_is_not_marked_partial(client_ready):
    assert "partial" not in client_ready.get("/api/agents/hermes/overview").json()


def test_overview_neutral_overview_has_the_shape_of_a_real_one(client_ready, monkeypatch):
    real = client_ready.get("/api/agents/hermes/overview").json()
    _reset_cache()
    _hang_gather(monkeypatch, [])
    neutral = client_ready.get("/api/agents/hermes/overview").json()

    assert set(neutral) == set(real) | {"partial"}
    for key in ("version", "provision", "api_server", "approvals", "memory", "links", "units"):
        assert set(neutral[key]) == set(real[key]), key


def test_overview_timed_out_build_still_shows_a_running_job(client_ready, monkeypatch):
    jobs = {"j": {"id": "j", "state": "running", "phase": "repair:hermes", "created_at": 1.0}}
    client_ready.app.state.update_jobs = jobs
    _hang_gather(monkeypatch, [])

    body = client_ready.get("/api/agents/hermes/overview").json()

    assert body["partial"] is True and body["phase"] == "working"
    assert body["job"] == {"id": "j", "kind": "repair", "state": "running"}


def test_overview_timed_out_build_still_shows_a_stopped_unit(client_ready, monkeypatch):
    async def _inactive(agent_id: str) -> bool:
        return False

    monkeypatch.setattr(overview_route, "unit_is_active", _inactive)
    _hang_gather(monkeypatch, [])

    body = client_ready.get("/api/agents/hermes/overview").json()

    assert body["partial"] is True and body["phase"] == "down"


def test_overview_waiters_share_the_timed_out_build(client_ready, monkeypatch):
    manager = _CountingManager([_record("hermes")])
    monkeypatch.setattr(agents_routes, "_manager", lambda: manager)
    entered = threading.Event()
    calls: list[str] = []

    async def _hang(request: Any) -> dict[str, Any]:
        calls.append("gather")
        entered.set()
        await asyncio.sleep(60)
        raise AssertionError("the build timeout did not cancel the gather")

    monkeypatch.setattr(overview_route, "_gather_hermes", _hang)
    monkeypatch.setattr(overview_route, "_BUILD_TIMEOUT_S", 1.5)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(client_ready.get, "/api/agents/hermes/overview")
        assert entered.wait(10)
        second = pool.submit(client_ready.get, "/api/agents/hermes/overview")
        assert manager.second_call.wait(10)
        bodies = [f.result(timeout=20).json() for f in (first, second)]

    assert calls == ["gather"]
    assert bodies[0] == bodies[1] and bodies[0]["partial"] is True


def test_overview_pi_build_timeout_answers_a_neutral_partial_overview(client_pi, monkeypatch):
    async def _hang() -> dict[str, Any]:
        await asyncio.sleep(60)
        raise AssertionError("the build timeout did not cancel the gather")

    monkeypatch.setattr(overview_route, "_gather_pi", _hang)
    monkeypatch.setattr(overview_route, "_BUILD_TIMEOUT_S", 0.05)

    body = client_pi.get("/api/agents/pi/overview").json()

    assert body["partial"] is True and body["installed"] is True
    assert body["phase"] == "installed" and body["status"] == "installed"
    assert body["version"] == {"installed": None, "pin": None, "update_available": False}
    assert body["profile"]["checks"] == []
    assert body["commands"]["upgrade"] == "sudo hal0 agent upgrade pi"
    assert body["generated_at"].endswith("Z")


# ── the platform-names probe ─────────────────────────────────────────────────


@pytest.fixture
def platform_probe(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """Count the vault reads; each build that reaches the probe appends its arguments."""
    calls: list[tuple[Any, ...]] = []

    def _names(home, secrets_env, *, seam=None):
        calls.append((home, secrets_env, seam))
        return ["api_server", "telegram"]

    monkeypatch.setattr(overview_route, "configured_platform_names", _names)
    return calls


def _expire_overview_cache(clock: list[float]) -> None:
    clock[0] += overview_route._CACHE_TTL_S + 1


def test_overview_platform_names_are_probed_once_per_five_minutes(
    client_ready, monkeypatch, platform_probe
):
    clock = [1000.0]
    monkeypatch.setattr(overview_route, "_now", lambda: clock[0])
    assert overview_route._PLATFORMS_TTL_S == 300.0

    first = client_ready.get("/api/agents/hermes/overview").json()
    _expire_overview_cache(clock)  # a second build, 31 s on
    second = client_ready.get("/api/agents/hermes/overview").json()

    assert len(platform_probe) == 1
    assert first["platforms"] == second["platforms"] == ["api_server", "telegram"]
    clock[0] = 1000.0 + 301
    client_ready.get("/api/agents/hermes/overview")
    assert len(platform_probe) == 2


def test_overview_platform_names_are_reused_until_the_ttl_edge(
    client_ready, monkeypatch, platform_probe
):
    clock = [1000.0]
    monkeypatch.setattr(overview_route, "_now", lambda: clock[0])
    client_ready.get("/api/agents/hermes/overview")
    clock[0] = 1000.0 + 299.9
    client_ready.get("/api/agents/hermes/overview")
    assert len(platform_probe) == 1


def test_overview_invalidate_forces_a_platform_probe(client_ready, monkeypatch, platform_probe):
    client_ready.get("/api/agents/hermes/overview")
    overview_route.invalidate("pi")  # another agent's invalidation leaves it alone
    client_ready.get("/api/agents/hermes/overview")
    assert len(platform_probe) == 1

    overview_route.invalidate("hermes")
    client_ready.get("/api/agents/hermes/overview")
    assert len(platform_probe) == 2


def test_overview_refresh_forces_a_platform_probe(client_ready, platform_probe):
    client_ready.get("/api/agents/hermes/overview")
    client_ready.get("/api/agents/hermes/overview?refresh=1")
    assert len(platform_probe) == 2


def test_overview_a_failed_platform_probe_is_not_cached(client_ready, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(overview_route, "_now", lambda: clock[0])
    calls: list[int] = []

    def _flaky(home, secrets_env, *, seam=None):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("sudo: a password is required")
        return ["telegram"]

    monkeypatch.setattr(overview_route, "configured_platform_names", _flaky)

    assert client_ready.get("/api/agents/hermes/overview").json()["platforms"] == []
    _expire_overview_cache(clock)
    assert client_ready.get("/api/agents/hermes/overview").json()["platforms"] == ["telegram"]
    assert len(calls) == 2


def test_overview_platform_cache_is_keyed_by_home_and_vault(monkeypatch, platform_probe):
    async def _run() -> None:
        await overview_route._platform_names(Path("/h1"), Path("/v"), None)
        await overview_route._platform_names(Path("/h1"), Path("/v"), None)
        await overview_route._platform_names(Path("/h2"), Path("/v"), None)
        await overview_route._platform_names(Path("/h1"), None, None)

    asyncio.run(_run())

    assert [(home, vault) for home, vault, _seam in platform_probe] == [
        (Path("/h1"), Path("/v")),
        (Path("/h2"), Path("/v")),
        (Path("/h1"), None),
    ]


def test_overview_platform_names_are_returned_as_a_copy(monkeypatch, platform_probe):
    async def _run() -> None:
        names = await overview_route._platform_names(Path("/h"), Path("/v"), None)
        names.append("mutated")
        assert await overview_route._platform_names(Path("/h"), Path("/v"), None) == [
            "api_server",
            "telegram",
        ]

    asyncio.run(_run())


def _stalling_probe(calls: list[int], started: threading.Event, release: threading.Event):
    """A vault read whose first call stalls and answers ``old``; later calls answer ``fresh``."""

    def _names(home, secrets_env, *, seam=None):
        calls.append(1)
        if len(calls) == 1:
            started.set()
            assert release.wait(10)
            return ["old"]
        return ["fresh"]

    return _names


def test_overview_a_platform_probe_running_during_invalidate_stores_nothing(monkeypatch):
    calls: list[int] = []
    started, release = threading.Event(), threading.Event()
    monkeypatch.setattr(
        overview_route, "configured_platform_names", _stalling_probe(calls, started, release)
    )

    async def _run() -> list[list[str]]:
        task = asyncio.create_task(overview_route._platform_names(Path("/h"), Path("/v"), None))
        await asyncio.to_thread(started.wait, 10)
        overview_route.invalidate("hermes")  # the operator changed a platform meanwhile
        release.set()
        own = await task  # its own build still gets what it read
        later = await overview_route._platform_names(Path("/h"), Path("/v"), None)
        return [own, later]

    own, later = asyncio.run(_run())

    assert own == ["old"]
    assert later == ["fresh"] and len(calls) == 2  # nothing stale was stored
    assert [names for _stamp, names in overview_route._platforms_cache.values()] == [["fresh"]]


def test_overview_a_platform_probe_running_during_refresh_stores_nothing(client_ready, monkeypatch):
    manager = _CountingManager([_record("hermes")])
    monkeypatch.setattr(agents_routes, "_manager", lambda: manager)
    calls: list[int] = []
    started, release = threading.Event(), threading.Event()
    monkeypatch.setattr(
        overview_route, "configured_platform_names", _stalling_probe(calls, started, release)
    )
    with ThreadPoolExecutor(2) as pool:
        slow = pool.submit(client_ready.get, "/api/agents/hermes/overview")
        assert started.wait(10)
        refreshed = pool.submit(client_ready.get, "/api/agents/hermes/overview?refresh=1")
        assert refreshed.result(timeout=20).json()["platforms"] == ["fresh"]
        release.set()
        assert slow.result(timeout=20).json()["platforms"] == ["old"]

    # The stalled probe finished last; the platform cache still holds the refresh's.
    assert [names for _stamp, names in overview_route._platforms_cache.values()] == [["fresh"]]
