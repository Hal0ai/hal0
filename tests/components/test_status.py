"""Snapshot merge: live probes × recorded results."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from hal0.components import state, status


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("HAL0_HOME", str(tmp_path))


def _probe_patches(installed_map, pinned_map):
    def fake_rows():
        return installed_map, pinned_map
    return fake_rows


def test_converged_when_installed_equals_pinned() -> None:
    with (
        patch("hal0.components.registry._hindsight_installed", return_value="0.9.2"),
        patch("hal0.components.registry._hindsight_pinned", return_value="0.9.2"),
    ):
        snap = status.component_status_snapshot()
    row = next(r for r in snap["components"] if r["id"] == "hindsight")
    assert row["status"] == "converged"


def test_pending_when_versions_differ() -> None:
    with (
        patch("hal0.components.registry._hindsight_installed", return_value="0.8.4"),
        patch("hal0.components.registry._hindsight_pinned", return_value="0.9.2"),
    ):
        snap = status.component_status_snapshot()
    row = next(r for r in snap["components"] if r["id"] == "hindsight")
    assert row["status"] == "pending"
    assert snap["pending"] >= 1


def test_recorded_failure_wins() -> None:
    state.record_component_result("hindsight", {"status": "rolled_back", "error": "postcheck"})
    with (
        patch("hal0.components.registry._hindsight_installed", return_value="0.8.4"),
        patch("hal0.components.registry._hindsight_pinned", return_value="0.9.2"),
    ):
        snap = status.component_status_snapshot()
    row = next(r for r in snap["components"] if r["id"] == "hindsight")
    assert row["status"] == "rolled_back"
    assert row["error"] == "postcheck"


def test_not_installed() -> None:
    with patch("hal0.components.registry._hermes_installed", return_value=None):
        snap = status.component_status_snapshot()
    row = next(r for r in snap["components"] if r["id"] == "hermes")
    assert row["status"] == "not-installed"


def test_probe_crash_degrades_to_unknown() -> None:
    with patch("hal0.components.registry._hindsight_installed", side_effect=OSError("boom")):
        snap = status.component_status_snapshot()  # must not raise
    row = next(r for r in snap["components"] if r["id"] == "hindsight")
    assert row["installed"] is None
