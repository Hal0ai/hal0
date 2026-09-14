"""Tests for OAuth token/client-secret storage (through api.env, never logged)."""

from __future__ import annotations

import time
from pathlib import Path

from hal0.oauth import store
from hal0.oauth.store import OAuthToken


def _api_env(tmp_path: Path) -> Path:
    return tmp_path / "api.env"


def _token(**overrides) -> OAuthToken:
    defaults = dict(
        access_token="access-xyz",
        refresh_token="refresh-abc",
        expires_at=time.time() + 3600,
        scope="calendar",
        token_type="Bearer",
    )
    defaults.update(overrides)
    return OAuthToken(**defaults)


def test_save_and_load_round_trips(tmp_path: Path) -> None:
    api_env = _api_env(tmp_path)
    token = _token()

    store.save_token("google", token, api_env=api_env)
    loaded = store.load_token("google", api_env=api_env)

    assert loaded == token


def test_is_connected_true_after_save_false_after_delete(tmp_path: Path) -> None:
    api_env = _api_env(tmp_path)
    store.save_token("google", _token(), api_env=api_env)
    assert store.is_connected("google", api_env=api_env) is True

    assert store.delete_token("google", api_env=api_env) is True
    assert store.is_connected("google", api_env=api_env) is False


def test_delete_nonexistent_returns_false(tmp_path: Path) -> None:
    api_env = _api_env(tmp_path)
    assert store.delete_token("google", api_env=api_env) is False


def test_load_returns_none_when_not_set(tmp_path: Path) -> None:
    api_env = _api_env(tmp_path)
    assert store.load_token("google", api_env=api_env) is None


def test_connected_provider_ids_lists_only_token_keys(tmp_path: Path) -> None:
    api_env = _api_env(tmp_path)
    store.save_token("google", _token(), api_env=api_env)
    store.save_client_secret("github", "shh-secret", api_env=api_env)

    ids = store.connected_provider_ids(api_env=api_env)

    assert ids == ["google"]  # the client-secret-only entry must not appear


def test_driver_env_lines_emit_the_stored_json_verbatim(tmp_path: Path) -> None:
    api_env = _api_env(tmp_path)
    token = _token(expires_at=1234.5)
    store.save_token("google", token, api_env=api_env)

    lines = store.driver_env_lines(api_env=api_env)

    assert len(lines) == 1
    key, _, value = lines[0].partition("=")
    assert key == "HAL0_OAUTH_GOOGLE_TOKEN"
    assert OAuthToken.from_json(value) == token


def test_provider_id_with_hyphen_normalizes_to_underscore_env_key(tmp_path: Path) -> None:
    api_env = _api_env(tmp_path)
    store.save_token("google-workspace", _token(), api_env=api_env)

    assert store.is_connected("google-workspace", api_env=api_env) is True
    assert "google-workspace" in store.connected_provider_ids(api_env=api_env)


def test_client_secret_round_trips_and_is_separate_from_token(tmp_path: Path) -> None:
    api_env = _api_env(tmp_path)
    store.save_client_secret("github", "top-secret", api_env=api_env)

    assert store.has_client_secret("github", api_env=api_env) is True
    assert store.load_client_secret("github", api_env=api_env) == "top-secret"
    assert store.is_connected("github", api_env=api_env) is False


def test_stored_value_never_appears_in_plaintext_on_disk(tmp_path: Path) -> None:
    """The api.env writer quotes + escapes; sanity-check nothing round-trips
    to a plain unquoted secret line an operator might mistake for config."""
    api_env = _api_env(tmp_path)
    store.save_client_secret("github", "sekrit-value", api_env=api_env)

    content = api_env.read_text(encoding="utf-8")
    assert 'HAL0_OAUTH_GITHUB_CLIENT_SECRET="sekrit-value"' in content
