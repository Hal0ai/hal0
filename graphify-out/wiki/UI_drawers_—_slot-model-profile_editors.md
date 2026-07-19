# UI drawers — slot/model/profile editors

> 22 nodes

## Key Concepts

- **UI drawers — slot/model/profile editors** (22 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **UI-1 · high · No unsaved-changes guard on any editing surface** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **UI-2 · high · The Edit-slot drawer mixes instant-apply and batched-save controls with no visual distinction** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **UI-3 · high · Three subtly different "compatible models" filters — one ships incompatible ids** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **UI-4 · high · The Edit-slot drawer exposes all four ownership layers (base / profile / model / slot) at once** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **UI-5 · medium · Model swap in the drawer = unconfirmed immediate cold restart** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **UI-6 · medium · "✓ fits in available memory" in the create modal is a fake check** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **UI-7 · medium · Slot-name validation is stricter than the backend and inconsistent across the three drawers** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **UI-8 · medium · The first-run "configure your slots" empty state seeds the wrong slot identities** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **UI-9 · medium · The download "Pause" button silently cancels (destroys) the pull** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **UI-10 · medium · "Used by" model→slot matching has a dead branch → the delete-cascade warning under-reports** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **UI-11 · medium · Two `normalizeApiModel` implementations applied on top of each other** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **UI-12 · medium · Massive drawer/form duplication between `stacks.jsx` and `profiles.jsx`, shadowing the shared primitive** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **UI-13 · medium · Component wiring via window globals instead of imports — documented load-order fragility** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **UI-14 · medium · Modal/Drawer claim focus management they don't implement; field labels aren't real `<label>`s** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **UI-15 · medium · The Stack editor lets you build a device/profile pair the create flow makes impossible** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **UI-16 · low · Destructive slot delete uses a raw `window.confirm`, unlike every other delete in the app** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **UI-17 · low · `ctx_size` inline messaging contradicts what Save actually does** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **UI-18 · low · `EditSlotDrawer` is an ~825-line component built from five inline IIFEs** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **UI-19 · low · Three unrelated form-state patterns for four near-identical editors** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **UI-20 · low · MTP toggle lags while Reasoning is optimistic — same section, different behavior** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **UI-21 · low · Changing Type after selecting a Model leaves a stale, now-incompatible model id in create state** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`

## Relationships

- [hal0 platform review — reliability, config surface & UI](hal0_platform_review_%E2%80%94_reliability%2C_config_surface_%26_UI.md) (1 shared connections)

## Source Files

- `docs/archive/handoffs/platform-review-2026-07-03.md`

## Audit Trail

- EXTRACTED: 43 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*