# QueryStringScrubber

> 13 nodes

## Key Concepts

- **QueryStringScrubber** (8 connections) — `src/hal0/api/middleware/log_scrub.py`
- **log_scrub.py** (4 connections) — `src/hal0/api/middleware/log_scrub.py`
- **install()** (4 connections) — `src/hal0/api/middleware/log_scrub.py`
- **test_log_scrubber_strips_query_string()** (3 connections) — `tests/api/test_chat_proxy.py`
- **test_log_scrubber_no_query_unchanged()** (3 connections) — `tests/api/test_chat_proxy.py`
- **.filter()** (2 connections) — `src/hal0/api/middleware/log_scrub.py`
- **FastAPI** (2 connections)
- **LogRecord** (1 connections)
- **uvicorn access-log query-string scrubber.  DA-sec-ops MUST-FIX #3 (re-iterated b** (1 connections) — `src/hal0/api/middleware/log_scrub.py`
- **Logging filter that strips ``?...`` from uvicorn access lines.      Applied to t** (1 connections) — `src/hal0/api/middleware/log_scrub.py`
- **Attach :class:`QueryStringScrubber` to the scrubbed uvicorn loggers.      The fi** (1 connections) — `src/hal0/api/middleware/log_scrub.py`
- **The QueryStringScrubber filter rewrites the request line.      Direct unit test** (1 connections) — `tests/api/test_chat_proxy.py`
- **A request line with no query string passes through unchanged.** (1 connections) — `tests/api/test_chat_proxy.py`

## Relationships

- [test_chat_proxy.py](test_chat_proxy.py.md) (3 shared connections)
- [_ServerThread](_ServerThread.md) (1 shared connections)

## Source Files

- `src/hal0/api/middleware/log_scrub.py`
- `tests/api/test_chat_proxy.py`

## Audit Trail

- EXTRACTED: 24 (75%)
- INFERRED: 8 (25%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*