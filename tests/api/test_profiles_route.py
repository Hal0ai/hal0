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
        """One entry per seed profile (7: rocm-moe + rocm-dnse split, Phase D comfyui)."""
        data = client.get("/api/profiles").json()
        assert len(data) == len(SEED_PROFILES)
        assert len(data) == 7

    def test_flm_npu_seed_present(self, client: TestClient) -> None:
        """Phase A added the flm container profile to the seeds."""
        data = client.get("/api/profiles").json()
        flm = next(item for item in data if item["name"] == "flm")
        assert flm["mtp"] is False

    def test_kokoro_cpu_seed_present(self, client: TestClient) -> None:
        """Phase B added the tts TTS profile to the seeds."""
        data = client.get("/api/profiles").json()
        kokoro = next(item for item in data if item["name"] == "tts")
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
        vulkan = next(item for item in data if item["name"] == "vulkan")
        assert vulkan["seed"] is True

    def test_device_class_values(self, client: TestClient) -> None:
        """Phase C: device_class surfaces in the route response."""
        data = client.get("/api/profiles").json()
        flm = next(item for item in data if item["name"] == "flm")
        assert flm["device_class"] == "npu"
        kokoro = next(item for item in data if item["name"] == "tts")
        assert kokoro["device_class"] == "cpu"
        vulkan = next(item for item in data if item["name"] == "vulkan")
        assert vulkan["device_class"] == "gpu"

    def test_backend_values(self, client: TestClient) -> None:
        """backend surfaces in the route response (rocm|vulkan|None)."""
        data = client.get("/api/profiles").json()
        by_name = {item["name"]: item for item in data}
        assert by_name["rocm"]["backend"] == "rocm"
        assert by_name["rocm-dnse"]["backend"] == "rocm"
        assert by_name["vulkan"]["backend"] == "vulkan"
        assert by_name["flm"]["backend"] is None
        assert by_name["tts"]["backend"] is None
        assert by_name["comfyui"]["backend"] is None

    def test_moe_rocmfp4_mtp_false(self, client: TestClient) -> None:
        data = client.get("/api/profiles").json()
        moe = next(item for item in data if item["name"] == "rocm")
        assert moe["mtp"] is False

    def test_dense_mtp_rocmfp4_mtp_true(self, client: TestClient) -> None:
        data = client.get("/api/profiles").json()
        dense = next(item for item in data if item["name"] == "rocm-dnse")
        assert dense["mtp"] is True

    def test_vulkan_std_image_contains_vulkan(self, client: TestClient) -> None:
        data = client.get("/api/profiles").json()
        vulkan = next(item for item in data if item["name"] == "vulkan")
        assert "vulkan" in vulkan["image"]

    def test_mtp_true_resolved_flags_contains_spec_type(self, client: TestClient) -> None:
        data = client.get("/api/profiles").json()
        dense = next(item for item in data if item["name"] == "rocm-dnse")
        assert "--spec-type draft-mtp" in dense["resolved_flags"]

    def test_mtp_true_resolved_flags_contains_bundle(self, client: TestClient) -> None:
        data = client.get("/api/profiles").json()
        dense = next(item for item in data if item["name"] == "rocm-dnse")
        assert MTP_FLAG_BUNDLE in dense["resolved_flags"]

    def test_mtp_false_resolved_flags_no_spec_type(self, client: TestClient) -> None:
        data = client.get("/api/profiles").json()
        moe = next(item for item in data if item["name"] == "rocm")
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


# ── profiles overhaul: enriched card fields ─────────────────────────────────────


class TestEnrichedFields:
    def test_seed_items_expose_intent_quant_bench(self, client: TestClient) -> None:
        data = client.get("/api/profiles").json()
        by_name = {p["name"]: p for p in data}
        rocm = by_name["rocm"]
        assert rocm["intent"] == "MoE agents"
        assert rocm["quant"] == "FP4"
        assert rocm["tps"] == 52.8
        assert rocm["rtf"] is None
        assert rocm["used_by"] == []
        assert by_name["tts"]["rtf"] == 0.18

    def test_create_round_trips_intent_and_quant(self, client: TestClient) -> None:
        body = {
            "name": "my-tuned",
            "image": "ghcr.io/x/y:z",
            "intent": "My workload",
            "quant": "Q5_K_M",
        }
        created = client.post("/api/profiles", json=body).json()
        assert created["intent"] == "My workload"
        assert created["quant"] == "Q5_K_M"
        assert created["tps"] is None
        listed = {p["name"]: p for p in client.get("/api/profiles").json()}
        assert listed["my-tuned"]["intent"] == "My workload"
