# hal0 benchmarking system — full design (batch runs, rich run detail, display, auto-update)

> 25 nodes

## Key Concepts

- **hal0 benchmarking system — full design (batch runs, rich run detail, display, auto-update)** (16 connections) — `docs/archive/handoffs/benchmark-system-design-2026-07-05.md`
- **9. Docs-site publish — the public table, auto-updated** (4 connections) — `docs/archive/handoffs/benchmark-system-design-2026-07-05.md`
- **10. Skills — the autonomy layer** (4 connections) — `docs/archive/handoffs/benchmark-system-design-2026-07-05.md`
- **3. Result store & record schema v2 (the "more detail on runs" core)** (3 connections) — `docs/archive/handoffs/benchmark-system-design-2026-07-05.md`
- **benchmark-system-design-2026-07-05.md** (1 connections) — `docs/archive/handoffs/benchmark-system-design-2026-07-05.md`
- **0. Goals / non-goals** (1 connections) — `docs/archive/handoffs/benchmark-system-design-2026-07-05.md`
- **1. What exists today (inventory — all of this is kept and reused)** (1 connections) — `docs/archive/handoffs/benchmark-system-design-2026-07-05.md`
- **2. Architecture overview** (1 connections) — `docs/archive/handoffs/benchmark-system-design-2026-07-05.md`
- **3.1 Layout** (1 connections) — `docs/archive/handoffs/benchmark-system-design-2026-07-05.md`
- **3.2 The record (one per cell × run)** (1 connections) — `docs/archive/handoffs/benchmark-system-design-2026-07-05.md`
- **4. Suites — the declarative layer (`/etc/hal0/bench/suites/*.toml`)** (1 connections) — `docs/archive/handoffs/benchmark-system-design-2026-07-05.md`
- **5. The `hal0 bench` CLI (new `src/hal0/cli/bench_commands.py`)** (1 connections) — `docs/archive/handoffs/benchmark-system-design-2026-07-05.md`
- **6. Auto-update semantics (the "set and forget" core)** (1 connections) — `docs/archive/handoffs/benchmark-system-design-2026-07-05.md`
- **7. API — `/api/benchmarks` (new `src/hal0/api/routes/benchmarks.py`)** (1 connections) — `docs/archive/handoffs/benchmark-system-design-2026-07-05.md`
- **8. Dashboard — the **Benchmarks** page (ui/)** (1 connections) — `docs/archive/handoffs/benchmark-system-design-2026-07-05.md`
- **9.1 Data contract** (1 connections) — `docs/archive/handoffs/benchmark-system-design-2026-07-05.md`
- **9.2 Pipeline (two stages, both automated)** (1 connections) — `docs/archive/handoffs/benchmark-system-design-2026-07-05.md`
- **9.3 Docs table upgrade (run detail on the public page)** (1 connections) — `docs/archive/handoffs/benchmark-system-design-2026-07-05.md`
- **10.1 `hal0-bench` (existing — update)** (1 connections) — `docs/archive/handoffs/benchmark-system-design-2026-07-05.md`
- **10.2 `hal0-bench-autopilot` (new skill)** (1 connections) — `docs/archive/handoffs/benchmark-system-design-2026-07-05.md`
- **10.3 `hal0-tune` (existing — unchanged relationship)** (1 connections) — `docs/archive/handoffs/benchmark-system-design-2026-07-05.md`
- **11. Regression detection** (1 connections) — `docs/archive/handoffs/benchmark-system-design-2026-07-05.md`
- **12. Privilege / seam changes (minimal, same pattern)** (1 connections) — `docs/archive/handoffs/benchmark-system-design-2026-07-05.md`
- **13. Implementation plan (each phase = one PR, independently shippable)** (1 connections) — `docs/archive/handoffs/benchmark-system-design-2026-07-05.md`
- **14. Open questions** (1 connections) — `docs/archive/handoffs/benchmark-system-design-2026-07-05.md`

## Relationships

- No strong cross-community connections detected

## Source Files

- `docs/archive/handoffs/benchmark-system-design-2026-07-05.md`

## Audit Trail

- EXTRACTED: 48 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*