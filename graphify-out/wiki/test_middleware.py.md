# test_middleware.py

> 14 nodes

## Key Concepts

- **test_middleware.py** (7 connections) — `tests/api/test_middleware.py`
- **_make_test_app()** (5 connections) — `tests/api/test_middleware.py`
- **TestClient** (4 connections)
- **test_unhandled_exception_returns_envelope()** (4 connections) — `tests/api/test_middleware.py`
- **test_hal0_error_envelope()** (4 connections) — `tests/api/test_middleware.py`
- **test_request_id_added()** (3 connections) — `tests/api/test_middleware.py`
- **test_request_id_echoed()** (3 connections) — `tests/api/test_middleware.py`
- **FastAPI** (2 connections)
- **Tests for hal0 API middleware.  Covers: - X-Request-ID propagation (request_id m** (1 connections) — `tests/api/test_middleware.py`
- **Build a minimal FastAPI app with both middleware pieces installed.** (1 connections) — `tests/api/test_middleware.py`
- **Response always contains x-request-id header.** (1 connections) — `tests/api/test_middleware.py`
- **Custom x-request-id on the request is echoed back in the response.** (1 connections) — `tests/api/test_middleware.py`
- **A route that raises a plain Exception returns 500 with system.internal envelope.** (1 connections) — `tests/api/test_middleware.py`
- **A route that raises a Hal0Error subclass returns the correct status and code.** (1 connections) — `tests/api/test_middleware.py`

## Relationships

- No strong cross-community connections detected

## Source Files

- `tests/api/test_middleware.py`

## Audit Trail

- EXTRACTED: 38 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*