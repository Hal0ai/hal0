"""Tests for /api/updates — check / apply / status / rollback / channel.

The release-manifest source is parameterised via ``HAL0_RELEASES_URL``;
tests point at a local JSON file written into the pytest tmp dir.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hal0.api import create_app


def _write_manifest(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture
def isolated_client(
    tmp_hal0_home: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[TestClient]:
    """TestClient with HAL0_RELEASES_URL pointing at a tmp file."""
    manifest = _write_manifest(
        tmp_path / "latest.json",
        {"version": "9.9.9", "url": "https://example.test/hal0-9.9.9.tar.gz"},
    )
    monkeypatch.setenv("HAL0_RELEASES_URL", str(manifest))

    app: FastAPI = create_app()
    with TestClient(app) as c:
        # stash for tests that need to rewrite the manifest mid-flight
        c.headers.update({})  # no-op; pattern hook
        c.__dict__["_manifest_path"] = manifest
        yield c


def test_check_returns_well_formed_envelope_with_update_available(
    isolated_client: TestClient,
) -> None:
    """GET /api/updates/check parses the manifest and computes update_available."""
    r = isolated_client.get("/api/updates/check")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "current" in body
    assert "latest" in body and body["latest"] == "9.9.9"
    assert body["channel"] == "stable"
    assert body["update_available"] is True
    assert body["manifest_url"].endswith("latest.json")
    assert isinstance(body["manifest"], dict)


def test_state_returns_shape_expected_by_dashboard(
    isolated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /api/updates/state matches the UpdateState shape in useUpdates.ts.

    Probes are stubbed so the test doesn't depend on lemonade/flm being
    installed on the test host. Issue #233.
    """
    from hal0.api.routes import updater as u_mod

    monkeypatch.setattr(u_mod, "_probe_version", lambda _c: None)

    r = isolated_client.get("/api/updates/state")
    assert r.status_code == 200, r.text
    body = r.json()
    # Top-level keys mirror UpdateState in ui/src/api/hooks/useUpdates.ts.
    assert set(body.keys()) == {"hal0", "lemonade", "flm", "autoCheck"}
    assert body["hal0"]["current"]  # __version__ is non-empty
    assert body["hal0"]["available"] == "9.9.9"
    assert body["hal0"]["channel"] == "stable"
    # Probes returned None — UI should render '—' rather than fabricated versions.
    assert body["lemonade"]["current"] is None
    assert body["flm"]["current"] is None
    assert body["autoCheck"] is True


def test_state_parses_lemonade_and_flm_version_strings(
    isolated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Probe output ``lemonade version 10.6.0`` / ``FLM v0.9.42`` parses cleanly."""
    from hal0.api.routes import updater as u_mod

    def fake_probe(candidates: tuple[str, ...]) -> str | None:
        # First candidate of the lemonade tuple is /opt/lemonade/lemonade.
        if "lemonade" in candidates[0]:
            return "lemonade version 10.6.0"
        if candidates[0] == "flm":
            return "FLM v0.9.42"
        return None

    monkeypatch.setattr(u_mod, "_probe_version", fake_probe)

    r = isolated_client.get("/api/updates/state")
    assert r.status_code == 200
    body = r.json()
    assert body["lemonade"]["current"] == "v10.6.0"
    assert body["flm"]["current"] == "v0.9.42"


def test_state_survives_manifest_fetch_failure(
    isolated_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing release-manifest must NOT 5xx /state — degrade hal0.available to None."""
    missing = tmp_path / "nope.json"
    monkeypatch.setenv("HAL0_RELEASES_URL", str(missing))
    from hal0.api.routes import updater as u_mod

    monkeypatch.setattr(u_mod, "_probe_version", lambda _c: None)

    r = isolated_client.get("/api/updates/state")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["hal0"]["available"] is None
    assert body["hal0"]["current"]  # still reports our version


def test_check_no_update_when_versions_match(
    isolated_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When manifest.version == hal0.__version__, update_available is False."""
    from hal0 import __version__

    manifest = _write_manifest(
        tmp_path / "match.json",
        {"version": __version__},
    )
    monkeypatch.setenv("HAL0_RELEASES_URL", str(manifest))

    r = isolated_client.get("/api/updates/check")
    assert r.status_code == 200
    body = r.json()
    assert body["update_available"] is False
    assert body["latest"] == __version__


def test_check_bad_manifest_returns_envelope(
    isolated_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A garbage manifest file yields the structured update_error envelope."""
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("HAL0_RELEASES_URL", str(bad))

    r = isolated_client.get("/api/updates/check")
    assert r.status_code == 500
    body = r.json()
    assert body["error"]["code"] == "system.update_error"


def test_check_missing_manifest_returns_envelope(
    isolated_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing release-manifest path yields the typed envelope."""
    missing = tmp_path / "does-not-exist.json"
    monkeypatch.setenv("HAL0_RELEASES_URL", str(missing))

    r = isolated_client.get("/api/updates/check")
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "system.update_error"


def test_apply_creates_a_queued_job_returning_id(isolated_client: TestClient) -> None:
    """POST /api/updates/apply returns a job with id + state, runs in background.

    Status code is 202 Accepted (issue #37) — the route queues background
    work and returns immediately, matching /api/models/{id}/pull's shape.
    """
    r = isolated_client.post("/api/updates/apply", json={})
    assert r.status_code == 202, r.text
    body = r.json()
    assert "id" in body and isinstance(body["id"], str)
    assert body["state"] in ("queued", "running", "failed")
    assert body["channel"] == "stable"


def test_status_unknown_job_returns_envelope(isolated_client: TestClient) -> None:
    """Unknown job id surfaces the typed not_found envelope."""
    r = isolated_client.get("/api/updates/status/does-not-exist")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "system.update_job_not_found"


def test_apply_status_eventually_failed_until_team_d_ports(
    isolated_client: TestClient,
) -> None:
    """Apply transitions queued → running → failed because Updater.apply()
    raises NotImplementedError until Team D ports the symlink swap.

    The route layer is what's under test here — that the background task
    runs, records its error, and the status endpoint reflects it.
    """
    r = isolated_client.post("/api/updates/apply", json={})
    job_id = r.json()["id"]

    # Poll up to a couple of seconds for the background task to land.
    deadline = time.monotonic() + 3.0
    final: dict = {}
    while time.monotonic() < deadline:
        s = isolated_client.get(f"/api/updates/status/{job_id}")
        assert s.status_code == 200
        final = s.json()
        if final["state"] in ("failed", "applied"):
            break
        # Give the event loop a chance to run the background task.
        asyncio.run(asyncio.sleep(0.05))
    assert final.get("state") == "failed", f"expected failed, got {final}"
    assert final.get("error")


def test_rollback_returns_pending_envelope_until_ported(
    isolated_client: TestClient,
) -> None:
    """Rollback surfaces the NotImplementedError as a typed envelope, not a 500."""
    r = isolated_client.post("/api/updates/rollback")
    assert r.status_code in (400, 500, 501)
    body = r.json()
    assert "error" in body
    # The message should mention rollback or team-d so the dashboard can
    # render a clear "rollback pending" hint.
    assert "rollback" in body["error"]["message"].lower()


def test_channel_get_returns_stable_default(isolated_client: TestClient) -> None:
    """GET /api/updates/channel returns the default stable channel."""
    r = isolated_client.get("/api/updates/channel")
    assert r.status_code == 200
    assert r.json() == {"channel": "stable"}


def test_channel_put_persists_to_hal0_toml(isolated_client: TestClient, tmp_hal0_home: str) -> None:
    """PUT /api/updates/channel writes telemetry.channel into hal0.toml."""
    r = isolated_client.put("/api/updates/channel", json={"channel": "nightly"})
    assert r.status_code == 200, r.text
    assert r.json() == {"channel": "nightly"}

    # On disk?
    import tomllib

    toml_path = Path(tmp_hal0_home) / "etc" / "hal0" / "hal0.toml"
    assert toml_path.exists()
    parsed = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    assert parsed["telemetry"]["channel"] == "nightly"

    # GET reflects the change.
    r2 = isolated_client.get("/api/updates/channel")
    assert r2.json() == {"channel": "nightly"}


def test_channel_put_invalid_value_returns_envelope(isolated_client: TestClient) -> None:
    """Invalid channel value rejects with a typed envelope."""
    r = isolated_client.put("/api/updates/channel", json={"channel": "beta"})
    assert r.status_code in (400, 500)
    body = r.json()
    assert "error" in body
    assert "allowed" in body["error"]["details"]
