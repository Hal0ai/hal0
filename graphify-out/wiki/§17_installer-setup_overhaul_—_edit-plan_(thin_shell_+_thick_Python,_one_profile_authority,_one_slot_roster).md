# §17 installer/setup overhaul — edit-plan (thin shell + thick Python, one profile authority, one slot roster)

> 36 nodes

## Key Concepts

- **§17 installer/setup overhaul — edit-plan (thin shell + thick Python, one profile authority, one slot roster)** (11 connections) — `docs/rework/hal0-specs/spec-17-installer.md`
- **PART C — Edit plan (9 ordered PRs, with the cluster atomicity noted)** (10 connections) — `docs/rework/hal0-specs/spec-17-installer.md`
- **PART B — Target architecture (the single-source maps, after this spec lands)** (9 connections) — `docs/rework/hal0-specs/spec-17-installer.md`
- **PART F — Risks + capped verification** (5 connections) — `docs/rework/hal0-specs/spec-17-installer.md`
- **PART D — Sequencing (cluster atomicity + cross-PR dependencies)** (4 connections) — `docs/rework/hal0-specs/spec-17-installer.md`
- **spec-17-installer.md** (1 connections) — `docs/rework/hal0-specs/spec-17-installer.md`
- **0. Executive summary** (1 connections) — `docs/rework/hal0-specs/spec-17-installer.md`
- **1. Verification note** (1 connections) — `docs/rework/hal0-specs/spec-17-installer.md`
- **PART A — Design recap (from §17; this is what we're turning into PRs)** (1 connections) — `docs/rework/hal0-specs/spec-17-installer.md`
- **B.1 Package layout — `src/hal0/installer/` (was empty stub; becomes the provisioner)** (1 connections) — `docs/rework/hal0-specs/spec-17-installer.md`
- **B.2 The one slot roster (`src/hal0/installer/slots.py`)** (1 connections) — `docs/rework/hal0-specs/spec-17-installer.md`
- **B.3 The one profile fn (`src/hal0/installer/profile.py`)** (1 connections) — `docs/rework/hal0-specs/spec-17-installer.md`
- **B.4 The one budget fn (`src/hal0/installer/profile.py::budget_for`)** (1 connections) — `docs/rework/hal0-specs/spec-17-installer.md`
- **B.5 The provisioner CLI (`hal0 provision`)** (1 connections) — `docs/rework/hal0-specs/spec-17-installer.md`
- **B.6 Converged fast-path (`src/hal0/installer/plugins.py`)** (1 connections) — `docs/rework/hal0-specs/spec-17-installer.md`
- **B.7 The minimal wizard** (1 connections) — `docs/rework/hal0-specs/spec-17-installer.md`
- **B.8 P3-perms handoff (what this spec ASSUMES, does not redo)** (1 connections) — `docs/rework/hal0-specs/spec-17-installer.md`
- **PR §17.1 — `installer/install.sh` shrinks to thin bootstrap (~2385 → ~200 lines)** (1 connections) — `docs/rework/hal0-specs/spec-17-installer.md`
- **PR §17.2 — One profile authority (`derive_profile` folds 6→1)** (1 connections) — `docs/rework/hal0-specs/spec-17-installer.md`
- **PR §17.3 — One slot roster (`SLOT_ROSTER` kills ×4 mirror)** (1 connections) — `docs/rework/hal0-specs/spec-17-installer.md`
- **PR §17.4 — Converged fast-path (`InstallerPlugin` protocol + plugin wiring)** (1 connections) — `docs/rework/hal0-specs/spec-17-installer.md`
- **PR §17.5 — `OwnershipStore` wrapper in the installer package (P3-perms handoff)** (1 connections) — `docs/rework/hal0-specs/spec-17-installer.md`
- **PR §17.6 — Thin shell wiring (the `install.sh` ↔ `hal0 provision` seam)** (1 connections) — `docs/rework/hal0-specs/spec-17-installer.md`
- **PR §17.7 — Remove Honcho block (unconditional)** (1 connections) — `docs/rework/hal0-specs/spec-17-installer.md`
- **PR §17.8 — Minimal wizard (delete `setup_ui.py`)** (1 connections) — `docs/rework/hal0-specs/spec-17-installer.md`
- *... and 11 more nodes in this community*

## Relationships

- No strong cross-community connections detected

## Source Files

- `docs/rework/hal0-specs/spec-17-installer.md`

## Audit Trail

- EXTRACTED: 70 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*