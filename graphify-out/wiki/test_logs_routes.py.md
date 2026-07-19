# test_logs_routes.py

> 17 nodes

## Key Concepts

- **test_logs_routes.py** (8 connections) — `tests/api/test_logs_routes.py`
- **TestClient** (7 connections)
- **test_logs_happy_path_returns_lines_and_count()** (3 connections) — `tests/api/test_logs_routes.py`
- **test_logs_validation_error_envelope_for_missing_unit()** (3 connections) — `tests/api/test_logs_routes.py`
- **test_logs_invalid_unit_returns_typed_envelope()** (3 connections) — `tests/api/test_logs_routes.py`
- **test_logs_invalid_level_returns_typed_envelope()** (3 connections) — `tests/api/test_logs_routes.py`
- **test_logs_n_out_of_range_returns_envelope()** (3 connections) — `tests/api/test_logs_routes.py`
- **test_logs_stream_returns_sse_content_type()** (3 connections) — `tests/api/test_logs_routes.py`
- **test_logs_stream_invalid_unit_rejects()** (3 connections) — `tests/api/test_logs_routes.py`
- **Tests for /api/logs and /api/logs/stream.  journalctl is rarely available in CI,** (1 connections) — `tests/api/test_logs_routes.py`
- **GET /api/logs?unit=... returns the lines+count shape.      On hosts without jour** (1 connections) — `tests/api/test_logs_routes.py`
- **Missing unit query param yields a 422 in the hal0 envelope shape.      The ``Req** (1 connections) — `tests/api/test_logs_routes.py`
- **A shell-special char in unit name rejects with the typed envelope.** (1 connections) — `tests/api/test_logs_routes.py`
- **An unknown ?level= value returns the typed logs error envelope.** (1 connections) — `tests/api/test_logs_routes.py`
- **?n=0 is below the validator floor and yields the hal0 envelope.      Pydantic-dr** (1 connections) — `tests/api/test_logs_routes.py`
- **GET /api/logs/stream sets the SSE content-type even without journalctl.      The** (1 connections) — `tests/api/test_logs_routes.py`
- **Validation runs before the SSE generator starts.** (1 connections) — `tests/api/test_logs_routes.py`

## Relationships

- No strong cross-community connections detected

## Source Files

- `tests/api/test_logs_routes.py`

## Audit Trail

- EXTRACTED: 44 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*