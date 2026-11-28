"""Tests for ClosedStreamFilter suppressing disconnect tracebacks (#3654).

When an MCP client disconnects while a standalone GET SSE stream is open,
the server's terminate() closes the stream, raising anyio.ClosedResourceError
in standalone_sse_writer. FastMCP's default logging prints a 44-line Rich
traceback at ERROR level. ClosedStreamFilter drops this routine disconnect
record while preserving genuine faults.
"""

from __future__ import annotations

import logging
from typing import Any

import anyio
import pytest

from hal0.mcp._binding import (
    STREAMABLE_HTTP_LOGGER_NAME,
    BoundFastMCP,
    ClosedStreamFilter,
    install_closed_stream_filter,
)


@pytest.fixture
def clean_logger() -> Any:
    """Provide the streamable HTTP logger with isolated handlers/filters."""
    logger = logging.getLogger(STREAMABLE_HTTP_LOGGER_NAME)
    saved_filters = list(logger.filters)
    saved_handlers = list(logger.handlers)
    saved_level = logger.level
    records: list[logging.LogRecord] = []

    class CapturingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger.handlers = [CapturingHandler()]
    logger.filters = []
    logger.setLevel(logging.DEBUG)
    try:
        yield logger, records
    finally:
        logger.handlers = saved_handlers
        logger.filters = saved_filters
        logger.setLevel(saved_level)


def test_closed_stream_filter_drops_closed_resource_error(clean_logger: Any) -> None:
    logger, records = clean_logger
    logger.addFilter(ClosedStreamFilter())

    try:
        raise anyio.ClosedResourceError()
    except anyio.ClosedResourceError:
        logger.exception("Error in standalone SSE writer")

    assert len(records) == 0


def test_closed_stream_filter_drops_exception_group_with_closed_resource_error(
    clean_logger: Any,
) -> None:
    logger, records = clean_logger
    logger.addFilter(ClosedStreamFilter())

    try:
        raise ExceptionGroup("stream error", [anyio.ClosedResourceError()])
    except ExceptionGroup:
        logger.exception("Error in standalone SSE writer")

    assert len(records) == 0


def test_closed_stream_filter_preserves_mixed_exception_group(clean_logger: Any) -> None:
    logger, records = clean_logger
    logger.addFilter(ClosedStreamFilter())

    try:
        raise ExceptionGroup(
            "stream error",
            [anyio.ClosedResourceError(), RuntimeError("writer failed")],
        )
    except ExceptionGroup:
        logger.exception("Error in standalone SSE writer")

    assert len(records) == 1
    assert "Error in standalone SSE writer" in records[0].getMessage()
    assert records[0].levelno == logging.ERROR


def test_closed_stream_filter_preserves_other_exceptions(clean_logger: Any) -> None:
    logger, records = clean_logger
    logger.addFilter(ClosedStreamFilter())

    try:
        raise RuntimeError("database connection crashed")
    except RuntimeError:
        logger.exception("Error in standalone SSE writer")

    assert len(records) == 1
    assert "Error in standalone SSE writer" in records[0].getMessage()
    assert records[0].levelno == logging.ERROR


def test_closed_stream_filter_preserves_other_messages(clean_logger: Any) -> None:
    logger, records = clean_logger
    logger.addFilter(ClosedStreamFilter())

    try:
        raise anyio.ClosedResourceError()
    except anyio.ClosedResourceError:
        logger.exception("Something completely different broke")

    assert len(records) == 1
    assert "Something completely different broke" in records[0].getMessage()


def test_closed_stream_filter_preserves_non_error_levels() -> None:
    flt = ClosedStreamFilter()

    # Even if message matches, non-ERROR levels must pass through
    rec = logging.LogRecord(
        name=STREAMABLE_HTTP_LOGGER_NAME,
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Error in standalone SSE writer",
        args=(),
        exc_info=None,
    )
    assert flt.filter(rec) is True


def test_install_closed_stream_filter_is_idempotent() -> None:
    logger = logging.getLogger(STREAMABLE_HTTP_LOGGER_NAME)
    install_closed_stream_filter()
    install_closed_stream_filter()
    filter_count = sum(1 for f in logger.filters if isinstance(f, ClosedStreamFilter))
    assert filter_count == 1


def test_bound_fastmcp_installs_filter() -> None:
    logger = logging.getLogger(STREAMABLE_HTTP_LOGGER_NAME)
    logger.filters = [f for f in logger.filters if not isinstance(f, ClosedStreamFilter)]
    assert not any(isinstance(f, ClosedStreamFilter) for f in logger.filters)

    BoundFastMCP("test-server", tools=[], state=None)
    assert any(isinstance(f, ClosedStreamFilter) for f in logger.filters)
