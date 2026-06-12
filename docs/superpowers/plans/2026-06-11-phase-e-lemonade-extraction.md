# Phase E — Lemonade Extraction (#687) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the Lemonade/lemond integration from the entire platform — backend, dispatcher, observability, UI, tests, installer — and remove lemond from CT105, so the codebase and product read as if Lemonade never existed.

**Architecture:** All slots already run as podman containers under `hal0-slot@<name>.service` (Phases A–D). Phase E removes the now-dead lemond control plane: the `hal0.lemonade` package, `LemonadeProvider`, 3 lemonade API route files, the FLM trio router, the lemond catch-all `/v1` proxy, lemond-coupled observability (MetricsShim, LemondLogRing, IdleDriver, `_lemonade_state_enrichment`, `server_models_gen`), the orchestrator's `_lemonade_provider` gate (#687), the omni-router's direct lemond POST (#709), all UI surfaces (§11 of the spec), the installer's lemonade steps, and finally the live daemon on CT105 (snapshot first).

**Tech stack:** Python/FastAPI + pytest, React/Vite dashboard + Playwright γ-suite, podman/systemd, bash installer.

**Spec:** `docs/superpowers/specs/2026-06-10-lemonade-removal-container-switchover-design.md` §5, §6, §9, §10, §11, §12-E. Issues: #687 (primary), #709 (folded in), #594 (auto-regen hook — reversed), #649 (draft; `lemonade_proxy.py` deletion here removes part of its motivation — re-scope after merge).

**Verified seam facts (2026-06-11, main = 036e399):**
- `src/hal0/capabilities/orchestrator.py:475` — `is_npu_modality = child in _CHILD_TO_SLOT_TYPE and self._lemonade_provider is not None`; also `:234` (ctor), `:759-761` (flm_args reader), `:52` (lemonade client import).
- `src/hal0/config/schema.py:91` `_VALID_PROVIDERS` contains `"lemonade"`; `:304` `runtime: Literal["lemonade", "container"]`; `:291` `provider` default `"lemonade"`.
- `src/hal0/providers/__init__.py:35,46,68-77,90` — LemonadeProvider import/registry/factory/`__all__`.
- `src/hal0/omni_router/router.py:68,72,191,203` + `dispatch.py:85,91,162` — `lemonade_base_url` plumbing, direct POST to 13305 (#709).
- `src/hal0/api/__init__.py` — 62 lemonade refs: IdleDriver/MetricsShim/LemondLogRing lifespan tasks, lemonade client dep, 3 route registrations, flm_trio attachment, server_models_gen call.
- `src/hal0/api/routes/journal.py:59` `SourceFilter = Literal["hal0", "lemond", "merged", "all"]` + lemond ring projection.

**Locked decisions (this plan):**
1. **Metrics replacement (slim):** `/metrics` + `/api/health` lose MetricsShim. Replace with a slot-state exposition rendered from SlotManager/slot-view state (`hal0_slot_up{slot=}`, `hal0_slot_state{slot=,state=}`, `hal0_slots_ready_total`). No remote scraping in E; per-slot llama-server `/metrics` scrape is a follow-up issue.
2. **Journal:** `SourceFilter` shrinks — `"lemond"` removed; `"merged"`/`"all"` retained as aliases of the full hal0 stream for API compat. `LemondLogRing`, bridge, and ring projection deleted. Container slot logs already flow via journald `hal0-slot@*`.
3. **Omni router (#709 folded in):** drop `lemonade_base_url`; `_chat_completion` and dispatch ctx route through the dispatcher (`Dispatcher.forward`) so the GpuArbiter `gpu.image_mode` 503 guard applies. Model-classification prefix pins stay in omni (per #695 guardrail).
4. **Schema migration (§9):** `runtime` literal → `Literal["container"]` with a `before` validator coercing `"lemonade"` → `"container"` (+ journal warning + assign device-class default profile when profile absent). `provider` default flips to `"llama-server"`; `"lemonade"` removed from `_VALID_PROVIDERS` and coerced like runtime.
5. **Env var:** `HAL0_LEMONADE_BASE` (hermes provision + agent unit override) → `HAL0_INFERENCE_BASE`, default `http://127.0.0.1:8080` (hal0-api owns `/v1`); old var read as fallback for one release.
6. **ComfyUI status:** `_LEMONADE_UNIT` probe and `inference.lemonade` field deleted (UI field too); arbiter-truth mode (D7/D8) already covers switchover safety.
7. **Docs:** Phase E scrubs only what lies about the system (installer README sections, `docs/operate/lemonade.md` replaced by a container-runtime ops stub). Full README/ARCHITECTURE/PLAN/promo sweep stays Phase F. `docs/superpowers/` specs/plans are historical records — untouched.
8. **PR strategy:** three sequential PRs, each green on CI before the next: **E1** backend + tests, **E2** UI + γ-fixtures, **E3** installer/scripts/ops-doc. Then the destructive CT105 deploy.

**Acceptance bar (the "never existed" test):**
```bash
grep -ri -e lemonade -e lemond \
  src/ ui/src/ ui/tests/ tests/ installer/ scripts/ Makefile pyproject.toml \
  --exclude-dir=node_modules | grep -v -i 'legacy lemonade cleanup'   # uninstaller legacy block only
```
→ zero hits except the uninstaller's explicitly-labelled legacy-cleanup block (spec §10 requires it for upgrades of old boxes). UI bar is absolute: zero case-insensitive matches under `ui/src`. CT105: `hal0-lemonade.service` gone, no listener on 9000/13305/8001, all modalities pass live e2e.

---

## PR E1 — backend extraction + tests

### Task 1: Orchestrator gate (#687)
**Files:** `src/hal0/capabilities/orchestrator.py`, `tests/capabilities/test_orchestrator_reconciliation.py`, `tests/capabilities/test_npu_container_modality.py`
- [ ] Red test: orchestrator constructed **without** `lemonade_provider` still treats `stt`/`embed` as NPU modalities (`is_npu_modality` true ⇒ container `_set_flm_modality` path runs; assert npu.toml booleans written).
- [ ] Drop `and self._lemonade_provider is not None` at `:475`; delete ctor param `:220/:234`, the `:759-761` lemond flm_args reader (container/npu.toml path stands alone), and the `:52` import of `flm_args_from_lemond_config`/`flm_args_set_payload`.
- [ ] Purge `FakeLemonadeClient` fixtures from the two test files; green; commit.

### Task 2: Providers registry + schema migration (§9)
**Files:** `src/hal0/providers/__init__.py`, delete `src/hal0/providers/lemonade.py`, `src/hal0/config/schema.py`, `tests/config/test_schema.py`, `tests/config/test_schema_migration.py`, delete `tests/providers/test_lemonade.py`
- [ ] Red tests: (a) `SlotConfig(runtime="lemonade")` loads, coerces to `"container"`, warns; (b) provider `"lemonade"` coerces to profile-derived/`"llama-server"`; (c) `get_provider("lemonade")` raises KeyError; (d) profile-less legacy slot gets device-class default profile (`DEVICE_DEFAULT_PROFILES`).
- [ ] Implement: remove import/registry/factory/`__all__` in providers `__init__`; delete `providers/lemonade.py` (719 ln); schema `_VALID_PROVIDERS` minus `"lemonade"`, `runtime: Literal["container"]` + before-validator coercion, provider default `"llama-server"`.
- [ ] Green; commit.

### Task 3: Dispatcher chain (§5)
**Files:** delete `src/hal0/dispatcher/flm_trio.py`, delete `src/hal0/dispatcher/lemonade_proxy.py` (API file is Task 5), `src/hal0/dispatcher/router.py`, `src/hal0/dispatcher/npu_swap_status.py`, `src/hal0/dispatcher/proxy.py`; tests: delete `tests/dispatcher/test_flm_trio.py`, rewrite `test_flm_trio_container.py` (drop lemond-fallback cases), `test_npu_swap_status.py` (container-only), `test_router.py` light edits
- [ ] Delete `FLMTrioRouter`; stt/embed-on-npu = static-port dispatch (already primary since Phase A — remove the lemond-walk fallback branch).
- [ ] router.py: delete `_lemonade_gateway_base` (:122), lemond fall-through (:138), `_recover_evicted_slot` (:844-891) + its call sites; systemd `Restart=` owns recovery.
- [ ] npu_swap_status.py: container/systemd path only; drop `LemonadeError` import + lemond branch.
- [ ] proxy.py: FLM-tag heuristic (`":" → npu`) keeps routing to the container port; scrub lemond comments. (Tier-4 retirement remains #649 — don't fold.)
- [ ] Green; commit.

### Task 4: Observability replacements (§6)
**Files:** delete `src/hal0/lemonade/` (7 files ~2.5k ln), `src/hal0/journal/__init__.py`, `src/hal0/api/routes/journal.py`, `src/hal0/api/routes/health.py`, `src/hal0/api/routes/v1.py`, new `src/hal0/slots/metrics.py`; tests: delete `tests/lemonade/*`, rewrite `tests/api/test_metrics_prometheus_route.py`, `tests/api/test_journal_routes.py`
- [ ] Red tests: `/metrics` renders `hal0_slot_up`/`hal0_slot_state` from a mocked SlotManager; `/api/journal?source=hal0` works, `source=lemond` → 422.
- [ ] New slim exposition (`slots/metrics.py`: `render_slot_metrics(slots: list[SlotView]) -> str`), wired into health.py `/metrics` route; delete `_lemonade_loaded_models`, MetricsShim/prometheus imports; v1.py `lemonade_metrics_shim` getattr.
- [ ] journal/__init__.py: delete `LemondLogRing`, `start_lemond_bridge`, `_consume_once`, `_bridge_loop`; journal.py: SourceFilter → `Literal["hal0", "merged", "all"]`, delete ring projection.
- [ ] Delete `hal0.lemonade` package + `tests/lemonade/`; green; commit.

### Task 5: API surgery — lifespan, routes, settings (§6)
**Files:** `src/hal0/api/__init__.py` (62 refs), delete `src/hal0/api/routes/lemonade_admin.py`/`lemonade_logs.py`/`lemonade_proxy.py`, `src/hal0/api/_settings_apply.py`, `src/hal0/api/routes/settings.py`, `src/hal0/registry/model_store.py`, `src/hal0/registry` on_change hook (#594 reversal), `src/hal0/api/routes/slots.py` + `src/hal0/slot_view/__init__.py` (`_lemonade_state_enrichment`, `lemonade_state` field), `src/hal0/slots/manager.py` lemond branches; tests: delete `tests/api/test_lemonade_admin_route.py`/`test_lemonade_logs_routes.py`/`test_slots_lemonade_state.py`/`tests/slots/test_manager_lemonade_bridge.py`, rewrite `test_settings_apply.py`, `test_settings_models_store.py`, `test_slots_routes.py`, `tests/slot_view/test_aggregator.py`, `tests/slots/test_manager.py`
- [ ] Red tests: app boots with no lemonade state attrs; `/api/lemonade/config` → 404; `/api/slots` payload has **no** `lemonade_state` key and container enrichment intact; settings `llamacpp_args` apply maps to per-slot unit restarts.
- [ ] api/__init__.py: remove all lemonade imports, `_lemonade_client` dep, `_start_lemonade_metrics_shim`, `_start_lemonade_idle_driver`, LemondLogRing init + bridge task + shutdown hooks, lemonade route registrations, flm_trio attachment, `write_server_models` call, `HAL0_SERVER_MODELS_PATH` plumbing.
- [ ] _settings_apply.py: delete `_expand_lemonade` + lemonade_admin imports; settings.py: `services: ["lemonade"]` → per-slot container restart entries; model_store.py: delete `propagate_lemonade_config`/`restart_lemonade_service`; registry on_change server_models hook deleted.
- [ ] slots.py/slot_view: delete lemonade enrichment fn + call + field; manager.py: delete the 7 in-function lemonade imports/branches (podman-inspect path is the only path).
- [ ] Green; commit.

### Task 6: Omni router through the dispatcher (#709)
**Files:** `src/hal0/omni_router/router.py`, `src/hal0/omni_router/dispatch.py`, `src/hal0/api/__init__.py` (attachment site); tests: `tests/omni_router/test_dispatch.py`, `test_route_to_chat.py`, `test_api_wiring.py`
- [ ] Red test: omni chat path invokes dispatcher forward (mock) — not a raw httpx POST to 13305; during `gpu.image_mode` the caller receives the 503 + Retry-After.
- [ ] Replace `lemonade_base_url` ctor/ctx params with a dispatcher-forward callable; keep prefix-pin classification in place (#695).
- [ ] Green; commit; note closes #709.

### Task 7: CLI + backend long-tail scrub
**Files:** `src/hal0/cli/capabilities_commands.py` (migrate-to-lemonade delete), `cli/migrate_commands.py`/`registry_commands.py`/`agent_shim.py` comments, `src/hal0/api/routes/comfyui.py` (`_LEMONADE_UNIT`, decision 6), `routes/updater.py` (`_LEMONADE_BIN_CANDIDATES`, `_parse_lemonade_version`), `routes/npu.py`, `routes/models.py`, `normalize/*`, `model_meta/__init__.py`, `bundles/schema.py`, `memory/hindsight_provider.py`, `providers/container.py`/`flm.py` comments, `slots/arbiter.py`/`capacity.py`, `agents/hermes_provision.py` + `installer/systemd/hal0-agent@.service` + `hal0-agent@hermes.service.d/override.conf` (`HAL0_LEMONADE_BASE` → `HAL0_INFERENCE_BASE`, unit dep on hal0-lemonade removed)
- [ ] Red tests: `hal0 capabilities migrate-to-lemonade` → unknown command; hermes provision renders `HAL0_INFERENCE_BASE` (old var honoured as fallback); comfyui status payload has no `lemonade` key.
- [ ] Sweep all sites per inventory; `grep -ri -e lemonade -e lemond src/` → 0.
- [ ] Full targeted suites green (`tests/api tests/slots tests/dispatcher tests/capabilities tests/omni_router tests/config tests/cli tests/providers tests/registry tests/normalize tests/slot_view tests/systemd tests/journal`); ruff check + format; commit; **PR E1 → squash-merge**.

## PR E2 — UI scrub (§11)

### Task 8: Hooks/endpoints/data layer
**Files:** delete `ui/src/api/hooks/useLemonade.ts`, `useLemonadeConfig.ts`; edit `hooks/index.ts`, `useBackends.ts`, `useComfyui.ts`, `useLogs.ts` (source enum, delete lemond WS branch), `useSlots.ts` (runtime default `container`, drop lemonade invalidation + comments), `useSettings.ts`, `useMcp.ts`, `useUpdates.ts`, `useChatCompletions.ts`, `api/endpoints.ts` (lemonade block, `lemonadeConfig`, `lemondLogsWs`), `api/mock.ts` (env `VITE_MOCK_LEMONADE` → `VITE_MOCK_HAL0` w/ legacy alias, delete lemond builders), `dash/data.jsx` (delete `lemond` block + `lemonade_state` fields).
### Task 9: Pages/components
**Files:** `dash/settings.jsx` (delete Runtime/lemonade section + LEMONADE_FIELDS + restart toasts + copy), `dash/slot-modals.jsx` (runtime default container, delete lemonade option/copy/provider strip/flm_args legacy parse), `dash/slot-status.js` (delete `_lemondPhase`, `LEMOND_LIVE_STATES`; container-only classifier), `dash/slots.jsx` (lemonade_state parsing out), `dash/chrome.jsx` + `dashboard.jsx` + `mcp-main.jsx` (useLemondRollup → container-native chips from useSlots/status), `dash/primitives.jsx` (delete 4 lemond banners, rewrite 2), `dash/main.jsx` filter label, `dash/command-palette.jsx`, `dash/extras.jsx` (lemond source toggle/demo/colors), `dash/extra-modals.jsx`, `dash/flow-modals.jsx` (systemctl copy), `dash/firstrun.jsx`, `dash/mcp-data.jsx`.
### Task 10: Fixtures + γ-suite
**Files:** `ui/tests/e2e/fixtures/mock-data.ts` + `apiMock.ts` (delete lemond block/type), ~14 specs per inventory (footer chips, slot-indicator, logs, npu-container, comfyui-arbiter, settings, etc.) — delete lemond-only cases, rewrite container equivalents.
- [ ] `grep -ri -e lemonade -e lemond ui/src ui/tests` → 0; `npm run build` clean (wipe `node_modules/.vite` + `dist` first); γ-suite targeted specs green; **PR E2 → squash-merge**.

## PR E3 — installer/scripts/ops doc (§10)

### Task 11
**Files:** `installer/install.sh` (delete PPA block :819-893, lemonade constants/tarball/unit/drop-ins/config.json/server_models_gen/health-wait ~600 ln; keep FLM .deb + NPU prereqs), `installer/uninstall.sh` (label legacy-cleanup block, keep PPA removal), `installer/README.md` (rewrite Configuration/Dev/ROCm/Troubleshooting for container runtime), `scripts/fresh-test-ct.sh` (keep `/opt/lemonade` + `hal0-lemonade` in cleanup loops **as legacy cleanup**, comment-labelled), `Makefile` comment, `docs/operate/lemonade.md` → `docs/operate/container-runtime.md` (ops guide: hal0-slot@ units, profiles, arbiter, journald).
- [ ] installer shellcheck/bash -n clean; harness-relevant tests (`tests/installer`) green; **PR E3 → squash-merge**.

## Deploy + verification (destructive — Tier 3)

### Task 12: CT105 extraction
- [ ] `wip hal0 status` → claim. Snapshot: `tar czf /root/lemonade-final-snapshot-$(date +%F).tgz /etc/hal0 /var/lib/hal0/lemonade /opt/lemonade/resources/server_models.json` + `systemctl cat hal0-lemonade.service`.
- [ ] `systemctl disable --now hal0-lemonade.service`; verify :9000/:13305/:8001 listeners gone (duplicate FLM child dies with it); `rm` unit + drop-ins; keep `/opt/lemonade` tree until soak passes, then remove.
- [ ] `scripts/deploy.sh` (rebuilds ui/dist); restart container slots (api restart drops upstream registry — restart, **not** load); `systemctl --failed` clean.
- [ ] Live e2e: chat + agent completion, npu chat + asr + embed (static ports), tts audio, rerank, utility, img generation via arbiter + auto-restore, `/metrics` exposition, journal stream, dashboard loads with zero lemonade strings (γ smoke or curl of bundle: `grep -i lemonade ui/dist/assets/*.js` → 0).
- [ ] Close #687 (+#709), comment on #649 re-scope; `graphify update .`; memory + handoff updates; release wip claims.
