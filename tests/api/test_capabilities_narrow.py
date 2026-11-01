"""``GET /api/capabilities`` ``section`` / ``summary`` narrowing (#3161)."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api.deps import get_capability_orchestrator
from hal0.api.middleware import error_codes
from hal0.api.routes.capabilities import narrow_capabilities
from hal0.api.routes.capabilities import router as capabilities_router


def _state() -> dict[str, Any]:
    return {
        "backends": [{"id": "gpu-vulkan"}, {"id": "cpu"}],
        "backends_settled": True,
        "backends_retry_in_s": 0,
        "catalogs": {
            "embed": {
                "embed": [
                    {
                        "id": "nomic-embed",
                        "capabilities": ["embed"],
                        "size_gb": 0.14,
                        "backends": [
                            {"id": "cpu", "provider": "llama-server", "downloaded": True},
                            {"id": "gpu-vulkan", "provider": "llama-server", "downloaded": False},
                        ],
                    }
                ],
                "rerank": [],
            },
            "img": {
                "img": [
                    {
                        "id": "sdxl-lightning",
                        "display_name": "SDXL Lightning",
                        "license": "openrail",
                        "recommended": True,
                        "capabilities": ["image"],
                        "size_gb": 7.1,
                        "backends": [{"id": "gpu-vulkan", "downloaded": False}],
                    }
                ]
            },
        },
        "selections": {"embed": {"embed": {"model": "nomic-embed", "enabled": True}}},
    }


class _Orchestrator:
    async def get_state(self) -> dict[str, Any]:
        return _state()


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(capabilities_router, prefix="/api/capabilities")
    app.dependency_overrides[get_capability_orchestrator] = lambda: _Orchestrator()
    return TestClient(app, raise_server_exceptions=False)


def test_no_params_returns_state_unchanged(client: TestClient) -> None:
    assert client.get("/api/capabilities").json() == _state()


def test_helper_returns_the_same_object_when_nothing_requested() -> None:
    state = _state()
    assert narrow_capabilities(state) is state


def test_section_keeps_only_named_sections_plus_settle_keys(client: TestClient) -> None:
    body = client.get("/api/capabilities", params={"section": "selections"}).json()
    assert set(body) == {"selections", "backends_settled", "backends_retry_in_s"}
    assert body["selections"] == _state()["selections"]


def test_section_comma_list(client: TestClient) -> None:
    body = client.get("/api/capabilities", params={"section": "backends, catalogs"}).json()
    assert set(body) == {"backends", "catalogs", "backends_settled", "backends_retry_in_s"}


def test_unknown_section_is_400_with_code_and_details(client: TestClient) -> None:
    resp = client.get("/api/capabilities", params={"section": "backends,bogus"})
    assert resp.status_code == 400, resp.text
    err = resp.json()["error"]
    assert err["code"] == "capability.unknown_section"
    assert err["details"] == {"section": "bogus", "legal": ["backends", "catalogs", "selections"]}


def test_summary_pins_the_reduced_catalog_shape(client: TestClient) -> None:
    body = client.get("/api/capabilities", params={"summary": "true"}).json()
    assert body["catalogs"] == {
        "embed": {
            "embed": {
                "count": 1,
                "entries": [{"id": "nomic-embed", "downloaded": True}],
            },
            "rerank": {"count": 0, "entries": []},
        },
        "img": {
            "img": {
                "count": 1,
                "entries": [
                    {"id": "sdxl-lightning", "display_name": "SDXL Lightning", "downloaded": False}
                ],
            }
        },
    }
    state = _state()
    assert body["backends"] == state["backends"]
    assert body["selections"] == state["selections"]
    assert body["backends_settled"] is True


def test_summary_false_is_the_full_catalog(client: TestClient) -> None:
    body = client.get("/api/capabilities", params={"summary": "false"}).json()
    assert body == _state()


def test_summary_composes_with_section(client: TestClient) -> None:
    body = client.get("/api/capabilities", params={"section": "catalogs", "summary": "true"}).json()
    assert set(body) == {"catalogs", "backends_settled", "backends_retry_in_s"}
    assert body["catalogs"]["img"]["img"]["count"] == 1


def test_summary_without_catalogs_section_is_a_no_op() -> None:
    out = narrow_capabilities(_state(), section="selections", summary=True)
    assert set(out) == {"selections", "backends_settled", "backends_retry_in_s"}


def test_helper_does_not_mutate_its_input() -> None:
    state = _state()
    before = copy.deepcopy(state)
    narrow_capabilities(state, section="catalogs", summary=True)
    assert state == before
