"""Tests for the ``hal0 update`` CLI subcommand (#510, #1111).

The CLI is a thin client over /api/updates/*; these tests stub the
``api_*`` helpers (imported into the update_commands namespace) so the
command logic - target-version normalization, the apply trigger, and the
``--restart-slots`` / drift-banner behaviour - is exercised without a
running daemon.

``--restart-slots`` was removed in #539/#510 (it blind-restarted every
``hal0-slot@*.service`` unconditionally — dead code targeting a naming
scheme that no longer applied) and reintroduced in #1111 with a completely
different, safe shape: it's forwarded to ``/commit`` as a body flag, and the
server only ever bounces the slots its own reconcile sweep found drifted.
The CLI itself never touches systemd.
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
    captured: dict = {
        "prepare_json": None,
        "commit_json": None,
        "get_paths": [],
        "put_json": None,
    }

    monkeypatch.setattr(uc, "_api_unreachable", lambda url: False)

    def fake_get(path: str, **kwargs: object) -> dict:
        captured["get_paths"].append(path)
        # /api/updates/check - advertise an available update so the flow runs.
        return {
            "current": "0.0.0",
            "latest": "9.9.9",
            "channel": "stable",
            "update_available": True,
            "manifest": {},
        }

    def fake_post(path: str, *, json: object = None, **kwargs: object) -> dict:
        if path == "/api/updates/prepare":
            captured["prepare_json"] = json
            return {"id": "prep123", "state": "queued"}
        if path == "/api/updates/commit":
            captured["commit_json"] = json
            return {"id": "commit123", "state": "queued"}
        return {}

    def fake_put(path: str, *, json: object = None, **kwargs: object) -> dict:
        captured["put_json"] = json
        return {"channel": json.get("channel") if isinstance(json, dict) else None}

    def fake_poll(
        job_id: str, *, terminal: tuple = ("applied", "failed"), **kwargs: object
    ) -> dict:
        # prepare poll → 'prepared' + resolved version + notes; commit poll → 'applied'.
        if "prepared" in terminal:
            return {
                "id": job_id,
                "state": "prepared",
                "resolved_version": "0.1.1",
                "notes": {"markdown": "", "highlights": [], "breaking": [], "migrations": []},
            }
        return {"id": job_id, "state": "applied"}

    monkeypatch.setattr(uc, "api_get", fake_get)
    monkeypatch.setattr(uc, "api_post", fake_post)
    monkeypatch.setattr(uc, "api_put", fake_put)
    monkeypatch.setattr(uc, "_poll_job", fake_poll)
    return captured


def test_restart_slots_flag_present_and_defaults_false() -> None:
    """``--restart-slots`` (#1111) exists and defaults to False (opt-in only)."""
    sig = inspect.signature(uc.update)
    assert "restart_slots" in sig.parameters
    default = sig.parameters["restart_slots"].default
    assert default.default is False  # typer.Option(False, ...)
    # The old (#539/#510-removed) helper that reached around the API to
    # blind-restart every hal0-slot@*.service is gone for good — the new
    # flag never touches systemd from the CLI process.
    assert not hasattr(uc, "_restart_slots")


def test_default_update_sends_restart_slots_false(
    stub_api: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the flag, /commit is sent restart_slots=False — never implicitly True."""
    monkeypatch.setattr(uc, "_warn_editable_version_drift", lambda: None)
    result = runner.invoke(app, ["update", "--target", "0.1.1"])
    assert result.exit_code == 0, result.output
    assert stub_api["commit_json"] == {"version": "0.1.1", "restart_slots": False}


def test_restart_slots_flag_forwarded_to_commit_body(
    stub_api: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--restart-slots`` is forwarded as a /commit body flag, not a local systemctl call."""
    monkeypatch.setattr(uc, "_warn_editable_version_drift", lambda: None)
    result = runner.invoke(app, ["update", "--target", "0.1.1", "--restart-slots"])
    assert result.exit_code == 0, result.output
    assert stub_api["commit_json"] == {"version": "0.1.1", "restart_slots": True}


def test_drift_banner_clean_when_no_drifted_slots(capsys: pytest.CaptureFixture[str]) -> None:
    """No drifted slots -> a quiet confirmation, no restart language."""
    uc._print_drift_banner({"drifted_slots": []}, restart_slots=False)
    out = capsys.readouterr().out
    assert "no restart needed" in out
    assert "restart" not in out.lower().replace("no restart needed", "")


def test_drift_banner_warns_without_restart_flag(capsys: pytest.CaptureFixture[str]) -> None:
    """Drifted slots + no --restart-slots -> a loud warning naming the slots, no restart claim."""
    uc._print_drift_banner(
        {"drifted_slots": ["chat", "embed"], "restarted_slots": []}, restart_slots=False
    )
    out = capsys.readouterr().out
    assert "2 slot(s)" in out
    assert "chat" in out and "embed" in out
    assert "--restart-slots" in out


def test_drift_banner_reports_restarted_slots(capsys: pytest.CaptureFixture[str]) -> None:
    """Drifted slots + --restart-slots -> reports what was actually bounced."""
    uc._print_drift_banner(
        {"drifted_slots": ["chat", "embed"], "restarted_slots": ["chat", "embed"]},
        restart_slots=True,
    )
    out = capsys.readouterr().out
    assert "restarted 2 drifted slot(s)" in out
    assert "chat" in out and "embed" in out
    assert "failed to restart" not in out


def test_drift_banner_flags_slots_that_failed_to_restart(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A drifted slot missing from restarted_slots is called out as a failure."""
    uc._print_drift_banner(
        {"drifted_slots": ["chat", "embed"], "restarted_slots": ["chat"]}, restart_slots=True
    )
    out = capsys.readouterr().out
    assert "restarted 1 drifted slot(s)" in out
    assert "1 drifted slot(s) failed to restart" in out
    assert "embed" in out


def test_target_strips_leading_v(stub_api: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """``--target v0.1.1`` is normalized to ``0.1.1`` before hitting /prepare."""
    monkeypatch.setattr(uc, "_warn_editable_version_drift", lambda: None)
    result = runner.invoke(app, ["update", "--target", "v0.1.1"])
    assert result.exit_code == 0, result.output
    assert stub_api["prepare_json"] == {"version": "0.1.1"}


def test_target_without_v_is_unchanged(stub_api: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """``--target 0.1.1`` behaves identically to ``--target v0.1.1``."""
    monkeypatch.setattr(uc, "_warn_editable_version_drift", lambda: None)
    result = runner.invoke(app, ["update", "--target", "0.1.1"])
    assert result.exit_code == 0, result.output
    assert stub_api["prepare_json"] == {"version": "0.1.1"}


def test_prepare_then_commit_flow(stub_api: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI stages via /prepare, then activates via /commit with the resolved version."""
    monkeypatch.setattr(uc, "_warn_editable_version_drift", lambda: None)
    result = runner.invoke(app, ["update", "--target", "0.1.1"])
    assert result.exit_code == 0, result.output
    # prepare got the (normalized) target; commit got the resolved_version from the poll.
    assert stub_api["prepare_json"] == {"version": "0.1.1"}
    assert stub_api["commit_json"] == {"version": "0.1.1", "restart_slots": False}


def test_yes_flag_present_and_skips_confirm(
    stub_api: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--yes`` is a real flag and drives straight to commit without prompting."""
    assert "yes" in inspect.signature(uc.update).parameters
    monkeypatch.setattr(uc, "_warn_editable_version_drift", lambda: None)
    # Force an interactive TTY so only --yes can suppress the prompt; if confirm
    # were reached it would flip the flag, so a clean commit proves it skipped.
    monkeypatch.setattr(uc, "_interactive", lambda: True)
    called = {"confirm": False}
    monkeypatch.setattr(
        uc.typer, "confirm", lambda *a, **k: called.__setitem__("confirm", True) or True
    )
    result = runner.invoke(app, ["update", "--target", "0.1.1", "--yes"])
    assert result.exit_code == 0, result.output
    assert called["confirm"] is False
    assert stub_api["commit_json"] == {"version": "0.1.1", "restart_slots": False}


def test_tty_decline_stages_without_commit(stub_api: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """On a TTY without --yes, declining the prompt stages but never commits."""
    monkeypatch.setattr(uc, "_warn_editable_version_drift", lambda: None)
    monkeypatch.setattr(uc, "_interactive", lambda: True)
    monkeypatch.setattr(uc.typer, "confirm", lambda *a, **k: False)
    result = runner.invoke(app, ["update", "--target", "0.1.1"])
    assert result.exit_code == 0, result.output
    assert stub_api["prepare_json"] == {"version": "0.1.1"}
    assert stub_api["commit_json"] is None  # declined → no commit


def test_render_notes_shows_breaking_and_migrations(capsys: pytest.CaptureFixture[str]) -> None:
    """_render_notes surfaces breaking/migration callouts and highlights."""
    uc._render_notes(
        {
            "markdown": "# 0.1.1\nbody",
            "highlights": ["new profiles"],
            "breaking": ["removed rocm-moe"],
            "migrations": ["slots fall back to rocm"],
        }
    )
    out = capsys.readouterr().out
    assert "removed rocm-moe" in out
    assert "slots fall back to rocm" in out
    assert "new profiles" in out


def test_render_notes_none_is_noop(capsys: pytest.CaptureFixture[str]) -> None:
    """No notes → nothing rendered (older releases without a notes payload)."""
    uc._render_notes(None)
    assert capsys.readouterr().out == ""


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
