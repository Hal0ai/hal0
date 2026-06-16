"""Tests for /api/config/urls.

The dashboard reads this endpoint on mount to discover the right
hostnames for the API and the OpenWebUI Chat link, plus a runtime flag
saying whether the OpenWebUI unit is actually up.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_urls_returns_three_required_keys(client: TestClient) -> None:
    """All three keys land in the response, with the documented types."""
    resp = client.get("/api/config/urls")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) >= {"api", "openwebui", "openwebui_enabled"}
    assert isinstance(body["api"], str) and body["api"].startswith("http://")
    assert isinstance(body["openwebui"], str) and body["openwebui"].startswith("http://")
    assert isinstance(body["openwebui_enabled"], bool)


def test_urls_use_request_host(client: TestClient) -> None:
    """Both URLs echo the hostname the request came in on (not localhost)."""
    resp = client.get("/api/config/urls", headers={"host": "hal0-test.lan:8080"})
    assert resp.status_code == 200
    body = resp.json()
    assert "hal0-test.lan" in body["api"], body
    assert "hal0-test.lan" in body["openwebui"], body
    # LAN-direct fallback: port :3001 for the chat link.
    assert body["openwebui"].endswith(":3001")


def test_urls_api_port_honours_env(
    monkeypatch,
    client: TestClient,
) -> None:
    """HAL0_PORT shifts the api URL port but not the openwebui port."""
    monkeypatch.setenv("HAL0_PORT", "9090")
    resp = client.get("/api/config/urls")
    body = resp.json()
    assert body["api"].endswith(":9090"), body["api"]
    assert body["openwebui"].endswith(":3001"), body["openwebui"]


def test_urls_openwebui_enabled_false_when_systemctl_missing(
    monkeypatch,
    client: TestClient,
) -> None:
    """If systemctl can't be exec'd, openwebui_enabled is False (not an error)."""
    # Point PATH at an empty dir so systemctl isn't found.
    monkeypatch.setenv("PATH", "/nonexistent-path-for-tests")
    resp = client.get("/api/config/urls")
    assert resp.status_code == 200
    assert resp.json()["openwebui_enabled"] is False


def test_urls_behind_proxy_without_public_url_uses_openwebui_port(client: TestClient) -> None:
    """Proxy deploys without custom DNS still get the default :3001 link."""
    resp = client.get(
        "/api/config/urls",
        headers={
            "x-forwarded-host": "ai-dev.thinmint.dev",
            "x-forwarded-proto": "https",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["openwebui"] == "http://ai-dev.thinmint.dev:3001", body
    assert body["openwebui_enabled"] is False, body
    assert body["api"] == "https://ai-dev.thinmint.dev", body


def test_urls_public_url_env_wins_behind_proxy(
    monkeypatch,
    client: TestClient,
) -> None:
    """HAL0_OPENWEBUI_PUBLIC_URL is the canonical override for proxy deploys."""
    monkeypatch.setenv("HAL0_OPENWEBUI_PUBLIC_URL", "https://hal0-chat.example.com/")
    resp = client.get(
        "/api/config/urls",
        headers={
            "x-forwarded-host": "hal0.example.com",
            "x-forwarded-proto": "https",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    # Trailing slash stripped so links concat predictably.
    assert body["openwebui"] == "https://hal0-chat.example.com", body
    assert body["api"] == "https://hal0.example.com", body


def test_urls_public_url_env_overrides_lan_direct(
    monkeypatch,
    client: TestClient,
) -> None:
    """The env var also overrides the LAN-direct host:3001 default."""
    monkeypatch.setenv("HAL0_OPENWEBUI_PUBLIC_URL", "http://chat.lan:7000")
    resp = client.get("/api/config/urls", headers={"host": "hal0-test.lan:8080"})
    body = resp.json()
    assert body["openwebui"] == "http://chat.lan:7000", body


def test_urls_hermes_keys_present_and_hidden_by_default(client: TestClient) -> None:
    """Hermes keys are always present; hidden (loopback) without the env var.

    Hermes' dashboard binds 127.0.0.1:9119 so there's no browser-reachable
    host:port fallback — a stock install advertises no hermes link.
    """
    resp = client.get("/api/config/urls", headers={"host": "hal0-test.lan:8080"})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) >= {"hermes", "hermes_enabled"}
    assert body["hermes"] == "", body
    assert body["hermes_enabled"] is False, body


def test_urls_hermes_public_url_env_wins(
    monkeypatch,
    client: TestClient,
) -> None:
    """HAL0_HERMES_PUBLIC_URL is the canonical override for the hermes link."""
    monkeypatch.setenv("HAL0_HERMES_PUBLIC_URL", "https://hermes.example.com/")
    resp = client.get("/api/config/urls", headers={"host": "hal0-test.lan:8080"})
    assert resp.status_code == 200
    body = resp.json()
    # Trailing slash stripped so links concat predictably.
    assert body["hermes"] == "https://hermes.example.com", body
    assert body["hermes_enabled"] is True, body


def test_urls_hermes_advertised_even_behind_proxy(
    monkeypatch,
    client: TestClient,
) -> None:
    """The hermes public URL is honoured on reverse-proxy deploys too."""
    monkeypatch.setenv("HAL0_HERMES_PUBLIC_URL", "https://hermes.example.com")
    resp = client.get(
        "/api/config/urls",
        headers={
            "x-forwarded-host": "hal0.example.com",
            "x-forwarded-proto": "https",
        },
    )
    body = resp.json()
    assert body["hermes"] == "https://hermes.example.com", body
    assert body["hermes_enabled"] is True, body
