"""Tests for container-slot state fields on /api/slots (Issue #656, Phase E #687).

Verifies:
  - ``container_status`` (running|stopped|starting|crashed) and
    ``container_health`` (bool) appear on slot entries.
  - The container probe runs for EVERY slot — profile-backed and
    profile-less alike all carry ``container_status`` /
    ``container_health`` / ``runtime`` / ``profile`` / ``image`` /
    ``image_status`` keys.
  - ``runtime``/``profile``/``image``/``resolved_command`` enrichment
    (issue #658) and ``actual_image``/``image_mismatch`` (#663).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app

# ── helpers ────────────────────────────────────────────────────────────────────


def _seed_slot_toml(home: str, name: str, lines: list[str]) -> Path:
    root = Path(home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ── fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def app_with_container_slot(tmp_hal0_home: str) -> FastAPI:
    """App with one profile-backed slot (gpu-chat) and one profile-less slot (chat)."""
    # Profile-backed container slot.
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
    # Profile-less slot — still probed, but has no image/resolved_command.
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


def test_every_slot_gets_container_state_fields(
    client_with_container_slot: TestClient,
) -> None:
    """The container probe covers EVERY slot — the profile-less chat slot
    carries container_status / container_health / runtime too."""
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
    for name in ("gpu-chat", "chat"):
        slot = by_name[name]
        assert slot["container_status"] == "running", f"{name} missing container_status"
        assert slot["container_health"] is True, f"{name} missing container_health"
        assert slot["runtime"] == "container", f"{name} must report runtime=container"


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


# ── runtime/profile/image/resolved_command enrichment (issue #658) ─────────────


def test_container_slot_has_runtime_profile_image_fields(
    client_with_container_slot: TestClient,
) -> None:
    """Container slots must expose runtime/profile/image/resolved_command on /api/slots."""
    from hal0.config.schema import ProfileConfig

    fake_profile = ProfileConfig(
        image="ghcr.io/hal0ai/amd-strix-halo-toolboxes:vulkan-radv-server",
        flags="--flash-attn on -ngl 999",
        mtp=False,
    )
    fake_catalog = MagicMock(profile={"vulkan-radv": fake_profile})
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
        # slot_view inline-imports load_profiles_config for the image field;
        # container.py's resolved_command_for_slot uses the same loader.
        patch(
            "hal0.config.loader.load_profiles_config",
            return_value=fake_catalog,
        ),
    ):
        r = client_with_container_slot.get("/api/slots")
    assert r.status_code == 200, r.text
    by_name = {e["name"]: e for e in r.json()}
    slot = by_name["gpu-chat"]

    assert slot.get("runtime") == "container", "runtime must be 'container'"
    assert slot.get("profile") == "vulkan-radv", "profile must be the slot's profile name"
    assert slot.get("image") == fake_profile.image, "image must come from profile"
    # resolved_command: list starting with the image tag
    rc = slot.get("resolved_command")
    assert rc is not None, "resolved_command must be present"
    assert isinstance(rc, list), "resolved_command must be a list"
    assert rc[0] == fake_profile.image, "resolved_command[0] must be the image"
    # model token must be the string value from [model] default, not a dict repr
    joined = " ".join(rc)
    assert "--model llama-3b" in joined, (
        f"resolved_command must contain '--model llama-3b' (got: {joined!r})"
    )


def test_container_slot_resolved_command_includes_flags(
    client_with_container_slot: TestClient,
) -> None:
    """resolved_command must include profile flags tokens."""
    from hal0.config.schema import ProfileConfig

    fake_profile = ProfileConfig(
        image="ghcr.io/hal0ai/amd-strix-halo-toolboxes:vulkan-radv-server",
        flags="--flash-attn on -ngl 999",
        mtp=False,
    )
    fake_catalog = MagicMock(profile={"vulkan-radv": fake_profile})
    with (
        patch(
            "hal0.providers.container.ContainerProvider.is_active",
            return_value=False,
        ),
        patch(
            "subprocess.run",
            return_value=MagicMock(stdout=b"inactive", returncode=3),
        ),
        patch(
            "hal0.config.loader.load_profiles_config",
            return_value=fake_catalog,
        ),
    ):
        r = client_with_container_slot.get("/api/slots")
    assert r.status_code == 200, r.text
    by_name = {e["name"]: e for e in r.json()}
    slot = by_name["gpu-chat"]
    rc = slot.get("resolved_command")
    assert isinstance(rc, list)
    # Flags should be spread into the command
    joined = " ".join(rc)
    assert "--flash-attn" in joined, "profile flags must appear in resolved_command"
    assert "-ngl" in joined, "profile flags must appear in resolved_command"


def test_profileless_slot_has_null_image_and_command(
    client_with_container_slot: TestClient,
) -> None:
    """A profile-less slot is still probed but carries no image facts:
    profile='', image=None, resolved_command=None, image_status=missing."""
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
    chat = by_name["chat"]

    assert chat["runtime"] == "container"
    assert chat["profile"] == ""
    assert chat["image"] is None
    assert chat["resolved_command"] is None
    assert chat["image_status"] == "missing"


# ── #663: actual_image + image_mismatch via running_image (podman inspect) ──────

_VULKAN_IMG = "ghcr.io/hal0ai/amd-strix-halo-toolboxes:vulkan-radv-server"
_ROCM_IMG = "ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocm-7.2.4-rocmfp4-server"


def _fake_vulkan_catalog() -> MagicMock:
    """Fake profiles catalog whose 'vulkan-radv' profile declares _VULKAN_IMG."""
    from hal0.config.schema import ProfileConfig

    prof = ProfileConfig(image=_VULKAN_IMG, flags="--flash-attn on", mtp=False)
    return MagicMock(profile={"vulkan-radv": prof})


def test_container_actual_image_no_mismatch_when_running_matches_profile(
    client_with_container_slot: TestClient,
) -> None:
    """#663: running container image == profile image -> actual_image set, image_mismatch False."""
    cat = _fake_vulkan_catalog()
    with (
        patch("hal0.providers.container.ContainerProvider.is_active", return_value=True),
        patch(
            "hal0.providers.container.ContainerProvider.health",
            new_callable=AsyncMock,
            return_value={"ok": True, "status": "healthy"},
        ),
        patch("hal0.config.loader.load_profiles_config", return_value=cat),
        patch(
            "hal0.providers.container.ContainerProvider.running_image",
            return_value=_VULKAN_IMG,
        ),
    ):
        r = client_with_container_slot.get("/api/slots")
    assert r.status_code == 200, r.text
    slot = {e["name"]: e for e in r.json()}["gpu-chat"]
    assert slot["actual_image"] == _VULKAN_IMG
    assert slot["image_mismatch"] is False


def test_container_image_mismatch_when_running_differs_from_profile(
    client_with_container_slot: TestClient,
) -> None:
    """#663: running container image != profile image -> image_mismatch True (deterministic drift)."""
    cat = _fake_vulkan_catalog()
    with (
        patch("hal0.providers.container.ContainerProvider.is_active", return_value=True),
        patch(
            "hal0.providers.container.ContainerProvider.health",
            new_callable=AsyncMock,
            return_value={"ok": True, "status": "healthy"},
        ),
        patch("hal0.config.loader.load_profiles_config", return_value=cat),
        patch(
            "hal0.providers.container.ContainerProvider.running_image",
            return_value=_ROCM_IMG,  # stale: still running the old rocm image
        ),
    ):
        r = client_with_container_slot.get("/api/slots")
    assert r.status_code == 200, r.text
    slot = {e["name"]: e for e in r.json()}["gpu-chat"]
    assert slot["actual_image"] == _ROCM_IMG
    assert slot["image_mismatch"] is True


def test_profileless_slot_actual_image_without_mismatch_key(
    client_with_container_slot: TestClient,
) -> None:
    """#663: a slot with no declared profile image still surfaces actual_image
    (the probe is universal) but carries NO image_mismatch key — there is no
    declared image to compare against."""
    with (
        patch("hal0.providers.container.ContainerProvider.is_active", return_value=True),
        patch(
            "hal0.providers.container.ContainerProvider.health",
            new_callable=AsyncMock,
            return_value={"ok": True, "status": "healthy"},
        ),
        patch(
            "hal0.providers.container.ContainerProvider.running_image",
            return_value=_VULKAN_IMG,
        ),
    ):
        r = client_with_container_slot.get("/api/slots")
    assert r.status_code == 200, r.text
    chat = {e["name"]: e for e in r.json()}["chat"]
    assert chat["actual_image"] == _VULKAN_IMG
    assert "image_mismatch" not in chat
