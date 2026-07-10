# Changelog

All notable changes to hal0 are recorded here. The format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the project adheres to semver pre-1.0 caveats: minor releases (v0.1 →
v0.2) may carry breaking changes; patch releases inside a minor line
(v0.2.0 → v0.2.1) won't.

Tags older than v0.2.0 ship release notes inside the GitHub release
page; this CHANGELOG starts at v0.2.0 (the Lemonade migration cut).
ADR-level architecture decisions are kept internal (the `docs/internal/`
tree is gitignored, #638) and referenced by number throughout the code.

**Release automation.** On a tagged release the matching `## [<version>]`
section below is bundled into the release tarball as `RELEASE_NOTES.md`, and its
optional `### Highlights`, `### Breaking`, and `### Migrations` subsections are
extracted into `release.json`. `hal0 update` shows both — rendering
breaking/migrations as callouts — from the cosign-verified tarball, before
applying. Add those subsections to a version's section to surface them; see
`scripts/gen_release_notes.py`.

## [Unreleased]

## [0.9.6] — 2026-07-10

### Highlights
- FLM NPU trio: the edit-slot drawer's Chat/ASR/Embed toggles now drive the running `flm serve` process, with a full-catalogue chat model picker that downloads on demand.
- FLM can run embed- or STT-primary (chat disabled) — a modality-aware readiness gate promotes the slot instead of wedging on a chat probe.
- Real FastFlowLM v0.9.44 toolbox image (`ghcr.io/hal0ai/hal0-toolbox-flm:0.9.44`), rebuilt from the actual v0.9.44 binary.
- NPU occupancy cards now glow purple when a slot is running (they used to read flat green regardless of state).
- hal0-bench is now in-tree: the `hal0.bench` engine, `/api/benchmarks`, and a Benchmarks dashboard page.

### Added
- NPU trio drawer wiring: ASR/Embed on-off toggles + a Chat model picker that lists the full FLM catalogue and pulls a not-yet-downloaded model on select (auto-applies on completion).
- `FLMProvider.verify_embed` — a one-shot `/v1/embeddings` readiness sentinel used when a slot serves embeddings without chat.
- hal0-bench in-tree port: `hal0.bench` engine, `/api/benchmarks` routes, and the Benchmarks dashboard tab (roster / runs / evals / run-queue).

### Changed
- FLM toolbox pinned to `0.9.44` across `manifest.json`, the flm seed profile, and the capabilities catalog (contains FastFlowLM binary v0.9.44).
- The warm→ready gate for FLM slots picks its sentinel by served modality: chat → `/v1/chat/completions`, chat-off+embed → `/v1/embeddings`, ASR-only → `/v1/models` liveness.
- `/api/slots/flm/models` returns the full FLM catalogue (installed + downloadable) with accurate `installed` flags, container-exec first with a host-probe fallback.

### Fixed
- Chat toggle now actually gates the container: `container_spec` no longer passes the positional chat tag when `[npu].chat=false` (it was cosmetic on the container path).
- Editing an NPU modality no longer clobbers `[model].default`/`context_size` — the drawer sends the model as a nested `[model]` table so the backend merge preserves sibling keys.
- `/api/npu/occupancy` no longer 500s while the FLM slot is offline (the degraded single-tenant fallback's `zip(..., strict=True)` mismatched `slots_out` vs `flm_slots`).
- NPU cards read a clear purple "running" glow for up/resident slots and dim for offline; the coresident STT/embed sub-cards reflect the anchor's `[npu]` toggles instead of the legacy shadow-slot `enabled` flag.

## [0.9.5.2] — 2026-07-09

### Fixed
- **Models → Downloads tab: React invariant 310 ("Rendered more hooks than during the previous render").** The Downloads pane in the Models view called `useStateM(false)` inside a `jobs.map((j) => { … })` callback. React counts hook calls per render, so the moment the downloads list changed length (a pull started, finished, failed, or was cleared) the hook count differed across renders and the entire `<ModelsView>` crashed behind the v3 error boundary — masking the rest of the dashboard. Fix: extract the per-row logic into a `DownloadRow` component that owns its own `cancelling` state, so the hook lives at row-component top level and its count is stable across renders. Pinned by `ui/src/dash/__tests__/react-hooks-order.test.mjs`, a new AST-based static check that walks every JSX/JS file under `src/dash/` and flags any useXxx (incl. the `useStateM`/`useEffectM`/etc. aliases used in this dashboard) called inside an array-iteration callback (`.map`/`.forEach`/`.filter`/`.reduce`/`.some`/`.every`/`.flatMap`/`.find`/`.findIndex`).

## [0.9.5.1] — 2026-07-09

### Highlights
- First green build on the 0.9.5 line — v0.9.5 shipped with a red CI (stale tests + lint); this hotfix greens the gate and closes v0.9.5's incomplete consolidations.

### Added
- **HF inspect recognizes FastFlowLM (NPU) repos.** `POST /api/models/inspect` now detects the FLM model shape (a `config.json` + tokenizer + `…nx` NPU-quant weight directory, e.g. `model.q4nx`) and surfaces one whole-repo variant flagged `flm` routed to the `flm pull` path — previously such repos inspected as "no variants" because the filter only admitted `.gguf`/`.mmproj`. Detection is shape-based (requires the `nx` weight blob) so a plain safetensors/GGUF repo is not misread as FLM.

### Fixed
- **Profile test suite realigned to the 2×2 seed grid.** v0.9.5 consolidated the retired `rocmfpx-rocm` / `vkfpx-*` slugs into the `{rocm,vulkan} × {dense,moe}` grid but left the tests asserting the old names, so `main` was red. Tests now assert the shipped grid.
- **Role-retirement cleanup finished.** v0.9.5 retired `SlotConfig.role` but left stale tests/docstrings referencing it (`test_slot_role`, `test_chat_normalization`, `test_llm_slot_views`, `hal0_llm_slot_views`). Removed/updated — slot identity is the name.
- **Curated catalogue guard for FLM/NPU entries.** FLM-served curated models (empty `hf_repo`) no longer surface in the `pullable` catalogue bucket (nothing to HF-pull), and a new `CuratedModel` validator requires every entry to be deployable — HF coords (`hf_repo` + `hf_file`) or an `npu` tag with `recommended_slot="flm"`.
- **FLM provider `load_chat` UnboundLocalError.** The legacy `defaults` branch no longer leaves `load_chat` unset; chat defaults on (`NpuConfig.chat=True`).
- **CI lint gate.** Cleared 12 `ruff check` errors + reformatted 7 files (import sorting, duplicate imports, placeholder-less f-strings, dead `result` binding, `SIM103`/`SIM110`/`RUF034`) that had blocked release CI since v0.9.5.

## [0.9.5] — 2026-07-08

### Added
- **NPU chat-first seed (FLM container shape).** `NpuConfig.chat` now
  defaults to `True` so a bare `[npu]` section in a slot TOML is a chat-ready
  NPU slot out of the box. Operators opt out by setting `chat = false` when
  they want an asr-only or embed-only NPU slot. A new seed
  `installer/etc-hal0/slots/npu.toml` is included so `hal0 setup` can
  register a clean (no FLM model pinned, `enabled = false`) `hal0/npu`
  tile on fresh boxes. The EditSlotDrawer now hides the Model field for
  `device = "npu"` slots (the NPU capability matrix replaces it).

### Changed
- **Slot routing key is now the slot `name`, not `role` (ADR-0023 §2.1).**
  The legacy `role` field on `SlotConfig` is gone — slot identity IS the
  routing key for `hal0/<slot>` aliases. Resolution chains in
  `src/hal0/normalize/resolver.py` now use `_slot_matches_name` (case-
  insensitive exact match on `name`), with one silicon-class escape
  hatch: the special name `npu` additionally matches any slot with
  `device == "npu"`, so a container that calls the trio's chat edge by
  a different slot name (`flm`, `npuchat`, …) still answers `hal0/npu`.
  Operator-custom seeds (`saber-fpx`, `deckard-fpx*`) and live slot
  TOMLs that used the old `role` tag now resolve by name. Live installs
  with the old `role` field in any slot TOML continue to parse (the
  field is parked under `extra` via Pydantic) and resolve to the slot
  by its `name`.

### Highlights
- **New canonical ROCmFPX runner: `ghcr.io/hal0ai/hal0-rocmfpx:vulkan-minicpm5`** —
  built from `Hal0ai/Hal0_ROCmFPX@5b395660` plus a 4-line upstream cherry-pick
  that wires the **minicpm5** pre-tokenizer (upstream PR #23384). Unblocks
  loading the BRAINTRAIN-1B GGUFs on Strix Halo (the `tokenizer.ggml.pre =
  minicpm5` field now resolves correctly). The new image serves both ROCm/HIP
  and Vulkan backends from a single artifact; this consolidates the
  previously-3-tag ROCmFPX image family into one.
- **Slot-level image override** — `slot.image` (top-level string in the slot
  TOML) now overrides the profile's `image`. Resolution order is
  `slot.image` → `profile.image` → `DEFAULT_ROCMFPX_IMAGE`. Future image
  bumps will be a code-only release (one constant in `schema.py`); operators
  no longer have to chase profile clones to keep their stack current. The
  EditSlotDrawer now exposes an **Image** form-row (free-form text input
  with a live `will use:` preview of the effective ref, plus a Reset
  button) so operators can pin/clear the override without editing TOMLs.

### Breaking
- **SEED_PROFILES reshuffled to a clean 2x2 grid.** The three old ROCmFPX
  runner slugs (`rocmfpx-rocm`, `vkfpx-moe`, `vkfpx-dense`) are replaced by
  four explicitly-named profiles that match the 2x2 (backend x {dense,moe})
  matrix. The names are self-describing — the backend is the prefix, the
  weight format is the suffix:

    | Old slug         | New slug         | Backend | Format   |
    |------------------|------------------|---------|----------|
    | `rocmfpx-rocm`   | `rocm-dense`     | ROCm0   | ROCmFP4  |
    | (new)            | `rocm-moe`       | ROCm0   | ROCmFPX  |
    | `vkfpx-dense`    | `vulkan-dense`   | Vulkan0 | ROCmFP4  |
    | `vkfpx-moe`      | `vulkan-moe`     | Vulkan0 | ROCmFPX  |

  All four reference `ghcr.io/hal0ai/hal0-rocmfpx:vulkan-minicpm5`
  (the new canonical runner). `rocm-moe` is a NEW profile — pre-0.9.5 the
  ROCmFPX MoEQuality format was Vulkan-only; this adds the ROCm0/HIP
  variant for operators who want a single-backend fleet.
- `profile.image` is now **deprecated** for new code paths. It still works as
  a fallback when no slot override is set, but the plan is to drop it from
  `SEED_PROFILES` entirely in **0.9.6**. Operator-custom profiles that
  override `image` (e.g. `saber-fpx`, `rocmfpx-rocm-custom`, `vkfpx-moe-custom`,
  `deckard-fpx*`) should migrate to per-slot `image` instead — a single
  slot TOML is a single grep hit, a profile clone is a maintenance liability.

### Migrations
- **Re-pin your custom profiles' images via the slot, not the profile.** Edit
  `/etc/hal0/slots/<name>.toml` and add a top-level `image = "ghcr.io/hal0ai/
  hal0-rocmfpx:vulkan-minicpm5"` line. The slot-level value wins on next
  `hal0 slot <name> restart`. Once your slots are migrated, you can remove
  the `image` line from your operator-custom profile in `profiles.toml`.
- **Rename operator-custom profile references in slot TOMLs.** If your slot
  points at one of the old slugs (`profile = "rocmfpx-rocm"`, `"vkfpx-moe"`,
  `"vkfpx-dense"`, or a `-custom` clone of those), update it to the matching
  new name (`"rocm-dense"`, `"vulkan-moe"`, `"vulkan-dense"`, or your
  custom variant of the new name). Example: `nano.toml`'s `profile` field
  should change from `"vkfpx-moe-custom"` → `"vulkan-moe-custom"` (and your
  `vulkan-moe-custom` profile in `profiles.toml` should mirror the new
  `vulkan-moe` seed's image + flags).
- **Verify the new image pulls cleanly on a non-production slot first**:
  `hal0 slot <name> image ghcr.io/hal0ai/hal0-rocmfpx:vulkan-minicpm5 && hal0
  slot <name> restart`. Then `curl :PORT/v1/models` to confirm the slot
  launched the new image (`system_fingerprint` carries the upstream commit
  hash for traceability).
- **The `image_mismatch` warning clears naturally** — it was a symptom of the
  old `profiles.toml` carrying stale image refs; once slots own their image,
  the mismatch can only happen on a manual `image` edit.

## [0.9.4.1] — 2026-07-08

### Changed
- **Idempotent `hal0 setup`** — removed the install-closed guard that blocked
  re-running `hal0 setup` after the first-run sentinel was written (#1161).
  The three provisioning endpoints (`/apply`, `/apply-selections`, `/complete`)
  are now idempotent and can be invoked at any time, fixing a Python traceback
  when `install.sh` re-launched the interactive setup after the `--auto` seed.

### Docs
- **README updated** — corrected version, slot list, setup flow description,
  and agent selection docs to match v0.9.4 reality.

## [0.9.4] — 2026-07-08

### Added
- **Downloads pane** in footer with pull job tracking (#1165).
- **Settings page reorganisation** with improved layout and navigation (#1163).
- **Configurable `direct_read_timeout`** via `[dispatcher]` TOML section (#1160).

### Fixed
- **NPU double-free**: stop FLM health poll from double-freeing the NPU slot (#1077).
- **Vision models**: add missing `mmproj_file` to curated vision model entries (#1162).
- **Memory tools UI**: make documents card full-width (#1166).
- **CI repair**: fix test failures post-settings-reorg (#1163).
- **Lint**: ruff formatting and `__all__` ordering across several files.

## [0.9.3] — 2026-07-06

**The Guided Setup.** hal0 setup grows a review-gated TUI, headless answer
files, hardware preflight, and a pick-free first-run workflow. The installer
no longer ships model recommendations — every box starts clean and the
operator chooses models interactively.

### Highlights
- **Guided Stage-2 setup TUI** with a review gate and two-stage handoff:
  hardware detection, model selection, capability scaffolding — all
  reviewed before writing config.
- **Headless answer files** — `hal0 setup --emit-answers` captures
  selections as JSON; `hal0 setup --answers <file>` replays them on
  another box. Combine with `--yes` for fully automated provisioning.
- **Pick-free install.** The installer no longer seeds model slots.
  Instead, the guided TUI (or headless answers) scaffolds capability
  slots and walks the operator through model selection interactively.
- **Hardware preflight.** GPU detection runs during install with an
  LXC smart-block and dev0 remedy; NPU functional state is persisted
  in a hardware.json written at install time.
- **hal0 doctor --verify** — a structured report card checking config,
  slots, models, and service health.

### Added
- **Guided Stage-2 setup TUI** with review gate + two-stage handoff (#1144).
- **Headless answer files** — `hal0 setup --answers` / `--emit-answers`
  round-trip (#1119, #1120).
- **`hal0 setup --plan` / `--dry-run` preview** (#1121).
- **GPU preflight during install** — LXC smart-block + dev0 remedy (#1135).
- **Authoritative hardware.json** persisted at install incl. NPU functional
  result (#1118).
- **Network coherence** — one `HAL0_BIND_HOST` + seeded origins (#1130).
- **HF_TOKEN gathered + persisted** to `secrets/` EnvironmentFile, threaded
  through in-process apply_setup (#1136, #1122).
- **`hal0 doctor --verify` report card** — config, slots, models, service
  health (#1145).
- **Post-update drift surfacing** + `hal0 update --restart-slots` (#1142).
- **Slot-render reconcile seam** shared by install + update (#1138).
- **Safe slot activation** — enable-on-pull-success + clamp context (#1137).
- **Clean seeded slots** — no model pins, derived device (#1140).
- **NPU opt-in** threaded through suggest+apply; NPU introduction gated on
  hardware present + healthy (#1134).
- **Capability + NPU slot scaffolding** — pick-free, guide models
  interactively (#1091).
- **ComfyUI gen branch** — scaffold-only default + per-variant download
  picker; repaired model fetch + shipped curated workflow JSONs (#1146,
  #1128).
- **First-run model workflow** — `hal0 models scan` / `add` / `store` /
  `run` + `hal0 doctor models`.
- **Pick-free install** — installer seeds zero model slots; the operator
  chooses via the guided TUI or headless answers (#1104, #1140).
- **Co-locate `[models].flm_store`** + free-space validation (#1132).
- **Honor `Selections.storage_dir`** — thread to `[models].store` (#1127).
- **Close `/api/install/*` provisioning** after first-run sentinel (#1126).
- **Apps skip/defer parity** — `openwebui` install verb + gateway in
  deferred hermes (#1125).
- **Converge `/apply` onto `/apply-selections`** + write first-run sentinel
  from endpoint (#1124).
- **Platform-gate hardening** — bootstrap-prereq parity, disk-on-store,
  early hal0 user (#1139).
- **Runtime `advertise_models` toggle** for upstream catalog entries (#1152).

### Fixed
- Dashboard URL no longer leaks `hal0.thinmint.dev` as default (#1092).
- Advertise all enabled LLM slots in `/v1/models` discovery, not just loaded
  ones (#1153).
- Support flat slot TOML shape in profile in-use scanning (#1129).
- Don't persist hardware.json on `--plan`/`--emit` preview paths (#1131).
- Delete stale `avahi/hal0.service` systemd unit (#1123).
- HF vision mmproj sidecars (`.mmproj`) + remove backend-switch surface (#1089).
- Seed `[models].store`, create FLM cache dir, add GPU/NPU preflight.
- Rescan models immediately when the store path changes.
- Honor `[models].flm_store` config + make NPU slot bind source
  reboot-durable.
- Auto-resolve a Hindsight-compatible Python instead of a raw pip wall.
- Don't write through the seeded uname symlink in prereq-parity tests (#1143).
- **HERMES.md.j2 rendering** — fix crash on empty env snapshots (Jinja2
  `Undefined.__getattr__` raises immediately, bypassing `|default` filters).

### Migrations
- First-run sentinel closes `/api/install/*` — a one-way gate at install
  time. The guided TUI or headless answer files are the canonical path
  for future configuration changes.

## [0.9.2] — 2026-07-05

Hotfix: restore the full model listing in every slot's model picker.

### Highlights
- Fix collapsed slot model dropdowns — `/api/models` rows again advertise the dispatcher-vocab `type` (`llm`/`embedding`/`reranking`) the pickers join on.

### Fixed
- **Slot model dropdowns (and the model→slot compatibility list) showed only the currently-assigned model.** `/api/models` stamped local- and upstream-registry rows' `type` with `classify()`'s coarse modality bucket (`chat` / `embed` / `rerank`) instead of the dispatcher vocabulary (`llm` / `embedding` / `reranking`) that the FLM path already emitted and that the UI joins on (`model.type === slot.type`), so every local model failed the picker filter and each dropdown collapsed to its default. Map the modality bucket → dispatcher type at both stamp sites; adds a local-row regression test (the FLM path was already covered, which is how this slipped through).

## [0.9.1] — 2026-07-05

ROCmFPX llama.cpp runner support, plus a safer notes-aware self-update.

### Highlights
- ROCmFPX runner support: GGUF quant-family detection, `rocmfpx-rocm` / `vkfpx-moe` seed profiles, and a build/quantize agent skill.
- `hal0 update` now shows cosign-verified release notes and asks before applying (staged prepare → commit).

### Added
- **Updater `prepare` / `commit` split.** `hal0 update` downloads + cosign-verifies + extracts a release and shows its notes — with breaking/migration callouts — *before* activating anything; `--yes` skips the prompt for headless/cron. Adds `POST /api/updates/prepare` + `/commit` (#1075).
- **Release notes in the update.** The release build bundles `RELEASE_NOTES.md` + `release.json` into the cosign-verified tarball; a CHANGELOG section's `### Highlights` / `### Breaking` / `### Migrations` subsections become the `hal0 update` callouts (#1078).
- **ROCmFPX quant detection** — the registry classifies the `ROCmFPX` / `ROCmFP{3,4,6,8}` quant family from a GGUF filename so FPX slots resolve their launch command (#1068).
- **ROCmFPX seed profiles** `rocmfpx-rocm` (ROCm0 dense) and `vkfpx-moe` (Vulkan0 MoE) for the custom ROCmFPX runner (#1069, renamed in #1076).
- `vkfpx-dense` seed profile — the Vulkan0 lane for DENSE ROCmFP4, for prefill-bound dense workloads (Vulkan wins prompt-processing); complements the decode-optimal ROCm0 `rocmfpx-rocm`.
- **`hal0-quantize` agent skill** — build the ROCmFPX toolchain and quantize a model to ROCmFP4/FPX (#1071).
- ROCmFPX bench tooling: server-ab (MTP / concurrency) aggregation in the benchmark SUMMARY, FPX sweep cells, run provenance (#1072).
- **Continuous batching — per-slot `parallel` field.** A slot can now set
  llama-server's `--parallel` / `-np` sequence-slot count so concurrent
  requests share the once-loaded weights instead of serializing through a
  single sequence and thrashing one prompt cache (the win the shared-slot
  architecture already earns but never harvested — every seed profile pins
  `--parallel 1`). `None` inherits the profile; a value >1 also emits
  `--kv-unified` so `--ctx-size` stays a SHARED pool (each request may use the
  full context) rather than being silently split to ctx/N per slot. Emitted as
  a slot override (beats the profile, loses to hand-authored `extra_args`);
  surfaced in the slot drawer with a shared-pool hint. The dead haloai
  `workers` field is deprecated (inert; a non-default value now logs at
  launch). MTP x batching runs but logs `mtp.batched_speculation` (unproven on
  gfx1151, bench-gated). Seed-profile defaults stay `--parallel 1` pending the
  on-box `-np` sweep (`server_ab.py --mode batch`). See the
  concurrency-batching plan handoff.

### Changed
- The plain `rocm` / `vulkan` seed profiles are reduced to basic flags
  (`-ngl 999 -fa on --jinja`); per-model KV/batch tuning now lives in the
  model's `defaults.extra_args` (#1076).
- Seed-profile `intent` labels normalised to terse structural tags
  (e.g. `ROCmFPX · DENSE · MTP`, `VULKFPX · MOE · MTP`, `ROCm`, `Embeddings`) —
  no served-model names, no filler.
- Slot units are re-rendered through the new code during an update's `commit`
  step, so a subsequent restart uses current argv (#1075).

### Removed
- Legacy MTP toolbox seed profiles `rocm-moe` and `rocm-dnse` (superseded by the
  ROCmFPX profiles); `rocmfpx-moe` renamed to `vkfpx-moe` to indicate its Vulkan
  lane (#1076).

### Migrations
- Slots pinned to a removed/renamed seed profile (`rocm-moe`, `rocm-dnse`, `rocmfpx-moe`) auto-fall-back to the backend's basic profile (`rocm` / `vulkan`) on launch — existing slots keep working with no operator action (#1076).

## [v0.9.0] — 2026-07-04

**The first public-beta cut.** hal0 graduates from the b-tagged 0.8.x
line: the dashboard gets its redesigned fixed-band layout and a live
telemetry header, seed profiles carry flags from a measured Strix Halo
bench matrix (plus a new per-model-family override layer), MCP servers
are manageable from the CLI, and the Memory view becomes a per-bank
workspace. **Safe upgrade from v0.8.5b2** — no on-disk migrations in
this cut. Profile flag changes (re-tune, `FAMILY_DEFAULTS`) land on each
slot's next restart; thanks to the v0.8.5b2 auto unit re-render, any
restart path picks them up.

### Added

- **Dashboard redesign — fixed-band layout with swap-in-place widgets
  (#1061).** The free-form drag/resize grid is replaced by a fixed
  vertical band stack: hero strip (steady-on + quick actions), 5-cell
  health strip, a full-width **Unified Memory hero** (slot allocations
  drawn inside the pool bar, striped system block, Proxmox-host block
  when configured), Throughput / Utilization / Requests band, locked
  dense slot rows, and an Activity / Services / Needs-Attention band
  with inline actions. Customization is swap-in-place per cell (layout
  v3 via the existing `PUT /api/user/dashboard-layout`, fail-soft to
  defaults on old payloads). Ships a new **Requests & Latency** widget
  against a new `/api/stats/requests` endpoint (gates to "source
  pending" until the dispatcher rollup ships).
- **Telemetry header on the Slots page (#1059, #1062, #1064).** One
  combined live-metrics card replaces the old hero band: throughput
  hero + 20-bucket spark, GPU semicircle gauge (sclk/temp/watts), CPU +
  memory gauge, and the NPU 4×8 occupancy grid with per-slot owner
  hues — above a full-width memory rack ruler in the #1061 memory-hero
  style (in-bar allocations, live tok/s on serving segments, click-through
  to the slot). Honest-data rules throughout: missing metric → em-dash,
  measured zero renders as 0.0 with the serving count, GPU util captioned
  "pinned" when forced high. Container queries keep the 4→3→2→1 column
  wrap gap-free.
- **`hal0 mcp` CLI surface** (#504) — `hal0 mcp {list,status,install,uninstall,restart,catalog}`
  backed by the existing `/api/mcp/*` routes. Rich tables with `--json` flag for
  machine output. The `restart` subcommand surfaces the 501 supervisor-stub
  gracefully until ADR-0015 lands.
- **`FAMILY_DEFAULTS` — per-model-family launcher-flag overrides.** A new
  resolution layer between a profile's generic flags and a slot's own
  `[model].defaults`, keyed on model family (matched from the id/filename).
  Applied automatically at slot resolution (launch + preview parity) and
  collapsed by `normalize_argv` last-wins, so a family override beats the
  profile but a per-slot `[server].extra_args` still beats the family. First
  tenant: **gemma → `-ctk f16 -ctv f16 --cache-reuse 0`** — any gemma model on
  any q8 profile is pinned back to f16 KV (gemma iSWA regresses on quantized KV:
  measured -28.5% pp on RADV / -10% tg on rocm, plus SWA+cache-reuse bugs
  #21468/#21749). This fixes the live gemma-on-`rocm-dnse` regression and makes
  adopting Vulkan q8 KV safe as a follow-up.
- **Configurable slot publish host — `[slots].publish_host` (#1058).**
  Slot containers published on 127.0.0.1 only; raw slot ports were
  reachable solely through hal0-api/Traefik. A first-class, UI-settable
  config key (default `127.0.0.1`, unchanged behavior) lets an operator
  widen to `0.0.0.0` or a specific interface IP. Fail-soft: an
  unresolvable value falls back to loopback, never opens the box. Baked
  into ExecStart, so live slots re-bind on their next restart; the
  Settings row carries a loud LAN-exposure warning.

### Changed

- **Memory Overview is a per-bank workspace (#1057).** Selecting a bank
  drives one combined primary card — retained-memories spark, graph
  extraction panel, embedded Tools (recall · reflect · documents ·
  mental models · directives), async operations, danger zone. The
  standalone `#memory/tools` route is retired; documents paginate and
  show composed titles instead of raw UUIDs; mental models and
  directives gain create forms.
- **The MTP control is always visible on llm slots (#1054).** Hiding the
  row for ineligible models made the tri-state undiscoverable. The slot
  drawer now always renders it, with a reason line for Auto·off ("model
  has no MTP heads" / "profile doesn't enable MTP") and a
  launch-will-fail warning when forcing On for a model without
  advertised heads. Stack editor rows follow the same contract.
- **Activity sidebar rework (#1063).** Taller pane, stacked
  timestamp/actor meta reclaiming horizontal space, and the free-text
  search replaced by a slot filter dropdown (exact target match,
  server-side pre-narrowed).
- **Seed profiles: bench-driven flag re-tune (Strix Halo matrix, 2026-07-04).**
  `rocm-moe` micro-batch `-ub 2048` → `-ub 1024` (+30% prompt-processing on
  Qwen3.6-35B-A3B-MTP: 1165 vs 895 t/s pp2048, consistent across all `-b`;
  token-gen flat ~47). `vulkan` `-ub 512` → `-ub 256` (+5.4% pp; the reported
  1024 sweet spot measured *worse*). Dropped `--threads-batch 32` and
  `--poll 100 --poll-batch 1` from the rocm chat profiles (measured within noise
  at full offload — simpler flags win ties). Added explicit `-ngl 999` to all
  GPU LLM profiles (GTT/unified free-mem autodetect is unreliable) and `--jinja`
  to all LLM profiles. Decode throughput is unchanged (all wins are prefill), so
  `PROFILE_BENCH` hero numbers stand. MTP draft depth measured `n-max 4` optimal
  (+23% decode vs n-max 2 on dense MTP) — seeded default kept.
- **Vulkan seed adopts symmetric q8 KV** (`-ctk q8_0 -ctv q8_0`): +45% pp at 32k
  depth on qwen (168 vs 116 t/s) and halves KV memory. It is the mirror image on
  gemma (gemma-4-12B @32k: q8 costs -28.5% pp on RADV), so the vulkan profile is
  no longer intrinsically gemma-safe — it relies on `FAMILY_DEFAULTS["gemma"]`
  pinning gemma slots back to f16 KV. (The upstream "~10x pp cliff" did NOT
  reproduce on this fork.)

### Fixed

- **OpenRouter OAuth callback route is gated behind
  `HAL0_OPENROUTER_OAUTH_ENABLED` (#775).** The callback endpoint no
  longer registers unless the flow is explicitly enabled, closing an
  unauthenticated surface on installs that never use OpenRouter OAuth.
- **Capability slot mini-cards regained Logs/Edit buttons (#1055).** The
  utility-tier (embedding/reranking/tts/transcription) cards rendered
  compact controls with no way to edit or tail a capability slot created
  via the UI.
- **Hermes memory identity defaults to `hermes` (#1056).** The upstream
  plugin base defaulted to `hermes-agent` on env-less code paths,
  spawning a stray duplicate `private:hermes-agent` bank alongside the
  correct `private:hermes`. Reads/writes now consistently target
  `private:hermes`.
- **Telemetry throughput no longer flaps to "source pending" on an idle
  box (#1062).** An empty 100-second history window is a measurement
  (0.0 with the live serving count), and the spark keeps the last 20
  measured buckets on screen; "source pending" is reserved for a
  genuinely missing source.
- Removed a dead duplicate `SELF_MANAGED_PROVIDERS` constant from
  `kokoro.py` (#982).

## [v0.8.5b2] — 2026-07-04

Hotfix over v0.8.5b1, closing the three findings from the first live
update+verification pass on Strix Halo hardware (CT105): stale `mtp = true`
overrides crashing slots on re-render, slot units not re-rendering on update,
and the gateway missing `POST /v1/rerank`. **Safe upgrade from v0.8.5b1** —
the one migration (crash-only MTP override defuse) touches exactly the slot
configs that could not have loaded anyway, with a loud per-slot log.

### Added
- **Slot units re-render automatically on update.** A slot's systemd unit
  bakes the launch argv at load time, so updating hal0 changed the code that
  WOULD render but not the file that DID — `systemctl restart`, crash
  restarts, and reboots kept running pre-update flags until an operator did a
  hal0-level slot restart (field finding). The updater (post venv-reinstall,
  via a fresh interpreter so the NEW code renders) and `install.sh` now
  rewrite every existing unit through current code plus one `daemon-reload` —
  running services are never bounced; fresh argv applies on each slot's next
  start from any path. Per-slot failures log and skip. The dashboard drift
  indicator still covers the "process running old argv until next restart"
  window.

### Fixed
- **Gateway now serves `POST /v1/rerank`.** The dispatcher's capability path
  map already resolved `/rerank` to the rerank slot, but the gateway only
  registered `/v1/rerankings` — so clients using llama-server's / Jina-style
  `/v1/rerank` got 405 and had to hit the slot port directly (field finding).
  `/v1/rerank` is now an alias of `/v1/rerankings` through the same dispatch
  path.
- **Crash-only `mtp = true` overrides are defused automatically.** A forced
  MTP override pointing at a model with no MTP heads crashes llama-server at
  load once the slot's unit re-renders under the v0.8.5b1 MTP separation
  (field-confirmed: pre-separation `mtp = true` debris on a headless MoE
  model). Two mechanisms now clear exactly that combination: an updater
  migration over the slot TOMLs (`updater.mtp_force_on_cleared` log; force-off,
  eligible force-on, and unresolvable models untouched) and a swap-path guard
  (swapping onto an ineligible model drops a forced `true` → AUTO, so the
  staleness can't regenerate). The false-negative escape hatch — forcing MTP
  on for an untagged-but-capable model — is preserved.

## [v0.8.5b1] — 2026-07-04

Everything landed on `main` since the v0.8.4b1 cut. The headlines: hal0
generalizes beyond the Strix Halo iGPU (experimental CUDA + multi-GPU
pinning), companion services get one management surface (registry +
`/api/services` + mDNS + dashboard page), the settings-completeness plan
finishes (phases 3–5 + an Advanced section with full `hal0.toml` parity),
and seed profiles go virtual with dedicated embed/rerank lanes and a
proper model×profile×slot MTP decision (#1045). **Safe upgrade from
v0.8.4b1** — the one on-disk migration (virtual-seed prune) backs up
`profiles.toml` first and rescues divergent operator content to `-custom`
names; note the MTP auto behaviour below if you run untagged local MTP
builds.

> **Upgrade note — re-render slot units after updating.** A container
> slot's systemd unit bakes the launch argv at load time, so after an
> update the running slots (and their unit files) still carry the
> PRE-update flags. A bare `systemctl restart hal0-slot@<name>` re-runs
> the stale ExecStart — restart slots **through hal0** (dashboard restart,
> or unload→load) so the unit re-renders through the new code. The
> dashboard's resolved-command drift indicator shows which slots are
> stale. Automatic unit re-rendering on update (without bouncing serving)
> is planned as the follow-up.
>
> **Upgrade note — stale `mtp = true` slot overrides crash on re-render.**
> An explicit `mtp = true` in a slot TOML is honored literally (it is the
> escape hatch for MTP-capable models the eligibility heuristics miss). If
> a stale override — typically left behind by the old binary MTP pill or a
> pre-#1045 stack apply, surviving a later model swap — points at a model
> with NO MTP layers, llama-server exits at load ("context type MTP
> requested but model doesn't contain MTP layers") once the unit
> re-renders. Fix: set the slot's MTP to Auto (`{"mtp": null}`) or Off in
> the drawer and restart. An updater migration that clears provably-stale
> force-ons (with a loud log) ships in the follow-up.

### Added
- **GPU generalization — experimental CUDA + multi-GPU.** A dedicated
  `cuda` seed profile (upstream `llama.cpp:server-cuda` image, preferred
  by the installer when NVIDIA CDI is present, Vulkan fallback otherwise)
  and per-slot `gpu_index` pinning for multi-GPU hosts. Ships alongside
  dead-path retirement and **multi-file pulls** (a model's mmproj/vision
  sidecars download with the main GGUF in one job).
- **Unified companion-service management (#1037).** A code-level service
  registry (Open WebUI, ComfyUI, Hermes, Hindsight, n8n), `GET
  /api/services` + allow-listed lifecycle actions, **mDNS advertisement**
  of addon services (`hal0-addon-<id>.service` files, avahi inotify
  pickup, `HAL0_HOSTNAME` precedence), and a dashboard **Services** page
  (cards, logs drawer, ComfyUI queue drawer, fail-soft probes).
- **Settings completeness, phases 3–5.** TTS request defaults
  (`default_voice` / `default_speed` / `default_response_format`) seeded
  into `/v1/audio/speech` plus a live voice list proxied from the `tts`
  slot (#1038); a Settings **NPU** section (#1040); ComfyUI
  `idle_restore_minutes` hot-reload (no API restart) and a workflow
  listing endpoint + dynamic strip (#1043).
- **Advanced settings section.** Full `hal0.toml` parity in the dashboard
  (every config key editable, grouped, with descriptions), a memory-graph
  panel, and an API restart button; an AWS secret-pair preset and a
  reload-config-from-disk button; previously-inert config keys wired
  through, and the memory schema aligned to the Hindsight era.
- **Dedicated `embed` and `rerank` seed profiles (#1045).** GPU llama-server
  templates that bake in the serving flags (`--embedding` / `--reranking`,
  `-ub 8192` so a full input fits one physical batch) so an embedding or
  reranking slot no longer hand-wires them in `extra_args`. On a `gpu-rocm`
  box, embed/rerank capabilities derive onto these lanes automatically
  (install path and picker/apply fit path both updated); Vulkan/CPU boxes
  keep falling back to the `vulkan` / `cpu-llm` profile until
  backend-specific variants ship.
- **Profile bench matrix tooling (#1045).** `installer/bench/profile-matrix.sh`
  scripts the seed-profile re-tune matrix as `hal0-benchctl` seam sweeps, and
  `installer/bench/server_ab.py` measures the server-level levers llama-bench
  can't see — MTP draft depth (with acceptance %), `--cache-reuse` on a
  shared-prefix trace, poll, and embed/rerank endpoint sanity — via hal0-api
  as the unprivileged user, always restoring the slot's original config.
  Supersedes the ad-hoc `/root/bench_mtp.py`; an on-box runbook ships at
  `handoffs/bench-profile-matrix-local-session-2026-07-04.md`.
- **Catalog UX finish (#1042).** Sort / tag-filter / quant chip wiring in
  the Models view, and a chat-template pick at pull time.
- **Canonical device/backend taxonomy.** One enum source at `GET
  /api/meta/enums` (ETag/cache-friendly), consumed by the dashboard —
  plus stacks fixes and dialog guards that rode the same change.

### Changed
- **Seed profiles are now virtual (#1045).** The built-in profile catalog
  (`SEED_PROFILES`) is overlaid from code on every load and never persisted to
  `/etc/hal0/profiles.toml`. Previously the installer materialised every seed
  inline and the loader only injected *missing* seeds, so a re-tuned seed (new
  flags, a bumped toolbox image) never reached an existing install. Now the code
  definition always wins: `load_profiles_config` overlays seeds over any on-disk
  copy, `save_profiles_config` strips seeds before writing, and the updater's
  `ensure_seed_profiles()` prunes any materialised seeds left by an older
  install (self-heal on upgrade). Seed profiles remain immutable — clone to
  customise. Operator (non-seed) profiles are untouched. **Data-safe
  migration**: the pre-prune file is backed up once
  (`profiles.toml.pre-virtual-seeds.bak`) and any seed-named entry whose
  content differs from the code seed (a hand-edited seed table, or an operator
  profile whose name only became a seed in this release, e.g. `embed`) is
  rescued to `<name>-custom` instead of deleted, with a loud log.
- **MTP is now a model × profile × slot decision (#1045).** Model
  eligibility (`mtp` registry tag or name marker) × profile opt-in
  (`profile.mtp` now means "enable for eligible models", not "append the
  bundle regardless") × a tri-state per-slot override (Auto/On/Off, Auto
  = profile opts in AND model eligible). A non-MTP model on an MTP
  profile no longer launches with dead `--spec-draft-*` flags, and the
  draft device tracks the profile backend (ROCm/Vulkan/CUDA) instead of
  hardcoded ROCm. The slot drawer swaps the binary MTP pill for the
  tri-state control with a live "Auto · active/inactive" hint; stack
  editor rows default to Auto, and an Auto row now clears a forced
  override on apply (the config write layer treats an explicit `null` as
  delete-key — TOML has no null — which is also what makes "back to
  Auto" work from the dashboard instead of 500ing). **Behaviour note:**
  an MTP-capable model that carries neither the registry `mtp` tag nor an
  MTP name marker stops speculating under Auto after this upgrade — tag
  the model or force the slot On; the launch log says
  `mtp.auto_off_model_ineligible` when this bites.

### Fixed
- **MTP auto-off breadcrumb is launch-gated.** The
  `mtp.auto_off_model_ineligible` hint lives inside the shared launch/preview
  scalar resolver, so it fired on every dashboard `GET /api/slots` poll
  (~0.4/s per client, forever) for any AUTO slot pairing an MTP profile with a
  non-MTP model. It now logs only on a real container launch; preview/status
  renders stay silent, and launch/preview argv parity is unchanged.
- **Upstream-advertised models are clearly identified as remote (#1035)**,
  not local, across the dashboard model surfaces.
- **Operator Board (#1032).** Hermes-contract repairs, honest UI state,
  and platform-assistant chat.
- **Slot pipeline hardening.** API boundary validation, backend-switch
  completion, manager guards, and guarded stack writes; a single argv
  assembler with model-defaults wiring and provider fixes; normalizer
  bug, dead-code removal, and a11y quick wins in the slot drawers.
- **Settings polish.** Truthful apply plan, safe engine picker, secret
  descriptions, rollback behaviour, and palette ghosts.

### Docs
- README re-baselined to v0.8.4b1 + full accuracy pass (#1044, #1046):
  canonical `agent`/`utility` seeded slots, real backend-profile and
  hardware-tier tables (experimental CUDA row), removed the
  no-longer-shipped `HAL0_USER` unprivileged mode, added the **Discord**
  invite (header + Contributing).
- hal0.dev docs mirror refreshed (#1044): new `operate/services` page,
  `operate/auth` rewritten to the real ADR-0012 reverse-proxy model (the
  fictional `--auth=basic` / managed-Caddy docs from 3e056de removed
  site-wide), plus the full v0.8.x feature-doc sweep.
- Handoffs: platform reliability/config/UI review (#1010), ONNX / Strix
  Halo NPU research and integration plan (#1034), llama.cpp seed-profile
  evaluation + consolidation proposal (#1041).

## [v0.8.4b1] — 2026-07-04

A **models, logs & memory** follow-up to v0.8.3b1. Models can now carry a
preferred runtime profile that loads with them, image-gen/ComfyUI models get
their own properly-tagged surface, the slot context window finally persists
across reloads, the logs/events system is unified, and the memory subsystem
gains a destructive-op audit trail plus console shape guards. **Safe upgrade
from v0.8.3b1 — no breaking changes; all additions are additive.**

### Added
- **Model preferred profile.** A registry model can declare `defaults.profile`
  — the runtime profile it wants loaded with it. A slot adopts it on create
  (when it has no explicit profile) and on **every model swap**, gated on
  device/type compatibility (an incompatible preference is ignored and the slot
  keeps its device-default profile; slot hardware is never flipped to satisfy a
  model). Surfaced as a **Preferred profile** selector in the model recipe
  editor.
- **ComfyUI / image-gen model surface.** The Models view gains a **Models |
  Image/ComfyUI** segmented toggle; image-gen models are grouped by their
  models-tree category (checkpoints/loras/vae/upscale_models/…) and kept out of
  the dispatcher list. ComfyUI models are correctly tagged `image`/`comfyui` at
  every registration path, and `/api/models` self-heals rows an older pull
  mis-tagged by deriving the ComfyUI category from the on-disk path (no
  migration needed).
- **Memory: audit trail for destructive ops (#1024).** Every destructive
  `/api/memory/*` op — bank delete, and memories/config/document/directive/
  operation/mental-model deletes, plus the namespace `POST /api/memory/delete`
  — now records a durable audit row (actor + target + truthful outcome) via the
  shared `record_action` facility, so a memory wipe is attributable after the
  fact. Complements the bank-DELETE `?confirm=` guard shipped in #1028.
- **Memory: response-shape guard on the cognition consoles (#1026).** The
  recall/reflect/directives passthroughs now validate the load-bearing envelope
  key (`results`/`text`/`items`); upstream Hindsight shape drift surfaces as a
  loud `memory.engine_shape` 502 instead of a silently-blank console panel. The
  two colliding `recall` contracts (namespace `{items}` vs bank `{results}`) are
  now documented at both call sites.

### Changed
- **Unified logs/events.** Restores per-slot model-load logs, real source/slot
  attribution, and a channel selector across the logs/events surface.
- **Memory Overview UI.** The graph-extraction gate now sits beside a shrunk
  "memories retained" spark in the top row; card headings share one unified
  "eyebrow" style; dropped the stray `ADR-0023` label from the extraction title.

### Fixed
- **Persistent slot context.** The slot edit drawer seeds the context field
  from the persisted `[model].context_size` (not the live runtime metric or a
  hardcoded 16384) and only writes `ctx_size` when it actually changed, so an
  unrelated save no longer clobbers the stored context window with 16k on a cold
  reload or swap.
- **Don't surface invisible models.** FLM tags advertised by the composite
  upstream before their weights are pulled are dropped from the catalog (the
  dedicated probe still surfaces the genuinely installed ones); freshly-pulled
  ComfyUI checkpoints are no longer mis-filed as chat models.
- **Memory tab functional; bank delete guarded (#1028).** The Memory tab renders
  its graph status/slot UI and consolidate/list actions correctly, and a bank
  `DELETE` now requires an explicit `?confirm=` guard.
- **Stack edit drawer (#1023).** Slot cards render as labeled multi-line
  entries and the escaped toggle-knob glyph is fixed.
- **Board: Hermes kanban task-detail drawer (#1014).** The task-detail envelope
  from Hermes is unwrapped so the board drawer renders instead of showing empty.
- **Installer: cosign optional for the one-line install.** The bootstrap no
  longer hard-requires `cosign`, so the one-line installer runs on hosts without
  it (signature verification still applies where `cosign` is present).

## [v0.8.3b1] — 2026-07-04

A large **reliability and UI-completeness** release. The headline is a
72-finding platform-review remediation delivered as eight verified waves
(each finding regression-tested and gated by CI + Playwright), landing
alongside earlier staged fixes. It retires several silent-failure bugs,
adds cross-process safety and pull resumability, makes every
backend-supported value editable in the slot/model/profile drawers, and
surfaces live telemetry on the dashboard. **Safe upgrade from v0.8.2b4 —
no breaking changes; all config/UI additions are additive.**

The most user-visible behaviour changes: the dashboard now shows Power &
Thermal (live GPU clock/temp/power) and Per-Slot Throughput cards **by
default**; disabling a capability now genuinely stops it serving; and the
`bge` reranker is now classified and routed as a reranker.

### Added
- **Dashboard live telemetry, on by default.** The Power & Thermal card
  (GPU clock MHz, temp, power) and the Per-Slot Throughput card are now
  default-on, and the Utilization card shows a live clock/temp caption.
  (#1019)
- **Edit-drawer completeness.** The model editor now exposes
  `capabilities`, `backends`, `rope_freq_base`, `mmproj`, and
  `hf_repo`/`hf_filename`; slots gain a per-slot `vision` toggle and NPU
  `asr`/`embed` modality toggles — all fields the API already accepted but
  no drawer surfaced. (#1020)
- **Settings.** An opt-in anonymous telemetry toggle and the image-gen
  defaults (`default_size`, `default_steps`, `idle_restore_minutes`). (#1021)
- **Interrupted pulls resume** via HTTP `Range` (with `If-Range`) instead
  of re-downloading from zero; the on-disk prefix is re-hashed so the final
  SHA-256 stays exact. (#1017)
- **Disk-space preflight** before multi-GB pulls fails fast with a
  structured `model.insufficient_disk` error instead of filling the disk.
  (#1013)
- **Host-memory-pressure LRU eviction** of idle slots. (#1003)

### Changed
- **Retired duplicated logic** that had silently drifted: one
  device→profile derivation, one filename→capability classifier, one
  dispatchable-state predicate, and one slot-projection reconcile. (#1015)
- **Rebuilt the dash editing drawers** on a shared `FormDrawer` + `useForm`
  with an unsaved-changes dirty guard, one `compatibleModels` filter, a
  focus trap and real `<label>` wiring (a11y), plus honest, confirmed
  destructive actions (styled type-to-confirm deletes; no more "Pause"
  that silently cancels; real "fits in memory" check). (#1016, #1018)
- **Cross-process safety:** advisory file locks around registry and
  capabilities writes; parent-directory `fsync` after atomic writes. (#1017)
- **Housekeeping:** startup GC of stale pull-job snapshots and orphaned
  `.part` partials. (#1017)
- Extracted the capability-resolution heuristics out of `dispatcher/router.py`.
  (#1017)

### Fixed
- **Disabling a capability now sticks.** The disable is written through to
  the slot config, so a later request can no longer wake a "disabled" slot
  and serve from it. (#1011)
- **The NPU trio is advertised on podman-only hosts** — the picker probed
  `docker` and never offered it on the reference platform. (#1011)
- **GPU-less installs get a chat-capable primary slot** instead of one
  bound to the Kokoro TTS engine (`cpu` now defaults to `cpu-llm`). (#1011)
- **Idle-evicted embed/rerank/tts slots wake on request** instead of
  404'ing until a manual load. (#1011)
- **The `bge` reranker is classified as a reranker** (was mislabeled
  `chat`) and is routable as one. (#1015)
- **Installer/bundle pulls survive an api restart** — status/stream polls
  no longer 404 mid-install. (#1012)
- **A completed pull is no longer reported "failed"** after a restart. (#1012)
- **The GpuArbiter drain no longer unloads a slot under an in-flight
  request** (image-mode switch race). (#1012)
- **Slots are no longer advertised READY on a health-probe timeout.** (#1012)
- **Write-time validation:** a second `default=true` slot of a type is
  refused at save; `create()` no longer clobbers an existing custom slot;
  stack apply flags unresolved profile/model refs and reports **degraded**
  (not "clean") when slots fail to load. (#1013, #1018)
- **The model editor no longer silently wipes unshown launcher defaults on
  Save**, and the ComfyUI image-profile control no longer corrupts
  `device_class` on edit. (#1020)
- **Chat requests with mis-positioned or stacked `role='system'` messages
  no longer 500 the Qwen3.6-35B-A3B upstream.** The OpenAI-compat
  normaliser now collapses every system entry into one and hoists it to
  position 0 (matching the OpenAI/Anthropic convention), so a stale
  mid-array system message from the SPA/Open WebUI/LibreChat — or two
  deliberately-stacked system blocks — no longer trips the Qwen3 Jinja
  template's `System message must be at the beginning`. No-system payloads
  skip the copy. (#992)
- Additional staged fixes from the assessment sweep: dead-port guard for
  container slots (#1001), `events.gap` on subscriber-queue overflow
  (#1000), non-retryable `SlotLoadFailed` for ERROR slots (#999), bounded
  httpx pool + tighter read timeout (#998), cold-slot 404 ordering (#996),
  container slots entering SERVING and bumping `last_used_at` (#995),
  installer port reachability when Docker is co-installed (#990), updater
  rollback re-pip (#994), an in-memory PgVector write warning (#1008),
  additive seed-profile merge (#1007), durable pull-job persistence (#1006),
  canonical slot fields on npu/load + install model-update (#1009), and the
  yellow-halo favicon restore (#991).

## [v0.8.2b4] — 2026-06-30

Documentation and installer hygiene — no runtime behaviour change. This
release re-baselines the engineering docs to the current v0.8.x reality
(container runtime, Hindsight memory, agent/utility roles) and fixes a
handful of installer drift bugs surfaced by a codebase assessment sweep.
Safe upgrade from v0.8.2b3.

### Changed
- **Docs re-baselined to v0.8.x.** Swept the Lemonade→container-runtime and
  Cognee→Hindsight terminology out of `AGENTS.md`, `ARCHITECTURE.md`,
  `CONTEXT.md`, `PLAN.md`, and `README.md`; corrected the Hermes provisioner
  to its real 15-phase pipeline; refreshed version/status lines; and rewrote the
  (largely fictional) `hal0-service-management` codebase-map reference against
  the current `src/hal0/` tree. Marked the shipped Stacks and voice-stack
  superpowers plans as completed.
- **Dead ADR links fixed.** Tracked docs no longer link into the gitignored
  `docs/internal/adr/` tree (#638); surviving decisions are inlined and ADRs are
  referenced by number. (In-code citation sweep tracked in #984.)

### Fixed
- **qwen3tts migration script aligned with the `tts`-slot model.** The
  standalone-to-slot migration script's Guard 1 checked a non-existent
  `qwen3tts` slot (always 404) and Guard 3 checked a removed kokoro `:8084`
  fallback; both are corrected to the deployed design where Qwen3-TTS serves
  from the canonical `tts` slot via `voice.tts`. (#979)
- **`uninstall.sh` now removes all three sudoers grants.** It only removed
  `hal0-benchctl`, leaking `hal0-agentenv` and `hal0-comfyui` behind on uninstall.
- **Hermes private memory bank seeded under its canonical name.** `install.sh`
  seeded `private__hermes-agent`, which the server (which derives the bank from the
  agent-id via `PRIVATE_PREFIX="private:"`) never matches — corrected to
  `private:hermes` so the pre-seeded retain-mission/dispositions actually apply.
- **Dropped the obsolete Lemonade boot-contention comment** from
  `installer/comfyui/scripts/comfy-up.sh`.

## [v0.8.2b3] — 2026-06-29

GPU text-to-speech lands: Qwen3-TTS now runs as a hal0-native slot with a
one-switch swap between the Kokoro (CPU) and Qwen3-TTS (GPU) engines, plus a
GPU benchmark harness and dashboard polish. Safe upgrade from v0.8.2b2.

### Added
- **GPU Qwen3-TTS as a hal0-native slot.** New `Qwen3TTSProvider` serves
  Qwen3-TTS from the `tts` slot, with a `voice.tts` capability switch that swaps
  the engine between Kokoro (CPU) and Qwen3-TTS (GPU) without reconfiguring the
  slot. (#972, #976) The toolbox image builds + pushes to ghcr.io and its digest
  is pinned in `manifest.json`. (#975, #977) Ships a standalone-to-slot migration
  runbook + guarded script. (#974)
- **GPU benchmark harness.** A GPU benchmarking toolbox with the `hal0-benchctl`
  seam and accompanying agent skills. (#967) Dashboard gains an iGPU usage gauge
  and a prefill TTFT readout. (#968) Topbar adds Kanban / Agent-Chat launchers, a
  global agent chat, and an Archived lane. (#966)

### Fixed
- **Dispatcher injects upstream auth headers for remote providers**, so requests
  routed to authenticated remote upstreams carry their credentials. (#973)
- **UI builds land reliably on deploy** (no-cache index + install to the served
  `dist`). (#969)

### Docs
- Benchmarking toolbox UI/feature handoff. (#971)

## [v0.8.2b2] — 2026-06-24

Two fixes on the 0.8.2 beta line — profile MTP tuning now actually takes
effect, and stacks can pull their referenced models. Safe upgrade from
v0.8.2b1.

### Fixed
- **Explicit profile spec flags win over the MTP bundle.** `resolve_profile_flags`
  appended `MTP_FLAG_BUNDLE` after a profile's own flags, so the bundle's
  spec-draft defaults (`--spec-draft-type-k q8_0`, `--spec-draft-p-min 0.0`)
  silently clobbered any `--spec-draft-*` a profile pinned — there was no way to
  tune the MTP draft through a profile. The bundle is now merged as defaults that
  the profile's explicit flags override (gap-filled only). (#963)
- **Absent stack models can be pulled.** Custom GGUF builds referenced by the
  seed stacks (saber, pi-agent, qwopus coders, halostrix, gemma, …) were
  auto-scanned with empty `hf_repo`/`hf_filename`, so on stack import/apply they
  classified "unresolvable" with no download URL. Registered their public HF
  coordinates (jcbtc/ + Jackrong/ + unsloth/ repos) in the curated catalogue;
  `embed_references` falls back to curated on export; and a new
  `backfill_coordless()` repairs existing coord-less registry rows on rescan. (#964)

### Changed
- **CI**: cancel superseded PR runs (never `main`); PRs test Python 3.12 only while
  `main` runs 3.12/3.13/3.14 (3.14 non-blocking); least-privilege workflow
  permissions; Node 20 → 22. (#898)

## [v0.8.2b1] — 2026-06-23

Profiles gain the same portable export/import/sharing model stacks already have.
Safe upgrade from v0.8.1-beta.2.

### Added
- **Portable profile export/import.** A profile can now be exported to a
  self-contained, checksummed `.hal0profile.json` envelope and imported on another
  host — the same file-based sharing model stacks use. The envelope carries the
  profile template plus a sha256 content checksum and an independent
  `schema_version`; no secrets or host paths are serialized. New routes
  `GET /api/profiles/{name}`, `POST /api/profiles/{name}/export`, and
  `POST /api/profiles/import` (dry-run reports checksum validity + name collision;
  commit creates under a chosen name, `409 profiles.exists` on a duplicate). New
  MCP tools mirror the `stack_*` set: `profile_list` / `profile_status` /
  `profile_export` (autonomous read) and `profile_import` / `profile_delete`
  (gated). The dashboard adds an Export button to every profile card and an Import
  dialog (file → dry-run preview → commit). (#962)

## [v0.8.1-beta.2] — 2026-06-23

Bugfix on the 0.8.1 beta line — restores fleet auto-update. Safe upgrade from
v0.8.1-beta.1.

### Fixed
- **Updater version comparison uses PEP 440.** `hal0 update` compared versions with
  a digit-tuple parser that split on `.` and stripped non-digits per segment, so the
  pip-normalised installed beta `0.8.0b3` parsed to `(0, 8, 3)` and the tag-form
  manifest `0.8.1-beta.1` to `(0, 8, 1, 1)` — `(0,8,1,1) > (0,8,3)` is false, so
  every box on a `0.8.0bN` beta saw the new release as "not newer" and `hal0 update`
  reported nothing to apply (the installed beta number was misread as the patch
  component). The comparator now uses `packaging.version.Version` in both the updater
  and the API route, falling back to the digit-tuple only for non-PEP-440 nightly
  tags (whose timestamp ordering still relies on it). (#957)

## [v0.8.1-beta.1] — 2026-06-23

Installer/privilege simplification + Hermes durable memory on by default. The
hal0-api privilege seams (hardened unprivileged mode, the slot privilege seam)
are gone, and a fresh `hal0 agent bootstrap hermes` now provisions a working
durable-memory provider out of the box.

### Added
- **Hermes durable memory enabled by default.** Provisioning now ships a working
  `hal0-memory` provider and sets `memory.provider=hal0-memory`, so Hermes gets
  cross-session recall with no manual config. Two banks — `private:hermes`
  (default) and `shared` (cross-agent) — backed by hal0's Hindsight engine via
  the hal0-api REST front door; reads union both banks. The provider exposes
  `hal0_memory_{search,recall,add}` (with `shared=true` to write the shared
  bank) and auto-injects recalled context each turn. (#955)

### Changed
- **Hermes agent identity is `hermes`** (was `hermes-agent`), matching the
  `hal0 agent` registry. The agent-id is the single source for the
  `X-hal0-Agent` MCP headers, the persona memory namespace, and the prelude. (#955)
- **Memory plugin install path fixed.** The plugin is copied to
  `$HERMES_HOME/plugins/hal0-memory/` (a direct child of `plugins/`, which the
  Hermes loader actually scans) instead of the nested `plugins/memory/…` that
  never loaded. (#955)

### Removed
- **Hardened (unprivileged hal0-api) mode removed**; hal0-api runs as root.
  Dropped live-hello; fixed ready-summary IPs. (#953)
- **Dormant slot privilege seam removed** (`hal0-slotctl` + euid routing). (#954)

### Breaking
- **Hermes memory namespace renamed `private:hermes-agent` → `private:hermes`.**
  Existing `private:hermes-agent` data is not auto-migrated; reprovisioned agents
  start recalling from `private:hermes` + `shared`. (#955)

## [v0.8.0-beta.3] — 2026-06-23

Canonical LLM roles + Hindsight-native memory extraction
([ADR-0023](docs/internal/adr/0023-canonical-llm-roles-agent-utility.md)). The two
canonical LLM roles are now **`agent`** (the capable default + fallback anchor,
replacing `chat`) and **`utility`** (the cheap helper, now seeded on every
install). `chat` and `primary` are retired as slot/role names.

### Changed
- **Canonical roles are `agent` + `utility`.** `agent` replaces `chat` as the
  default/anchor everywhere (seeded slots, dispatch rule-9 fallback, the default
  pin set, `_configured_primary`). `utility` joins `SEEDED_SLOTS` so a fresh box
  never silently falls back to a heavy model for cheap extraction.
- **Generalized virtual addressing.** Any enabled `type=llm` slot `X` is now
  addressable as `hal0/X` (chain `(X, agent)`); the advertised canonical virtuals
  are `hal0/agent`, `hal0/utility`, `hal0/npu`.
- **Memory graph extraction is operator-selectable and actually wired.**
  `[memory.graph].extraction_slot` names the local llm slot Hindsight uses for
  graph extraction; hal0 propagates it to `hindsight-api` via a systemd drop-in
  (`HINDSIGHT_API_LLM_MODEL=hal0/<slot>`) + restart. `hal0 memory graph enable`
  takes `--slot <name>` (validated against the live enabled-llm-slot set).
- **Cognee fully removed.** The Cognee engine + wrapper are deleted; Hindsight is
  the platform engine (with a PgVector boot-degrade fallback). `MemoryRecord`
  survives as an alias of `MemoryItem`.

### Breaking
- **`hal0/chat` is no longer advertised.** Clients pinned to `hal0/chat` (Hermes,
  OpenWebUI, any custom consumer) must repoint to **`hal0/agent`**.
  Hermes `model.default` is now `hal0/agent`.
- **`memory.graph.route` / `memory.graph.upstream` removed**, replaced by
  **`memory.graph.extraction_slot`** (default `"utility"`). Old `route`/`upstream`
  keys in `hal0.toml` are silently dropped on load (no hard-fail on upgrade). The
  `hal0 memory graph enable --route/--provider/--model` options are gone — use
  `--slot`.
- **`primary` is no longer a slot alias.** `SLOT_ALIASES` is `{"agent-hermes": "agent"}`.

## [v0.8.0-beta.2] — 2026-06-22

Bugfix release on the 0.8.0 beta line. No behaviour changes beyond the two
fixes below — a safe upgrade from v0.8.0-beta.1. First release to carry #948.

### Fixed
- **Operator Board live updates restored.** The board's events-WS proxy
  (`/api/board/events`) resolved its upstream Hermes session token from the
  `HERMES_SESSION_TOKEN` env var only, while the REST path harvests the
  rotating per-process token from the dashboard HTML. With no env pin (the
  default), the WS connected upstream with no token, Hermes rejected the
  upgrade (403), and the browser socket died with 1011 — so tasks created in
  Hermes loaded on refresh but never pushed live to the board. The WS bridge
  now shares the REST client's token resolution (env-pin → HTML-harvest →
  rotation cache) and re-harvests + retries once on connect failure. (#949)
- **Hermes privileged env seam.** A privileged env-write seam lets the
  unprivileged provisioner write `root:root` `.env` files, so Hermes agent
  config provisioning works under the dropped-root `hal0-api`. (#948)

## [v0.8.0-beta.1] — 2026-06-21

First beta of the 0.8.0 line — the model-config, Hermes, and permissions
overhaul. Configuration becomes a declarative single source of truth
(**Stacks** + single-source launch argv), Hermes consolidates onto its own
config ownership, and `hal0-api` can finally drop root. Voice (TTS + STT)
lands end-to-end. One behaviour change to be aware of: the `hal0/primary`
and `hal0/flm` virtual aliases are gone — see **Changed**.

### Added
- **Stacks — declarative config SSOT.** A `StackConfig` schema plus a
  `StackApplyEngine` that `plan()`s a Stack into a ChangeSet, `apply_config()`s
  it as an atomic commit with rollback, and `converge()`s the live slot set
  (primary-slot load/swap/skip + capability-child routing through the
  orchestrator). Content-hash drift detection and an active-stack pointer,
  export/import via a checksummed `.hal0stack.json` envelope, snapshot of live
  config into a Stack, and a `StacksCatalog` CRUD with seed guards. Seed stacks
  (saber / forge / pi) derived from the roster bench.
  (#921, #923, #925, #926)
- **Voice stack — TTS + STT.** Brought up and verified end-to-end: `voice_wire`
  fixed, Open WebUI Call mode wired, and the NPU-trio facade auto-provisions
  STT. (#924, #928)
- **Single-source slot argv (overhaul stream A).** A resolver dedups the launch
  flag soup down to a last-wins canonical command; per-flag provenance is
  exposed at `GET /api/slots/{name}/resolved`, and the slot Edit drawer renders
  the resolved command with per-flag source badges (base / profile /
  extra_args). (#929, #930, #931, #932)
- **Capability-based slot fallback.** When a slot's `model.default` isn't
  locally servable (registered-but-no-file, or pulled-away), `load()` falls back
  to the best locally-registered model matching the slot's capability —
  excluding diffusion / image / video models and preferring name-similarity to
  the configured id. (#940, #942)
- **Hardened permissions — run `hal0-api` unprivileged (opt-in).** Set
  `HAL0_USER=hal0` and the installer drops the API off root: a declarative
  ownership table (audited read-only by `hal0 doctor perms`), a narrow
  privileged seam (`hal0-slotctl` + a no-wildcard sudoers grant) so the
  unprivileged API can still write per-slot units and drive `hal0-slot@*`, and
  a codified flip (run-as drop-in + recursive chown of config + state, pruning
  `agents/` + `secrets/` + the models dir). Slot containers stay rootful — the
  container remains the sandbox boundary. Default `HAL0_USER=root` is
  byte-for-byte unchanged. (#929, #943, #944, #945)
- **Hermes owns its own config.** The runtime is unpinned with a real upgrade
  path, and a config-set overlay replaces the whole-file `config.yaml` render —
  Hermes owns and self-migrates its config while hal0 layers only its keys.
  (#934, #938)
- **Chat-template render-validation.** The template catalog is render-validated
  so a broken template can no longer ship silently. (#917)

### Changed
- **Breaking — `hal0/primary` and `hal0/flm` virtual aliases removed.** Virtual
  model names now map 1:1 to their resolution chains; `hal0/primary` no longer
  resolves (use `hal0/chat`) and `hal0/flm` is gone (use `hal0/npu`). Slot-name
  back-compat is intentionally kept, and the Hermes overlay now emits
  `model.default: hal0/chat`. (#939)
- **q8_0 KV cache, universally.** Main and MTP-draft KV caches are now `q8_0`
  across slots — near-lossless and keeps fused FlashAttention on AMD HIP. (#933)
- **Profiles** lift bench-tuned MTP config into `rocm-moe` / `rocm-dnse`. (#922)
- **Open WebUI** disables PersistentConfig so the env prewire wins the chat
  connection. (#927)
- Docs mirrored from hal0-web.

### Fixed
- **Installer** — `setup --storage-dir` is passed as a separate argv token so
  fresh `--models-dir` installs seed slots correctly (#946); the hardened-perms
  flip chowns config + state recursively so a root→hal0 upgrade doesn't strand
  root-owned state subdirs (#945); `hermes gateway install` runs
  non-interactively and skips `enable` when the unit is absent (#941); a
  lemonade-team PPA is added so the FLM/NPU `.deb` resolves on a fresh Ubuntu
  box (#937); the registry scans the effective store / pull_root rather than
  only declared roots (#935).
- **Hermes gateway** marks its `EnvironmentFile` optional (`-`) so fresh
  installs don't crash-loop on a missing secrets vault. (#936)
- **Dependencies** — bump vulnerable deps flagged by Dependabot. (#919)

## [v0.7.3-beta.2] — 2026-06-19

Second beta in the 0.7.3 line. Vision lands on the chat slot, idle slots
finally give their RAM back, and the Operator Board stops crashing on
task creation.

### Added
- **Chat-slot vision** — mmproj sidecars are now associated with their parent
  model in the registry, the container provider emits `--mmproj` from that
  sidecar, and vision auto-surfaces as a capability with a per-slot toggle.
  (#899, #900, #901)
- **TTL-driven hard eviction** — idle slots are now unloaded after their timeout,
  freeing resident RAM instead of merely relabelling READY→IDLE. (#902)
- **Hermes memory authorship** — writes from Hermes are stamped with an
  `agent:hermes` author tag. (#912)

### Fixed
- **Operator Board** no longer black-screens (React #31) when adding a task;
  modal styling, drag-to-delete, and the agent-chat drawer are reworked, and
  board chat now runs on the agent slot instead of the (wedged) chat slot.
  (#905, #914, #916)
- **Slot MTP gate** hardened on the backend and NUL bytes stripped from
  model-modal inputs. (#918)
- **Memory** rejects anonymous private writes (stops `private__anonymous`
  misrouting). (#915)
- **Memory banks grid** fills its width again, unnested from the section
  title row. (#911)
- **ComfyUI** reads slot logs from journald and reworks its card layout. (#909)

### Changed
- Docs mirrored from hal0-web.

## [v0.7.3-beta.1] — 2026-06-19

First Beta. The dashboard becomes a full operations console — ComfyUI image
generation, an agent task board, NPU/FLM slots, and a unified profile-card
layout — on top of honest slot health and per-slot context derivation.

### Added
- **ComfyUI generation engine** — full platform integration (model store,
  capability picker, installer wiring, V2 Image-Gen pane). The Image-Gen tab
  collapses its queue/workflows and an Inference-tab dot tracks live state; image
  generation flips the GPU into exclusive image mode via the iGPU switchover.
  (#878, #890, #881)
- **NPU occupancy** — a living occupancy grid with per-slot accents and
  activity-driven breathing, replacing the NpuFlmStack/trio picker. (#859, #861, #860)
- **Operator Board** — a hal0-skinned kanban wired to Hermes (`/api/board/*`) with
  a live agent-chat drawer and working task creation. (#852, #858)
- **Agents page** — an agent-card library with a live Hermes card. (#848)
- **Dashboard overhaul** — inference/NPU/ComfyUI cards unified to the profile-card
  style; Profiles given an engine-style section header and moved into the Slots
  tab; inference-pane living-grid redesign; sidebar nav accordion + bottom Services
  launch zone; a live-journal footer with runtime + service health groups; the
  memory+throughput band lifted above the tabs and slot cards freed from the
  accordion. (#888, #889, #879, #894, #867, #853)
- **Editable per-slot `extra_args`** with a Regenerate overlay. (#854)
- **Qwen3.6 MTP** chat template + slot rails.
- A **generated changelog** is now included in every release (nightly + stable). (#842)

### Changed
- **Slot health-probe honesty** — a slot is marked ready only once its real
  `/health` passes, not on a systemd snapshot. (#866)
- Slot context is derived per-slot and never silently inherits llama-server's
  4096; the edit-drawer default is 16k. (#862, #850)
- Disabled-but-running slots are surfaced; the enable toggle moved into the drawer. (#856)
- The runtime indicator split into a sidebar launcher + a footer health chip. (#864)
- Durable group-shared model ownership for an editable `/opt/hal0`. (#843, #857)
- Nightly versions carry a sub-day timestamp so same-day re-cuts stay monotonic. (#841)

### Fixed
- **Hardware:** report the live GTT total instead of a stale cached probe value. (#891)
- **NPU:** probe AIE columns via a temp file, not `-o /dev/stdout`. (#893)
- **Slots:** harden container config-drift comparisons and warn on drift. (#880, #869)
- **Routing:** translate FLM `<tag>-FLM` ids to served tags in the chat-slot rewrite. (#840)
- **Hermes:** run-as-`hal0` guard + ownership handover prevents root-clobber;
  corrected env arg order and dashboard TUI argv order. (#844, #847)
- **Dashboard:** dedup the journal SSE ring; chyron/timestamp polish; grid
  alignment; empty memory-bank graph no longer locks the dashboard; stray
  dev-test slots removed from the persona UI; responsive sizing + chrome cleanup.
  (#868, #871, #870, #845, #855, #846, #851)

### Docs
- Restored `doctor perms` + `migrate model-layout` to the CLI reference; added the
  deploy + PR workflow for parallel teammate sessions. (#849, #865)

## [v0.5.1-alpha.1] — 2026-06-15

Pre-Alpha. Retires the web FirstRun picker in favour of a terminal `hal0 setup`
TUI, and adds Ubuntu 26.04 / Python 3.14 install support.

### Added
- **`hal0 setup` TUI** — replaces the web FirstRun picker with a rich two-column
  terminal setup (storage → Extensions → Main model → Agent model → NPU) over an
  always-on context pane. Hybrid apply (in-process at install time, via the API
  when it's up — roster coherence), `--auto`/`--storage-dir`/`--no-pull`/
  `--no-extensions` flags, and a tier-less `POST /api/install/apply-selections`
  endpoint (#833).
- **Extensions** — selectable, auto-wired Apps (Open WebUI) + Agents (Hermes, Pi),
  a growing registry surfaced in `hal0 setup` (#833).
- **Ubuntu 26.04 / Python 3.14 install support** — per-distro FLM `.deb` selection,
  hindsight `--ignore-requires-python`, py-version-agnostic Hermes web_dist (#829).

### Changed
- A fresh install seeds the hardware-recommended Main slot **non-destructively**
  (only slots whose config is absent) and writes the first-run sentinel via
  `hal0 setup --auto --no-pull` — so `hal0 update`/re-install never overwrites a
  customised slot. The web bundle-tier picker is retired; the bundle backend is
  kept dormant for the future *Stacks* feature (#833).

### Removed
- Web FirstRun picker (`firstrun.jsx` + hooks), the v1 `/api/bundles` surface,
  `bundles/store.py`, and the legacy `/api/install/pick-default` route (#833).

## [v0.5.0-alpha.1] — 2026-06-14

Pre-Alpha. Zero-boot install + FirstRun v2: a fresh install now stands up the
memory engine, agents, and Hermes with no manual steps, and the FirstRun wizard
orchestrates a full multi-slot bring-up from a single bundle pick.

### Added
- **FirstRun v2** — quick-path wizard + orchestrated multi-slot install from a
  single bundle/kit pick (#809), with an Advanced drawer exposing per-slot
  model/profile overrides (#812).
- **Slot config UX** — Phase 2 per-slot MTP override + capability-gated MTP pill
  (#800); Phase 3 non-manual chat templates, model-level and per-slot (#802).
- **Zero-boot installer** — stands up a local Hindsight memory engine + seed
  banks (#806), ships the hal0 agent skills + drop-in dirs (#805), and
  provisions Hermes on a fresh install (#804).
- **NPU telemetry** — live column / duty / tok-s / KV surface, repointed to
  `hal0-toolbox-flm:0.9.43` (#813).
- **Settings** — HuggingFace token field + `api.env` hint for gated pulls
  (#816); standalone `/pull` uses capability-grouped paths (#815).
- **Dashboard overhaul** — the home page becomes a customizable operator
  widget board: drag/resize/pin-to-home slot cards, live memory-map,
  throughput, utilization and power monitors, a quick-chat tester, and a
  live ComfyUI job-queue widget; layout persists per operator (#814).
- **v0.5 navigation** — Connections dissolves into Slots/Agent tabs with
  sidebar sub-links; Memory + MCP unify under a tabbed Agent shell (#817).

### Fixed
- Non-blocking slot controls + NPU/image-gen toggles; cancel mid-load (#801).
- Slot edit drawer shows profile intent in its dropdown (#811).
- Enforce device↔profile backend coherence on slot create/update (#807).
- Drop the unimplemented `memory migrate --apply` flag (#820).

### Internal
- Recolor the device palette — free red for errors/stop (#803).
- CI tests against the latest supported Python (3.12) only (#808).
- gitignore `.superpowers/` brainstorm scratch (#810).

## [v0.4.1-alpha.1] — 2026-06-14

Pre-Alpha. First release carrying the clean-install hardening proven end-to-end
on fresh Ubuntu 24.04 containers:

- Bundled-agent install converges on the hal0-managed venv — `hal0 agent
  install hermes` provisions toolchain → venv → wrapper → unit in one
  foreground command, and the API path becomes a thin register-or-hint (#766).
- Installer auto-installs the python venv stdlib on clean Debian/Ubuntu instead
  of aborting at preflight (#778); NPU host-lib prereqs (ffmpeg6/XRT) are now
  best-effort, not fatal (#779).
- `/var/lib/hal0` permissions let the `hal0` agent refresh the shared STATE.md
  the session hook reads (#777).
- Slot config UX Phase 1: grouped drawer, reasoning pill, type-default pane,
  reactive model dropdown (#796).

## [v0.3.2-alpha.1] — 2026-05-29

End-of-stream cut for v0.3. Bundles MCP-completion, memory-map redesign,
the Settings → Updates fix, the silent-eviction dispatcher recovery,
ADR-0020 OpenRouter callback skeleton, the persona spending-cap primitive,
and the docs/internal pin + dashboard-v3 walkthrough.

After this tag, active scope rolls to v0.4 (install-mode reconciliation,
UI polish, fully-implemented Agents/UI/Install bootstrapped) and v0.5
(MCP admin + memory wiring across UI and agents).

### Added

- **Per-persona spending-cap primitive** (#411 — Phase 0 OpenRouter
  prereq). `[persona.budget]` TOML sub-table + pure-Python budget
  enforcement layer landing BEFORE the V1 OpenRouter upstream provider
  and V2 `hal0-fusion` MCP server. DA review of the OpenRouter
  integration plan flagged this as P0 must-fix #3 — without a
  spending-cap envelope, fusion (4.4× cost vs single-model) plus a
  recursing Hermes loop could drain a $200/credit pool overnight.
  - `src/hal0/agents/budget.py` — `Budget` dataclass, append-only
    `BudgetLedger`, pure `check_budget` / `record_charge`, daily /
    monthly / lifetime aggregation + per-call max.
  - REST surface under `/api/agents/{id}/personas/{pid}/budget` —
    `GET` (caps + spend + headroom), `PUT` (replace; round-trip
    preserves), `POST /check` (dry-run pre-call gate), `POST /charge`
    (post-response recorder).
  - Ledger at `/var/lib/hal0/agents/{agent_id}/personas/{persona_id}/spend.jsonl`
    — append-only JSON-lines, fsync per write, `tail -f | jq` friendly.
  - `PersonaBudgetPanel` dashboard editor under Personas tab.
  - Persona seed (hermes + coder) ships with empty budget block;
    operators opt in. `hal0 agent reprovision hermes` preserves
    operator-set budgets (idempotent seed, `overwrite=False`).
  - **Scope:** per-persona only in v0.3.2; per-agent and platform-wide
    scopes deferred to v0.4. No provider charges this primitive yet —
    V1 OpenRouter wires the pre-call gate and post-response record.
- **ADR-0020 + OpenRouter callback skeleton + loopback guard** (#409,
  Phase 0 OpenRouter prereq). Documents why the future OAuth PKCE
  callback URL is constrained to `127.0.0.1` so ADR-0012's LAN-trust
  posture survives the V1 OpenRouter integration. Ships a registered
  `GET /api/openrouter/auth/callback` route returning HTTP 501 with a
  per-route loopback guard so V1 inherits a baseline that respects
  the constraint from day 1. No live behaviour change.
- **Dashboard v3 `/agent` real-backend wiring** (#364, closes #207
  #228 #227 #226). `useAgents()` hook against `/api/agents`; live
  Memory tab against `/api/memory/graph/status`; live Skills tab
  against new `GET /api/agents/skills`; PersonaEditModal hydrated
  from new `GET /api/agents/persona-enums`. Server-side TONES + TOOLS
  + skill catalog moved to `src/hal0/agents/persona.py`.
- **Embedding model pinning + rerank wiring** (#365, closes #116).
  New `[memory.embedding]` config block — `model`,
  `rerank_enabled`, `rerank_url`, `rerank_over_fetch_factor`,
  `rerank_max_candidates`, split `rerank_connect_timeout_s` /
  `rerank_read_timeout_s`. Defaults preserve v0.3.0 semantics
  (rerank off, embedding model unchanged).
- **Private namespace contract for REST + read path** (#366 + #369,
  closes #317 #367). `X-hal0-Agent` + `X-hal0-Private` header
  contract on `/api/memory/{add,search,list,delete}` — shared
  ADR-0005 §3 resolver in `src/hal0/memory/namespace.py`. Wrapper
  `add` / `search` / `list_items` / `delete` accept per-call
  `client_id`; `_allowed_read_datasets` honors it so per-agent
  reads work end-to-end. Audit rows now stamp the resolved per-call
  identity instead of the singleton wrapper's anonymous default.
  Identity hardening: regex on agent id (path-traversal blocked),
  rejection of `private:*` agent values, rejection of body
  `dataset=private:*` when the private toggle is off.
- **Dashboard v3 `/mcp` install/uninstall/config + real audit
  stream** (#368, closes #305 #224 #222). New
  `src/hal0/mcp/installed.py` registry + `src/hal0/mcp/manifest.py`
  resolver (`oci` / `npm` / `uvx` / `git` / `http` specs). 501
  stubs for install / uninstall / config replaced with real
  impls; `/api/mcp/resolve` added; `/api/mcp/servers` merges
  bundled (live FastMCP introspection) + installed (registry).
  Real audit stream consumed by `useMcpServerLogs`. SSRF guard
  on URL fetch (loopback / RFC-1918 / link-local / 169.254.169.254
  / mDNS / CGNAT / unspecified all blocked; redirects disabled).
  Registry files at `/etc/hal0/mcp-servers/<id>.toml` written
  0o600 inside a 0o700 directory.

### Fixed

- **Settings → Updates: Install update silently no-op'd** (#386).
  The dashboard's Install button hit `POST /api/updates/apply`,
  received 202 with a `job_id`, toasted "Update started", and never
  polled the job — so when the background apply hit
  `UpdateExtractError` from a leftover `/usr/lib/hal0/hal0-<v>/`
  the user saw nothing. Three fixes:
  - UI: `useUpdateApply` signature corrected (`version?`, not
    misnamed `channel`); `useUpdateCheck` GETs `/api/updates/check`
    (was POSTing to a GET-only route → silent 405); new
    `useUpdateJob(jobId)` poller surfaces `running` / `applied` /
    `failed` to inline progress + toasts.
  - Backend: `Updater._extract_tarball` now quarantines a prior
    hal0 extraction at the same path to `<dest>.stale-<unix-ts>`
    instead of refusing, so a retry after a half-failed apply
    isn't permanently wedged. Foreign non-empty dirs are still
    refused — heuristic recognises hal0 installs by `VERSION`
    file or `pyproject.toml` `name="hal0"`.
  - Deduped the non-empty check in `Updater.apply()`; the extract
    step is the single source of truth.
- **Dispatcher silent-eviction recovery** (#392). When Lemonade
  silently evicts a model mid-stream the dispatcher now catches the
  upstream 502, refreshes slot state, and retries once before
  surfacing — turning a user-visible 502 into a transparent recovery.

### Tests

- **δ-harness coverage of Hermes `delegate_task` for 3 backends**
  (Phase 0 OpenRouter prereq — DA must-fix #2). New δ-tier
  pytest suite at `tests/harness/integration/test_delegate_task_*.py`
  proves the `delegate_task → execution-backend` dispatch hop works
  end-to-end for local + docker + modal with mocked
  `BaseEnvironment` subclasses (no Modal credits, no docker pulls in
  CI). The matrix test fans out one call across all three backends
  and asserts each was invoked exactly once with a per-backend-shaped
  payload. Findings catalogued at `tests/harness/FINDINGS.md` §46
  including the upstream audit (R7's "7 backends" claim corrected
  to 6 — local/docker/singularity/modal/daytona/ssh; Vercel Sandbox
  not present in upstream pin `0554ef1a`). Gates V3a Hermes
  observability per
  `openrouter-research-2026-05-28/PLANNING.md` §3 Phase 0.

### Docs

- **Internal docs pin + ADR-0017 + release-manifest refresh** (#389).
- **Operate + dashboard + installer sweep** (#390): Lemonade reference
  page, dashboard v3 walkthrough, installer auth section gutted to
  match the ADR-0012 post-Caddy reality.
- **PLAN §9 async-job polling contract** (#387). Codifies that any
  202+job_id endpoint requires UI polling of `GET /status/{id}` until
  terminal state — the underlying pattern behind the #386 fix.

### Deferred

- MCP-installed-server supervisor: start / stop / restart still
  return 501; installed servers report `state=stopped`. Dashboard
  buttons disabled with tooltip pending the supervisor design.
- AgentInbox / AgentOverview hero strip / Recent records pane /
  Skills "calls" column / per-store DB tile breakdown — adjacent
  hardcoded surfaces in dashboard v3 (filed as #374-#380).
- Manifest fetcher streaming + size guard, patch_config R-M-W lock,
  bundled-id shadow defense, dev-host worktree disk footprint
  (filed as #381-#384).
- **Install-mode reconciliation** (#406, HITL→AFK) and **hal0-test-template
  CT 200 + clone harness** (#407, AFK) — both filed against v0.4 scope.

## [v0.3.1-alpha.1] — 2026-05-27

Hermes-and-Cognee + dashboard v3 polish release. v0.3 stream work that
landed on `main` between 2026-05-23 and 2026-05-27 — 64 PRs — packaged
into the first patch tag after the v0.3.0-alpha.1 auth/Caddy cut.

### Added

- **Hermes-Agent bootstrap pipeline** (PRs #279, #284, #286, #289, #291,
  #292, #295, #296, #298, #316). 12-phase pipeline (`preflight`,
  `install`, `home_init`, `env_probe`, `config_write`, `mcp_wire`,
  `namespace_register`, `context_link`, `model_automap`, `voice_wire`,
  `smoke_tests`, `self_report`). Plugin model (`Hal0Profile`,
  `Hal0MemoryProvider`). `hal0 agent {status,log,upgrade}` CLI.
- **MCP host: per-agent client allow-list** (ADR-0013 — PRs #278, #293,
  #295, #300, #304). `mcp_client.py`, host-introspection probe tools
  for `hal0-admin`, per-agent MCP-clients view in the dashboard, full
  read-only introspection + audit-log SSE on the MCP page.
- **Memory graph extraction** (ADR-0014 — PRs #287, #290, #294, #297,
  #303). `[memory.graph]` schema + cognify gate on Cognee. New
  `/api/memory/{add,search,list,delete}` REST shims (closes #302).
  `hal0 memory graph {status,enable,disable}` CLI. Graph-extraction
  panel in dashboard Memory tab.
- **Agents > Peers tab** (PR #299) — identity cards from agents
  dataset.
- **Models surface** (PRs #313, #319, #343, #353) — scan +
  add-by-path + model-dir setting, single `[models].store` setting
  with firstrun + migration, default scan/preview recursive with UI
  toggle, model.type derived at the `useModels` hook.
- **Chat surface in dashboard** (PRs #309, #314, #315, #356, #357,
  #358) — real chat against the primary slot, slot indicator dots +
  warming pulse, collapsible reasoning above the answer, chat moves
  to its own `/chat` route, snapshot/memmap/throughput sidebar
  mirrored onto `/slots`.
- **Footer journal + update banner** (Epic #322 — PRs #321, #328,
  #329, #330, #332). `/api/journal` + `/api/journal/stream` merged
  log surface; Settings → Updates wired to the real backend.
- **Slot UX bundle** (PRs #281, #282, #283, #342, #344, #351) — POST
  normalizes Lemonade-shape model + auto-assigns port, `hal0 slot
  create --type` derives Lemonade device, max_loaded_models 4→8,
  swap-arrow affordance, zero-red-dots bundle, swap popover reads
  live `/api/models`.
- **One-line Proxmox VE LXC installer** (PR #341).

### Fixed

- **Slot backend update now invalidates state.json** (PR #360, issue
  #359). Previously `POST /api/slots/{name}/backend` rewrote the TOML
  but `extra.backend` in state.json stuck at the boot-time adoption
  value forever, so the snapshot lied even though inference itself ran
  on the new backend.
- **Dispatcher fall-through to Lemonade proxy** (PR #277) and **drift
  to OFFLINE not ERROR when lemond evicts a model** (PR #276).
- **Hermes uninstall** — registry coherence + state-dir cleanup
  (#352), venv + context_link teardown (#354), memory teardown failure
  surfacing (#355).
- **`/v1/health.last_use`** treated as an opaque counter (PR #307);
  removes spurious "idle since the unix epoch" rendering.
- **Live sidebars + memory map + throughput** (PRs #306, #308, #328)
  finally read the real backend instead of HAL0_DATA seed fixtures.

### Changed

- **Bundle name** rendered from manifest instead of placeholder text
  across install banners + progress (#214 / #331).
- **MCP page** moved from mock to real backend introspection (#304).
- **Settings → Updates** moved from mock to real backend (#321).
- **UpdateBanner** wired to live update state (#324 / #329).
- **HAL0_DATA fixtures** further retired — multiple dash surfaces now
  read `/api/models` (#345 / #351).

### Notes

This is a patch-level tag (`0.3.0 → 0.3.1`) by SemVer convention, but
the scope is closer to a minor release — Hermes, memory graph, and the
MCP host surface are all new user-facing systems. Future patch tags
## [v0.3.0-alpha.2] — 2026-05-28 (Hermes integration sweep)

End-to-end Hermes-Agent integration lands. The 12-PR master-plan
(`docs/internal/scratch/hermes-research-2026-05-28/MASTER-PLAN.md`)
ships as one mergeable surface: provisioner overhaul, persona TOML,
hal0-cognee memory plugin, hal0-agent@.service template, chat WS
proxy, plugin host, SidebarAgentBlock, v3 dashboard refactor, HermesChat
composer/transcript, the missing endpoints (`restart`, `skills`,
`memory/stats`), tests + docs sweep, and the upstream pin / weekly
drift CI job.

Decision record consolidated in
[ADR-0019](docs/internal/adr/0019-v0_3-hermes-integration.md);
upstream pin process in
[ADR-0018](docs/internal/adr/0018-upstream-hermes-pin-and-upgrade.md).

### New / improved

- **`hermes_provision` overhaul (#393, #396)** — 12-phase orchestrator
  (preflight → install → env_probe → home_init → config_write →
  mcp_wire → context_link → namespace_register → model_automap →
  voice_wire → smoke_tests → self_report). Idempotent + checkpointed.
  Composite `hal0` upstream + MCP registration + system-prompt
  addendum + persona seed all happen during bootstrap.
- **hal0-cognee MemoryProvider (#394)** — `src/hal0/agents/hermes/plugins/memory_cognee/`
  wraps `/api/memory/*` so memory is part of the prompt
  (`system_prompt_block`), not a tool the agent has to remember to
  call. Locks the #317 dataset-namespace contract.
- **`hal0-agent@.service` template (#395)** — sandboxed systemd
  instance template (`NoNewPrivileges`, `ProtectSystem=strict`,
  `ProtectHome=yes`, `Type=notify`, `WatchdogSec=60`). Soft-link to
  lemonade (`Wants=`, not `Requires=`/`BindsTo=`) so the agent
  survives a lemonade GPU-cleanup hang. CLI shim at
  `/usr/local/bin/hal0-agent`.
- **Persona TOML store + endpoints (#399)** — `GET/POST
  /api/agents/{id}/personas[/{pid}/activate]`. Hot-reload nudge over
  JSON-RPC swaps system-prompt scope on the next turn without restart.
  Seeded personas: `hermes`, `coder`.
- **Plugin host (#397)** — manifest proxy at
  `/api/dashboard/plugins`; per-plugin static-asset surface at
  `/dashboard-plugins/{name}/...`; shadow-DOM SDK shim. Lets the v3
  dashboard mount upstream Hermes plugin bundles (kanban today)
  inside an `<AgentView>` tab.
- **Chat WS proxy + session REST shim (#398)** — `/api/agents/{id}/{events,
  submit,session/*}`. Origin allowlist + HMAC session cookie on every
  WS upgrade; embed token in `Authorization: Bearer` (never the query
  string). `tool.progress` server-side coalesced at 100ms; ordering
  invariant (progress before complete) preserved.
- **SidebarAgentBlock (#400)** — service/persona/approvals/skills/
  memory chips + `[Open chat]` button. Parameterised by `agent_id` so
  v0.4 pi-coder lights up by adding a row.
- **Dashboard v3 agents refactor (#401)** — `<AgentView>` monolith
  split into Composer, Transcript, Sidecar; Inbox tab dropped; Peers
  tab folded into Memory.
- **HermesChat composer + transcript (#404)** — React composer
  (Enter submits, Shift+Enter newline); zustand transcript with
  WebSocket reconnect (250ms → 4s jittered backoff); inline tool-call
  cards.
- **ADR-0018 upstream Hermes pin + weekly hermes-sdk-diff CI (#403)**
  — `pyproject.toml [tool.hal0.upstream-hermes]` is the
  machine-readable pin; `.github/workflows/hermes-sdk-diff.yml` opens
  a drift issue weekly when any tracked file changes between pin and
  upstream HEAD.
- **PR-11 sweep — tests + docs + final missing endpoints**:
  - `POST /api/agents/{id}/restart` — systemctl restart wrapper for
    the SidebarAgentBlock service chip. Audit-logged via
    `hal0.agents.audit`. Subprocess-level timeout + spawn-failure
    envelopes.
  - `GET /api/agents/skills` — replaces the static catalog the
    SidebarAgentBlock used during build-out. Returns the v0.3
    catalog (`hermes-core` + `hal0-admin` + `hal0-memory`). Bumps
    ride ADR-0018 drift PRs.
  - `GET /api/agents/{id}/memory/stats` — per-agent counts the
    sidebar memory chip renders; pulls from the in-process Cognee
    wrapper. Graceful `available=false` fallback when memory isn't
    configured.
  - δ-harness `tests/harness/integration/` — full chat round-trip +
    persona activate round-trip against a `FakeWsServer` mock hermes
    (no GGUF download required).
  - `AGENTS.md`, `ARCHITECTURE.md`, `CONTEXT.md` glossary refresh
    (composer, transcript, plugin host, sidecar agent block, persona
    TOML, hal0-cognee, hermes-sdk-diff, HMAC session cookie,
    X-hal0-Agent, composite hal0 upstream).
  - ADR-0019 consolidates the master-plan decisions.

### Internal contracts

- `X-hal0-Agent` (NOT Bearer) is the identity claim on hal0-api per
  ADR-0012; the chat-proxy injects it on outbound hops, the browser
  never sees it.
- `/api/agents/{id}/*` is the v0.4-ready shape — every endpoint is
  parameterised by agent id; v0.3 only resolves `"hermes"`.
- Bundled agents follow single-pick (ADR-0004): installing one
  uninstalls any other.

### Known follow-up

- hal0-web `public/CONTENT_BRIEF.md` + `src/pages/agents.astro`
  update lands in a sibling PR on the `Hal0ai/hal0-web` repo.

## [v0.3.0-alpha.1] — 2026-05-23

**Caddy and the auth surface are removed.** PLAN.md v0.3 stream 4
("Admin / auth simplification") lands as a hard cut rather than the
softer "reduce/keep simplified password auth" originally planned in
ADR-0001. Architecture in [ADR-0012](docs/internal/adr/0012-remove-auth-and-caddy.md),
which supersedes [ADR-0001](docs/internal/adr/0001-collapse-edge-auth-into-fastapi.md).

### Breaking

- **Auth is gone.** A fresh install is open on `0.0.0.0:8080`. There is
  no password, no Bearer-token store, no `/api/auth/*` router, no
  first-run claim OTP, no session cookie. If hal0 is reachable from a
  hostile network, you must front it with an upstream reverse proxy
  that owns auth (Traefik / nginx / Cloudflare Tunnel; see
  `docs/operate/auth.mdx`).
- **Caddy is gone.** The installer no longer installs Caddy or renders
  a Caddyfile. The `hal0-caddy.service` unit is no longer shipped.
  `uninstall.sh` still tears down legacy `hal0-caddy.service` and
  `/var/lib/hal0/.first-run.lock` artifacts from older installs.
- **`--no-tls` install flag is gone** (now the only path).
- **`HAL0_AUTH_ENABLED` / `HAL0_AUTH_DISABLED` env vars are no-ops.**
  Both are unread by any hal0 process post-upgrade.
- Bearer tokens minted under v0.2.x stop working — there's no token
  store to validate them against. Programmatic clients that hit
  `/v1/*` no longer need (or are even able to use) an Authorization
  header.

### New / improved

- **v3 React dashboard on `main`** (#235), with the deferred
  slot-metrics normalizer (#249) and the slot type/group inference +
  hardware shape normalizer (#253) that took the sparse Lemonade
  payloads to a rendered state.
- **`/v1/*` reverse-proxy to Lemonade** (#248, closes #212). hal0-api
  catches every un-routed `/v1/{path:path}` and forwards to
  `127.0.0.1:13305`. Sidebar `lemond` status chip now updates from
  `/v1/health` instead of permanently reading "down."
- **Footer chips honor backend null** (#252, closes #221). `queued` /
  `coresident` render as `—` when Lemonade hasn't surfaced them.
- **Settings → default landing tab is now "Secrets"** (was "Auth"; the
  panel is gone).

### Removed code

- `src/hal0/api/auth/` (4 files, 712 lines) — first-run lockfile,
  password hash/verify, OTP rate-limiter
- `src/hal0/auth/` (3 files, 646 lines) — token store, password
  helpers, `auth_enabled()`
- `src/hal0/api/middleware/auth.py` (508 lines) — `require_token`,
  `require_writer`, `require_admin` deps + `AuthIdentity` resolver
- `src/hal0/api/routes/auth.py` (33 KB) — `/api/auth/{status,login,
  logout,password,me,tokens,tokens/{id}/rotate}`
- `ui/src/api/hooks/useAuth.ts` (58 lines) — token reveal/rotate hooks
- `ui/src/dash/settings.jsx::AuthSection` (~60 lines)
- `tests/api/test_auth_*` + `tests/auth/` — ~2,500 lines of test
  coverage for moot architecture
- `packaging/caddy/Caddyfile.template` + `packaging/systemd/hal0-caddy.service`
- ~135 lines of `install_caddy_tls()` + `--no-tls` handling in `install.sh`
- ~110 lines of first-run-lockfile + OTP minting + password-claim
  banner in `install.sh`

### Upgrade notes

- An existing v0.2.x install will lose its password + tokens on the next
  install. `uninstall.sh` cleans up the legacy Caddy unit + lockfile if
  you want a clean slate first.
- If you were relying on `--no-tls`, drop the flag — the installer no
  longer accepts it (and no longer needs it).

## [v0.2.0] — 2026-05-23

**The Lemonade Server adoption release.** AMD's Lemonade Server
replaces the six per-modality toolbox containers and the
`hal0-slot@.service` template as the unified inference runtime; one
`hal0-lemonade.service` supervises a single `lemond` daemon. Architecture
recorded in [ADR-0008](docs/internal/adr/0008-lemonade-adoption.md),
[ADR-0009](docs/internal/adr/0009-flm-trio-npu-packing.md),
[ADR-0010](docs/internal/adr/0010-bundle-picker-no-default-stack.md);
locked implementation contract at
[`docs/internal/lemonade-adoption-plan-2026-05-22.md`](docs/internal/lemonade-adoption-plan-2026-05-22.md).

### Breaking

- **v0.1.x → v0.2 is a clean break — no auto-migration.** `install.sh`
  detects v0.1.x state (presence of `/etc/hal0/slots/*.toml` AND
  absence of `/var/lib/hal0/lemonade/config.json`) and refuses to
  overwrite it, printing explicit backup + wipe instructions and
  exiting non-zero. See [https://hal0.dev/docs/v0.2-upgrade](https://hal0.dev/docs/v0.2-upgrade)
  for the user-facing procedure.
- **Per-modality toolbox containers retired.** `hal0-toolbox-vulkan` /
  `rocm` / `flm` / `moonshine` / `kokoro` / `comfyui` are no longer
  built or pulled. Their dispatch responsibilities consolidate into
  Lemonade's `llamacpp` / `flm:npu` / `whisper.cpp` / `kokoro:cpu` /
  `sd-cpp` recipes.
- **`hal0-slot@.service` systemd template retired.** Per-slot units
  no longer exist. `hal0-lemonade.service` is the new daemon
  supervisor — one process serving every slot via Lemonade's per-type
  LRU.
- **Model layout reorganised** to the canonical
  `/var/lib/hal0/models/<recipe>/<capability>/` tree. PR-7's
  migration script reorganises `/mnt/ai-models/{local,flm-ubuntu,moonshine_voice,voices,comfyui}`
  into the same shape with per-leaf symlinks back to the canonical
  path. Lemonade's `extra_models_dir` points at the canonical tree.
- **`/etc/hal0/slots/*.toml` removed** as a persistence surface;
  `capabilities.toml` is now the single source of truth for slot
  selections. The slot lifecycle state machine in
  `src/hal0/slots/state.py` survives; per-slot Provider classes and
  the slot-systemd-template do not.
- **Moonshine STT retired** in favour of `whisper.cpp` via Lemonade.
  More accurate but heavier on weak CPUs; lite-tier users may notice.
- **ComfyUI workflows lost.** `sd-cpp` covers the 90% case; power
  users are directed to external ComfyUI installations for advanced
  workflow graphs.
- **`HAL0_BACKEND=lemonade` env flag** introduced in PR-8 and removed
  in PR-10 — Lemonade is now the unconditional runtime.

### Features

- **Lemonade Server unified inference runtime** (PR-3 #156 through
  PR-22). One `lemond` process per host on `127.0.0.1:13305`, cache
  + config at `/var/lib/hal0/lemonade/`, supervised by
  `hal0-lemonade.service`.
- **`LemonadeProvider`** is the only `Provider` in v0.2's dispatch
  path. Capability dispatcher reads `/v1/health` for slot state and
  routes through Lemonade's `/v1/chat/completions` / `/v1/embeddings`
  / `/v1/rerank` / `/v1/audio/*` / `/v1/images/*` endpoints.
- **FLM trio NPU packing** (PR-19 #201, PR-20 #202). Lemonade's
  `flm.args = "--asr 1 --embed 1"` packs chat + transcription +
  embedding into one `flm serve` process sharing the single AMDXDNA
  hardware context. hal0 exposes three slots (`agent`, `stt-npu`,
  `embed-npu`); the capability dispatcher reads
  `/v1/health.loaded[].backend_url` for the FLM model and routes
  `stt-npu` / `embed-npu` requests directly to the child's port
  (Lemonade only knows about the chat role). NPU exclusivity (one
  `device = "npu", type = "llm"` slot enabled at a time) is enforced
  in `capabilities.toml` validation; chat-model swap surfaces a
  "swap incoming, voice + embed paused" UX. See ADR-0009.
- **OmniRouter client-side tool-calling** (PR-16 #189). 8 tools — 5
  upstream-mirrored (`generate_image`, `edit_image`,
  `text_to_speech`, `transcribe_audio`, `analyze_image`) + 3
  hal0-custom (`embed_text`, `rerank_documents`, `route_to_chat`).
  Dynamic per-request filtering: a tool is included in the LLM
  prompt only if at least one enabled slot of its target type exists
  AND (for label-gated tools) at least one of those slots has a
  model with the required labels. LLMs without the `tool-calling`
  label receive no tools. `route_to_chat` is one-shot delegation,
  blocked at depth=1, blocked across NPU LLM slots.
- **First-run bundle picker** (PR-17 #196, PR-18 #198).
  `capabilities.toml` ships empty by design; the dashboard's first
  load renders four hardware-anchored tiers (`hal0-Lite` ≥16 GB /
  `Default` ≥32 GB / `Pro` ≥64 GB / `Max` ≥100 GB Strix Halo) plus
  the AMD-curated `LMX-Omni-52B-Halo` kit, with a "Skip — configure
  manually" path. Tiers that don't fit detected unified RAM grey out
  with a tooltip. Bundle manifests live at
  `/var/lib/hal0/models/collections/omni/`. The NPU trio is opt-in
  even at Pro and Max tiers. See ADR-0010.
- **Settings → Lemonade admin panel** (PR-13 #183). Surfaces
  `/internal/config` snapshot + `/internal/set` atomic writes for a
  curated subset of keys. Guards against overriding `llamacpp.args`
  to an unbounded value (would cause the multi-LLM CPU
  oversubscription deadlock).
- **Journal panel folded into Logs tab** (PR-14 #184). Lemonade's
  `/logs/stream` WebSocket streams into the dashboard's event ring,
  alongside hal0's own structured journal.
- **Metrics shim** (PR-12 #179). Per-slot TTFT + tok/s +
  prompt_tokens scraped from `/v1/stats`. FLM-native KV%
  (`kv_token_occupancy_rate_percentage`) on NPU slots. See known
  limitations below for the GPU-slot KV% gap.
- **`[CPU]` chip + tooltip** on the voice slot card (PR-15 #186)
  disclosing that kokoro is CPU-only in v0.2. GPU TTS deferred to
  v0.3.
- **Dashboard reads `/v1/health` for slot state** (PR-11 #163);
  surfaces NPU exclusivity, FLM trio coresident marker, and the
  nuclear-evict banner via `/logs/stream` line parsing.
- **Mandatory `llamacpp.args = "--parallel 1 --threads N"`** in the
  `lemond` config baseline (PR-5 #159). N is computed at install
  time as `(cores − 2) / 4`, min 2. Without this, two concurrent
  child llama-servers oversubscribe the CPU and freeze the Vulkan
  dispatch — a hard install-time requirement, not a tunable.
- **Per-type LRU concurrency.** Six independent type budgets
  (`llm`, `embedding`, `reranking`, `transcription`, `tts`, `image`)
  reported by `/v1/health.max_models`; default global budget set to
  4. Nuclear evict-all only fires when a `/v1/load` errors AND the
  error message does NOT substring-match "not found" / "does not
  exist" / "No such file" — common failure modes (bad path, missing
  variant, mistyped name) return graceful errors and leave the
  loaded pool intact.
- **Slot model**: bare-name identity + `type` (Lemonade vocab:
  `llm | embedding | reranking | transcription | tts | image`) +
  `device` (`gpu-rocm | gpu-vulkan | cpu | npu`) + `model` + `enabled`
  + optional `default` + `group` for dashboard rollup. User-added
  slots via `hal0 slot add NAME --type TYPE --model MODEL`. Exactly
  one `default = true` per type enforced at save / load.
- **Canonical model namespace.** `registered` (no prefix, from
  `registry.toml` → Lemonade's `server_models.json`) vs `user.*`
  (on-demand pulls via `POST /v1/pull`). `extra.*` auto-discovery
  unused. Dashboard surfaces two badges: `blessed` and `pulled`.
- **`hal0 registry sync`** (PR-6 #141 → #151) — regenerates
  `/var/lib/hal0/lemonade/resources/server_models.json` from
  `registry.toml` and restarts `lemond`. Hourly drift detector
  surfaces a dashboard banner when `registry.toml` is newer than
  `server_models.json`.
- **`hal0 registry import`** (PR-21 #203) — single command, restores
  `registry.toml` from a v0.1.x backup tarball. Slot selections must
  be redone via the bundle picker.
- **`hal0 doctor` extended** to probe `lemond` reachability + FLM
  `.deb` presence (Linux NPU path).

### Internal

- **22 implementation PRs landed across 6 sub-phases.** Foundation
  (PR-2 #137, PR-3 #156), install + registry (PR-4 #157, PR-5 #159,
  PR-6 #141 → #151, PR-7 #158), slot layer rewrite (PR-8 #161, PR-9
  #160, PR-10 #162), UI + metrics (PR-11 #163, PR-12 #179, PR-13
  #183, PR-14 #184, PR-15 #186), OmniRouter + bundles (PR-16 #189,
  PR-17 #196, PR-18 #198), NPU + close-out (PR-19 #201, PR-20 #202,
  PR-21 #203, PR-22 — this PR).
- **`SlotManager` simplified** ~358 LOC in PR-10 (#162) — provider
  ABC dispatch + per-slot systemd adoption logic deleted.
- **Legacy provider classes preserved as code** (used by image-gen /
  hardware-probe / catalog non-slot consumers) but no longer in the
  Lemonade dispatch path.
- **`SlotConfig.device` refactor + `capabilities.toml`
  `schema_version=2` migration** (#143 → #153).
- **Preload validation + idle-unload driver** (#144 → #152) shipped
  ahead of ADR-0007 supersession; preload validation removed per
  ADR-0008 §3 in `e660fa3`.
- **`src/hal0/lemonade/`** — HTTP client + `catalog_sync.py` +
  `metrics_shim.py` + `log_proxy.py`.
- **`src/hal0/omni_router/`** — client + tool definitions
  (checksum-pinned mirror of Lemonade upstream's
  `toolDefinitions.json`; CI script `scripts/check-tool-definitions.sh`
  fails on drift).
- **NPU FLM trio dispatch carve-out** documented in
  ADR-0009 — narrow exception to ADR-0008's "Lemonade owns
  inference lifecycle" thesis; scoped to the two endpoint paths
  (`/v1/audio/transcriptions`, `/v1/embeddings`) that Lemonade
  doesn't know exist on the FLM child.
- **v0.2.1 dashboard rewrite** (slice #176, PR #199) cut over on
  `main` in parallel; PR #197 carries v2 polish work and remains
  open at v0.2 ship.

### Known limitations

- **KV% for GPU slots reads `—`.** Lemonade's bundled `llama-server`
  (b9253 Vulkan, b1274 ROCm) returns `null` for `n_past` /
  `n_prompt_tokens` / `prompt` in `/slots` responses, even during
  active inference. PR #124's KV%-from-`/slots` strategy did not
  survive the migration. FLM/NPU slots get KV% native from the
  `kv_token_occupancy_rate_percentage` field in
  `/v1/chat/completions` responses. v0.2.x patch path: hal0 builds
  its own llama-server and swaps via `lemonade config set
  llamacpp.{rocm_bin,vulkan_bin}` if upstream doesn't populate the
  fields within ~6 weeks. See ADR-0008 §Costs.
- **Kokoro TTS is CPU-only in v0.2.** No upstream GPU-Kokoro on
  Linux at v0.2 ship. UI surfaces a `[CPU]` chip + tooltip on the
  voice slot card. GPU-accelerated TTS deferred to v0.3.
- **Performance: parity-to-regression vs the v0.1 hal0-Vulkan
  baseline** (-13% to -18% on tested models in spike #1;
  hermes-14b at parity). Accepted in exchange for the
  six-toolbox-to-one-runtime maintenance collapse.
- **NPU LLM swap is slow (~14s).** Changing the `agent` slot's
  chat model tears down the FLM trio (stt + embed go with it) and
  restarts `flm serve <new-chat-model> --asr 1 --embed 1`. UI
  surfaces "swap incoming, voice + embed paused".
- **FLM .deb install is manual on Linux.** Lemonade's `flm:npu`
  auto-installer is Windows-only as of v0.2. Linux install
  procedure is PPA `lemonade-team/stable` + libxrt-npu2 + ffmpeg6
  + boost1.83 + fftw3 + FastFlowLM `.deb`. The hal0 installer
  handles this end-to-end; users running off-script need the
  `hal0_lemonade_flm_npu_install` recipe.
- **Ongoing pin maintenance** for two upstream artifacts (the
  Lemonade embeddable tarball + the FastFlowLM `.deb`). Each hal0
  release manually bumps both pins, sha256-verifies, and CI-smokes
  the install + a triple-concurrency probe before tagging.

[v0.2.0]: https://github.com/Hal0ai/hal0/releases/tag/v0.2.0
