"""Tests for Profile CRUD API — POST/PUT/DELETE /api/profiles.

Run targeted:
    ~/dev/wt-phase-c/.venv/bin/python -m pytest tests/api/test_profiles_crud.py -q
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.config.schema import MTP_FLAG_BUNDLE, SEED_PROFILES

# ── helpers ────────────────────────────────────────────────────────────────────


def _seed_slot_toml(home: str, name: str, profile: str, port: int = 8090) -> Path:
    """Write a minimal slot TOML that references *profile*."""
    root = Path(home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.toml"
    path.write_text(
        f'[slot]\nname = "{name}"\nport = {port}\nprofile = "{profile}"\n',
        encoding="utf-8",
    )
    return path


def _seed_corrupt_slot_toml(home: str, name: str) -> Path:
    """Write a slot TOML that fails to parse."""
    root = Path(home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.toml"
    path.write_text("[slot\nthis is not = valid toml {{{", encoding="utf-8")
    return path


# ── fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def app(tmp_hal0_home: str) -> FastAPI:
    """Fresh app; tmp_hal0_home means no profiles.toml → seeds returned."""
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


# ── POST /api/profiles ─────────────────────────────────────────────────────────


def test_create_profile_201_and_listed(client: TestClient) -> None:
    r = client.post(
        "/api/profiles",
        json={
            "name": "my-vulkan",
            "image": "ghcr.io/x/y:z",
            "flags": "-fa on",
            "mtp": False,
            "device_class": "gpu",
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "my-vulkan"
    assert body["image"] == "ghcr.io/x/y:z"
    assert body["flags"] == "-fa on"
    assert body["mtp"] is False
    assert body["device_class"] == "gpu"
    assert "resolved_flags" in body
    assert body["seed"] is False
    listed = client.get("/api/profiles").json()
    assert any(p["name"] == "my-vulkan" for p in listed)


def test_create_persists_across_reload(tmp_hal0_home: str) -> None:
    """Second app/client constructed after the POST sees the written file."""
    app1 = create_app()
    with TestClient(app1) as c1:
        r = c1.post(
            "/api/profiles",
            json={"name": "persist-me", "image": "ghcr.io/x/y:z"},
        )
        assert r.status_code == 201

    # New app reads profiles.toml from disk — must include the new profile.
    app2 = create_app()
    with TestClient(app2) as c2:
        listed = c2.get("/api/profiles").json()
    assert any(p["name"] == "persist-me" for p in listed)


def test_create_duplicate_name_409(client: TestClient) -> None:
    """Duplicate against existing custom profile → 409 profiles.exists."""
    client.post(
        "/api/profiles",
        json={"name": "my-vulkan", "image": "ghcr.io/x/y:z"},
    )
    r = client.post(
        "/api/profiles",
        json={"name": "my-vulkan", "image": "ghcr.io/a/b:c"},
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "profiles.exists"


def test_create_seed_name_409(client: TestClient) -> None:
    """Duplicate against a seed profile name → 409 profiles.exists."""
    seed_name = next(iter(SEED_PROFILES))
    r = client.post(
        "/api/profiles",
        json={"name": seed_name, "image": "ghcr.io/x/y:z"},
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "profiles.exists"


def test_create_mtp_false_resolved_flags_equals_flags(client: TestClient) -> None:
    """Custom profile with mtp=False: resolved_flags == flags (stripped)."""
    r = client.post(
        "/api/profiles",
        json={"name": "no-mtp", "image": "ghcr.io/x/y:z", "flags": "-fa on", "mtp": False},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["resolved_flags"] == body["flags"].strip()
    assert "--spec-type" not in body["resolved_flags"]


def test_create_mtp_true_resolved_flags_contains_bundle(client: TestClient) -> None:
    """Custom profile with mtp=True: resolved_flags carries the MTP bundle."""
    r = client.post(
        "/api/profiles",
        json={"name": "with-mtp", "image": "ghcr.io/x/y:z", "flags": "-fa on", "mtp": True},
    )
    assert r.status_code == 201
    body = r.json()
    assert MTP_FLAG_BUNDLE in body["resolved_flags"]
    assert body["resolved_flags"].startswith("-fa on")


def test_create_invalid_empty_image_422(client: TestClient) -> None:
    r = client.post(
        "/api/profiles",
        json={"name": "valid-name", "image": ""},
    )
    assert r.status_code == 422


def test_create_invalid_bad_device_class_422(client: TestClient) -> None:
    r = client.post(
        "/api/profiles",
        json={"name": "valid-name", "image": "ghcr.io/x/y:z", "device_class": "badvalue"},
    )
    assert r.status_code == 422


def test_create_invalid_uppercase_name_422(client: TestClient) -> None:
    r = client.post(
        "/api/profiles",
        json={"name": "MyProfile", "image": "ghcr.io/x/y:z"},
    )
    assert r.status_code == 422


def test_create_invalid_name_with_spaces_422(client: TestClient) -> None:
    r = client.post(
        "/api/profiles",
        json={"name": "my profile", "image": "ghcr.io/x/y:z"},
    )
    assert r.status_code == 422


# ── PUT /api/profiles/{name} ───────────────────────────────────────────────────


def test_update_custom_profile_200(tmp_hal0_home: str) -> None:
    """PUT updates the profile and the change persists across reload."""
    app1 = create_app()
    with TestClient(app1) as c1:
        c1.post(
            "/api/profiles",
            json={"name": "my-vulkan", "image": "ghcr.io/x/y:z", "flags": "-fa on"},
        )
        r = c1.put("/api/profiles/my-vulkan", json={"flags": "-fa off"})
        assert r.status_code == 200
        assert r.json()["flags"] == "-fa off"

    # Verify persisted.
    app2 = create_app()
    with TestClient(app2) as c2:
        listed = c2.get("/api/profiles").json()
    updated = next(p for p in listed if p["name"] == "my-vulkan")
    assert updated["flags"] == "-fa off"


def test_update_only_device_class_preserves_other_fields(client: TestClient) -> None:
    """PUT with ONLY device_class set updates it and preserves the rest."""
    client.post(
        "/api/profiles",
        json={
            "name": "my-vulkan",
            "image": "ghcr.io/x/y:z",
            "flags": "-fa on",
            "mtp": True,
            "device_class": "gpu",
        },
    )
    r = client.put("/api/profiles/my-vulkan", json={"device_class": "cpu"})
    assert r.status_code == 200
    body = r.json()
    assert body["device_class"] == "cpu"
    assert body["image"] == "ghcr.io/x/y:z"
    assert body["flags"] == "-fa on"
    assert body["mtp"] is True
    # Persisted view agrees.
    listed = client.get("/api/profiles").json()
    item = next(p for p in listed if p["name"] == "my-vulkan")
    assert item["device_class"] == "cpu"
    assert item["flags"] == "-fa on"
    assert item["mtp"] is True


def test_update_missing_404(client: TestClient) -> None:
    r = client.put("/api/profiles/does-not-exist", json={"flags": "-fa off"})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "profiles.not_found"


def test_seed_immutable_put_409(client: TestClient) -> None:
    seed_name = next(iter(SEED_PROFILES))
    r = client.put("/api/profiles/" + seed_name, json={"flags": "-fa off"})
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "profiles.seed_immutable"
    assert "clone" in err["message"]


# ── DELETE /api/profiles/{name} ────────────────────────────────────────────────


def test_delete_custom_204(client: TestClient) -> None:
    client.post(
        "/api/profiles",
        json={"name": "my-vulkan", "image": "ghcr.io/x/y:z"},
    )
    r = client.delete("/api/profiles/my-vulkan")
    assert r.status_code == 204
    listed = client.get("/api/profiles").json()
    assert not any(p["name"] == "my-vulkan" for p in listed)


def test_delete_missing_404(client: TestClient) -> None:
    r = client.delete("/api/profiles/does-not-exist")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "profiles.not_found"


def test_seed_immutable_delete_409(client: TestClient) -> None:
    seed_name = next(iter(SEED_PROFILES))
    r = client.delete("/api/profiles/" + seed_name)
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "profiles.seed_immutable"
    assert "clone" in err["message"]


def test_delete_in_use_409(tmp_hal0_home: str) -> None:
    """DELETE a profile that a slot TOML references → 409 profiles.in_use."""
    # Seed slot TOML referencing my-vulkan BEFORE building the app so
    # the slot is on-disk when the route scans list_slots().
    _seed_slot_toml(tmp_hal0_home, "gpu-slot", "my-vulkan")

    app = create_app()
    with TestClient(app) as c:
        # Create the custom profile (seeds are the starting catalog).
        c.post(
            "/api/profiles",
            json={"name": "my-vulkan", "image": "ghcr.io/x/y:z"},
        )
        r = c.delete("/api/profiles/my-vulkan")
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "profiles.in_use"
    assert "gpu-slot" in err["details"]["slots"]


def test_delete_in_use_409_despite_corrupt_sibling_toml(tmp_hal0_home: str) -> None:
    """Corrupt slot TOML next to a valid referencing slot: DELETE still 409."""
    _seed_corrupt_slot_toml(tmp_hal0_home, "broken-slot")
    _seed_slot_toml(tmp_hal0_home, "gpu-slot", "my-vulkan", port=8091)

    app = create_app()
    with TestClient(app) as c:
        c.post(
            "/api/profiles",
            json={"name": "my-vulkan", "image": "ghcr.io/x/y:z"},
        )
        r = c.delete("/api/profiles/my-vulkan")
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "profiles.in_use"
    assert "gpu-slot" in err["details"]["slots"]


def test_delete_succeeds_with_only_corrupt_toml_and_warns(
    tmp_hal0_home: str,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Only a corrupt slot TOML on disk: DELETE succeeds; scan warning fires.

    structlog output routing is GLOBAL and order-dependent across the test
    suite: standalone, this app renders via PrintLogger to stdout (capsys);
    under the full suite another test may bridge structlog into stdlib
    logging (caplog). Assert across BOTH channels — the contract is that
    the warning fires, not where it lands. (Full-suite flake, Phase C gate.)
    """
    _seed_corrupt_slot_toml(tmp_hal0_home, "broken-slot")

    app = create_app()
    with TestClient(app) as c:
        c.post(
            "/api/profiles",
            json={"name": "my-vulkan", "image": "ghcr.io/x/y:z"},
        )
        capsys.readouterr()  # drain startup noise
        with caplog.at_level("WARNING"):
            r = c.delete("/api/profiles/my-vulkan")
        captured = capsys.readouterr()
    assert r.status_code == 204
    log_text = captured.out + captured.err + caplog.text
    assert "profiles.in_use_scan_error" in log_text
    assert "broken-slot" in log_text
