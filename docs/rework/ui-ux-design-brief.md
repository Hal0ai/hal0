# hal0 UI/UX design brief — post-R3 surface rework

> PROMPT — paste this whole document into a Claude design session. If the session has repo
> access, the referenced paths resolve; if not, every constraint needed is stated inline.
> Written 2026-07-18 by the rework orchestrator; decisions cited here are ratified and final
> unless marked OPEN.

## Your role

You are the UI/UX designer for **hal0**, an open-source, self-hosted home AI inference
appliance for AMD Strix Halo (Vue/React dashboard + FastAPI control plane + Podman-backed
inference "slots" + systemd). Your job: design the dashboard changes that the just-completed
backend rework now requires — as concrete component/interaction specs an implementation agent
can build from, not mood boards. The operator is one technical homelab admin on one box;
optimize for legibility and trust, not multi-tenant ceremony.

**Before designing anything, inspect the existing UI** (`ui/src/dash/`) and match its
conventions: the settings shell + ~16 page components (`ui/src/dash/settings/pages/`), the
typed `settingsClient` façade + `useSettingsForm` hook + `ApplyBadge`/`reloadClass` pattern
(a P3-ui-dataseam lane landed these — every new page uses them; no window-globals, no ad-hoc
fetch), and the existing Playwright γ-suite (~430 specs) selector style. `slot-modals.jsx`
(~2,190 lines) is a diagnosed god-module — your slot designs should decompose it, not grow it.

## The architecture decisions your designs must express (ratified)

These change what the UI *means*, not just how it looks:

1. **Flags are materialized onto models; profiles are copy-on-stamp templates.**
   A model owns its full launch-flags text. Choosing a profile in the model editor COPIES the
   profile's flags into the model's own editable text; saving saves to the model; the profile
   is never mutated by model edits. There is NO live inheritance layer — what you see in the
   model's flags editor is exactly what launches. Provenance is remembered (which profile
   seeded it) and divergence is a derived, informational fact.
2. **Slots are pure instances: `(slot_id, name-label, model, port, state)`.**
   A slot has NO flags, NO device picker, NO chat-template field of its own. Identity is the
   stable numeric `slot_id`; the name is a mutable display label (`POST /{name}/rename`
   exists today; rename requires the slot offline until a later migration — surface that).
   Ports come from a SQLite PortAuthority — display-only in the UI, never editable.
3. **Container images belong to runners.** A code registry (`RUNNER_IMAGES`) pins each
   runner's image+digest; models select a runner via `preferred_runner`; slots inherit
   through the model. No image strings anywhere in slot/model/profile editors. Image updates
   arrive with hal0 releases and are reconciled by the updater.
4. **Device (rocm/vulkan/npu) is a property of the model's stamped tune**, not of the slot.
   "Run this model on the NPU" = stamp the model with an NPU profile template, or duplicate
   the model row (weights are refcounted — duplicates are metadata-cheap).
5. **Typed capabilities stay typed.** `mtp`, `jinja`, `chat_template`, modality are discrete
   model fields, never buried in the freeform flags text. The flags text is the tune
   remainder (`-dev`, `-b/-ub`, `--threads`, `-fa`, KV-quant, …).
6. **Managed args are non-editable, ever:** model path, host, port, and other
   authority-owned values are computed; the flags editor must REJECT them on save (a
   denylist exists server-side — design the error surface).

Full decision record: `docs/rework/hal0-specs/spec-flags-ownership.md`,
`docs/rework/slot-model-architecture.md` (diagrams of the runtime model).

## Deliverable 1 — Model editor (drawer) redesign  [highest priority]

Design the model drawer around "the model is the launchable thing":

- **Template picker + flags editor.** Profile dropdown (template library). Selecting one
  seeds/replaces the flags textarea — confirm before clobbering dirty text. The textarea
  shows the REAL, editable, effective tune text. Monospace; consider soft syntax hints
  (flag tokens), but it is text, not a form.
- **Provenance + divergence.** A chip showing the source profile; a "diverged" state when
  model text ≠ that profile's current text, with a diff affordance (view what changed) and
  an explicit **Reset to profile** action (re-stamp, confirm).
- **Validation surface.** On save: shlex parse errors, managed-arg rejections (name the
  offending flag, say WHY it's managed and where it's controlled from), JSON-token integrity.
  Design inline, specific errors — not a toast.
- **Runner selector.** Derived default (from architecture/capabilities) with an override
  dropdown filtered to COMPATIBLE runners; incompatibility (e.g. runner fork can't load this
  GGUF format) warns at selection time, not at spawn. Show the runner's image+digest
  read-only.
- **Typed capability fields** (mtp / jinja / chat_template / modality) as discrete controls,
  visually separate from the freeform tune text.
- **Device flavor.** Since device rides the stamped tune: make the template picker the device
  gesture (templates are device-flavored: `rocm-dense`, `vulkan-…`, NPU), and design the
  "duplicate model for a second device" flow (one click from the drawer; names the new row
  sensibly).

## Deliverable 2 — Slot surfaces simplification

- **Create-slot flow**: pick a model (which already carries tune/device/runner), name it,
  done. If the user reaches for a device choice here, the affordance redirects to the model
  stamping flow (design that redirect so it teaches the mental model instead of frustrating).
- **Slot card/detail**: state, model (link to drawer), port (read-only, "assigned by
  PortAuthority" affordance), stable id visible somewhere unobtrusive (it's the API key for
  debugging), name-label edit = rename flow with the offline-only constraint surfaced
  honestly (disabled-with-reason when running).
- **Delete slot**: confirm dialog that shows the blast radius truthfully (unit removed, port
  released, state deleted; model/weights untouched).
- **Decompose `slot-modals.jsx`** as part of this: propose the component split.

## Deliverable 3 — "Runtimes" settings page (new)

One page for the runner/image axis: a row per runner — family, image ref + digest
(read-only), status (current / stale vs shipped registry / pulling), and which models + slots
resolve to it. Actions: pre-pull / re-pull. This is evidence UI, not config UI — nothing here
edits an image string. Updates land via hal0 releases; the page shows drift, the updater
reconciles it.

## Deliverable 4 — Security settings page (deferred from KB-1, still owed)

Admin/client key management: view key *status* (set/unset, never values), rotate with
confirm + "existing clients will break" messaging, login-throttle status, and a read-only
exposure table view (route class: OPEN / CLIENT / ADMIN / BOOTSTRAP — the backend's
deny-by-default table is the source). Everything here is ADMIN-gated; assume the session is
already admin (browser HMAC session = admin-equivalent).

## Deliverable 5 — Migration-moment UX

The flags/slot migration folds slot-level overrides into models. When multiple slots share a
model with DIVERGENT overrides, the migrator refuses that model and reports it for the
operator to resolve. Design that resolution moment: what the operator sees (which slots,
which values differ), and the two resolutions (pick one canonical / split into a second model
row). Rare, but it is the single moment the new mental model must be taught correctly.

## Deliverable 6 (lower priority) — Diagnostics surfacing

`hal0 doctor` now emits typed diagnoses (`HAL0-*` ids, severity, evidence, next steps) and
`GET /api/system-info` exists (CLIENT-gated). Propose (lightly — a sketch, not full spec) how
doctor verdicts and system evidence could surface in the dashboard (e.g. a health panel that
renders Diagnosis objects generically), so the backend taxonomy and any future UI stay
aligned.

## Constraints (hard)

- Frozen FE↔BE transport contracts stay frozen (e.g. the dashboard board-chat slide-out).
  New pages talk through `settingsClient`-style typed clients; flag any new endpoint you
  need — every new route requires a deny-by-default exposure classification server-side.
- Reload/restart consequences of a setting change come from the single `reloadClass`
  source — design the ApplyBadge/consequence messaging with that, never hardcode.
- Playwright-testable: stable selectors/test-ids on every interactive element; call out the
  γ-spec intents per design (what a test should assert).
- Single-box appliance ethos: no org/team/multi-user constructs; terse, honest, technical
  voice in copy ("port assigned by PortAuthority", not marketing).
- Out of scope: Hermes voice/automation UI, Hermes dashboard extensions, Grafana/Prometheus
  panels, document-RAG — all post-core.

## What to return

For each deliverable: (1) user flows, (2) component-level specs mapped to the existing page/
component structure (name files/components to create or modify), (3) states — empty/loading/
error/degraded, (4) exact copy for the tricky moments (managed-arg rejection, divergence,
rename-offline, migration refusal), (5) γ-test intents, (6) an implementation phasing that
matches lane discipline: UI-class lanes that each land green independently, ordered
Model-drawer → Slots → Runtimes → Security → migration UX. Flag every place where you need a
backend affordance that does not exist yet (e.g. a diff endpoint for profile divergence) —
those become API lane requests, not silent assumptions.
