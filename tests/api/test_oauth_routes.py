"""Tests for the /api/oauth router (agent-driven OAuth passthrough, study 3.3).

Covers the registry listing, start (PKCE + state issuance), callback
(state validation, replay rejection, provider-mismatch rejection, the
actual token exchange), status, disconnect (+ best-effort revoke), and the
client-secret write-only surface. Outbound provider HTTP calls are faked
via a stand-in for ``httpx.AsyncClient`` — no real network access.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from hal0.oauth import store as oauth_store


def _api_env_path(home: str) -> Path:
    return Path(home) / "etc" / "hal0" / "api.env"


def _registry_path(home: str) -> Path:
    return Path(home) / "etc" / "hal0" / "oauth-providers.toml"


def _set_client_id(home: str, provider_id: str, client_id: str) -> None:
    """Seed the registry (via a real load) then hand-patch one provider's client_id."""
    from hal0.oauth.providers import load_providers

    path = _registry_path(home)
    load_providers(path=path)  # seeds the shipped default if missing
    text = path.read_text(encoding="utf-8")
    # Each provider block starts with `id = "<id>"` immediately followed by
    # its `client_id = ""` line further down; simplest robust edit is a
    # per-block string replace scoped to right after this provider's id.
    marker = f'id = "{provider_id}"'
    idx = text.index(marker)
    block_end = text.index("[[providers]]", idx + 1) if "[[providers]]" in text[idx + 1 :] else len(text)
    block = text[idx:block_end]
    patched_block = block.replace('client_id = ""', f'client_id = "{client_id}"')
    path.write_text(text[:idx] + patched_block + text[block_end:], encoding="utf-8")


def _configure_google(home: str) -> None:
    """Google requires a client secret too — wire both in one place."""
    _set_client_id(home, "google", "g-client")
    oauth_store.save_client_secret("google", "g-secret", api_env=_api_env_path(home))


class _FakeResponse:
    def __init__(self, status_code: int = 200, json_data: dict | None = None) -> None:
        self.status_code = status_code
        self._json = json_data or {}

    def json(self) -> dict:
        return self._json


class _FakeAsyncClient:
    """Stand-in for httpx.AsyncClient — records calls, returns a canned token response."""

    calls: ClassVar[list[tuple[str, dict]]] = []
    response: ClassVar[_FakeResponse] = _FakeResponse(
        200, {"access_token": "at-123", "refresh_token": "rt-456", "expires_in": 3600, "scope": "calendar"}
    )

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *args) -> bool:
        return False

    async def post(self, url: str, data: dict | None = None, headers: dict | None = None) -> _FakeResponse:
        type(self).calls.append((url, data or {}))
        return type(self).response


@pytest.fixture(autouse=True)
def _fake_httpx(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.response = _FakeResponse(
        200, {"access_token": "at-123", "refresh_token": "rt-456", "expires_in": 3600, "scope": "calendar"}
    )
    monkeypatch.setattr("httpx.AsyncClient", _FakeAsyncClient)
    # The driver-env refresh after connect/disconnect shells out to the
    # hal0-agentenv seam when unprivileged; make it a no-op in tests.
    monkeypatch.setattr("hal0.api.routes.oauth._refresh_hermes_driver_env", lambda: None)


def test_list_providers_seeds_default_registry(client: TestClient) -> None:
    r = client.get("/api/oauth/providers")
    assert r.status_code == 200, r.text
    ids = {p["id"] for p in r.json()["providers"]}
    assert {"google", "spotify", "github"} <= ids
    google = next(p for p in r.json()["providers"] if p["id"] == "google")
    assert google["connected"] is False
    assert google["configured"] is False  # no client_id set yet


def test_start_fails_when_not_configured(client: TestClient) -> None:
    r = client.post("/api/oauth/google/start")
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "oauth.not_configured"


def test_start_unknown_provider_404(client: TestClient) -> None:
    r = client.post("/api/oauth/nope/start")
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "oauth.provider_not_found"


def test_start_returns_authorize_url_with_state_and_pkce(client: TestClient, tmp_hal0_home: str) -> None:
    _set_client_id(tmp_hal0_home, "spotify", "client-abc")  # spotify: pkce=true, no secret required

    r = client.post("/api/oauth/spotify/start")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider_id"] == "spotify"
    parsed = urlsplit(body["authorize_url"])
    qs = parse_qs(parsed.query)
    assert qs["state"] == [body["state"]]
    assert qs["client_id"] == ["client-abc"]
    assert "code_challenge" in qs
    assert qs["code_challenge_method"] == ["S256"]


def test_start_requires_client_secret_when_provider_needs_one(client: TestClient, tmp_hal0_home: str) -> None:
    _set_client_id(tmp_hal0_home, "github", "gh-client")  # github requires_client_secret=true

    r = client.post("/api/oauth/github/start")

    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "oauth.not_configured"


def test_callback_with_unknown_state_is_rejected(client: TestClient, tmp_hal0_home: str) -> None:
    r = client.get("/api/oauth/google/callback", params={"code": "abc", "state": "never-issued"})
    assert r.status_code == 400
    assert not oauth_store.is_connected("google", api_env=_api_env_path(tmp_hal0_home))


def test_callback_provider_error_param_is_reported(client: TestClient) -> None:
    r = client.get("/api/oauth/google/callback", params={"error": "access_denied", "state": "whatever"})
    assert r.status_code == 400
    assert "access_denied" in r.text


def test_full_start_to_callback_flow_stores_token(client: TestClient, tmp_hal0_home: str) -> None:
    _configure_google(tmp_hal0_home)

    start = client.post("/api/oauth/google/start")
    state = start.json()["state"]

    callback = client.get("/api/oauth/google/callback", params={"code": "auth-code-xyz", "state": state})

    assert callback.status_code == 200, callback.text
    assert "Connected" in callback.text
    api_env = _api_env_path(tmp_hal0_home)
    assert oauth_store.is_connected("google", api_env=api_env) is True
    token = oauth_store.load_token("google", api_env=api_env)
    assert token.access_token == "at-123"
    # The exchanged code must never appear stored verbatim anywhere queryable.
    assert "auth-code-xyz" not in api_env.read_text(encoding="utf-8")


def test_callback_state_is_single_use_replay_rejected(client: TestClient, tmp_hal0_home: str) -> None:
    _configure_google(tmp_hal0_home)
    start = client.post("/api/oauth/google/start")
    state = start.json()["state"]

    first = client.get("/api/oauth/google/callback", params={"code": "auth-code-xyz", "state": state})
    second = client.get("/api/oauth/google/callback", params={"code": "auth-code-xyz", "state": state})

    assert first.status_code == 200
    assert second.status_code == 400


def test_callback_provider_path_must_match_nonces_provider(client: TestClient, tmp_hal0_home: str) -> None:
    _configure_google(tmp_hal0_home)
    _set_client_id(tmp_hal0_home, "spotify", "s-client")
    start = client.post("/api/oauth/google/start")
    state = start.json()["state"]

    # Same state, but hitting spotify's callback path — must be refused.
    r = client.get("/api/oauth/spotify/callback", params={"code": "x", "state": state})

    assert r.status_code == 400
    assert oauth_store.is_connected("spotify", api_env=_api_env_path(tmp_hal0_home)) is False


def test_callback_missing_code_is_rejected(client: TestClient, tmp_hal0_home: str) -> None:
    _configure_google(tmp_hal0_home)
    start = client.post("/api/oauth/google/start")
    state = start.json()["state"]

    r = client.get("/api/oauth/google/callback", params={"state": state})

    assert r.status_code == 400


def test_exchange_failure_surfaces_502(client: TestClient, tmp_hal0_home: str) -> None:
    _FakeAsyncClient.response = _FakeResponse(400, {"error": "invalid_grant"})
    _configure_google(tmp_hal0_home)
    start = client.post("/api/oauth/google/start")
    state = start.json()["state"]

    r = client.get("/api/oauth/google/callback", params={"code": "bad-code", "state": state})

    assert r.status_code == 502
    assert "invalid_grant" not in r.text  # never echo the provider's raw error body


def test_status_reflects_connection(client: TestClient, tmp_hal0_home: str) -> None:
    _configure_google(tmp_hal0_home)
    start = client.post("/api/oauth/google/start")
    client.get("/api/oauth/google/callback", params={"code": "c", "state": start.json()["state"]})

    r = client.get("/api/oauth/google/status")

    assert r.status_code == 200
    assert r.json()["connected"] is True
    assert r.json()["expired"] is False


def test_disconnect_removes_token_and_calls_revoke(client: TestClient, tmp_hal0_home: str) -> None:
    _configure_google(tmp_hal0_home)
    start = client.post("/api/oauth/google/start")
    client.get("/api/oauth/google/callback", params={"code": "c", "state": start.json()["state"]})
    _FakeAsyncClient.calls = []

    r = client.delete("/api/oauth/google")

    assert r.status_code == 204
    assert oauth_store.is_connected("google", api_env=_api_env_path(tmp_hal0_home)) is False
    revoke_calls = [c for c in _FakeAsyncClient.calls if "revoke" in c[0]]
    assert len(revoke_calls) == 1


def test_disconnect_when_never_connected_is_idempotent(client: TestClient) -> None:
    r = client.delete("/api/oauth/google")
    assert r.status_code == 204


def test_disconnect_unknown_provider_404(client: TestClient) -> None:
    r = client.delete("/api/oauth/nope")
    assert r.status_code == 404


def test_set_client_secret_never_echoed(client: TestClient, tmp_hal0_home: str) -> None:
    r = client.post("/api/oauth/github/client-secret", json={"value": "super-secret-value"})
    assert r.status_code == 204
    assert "super-secret-value" not in r.text

    listing = client.get("/api/oauth/providers")
    assert "super-secret-value" not in listing.text
    github = next(p for p in listing.json()["providers"] if p["id"] == "github")
    assert github["has_client_secret"] is True


def test_set_client_secret_rejects_control_characters(client: TestClient) -> None:
    r = client.post("/api/oauth/github/client-secret", json={"value": "bad\nvalue"})
    assert r.status_code == 400


def test_ssrf_guard_blocks_private_authorize_url(client: TestClient, tmp_hal0_home: str) -> None:
    registry = _registry_path(tmp_hal0_home)
    registry.write_text(
        'schema_version = "hal0.oauth-providers.v1"\n'
        "[[providers]]\n"
        'id = "evil"\n'
        'name = "Evil"\n'
        'skill_id = "evil"\n'
        'authorize_url = "http://127.0.0.1:9999/auth"\n'
        'token_url = "https://example.com/token"\n'
        "pkce = false\n"
        'client_id = "x"\n',
        encoding="utf-8",
    )

    r = client.post("/api/oauth/evil/start")

    assert r.status_code == 400
    assert r.json()["error"]["code"] == "mcp.ssrf_blocked"
