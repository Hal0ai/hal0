# PART 0 — Current registry, mapped (file:line)

> 28 nodes

## Key Concepts

- **PART 0 — Current registry, mapped (file:line)** (8 connections) — `docs/rework/hal0-specs/spec-ml1-sqlite.final.md`
- **ML-1 — SQLite Registry Pilot: Implementation Spec** (7 connections) — `docs/rework/hal0-specs/spec-ml1-sqlite.final.md`
- **PART (a) — `src/hal0/db/` foundation** (6 connections) — `docs/rework/hal0-specs/spec-ml1-sqlite.final.md`
- **PART (e) — Files to add / touch, and test impact** (6 connections) — `docs/rework/hal0-specs/spec-ml1-sqlite.final.md`
- **PART (d) — Import, export, cutover** (4 connections) — `docs/rework/hal0-specs/spec-ml1-sqlite.final.md`
- **spec-ml1-sqlite.final.md** (1 connections) — `docs/rework/hal0-specs/spec-ml1-sqlite.final.md`
- **0.1 `ModelRegistry` — `src/hal0/registry/store.py`** (1 connections) — `docs/rework/hal0-specs/spec-ml1-sqlite.final.md`
- **0.2 Public interface — every method (the drop-in contract)** (1 connections) — `docs/rework/hal0-specs/spec-ml1-sqlite.final.md`
- **0.3 `Model` / `ModelDefaults` — `src/hal0/registry/model.py`** (1 connections) — `docs/rework/hal0-specs/spec-ml1-sqlite.final.md`
- **0.4 All callers of `ModelRegistry` across `src` (method-call census)** (1 connections) — `docs/rework/hal0-specs/spec-ml1-sqlite.final.md`
- **0.5 CLI surface — `src/hal0/cli/registry_commands.py`** (1 connections) — `docs/rework/hal0-specs/spec-ml1-sqlite.final.md`
- **0.6 Existing tests (drop-in must keep green)** (1 connections) — `docs/rework/hal0-specs/spec-ml1-sqlite.final.md`
- **0.7 House SQLite pattern already in-repo (match it)** (1 connections) — `docs/rework/hal0-specs/spec-ml1-sqlite.final.md`
- **`src/hal0/db/connection.py`** (1 connections) — `docs/rework/hal0-specs/spec-ml1-sqlite.final.md`
- **`src/hal0/db/migrate.py`** (1 connections) — `docs/rework/hal0-specs/spec-ml1-sqlite.final.md`
- **`src/hal0/db/migrations/001_registry.sql`** (1 connections) — `docs/rework/hal0-specs/spec-ml1-sqlite.final.md`
- **`src/hal0/db/repository.py` (new — the pydantic⇄row seam)** (1 connections) — `docs/rework/hal0-specs/spec-ml1-sqlite.final.md`
- **Backup note (plan §8.1)** (1 connections) — `docs/rework/hal0-specs/spec-ml1-sqlite.final.md`
- **PART (b) — Registry pilot SQL schema (`001_registry.sql`)** (1 connections) — `docs/rework/hal0-specs/spec-ml1-sqlite.final.md`
- **PART (c) — `SqliteModelRegistry` (drop-in behind `ModelRegistry`)** (1 connections) — `docs/rework/hal0-specs/spec-ml1-sqlite.final.md`
- **One-shot import (idempotent) — `src/hal0/registry/import_toml.py`** (1 connections) — `docs/rework/hal0-specs/spec-ml1-sqlite.final.md`
- **Export (SQLite → TOML) — `hal0 registry export`** (1 connections) — `docs/rework/hal0-specs/spec-ml1-sqlite.final.md`
- **Cutover (plan §8.3 steps 2 & 4–5)** (1 connections) — `docs/rework/hal0-specs/spec-ml1-sqlite.final.md`
- **Files to ADD** (1 connections) — `docs/rework/hal0-specs/spec-ml1-sqlite.final.md`
- **Files to TOUCH** (1 connections) — `docs/rework/hal0-specs/spec-ml1-sqlite.final.md`
- *... and 3 more nodes in this community*

## Relationships

- No strong cross-community connections detected

## Source Files

- `docs/rework/hal0-specs/spec-ml1-sqlite.final.md`

## Audit Trail

- EXTRACTED: 54 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*