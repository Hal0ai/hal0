# Changelog

All notable changes to hal0 are recorded here. The format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the project adheres to semver pre-1.0 caveats: minor releases (v0.1 →
v0.2) may carry breaking changes; patch releases inside a minor line
(v0.2.0 → v0.2.1) won't.

Tags older than v0.2.0 ship release notes inside the GitHub release
page; this CHANGELOG starts at v0.2.0 (the Lemonade migration cut).
For ADR-level architecture context see `docs/internal/adr/`.

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
inside v0.3.x will hold the line at fixes-only.

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
  exiting non-zero. See [`docs/v0.2-upgrade.md`](docs/v0.2-upgrade.md)
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
