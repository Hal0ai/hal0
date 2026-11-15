"""ADR-0060 on the HTTP surface: the admin passthrough stamps the refresh
policy's timing on page and model writes, and a ``memory.refresh.*`` save
starts the reconcile."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any, ClassVar

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api.middleware import error_codes
from hal0.api.routes import memory_admin
from hal0.config.schema import Hal0Config
from hal0.memory.hindsight_client import HindsightRestClient

POLICY_TIMING = {
    "refresh_after_consolidation": False,
    "refresh_cron": "0 3 * * *",
    "min_refresh_interval_seconds": 86400,
}


class _Recorder:
    def __init__(self) -> None:
        self.bodies: list[Any] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content) if request.content else None)
        return httpx.Response(200, json={"ok": True})


class _Provider:
    def __init__(self, client: HindsightRestClient) -> None:
        self.hindsight_client = client


@pytest.fixture
def recorder() -> _Recorder:
    return _Recorder()


def _app(recorder: _Recorder, cfg: Hal0Config | None = None) -> FastAPI:
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(recorder.handler), base_url="http://127.0.0.1:9177"
    )
    app = FastAPI()
    error_codes.install(app)
    app.include_router(memory_admin.router, prefix="/api/memory")
    app.state.memory_provider = _Provider(
        HindsightRestClient(http_client=http, api_key="hal0-local-noauth")
    )
    if cfg is not None:
        app.state.hal0_config = cfg
    return app


@pytest.fixture
def client(recorder: _Recorder) -> Iterator[TestClient]:
    with TestClient(_app(recorder)) as c:
        yield c


def test_model_create_gets_the_policy_timing(client: TestClient, recorder: _Recorder) -> None:
    body = {
        "name": "n",
        "source_query": "q",
        "trigger": {"mode": "delta", "refresh_after_consolidation": True},
    }
    r = client.post("/api/memory/banks/shared/mental-models", json=body)
    assert r.status_code == 200
    assert recorder.bodies[-1]["trigger"] == {"mode": "delta", **POLICY_TIMING}
    assert r.headers["X-hal0-Ignored-Fields"] == "trigger.refresh_after_consolidation"


def test_page_create_without_a_trigger_gets_one(client: TestClient, recorder: _Recorder) -> None:
    r = client.post(
        "/api/memory/banks/shared/knowledge-base/pages", json={"name": "n", "source_query": "q"}
    )
    assert r.status_code == 200
    assert recorder.bodies[-1]["trigger"] == POLICY_TIMING
    assert "X-hal0-Ignored-Fields" not in r.headers


def test_model_patch_without_a_trigger_is_forwarded_as_sent(
    client: TestClient, recorder: _Recorder
) -> None:
    r = client.patch("/api/memory/banks/shared/mental-models/mm-1", json={"name": "x"})
    assert r.status_code == 200
    assert recorder.bodies[-1] == {"name": "x"}


def test_model_patch_trigger_follows_the_configured_policy(recorder: _Recorder) -> None:
    cfg = Hal0Config()
    cfg.memory.refresh.pages = "after_consolidation"
    cfg.memory.refresh.min_interval_s = 3600
    with TestClient(_app(recorder, cfg)) as c:
        r = c.patch(
            "/api/memory/banks/shared/mental-models/mm-1",
            json={"trigger": {"refresh_cron": "0 */4 * * *", "budget": "low"}},
        )
    assert r.status_code == 200
    assert recorder.bodies[-1]["trigger"] == {
        "budget": "low",
        "refresh_after_consolidation": True,
        "refresh_cron": None,
        "min_refresh_interval_seconds": 3600,
    }
    assert r.headers["X-hal0-Ignored-Fields"] == "trigger.refresh_cron"


def test_other_forwards_are_untouched(client: TestClient, recorder: _Recorder) -> None:
    r = client.post("/api/memory/banks/shared/directives", json={"name": "d", "content": "c"})
    assert r.status_code == 200
    assert recorder.bodies[-1] == {"name": "d", "content": "c"}


def test_status_route_reports_the_runner_and_its_notes(recorder: _Recorder) -> None:
    from hal0.memory.refresh_policy import BankPolicyReport, PolicyReport

    app = _app(recorder)
    with TestClient(app) as c:
        assert c.get("/api/memory/refresh-policy").json() == {
            "running": False,
            "last_report": None,
            "consolidation": None,
            "notes": [],
        }

        class Runner:
            last_report = PolicyReport(
                dry_run=False,
                policy={},
                trigger={},
                banks=[BankPolicyReport(bank="hal0:shared", notes=["auto-consolidation is off"])],
            )
            last_consolidation: ClassVar[dict[str, Any]] = {"due_at": 1.0, "pending": []}

        app.state.refresh_policy_runner = Runner()
        body = c.get("/api/memory/refresh-policy").json()
    assert body["running"] is True
    assert body["notes"] == ["auto-consolidation is off"]
    assert body["consolidation"] == {"due_at": 1.0, "pending": []}
    assert recorder.bodies == []  # the engine is never asked


# ── the settings save ───────────────────────────────────────────────────────


class _Runner:
    def __init__(self) -> None:
        self.kicks = 0

    def kick(self) -> None:
        self.kicks += 1


def test_a_refresh_save_kicks_the_reconcile_and_others_do_not() -> None:
    from hal0.api import create_app

    app = create_app()
    with TestClient(app) as c:
        runner = _Runner()
        app.state.refresh_policy_runner = runner
        r = c.put("/api/settings", json={"telemetry": {"enabled": False}})
        assert r.status_code == 200, r.text
        assert "refresh_policy" not in r.json()["_hal0"]
        assert runner.kicks == 0
        r = c.put(
            "/api/settings",
            json={"memory": {"refresh": {"pages": "manual", "schedule": "0 4 * * 1"}}},
        )
        assert r.status_code == 200, r.text
        assert r.json()["_hal0"]["refresh_policy"] == {"reconcile": "queued"}
        assert r.json()["memory"]["refresh"]["pages"] == "manual"
        assert sorted(r.json()["_hal0"]["apply_plan"]["immediate"]) == [
            "memory.refresh.pages",
            "memory.refresh.schedule",
        ]
        assert runner.kicks == 1
        # The same values again change nothing, so nothing is kicked.
        r = c.put("/api/settings", json={"memory": {"refresh": {"pages": "manual"}}})
        assert "refresh_policy" not in r.json()["_hal0"]
        assert runner.kicks == 1
        # No runner (memory off): the save says so.
        app.state.refresh_policy_runner = None
        r = c.put("/api/settings", json={"memory": {"refresh": {"min_interval_s": 3600}}})
        assert r.json()["_hal0"]["refresh_policy"] == {"reconcile": "unavailable"}


def test_a_bad_schedule_is_refused_by_the_settings_api() -> None:
    from hal0.api import create_app

    with TestClient(create_app()) as c:
        r = c.put("/api/settings", json={"memory": {"refresh": {"schedule": "every day"}}})
    assert r.status_code in (400, 422), r.text
    assert r.json()["error"]["code"] == "config.invalid"
