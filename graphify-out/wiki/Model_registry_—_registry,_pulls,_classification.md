# Model registry — registry, pulls, classification

> 15 nodes

## Key Concepts

- **Model registry — registry, pulls, classification** (15 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **MR-1 · critical · Installer / bundle-tier pulls bypass the #626 disk-persistence layer** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **MR-2 · high · A pull that actually completed can be reported "failed" after a restart** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **MR-3 · high · Reranker auto-scan misclassifies as "chat"** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **MR-4 · medium · No disk-space preflight before multi-GB pulls** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **MR-5 · medium · No cross-process registry write serialization → lost update** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **MR-6 · medium · Model-delete cascade is non-atomic with no rollback** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **MR-7 · medium · No resume / partial-download support** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **MR-8 · medium · Pull-job JSON files are never garbage-collected** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **MR-9 · medium · No startup sweep of orphaned `.part` partials** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **MR-10 · medium · Reconcile never rewrites disk → the on-disk snapshot lies indefinitely** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **MR-11 · low · Concurrent double-pull guard isn't atomic across an await** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **MR-12 · low · GGUF embed detection misses `pooling_type` when it precedes `general.architecture`** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **MR-13 · low · Registry atomic write doesn't fsync the parent directory** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **MR-14 · low · Failed-pull errors collapse distinct causes; alias blocklist can hide real models** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`

## Relationships

- [hal0 platform review — reliability, config surface & UI](hal0_platform_review_%E2%80%94_reliability%2C_config_surface_%26_UI.md) (1 shared connections)

## Source Files

- `docs/archive/handoffs/platform-review-2026-07-03.md`

## Audit Trail

- EXTRACTED: 29 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*