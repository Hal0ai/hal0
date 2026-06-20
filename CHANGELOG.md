# Changelog

All notable changes to hal0 are recorded here. The format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the project adheres to semver pre-1.0 caveats: minor releases (v0.1 →
v0.2) may carry breaking changes; patch releases inside a minor line
(v0.2.0 → v0.2.1) won't.

Tags older than v0.2.0 ship release notes inside the GitHub release
page; this CHANGELOG starts at v0.2.0 (the Lemonade migration cut).
For ADR-level architecture context see `docs/internal/adr/`.

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
