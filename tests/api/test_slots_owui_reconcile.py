"""POST /api/slots and DELETE /api/slots/{name} trigger OpenWebUI's env
reconcile for the two fixed system slot names that feed its dynamic
env blocks — "embed" and "img" — and only those two.

Reuses the isolated_client/slot_root/container_stub fixtures from
test_slots_routes.py (same app wiring, same container-provider stub).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests.api.test_slots_routes import (  # noqa: F401
    container_stub,
    isolated_app,
    isolated_client,
    slot_root,
)

_RECONCILE = "hal0.components.openwebui_arm.reconcile_openwebui_env"

_BASE_BODY = {
    "model": "qwen3-4b-q4_k_m",
    "device": "gpu-vulkan",
    "profile": "vulkan-radv",
}


@pytest.mark.parametrize("name", ["embed", "img"])
def test_create_wired_slot_triggers_owui_reconcile(
    name: str,
    slot_root,  # noqa: F811
    container_stub: dict[str, Any],  # noqa: F811
    isolated_client: TestClient,  # noqa: F811
) -> None:
    with patch(_RECONCILE) as reconcile:
        r = isolated_client.post("/api/slots", json={"name": name, **_BASE_BODY})
    assert r.status_code == 201, r.text
    reconcile.assert_called_once_with()


def test_create_unrelated_slot_does_not_trigger_owui_reconcile(
    slot_root,  # noqa: F811
    container_stub: dict[str, Any],  # noqa: F811
    isolated_client: TestClient,  # noqa: F811
) -> None:
    with patch(_RECONCILE) as reconcile:
        r = isolated_client.post("/api/slots", json={"name": "fresh", **_BASE_BODY})
    assert r.status_code == 201, r.text
    reconcile.assert_not_called()


def test_delete_wired_slot_triggers_owui_reconcile(
    slot_root,  # noqa: F811
    container_stub: dict[str, Any],  # noqa: F811
    isolated_client: TestClient,  # noqa: F811
) -> None:
    r = isolated_client.post("/api/slots", json={"name": "img", **_BASE_BODY})
    assert r.status_code == 201, r.text

    with patch(_RECONCILE) as reconcile:
        r = isolated_client.delete("/api/slots/img")
    assert r.status_code == 200, r.text
    reconcile.assert_called_once_with()


def test_delete_unrelated_slot_does_not_trigger_owui_reconcile(
    slot_root,  # noqa: F811
    container_stub: dict[str, Any],  # noqa: F811
    isolated_client: TestClient,  # noqa: F811
) -> None:
    # 'chat' comes pre-seeded by the slot_root fixture.
    with patch(_RECONCILE) as reconcile:
        r = isolated_client.delete("/api/slots/chat?force=true")
    assert r.status_code == 200, r.text
    reconcile.assert_not_called()
