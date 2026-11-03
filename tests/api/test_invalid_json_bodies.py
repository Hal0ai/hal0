"""Malformed bodies on hand-parsed ``/api`` routes answer 400, never 500 (#2701).

These handlers parse ``await request.json()`` themselves, so an unparseable
body or a non-object JSON value used to hit a bare ``Hal0Error`` (code
``system.internal``, HTTP 500). Every site must answer
``400 request.invalid_json``. Valid JSON with a wrong field keeps its own
``400`` code.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

_SITES: list[tuple[str, str]] = [
    ("POST", "/api/memory/add"),
    ("POST", "/api/memory/search"),
    ("POST", "/api/memory/recall"),
    ("POST", "/api/memory/delete"),
    ("PUT", "/api/memory/graph"),
    ("PUT", "/api/settings"),
    ("POST", "/api/settings/preview"),
    ("POST", "/api/settings/network"),
    ("PUT", "/api/config/models"),
    ("PUT", "/api/settings/proxmox"),
    ("POST", "/api/settings/proxmox/test"),
]


@pytest.mark.parametrize("body", [b"{bad", b"[1,2]"], ids=["unparseable", "not_an_object"])
@pytest.mark.parametrize(("method", "path"), _SITES, ids=[f"{m} {p}" for m, p in _SITES])
def test_malformed_body_is_400_invalid_json(
    isolated_client: TestClient, method: str, path: str, body: bytes
) -> None:
    resp = isolated_client.request(
        method, path, content=body, headers={"content-type": "application/json"}
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "request.invalid_json"


@pytest.mark.parametrize(
    ("path", "body", "needle"),
    [
        ("/api/memory/search", {"q": "x"}, "memory_search requires"),
        ("/api/memory/delete", {"q": "x"}, "memory_delete requires"),
    ],
    ids=["search", "delete"],
)
def test_memory_valid_json_wrong_field_is_400_validation_invalid(
    isolated_client: TestClient, path: str, body: dict[str, str], needle: str
) -> None:
    resp = isolated_client.post(path, json=body)
    assert resp.status_code == 400, resp.text
    err = resp.json()["error"]
    assert err["code"] == "validation.invalid"
    assert needle in err["message"]
