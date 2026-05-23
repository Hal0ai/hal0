# ADR 0010 — Bundle picker, no default model stack at install

- **Status:** Accepted
- **Date proposed:** 2026-05-22
- **Date accepted:** 2026-05-22
- **Implementing PRs:** PR-17 (bundle picker UI + bundle manifests), PR-18 (dashboard chat surface + persona dropdown) — per adoption plan §11
- **Depends on:** ADR-0008 (Lemonade adoption)

## Context

Most consumer software ships with sensible defaults — picks a competent stack, runs on first boot, lets the user re-decide later. The pull is strong: time-to-first-chat is the demo metric, and a fresh install with no models loaded looks broken.

hal0 deliberately doesn't ship that way. v0.2's `capabilities.toml` lands empty (`enabled = false` for every seeded slot, `model = ""`), and the first-run dashboard surfaces a **bundle picker** the user must engage with — pick a tier, pick the vendor-blessed kit, or explicitly click "Skip — configure manually" — before any model loads. The installer never silently chooses.

This decision sits at a real product tension:

- **The opinionated path.** Pick a default stack at install. Users get a working chatbot in 60 seconds. "Good defaults" are good UX. hal0 looks polished out of the box. Cost: hal0 is implicitly endorsing those specific model choices forever; every future model-catalog change risks invalidating shipped defaults; users may not realise the bundled model is one of many options.
- **The platform path.** hal0 is a platform — orchestrator over Lemonade, with user-defined slots, multiple chat personas, optional NPU trio, label-gated tool routing. The user's stack is their stack. A pre-selected default would be hal0 picking a stack on the user's behalf, silently — exactly the "why did hal0 download THAT model" surprise the project keeps committing to avoid. Cost: slower time-to-first-chat; first-run dashboard must handle a blank, not-yet-loaded state well.

Four options were considered during the /grill-with-docs adoption-plan session 2026-05-22:

| Option | Shape | Disposition |
|---|---|---|
| A | Blank dashboard, no prompt | Rejected — too cold; user has no signal that bundles exist or that the seeded slots are catalog-only |
| B | Per-slot wizard at first run | Rejected — six slot decisions before the user has seen the dashboard is a worse onboarding than one bundle pick |
| C | Bundle picker (4 tiers + LMX kit + Skip) | **Accepted** |
| D | Auto-detect RAM + recommend tier silently | Rejected — auto-applying without an explicit click reintroduces the "silent default" failure mode this ADR exists to avoid |

The deciding factor is closely tied to the user-extensibility decision for slots (see CONTEXT.md `user-defined slots`). Because users can add named slots beyond the seeded six — declaring their own type/model/group — the install cannot reasonably pre-decide the stack a given user will end up with. The bundle picker is a fast path that respects user agency by making the choice **explicit**, not silent.

## Decision

`capabilities.toml` ships empty on every fresh install. Every seeded slot lands `enabled = false`, `model = ""`. No model downloads happen at install time except for the `kokoro:cpu` voice baseline already documented in CONTEXT.md.

First dashboard load detects the empty `capabilities.toml` and renders the bundle picker. The user picks **exactly one** of:

1. One of the four hardware-anchored tiers (Lite / Default / Pro / Max)
2. The vendor-blessed `LMX-Omni-52B-Halo` pre-built kit
3. **Skip — configure manually** → blank dashboard with empty seeded slot cards (each shows a "Configure" button)

### Bundle tiers

Per adoption plan §8.2:

| Bundle | Target RAM | `chat.primary` | `chat.coder` | Aux | NPU trio |
|---|---|---|---|---|---|
| **hal0-Lite** | ≥16 GB | qwen3.5-0.8b (1.0 GB) | — | — | — (not shown) |
| **hal0-Default** | ≥32 GB | qwen3.5-9b (6.9 GB) | — | nomic-v1.5, whisper-tiny, kokoro:cpu | — (not shown) |
| **hal0-Pro** | ≥64 GB | Qwen3.6-27B-MTP (18.8 GB) | Qwen3-Coder-30B-A3B (18.6 GB, LRU) | + bge-reranker, whisper-base, sd-turbo | shown, **opt-in** |
| **hal0-Max** | ≥100 GB Strix Halo | Qwen3.6-35B-A3B-MTP (23.8 GB) | Qwen3-Coder-Next-80B-A3B (48 GB, LRU) | + whisper-large-v3-turbo, flux-2-klein-9b | shown, **opt-in** |
| **LMX-Omni-52B-Halo** *(AMD-curated)* | ≥100 GB Strix Halo | Qwen3.6-35B-A3B-MTP | — | Whisper-Large-v3-Turbo, kokoro-v1, Flux-2-Klein-9B | — |

### Rules

- Four hardware-anchored tiers + one vendor-blessed kit. No additional tiers in v0.2.
- The LMX kit appears under a "Pre-built kits" section **below** the tier picker — not as a fifth tier card. It is shape-different from the tiers (vendor-curated bundle, no per-RAM scaling).
- `gpt-oss-120b` (62.8 GB) and other extreme models are **intentionally excluded** from every default bundle. Power users install them manually via `hal0 model pull` or the dashboard "Add model" form.
- The installer reads `/proc/meminfo` at install time. Tiers that don't fit the detected unified RAM are greyed out in the picker with a tooltip explaining why. The user can override by command-line flag if they want to install a too-large bundle anyway, but the picker never recommends one.
- Bundle manifests live at `/var/lib/hal0/models/collections/omni/<name>.json`. Each is a `collection.omni` Lemonade manifest plus hal0-specific slot-selection metadata.
- Selecting a bundle:
  - Triggers model downloads in the background (progress toast surfaces in the dashboard).
  - Writes the selections into `capabilities.toml` as the slots come online.
  - Marks `default = true` per type on the seeded slot the bundle populates (per the default-slot resolution rules in CONTEXT.md).
- The **NPU trio** (FLM coresident `agent` + `stt-npu` + `embed-npu`) is opt-in even at Pro and Max tiers. The Pro/Max tier cards show the trio's existence and let the user toggle it, but the trio only auto-enables when a bundled agent is also being installed in the same first-run flow (Phase 8 cross-decision; see ADR-0004).
- Selecting "Skip" writes an empty marker to `capabilities.toml` so the picker doesn't re-appear on next load. Users can re-open the picker from the dashboard's settings at any time.

## Consequences

### Positive

- **User agency is structural, not a setting.** The first thing the user does is pick — there is no silent default to discover and reverse later. This matches hal0's platform framing.
- **No "why did hal0 download THAT model" surprise.** Every model on disk traces to a click the user made, either a bundle pick or a manual `model pull`.
- **Future-proof against curated-default drift.** As the model catalog evolves (new Qwen, new Flux, new Whisper), the bundle tiers are versionable, named artefacts (`hal0-Pro.json` etc.) that future installs pick up without changing any installer logic. There is no `DEFAULT_PRIMARY_MODEL` constant in the codebase to keep current.
- **Capacity warnings are visible.** Because the picker reads `/proc/meminfo` and greys out tiers that don't fit, the user learns their hardware ceiling *before* picking — not after a confusing failed model load.
- **Composes cleanly with user-defined slots.** Users who want to add their own slots beyond the seeded six are doing the same thing the bundle picker does (declare slot → pick model), just one slot at a time. The mental model is consistent.
- **The LMX kit slot exists.** Vendor-blessed bundles get a clean home in the UI — below the tier picker, visually distinct — without having to compete with hal0-curated tiers on the same row.

### Negative / accepted costs

- **Slower time-to-first-chat than option D's silent auto-pick.** A user who just wants to chat has to click a tier card first. Mitigated by making the picker a single click for the common case, with the recommended tier visually pre-highlighted based on detected RAM (highlighted, not selected).
- **Dashboard must handle blank-state UX well.** "Skip" leads to a dashboard with six empty slot cards. Those cards need to look like "ready to configure," not "broken." This is a real UI design surface and a v0.2 ship blocker — PR-18 owns it.
- **Bundle manifests are a maintenance surface.** Each bundle JSON pins specific model versions; as the catalog evolves the bundles need version bumps. Drift between catalog and bundle manifests will produce confused users ("hal0-Pro says Qwen3.6-27B-MTP but it's not in the dropdown"). Mitigation: a manifest validation step in CI that asserts every bundle's referenced models exist in `registry.toml`.
- **The bundle picker UX is itself a v0.2 ship blocker.** Without it, the empty `capabilities.toml` is a worse UX than v0.1.x's silently-defaulted one. PR-17 + PR-18 must land together — split would ship a broken state.
- **Onboarding analytics become harder.** Without a default stack, "how many users use the Default tier" is a question only the picker telemetry can answer. v0.2 doesn't ship telemetry so this is deferred, but worth noting for later.

## References

- Lemonade adoption plan §8 (First-run UX bundle picker) — `docs/internal/lemonade-adoption-plan-2026-05-22.md`
- CONTEXT.md — `fresh install`, `bundle tiers`, `slot`, `user-defined slots`, `default slot` entries
- ADR-0008 — Lemonade adoption (provides the runtime layer the bundle manifests target)
- ADR-0004 — Agents (cross-decision for the NPU trio auto-enable rule)
