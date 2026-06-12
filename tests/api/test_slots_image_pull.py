"""Tests for container image-pull progress (Issue #659).

Verifies:
  - ``image_status`` (present | pulling | missing) appears on container slots.
  - ``image_status=pulling`` is set when a slot_pull_job is active.
  - ``image_status=present`` is set when image_present() returns True.
  - ``image_status=missing`` is set when image_present() returns False.
  - POST /api/slots/{name}/pull returns 202 with job snapshot.
  - POST /api/slots/{name}/pull is idempotent when already in-flight.
  - GET /api/slots/{name}/pull/stream emits terminal frame when no pull is active.
  - ContainerProvider.image_present() returns True for zero exit-code, False otherwise.
  - ContainerProvider.pull_image_stream() yields completed / failed correctly.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app

# ── helpers ───────────────────────────────────────────────────────────────────


def _seed_slot_toml(home: str, name: str, lines: list[str]) -> Path:
    root = Path(home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _fake_profile_catalog():
    """Return (fake_catalog, fake_profile) for the vulkan-radv profile."""
    from hal0.config.schema import ProfileConfig

    fake_profile = ProfileConfig(
        image="ghcr.io/hal0ai/amd-strix-halo-toolboxes:vulkan-radv-server",
        flags="--flash-attn on",
        mtp=False,
    )
    return MagicMock(profile={"vulkan-radv": fake_profile}), fake_profile


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def container_app(tmp_hal0_home: str) -> FastAPI:
    """App with one container slot (gpu-chat)."""
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
    return create_app()


@pytest.fixture
def container_client(container_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(container_app) as c:
        yield c


# ── image_status tests ────────────────────────────────────────────────────────


def test_image_status_present(container_client: TestClient) -> None:
    """image_status=present when image_present() returns True."""
    fake_catalog, _ = _fake_profile_catalog()
    with (
        patch("hal0.providers.container.ContainerProvider.is_active", return_value=False),
        patch(
            "hal0.providers.container.ContainerProvider.health",
            new_callable=AsyncMock,
            return_value={"ok": False},
        ),
        patch("hal0.config.loader.load_profiles_config", return_value=fake_catalog),
        patch("hal0.providers.container.ContainerProvider.image_present", return_value=True),
    ):
        r = container_client.get("/api/slots")
    assert r.status_code == 200, r.text
    by_name = {e["name"]: e for e in r.json()}
    assert by_name["gpu-chat"]["image_status"] == "present"


def test_image_status_missing(container_client: TestClient) -> None:
    """image_status=missing when image_present() returns False."""
    fake_catalog, _ = _fake_profile_catalog()
    with (
        patch("hal0.providers.container.ContainerProvider.is_active", return_value=False),
        patch(
            "hal0.providers.container.ContainerProvider.health",
            new_callable=AsyncMock,
            return_value={"ok": False},
        ),
        patch("hal0.config.loader.load_profiles_config", return_value=fake_catalog),
        patch("hal0.providers.container.ContainerProvider.image_present", return_value=False),
    ):
        r = container_client.get("/api/slots")
    assert r.status_code == 200, r.text
    by_name = {e["name"]: e for e in r.json()}
    assert by_name["gpu-chat"]["image_status"] == "missing"


def test_image_status_pulling_when_job_active(
    container_client: TestClient,
    container_app: FastAPI,
) -> None:
    """image_status=pulling when a slot_pull_jobs entry with state=pulling exists."""
    from hal0.api.routes.slots import _ImagePullJob

    fake_catalog, _ = _fake_profile_catalog()
    job = _ImagePullJob("gpu-chat", "ghcr.io/hal0ai/test:tag")
    container_app.state.slot_pull_jobs = {"gpu-chat": job}

    with (
        patch("hal0.providers.container.ContainerProvider.is_active", return_value=False),
        patch(
            "hal0.providers.container.ContainerProvider.health",
            new_callable=AsyncMock,
            return_value={"ok": False},
        ),
        patch("hal0.config.loader.load_profiles_config", return_value=fake_catalog),
    ):
        r = container_client.get("/api/slots")
    assert r.status_code == 200, r.text
    by_name = {e["name"]: e for e in r.json()}
    assert by_name["gpu-chat"]["image_status"] == "pulling"


# ── POST /api/slots/{name}/pull tests ─────────────────────────────────────────


def test_pull_start_returns_202(container_client: TestClient) -> None:
    """POST /api/slots/{name}/pull returns 202 with a job snapshot."""
    fake_catalog, _ = _fake_profile_catalog()

    async def _noop(job, request):
        pass  # Don't actually pull — background task stub.

    with (
        patch("hal0.providers.container.ContainerProvider.is_active", return_value=False),
        patch(
            "hal0.providers.container.ContainerProvider.health",
            new_callable=AsyncMock,
            return_value={"ok": False},
        ),
        patch("hal0.config.loader.load_profiles_config", return_value=fake_catalog),
        patch("hal0.api.routes.slots._run_image_pull", side_effect=_noop),
    ):
        r = container_client.post("/api/slots/gpu-chat/pull")
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["slot_name"] == "gpu-chat"
    assert body["state"] == "pulling"
    assert "image" in body
    assert body.get("resumed") is False


def test_pull_start_idempotent(
    container_client: TestClient,
    container_app: FastAPI,
) -> None:
    """POST /api/slots/{name}/pull returns resumed=True when already in-flight."""
    from hal0.api.routes.slots import _ImagePullJob

    fake_catalog, _ = _fake_profile_catalog()
    job = _ImagePullJob("gpu-chat", "ghcr.io/hal0ai/test:tag")
    container_app.state.slot_pull_jobs = {"gpu-chat": job}

    with (
        patch("hal0.providers.container.ContainerProvider.is_active", return_value=False),
        patch(
            "hal0.providers.container.ContainerProvider.health",
            new_callable=AsyncMock,
            return_value={"ok": False},
        ),
        patch("hal0.config.loader.load_profiles_config", return_value=fake_catalog),
    ):
        r = container_client.post("/api/slots/gpu-chat/pull")
    assert r.status_code == 202, r.text
    assert r.json()["resumed"] is True


def test_pull_start_404_for_unknown_slot(container_client: TestClient) -> None:
    """POST /api/slots/{name}/pull returns 404 for an unknown slot name."""
    r = container_client.post("/api/slots/no-such-slot/pull")
    assert r.status_code == 404, r.text


# ── GET /api/slots/{name}/pull/stream tests ───────────────────────────────────


def test_pull_stream_present_when_no_job(container_client: TestClient) -> None:
    """GET /pull/stream with no active job and image present emits state=present."""
    fake_catalog, _ = _fake_profile_catalog()
    with (
        patch("hal0.providers.container.ContainerProvider.is_active", return_value=False),
        patch(
            "hal0.providers.container.ContainerProvider.health",
            new_callable=AsyncMock,
            return_value={"ok": False},
        ),
        patch("hal0.config.loader.load_profiles_config", return_value=fake_catalog),
        patch("hal0.providers.container.ContainerProvider.image_present", return_value=True),
        container_client.stream("GET", "/api/slots/gpu-chat/pull/stream") as resp,
    ):
        assert resp.status_code == 200
        lines = [ln for ln in resp.iter_lines() if ln.startswith("data:")]
    assert lines, "must emit at least one SSE data frame"
    payload = json.loads(lines[0].removeprefix("data:").strip())
    assert payload["state"] == "present"


def test_pull_stream_missing_when_no_job(container_client: TestClient) -> None:
    """GET /pull/stream with no active job and image absent emits state=missing."""
    fake_catalog, _ = _fake_profile_catalog()
    with (
        patch("hal0.providers.container.ContainerProvider.is_active", return_value=False),
        patch(
            "hal0.providers.container.ContainerProvider.health",
            new_callable=AsyncMock,
            return_value={"ok": False},
        ),
        patch("hal0.config.loader.load_profiles_config", return_value=fake_catalog),
        patch("hal0.providers.container.ContainerProvider.image_present", return_value=False),
        container_client.stream("GET", "/api/slots/gpu-chat/pull/stream") as resp,
    ):
        assert resp.status_code == 200
        lines = [ln for ln in resp.iter_lines() if ln.startswith("data:")]
    payload = json.loads(lines[0].removeprefix("data:").strip())
    assert payload["state"] == "missing"


# ── ContainerProvider.image_present() unit tests ──────────────────────────────


def test_image_present_returns_true_on_zero_exit(tmp_path: Path) -> None:
    """image_present() returns True when the runtime exits 0."""
    from hal0.providers.container import ContainerProvider

    cp = ContainerProvider()
    fake_runtime = tmp_path / "fake-runtime"
    fake_runtime.write_text("#!/bin/sh\nexit 0\n")
    fake_runtime.chmod(0o755)
    with patch("hal0.providers.container._container_runtime", return_value=str(fake_runtime)):
        assert cp.image_present("some/image:tag") is True


def test_image_present_returns_false_on_nonzero_exit(tmp_path: Path) -> None:
    """image_present() returns False when the runtime exits non-zero."""
    from hal0.providers.container import ContainerProvider

    cp = ContainerProvider()
    fake_runtime = tmp_path / "fake-runtime"
    fake_runtime.write_text("#!/bin/sh\nexit 1\n")
    fake_runtime.chmod(0o755)
    with patch("hal0.providers.container._container_runtime", return_value=str(fake_runtime)):
        assert cp.image_present("some/image:tag") is False


# ── ContainerProvider.pull_image_stream() unit tests ─────────────────────────


@pytest.mark.asyncio
async def test_pull_image_stream_completed_on_success(tmp_path: Path) -> None:
    """pull_image_stream() yields a completed frame when the pull exits 0."""
    from hal0.providers.container import ContainerProvider

    cp = ContainerProvider()
    fake_runtime = tmp_path / "fake-pull"
    fake_runtime.write_text(
        "#!/bin/sh\n"
        'echo "Pulling from library/alpine"\n'
        'echo "abc123: Pulling fs layer"\n'
        'echo "abc123: Download complete"\n'
        'echo "abc123: Pull complete"\n'
        'echo "Digest: sha256:abc"\n'
        "exit 0\n"
    )
    fake_runtime.chmod(0o755)

    chunks = []
    with patch("hal0.providers.container._container_runtime", return_value=str(fake_runtime)):
        async for chunk in cp.pull_image_stream("alpine:latest"):
            chunks.append(chunk)

    states = [c["state"] for c in chunks]
    assert "pulling" in states, "must emit at least one pulling frame"
    assert states[-1] == "completed", f"last frame must be completed, got: {states}"


@pytest.mark.asyncio
async def test_pull_image_stream_failed_on_nonzero_exit(tmp_path: Path) -> None:
    """pull_image_stream() yields a failed frame when the pull exits non-zero."""
    from hal0.providers.container import ContainerProvider

    cp = ContainerProvider()
    fake_runtime = tmp_path / "fake-pull-fail"
    fake_runtime.write_text("#!/bin/sh\nexit 1\n")
    fake_runtime.chmod(0o755)

    chunks = []
    with patch("hal0.providers.container._container_runtime", return_value=str(fake_runtime)):
        async for chunk in cp.pull_image_stream("bad/image:tag"):
            chunks.append(chunk)

    assert chunks, "must yield at least one chunk"
    assert chunks[-1]["state"] == "failed"
