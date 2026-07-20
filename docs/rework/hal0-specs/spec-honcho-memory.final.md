I have a complete picture. Here is the implementation-ready spec.

---

# hal0 rework: P1-honcho + P2-memory — implementation-ready spec

Repo `/home/mint/hal0` @ `rework/descar`. Verified against code (docs are stale). Decision confirmed: **memory engine = Hindsight only; remove Honcho.** Live lxc105 self-hosts Honcho as its store (127.0.0.1:8000, no-auth, workspace default) → a one-time Honcho→Hindsight data migration must run before any code deletion.

Aligns with `hal0-rework-plan.md:219-224` (P1 decision) and `:124-127` (collapse ABC→concrete).

---

## 0. EXACT Honcho footprint (file:line)

### Python — delete whole modules
- `src/hal0/memory/honcho_migrate.py` (583 lines) — the bidirectional sync engine. `migrate_hindsight_to_honcho` (:336), `migrate_honcho_to_hindsight` (:485), `MigrateState` (:69). **Reused for the one-time migration first, then deleted.**
- `src/hal0/memory/honcho_env.py` (241 lines) — renders `/etc/hal0/honcho.env`, restarts `hal0-honcho`. `render_env` (:88), `apply_honcho_env` (:160).

### Python — surgical edits (Honcho lives inside a shared file)
- `src/hal0/config/schema.py`
  - `HonchoLLMFeatureConfig` (:2707-2746), `HonchoLLMConfig` (:2749-2764), `HonchoConfig` (:2767-2809) → **delete all three classes**.
  - `Hal0Config.honcho` field (:3027) → delete.
  - `__all__` entries `"HonchoConfig"`, `"HonchoLLMConfig"`, `"HonchoLLMFeatureConfig"` (:3057-3059) → delete.
  - `_VALID_HONCHO_TRANSPORTS` / `_HONCHO_NAME_RE` (:2703-2704) → delete.
  - `MemoryConfig.agent_providers` (:2659-2668), `agent_private` (:2669-2678), `_VALID_AGENT_MEMORY_PROVIDERS` (:2609), `_agent_providers_known` validator (:2689-2698) → **delete** (per-agent engine routing).
  - `MemoryConfig.engine` field (:2648-2658) + `_engine_is_known` validator (:2680-2687) → **KEEP but simplify** (see §C — the `"cognee"` literal stays as back-compat; drop `"mem0"`/leave `"pgvector"` per collapse decision).
- `src/hal0/api/routes/memory.py`
  - Provider-routing block: `_HONCHO_UNIT_PATH` (:498), `MemoryProviderUnavailable` (:508-517), `get_memory_provider` honcho half (:545-567), `set_memory_provider` honcho probe/persist (:601-637, honcho branch) → **rework to Hindsight-only** (the `/provider` GET/PUT surface loses its reason to exist once there is one engine — see §D).
  - Honcho observability block **delete entirely**: `_HONCHO_SYNC_TIMER`/`_HONCHO_SYNC_SERVICE` (:682-683), `_honcho_probe_json` (:687-696), `honcho_stats` (:699-790), `honcho_sync_status` (:793-833), `set_honcho_sync_timer` (:836-876), `run_honcho_sync_now` (:879-893). Routes `/honcho/stats`, `/honcho/sync` GET/PUT, `/honcho/sync/run`.
- `src/hal0/cli/memory_commands.py`
  - `provider_app` (:58) + `add_typer` (:59), `_render_provider_status` (:311-334), `provider_list_cmd` (:337-356), `provider_set_cmd` (:359-407) → delete (per-agent routing UI).
  - `sync_graph_cmd` `@app.command("sync-graph")` (:426-472) → delete.
  - `honcho_app` + `honcho_render_env_cmd` (:477-514) → delete.
  - `__all__` (:517) `"honcho_app"`, `"provider_app"` → delete.
- `src/hal0/cli/memory_migrate_commands.py`
  - Bidirectional `migrate_default` callback (:84-182) + helpers `_load_honcho_cli_config` (:185), `_migrate_state` (:191), `_run_migrate_hindsight_to_honcho` (:197-245), `_run_migrate_honcho_to_hindsight` (:248-284) → delete. `_VALID_MIGRATE_ENGINES` (:75).
  - `migrate unify` command (:358-580) → **KEEP** (cross-bank Hindsight unify, no Honcho). Its module docstring (:1-13) references Honcho → update. Note `sync_graph_cmd`/`honcho_render_env_cmd` import these helpers, so delete those callers first.
- `src/hal0/agents/hermes_provision.py`
  - `_resolve_memory_provider` (:1767-1780) → collapse to always return `"hal0-memory"`.
  - `_render_honcho_json` (:1783-1846) → delete.
  - `_disable_honcho_hermes_host` (:1849-1875) → **KEEP one release** (cleans up stale `honcho.json` on live/migrated boxes; make it unconditional in the provision flow). Optional: delete in a later pass.
  - `_HONCHO_SDK_SPEC` (:1878) + `_ensure_honcho_sdk` (:1881-1901) → delete.
  - Provision wiring (:2065-2069) honcho branch → simplify to hindsight-only + call `_disable_honcho_hermes_host` unconditionally. Report keys `honcho_json_changed`/`honcho_sdk_upgraded` (:2096-2097) → drop.
- `src/hal0/services/registry.py` — service entry `id="honcho"` (:124-127, `unit="hal0-honcho.service"`) → delete.
- `src/hal0/registry/curated.py` (:585-615) — the `qwen3-embedding` curated model entry: **KEEP the entry** (Hindsight needs it), just scrub the "+ Honcho"/`honcho.llm.embedding` comment text.

### Installer / systemd / ops
- `installer/install.sh` — Honcho standup block **`:1838-2087`** (guarded by `HAL0_INSTALL_HONCHO=1`): apparmor drop-in, docker-compose-v2 install, honcho src clone @ `HONCHO_REF`, image build, alembic/schema reconcile, `systemctl enable --now hal0-honcho`, persist `[honcho].enabled=true`. Delete whole block. Also comment ref `:417`.
- `installer/systemd/hal0-honcho.service`, `hal0-honcho-sync.service`, `hal0-honcho-sync.timer` → delete all three files.
- `installer/honcho/` (dir: `README.md`, `docker-compose.yml`) → delete dir.
- `installer/uninstall.sh` — Honcho refs at `:21-34` (comments), `:217-226` (unit stop list — `hal0-honcho hal0-honcho-sync hal0-honcho-sync.timer`), `:261-274` (compose-stack backstop `podman compose down`), `:284-298` (container name glob `hal0-honcho*`), `:378-380` (unit-file removal), `:403` (timer wants symlink), `:563-577` (image removal glob `hal0-honcho*`). **KEEP as best-effort cleanup for already-installed boxes** — uninstall should still tear down a legacy Honcho stack. Optionally trim comments; do NOT remove the teardown logic (live boxes have the stack).
- `installer/lib/preflight.sh:468` — comment mentioning Honcho compose stack → trim (cosmetic).

### UI (`ui/`)
- `ui/src/api/hooks/useHoncho.ts` (139 lines) → delete file.
- `ui/src/api/hooks/index.ts:20` `export * from './useHoncho'` → delete.
- `ui/src/api/endpoints.ts:221-231` — `memoryProvider`, `memoryHonchoStats`, `memoryHonchoSync`, `memoryHonchoSyncRun` endpoints → delete honcho ones (keep any pure-hindsight).
- `ui/src/dash/memory-hook-bridge.ts:57-89` — imports + `__hal0UseHonchoStats/Sync/SetHonchoSync/SyncRun` bridge globals → delete.
- `ui/tests/e2e/specs/memory-provider-v3.spec.ts` → delete (provider-routing UI spec).
- `ui/tests/e2e/fixtures/apiMock.ts` — honcho mock entries → delete.
- Any dashboard "Honcho provider card" component consuming the bridge globals — grep the dash bundle for `HonchoStats`/`memoryProvider` and remove the card (not surfaced in this src tree; the bridge globals feed a separate dashboard repo per ADR-0023 note in `provider.py:106` — confirm the consumer).

### Docs (stale — rewrite/remove)
- `docs/guides/honcho-memory.mdx` (67 honcho refs) → delete.
- `docs/reference/cli.mdx` (7 refs — `memory provider`, `sync-graph`, `honcho render-env`) → remove those command sections.
- `docs/reference/env-vars.mdx` (5 refs — `HAL0_INSTALL_HONCHO`, `HONCHO_REF`) → remove.

### Tests
- **Delete:** `tests/config/test_honcho_schema.py`, `tests/agents/test_hermes_provision_honcho.py`, `tests/api/test_memory_honcho_routes.py`, `tests/api/test_services_honcho.py`, `tests/memory/test_honcho_env.py`, `tests/memory/test_honcho_migrate.py`, `tests/cli/test_memory_provider_commands.py`, `tests/cli/test_memory_migrate_unify.py` (verify it is Honcho-migrate, not `unify` — the file name is ambiguous; the *unify* command stays so keep its coverage, delete only Honcho-engine cases).
- **Edit:** `tests/config/test_memory_engine_field.py` (engine validator — keep cognee/hindsight, drop mem0), `tests/memory/test_provider_factory.py` (`ADR-0023: cognee branch removed` — update for mem0/pgvector collapse), `tests/memory/test_provider_abc.py` (ABC→concrete), `tests/memory/test_pgvector_degrade_safety.py` (see §C decision), `tests/api/test_services_page.py`, `tests/registry/test_curated.py`, `tests/agents/test_hermes_provision.py`, `tests/api/test_memory_provider_rename.py`.
- **Keep:** `tests/cli/test_memory_migrate.py` (cognee dry-run — back-compat shim).

`CHANGELOG.md` also carries Honcho entries (historical — leave; add a removal entry).

---

## (a) Ordered removal plan (dependency order)

**Phase 0 — data migration (see (b)). Do this on lxc105 BEFORE touching code.**

1. **UI first** (leaf, no back-refs): delete `useHoncho.ts`, its `index.ts` export, the `endpoints.ts` honcho keys, `memory-hook-bridge.ts` honcho block, e2e spec + apiMock entries, dashboard Honcho card. UI has no dependents.
2. **CLI command surfaces** (depend on migrate helpers/env): delete `sync_graph_cmd`, `honcho_app`/`honcho_render_env_cmd`, `provider_app` + its commands in `memory_commands.py`; delete the bidirectional `migrate_default` + its 4 helpers in `memory_migrate_commands.py` (keep `unify`). These are the only importers of `honcho_migrate` and `honcho_env` from the CLI.
3. **API routes**: remove the `/honcho/*` block and rework `/provider` (see §D) in `routes/memory.py`. Removes the API importers of `honcho_migrate.MigrateState`.
4. **Agent provisioning**: collapse `_resolve_memory_provider`, delete `_render_honcho_json`/`_ensure_honcho_sdk`, keep+unconditional `_disable_honcho_hermes_host`.
5. **Service registry**: delete the `honcho` service entry.
6. **Now the two modules have zero importers** → delete `src/hal0/memory/honcho_migrate.py` and `src/hal0/memory/honcho_env.py`. (grep `honcho_migrate|honcho_env` across `src/` returns empty before deleting — gate on this.)
7. **Config schema**: delete `HonchoConfig`/`HonchoLLMConfig`/`HonchoLLMFeatureConfig` + `Hal0Config.honcho` + `__all__` + `agent_providers`/`agent_private`/`_VALID_AGENT_MEMORY_PROVIDERS`. Do this **after** 1-6 because every honcho consumer reads `cfg.honcho.*`.
8. **Installer + systemd + docs**: delete the install.sh block, the 3 systemd units, `installer/honcho/`, docs. Trim (don't gut) uninstall.sh teardown.
9. **Tests**: delete/edit per the test list. Run full suite.
10. **P2 collapse** (see §C) — separate, self-contained commit after Honcho is gone.

Gate between each phase: `grep -rn "honcho\|Honcho" src/ | grep -v CHANGELOG` shrinks monotonically to zero (minus intentional `_disable_honcho_hermes_host` if kept).

---

## (b) One-time live-data migration procedure (lxc105)

**Direction exists and is reusable:** `migrate_honcho_to_hindsight` in `honcho_migrate.py:485`, exposed as CLI `hal0 memory migrate --from honcho --to hindsight` (`memory_migrate_commands.py:170`) and as `hal0 memory sync-graph` (`memory_commands.py:426`). It pages Honcho `POST /v3/workspaces/{ws}/conclusions/list` and writes each conclusion via `POST /api/memory/add` (hal0-api → Hindsight), idempotent through `document_id = conclusion.id` and a `created_at` watermark in `/var/lib/hal0/honcho/migrate-state.json`.

**What it runs against / needs:**
- `hal0_base = http://127.0.0.1:8080` (hal0-api) — must be up + Hindsight-backed.
- `honcho_base = http://127.0.0.1:{cfg.honcho.port}` (default **8000**) — Honcho stack must be **running** (`hal0-honcho.service` active).
- `workspace = cfg.honcho.workspace` (default `"hal0"`), `agent_id` = required arg (identity + private-bucket scope; writes land with `X-hal0-Private: 1` → the agent's private namespace, tagged `honcho-sync`).

**Preconditions (all on lxc105):**
1. `systemctl is-active hindsight-api hal0-api hal0-honcho` all `active`.
2. `curl -fs 127.0.0.1:8000/health` and `127.0.0.1:8080/api/memory/list?limit=1` both 200.
3. Confirm the real workspace + peer: memory note says workspace default, `user_peer` likely `operator`/`alexander`. Check `/etc/hal0/hal0.toml [honcho]` and `honcho workspace list` (honcho-cli).
4. **Private-workspace caveat:** the migrator reads only ONE workspace (`cfg.honcho.workspace`). If any agent used `agent_private=true` (isolated `<workspace>__private__<agent>`), each such workspace needs its own run — temporarily point `cfg.honcho.workspace` at it (or extend the call). Verify via `honcho workspace list` before assuming a single run suffices.

**Procedure:**
```
# 1. Dry-run (no writes — pure scan/count)
hal0 memory migrate --from honcho --to hindsight --agent hermes --dry-run
#    -> reports scanned/migrated/skipped; sanity-check the count vs honcho stats

# 2. Real run (idempotent; re-runnable). Fresh state file => no watermark => migrates all.
hal0 memory migrate --from honcho --to hindsight --agent hermes

# 3. (if multiple agents/private workspaces) repeat per agent / per workspace.

# 4. Verify in Hindsight
curl -s '127.0.0.1:8080/api/memory/search' -H 'X-hal0-Agent: hermes' -H 'X-hal0-Private: 1' \
     -d '{"query":"<known fact>","limit":5}'   # expect honcho-sync-tagged hits
```
Re-running is safe (document_id + watermark dedupe). Keep `/var/lib/hal0/honcho/migrate-state.json` until verified. **Only after verification proceed to code deletion** — the migrator, `hal0-honcho.service`, and `cfg.honcho.*` must all still exist while this runs.

Content fidelity note: only conclusion `content` text crosses over (metadata carries `observer/observed/session/created_at`). Honcho's derived structure (deriver/dream graph) is NOT ported — Hindsight rebuilds its own graph from the imported facts via its extraction slot. This is expected and acceptable per the plan.

---

## (c) P2: ABC → concrete `HindsightProvider` collapse

**Current shape:**
- ABC: `src/hal0/memory/provider.py` — `MemoryProvider(ABC)` (:130) with 5 abstract methods (`add`/`search`/`list_items`/`delete`/`graph_status`/`set_graph_enabled`/`set_rerank_enabled` — really 7) + 4 concrete optional defaults (`recall`/`reflect`/`consolidate`/`register_compiled`). Plus dataclasses `MemoryItem`/`AddResult`/`ListPage`/`DeleteResult`/`GraphStatus`/`Mode` and alias `MemoryRecord`.
- Concrete impls: `HindsightProvider(MemoryProvider)` (`hindsight_provider.py:140`), `PgVectorProvider(MemoryProvider)` (`pgvector_provider.py:35`, in-memory degrade fallback, `degraded=True`).
- Factory: `provider_from_config` (`memory/__init__.py:60`) — branches `engine=="pgvector"`→PgVector; `mem0`→warn+fallthrough; unknown→warn+fallthrough; then build Hindsight client, **on client build failure degrade to PgVector** (:86-90).
- **Only one construction caller:** `src/hal0/api/__init__.py:1615-1617` (`memory_provider = provider_from_config(create_app_cfg)`). Everything else (MCP dispatcher, REST shims) consumes the `MemoryProvider` interface, never constructs.

**What GOES:**
- `mem0` branch (`__init__.py:81-82`) — dead stub, never implemented.
- `engine` handling for mem0 in schema `_engine_is_known` (:2683 — drop `"mem0"` from `known`).

**What STAYS (intentional back-compat shims — confirmed):**
- `engine="cognee"` literal in the schema validator (`schema.py:2683-2687`) — kept, resolves to Hindsight at runtime. **No Honcho analog to keep** — Honcho was a *live parallel engine*, not a dead-value alias, so its removal is total (validator never accepted `"honcho"` for `[memory].engine`; that lived in `agent_providers`, which is being deleted).
- `migrate_cognee_to_hindsight_dryrun` (`memory/migrate.py:18`) — kept (used by `tests/cli/test_memory_migrate.py`; only wired to tests, no live CLI command — confirmed no `cognee` registration in `src/hal0/cli/`). **No Honcho analog to keep** — `honcho_migrate.py` is deleted wholesale post-migration.
- `MemoryRecord` alias (`provider.py:65`) — keep.

**Decision point you must resolve — `pgvector` / the ABC's abstractness:**
The plan (`:124-127`) says "collapse ABC/factory into the one concrete provider." But `PgVectorProvider` is the **boot-degrade fallback** (`provider.py` docstring §1, `__init__.py:86-90`): when Hindsight is unreachable at boot, the app serves an in-memory volatile provider so tools return empties instead of crashing. Two viable collapse targets:

- **Option A (recommended — keep the seam thin):** Remove the ABC's `abstractmethod` decorators / keep `MemoryProvider` as a plain base but make `HindsightProvider` the only *real* engine. **Keep `PgVectorProvider`** strictly as the degrade fallback (it is not a "dead branch" — it is load-bearing for the unreachable-daemon path). Delete only the `mem0` branch and unknown-engine fan-out. Factory becomes: `pgvector` explicit → PgVector (or drop this explicit path); else build Hindsight, degrade to PgVector on failure. This satisfies "one engine" (Hindsight is the only durable engine) while preserving crash-safety.
- **Option B (literal collapse):** Delete `PgVectorProvider` + the degrade ladder; factory returns `HindsightProvider` unconditionally and the app hard-fails at boot when Hindsight is down. Simpler, but removes the documented no-crash posture and `getattr(provider,"degraded",...)` consumers.

**Flag for the decision-maker:** the plan's "collapse to one concrete provider" reads as Option B, but Option A is safer and still "one engine." Pin this before implementation. `tests/memory/test_pgvector_degrade_safety.py` + `test_provider_factory.py` outcomes depend on the choice.

**Edit plan (Option A):**
1. `provider.py`: drop `@abstractmethod` on the core 7 → provide `raise NotImplementedError` bodies or keep ABC but note it now has exactly two impls. (Cheapest: leave file as-is; the abstractness costs nothing once `mem0` is gone.)
2. `__init__.py:provider_from_config`: delete lines 81-84 (mem0 + unknown warnings), keep the pgvector explicit branch (or remove if `[memory].engine="pgvector"` is unsupported), keep the try/except degrade ladder (:86-90). Update docstring (:60-73) to drop mem0.
3. `schema.py:_engine_is_known`: `known = {"cognee", "hindsight", "pgvector"}` (drop `mem0`); update `engine` field description (:2650-2657).
4. `memory/__init__.py:__all__`: keep `HindsightProvider`, `PgVectorProvider`, `MemoryProvider`, `provider_from_config`.
5. Update `tests/memory/test_provider_factory.py`, `test_provider_abc.py`, `test_pgvector_degrade_safety.py`, `tests/config/test_memory_engine_field.py`.

No call-site changes needed (`api/__init__.py:1615` unchanged; interface preserved). The `route`-mirror back-compat in `GraphStatus.to_dict` (:121) and `PgVectorProvider.graph_status` (:165) stay (dashboard cutover shim, ADR-0023).

---

## (d) Surface impacts

- **Installer:** `HAL0_INSTALL_HONCHO` / `HONCHO_REF` env vars gone; 3 systemd units + `installer/honcho/` gone; apparmor drop-in `99-hal0-honcho-apparmor.conf` no longer written (existing boxes keep the file — harmless; uninstall removes it). docker-compose-v2 no longer force-installed.
- **CLI:** removes `hal0 memory provider {list,status,set}`, `hal0 memory sync-graph`, `hal0 memory honcho render-env`, and `hal0 memory migrate --from/--to`. **Keeps** `hal0 memory migrate unify`. Update `docs/reference/cli.mdx`.
- **API:** removes `GET/PUT /api/memory/provider`, `GET /api/memory/honcho/stats`, `GET/PUT /api/memory/honcho/sync`, `POST /api/memory/honcho/sync/run`. Decide `/provider`: with one engine + no per-agent routing, either delete it or reduce to a read-only single-engine health probe (`GET /api/memory/engine` in `memory_admin.py` already covers engine health — likely delete `/provider` entirely).
- **UI/dashboard:** Honcho provider card + provider-routing controls removed; the `__hal0UseHoncho*` bridge globals gone. Confirm the external dashboard repo (per ADR-0023 note) isn't left calling deleted endpoints — coordinate that repo's removal or it will 404 fail-soft.
- **systemd/services page:** `hal0 services` no longer lists `honcho` (registry entry removed); `hal0-honcho*` units gone from `hal0 services` and the dashboard Services page (`tests/api/test_services_page.py` edit).
- **Config:** `[honcho]` and `[memory] agent_providers/agent_private` become unknown keys. `MemoryConfig` has `extra="allow"` (`schema.py:2622`) so stale `agent_providers` won't crash load; but `Hal0Config` — verify its `extra` policy so a leftover `[honcho]` table in a live `hal0.toml` doesn't error. **Add config-load tolerance / a cleanup note** for lxc105's existing `hal0.toml` (it has `[honcho] enabled=true` persisted by the old installer at `install.sh:2065`).

---

## (e) Risks (what breaks if Honcho goes mid-flight)

1. **Data loss if deletion precedes migration.** `migrate_honcho_to_hindsight`, `hal0-honcho.service`, and `cfg.honcho.*` must all still exist when the one-time migration runs. Deleting code first strands the only copy of the live conclusions (Honcho's Postgres is the store). **Hard ordering constraint: Phase 0 migration → verify → then Phase 1 deletion.**
2. **Agents routed to Honcho mid-cutover.** Any agent with `agent_providers[x]="honcho"` has its Hermes `memory.provider="honcho"` + a live `honcho.json`. Removing the engine without re-provisioning leaves that agent writing to a dead endpoint. **Mitigation:** before deletion, force every agent to Hindsight (set `agent_providers` empty / run `hal0 agent bootstrap <agent> --repair`), and keep `_disable_honcho_hermes_host` so re-provision flips `honcho.json hosts.hermes.enabled=false`. On lxc105 confirm which agents are Honcho-routed (memory note: hermes uses hal0-memory/hindsight already, but verify).
3. **Config load failure on live box.** lxc105's `hal0.toml` has persisted `[honcho] enabled=true`. If `Hal0Config` rejects unknown tables after `HonchoConfig` removal, hal0-api won't boot. Verify `Hal0Config` `extra` policy; ship a small config-scrub (or rely on `extra="allow"`) and document a `hal0 config edit` cleanup step.
4. **Stale watermark re-runs.** If migration is re-run after partial code changes, `/var/lib/hal0/honcho/migrate-state.json` must be intact; deleting `honcho_migrate.py` removes `MigrateState` — so never re-run migration after Phase 1. Snapshot the state file.
5. **External dashboard 404s.** The separate dashboard repo (ADR-0023 mirror consumer) may call `/api/memory/honcho/stats` + `/provider`; these will 404. Fail-soft in that UI is likely but confirm; sequence the dashboard-repo change.
6. **Uninstall regression.** Do NOT strip uninstall.sh's Honcho teardown — existing boxes still have the `hal0-honcho*` containers/units/images/pgdata. Removing the teardown orphans them on uninstall.
7. **P2 degrade removal (if Option B chosen).** Deleting `PgVectorProvider` makes an unreachable Hindsight daemon a hard boot failure instead of a degraded-but-up app. Confirm that trade is intended before removing the ladder.

---

**Key files (absolute):** `/home/mint/hal0/src/hal0/memory/{honcho_migrate.py,honcho_env.py,provider.py,pgvector_provider.py,hindsight_provider.py,migrate.py,__init__.py}`, `/home/mint/hal0/src/hal0/config/schema.py`, `/home/mint/hal0/src/hal0/api/routes/memory.py`, `/home/mint/hal0/src/hal0/api/__init__.py` (:1615), `/home/mint/hal0/src/hal0/cli/{memory_commands.py,memory_migrate_commands.py}`, `/home/mint/hal0/src/hal0/agents/hermes_provision.py`, `/home/mint/hal0/src/hal0/services/registry.py`, `/home/mint/hal0/src/hal0/registry/curated.py`, `/home/mint/hal0/installer/install.sh` (:1838-2087), `/home/mint/hal0/installer/systemd/hal0-honcho{,-sync}.{service,timer}`, `/home/mint/hal0/installer/honcho/`, `/home/mint/hal0/installer/uninstall.sh`, `/home/mint/hal0/ui/src/api/hooks/useHoncho.ts` + `endpoints.ts` + `memory-hook-bridge.ts`.