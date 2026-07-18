"""Tests for ``GET /api/system-info`` (§21.3 — hardware + features + backend
lifecycle state, folded into one consolidated read surface).

The route composes the already-tested ``get_hardware``/``list_features``
handlers plus a per-``RUNNER_IMAGES`` local install-state classifier; these
tests cover the fold + the podman-absent degrade path (this dev sandbox has
no podman, so every backend should report ``"unavailable"`` rather than
raising or lying about "installable").
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hal0.api.routes.hardware import _backend_state, _image_repo, _local_image_repos
from hal0.security.exposure import AuthClass, classify


def test_system_info_route_folds_hardware_features_backends(
    isolated_client: TestClient,
) -> None:
    resp = isolated_client.get("/api/system-info")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"hardware", "features", "backends"}
    assert isinstance(body["hardware"], dict)
    assert isinstance(body["features"], dict)
    assert isinstance(body["backends"], dict)
    # every RUNNER_IMAGES key is present with the expected shape.
    from hal0.runners import RUNNER_IMAGES

    assert set(body["backends"]) == set(RUNNER_IMAGES)
    for key, entry in body["backends"].items():
        assert entry["state"] in ("installed", "installable", "unavailable")
        assert entry["image"]
        assert entry["device_class"] == RUNNER_IMAGES[key].device_class


def test_system_info_backends_expose_runner_supports_metadata(
    isolated_client: TestClient,
) -> None:
    """UI-API-1 item 2: each backend exposes the runner registry's per-runner
    capability metadata (runtime_family, backend, and the typed launch-support
    gates) so the model drawer can filter to compatible runners."""
    from hal0.runners import RUNNER_IMAGES

    body = isolated_client.get("/api/system-info").json()
    for key, entry in body["backends"].items():
        runner = RUNNER_IMAGES[key]
        assert entry["runtime_family"] == runner.runtime_family
        assert entry["backend"] == runner.backend
        assert entry["supports"] == {
            "mtp": runner.supports.mtp,
            "jinja": runner.supports.jinja,
            "mmproj": runner.supports.mmproj,
        }


def test_system_info_matches_hardware_and_features_endpoints(
    isolated_client: TestClient,
) -> None:
    info = isolated_client.get("/api/system-info").json()
    hardware = isolated_client.get("/api/hardware").json()
    features = isolated_client.get("/api/features").json()

    # The two calls are separate live probes — free-memory/uptime/disk move
    # between them (CI flaked on a 5 MB ram_available_mb delta). Compare the
    # stable shape; volatile gauges only need to exist.
    volatile = {"ram_available_mb", "uptime_s", "disk_free_mb", "probed_at"}
    for key in volatile:
        assert key in info["hardware"]
    stable_info = {k: v for k, v in info["hardware"].items() if k not in volatile}
    stable_hw = {k: v for k, v in hardware.items() if k not in volatile}
    assert stable_info == stable_hw
    assert info["features"] == features


def test_backends_degrade_to_unavailable_without_podman(
    isolated_client: TestClient, monkeypatch
) -> None:
    import hal0.api.routes.hardware as hw_mod

    monkeypatch.setattr(hw_mod, "_local_image_repos", lambda: None)
    body = isolated_client.get("/api/system-info").json()
    assert all(entry["state"] == "unavailable" for entry in body["backends"].values())


# ── pure helpers ─────────────────────────────────────────────────────────────


def test_image_repo_strips_tag_and_digest() -> None:
    assert _image_repo("ghcr.io/hal0ai/tb:v1") == "ghcr.io/hal0ai/tb"
    assert _image_repo("ghcr.io/hal0ai/tb@sha256:abc") == "ghcr.io/hal0ai/tb"


def test_backend_state_unavailable_when_podman_absent() -> None:
    assert _backend_state("ghcr.io/hal0ai/tb:v1", None) == "unavailable"


def test_backend_state_installed_vs_installable() -> None:
    repos = {"ghcr.io/hal0ai/tb"}
    assert _backend_state("ghcr.io/hal0ai/tb:v1", repos) == "installed"
    assert _backend_state("ghcr.io/hal0ai/other:v1", repos) == "installable"


def test_local_image_repos_none_when_podman_missing(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    assert _local_image_repos() is None


# ── exposure classification (HARD REQUIREMENT #3) ───────────────────────────


def test_system_info_is_classified_client_not_admin_fallback() -> None:
    # Pre-existing rule in security/exposure.py (landed with the OBS-1
    # /api/system-stats classification); this test pins it so a future edit
    # can't silently widen/narrow it without the assertion failing.
    assert classify("GET", "/api/system-info") is AuthClass.CLIENT
