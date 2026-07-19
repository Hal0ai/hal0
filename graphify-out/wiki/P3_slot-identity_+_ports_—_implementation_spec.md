# P3 slot-identity + ports — implementation spec

> 36 nodes

## Key Concepts

- **P3 slot-identity + ports — implementation spec** (10 connections) — `docs/rework/hal0-specs/spec-p3-slot-identity-ports.md`
- **4. Edit plan — file, order, what to keep** (9 connections) — `docs/rework/hal0-specs/spec-p3-slot-identity-ports.md`
- **0. Current state — what's broken and where** (6 connections) — `docs/rework/hal0-specs/spec-p3-slot-identity-ports.md`
- **2. Target Python surface** (6 connections) — `docs/rework/hal0-specs/spec-p3-slot-identity-ports.md`
- **3. Migration window — name → id keying** (5 connections) — `docs/rework/hal0-specs/spec-p3-slot-identity-ports.md`
- **1. Target schema — `db/` tables (S8 substrate)** (4 connections) — `docs/rework/hal0-specs/spec-p3-slot-identity-ports.md`
- **spec-p3-slot-identity-ports.md** (1 connections) — `docs/rework/hal0-specs/spec-p3-slot-identity-ports.md`
- **0.1 Name-keyed everywhere in `slots/manager.py`** (1 connections) — `docs/rework/hal0-specs/spec-p3-slot-identity-ports.md`
- **0.2 Port management is ad-hoc (no allocator)** (1 connections) — `docs/rework/hal0-specs/spec-p3-slot-identity-ports.md`
- **0.3 NPU-trio shadow has a parallel lifecycle** (1 connections) — `docs/rework/hal0-specs/spec-p3-slot-identity-ports.md`
- **0.4 What the harvester (`hal0.ports`) already gives us** (1 connections) — `docs/rework/hal0-specs/spec-p3-slot-identity-ports.md`
- **0.5 Schema (`config/schema.py`)** (1 connections) — `docs/rework/hal0-specs/spec-p3-slot-identity-ports.md`
- **1.1 `003_slots.sql` (id-keyed slot identity)** (1 connections) — `docs/rework/hal0-specs/spec-p3-slot-identity-ports.md`
- **1.2 `004_port_claim.sql` (PortAuthority, §11.2)** (1 connections) — `docs/rework/hal0-specs/spec-p3-slot-identity-ports.md`
- **1.3 Migration to a dense pool table (separate migration)** (1 connections) — `docs/rework/hal0-specs/spec-p3-slot-identity-ports.md`
- **2.1 New module `src/hal0/slots/identity.py`** (1 connections) — `docs/rework/hal0-specs/spec-p3-slot-identity-ports.md`
- **2.2 New module `src/hal0/ports/authority.py`** (1 connections) — `docs/rework/hal0-specs/spec-p3-slot-identity-ports.md`
- **2.3 Updated `SlotManager` (decomposed, post-P3-slots)** (1 connections) — `docs/rework/hal0-specs/spec-p3-slot-identity-ports.md`
- **2.4 Group carve-out for coresident slots (FOLLOW-UP)** (1 connections) — `docs/rework/hal0-specs/spec-p3-slot-identity-ports.md`
- **2.5 API surface changes** (1 connections) — `docs/rework/hal0-specs/spec-p3-slot-identity-ports.md`
- **3.1 File:line renames (one-shot boot fold)** (1 connections) — `docs/rework/hal0-specs/spec-p3-slot-identity-ports.md`
- **3.2 Port-claim seeding (separate step)** (1 connections) — `docs/rework/hal0-specs/spec-p3-slot-identity-ports.md`
- **3.3 Idempotency + crash safety** (1 connections) — `docs/rework/hal0-specs/spec-p3-slot-identity-ports.md`
- **3.4 What is **not** migrated** (1 connections) — `docs/rework/hal0-specs/spec-p3-slot-identity-ports.md`
- **PR 1 — `db/` foundation + slot identity table** (1 connections) — `docs/rework/hal0-specs/spec-p3-slot-identity-ports.md`
- *... and 11 more nodes in this community*

## Relationships

- No strong cross-community connections detected

## Source Files

- `docs/rework/hal0-specs/spec-p3-slot-identity-ports.md`

## Audit Trail

- EXTRACTED: 70 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*