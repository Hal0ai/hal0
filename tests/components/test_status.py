"""Snapshot merge: live probes x recorded results."""

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


def test_activation_failed_is_a_recorded_failure() -> None:
    state.record_component_result(
        "hermes",
        {
            "status": "activation_failed",
            "error": "re-verify",
            "remedy": "sudo hal0 agent reprovision hermes --repair",
        },
    )
    with (
        patch("hal0.components.registry._hermes_installed", return_value="3ef6bbd20126"),
        patch("hal0.components.registry._hermes_pinned", return_value="3ef6bbd20126"),
    ):
        snap = status.component_status_snapshot()
    row = next(r for r in snap["components"] if r["id"] == "hermes")
    assert row["status"] == "activation_failed"
    assert row["remedy"].startswith("sudo hal0 agent reprovision")
    assert snap["pending"] >= 1


def test_busy_is_not_a_failure_and_derives_pending() -> None:
    """ADR-0032 decision 6: a held builder lock never reads as build_failed."""
    state.record_component_result("hermes", {"status": "busy", "holder_pid": 42})
    with (
        patch("hal0.components.registry._hermes_installed", return_value="aaaaaaaaaaaa"),
        patch("hal0.components.registry._hermes_pinned", return_value="3ef6bbd20126"),
    ):
        snap = status.component_status_snapshot()
    row = next(r for r in snap["components"] if r["id"] == "hermes")
    assert row["status"] == "pending"


def test_status_tuples_stay_in_step() -> None:
    from hal0.components import runner

    assert set(runner._FAILURE_STATUSES) == set(status._FAILURE_STATUSES)
    assert "busy" not in status._FAILURE_STATUSES


def test_pre_layout_hermes_host_is_pending_although_the_stamp_matches() -> None:
    with (
        patch("hal0.components.registry._hermes_installed", return_value="3ef6bbd20126"),
        patch("hal0.components.registry._hermes_pinned", return_value="3ef6bbd20126"),
        patch("hal0.components.status._hermes_needs_build", return_value=True),
    ):
        snap = status.component_status_snapshot()
    row = next(r for r in snap["components"] if r["id"] == "hermes")
    assert row["status"] == "pending"


def test_busy_with_a_matching_stamp_is_not_green() -> None:
    state.record_component_result("hermes", {"status": "busy", "holder_pid": 42})
    with (
        patch("hal0.components.registry._hermes_installed", return_value="3ef6bbd20126"),
        patch("hal0.components.registry._hermes_pinned", return_value="3ef6bbd20126"),
        patch("hal0.components.status._hermes_needs_build", return_value=False),
    ):
        snap = status.component_status_snapshot()
    row = next(r for r in snap["components"] if r["id"] == "hermes")
    assert row["status"] == "pending"


def test_busy_counts_as_pending_in_the_update_cli() -> None:
    from hal0.cli.update_commands import _COMPONENT_PENDING

    assert "busy" in _COMPONENT_PENDING and "activation_failed" in _COMPONENT_PENDING


# ── Hermes --target override (#2865) ────────────────────────────────────────

_HPIN = "4d04303818b61bf3aa09278af48356315515965b"
_HPREV = "d923a5eb487a17c0a68801bba9ef0f56c81040a8"


def _hermes_row(*, installed: str | None, override: str | None, needs_build: bool = False):
    from hal0.components import hermes_arm

    if override is not None:
        hermes_arm.override_path().parent.mkdir(parents=True, exist_ok=True)
        hermes_arm.override_path().write_text(override + "\n", encoding="utf-8")
    with (
        patch("hal0.components.registry._hermes_installed", return_value=installed),
        patch("hal0.components.registry._hermes_pinned", return_value=_HPIN[:12]),
        patch("hal0.components.status._hermes_needs_build", return_value=needs_build),
    ):
        snap = status.component_status_snapshot()
    return next(r for r in snap["components"] if r["id"] == "hermes")


def test_hermes_row_without_an_override_has_none() -> None:
    row = _hermes_row(installed=_HPIN[:12], override=None)
    assert row["status"] == "converged"
    assert row["override"] is None


def test_hermes_live_on_the_override_reads_override_with_both_shas() -> None:
    row = _hermes_row(installed=_HPREV[:12], override=_HPREV)
    assert row["status"] == "override"
    assert row["pinned"] == _HPIN[:12]
    assert row["override"] == _HPREV


def test_hermes_override_not_yet_live_is_pending() -> None:
    row = _hermes_row(installed=_HPIN[:12], override=_HPREV)
    assert row["status"] == "pending"
    assert row["override"] == _HPREV


def test_hermes_override_with_a_build_due_is_pending() -> None:
    row = _hermes_row(installed=_HPREV[:12], override=_HPREV, needs_build=True)
    assert row["status"] == "pending"


def test_hermes_unvetted_override_is_pending_and_names_clear_override() -> None:
    from hal0.agents import hermes_provision as hp

    with patch.object(hp, "VETTED_HERMES_REFS", frozenset({_HPIN})):
        row = _hermes_row(installed=_HPREV[:12], override=_HPREV)
    assert row["status"] == "pending"
    assert "--clear-override" in row["note"]
