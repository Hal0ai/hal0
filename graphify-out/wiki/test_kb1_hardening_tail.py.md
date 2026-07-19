# test_kb1_hardening_tail.py

> 41 nodes

## Key Concepts

- **test_kb1_hardening_tail.py** (18 connections) — `tests/security/test_kb1_hardening_tail.py`
- **SlidingWindowRateLimiter** (10 connections) — `src/hal0/security/ratelimit.py`
- **login_limiter_from_env()** (6 connections) — `src/hal0/security/ratelimit.py`
- **hardened_app()** (6 connections) — `tests/security/test_kb1_hardening_tail.py`
- **ratelimit.py** (5 connections) — `src/hal0/security/ratelimit.py`
- **_record()** (5 connections) — `tests/security/test_kb1_hardening_tail.py`
- **_scope()** (5 connections) — `tests/security/test_kb1_hardening_tail.py`
- **TestClient** (5 connections)
- **test_login_route_rate_limited()** (5 connections) — `tests/security/test_kb1_hardening_tail.py`
- **._prune()** (4 connections) — `src/hal0/security/ratelimit.py`
- **.allow()** (3 connections) — `src/hal0/security/ratelimit.py`
- **.retry_after()** (3 connections) — `src/hal0/security/ratelimit.py`
- **test_origin_allowed_allowlisted_origin()** (3 connections) — `tests/security/test_kb1_hardening_tail.py`
- **MonkeyPatch** (3 connections)
- **.reset()** (2 connections) — `src/hal0/security/ratelimit.py`
- **_int_env()** (2 connections) — `src/hal0/security/ratelimit.py`
- **_float_env()** (2 connections) — `src/hal0/security/ratelimit.py`
- **test_scrubber_strips_ws_accept_line_api_key()** (2 connections) — `tests/security/test_kb1_hardening_tail.py`
- **test_scrubber_strips_access_request_line()** (2 connections) — `tests/security/test_kb1_hardening_tail.py`
- **test_scrubber_leaves_non_request_records_untouched()** (2 connections) — `tests/security/test_kb1_hardening_tail.py`
- **test_origin_allowed_no_origin_is_allowed()** (2 connections) — `tests/security/test_kb1_hardening_tail.py`
- **test_origin_allowed_same_origin_via_host()** (2 connections) — `tests/security/test_kb1_hardening_tail.py`
- **test_origin_rejected_cross_site()** (2 connections) — `tests/security/test_kb1_hardening_tail.py`
- **Path** (2 connections)
- **test_middleware_rejects_cross_site_state_change()** (2 connections) — `tests/security/test_kb1_hardening_tail.py`
- *... and 16 more nodes in this community*

## Relationships

- [create_app](create_app.md) (3 shared connections)

## Source Files

- `src/hal0/security/ratelimit.py`
- `tests/security/test_kb1_hardening_tail.py`

## Audit Trail

- EXTRACTED: 116 (94%)
- INFERRED: 7 (6%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*