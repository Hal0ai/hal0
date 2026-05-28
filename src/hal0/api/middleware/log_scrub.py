"""uvicorn access-log query-string scrubber.

DA-sec-ops MUST-FIX #3 (re-iterated by R1 §runtime.json risk): the
hermes embed token MUST move from query string to ``Authorization``
header. Even after the move, any *future* sensitive parameter could
slip into a query string and end up in journald via the uvicorn access
log line. The scrubber pre-empts that whole class of bug.

How it works
------------
uvicorn formats access lines as ``%(client_addr)s - "%(request_line)s" %(status_code)s``
where ``request_line`` includes the full path + query string. We install
a :class:`logging.Filter` on ``uvicorn.access`` that rewrites the
``args[1]`` field (the request line) to strip the ``?...`` suffix
before the formatter ever sees it. The filter is idempotent and
allocation-cheap; the access record is only mutated when a query
string is present.

We attach the filter once on app startup via :func:`install`. Tests
exercise the filter directly to keep the assertions simple.
"""

from __future__ import annotations

import logging
import re
from typing import Final

from fastapi import FastAPI

# uvicorn's access logger name. Stable since uvicorn 0.x — present in
# both 0.27 (pyproject pin lower bound) and current releases.
ACCESS_LOGGER_NAME: Final[str] = "uvicorn.access"


# uvicorn's default access format places the request line at args[1].
# Format string (from uvicorn.logging.AccessFormatter):
#   '%(client_addr)s - "%(request_line)s" %(status_code)s'
# where args is the tuple
#   (client_addr, request_line, status_code)
_REQUEST_LINE_ARG_INDEX: Final[int] = 1


# Cheap pattern: ``METHOD /path?query HTTP/1.1`` -> drop everything from
# the first ``?`` until the next space (which precedes the protocol).
_QS_RE: Final[re.Pattern[str]] = re.compile(r"\?[^\s]*")


class QueryStringScrubber(logging.Filter):
    """Logging filter that strips ``?...`` from uvicorn access lines.

    Applied to the ``uvicorn.access`` logger so the access log never
    persists query parameters. Filter ALWAYS returns ``True`` (it never
    drops a record) — only the args are mutated.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) <= _REQUEST_LINE_ARG_INDEX:
            return True
        line = args[_REQUEST_LINE_ARG_INDEX]
        if not isinstance(line, str) or "?" not in line:
            return True
        scrubbed = _QS_RE.sub("", line)
        if scrubbed == line:
            return True
        new_args = list(args)
        new_args[_REQUEST_LINE_ARG_INDEX] = scrubbed
        record.args = tuple(new_args)
        return True


def install(app: FastAPI) -> None:
    """Attach :class:`QueryStringScrubber` to uvicorn.access at import.

    The filter is attached to the ``uvicorn.access`` logger at install
    time (i.e. at app-factory call) so it's in place before uvicorn
    initialises its access logger. Idempotent: if a scrubber is already
    attached, we don't add a second one. ``app`` is a no-op here but is
    accepted so the install signature mirrors the rest of the
    middleware family in this package.
    """
    del app  # signature parity with siblings
    logger = logging.getLogger(ACCESS_LOGGER_NAME)
    for existing in logger.filters:
        if isinstance(existing, QueryStringScrubber):
            return
    logger.addFilter(QueryStringScrubber())


__all__ = ["ACCESS_LOGGER_NAME", "QueryStringScrubber", "install"]
