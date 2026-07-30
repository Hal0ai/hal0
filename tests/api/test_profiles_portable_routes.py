"""Tests for the portable profile routes — export/import over HTTP.

Mirrors tests/api/test_stacks_routes.py + the profiles CRUD route style:
fresh TestClient over a tmp_hal0_home-isolated app, asserting status codes
and error ``code`` fields.

Targeted file run only (full suite hangs):
    ~/dev/hal0/.venv/bin/python -m pytest tests/api/test_profiles_portable_routes.py -q
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app
from hal0.config.schema import SEED_PROFILES


@pytest.fixture
def app(tmp_hal0_home: str) -> FastAPI:
    """Fresh app; tmp_hal0_home means no profiles.toml → seeds returned."""
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _seed_name() -> str:
    return next(iter(SEED_PROFILES))


def _create_custom(client: TestClient, name: str = "my-custom") -> None:
    r = client.post(
        "/api/profiles",
        json={
            "name": name,
            "flags": "-fa on",
            "mtp": True,
            "device_class": "gpu",
            "backend": "rocm",
            "intent": "My workload",
            "quant": "Q5_K_M",
        },
    )
    assert r.status_code == 201


# ── POST /api/profiles/{name}/export ────────────────────────────────────────


class TestExportRoute:
    def test_export_seed_profile_200_valid_envelope(self, client: TestClient) -> None:
        name = _seed_name()
        r = client.post(f"/api/profiles/{name}/export")
        assert r.status_code == 200
        env = r.json()
        assert env["kind"] == "hal0.profile"
        assert env["name"] == name
        assert env["checksum"].startswith("sha256:")
        assert "image" not in env["profile"]

    def test_export_custom_profile_200_valid_envelope(self, client: TestClient) -> None:
        _create_custom(client)
        r = client.post("/api/profiles/my-custom/export")
        assert r.status_code == 200
        env = r.json()
        assert env["kind"] == "hal0.profile"
        assert env["name"] == "my-custom"
        assert env["profile"]["flags"] == "-fa on"
        assert env["profile"]["backend"] == "rocm"

    def test_export_unknown_404(self, client: TestClient) -> None:
        r = client.post("/api/profiles/does-not-exist/export")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "profiles.not_found"


# ── POST /api/profiles/import (dry_run) ─────────────────────────────────────


class TestImportDryRun:
    def test_dry_run_shape_and_checksum_ok(self, client: TestClient) -> None:
        env = client.post(f"/api/profiles/{_seed_name()}/export").json()
        r = client.post("/api/profiles/import", json={"envelope": env, "dry_run": True})
        assert r.status_code == 200
        body = r.json()
        assert body["dry_run"] is True
        assert body["valid"] is True
        assert body["checksum_ok"] is True
        assert body["name"] == _seed_name()
        assert body["schema_version"] == env["schema_version"]
        assert isinstance(body["collides"], bool)

    def test_dry_run_collides_true_for_existing_name(self, client: TestClient) -> None:
        env = client.post(f"/api/profiles/{_seed_name()}/export").json()
        # Default target name is the envelope's own (seed) name → collides.
        r = client.post("/api/profiles/import", json={"envelope": env, "dry_run": True})
        assert r.json()["collides"] is True

    def test_dry_run_collides_false_for_fresh_name(self, client: TestClient) -> None:
        env = client.post(f"/api/profiles/{_seed_name()}/export").json()
        r = client.post(
            "/api/profiles/import",
            json={"envelope": env, "name": "brand-new-name", "dry_run": True},
        )
        assert r.json()["collides"] is False

    def test_dry_run_checksum_ok_false_when_tampered(self, client: TestClient) -> None:
        env = client.post(f"/api/profiles/{_seed_name()}/export").json()
        env["profile"]["flags"] = "-fa off TAMPERED"
        r = client.post("/api/profiles/import", json={"envelope": env, "dry_run": True})
        assert r.json()["checksum_ok"] is False


# ── POST /api/profiles/import (commit) ──────────────────────────────────────


class TestImportCommit:
    def test_commit_creates_profile(self, client: TestClient) -> None:
        env = client.post(f"/api/profiles/{_seed_name()}/export").json()
        r = client.post("/api/profiles/import", json={"envelope": env, "name": "imported-one"})
        assert r.status_code == 201
        body = r.json()
        assert body["dry_run"] is False
        assert body["profile"]["name"] == "imported-one"
        # Now resolvable via GET /api/profiles/{name}.
        got = client.get("/api/profiles/imported-one")
        assert got.status_code == 200
        assert got.json()["name"] == "imported-one"

    def test_commit_without_name_400(self, client: TestClient) -> None:
        env = client.post(f"/api/profiles/{_seed_name()}/export").json()
        r = client.post("/api/profiles/import", json={"envelope": env})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "profiles.import_no_name"

    def test_commit_duplicate_name_409(self, client: TestClient) -> None:
        env = client.post(f"/api/profiles/{_seed_name()}/export").json()
        client.post("/api/profiles/import", json={"envelope": env, "name": "dup"})
        r = client.post("/api/profiles/import", json={"envelope": env, "name": "dup"})
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "profiles.exists"

    def test_commit_bad_envelope_400(self, client: TestClient) -> None:
        r = client.post(
            "/api/profiles/import",
            json={"envelope": {"kind": "nope"}, "name": "whatever"},
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "profiles.bad_envelope"

    def test_commit_too_new_schema_400(self, client: TestClient) -> None:
        env = client.post(f"/api/profiles/{_seed_name()}/export").json()
        env["schema_version"] = env["schema_version"] + 1
        r = client.post("/api/profiles/import", json={"envelope": env, "name": "too-new"})
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "profiles.envelope_too_new"


# ── #1416: commit verifies the checksum + screens the flags ──────────────────
#
# `verify_checksum` used to be referenced ONLY inside the `if dry_run:` branch,
# so the checksum was decorative on the one path that writes: a tampered or
# transport-corrupted envelope imported with a 200. The flag screen the POST and
# PUT both apply was missing from the same path, letting an envelope back-door
# `-ngl`/`-dev`/`--port` into the catalog.


class TestImportCommitIntegrity:
    def test_commit_rejects_tampered_checksum(self, client: TestClient) -> None:
        env = client.post(f"/api/profiles/{_seed_name()}/export").json()
        env["checksum"] = "sha256:deadbeef"
        r = client.post("/api/profiles/import", json={"envelope": env, "name": "tampered"})
        assert r.status_code == 400, r.text
        assert r.json()["error"]["code"] == "profiles.checksum_mismatch"
        # And nothing was written.
        assert client.get("/api/profiles/tampered").status_code == 404

    def test_commit_rejects_drifted_body(self, client: TestClient) -> None:
        """The checksum covers the profile body, so editing the body without
        restamping is the same failure — this is the shape a lossy transport or
        a hand-edit produces."""
        env = client.post(f"/api/profiles/{_seed_name()}/export").json()
        env["profile"]["flags"] = "-fa off"
        r = client.post("/api/profiles/import", json={"envelope": env, "name": "drifted"})
        assert r.status_code == 400, r.text
        assert r.json()["error"]["code"] == "profiles.checksum_mismatch"
        assert client.get("/api/profiles/drifted").status_code == 404

    def test_commit_force_bypasses_the_checksum(self, client: TestClient) -> None:
        """`force` is the documented escape hatch for a deliberately hand-edited
        envelope — it waives the INTEGRITY check only."""
        env = client.post(f"/api/profiles/{_seed_name()}/export").json()
        env["checksum"] = "sha256:deadbeef"
        r = client.post(
            "/api/profiles/import",
            json={"envelope": env, "name": "forced", "force": True},
        )
        assert r.status_code == 200, r.text
        assert client.get("/api/profiles/forced").status_code == 200

    def test_commit_screens_slot_hardware_flags(self, client: TestClient) -> None:
        """An envelope may not back-door a §5 hardware flag past the guard the
        POST/PUT paths both apply — not even with `force`."""
        _create_custom(client, name="hw-src")
        env = client.post("/api/profiles/hw-src/export").json()
        env["profile"]["flags"] = "-fa on -dev ROCm0"
        r = client.post(
            "/api/profiles/import",
            json={"envelope": env, "name": "hw-import", "force": True},
        )
        assert r.status_code == 400, r.text
        assert r.json()["error"]["code"] == "slot.hardware_flag_denied"
        assert client.get("/api/profiles/hw-import").status_code == 404

    def test_commit_screens_managed_flags(self, client: TestClient) -> None:
        _create_custom(client, name="mg-src")
        env = client.post("/api/profiles/mg-src/export").json()
        env["profile"]["flags"] = "-fa on --port 9999"
        r = client.post(
            "/api/profiles/import",
            json={"envelope": env, "name": "mg-import", "force": True},
        )
        assert r.status_code == 400, r.text
        assert r.json()["error"]["code"] == "slot.managed_arg_denied"
        assert client.get("/api/profiles/mg-import").status_code == 404

    def test_untampered_commit_still_succeeds(self, client: TestClient) -> None:
        """Regression fence: the happy path must not need `force`."""
        env = client.post(f"/api/profiles/{_seed_name()}/export").json()
        r = client.post("/api/profiles/import", json={"envelope": env, "name": "clean-import"})
        assert r.status_code == 200, r.text
        assert client.get("/api/profiles/clean-import").status_code == 200


# ── round-trip over HTTP ────────────────────────────────────────────────────


class TestRoundTripHttp:
    def test_export_then_import_under_new_name_appears_in_list(self, client: TestClient) -> None:
        _create_custom(client, name="source")
        env = client.post("/api/profiles/source/export").json()

        r = client.post("/api/profiles/import", json={"envelope": env, "name": "round-tripped"})
        assert r.status_code == 201

        names = {p["name"] for p in client.get("/api/profiles").json()}
        assert "round-tripped" in names

        imported = client.get("/api/profiles/round-tripped").json()
        assert imported["flags"] == "-fa on"
        assert imported["intent"] == "My workload"
        assert imported["quant"] == "Q5_K_M"
        assert imported["backend"] == "rocm"
