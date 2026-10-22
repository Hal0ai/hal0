"""Boot phase that mirrors Hermes profiles into the upstream registry."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_boot_reconciles_hermes_upstreams_once(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []

    async def fake(registry: Any, **_: Any) -> dict[str, Any]:
        calls.append(registry)
        return {"registered": ["hermes"], "removed": [], "skipped": [], "profiles_without_key": []}

    monkeypatch.setattr("hal0.agents.hermes_upstreams.reconcile_hermes_upstreams", fake)
    with TestClient(app) as c:
        assert c.get("/api/health").status_code == 200
        assert calls == [app.state.upstreams]


def test_boot_survives_reconcile_failure(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(registry: Any, **_: Any) -> dict[str, Any]:
        raise RuntimeError("hermes down")

    monkeypatch.setattr("hal0.agents.hermes_upstreams.reconcile_hermes_upstreams", boom)
    with TestClient(app) as c:
        assert c.get("/api/health").status_code == 200


def test_boot_writes_profile_keys_before_reconcile(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    """I2: hal0-api start (so every ``hal0 update``) gives named profiles their keys."""
    order: list[str] = []

    def keys(home: Any) -> list[str]:
        order.append("keys")
        return ["coder"]

    async def fake(registry: Any, **_: Any) -> dict[str, Any]:
        order.append("reconcile")
        return {"registered": [], "removed": [], "skipped": [], "profiles_without_key": []}

    monkeypatch.setattr("hal0.agents.hermes_api.ensure_profile_keys", keys)
    monkeypatch.setattr("hal0.agents.hermes_upstreams.reconcile_hermes_upstreams", fake)
    with TestClient(app) as c:
        assert c.get("/api/health").status_code == 200
    assert order[:2] == ["keys", "reconcile"]


def test_boot_reconciles_even_when_key_sweep_fails(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Any] = []

    def boom(home: Any) -> list[str]:
        raise OSError("read-only")

    async def fake(registry: Any, **_: Any) -> dict[str, Any]:
        calls.append(registry)
        return {"registered": [], "removed": [], "skipped": [], "profiles_without_key": []}

    monkeypatch.setattr("hal0.agents.hermes_api.ensure_profile_keys", boom)
    monkeypatch.setattr("hal0.agents.hermes_upstreams.reconcile_hermes_upstreams", fake)
    with TestClient(app) as c:
        assert c.get("/api/health").status_code == 200
    assert calls == [app.state.upstreams]


def test_reconcile_interval_constant() -> None:
    import hal0.api as api_mod

    assert api_mod.HERMES_RECONCILE_INTERVAL_S == 120


async def test_periodic_reconcile_reruns_and_survives_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    import hal0.api as api_mod
    from hal0.upstreams.registry import UpstreamRegistry

    calls: list[Any] = []
    sweeps: list[Any] = []

    async def flaky(registry: Any, **_: Any) -> dict[str, Any]:
        calls.append(registry)
        if len(calls) == 1:
            raise RuntimeError("gateway restarting")
        return {"registered": [], "removed": [], "skipped": [], "profiles_without_key": []}

    monkeypatch.setattr("hal0.agents.hermes_upstreams.reconcile_hermes_upstreams", flaky)
    # Codex P2: each pass sweeps keys first, so a profile created after boot
    # gets its key without an API restart.
    monkeypatch.setattr("hal0.agents.hermes_api.ensure_profile_keys", lambda h: sweeps.append(h))
    monkeypatch.setattr(api_mod, "HERMES_RECONCILE_INTERVAL_S", 0.01)
    ctx = api_mod.BootState()
    ctx.upstreams = UpstreamRegistry()
    api_mod._start_hermes_reconcile_task(ctx)
    try:
        for _ in range(200):
            if len(calls) >= 3:
                break
            await asyncio.sleep(0.01)
        assert len(calls) >= 3
        assert len(sweeps) >= len(calls)
        assert all(r is ctx.upstreams for r in calls)
        assert ctx.hermes_reconcile_task is not None
        assert not ctx.hermes_reconcile_task.done()
    finally:
        await ctx.stop_hermes_reconcile_task()
    assert ctx.hermes_reconcile_task.done()
    seen = len(calls)
    await asyncio.sleep(0.05)
    assert len(calls) == seen


def test_lifespan_starts_and_stops_periodic_reconcile(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    import hal0.api as api_mod

    calls: list[Any] = []

    async def fake(registry: Any, **_: Any) -> dict[str, Any]:
        calls.append(registry)
        return {"registered": [], "removed": [], "skipped": [], "profiles_without_key": []}

    monkeypatch.setattr("hal0.agents.hermes_upstreams.reconcile_hermes_upstreams", fake)
    monkeypatch.setattr(api_mod, "HERMES_RECONCILE_INTERVAL_S", 0.01)
    with TestClient(app) as c:
        deadline = time.monotonic() + 5
        while len(calls) < 3 and time.monotonic() < deadline:
            assert c.get("/api/health").status_code == 200
            time.sleep(0.02)
        assert len(calls) >= 3  # boot reconcile + periodic re-runs
    seen = len(calls)
    time.sleep(0.05)
    assert len(calls) == seen


def test_suite_isolation_pins_hermes_home_under_tmp(tmp_path: Any) -> None:
    """Codex P1: app boot sweeps keys under HERMES_HOME; a suite run on a box
    with a live Hermes tree must never append to its profile .env files."""
    import os
    from pathlib import Path

    from hal0.agents import hermes_api

    assert Path(os.environ["HERMES_HOME"]) == tmp_path / "hermes"
    assert hermes_api.hermes_home() == tmp_path / "hermes"
