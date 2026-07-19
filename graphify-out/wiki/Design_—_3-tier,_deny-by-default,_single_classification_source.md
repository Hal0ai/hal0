# Design — 3-tier, deny-by-default, single classification source

> 12 nodes

## Key Concepts

- **KB-1 / §1 — Authentication (security fast-track)** (6 connections) — `docs/rework/hal0-specs/spec-kb1-auth.md`
- **Design — 3-tier, deny-by-default, single classification source** (6 connections) — `docs/rework/hal0-specs/spec-kb1-auth.md`
- **spec-kb1-auth.md** (1 connections) — `docs/rework/hal0-specs/spec-kb1-auth.md`
- **Problem (real, today)** (1 connections) — `docs/rework/hal0-specs/spec-kb1-auth.md`
- **Credential model (matches §22 Settings Security page: "require API key, client key, admin key")** (1 connections) — `docs/rework/hal0-specs/spec-kb1-auth.md`
- **Enforcement — pure-ASGI middleware, installed at `__init__.py:1275` (after log_scrub, before routers)** (1 connections) — `docs/rework/hal0-specs/spec-kb1-auth.md`
- **Classification source of truth = `security/exposure.py` (doubles as §21.11 exposure-CI)** (1 connections) — `docs/rework/hal0-specs/spec-kb1-auth.md`
- **Rollout posture (backward-compatible, test-safe)** (1 connections) — `docs/rework/hal0-specs/spec-kb1-auth.md`
- **§21.11 exposure CI (ships WITH this, cheap ratchet)** (1 connections) — `docs/rework/hal0-specs/spec-kb1-auth.md`
- **Shippable steps (each green + pushed before next)** (1 connections) — `docs/rework/hal0-specs/spec-kb1-auth.md`
- **Files** (1 connections) — `docs/rework/hal0-specs/spec-kb1-auth.md`
- **Risks** (1 connections) — `docs/rework/hal0-specs/spec-kb1-auth.md`

## Relationships

- No strong cross-community connections detected

## Source Files

- `docs/rework/hal0-specs/spec-kb1-auth.md`

## Audit Trail

- EXTRACTED: 22 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*