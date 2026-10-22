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
