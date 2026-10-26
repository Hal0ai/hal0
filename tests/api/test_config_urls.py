"""Tests for /api/config/urls.

The dashboard reads this endpoint on mount to discover the right
hostnames for the API and the OpenWebUI Chat link, plus a runtime flag
saying whether the OpenWebUI unit is actually up.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

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
    # `openwebui_enabled` shells out to the *real* systemctl on whatever host
    # runs this test — on a dev box with hal0-openwebui.service genuinely
    # active it would come back True regardless of test config, leaking host
    # state into an assertion that only cares about URL shape. Stub it so
    # the test is hermetic across hosts.
    with patch(
        "hal0.api.routes.config._openwebui_is_active",
        new_callable=AsyncMock,
        return_value=False,
    ):
        resp = client.get(
            "/api/config/urls",
            headers={
                "x-forwarded-host": "ai-dev.example.com",
                "x-forwarded-proto": "https",
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["openwebui"] == "http://ai-dev.example.com:3001", body
    assert body["openwebui_enabled"] is False, body
    assert body["api"] == "https://ai-dev.example.com", body


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


def test_urls_comfyui_lan_direct_default_port_8188(client: TestClient) -> None:
    """ComfyUI's own web UI is advertised at the request host on :8188.

    The dashboard is served from :8080; ComfyUI's frontend lives on the
    runtime host's :8188, so a LAN-direct hit derives ``http://<host>:8188``.
    """
    resp = client.get("/api/config/urls", headers={"host": "hal0-test.lan:8080"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["comfyui"] == "http://hal0-test.lan:8188", body


def test_urls_comfyui_public_url_env_wins(
    monkeypatch,
    client: TestClient,
) -> None:
    """HAL0_COMFYUI_PUBLIC_URL is the canonical override.

    This is how a reverse-proxy deploy points the ComfyUI link at a clean
    HTTPS hostname (e.g. ``https://comfyui.example.com``) instead of the
    mixed-content ``http://<host>:8188`` that a browser on an HTTPS
    dashboard would block.
    """
    monkeypatch.setenv("HAL0_COMFYUI_PUBLIC_URL", "https://comfyui.example.com/")
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
    assert body["comfyui"] == "https://comfyui.example.com", body


def test_urls_comfyui_behind_proxy_without_env_uses_port_8188(client: TestClient) -> None:
    """Proxy deploys without the env var still get a host:8188 link.

    The port-stripped forwarded host keeps the link reachable on the LAN
    even before an operator declares a dedicated ComfyUI subdomain.
    """
    resp = client.get(
        "/api/config/urls",
        headers={
            "x-forwarded-host": "hal0.example.com",
            "x-forwarded-proto": "https",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["comfyui"] == "http://hal0.example.com:8188", body


_PROXY_HEADERS = {"x-forwarded-host": "hal0.example.com", "x-forwarded-proto": "https"}


def test_urls_comfyui_lan_follows_the_listen_knob(monkeypatch, client: TestClient) -> None:
    """``comfyui_lan`` says whether the derived ``http://<host>:8188`` answers off the box.

    ADR-0041: it is true under ``[network].comfyui_listen = "lan"`` (the default) and false under
    ``"loopback"``, in every response shape, and independent of ``comfyui_public``.
    """
    monkeypatch.delenv("HAL0_COMFYUI_PUBLIC_URL", raising=False)
    monkeypatch.delenv("HAL0_OPENWEBUI_PUBLIC_URL", raising=False)
    for mode, expected in (("lan", True), ("loopback", False)):
        monkeypatch.setattr("hal0.comfyui.reach.comfyui_listen", lambda m=mode: m)
        for headers in ({"host": "hal0-test.lan:8080"}, _PROXY_HEADERS):
            body = client.get("/api/config/urls", headers=headers).json()
            assert body["comfyui_lan"] is expected, (mode, body)
            assert body["comfyui_public"] is False, body
        monkeypatch.setenv("HAL0_OPENWEBUI_PUBLIC_URL", "https://chat.example.com")
        body = client.get("/api/config/urls", headers={"host": "hal0-test.lan:8080"}).json()
        assert body["comfyui_lan"] is expected, (mode, body)
        monkeypatch.delenv("HAL0_OPENWEBUI_PUBLIC_URL", raising=False)


def test_urls_comfyui_public_false_without_env(monkeypatch, client: TestClient) -> None:
    """``comfyui_public`` is false while the link is only the one derived from the request host.

    Under ``[network].comfyui_listen = "loopback"`` that derived link does not answer from another
    machine. The flag is how the dashboard tells it from a URL the operator declared.
    """

    monkeypatch.delenv("HAL0_COMFYUI_PUBLIC_URL", raising=False)
    monkeypatch.delenv("HAL0_OPENWEBUI_PUBLIC_URL", raising=False)
    for headers in ({"host": "hal0-test.lan:8080"}, _PROXY_HEADERS):
        body = client.get("/api/config/urls", headers=headers).json()
        assert body["comfyui_public"] is False, body


def test_urls_comfyui_public_true_when_env_set(monkeypatch, client: TestClient) -> None:
    """HAL0_COMFYUI_PUBLIC_URL set means the link is the operator's, in every response shape."""
    monkeypatch.setenv("HAL0_COMFYUI_PUBLIC_URL", "https://comfyui.example.com")
    monkeypatch.delenv("HAL0_OPENWEBUI_PUBLIC_URL", raising=False)
    for headers in ({"host": "hal0-test.lan:8080"}, _PROXY_HEADERS):
        body = client.get("/api/config/urls", headers=headers).json()
        assert body["comfyui"] == "https://comfyui.example.com", body
        assert body["comfyui_public"] is True, body

    # The OpenWebUI public-URL branch builds its own payload.
    monkeypatch.setenv("HAL0_OPENWEBUI_PUBLIC_URL", "https://chat.example.com")
    for headers in ({"host": "hal0-test.lan:8080"}, _PROXY_HEADERS):
        body = client.get("/api/config/urls", headers=headers).json()
        assert body["comfyui_public"] is True, body


def test_urls_comfyui_public_false_for_a_blank_env(monkeypatch, client: TestClient) -> None:
    """A blank value (or a lone slash, which the link trims to nothing) declares no URL."""
    monkeypatch.delenv("HAL0_OPENWEBUI_PUBLIC_URL", raising=False)
    for value in ("", "   ", " / "):
        monkeypatch.setenv("HAL0_COMFYUI_PUBLIC_URL", value)
        body = client.get("/api/config/urls", headers={"host": "hal0-test.lan:8080"}).json()
        assert body["comfyui"] == "http://hal0-test.lan:8188", body
        assert body["comfyui_public"] is False, body
