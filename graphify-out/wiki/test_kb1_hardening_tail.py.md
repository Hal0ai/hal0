# test_kb1_hardening_tail.py

> 41 nodes · cohesion 0.07

## Key Concepts

- **test_kb1_hardening_tail.py** (18 connections) — `tests/security/test_kb1_hardening_tail.py`
- **SlidingWindowRateLimiter** (10 connections) — `src/hal0/security/ratelimit.py`
- **login_limiter_from_env()** (6 connections) — `src/hal0/security/ratelimit.py`
- **hardened_app()** (6 connections) — `tests/security/test_kb1_hardening_tail.py`
- **ratelimit.py** (5 connections) — `src/hal0/security/ratelimit.py`
- **TestClient** (5 connections)
- **_record()** (5 connections) — `tests/security/test_kb1_hardening_tail.py`
- **_scope()** (5 connections) — `tests/security/test_kb1_hardening_tail.py`
- **test_login_route_rate_limited()** (5 connections) — `tests/security/test_kb1_hardening_tail.py`
- **._prune()** (4 connections) — `src/hal0/security/ratelimit.py`
- **.allow()** (3 connections) — `src/hal0/security/ratelimit.py`
- **.retry_after()** (3 connections) — `src/hal0/security/ratelimit.py`
- **MonkeyPatch** (3 connections)
- **test_origin_allowed_allowlisted_origin()** (3 connections) — `tests/security/test_kb1_hardening_tail.py`
- **_float_env()** (2 connections) — `src/hal0/security/ratelimit.py`
- **_int_env()** (2 connections) — `src/hal0/security/ratelimit.py`
- **.reset()** (2 connections) — `src/hal0/security/ratelimit.py`
- **Path** (2 connections)
- **test_limiter_allows_up_to_budget_then_blocks()** (2 connections) — `tests/security/test_kb1_hardening_tail.py`
- **test_limiter_keys_are_independent()** (2 connections) — `tests/security/test_kb1_hardening_tail.py`
- **test_middleware_allows_no_origin_state_change()** (2 connections) — `tests/security/test_kb1_hardening_tail.py`
- **test_middleware_rejects_cross_site_state_change()** (2 connections) — `tests/security/test_kb1_hardening_tail.py`
- **test_middleware_rejects_cross_site_websocket()** (2 connections) — `tests/security/test_kb1_hardening_tail.py`
- **test_origin_allowed_no_origin_is_allowed()** (2 connections) — `tests/security/test_kb1_hardening_tail.py`
- **test_origin_allowed_same_origin_via_host()** (2 connections) — `tests/security/test_kb1_hardening_tail.py`
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