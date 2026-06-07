# OPTIMIZATION BACKLOG — hal0 (synthesis, 2026-06-07)

> Prioritized cleanup / refactor / deepen list, cross-referenced across all six audit
> docs. Each item: `[effort: S/M/L] [impact: H/M/L]`, file:line, flagging agent(s).
> Order: OSS-blocking + cross-cutting first, then high-impact single-area, then polish.
> **Cross-agent items (★) are the synthesis payoff — no single agent saw the whole story.**

---

## Tier 0 — OSS-release blockers (must fix before public tag)

### 0.1 ★ "Auth was removed" is FALSE — and the residue ships a private domain
`[effort: M] [impact: H]` — flagged by **A2 + A3 + B2 + B3** converging.
The docs (ADR-0012, `auth.mdx`, `CONTEXT.md:36`) say network auth was *removed*. Reality:
- `hal0-api` runs as **root** on `0.0.0.0:8080` with no gate (A3: `install.sh:160,640,643`).
- Yet **three** live auth code paths exist (A2): global `deps.py` `require_token`/`require_writer`,
  agents chat-WS HMAC `api/agents/_auth.py`, MCP bearer `mcp_mount.py:46,134`.
- And `api/agents/_auth.py:61` hardcodes `thinmint.dev` as the **default WebSocket origin
  allowlist** (B3) — a security default leaking the operator's LAN domain.

Synthesis: auth wasn't removed, it was *scattered* into three partial mechanisms the docs
deny exist, one of which leaks a private domain as a shipped security default. **Action:**
(a) make `DEFAULT_ALLOWED_ORIGINS` generic (`hal0.local`, `localhost:5173`, `127.0.0.1:8080`)
or env-driven; (b) reconcile the three auth paths under one documented model; (c) rewrite
`auth.mdx` (in hal0-web) to the ADR-0012 reality + document that the WS/MCP paths still exist.

### 0.2 thinmint.dev / hal0.thinmint.dev baked into shipped source
`[effort: S] [impact: H]` — **B3** (+B2 for docs). Beyond `_auth.py:61` above:
- `agents/hermes_provision.py:2023` — `dashboard_url` fallback `https://hal0.thinmint.dev`.
- `agents/hermes_templates/HERMES.md.j2:40`, `AGENTS.md.j2:10` — written verbatim into every
  user's agent config at provision time.
**Action:** default to `http://hal0.local:8080` / derive from `HAL0_API_BASE_URL`; parametrize
templates via the `dashboard_url` var `hermes_provision.py` already computes.

### 0.3 Lemonade SHA256 is an all-zeroes placeholder → silent inference death
`[effort: S] [impact: H]` — **A3** (`installer/install.sh:1131–1138`).
Clean install without `HAL0_SKIP_LEMONADE_SHA=1` silently SKIPS Lemonade → dashboard up,
chat dead. **Action:** pin the real digest in the release pipeline; make a skipped/failed
Lemonade install a hard error (or a loud, surfaced degrade state — see 0.4).

### 0.4 ★ Lemonade-skip → no clear "lemond missing" state on the dashboard
`[effort: M] [impact: H]` — **A3 cross-question → A1/A2 slots layer**.
If 0.3 fires (or the tarball download fails, `install.sh:1117`), nothing surfaces a clear
"inference unavailable" state — chat just 502s/times out. **Action:** SlotManager/dashboard
should report a distinct degraded state when lemond is absent. (A3's open cross-question to
the slots agent — see QUESTIONS.)

### 0.5 ★ Memory: docs say on, code is dark, fallback silently eats data
`[effort: M] [impact: H]` — **A2 + B2 + B1** converging.
- Docs present Cognee memory as default-on (B2: `CONTEXT.md:24`, `docs/memory/overview.md`).
- Code gates it behind `HAL0_MEMORY_ENABLED=0` (A2/B2: `api/__init__.py:1486`); every memory
  surface degrades to no-op/503 when `memory_provider is None`.
- The degrade fallback `pgvector_provider.py` is an **in-memory stub that drops all writes on
  restart** (B1: `memory/pgvector_provider.py:1`, wired at `memory/__init__.py:89`).

Compound risk no single agent stated: a user who believes the docs, flips the flag, and lands
on the degrade path **loses data silently**. **Action:** (a) doc the gate; (b) make
`pgvector_provider` log a loud warning on every degraded write (or refuse writes); (c) decide
whether degrade-with-silent-loss is acceptable at all.

### 0.6 Version-number scatter
`[effort: S] [impact: M]` — **B3 + B2**. `README.md:32` (v0.2.0), `CONTRIBUTING.md:3`,
`PLAN.md:10` all stale vs `pyproject.toml` `0.3.2-alpha.1`. `manifest.json:version` says
`0.1.0-alpha.1`. (NOTE: the static `manifest.json:version` is **cosmetic** — verified
`updater.py:899` reads `version` from the *fetched* GH release manifest, not this file; B3's
"could cause update checks to misfire" is unfounded. Fix for tidiness, not correctness.)
**Action:** single sweep; pyproject is the source of truth.

### 0.7 Hardcoded homelab IPs in OSS-facing files
`[effort: S] [impact: M]` — **B2 + B3**. `CONTRIBUTING.md` (`10.0.1.230/.231`),
`auth.mdx:331` (`10.0.1.230`), `scripts/prototype_ttft/live_probe.py:31`,
`scripts/import_haloai_models.py:11` (decommissioned CT220), `installer/lib/ui.sh:261`.
`docs/internal/` tree not gitignored → ships lab IPs + topology. **Action:** flip script
defaults to localhost; gitignore-or-sanitise `docs/internal/`; gitignore `graphify-out/*.json`.

### 0.8 Repo hygiene files missing
`[effort: S] [impact: M]` — **B3**. No `CODE_OF_CONDUCT.md`, no `SECURITY.md` at root.
README links three nonexistent docs (`docs/v0.2-upgrade.md`, `docs/api/mcp.md`,
`docs/api/agents.md`). **Action:** add the two files; fix/remove dead README links.

---

## Tier 1 — High-impact refactor / deepen (cross-cutting)

### 1.1 ★ Routing is split THREE ways AND `route_for_request` is NOT safely demotable
`[effort: L] [impact: H]` — **A1 + omni_router reality (verified)**.
A1 wants one routing authority behind the Dispatcher and proposes demoting
`SlotManager.route_for_request` (`slots/manager.py:1064`) to a delegate + deleting the
legacy `proxy.resolve_slot` tier-4.
**Reconcile conflict (verified by grep):** `route_for_request` is the omni-router's routing
primitive — consumed at `omni_router/dispatch.py:127` and `omni_router/filter.py:121`, and
`app.state.omni_router` IS live (assigned `api/__init__.py:1100`, falls back to None only on
start failure). And `proxy.resolve_slot` IS still reachable (`router.py:549`). So neither can
be deleted blind. **Action:** treat the three sites (Dispatcher ladder / `route_for_request`
+ omni_router / `resolve_slot` tier-4) as ONE consolidation with omni_router in scope, not a
simple demote. See QUESTIONS for the seam question.

### 1.2 ★ api/__init__.py is a god-module that owns other layers' wiring
`[effort: L] [impact: H]` — **A1 + B1**. 1596 lines fusing slot views + upstream hydration +
lemonade lifecycle + app factory + lifespan (`:808`, ~370 lines). Specifically:
- Slot-alias/model-view glue (`:313`, `:398`, `:433`) → extract to slots-owned
  `slot_model_views(slot_manager, registry)` seam.
- `_autoregister_slot_upstreams` (`:500`) + `_hydrate_upstreams` (`:647`) → move to
  `UpstreamRegistry.hydrate_from_slots()` (upstream layer owning its own hydration).
- App factory → own `app_factory.py`.

### 1.3 SlotManager god object
`[effort: L] [impact: M]` — **A1 + B1**. `slots/manager.py:182`, ~50 methods, 2221 lines.
Extract FSM/broadcast/fail-watch cluster (`:301–564`) into `SlotStateMachine`; idle loop
(`:1824–1896`) into the existing `IdleDriver`. Lifecycle methods stay.

### 1.4 ★ EventBus ↔ Journal: parallel ring+subscriber implementations
`[effort: M] [impact: M]` — **B1 + A1**. `events/__init__.py:74` and `journal/__init__.py`
are near-identical bounded-ring+fan-out buses; journal explicitly mirrors EventBus shape.
EventBus is the dashboard SSE spine and has **zero unit tests**. **Action:** shared base class;
add EventBus tests as part of the extraction. (Confirm the id-namespace split is intentional —
QUESTIONS.)

### 1.5 ★ FLM/NPU trio implemented as a triangle
`[effort: L] [impact: M]` — **A1 + A2 (LemonadeClient ownership)**.
`FLMTrioRouter` (`flm_trio.py:99`, owns own `LemonadeClient` `:127`) + `v1._is_npu_trio_request`
gate + `orchestrator._apply_npu_trio_modality` (`:682`) side-effects. LemonadeClient is
*constructed* by both flm_trio and the orchestrator (A1 + A2 both flag the ownership question).
**Action:** one owning module; inject a single LemonadeClient; fold `flm_args` read/modify/write
into `LemonadeClient.set_flm_modality()` (also fixes 1.7's leak).

### 1.6 Dispatcher reaches private `SlotManager._current_state()`
`[effort: S] [impact: M]` — **A1**. The one private-member leak in an otherwise clean DI seam
(`router.py:669,704`). **Action:** add public `SlotManager.state(name) -> SlotState`.

### 1.7 CapabilityOrchestrator imports LemonadeClient flm_args internals
`[effort: S] [impact: M]` — **A1**. `orchestrator.py:47` mutates lemond process args two layers
down. **Action:** `LemonadeClient.set_flm_modality(child, enable)` (shares fix with 1.5).

---

## Tier 2 — Durability / correctness gaps

### 2.1 ★ Editable-install `hal0 update` silently no-ops
`[effort: M] [impact: H]` — **A3** (live CT105 trap, not reconciled).
On editable `/opt/hal0`, `apply()` extracts + swaps `/usr/lib/hal0/current` (never read) +
skips re-pip + reports success (`updater.py:1079`). Neither the apply route
(`routes/updater.py:433`) nor CLI (`cli/update_commands.py:155`) refuse — only a warning.
install.sh's own comment (`:169`) **falsely** claims it refuses. **Action:** hard-refuse on
`_is_editable_install()` at the CLI + route + updater (QUESTIONS asks where the refusal belongs).

### 2.2 Model-pull jobs not durable (restart 404s the poll)
`[effort: M] [impact: M]` — **A2**. Updater jobs mirror to `/var/lib/hal0/update-jobs`
(`updater.py:114`); model-pull jobs live only in `app.state.model_pull_jobs`
(`models.py:1230`) despite the docstring saying "Mirror of the updater route shape." Same
`make_job` primitive, divergent persistence. **Action:** extend the disk-mirror pattern.

### 2.3 Updater re-pip uses `--no-deps`
`[effort: S] [impact: M]` — **A3** (`updater.py:798`). A release changing deps installs new
code against stale deps with no guard.

### 2.4 OpenWebUI image digest hand-synced in two places
`[effort: S] [impact: L]` — **A3** (`install.sh:664` + packaging unit). Single source it.

---

## Tier 3 — Stubs that raise at runtime

### 3.1 FeatureFlags stubs raise NotImplementedError
`[effort: M] [impact: M]` — **B1** (`config/features.py:34,48,56`). Every method raises; only
ref is a docstring. Phase-1 haloai port — confirm it's still planned (QUESTIONS).

### 3.2 FirstRunWizard.state()/pick_default() raise NotImplementedError
`[effort: M] [impact: M]` — **B1** (`installer/wizard.py:71,96`). Reachable from the installer
route. **Note:** A3 found the *actual* first-run OTP/`.first-run.lock` lives in
`routes/installer.py`, not the wizard — so the wizard stub may be a dead parallel path.
Confirm reachability (QUESTIONS).

### 3.3 MCP lifecycle mutations are 501 stubs
`[effort: M] [impact: M]` — **A2** (`routes/mcp.py:22,57,806`). `/api/mcp/{id}/{action}`
supervisor surface advertises install/uninstall/restart/config-write that all 501. A dashboard
build wiring the buttons gets "pending" toasts. (ADR-0015 partially shipped — B2.)

### 3.4 Provider.image_ref required but not @abstractmethod
`[effort: S] [impact: L]` — **A2 + B1** (`providers/base.py:172`). Fails at runtime, not
instantiation, unlike the 5 real abstract methods.

---

## Tier 4 — Test-coverage gaps (no tests on live logic)

`[effort: M each] [impact: M]` — **B1**.
- EventBus `events/__init__.py` (SSE spine) — see 1.4.
- Entire CLI command surface (7 modules) — user-facing entry points, zero dedicated tests.
- `slots/capacity.py` (357 ln), `slots/ttft_samples.py`, `api/image_cache.py` (LRU),
  `dispatcher/memory_dispatcher.py`, `routes/health.py`, `routes/backends.py` (453 ln).

---

## Tier 5 — Duplication / naming polish

`[effort: S each] [impact: L]` — **B1**.
- `ConfigInvalidError` duplicated: `routes/settings.py:63` + `routes/config.py:33` → move to
  `errors.py`.
- Byte-formatters ×3: `useModels.ts:403`, `normalizeApiModel.ts:58`, `settings.jsx:160` →
  `ui/src/lib/fmt.ts`.
- No shared `httpx.AsyncClient` factory (15+ call sites set own timeouts/pools).
- `httpx` imported lazily inside 4 function bodies (`v1.py:1219`, `slots.py:715`,
  `updater.py:287,421`) — normalise.
- `persona.py` vs `personas.py` (`agents/`); `api/agents/` package vs `api/routes/agents.py`
  module name collision.
- `_legacy_toolboxes` placeholder-digest block in `manifest.json` — remove.
- `import_haloai_models.py` references decommissioned CT220 — archive/update.
- `registry/curated.py:378` casual prod comment — cosmetic.
- No SPDX headers on Python source.

---

## Hermes provision monolith
`[effort: L] [impact: M]` — **B1** (`agents/hermes_provision.py:1`, 3393 lines, 9 phases,
84 functions). Split `hermes_install.py` / `hermes_config.py` / `hermes_mcp.py`. Couples to
persona rendering + MCP wiring — refactor touches the agents integration surface.
