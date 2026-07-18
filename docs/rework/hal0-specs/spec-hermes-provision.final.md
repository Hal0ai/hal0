I now have a complete picture from direct reading plus both agents. Here is the implementation-ready spec.

---

# P3-hermes — Slim the Hermes Provisioner + Hermes User/Permission Model
Implementation-ready spec. Verified against `rework/descar` @ `/home/mint/hal0`. Docs cross-checked and stale points flagged.

All line numbers are `src/hal0/agents/hermes_provision.py` unless noted. The file is **5314 lines**; the `PHASES` list has **18 entries** (agent chatter of "21" is wrong — the list at 5020-5069 has 18).

---

## PART 1 — CURRENT PROVISIONER MAP

### 1.1 The checkpoint / PhaseIO / output_of framework (5314-line skeleton)

| Element | Location | Role |
|---|---|---|
| `PhaseStatus` (StrEnum OK/FAIL/SKIP) | 63 | phase outcome |
| `PhaseResult` (status/details/hash/reason/`fatal`) | 81, `to_dict` 104 | what a phase returns |
| `BootstrapState` | 118 | in-memory mirror of `provision.json`; fields `hermes_home=/var/lib/hal0/.hermes` (135), `venv=/var/lib/hal0/venvs/hermes` (136), `agent_id=hermes` (137), `phases` dict (138) |
| `provision.json` persistence | `save` 165, `load` 173, `phase_done` 152 | resumable checkpoints; `_DEFAULT_STATE_ROOT` |
| `PhaseNeedError` | 4908 | raised on undeclared cross-phase read |
| `PhaseIO` (frozen dataclass) | 4912-4930 | the injectable IO seams: `http_get`, `fetch_slots`, `fetch_model_contexts`, `probe_mcp_server`, `mcp_memory_call`, `install_venv`, `read_env_probe`, `load_config`, `run` |
| `PhaseContext` (state/repair/io/`output_of`/adopt) | 4933-4963 | what each phase body sees; `output_of(name)` 4955 enforces `needs` |
| `Phase` (name/fn/`needs`/`needs_previous`/`always_run`) | 4966-4991 | one PHASES entry |
| `_validate_phase_graph` | 4994 | import-time ordering guard |
| `PHASES` list | 5020-5069 | the 18-phase pipeline |
| `run()` orchestrator (the phase loop) | 5147, loop body `for phase in PHASES:` 5193-5267 | run-all FAIL policy; `always_run`/`--repair`/`--skip-phase`/FATAL-abort handling |
| `bootstrap_cli()` CLI entry | 5273 → calls `run()` 5283 | POSIX exit code wrapper |

Note: the primitives are duplicated/split into `src/hal0/agents/provision_engine.py` (~446 lines) per the second research pass.

### 1.2 The 18 phases — file:line, what it does, classification

Legend: **(a)** genuine install · **(b)** defensive host-wrangling / ownership churn · **(c)** brain/memory-identity (→ move to API lifespan per §7.3) · **(d)** config/plugin wiring.

| # | Phase (fn line) | What it does | Class |
|---|---|---|---|
| 1 | `preflight` (481) | Python 3.11-3.13 resolvable (or uv fallback), daemon `/api/status` reachable, ≥4 GiB free, **write-probe of root-owned `$HERMES_HOME`** (532-548), **foreign-gateway scan** (562-589) | (a) core checks + **(b)** the EACCES home-probe & foreign-gateway scan |
| 2 | `install` (972) | build venv from `requirements.txt` (`ctx.io.install_venv` 1023), copy `hermes` wrapper (1035) + `hal0-hermes` back-compat symlink (1040), **dir-drop `hal0-memory` plugin** (1069-1082), remove legacy `hal0` provider plugin (1059) — all wrapped in **`_claim_hermes_home`** (1014) + **`_chown_tree_to_hal0(venv)`** (1088) | (a) venv+plugin **/** (b) claim + chown + foreign-wrapper backup |
| 3 | `env_probe` (1325) | snapshot `probes.env_report/gpu/npu/ai_models` → `$HERMES_HOME/env-<ts>.json` for context_link/config_write | (d) |
| 4 | `home_init` (1261) | `_claim_hermes_home` (1271), mkdir 9 standard subdirs (1286), **`_chown_tree_to_hal0(hermes_home)`** (1292) | (b) claim+chown / (a) mkdir |
| 5 | `install_artifacts` (4770) | write seed TOML `/etc/hal0/agents/hermes.toml`, driver env `/etc/hal0/agents/hermes.env`, `runtime.json` embed token; **`_chown_tree_to_hal0(runtime_path)`** (4814). Duplicates `AgentManager._write_seed` (manager.py:508) | (d) + (b) chown |
| 6 | `persona_seed` (2349) | `_personas.seed_default_personas` into `$HERMES_HOME/personas` + `active.txt` | **(c)** |
| 7 | `config_write` (1957) | `hermes config migrate` (1992) → build scalar overlay (`_build_config_overlay` 1514) → apply via **`hermes config set`** (`_apply_config_set` 1670) → deep-merge list-keys + `overrides.yaml` (`_merge_config_yaml_layers` 1700) → honcho.json routing | (d) — **the core keep step** |
| 8 | `mcp_wire` (2271) | probe the 2 hal0 MCP servers, record tool list; feeds config_write's *next* run via `needs_previous` cross-run edge (5038) | (d) |
| 9 | `context_link` (2521) | Jinja-render `SOUL.md` / `HERMES.md` / `AGENTS.md` (`hermes_templates/`), mirror bundled skills, symlink `HOST.md` | (d) |
| 10 | `namespace_register` (2854) | write Hermes identity card to `agents` memory dataset via MCP (search→delete→add); warn-as-OK | **(c)** memory identity publish |
| 11 | `brain_profile_seed` (3048) | write `hal0-brain` identity card to memory; warn-as-OK | **(c)** brain |
| 12 | `brain_profile_mcp_wire` (3211) | deep-merge hal0-admin+hal0-memory MCP + `memory.provider` into `profiles/hal0-brain/config.yaml` | **(c)** brain |
| 13 | `model_automap` (3876) | re-apply `model.*` + `model_aliases.*` via `config set` (post-bootstrap slot refresh) — **overlaps config_write** | (d) redundant |
| 14 | `voice_wire` (4287) | when STT/TTS slots ready, emit `STT_*`/`TTS_*` config+secrets; else SKIP | (d) |
| 15 | `ownership_reconcile` (4847, `always_run`) | **re-`_chown_tree_to_hal0(hermes_home)` + chmod `/var/lib/hal0/agents` 0711** — exists solely to undo the root:root `config.yaml` that phase 7 writes after phase 4's chown | **(b) pure churn** |
| 16 | `gateway_secrets_wire` (4137) | write `hermes-gateway.service.d/10-hal0-secrets.conf` drop-in + daemon-reload (`write_gateway_secrets_dropin` 4014) | (d) |
| 17 | `smoke_tests` (4541) | 6 diagnostic probes (wrapper/doctor/chat/memory/admin/HERMES.md) | (a) verification |
| 18 | `self_report` (4575, `needs=smoke_tests`) | write bootstrap-completion summary to agent private memory | **(c)** memory write |

### 1.3 Host-wrangling helper inventory (the (b) mass — all become dead)

- Foreign-gateway: `_detect_foreign_gateways` (410), `_pgrep_hermes_gateway` (395), `_systemctl_is_active` (379), `_user_from_systemd_dir` (367), consts `_USER_SYSTEMD_SCAN_GLOBS`/`GATEWAY_UNIT_NAME`/`GATEWAY_SYSTEMD_DROPIN_FILE`.
- Ownership: `_chown_tree_to_hal0` (867), `_resolve_user_ids` (858), `_HAL0_SERVICE_USER` (855), `AGENTS_DIR` (4844).
- Claim/adopt: `_home_is_foreign` (1113), `_parse_env_secrets` (1124), `_adopt_foreign_home` (1151), `_unclaimed_home_reason` (1196), `_claim_hermes_home` (1205), `mark_home_managed_if_owned` (1236), `_HAL0_MANAGED_MARKER` (1096), `_ADOPT_SECRET_PREFIXES` (1102), the `adopt` flag threaded through `PhaseContext`/`run`/`bootstrap_cli`.
- Wrapper capture: `_copy_wrapper` foreign-backup branch (930-945), `_is_hal0_managed_wrapper` (918), `_install_backcompat_symlink` (948), `_MANAGED_WRAPPER_MARKER` (915).

### 1.4 Perm model — the root-vs-hal0 conflict origin

- **Runtime is `User=hal0`**: `installer/systemd/hal0-agent@.service:31-32` (`User=hal0`/`Group=hal0`), `ProtectSystem=strict`, `ReadWritePaths=/etc/hal0 /var/lib/hal0 /var/log/hal0 /run/hal0` (97). Hermes-specific drop-in `hal0-agent@hermes.service.d/override.conf:17` pins `HERMES_HOME=/var/lib/hal0/.hermes`.
- **Provisioner runs as root**: invoked `sudo hal0 agent install hermes` → `_install_hermes` (cli/agent_commands.py:130) → `bootstrap_cli` (189). Root writes `config.yaml`, `runtime.json`, seed/env, personas as **root:root**.
- **The cycle**: `home_init` (phase 4) chowns the tree to hal0 → `config_write` (phase 7) then writes `config.yaml` **root:root** after that chown → the `User=hal0` unit can't read it (falls back to defaults / offline) → `ownership_reconcile` (phase 15, `always_run`) re-chowns. This fix-clobber-refix loop is documented verbatim in the phase's own header comment (4829-4839).
- **`install/perms.py` `OwnershipStore` is dead in apply**: `ownership_table` (83) declares `HERMES_HOME` as `hal0:hal0 0700` (176-182) and `/var/lib/hal0` `hal0:hal0 2775`, but the module docstring (100-105) states the non-root branch "was removed — hal0-api runs as root … no longer wired in." `plan`/`commit`/`audit_rows` exist but are exercised **only by `hal0 doctor perms`** (root-only, audit). Real chowns are the scattered imperative `chown hal0:hal0` in `installer/install.sh` (1639-1692) + `_chown_tree_to_hal0` in the provisioner.
- Runtime homes traversal needs `/var/lib/hal0/agents` `0711` (why `ownership_reconcile` also chmods it, 4861-4867).

---

## PART 2 — DELIVERABLES

### (a) Phases/code that DELETE once §7.2 perms fixed + brain extracted

**Dead purely from the perms fix (drop to `hal0` before config-writing phases → files born `hal0:hal0`):**
1. **`ownership_reconcile` phase** (4847-4880) + `AGENTS_DIR` (4844) — entire phase deletes. Its only job is undoing the root-clobber.
2. **`_chown_tree_to_hal0`** (867-907) + `_resolve_user_ids` (858) + `_HAL0_SERVICE_USER` — all 4 call sites gone (`install` 1088, `home_init` 1292, `install_artifacts` 4814, `ownership_reconcile` 4857).
3. **`home_init` shrinks** to `mkdir(parents, exist_ok)` of the standard subdirs only (drop the claim + chown, 1271 & 1292).
4. **`install_artifacts`** drops its chown (4814); artifacts are born `hal0:hal0`.
5. **`preflight`** drops the root-owned-home EACCES write-probe rationale (532-548 simplifies to a plain writability check under the `hal0` uid) — the "root-owned `$HERMES_HOME`" failure mode it guards against ceases to exist.

**Dead from removing the adopt/capture feature (host-wrangling, §7.4 "home adoption / foreign-gateway detection"):**
6. Foreign-gateway scan block in `preflight` (562-589) + `_detect_foreign_gateways` (410), `_pgrep_hermes_gateway` (395), `_systemctl_is_active` (379), `_user_from_systemd_dir` (367) + the 3 gateway consts.
7. The whole claim/adopt marker system: `_home_is_foreign` (1113), `_parse_env_secrets` (1124), `_adopt_foreign_home` (1151), `_unclaimed_home_reason` (1196), `_claim_hermes_home` (1205), `mark_home_managed_if_owned` (1236), `_HAL0_MANAGED_MARKER`, `_ADOPT_SECRET_PREFIXES`, and the `adopt` flag threaded through `PhaseContext`(4953)/`run`(5150)/`bootstrap_cli`(5276) + the CLI `--adopt`.
8. Wrapper-capture: `_copy_wrapper` backup branch (940-943), `_is_hal0_managed_wrapper` (918), `_MANAGED_WRAPPER_MARKER`, and the `hal0-hermes` back-compat symlink (`_install_backcompat_symlink` 948, call 1040).

**Extracted to `src/hal0/brain/` + hal0-api lifespan (§7.3):**
9. `persona_seed` (2349) brain/default-persona seeding, `brain_profile_seed` (3048), `brain_profile_mcp_wire` (3211) + `_build_brain_identity_card` (3015), `_brain_profile_config_path` (3181), `_build_brain_profile_mcp_servers` (3185), `_BRAIN_PROFILE_NAME`.
10. Memory-identity publishing — `namespace_register` (2854) + `_build_identity_card` (2742) and `self_report` (4575) — move to the lifespan "memory known-up" hook (§7.3: "identity-card publishing out of the installer into the API lifespan"). `tests/api/test_startup_persona_seed.py` already proves the lifespan can re-run `persona_seed`, so this seam exists.

**Dead from collapsing the resumable pipeline (§7.4 "checkpoint … framework disappears"):**
11. `BootstrapState`/`provision.json` (118-201), `PhaseIO`/`PhaseContext`/`Phase`/`PhaseNeedError`/`_validate_phase_graph` (4908-5017), `PHASES` (5020) + `PHASE_NAMES`/`context_for`, the `run()` loop (5147) with `needs`/`needs_previous`/`output_of`/`always_run`/FATAL-abort, `provision_engine.py`.
12. `model_automap` (3876) folds into the single config render (config_write already sets `model.*`+`model_aliases.*`); the "post-bootstrap slot refresh" becomes a runtime concern of `render_live_context` / the agent shim's `ExecStartPre render-context`, not an install phase.

**Kept-but-slimmed:** `config_write`, `context_link`, `voice_wire`, `gateway_secrets_wire`, `smoke_tests`, `install` (venv+plugin only), `preflight` (core checks only), `env_probe` (optional, feeds context render).

### (b) Target ~200-line idempotent installer design

Single module `hermes_provision.py`, one public `install_hermes(*, repair=False) -> InstallReport`, linear + idempotent (no checkpoints — idempotency comes from every step being a converging write, exactly as `hermes config set` already is). No `PhaseIO`/`PhaseContext`/`Phase`/`provision.json`.

```
def install_hermes(repair=False):
    1. preflight()            # ~25 lines
    2. venv = ensure_venv()   # ~25 lines
    3. plugins()              # ~15 lines  (dir-drop, §18)
    4. render_config(venv)    # ~60 lines  (the keep-core)
    5. render_context()       # ~25 lines  (Jinja SOUL/HERMES/AGENTS)
    6. drop_unit()            # ~15 lines
    7. gateway_secrets()      # ~15 lines
    8. return smoke()         # ~20 lines  (report-only)
```

Step-by-step:

1. **preflight** — resolve a 3.11-3.13 interpreter (`_resolve_supported_python` 708, keep) or uv fallback (`_uv_available` 631); assert daemon `/api/status`; assert `/var/lib/hal0` writable as the running user; ≥4 GiB free. Drop the foreign-gateway scan and the root-owned-home probe.

2. **resolve python → uv venv → install pinned SDK** — keep `_install_venv` (751) but simplify: `uv venv` then `uv pip install -r installer/agents/hermes/requirements.txt` (pin line: `hermes-agent[web]>=0.16.0,<1.0`, `HERMES_REQUIREMENTS` 797). Idempotent: skip when `venv/bin/hermes` exists unless `repair`. Copy the `hermes` wrapper (`installer/wrappers/hermes`) to `/usr/local/bin/hermes`, plain overwrite (no foreign backup, no back-compat symlink).

3. **install plugins (dir-drop, §18)** — keep `_copy_plugin_tree` (965) → `$HERMES_HOME/plugins/hal0-memory/`. **Do NOT pip-package** (plan §18 explicitly corrects §15.5/§15.6/§15.9 — there is no pip entry-point group for memory; keep the `_copy_plugin_tree` path). Canonicalize to one source copy (dedupe the 3 copies per §7.4/HP-1: `installer/agents/hermes/plugins/hal0-memory/` is the shipped one).

4. **render `config.yaml`** — keep the *current* config_write mechanism verbatim in spirit (it is already the right shape and this is the single most load-bearing keep):
   - `hermes config migrate` (`_ensure_hermes_config` 1649) — Hermes owns + schema-migrates its file.
   - scalar/nested-scalar overlay via `hermes config set` (`_build_config_overlay` 1514 → `_apply_config_set` 1670): `model.default=hal0/agent`, `model.provider=custom`, `model.base_url`, `providers.custom.*`, `model_aliases.<slot>.*`, `delegation.*`, `memory.provider`, `mcp_servers.<name>.*` (+`X-hal0-Agent`), `agent.*`, `display.*`, `auxiliary.*`.
   - list-keys + operator `overrides.yaml` deep-merge (`_merge_config_yaml_layers` 1700 / `_deep_merge` 1732).
   - **STALE-PROMPT CORRECTION:** the task brief and §7.4 say "render one config.yaml (Jinja, deep-merge overrides)". The Jinja `config.yaml.j2` was **already deleted** (config_write docstring 1960: "replaces the old whole-file Jinja render"); §18 gotcha confirms **never rewrite Hermes's config.yaml — apply keys via `hermes config set` only** (else `image_gen.provider`/`tts.provider` clobber on migrate). So the target is `migrate + config set + deep-merge`, NOT a Jinja whole-file render. Jinja stays only for the *context* files (step 5).
   - Fold `model_automap` and `voice_wire` in here (they are just more `config set` pairs on the same file).

5. **render context** — keep `context_link` Jinja renders (`SOUL.md`/`HERMES.md`/`AGENTS.md` from `hermes_templates/`) + skills mirror. Live STATE.md/HERMES.md re-render stays a runtime job (`render_live_context`, driven by `ExecStartPre=… render-context`).

6. **drop systemd unit** — ship `hal0-agent@.service` + `hal0-agent@hermes.service.d/override.conf` from `installer/systemd/` (install.sh:997-1015 already does the copy); the installer only `daemon-reload` + `enable --now`. Under §7.2 the unit is unchanged (already `User=hal0`).

7. **gateway secrets** — keep `gateway_secrets_wire`'s `write_gateway_secrets_dropin` (4014); the main `hermes-gateway.service` is generated by `hermes gateway install --system` orchestrated by the CLI (agent_commands.py `_install_hermes_gateway`), not this module.

8. **smoke** — keep `smoke_tests` (4541) as a report-only return value (no checkpoint).

Brain seeding, the hermes identity card, and self-report are **not** called here — they run from the hal0-api lifespan (§7.3).

### (c) Proper HERMES_HOME ownership (born-owned, not chowned)

Single declarative truth = **`install/perms.py` `OwnershipStore` with `service_user="hal0"`** (§7.2 "adopt OwnershipStore as the single declarative truth, default user `hal0`"). Target map (matches `ownership_table` rows once the flip is the default):

- `/usr/lib/hal0` (code) → `root:root 0755` ro.
- `/etc/hal0` → `hal0:hal0 2775` **setgid** (so daemon temp-file+rename works); mutable config files `hal0:hal0`; `agents/` `root:root 0755` read-only allow-list world (#843); `secrets/` `root:root 0600` (systemd reads `EnvironmentFile` as root before dropping).
- `/var/lib/hal0` → `hal0:hal0 2775`; **`HERMES_HOME=/var/lib/hal0/.hermes` → `hal0:hal0`** (perms.py:176-182), `agents/` `0711`.
- `/var/log/hal0` → `hal0:hal0 0755`.

Mechanism: **drop privileges to `hal0` before any config-writing step** (§7.2). Two clean options — (i) the installer re-execs itself as `hal0` after the root-only prelude (matching `installer/lib/run-as-hal0.sh`, which already `env -u HERMES_HOME` re-execs the wrapper as hal0), or (ii) create dirs with the setgid parent + write everything as the `hal0` service account. Either way every file is **born `hal0:hal0`**; nothing is chowned afterward. The genuinely-privileged residue (write `/etc/systemd/system/hal0-agent@*`, `daemon-reload`, iptables) goes behind one `sudo -n`/polkit helper (§7.2). `hal0-api` itself finishes the reverted flip to `User=hal0`.

Result: `_chown_tree_to_hal0`, `ownership_reconcile`, and the `UMask=0002` kludge are all dead; `hal0 doctor perms` becomes the audit that the born-owned state matches `ownership_table`.

### (d) Ordered removal plan + what stays

Land in this order (each step compiles + tests green before the next; DoD per plan §9):

1. **P3-perms prerequisite** — wire `OwnershipStore` into the apply path with default `service_user="hal0"`; run `plan/commit` from `provision --stage=system`; `hal0-api` → `User=hal0`; add the privileged-ops helper. (Blocks everything else — until files are born `hal0:hal0`, the chown phases can't be removed.)
2. **Extract brain (§7.3 / P3-brain)** — move `persona_seed`, `brain_profile_seed`, `brain_profile_mcp_wire`, `_build_brain_identity_card`, `namespace_register`+`_build_identity_card`, `self_report` into `src/hal0/brain/` invoked by the hal0-api lifespan. Repoint `tests/api/test_startup_persona_seed.py`.
3. **Delete ownership churn** — remove `ownership_reconcile` phase, `_chown_tree_to_hal0`, `_resolve_user_ids`; strip the chown calls from `install`/`home_init`/`install_artifacts`. Update/remove `tests/agents/test_hermes_provision_ownership.py`.
4. **Delete adopt/foreign-gateway/wrapper-capture** — remove the claim/adopt/marker set, foreign-gateway scan, wrapper backup + back-compat symlink, and the `adopt` flag chain + CLI `--adopt`. Update/remove `tests/agents/test_hermes_capture_adopt.py`.
5. **Collapse the pipeline** — replace `PHASES`/`run()`/`PhaseIO`/`PhaseContext`/`Phase`/`provision.json`/`provision_engine.py` with the linear `install_hermes()`; fold `model_automap`+`voice_wire` into config render; keep `config_write`/`context_link`/`gateway_secrets_wire`/`smoke_tests`/`install`/`preflight`/`env_probe` as functions. Rewrite `tests/agents/test_hermes_provision*.py` (phase-order/loop tests → linear-flow tests).

**What stays:** the Hermes agent itself (single bundled agent, §7.4 "keep Hermes"); the venv+`requirements.txt` pin; the `hal0-memory` dir-drop plugin (deduped to 1 copy); the `hermes config migrate` + `config set` + deep-merge config model; the Jinja context renders; the systemd units (`User=hal0`, unchanged); the gateway secrets drop-in; `upgrade_hermes_runtime` (801); `render_live_context` (3471, runtime). Core must keep working **without** Hermes (§16.1 standing rule) — nothing in `src/hal0/{api,brain,board}` may import this module.

### (e) Surface impacts

**Installer (`installer/`):**
- `install.sh:2143-2232` — the `hal0 agent install hermes` trigger, gateway install (2208-2227), and unit enable stay; no shape change (still one CLI call).
- `install.sh:1639-1692` — the imperative `chown hal0:hal0 …STATE.md/.cache/models` block is **subsumed by `OwnershipStore.commit`**; remove or reduce to the store call.
- `installer/agents/hermes-prereqs.sh` — still shelled by `_install_hermes` (agent_commands.py:178).
- `installer/agents/hermes/requirements.txt` — unchanged pin.
- `installer/lib/run-as-hal0.sh` — becomes the drop-to-hal0 mechanism (or is retired if the installer re-execs itself).
- Dedupe the 3 `hal0-memory` copies (`installer/agents/hermes/plugins/hal0-memory/`, `src/hal0/agents/hermes/plugins/memory_hindsight/`, the pi_coder TS copy) → 1 canonical (§7.4/HP-1).

**CLI (`src/hal0/cli/agent_commands.py`):**
- `_install_hermes` (130-226): step 4 still calls the new `install_hermes()` (replacing `bootstrap_cli` at 189); **remove** `_chown_hermes_trees_to_agent_user()` (199) and `--adopt` forwarding. Keep prereqs→install→unit-enable→gateway order.
- `bootstrap_hermes` (1009), `agent_reprovision` (1158), `agent_upgrade` (1110): repoint from `bootstrap_cli` to the new entry; drop `--skip-phase`/`--dry-run`/`--adopt` flags that keyed off the phase engine. `agent_status`/`agent_log` (1063/1092) read `provision.json` — either keep a minimal report file or drop these subcommands.
- `AgentManager._write_seed` (agents/manager.py:508) duplicates `install_artifacts` seed write — pick one owner (manager or installer) to avoid the two diverging.

**Systemd:** no unit change required (`hal0-agent@.service` already `User=hal0`; hermes drop-in already pins `HERMES_HOME`). `tests/systemd/test_unit_files.py` unaffected.

**Tests to rewrite/retire:** `test_hermes_provision.py`, `test_hermes_provision_context.py`, `test_hermes_provision_install_artifacts.py`, `test_hermes_provision_idempotency.py`, `test_hermes_provision_ownership.py` (delete), `test_hermes_capture_adopt.py` (delete), `test_hermes_env_seam.py`, `test_cli/test_agent_install_hermes.py`, `test_api/test_startup_persona_seed.py` (repoint to brain lifespan). Keep largely as-is: `test_hermes_upgrade.py`, `test_hermes_state_render.py`, `test_hermes_provision_honcho.py`, `test_hermes_provision_collect.py`, `test_hermes_live_resolve_render.py` (they test `_build_config_overlay`/`upgrade_hermes_runtime`/`render_live_context`, which survive).

---

### Key stale-doc flags for the implementer
1. **Config render is NOT Jinja.** Task brief + plan §7.4 say "render config.yaml (Jinja)"; the `config.yaml.j2` was deleted (1960) and §18 mandates `hermes config set` only. Jinja survives only for SOUL/HERMES/AGENTS context files.
2. **Plugins are dir-drop, NOT pip.** §18/§18.1 + tracker HP-1 explicitly correct the earlier §15.5/§15.6/§15.9 "pip package, monorepo, entry-point `hermes_agent.plugins`" framing and the `P3-hermes-mem` "standalone repo" note. Keep `_copy_plugin_tree`. Memory API is `prefetch()`/`system_prompt_block()`/`sync_turn()` — there is **no** `pre_llm_call` hook.
3. The `hal0` model-provider plugin is already removed (chat works via `provider: custom` + `base_url=…:8080/v1`); re-adding a `ProviderProfile` is optional (only pins the aux slot local).
4. `install_artifacts` seed write and `AgentManager._write_seed` are duplicate writers of `/etc/hal0/agents/hermes.toml` — reconcile ownership during the slim.