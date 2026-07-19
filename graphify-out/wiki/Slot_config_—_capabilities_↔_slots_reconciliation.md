# Slot config — capabilities ↔ slots reconciliation

> 17 nodes

## Key Concepts

- **Slot config — capabilities ↔ slots reconciliation** (17 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **SC-1 · critical · Disabling a capability never writes `enabled=false` to the slot TOML** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **SC-2 · critical · NPU picker probes `docker` on a podman-only runtime** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **SC-3 · high · Raw TOML reads skip backend→device promotion** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **SC-4 · high · "One default per type" refuse-to-save rule is unimplemented** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **SC-5 · high · `create()` has no existence guard → overwrites a custom slot, orphans its container** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **SC-6 · high · `rerank` slot-name split — seeded `rerank` is vestigial** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **SC-7 · medium · Port allocation races across an await** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **SC-8 · medium · `enabled` is overloaded: "autostart on boot" vs "routable now"** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **SC-9 · medium · `SlotConfigStore.commit()` rollback is best-effort** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **SC-10 · medium · No cross-process lock on `capabilities.toml`** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **SC-11 · medium · Slot-projection reconciliation is duplicated by hand** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **SC-12 · medium · Three parallel migration mechanisms for one transform** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **SC-13 · low · `auto_migrate` clobbers the live capabilities file despite its "leave untouched" contract** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **SC-14 · low · Three inconsistent slot-port ranges** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **SC-15 · low · Dead docs reference the retired `primary→chat` alias** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **SC-16 · low · `_profile_for_fit` / `_CAPABILITY_TO_SLOT_TYPE` duplicated** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`

## Relationships

- [hal0 platform review — reliability, config surface & UI](hal0_platform_review_%E2%80%94_reliability%2C_config_surface_%26_UI.md) (1 shared connections)

## Source Files

- `docs/archive/handoffs/platform-review-2026-07-03.md`

## Audit Trail

- EXTRACTED: 33 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*