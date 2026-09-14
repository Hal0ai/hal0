# Dashboard & self-explanation: ODS vs. hal0

Comparative read of how ODS (Osmantic Deployment System) makes itself legible to a
non-expert, and where hal0 — a homelab appliance for a more technical operator —
matches, exceeds, or falls short of that bar. All citations are `file:line` against
the two repos on disk (`/home/user/ods/ods/…`, `/home/user/hal0/…`).

## A. ODS UX inventory

**Nav / IA.** The sidebar (`extensions/services/dashboard/src/components/Sidebar.jsx`)
and route registry (`extensions/services/dashboard/src/plugins/core.js:25-121`) expose
only seven top-level items — Dashboard, GPU Monitor (hidden unless `gpu_count>1`,
`core.js:44`), Extensions, Integrations, Models, Remote GPU, Settings — with Usage and
"Setup / Owner" deliberately kept *off* the top nav (`core.js:87-90`: "a
factory/distributor/service-provider flow, not a day-to-day dashboard surface").
Agents (Hermes), Privacy, Workflows, Features and Updates are not separate pages —
they're sections/cards embedded in Dashboard and Settings, keeping the nav shallow
while the underlying surface area is large.

| Page | Question it answers | Actions offered | Status vocabulary | Empty/error handling |
|---|---|---|---|---|
| **Dashboard** (`pages/Dashboard.jsx`) | "Is my system healthy, and what can I turn on?" | Click a Feature card to launch it; jump to GPU Monitor | `healthy/degraded/not_deployed` rolled into `computeHealth()` (`Dashboard.jsx:30-40`, "3/4 core services online."); feature status `enabled/available/services_needed/insufficient_vram` normalized to `ready/disabled` for display (`:132-144`) | Loading: "Linking modules... reading telemetry..." (`:679`); feature fetch failure silently degrades to status-only view (comment `:623`) |
| **FirstBoot wizard** (`pages/FirstBoot.jsx`, 717 lines) | "What is this box and who am I?" | 4 taps: label the setup → name the first user → pick a stack (Chat only / Chat+Agents / Full Stack) → Finish, generating a QR owner card | n/a (linear wizard) | Owner-card-unavailable and finish-error states both explain *why* and *what to do next* (`:547-562`) |
| **Extensions** (`pages/Extensions.jsx`, 1328 lines) | "What can I install, and what state is it in?" | Install/Enable/Disable/Remove/Purge, per-extension console, live install progress | 10-value enum each with a plain-English `STATUS_DESCRIPTIONS` sentence (`:60-84`, e.g. `unhealthy` → "Container is running but health check is failing — check logs") | `friendlyError()` maps raw backend strings to sentences (`:38-56`); a "polling lost" banner with auto-recovery tracking (`:111-160`) |
| **Integrations** (`pages/ServiceMap.jsx`) | "How do these services actually talk to each other?" | None (read-only topology) | 4-color legend: healthy/degraded/down/not deployed (`:332-337`) | `"Topology data unavailable: {error}"` (`:314`) |
| **Models** (`pages/Models.jsx`, 1970 lines) + **HuggingFaceModelBrowser** | "What models do I have, what can I add?" | Search HF, pull, assign, insights panel | download/insufficient-VRAM style badges | — |
| **Remote GPU** (`pages/RemoteProvider.jsx`) | "Is my cloud-burst route working?" | "Test route" probe, refresh | `StatusPill` | Inline red banner with the raw error |
| **Usage** (`pages/Usage.jsx`) | "What is this costing me, and can I trust the number?" | Enable/Restart Token Spy inline | readiness `missing/unconfigured` tones; a **"Tracking Source Guide"** panel labelled *"Honest by design"* explains each cost-source tag (`actual_billed`, `priced_from_tokens`, `local_zero_cost`, `untracked`) (`:761-799`) | `UsageReadinessPanel` shows message + detail + a real "Enable Usage Tracking" button with busy state (`:511-562`) |
| **Setup / Owner** (`pages/Invites.jsx`) | "Who has a key to this box?" | Create/revoke owner cards and guest invites (QR) | — | Icon+heading+explanation+CTA empty states (`:304-339`, "No owner cards yet") |
| **GPU Monitor** (`pages/GPUMonitor.jsx`) | "Is my GPU the bottleneck?" | none | per-GPU aggregate bars | `"GPU data unavailable" / {error}` (`:38-49`) |
| **Settings** (`pages/Settings.jsx` + `components/settings/EnvEditor.jsx`) | "What can I change, and what happens if I do?" | Save / **Apply changes** (recreates only the affected containers) | field-level required/enum/type errors in plain English (`settings.py:244-289`) | `BehaviorCard`s literally state Save/Restart/Apply behavior (`EnvEditor.jsx:268-279`); Apply is disabled with an explanation until it has a plan (`:271-273`) |

**Feature/Services/Extensions split.** "Features" (Chat, Voice, Document Q&A,
Workflows…) are outcome-level, declared as data inside each service's
`manifest.yaml` (`extensions/services/llama-server/manifest.yaml:24-39`) and
computed server-side into `enabled/available/services_needed/insufficient_vram`
(`dashboard-api/routers/features.py:19-110`). "Services" are the technical
containers; "Extensions" is the installer/marketplace layer over them. A
`NON_USER_FACING_LINK_SERVICES` set (`Dashboard.jsx:73-84`) hides infra services
(litellm, qdrant, embeddings…) from click-through — deliberate complexity hiding.
Hardware tiers get human names (`installers/lib/tier-map.sh`: "Entry Level",
"Prosumer", "Pro", "Enterprise", "Strix Halo 90+", "Cloud (API)"), and `GET
/api/features` returns plain-English GPU-tier advice (`features.py:162-177`, e.g.
"Entry-level GPU — focus on chat first").

**Self-explanatory API shapes.** `features.py:130-144` turns "available"/
"services_needed" into first-person suggestions ("Your hardware can run Document
Q&A. Enable it?" / "Voice needs whisper, tts to be running."). `GET
/api/features/{id}/enable` (`:188-250`) returns numbered steps + deep links per
feature, rendered by `components/FeatureDiscovery.jsx:246-300` as a modal — click
"Enable Document Q&A" and you get "1. Ensure Qdrant vector database is running… 2.
Open Open WebUI…" with a working "Open Chat" button. `routers/privacy.py:44-51,
68-90` puts a human `message` on every response ("Privacy Shield is active" /
"…is not running. Check: `docker compose ps privacy-shield`"). `routers/workflows.py:166-168`
turns a 400 into "Missing dependencies: qdrant. Enable these services first."
`components/TroubleshootingAssistant.jsx` pairs Symptoms → Likely cause → Solutions
with copy-to-clipboard commands, auto-surfacing the ones relevant to whatever is
currently unhealthy.

**First-run / guidance surfaces.** `installers/lib/ui.sh:97-103` defines a
consistent "AI narrator" voice (`ai/ai_ok/ai_warn/ai_bad/signal`); `:154-168` carry
brand-voice lore lines; `:507-597` `show_install_menu` ("Choose how deep you want
to go. I can install everything, or keep it minimal.") auto-disables ComfyUI on
low-VRAM tiers with an explicit follow-up ("You can enable it later with: `ods
enable comfyui`"); `:600-632` `show_success_card` closes with "THE GATEWAY IS
OPEN" / "Your data never leaves this machine." Phase 13
(`installers/phases/13-summary.sh`) writes a desktop shortcut + GNOME sidebar pin
(`:229-256`), retries preflight validation up to 3× with a spoken wait message
(`:170-183`), and writes a versioned, atomically-written summary JSON
(`:435-501`). `docs/SETUP-CARD.md` documents a script that renders a physical 4×6,
300-DPI card with two QR codes (Wi-Fi + setup URL) for zero-typing onboarding of a
boxed unit. `docs/POST-INSTALL-CHECKLIST.md` is a 6-step "does it actually work"
script. `docs/HOW-ODS-SERVER-WORKS.md` explains the whole microservice mesh as "an
office building" with each service as a named team member — no jargon. The outer
`README.md:120` links that guide plus ["listen to the audio
version"](https://open.spotify.com/episode/40MvqJ41bC8cEgvUyOyE3K) (a Spotify
episode), then `:124-139` gives an "At A Glance" Q&A table and an "If you know
Ollama / Open WebUI / AnythingLLM / n8n… ODS adds…" table that anchors newcomers to
tools they already know. `templates/*.yaml` are outcome-named install bundles
("Creative Studio", "Developer Homelab", "Voice Assistant") consumed by
`components/TemplatePicker.jsx`. `ods status` (`ods-cli:1273-1400`) prints
sectioned `━━━ ODS Status ━━━` / Health Checks / GPU Status blocks with ✓/⚠ per
service, plus `--json`.

## B. hal0 UX inventory

**Nav / IA.** `ui/src/dash/chrome.jsx:480-524` (`useNavItems`) lists Overview,
Slots (children: Endpoints, Runner Images, Stacks), Models (children: Profiles),
Benchmarks, Agents (children: Memory, MCP), Services, Logs, Settings. Compared to
ODS, the vocabulary is implementation-first: "Slots", "Runner Images", "MCP",
"Profiles", "Stacks" are hal0's own internal nouns exposed directly, not translated
into outcomes ("Chat", "Voice"). This matches the product's own framing — see
README.md's target hardware and register — but it does mean the nav teaches
hal0's architecture before it teaches what you can *do*.

| Page | Question it answers | Actions offered | Status vocabulary | Empty/error handling |
|---|---|---|---|---|
| **Overview** (`dash/dashboard-redesign.jsx`, active; `dashboard.jsx` is the fallback) | "Is the box up and who's using memory?" | Quick actions: restart agent / pull model / evict idle / open webui (`:166-215`) | slot phases `missing/pulling/starting/serving/ready/idle/stopped/crashed` (`slot-status.js:8-16`) | `RDAttentionCard`: "nothing needs you" when clear (`:920`); zero-slot state: "No models configured yet… Run `hal0 setup` in your terminal" (`dashboard.jsx:253-263`) |
| **Slots** (`slots.jsx`, 1118 lines) | "What's loaded, on what device, doing what right now?" | Load/Stop/Restart/Swap, per-slot capability toggles | color-coded dot + tooltip (`slot-status.js:158-226`, e.g. "Container stopped (auto-reloads on next request)") | pull-progress bar with live label (`:106-124`) |
| **Models** (`models.jsx`) | "What's on disk / pullable from HF?" | Pull, filter, assign to slot | pull job states | — |
| **Agents overview** (`agents/agents-overview.jsx`) | "What agents can drive this box?" | Restart Hermes, view persona | live/health per agent card, rendered as **collectible trading cards** (rarity, abilities, "pow") (`:29-50`) | roadmap agents shown greyed "coming soon" |
| **Brain chat** (`board/agent-chat.jsx`) | "Talk to the steward model directly" | send/stop | `placeholder="Ask hal0-brain…"` vs `"chat unavailable"` (`:354`) | — |
| **Board** (`board/board-view.jsx`) + **Approvals** (`chrome.jsx:1101-1214`) | "What is an agent waiting on me for?" | Approve/Deny gated tool calls | "no pending approvals" empty state; reassuring copy: "Calls block until you approve or deny — agents pause cleanly." (`:1114`) | badge is poll-driven, no arrival toast (issue #1994) |
| **Memory** (`memory-overview-v2.jsx`, `memory-map.jsx`, `telemetry-header.jsx`) | "How much unified memory is free, by whom?" | delete/curate facts, approve deletes | ruler + bar segments | header vs. bar computed "free" from two different bases on some boxes (issues #1928/#1929) |
| **Journal / Logs** (`activity-log.jsx`, `routes/journal.py`, `routes/logs.py`) | "What just happened?" | filter/expand | — | — |
| **Stacks** (`stacks.jsx`, 876 lines) | "What's my named slot+profile+model bundle?" | New/Clone/Snapshot/Import/Export/Delete | breadcrumb descriptor only: "Stacks · runtime · slot + profile + model bundles" (`:793-798`) | "No stacks yet — create one, snapshot the live config, or import a `.hal0stack.json`." (`:833`) |
| **Settings → Capabilities/Model Defaults/Security/Doctor/…** (`settings/pages/**`) | "What's configured, and does changing it need a restart?" | Save; one-click **Restart hal0-api** with confirm + live health-poll + toast (`settings/shared/RestartApiPanel.jsx`) | per-field `ApplyBadge`: green "live" / amber "⟳" / red "⚠ manual restart" (`settings/shared/ApplyBadge.jsx:9-16`) | `PanelFooter`: "capability probe failed — Save disabled" + Retry (`capabilities/shared.jsx:33-46`) |

**Guidance/diagnosis surfaces.** hal0's nearest equivalent to ODS's Troubleshooting
Assistant + Feature-enable modal is the typed `Diagnosis` model
(`src/hal0/diagnostics.py:41-80`: `id/severity/confidence/summary/detail/evidence/
next_steps/fixable`), fed by `GET /api/doctor` (`src/hal0/api/routes/doctor.py`)
into a single generic `DiagnosisPanel.jsx` that already renders `next_steps` as
chips (`$` command / `↗` doc / `•` manual, `:31-44`). The scaffolding is more
rigorous than ODS's ad hoc per-router strings — one classifier
(`src/hal0/health_report.py`) is shared verbatim by `hal0 doctor verify --json`
and the API, so the two can't drift. The install-time and per-setting
remediation UX (`RestartApiPanel`, `ApplyBadge`, `PanelFooter`) is genuinely
strong and, in places, ahead of ODS (a real restart-and-confirm loop with a
success/failure toast, not just a banner).

**First-run.** There is no wizard: `README.md:39-45` states this as policy —
"**⚡ The installer is the whole setup.** There is no separate first-run wizard
afterwards." `README.md:160-183` ("First ten minutes") is entirely
CLI/curl. `docs/` is a Starlight site with a conventional
getting-started/concepts/guides/reference split (`docs/getting-started/*.mdx`,
`docs/concepts/*.mdx`, `docs/guides/*.mdx`, `docs/reference/*.mdx`) — better
taxonomy than ODS's flat 60-file `docs/`, but reference-heavy (`cli.mdx`,
`config-schema.mdx`, `env-vars.mdx`, `mcp-tools.mdx`) rather than beginner-first.
`docs/getting-started/index.mdx:109-114` does disclose the no-auth-by-default LAN
posture up front, in a Starlight `<Aside type="caution">`.

## C. Where hal0 falls short (evidenced) — and where hal0 is ahead

**1. The remediation affordance is designed but not wired end-to-end.**
`Diagnosis.next_steps` and `NextStep(kind="command"|"manual"|"doc")`
(`diagnostics.py:41-56`) are real, and `DiagnosisPanel.jsx:31-44` already renders
them as chips. But the classifier actually wired to `/api/doctor`
(`health_report.py:246-283`, `to_diagnosis()`) hard-codes `next_steps=[]` for
every one of its 7 checks — even though the richer CLI path
(`src/hal0/cli/doctor_commands.py:887,899,911,1208,1251,1272,1304,1331,1645,1934,
1953`) already knows real remediation commands for adjacent checks (e.g. `hal0
model scan`, `podman pull <image>`). And even where a chip *is* populated, its
`target` is shown only as a `title=` tooltip (`DiagnosisPanel.jsx:38`) — not
click-to-run, not click-to-copy. Contrast ODS, where `GET
/api/features/{id}/enable` (`features.py:188-250`) returns real numbered steps
with deep links, and `FeatureDiscovery.jsx:246-300` renders them as an actual
modal with clickable "Open Chat"/"Open n8n" buttons. **This is precisely the
shape of issue #1845** (a printed remediation, `hal0 slot migrate-flags
--apply`, that fails because the panel omitted `--stop-services` even though the
*error message itself* named the fix) — a self-explanation gap in the exact same
family, not a one-off typo.

**2. Long silent waits with no progress line (issue #1870).** `slot load`,
`unload`, `restart`, `swap`, and `update --restart-slots` can legitimately block
10s to 2.7 hours with *nothing* printed — no spinner, no elapsed counter, no
state line — because the fix for a prior regression (#1832) raised the timeout
ceiling without adding the promised progress polling. ODS's closest analogue,
extension installs, streams a live progress bar and label
(`Extensions.jsx:106-124` pattern; poll loop `:111-160`) and degrades gracefully
with a "polling lost" banner rather than silence.

**3. A remediation that cannot work as printed (issue #1845).** The `hal0
update` "Convergence incomplete" panel tells the operator to "Stop hal0, then
run: `hal0 slot migrate-flags --apply`" — but that literal command fails,
because `hal0-slot@*` units are separate from `hal0.target`/`hal0-api`, and the
actually-sufficient invocation (`--apply --stop-services`) is only visible in
the *error* the failed command itself prints. ODS's install-menu logic shows the
alternative discipline: when it auto-disables a feature, it immediately states
the real command to reverse that (`ui.sh:547-549`, "You can enable it later
with: `ods enable comfyui`") in the same breath as the decision, not two failed
attempts later.

**4. Notification infrastructure exists but isn't wired to the moment that
matters (issue #1994).** `globals-install.ts:26,39-40` installs a working toast
system and hooks a "queue" from a zustand store; `notifications.jsx` already
unifies bell + "Needs attention" card. Yet an agent-requested memory delete
landing in the approval queue is silent — "the badge is poll-driven and
silent," per the issue, filed by the team's own reviewer during the memory-v2
build. ODS's parallel case (a background SDXL/model download finishing) is
explicitly narrated at the next natural checkpoint (`13-summary.sh:170-183`:
"SDXL Lightning model download still in progress" / "…completed").

**5. Same-screen contradicting numbers (issues #1928, #1929).** The memory
ruler's header (`telemetry-header.jsx:364`, `total - modelUsedGb`) and its bar
segment (`:389-390`, reducing to `total - gttUsedGb`) compute "free" from two
different bases, so on a box where host GTT usage diverges from the per-slot
model sum, the same screen shows two different "free" numbers (measured ~16 GB
apart in the issue). A second, related bug: `memory-map.jsx:141-142` reads dead
fallback keys (`stats.gtt_total_mb`, `rawHw.unified_memory_mb`) that don't exist
on the normalized shapes, so the pool can silently become "system RAM" while
still labeled "GPU pool (GTT)." ODS's dashboard computes a comparable figure
(sidebar memory bar, `Sidebar.jsx:76-89`) from a single source per render and
explicitly branches on `memoryType === 'unified'` rather than blending two
derivations.

**6. Help text explains internals, not consequences.** hal0's `FieldInfoIcon`
tooltips are widespread (20 files) and the popover mechanics are solid, but the
copy is written for someone who already knows the codebase: `"/api/health/system
· per-dependency status"`, `"/api/stats/power · sysfs hwmon, sensor-dependent"`,
`"hal0.toml [memory] · requires restart to switch"`
(`settings/pages/general/OverviewPage.jsx:59-231`, `settings/pages/data/
MemoryPage.jsx:69,161`). None say what changing the setting *does for you* or
what happens if you don't. ODS's equivalent — `STATUS_DESCRIPTIONS`
(`Extensions.jsx:73-84`) and the `saveHint`/`restartHint` strings
(`main.py:921-922`) — run the other way: consequence first, mechanism only if
needed ("Saving writes the .env file directly, keeps existing secret values when
left blank… stores a timestamped backup"). This is a register choice consistent
with each product's stated audience, but it means hal0 currently explains itself
to an engineer, not a first-time user — worth naming explicitly if hal0 wants a
broader audience.

**7. An unsurfaced security tradeoff (issue #1822).** `HAL0_BIND_HOST` (LAN) and
`require_auth` (off by default) are decided independently and never compared out
loud at the moment that matters — install completion or `hal0 doctor all`. The
posture is intentional and *is* documented (`docs/getting-started/index.mdx:
109-114`, a caution Aside), so this is "disclosed but not surfaced at the
decision point," a narrower gap than "hidden." ODS states comparable
consequences inline at the moment of the choice (e.g., `ui.sh:547-549` above),
which is the standard #1822 is asking hal0 to meet.

**8. `tool_model` has no dashboard path (issue #2108).**
`AgentsBrainPage.jsx` persists `enabled/read_only/model/max_rounds/
completion_timeout_s` but never `tool_model`, even though it's the one
`[brain_chat]` key with real routing consequences — it's `hal0.toml`-only. ODS
keeps this kind of thing symmetric: anything a manifest or schema declares
(`.env.schema.json`) is automatically surfaced as a labeled, described row in
Settings via `_build_env_fields` (`settings.py:194-241`) — there's no
config-file-only escape hatch for a setting with live behavior.

**9. Open WebUI is only partly pre-wired.** `src/hal0/openwebui/
env_writer.py:106-131` sets chat routing and voice (STT/TTS) into Open WebUI,
but never sets `RAG_EMBEDDING_ENGINE`/`RAG_OPENAI_API_BASE_URL`,
`ENABLE_WEB_SEARCH`/`SEARXNG_QUERY_URL`, or `ENABLE_IMAGE_GENERATION`/
`COMFYUI_BASE_URL` — confirmed absent by a repo-wide grep. hal0 *has* an
embeddings/rerank surface (`/v1/embeddings`, `/v1/rerank`) and an image
pipeline (`routes/comfyui.py`, `comfyui-pane.jsx`), but neither is reachable
through Open WebUI's own document-upload or image-gen buttons — a user who
opens the "familiar" chat UI and clicks "generate image" or uploads a PDF gets
Open WebUI's un-pointed defaults, not hal0. ODS wires all of this, including a
full baked-in ComfyUI node-graph JSON (`docker-compose.base.yml:127-146`) so
Open WebUI's native image button works unmodified.

**Where hal0 is genuinely ahead.** (a) `RestartApiPanel.jsx` is a complete,
correctly-sequenced repair loop — confirm → mutate → poll `/api/health` → toast
success or a specific failure message pointing at `journalctl -u hal0-api` —
better than any single ODS remediation flow reviewed here. (b) `ApplyBadge.jsx`
gives *per-field* reload-class information (live/needs-service-restart/needs-
manual-restart) with the affected service named in a tooltip, which is more
precise than ODS's page-level "Apply changes will recreate: X, Y" banner. (c)
One shared classifier (`health_report.py`) drives both the CLI and the API,
structurally preventing the two from disagreeing — ODS's status text is
duplicated per-router (`SERVICE_DESCRIPTIONS` in the frontend, `message=`
strings in each Python router) with no single source of truth. (d) The
agents-as-trading-cards treatment (`agents-overview.jsx`) is a distinctive,
memorable identity choice ODS has nothing like — a legitimate strength if
delight is a goal, at some cost to a brand-new user's ability to tell what an
"ability" with "cost 2" and "pow 90" actually does.

## D. Port candidates

| From ODS | hal0 target | Size | Risk |
|---|---|---|---|
| `next_steps` population pattern: `features.py:130-144` (human suggestion sentences) + `:188-250` (numbered steps/links) | Populate `health_report.to_diagnosis()` (`src/hal0/health_report.py:246-283`) with real `NextStep`s for the 7 existing checks (reuse the commands already known to `cli/doctor_commands.py`); make `DiagnosisPanel.jsx`'s command chips actually copy-to-clipboard or run | S–M | Low — additive; scaffolding (`Diagnosis`, `NextStep`, the panel) already exists and is tested |
| `FeatureDiscovery.jsx:246-300` modal (numbered steps + deep links, rendered from a live API response) | Reuse for `next_steps` once populated above, or for slot-load failures surfaced by #1845/#1870 | S | Low |
| `Extensions.jsx:60-84` `STATUS_STYLES`/`STATUS_DESCRIPTIONS` (status word → one sentence) | Add a parallel plain-language map alongside `slot-status.js`'s tooltips — keep the precise phase vocabulary, add a "what this means" clause for `crashed`/`stopped`/`pulling` | S | Low |
| `installers/lib/ui.sh:547-549` "auto-disabled X — here's the command to re-enable" pattern | Apply anywhere hal0 auto-derives a default with a workaround (e.g. `tool_model` #2108, ComfyUI-gated low-VRAM boxes) | S | Low |
| `docker-compose.base.yml:82-175` full Open WebUI env wiring (RAG/websearch/image-gen) | `src/hal0/openwebui/env_writer.py` — conditionally add `RAG_EMBEDDING_*`/`ENABLE_IMAGE_GENERATION`/`COMFYUI_*` only when the relevant hal0 slot is actually bound, mirroring ODS's `ENABLE_COMFYUI:-false}` gating discipline (`docker-compose.base.yml:126-127`) | M | Medium — must gate on live slot state or it repeats ODS's own Apple-VRAM-fallback footgun (`features.py:27-32`) of claiming a capability that isn't really there |
| `README.md:124-139` "At A Glance" + "If you know X, ODS adds Y" tables | hal0 `README.md`, near the top, framed against Ollama/llama.cpp/Open WebUI/LocalAI | XS | None (copy only) |
| `docs/POST-INSTALL-CHECKLIST.md` 6-step "did it work" script | `docs/getting-started/first-chat.mdx` or a new `verify.mdx` | XS–S | None |
| `TroubleshootingAssistant.jsx` Symptoms → Cause → Solutions with copy buttons | A CLI-doctor-backed panel using the *already-existing* `NextStep` data once wired (overlaps row 1) | — | — |

## E. Do-not-copy

- **The CRT typing-effect boot theatrics** (`ui.sh` `type_line`/`type_line_dramatic`,
  a real per-character `sleep`). ODS itself gates this off when
  `$INTERACTIVE != "true"` — it knows it's for a human's first, foreground,
  one-time run. hal0's installer is used repeatedly (upgrades, Proxmox CT
  scripting, fleet installs); adding artificial per-character delay anywhere in
  that path would actively hurt the audience hal0 has.
- **The LORE_MESSAGES brand-voice copy verbatim** (`ui.sh:154-168`). The *pattern*
  of a consistent narrator voice is worth having; the specific ideological
  copy ("No terms of service. No content policy. Just freedom.") is ODS's brand,
  not a UX mechanism, and shouldn't be imported as text.
- **ODS's flat, 60-file, ALL-CAPS `docs/` directory.** hal0's Starlight
  getting-started/concepts/guides/reference split is already better information
  architecture (less duplication, clearer entry points); don't flatten it to
  match ODS.
- **Client-side status re-derivation** (`Dashboard.jsx`'s ~250 lines of
  `pickFeatureLink`/`findHealthyService`/`normalizeFeatureStatus` helpers).
  hal0 already has the better pattern — one server-side classifier
  (`health_report.py`) shared by CLI and API — and should keep extending that,
  not add a second, client-side resolution layer like ODS's.
- **The Setup/Owner magic-link invite model** (`Invites.jsx`). This solves a
  multi-tenant "who's allowed in this household appliance" problem hal0's
  single-operator, trusted-LAN posture doesn't have today. Not applicable
  unless hal0 adds multi-user access as a feature in its own right.

## F. Owner decisions

1. **First-run wizard, or stronger post-install confirmation?** hal0's
   "installer is the whole setup" stance (`README.md:39-45`) is explicit
   policy, not an oversight — the question is narrower than "add a wizard": does
   the *end* of `install.sh` need an ODS-style summary (what's running, what
   isn't, exactly what to run next) rather than relying on the operator to find
   `hal0 status` themselves?
2. **Wire `next_steps` end-to-end for `/api/doctor`.** Low-risk, scoped, and the
   scaffolding already exists on both the CLI (`doctor_commands.py`) and UI
   (`DiagnosisPanel.jsx`) sides — this is the single highest-leverage fix in this
   review.
3. **Pre-wire Open WebUI's RAG/search/image-gen, or keep them hal0-native-only?**
   If the dashboard's own comfyui-pane and `/v1/embeddings` are meant to be the
   *only* front door, that's a legitimate "one UI, not two" decision — but it
   should be a decision, not an omission, and should be stated somewhere a user
   would find it before they click Open WebUI's native "generate image" button
   and get nothing.
4. **Who is the copy for?** The current technical register (API paths and
   config keys in tooltips) is coherent for hal0's stated homelab-operator
   audience. Moving any of it toward ODS's consequence-first, jargon-free
   register is a deliberate audience decision, not a bug fix — worth scoping
   explicitly rather than drifting page-by-page.
5. **Give cross-cutting notification gaps (toast-on-approval, #1994) an owner.**
   The issue itself flags this as "cross-cutting… not memory-specific" — worth
   deciding whether it's a tracked initiative (audit every SSE/poll surface for
   a matching toast) rather than a one-off patch.
6. **#1822's LAN-bind warning: disclose more loudly, without re-creating KB-1's
   scar.** The doc Aside already exists; the ask is a *non-enforcing*,
   *non-blocking* line in the install summary and a `hal0 doctor all` row. Given
   the prior incident (auto-enforcement made things worse), any implementation
   should be reviewed explicitly against the O19 lesson cited in the issue
   before shipping.
