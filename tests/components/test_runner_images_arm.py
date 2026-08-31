"""Runner-images arm: detail rows + retag pass wrapper."""

from __future__ import annotations

from unittest.mock import MagicMock

from hal0.components import runner_images_arm
from hal0.runners import RUNNER_IMAGES


def test_detail_covers_every_runner() -> None:
    res = runner_images_arm.converge_runner_images(retag=MagicMock(return_value=0))
    assert {d["key"] for d in res["detail"]} == set(RUNNER_IMAGES)
    assert all(d["image"] for d in res["detail"])


def test_apply_runs_retag_and_converges() -> None:
    retag = MagicMock(return_value=2)
    res = runner_images_arm.converge_runner_images(retag=retag)
    retag.assert_called_once()
    assert res["status"] == "converged"
    assert res["retagged"] == 2


def test_diagnose_only_skips_retag() -> None:
    retag = MagicMock()
    res = runner_images_arm.converge_runner_images(apply=False, retag=retag)
    retag.assert_not_called()
    assert res["status"] == "converged"


def test_retag_failure_is_build_failed() -> None:
    res = runner_images_arm.converge_runner_images(retag=MagicMock(side_effect=OSError("disk")))
    assert res["status"] == "build_failed"
