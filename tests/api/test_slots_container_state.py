"""Tests for container-slot state fields on /api/slots (Issue #656).

Verifies:
  - ``container_status`` (running|stopped|starting|crashed) and
    ``container_health`` (bool) appear on container slot entries.
  - Lemonade slots are unaffected — no ``container_*`` keys, and
    ``lemonade_state`` is still present as before.
  - Container slots are skipped by Lemonade enrichment (no spurious
    ``lemonade_state`` field on a container slot entry).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import hal0.providers as providers_mod
from hal0.api import create_app
from hal0.lemonade.client import LemonadeClient
from hal0.providers.lemonade import LemonadeProvider

# ── helpers ────────────────────────────────────────────────────────────────────


def _seed_slot_toml(home: str, name: str, lines: list[str]) -> Path:
    root = Path(home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ── fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def lemonade_stub(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Install a minimal Lemonade stub so Lemonade slots still enrich correctly."""
    state: dict[str, Any] = {"loaded": []}

    def h(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/v1/health":
            return httpx.Response(200, json={"loaded": state["loaded"]})
        return httpx.Response(200, json={"status": "ok"})

    transport = httpx.AsyncClient(
        transport=httpx.MockTransport(h),
        base_url="http://test",
    )
    provider = LemonadeProvider(client=LemonadeClient(http_client=transport))
    original = providers_mod._PROVIDERS["lemonade"]
    providers_mod._PROVIDERS["lemonade"] = provider
    try:
        yield state
    finally:
        providers_mod._PROVIDERS["lemonade"] = original


@pytest.fixture
def app_with_container_slot(
    tmp_hal0_home: str,
    lemonade_stub: dict[str, Any],
) -> FastAPI:
    """App with one container slot (gpu-chat) and one Lemonade slot (chat)."""
    # Container slot: profile set → _is_container_slot returns True
    _seed_slot_toml(
        tmp_hal0_home,
        "gpu-chat",
        [
            'name = "gpu-chat"',
            "port = 8088",
            'type = "llm"',
            'profile = "vulkan-radv"',
            "[model]",
            'default = "llama-3b"',
        ],
    )
    # Lemonade slot
    _seed_slot_toml(
        tmp_hal0_home,
        "chat",
        [
            'name = "chat"',
            "port = 8081",
            'type = "llm"',
            "[model]",
            'default = "qwen3-4b"',
        ],
    )
    return create_app()


@pytest.fixture
def client_with_container_slot(
    app_with_container_slot: FastAPI,
) -> Iterator[TestClient]:
    with TestClient(app_with_container_slot) as c:
        yield c


# ── container state field tests ────────────────────────────────────────────────


def test_container_slot_has_container_status_and_health_when_running(
    client_with_container_slot: TestClient,
) -> None:
    """Container slot with a healthy /health returns container_status=running, container_health=True."""
    with (
        patch(
            "hal0.providers.container.ContainerProvider.is_active",
            return_value=True,
        ),
        patch(
            "hal0.providers.container.ContainerProvider.health",
            new_callable=AsyncMock,
            return_value={"ok": True, "status": "healthy"},
        ),
    ):
        r = client_with_container_slot.get("/api/slots")
    assert r.status_code == 200, r.text
    by_name = {e["name"]: e for e in r.json()}
    assert "gpu-chat" in by_name, "container slot must appear"
    slot = by_name["gpu-chat"]
    assert slot["container_status"] == "running"
    assert slot["container_health"] is True


def test_container_slot_status_stopped_when_inactive(
    client_with_container_slot: TestClient,
) -> None:
    """Container slot with inactive unit returns container_status=stopped, container_health=False."""
    with (
        patch(
            "hal0.providers.container.ContainerProvider.is_active",
            return_value=False,
        ),
        patch(
            "subprocess.run",
            return_value=MagicMock(stdout=b"inactive", returncode=3),
        ),
    ):
        r = client_with_container_slot.get("/api/slots")
    assert r.status_code == 200, r.text
    by_name = {e["name"]: e for e in r.json()}
    slot = by_name["gpu-chat"]
    assert slot["container_status"] == "stopped"
    assert slot["container_health"] is False


def test_container_slot_status_crashed_when_failed(
    client_with_container_slot: TestClient,
) -> None:
    """Container slot in 'failed' systemd state returns container_status=crashed."""
    with (
        patch(
            "hal0.providers.container.ContainerProvider.is_active",
            return_value=False,
        ),
        patch(
            "subprocess.run",
            return_value=MagicMock(
                stdout=b"failed",
                returncode=1,
                decode=lambda *a, **kw: "failed",
            ),
        ),
    ):
        r = client_with_container_slot.get("/api/slots")
    assert r.status_code == 200, r.text
    by_name = {e["name"]: e for e in r.json()}
    slot = by_name["gpu-chat"]
    assert slot["container_status"] == "crashed"
    assert slot["container_health"] is False


def test_container_slot_status_starting_when_active_but_unhealthy(
    client_with_container_slot: TestClient,
) -> None:
    """Active unit + unhealthy /health → container_status=starting (inference server not yet up)."""
    with (
        patch(
            "hal0.providers.container.ContainerProvider.is_active",
            return_value=True,
        ),
        patch(
            "hal0.providers.container.ContainerProvider.health",
            new_callable=AsyncMock,
            return_value={"ok": False, "status": "connection_refused"},
        ),
    ):
        r = client_with_container_slot.get("/api/slots")
    assert r.status_code == 200, r.text
    by_name = {e["name"]: e for e in r.json()}
    slot = by_name["gpu-chat"]
    assert slot["container_status"] == "starting"
    assert slot["container_health"] is False


def test_lemonade_slot_unaffected_no_container_keys(
    client_with_container_slot: TestClient,
    lemonade_stub: dict[str, Any],
) -> None:
    """Lemonade slot (chat) has lemonade_state but no container_status/container_health keys."""
    lemonade_stub["loaded"] = [{"model_name": "qwen3-4b"}]
    with (
        patch(
            "hal0.providers.container.ContainerProvider.is_active",
            return_value=True,
        ),
        patch(
            "hal0.providers.container.ContainerProvider.health",
            new_callable=AsyncMock,
            return_value={"ok": True, "status": "healthy"},
        ),
    ):
        r = client_with_container_slot.get("/api/slots")
    assert r.status_code == 200, r.text
    by_name = {e["name"]: e for e in r.json()}
    primary = by_name["chat"]
    # Lemonade enrichment must still fire
    assert "lemonade_state" in primary
    # Container enrichment must NOT apply
    assert "container_status" not in primary
    assert "container_health" not in primary


def test_container_slot_has_no_lemonade_state(
    client_with_container_slot: TestClient,
) -> None:
    """Container slot must NOT have a lemonade_state key (skip in Lemonade enrichment)."""
    with (
        patch(
            "hal0.providers.container.ContainerProvider.is_active",
            return_value=True,
        ),
        patch(
            "hal0.providers.container.ContainerProvider.health",
            new_callable=AsyncMock,
            return_value={"ok": True, "status": "healthy"},
        ),
    ):
        r = client_with_container_slot.get("/api/slots")
    assert r.status_code == 200, r.text
    by_name = {e["name"]: e for e in r.json()}
    gpu_slot = by_name["gpu-chat"]
    assert "lemonade_state" not in gpu_slot, (
        "container slots must not receive a spurious lemonade_state"
    )


def test_get_slot_container_state_fields(
    client_with_container_slot: TestClient,
) -> None:
    """GET /api/slots/{name} for a container slot also includes container_status/health."""
    with (
        patch(
            "hal0.providers.container.ContainerProvider.is_active",
            return_value=True,
        ),
        patch(
            "hal0.providers.container.ContainerProvider.health",
            new_callable=AsyncMock,
            return_value={"ok": True, "status": "healthy"},
        ),
    ):
        r = client_with_container_slot.get("/api/slots/gpu-chat")
    assert r.status_code == 200, r.text
    slot = r.json()
    assert slot["container_status"] == "running"
    assert slot["container_health"] is True
