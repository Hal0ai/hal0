"""Tests for ``GET /api/system-info`` (§21.3 — hardware + features + backend
lifecycle state, folded into one consolidated read surface).

The route composes the already-tested ``get_hardware``/``list_features``
handlers plus a per-``RUNNER_IMAGES`` local install-state classifier; these
tests cover the fold + the podman-absent degrade path (this dev sandbox has
no podman, so every backend should report ``"unavailable"`` rather than
raising or lying about "installable").

O12: ``_local_image_repos`` now routes through
:mod:`hal0.providers.podman_introspect` (the rootful ``hal0-podman-ro`` seam,
falling back to a rootless read) instead of hand-rolling a bare ``podman
images`` call; the route surfaces which store served the read as
``podman_context``.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hal0.api.routes.hardware import _backend_state, _image_repo, _local_image_repos
from hal0.providers.podman_introspect import PodmanImagesResult
from hal0.security.exposure import AuthClass, classify


def test_system_info_route_folds_hardware_features_backends(
    isolated_client: TestClient,
) -> None:
    resp = isolated_client.get("/api/system-info")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"hardware", "features", "backends", "podman_context"}
    assert isinstance(body["hardware"], dict)
    assert isinstance(body["features"], dict)
    assert isinstance(body["backends"], dict)
    assert body["podman_context"] in ("rootful", "rootless", "unavailable")
    # every RUNNER_IMAGES key is present with the expected shape.
    from hal0.runners import RUNNER_IMAGES

    assert set(body["backends"]) == set(RUNNER_IMAGES)
    for key, entry in body["backends"].items():
        assert entry["state"] in ("installed", "installable", "unavailable")
        assert entry["image"]
        assert entry["device_class"] == RUNNER_IMAGES[key].device_class
        # Removed with the fresh runner system: the per-runner default flag
        # (tier is the one source) and the arch veto list.
        assert "is_default" not in entry
        assert "unsupported_archs" not in entry


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


def test_system_info_backends_have_no_specialties(
    isolated_client: TestClient,
) -> None:
    """``supports.specialties`` left the wire with the promptforge runner."""
    body = isolated_client.get("/api/system-info").json()
    for key, entry in body["backends"].items():
        assert "specialties" not in entry["supports"], key


def test_system_info_backends_expose_fit_check_metadata(
    isolated_client: TestClient,
) -> None:
    """spec-hw-slot-ownership §4: each backend exposes the runner registry's
    fit-check metadata (``supported_backends`` + ``format_arch``) so the UI
    slot-HW-grid fit-check and the Runtimes panel can read registry truth."""
    from hal0.runners import RUNNER_IMAGES

    body = isolated_client.get("/api/system-info").json()
    for key, entry in body["backends"].items():
        runner = RUNNER_IMAGES[key]
        assert entry["supported_backends"] == list(runner.supported_backends)
        assert entry["format_arch"] == runner.format_arch
        # A slot's own device backend must resolve against this list — every
        # runner declares at least one supported backend.
        assert isinstance(entry["supported_backends"], list)
        assert entry["supported_backends"], f"{key} declares no supported_backends"


def test_system_info_backend_display_metadata(
    isolated_client: TestClient,
) -> None:
    """runtime-cascade D2/D5 + ADR-0026: each backend exposes the runner
    registry's operator-facing title/blurb and its tier, so the dashboard
    presents runtimes by human name and sorts them by the same tier the
    Runner Images page groups by."""
    from hal0.runners import RUNNER_IMAGES

    payload = isolated_client.get("/api/system-info").json()
    standard = payload["backends"]["standard"]
    assert standard["title"] == "Standard"
    assert standard["blurb"]
    assert standard["tier"] == "default"
    assert payload["backends"]["qwen3tts"]["tier"] == "optional"
    assert payload["backends"]["cuda"]["tier"] == "optional"
    # The payload is a projection of the registry, never its own opinion.
    for key, runner in RUNNER_IMAGES.items():
        assert payload["backends"][key]["tier"] == runner.tier, key
        assert payload["backends"][key]["title"] == runner.title, key
        assert payload["backends"][key]["blurb"] == runner.blurb, key


def test_system_info_backend_image_is_the_runner_pin_ref(
    isolated_client: TestClient, monkeypatch
) -> None:
    """``image`` is ``resolve_runner_image``: the release pin ref, or the
    ``HAL0_RUNNER_IMAGE_<KEY>`` env override when one is set."""
    from hal0.runners.pins import RunnerPin, load_runner_pins

    digest = "sha256:" + "d" * 64
    table = dict(load_runner_pins())
    table["standard"] = RunnerPin(
        key="standard",
        image="ghcr.io/hal0ai/hal0-runner-standard",
        tag="b2-r1",
        digest=digest,
    )
    table["flm"] = RunnerPin(key="flm", image="ghcr.io/hal0ai/hal0-runner-flm")
    monkeypatch.setattr("hal0.runners.pins.runner_pins", lambda: table)
    monkeypatch.setenv("HAL0_RUNNER_IMAGE_CPU", "ghcr.io/me/my-cpu:dev")

    backends = isolated_client.get("/api/system-info").json()["backends"]
    assert backends["standard"]["image"] == f"ghcr.io/hal0ai/hal0-runner-standard:b2-r1@{digest}"
    assert backends["cpu"]["image"] == "ghcr.io/me/my-cpu:dev"
    # An unpublished pin (tag null) has no image, never the bare repo.
    assert backends["flm"]["image"] is None


def test_system_info_unresolvable_pin_reports_no_image_and_unavailable(
    isolated_client: TestClient, monkeypatch
) -> None:
    """A runner whose pin cannot be resolved reports ``image: null`` and state
    ``"unavailable"`` — never a 500, and never "installed" off a guessed ref —
    while the other runners are unaffected."""
    import hal0.api.routes.hardware as hw_mod
    import hal0.runners as runners_mod

    real_resolve = runners_mod.resolve_runner_image

    def _resolve(runner):
        if runner.key == "cuda":
            raise RuntimeError("manifest.json runners table has no row for runner 'cuda'")
        return real_resolve(runner)

    monkeypatch.setattr(runners_mod, "resolve_runner_image", _resolve)
    # Every shipped repo is "on disk": a resolvable runner reads installed.
    all_repos = {hw_mod._image_repo(real_resolve(r)) for r in runners_mod.RUNNER_IMAGES.values()}
    monkeypatch.setattr(
        hw_mod,
        "_local_image_repos",
        lambda: PodmanImagesResult(repos=all_repos, context="rootful"),
    )

    resp = isolated_client.get("/api/system-info")
    assert resp.status_code == 200
    backends = resp.json()["backends"]
    assert backends["cuda"]["image"] is None
    assert backends["cuda"]["state"] == "unavailable"
    assert backends["cuda"]["provenance"] is None
    assert backends["standard"]["image"]
    assert backends["standard"]["state"] == "installed"


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
    assert body["podman_context"] == "unavailable"


def test_system_info_surfaces_rootful_podman_context(
    isolated_client: TestClient, monkeypatch
) -> None:
    """O12: when the rootful seam serves the read, the route says so — the
    UI/caller can trust the backend states came from the store slots use."""
    import hal0.api.routes.hardware as hw_mod

    monkeypatch.setattr(
        hw_mod,
        "_local_image_repos",
        lambda: PodmanImagesResult(repos=set(), context="rootful"),
    )
    body = isolated_client.get("/api/system-info").json()
    assert body["podman_context"] == "rootful"


def test_system_info_surfaces_rootless_podman_context(
    isolated_client: TestClient, monkeypatch
) -> None:
    """O12: when the seam wasn't usable and the read fell back to hal0-api's
    OWN store, the route flags that too — a "rootless" context may disagree
    with what's actually installed for slots."""
    import hal0.api.routes.hardware as hw_mod

    monkeypatch.setattr(
        hw_mod,
        "_local_image_repos",
        lambda: PodmanImagesResult(repos=set(), context="rootless"),
    )
    body = isolated_client.get("/api/system-info").json()
    assert body["podman_context"] == "rootless"


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
