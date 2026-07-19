# Findings from the 2026-05-16 hal0-test deep-probe round

> 17 nodes

## Key Concepts

- **Findings from the 2026-05-16 hal0-test deep-probe round** (17 connections) — `tests/harness/FINDINGS.md`
- **10. Caddy basic_auth swallows the PUBLIC_PATHS allowlist — **critical / bug** · ✅ FIXED BY ARCHITECTURE REMOVAL (ADR-0001)** (1 connections) — `tests/harness/FINDINGS.md`
- **11. `require_token` ignores scope on every write router — **high / security bug**** (1 connections) — `tests/harness/FINDINGS.md`
- **12. Slot state machine reports `offline` while slots are actively serving 200s — **high / bug**** (1 connections) — `tests/harness/FINDINGS.md`
- **13. Validation errors raised as bare `Hal0Error(...)` return HTTP 500 — **medium / contract bug**** (1 connections) — `tests/harness/FINDINGS.md`
- **14. STT pipeline leaks ffmpeg subprocess argv on bad input — **medium / bug**** (1 connections) — `tests/harness/FINDINGS.md`
- **15. `utility` slot reports `state=ready` while serving zero models — **medium / UX bug**** (1 connections) — `tests/harness/FINDINGS.md`
- **16. basic_auth password is unrecoverable post-install — **medium / gap** · ✅ FIXED BY ARCHITECTURE REMOVAL (ADR-0001)** (1 connections) — `tests/harness/FINDINGS.md`
- **17. Deployed install at /opt/hal0 on hal0-test LXC is several commits behind main — **medium / operational**** (1 connections) — `tests/harness/FINDINGS.md`
- **18. `/v1/audio/speech` returns 404 instead of 400 when `model` is omitted — **low / bug**** (1 connections) — `tests/harness/FINDINGS.md`
- **19. `/api/slots/{name}/config` returns 400/`slot.config_error` for unknown slot — **low / envelope inconsistency**** (1 connections) — `tests/harness/FINDINGS.md`
- **20. `/api/logs?unit=` missing param returns FastAPI default envelope — **low / envelope inconsistency** · ✅ RESOLVED 2026-05-21** (1 connections) — `tests/harness/FINDINGS.md`
- **21. `/api/metrics/prometheus` is in PUBLIC_PATHS but the route is unimplemented — **low / dead config** · ✅ FIXED BY ARCHITECTURE REMOVAL (ADR-0001)** (1 connections) — `tests/harness/FINDINGS.md`
- **22. `cli-doctor` row in δ-harness false-positives on hosts with co-resident hal0 — **info / harness**** (1 connections) — `tests/harness/FINDINGS.md`
- **23. Spec drift — there is no `/api/slots/{name}/events` route — **info / drift**** (1 connections) — `tests/harness/FINDINGS.md`
- **24. `POST /api/updates/apply` returns 200, not 202 — **trivial / drift**** (1 connections) — `tests/harness/FINDINGS.md`
- **25. v1 proxied 4xx leak upstream's OpenAI envelope (vs hal0 envelope) — **info / by-design**** (1 connections) — `tests/harness/FINDINGS.md`

## Relationships

- [FINDINGS.md](FINDINGS.md.md) (1 shared connections)

## Source Files

- `tests/harness/FINDINGS.md`

## Audit Trail

- EXTRACTED: 33 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*