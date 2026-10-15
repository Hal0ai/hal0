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
from hal0.config.schema import SEED_PROFILES


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
        """One entry per code-shipped seed profile (config/data/seed_profiles.toml)."""
        data = client.get("/api/profiles").json()
        assert len(data) == len(SEED_PROFILES)

    def test_no_slot_hardware_flags_in_any_seed_profile(self, client: TestClient) -> None:
        """Seed flags are logical workload tuning; slots own physical facts."""
        data = client.get("/api/profiles").json()
        forbidden = {"-ngl", "--n-gpu-layers", "-dev", "--device", "--threads", "-t"}
        for item in data:
            assert not forbidden.intersection(item["flags"].split()), item["name"]

    def test_no_model_fact_flags_in_any_seed_profile(self, client: TestClient) -> None:
        """--jinja / --mmproj* / --chat-template* are the model's (ADR-0029)."""
        data = client.get("/api/profiles").json()
        for item in data:
            for token in item["flags"].split():
                assert token not in {"--jinja", "--no-mmproj", "--mmproj", "--mmproj-auto"}
                assert not token.startswith("--chat-template"), item["name"]

    def test_generic_workload_seeds_default_to_auto(self, client: TestClient) -> None:
        by_name = {item["name"]: item for item in client.get("/api/profiles").json()}
        for name in ("chat", "cpu-chat", "embedding", "reranking"):
            assert by_name[name]["seed"] is True
            assert by_name[name]["mtp"] == "auto"
            assert by_name[name]["vision"] == "auto"

    def test_flm_seed_carries_its_real_window(self, client: TestClient) -> None:
        """FLM reads the profile window as its real context (ADR-0029)."""
        data = client.get("/api/profiles").json()
        flm = next(item for item in data if item["name"] == "flm")
        assert flm["ctx_size"] == 16384
        assert flm["runtime_family"] == "flm"

    def test_kokoro_cpu_seed_present(self, client: TestClient) -> None:
        data = client.get("/api/profiles").json()
        kokoro = next(item for item in data if item["name"] == "kokoro")
        assert kokoro["mtp"] == "auto"
        # The provider supplies --model_path from the store (#2739).
        assert "--model_path" not in kokoro["flags"]

    def test_shipped_slot_seeds_exist(self, client: TestClient) -> None:
        """agent/utility/coding/brain are seeds, with the windows the slots rely on."""
        by_name = {item["name"]: item for item in client.get("/api/profiles").json()}
        assert by_name["agent"]["ctx_size"] == 65536
        assert by_name["agent"]["vision"] == "off"
        assert by_name["utility"]["ctx_size"] == 65536
        assert by_name["brain"]["ctx_size"] == 65536
        assert by_name["coding"]["ctx_size"] is None
        assert by_name["embedding"]["ctx_size"] == 4096
        assert by_name["reranking"]["ctx_size"] == 4096

    def test_seed_names_present(self, client: TestClient) -> None:
        data = client.get("/api/profiles").json()
        names = {item["name"] for item in data}
        assert names == set(SEED_PROFILES.keys())

    def test_item_has_the_contract_fields(self, client: TestClient) -> None:
        data = client.get("/api/profiles").json()
        for item in data:
            for key in (
                "name",
                "flags",
                "resolved_flags",
                "ctx_size",
                "enable_thinking",
                "vision",
                "mtp",
                "runner",
                "seed",
                "runtime_family",
                "supported_slot_types",
                "cloned_from",
                "intent",
                "tps",
                "rtf",
                "used_by",
            ):
                assert key in item, (item["name"], key)

    def test_item_drops_the_retired_fields(self, client: TestClient) -> None:
        data = client.get("/api/profiles").json()
        for item in data:
            assert not {"device_class", "backend", "quant", "image"} & set(item), item["name"]
            assert item["vision"] in {"auto", "off"}
            assert item["mtp"] in {"auto", "off"}

    def test_seed_flag_true_for_seeds(self, client: TestClient) -> None:
        """Phase C6: the UI keys immutability off the serialized seed flag."""
        data = client.get("/api/profiles").json()
        chat = next(item for item in data if item["name"] == "chat")
        assert chat["seed"] is True

    def test_resolved_flags_equals_flags_for_every_seed(self, client: TestClient) -> None:
        """The profile list never MTP-expands: resolved_flags is just flags.

        The MTP draft bundle is composed at launch from the model + runner
        (the profile's ``mtp`` switch only says whether it may be)."""
        data = client.get("/api/profiles").json()
        for item in data:
            assert item["resolved_flags"] == item["flags"], item["name"]
            assert "--spec-type" not in item["resolved_flags"], item["name"]

    def test_custom_profiles_file_used_when_present(self, tmp_hal0_home: str) -> None:
        """A custom profiles.toml is preserved AND missing seed profiles are
        additively merged in (#838): operator customisations coexist with the
        canonical seeds so upgrades pick up newly-added seed profiles."""
        profiles_path = Path(tmp_hal0_home) / "etc" / "hal0" / "profiles.toml"
        profiles_path.parent.mkdir(parents=True, exist_ok=True)
        profiles_path.write_text(
            '[profile.custom-only]\nflags = "-b 128"\nctx_size = 12288\nmtp = "off"\n',
            encoding="utf-8",
        )
        app = create_app()
        with TestClient(app) as c:
            data = c.get("/api/profiles").json()
        names = {item["name"] for item in data}
        # Operator's custom profile is preserved...
        assert "custom-only" in names
        # ...and the missing seed profiles were additively merged in (#838).
        assert set(SEED_PROFILES.keys()) <= names
        custom = next(item for item in data if item["name"] == "custom-only")
        assert custom["seed"] is False
        assert custom["ctx_size"] == 12288
        assert custom["mtp"] == "off"


# ── profiles overhaul: enriched card fields ─────────────────────────────────────


class TestEnrichedFields:
    def test_seed_items_expose_intent_bench(self, client: TestClient) -> None:
        data = client.get("/api/profiles").json()
        by_name = {p["name"]: p for p in data}
        chat = by_name["chat"]
        assert chat["intent"] == "Generic chat (fallback for unknown models)"
        assert chat["tps"] == 52.8
        assert chat["rtf"] is None
        assert chat["used_by"] == []
        assert by_name["kokoro"]["rtf"] == 0.18

    def test_create_round_trips_intent_and_the_tune(self, client: TestClient) -> None:
        body = {
            "name": "my-tuned",
            "intent": "My workload",
            "ctx_size": 24576,
            "enable_thinking": True,
            "vision": "off",
            "mtp": "off",
        }
        created = client.post("/api/profiles", json=body).json()
        assert created["intent"] == "My workload"
        assert created["ctx_size"] == 24576
        assert created["enable_thinking"] is True
        assert created["vision"] == "off"
        assert created["mtp"] == "off"
        assert created["tps"] is None
        listed = {p["name"]: p for p in client.get("/api/profiles").json()}
        assert listed["my-tuned"]["intent"] == "My workload"
        assert listed["my-tuned"]["ctx_size"] == 24576
