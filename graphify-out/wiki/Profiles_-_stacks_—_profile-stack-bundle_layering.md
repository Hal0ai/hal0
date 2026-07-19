# Profiles / stacks — profile/stack/bundle layering

> 10 nodes

## Key Concepts

- **Profiles / stacks — profile/stack/bundle layering** (10 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **PS-1 · critical · `DEVICE_DEFAULT_PROFILES['cpu'] = 'tts'` → GPU-less installs get a chat slot on the TTS engine** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **PS-2 · high · Seed-profile *definition* changes never reach existing installs** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **PS-3 · medium · The device/profile coherence guard is blind to `backend=None` profiles** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **PS-4 · medium · Three parallel device→profile derivations (root cause of the #834 churn)** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **PS-5 · medium · Stack apply reports "Applied · clean" while runtime silently diverges** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **PS-6 · medium · `ProfileConfig` has no cross-field validation between `device_class` and `backend`** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **PS-7 · low · First custom-profile write silently rewrites `profiles.toml`** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **PS-8 · low · Pre-existing incoherent device/profile pairings never self-heal and never warn** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`
- **PS-9 · low · Bundle and Stack both express "slot → model" with different slot vocabularies** (1 connections) — `docs/archive/handoffs/platform-review-2026-07-03.md`

## Relationships

- [hal0 platform review — reliability, config surface & UI](hal0_platform_review_%E2%80%94_reliability%2C_config_surface_%26_UI.md) (1 shared connections)

## Source Files

- `docs/archive/handoffs/platform-review-2026-07-03.md`

## Audit Trail

- EXTRACTED: 19 (100%)
- INFERRED: 0 (0%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*