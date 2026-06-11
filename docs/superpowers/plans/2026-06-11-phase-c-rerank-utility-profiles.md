# Phase C: Rerank + Utility Containers, Profile CRUD, Drawer-Editable Profiles — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `rerank` and `utility` run as vulkan llama-server podman containers (fixing #485's rerank half), profiles get full dashboard CRUD, the edit drawer gains a profile dropdown (rocm⇄vulkan swap per slot), and stale lemonade drop-ins are auto-cleaned (#694) — leaving lemond with zero actively-served slots.

**Architecture:** Both new slots ride the *existing* GPU container path (profile → `_render_unit`) — no provider work. The dispatcher splits `/rerankings` out of the embed path-pin into a `rerank` candidate (with the Phase B container-remote acceptance). Profile CRUD = `save_profiles_config` atomic writer + POST/PUT/DELETE routes with seed-immutability + in-use delete guard. `ProfileConfig.device_class` + `DEVICE_DEFAULT_PROFILES` power drawer filtering and create-modal defaults (spec D6/D7).

**Tech Stack:** Python 3.12/FastAPI/pydantic v2, podman+systemd, `ghcr.io/hal0ai/amd-strix-halo-toolboxes:vulkan-radv-server`, React dashboard, pytest + γ-suite.

**Spec:** `docs/superpowers/specs/2026-06-10-lemonade-removal-container-switchover-design.md` §3, §8, §12-C.
**Worktree:** `/home/halo/dev/wt-phase-c` (branch `feat/phase-c-rerank-utility-profiles`, base 4a58a0d). `.venv/bin/python -m pytest`. Commits `git commit -s`.

**Verified facts (research 2026-06-11 — don't re-derive):**
- Live `rerank.toml`: `device=""` (blank!), `backend=vulkan`, model `jina-...` in TOML but state.json tried `bge-reranker-v2-m3-q4_k_m` (drift), `[server] port=8083`, NO top-level port, state=error. The old combined `embed-rerank` slot is gone but its **stale drop-in survives** (`hal0-slot@embed-rerank.service.d`, port 8086, bge GGUF, `--reranking`, old `hal0-toolbox-vulkan:v1` image) — proof `--reranking` works containerized.
- GGUFs present: `/mnt/ai-models/local/bge-reranker-v2-m3-q4_k_m/bge-reranker-v2-m3-Q4_K_M.gguf` (chosen default), jina-tiny also present.
- **Two rerank consumers, two paths:** (1) gateway `POST /v1/rerankings` (v1.py:948) → currently path-pins to the **embed** slot (`_EMBED_PATHS` includes `/rerank` fragment; `_RERANK_DEFAULT="embed"` router.py:160); (2) the memory wrapper POSTs to `{rerank_url}/rerank` **directly** (llama-server native endpoint; `MemoryEmbeddingConfig.rerank_url` default `http://127.0.0.1:8086` = the dead embed-rerank port; live `rerank_enabled=false`).
- llama-server native rerank endpoints: `POST /rerank` (+`/v1/rerank` alias upstream) — it does NOT serve `/v1/rerankings`. The gateway forward appends the request path to the upstream `/v1` base, so a path REWRITE is required when forwarding `/v1/rerankings` to the rerank container.
- Live `utility.toml`: `provider=llama-server`, no runtime/profile → lemonade path, state offline. GGUF present (`gemma-4-12b-it-UD-Q4_K_XL.gguf`). `hal0/utility` virtual name resolves by slot NAME (no role field needed). Nothing utility-special — pure TOML migration + seed.
- Profiles: GET-only API; `load_profiles_config` **REPLACES** seeds when `/etc/hal0/profiles.toml` exists (no merge); `write_toml_atomic` (loader.py:69) is the write utility; no `save_profiles_config` yet; no reverse "slots using profile X" lookup anywhere.
- Drawer: profile is `<input readOnly>` (slot-modals.jsx:707); create modal hardcodes `device:"gpu-rocm"` (line 220). Model swap for container slots = unload+load (restart) — profile change should reuse that.
- `update_config` writes TOML only — container restart is a separate explicit step.
- **#649 decision (made): CLOSE.** It deletes proxy.py wholesale; Phase B made that file load-bearing (container-remote acceptance), Phase C adds more; its intent (retire heuristics) is Phase E scope. `SlotManager.state()` is a trivial cherry-pick if ever needed. Close with comment at deploy time.
- #694 (stale drop-ins): fix lands HERE (Task C3) — `_write_and_start_unit` cleans `hal0-slot@<name>.service.d` before starting; closes #694.
- agent slot: containerized (state.json `provider:lemonade` is stale cosmetic metadata — ignore researcher claim to the contrary).

---

### Task C1: Schema — `ProfileConfig.device_class`, `DEVICE_DEFAULT_PROFILES`, `save_profiles_config`

**Files:**
- Modify: `src/hal0/config/schema.py` (ProfileConfig ~line 618, SEED_PROFILES ~line 589)
- Modify: `src/hal0/config/loader.py` (next to `load_profiles_config` ~line 386)
- Modify: `installer/etc-hal0/profiles.toml` (parity)
- Test: `tests/config/test_profiles.py` (extend) + `tests/config/test_loader_profiles_save.py` (create)

- [ ] **Step 1: failing tests**

```python
# tests/config/test_profiles.py additions
def test_profile_device_class_defaults_gpu() -> None:
    assert ProfileConfig(image="x").device_class == "gpu"


def test_seed_device_classes() -> None:
    assert SEED_PROFILES["vulkan-std"]["device_class"] == "gpu"
    assert SEED_PROFILES["flm-npu"]["device_class"] == "npu"
    assert SEED_PROFILES["kokoro-cpu"]["device_class"] == "cpu"


def test_device_default_profiles_map() -> None:
    from hal0.config.schema import DEVICE_DEFAULT_PROFILES

    assert DEVICE_DEFAULT_PROFILES == {
        "gpu-rocm": "moe-rocmfp4",
        "gpu-vulkan": "vulkan-std",
        "cpu": "kokoro-cpu",
        "npu": "flm-npu",
    }
```

```python
# tests/config/test_loader_profiles_save.py (mirror the tmp_hal0_home fixture pattern of sibling loader tests)
def test_save_profiles_config_round_trips(tmp_hal0_home) -> None:
    catalog = load_profiles_config()                       # seeds (no file yet)
    catalog.profile["my-custom"] = ProfileConfig(image="ghcr.io/x/y:z", flags="-fa on")
    save_profiles_config(catalog)
    reloaded = load_profiles_config()
    assert "my-custom" in reloaded.profile
    assert set(SEED_PROFILES) <= set(reloaded.profile)     # seeds survive the full-catalog write


def test_save_is_atomic_full_catalog(tmp_hal0_home) -> None:
    # file content after save parses as ProfilesConfig and contains ALL profiles
    # (REPLACE semantics of load_profiles_config make partial writes data loss)
    ...
```

- [ ] **Step 2:** run → AttributeError/ImportError.
- [ ] **Step 3: implement.** `ProfileConfig` gains `device_class: Literal["gpu", "cpu", "npu", "img"] = "gpu"` (docstring: drives drawer filtering + create-modal defaults; "img" reserved for Phase D). Add `device_class` to every SEED_PROFILES entry + mirror in installer profiles.toml. Add `DEVICE_DEFAULT_PROFILES` (exact map above, docstring: create-modal preselect + Phase E migration default). `save_profiles_config(cfg: ProfilesConfig) -> None` in loader.py using `write_toml_atomic` at `paths.profiles_toml()`, writing the FULL catalog (header comment in the written file: "managed by hal0 — full catalog incl. seeds; load semantics REPLACE"). Export all in `__all__`.
- [ ] **Step 4:** `.venv/bin/python -m pytest tests/config tests/api/test_profiles_route.py tests/providers -q` (providers: ProfileConfig consumers must tolerate the new field — `extra="forbid"` means old files without device_class still parse since it has a default).
- [ ] **Step 5:** `git commit -s -m "feat(config): profile device_class + device defaults + save_profiles_config"`

---

### Task C2: Profile CRUD API

**Files:**
- Modify: `src/hal0/api/routes/profiles.py`
- Test: `tests/api/test_profiles_crud.py` (create; reuse test_profiles_route.py fixtures)

- [ ] **Step 1: failing tests** (client fixture per test_profiles_route.py):

```python
def test_create_profile(client) -> None:
    r = client.post("/api/profiles", json={"name": "my-vulkan", "image": "ghcr.io/x/y:z", "flags": "-fa on", "mtp": False, "device_class": "gpu"})
    assert r.status_code == 201
    assert any(p["name"] == "my-vulkan" for p in client.get("/api/profiles").json())


def test_create_duplicate_409(client) -> None: ...        # existing name (incl. seed names) → 409
def test_update_custom_profile(client) -> None: ...       # PUT /api/profiles/my-vulkan changes flags → 200, persisted
def test_seed_immutable(client) -> None:                  # PUT or DELETE on a seed → 409 with code "profiles.seed_immutable"
    for method in ("put", "delete"): ...
def test_delete_custom(client) -> None: ...               # DELETE → 204, gone after reload
def test_delete_in_use_409(client, tmp_hal0_home) -> None:
    # seed a slot TOML with profile = "my-vulkan" → DELETE → 409 code "profiles.in_use", details list slot names
def test_invalid_body_422(client) -> None: ...            # bad device_class / empty image
```

- [ ] **Step 2:** run → 405s.
- [ ] **Step 3: implement.** Request model `ProfileBody(BaseModel)` (name kebab-case ≤32 like slot names, plus ProfileConfig fields). Routes: `POST ""` (201), `PUT "/{name}"` (200), `DELETE "/{name}"` (204). All: load catalog → mutate → `save_profiles_config`. Guards: name in `SEED_PROFILES` → 409 `profiles.seed_immutable` (message: "seed profiles are immutable — clone it under a new name"); create on existing → 409; update/delete on missing → 404; delete with any slot TOML referencing the profile → 409 `profiles.in_use` + slot list (scan via the same slot-config iteration the slots route uses — read how `list_slots` iterates configs and reuse, no new scanning machinery). Use the repo's typed error envelope (Hal0Error subclasses — match how slots routes raise).
- [ ] **Step 4:** `.venv/bin/python -m pytest tests/api/test_profiles_crud.py tests/api/test_profiles_route.py -q` green.
- [ ] **Step 5:** `git commit -s -m "feat(api): profile CRUD with seed immutability + in-use guard"`

---

### Task C3: Stale drop-in cleanup in `_write_and_start_unit` (closes #694)

**Files:**
- Modify: `src/hal0/providers/container.py` (`_write_and_start_unit`)
- Test: `tests/providers/test_container_dropin_cleanup.py` (create)

- [ ] **Step 1: failing tests:**

```python
def test_stale_dropin_dir_removed_before_start(tmp_path) -> None:
    # arrange: fake systemd dir with hal0-slot@tts.service.d/override.conf
    # act: _write_and_start_unit (with _run patched, unit path redirected into tmp_path
    #      — mirror how existing tests redirect _unit_path / patch _run)
    # assert: the .service.d dir is gone AND daemon-reload was invoked (check _run call list)


def test_no_dropin_no_error(tmp_path) -> None: ...   # absent dir → clean start, no crash
```

- [ ] **Step 2:** run → dir survives.
- [ ] **Step 3: implement.** In `_write_and_start_unit`, before writing the unit: derive the drop-in dir path next to the unit path (`<unit_path>.d` — i.e. `hal0-slot@<name>.service.d`), `shutil.rmtree(dropin_dir, ignore_errors=False)` when it exists, log `container.stale_dropin_removed` with the dir. Comment: lemonade-era `Provider.render_systemd_override` drop-ins carry dead EnvironmentFile refs that fail container units (#694, hit live on the Phase B tts deploy). Ensure daemon-reload happens after removal (it already reloads for the unit write — verify ordering covers the removal too).
- [ ] **Step 4:** `.venv/bin/python -m pytest tests/providers -q` green.
- [ ] **Step 5:** `git commit -s -m "fix(providers): remove stale lemonade-era drop-ins before container unit start (closes #694)"`

---

### Task C4: Rerank dispatch — dedicated path-pin + upstream path rewrite

**Files:**
- Modify: `src/hal0/dispatcher/proxy.py` (split `_EMBED_PATHS`)
- Modify: `src/hal0/dispatcher/router.py` (`_RERANK_DEFAULT`, forward-path rewrite)
- Test: `tests/dispatcher/test_rerank_path_routing.py` (create)

- [ ] **Step 1 — read first:** how the dispatcher builds the upstream URL on forward (the `_join_url` call sites in router.py) and where a path rewrite can be injected per-route; the Phase B `_TTS_PATHS`/`path_pinned` mechanics (mirror them); existing embed-path tests (they pin `/rerank`→embed today and WILL need updating — that's expected, do it consciously).

- [ ] **Step 2 — failing tests:**

```python
def test_rerankings_path_pins_to_rerank_slot() -> None: ...
    # proxy: path /v1/rerankings → candidate "rerank"; container kind=remote slot_name="rerank" accepted
def test_embeddings_still_pin_to_embed() -> None: ...
def test_router_default_for_rerank_path_is_rerank() -> None: ...
def test_forward_path_rewritten_to_v1_rerank() -> None: ...
    # dispatch full chain with rerank remote at 8083: outgoing request URL ends /v1/rerank (NOT /v1/rerankings)
    # (llama-server serves /rerank + /v1/rerank only)
def test_no_rerank_slot_falls_back() -> None: ...   # pre-migration behavior: NoRouteFound → lemonade fall-through unchanged
```

- [ ] **Step 3 — implement:**
- proxy.py: `_EMBED_PATHS = ("/embeddings",)`; new `_RERANK_PATHS = ("/rerankings", "/rerank")` → candidate `"rerank"`, `path_pinned = True`. Update the rule docstring/numbering.
- router.py: `_RERANK_DEFAULT = "rerank"`. Forward rewrite: where the dispatcher constructs the upstream path for the selected upstream, map `/v1/rerankings` → `/v1/rerank` when the target is the rerank slot (smallest correct mechanism — if a generic per-path rewrite table exists use it; if not, a `_UPSTREAM_PATH_REWRITES = {"/v1/rerankings": "/v1/rerank"}` consulted at the `_join_url` site, with a comment explaining llama-server's native endpoint).
- [ ] **Step 4:** `.venv/bin/python -m pytest tests/dispatcher tests/api -q -p no:randomly` (api suite included — embed-path tests will surface; fix the ones that pinned the OLD `/rerank`→embed behavior, consciously, with comments).
- [ ] **Step 5:** `git commit -s -m "feat(dispatcher): /rerankings path-pins to the rerank slot + native /v1/rerank rewrite"`

---

### Task C4B: #696 — public `SlotManager.state()` + `is_ready_for_dispatch()` (locked interface)

**Context (arch-review drop 2026-06-11):** issue #696 locked a public-readiness interface (glossary PR #699). `state(name) -> SlotState`: cache-first, state.json fallback, OFFLINE default — keep that signature EXACTLY. `is_ready_for_dispatch(name) -> bool` owns the ready-set rule (READY | SERVING | IDLE) in exactly one place. The rule is currently duplicated in THREE sites: `dispatcher/router.py` `_ensure_slot_loaded_backend_aware` + `_check_slot_ready_for_dispatch`, and `dispatcher/flm_trio.py` `_container_npu_url` (Phase A inline `{"ready","serving"}` — note adopting the locked set is a BEHAVIOR CHANGE there: IDLE npu containers become dispatchable instead of falling back to lemond; that's correct and gets its own test). Claimed on #696 — do not narrow.

**Files:**
- Modify: `src/hal0/slots/manager.py` (public `state()` + `is_ready_for_dispatch()`; read what private accessors exist — if a `status()`-based path already computes this, the new methods wrap, not duplicate)
- Modify: `src/hal0/dispatcher/router.py` (both call sites consume `is_ready_for_dispatch`)
- Modify: `src/hal0/dispatcher/flm_trio.py` (`_container_npu_url` state check → `is_ready_for_dispatch`)
- Test: `tests/slots/test_manager_readiness_api.py` (create) + extend `tests/dispatcher/test_flm_trio_container.py` (IDLE case)

- [ ] **Steps:** failing tests first (state(): cache hit, cache-miss→state.json, missing→OFFLINE; is_ready_for_dispatch: true for READY/SERVING/IDLE, false for everything else; flm_trio: IDLE npu container resolves static port — lemond never called) → implement → refactor the three call sites (each site's existing tests must stay green; where a site's old inline set differed, update ITS tests consciously with a comment) → `.venv/bin/python -m pytest tests/slots tests/dispatcher tests/api -q -p no:randomly` → `git commit -s -m "feat(slots): public state() + is_ready_for_dispatch() — single ready-set rule (closes #696)"`.

---

### Task C5: Seeds + consumer config — rerank.toml, utility.toml, rerank_url default

**Files:**
- Create: `installer/etc-hal0/slots/rerank.toml`, `installer/etc-hal0/slots/utility.toml`
- Modify: `installer/install.sh` (seed loop: `npu tts rerank utility`)
- Modify: `src/hal0/config/schema.py` (`MemoryEmbeddingConfig.rerank_url` default → `http://127.0.0.1:8083`)
- Test: extend the seed-validation tests (mirror npu/tts ones)

- [ ] **Step 1: failing tests** — `test_seed_rerank_toml_validates` (runtime container, profile vulkan-std, device gpu-vulkan, port 8083, extra_args contains `--reranking`, model bge-reranker-v2-m3-q4_k_m) + `test_seed_utility_toml_validates` (vulkan-std, port 8081, model gemma-4-12b-it) + `test_rerank_url_default_is_rerank_slot`.
- [ ] **Step 2:** FileNotFoundError.
- [ ] **Step 3:** seeds:

```toml
# Rerank slot — llama-server --reranking in a podman container (hal0-slot@rerank).
name = "rerank"
type = "reranking"
device = "gpu-vulkan"
runtime = "container"
profile = "vulkan-std"
enabled = true
port = 8083

[model]
default = "bge-reranker-v2-m3-q4_k_m"
context_size = 4096

[server]
# llama-server reranking mode (serves /rerank + /v1/rerank).
extra_args = "--reranking"
```

```toml
# Utility LLM slot — vulkan llama-server container (hal0-slot@utility).
name = "utility"
type = "llm"
device = "gpu-vulkan"
runtime = "container"
profile = "vulkan-std"
enabled = true
port = 8081

[model]
default = "gemma-4-12b-it"
context_size = 65536
```

install.sh loop: `for seed_slot in npu tts rerank utility`. schema.py rerank_url default `http://127.0.0.1:8083` (docstring notes the rerank container slot + that hal0.toml overrides win).
- [ ] **Step 4:** `.venv/bin/python -m pytest tests/config -q` + `bash -n installer/install.sh`.
- [ ] **Step 5:** `git commit -s -m "feat(installer): seed rerank + utility container slots; rerank_url default to rerank slot"`

---

### Task C6: UI — profiles page CRUD

**Files:**
- Modify: `ui/src/api/hooks/useProfiles.ts` (+create/update/delete mutations), `ui/src/api/endpoints.ts`
- Modify: `ui/src/dash/profiles.jsx` (cards gain Edit/Delete/Clone; "New profile" button; form drawer/modal with name/image/flags/mtp/device_class; seeds render badges + disabled edit/delete with the clone affordance)
- Test: γ spec `ui/tests/e2e/profiles-crud-v3.spec.ts` (mirror profiles-page-v3.spec.ts harness; write-path via page.route interception per the suite's established pattern) + mock-data update (device_class on the 5 profiles)

- [ ] **Steps:** failing γ spec (create → card appears; seed shows immutable state; delete custom → gone; delete-in-use → error toast) → implement → `npx playwright test profiles 2>&1 | tail -3` + `npm run build` green → `git commit -s -m "feat(ui): profile CRUD on the profiles page"`.
- Keep forms consistent with the drawer's existing form-row classes; validation mirrors API (kebab-case name, non-empty image). Error toasts reuse the standard toast store. PROFILE_INTENT: add entries for flm-npu/kokoro-cpu while in there (B1 follow-up).

---

### Task C7: UI — drawer-editable profile + create-modal device derivation

**Files:**
- Modify: `ui/src/dash/slot-modals.jsx` (drawer ~707: readOnly input → select; create ~220: derive device)
- Test: γ spec `ui/tests/e2e/slot-drawer-profile-v3.spec.ts` + mock-data (chat slot etc. already container)

- [ ] **Steps:** failing γ spec first:
- drawer on a GPU container slot shows a profile `<select>` listing only `device_class=="gpu"` profiles; changing it + Save issues PUT `{profile}` then the restart mutation (reuse the B-phase toggle pattern: editMut → useSlotRestart); npu/tts slots show the profile as fixed text (no select) — filter `device_class` ≠ slot's class or the known fixed set.
- create modal: choosing a profile sets `device` from `DEVICE_DEFAULT_PROFILES` inverse — concretely: device sent = profile.device_class=="gpu" ? (profile name includes "vulkan" ? "gpu-vulkan" : "gpu-rocm") — NO. Cleaner: API slot view already returns profiles with device_class; create body device = `{"gpu":"gpu-rocm","npu":"npu","cpu":"cpu","img":"gpu-rocm"}[profile.device_class]`, EXCEPT vulkan-image profiles. **Decision:** add the explicit reverse map as a tiny UI constant `DEVICE_FOR_PROFILE_CLASS` and special-case vulkan-std → "gpu-vulkan" via profile name match is ugly — instead extend `ProfileConfig.device_class` granularity? NO scope creep: send `device_class=="gpu"` → keep a per-profile `device` chosen as: image tag contains "vulkan" → "gpu-vulkan" else "gpu-rocm", with a comment that Phase E may promote device to a profile field. Pin with a unit-style γ assertion for both vulkan-std and moe-rocmfp4 creation paths.
- Implement → `npx playwright test slot-drawer-profile profiles 2>&1 | tail -3` + `npm run build` → `git commit -s -m "feat(ui): drawer-editable profile + create-modal device derivation"`.

---

### Task C8: Gate, PR, CT105 deploy + e2e, #649 close

- [ ] **Step 1:** full gate (pytest --ignore=harness; ruff check/format src tests; ui build). Known env-dependent: hermes-provision docker test.
- [ ] **Step 2:** push `feat/phase-c-rerank-utility-profiles`, PR (`--head`), CI green, squash-merge. PR body MUST state: "#696 implemented here (both halves: state() per locked signature + is_ready_for_dispatch); #649 narrowed to proxy.py retirement = Phase E" (coordination comments already posted on #696 + #649 — do NOT close #649 unilaterally; the arch-review session coordinates it). Phase E guardrail (#695): when Tier-4 heuristics are deleted, the omni-router filter is the surviving classification site — never relocate the model-id prefix pins.
- [ ] **Step 3 (deploy, Tier 2/3):** `wip hal0 claim`; backups (`rerank.toml.bak-phase-c`, `utility.toml.bak-phase-c`); `git pull && scripts/deploy.sh`; migrate live rerank.toml + utility.toml to seed shapes (fix the blank `device=""`!); delete stale `/var/lib/hal0/slots/embed-rerank/` state + the embed-rerank drop-in dir (C3 only cleans dirs for slots being started — embed-rerank has no slot anymore, clean manually); update live `hal0.toml` `rerank_url = "http://127.0.0.1:8083"` (leave `rerank_enabled` as-is).
- [ ] **Step 3b — REGISTRY PRECHECK (final-review CRITICAL, before any load):** the GPU container path falls back to the BARE model id when the registry has no `path` (locked by `test_resolve_model_path_registry_miss_falls_back_to_bare_id`) → llama-server file-not-found → slot ERROR. Verify BOTH ids registry-resident with real GGUF paths: `bge-reranker-v2-m3-q4_k_m` (likely present — was the live embed-rerank model) and `gemma-4-12b-it` (NOT in curated/seeds — almost certainly must be registered: GGUF on disk is `/mnt/ai-models/gemma-4-12b-it/gemma-4-12b-it-UD-Q4_K_XL.gguf`). Check via `/api/models` or registry.toml; register missing ids through the registry API/CLI (NEVER hand-splice registry.toml — memory: malformed TOML triggers destructive auto-rescan). Only then `POST /api/slots/rerank/load` + `/api/slots/utility/load`.
- [ ] **Step 4 (e2e matrix → PR comment):**
  1. Both units active, containers up, vulkan image, `--reranking` in rerank argv
  2. `curl 127.0.0.1:8080/v1/rerankings -d '{"model":"rerank","query":"q","documents":["a","b"]}'` → 200 scores (gateway path-pin + rewrite)
  3. Native consumer path: `curl 127.0.0.1:8083/rerank -d '{...}'` → 200 (memory wrapper contract)
  4. `curl 127.0.0.1:8080/v1/chat/completions -d '{"model":"hal0/utility",...}'` → completion (virtual-name chain)
  5. Profile CRUD smoke via API: create → list → delete
  6. Drawer profile swap live: flip utility vulkan-std→moe-rocmfp4→back from the dashboard (or via PUT+restart), verify image change in `podman ps`
  7. `systemctl list-units 'hal0-slot@*'` — every defined slot containerized; lemond serves NOTHING (Phase E entry state confirmed)
- [ ] **Step 5:** close out — wip release, tracker, memory update (Phase E entry state), Phase D next.

---

## Self-review notes
- Spec coverage: rerank+utility containers ✓ (C4/C5/deploy), #485 rerank half ✓ (C4), profile CRUD D5 ✓ (C1/C2/C6), drawer profile D6 ✓ (C7), device defaults D7 ✓ (C1/C7), #694 ✓ (C3), #649 decision ✓ (C8).
- Open implementation reality-checks: llama-server vulkan-radv image supports `--reranking` (stale drop-in proves the OLD image did; C8 e2e proves the new one or we pin the old image in a `vulkan-rerank` profile fallback), the exact `_join_url` rewrite site (C4 step 1), and the create-modal device derivation compromise (C7 — flagged for Phase E promotion of device onto ProfileConfig).
- The C7 device-derivation is the weakest design point; acceptable because create-modal is the only consumer and Phase E revisits.
