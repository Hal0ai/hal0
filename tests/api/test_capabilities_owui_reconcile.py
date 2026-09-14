"""POST /api/capabilities/{slot}/{child} triggers OpenWebUI's env reconcile
for the two slots that feed its dynamic env blocks — embed and img —
and only those two; a voice apply has nothing new for OpenWebUI to render
(STT/TTS are prewired unconditionally, see hal0.openwebui.env_writer).

Standalone router-only app (mirrors tests/api/test_services_page.py's
``svc_client`` pattern) — the capabilities router's own dependency is
overridden with a stub orchestrator so this stays independent of real slot
lifecycle plumbing.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api.deps import get_capability_orchestrator
from hal0.api.middleware import error_codes
from hal0.api.routes.capabilities import router as capabilities_router

_RECONCILE = "hal0.components.openwebui_arm.reconcile_openwebui_env"


class _FakeOrchestrator:
    async def apply(self, slot: str, child: str, body: dict) -> dict:
        return {"device": "", "provider": "", "model": body.get("model", ""), "enabled": True}


@pytest.fixture
def cap_client() -> TestClient:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(capabilities_router, prefix="/api/capabilities")
    app.dependency_overrides[get_capability_orchestrator] = lambda: _FakeOrchestrator()
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("slot,child", [("embed", "embed"), ("embed", "rerank"), ("img", "img")])
def test_apply_triggers_owui_reconcile_for_wired_slots(
    cap_client: TestClient, slot: str, child: str
) -> None:
    with patch(_RECONCILE) as reconcile:
        r = cap_client.post(f"/api/capabilities/{slot}/{child}", json={"model": "some-model"})
    assert r.status_code == 200, r.text
    reconcile.assert_called_once_with()


def test_apply_does_not_trigger_owui_reconcile_for_voice(cap_client: TestClient) -> None:
    with patch(_RECONCILE) as reconcile:
        r = cap_client.post("/api/capabilities/voice/stt", json={"model": "some-model"})
    assert r.status_code == 200, r.text
    reconcile.assert_not_called()


def test_apply_reconcile_failure_never_surfaces_on_the_response(cap_client: TestClient) -> None:
    """A broken resolver is the arm's own problem (logged), never a 5xx on
    the capability-apply response it's piggybacking on."""
    with patch(_RECONCILE, side_effect=RuntimeError("boom")):
        r = cap_client.post("/api/capabilities/embed/embed", json={"model": "some-model"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
