"""Tests for /api/updates — check / apply / status / rollback / channel.

The release-manifest source is parameterised via ``HAL0_RELEASES_URL``;
tests point at a local JSON file written into the pytest tmp dir.
"""

from __future__ import annotations

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


def test_check_revoked_latest_not_recommended(
    isolated_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A revoked latest must report update_available=False + surface the reason."""
    manifest = _write_manifest(
        tmp_path / "revoked.json",
        {
            "version": "99.9.9",
            "url": "https://example.test/hal0-99.9.9.tar.gz",
            "revoked": True,
            "revoked_reason": "yanked: corrupt tarball",
        },
    )
    monkeypatch.setenv("HAL0_RELEASES_URL", str(manifest))

    r = isolated_client.get("/api/updates/check")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["latest"] == "99.9.9"
    assert body["update_available"] is False
    assert body["revoked"] is True
    assert body["revoked_reason"] == "yanked: corrupt tarball"


def test_state_revoked_latest_surfaced(
    isolated_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /api/updates/state nests revoked info in the hal0 block."""
    from hal0.api.routes import updater as u_mod

    monkeypatch.setattr(u_mod, "_probe_version", lambda _c: None)
    manifest = _write_manifest(
        tmp_path / "revoked.json",
        {
            "version": "99.9.9",
            "url": "https://example.test/hal0-99.9.9.tar.gz",
            "revoked": True,
            "revoked_reason": "yanked: corrupt tarball",
        },
    )
    monkeypatch.setenv("HAL0_RELEASES_URL", str(manifest))

    r = isolated_client.get("/api/updates/state")
    assert r.status_code == 200, r.text
    body = r.json()
    # Top-level shape unchanged for the dashboard contract.
    assert set(body.keys()) == {"hal0", "lemonade", "flm", "autoCheck"}
    # Revoked latest is not advertised as available …
    assert body["hal0"]["available"] is None
    # … but the withdrawal is surfaced so the UI can show a note.
    assert body["hal0"]["revoked"] is True
    assert body["hal0"]["revoked_reason"] == "yanked: corrupt tarball"
    assert body["hal0"]["revoked_version"] == "99.9.9"


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


def test_apply_status_eventually_failed_on_invalid_manifest(
    isolated_client: TestClient,
) -> None:
    """Apply transitions queued -> running -> failed on a bad manifest.

    The isolated_client manifest is minimal ({"version", "url"}) so it
    fails schema validation inside ``Updater.apply()``. The route layer is
    what's under test here - that the background task runs, records its
    error, and the status endpoint reflects it.
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
        time.sleep(0.05)
    assert final.get("state") == "failed", f"expected failed, got {final}"
    assert final.get("error")


def test_rollback_without_previous_returns_envelope(
    isolated_client: TestClient,
) -> None:
    """Rollback with no previous-version record surfaces the typed envelope.

    ``Updater.rollback()`` raises ``UpdateRollbackUnavailable`` (a typed
    Hal0Error) which the route now surfaces directly instead of wrapping
    it - the dashboard keys off the structured code.
    """
    r = isolated_client.post("/api/updates/rollback")
    assert r.status_code in (400, 500, 501)
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == "system.update_rollback_unavailable"


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


def test_channel_put_rejects_dev(isolated_client: TestClient) -> None:
    """The ``dev`` channel is not a valid update channel (reconciled #510)."""
    r = isolated_client.put("/api/updates/channel", json={"channel": "dev"})
    assert r.status_code in (400, 500)
    body = r.json()
    assert "error" in body
    assert "dev" not in body["error"]["details"]["allowed"]


# ── #509: durable job store + hal0-api restart on apply ────────────────────────


def test_apply_persists_job_to_disk(isolated_client: TestClient, tmp_hal0_home: str) -> None:
    """A queued apply job is written under /var/lib/hal0/update-jobs/<id>.json."""
    r = isolated_client.post("/api/updates/apply", json={})
    assert r.status_code == 202, r.text
    job_id = r.json()["id"]

    job_file = Path(tmp_hal0_home) / "var-lib" / "hal0" / "update-jobs" / f"{job_id}.json"
    # The queued snapshot is persisted synchronously before the route returns.
    assert job_file.exists(), f"expected on-disk job record at {job_file}"
    on_disk = json.loads(job_file.read_text(encoding="utf-8"))
    assert on_disk["id"] == job_id
    assert "state" in on_disk
    assert "updated_at" in on_disk


def test_status_falls_back_to_disk_after_restart(
    isolated_client: TestClient, tmp_hal0_home: str
) -> None:
    """Status poll resolves from disk even when the in-memory dict was wiped.

    Simulates an ``hal0-api`` restart mid-apply: the process-local
    ``app.state.update_jobs`` dict is cleared, but the durable record on
    disk lets the CLI's status poll still resolve (no 404 -> 600s timeout).
    """
    r = isolated_client.post("/api/updates/apply", json={})
    job_id = r.json()["id"]

    # Wait for the background task to land a terminal state on disk.
    job_file = Path(tmp_hal0_home) / "var-lib" / "hal0" / "update-jobs" / f"{job_id}.json"
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline:
        if job_file.exists():
            rec = json.loads(job_file.read_text(encoding="utf-8"))
            if rec.get("state") in ("applied", "failed"):
                break
        time.sleep(0.05)

    # Simulate the restart: blow away the in-memory registry.
    isolated_client.app.state.update_jobs = {}

    s = isolated_client.get(f"/api/updates/status/{job_id}")
    assert s.status_code == 200, s.text
    body = s.json()
    assert body["id"] == job_id
    assert body["state"] in ("queued", "running", "applied", "failed")


def test_apply_success_tries_restart_hal0_api_fail_soft(
    isolated_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful apply records a restart breadcrumb and never tears down.

    We stub ``Updater.apply`` to succeed and capture the systemctl call so
    the test doesn't depend on systemd. A restart failure must be fail-soft
    (logged + recorded), not fatal to the applied job.
    """
    from hal0.api.routes import updater as u_mod

    async def fake_apply(self: object, version: str | None = None) -> dict:
        return {"version": "0.0.9", "installed_at": time.time()}

    monkeypatch.setattr(u_mod.Updater, "apply", fake_apply)

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], *a: object, **k: object) -> object:
        calls.append(cmd)

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        return _R()

    monkeypatch.setattr(u_mod.subprocess, "run", fake_run)

    r = isolated_client.post("/api/updates/apply", json={})
    job_id = r.json()["id"]

    deadline = time.monotonic() + 6.0
    final: dict = {}
    while time.monotonic() < deadline:
        final = isolated_client.get(f"/api/updates/status/{job_id}").json()
        if final["state"] in ("applied", "failed"):
            break
        time.sleep(0.05)

    assert final.get("state") == "applied", final
    # systemctl try-restart hal0-api.service was invoked.
    assert any("try-restart" in c and "hal0-api.service" in c for c in calls), calls
    assert final.get("restarted") is True


def test_apply_success_restart_failure_is_fail_soft(
    isolated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the restart raises, the job is still 'applied' with a breadcrumb."""
    from hal0.api.routes import updater as u_mod

    async def fake_apply(self: object, version: str | None = None) -> dict:
        return {"version": "0.0.9"}

    monkeypatch.setattr(u_mod.Updater, "apply", fake_apply)

    def boom_run(cmd: list[str], *a: object, **k: object) -> object:
        raise OSError("systemctl exploded")

    monkeypatch.setattr(u_mod.subprocess, "run", boom_run)

    r = isolated_client.post("/api/updates/apply", json={})
    job_id = r.json()["id"]

    deadline = time.monotonic() + 6.0
    final: dict = {}
    while time.monotonic() < deadline:
        final = isolated_client.get(f"/api/updates/status/{job_id}").json()
        if final["state"] in ("applied", "failed"):
            break
        time.sleep(0.05)

    # The just-installed tree is NOT torn down: the job stays applied.
    assert final.get("state") == "applied", final
    assert final.get("restarted") is False
    assert final.get("restart_error")


def test_apply_target_strips_leading_v(
    isolated_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST /api/updates/apply normalizes a leading 'v' on the version.

    ``{"version": "v0.1.1"}`` and ``{"version": "0.1.1"}`` must drive the
    same target so the CLI and direct API callers behave identically (#510).
    """
    from hal0.api.routes import updater as u_mod

    seen: list[str | None] = []

    async def fake_apply(self: object, version: str | None = None) -> dict:
        seen.append(version)
        return {"version": version or "latest"}

    monkeypatch.setattr(u_mod.Updater, "apply", fake_apply)
    monkeypatch.setattr(u_mod.subprocess, "run", lambda *a, **k: type("R", (), {"returncode": 0})())

    r = isolated_client.post("/api/updates/apply", json={"version": "v0.1.1"})
    job_id = r.json()["id"]
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline:
        if isolated_client.get(f"/api/updates/status/{job_id}").json()["state"] in (
            "applied",
            "failed",
        ):
            break
        time.sleep(0.05)

    assert seen == ["0.1.1"], seen
