"""Tests for GET /api/profiles.

Targeted file run only (full suite hangs):
    ~/dev/hal0/.venv/bin/python -m pytest tests/api/test_profiles_route.py -q
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.config.schema import MTP_FLAG_BUNDLE, SEED_PROFILES


@pytest.fixture
def app(tmp_hal0_home: str) -> FastAPI:
    """Fresh app; tmp_hal0_home means no profiles.toml → seeds returned."""
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


# ── GET /api/profiles ─────────────────────────────────────────────────────────


class TestListProfiles:
    def test_returns_200(self, client: TestClient) -> None:
        resp = client.get("/api/profiles")
        assert resp.status_code == 200

    def test_returns_list(self, client: TestClient) -> None:
        data = client.get("/api/profiles").json()
        assert isinstance(data, list)

    def test_returns_seed_profiles(self, client: TestClient) -> None:
        """One entry per seed profile (6 as of Phase D: comfyui joined)."""
        data = client.get("/api/profiles").json()
        assert len(data) == len(SEED_PROFILES)
        assert len(data) == 6

    def test_flm_npu_seed_present(self, client: TestClient) -> None:
        """Phase A added the flm-npu container profile to the seeds."""
        data = client.get("/api/profiles").json()
        flm = next(item for item in data if item["name"] == "flm-npu")
        assert flm["mtp"] is False

    def test_kokoro_cpu_seed_present(self, client: TestClient) -> None:
        """Phase B added the kokoro-cpu TTS profile to the seeds."""
        data = client.get("/api/profiles").json()
        kokoro = next(item for item in data if item["name"] == "kokoro-cpu")
        assert kokoro["mtp"] is False
        assert "--model_path" in kokoro["flags"]

    def test_seed_names_present(self, client: TestClient) -> None:
        data = client.get("/api/profiles").json()
        names = {item["name"] for item in data}
        assert names == set(SEED_PROFILES.keys())

    def test_item_has_required_fields(self, client: TestClient) -> None:
        data = client.get("/api/profiles").json()
        for item in data:
            assert "name" in item
            assert "image" in item
            assert "flags" in item
            assert "mtp" in item
            assert "device_class" in item
            assert "resolved_flags" in item
            assert "seed" in item

    def test_seed_flag_true_for_seeds(self, client: TestClient) -> None:
        """Phase C6: the UI keys immutability off the serialized seed flag."""
        data = client.get("/api/profiles").json()
        vulkan = next(item for item in data if item["name"] == "vulkan-std")
        assert vulkan["seed"] is True

    def test_device_class_values(self, client: TestClient) -> None:
        """Phase C: device_class surfaces in the route response."""
        data = client.get("/api/profiles").json()
        flm = next(item for item in data if item["name"] == "flm-npu")
        assert flm["device_class"] == "npu"
        kokoro = next(item for item in data if item["name"] == "kokoro-cpu")
        assert kokoro["device_class"] == "cpu"
        vulkan = next(item for item in data if item["name"] == "vulkan-std")
        assert vulkan["device_class"] == "gpu"

    def test_moe_rocmfp4_mtp_false(self, client: TestClient) -> None:
        data = client.get("/api/profiles").json()
        moe = next(item for item in data if item["name"] == "moe-rocmfp4")
        assert moe["mtp"] is False

    def test_dense_mtp_rocmfp4_mtp_true(self, client: TestClient) -> None:
        data = client.get("/api/profiles").json()
        dense = next(item for item in data if item["name"] == "dense-mtp-rocmfp4")
        assert dense["mtp"] is True

    def test_vulkan_std_image_contains_vulkan(self, client: TestClient) -> None:
        data = client.get("/api/profiles").json()
        vulkan = next(item for item in data if item["name"] == "vulkan-std")
        assert "vulkan" in vulkan["image"]

    def test_mtp_true_resolved_flags_contains_spec_type(self, client: TestClient) -> None:
        data = client.get("/api/profiles").json()
        dense = next(item for item in data if item["name"] == "dense-mtp-rocmfp4")
        assert "--spec-type draft-mtp" in dense["resolved_flags"]

    def test_mtp_true_resolved_flags_contains_bundle(self, client: TestClient) -> None:
        data = client.get("/api/profiles").json()
        dense = next(item for item in data if item["name"] == "dense-mtp-rocmfp4")
        assert MTP_FLAG_BUNDLE in dense["resolved_flags"]

    def test_mtp_false_resolved_flags_no_spec_type(self, client: TestClient) -> None:
        data = client.get("/api/profiles").json()
        moe = next(item for item in data if item["name"] == "moe-rocmfp4")
        assert "--spec-type" not in moe["resolved_flags"]

    def test_mtp_false_resolved_flags_equals_flags(self, client: TestClient) -> None:
        data = client.get("/api/profiles").json()
        for item in data:
            if not item["mtp"]:
                assert item["resolved_flags"] == item["flags"].strip()

    def test_custom_profiles_file_used_when_present(self, tmp_hal0_home: str) -> None:
        """When profiles.toml is present, its contents are used (not seeds)."""
        profiles_path = Path(tmp_hal0_home) / "etc" / "hal0" / "profiles.toml"
        profiles_path.parent.mkdir(parents=True, exist_ok=True)
        profiles_path.write_text(
            "[profile.custom-only]\n"
            'image = "ghcr.io/hal0ai/test:custom"\n'
            'flags = "-b 128"\n'
            "mtp = false\n",
            encoding="utf-8",
        )
        app = create_app()
        with TestClient(app) as c:
            data = c.get("/api/profiles").json()
        names = {item["name"] for item in data}
        assert names == {"custom-only"}
        custom = next(item for item in data if item["name"] == "custom-only")
        assert custom["seed"] is False
