# Security review — v1.0 auth surface (2026-05-21)

> 21 nodes

## Key Concepts

- **Security review — v1.0 auth surface (2026-05-21)** (21 connections) — `tests/harness/FINDINGS.md`
- **26. `X-Forwarded-Email` auth bypass — Caddy does NOT strip inbound copies — **critical**** (1 connections) — `tests/harness/FINDINGS.md`
- **27. CSRF token compared with `==` — timing-leak — **high**** (1 connections) — `tests/harness/FINDINGS.md`
- **28. First-run `POST /api/auth/password` race — LAN attacker can claim ownership — **high**** (1 connections) — `tests/harness/FINDINGS.md`
- **29. `/api/install/*` router is wholly unauthenticated and mutating — **critical**** (1 connections) — `tests/harness/FINDINGS.md`
- **30. Path traversal in `_assign_to_slot(slot, ...)` — **critical**** (1 connections) — `tests/harness/FINDINGS.md`
- **31. `_admin_auth` router-level dep is `require_token`, not `require_writer` — **medium**** (1 connections) — `tests/harness/FINDINGS.md`
- **32. No login rate-limit / lockout — **high**** (1 connections) — `tests/harness/FINDINGS.md`
- **33. Session JWT cannot be revoked server-side — **medium**** (1 connections) — `tests/harness/FINDINGS.md`
- **34. `_load_or_create_signing_key` silently rotates key on read-failure — **medium**** (1 connections) — `tests/harness/FINDINGS.md`
- **35. OpenWebUI exposed on `0.0.0.0:3001` by default — **medium**** (1 connections) — `tests/harness/FINDINGS.md`
- **36. `HAL0_AUTH_ENABLED` defaults to FALSE — open by default — **high**** (1 connections) — `tests/harness/FINDINGS.md`
- **37. No security response headers (HSTS, CSP, X-Frame-Options, X-Content-Type-Options) — **low**** (1 connections) — `tests/harness/FINDINGS.md`
- **38. Session cookie has no `Max-Age` — relies on JWT exp — **info**** (1 connections) — `tests/harness/FINDINGS.md`
- **39. `/api/install/probe` triggers heavy subprocess fanout without auth — **medium**** (1 connections) — `tests/harness/FINDINGS.md`
- **40. SSE journal-stream `--since` is forwarded unvalidated — **low**** (1 connections) — `tests/harness/FINDINGS.md`
- **41. `slot` parameter in `/api/slots/{name}/logs` not validated before journalctl invocation — **info / defense-in-depth**** (1 connections) — `tests/harness/FINDINGS.md`
- **42. `HAL0_UPDATE_SKIP_COSIGN=1` env-var bypass is mostly safe but uses string compare — **info**** (1 connections) — `tests/harness/FINDINGS.md`
- **Security review — severity summary** (1 connections) — `tests/harness/FINDINGS.md`
- **What the harness *didn't* try (and why)** (1 connections) — `tests/harness/FINDINGS.md`
- **How to re-run** (1 connections) — `tests/harness/FINDINGS.md`

## Relationships

- [FINDINGS.md](FINDINGS.md.md) (1 shared connections)

## Source Files

- `tests/harness/FINDINGS.md`

## Audit Trail

- EXTRACTED: 41 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*