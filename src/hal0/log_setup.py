"""One owner for how a hal0 process configures structlog's console output.

structlog's default ``ConsoleRenderer`` formats tracebacks with Rich when
Rich is installed, and Rich's traceback renderer prints every frame's local
variables (``show_locals=True``). hal0 processes hold credentials in locals
all the time: a request header dict with ``Authorization: Bearer …``, a
provider API key, a bridge key. Any caught exception that reaches
``log.exception(...)`` / ``log.error(..., exc_info=True)`` therefore printed
them to the journal, where ``systemd-journal`` members, doctor bundles and
Sentry breadcrumbs could read them (#3137).

Every ``structlog.configure`` in hal0 goes through here so the renderer is
built once, with locals off. The exception text itself still goes through
:mod:`hal0.redaction` wherever hal0 re-emits it; this module closes the
traceback-locals hole, which line-based redaction cannot see because Rich
wraps a long value onto its own line.
"""

from __future__ import annotations

import sys
from typing import Any, TextIO

import structlog

__all__ = ["configure_stderr_logging", "console_renderer", "stderr_processors"]


def console_renderer() -> structlog.dev.ConsoleRenderer:
    """structlog's console renderer with traceback locals switched off.

    Rich stays in use for the frame listing when it is installed, so a CLI
    user keeps the readable traceback; only the per-frame locals table goes.
    """
    return structlog.dev.ConsoleRenderer(
        exception_formatter=structlog.dev.RichTracebackFormatter(show_locals=False),
    )


def stderr_processors() -> list[Any]:
    """structlog's default processor chain with :func:`console_renderer` last.

    Mirrors ``structlog.get_config()["processors"]`` as shipped (context vars,
    level, stack info, exc_info, timestamp), swapping only the renderer, so
    the log lines look exactly as they did before #3137.
    """
    return [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
        console_renderer(),
    ]


def configure_stderr_logging(*, level: int | None = None, file: TextIO | None = None) -> None:
    """Send structured logs to *file* (stderr by default) with locals off.

    *level* adds a filtering bound logger (``hal0 serve`` and the one-shot
    CLI use it so debug telemetry stays quiet for humans); ``None`` keeps
    structlog's unfiltered default, which the root updater helpers rely on.
    """
    kwargs: dict[str, Any] = {
        "processors": stderr_processors(),
        "logger_factory": structlog.PrintLoggerFactory(file=file or sys.stderr),
    }
    if level is not None:
        kwargs["wrapper_class"] = structlog.make_filtering_bound_logger(level)
    structlog.configure(**kwargs)
