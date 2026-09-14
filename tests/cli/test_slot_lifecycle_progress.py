"""``hal0 slot load|unload|restart|swap`` progress + timeout UX (#1869/#1870).

Before this, every mutating lifecycle verb made one blocking ``api_post``
with a multi-minute read timeout and printed nothing until it returned — an
operator staring at a silent terminal for up to ~2400s had no way to tell
"still working" from "hung", and a timeout produced a generic error with no
remedy.

Covers ``hal0.cli._shared.run_with_progress`` (the shared elapsed+state
renderer, on and off a TTY) and ``slot_commands._run_lifecycle_call``'s
timeout handling (the typed-error message plus the remedy lines).
"""

from __future__ import annotations

import io
import time
from typing import Any

import pytest
from rich.console import Console
from typer.testing import CliRunner

from hal0.cli import slot_commands
from hal0.cli._shared import CliApiError, CliApiTimeout, run_with_progress

runner = CliRunner()


def _capturing_console(*, interactive: bool) -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=interactive, width=120)
    return console, buf


# ── run_with_progress ─────────────────────────────────────────────────────


def test_run_with_progress_non_interactive_prints_one_summary_line_and_calls_directly() -> None:
    console, buf = _capturing_console(interactive=False)
    calls: list[str] = []

    def call() -> str:
        calls.append("ran")
        return "done"

    result = run_with_progress(call, console=console, label="Loading primary", timeout_s=90.0)

    assert result == "done"
    assert calls == ["ran"]
    out = buf.getvalue()
    assert "Loading primary" in out
    assert "up to 90s" in out


def test_run_with_progress_json_out_suppresses_all_output() -> None:
    console, buf = _capturing_console(interactive=False)

    result = run_with_progress(
        lambda: "done", console=console, label="Loading primary", timeout_s=90.0, json_out=True
    )

    assert result == "done"
    assert buf.getvalue() == ""


def test_run_with_progress_interactive_shows_elapsed_and_polled_state() -> None:
    console, buf = _capturing_console(interactive=True)
    poll_calls: list[int] = []

    def poll_state() -> str:
        poll_calls.append(1)
        return "starting"

    def call() -> str:
        time.sleep(0.05)
        return "done"

    result = run_with_progress(
        call,
        console=console,
        label="Loading primary",
        timeout_s=90.0,
        poll_state=poll_state,
        poll_interval_s=0.01,
    )

    assert result == "done"
    assert poll_calls, "poll_state was never called while waiting"
    assert "starting" in buf.getvalue()


def test_run_with_progress_interactive_reraises_call_error() -> None:
    console, _buf = _capturing_console(interactive=True)

    def call() -> str:
        raise CliApiTimeout("POST ... did not respond within 90s")

    with pytest.raises(CliApiTimeout):
        run_with_progress(
            call, console=console, label="Loading primary", timeout_s=90.0, poll_interval_s=0.01
        )


def test_run_with_progress_poll_state_failure_is_swallowed() -> None:
    """A status-line probe must never abort the wait it is only decorating."""
    console, _buf = _capturing_console(interactive=True)

    def poll_state() -> str:
        raise RuntimeError("boom")

    result = run_with_progress(
        lambda: "done",
        console=console,
        label="Loading primary",
        timeout_s=90.0,
        poll_state=poll_state,
        poll_interval_s=0.01,
    )

    assert result == "done"


# ── slot_commands._run_lifecycle_call / CliApiTimeout remedy ───────────────


@pytest.fixture(autouse=True)
def _api_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slot_commands, "_api_unreachable", lambda _url: False)


def test_slot_load_prints_timeout_remedy_and_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(path: str, **_kw: Any) -> Any:
        raise CliApiTimeout(f"POST {path} did not respond within 90s")

    monkeypatch.setattr(slot_commands, "api_post", fake_post)

    result = runner.invoke(slot_commands.app, ["load", "primary"])
    output = " ".join(result.output.split())  # CliRunner's narrow width wraps lines

    assert result.exit_code == 1
    assert "did not respond within 90s" in output
    assert "hal0 slot logs primary" in output
    assert "journalctl -u hal0-slot@primary" in output


def test_slot_load_plain_api_error_has_no_timeout_remedy(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real 4xx/5xx (not a timeout) gets the ordinary ``die()`` treatment —
    no "still converging?" remedy, which would be misleading for a request
    the server flatly rejected."""

    def fake_post(path: str, **_kw: Any) -> Any:
        raise CliApiError(f"POST {path} -> HTTP 404: slot not found")

    monkeypatch.setattr(slot_commands, "api_post", fake_post)

    result = runner.invoke(slot_commands.app, ["load", "primary"])

    assert result.exit_code == 1
    assert "slot not found" in result.output
    assert "still converging" not in result.output


def test_slot_load_json_flag_suppresses_progress_and_emits_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(path: str, **_kw: Any) -> Any:
        return {"state": "serving", "model_id": "demo"}

    monkeypatch.setattr(slot_commands, "api_post", fake_post)

    result = runner.invoke(slot_commands.app, ["load", "primary", "--json"])

    assert result.exit_code == 0, result.output
    assert "Loading primary" not in result.output
    assert '"state": "serving"' in result.output


def test_slot_unload_timeout_remedy_names_the_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(path: str, **_kw: Any) -> Any:
        raise CliApiTimeout(f"POST {path} did not respond within 60s")

    monkeypatch.setattr(slot_commands, "api_post", fake_post)

    result = runner.invoke(slot_commands.app, ["unload", "chat"])
    output = " ".join(result.output.split())

    assert result.exit_code == 1
    assert "hal0 slot logs chat" in output
    assert "journalctl -u hal0-slot@chat" in output


def test_poll_slot_state_is_none_on_api_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """The progress line's state probe is best-effort — GET failing must
    never surface as an error (the lifecycle call is the one that matters)."""

    def fake_get(path: str, **_kw: Any) -> Any:
        raise CliApiError(f"GET {path} -> HTTP 503")

    monkeypatch.setattr(slot_commands, "api_get", fake_get)

    assert slot_commands._poll_slot_state("primary") is None
