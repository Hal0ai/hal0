# P3-slots: Decomposition Spec for `slots/manager.py`

> 29 nodes

## Key Concepts

- **P3-slots: Decomposition Spec for `slots/manager.py`** (12 connections) — `docs/rework/hal0-specs/spec-p3-slots.final.md`
- **1. Responsibility map (verified, method-by-method)** (11 connections) — `docs/rework/hal0-specs/spec-p3-slots.final.md`
- **3. Interface boundaries (buildable contracts)** (6 connections) — `docs/rework/hal0-specs/spec-p3-slots.final.md`
- **10. Line-budget accounting (→ "roughly halve")** (2 connections) — `docs/rework/hal0-specs/spec-p3-slots.final.md`
- **spec-p3-slots.final.md** (1 connections) — `docs/rework/hal0-specs/spec-p3-slots.final.md`
- **0. Executive summary** (1 connections) — `docs/rework/hal0-specs/spec-p3-slots.final.md`
- **(a) CORE — state machine + persistence + lifecycle + CRUD → **STAYS**** (1 connections) — `docs/rework/hal0-specs/spec-p3-slots.final.md`
- **(b) Idle / eviction loops → **EXTRACT to `slots/reaper.py`**** (1 connections) — `docs/rework/hal0-specs/spec-p3-slots.final.md`
- **(b′) Failure watchdog + health probing → **EXTRACT to `slots/watchdog.py`**** (1 connections) — `docs/rework/hal0-specs/spec-p3-slots.final.md`
- **(c) Config-drift comparator → **DELETE (preferred) or `slots/drift.py`**** (1 connections) — `docs/rework/hal0-specs/spec-p3-slots.final.md`
- **(d) NPU-trio reconciler → **EXTRACT to `slots/npu/` package**** (1 connections) — `docs/rework/hal0-specs/spec-p3-slots.final.md`
- **(e) Model-resolution HEURISTICS ("guess what operator meant") → **MOVE to `registry/`**** (1 connections) — `docs/rework/hal0-specs/spec-p3-slots.final.md`
- **(f) Config CRUD-write guard pipeline → **already module-level; move to `slots/config_write.py`**** (1 connections) — `docs/rework/hal0-specs/spec-p3-slots.final.md`
- **(g) Model-preferred-profile adoption → **EXTRACT to `slots/profile_adopt.py`** (secondary)** (1 connections) — `docs/rework/hal0-specs/spec-p3-slots.final.md`
- **(h) Upstream registration → **STAYS in core (or thin `slots/upstreams_bridge.py`)**** (1 connections) — `docs/rework/hal0-specs/spec-p3-slots.final.md`
- **(i) Seeded catalogue + routing → **EXTRACT to `slots/routing.py`** (secondary)** (1 connections) — `docs/rework/hal0-specs/spec-p3-slots.final.md`
- **2. Target module layout** (1 connections) — `docs/rework/hal0-specs/spec-p3-slots.final.md`
- **`slots/reaper.py`** (1 connections) — `docs/rework/hal0-specs/spec-p3-slots.final.md`
- **`slots/watchdog.py`** (1 connections) — `docs/rework/hal0-specs/spec-p3-slots.final.md`
- **`registry/fallback.py`** (1 connections) — `docs/rework/hal0-specs/spec-p3-slots.final.md`
- **`slots/npu/trio.py`** (1 connections) — `docs/rework/hal0-specs/spec-p3-slots.final.md`
- **`slots/config_write.py`** (1 connections) — `docs/rework/hal0-specs/spec-p3-slots.final.md`
- **4. Extraction order (least-coupled first)** (1 connections) — `docs/rework/hal0-specs/spec-p3-slots.final.md`
- **5. Delegation & re-export policy (keep callers unbroken)** (1 connections) — `docs/rework/hal0-specs/spec-p3-slots.final.md`
- **6. Drift-delete investigation (Phase3.2 hypothesis)** (1 connections) — `docs/rework/hal0-specs/spec-p3-slots.final.md`
- *... and 4 more nodes in this community*

## Relationships

- No strong cross-community connections detected

## Source Files

- `docs/rework/hal0-specs/spec-p3-slots.final.md`

## Audit Trail

- EXTRACTED: 56 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*