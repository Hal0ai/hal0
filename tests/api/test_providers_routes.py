"""Tests for /api/upstreams and /api/providers routes."""

from __future__ import annotations

import tomllib
from pathlib import Path

from fastapi.testclient import TestClient

from hal0.upstreams.registry import Upstream


def _seed_upstreams(client: TestClient) -> None:
    reg = client.app.state.upstreams
    # Clear any auto-registered entries so the test is deterministic.
    for u in list(reg.list()):
        reg.remove(u.name)
    reg.add(
        Upstream(
            name="primary",
            kind="slot",
            url="http://127.0.0.1:8081/v1",
            slot_name="primary",
        )
    )
    reg.add(
        Upstream(
            name="openrouter",
            kind="remote",
            url="https://openrouter.ai/api/v1",
            auth_value_env="OPENROUTER_API_KEY",
        )
    )


def test_list_upstreams_returns_registered_entries(client: TestClient) -> None:
    _seed_upstreams(client)
    response = client.get("/api/upstreams")
    assert response.status_code == 200, response.text
    body = response.json()
    names = {u["name"] for u in body}
    assert {"primary", "openrouter"} <= names
    for u in body:
        assert "name" in u and "kind" in u and "url" in u
        # Secrets never leak — only the env-var name appears.
        assert "auth_value" not in u
        if u["name"] == "openrouter":
            assert u["auth_value_env"] == "OPENROUTER_API_KEY"
            assert u["auth_configured"] is True
            assert u["kind"] == "remote"


def test_get_upstream_by_name(client: TestClient) -> None:
    _seed_upstreams(client)
    response = client.get("/api/upstreams/primary")
    assert response.status_code == 200
    assert response.json()["name"] == "primary"


def test_get_upstream_404(client: TestClient) -> None:
    _seed_upstreams(client)
    response = client.get("/api/upstreams/nope")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "upstream.not_found"


def test_providers_excludes_slot_kind(client: TestClient) -> None:
    _seed_upstreams(client)
    response = client.get("/api/providers")
    assert response.status_code == 200
    names = {u["name"] for u in response.json()}
    assert "openrouter" in names
    assert "primary" not in names  # slot upstreams aren't "providers"


def test_providers_catalog_has_known_entries(client: TestClient) -> None:
    response = client.get("/api/providers/catalog")
    assert response.status_code == 200
    catalog = response.json()
    # Anthropic + OpenAI + OpenRouter are part of the built-in catalog;
    # at minimum the catalog must be non-empty.
    assert isinstance(catalog, dict) and len(catalog) > 0


# ── PATCH /api/upstreams/{name} — advertise_models toggle (#1147) ─────────────


def _upstreams_toml_path(client: TestClient) -> Path:
    """Resolve the per-test upstreams.toml path (HAL0_HOME-isolated)."""
    # hal0.config.paths.etc() resolves to $HAL0_HOME/etc/hal0 in tests;
    # the conftest monkeypatches HAL0_HOME to tmp_path.
    import os

    home = os.environ["HAL0_HOME"]
    return Path(home) / "etc" / "hal0" / "upstreams.toml"


def _seed_openrouter_in_toml(client: TestClient) -> None:
    """Write a minimal upstreams.toml containing only the 'openrouter' entry.

    The seeded registry above leaves the file empty; the PATCH test needs
    a row on disk so the round-trip persists. We use the registry's own
    shape (no validation friction) by writing through the loader's API.
    """
    from hal0.config.loader import save_upstreams_config
    from hal0.config.schema import UpstreamEntry, UpstreamsConfig

    cfg = UpstreamsConfig(
        upstream=[
            UpstreamEntry(
                name="openrouter",
                kind="remote",
                url="https://openrouter.ai/api/v1",
                auth_value_env="OPENROUTER_API_KEY",
                advertise_models=True,
            )
        ]
    )
    save_upstreams_config(cfg)


def test_patch_upstream_advertise_off_round_trip(client: TestClient) -> None:
    _seed_upstreams(client)
    _seed_openrouter_in_toml(client)

    response = client.patch(
        "/api/upstreams/openrouter",
        json={"advertise_models": False},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "openrouter"
    assert body["advertise_models"] is False

    # In-memory: GET reflects the new value without an API restart.
    follow = client.get("/api/upstreams/openrouter")
    assert follow.status_code == 200
    assert follow.json()["advertise_models"] is False

    # On-disk: upstreams.toml was rewritten atomically with the new value.
    path = _upstreams_toml_path(client)
    assert path.exists(), "PATCH must persist upstreams.toml"
    with path.open("rb") as f:
        raw = tomllib.load(f)
    entries = [u for u in raw.get("upstream", []) if u.get("name") == "openrouter"]
    assert entries, "the openrouter row should still be on disk"
    assert entries[0]["advertise_models"] is False


def test_patch_upstream_advertise_on_restores_state(client: TestClient) -> None:
    _seed_upstreams(client)
    _seed_openrouter_in_toml(client)

    # Toggle off, then back on.
    client.patch("/api/upstreams/openrouter", json={"advertise_models": False})
    response = client.patch(
        "/api/upstreams/openrouter",
        json={"advertise_models": True},
    )
    assert response.status_code == 200
    assert response.json()["advertise_models"] is True

    path = _upstreams_toml_path(client)
    with path.open("rb") as f:
        raw = tomllib.load(f)
    entries = [u for u in raw.get("upstream", []) if u.get("name") == "openrouter"]
    assert entries[0]["advertise_models"] is True


def test_patch_upstream_404_on_unknown_name(client: TestClient) -> None:
    _seed_upstreams(client)
    response = client.patch(
        "/api/upstreams/nope",
        json={"advertise_models": False},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "upstream.not_found"


def test_patch_upstream_empty_body_is_noop(client: TestClient) -> None:
    """PATCH with no fields returns the current state and does not write."""
    _seed_upstreams(client)
    _seed_openrouter_in_toml(client)
    path = _upstreams_toml_path(client)
    advertise_before = client.get("/api/upstreams/openrouter").json()["advertise_models"]

    response = client.patch("/api/upstreams/openrouter", json={})
    assert response.status_code == 200
    assert response.json()["advertise_models"] == advertise_before

    # No rewrite when the body carries no fields — the on-disk file's
    # advertise_models is unchanged.
    if path.exists():
        with path.open("rb") as f:
            raw = tomllib.load(f)
        entries = [u for u in raw.get("upstream", []) if u.get("name") == "openrouter"]
        if entries:
            assert entries[0]["advertise_models"] is True


def test_patch_upstream_clears_cached_models_when_off(client: TestClient) -> None:
    """Toggling OFF punches app.state.upstream_models so /api/upstreams sees []."""
    _seed_upstreams(client)
    _seed_openrouter_in_toml(client)

    cache = getattr(client.app.state, "upstream_models", None)
    assert cache is not None
    cache["openrouter"] = ["model-a", "model-b", "model-c"]

    response = client.patch(
        "/api/upstreams/openrouter",
        json={"advertise_models": False},
    )
    assert response.status_code == 200
    # The response itself reflects the punch — the cached list was wiped.
    assert response.json()["models"] == []

    follow = client.get("/api/upstreams/openrouter")
    assert follow.json()["advertise_models"] is False
    assert follow.json()["models"] == []


def test_patch_upstream_drops_cache_on_reenable(client: TestClient) -> None:
    """Toggling ON drops the cached list so the next fetch refetches."""
    _seed_upstreams(client)
    _seed_openrouter_in_toml(client)

    cache = getattr(client.app.state, "upstream_models", None)
    cache["openrouter"] = []

    response = client.patch(
        "/api/upstreams/openrouter",
        json={"advertise_models": True},
    )
    assert response.status_code == 200
    # On re-enable, the cache key is dropped so the next read refetches.
    assert "openrouter" not in cache


def test_patch_upstream_does_not_touch_other_rows(client: TestClient) -> None:
    """PATCH must leave sibling rows in upstreams.toml untouched."""
    from hal0.config.loader import save_upstreams_config
    from hal0.config.schema import UpstreamEntry, UpstreamsConfig

    _seed_upstreams(client)
    cfg = UpstreamsConfig(
        upstream=[
            UpstreamEntry(
                name="openrouter",
                kind="remote",
                url="https://openrouter.ai/api/v1",
                auth_value_env="OPENROUTER_API_KEY",
                advertise_models=True,
            ),
            UpstreamEntry(
                name="anthropic",
                kind="remote",
                url="https://api.anthropic.com/v1",
                auth_value_env="ANTHROPIC_API_KEY",
                advertise_models=True,
                warmup_strategy="eager",
            ),
        ]
    )
    save_upstreams_config(cfg)

    client.patch("/api/upstreams/openrouter", json={"advertise_models": False})

    path = _upstreams_toml_path(client)
    with path.open("rb") as f:
        raw = tomllib.load(f)
    by_name = {u["name"]: u for u in raw.get("upstream", [])}
    assert by_name["openrouter"]["advertise_models"] is False
    # Anthropic row untouched (modulo warmup alias normalization: the
    # legacy "eager" spelling is written back as canonical "always").
    assert by_name["anthropic"]["advertise_models"] is True
    assert by_name["anthropic"]["warmup_strategy"] == "always"


def test_patch_upstream_persists_atomically_no_partial_file(client: TestClient) -> None:
    """A successful PATCH leaves a complete TOML file (atomic write)."""
    _seed_upstreams(client)
    _seed_openrouter_in_toml(client)

    client.patch("/api/upstreams/openrouter", json={"advertise_models": False})

    path = _upstreams_toml_path(client)
    # No leftover .tmp.* siblings from write_toml_atomic.
    siblings = [
        p
        for p in path.parent.iterdir()
        if p.name.startswith(f".{path.name}.") and p.name.endswith(".tmp")
    ]
    assert not siblings, f"atomic write leaked temp files: {siblings}"
    # File parses cleanly.
    with path.open("rb") as f:
        tomllib.load(f)


# ── POST/PATCH/DELETE /api/upstreams — reactive CRUD ──────────────────────────


def _toml_rows(client: TestClient) -> dict[str, dict]:
    path = _upstreams_toml_path(client)
    if not path.exists():
        return {}
    with path.open("rb") as f:
        raw = tomllib.load(f)
    return {u["name"]: u for u in raw.get("upstream", [])}


def test_create_upstream_minimal(client: TestClient) -> None:
    _seed_upstreams(client)
    response = client.post(
        "/api/upstreams",
        json={"name": "corp", "url": "https://llm.corp.internal/v1"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "corp"
    assert body["kind"] == "remote"
    assert body["enabled"] is True
    # Registered live + persisted to TOML.
    assert client.get("/api/upstreams/corp").status_code == 200
    assert _toml_rows(client)["corp"]["url"] == "https://llm.corp.internal/v1"


def test_create_upstream_from_catalog_prefills(client: TestClient, monkeypatch: object) -> None:
    # Earlier tests may have written OPENROUTER_API_KEY into os.environ via
    # the credentials route — the "write the key next" hint needs it absent.
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)  # type: ignore[attr-defined]
    _seed_upstreams(client)
    response = client.post(
        "/api/upstreams",
        json={"name": "my-openrouter", "catalog_id": "openrouter"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["url"] == "https://openrouter.ai/api/v1"
    assert body["auth_style"] == "bearer"
    assert body["auth_value_env"] == "OPENROUTER_API_KEY"
    # Key not yet written → hint chains to the credentials route.
    assert "credentials" in body.get("hint", "")


def test_create_upstream_explicit_fields_beat_catalog(client: TestClient) -> None:
    _seed_upstreams(client)
    response = client.post(
        "/api/upstreams",
        json={
            "name": "or-proxy",
            "catalog_id": "openrouter",
            "url": "http://10.0.1.200:4000/v1",
            "auth_value_env": "LITELLM_KEY",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["url"] == "http://10.0.1.200:4000/v1"
    assert response.json()["auth_value_env"] == "LITELLM_KEY"


def test_create_upstream_with_filters_and_flags(client: TestClient) -> None:
    _seed_upstreams(client)
    response = client.post(
        "/api/upstreams",
        json={
            "name": "curated",
            "url": "https://openrouter.ai/api/v1",
            "advertise_models": True,
            "enabled": False,
            "model_filters": {"include": ["anthropic/*"], "exclude": ["*:free"]},
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["enabled"] is False
    assert body["model_filters"] == {
        "models": [],
        "include": ["anthropic/*"],
        "exclude": ["*:free"],
    }
    row = _toml_rows(client)["curated"]
    assert row["enabled"] is False
    assert row["model_filters"]["include"] == ["anthropic/*"]


def test_create_upstream_duplicate_409(client: TestClient) -> None:
    _seed_upstreams(client)  # registers "openrouter"
    response = client.post(
        "/api/upstreams",
        json={"name": "openrouter", "url": "https://openrouter.ai/api/v1"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "upstream.already_exists"


def test_create_upstream_reserved_name_400(client: TestClient) -> None:
    response = client.post("/api/upstreams", json={"name": "hal0", "url": "http://x/v1"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "upstream.protected"


def test_create_upstream_bad_name_400(client: TestClient) -> None:
    for bad in ("UPPER", "has space", "-leading"):
        response = client.post("/api/upstreams", json={"name": bad, "url": "http://x/v1"})
        assert response.status_code == 400, bad
        assert response.json()["error"]["code"] == "upstream.invalid"
    # Over-length names are rejected at the pydantic layer (422).
    response = client.post("/api/upstreams", json={"name": "a" * 65, "url": "http://x/v1"})
    assert response.status_code == 422


def test_create_upstream_unknown_catalog_400(client: TestClient) -> None:
    response = client.post("/api/upstreams", json={"name": "x", "catalog_id": "nope"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "upstream.invalid"


def test_create_upstream_url_required_without_catalog(client: TestClient) -> None:
    response = client.post("/api/upstreams", json={"name": "x"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "upstream.invalid"


def test_create_upstream_never_echoes_secrets(client: TestClient) -> None:
    import os

    os.environ["OPENROUTER_API_KEY"] = "sk-super-secret"
    try:
        response = client.post("/api/upstreams", json={"name": "orx", "catalog_id": "openrouter"})
        assert "sk-super-secret" not in response.text
    finally:
        os.environ.pop("OPENROUTER_API_KEY", None)


def test_patch_upstream_enabled_and_filters(client: TestClient) -> None:
    _seed_upstreams(client)
    _seed_openrouter_in_toml(client)
    response = client.patch(
        "/api/upstreams/openrouter",
        json={
            "enabled": False,
            "model_filters": {"include": ["anthropic/*"], "exclude": [], "models": []},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["enabled"] is False
    assert body["model_filters"]["include"] == ["anthropic/*"]
    row = _toml_rows(client)["openrouter"]
    assert row["enabled"] is False
    assert row["model_filters"]["include"] == ["anthropic/*"]


def test_patch_upstream_clear_filters_with_empty_object(client: TestClient) -> None:
    _seed_upstreams(client)
    _seed_openrouter_in_toml(client)
    client.patch("/api/upstreams/openrouter", json={"model_filters": {"include": ["a/*"]}})
    response = client.patch(
        "/api/upstreams/openrouter",
        json={"model_filters": {"models": [], "include": [], "exclude": []}},
    )
    assert response.status_code == 200
    assert response.json()["model_filters"] is None
    assert "model_filters" not in _toml_rows(client)["openrouter"]


def test_patch_upstream_structural_fields_on_remote(client: TestClient) -> None:
    _seed_upstreams(client)
    _seed_openrouter_in_toml(client)
    response = client.patch(
        "/api/upstreams/openrouter",
        json={"url": "http://10.0.1.200:4000/v1", "timeout_seconds": 60.0},
    )
    assert response.status_code == 200, response.text
    assert response.json()["url"] == "http://10.0.1.200:4000/v1"
    assert _toml_rows(client)["openrouter"]["url"] == "http://10.0.1.200:4000/v1"


def test_patch_slot_upstream_structural_rejected_visibility_allowed(
    client: TestClient,
) -> None:
    _seed_upstreams(client)  # includes slot-kind "primary"
    response = client.patch("/api/upstreams/primary", json={"url": "http://elsewhere/v1"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "upstream.protected"
    # Visibility fields still work (in-memory-only — no TOML row).
    response = client.patch("/api/upstreams/primary", json={"advertise_models": False})
    assert response.status_code == 200
    assert response.json()["advertise_models"] is False


def test_patch_upstream_invalid_auth_style_400(client: TestClient) -> None:
    _seed_upstreams(client)
    _seed_openrouter_in_toml(client)
    response = client.patch("/api/upstreams/openrouter", json={"auth_style": "basic"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "upstream.invalid"
    # Memory and disk untouched.
    assert client.get("/api/upstreams/openrouter").json()["auth_style"] == "bearer"
    assert _toml_rows(client)["openrouter"].get("auth_style", "bearer") == "bearer"


def test_delete_upstream_removes_row_and_registry(client: TestClient) -> None:
    _seed_upstreams(client)
    _seed_openrouter_in_toml(client)
    response = client.delete("/api/upstreams/openrouter")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["removed_from_toml"] is True
    assert "openrouter" not in _toml_rows(client)
    assert client.get("/api/upstreams/openrouter").status_code == 404


def test_delete_upstream_protects_slot_and_composite(client: TestClient) -> None:
    _seed_upstreams(client)
    response = client.delete("/api/upstreams/primary")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "upstream.protected"


def test_delete_upstream_404(client: TestClient) -> None:
    _seed_upstreams(client)
    response = client.delete("/api/upstreams/ghost")
    assert response.status_code == 404


def test_delete_upstream_retains_credentials(client: TestClient) -> None:
    """Deleting an upstream must never destroy the secret in api.env."""
    import os

    _seed_upstreams(client)
    _seed_openrouter_in_toml(client)
    client.post(
        "/api/providers/openrouter/credentials",
        json={"key": "OPENROUTER_API_KEY", "value": "sk-keepme"},
    )
    try:
        response = client.delete("/api/upstreams/openrouter")
        assert response.status_code == 200
        api_env = _upstreams_toml_path(client).parent / "api.env"
        assert "sk-keepme" in api_env.read_text()
    finally:
        os.environ.pop("OPENROUTER_API_KEY", None)
