I have everything needed. Here is the implementation-ready decomposition spec.

---

# P3-slots: Decomposition Spec for `slots/manager.py`

**Repo:** `/home/mint/hal0` @ `rework/descar` · **Target file:** `/home/mint/hal0/src/hal0/slots/manager.py` (**4,146 lines** as-built; plan cites 4,087) · **Mode:** READ-ONLY spec, verified against code.

## 0. Executive summary

`SlotManager` is a ~90-method god object mixing 8 responsibilities. The **legit core** (state machine + persistence + systemd/podman lifecycle + slot CRUD) is ~1,900 lines and must stay. The other ~2,200 lines are policy collaborators (idle/eviction, failure watchdog, drift comparator, NPU-trio reconciler, model-fallback guessing, routing/catalog, profile-adoption) that can extract. Extracting the recommended set lands the file at **~2,050 lines (≈50% cut)** while every externally-called public method keeps its name as a thin delegator (zero caller migration).

**Key constraint discovered:** `slots/capacity.py` **already exists** and is the VRAM/RAM *snapshot* (`CapacitySnapshot`, `build_per_slot`). The plan's "capacity manager (idle/sweep/pressure-evict loops)" therefore **cannot** be named `capacity.py` — use **`slots/reaper.py`**.

**Existing collaborators already carved out** (do not recreate): `slots/state.py` (state enum + records + legality), `slots/argv.py` (`FLAG_ALIASES`, `normalize_argv`, `merge_flags`), `slots/arbiter.py` (`GpuArbiter`, GPU llm⇄img exclusion), `slots/capacity.py` (snapshot), `slots/metrics.py`, `slots/ttft_samples.py`, and the module-level guard pipeline (`check_npu_exclusivity`/`check_default_uniqueness`/`reconcile_and_guard_slot_config`) already sits at module scope and is shared with `stacks/apply.py`.

---

## 1. Responsibility map (verified, method-by-method)

Line numbers are as-built in the current file.

### (a) CORE — state machine + persistence + lifecycle + CRUD → **STAYS**
| Method | Lines | Note |
|---|---|---|
| `Slot`, `LoadedSlot` classes | 239-305 | snapshots (LoadedSlot → could move w/ routing, §5.6) |
| `__init__` | 348-434 | owns all mutable state dicts |
| `_lock`, `_resolve_alias`, `_state_file`, `_config_file`, `_all_configured_slot_names`, `_ensure_known` | 542-585 | path/alias helpers |
| `state`, `is_ready_for_dispatch`, `_DISPATCHABLE_STATES` | 587-623 | public readiness (issue #696) |
| `_current_state`, `_transition`, `_broadcast`, `state_stream` | 627-796 | **the legit core** |
| `load`, `unload`, `restart`, `start`, `swap` | 1107-1352 | lifecycle |
| `status`, `list`, `iter_configs`, `_maybe_load_config`, `get_config` | 1355-1575, 3276 | queries |
| `spawn`, `_spawn_locked`, `terminate` | 1825-1896 | systemd/podman lifecycle |
| `create`, `delete`, `update_config`, `reconcile_unconfigured_slots` | 1900-2245 | CRUD |
| `_persist_model_default` | 2395-2425 | TOML default writer |
| `_load_slot_config`, `_invalidate_cfg_cache` | 3286-3398 | TOML read + mtime cache |
| `_resolve_model_info` | 3400-3454 | **⚠ ML-STORE OVERLAP** — see §7 |
| `serving`/`_serving_enter`/`_serving_exit`, `in_flight_count`, `enter_dispatch`, `exit_dispatch`, `bump_last_used`, `last_used` | 2639-2953 | SERVING counters + dispatch tickets (core dispatch coupling) |
| `_cfg_to_dict`, `_cfg_port`, `_cfg_provider`, `_model_default` | 3750-3783 | config accessors (used everywhere) |

### (b) Idle / eviction loops → **EXTRACT to `slots/reaper.py`**
`start_idle_monitor` (3008), `stop_idle_monitor` (3044), `_idle_monitor_loop` (3054), `_evict_timeout_for` (3070), `_sweep_candidates` (3100), `_sweep_idle_once` (3122), `_probe_host_free_mb` (3178), `_pressure_evict_once` (3197). Tunables `_IDLE_AFTER_S`, `_IDLE_MONITOR_INTERVAL_S`, `_EVICT_AFTER_S`, `_PINNED_BY_DEFAULT` (198-211). **~260 lines.**

### (b′) Failure watchdog + health probing → **EXTRACT to `slots/watchdog.py`**
> **Divergence from the task's grouping:** the task lumps `_fail_watch_loop` with the three idle loops as "capacity manager." Architecturally it is a **failure detector**, not capacity policy — it flips slots to ERROR/OFFLINE on a dead unit / failed `/health`, sharing nothing with idle/eviction except "a background task." Keeping them separate makes each collaborator single-purpose and independently testable. Recommend a distinct `watchdog.py`.

`_update_fail_watcher` (800), `_fail_watch_loop` (840-1000, **~160 lines**), `_is_active` (1002), `_probe_health` (1027), `container_readiness_check` (1064). Tunables `_FAIL_WATCH_INTERVAL_S`, `_FAIL_WATCH_LIVE_STATES`, `_HEALTH_FAIL_STRIKES`, `_WARMING_INACTIVE_STRIKES`, `_WARMING_STALE_AFTER_S` (156-188). `_await_ready` (3458-3552) is the load-path health gate — keep in core but call watchdog probes. **~340 lines.**

### (c) Config-drift comparator → **DELETE (preferred) or `slots/drift.py`**
`compute_config_drift` (1465), module fns `_argv_values` (3786), `_resolve_drift_flags` (3819), `_config_drift_values_equal` (3849), const `_CONFIG_DRIFT_KEYS` (193). **~115 lines.** Plan §Phase3.2: *"likely deletable — running argv equals rendered argv by construction."*

### (d) NPU-trio reconciler → **EXTRACT to `slots/npu/` package**
`is_npu_trio_shadow` (307-321, module fn), `reconcile_npu_trio_slots` (2254-2393), `_TRIO_SHADOW_SPEC` (2249), const `NPU_SEEDED_SLOTS` (122). **~170 lines.**

### (e) Model-resolution HEURISTICS ("guess what operator meant") → **MOVE to `registry/`**
`_resolve_servable_model` (2695), `_fallback_local_model` (2744), `_default_model_cache_check` (2816 — registry-facing), `_needs_pull` (2660), `RegistryUnavailableError` (72), plus module fns `_looks_diffusion_or_nontext` (3686), `_id_tokens` (3723), `_leading_token_overlap` (3733), consts `_SLOT_TYPE_TO_CAPABILITY` (3649), `_DIFFUSION_*`/`_NONTEXT_*` (3672-3683). **~330 lines.** Plan §Phase3.2: *"move the model-fallback guessing heuristics back to the registry/discovery layer."*

### (f) Config CRUD-write guard pipeline → **already module-level; move to `slots/config_write.py`**
`_read_slot_toml_dict` (3992), `_iter_peer_configs` (4013), `check_npu_exclusivity` (4030), `check_default_uniqueness` (4066), `reconcile_slot_updates` (4100), `reconcile_and_guard_slot_config` (4114), `_reconcile_device_profile` (3915), `_base_profile_for_backend` (3890), `_cfg_effective_backend` (3855). Async wrappers `_check_npu_exclusivity` (2568) / `_check_default_uniqueness` (2601) stay as thin delegators. **~360 lines.**

### (g) Model-preferred-profile adoption → **EXTRACT to `slots/profile_adopt.py`** (secondary)
`_preferred_profile_for` (2429), `_profile_fits_slot` (2443), `_apply_preferred_profile` (2482), `_defuse_stale_mtp_on_swap` (2522). **~140 lines.**

### (h) Upstream registration → **STAYS in core (or thin `slots/upstreams_bridge.py`)**
`_register_container_upstream` (438), `_deregister_container_upstream` (467), `reconcile_container_upstreams` (475). **~100 lines.** Tightly coupled to load/unload side-effects; low value to move. Keep in core.

### (i) Seeded catalogue + routing → **EXTRACT to `slots/routing.py`** (secondary)
`SEEDED_SLOTS`/`SLOT_ALIASES` consts (98-138), `seeded_slots` (1579), `default_slot_for` (1599), `_loaded_slot_from_config` (1624), `loaded_slot` (1669), `resolve_for_request` (1682), `route_for_request` (1735), `add_slot` (1749), `remove_slot` (1806). **~250 lines.** Pure config-query routing; no state-machine coupling. `LoadedSlot` moves here.

---

## 2. Target module layout

```
slots/
  manager.py          # CORE: state machine, _transition, persistence, lifecycle,
                      #        CRUD, spawn/terminate, serving counters, upstream reg.
                      #        (~2,050 lines)
  reaper.py       NEW # SlotReaper: idle demotion + TTL/pressure eviction loops
  watchdog.py     NEW # SlotWatchdog: fail-watch loop + is_active/health probes
  config_write.py NEW # guard/merge/device-profile write pipeline (from module scope)
  profile_adopt.py NEW# model-preferred-profile + MTP-defuse (secondary)
  routing.py      NEW # seeded catalogue, SLOT_ALIASES, LoadedSlot, resolve/route
  drift.py        NEW?# ONLY if compute_config_drift survives the delete attempt
  npu/            NEW # package: trio shadow reconciler + is_npu_trio_shadow
    __init__.py
    trio.py
  # existing, unchanged:
  state.py  argv.py  arbiter.py  capacity.py  metrics.py  ttft_samples.py

registry/
  fallback.py     NEW # resolve_servable_model + _fallback_local_model + diffusion guard
                      #  (destination coordinated with ML-store lane — see §7)
```

---

## 3. Interface boundaries (buildable contracts)

The two loop collaborators mutate the state machine (`_transition`, `unload`, `load`), so they need a back-reference. Use a **narrow `typing.Protocol`** (constructor-injected) rather than the whole `SlotManager` — makes both unit-testable with a fake and documents the exact seam.

### `slots/reaper.py`
```python
class ReaperHost(Protocol):
    _last_used: dict[str, float]
    _states: dict[str, SlotStateRecord]
    _serving_count: dict[str, int]
    _idle_after_s: float
    _evict_after_s: float
    _evict_pressure_mb: float
    _idle_monitor_interval_s: float
    def _current_state(self, name: str) -> SlotState: ...
    def _resolve_alias(self, name: str) -> str: ...
    async def _load_slot_config(self, name: str) -> dict[str, Any]: ...
    async def _transition(self, name, to, **kw) -> SlotStateRecord: ...
    async def unload(self, name: str) -> Slot: ...

class SlotReaper:
    def __init__(self, host: ReaperHost) -> None: ...
    async def start(self, *, idle_after_s=None, evict_after_s=None,
                    evict_pressure_mb=None, interval_s=None) -> None: ...
    async def stop(self) -> None: ...
    # internal: _loop, _sweep_idle_once, _pressure_evict_once,
    #           _evict_timeout_for, _sweep_candidates, _probe_host_free_mb
```
`SlotManager.__init__` constructs `self._reaper = SlotReaper(self)`. Public `start_idle_monitor`/`stop_idle_monitor` become one-line delegators (they are called from the API lifespan at `api/__init__.py:921` and must keep their names/signatures). `_probe_host_free_mb` keeps reusing `capacity._read_meminfo`.

### `slots/watchdog.py`
```python
class WatchdogHost(Protocol):  # superset of ReaperHost's mutators
    _fail_watchers: dict[str, asyncio.Task[None]]
    _states: dict[str, SlotStateRecord]
    def _current_state(self, name) -> SlotState: ...
    async def _maybe_load_config(self, name) -> dict|None: ...
    async def _transition(...): ...
    async def load(self, name) -> Slot: ...
    async def unload(self, name) -> Slot: ...

class SlotWatchdog:
    def update(self, name: str, new_state: SlotState) -> None    # was _update_fail_watcher
    async def is_active(self, name: str) -> bool
    async def probe_health(self, name: str) -> bool
    async def readiness_check(self, name: str) -> tuple[bool, str]  # container_readiness_check
    # internal: _fail_watch_loop
```
`_transition` (core) calls `self._watchdog.update(name, to_state)` at its tail (currently line 757). `container_readiness_check` is called by `dispatcher/router.py:924` — keep a public delegator on `SlotManager`. `_await_ready` (core) calls `self._watchdog.probe_health` / provider probes.

### `registry/fallback.py`
```python
def resolve_servable_model(model_id: str, *, slot_type: str, device: str,
                           cache_check: Callable[[str], bool]) -> str
def fallback_local_model(capability: str, configured_id: str = "") -> Model | None
```
Pure functions — no `SlotManager` reference. `SlotManager.load` (line 1156) calls `resolve_servable_model(resolved_model, slot_type=..., device=..., cache_check=self._default_model_cache_check)`.

### `slots/npu/trio.py`
```python
def is_npu_trio_shadow(cfg) -> bool                    # unchanged predicate
async def reconcile_trio_slots(mgr: SlotManager) -> int  # takes mgr; uses create/iter_configs
NPU_SEEDED_SLOTS: tuple[str, ...]
```
`is_npu_trio_shadow` is imported by tests (`tests/slots/test_npu_trio_shadow.py`) and used in `load`/`status`/`compute_config_drift`/`reconcile_container_upstreams`/`_probe_health` — **re-export from `slots.manager` and `slots/__init__`** to avoid churn. `reconcile_npu_trio_slots` is called from `api/__init__.py:911` — keep a public delegator.

### `slots/config_write.py`
Move the 9 module fns verbatim. `stacks/apply.py:26,259` imports `reconcile_and_guard_slot_config` from `hal0.slots.manager` — **re-export** it (and `check_*`, `reconcile_slot_updates`) from `manager.py` so that import is unbroken. `_cfg_effective_backend` is used by `status`/`create`/`update_config`/`_maybe_adopt_running_slot` — re-export.

---

## 4. Extraction order (least-coupled first)

Each step is independently shippable + green before the next. Ordering minimizes churn and de-risks the mutating collaborators last.

1. **`registry/fallback.py`** (pure functions, only `load()` calls them). Zero state coupling. **Coordinate landing dir with ML-store** (§7).
2. **`slots/config_write.py`** (already module-level; mechanical move + re-export). No behavior change.
3. **`slots/routing.py`** (pure config-query; move `LoadedSlot`, seeded consts, resolve/route). Re-export `SEEDED_SLOTS`/`SLOT_ALIASES`/`LoadedSlot`.
4. **`slots/npu/` package** (`reconcile_npu_trio_slots` + predicate). Delegator + re-export.
5. **Drift: attempt DELETE.** Prove running≡rendered by construction (see §6); if a real drift source remains, land `slots/drift.py`. Do this before touching status internals.
6. **`slots/profile_adopt.py`** (secondary; 4 self-contained methods).
7. **`slots/reaper.py`** (mutates state via `_transition`/`unload`). Delegators for `start/stop_idle_monitor`.
8. **`slots/watchdog.py`** (mutates state; the `_fail_watch_loop` is the highest-risk, most-tested block — do last). Delegators for `container_readiness_check`; wire `update()` into `_transition`.

---

## 5. Delegation & re-export policy (keep callers unbroken)

External callers verified — these public names **must survive on `SlotManager`** as delegators:
- `api/__init__.py` lifespan: `reconcile_unconfigured_slots` (903), `reconcile_npu_trio_slots` (911), `start_idle_monitor` (921), `reconcile_container_upstreams` (948), `arbiter` (1182).
- `api/routes/updater.py:889`: `compute_config_drift`.
- `dispatcher/router.py:924`: `container_readiness_check`.
- `api/routes/slots.py`, `api/deps.py`, `capabilities/orchestrator.py`, `cli/setup_command.py`: `SlotManager`, `Slot`.
- `dispatcher/_capability_resolve.py:23` + `api/__init__.py:407`: `SLOT_ALIASES`.
- `stacks/apply.py:26`: `reconcile_and_guard_slot_config`.
- `omni_router/*`, `tests/omni_router/conftest.py`, `tests/slots/test_loaded_slot.py`: `LoadedSlot`.

Module-symbol re-exports to preserve `from hal0.slots.manager import X`: `SEEDED_SLOTS`, `NPU_SEEDED_SLOTS`, `SLOT_ALIASES`, `LoadedSlot`, `is_npu_trio_shadow`, `RegistryUnavailableError`, `check_npu_exclusivity`, `check_default_uniqueness`, `reconcile_slot_updates`, `reconcile_and_guard_slot_config`, `_cfg_port`/`_model_default`/`_cfg_effective_backend` (used by `slot_view/__init__.py:405`). Update `manager.__all__` (currently 135-4145) and `slots/__init__.py`.

---

## 6. Drift-delete investigation (Phase3.2 hypothesis)

Before writing `drift.py`, test the plan's claim. `compute_config_drift` (1465) compares `provider.running_argv(slot)` vs `provider.expected_argv(cfg, model_info)` over `_CONFIG_DRIFT_KEYS` (`--ctx-size --model --alias -b -ub`), after alias-canonicalization (`_argv_values` via `FLAG_ALIASES`) and id→path resolution (`_resolve_drift_flags`). It exists because a slot could be **running stale argv** after a TOML edit without restart. Under §11.2 PortAuthority + the ML-store single-`Store` resolver, `--model`/`--alias`/`port` become authority-issued and reconciled on startup, removing the id/path drift class (the `#1226` false-warn the resolver code fights). **Recommendation:** keep drift only for the `--ctx-size`/`-b`/`-ub` runtime-flag class if that still diverges post-restart; otherwise delete `compute_config_drift` + 4 helpers + the `include_config_drift` param on `status` and have `api/routes/updater.py:889` return "no drift" or drop the panel. Decide with the P3-quadlet owner (unit rendering owns `expected_argv`).

---

## 7. Cross-lane overlaps (must coordinate — do NOT design here)

- **ML-store lane (owns `container._resolve_model_path`, `registry/store`):**
  - **`_resolve_model_info` (3400)** builds the `model_info` dict (registry dump + `_model_key`/`flm_tag`) that every provider consumes and that drift/spawn/create depend on. ML-store's single-`Store` resolver rewrites this path. **Keep `_resolve_model_info` in core for P3-slots; flag it as a shared seam.** Don't refactor its internals.
  - **Model-fallback heuristics (§1e)** land in `registry/`. **Agree the exact module** (`registry/fallback.py` vs folding into `registry/discover.py`, which already references the `#940 backstop contract` at `discover.py:123`) with ML-store so both land cohesively and the diffusion-guard consts aren't duplicated.
  - `_default_model_cache_check`/`_needs_pull` gate PULLING on `Model.path` existence — ML-store's "assert `model.path` under `model_store_root()`" changes this contract. Move them next to the store resolver, not into slots.

- **§11.1 slot ID-keying:** the whole NPU-trio *shadow* parallel lifecycle (`slots/npu/`) is **interim** — §11.1 folds all slot types into one uniform id-keyed lifecycle and dissolves the shadow path. Build `slots/npu/` so the reconciler is one importable function, not a mixin, so §11.1 can delete it cleanly. Anything you key by `name` (state path, unit `hal0-slot@<name>`, `_states`/`_locks`/`_last_used` dicts) will re-key to `id` — keep the alias-resolution chokepoints (`_resolve_alias`, `_load_slot_config`) intact as the future id-lookup seam.

- **§11.2 PortAuthority:** `_cfg_port` reads, `add_slot`'s `port=8081` default (1756), and `_register_container_upstream(name, port)` all become authority-issued. Note the interface; don't build it. `reduce`d drift (§6) depends on this.

- **P3-quadlet:** owns `providers.container` unit rendering (`expected_argv`, `load_sync`, `wait_ready`, `_resolve_context_size`, `_spec_provider_for`). Watchdog/`_await_ready`/`spawn` call into it — treat as a stable provider interface; the drift-delete decision is joint with this lane.

---

## 8. Tests impact (35 files in `tests/slots/`)

- **Direct symbol imports** — update import path OR rely on re-export (prefer re-export to keep diffs small): `test_loaded_slot.py` (`LoadedSlot`), `test_npu_trio_shadow.py` (`is_npu_trio_shadow`), `test_npu_exclusivity.py`, `test_default_uniqueness.py`, `test_device_profile_coherence.py`.
- **Monkeypatch on the class** — these patch `SlotManager.<method>`; if the method moves off the class they break. Keep as delegators OR update patch target: `test_config_drift_aliases.py:113,149` (`SlotManager._resolve_model_info` — stays core, safe), `test_fail_watcher.py` / `test_fail_watcher_warming.py` (patch `_fail_watch_loop`/`_update_fail_watcher` → now on `SlotWatchdog`; update or keep delegators), `tests/providers/test_flm.py:689` (`_resolve_model_info` — stays).
- **Behavioral suites that should pass unchanged if delegation is faithful:** `test_pressure_eviction.py`, `test_adopted_slot_eviction.py`, `test_pulling_serving_idle.py` (reaper), `test_health_probe_cfg.py` (watchdog), `test_npu_trio_reconcile.py`, `test_model_fallback.py` (→ retarget to `registry/fallback`), `test_model_preferred_profile.py`, `test_mtp_defuse.py` (profile_adopt), `test_config_drift_aliases.py` (drift — delete these if drift is deleted).
- **New unit tests** enabled by the Protocol seams: `SlotReaper`/`SlotWatchdog` against a fake `Host` — no full manager wiring, no filesystem. This is the testability payoff.
- Whole-manager suites `test_manager.py`, `test_manager_npu_container.py`, `test_manager_readiness_api.py`, `test_restart_errored_slot.py`, `test_slot_aliases.py`, `test_slot_create_conflict.py`, `test_upstream_reconcile.py` exercise core — must stay green throughout (regression gate on every step).

---

## 9. Risks

1. **`_fail_watch_loop` (160 lines, heavy state-machine logic: WARMING staleness auto-recover, health strikes, self-cancel semantics).** Highest-risk move. The self-cancel-via-own-transition logic (`_update_fail_watcher`, 828-838) is subtle. Mitigation: move last (step 8), keep `test_fail_watcher*` as the gate, preserve `_fail_watchers` dict ownership on the host or transfer atomically.
2. **Background-task ownership / event-loop lifecycle.** `_idle_monitor_task` and `_fail_watchers` are created in `_transition`/`start_idle_monitor` and cancelled on shutdown. Moving task ownership into collaborators must preserve the `RuntimeError` "no running loop" fallbacks (sync-context tests, lines 820, 3039) and the lifespan cancel path.
3. **`_transition` tail coupling.** It calls `_update_fail_watcher` (757) and `_broadcast` (731). The watchdog extraction reaches into the hottest core method — verify ordering (broadcast before watcher spawn) is preserved.
4. **Re-export drift.** Missing a re-export breaks a distant importer at runtime, not import-time in some lazy-import sites (`api/__init__.py:407`, `slot_view:405` import inside functions). Grep-verify every symbol in §5 after each step.
5. **Drift delete is a behavior change** (removes the updater's drift panel signal). Needs product sign-off + joint decision with P3-quadlet; don't delete unilaterally.
6. **ML-store race on model-fallback destination.** If both lanes move heuristics into `registry/` independently they'll collide on the diffusion-guard consts. Sequence: fallback extraction (step 1) coordinates first or defers to ML-store's module.
7. **NPU predicate fan-out.** `is_npu_trio_shadow` is called in 5 core sites + tests; moving it must keep a stable import. Low risk with re-export, but easy to miss one call site.

---

## 10. Line-budget accounting (→ "roughly halve")

| Extraction | ~lines out | Cumulative file size |
|---|---:|---:|
| start 4,146 | — | 4,146 |
| registry/fallback (§1e) | 330 | 3,816 |
| config_write (§1f) | 360 | 3,456 |
| routing (§1i) | 250 | 3,206 |
| npu/ (§1d) | 170 | 3,036 |
| drift delete/move (§1c) | 115 | 2,921 |
| profile_adopt (§1g) | 140 | 2,781 |
| reaper (§1b) | 260 | 2,521 |
| watchdog (§1b′) | 340 | **~2,180** |

Net **≈47% reduction** (plus delegator/re-export lines added back, ~80, landing ~2,050 effective core). If a leaner target is wanted, `_await_ready` (95) can also move to watchdog and the seeded-catalogue consts fully to routing. The plan-mandated set alone (fallback+drift+npu+reaper) is ~875 lines (→3,270, 21%); reaching the halving requires the secondary extractions (config_write, routing, profile_adopt, watchdog), all of which are clean single-purpose seams.

---

### Files referenced (all absolute)
- Target: `/home/mint/hal0/src/hal0/slots/manager.py`
- Existing collaborators: `/home/mint/hal0/src/hal0/slots/{state,argv,arbiter,capacity,metrics,ttft_samples,__init__}.py`
- Callers to preserve: `/home/mint/hal0/src/hal0/api/__init__.py`, `/home/mint/hal0/src/hal0/api/routes/{slots,updater,v1}.py`, `/home/mint/hal0/src/hal0/dispatcher/router.py`, `/home/mint/hal0/src/hal0/stacks/apply.py`, `/home/mint/hal0/src/hal0/slot_view/__init__.py`
- ML-store overlap: `/home/mint/hal0/src/hal0/registry/{store,discover}.py`, `/home/mint/hal0/src/hal0/providers/container.py`
- Plan: `/home/mint/hal0-rework-plan.md` (§Phase3 L146-181, §11.1 L663, §11.2 L679, ML-store L418-430)
- Tests: `/home/mint/hal0/tests/slots/` (35 files)