"""Tests for hal0.api._redact — shared config-echo redaction (#553).

Every config-echoing endpoint (settings, lemonade config, upstreams,
secrets) routes its response through :func:`redact_config` so a key
whose NAME matches a sensitive regex is returned masked, with a ``set``
flag carrying the "is it configured" bit. This file pins down that
behaviour at the helper level — endpoint-level wiring is asserted
alongside the existing route tests in test_settings_routes.py and
test_upstream_dedup.py.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.api._redact import (
    is_sensitive_key,
    redact_config,
    redact_value,
)

# ── is_sensitive_key ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "key",
    [
        "OPENROUTER_API_KEY",
        "api_key",
        "Api_Key",
        "HF_TOKEN",
        "hf_token",
        "TOKENIZER_ID",  # TOKEN as substring
        "password",
        "PASSWORD",
        "PRIVATE_KEY",
        "private_key",
        "ENCRYPTION_KEY",
        "encryption_key",
        "SALT",
        "salt",
        "secret",
        "SECRET_KEY",
    ],
)
def test_is_sensitive_key_matches_documented_patterns(key: str) -> None:
    """Every pattern from the issue spec matches, case-insensitive."""
    assert is_sensitive_key(key), key


@pytest.mark.parametrize(
    "key",
    [
        "ctx_size",
        "port",
        "url",
        "host",
        "name",
        "kind",
        "auth_value_env",  # env-var NAME, not the secret itself
        "auth_style",
        "models",
        "enabled",
    ],
)
def test_is_sensitive_key_leaves_plain_keys_alone(key: str) -> None:
    """Non-sensitive config keys are not flagged (no over-redaction)."""
    assert not is_sensitive_key(key), key


# ── redact_value ───────────────────────────────────────────────────────────


def test_redact_value_masks_nonempty_token() -> None:
    """A non-empty sensitive value → MASK + set=True."""
    out = redact_value("sk-abc123")
    assert out == {"value": "***REDACTED***", "set": True}


def test_redact_value_empty_string_yields_set_false() -> None:
    """An empty string is treated as 'unset' so the UI can render a blank slot."""
    assert redact_value("") == {"value": "***REDACTED***", "set": False}


def test_redact_value_none_yields_set_false() -> None:
    """A None value is treated as 'unset'."""
    assert redact_value(None) == {"value": "***REDACTED***", "set": False}


def test_redact_value_zero_is_treated_as_set() -> None:
    """A non-None falsy value (0, False) still counts as 'set' — only the
    empty-string / None cases are 'unset'."""
    assert redact_value(0) == {"value": "***REDACTED***", "set": True}
    assert redact_value(False) == {"value": "***REDACTED***", "set": True}


# ── redact_config (flat) ───────────────────────────────────────────────────


def test_redact_config_token_key_masked_with_set_true() -> None:
    """Acceptance criterion #1: a known token-bearing key comes back masked,
    with ``set=true``."""
    out = redact_config({"OPENROUTER_API_KEY": "sk-abc"})
    assert out == {"OPENROUTER_API_KEY": {"value": "***REDACTED***", "set": True}}


def test_redact_config_plain_key_passes_through_unmasked() -> None:
    """A non-sensitive key (e.g. ``ctx_size``) is echoed verbatim."""
    out = redact_config({"ctx_size": 4096, "port": 8080})
    assert out == {"ctx_size": 4096, "port": 8080}


def test_redact_config_empty_sensitive_key_yields_set_false() -> None:
    """An empty sensitive value is masked with ``set=false`` so the UI can
    render the slot as 'not configured' without ever receiving the secret."""
    out = redact_config({"API_KEY": ""})
    assert out == {"API_KEY": {"value": "***REDACTED***", "set": False}}


def test_redact_config_does_not_mutate_input() -> None:
    """The input dict is not mutated — redaction is a pure projection."""
    src = {"OPENROUTER_API_KEY": "sk-abc", "ctx_size": 4096}
    redact_config(src)
    assert src == {"OPENROUTER_API_KEY": "sk-abc", "ctx_size": 4096}


# ── redact_config (nested) ─────────────────────────────────────────────────


def test_redact_config_walks_nested_dicts() -> None:
    """Nested dicts are scrubbed: any sensitive key at any depth is masked."""
    out = redact_config(
        {
            "providers": {
                "openrouter": {
                    "api_key": "sk-abc",
                    "base_url": "https://openrouter.ai",
                },
            },
        },
    )
    assert out["providers"]["openrouter"]["api_key"] == {
        "value": "***REDACTED***",
        "set": True,
    }
    assert out["providers"]["openrouter"]["base_url"] == "https://openrouter.ai"


def test_redact_config_walks_lists_of_dicts() -> None:
    """Lists of dicts are walked element-by-element.

    Note the container key (``upstreams``) is deliberately NON-sensitive:
    a sensitive container key (e.g. ``secrets``) is over-redacted wholesale
    by design (see test_redact_sensitive_container_masks_wholesale), so it
    would never reach the list. We want to exercise list recursion here.
    """
    out = redact_config(
        {
            "upstreams": [
                {"name": "OPENAI", "token": "sk-1"},
                {"name": "ANTHROPIC", "token": ""},
            ],
        },
    )
    assert out["upstreams"][0]["token"] == {"value": "***REDACTED***", "set": True}
    assert out["upstreams"][1]["token"] == {"value": "***REDACTED***", "set": False}
    assert out["upstreams"][0]["name"] == "OPENAI"  # plain key passes through


def test_redact_sensitive_container_masks_wholesale() -> None:
    """A sensitive *container* key over-redacts its whole value (by design).

    ``re.search`` on the key name means a container named ``secrets`` is
    masked wholesale rather than recursed — the conservative behaviour the
    spec asks for (never leak a secret), accepting that structure under
    such a key is lost.
    """
    out = redact_config({"secrets": [{"token": "sk-1"}]})
    assert out["secrets"] == {"value": "***REDACTED***", "set": True}


def test_redact_config_list_of_scalars_passes_through() -> None:
    """A list of scalars is not masked — only keyed containers are scrubbed."""
    assert redact_config({"models": ["a", "b", "c"]}) == {"models": ["a", "b", "c"]}


def test_redact_config_scalars_returned_verbatim() -> None:
    """Scalars at the root are passed through (helper expects a dict/list)."""
    assert redact_config(42) == 42
    assert redact_config("hello") == "hello"
    assert redact_config(None) is None


# ── integration: settings endpoint echoes masked secrets ───────────────────


@pytest.fixture
def isolated_client(tmp_hal0_home: str) -> Iterator[TestClient]:
    """TestClient with writes isolated under tmp_hal0_home.

    Mirrors the pattern in tests/api/test_settings_routes.py — the
    shared ``client`` fixture instantiates the app before tmp_hal0_home
    is set, so a PUT-driven test would write to /etc/hal0.
    """
    app: FastAPI = create_app()
    with TestClient(app) as c:
        yield c


def test_settings_get_redacts_sensitive_keys(
    isolated_client: TestClient,
) -> None:
    """End-to-end: PUT a sensitive-named extra-allow field, then GET — the
    echoed config must mask the value, never return it in plaintext.

    Hal0Config uses ``extra="allow"`` at the top level (forward-compat for
    future tables) so a top-level ``api_key`` is accepted by the
    validator and round-trips through the file. The redaction pass then
    catches the key on the way out.
    """
    secret = "sk-not-a-real-key-12345"
    put = isolated_client.put("/api/settings", json={"api_key": secret})
    assert put.status_code == 200, put.text

    r = isolated_client.get("/api/settings")
    assert r.status_code == 200, r.text
    body = r.json()

    # The sensitive field is present, masked, with set=True.
    assert "api_key" in body, body
    assert body["api_key"] == {"value": "***REDACTED***", "set": True}
    # Plaintext is never echoed.
    assert secret not in str(body)


def test_settings_get_empty_sensitive_key_yields_set_false(
    isolated_client: TestClient,
) -> None:
    """Empty sensitive value comes back masked with set=false (so the UI
    can render the slot as 'not configured')."""
    put = isolated_client.put("/api/settings", json={"openrouter_api_key": ""})
    assert put.status_code == 200, put.text

    r = isolated_client.get("/api/settings")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["openrouter_api_key"] == {"value": "***REDACTED***", "set": False}


def test_upstreams_serialize_redacts_api_key_if_present() -> None:
    """If an Upstream entry carries an api_key in its extra-allow dict, the
    serialize helper masks it before it leaves the API. This is a pure
    unit test against the redact_config helper as applied to the
    upstream-shape dict — the route's serializer is the integration
    surface; the route itself only stores the env-var NAME, not a value.
    """
    upstream_dict = {
        "name": "openrouter",
        "kind": "remote",
        "url": "https://openrouter.ai/api/v1",
        "auth_style": "bearer",
        "auth_value_env": "OPENROUTER_API_KEY",
        "api_key": "sk-abc",  # someone put this in their toml via extra="allow"
        "models": [],
    }
    out = redact_config(upstream_dict)
    assert out["name"] == "openrouter"
    assert out["auth_value_env"] == "OPENROUTER_API_KEY"  # env-var NAME, not value
    assert out["api_key"] == {"value": "***REDACTED***", "set": True}
    assert out["models"] == []
