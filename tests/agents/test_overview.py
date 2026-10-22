"""Pure builders behind the agent overview route."""

from __future__ import annotations

from pathlib import Path

from hal0.agents.hermes_release import Release
from hal0.agents.overview import hermes_version, unit_states


def test_hermes_version_marks_update_when_installed_differs_from_pinned():
    live = Release(
        id="d923a5eb487a-20261022T021933Z",
        path=Path("/x"),
        data={
            "ref": "d923a5eb" + "0" * 32,
            "describe": "v0.22.2",
            "built_at": "2026-10-22T02:19:58Z",
            "python_minor": "3.14",
            "extras": ["web", "mcp"],
            "platform_extras": ["telegram"],
            "hal0_version": "1.5.0",
        },
    )
    out = hermes_version(
        live,
        None,
        pin_ref="4d043038" + "0" * 32,
        component={"installed": "d923a5eb", "pinned": "4d043038", "status": "pending"},
    )
    assert out["live"]["describe"] == "v0.22.2"
    assert out["pin"] == {"ref": "4d043038" + "0" * 32, "describe": None}
    assert out["component"] == {"installed": "d923a5eb", "pinned": "4d043038", "status": "pending"}
    assert out["update_available"] is True


def test_hermes_version_converged_is_not_update():
    out = hermes_version(
        None,
        None,
        pin_ref="a" * 40,
        component={"installed": "x", "pinned": "x", "status": "converged"},
    )
    assert out["update_available"] is False and out["live"] is None


def test_unit_states_maps_probe_results():
    assert unit_states(True, "active") == {"agent": "active", "gateway": "active"}
    assert unit_states(False, "failed") == {"agent": "inactive", "gateway": "failed"}
    assert unit_states(None, None) == {"agent": "unknown", "gateway": "unknown"}
