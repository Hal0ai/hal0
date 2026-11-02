"""Pi component arm: a read-only report, because only the CLI can re-pin Pi."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from hal0.agents.pi_coder import PiDriver
from hal0.components import pi_arm, status
from hal0.components.registry import component_by_id

PINS = {"pi": "2.0.0", "adapter": "1.0.0"}


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))


@pytest.fixture
def marker():
    path = PiDriver()._profile_marker_path()
    path.parent.mkdir(parents=True)
    path.write_text("{}", encoding="utf-8")
    return path


def _on_box(pi: str, adapter: str | None):
    return (
        patch("hal0.components.pi_arm.shutil.which", return_value="/usr/bin/pi"),
        patch(
            "hal0.agents.overview.pi_installed_versions",
            return_value={"pi": pi, "adapter": adapter},
        ),
        patch("hal0.agents.overview.pi_pins", return_value=PINS),
    )


def test_catalog_entry() -> None:
    comp = component_by_id("pi")
    assert comp is not None and comp.service_id is None and comp.kind == "npm-global"
    # The real installer script carries both pins.
    assert "adapter" in comp.pinned()


def test_no_marker_is_not_installed_even_with_pi_on_path() -> None:
    a, b, c = _on_box("2.0.0", "1.0.0")
    with a, b, c:
        assert pi_arm.installed_pi() is None
        assert pi_arm.converge_pi() == {"status": "skipped", "reason": "pi not installed"}


@pytest.mark.usefixtures("marker")
def test_on_the_pin_is_converged() -> None:
    a, b, c = _on_box("2.0.0", "1.0.0")
    with a, b, c:
        assert pi_arm.converge_pi()["status"] == "converged"


@pytest.mark.usefixtures("marker")
@pytest.mark.parametrize("apply", [True, False])
def test_behind_the_pin_reports_and_never_touches_the_driver(apply: bool) -> None:
    a, b, c = _on_box("1.9.0", None)
    with a, b, c, patch.object(PiDriver, "upgrade") as upgrade:
        result = pi_arm.converge_pi(job_id="j", apply=apply)
    upgrade.assert_not_called()
    assert result["status"] == "stale"
    assert result["installed"] == "1.9.0 (adapter none)"
    assert result["remedy"] == "run 'sudo hal0 update component pi'"


@pytest.mark.usefixtures("marker")
def test_unreadable_pin_is_skipped_not_a_sticky_failure() -> None:
    a, b, _ = _on_box("2.0.0", "1.0.0")
    with a, b, patch("hal0.agents.overview.pi_pins", side_effect=ValueError("no PI_PKG")):
        result = pi_arm.converge_pi()
    assert result["status"] == "skipped" and "no PI_PKG" in result["reason"]


@pytest.mark.usefixtures("marker")
def test_status_row_behind_the_pin_is_pending() -> None:
    a, b, c = _on_box("1.9.0", "1.0.0")
    with a, b, c:
        row = next(r for r in status.component_status_snapshot()["components"] if r["id"] == "pi")
    assert row["status"] == "pending"
    assert row["pinned"] == "2.0.0 (adapter 1.0.0)"


def test_status_row_without_pi_is_not_installed_and_not_pending() -> None:
    snap = status.component_status_snapshot()
    row = next(r for r in snap["components"] if r["id"] == "pi")
    assert row["status"] == "not-installed"
