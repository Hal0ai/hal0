"""``GET /api/capabilities`` keeps answering 200 when one capability's picker cannot be built.

The payload is one synchronous build over every capability (embed, voice, img), so an image tree
the service user cannot traverse used to take the voice and embed pickers down with it (a 500).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api.deps import get_capability_orchestrator
from hal0.api.middleware import error_codes
from hal0.api.routes.capabilities import router as capabilities_router
from hal0.capabilities import catalog
from hal0.capabilities.orchestrator import CapabilityOrchestrator
from hal0.comfyui.catalog import CATALOG
from tests.capabilities.test_orchestrator_reconciliation import FakeSlotManager


def test_get_capabilities_is_200_with_an_unreadable_image_tree(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setenv("HAL0_COMFYUI_DATA_ROOT", str(tmp_path / "comfyui-data"))
    monkeypatch.setattr(
        catalog, "available_backends", lambda: [{"id": "gpu-vulkan"}, {"id": "gpu-rocm"}]
    )
    monkeypatch.setattr(catalog, "rocm_lane_present", lambda: True)

    def denied(*_a: Any, **_k: Any) -> Any:
        raise PermissionError(13, "Permission denied", "/srv/models/checkpoints")

    monkeypatch.setattr("hal0.comfyui.picker.install_state", denied)
    monkeypatch.setattr("hal0.comfyui.picker.inventory.list_files", denied)

    orchestrator = CapabilityOrchestrator(slot_manager=FakeSlotManager())  # type: ignore[arg-type]
    app = FastAPI()
    error_codes.install(app)
    app.include_router(capabilities_router, prefix="/api/capabilities")
    app.dependency_overrides[get_capability_orchestrator] = lambda: orchestrator
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/api/capabilities")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    img_rows = body["catalogs"]["img"]["img"]
    assert [r["id"] for r in img_rows] == [m.id for m in CATALOG.values() if m.api_renderable]
    assert set(body["catalogs"]) >= {"embed", "voice", "img"}
