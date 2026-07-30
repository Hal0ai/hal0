"""Issue #1450: the Secrets page is not a generic editor for ``api.env``.

``/etc/hal0/api.env`` is two things wearing one hat: the operator's
third-party credential store (``HF_TOKEN``, ``OPENROUTER_API_KEY``, …) and
the systemd ``EnvironmentFile`` carrying hal0's own service configuration
(``HAL0_BIND_HOST``, ``HAL0_PORT``, ``HAL0_UI_DIST``, ``HAL0_MCP_ALLOWED_HOSTS``)
and — after a rotation — its auth keys (``HAL0_ADMIN_KEY`` /
``HAL0_CLIENT_KEY``, written there by ``service_identity``).

``list_secrets`` enumerated every uncommented ``KEY=`` line with no filter,
and ``delete_secret`` removed any name matching
``^[A-Z][A-Z0-9_]{0,63}$`` — atomically, popping ``os.environ`` live, with a
204. So the dashboard rendered ``HAL0_ADMIN_KEY`` as a one-click "Remove"
button whose effect is *disarming login* (``auth.py`` validates against
``HAL0_ADMIN_KEY`` from env, so every new session locks out), and
``HAL0_PORT`` / ``HAL0_UI_DIST`` as buttons that break the service on next
restart. No confirmation dialog anywhere in the page.

The rule encoded here: **``HAL0_`` is hal0's own configuration namespace,
owned by the installer, ``hal0.toml`` and key rotation — not by this
route.** Those keys still *list* (hiding them would trade one lie for
another: the operator should see what the service is configured with) but
they are marked read-only and every mutation is refused server-side, so a
hand-rolled ``curl`` is bound by the same rule as the UI. Third-party
credential names are untouched.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hal0.api.routes.secrets import PROTECTED_SECRET_PREFIX, is_protected_secret

#: Sampled verbatim from the live box's api.env in the #1450 audit.
LIVE_SERVICE_KEYS = [
    "HAL0_BIND_HOST",
    "HAL0_PORT",
    "HAL0_UI_DIST",
    "HAL0_LOG_LEVEL",
    "HAL0_MEMORY_ENABLED",
    "HAL0_MCP_ALLOWED_HOSTS",
    "HAL0_FLM_MODELS_DIR",
]
AUTH_KEYS = ["HAL0_ADMIN_KEY", "HAL0_CLIENT_KEY"]
OPERATOR_SECRETS = ["HF_TOKEN", "OPENROUTER_API_KEY", "MINIMAX_API_KEY", "AWS_SECRET_ACCESS_KEY"]


@pytest.fixture(autouse=True)
def _restore_environ() -> Iterator[None]:
    snapshot = dict(os.environ)
    yield
    for key in set(os.environ) - set(snapshot):
        del os.environ[key]
    for key, value in snapshot.items():
        os.environ[key] = value


def _api_env(home: str) -> Path:
    return Path(home) / "etc" / "hal0" / "api.env"


def _seed(home: str, *names: str) -> Path:
    """Write an api.env holding ``names`` the way the installer would —
    directly, bypassing the route, because the route now refuses to."""
    path = _api_env(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f'{n}="value-for-{n}"\n' for n in names), encoding="utf-8")
    return path


# ── 1. the predicate ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", LIVE_SERVICE_KEYS + AUTH_KEYS)
def test_hal0_namespaced_names_are_protected(name: str) -> None:
    assert is_protected_secret(name) is True


@pytest.mark.parametrize("name", OPERATOR_SECRETS)
def test_third_party_credential_names_are_not_protected(name: str) -> None:
    assert is_protected_secret(name) is False


def test_the_reserved_prefix_is_hal0() -> None:
    """Pinned so widening the namespace is a deliberate diff, not a typo."""
    assert PROTECTED_SECRET_PREFIX == "HAL0_"


def test_the_auth_keys_are_covered_by_the_rule() -> None:
    """``service_identity`` owns the auth-key names; this asserts the two
    modules agree rather than re-listing them here."""
    from hal0.service_identity import _KEY_ENV

    assert set(_KEY_ENV.values()) == set(AUTH_KEYS)
    assert all(is_protected_secret(n) for n in _KEY_ENV.values())


# ── 2. the route refuses to mutate them ──────────────────────────────────────


@pytest.mark.parametrize("name", ["HAL0_ADMIN_KEY", "HAL0_PORT", "HAL0_UI_DIST"])
def test_delete_of_a_protected_key_is_refused_and_changes_nothing(
    client: TestClient, tmp_hal0_home: str, name: str
) -> None:
    """The headline defect: one unconfirmed click returned 204 and removed
    the line. ``HAL0_ADMIN_KEY`` specifically disarms every new login."""
    path = _seed(tmp_hal0_home, name, "HF_TOKEN")
    before = path.read_text(encoding="utf-8")
    os.environ[name] = "live"

    r = client.delete(f"/api/secrets/{name}")
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "secret.protected"
    assert path.read_text(encoding="utf-8") == before
    assert os.environ[name] == "live", "the live process env was mutated anyway"


@pytest.mark.parametrize("verb", ["post", "put"])
def test_set_of_a_protected_key_is_refused(
    client: TestClient, tmp_hal0_home: str, verb: str
) -> None:
    """Overwriting ``HAL0_ADMIN_KEY`` with an attacker-chosen value is a
    worse outcome than deleting it — the write path is gated too."""
    path = _seed(tmp_hal0_home, "HAL0_ADMIN_KEY")
    before = path.read_text(encoding="utf-8")

    r = getattr(client, verb)("/api/secrets/HAL0_ADMIN_KEY", json={"value": "attacker"})
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "secret.protected"
    assert path.read_text(encoding="utf-8") == before


def test_protected_refusal_names_the_key_but_never_its_value(
    client: TestClient, tmp_hal0_home: str
) -> None:
    _seed(tmp_hal0_home, "HAL0_TURNSTONE_TOKEN")
    r = client.delete("/api/secrets/HAL0_TURNSTONE_TOKEN")
    assert r.status_code == 403
    assert "HAL0_TURNSTONE_TOKEN" in r.text
    assert "value-for-" not in r.text


# ── 3. ordinary secrets are untouched ────────────────────────────────────────


def test_operator_secrets_still_set_and_delete(client: TestClient, tmp_hal0_home: str) -> None:
    """Negative control — the guard must not break the route's actual job."""
    assert client.post("/api/secrets/HF_TOKEN", json={"value": "hf_x"}).status_code == 204
    assert client.delete("/api/secrets/HF_TOKEN").status_code == 204
    assert 'HF_TOKEN="hf_x"' not in _api_env(tmp_hal0_home).read_text(encoding="utf-8")


# ── 4. the list still shows them, flagged ────────────────────────────────────


def test_list_marks_protected_rows_read_only(client: TestClient, tmp_hal0_home: str) -> None:
    """Rows stay visible — an operator needs to see the service config — but
    carry the flag the UI renders as a lock instead of a Remove button."""
    _seed(tmp_hal0_home, "HAL0_PORT", "HAL0_ADMIN_KEY", "HF_TOKEN")
    rows = {r["name"]: r for r in client.get("/api/secrets").json()["secrets"]}

    assert set(rows) == {"HAL0_PORT", "HAL0_ADMIN_KEY", "HF_TOKEN"}
    assert rows["HAL0_PORT"]["protected"] is True
    assert rows["HAL0_ADMIN_KEY"]["protected"] is True
    assert rows["HF_TOKEN"]["protected"] is False


def test_list_still_never_returns_a_value(client: TestClient, tmp_hal0_home: str) -> None:
    _seed(tmp_hal0_home, "HAL0_ADMIN_KEY", "HF_TOKEN")
    assert "value-for-" not in client.get("/api/secrets").text
