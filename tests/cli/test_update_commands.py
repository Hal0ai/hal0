"""Tests for the ``hal0 update`` CLI subcommand (#510).

The CLI is a thin client over /api/updates/*; these tests stub the
``api_*`` helpers (imported into the update_commands namespace) so the
command logic - target-version normalization, the apply trigger, and the
drift-aware ``--restart-slots`` flag (WS-J, #1111) - is exercised without a
running daemon.
"""

from __future__ import annotations

import inspect
from typing import get_args

import httpx
import pytest
from typer.testing import CliRunner

from hal0.cli import update_commands as uc
from hal0.cli._shared import CliApiError
from hal0.cli.main import app
from hal0.release.policy import ReleaseKind

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
    # The test venv is itself an editable install, so the audit-4.1 refusal
    # would fire before staging; pin it off so the apply flow is exercised.
    monkeypatch.setattr("hal0.updater.updater._is_editable_install", lambda: False)

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


def test_update_channel_values_match_shared_release_kinds() -> None:
    assert {channel.value for channel in uc.UpdateChannel} == set(get_args(ReleaseKind))


@pytest.mark.parametrize("channel", get_args(ReleaseKind))
def test_channel_posts_shared_release_kind_payload(channel: str, stub_api: dict) -> None:
    result = runner.invoke(app, ["update", "--channel", channel, "--check"])

    assert result.exit_code == 0, result.output
    assert stub_api["put_json"] == {"channel": channel}


def test_channel_help_lists_all_shared_release_kinds() -> None:
    result = runner.invoke(app, ["update", "--help"])

    assert result.exit_code == 0, result.output
    for channel in get_args(ReleaseKind):
        assert channel in result.output


def test_restart_slots_flag_present_and_drift_aware() -> None:
    """``--restart-slots`` is back — but drift-aware, via the API (WS-J, #1111).

    The retired flag bounced ``hal0-slot@*.service`` unconditionally over
    systemd; the new one restarts only drifted slots through
    /api/updates/restart-slots. The old systemd-bouncing helper stays gone.
    """
    sig = inspect.signature(uc.update)
    assert "restart_slots" in sig.parameters
    # The retired systemd-bouncing helper must NOT come back.
    assert not hasattr(uc, "_restart_slots")
    # The new drift-aware helpers exist instead.
    assert hasattr(uc, "_restart_drifted_slots")
    assert hasattr(uc, "_fetch_slot_drift")
    assert hasattr(uc, "_print_drift_banner")


def test_restart_slots_bounces_only_drifted_and_skips_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``hal0 update --restart-slots`` POSTs restart-slots and never runs apply."""
    monkeypatch.setattr(uc, "_api_unreachable", lambda url: False)
    calls: dict = {"get": [], "post": []}

    def fake_get(path: str, **kwargs: object) -> dict:
        calls["get"].append(path)
        if path == "/api/updates/slot-drift":
            return {"count": 1, "slots": [{"slot": "chat", "diffs": []}]}
        return {}

    def fake_post(path: str, *, json: object = None, **kwargs: object) -> dict:
        calls["post"].append(path)
        if path == "/api/updates/restart-slots":
            return {"restarted": ["chat"], "failed": [], "count": 1}
        return {}

    monkeypatch.setattr(uc, "api_get", fake_get)
    monkeypatch.setattr(uc, "api_post", fake_post)
    result = runner.invoke(app, ["update", "--restart-slots"])
    assert result.exit_code == 0, result.output
    # It restarted drifted slots …
    assert "/api/updates/restart-slots" in calls["post"]
    assert "restarted 1 slot" in result.output
    # … and never touched the check / prepare / commit apply path.
    assert "/api/updates/check" not in calls["get"]
    assert "/api/updates/prepare" not in calls["post"]
    assert "/api/updates/commit" not in calls["post"]


def test_restart_slots_clean_message_when_nothing_drifted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean box prints an explicit 'no slots need restart' and skips POST."""
    monkeypatch.setattr(uc, "_api_unreachable", lambda url: False)
    posted: list[str] = []
    monkeypatch.setattr(uc, "api_get", lambda path, **k: {"count": 0, "slots": []})
    monkeypatch.setattr(uc, "api_post", lambda path, **k: posted.append(path) or {})
    result = runner.invoke(app, ["update", "--restart-slots"])
    assert result.exit_code == 0, result.output
    assert "no slots need restart" in result.output
    assert posted == []  # nothing drifted → no restart POST


def test_post_apply_shows_drift_banner(monkeypatch: pytest.MonkeyPatch) -> None:
    """After a successful apply the 'N slots need restart' banner is surfaced."""
    monkeypatch.setattr(uc, "_api_unreachable", lambda url: False)
    monkeypatch.setattr(uc, "_interactive", lambda: False)
    monkeypatch.setattr("hal0.updater.updater._is_editable_install", lambda: False)

    def fake_get(path: str, **kwargs: object) -> dict:
        if path == "/api/updates/slot-drift":
            return {"count": 2, "slots": [{"slot": "chat"}, {"slot": "code"}]}
        return {
            "current": "0.0.0",
            "latest": "9.9.9",
            "channel": "stable",
            "update_available": True,
            "manifest": {},
        }

    def fake_post(path: str, *, json: object = None, **kwargs: object) -> dict:
        if path == "/api/updates/prepare":
            return {"id": "p1", "state": "queued"}
        if path == "/api/updates/commit":
            return {"id": "c1", "state": "queued"}
        return {}

    def fake_poll(
        job_id: str, *, terminal: tuple = ("applied", "failed"), **kwargs: object
    ) -> dict:
        if "prepared" in terminal:
            return {"id": job_id, "state": "prepared", "resolved_version": "9.9.9", "notes": {}}
        return {"id": job_id, "state": "applied"}

    monkeypatch.setattr(uc, "api_get", fake_get)
    monkeypatch.setattr(uc, "api_post", fake_post)
    monkeypatch.setattr(uc, "_poll_job", fake_poll)
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 0, result.output
    assert "2 slots need restart" in result.output
    # Panel word-wraps at the runner's 80-col width, so match the flag token
    # (which never splits) rather than the full "hal0 update --restart-slots".
    assert "--restart-slots" in result.output


def test_target_strips_leading_v(stub_api: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """``--target v0.1.1`` is normalized to ``0.1.1`` before hitting /prepare."""
    result = runner.invoke(app, ["update", "--target", "v0.1.1"])
    assert result.exit_code == 0, result.output
    assert stub_api["prepare_json"] == {"version": "0.1.1"}


def test_target_without_v_is_unchanged(stub_api: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """``--target 0.1.1`` behaves identically to ``--target v0.1.1``."""
    result = runner.invoke(app, ["update", "--target", "0.1.1"])
    assert result.exit_code == 0, result.output
    assert stub_api["prepare_json"] == {"version": "0.1.1"}


def test_prepare_pin_mismatch_never_posts_commit(
    stub_api: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed prepare job stops the CLI before the activation request."""

    def failed_poll(
        job_id: str, *, terminal: tuple = ("applied", "failed"), **kwargs: object
    ) -> dict:
        return {
            "id": job_id,
            "state": "failed",
            "error": "requested version does not match authenticated channel manifest",
        }

    monkeypatch.setattr(uc, "_poll_job", failed_poll)

    result = runner.invoke(app, ["update", "--target", "0.1.1"])

    assert result.exit_code != 0
    assert "authenticated channel" in result.output
    assert "manifest" in result.output
    assert stub_api["prepare_json"] == {"version": "0.1.1"}
    assert stub_api["commit_json"] is None


def test_prepare_then_commit_flow(stub_api: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI stages via /prepare, then activates via /commit with the resolved version."""
    result = runner.invoke(app, ["update", "--target", "0.1.1"])
    assert result.exit_code == 0, result.output
    # prepare got the (normalized) target; commit got the resolved_version from the poll.
    assert stub_api["prepare_json"] == {"version": "0.1.1"}
    assert stub_api["commit_json"] == {"version": "0.1.1"}


def test_update_refuses_on_editable_install(
    stub_api: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An editable/dev install is hard-refused before staging (audit 4.1).

    The refusal names the editable tree and points at the release wheel; it
    must never reach /prepare or /commit.
    """
    monkeypatch.setattr("hal0.updater.updater._is_editable_install", lambda: True)
    monkeypatch.setattr("hal0.updater.updater._editable_install_path", lambda: "/opt/hal0")
    result = runner.invoke(app, ["update", "--target", "0.1.1"])
    assert result.exit_code != 0
    assert "editable mode from /opt/hal0" in result.output
    assert "pip install hal0" in result.output
    # Never staged or committed.
    assert stub_api["prepare_json"] is None
    assert stub_api["commit_json"] is None


def test_yes_flag_present_and_skips_confirm(
    stub_api: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--yes`` is a real flag and drives straight to commit without prompting."""
    assert "yes" in inspect.signature(uc.update).parameters
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
    assert stub_api["commit_json"] == {"version": "0.1.1"}


def test_tty_decline_stages_without_commit(stub_api: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """On a TTY without --yes, declining the prompt stages but never commits."""
    monkeypatch.setattr(uc, "_interactive", lambda: True)
    monkeypatch.setattr(uc.typer, "confirm", lambda *a, **k: False)
    result = runner.invoke(app, ["update", "--target", "0.1.1"])
    assert result.exit_code == 0, result.output
    assert stub_api["prepare_json"] == {"version": "0.1.1"}
    assert stub_api["commit_json"] is None  # declined → no commit


def _up_to_date_stub(
    monkeypatch: pytest.MonkeyPatch, *, profile_reset: dict, converge_result: dict
) -> dict:
    """Wire the CLI so /check reports no update but the given profile_reset, and
    /converge-profiles returns ``converge_result``. Returns a captured dict."""
    captured: dict = {"posts": [], "converge_json": None}
    monkeypatch.setattr(uc, "_api_unreachable", lambda url: False)
    monkeypatch.setattr("hal0.updater.updater._is_editable_install", lambda: False)

    def fake_get(path: str, **kwargs: object) -> dict:
        return {
            "current": "1.0.0",
            "latest": "1.0.0",
            "channel": "stable",
            "update_available": False,
            "manifest": {},
            "profile_reset": profile_reset,
        }

    def fake_post(path: str, *, json: object = None, **kwargs: object) -> dict:
        captured["posts"].append(path)
        if path == "/api/updates/converge-profiles":
            captured["converge_json"] = json
            return converge_result
        return {}

    monkeypatch.setattr(uc, "api_get", fake_get)
    monkeypatch.setattr(uc, "api_post", fake_post)
    return captured


def test_up_to_date_converges_outstanding_reset_with_yes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#1585: no update available but the v1.0 reset is due — `--yes` converges
    it via /converge-profiles instead of printing a silent "nothing to apply"."""
    cap = _up_to_date_stub(
        monkeypatch,
        profile_reset={"due": True, "needs_consent": True, "custom_profiles": ["mine"]},
        converge_result={"performed": True, "outcome": "reset", "backup": "/var/lib/hal0/b.toml"},
    )
    result = runner.invoke(app, ["update", "--yes"])
    assert result.exit_code == 0, result.output
    assert "/api/updates/converge-profiles" in cap["posts"]
    assert cap["converge_json"] == {"reset_profiles": True}
    assert "profile catalog converged" in result.output
    assert "nothing to apply" not in result.output


def test_up_to_date_outstanding_reset_headless_exits_2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Headless without --yes: a consent-needing reset is left outstanding, and
    the CLI exits 2 (up to date, convergence outstanding) — never silent."""
    cap = _up_to_date_stub(
        monkeypatch,
        profile_reset={"due": True, "needs_consent": True, "custom_profiles": ["mine"]},
        converge_result={},
    )
    monkeypatch.setattr(uc, "_interactive", lambda: False)
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 2, result.output
    assert "NOT fully converged" in result.output
    # Nothing was posted — consent was absent, so no wipe was attempted.
    assert "/api/updates/converge-profiles" not in cap["posts"]


def test_up_to_date_no_consent_needed_converges_silently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A due reset with nothing to lose (no custom profiles) converges without a
    prompt even headless — reset_profiles is not sent, but the call is made."""
    cap = _up_to_date_stub(
        monkeypatch,
        profile_reset={"due": True, "needs_consent": False, "custom_profiles": []},
        converge_result={"performed": True, "outcome": "reset", "backup": None},
    )
    monkeypatch.setattr(uc, "_interactive", lambda: False)
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 0, result.output
    assert cap["converge_json"] == {}  # no consent flag needed
    assert "profile catalog converged" in result.output


def test_up_to_date_no_reset_due_says_nothing_to_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ordinary up-to-date box (reset already done) still says so, cleanly."""
    cap = _up_to_date_stub(
        monkeypatch,
        profile_reset={"due": False, "reason": "already_reset", "needs_consent": False},
        converge_result={},
    )
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 0, result.output
    assert "nothing to apply" in result.output
    assert "/api/updates/converge-profiles" not in cap["posts"]


def test_rollback_headless_proceeds_without_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Headless/piped (non-TTY) rollback proceeds unattended, like apply."""
    monkeypatch.setattr(uc, "_api_unreachable", lambda url: False)
    monkeypatch.setattr(uc, "_interactive", lambda: False)
    posted: list[str] = []

    def fake_post(path: str, *, json: object = None, **kwargs: object) -> dict:
        posted.append(path)
        return {"channel": "stable"}

    monkeypatch.setattr(uc, "api_post", fake_post)
    result = runner.invoke(app, ["update", "--rollback"])
    assert result.exit_code == 0, result.output
    assert "/api/updates/rollback" in posted
    assert "rolled back" in result.output


def test_rollback_yes_flag_skips_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--yes`` drives straight to the rollback POST even on a TTY."""
    monkeypatch.setattr(uc, "_api_unreachable", lambda url: False)
    monkeypatch.setattr(uc, "_interactive", lambda: True)
    called = {"confirm": False}
    monkeypatch.setattr(
        uc.typer, "confirm", lambda *a, **k: called.__setitem__("confirm", True) or True
    )
    posted: list[str] = []
    monkeypatch.setattr(
        uc, "api_post", lambda path, **k: posted.append(path) or {"channel": "stable"}
    )
    result = runner.invoke(app, ["update", "--rollback", "--yes"])
    assert result.exit_code == 0, result.output
    assert called["confirm"] is False
    assert "/api/updates/rollback" in posted


def test_rollback_tty_decline_never_posts(monkeypatch: pytest.MonkeyPatch) -> None:
    """On a TTY without --yes, declining the prompt never issues the rollback POST."""
    monkeypatch.setattr(uc, "_api_unreachable", lambda url: False)
    monkeypatch.setattr(uc, "_interactive", lambda: True)
    monkeypatch.setattr(uc.typer, "confirm", lambda *a, **k: False)
    posted: list[str] = []
    monkeypatch.setattr(uc, "api_post", lambda path, **k: posted.append(path) or {})
    result = runner.invoke(app, ["update", "--rollback"])
    assert result.exit_code == 0, result.output
    assert posted == []
    assert "cancelled" in result.output


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


# ── _poll_job across the restart window (#1540) ───────────────────────────────
#
# These are the first tests to exercise _poll_job itself. Every pre-existing
# test monkeypatches it out, which is exactly why a successful update shipped
# reporting failure: `commit` restarts hal0-api underneath the poll, and the
# poll treated the resulting connection-refused as fatal.


def _refusing_then(results: list, *, refusals: int):
    """api_get stub: `refusals` transport failures, then `results` in order."""
    calls = {"n": 0}

    def fake_get(path: str, **kwargs: object) -> dict:
        calls["n"] += 1
        if calls["n"] <= refusals:
            raise CliApiError(
                f"GET {path} failed: ConnectError: [Errno 111] Connection refused"
            ) from httpx.ConnectError("[Errno 111] Connection refused")
        return results[min(calls["n"] - refusals - 1, len(results) - 1)]

    return fake_get, calls


def test_poll_job_survives_the_post_restart_connection_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connection-refused mid-commit is the restart, not a failure (#1540)."""
    fake_get, calls = _refusing_then([{"id": "c1", "state": "applied"}], refusals=4)
    monkeypatch.setattr(uc, "api_get", fake_get)
    monkeypatch.setattr(uc.time, "sleep", lambda _s: None)

    job = uc._poll_job("c1", terminal=("applied", "failed"))

    assert job["state"] == "applied"
    assert calls["n"] == 5, "should have retried through every refusal"


def test_poll_job_still_dies_on_a_live_api_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live API answering 4xx/5xx must still fail fast, not be retried.

    Guards the over-broad-retry regression: blanket-retrying every
    CliApiError would turn a genuine commit failure into a long hang.
    """

    def fake_get(path: str, **kwargs: object) -> dict:
        raise CliApiError("GET /api/updates/status/c1 failed: HTTP 500: boom")

    monkeypatch.setattr(uc, "api_get", fake_get)
    monkeypatch.setattr(uc.time, "sleep", lambda _s: None)

    with pytest.raises(SystemExit) as excinfo:
        uc._poll_job("c1", terminal=("applied", "failed"))
    assert excinfo.value.code == 1


def test_poll_job_gives_up_once_the_unreachable_budget_is_spent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A permanently dead API fails on the unreachable budget, not the 600s one."""
    fake_get, _calls = _refusing_then([{}], refusals=10_000)
    monkeypatch.setattr(uc, "api_get", fake_get)
    monkeypatch.setattr(uc.time, "sleep", lambda _s: None)

    clock = {"t": 0.0}

    def fake_monotonic() -> float:
        clock["t"] += 10.0
        return clock["t"]

    monkeypatch.setattr(uc.time, "monotonic", fake_monotonic)

    with pytest.raises(SystemExit) as excinfo:
        uc._poll_job("c1", terminal=("applied", "failed"))
    assert excinfo.value.code == 1
