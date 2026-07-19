# Dispatch / runtime — routing, arbiter, lifecycle

> 13 nodes

## Key Concepts

- **Dispatch / runtime — routing, arbiter, lifecycle** (13 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **DR-1 · critical · Idle-evicted non-chat slots (embed/rerank/tts) never wake on request** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **DR-2 · high · GpuArbiter drain is racy — a slot can be unloaded under an in-flight request** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **DR-3 · high · `_await_ready` reports READY on health-probe timeout** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **DR-4 · medium · The same backend failure surfaces as two contradictory error envelopes** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **DR-5 · medium · NPU single-context exclusivity is enforced at config-write time only, not at load time** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **DR-6 · medium · Chat-slot load failures are swallowed** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **DR-7 · medium · Two overlapping, inconsistent lazy-load strategies (root cause of DR-1)** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **DR-8 · medium · Ready-set semantics duplicated in three (four) places** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **DR-9 · low · Arbiter `guard_dispatch` does blocking file I/O on the event loop in the hot path** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **DR-10 · low · In-flight / idle bookkeeping is keyed on unresolved slot names** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **DR-11 · low · A hung streaming client pins a slot SERVING and stalls every image-mode switch for 2 minutes** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **DR-12 · low · `router.py` (1592 lines) mixes five concerns; a stale docstring implies a private-state reach-in that no longer exists** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`

## Relationships

- [hal0 platform review — reliability, config surface & UI](hal0_platform_review_%E2%80%94_reliability%2C_config_surface_%26_UI.md) (1 shared connections)

## Source Files

- `docs/archive/handoffs/platform-review-2026-07-03.md`

## Audit Trail

- EXTRACTED: 25 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*