# hal0 SETTINGS rework — implementation-ready spec

Verified against code @ `rework/descar` (commit c69e25cb). Produced by settings-spec agent 2026-07-18.

**Docs stale in one load-bearing way:** the wireframe (`docs/plans/settings-wireframe.md`) describes a *Lemonade-shaped* config plane (`~/.hal0/hal0.env` + `recipe_options.json` + `/internal/config` + `/internal/set` server/deferred split). hal0 does **not** work that way — and hal0 has **already built the wireframe's core thesis under a different shape**. That gap is the spine of this spec.

## 0. Headline findings (verify-vs-code)

1. **"Settings tree IS the config schema / one source, three surfaces" already exists** — as `/api/settings`, not `/internal/config`:
   - `GET /api/settings` → full `Hal0Config` as JSON (`api/routes/settings.py:106`)
   - `PUT /api/settings` → partial deep-merge, pydantic-validated, atomic write (`settings.py:121`)
   - `GET /api/settings/schema` → `Hal0Config.model_json_schema()` — **the renderer source** (`settings.py:226`)
   - `GET /api/settings/apply-plan` + per-save `_hal0.apply_plan` — **this IS the ⟳/⏻ split** (`settings.py:237`, engine `api/_settings_apply.py`)
   - `POST /api/settings/reload` (`settings.py:208`); model-store migrate flow (`settings.py:301-564`)
   - UI already renders effect badges: `ApplyBadge` (`ui/src/dash/settings.jsx:123-160`) maps `immediate`→green "live", `service-restart`→amber "⟳ restart {svc}", `manual-restart`→red "⚠ manual restart".
2. **⟳/⏻ model is 3-class, not Lemonade's 2-way.** `_settings_apply.py:47` — `("immediate","service-restart","manual-restart")`. Wireframe ⟳=`immediate`; ⏻=`service-restart` OR `manual-restart`. Keep hal0's 3-class; render ⏻ for both non-immediate classes with the service name.
3. **hal0 config = TOML + pydantic, in several files** — not one env. `Hal0Config` (`schema.py:3008`) top-level keys: `meta, slots, dispatcher, telemetry, models, memory, honcho, activity, brain_chat`. Per §7.5: config stays TOML (`hal0.toml`, `slots/*.toml`, `providers.toml`, `profiles.toml`, `upstreams.toml`); machine state → one SQLite DB.
4. **Many wireframe knobs are NOT in `Hal0Config`** — ENV-only (resolved at service start) or separate TOMLs, so the generic `/api/settings` plane can't touch them today:
   - bind host / hostname / API port / origins → `config/network.py` via `HAL0_BIND_HOST`, `HAL0_HOSTNAME`, `HAL0_PORT`.
   - providers/upstreams → `routes/providers.py`; profiles → `routes/profiles.py`. Separate CRUD, separate TOMLs.
   - slots → per-slot `slots/<name>.toml` via `/api/slots/{name}/config`.
5. **No auth exists anywhere.** Only `request_id`+`error_codes`+`log_scrub` middleware (`api/__init__.py:1269`). `HAL0_API_KEY` appears nowhere in `src/hal0`. Only agent chat-proxy has scoped HMAC-cookie+origin seam (`api/agents/_auth.py`). This is KB-1/§1 and blocks the entire Security page.
6. **§21 (Adoption integration) IS in the plan** (line 1220). Settings wireframe is the UI/config front for §21 + §13 (observability) + §7.1 (model-owned config). No `/internal/config`/`/internal/set` anywhere in plan — hal0's `/api/settings` is the intended surface.

## (a) Config-schema-as-single-source design

**Decision: do NOT build `/internal/config`/`/internal/set`. Adopt & extend `/api/settings`.** It already delivers "one schema, three surfaces": pydantic `Hal0Config`=schema; `GET /schema` renders UI; `hal0 config` CLI + PUT/GET = API.

Three extensions so the plane covers the whole tree:

- **E1 — Bring ENV-only server knobs into a config surface (`[server]`).** Add dedicated `GET/PUT /api/settings/server` reading/writing `/etc/hal0/api.env` (bind host, hostname, port, admin/api keys) with the apply-plan contract. Recommend **api.env-backed endpoint** (systemd `EnvironmentFile` values; folding into `hal0.toml` fights the unit's `${HAL0_BIND_HOST}` substitution in `config/network.py`). Register every key in `_settings_apply.REGISTRY` class `manual-restart`.
- **E2 — Unify separate-TOML surfaces under the IA without merging stores.** Providers/Agent-Profiles/Loaded-Models/Slots stay backed by dedicated routes (`/api/upstreams`, `/api/profiles`, `/api/slots/*`); the settings left-rail just routes to those panes (already how `settings.jsx` works). Document: "one navigation tree over several typed config endpoints, each with an apply-plan" — soften the wireframe's "one `/internal/config` payload."
- **E3 — Extend `_settings_apply.REGISTRY` to every new key.** Today covers only 9 `Hal0Config` sections (`_settings_apply.py:107-188`). Every NEW key (auth, bind, backend, tuning) needs an entry so the UI badge + confirm-gate work. Single most important backend chore; cheap.

**Apply-class for new/changed keys:** device_name/bind_host/api_port/admin_port → ⏻ manual-restart; log_level/global-timeout → ⟳ immediate; auth enable/keys → ⏻ service-restart[hal0-api]; backend/rocm_channel/image-pin → ⏻ service-restart[slots]; hardware-tuning knobs → ⏻ manual-restart + **host reboot**; telemetry/brain_chat/memory.graph/slots.max_slots → ⟳ immediate.

**5-tier precedence mapping.** Wireframe's `request→recipe_options→arch_defaults→env→default` is Lemonade's *model-flag* precedence, not 1:1. hal0 has TWO axes:
- **Scalar config precedence (per key):** env var → `hal0.toml` (`Hal0Config`) → pydantic default.
- **Launch-flag/argv precedence (per model, §7.1a, `container.py:561` `resolve_argv`, 7 last-wins segments):** runner image → profile tune → arch defaults (`FAMILY_DEFAULTS`, `schema.py:1230`) → per-model metadata (mtp/jinja/ctx/extra_args) → slot `[server].extra_args` (always wins). Wireframe's `recipe_options.json` = hal0's per-model registry row (§7.1) + per-slot TOML; no single file.

## (b) Per-page → config-key map

Legend: **E**=existing key/route · **N**=new key · **G[§]**=feature-gated on unbuilt adoption work.

- **SERVER▸General:** device name/bind/port/admin-port = **N**(E1) ⏻; API base path = G[§21.5]; log level = **N** ⟳; global timeout = E(`dispatcher.direct_read_timeout_s`)+**N**; config export/reset = E+**N**(`/defaults`).
- **SERVER▸Security⛔ (entirely NEW, blocked KB-1/§1):** require API key, client/admin key, WS `?api_key=` = G[§1]; network-exposure-policy table = G[§21.11]; image-pin/run-nonroot/mem-caps/gitleaks/SHA256 = G[§21.15/§1].
- **SERVER▸Network:** mDNS = E(`/api/services/mdns`) ⟳; Tailscale = G[§1]; LAN/clients = N/G.
- **MODELS▸Library&Downloads:** source+endpoint = E(`/api/hf/search`)+G[§3]; pull = E; variants = **G[§3] MISSING**; downloads bg/cancel = E(`/api/models/pulls`); extra-dir scan = E+G[§3]; catalog CRUD = E.
- **MODELS▸Loaded:** slots table/unload = E(`/api/slots/*`); pin = **G[§21.10] MISSING**; TTFT/tok-s = E.
- **MODELS▸Defaults:** ctx/quant/per-arch/load-on-start/per-model opts = E reshaped by G[§7.1a/d].
- **INFERENCE▸Backend&GPU:** engine/backend/ROCm/image = E(`SEED_PROFILES`)+G[§7.1b/§21.6]; gfx guard = **G[§21.2] MISSING**; detected hw = E(`/api/hardware`).
- **INFERENCE▸Hardware Tuning🔒 (entirely NEW Danger Zone):** gttsize/iommu/tuned/ppfeaturemask/ttm/swappiness/preview-diff/apply+initramfs/schedule-reboot = G[§2/§21.1] ⏻+host-reboot.
- **INFERENCE▸Performance:** parallel/GPU-layers/eager/prefix-cache/MTP/kernel-cache = E(`schema.py:390`,`mtp`)+G[§7.1a].
- **INFERENCE▸Memory Manager:** max_loaded/auto-evict/idle-degrade/weight/orphan-kill = E(`slots.evict_pressure_mb`,`idle_timeout_s`)+**G[§21.10] (pin/two-stage/auto-evict MISSING)**.
- **ROUTING▸Mode&Fallback:** mode local/cloud/hybrid + ladder = **G[§21 D1/§4] MISSING as surface**; providers = E(`/api/upstreams`); agent profiles = E(`/api/profiles`)+G[§21.13].
- **OBSERVABILITY▸Telemetry/Health/Logs:** telemetry on/channel = E(`schema.py:2194`); OTLP/redaction = G[§13/§6]; health&stats = E(`/api/stats/*`,`/api/health`,`/api/metrics/prometheus`); **Logs = E SSE `/api/logs/stream` (wireframe wrongly says WS)**.
- **DATA▸Honcho/Storage/Offline:** honcho = E(`schema.py:2767`); models_dir/disk = E(`models.store`+migrate); offline = G[§12].
- **DIAGNOSTICS▸Doctor/Bundle/Updates:** doctor = CLI exists (`cli/doctor_commands.py`) but **no HTTP `/doctor`, no stable IDs → G[§21.4]**; support bundle = **G[§21.4] MISSING**; updates = E(`/api/updater/*`).
- **INTEGRATIONS▸API Compat/Client Setup:** OpenAI /v1 = E; Anthropic/Ollama/Realtime/tokenize/variants = **G[§21.5/§21.9] MISSING**; client setup + `hal0 launch claude` = G[§21.9/§21.12].

**Tally:** ~40% map to EXISTING key/route (buildable now); ~15% NEW config keys (mostly `[server]`/api.env); ~45% feature-gated on §1(auth)/§2+§21.1(tuning)/§21.4(doctor)/§21.10(memory)/§21.5+9(API)/§7.1(model config)/§13(telemetry).

## (c) settings.jsx rework (2598 → per-page)

Current: single `SettingsView({param})` (line 40) with left-rail+content-pane already present (`.settings-layout`, lines 67-94) + local `section` state (line 43). 12 sections (line 44). Ends `Object.assign(window,{SettingsView})` (line 2598) — **window-globals module, no ES export.**

**Ties to P3-ui (do together, once):** tracker line 113 "kill window-globals shim + split settings.jsx". §7.1d taxonomy split touches settings.jsx/slot-modals.jsx/model-modals.jsx/models.jsx/model-types.js — sequence Model-Defaults/Backend pages AFTER taxonomy lands.

**Target tree:**
```
SettingsShell (ES module; owns nav + routing)
  ├─ SettingsNav (grouped: SERVER/MODELS/INFERENCE/ROUTING/OBSERVABILITY/DATA/DIAGNOSTICS/INTEGRATIONS)
  ├─ one file per page (server/GeneralPage, SecurityPage⛔, ...)
  └─ shared/ (EXTRACT FIRST — precondition):
       ApplyBadge (settings.jsx:123) → shared/ApplyBadge
       schema engine _schemaField/_advCoerce/AdvRow/_deepMergePatch/_getIn (2061-2187) → shared/SchemaRow
       ConfirmDialog, AddSecretModal (window-globals) → import properly
       RestartApiPanel (2498) → shared/RestartApiPanel
       DangerConfirm + RebootScheduleDialog (NEW host-mutating)
```
**Routing:** replace `useState(section)` with real routes (`/settings/:group/:page`), lazy-loaded ESM chunks (helps P3 bundle goal). Preserve `param`→initial-section.
**Data layer reuse as-is:** existing hooks (`useSettings/useSettingsUpdate/useSettingsSchema/useApplyPlan/useModelStore*`, `useSlots/useUpdates/useProfiles/useSecrets`). Every new page fetches apply-plan registry + badges rows. **Fix NPU hardcoded amber chip (settings.jsx:1888) to use registry.**
**New affordances:** ⛔ `useIsAdmin()` gate (depends §1) hiding Security/Hardware-Tuning/host buttons for non-admins; 🔒 `HardwareTuningPage` Preview-diff→Apply(+initramfs)→Schedule-reboot behind `DangerConfirm`; ⟳/⏻ legend in shell header.

## (d) Backend surface

1. Keep `/api/settings` GET/PUT/reload/schema/apply-plan. **Add** `GET /api/settings/defaults` (return `Hal0Config().model_dump()`) + `POST /api/settings/reset` (confirm-gated). **Add `[server]`/api.env endpoint** (E1). **Register all new keys** in `_settings_apply.REGISTRY`.
2. **Network-exposure-policy table + CI (§21.11, MUST):** new artifact + `GET /api/settings/exposure` (derive from `network.bind_host`+router mount table) + CI test asserting no route binds beyond posture without §1 auth — codifies "unauthenticated by convention" surfaces (`/api/metrics/prometheus`, board) as explicit allowlist feeding **D2**. Cheap Phase-0 guardrail.
3. **Doctor→`hal0 doctor` (§21.4):** wrap `doctor_verify.run_verify` (`doctor_verify.py:350`) as `GET /api/doctor` (JSON), retrofit onto `_diagnosis(id,severity,confidence,evidence[],next_steps[])` with stable IDs. Add `hal0 doctor bundle`.
4. **Hardware Tuning (§2/§21.1) — Decision D4 RESOLVED (manual runbook only):** §21.1 scopes Strix-Halo tuning to PVE host, outside hal0's install. Surface as guided script generation + preview-diff + status, NOT a hal0 process mutating the PVE host. (See plan D4 resolution.)
5. **Health&Stats→existing introspection:** wire `/api/stats/hardware|slots|power`, `/api/health`, `/api/metrics/prometheus`, `/api/slots/metrics` (all EXIST). Wireframe's `/v1/health|stats|system-*`, bare `/metrics`, `/live` are Lemonade paths that don't exist — alias (§21.3) or relabel. Logs = SSE not WS — relabel.

## (e) MVP v0.1 subset

MVP pages: General · Security&Access · Backend&GPU · Hardware Tuning · Loaded Models · Library&Downloads · Doctor · Health&Stats.

**Buildable now (reorg + thin new config):** General (reorg + api.env bind/port/hostname + log_level), Loaded Models (UI over `/api/slots/*`), Library&Downloads (UI over `/api/models/*`; variants defer §3), Health&Stats (UI over `/api/stats/*`), Backend&GPU (read-only hw now; controls follow §7.1b).
**Blocked-on-adoption:** Security→§1/KB-1 auth (stated #1 urgency, most-gated — sequence auth first); Hardware Tuning→§2/§21.1 (+D4, manual-runbook resolved); Doctor→§21.4 HTTP wrap+IDs (small, CLI exists).
**Recommended MVP cut:** ship General/Loaded/Library/Health/Doctor(min) + the shell/nav/ApplyBadge/schema-engine extraction (P3-ui split) first. Gate Security + Hardware Tuning as visible-but-disabled "coming with auth/tuning lanes."
**Defer v0.2+:** Agent Profiles, Providers/fallback, Offline, Tailscale, Support Bundle, full Integrations.

## (f) Coordination — hard deps

- **Auth=KB-1/§1** → Security page, ⛔ gate, WS key, D2 metrics auth. Pulled into Phase 0/1. Everything admin-gated waits on this.
- **Host-tuning=§2/§21.1 (D4 manual-runbook)** → Hardware Tuning page as guided script, not in-process mutation.
- **Doctor=§21.4** → HTTP + stable-ID retrofit (CLI exists).
- **Model-mgmt=§3/§7.1/ML lane** → Library variants, extra_models_dir, Model Defaults (typed mtp/jinja caps replace `*-nojinja`/`*-small` profiles), pin. Coordinate §7.1d taxonomy split.
- **Memory-manager=§21.10/P3-slots reaper** → fold into reaper, don't build standalone.
- **Providers/fallback=§21 D1/§4** → Mode&Fallback page; providers CRUD exists, hybrid-ladder surface undecided (D1 resolved: local-first, no page until routing lands).
- **Config-contracts=§5/§21.11** → exposure CI + ports + golden-paths. Cheap Phase-0.
- **Telemetry/OBS=§13** → richer stats need SQLite metrics core first.
- **P3-ui ESM migration** → the settings.jsx split itself. Same lane, do once.

## Risks
1. Wireframe implies a config architecture hal0 didn't build; building literally would duplicate `/api/settings`. Mitigation: adopt/extend `/api/settings`; relabel doc. (Highest-leverage correction.)
2. Config is multi-store (hal0.toml + api.env + slots/*.toml + providers/profiles/upstreams TOML). "One payload" false; "one nav tree over typed endpoints" true. Every new key must be registered or badges silently no-op (NPU chip is the anti-pattern, settings.jsx:1888).
3. Security page (stated #1 urgency) is the most-blocked MVP page — sequence auth before promising it.
4. Hardware Tuning host-scope (D4) — resolved manual-runbook; surface as guided script.
5. settings.jsx is a window-globals monolith (implicit `ConfirmDialog`,`AddSecretModal`,`Icons`,`window.__hal0Toast`, global `React`). ESM split must thread these or pages break at runtime with no compile error. Extract schema-engine (2061-2187) + ApplyBadge first.
6. Taxonomy churn (§7.1d) reworks Model-Defaults/Backend pages — build after the split.

**Key files:** `ui/src/dash/settings.jsx` (2598) · `ui/src/api/hooks/useSettings.*` + `endpoints.ts` · `src/hal0/api/routes/settings.py` (564) · `src/hal0/api/_settings_apply.py` (307, the ⟳/⏻ registry) · `src/hal0/config/schema.py:3008` (`Hal0Config`) · `src/hal0/config/network.py` · `routes/{config,providers,profiles,models,slots,hardware,health,logs,updater,services}.py` · `cli/doctor_verify.py`+`doctor_commands.py` · plan §7.5(509)/§13(720)/§7.1(264)/§17(1048)/§21(1220) · `docs/rework/adoption-candidates.md` · `docs/plans/settings-wireframe.md`.
