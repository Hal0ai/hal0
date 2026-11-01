"""hal0.log_setup: a caught exception never prints frame locals (#3137).

A frame that holds a credential in a local (a request header dict with an
``Authorization: Bearer …`` value, a provider key, a bridge key) used to land
in the journal through structlog's default Rich traceback renderer. These
tests drive the real renderer with a bearer key in scope and read the output.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import subprocess
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
import structlog

from hal0.log_setup import configure_stderr_logging, console_renderer, stderr_processors

SECRET = "hal0bk_test.SECRETVALUEqz8rT2mN4kL9pW1xY7vB3cD5f"


def _log_exception_with_bearer_local() -> None:
    def handler() -> None:
        headers = {"Authorization": f"Bearer {SECRET}"}  # noqa: F841 - the local under test
        raise ValueError("mistyped API_SERVER_HOST")

    try:
        handler()
    except ValueError:
        structlog.get_logger("hal0.test").exception("hal0.request_failed", route="/v1/x")


@pytest.fixture
def structlog_config() -> Iterator[None]:
    saved = structlog.get_config()
    try:
        yield
    finally:
        structlog.configure(**saved)


def test_the_structlog_default_would_print_the_bearer(structlog_config: None) -> None:
    """Sanity check that the hole is real with this structlog + rich build."""
    sio = io.StringIO()
    structlog.configure(
        processors=[structlog.dev.set_exc_info, structlog.dev.ConsoleRenderer()],
        logger_factory=structlog.PrintLoggerFactory(file=sio),
    )
    _log_exception_with_bearer_local()
    assert "SECRETVALUE" in sio.getvalue()


def test_configure_stderr_logging_keeps_the_traceback_and_drops_locals(
    structlog_config: None,
) -> None:
    sio = io.StringIO()
    configure_stderr_logging(file=sio)
    _log_exception_with_bearer_local()
    out = sio.getvalue()
    assert "hal0.request_failed" in out
    assert "route=/v1/x" in out or "route" in out
    assert "ValueError" in out and "mistyped API_SERVER_HOST" in out
    assert "handler" in out  # the frame listing stays
    # The source line ``f"Bearer {SECRET}"`` may show in the frame listing; the
    # value bound to the local must not.
    assert "SECRETVALUE" not in out


def test_console_renderer_hides_locals_for_any_width(structlog_config: None) -> None:
    """Rich wraps a long local onto its own line; no width may reveal it."""
    for width in (40, 80, 400):
        sio = io.StringIO()
        renderer = console_renderer()
        renderer._exception_formatter.width = width  # type: ignore[attr-defined]
        structlog.configure(
            processors=[structlog.dev.set_exc_info, renderer],
            logger_factory=structlog.PrintLoggerFactory(file=sio),
        )
        _log_exception_with_bearer_local()
        assert "SECRETVALUE" not in sio.getvalue(), width


def test_stderr_processors_match_the_structlog_default_chain() -> None:
    default = [
        type(p) if not callable(p) or hasattr(p, "__self__") else p
        for p in structlog.get_config()["processors"]
    ]
    ours = stderr_processors()
    assert [getattr(p, "__name__", type(p).__name__) for p in ours] == [
        "merge_contextvars",
        "add_log_level",
        "StackInfoRenderer",
        "set_exc_info",
        "TimeStamper",
        "ConsoleRenderer",
    ]
    assert len(ours) == len(default)


def test_configure_stderr_logging_level_filters(structlog_config: None) -> None:
    import logging

    sio = io.StringIO()
    configure_stderr_logging(level=logging.WARNING, file=sio)
    log = structlog.get_logger("hal0.test")
    log.debug("hal0.quiet")
    log.warning("hal0.loud")
    assert "hal0.quiet" not in sio.getvalue()
    assert "hal0.loud" in sio.getvalue()


def test_cli_callback_configures_through_log_setup(
    structlog_config: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from hal0.cli.main import _configure_cli_logging

    # The callback skips itself under pytest; lift that for this one call.
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("HAL0_LOG_LEVEL", "ERROR")
    _configure_cli_logging()
    _log_exception_with_bearer_local()
    err = capsys.readouterr().err
    assert "hal0.request_failed" in err
    assert "SECRETVALUE" not in err


def test_privileged_helper_configures_through_log_setup(
    structlog_config: None, capsys: pytest.CaptureFixture[str]
) -> None:
    from hal0.updater.privileged import _pin_logs_to_stderr

    _pin_logs_to_stderr()
    _log_exception_with_bearer_local()
    err = capsys.readouterr().err
    assert "hal0.request_failed" in err
    assert "SECRETVALUE" not in err


def test_post_swap_migration_script_renders_plain_tracebacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The child script is executed by the INCOMING venv, so it must not import
    hal0.log_setup (a rollback target may predate it); it inlines a renderer
    with ``plain_traceback`` instead. Run the captured script's configure line
    for real and log an exception with a bearer in scope."""
    from hal0.updater import updater as mod

    captured: dict[str, Any] = {}

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["script"] = argv[2]
        return subprocess.CompletedProcess(argv, 0, stdout='{"x": {}}\n', stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(mod, "_MIGRATION_RESULT_KEY", "x", raising=True)
    with patch.object(mod, "_relay_child_log", create=True), contextlib.suppress(Exception):
        # Only the captured script matters here; the fake result may not parse.
        asyncio.run(mod._run_post_activation_migrations_after_swap(1, job_id="t"))
    script = captured["script"]
    assert "hal0.log_setup" not in script
    assert "plain_traceback" in script
    configure_line = next(
        line for line in script.splitlines() if line.startswith("structlog.configure(")
    )
    saved = structlog.get_config()
    sio = io.StringIO()
    try:
        exec(
            configure_line.replace("file=sys.stderr", "file=sio"),
            {"structlog": structlog, "sio": sio, "sys": os.sys},
        )
        _log_exception_with_bearer_local()
    finally:
        structlog.configure(**saved)
    out = sio.getvalue()
    assert "ValueError" in out and "SECRETVALUE" not in out
