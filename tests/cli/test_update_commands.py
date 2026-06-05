"""Tests for the ``hal0 update`` CLI subcommand (#510).

The CLI is a thin client over /api/updates/*; these tests stub the
``api_*`` helpers (imported into the update_commands namespace) so the
command logic - target-version normalization, the apply trigger, and the
absence of the retired ``--restart-slots`` flag - is exercised without a
running daemon.
"""

from __future__ import annotations

import inspect

import pytest
from typer.testing import CliRunner

from hal0.cli import update_commands as uc
from hal0.cli.main import app

runner = CliRunner()


@pytest.fixture
def stub_api(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Stub the api_* helpers + reachability so update() runs offline.

    Returns a captured-calls dict so assertions can inspect what the CLI
    sent to /api/updates/apply.
    """
    captured: dict = {"apply_json": None, "get_paths": [], "put_json": None}

    monkeypatch.setattr(uc, "_api_unreachable", lambda url: False)

    def fake_get(path: str, **kwargs: object) -> dict:
        captured["get_paths"].append(path)
        # /api/updates/check - advertise an available update so apply runs.
        return {
            "current": "0.0.0",
            "latest": "9.9.9",
            "channel": "stable",
            "update_available": True,
            "manifest": {},
        }

    def fake_post(path: str, *, json: object = None, **kwargs: object) -> dict:
        if path == "/api/updates/apply":
            captured["apply_json"] = json
            return {"id": "job123", "state": "queued"}
        return {}

    def fake_put(path: str, *, json: object = None, **kwargs: object) -> dict:
        captured["put_json"] = json
        return {"channel": json.get("channel") if isinstance(json, dict) else None}

    def fake_poll(job_id: str, **kwargs: object) -> dict:
        return {"id": job_id, "state": "applied"}

    monkeypatch.setattr(uc, "api_get", fake_get)
    monkeypatch.setattr(uc, "api_post", fake_post)
    monkeypatch.setattr(uc, "api_put", fake_put)
    monkeypatch.setattr(uc, "_poll_job", fake_poll)
    return captured


def test_restart_slots_flag_removed() -> None:
    """The retired ``--restart-slots`` flag is gone from the command signature."""
    sig = inspect.signature(uc.update)
    assert "restart_slots" not in sig.parameters
    # And the helper that bounced hal0-slot@*.service is removed too.
    assert not hasattr(uc, "_restart_slots")


def test_target_strips_leading_v(stub_api: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """``--target v0.1.1`` is normalized to ``0.1.1`` before hitting the API."""
    monkeypatch.setattr(uc, "_warn_editable_version_drift", lambda: None)
    result = runner.invoke(app, ["update", "--target", "v0.1.1"])
    assert result.exit_code == 0, result.output
    assert stub_api["apply_json"] == {"version": "0.1.1"}


def test_target_without_v_is_unchanged(stub_api: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """``--target 0.1.1`` behaves identically to ``--target v0.1.1``."""
    monkeypatch.setattr(uc, "_warn_editable_version_drift", lambda: None)
    result = runner.invoke(app, ["update", "--target", "0.1.1"])
    assert result.exit_code == 0, result.output
    assert stub_api["apply_json"] == {"version": "0.1.1"}


def test_editable_drift_warns_when_source_ahead(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """When the source pyproject is ahead of the metadata version, warn."""
    import hal0

    monkeypatch.setattr(hal0, "__version__", "0.3.0")
    monkeypatch.setattr(uc, "_editable_source_version", lambda: "0.4.0")
    uc._warn_editable_version_drift()
    out = capsys.readouterr().out
    assert "0.3.0" in out
    assert "0.4.0" in out


def test_editable_drift_silent_when_matching(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No warning when metadata and source versions agree (or no source)."""
    import hal0

    monkeypatch.setattr(hal0, "__version__", "0.4.0")
    monkeypatch.setattr(uc, "_editable_source_version", lambda: "0.4.0")
    uc._warn_editable_version_drift()
    assert capsys.readouterr().out == ""

    monkeypatch.setattr(uc, "_editable_source_version", lambda: None)
    uc._warn_editable_version_drift()
    assert capsys.readouterr().out == ""
