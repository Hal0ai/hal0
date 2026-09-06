"""Smoke tests for the hal0 FastAPI application.

Verifies that the app factory works, key endpoints respond,
and the OpenAPI schema covers all expected router prefixes.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_status_endpoint(client: TestClient) -> None:
    """GET /api/status returns 200 with name='hal0' and a non-empty version."""
    response = client.get("/api/status")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    body = response.json()
    assert body["name"] == "hal0", f"Expected name='hal0', got {body.get('name')!r}"
    assert body["version"], f"Expected non-empty version, got {body.get('version')!r}"
    # #1550/H7: build_sha is present (None or a str) even without a .git dir —
    # the field must never be missing, only possibly null.
    assert "build_sha" in body
    assert body["build_sha"] is None or isinstance(body["build_sha"], str)


def test_openapi_loads(client: TestClient) -> None:
    """GET /api/openapi.json returns 200 and lists at least one path per router prefix."""
    response = client.get("/api/openapi.json")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    schema = response.json()
    paths = schema.get("paths", {})
    assert paths, "OpenAPI schema has no paths"

    # Each of these prefixes must appear in at least one registered path.
    required_prefixes = [
        "/v1",
        "/api/slots",
        "/api/models",
        "/api/hardware",
        "/api/logs",
        "/api/settings",
        "/api/providers",
        "/api/upstreams",
        "/api/config/urls",
        "/api/updates",
        "/api/install",
    ]
    for prefix in required_prefixes:
        matched = [p for p in paths if p.startswith(prefix)]
        assert matched, (
            f"No OpenAPI path starts with '{prefix}'. "
            f"Available prefixes: {sorted({p.split('/')[1] for p in paths})}"
        )


def test_docs_page(client: TestClient) -> None:
    """GET /api/docs returns 200 (Swagger UI is mounted)."""
    response = client.get("/api/docs")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"


def test_config_urls(client: TestClient) -> None:
    """GET /api/config/urls returns hal0 API URL and openwebui URL."""
    response = client.get("/api/config/urls")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    body = response.json()
    assert "api" in body, f"Missing 'api' key in /api/config/urls response: {body}"
    assert "openwebui" in body, f"Missing 'openwebui' key in /api/config/urls response: {body}"
    assert body["api"], "api URL must be non-empty"
    assert body["openwebui"], "openwebui URL must be non-empty"
