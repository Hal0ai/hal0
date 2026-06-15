# Retire the FirstRun web picker — fold setup into a `hal0 setup` rich TUI

- **Date:** 2026-06-14
- **Status:** Design — pending user review
- **Branch:** `docs/retire-firstrun-installer-tui`
- **Author:** Claude (with Alexander)

## 1. Motivation

The web "FirstRun" picker has become a maintenance sink. It is a 666-line
prototype (`ui/src/dash/firstrun.jsx`) that we keep re-debugging: tier-name
canonicalization (#821), skip-sentinel regressions (#823), per-slot override
coherence (#807/#812), gate query invalidation, e2e churn. Each fix is a
round-trip through a browser surface to exercise logic that already lives in
plain Python.

The key realisation from the code audit: **the entire FirstRun *backend* is
already importable Python, and the web UI is a thin client over it.** Tier
registry (`bundles/tiers.py`), RAM eligibility (`bundles/eligibility.py`),
hardware-driven device/profile derivation (`install/profile_derive.py`), the
orchestrated multi-slot install (`/api/install/apply` in
`api/routes/installer.py`), the model pull engine (`registry/pull.py`), and the
curated catalogue (`registry/curated.py`) all run without a browser.

So "fold the picker into the installer" is **mostly a new TUI front-end calling
logic that already exists** — not a rewrite. We delete the web surface, refactor
the orchestration body out of its HTTP route into a reusable module, and build a
`rich`-based terminal setup experience that feels premium while staying
lightweight enough to run over SSH/tmux.

## 2. Goals / Non-goals

### Goals
- A single `hal0 setup` command that walks a user through first-run
  configuration in the terminal with a premium, always-on context pane.
- `curl hal0.dev/install.sh | bash` stays a working one-liner: the installer
  runs `hal0 setup --auto` non-interactively with hardware-recommended defaults.
- Increase install-time customization vs. today: a selectable **Extensions**
  step (Apps / Agents) and per-slot model choice.
- Delete the web FirstRun picker and the dead v1 bundles surface entirely.
- Keep the roster coherent whether setup runs before or after `hal0-api` is up
  (hybrid execution).

### Non-goals (deferred)
- **Stacks.** The future "stacks" concept (runtime-switchable, named full-layout
  snapshots — *Coding* ↔ *Research* ↔ *Image-Gen*) is the better long-term
  abstraction and will eventually retire the bundle tier matrix outright. We do
  **not** port the 5-tier `BundleGrid`/advanced-override-drawer UI into the TUI.
  We do **not** build stacks here. We keep the bundle backend (`tiers.py`,
  manifests, `eligibility.py`) dormant and unsurfaced; the TUI uses the simpler
  `recommend.py` + curated-catalogue path instead. When stacks land, first-run
  gains a "start from a Stack" option and bundles fully retire. See §13.
- No image-gen / ComfyUI slot in first-run setup (post-install concern).
- No change to the slot runtime, podman dispatch, or the registry format.

## 3. Locked decisions

| # | Decision | Choice |
|---|----------|--------|
| 1 | Retire scope | Remove the web picker + dead v1 bundles surface. Refactor `/apply` orchestration into an importable module. Keep `/api/install/apply` as a thin route for the API-up branch. |
| 2 | Interactivity under `curl \| bash` | install.sh runs `hal0 setup --auto` (non-interactive, recommended defaults). The interactive premium TUI is `hal0 setup`, run on a real tty post-install. |
| 3 | TUI tech | `rich` (already a dependency). No textual. |
| 4 | Context pane | Two-column layout, **redraw-per-step** (rich `Layout` via `console.clear()` + print, input at the bottom). Not continuously animated. |
| 5 | Apply execution | **Hybrid**: in-process Python when `hal0-api` is down (install time), through `POST /api/install/apply` when it is up (so the running service registers slots live — no restart, no roster drift). |
| 6 | Extension ↔ slot influence | **Gating** (see §6.3): extension picks decide whether slot steps appear. |
| 7 | Setup scope | Minimal hardware-guided walk: Extensions → (Main) → (Agent) → NPU. No tier matrix. |
| 8 | Primary-slot naming | The default model slot is called **Main** (the primary model OWUI + agents route to), not "Chat". TUI-facing label only; backend slot id stays `chat`/`primary` (the `hal0/primary` virtual name) — a backend rename is out of scope. |

## 4. Architecture

```
                 ┌──────────────────────────────────────────────┐
                 │  hal0 setup   (rich TUI, cli/setup_command.py) │
                 │  --auto skips prompts, takes recommended picks │
                 └───────────────┬──────────────────────────────-┘
            probe api reachable? │
                 ┌───────────────┴────────────────────┐
            NO (install time)                   YES (post-install)
                 │                                     │
   install/orchestrate.apply_setup()         POST /api/install/apply
                 │                                     │  (route → same apply_setup)
                 └──────────────────┬──────────────────┘
                                    ▼
   suggest.py (hw→ranked picks) · extensions.py (registry) ·
   profile_derive (device/profile) · slot_manager.create(OFFLINE) ·
   registry/pull.run_pull (SSE progress) · extension install + wiring
                                    ▼
              write sentinel /var/lib/hal0/.first_run_done
```

The TUI is purely a presentation + selection layer. All mutation goes through
`install/orchestrate.apply_setup(selections, *, hardware)`, which is the single
source of truth for "given a set of choices, make the system match them." The
HTTP route becomes a ~5-line wrapper around it; the in-process branch calls it
directly.

### 4.1 Module map

**New:**
- `src/hal0/install/orchestrate.py` — `apply_setup(selections, *, hardware) -> SetupResult`. Pure-ish: creates slots OFFLINE, seeds pull jobs, installs+wires extensions, writes sentinel. Best-effort per item (skip-with-reason, never abort) — preserves today's `/apply` semantics.
- `src/hal0/install/suggest.py` — generalizes `hardware/recommend.py` from "pick a primary chat model" to "given hardware + a capability + current selections, return ranked curated picks." Drives every slot step.
- `src/hal0/install/extensions.py` — the Extensions registry (`Extension` dataclass + `EXTENSIONS` list). Apps and Agents.
- `src/hal0/cli/setup_command.py` — the `hal0 setup` Typer command + the rich TUI step machine.
- `src/hal0/cli/setup_copy.py` — curated per-step context-pane copy (marketing/educational), kept out of the logic.

**Refactored:**
- `src/hal0/api/routes/installer.py` — `POST /apply` body lifts into `orchestrate.apply_setup`; route becomes a thin wrapper. `/state`, `/probe`, `/complete` (sentinel) stay.

**Deleted:** see §5.

### 4.2 Setup session state (`SetupState`)

A single dataclass threaded through every step, accumulating choices. Cross-
influence (§6.3) reads earlier fields to decide later steps.

```python
@dataclass
class SetupState:
    hardware: HardwareInfo
    storage_dir: str
    extensions: dict[str, bool]          # id -> enabled
    main: SlotChoice | None = None       # the primary model slot: model_id, device, profile
    agent: SlotChoice | None = None
    npu_trio: bool = False
    # derived helpers
    def needs_main(self) -> bool: ...     # OWUI on OR any agent on
    def needs_agent(self) -> bool: ...    # any agent extension on
```

## 5. Demolition list

**Frontend (`ui/`):**
- `src/dash/firstrun.jsx` (all 666 lines)
- `src/api/hooks/useFirstRun.ts`
- `src/dash/install-state-bridge.ts`
- FirstRun routing in `src/dash/main.jsx`: `frStage` state, the auto-route
  effect (lines ~199-203), the `firstRunLayout` tweak + "Jump to FirstRun"
  button, the `useInstallState` import wiring.
- FirstRun endpoint constants in `src/api/endpoints.ts` (lines ~283-293).
- e2e: `ui/tests/e2e/specs/firstrun-v2.spec.ts`, `firstrun-v3.spec.ts`.
- Dead components still exported but unwired: `FirstRunPicker`, `FirstRunStorage`.
- The `HAL0_DATA.bundles` fixture in `src/dash/data.jsx` (the static tier cards).

**Backend (`src/hal0/`):**
- v1 bundles HTTP surface: `api/routes/bundles.py` and its mount.
- `bundles/store.py` (`.bundle-chosen` marker, `mark_bundle_chosen`,
  `apply_bundle_to_capabilities`, `read_choice`, `mark_skipped`).
- `POST /api/install/pick-default` and `PUT /api/install/slots/{slot}/model`
  (v1 single-model paths superseded by `apply_setup`).
- Tests for the above: `tests/api/test_bundles_route.py`, the pick-default
  cases in `tests/api/test_installer_routes.py`.

**Kept dormant (NOT deleted — reused or reserved for stacks):**
- `bundles/tiers.py`, `bundles/schema.py`, `bundles/eligibility.py`,
  `installer/manifests/omni/*.json` — no longer surfaced in any UI, retained as
  the seed for stacks. (Optionally move under a `legacy/` or `reserved/`
  namespace; decide during Phase 6.)
- `registry/curated.py`, `registry/pull.py`, `hardware/probe.py`,
  `hardware/recommend.py`, `install/profile_derive.py` — actively reused.

## 6. The setup flow

Eight screens, each rendered in the two-column shell. Left column = the step's
question/selection; right column = the persistent context pane (copy from
`setup_copy.py`) plus a compact "Detected: <hw>" footer.

### 6.1 Layout shell

```
┌─ hal0 setup ──────────────────────────────────────────────────────────────┐
│                                          │                                  │
│  <STEP TITLE>                            │  ✦ <pane headline>               │
│                                          │                                  │
│  <step body: table / checklist / prompt> │  <pane body copy — swaps per     │
│                                          │   step from setup_copy.py>       │
│                                          │                                  │
│  <key hints>                             │  Detected: <platform · ram · npu>│
└──────────────────────────────────────────┴──────────────────────────────────┘
```

Implementation: `rich.layout.Layout` split left/right. Each step
`console.clear()` → `console.print(layout)` → take input below via
`rich.prompt.Prompt/Confirm` (or a small `space-to-toggle` checklist helper for
multi-select). Redraw on every transition. No `Live` except the install step
(§6.7), where `Live` renders the same Layout with progress bars in the left
column.

### 6.2 Steps

1. **Welcome + hardware** — render `HardwareProbe` cards (RAM / GPU / NPU /
   platform). Pane: what hal0 is, "we detected your hardware and tuned the
   defaults below." No input but Enter to continue.
2. **Storage location** — model-store dir, suggestions from `model_store_probe`,
   default `/var/lib/hal0/models`. Pane: where models live, disk implications.
3. **Extensions** — grouped checklist (Apps / Agents), defaults pre-checked
   (OWUI + Hermes on, Pi off). Pane: the "one-shot perfection" auto-wiring
   message (§6.4).
4. **Main slot** (primary model) — *shown iff `state.needs_main()`*. 2-3 curated
   models that fit RAM from `suggest.py`, recommended starred, size/ctx/backend
   shown. This is the `hal0/primary` slot that OWUI and agents route to. Pane:
   what the Main slot is, which extensions consume it.
5. **Agent slot** — *shown iff `state.needs_agent()`*; otherwise skipped
   silently. Suggestions biased toward coder models if Pi is enabled (§6.3).
   Offers "use same model as Main" / "skip."
6. **NPU trio** — if NPU present: one Y/N to put embed+stt+tts on FLM. If absent:
   skipped silently. Pane: what the NPU trio buys, latency note.
7. **Review + confirm** — table of every slot → model → device/profile and every
   extension that will be installed/wired. One confirm.
8. **Install (Live)** — slots created OFFLINE first, then `run_pull` jobs stream
   into per-model progress bars; extensions install + wire. Pane: live "what's
   happening" copy. On completion → write sentinel → print dashboard URL + the
   "hello" greeting install.sh already produces.

### 6.3 Cross-influence (gating) rules

Extension picks gate slot steps. Explicit rules:

- `state.needs_main()` = OWUI enabled **OR** any agent enabled. (Agents route to
  the primary slot too — Hermes → `hal0/primary` — so the Main slot is required
  whenever anything consumes it.) → controls whether **Step 4 (Main)** shows.
- `state.needs_agent()` = any agent extension (Hermes/Pi) enabled. → controls
  whether **Step 5** shows. No agent checked → Agent slot is skipped entirely.
- If **Pi** (coding agent) is enabled, `suggest.py` for the agent slot biases
  toward `capability=coder` curated models; otherwise general/instruct models.
- If OWUI is disabled, the Main-step pane drops the "set as default chat UI"
  note.

The earlier "Hermes-without-OWUI" ambiguity is resolved by the **Main** naming
(decision 8): the slot is the primary model, not OWUI-specific, so it shows
whenever OWUI **or** any agent is enabled. The TUI label is "Main"; the backend
slot id remains `chat`/`primary`.

### 6.4 Extensions registry (`extensions.py`)

```python
@dataclass(frozen=True)
class Extension:
    id: str
    kind: Literal["app", "agent"]
    name: str
    summary: str
    default_enabled: bool
    install: Callable[[SetupState], InstallResult]   # enable unit OR `hal0 agent install <id>`
    wire: Callable[[SetupState], None] | None = None  # base_url/routing/creds

EXTENSIONS = [
    Extension("openwebui", "app",   "Open WebUI", "Chat web UI for your models",   True,  ...),
    Extension("hermes",    "agent", "Hermes",     "Conversational agent + memory", True,  ...),
    Extension("pi",        "agent", "Pi",         "Coding agent",                  False, ...),
]
```

This is genuinely new capability, not just UI: today install.sh installs OWUI +
Hermes **unconditionally**. Making them a selectable, auto-wired list is the
feature. The "automagically wired" pane copy is true (and historically
hard-won — Hermes `base_url` routing, OWUI → hal0-api, gateway envfile), which
is exactly why it earns a callout. The list is designed to grow.

### 6.5 `suggest.py`

Generalizes `recommend.py`. Signature roughly:

```python
def suggest_models(capability: str, hw: HardwareInfo, state: SetupState,
                   *, limit: int = 3) -> list[Suggestion]:
    # filter CURATED_MODELS by capability + vram/ram fit, rank by size/quality,
    # mark the top as recommended, honor Pi-coder bias for agent capability.
```

Returns enough to render a selection table (display name, size_gb, ctx, derived
device/profile via `profile_derive`, recommended flag).

### 6.6 `apply_setup`

Mirrors today's `/api/install/apply` algorithm, generalized to the new
selections:
1. Persist `storage_dir`.
2. For each chosen slot (main, agent): resolve curated `hf_repo/hf_file`,
   `derive_device`/`derive_profile` (or honor explicit choice),
   `slot_manager.create(slot, cfg, state=OFFLINE)`, seed a `run_pull` job.
3. NPU trio: if opted in, flip embed/stt/tts slots to FLM/NPU.
4. Extensions: call each enabled extension's `install` then `wire`.
5. Write sentinel `/var/lib/hal0/.first_run_done`.
Best-effort per item; collect skip reasons into `SetupResult.skipped`.

### 6.7 Live progress

The install step uses `rich.Live` rendering the same `Layout`. Left column holds
one progress bar per model pull (reattach to `run_pull` SSE/job progress, the
same mechanism `FrDownloadRow` used). Slots are created OFFLINE immediately;
downloads stream; the user can let them finish in the background. Right pane
keeps the context copy.

## 7. install.sh integration

Replace two blobs in `installer/install.sh`:
- The models-dir `read -p` prompt (~lines 193-200).
- The single-slot hardware-probe block (~lines 693-746) that today silently
  seeds only `chat.toml`.

…with a single call **after the Python env is built (stage 4, ~line 400)**:

```sh
hal0 setup --auto ${HAL0_MODELS_DIR:+--storage-dir "$HAL0_MODELS_DIR"}
```

`--auto` takes the recommended pick for every slot and the default extension set
(OWUI + Hermes), runs the in-process branch (API not up yet), and writes the
sentinel. install.sh already calls the CLI in the foreground (e.g.
`hal0 agent install hermes`), so the pattern exists. The later unconditional
OWUI/Hermes install blocks in install.sh are removed — extension install now
flows through `apply_setup`.

A `HAL0_SKIP_SETUP=1` escape hatch leaves the system unconfigured (sentinel
unset) so a user can run interactive `hal0 setup` themselves later.

## 8. Sentinel / gate

Unchanged mechanism, simplified consumers:
- Sentinel `/var/lib/hal0/.first_run_done` — written by `apply_setup` (and still
  by `POST /api/install/complete`).
- `GET /api/install/state` keeps reporting `first_run`, but the dashboard no
  longer auto-routes to a picker (that effect is deleted). The dashboard may
  show a passive "run `hal0 setup` to add models" banner if `first_run` is true
  and no slots exist — optional, Phase 7.
- The v1 `.bundle-chosen` marker and its read in `/state` are removed.

## 9. Testing strategy

- **`orchestrate.apply_setup`** (Phase 0): pytest with a fake slot_manager +
  monkeypatched `run_pull`; assert slot configs written, pull jobs seeded,
  extensions invoked, sentinel written, skip-with-reason on bad rows. This is
  the highest-value test surface — it replaces the e2e picker specs.
- **`suggest.py`** (Phase 1): table tests over synthetic `HardwareInfo`
  (low-RAM, Strix-Halo, no-GPU) asserting ranked picks + recommended flag + Pi
  bias.
- **`extensions.py`** (Phase 1): registry shape + each `install`/`wire` callable
  unit-tested with fakes.
- **TUI steps** (Phase 3): drive the step machine with scripted inputs (rich
  supports injected input / a stubbed Console); snapshot the rendered Layout
  text per step; assert gating (no agent → agent step skipped; no NPU → NPU step
  skipped).
- **install.sh** (Phase 5): extend the existing installer harness to assert
  `hal0 setup --auto` runs and the sentinel + expected slots appear.
- **Demolition** (Phase 6): grep-gate that no `firstrun`/`bundles` web symbols
  remain; UI build + remaining e2e green.

## 10. Phased `/loop` plan

Each phase is an independently-loopable unit with its own red→green test, in
dependency order. Phases 0 and 6 bookend; 1-5 are the per-step iterations.

```
Phase 0  Refactor /apply → install/orchestrate.apply_setup (pure, tested)   ← unblocks all
Phase 1  suggest.py (hw→ranked picks)  +  extensions.py registry  + tests
Phase 2  hal0 setup skeleton: api-reachability routing, --auto, 2-col Layout
         shell + setup_copy.py
Phase 3  Selection steps (welcome · storage · extensions · main · agent · npu ·
         review) with gating; snapshot-tested
Phase 4  Install step — Live progress over run_pull; context pane persists
Phase 5  install.sh integration (replace probe block + models-dir read)
Phase 6  Demolition: firstrun.jsx, v1 bundles surface, dead routes, e2e specs
Phase 7  Sentinel/passive-banner wiring + docs (README/PLAN/hal0-web CONTENT_BRIEF)
```

## 11. Risks / open points

- **rich input + Layout composition.** `Prompt.ask` doesn't compose with a live
  `Layout`; the redraw-per-step pattern (clear → print Layout → prompt below) is
  the agreed workaround. Multi-select checklist needs a small custom
  space-to-toggle helper (no extra dep).
- **API-up detection.** `hal0 setup` must reliably detect whether `hal0-api` is
  reachable to pick the branch. Use the existing `_shared` API base + a short
  health probe with a fast timeout; fall back to in-process on any failure.
- **Roster coherence (the reason for hybrid).** Writing slot TOML behind a
  running API desyncs its in-memory slot manager → restart-required drift (a
  recurring class of CT105 bug). The API-up branch must go through
  `/api/install/apply` so the live service registers slots itself.

## 12. Future: stacks (deferred, informs this design)

Stacks = named, runtime-switchable snapshots of a full slot/model/ctx/profile
layout (e.g. *Coding*, *Research/Round-Table*, *Image-Gen*). They supersede the
install-time bundle tier matrix. This design deliberately keeps the bundle
backend dormant rather than deleting it so stacks can reuse the manifest/schema
machinery. When stacks ship, `hal0 setup` gains a "start from a Stack" first
choice, and the dormant bundle surface retires for good.
