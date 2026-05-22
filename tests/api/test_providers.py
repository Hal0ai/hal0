"""Tests for the /api/providers credential write route (Phase 8 closeout).

Covers POST /api/providers/{name}/credentials:

  * happy path: writes ``key="value"`` line to api.env atomically;
    response redacts the value; an audit row is emitted.
  * validation: rejects keys that don't match the env-var grammar
    (lowercase, leading digit, shell metacharacters).
  * binding: rejects key that doesn't match the upstream's declared
    ``auth_value_env`` so a typo'd key can't land in api.env.
  * unknown upstream → 404.
  * idempotent: re-writing replaces the existing line, doesn't append.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.upstreams.registry import Upstream


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    """TestClient with the openrouter upstream registered.

    The upstream registry is populated during the lifespan startup hook,
    so we must enter the TestClient context first and then upsert the
    test fixture onto ``app.state.upstreams``.
    """
    with TestClient(app) as c:
        upstream = Upstream(
            name="openrouter",
            kind="remote",
            url="https://openrouter.ai/api/v1",
            auth_style="bearer",
            auth_value_env="OPENROUTER_API_KEY",
        )
        app.state.upstreams.upsert(upstream)
        yield c


def _api_env_path(home: str) -> Path:
    """Resolve the api.env file inside the tmp_hal0_home sandbox."""
    return Path(home) / "etc" / "hal0" / "api.env"


def test_credential_write_persists_key_and_redacts_value(
    client: TestClient,
    tmp_hal0_home: str,
) -> None:
    response = client.post(
        "/api/providers/openrouter/credentials",
        json={"key": "OPENROUTER_API_KEY", "value": "sk-or-secret-xyz"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["name"] == "openrouter"
    assert body["key"] == "OPENROUTER_API_KEY"
    # Value must NEVER round-trip back to the caller.
    assert body["value"] == "***REDACTED***"
    assert "sk-or-secret-xyz" not in response.text

    api_env = _api_env_path(tmp_hal0_home)
    assert api_env.exists(), "api.env should have been created"
    content = api_env.read_text(encoding="utf-8")
    assert 'OPENROUTER_API_KEY="sk-or-secret-xyz"' in content


def test_credential_write_rewrites_existing_line(
    client: TestClient,
    tmp_hal0_home: str,
) -> None:
    """Second write of the same key replaces the line in place — no
    duplicate ``OPENROUTER_API_KEY=`` lines in api.env."""
    client.post(
        "/api/providers/openrouter/credentials",
        json={"key": "OPENROUTER_API_KEY", "value": "old-value"},
    )
    client.post(
        "/api/providers/openrouter/credentials",
        json={"key": "OPENROUTER_API_KEY", "value": "new-value"},
    )
    content = _api_env_path(tmp_hal0_home).read_text(encoding="utf-8")
    assert content.count("OPENROUTER_API_KEY=") == 1
    assert "new-value" in content
    assert "old-value" not in content


def test_credential_write_unknown_upstream_404(client: TestClient) -> None:
    response = client.post(
        "/api/providers/does-not-exist/credentials",
        json={"key": "FOO_KEY", "value": "bar"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "upstream.not_found"


def test_credential_write_rejects_mismatched_key(client: TestClient) -> None:
    """Upstream declares auth_value_env=OPENROUTER_API_KEY; the writer
    refuses to land a credential under a different env-var name."""
    response = client.post(
        "/api/providers/openrouter/credentials",
        json={"key": "SOME_OTHER_KEY", "value": "x"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "provider.credential_write_failed"


@pytest.mark.parametrize(
    "bad_key",
    [
        "lowercase_key",  # lowercase
        "1LEADING_DIGIT",  # leading digit
        "KEY WITH SPACE",
        "KEY;rm -rf /",  # shell metachar
        "KEY\nINJECTED=1",  # newline injection
    ],
)
def test_credential_write_rejects_malformed_keys(
    client: TestClient,
    bad_key: str,
) -> None:
    """Body validation must reject keys that wouldn't survive POSIX env-var
    grammar — they could otherwise injection-escape into the api.env file.
    Either pydantic 422 or our own 400 is acceptable here; the absolute
    requirement is that we don't return 200.
    """
    response = client.post(
        "/api/providers/openrouter/credentials",
        json={"key": bad_key, "value": "x"},
    )
    assert response.status_code in (400, 422), response.text


def test_credential_write_sets_in_process_env(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The route updates os.environ[key] so the running registry can pick
    up the new value without a restart — registry.py reads
    os.environ[auth_value_env] per call."""
    import os

    # Clear it first so we know we're observing the route's side effect.
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    client.post(
        "/api/providers/openrouter/credentials",
        json={"key": "OPENROUTER_API_KEY", "value": "live-rotation"},
    )
    assert os.environ.get("OPENROUTER_API_KEY") == "live-rotation"
