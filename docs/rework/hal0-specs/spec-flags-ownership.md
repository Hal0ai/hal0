# FLAGS-own — llama.cpp flags stick to models; profiles become templates

> Decision ratified 2026-07-18 (user directive, R4-primary session). Supersedes the *layering*
> half of ML-5 §3.3: the 7-segment last-wins engine stays, but the user-editable flag surfaces
> collapse to TWO (profile-as-template, model-as-owner) and the launch-time chain stops reading
> profiles and slots.

## 1. Decision

Launch flags attach ONLY to **models**. **Profiles** remain as reusable *templates* ("start
points") that are **copied, not layered**: selecting a profile in the model drawer copies the
profile's flags into the model's own editable flags text; **saving saves to the model — the
profile is never mutated by model edits**. Slots carry **no** user flag overrides.

Rationale (one-owner rule): with live layering, the effective argv has three writers and the UI
must explain merge semantics forever. With materialization, the model owns its full launch tune
as plain visible text; the profile is provenance + a re-stamp source, nothing more.

## 2. Resolution chain (new)

```text
runner image/supports (registry, computed)
  -> architecture defaults (FAMILY_DEFAULTS, computed)
  -> model flags text (materialized; seeded from profile template at edit time)
  -> managed args (computed, non-overridable: model path, host, port [PortAuthority],
     alias/authority-owned values — §21.7 denylist enforced at save AND launch)
```

- `resolve_argv` / `merge_flags` (slots/argv.py) unchanged — segment *contents* change:
  the `profile` segment and the slot `slot_overrides`/`extra_args` segments are deleted from
  the chain; the `model_defaults` segment carries the materialized text.
- Typed capabilities (`mtp`, `jinja`, `chat_template`, modality) stay **typed fields** on the
  model (ML-5, done) — they are NOT folded into the freeform text. The text is the tune
  remainder only (`-dev`, `-b/-ub`, `--threads`, `-fa`, KV-quant, `--no-mmap`, …).
- Profile flags NO LONGER enter launch argv directly. A profile is read exactly twice:
  (a) when stamping a model in the drawer, (b) when the operator explicitly re-applies it.

## 3. Model drawer semantics (UI)

- Profile selector (template dropdown) + flags **textarea containing the actual effective
  tune text** — no ghost/inherited values, what you see is what launches.
- Selecting a profile **seeds/replaces** the textarea (confirm if dirty). Saving writes
  `model.defaults`. Profile stays untouched.
- **Diverged chip**: derived diff between model text and its source profile's current text
  (informational only) + a **"Reset to profile"** action (explicit re-stamp).
- Validation on save: shlex-parse, §21.7 managed-arg denylist rejection, and JSON-token
  integrity (the container.py quoting fix from SLOT increment B is a hard dependency —
  editable text with `--chat-template-kwargs '{"…":…}'` must survive to the runner intact).
- Profile editor pages remain, as the template library (create/clone/edit templates).

## 4. Slots lose the flag surface

A slot becomes: `(slot_id, name-label, model ref, port [authority], lifecycle state)`. Backend
and device selection ride the model's materialized tune + runner registry, not slot fields.
Two slots of the same model launch identically (modulo computed managed args). A genuine
per-instance exception = a new model registry row (cheap) or a profile fork + re-stamp —
NOT a hidden slot override.

## 5. Migration (one-shot, fold into the P2-config window; AFTER SLOT increment B)

1. For each slot TOML carrying `[server].extra_args`/ngl/ctx/tune overrides: compute the
   slot's current *effective* tune (existing resolver, verbatim) and write it to the slot's
   model as materialized text; record source profile as provenance; delete the slot fields.
2. Sole-referencing slot → auto-fold. **Multiple slots referencing one model with divergent
   overrides → migrator refuses that model and reports** (operator resolves: pick one, or
   split model rows). Expected rare on a single box.
3. Idempotent, re-runnable, snapshot-first — standard migration-window rules.
4. Readers of slot-level flags become expired shims with a sunset (scar-marked).

## 6. Sequencing + fences

- Class: MODEL + UI (+ container.py edge ⇒ serialize behind SLOT increment B's merge).
- Depends: ML-4/5 ✔, P3-ui-dataseam ✔ (settingsClient/drawer seam), SLOT-B (quoting fix).
- Coordinates with P2-config (migration window) and P3-schema (profile seeds in share/*.toml).
- Does NOT touch: PortAuthority, exposure classes (no new routes expected — model PATCH
  surface exists), the argv engine.

## 7. IMAGE axis + container-image management (companion decision)

`docs/rework/slot-model-architecture.md` maps the CURRENT image axis:
`slot.device → model.preferred_runner → RUNNER_IMAGES[backend]`, with a `profile.image` pin
override. Target collapses it to TWO participants:

```text
RUNNER_IMAGES[runner]      (code registry, digest-pinned — ML-4; ships with hal0 releases)
model.preferred_runner     (selects the runner; validated against fit/format compat)
```

- **Images belong to RUNNERS, full stop.** Neither slots nor models nor profiles carry raw
  image strings. `profile.image` pin: delete (ML-4 §3.2 already directs image out of profiles;
  the pin is the last crosscutting layer). `SlotConfig.image` override: expiring shim, sunset.
- **RATIFIED (user, 2026-07-18): `slot.device` and per-slot `chat_template` fold into the
  model.** With materialized flags,
  the model's tune text is already device-flavored (`-dev`, `-ngl`, backend choice); a separate
  slot-level device knob would silently contradict the stamped text. Same model on two devices
  = two model rows (weights are refcounted/shared — no storage cost) or a profile re-stamp.
  `chat_template` is model-intrinsic (which template the artifact needs — also where the
  no-think fix naturally lives). **Slot becomes purely `(slot_id, name, model, port, state)`.**
- **Update flow ("from hal0 repo sources"):** RUNNER_IMAGES digests are code, updated by hal0
  releases; `updater.retag_stale_slot_images` reconciles running slots after an update. No
  floating tags, no UI-editable image strings.
- **UI/UX — one "Runtimes" panel (settings):** one row per runner — family, image ref, digest,
  state (current / stale-vs-shipped / pulling), and which models+slots resolve to it; actions:
  pre-pull, re-pull, view pinned digest. Read-only image refs.
  **Model drawer:** runner shown as a derived default (architecture/capability → preferred
  runner) with an override dropdown filtered to COMPATIBLE runners (fit-check + supported-
  format metadata — lxc105 finding: forks reject newer GGUFs; the registry should carry a
  format/arch-support field so incompatibility warns at assignment, not at spawn).

## 8. Verification

- Unit: seeding/stamping (profile→text copy), divergence diff, denylist rejection, JSON-token
  round-trip through container launch argv.
- Migration tests: fixture slots with overrides → folded models; divergent-share refusal path.
- Golden path #5 (pull→assign→infer) must pass with a stamped model and NO profile read at
  launch (assert profile resolver not consulted post-stamp).
- UI γ: drawer seed/edit/save/reset-to-profile; profile edit does not change a stamped model.
