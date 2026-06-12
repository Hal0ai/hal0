"""Tests for the /api/secrets router (operator-managed secret store).

Covers GET (list names, never values), POST + PUT (set → 204), DELETE
(→ 204, idempotent), name validation, and the write-only invariant:
secret VALUES must never appear in any response body. Storage mirrors the
provider-credential writer — atomic, mode-0600, into api.env under the
``tmp_hal0_home`` sandbox.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _restore_environ() -> Iterator[None]:
    """Snapshot + restore os.environ — the router mutates it on set/delete."""
    snapshot = dict(os.environ)
    yield
    for key in set(os.environ) - set(snapshot):
        del os.environ[key]
    for key, value in snapshot.items():
        os.environ[key] = value


def _api_env_path(home: str) -> Path:
    return Path(home) / "etc" / "hal0" / "api.env"


def test_list_empty_when_no_api_env(client: TestClient) -> None:
    r = client.get("/api/secrets")
    assert r.status_code == 200, r.text
    assert r.json() == {"secrets": []}


def test_set_secret_post_persists_and_redacts(
    client: TestClient,
    tmp_hal0_home: str,
) -> None:
    r = client.post("/api/secrets/MY_TOKEN", json={"value": "super-secret-xyz"})
    assert r.status_code == 204, r.text
    # 204 → empty body, value must not leak anywhere.
    assert "super-secret-xyz" not in r.text

    api_env = _api_env_path(tmp_hal0_home)
    assert api_env.exists()
    content = api_env.read_text(encoding="utf-8")
    assert 'MY_TOKEN="super-secret-xyz"' in content
    # Mode 0600 — secrets file.
    assert (api_env.stat().st_mode & 0o777) == 0o600
    # Live process env updated so no restart needed.
    assert os.environ["MY_TOKEN"] == "super-secret-xyz"


def test_set_secret_put_also_supported(client: TestClient, tmp_hal0_home: str) -> None:
    r = client.put("/api/secrets/PUT_TOKEN", json={"value": "v1"})
    assert r.status_code == 204, r.text
    assert 'PUT_TOKEN="v1"' in _api_env_path(tmp_hal0_home).read_text(encoding="utf-8")


def test_list_returns_names_never_values(client: TestClient) -> None:
    client.post("/api/secrets/ALPHA", json={"value": "secret-alpha"})
    client.post("/api/secrets/BETA", json={"value": "secret-beta"})

    r = client.get("/api/secrets")
    assert r.status_code == 200, r.text
    body = r.json()
    names = sorted(e["name"] for e in body["secrets"])
    assert names == ["ALPHA", "BETA"]
    for entry in body["secrets"]:
        assert entry["set"] is True
        assert "updated_at" in entry
    # Values NEVER round-trip.
    assert "secret-alpha" not in r.text
    assert "secret-beta" not in r.text


def test_set_overwrites_existing_line(client: TestClient, tmp_hal0_home: str) -> None:
    client.post("/api/secrets/TOK", json={"value": "first"})
    client.post("/api/secrets/TOK", json={"value": "second"})
    content = _api_env_path(tmp_hal0_home).read_text(encoding="utf-8")
    assert content.count("TOK=") == 1
    assert 'TOK="second"' in content
    assert "first" not in content


def test_delete_removes_secret(client: TestClient, tmp_hal0_home: str) -> None:
    client.post("/api/secrets/GONE", json={"value": "bye"})
    assert os.environ.get("GONE") == "bye"

    r = client.delete("/api/secrets/GONE")
    assert r.status_code == 204, r.text
    content = _api_env_path(tmp_hal0_home).read_text(encoding="utf-8")
    assert "GONE" not in content
    assert "GONE" not in os.environ


def test_delete_is_idempotent(client: TestClient) -> None:
    # Deleting a never-set secret is a no-op 204, not a 404.
    r = client.delete("/api/secrets/NEVER_SET")
    assert r.status_code == 204, r.text


@pytest.mark.parametrize("name", ["lower_case", "1LEADING_DIGIT", "BAD-DASH"])
def test_invalid_names_rejected(client: TestClient, name: str) -> None:
    r = client.post(f"/api/secrets/{name}", json={"value": "x"})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "secret.name_invalid"


def test_set_coexists_with_provider_credentials(client: TestClient, tmp_hal0_home: str) -> None:
    """Secrets share api.env with provider creds — both lines survive."""
    api_env = _api_env_path(tmp_hal0_home)
    api_env.parent.mkdir(parents=True, exist_ok=True)
    api_env.write_text('OPENROUTER_API_KEY="provider-key"\n', encoding="utf-8")

    r = client.post("/api/secrets/EXTRA_TOKEN", json={"value": "tok"})
    assert r.status_code == 204, r.text
    content = api_env.read_text(encoding="utf-8")
    assert 'OPENROUTER_API_KEY="provider-key"' in content
    assert 'EXTRA_TOKEN="tok"' in content

    # The list surfaces both (every api.env key is a secret).
    names = sorted(e["name"] for e in client.get("/api/secrets").json()["secrets"])
    assert names == ["EXTRA_TOKEN", "OPENROUTER_API_KEY"]
