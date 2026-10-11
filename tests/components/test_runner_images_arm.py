"""Runner-images arm: detail rows + catalogue sync wrapper."""

from __future__ import annotations

from unittest.mock import MagicMock

from hal0.components import runner_images_arm
from hal0.runners import RUNNER_IMAGES, resolve_runner_image


def test_detail_covers_every_runner() -> None:
    res = runner_images_arm.converge_runner_images(apply=False)
    assert {d["key"] for d in res["detail"]} == set(RUNNER_IMAGES)
    assert all(d["image"] for d in res["detail"])


def test_detail_reports_the_resolved_image_per_runner() -> None:
    """Each row is what ``resolve_runner_image`` answers: the pin ref or an
    ``HAL0_RUNNER_IMAGE_<KEY>`` override."""
    res = runner_images_arm.converge_runner_images(apply=False)
    by_key = {d["key"]: d["image"] for d in res["detail"]}
    for key, runner in RUNNER_IMAGES.items():
        assert by_key[key] == resolve_runner_image(runner)


def test_detail_follows_an_env_override(monkeypatch) -> None:
    monkeypatch.setenv("HAL0_RUNNER_IMAGE_KOKORO", "ghcr.io/me/kokoro:test")
    res = runner_images_arm.converge_runner_images(apply=False)
    assert {d["key"]: d["image"] for d in res["detail"]}["kokoro"] == "ghcr.io/me/kokoro:test"


def test_apply_runs_the_catalogue_sync_and_converges() -> None:
    sync = MagicMock(return_value=8)
    res = runner_images_arm.converge_runner_images(sync=sync)
    sync.assert_called_once_with()
    assert res["status"] == "converged"
    assert res["catalogue_rows"] == 8
    assert "catalogue_sync_error" not in res


def test_diagnose_only_skips_the_catalogue_sync() -> None:
    sync = MagicMock()
    res = runner_images_arm.converge_runner_images(apply=False, sync=sync)
    sync.assert_not_called()
    assert res["status"] == "converged"
    assert "catalogue_rows" not in res


def test_sync_failure_is_recorded_and_never_fails_the_update() -> None:
    """The catalogue is display state: a failed fetch is reported on the result
    and the arm still reports ``converged``."""
    res = runner_images_arm.converge_runner_images(sync=MagicMock(side_effect=OSError("disk")))
    assert res["status"] == "converged"
    assert res["catalogue_sync_error"] == "disk"
    assert "catalogue_rows" not in res


def test_default_sync_raises_on_an_images_json_error(monkeypatch) -> None:
    """With no injected sync the arm runs the real catalogue sync; an
    ``images_json_error`` on its result surfaces as the recorded error."""
    import hal0.registry.runner_image_sync as sync_mod

    fake = MagicMock()
    fake.images_json_error = "fetch failed: 503"
    fake.images = []
    monkeypatch.setattr(sync_mod, "sync_catalogue_blocking", lambda: fake)
    res = runner_images_arm.converge_runner_images()
    assert res["status"] == "converged"
    assert res["catalogue_sync_error"] == "fetch failed: 503"
