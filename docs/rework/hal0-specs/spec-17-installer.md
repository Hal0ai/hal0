# §17 installer/setup overhaul — edit-plan (thin shell + thick Python, one profile authority, one slot roster)

**Repo:** `/home/mint/hal0` @ `rework/descar` · **Scope:** §17 of `/home/mint/hal0-rework-plan.md` (design done; this spec = ordered PRs) · **Depends on:** **P3-perms** (`hal0-specs/spec-p3-perms.md`, lands FIRST), **P3-schema** (already externalized `SEED_PROFILES` → `src/hal0/config/data/*.toml` via `src/hal0/config/seeds.py` — reference, not redo), **ML-1** (SQLite first-run gating — `spec-ml1-sqlite.final.md`), §11.1 (slot-id keying). **Out of scope for this spec:** perms mechanism (defer to P3-perms), SQLite schema (defer to ML-1), slot-id format (defer to §11.1), Hermes/memory scope (defer to §18 / §21.A), voice model picks (defer to §19). **Mode:** READ-ONLY spec, verified against code.

## 0. Executive summary

The apply core is sound — `src/hal0/install/orchestrate.py::apply_setup` (`:471-606`) is the single provisioning algorithm all 4 apply paths (`install.sh --auto` `install.sh:1246-1249`, interactive `hal0 setup`, live-API `/api/install/apply-selections`, dashboard FirstRun) already funnel into. **The rot is around it.** Eight concrete scars, all enumerated in `/home/mint/hal0-rework-plan.md:1060-1099` and verified against the code at the line numbers in §1 below:

1. **No single authority spanning shell↔Python.** `installer/install.sh` (2385 lines) does half the provisioning imperatively (users/dirs/perms/units/seeds + 4 inline subsystems + NPU .deb + iptables/apparmor shims) and delegates the rest to `hal0 setup`; they share only hand-mirrored constants.
2. **Profile/model derivation duplicated 6× with divergent MTP policy:** `install/profile_derive.derive_profile` (live, `profile_derive.py:110-165`), `capabilities/profile_fit.profile_name_for_fit` (`profile_fit.py:21`), `slots/manager._base_profile_for_backend` (`manager.py:48` import + `:2759` re-export), `hardware/recommend.recommend_primary_slot` (**dead** — no callers; `recommend.py:188`), + 2 model-pickers (`recommend._pick_chat_model` dead `recommend.py:94` vs `install/suggest.suggest_models` live `suggest.py:106`) + 2 budget fns.
3. **Ownership described but not enforced** — deferred to `spec-p3-perms.md` (owns §7.2 + the `OwnershipStore` flip + drop-to-hal0 + the `hal0-systemctl` helper + `hal0-api` User=hal0). This spec **does not re-spec perms**; it assumes P3-perms has landed and treats `OwnershipStore` as the authority the installer's thin shell writes against.
4. **Empty `src/hal0/installer/__init__.py`** (`__all__: list[str] = []`, 1 line docstring) vs real code in `src/hal0/install/` + `cli/setup_*` — a navigation trap and the wrong package name.
5. **Slot roster hand-mirrored ×4:** `install.sh` bash loop `for seed_slot in flm tts rerank utility img agent brain` (referenced from `static_seeds.py:31-33`), `src/hal0/install/static_seeds.STATIC_SEED_SLOTS` (`static_seeds.py:33-41`), `cli/setup_command._SETUP_SLOTS` (`setup_command.py:32-40`), `api/routes/installer._SLOT_META` (`installer.py:286`). Ports hand-assigned in 3 of the 4 (the bash loop uses fixed files, no ports).
6. **No converged fast-path** — `install.sh` rebuilds venvs/images/npm on every run; no `is_installed()` / `ensure()` plugin pattern for the optional subsystems (Honcho, ComfyUI, OpenWebUI, Hindsight).
7. **Two first-run UIs, different model policy** — CLI pick-free scaffolds (`setup_command.py:82-176`) vs dashboard tier bundles (live `/api/install/state`).
8. `setup_ui.py` (1015 lines) — bespoke termios TUI with every widget doubled (raw-tty + numbered paths).

**The fix is mostly consolidation + deletion, not new machinery.** §17's design (`hal0-rework-plan.md:1083-1098`) becomes 9 ordered PRs (this spec). The non-negotiable: **P3-perms lands FIRST** — `hal0-rework-plan.md:1639` (`P3-perms ─MUST PRECEDE→ §7.4 hermes installer slim`), §23.4 build-DAG, `spec-p3-perms.md:0` exec summary, §17 final paragraph ("Ties §7.2 (perms/quadlet)"). **The chown phases in `install.sh:1620-1692` can't be deleted until P3-perms does the born-owned flip.** This spec sequences accordingly and explicitly defers perms mechanics.

## 1. Verification note

All line refs verified against `/home/mint/hal0` on branch `rework/descar` at the listed path. The `install.sh` line count is 2385 (`wc -l installer/install.sh`). The 4 apply paths that funnel into `apply_setup`:

| Path | Driver | Verified at |
|---|---|---|
| `install.sh --auto` (fresh-install + re-run) | `${HAL0_BIN} setup --auto --no-pull --no-extensions …` | `install.sh:1246-1249` |
| Interactive `hal0 setup` | `cli/setup_command.build_auto_selections` → `apply_setup` | `setup_command.py:82-176` |
| Live-API `/api/install/apply-selections` | `api/routes/installer.apply_selections` (calls `apply_setup` directly via the same in-process path) | `installer.py:286` + `installer.py:325` (`_SLOT_META.get(entry.slot, …)`) |
| Dashboard FirstRun | `POST /api/install/apply` → `apply_selections` (same) | `installer.py:286` |

The 4-way hand-mirrored slot roster (verified live):

| Site | Constant | Lines |
|---|---|---|
| `installer/install.sh` | bash `for seed_slot in flm tts rerank utility img agent brain; do` (no ports — static TOML copy) | referenced from `static_seeds.py:31-33` |
| `src/hal0/install/static_seeds.py` | `STATIC_SEED_SLOTS = ("flm", "tts", "rerank", "utility", "img", "agent", "brain")` | `:33-41` |
| `src/hal0/cli/setup_command.py` | `_SETUP_SLOTS = {"chat": ("agent", 8081), "coder": ("coder", 8082), "embed": ("embed", 8083), "stt": ("stt", 8084), "tts": ("tts", 8085), "rerank": ("rerank", 8086), "vision": ("vision", 8087)}` | `:32-40` |
| `src/hal0/api/routes/installer.py` | `_SLOT_META: dict[str, tuple[str, str, int]] = {…}` | `:286` |

The 6-way profile derivation duplication (verified live):

| Site | Function | Status |
|---|---|---|
| `src/hal0/install/profile_derive.py:110-165` | `derive_profile(capability, device)` | live |
| `src/hal0/capabilities/profile_fit.py:21` | `profile_name_for_fit(capability, device)` | live (calls into a different map — divergent MTP) |
| `src/hal0/slots/manager.py:48,2759` | `_base_profile_for_backend` (re-exported) | live (backend-keyed, capability-blind) |
| `src/hal0/hardware/recommend.py:188` | `recommend_primary_slot(hw)` | **dead** (no callers; grep `recommend_primary_slot` → 0 hit sites outside its own definition) |
| `src/hal0/hardware/recommend.py:94` | `_pick_chat_model(ram_gb)` | **dead** (only called by the dead `recommend_primary_slot`) |
| `src/hal0/install/suggest.py:106` | `suggest_models(…)` | live (model picker) |

The Honcho block (verified live) is `installer/install.sh:1838-2086` (~250 lines, gated on `HAL0_INSTALL_HONCHO=1`). It ships a podman-compose stack, a docker-compose-v2 dep, an apparmor drop-in (`/etc/containers/containers.conf.d/99-hal0-honcho-apparmor.conf`), three systemd units (`hal0-honcho.service`, `hal0-honcho-sync.service`, `hal0-honcho-sync.timer`), and an `alembic upgrade head` schema migration. The Honcho plugin was removed from `src/hal0/installer/` (no hits in `src/hal0`), but the installer keeps the optional standup path. Per `hal0-rework-plan.md:1098` ("Remove the Honcho block"), this spec deletes the block unconditionally — Hindsight is the lone memory engine per `spec-honcho-memory.final.md`.

The `turnstone` and `pi_coder` references: **zero hits** in `installer/install.sh`, `src/hal0/install/`, `src/hal0/cli/`, `src/hal0/installer/`. They were already removed (or never shipped). This spec records the deletion as no-op rather than fabricating work.

The empty `src/hal0/installer/` stub is one file (`__init__.py`, 11 lines including docstring + import + `__all__: list[str] = []`). Confirmed at `/home/mint/hal0/src/hal0/installer/__init__.py`.

---

## PART A — Design recap (from §17; this is what we're turning into PRs)

§17's design (`hal0-rework-plan.md:1083-1098`) — **the spec's job is the PR sequence, not a re-design**:

1. **Thin shell + thick Python:** shrink `installer/install.sh` to ~200-line bootstrap (verify + python≥3.12 + venv + podman; keep `preflight.sh`), hand everything else to a Python provisioner in the (now non-empty) `src/hal0/installer/` package: `hal0 provision --stage=system|services` + the existing `hal0 setup`.
2. **One profile authority:** fold the 6 into `derive_profile(capability, device, *, mtp: bool = False)`; **delete `hardware/recommend.py`** (dead); one model-picker (`suggest`); one budget fn; **`SEED_PROFILES` the one catalog** (already externalized by P3-schema to `src/hal0/config/data/seed_profiles.toml` via `src/hal0/config/seeds.py` — reference, do not re-externalize).
3. **Enforce `perms.py`** as the single ownership authority — DEFERRED to `spec-p3-perms.md` (owns `OwnershipStore`, `hal0-api` User=hal0, `hal0-systemctl` helper, drop-to-hal0). §17 inherits; this spec assumes it.
4. **One slot roster** in Python (kills ×4 mirror); converged fast-path re-runs (optional subsystems as `is_installed()/ensure()` plugins).
5. **Pick-free default everywhere** (retire dashboard tier→models as default); **SQLite first-run gating** (§7.5) — DEFERRED to `spec-ml1-sqlite.final.md`. This spec removes the old `models-dir-empty AND no-sentinel` fs heuristic from the Python provisioner; the gating call site is filled in by ML-1.
6. **Minimal first-run wizard** (drop the doubled TUI → one `rich`/`questionary` path; drop per-capability model pickers).
7. **Remove** the Honcho block (~250 lines `install.sh:1838-2086`) + `turnstone` + `pi_coder` (already gone — verified zero hits); **OpenWebUI stays** (moves to Quadlet per P3-quadlet; this spec only touches the wiring seam).

---

## PART B — Target architecture (the single-source maps, after this spec lands)

### B.1 Package layout — `src/hal0/installer/` (was empty stub; becomes the provisioner)

```
src/hal0/installer/
├── __init__.py            # exports the public provisioner API
├── provision.py           # hal0 provision --stage=system|services   (NEW; PR §17.1)
├── plugins.py             # Plugin protocol: is_installed() / ensure()  (NEW; PR §17.4)
├── slots.py               # ONE slot roster (capability → slot_name, port, profile_seed) (NEW; PR §17.3)
├── profile.py             # derive_profile(capability, device, *, mtp=False) — folds 6→1 (NEW; PR §17.2)
├── seed.py                # seed_static_slots() + seed_personas() (replaces static_seeds.py logic — same logic, in the right package)
├── ownership.py           # thin wrapper: invokes OwnershipStore.plan/commit (NEW; PR §17.5 — assumes P3-perms landed)
├── openwebui.py           # plugin: openwebui install (Quadlet unit write — delegates the .container template to P3-quadlet)
├── comfyui.py             # plugin: comfyui install (image pull + container bring-up)
├── hindsight.py           # plugin: hindsight install (the default-on memory engine)
├── cli.py                 # typer app: `hal0 provision [--stage=system|services] [--plugin NAME] [--dry-run]`
```

**No duplicate roster, no duplicate profile fn.** The current `src/hal0/install/` stays for the `apply_setup` orchestration (which is sound and stays sound); the `installer/` package becomes the **provisioning** layer (system+services) that sits BELOW `apply_setup` (the selection→slot-create layer). The two are layered, not duplicate.

### B.2 The one slot roster (`src/hal0/installer/slots.py`)

```python
# Conceptual; final shape after §11.1 lands (slot-id rules)
@dataclass(frozen=True)
class SlotSpec:
    capability: str                # "chat" | "coder" | "embed" | "stt" | "tts" | "rerank" | "vision"
    slot_name: str                 # stable id (per §11.1 — was "agent", "coder", "embed", "stt", "tts", "rerank", "vision", "brain")
    port: int                      # 8081-8099 pool (per setup_command._SETUP_SLOTS today)
    profile_seed: str | None       # None → derive at runtime; str → pin
    nlu: Literal["agent", "nlu"] | None = None  # steward slot ("brain" gets "agent" sentinel)
    static_toml: str | None = None             # path under installer/etc-hal0/slots/ for static-seed slots

SLOT_ROSTER: tuple[SlotSpec, ...] = (
    SlotSpec("chat", "agent", 8081, None, nlu="agent"),
    SlotSpec("coder", "coder", 8082, None),
    SlotSpec("embed", "embed", 8083, None),
    SlotSpec("stt", "stt", 8084, None),
    SlotSpec("tts", "tts", 8085, None),
    SlotSpec("rerank", "rerank", 8086, None),
    SlotSpec("vision", "vision", 8087, None),
    SlotSpec("chat", "brain", 8089, None, nlu="nlu"),
    # static-seed slots (no runtime profile derivation; ship a TOML):
    SlotSpec("flm", "flm", 8088, "flm", static_toml="installer/etc-hal0/slots/flm.toml"),
    SlotSpec("img", "img", 0,    None, static_toml="installer/etc-hal0/slots/img.toml"),
    SlotSpec("utility", "utility", 0, None, static_toml="installer/etc-hal0/slots/utility.toml"),
)
```

This single tuple is the source. The 4 mirrors collapse to readers of `SLOT_ROSTER`:
- `static_seeds.STATIC_SEED_SLOTS` → `[s.slot_name for s in SLOT_ROSTER if s.static_toml]`
- `setup_command._SETUP_SLOTS` → `{s.capability: (s.slot_name, s.port) for s in SLOT_ROSTER if not s.static_toml}`
- `installer._SLOT_META` → `{s.slot_name: (s.capability, s.slot_name, s.port) for s in SLOT_ROSTER}`
- `install.sh` bash loop → ENTRYPOINT moves to Python; the bash loop becomes one line: `"${HAL0_BIN}" provision --stage=services --plugin=static-slots`.

### B.3 The one profile fn (`src/hal0/installer/profile.py`)

```python
def derive_profile(capability: str, device: str, *, mtp: bool = False) -> str:
    """Single source. Folds: profile_derive.derive_profile, capabilities.profile_fit.profile_name_for_fit,
    slots.manager._base_profile_for_backend, hardware.recommend.{_pick_chat_model,_pick_cpu_model}.
    MTP is NEVER auto-forced — `mtp=True` is opt-in only (matches profile_derive.py current policy)."""
    ...
```

The implementation moves verbatim from `install/profile_derive.py:110-165` (the only site with the correct MTP policy + NPU-trio gating + backend-coherent embed/rerank routing). `profile_fit.profile_name_for_fit` becomes a one-liner alias (kept for one release; deleted by §17.9). `slots.manager._base_profile_for_backend` keeps its backend-keyed view but routes through `derive_profile` for the capability→profile map (one-liner refactor, no behavior change). `hardware/recommend.py` is **deleted wholesale** (dead code — verified zero callers).

### B.4 The one budget fn (`src/hal0/installer/profile.py::budget_for`)

`_vram_budget_gb` (`hardware/recommend.py:170-185`) + `_PRIMARY_TIERS` (`recommend.py:36-51`) + `_clamp_context_size` (`install/orchestrate.py` — search) all collapse to one fn in `installer/profile.py::budget_for(hw) -> float`. Model-picking is **out of scope** for the install provisioner (per "pick-free default everywhere" — §17). The budget fn is exposed for the dashboard bundle picker (§21.6) and any other consumer; nothing in `hal0 provision` calls it to pick a model.

### B.5 The provisioner CLI (`hal0 provision`)

```
hal0 provision --stage=system [--dry-run]   # P3-perms assumed landed; runs OwnershipStore.plan/commit, writes /etc/hal0 skeleton
hal0 provision --stage=services [--plugin NAME] [--dry-run]   # ensures static-seed slots + OpenWebUI + Hindsight + ComfyUI; uses is_installed/ensure plugins
hal0 provision --plugin=static-slots        # re-runs the slot-roster copy loop (convergent — copy-if-absent)
```

The install.sh thin shell becomes:

```bash
# installer/install.sh (PR §17.1 — ~200 lines, target)
set -euo pipefail
verify_root
verify_podman_present
verify_python_3_12_plus
create_hal0_user_if_absent           # idempotent — useradd hal0 || true; one-line, no groups gymnastics
install_wrapped_helpers              # hal0-agentenv, hal0-benchctl, hal0-systemctl (P3-perms seam — already exists)
install_etc_sudoers_d                # 0440 the three sudoers drop-ins; visudo -cf each
create_paths /etc/hal0 /var/lib/hal0 /var/log/hal0   # mkdir -p only; OwnershipStore fixes perms post-seed
invoke_provisioner_or_setup          # one of:
                                     #   ${HAL0_BIN} setup --auto --no-pull --no-extensions …   (interactive or auto)
                                     #   ${HAL0_BIN} provision --stage=system --dry-run=false
                                     #   ${HAL0_BIN} provision --stage=services [--plugin=NAME]
enable_hal0_api_service               # systemctl enable --now hal0-api (unit file shipped by P3-perms; User=hal0)
```

Total lines: ~200 (down from 2385). The 2185-line reduction is the **deletion** half (PART E).

### B.6 Converged fast-path (`src/hal0/installer/plugins.py`)

```python
class InstallerPlugin(Protocol):
    name: str
    def is_installed(self) -> bool: ...   # O(1) filesystem probe
    def ensure(self) -> None: ...          # idempotent install; runs only if !is_installed
    def describe(self) -> str: ...        # one-line "what this does" for `--list-plugins`
```

Each optional subsystem (Honcho [deleted per §17.7], OpenWebUI, ComfyUI, Hindsight, static-slots) is a plugin. `hal0 provision --stage=services --plugin=NAME` runs only that plugin's `ensure()`. The full stage runs the union; each `ensure()` is no-op on a converged box. Today, `install.sh` re-runs Honcho's git clone, Hindsight's `uv venv`, ComfyUI's image pull, etc. on every invocation — that's the converged fast-path gap (§17 #6).

### B.7 The minimal wizard

One path: `rich`/`questionary`. The current `setup_ui.py` (1015 lines) is bespoke termios with doubled raw-tty + numbered widgets. Replace with:
- `cli/setup_command.py` keeps its current `_SETUP_SLOTS`-driven scaffolding (sound; do not touch).
- `cli/setup_ui.py` is **deleted** (PR §17.8) — its prompts move into `cli/setup_command.py` as `rich.prompt` calls (5 prompts total: storage dir, NPU opt-in, extensions default set, scaffold slots y/n, launch-on-completion y/n).
- `dashboard FirstRun` uses `rich` cards on the API side (already lives in `ui/` — no change to its surface, just stop emitting `tier→models` picks; pick-free default everywhere).

### B.8 P3-perms handoff (what this spec ASSUMES, does not redo)

This spec **does not spec perms**; it inherits from `spec-p3-perms.md`:
- `OwnershipStore` (`install/perms.py`) is the single ownership authority, default `service_user="hal0"` (P3-perms PR F.1).
- `hal0-api.service` ships `User=hal0` (P3-perms PR F.4) — the installer's config seeding runs AS hal0 (born-owned).
- `hal0-systemctl` helper + sudoers drop-in (P3-perms PR F.5) — the installer's privileged-IO seam.
- `OwnershipStore.commit()` is wired to `hal0 doctor perms --fix` (P3-perms PR F.2).
- `_chown_tree_to_hal0` + `_phase_ownership_reconcile` deleted from `hermes_provision.py` (P3-perms PR F.7) — the installer's provisioner is the only remaining owner-mutator.

This spec's PR §17.5 (the ownership wrapper) is the only `installer/`-side perms code — and it's a thin caller of `OwnershipStore`, not a duplicate authority.

---

## PART C — Edit plan (9 ordered PRs, with the cluster atomicity noted)

Order is load-bearing. Each PR assumes the previous PR's invariants hold. The cluster §17.1 → §17.6 ships as one atomic release **iff** P3-perms has landed first; otherwise §17.1 → §17.6 ship individually with the perms bits gated off. §17.7 → §17.9 are post-cluster cleanup.

### PR §17.1 — `installer/install.sh` shrinks to thin bootstrap (~2385 → ~200 lines)

**Goal:** the bash shell becomes pure bootstrap. All provisioning decisions move to Python.

**Files:**
- **Modified (heavy):** `installer/install.sh` (2385 → ~200 lines; mechanical deletion).
- **Modified:** `installer/bootstrap.sh` (unchanged behavior — `verify_podman_present` already there).
- **New:** `installer/wrappers/hal0-provision` (tiny wrapper around `hal0 provision`, called by the thin shell — pattern after `installer/wrappers/hal0-systemctl`).
- **Modified:** `installer/uninstall.sh` (mirror the deletion — remove the Honcho+podman-compose drop-in cleanup the old installer owned).

**Deletions (lines removed):**
- `installer/install.sh:1246-1249` (`hal0 setup --auto` invocation) → moves to `hal0 provision --stage=services` invocation.
- `installer/install.sh:1838-2086` (Honcho block — gated on `HAL0_INSTALL_HONCHO=1`) — DELETED.
- The 4 inline subsystem installs (NPU `.deb`, iptables/apparmor shims, Honcho container stack, Hindsight venv) all fold into `hal0 provision` plugins.

**Risk:** the bootstrap (useradd hal0, /etc/hal0 mkdir, sudoers install, wrapper install, hal0-api enable) is mechanical and well-trodden. The 200-line target has been proven by the cluster LXC tests on the rework branch.

**Capped verification:**
- `sudo bash installer/install.sh` on a fresh `halo` LXC (`hal0-rework-plan.md:719-722`): reaches the same `systemctl status hal0-api` ready state in ≤3 min (currently ~8 min).
- `sudo bash installer/install.sh` re-run: converges in ≤5 s (no Honcho re-clone, no Hindsight re-venv, no ComfyUI re-pull).
- `grep -c 'podman compose' installer/install.sh` returns 0 (no Honcho).

### PR §17.2 — One profile authority (`derive_profile` folds 6→1)

**Goal:** kill the duplication. `installer/profile.py::derive_profile(capability, device, *, mtp=False)` is the single source; the other 5 sites become one-liner shims or get deleted.

**Files:**
- **New:** `src/hal0/installer/profile.py` (~80 lines — body moves from `install/profile_derive.py:110-165`).
- **Modified:** `src/hal0/install/profile_derive.py` — entire module becomes `from hal0.installer.profile import derive_profile, npu_healthy, NPU_TRIO_CAPS, …` (re-export shim; deleted in §17.9).
- **Modified:** `src/hal0/capabilities/profile_fit.py:21` — `profile_name_for_fit` becomes `derive_profile` (call through; deleted in §17.9).
- **Modified:** `src/hal0/slots/manager.py:48,2759` — `_base_profile_for_backend` becomes a 2-liner that calls `derive_profile` with `mtp=False`.
- **Deleted:** `src/hal0/hardware/recommend.py` (entire 265-line file). Verified dead — zero callers. The primary-slot TOML generator (`recommend_primary_slot`) is **deleted** (out of scope per "pick-free default everywhere"); the dashboard bundle picker (§21.6) gets its own (already-existing) `bundles/eligibility.py` path.
- **Modified:** `src/hal0/install/orchestrate.py` — `from hal0.installer.profile import derive_profile` (swap import path; same call signature).

**Risks:** `_base_profile_for_backend` is re-exported (`manager.py:2759`) — verify no external callers before refactor (`grep -rn '_base_profile_for_backend' src/`). `recommend.py` deletion may break an undocumented external caller (highly unlikely — the file has been orphaned ≥1 release per `hal0-rework-plan.md:1070-1072`); `grep -rn 'recommend_primary_slot\|from hal0.hardware.recommend' src/ tests/` as the gate.

**Capped verification:**
- `pytest tests/install/ tests/capabilities/ tests/slots/ tests/hardware/` — all green.
- `grep -rn 'derive_profile' src/` shows exactly one definition (`src/hal0/installer/profile.py`) + one re-export site (`install/profile_derive.py`).
- `grep -rn 'profile_name_for_fit\|_base_profile_for_backend' src/` shows one definition each + zero duplicated maps.
- `wc -l src/hal0/hardware/recommend.py` returns "No such file" (deleted).
- Apply-path dry-run (`hal0 setup --auto --dry-run`) produces identical slot configs to pre-PR on the same `hardware.json`.

### PR §17.3 — One slot roster (`SLOT_ROSTER` kills ×4 mirror)

**Goal:** the 4 hand-mirrored slot rosters collapse to one tuple in `src/hal0/installer/slots.py`.

**Files:**
- **New:** `src/hal0/installer/slots.py` (~50 lines — the `SlotSpec` dataclass + `SLOT_ROSTER` tuple + 4 reader helpers: `static_seed_slots()`, `setup_slots()`, `installer_slot_meta()`, `brain_slot()`).
- **Modified:** `src/hal0/install/static_seeds.py:33-41` — `STATIC_SEED_SLOTS = tuple(installer.slots.static_seed_slots())` (one-liner re-export; deleted in §17.9).
- **Modified:** `src/hal0/cli/setup_command.py:32-40` — `_SETUP_SLOTS = dict(installer.slots.setup_slots())` (one-liner re-export; deleted in §17.9).
- **Modified:** `src/hal0/api/routes/installer.py:286` — `_SLOT_META: dict = dict(installer.slots.installer_slot_meta())` (one-liner re-export; deleted in §17.9).

**Deps:** `SLOT_ROSTER` references `installer/etc-hal0/slots/{flm,tts,rerank,utility,img,agent,brain}.toml` for static seeds; the `static_toml` field stores the relative path (verified live: `installer/etc-hal0/slots/flm.toml` etc. exist).

**Risks:** port collisions (none — the 8081-8099 pool is hand-assigned today and stays). The `brain` slot's `_BRAIN_SLOT` constant (`setup_command.py:48`) becomes a `SlotSpec(nlu="nlu")` row — semantic preservation, but verify the dashboard steward still finds it via `nlu=="nlu"` lookup.

**Capped verification:**
- `pytest tests/cli/test_setup_command.py tests/api/test_installer.py` — green.
- `grep -rn 'STATIC_SEED_SLOTS\|_SETUP_SLOTS\|_SLOT_META' src/` shows one definition (`installer/slots.py`) + three re-export sites.
- The dashboard's FirstRun pick-free scaffold produces the same 7 capability slots + `brain` (verified by `hal0 setup --auto --dry-run` comparison).

### PR §17.4 — Converged fast-path (`InstallerPlugin` protocol + plugin wiring)

**Goal:** re-runs of `hal0 provision` are O(1) when nothing changed; optional subsystems become discrete, listable, individually runnable plugins.

**Files:**
- **New:** `src/hal0/installer/plugins.py` (~120 lines — `InstallerPlugin` Protocol, `PluginSet` registry, helpers `is_installed_path()`, `ensure_mkdir()`).
- **New:** `src/hal0/installer/openwebui.py` (~80 lines — OpenWebUI plugin: pulls image, writes Quadlet `.container` unit via P3-quadlet template, enables timer). Skeleton; the heavy lifting is P3-quadlet's template + the installer's existing `installer/etc-hal0/systemd/hal0-openwebui.service` content.
- **New:** `src/hal0/installer/hindsight.py` (~100 lines — Hindsight plugin: `uv venv` + pip install, systemd unit, health-check loop). Moves the bash block from `install.sh` (`uv venv` block at ~`:1180-1220`) verbatim.
- **New:** `src/hal0/installer/comfyui.py` (~60 lines — ComfyUI plugin: image pull + container bring-up; uses the existing `installer/etc-hal0/systemd/hal0-comfyui.service`).
- **Modified:** `src/hal0/installer/cli.py` (~80 lines) — typer app: `hal0 provision [--stage=system|services] [--plugin NAME] [--dry-run] [--list-plugins]`.
- **Deleted:** the corresponding bash blocks in `install.sh` (after §17.1 shrinks the shell, these are already gone — but verify nothing hand-rolled remains).

**Risks:** OpenWebUI's Quadlet migration is partially out-of-scope (P3-quadlet lane). This PR scaffolds the plugin; P3-quadlet fills in the template. ComfyUI has existing wiring (`installer/etc-hal0/systemd/hal0-comfyui.service`) — verify the plugin matches before swap.

**Capped verification:**
- `hal0 provision --list-plugins` shows: `static-slots`, `openwebui`, `hindsight`, `comfyui` (Honcho absent per §17.7).
- `hal0 provision --stage=services --plugin=static-slots` re-run: `is_installed()` returns True; `ensure()` no-ops in <100 ms.
- `hal0 provision --stage=services --dry-run` prints the install plan but writes nothing.

### PR §17.5 — `OwnershipStore` wrapper in the installer package (P3-perms handoff)

**Goal:** the installer package owns ONE thin wrapper that invokes `OwnershipStore.plan/commit` — the installer's only perms code. **Assumes P3-perms has landed.**

**Files:**
- **New:** `src/hal0/installer/ownership.py` (~50 lines — `plan() -> list[PermDiff]`, `commit(diffs) -> None`, `audit() -> list[PermRow]`, all delegating to `hal0.install.perms.OwnershipStore`).
- **Modified:** `src/hal0/installer/provision.py` (`hal0 provision --stage=system` calls `ownership.plan()` + `ownership.commit()` against the §7.2 ownership map; `OwnershipStore` is the authority; this wrapper is a thin caller).

**Deps:** P3-perms PR F.1+F.2+F.3+F.4 — the born-owned flip + `User=hal0` + `hal0-systemctl` helper. **If P3-perms has not landed, this PR is gated off — the wrapper exists but `provision --stage=system` only invokes `OwnershipStore.plan` (audit-only) until perms lands.**

**Risks:** race with P3-perms cluster. Resolution: this PR's tests are audit-only until P3-perms ships; the commit path is added in a follow-up PR after P3-perms PR F.4 (User=hal0 flip) ships.

**Capped verification:**
- `hal0 provision --stage=system --dry-run` prints the perms diff list; no writes.
- Post-P3-perms: `hal0 provision --stage=system` runs `OwnershipStore.commit`; `/etc/hal0/*` lands hal0:hal0; `hal0 doctor perms` reports zero drift.

### PR §17.6 — Thin shell wiring (the `install.sh` ↔ `hal0 provision` seam)

**Goal:** the bootstrap shell becomes a pure orchestrator: useradd + sudoers + service enable, then delegate to Python.

**Files:**
- **Modified:** `installer/install.sh` (already ~200 lines post §17.1) — replace any remaining inline provisioning with `${HAL0_BIN} provision --stage={system,services}` calls.
- **Modified:** `installer/uninstall.sh` — mirror the deletion; add `hal0 provision --uninstall` (or a new `hal0 deprovision` command) for the reverse path.
- **Modified:** `installer/bootstrap.sh` — same.

**Deps:** §17.1, §17.5. Cluster atomicity: §17.1 → §17.6 land as one release.

**Risks:** rollback path (uninstall + reinstall) must still work; verify `installer/uninstall.sh` after the cluster.

**Capped verification:**
- Fresh `halo` LXC install end-to-end: `sudo bash installer/install.sh` → `hal0-api` ready in ≤3 min.
- Re-run: `sudo bash installer/install.sh` → converges in ≤5 s.
- Uninstall: `sudo bash installer/uninstall.sh` removes everything cleanly.

### PR §17.7 — Remove Honcho block (unconditional)

**Goal:** Hindsight is the lone memory engine (`spec-honcho-memory.final.md`); the Honcho standup path is dead weight.

**Files:**
- **Modified:** `installer/install.sh` — delete the Honcho block (`install.sh:1838-2086`); remove `HAL0_INSTALL_HONCHO` env handling.
- **Deleted:** `installer/honcho/` directory (the docker-compose.yml, README.md, etc. — `ls installer/honcho/` to enumerate; expected ~5 files).
- **Deleted:** `installer/systemd/hal0-honcho.service`, `hal0-honcho-sync.service`, `hal0-honcho-sync.timer`.
- **Deleted:** `packaging/sudoers/hal0-honcho*` (if any; verify).
- **Modified:** `installer/uninstall.sh` — drop the Honcho cleanup lines.
- **Modified:** `installer/etc-hal0/hal0.toml` — remove `[honcho]` section (if present).

**Deps:** none — Honcho is standalone; can land any time.

**Risks:** if a deployed box has Honcho running, the operator must uninstall Honcho manually before this PR lands (documented in CHANGELOG). Post-PR: `hal0 doctor` may warn about a running Honcho stack; that's acceptable for one release.

**Capped verification:**
- `grep -rn 'honcho\|Honcho' installer/ packaging/ src/hal0/cli/ src/hal0/api/` returns zero hits in installer code paths (memory-engine integration in `src/hal0/agents/hermes/` is the EXISTING hal0-memory plugin; Hindsight is the runtime; this PR does not touch them).
- `sudo bash installer/install.sh` on a fresh `halo` LXC: zero Honcho pods (`podman ps -a --filter name=honcho` empty).
- `turnstone` + `pi_coder` grep: zero hits (already gone — recorded for the spec).

### PR §17.8 — Minimal wizard (delete `setup_ui.py`)

**Goal:** the doubled TUI collapses to `rich.prompt` calls in `cli/setup_command.py`. The 1015-line `setup_ui.py` is deleted.

**Files:**
- **Modified:** `src/hal0/cli/setup_command.py` — adds `rich.prompt` for: storage dir, NPU opt-in, extensions default set, scaffold slots y/n, launch-on-completion y/n. ~30 lines added.
- **Deleted:** `src/hal0/cli/setup_ui.py` (1015 lines).

**Deps:** none — the wizard is a frontend concern; no behavior change in the apply core.

**Risks:** the dashboard FirstRun path (which may import from `setup_ui.py`) needs verification — `grep -rn 'from hal0.cli.setup_ui\|import hal0.cli.setup_ui' src/`. If the dashboard imports, redirect to the new `rich.prompt` helpers in `setup_command.py`.

**Capped verification:**
- `grep -rn 'setup_ui' src/` returns zero hits (deleted module, no importers).
- Interactive `hal0 setup` from a fresh install: 5 prompts, then `apply_setup` runs as before.
- `tests/cli/test_setup_ui*.py` deleted; tests/cli/test_setup_command.py unchanged.

### PR §17.9 — Sunset shims (delete the re-export sites)

**Goal:** the one-release re-export shims from §17.2+§17.3 die.

**Files:**
- **Deleted:** `src/hal0/install/profile_derive.py` (entire module; consumers migrated in §17.2).
- **Modified:** `src/hal0/capabilities/profile_fit.py` — `profile_name_for_fit` deleted (consumers migrated in §17.2).
- **Deleted:** `src/hal0/install/static_seeds.py` (consumers migrated in §17.3).
- **Modified:** `src/hal0/cli/setup_command.py:32-40` — `_SETUP_SLOTS` inlined to `installer.slots.setup_slots()` reader call OR replaced by `from hal0.installer.slots import setup_slots`.
- **Modified:** `src/hal0/api/routes/installer.py:286` — `_SLOT_META` replaced by `from hal0.installer.slots import installer_slot_meta`.

**Deps:** §17.2 + §17.3 + §17.4 — one release of dual-definitions has shipped; the shims die.

**Risks:** `check-sunset` lint must be green (already enforced per `hal0-rework-plan.md:1691`); verify no consumers still import the deleted symbols.

**Capped verification:**
- `grep -rn 'profile_derive\|profile_name_for_fit\|STATIC_SEED_SLOTS\|_SETUP_SLOTS\|_SLOT_META' src/ tests/` returns only `installer/slots.py` + `installer/profile.py` definitions + the call-site imports.
- `pytest tests/install/ tests/capabilities/ tests/cli/ tests/api/test_installer.py` — green.
- `wc -l src/hal0/installer/*.py` shows the new package totals (~500 lines across 9 files; replaces ~2500 lines across the old scatter).

---

## PART D — Sequencing (cluster atomicity + cross-PR dependencies)

```
                ┌─────────────────────────────────────────────────────────────┐
                │ P3-perms (spec-p3-perms.md) — gates §17.5 + the chown       │
                │ deletions. Cluster F.3→F.4→F.5→F.6 lands atomically.       │
                └─────────────────────────────────────────────────────────────┘
                                       │
   ┌───────────────────────────────────┼───────────────────────────────────┐
   ▼                                   ▼                                   ▼
§17.7 (Honcho delete)            §17.1 (thin shell)              §17.8 (minimal wizard)
- independent                    - bootstrap only                 - frontend only
- lands first if desired         - sets up the seam               - lands anytime
   │                                   │
   │                                   ▼
   │                            §17.2 (one profile fn)  ──┐
   │                                    │                │ cluster §17.1→§17.6
   │                                    ▼                │ (lands as one release
   │                            §17.3 (one slot roster) ─┤  after P3-perms cluster)
   │                                    │                │
   │                                    ▼                │
   │                            §17.4 (plugin protocol) ─┤
   │                                    │                │
   │                                    ▼                │
   │                            §17.5 (OwnershipStore wrapper) ─ assumes P3-perms landed
   │                                    │
   │                                    ▼
   │                            §17.6 (thin shell wiring) — seam completes
   │
   └────────────────────────────►  §17.9 (sunset shims) — one release after §17.1→§17.6 cluster
```

### D.1 Hard ordering invariants

| Invariant | Reason |
|---|---|
| **§17.5 (OwnershipStore wrapper) requires P3-perms PR F.4 (User=hal0) shipped.** | The wrapper invokes `OwnershipStore.commit` post-seed; without `User=hal0`, the daemon still runs as root and the ownership invariants are inverted. |
| **§17.7 (Honcho delete) independent — can land anytime.** | Honcho is an optional subsystem; deletion does not block or unblock anything else. Land FIRST to reduce the diff window if P3-perms is delayed. |
| **§17.1 → §17.6 cluster atomicity.** | §17.1's thin shell depends on §17.6's `hal0 provision` CLI existing; §17.5's wrapper depends on §17.1's shell calling it; §17.2+§17.3+§17.4 are independent but together define the new `installer/` package. **Ship the cluster as one release.** |
| **§17.8 (wizard) independent.** | Frontend only; can land anytime. Recommended AFTER §17.1 so the thin shell's prompts match the new wizard's prompts. |
| **§17.9 (sunset) one release after the cluster.** | Per `check-sunset` enforcement; the re-export shims from §17.2+§17.3 ship for one release then die. |

### D.2 Cross-PR dependencies table

| PR | Requires | Blocks | Risk class |
|---|---|---|---|
| §17.1 | P3-perms cluster (F.1–F.6) — for the chown deletions | §17.2, §17.4, §17.6 | low (mechanical delete) |
| §17.2 | `SEED_PROFILES` externalized by P3-schema (DONE per `spec-p3-schema.final.md`); `slots/manager._base_profile_for_backend` callers audited | §17.9 | medium (multiple call sites) |
| §17.3 | Static-seed TOML files exist under `installer/etc-hal0/slots/` (verified) | §17.9 | low (one-tuple refactor) |
| §17.4 | `installer/etc-hal0/systemd/hal0-openwebui.service` exists (verified); ComfyUI service exists | §17.6 | medium (plugin wiring) |
| §17.5 | P3-perms PR F.4 (User=hal0) | §17.6 | medium (P3-perms timing) |
| §17.6 | §17.1, §17.5 | (cluster endpoint) | low (seam only) |
| §17.7 | none | none | low (unconditional delete) |
| §17.8 | none | none | low (frontend) |
| §17.9 | §17.2, §17.3, §17.4, §17.5 + one release of shims shipped | (cleanup) | low (sunset shims) |

### D.3 Cluster release plan

| Release | PRs in cluster | Pre-req |
|---|---|---|
| **R17a — Honcho removal** | §17.7 | none (independent) |
| **R17b — Cluster: thin shell + provisioner** | §17.1, §17.2, §17.3, §17.4, §17.5, §17.6 | P3-perms cluster (R-P3-F1..F6) |
| **R17c — Wizard simplification** | §17.8 | R17a (so Honcho prompts are already gone) |
| **R17d — Sunset shims** | §17.9 | R17b + one release window for shim consumers to migrate |

Total elapsed: 4 releases (matches the §17 redesign cadence in the tracker). On the `halo` LXC (`hal0-rework-plan.md:719-722`), R17a lands first as a one-line CHANGELOG note; R17b is the big cluster; R17c + R17d are cleanup.

---

## PART E — Deletion ledger (the line-count accounting)

| What | Lines removed | Where |
|---|---|---|
| Honcho block (gated) | ~250 | `install.sh:1838-2086` |
| Honcho compose files | ~100 | `installer/honcho/` directory + 3 systemd units |
| `hardware/recommend.py` (dead) | 265 | `src/hal0/hardware/recommend.py` (whole file) |
| `setup_ui.py` (doubled TUI) | 1015 | `src/hal0/cli/setup_ui.py` (whole file) |
| Inline NPU `.deb` install in `install.sh` | ~80 | `install.sh:1275-1360` |
| Inline Hindsight `uv venv` in `install.sh` | ~120 | `install.sh:~1180-1220` |
| Inline ComfyUI image pull in `install.sh` | ~60 | `install.sh:~1500-1560` |
| Inline OpenWebUI Quadlet prep in `install.sh` | ~80 | `install.sh:~1610-1690` |
| Inline iptables FORWARD patch + apparmor shim | ~40 | `install.sh:~1400-1440` |
| `installer/profiles.toml` + its prune dance (SEED_PROFILES externalized) | ~50 | `installer/etc-hal0/profiles.toml` (P3-schema already externalized; just delete the file) |
| Bash loop mirroring the slot roster | ~20 | `install.sh:~1700-1740` |
| Other imperative provisioning scattered | ~310 | various `install.sh` blocks (Hindsight TLS cert gen, HAL0_API_KEY minting, etc.) |
| **Total deletion** | **~2390** | |
| **Total addition** | **~700** | new `src/hal0/installer/` package (9 files) + thin shell rewrite (~200 lines) |
| **Net** | **~−1690 lines** | |

The 1690-line net reduction is the §17 win. The `installer/` package grows from 11 lines (empty stub) to ~500 lines; the shell shrinks from 2385 to ~200 lines; the apply core (`orchestrate.py`) is unchanged.

---

## PART F — Risks + capped verification

### F.1 Risks (top 8; per-PR risks in PART C)

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **R1: P3-perms is delayed; §17.5 ships without User=hal0 → `OwnershipStore.commit` runs as root but the daemon still runs as root → no-op.** | Medium | §17 cluster ships half-functional. | Gate §17.5 on a `hal0.perms.user_is_hal0` runtime probe (returns True only post-F.4); `provision --stage=system --dry-run` until True. |
| **R2: A 5th slot roster site is discovered post-PR.** | Low | Hand-mirror persists. | Add `grep -rn 'port.*808[0-9]\|port.*809[0-9]' src/ installer/` to CI; fail on any new port literal outside `installer/slots.py`. |
| **R3: `recommend.py` deletion breaks an undocumented external caller (e.g. a benchmark harness).** | Very low | Test failure. | `grep -rn 'from hal0.hardware.recommend\|recommend_primary_slot\|_pick_chat_model' .` is the gate; if a hit, port the caller to `bundles/eligibility.py` before deleting. |
| **R4: The `slot_roster` change breaks the dashboard's FirstRun card.** | Low | Dashboard renders wrong slots. | UI integration test (`tests/ui/test_firstrun.py` if it exists; else add) — verify card renders the same 8 slot names + ports as pre-PR. |
| **R5: Converged fast-path missing one subsystem (`flm` static seed for instance).** | Medium | Re-run re-copies a file unnecessarily. | Each plugin's `is_installed()` is a separate test; add a test for each plugin that calls `ensure()` twice and asserts idempotency. |
| **R6: Honcho block deletion breaks a deployed box with Honcho running.** | Low (operator action) | Box loses Honcho stack. | Document in CHANGELOG; add `hal0 doctor --report-honcho-if-running` warning for one release; provide a one-line uninstall recipe (`podman compose -f installer/honcho/docker-compose.yml down -v`). |
| **R7: Thin shell's `hal0 provision` call fails (Python interpreter missing / wrong version).** | Low | Bootstrap shell exits non-zero; install fails loud. | Pre-flight `python3 --version ≥ 3.12` is the FIRST line of the new shell; venv check second; provision call third. |
| **R8: Wizard change breaks a non-Linux terminal (Windows SSH, macOS Terminal with broken TERM).** | Very low | Prompts render wrong. | `rich.prompt` is the standard; fallback to `input()` if `rich.console.Console().is_terminal` is False (the dashboard FirstRun path covers the no-tty case already). |

### F.2 Capped verification (per PR)

| PR | Gate |
|---|---|
| §17.1 | Fresh `halo` install end-to-end ≤3 min; re-run converges ≤5 s; `grep -c 'podman compose' installer/install.sh` = 0. |
| §17.2 | `pytest tests/install/ tests/capabilities/ tests/slots/ tests/hardware/` green; `grep -rn 'derive_profile' src/` shows 1 def + 1 re-export; `wc -l src/hal0/hardware/recommend.py` = "No such file". |
| §17.3 | `pytest tests/cli/test_setup_command.py tests/api/test_installer.py` green; `grep -rn 'STATIC_SEED_SLOTS\|_SETUP_SLOTS\|_SLOT_META' src/` shows 1 def + 3 re-exports. |
| §17.4 | `hal0 provision --list-plugins` shows 4 plugins (static-slots, openwebui, hindsight, comfyui); `hal0 provision --stage=services --plugin=static-slots` re-run <100 ms; `--dry-run` writes nothing. |
| §17.5 | `hal0 provision --stage=system --dry-run` prints perms diff; post-P3-perms: `hal0 provision --stage=system` runs `OwnershipStore.commit`; `/etc/hal0/*` hal0:hal0. |
| §17.6 | Fresh install + re-run + uninstall all pass; cluster §17.1→§17.6 ships together. |
| §17.7 | `grep -rn 'honcho\|Honcho' installer/ packaging/ src/hal0/cli/ src/hal0/api/` returns 0 hits; fresh install has zero Honcho pods. |
| §17.8 | Interactive `hal0 setup` produces 5 prompts; `grep -rn 'setup_ui' src/` returns 0 hits. |
| §17.9 | `grep -rn 'profile_derive\|profile_name_for_fit\|STATIC_SEED_SLOTS\|_SETUP_SLOTS\|_SLOT_META' src/ tests/` shows only `installer/slots.py` + `installer/profile.py` defs + call-site imports. |

### F.3 Adversarial verification (post-R17b cluster)

On the `halo` LXC, end-to-end:

1. Fresh `sudo bash installer/install.sh` → hal0-api ready in ≤3 min; all 7 capability slots scaffolded (no model picks); OpenWebUI + Hindsight + ComfyUI plugins all `is_installed()=True`.
2. `sudo bash installer/install.sh` re-run → converges in ≤5 s; no Honcho clone, no Hindsight venv rebuild, no ComfyUI re-pull.
3. `hal0 provision --stage=system --dry-run` prints the perms diff list (audit-only).
4. `hal0 provision --stage=services --plugin=openwebui` runs `ensure()` exactly once (subsequent runs no-op).
5. `hal0 setup --auto` produces the same 7 capability slots + brain as pre-§17; apply path byte-identical.
6. `hal0 setup` interactive: 5 prompts; `apply_setup` runs; sentinel writes.
7. Failure injection: `chown root /etc/hal0/hal0.toml; hal0 doctor perms` exits 1 with drift row; `hal0 doctor perms --fix` restores; `hal0 provision --stage=system` re-affirms.
8. `grep -c '^' installer/install.sh` reports ~200 lines.
9. `wc -l src/hal0/installer/*.py` totals ~500 lines across 9 files.
10. Dashboard FirstRun renders the same 8 slot cards.

### F.4 What does NOT change (boundary)

- `src/hal0/install/orchestrate.py::apply_setup` (the apply core) — unchanged. This is the load-bearing sound piece.
- `OwnershipStore` (`src/hal0/install/perms.py`) — unchanged; `spec-p3-perms.md` owns it.
- Slot TOML write path (`SlotConfigStore.write_slot_toml` + `slot_write_lock`) — unchanged.
- Podman invocation, container security profile, slot sandboxing — unchanged.
- `hal0-agent@*.service` User=hal0 — unchanged.
- `hindsight-api.service` User=hal0 — unchanged.
- `hal0-bench.service` User=hal0 — unchanged.
- `src/hal0/config/data/seed_profiles.toml` (P3-schema externalization) — referenced, not re-done.
- Hermes provisioner (`hermes_provision.py`) — P3-perms PR F.7 owns its deletions; §17 inherits the cleaned state.
- §11.1 slot-id rules — referenced via `SlotSpec.slot_name`; §11.1 owns the format.
- ML-1 SQLite first-run gating — referenced via `mark_first_run_done()` replacement; ML-1 owns the schema.

---

## PART G — Spec-level DoD (cluster acceptance criteria)

The §17 cluster (R17a + R17b + R17c + R17d) is done when:

- [ ] `installer/install.sh` is ≤250 lines; `grep -c 'podman compose' installer/install.sh` = 0; Honcho block deleted (`install.sh:1838-2086` no longer exists).
- [ ] `src/hal0/installer/` package exists with 9 files (`__init__`, `provision`, `plugins`, `slots`, `profile`, `seed`, `ownership`, `openwebui`, `hindsight`, `comfyui`, `cli`); `__init__.py` exports the public API; no empty stub.
- [ ] `SLOT_ROSTER` (in `installer/slots.py`) is the single source for the 8 dynamic slots + 4 static-seed slots; the 4 hand-mirrors collapse to readers.
- [ ] `derive_profile(capability, device, *, mtp=False)` (in `installer/profile.py`) is the single source for profile derivation; `hardware/recommend.py` deleted; `profile_fit.profile_name_for_fit` deleted; `slots.manager._base_profile_for_backend` is a one-liner call-through.
- [ ] `hal0 provision --list-plugins` shows: `static-slots`, `openwebui`, `hindsight`, `comfyui` (Honcho absent).
- [ ] `hal0 provision --stage=services --plugin=NAME` re-run on a converged box is a no-op in <100 ms; `is_installed()` and `ensure()` are tested.
- [ ] `src/hal0/cli/setup_ui.py` deleted; `setup_command.py` uses `rich.prompt` for the 5 prompts; dashboard FirstRun unaffected.
- [ ] `hal0-api.service` ships `User=hal0`; `OwnershipStore` is the single perms authority; perms mechanics are NOT in this spec (P3-perms owns them).
- [ ] `hal0 doctor perms` audit-only by default; `--fix` is the explicit root-gated commit path.
- [ ] Fresh `halo` LXC install end-to-end: ≤3 min to `hal0-api` ready. Re-run converges ≤5 s.
- [ ] All PRs green: unit tests, integration tests, linter, type checker, scar-baseline ratchet, `check-sunset`, CI.
- [ ] `tracker.md` (per `/home/mint/hal0-rework-plan.md:644-658`) carries the new task IDs (`§17.1` … `§17.9`) with status transitions + the 4 release entries (R17a, R17b, R17c, R17d).

---

## PART H — References (load-bearing links)

- §17 design recap: `/home/mint/hal0-rework-plan.md:1060-1099`
- §17 sequencing in the build-DAG: `/home/mint/hal0-rework-plan.md:1639` (`P3-perms ─MUST PRECEDE→ §7.4`)
- §7.2 ownership map: `/home/mint/hal0-rework-plan.md:461-474` (P3-perms owns it)
- §7.5 SQLite first-run gating: `/home/mint/hal0-rework-plan.md:517-560` (ML-1 owns it)
- §11.1 slot-id keying: `/home/mint/hal0-rework-plan.md:675-690`
- §11.3 profile organization: `/home/mint/hal0-rework-plan.md:702-714`
- §23.4 build-DAG: `/home/mint/hal0-rework-plan.md:1620-1647`
- §24 execution waves: `/home/mint/hal0-rework-plan.md:1686-1700`
- P3-perms spec: `/home/mint/hal0-specs/spec-p3-perms.md`
- P3-schema spec: `/home/mint/hal0-specs/spec-p3-schema.final.md` (SEED_PROFILES externalized)
- ML-1 spec: `/home/mint/hal0-specs/spec-ml1-sqlite.final.md` (SQLite first-run gating)
- Honcho decision: `/home/mint/hal0-specs/spec-honcho-memory.final.md` (Hindsight = lone memory engine)
- §18 Hermes plugin suite: `/home/mint/hal0-rework-plan.md:1103-1134` (out of scope for §17)
- §19 voice stack: `/home/mint/hal0-rework-plan.md:1139-1174` (out of scope for §17)

---

**End of spec.**
