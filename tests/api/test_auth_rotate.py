"""Tests for ``POST /api/auth/rotate`` (key rotation, KB-1 tail).

Covers the rotate route + its service-identity write seam:

- happy path: writes ``/etc/hal0/api.env`` with a fresh key, returns
  STATUS ONLY (never the value), and applies live (new key works, old
  key stops working — no restart);
- the atomic write preserves a never-world-readable ``0640`` mode;
- ADMIN gating (anon 401, client-bearer 403) once enforcement is armed;
- the shared per-IP login limiter throttles rotate;
- the key value never leaks into the response body.

The value is retrieved out-of-band from api.env on disk (what the box
operator does); the test reads it there to prove the live-swap, exactly
as the design intends — it is never in the HTTP response.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hal0.api import auth as auth_mod
from hal0.api import create_app
from hal0.service_identity import keys_from_api_env


@pytest.fixture(autouse=True)
def _restore_key_env() -> Iterator[None]:
    """Save/restore the two key env vars.

    ``rotate_api_env_key`` mutates ``os.environ`` directly (that's the
    live-swap), which ``monkeypatch`` can't see — so we snapshot + restore
    them ourselves to keep tests isolated.
    """
    saved = {k: os.environ.get(k) for k in ("HAL0_ADMIN_KEY", "HAL0_CLIENT_KEY")}
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _make_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("HAL0_AGENT_SECRET_PATH", str(tmp_path / "secret.bin"))
    monkeypatch.setenv("HAL0_HOME", str(tmp_path / "hal0_home"))
    os.makedirs(tmp_path / "hal0_home" / "etc" / "hal0", exist_ok=True)
    auth_mod._require_auth_cache = None
    return TestClient(create_app())


@pytest.fixture
def rotate_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    with _make_client(tmp_path, monkeypatch) as c:
        yield c


def _api_env_path(tmp_path: Path) -> Path:
    return tmp_path / "hal0_home" / "etc" / "hal0" / "api.env"


# ---------------------------------------------------------------------------
# happy path


def test_rotate_admin_writes_api_env_status_only(
    rotate_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAL0_ADMIN_KEY", "old-admin-key")

    resp = rotate_client.post("/api/auth/rotate", json={"tier": "admin"})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Status-only contract.
    assert body["tier"] == "admin"
    assert body["key_len"] >= 32
    assert len(body["fingerprint"]) == 8
    assert body["applies_live"] is True
    assert body["restart_required"] is False
    assert body["session_preserved"] is True
    assert "note" in body
    # Never a value field.
    assert "key" not in body
    assert "value" not in body

    # api.env was written with the new key line (keys_from_api_env reads
    # paths.etc(), which HAL0_HOME points at tmp).
    new_key = keys_from_api_env().get("HAL0_ADMIN_KEY")
    assert new_key and new_key != "old-admin-key"


def test_rotate_admin_never_leaks_the_key(
    rotate_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAL0_ADMIN_KEY", "old-admin-key")
    resp = rotate_client.post("/api/auth/rotate", json={"tier": "admin"})
    assert resp.status_code == 200

    # The real new key is on disk; it must NOT appear anywhere in the response.
    new_key = keys_from_api_env()["HAL0_ADMIN_KEY"]
    assert new_key not in resp.text
    # The fingerprint is a one-way prefix, not the key.
    assert resp.json()["fingerprint"] != new_key[:8]


def test_rotate_applies_live_new_key_works_old_fails(
    rotate_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After rotation the running process honours the new key immediately."""
    monkeypatch.setenv("HAL0_ADMIN_KEY", "old-admin-key")
    assert auth_mod.verify_admin_key("old-admin-key") is True

    rotate_client.post("/api/auth/rotate", json={"tier": "admin"})

    new_key = keys_from_api_env()["HAL0_ADMIN_KEY"]
    # Live: os.environ was updated in-process, no restart.
    assert auth_mod.verify_admin_key(new_key) is True
    assert auth_mod.verify_admin_key("old-admin-key") is False


def test_rotate_preserves_non_world_readable_mode(
    rotate_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAL0_ADMIN_KEY", "old-admin-key")
    rotate_client.post("/api/auth/rotate", json={"tier": "admin"})

    mode = stat.S_IMODE(os.stat(_api_env_path(tmp_path)).st_mode)
    assert mode == 0o640
    # Never world-readable — the whole point once it holds a live secret.
    assert not (mode & 0o007)


def test_rotate_preserves_other_env_lines(
    rotate_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rotation only touches the one key line; everything else is verbatim."""
    api_env = _api_env_path(tmp_path)
    api_env.write_text(
        "# a comment\nHAL0_PORT=8080\nHAL0_ADMIN_KEY=old\nHAL0_BIND_HOST=0.0.0.0\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HAL0_ADMIN_KEY", "old")

    rotate_client.post("/api/auth/rotate", json={"tier": "admin"})

    text = api_env.read_text(encoding="utf-8")
    assert "# a comment" in text
    assert "HAL0_PORT=8080" in text
    assert "HAL0_BIND_HOST=0.0.0.0" in text
    assert "HAL0_ADMIN_KEY=old\n" not in text  # replaced


def test_rotate_client_tier(
    rotate_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAL0_CLIENT_KEY", "old-client")
    resp = rotate_client.post("/api/auth/rotate", json={"tier": "client"})
    assert resp.status_code == 200
    assert resp.json()["tier"] == "client"
    new_key = keys_from_api_env()["HAL0_CLIENT_KEY"]
    assert new_key and new_key != "old-client"
    assert auth_mod._client_key() == new_key


def test_rotate_default_tier_is_admin(
    rotate_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAL0_ADMIN_KEY", "old")
    resp = rotate_client.post("/api/auth/rotate", json={})
    assert resp.status_code == 200
    assert resp.json()["tier"] == "admin"


def test_rotate_rejects_bad_tier(rotate_client: TestClient) -> None:
    resp = rotate_client.post("/api/auth/rotate", json={"tier": "root"})
    assert resp.status_code == 422  # pydantic Literal rejects it


# ---------------------------------------------------------------------------
# ADMIN gating (enforcement armed)


def _arm_auth(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_ADMIN_KEY", "the-admin-key")
    monkeypatch.setenv("HAL0_CLIENT_KEY", "the-client-key")
    monkeypatch.setenv("HAL0_REQUIRE_AUTH", "1")
    auth_mod._require_auth_cache = None


def test_rotate_denied_anon_when_armed(
    rotate_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _arm_auth(rotate_client, monkeypatch)
    resp = rotate_client.post("/api/auth/rotate", json={"tier": "admin"})
    assert resp.status_code == 401


def test_rotate_denied_client_bearer_when_armed(
    rotate_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _arm_auth(rotate_client, monkeypatch)
    resp = rotate_client.post(
        "/api/auth/rotate",
        json={"tier": "admin"},
        headers={"Authorization": "Bearer the-client-key"},
    )
    assert resp.status_code == 403


def test_rotate_allowed_admin_bearer_when_armed(
    rotate_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _arm_auth(rotate_client, monkeypatch)
    resp = rotate_client.post(
        "/api/auth/rotate",
        json={"tier": "admin"},
        headers={"Authorization": "Bearer the-admin-key"},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# rate limiting (shared login limiter)


def test_rotate_is_rate_limited(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAL0_LOGIN_RATELIMIT_MAX", "2")
    monkeypatch.setenv("HAL0_ADMIN_KEY", "old")
    with _make_client(tmp_path, monkeypatch) as client:
        assert client.post("/api/auth/rotate", json={"tier": "admin"}).status_code == 200
        assert client.post("/api/auth/rotate", json={"tier": "admin"}).status_code == 200
        resp = client.post("/api/auth/rotate", json={"tier": "admin"})
        assert resp.status_code == 429
        assert resp.json()["error"]["code"] == "auth.rate_limited"
