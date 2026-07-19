# P2-device: make `device` the sole truth, delete the `backend` dual-write

> 14 nodes

## Key Concepts

- **P2-device: make `device` the sole truth, delete the `backend` dual-write** (9 connections) — `docs/rework/hal0-specs/spec-p2-device.final.md`
- **2. Concept-A surfaces to remove (the dual-write)** (5 connections) — `docs/rework/hal0-specs/spec-p2-device.final.md`
- **spec-p2-device.final.md** (1 connections) — `docs/rework/hal0-specs/spec-p2-device.final.md`
- **0. The critical distinction (read first)** (1 connections) — `docs/rework/hal0-specs/spec-p2-device.final.md`
- **1. The 4 translators — disposition** (1 connections) — `docs/rework/hal0-specs/spec-p2-device.final.md`
- **2a. Model fields + validators** (1 connections) — `docs/rework/hal0-specs/spec-p2-device.final.md`
- **2b. Dual-write sites (write both `device` and `backend`)** (1 connections) — `docs/rework/hal0-specs/spec-p2-device.final.md`
- **2c. Strip-on-save / migration (already device-only; simplify)** (1 connections) — `docs/rework/hal0-specs/spec-p2-device.final.md`
- **2d. POST/CLI input alias (decision needed, independent of the field)** (1 connections) — `docs/rework/hal0-specs/spec-p2-device.final.md`
- **3. Load-bearing / risky sites — flag before editing** (1 connections) — `docs/rework/hal0-specs/spec-p2-device.final.md`
- **4. Edit order** (1 connections) — `docs/rework/hal0-specs/spec-p2-device.final.md`
- **5. Grep to confirm zero remaining live Concept-A refs** (1 connections) — `docs/rework/hal0-specs/spec-p2-device.final.md`
- **6. Test files (assert on the deprecated dual-write — will break, must update)** (1 connections) — `docs/rework/hal0-specs/spec-p2-device.final.md`
- **Summary** (1 connections) — `docs/rework/hal0-specs/spec-p2-device.final.md`

## Relationships

- No strong cross-community connections detected

## Source Files

- `docs/rework/hal0-specs/spec-p2-device.final.md`

## Audit Trail

- EXTRACTED: 26 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*