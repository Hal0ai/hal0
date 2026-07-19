# hal0 R5 live install-validation — findings (stamp r5v1)

**Ref under test:** `471c365a07eda87c643e70c96ce99f2b3c9c3df6` (rework/descar tip; pyproject version `0.9.8`)
**Mechanism:** committed tree shipped from mint via `git archive` → extracted on box → `HAL0_INSTALL_SKIP_VERIFY=1 bash installer/install.sh` (the `fresh-test-ct.sh --from-tree` path). Installer installs from a local checkout — it does NOT clone/pull, and neither box's on-box checkouts could reach 471c365a (local bundle `hal0-c98.bundle` @ `c98a7bc3`; `/root/hal0-descar` is a non-git tree).
**Date:** 2026-07-19. **Operator:** live-install-validation agent (from mint).
**Transcripts:** `/tmp/hal0-deploy/143-transcript-r5v1.log`, `/tmp/hal0-deploy/150-transcript-r5v1.log` (every cmd + full output + exit + UTC stamp).

---

## Per-box install outcome

| Box | Substrate | Install result | Decisive exit |
|-----|-----------|----------------|---------------|
| **150** | Ubuntu 24.04 · podman 4.9.3 · py3.12 · **privileged** LXC · kernel 7.0.6-2-pve | **SUCCESS** (141s, 13/13 steps) | `INSTALL_EXIT=0` |
| **143** | Ubuntu 26.04 · podman 5.7.0 · py3.14 · **unprivileged** LXC · kernel 7.0.6-2-pve | **BLOCKED at preflight** (step 1/13) | `INSTALL_EXIT=1` |

### 150 phase timing
- Total install: **141s** wall (background/detached, streamed to `/root/hal0-install-r5v1.log`).
- Preflight → python → venv → node → UI build → config → units → service start all clean; final `/dev/tty` hint line is post-success cosmetic.

### 143 phase timing
- Died in **step 1/13 (Pre-flight checks)** at the container-runtime gate — no later phase reached.

---

## ⚠ TOP SCOPE DEVIATION (read first)
**Neither box was fresh.** Orchestrator brief said "Both containers currently have NO hal0 installed (fresh install)." Reality:
- **143:** hal0 `0.9.8` fully installed & serving (API active), `qtest` slot unit generated, `hal0` user 999:988, two on-box checkouts.
- **150:** hal0 `0.9.8` installed (FHS `/usr/lib/hal0/current → hal0-0.9.8`, CLI symlinked), `hal0-slot@agent` in FAILED state pre-run.

The R4-stage runbook itself assumes an existing install (Phase 0 records `hal0 --version` as rollback ref; Phase 1 is `hal0 update`), so reality matches the runbook, not the brief. Validation was therefore an **upgrade/reinstall-in-place to 471c365a**, not a bare-metal fresh install. Non-destructive throughout; no data wiped.

---

## Findings (severity-ranked)

### BLOCKER
**B1 — [143] Installer preflight hard-fails: `podman run` keyring EDQUOT (box env).**
- Repro (verbatim): `podman run --rm quay.io/podman/hello` →
  `Error: OCI runtime error: crun: create keyring '…': Disk quota exceeded` (exit 126). Same with a local image (`ubuntu:24.04 true`) → isolates to the **runtime keyring**, not image pull (quay reachable, http 200).
- Root cause: `/proc/key-users` shows **uid 0 = 19999/20000 bytes** (`kernel.keys.maxbytes=20000`) — root's kernel keyring byte-quota is exhausted (leaked/accumulated session keyrings, likely from the many repeated **failed podman healthcheck** units on this box). crun cannot create a new session keyring → EDQUOT.
- Installer behavior is **correct** to block (a fresh slot could not start either); expected vs actual match for the gate. This is a **box-environment** condition, not a hal0 code defect.
- Box-specific: 143 only. **NOT remediated** (release-gate discipline — no fix-in-place). Operator remedy: free root keyring (reboot the CT, or `keyctl clear @s`/kill leaked holders) or raise `kernel.keys.maxbytes`, then re-run install.

### MAJOR
**M1 — [process] Brief/reality mismatch: boxes not fresh.** See TOP DEVIATION above. Changes the nature of the validation (upgrade vs fresh). Recorded so the orchestrator doesn't read "fresh install validated."

**M2 — [143 · installer] Container-runtime gate gives a misleading remedy for the keyring case.** On the EDQUOT failure the gate prints *"inside an unprivileged Proxmox/LXC container this needs 'features: nesting=1' (and often keyctl=1)"* — but 143's LXC config already has `features: nesting=1,fuse=1,keyctl=1,mknod=1`. The true cause (keyring **byte-quota exhaustion**) is never surfaced. `preflight_container_runtime`/`_smoke` should distinguish EDQUOT-on-keyring from a missing-keyctl config and emit the quota remedy. (installer/lib/preflight.sh smoke probe ~L474-492.)

**M3 — [143 · GPU gate] Render-node gid/name collision false-passes the GPU gate.** `/dev/dri/renderD128` is gid **993**, which on 143 maps to group **`clock`** (real `render` group is gid **991**; `hal0` is a member of 991+44, NOT 993). `preflight_gpu` only checks the gid maps to *a* group (line ~703), so it reports `renderD128 → group clock (gid 993)` and PASSES — hal0-user GPU access to the render node would actually be denied. Host `dev0: /dev/dri/renderD128,gid=993` is the wrong in-container gid for 143. On 150 the identical `gid=993` correctly maps to `render`, so the gate is right there. Gate should validate the gid maps to the **render** group specifically (or that hal0 is a member), not just any named group.

### MINOR
**m1 — [150] `doctor perms` reports Hermes ownership drift right after install.** Trailing line: `✗ Hermes ownership drift — run sudo hal0 agent bootstrap hermes --repair`. A just-provisioned box should not report ownership drift (O3-class). Everything else in `doctor perms` is `ok`.

**m2 — [150 · renderer, present in 471c365a] Slot quadlet emits `StartLimitIntervalSec` in `[Service]`.** systemd generator logs `Unknown key name 'StartLimitIntervalSec' in section 'Service', ignoring` for `hal0-slot@agent`/`@brain`. The key belongs in `[Unit]`; in `[Service]` it is silently dropped, so the intended start-rate-limit is not applied. Confirmed still present on the new 471c365a render (`grep -c StartLimitIntervalSec /run/systemd/generator/hal0-slot@*.service` = 1 each).

**m3 — [150] Three anonymous `podman healthcheck run …` units in FAILED state.** Correspond to unhealthy slot/openwebui container healthchecks (systemd stays `degraded`). Pre-existing and post-install.

**m4 — [150] `hal0 agent status hermes --json` emits empty stdout.** The non-json table renders fine; `--json` produces nothing (broken/unsupported flag).

**m5 — [150 · PLAUSIBLE] Phase-3 convergence: `config_write`/`context_link`/`env_probe` report `changed:true` after run-2.** CLI top-level output of both `hal0 agent install hermes` runs was idempotent ("already present"), and all structural markers/keys converged (see Phase 3 GREEN below), but `hal0 agent status hermes` shows those three phases `changed:true` with timestamps just after run-2. Either a genuine non-convergent rewrite or a status-call-triggered re-probe — needs owner confirmation. (The runbook's central run-2-mutates-nothing assertion; brain/persona are exempt but these three are not.)

**m6 — [150] `hal0 status` shows `brain` slot in `error` (rocm) and `agent` `warming`, while `doctor all` reports "Runners 9/9 healthy".** Status-vs-doctor inconsistency; brain(rocm) slot error may be image/backend-specific, not install-caused.

### COSMETIC
- **c1 — [both]** `LC_ALL: cannot change locale (en_US.UTF-8)` warning on every shell (known/pre-existing; note-only).
- **c2 — [150]** `installer/install.sh: line 2376: /dev/tty: No such device or address` when run detached/non-tty (final `hal0 setup` hint). Non-fatal; exit 0.
- **c3 — [150]** Phase-4 `[brain_chat] read_only = false` because the installer correctly preserves the **pre-existing** hal0.toml — the shipped `read_only=true` default is masked on an upgrade-over-existing box (expected; deploy note, not a bug).

---

## Runbook (both-boxes-runbook-r4-stage.md) results — 150

| Phase | Result | Notes |
|-------|--------|-------|
| 0 Preflight/baseline | ✔ | version 0.9.8, podman 4.9.3 recorded |
| 1 Deploy + health | ✔ | install exit 0; `/api/health`=200 `{"status":"ok","version":"0.9.8"}`; `doctor all` clean (1 expected WARN: model-layout migration 1303 links, O4) |
| 2 O12 rootful seam | ✔ **GREEN** | `podman_context:"rootful"`; `hal0-podman-ro` 0755 root:root + `sudoers.d/hal0-podman-ro` 0440 parsed OK; `sudo -u hal0 sudo -n hal0-podman-ro images` lists rootful store |
| 3 install_hermes convergence | ✔ structural / ⚠ m5 | both runs exit 0; `.hal0-managed` ✓, `plugins/hal0-memory/` ✓, `plugins/model-providers/hal0/` (NEW) ✓, `scratch` hal0:hal0 ✓, `API_SERVER_KEY` count=1 (not rotated) ✓, `--adopt` absent ✓; but status shows changed:true on run-2 for config_write/context_link/env_probe (m5) |
| 4 Read-only steward | n/a | config pre-existing `read_only=false` (c3); shipped default not observable on upgrade box |
| 5 Hermes plugin liveness | not exercised | requires live chat/dashboard; provider+memory plugin trees present on disk (Phase 3) |
| 6 HP-executor first contact | not exercised | requires `HERMES_DASHBOARD_BASE_URL` toggle + board dispatch |
| 7 Slot regression / uniform render | ✔ **GREEN** | `hal0-slot@agent.container`: `PodmanArgs=--group-add 993 --group-add 44 --security-opt apparmor=unconfined --security-opt seccomp=unconfined`; zero bare `AutoRemove=`/`GroupAdd=`/`SecurityOpt=` keys (matches O11 uniform render) |
| 8 Uninstall gate | **deferred** | not executed — chose to preserve the live 10-slot reference box (non-destructive discipline). Structural gate (`.hal0-managed` marker) verified in Phase 3 |

Additional 150 checks: hal0-api/openwebui/hermes-gateway/hal0.target **active + enabled** (reboot-autostart ready); pre-existing `hal0-slot@agent` FAILED state **cleared** post-install (now active/running).

### 143 runbook results
All phases **N/A** — install blocked at preflight (B1). Env/preflight only: OS/podman/py recorded, GPU gid collision (M3) recorded, keyring blocker (B1) recorded.

---

## 143 vs 150 differences
| Dim | 143 | 150 |
|-----|-----|-----|
| Distro | Ubuntu 26.04 | Ubuntu 24.04 |
| podman | 5.7.0 | 4.9.3 |
| python | 3.14.4 | 3.12.3 |
| LXC | unprivileged | privileged |
| renderD128 gid→group | 993 → **clock** (mis-map; render=991) | 993 → **render** (correct) |
| `podman run` | **fails** (keyring EDQUOT) | works (exit 0) |
| Install | **BLOCKED (exit 1)** | **SUCCESS (exit 0, 141s)** |
| systemd | degraded (podman healthcheck + postfix) | degraded (3 podman healthcheck) |

---

## 143 RE-RUN (post-keyring-reboot) — stamp r5v2

**Context:** CT 143 rebooted → root kernel keyring cleared (uid0 **2079/20000** bytes, was 19999/20000). Same ref 471c365a, same mechanism, 100% recorded → `/tmp/hal0-deploy/143-transcript-r5v2.log`.

| Q | Result |
|---|--------|
| **B1 cleared?** | **YES.** `podman run --rm quay.io/podman/hello` → exit 0. Keyring quota healthy. |
| **Install completes on podman-5.7/unprivileged?** | **YES — SUCCESS, `INSTALL_EXIT=0`, 22s** (fast: venv/UI cached, no rebuild). |
| **Post-install verification** | **GREEN.** `doctor all` PASS (11/11 slots healthy, Hindsight 2 banks, OpenWebUI ok, Hermes active, hal0.target enabled, 11 ports bound; only WARN = model-layout migration 1303/O4). `/api/health`=200. **O12 `podman_context:"rootful"`** + seam works (lists rootful store). Units active+enabled (autostart ready). Only failed unit = `postfix` (unrelated; no hal0 unit failed — cleaner than 150). Phase 3 hermes convergence GREEN (both runs exit 0; `.hal0-managed` + hal0-memory + model-providers/hal0 plugin trees + scratch hal0:hal0; `API_SERVER_KEY` count=1 not rotated; units active). Phase 7 render: `PodmanArgs=--group-add 991 --group-add 44 --security-opt …`, **0 bare AutoRemove/GroupAdd/SecurityOpt keys**. |

### M3 GPU — CONFIRMED, with runtime truth (143 only)
- **Gate still false-passes:** install log shows `OK gpu: /dev/dri/renderD128 → group clock (gid 993)` and `added hal0 to groups: render,video` (991+44, NOT 993).
- **Renderer emits the WRONG gid:** qtest slot render group-adds **991** (the group *named* "render") + 44 — but `/dev/dri/renderD128` is owned by **gid 993** (group "clock"). `hal0` (groups 44,991) **cannot** read renderD128.
- **Root cause (code):** `providers/_gpu.py:resolve_gpu_group_ids()` resolves the render gid by **group name** (`grp.getgrnam("render")`→991), not from the device node's actual owner gid (993). Its constant fallback is `render=993` (which would be *correct* here), but path #1 (name match) wins and returns 991. Fix direction: derive `--group-add` from `stat` of the device node, not the group name.
- **Runtime impact = NOT broken here, but fragile/latent:** renderD128 owner=**root(0)** perms 660; slots run **rootful, no `User=` → container process is root**, so root gets device access via **owner** perms (group-add irrelevant for root). `hal0-slot-qtest` = **Up, healthy** post-install. So GPU inference works on 143 *only because* of the rootful-root + root-owned-device coincidence. It would break on any non-root/rootless slot. Plus hal0-**user** GPU probes (doctor/hardware.json) silently misreport (can't open the device). The gate's stated purpose — prevent "silently CPU-only" installs — is defeated on this box.

### NEW on 5.7/unprivileged vs 150's 4.9.3/privileged
- **Render is NOT byte-identical across substrates:** 143 render carries **`Network=host`** (line 11) — the host-net lane for 143's unprivileged bridge-netns teardown; 150's render has no `Network=` key. Group-add/security-opt shape is otherwise identical. (Expected per runbook Phase 7's 143 host-net exception; recorded so "uniform render" isn't over-claimed.)
- **Install far faster (22s vs 141s):** everything cached from the prior partial run + existing 0.9.8 tree.
- **Cleaner systemd:** post-reboot 143 has no failed podman-healthcheck units (only `postfix`); 150 still shows 3 failed anonymous healthcheck units.
- **m1 (Hermes ownership drift) reproduces on 143** too — `doctor perms` trailing `✗ Hermes ownership drift`.

### 143 re-run verdict
**471c365a install is now VALIDATED on the podman-5.7 / unprivileged substrate** — clean install (exit 0, 22s), all core phases green (O12 rootful seam, uniform PodmanArgs render + host-net, hermes convergence, health 200, autostart). B1 was purely the box's exhausted keyring quota (reboot fixed it), not a code defect. **M3 remains an open code bug** (wrong render-gid resolution on gid/name-divergent boxes) — currently masked by rootful-root device access, but real and substrate-fragile; fix `resolve_gpu_group_ids` to key off the device node's owner gid.

---

## DEPLOY-WINDOW SAFE PASS — stamp r5v3 (R5 on main = f2db4d64)

Read-only / non-destructive only. NO migration apply, NO live-data mutation, lxc105 untouched. Recorded → `/tmp/hal0-deploy/deploy-window-r5v3-143.log`, `…-150.log`.

### Step 1 — flags-migrator dry-run (`hal0.config.migrations.slot_flags_fold`) — WROTE NOTHING
No CLI subcommand exists; invoked programmatically via the box venv: `collect_inputs()` + `plan_slot_flags_fold()` (pure read) for the full plan, plus `run_migration(dry_run=True)` (deploy_window NOT set) for the official report. NOTE: `apply_fold_plan` raises on refusals **even in dry-run** (refusal check precedes the dry-run branch), so `run_migration(dry_run=True)` raises `RuntimeError` when refusals exist — that IS the intended gate.

**BOTH boxes hit a DIVERGENT-SHARE REFUSAL → the apply is GATED on both. 0 folds available on either.**

- **143 — REFUSE** (folds=0, refusals=1, skipped=0, ok=False): model **`qwen3.5-0.8b`** shared by 3 slots with divergent tunes:
  - `brain`: extra_args=`-fa on -dev ROCm0 -b 512 -ub 512 --parallel 1 --threads 16 --no-mmap --metrics --no-webui --ctx-checkpoints 0 --checkpoint-every-n-tokens -1`, n_gpu_layers=999, **context_size=32000**
  - `qtest`: extra_args=None, n_gpu_layers=None, **context_size=4096**
  - `smoke-test`: extra_args=None, n_gpu_layers=None, **context_size=8192**
  - → divergent context_size (32000/4096/8192) + divergent extra_args. `run_migration` raised `RuntimeError` (refuses 1 model).
- **150 — REFUSE** (folds=0, refusals=1, skipped=0, ok=False): model **`hal0-brain-fpx8-agent`** shared by 2 slots:
  - `agent`: extra_args=`-fa on`, n_gpu_layers=999, context_size=64000
  - `brain`: extra_args=`-fa on -dev ROCm0 -b 512 -ub 512 --parallel 1 --threads 16 --no-mmap --metrics --no-webui --ctx-checkpoints 0 --checkpoint-every-n-tokens -1`, n_gpu_layers=999, context_size=64000
  - → same context_size (64000) but **divergent extra_args**. `run_migration` raised `RuntimeError`.

**Apply-gate conclusion:** neither box can fold cleanly. Operator must first resolve each shared model's conflict (pick one canonical tune, or split into per-slot model rows) before any `deploy_window=True` apply. The migrator's refuse-the-whole-run behavior worked exactly as specified (no partial fold).

### Step 2 — Honcho usage check — NOT present on either box
Both 143 and 150: `hal0 memory status` → State ON, **Provider `durable`**; `[memory] engine = "hindsight"` (enabled=true, unified_bank=true); only **`hindsight-api.service` active running** (no honcho unit); system-info provider string = `hindsight` only. **Memory is Hindsight-only on both boxes → the Honcho→Hindsight migration does not apply (already Hindsight).**

### Step 3 — 150 installer-gate re-check from MAIN f2db4d64 (idempotent, non-destructive)
Shipped f2db4d64 tree → `bash installer/install.sh`. **SUCCESS, INSTALL_EXIT=0, 137s. No regression** on privileged/podman-4.9.3.
- **M3 fix behaves (GREEN):** GPU gate now emits `gpu: /dev/dri/renderD128 → group render (gid 993)` **AND a new** `gpu: hal0 is a member of render` check — passes on 150 (gid 993 = render, hal0 is a member). `providers/_gpu.py` now `import stat` + `os.stat(node)`. On a 143-shaped box (gid 993 = "clock", hal0 not a member) this new membership assertion is what would now correctly catch the misconfig instead of false-passing.
- **M2 fix present (code-verified):** `installer/lib/preflight.sh` now greps the smoke stderr for `create keyring.*(disk quota exceeded|quota exceeded)|keyring.*edquot` and prints the **accurate** remedy — "kernel keyring quota exhausted … NOT a missing nesting/keyctl config. Check `cat /proc/key-users` … reboot / `keyctl clear @s` / raise `kernel.keys.maxbytes`" — directly replacing the misleading nesting/keyctl message from B1/M2. (Didn't trip on 150 since podman run works there; wording confirmed in source.)
- Post-install sanity: `hal0 --version` 0.9.8, `/api/health` 200, doctor all PASS except WARN `Runners 8/9 — errored: brain` (pre-existing rocm brain-slot error, m6) + WARN model-layout migration (O4). `hal0-slot@brain` + 5 anon podman-healthcheck units failed (pre-existing). Cosmetic `/dev/tty` line persists (now L2395).

### Deploy-window verdict
All three safe steps done, nothing written. **Apply decision is GATED:** the flags-fold migration would refuse on BOTH boxes (divergent shared-model tunes — 143: qwen3.5-0.8b×3 slots; 150: hal0-brain-fpx8-agent×2 slots) — resolve conflicts before any apply. Honcho→Hindsight migration is a no-op (already Hindsight both boxes). The M2+M3 installer-gate fixes on main f2db4d64 are validated live on 150 (behave correctly, no regression).

---

## PRE-CANONICALIZE INSPECT — stamp r5v3 (READ-ONLY + backup; NOTHING written)

Goal: before the operator canonicalizes the two divergent-share models to the brain slot's ROCm tune, confirm live state — are the affected slots currently launching with their intended tune, or did FLAGS-own §2 drop it (live regression)?

### 1. Backup (reversible) — BOTH boxes
Backed up to `/var/lib/hal0/backups/pre-canonicalize-r5v3/` on each box:
- **`hal0.db`** — THE real store. Model registry is **`SqliteModelRegistry` at `/var/lib/hal0/hal0.db`** (NOT `registry/registry.toml` — that legacy dir is empty). `model.defaults` (the canonicalize write target) lives in this sqlite db. **143** hal0.db sha `18367e69…` (25.4 MB); **150** sha `18367e69…`→ actually `18367e69…` listed for 143; 150 hal0.db in its own manifest. `slots/*.toml` + legacy `registry/` also copied. All files sha256'd in the transcripts.
- Restore = `cp -a backups/pre-canonicalize-r5v3/hal0.db /var/lib/hal0/hal0.db` (+ slots) + `systemctl restart hal0-api`.

### 2. Current model.defaults (the shared models) — EMPTY on both
- **143 `qwen3.5-0.8b`.defaults = `None`**
- **150 `hal0-brain-fpx8-agent`.defaults = `None`**
Neither shared model carries any materialized tune. Under FLAGS-own §2 the launch argv reads ONLY `model.defaults` (+ base scalars) — profile flags and slot `extra_args`/`n_gpu_layers` are *accepted-and-ignored* (`_llama_argv_segments` docstring: "a slot carries no flag surface"; `flags_str=""`). So with defaults=None, any tune that lived in a profile/slot override is NOT applied at launch.

### 3. Actual rendered launch argv (byte-identical to launch via `_resolve_slot_argv`, slots offline)
| Box·slot | profile / device | RENDERED LAUNCH ARGV | tune present? |
|---|---|---|---|
| 143·**brain** | rocm-dense / gpu-rocm | `--host 0.0.0.0 --port 8089 --model …Qwen3.5-0.8B….gguf --alias qwen3.5-0.8b --ctx-size 32000 --jinja` | **NO** — lost `-fa on -dev ROCm0 -b 512 -ub 512 --parallel 1 --threads 16 --no-mmap --metrics --no-webui …` **and `-ngl 999`** |
| 143·qtest | None / gpu-rocm | `_resolve_slot_argv=None` (no profile) — launches bare (model + ctx 4096) | n/a — never had a tune |
| 143·smoke-test | None / gpu-rocm | `_resolve_slot_argv=None` (no profile) — bare (model + ctx 8192) | n/a — never had a tune |
| 150·**agent** | vulkan / gpu-**vulkan** | `--host 0.0.0.0 --port 8081 --model …/model.gguf --alias hal0-brain-fpx8-agent --ctx-size 64000 --jinja` | **NO** — lost `-fa on` + `-ngl 999` |
| 150·**brain** | rocm-dense / gpu-rocm | `… --ctx-size 64000 --jinja --chat-template-file …minicpm5-1b-toolfix.jinja` | **NO** — lost the full ROCm tune + `-ngl 999` (kept chat-template) |

Note: `--ctx-size` and `--jinja`/`--chat-template` come from `base`/`model_extra_args`/`chat_template` segments — they SURVIVE. `--ctx-size` is a **slot override that wins** (per-slot: 32000/4096/8192 on 143; 64000 on 150), so canonicalizing `model.defaults` will NOT change per-slot ctx.

### 4. VERDICT
**LIVE REGRESSION CONFIRMED (both boxes).** Every slot whose tune lived in its *profile* now launches **bare** — no backend flags and, critically, **no `-ngl 999`, i.e. no GPU offload** (→ CPU-only / mis-tuned). Affected: **143 brain**, **150 agent + 150 brain**. (143 qtest/smoke-test have no profile → nothing to lose; only their ctx-size, which survives.) This is the expected post-R5-code / pre-migration gap: §2 relocated the tune to `model.defaults`, and the flags-fold migrator is the mechanism to populate it — but it hasn't run, so `model.defaults` is empty.

⇒ Canonicalizing `model.defaults` to the brain ROCm tune is a **RESTORE** for the brain slots (fixes the regression). BUT two hazards for the write:
- **⚠ 150 BACKEND MISMATCH (blocker for a blind ROCm canonical):** `hal0-brain-fpx8-agent` is shared by **agent (gpu-vulkan)** + **brain (gpu-rocm)**. Canonicalizing to brain's ROCm tune pushes `-dev ROCm0` (+ ROCm batch/thread flags) onto the **vulkan** agent slot — wrong backend, will mis-tune/break agent. Safer: split per-backend (agent→vulkan tune `-fa on -ngl 999`; brain→rocm tune) rather than one canonical ROCm default. This is exactly why the migrator refused this share.
- **143 is backend-uniform** (brain/qtest/smoke all gpu-rocm): a ROCm canonical is backend-consistent; qtest/smoke would *gain* the ROCm tune + `-ngl 999` (a behavior change for those two test slots, but same backend; their ctx preserved).

**HOLD — nothing written.** Awaiting the orchestrator's gate on the write (and on the 150 backend-mismatch decision).

---

## CANONICALIZE-RESOLVE — stamp r5v3

### 143 — brain-only SPLIT (authorized live write; EXECUTED, verified)
Mechanism: SqliteModelRegistry (`/var/lib/hal0/hal0.db`). Backup taken (pre-canonicalize-r5v3). brain slot was offline → no restart needed.

**DB rows written (2):**
1. **New model row `qwen3.5-0.8b-brain`** in `hal0.db` `model` table — path **shared** `/mnt/ai-models/qwen3.5-0.8b/Qwen3.5-0.8B-UD-Q4_K_XL.gguf` (refcount share, weights NOT duplicated), `defaults.extra_args = "-fa on -dev ROCm0 -b 512 -ub 512 --parallel 1 --threads 16 --no-mmap --metrics --no-webui --ctx-checkpoints 0 --checkpoint-every-n-tokens -1"`, `defaults.n_gpu_layers = 999`, `defaults.context_size = None` (intentionally omitted — brain slot ctx override wins).
2. **`/etc/hal0/slots/brain.toml`** `[model].default`: `qwen3.5-0.8b` → **`qwen3.5-0.8b-brain`**.
Base `qwen3.5-0.8b`.defaults stays `None` (qtest/smoke untouched).

**brain before/after launch argv (rendered):**
- BEFORE: `--host 0.0.0.0 --port 8089 --model …Qwen3.5-0.8B….gguf --alias qwen3.5-0.8b --ctx-size 32000 --jinja`  ← bare (regressed)
- AFTER: `… --alias qwen3.5-0.8b-brain --ctx-size 32000 -fa on -dev ROCm0 -b 512 -ub 512 --parallel 1 --threads 16 --no-mmap --metrics --no-webui --ctx-checkpoints 0 --checkpoint-every-n-tokens -1 --jinja -ngl 999`  ← **tune restored** (`-ngl 999` ✓, ctx 32000 ✓, ROCm flags ✓)
- **qtest / smoke-test argv: UNCHANGED** (`_resolve_slot_argv=None`, still bare — not re-tuned). ✓

**Migrator dry-run recheck (post-split):** `folds=1 refusals=1 ok=False`. The **brain divergence is RESOLVED** (brain no longer in any refusal). ⚠ **Residual:** `qwen3.5-0.8b` STILL refuses for **qtest vs smoke-test** — divergent `context_size` (4096 vs 8192). This is a separate, benign divergence: both are bare test slots with no real tune, and `context_size` survives at launch as a slot override regardless, so no fold is functionally needed. Options if a clean migrator is wanted: align qtest/smoke ctx, or split them too, or leave as-is (test slots). The brain-restore goal is fully met; the share is only *partially* cleared (residual is qtest/smoke ctx, not brain).

### 150 — rocm vs vulkan benchmark (read-only; no config write)
Model `hal0-brain-fpx8-agent` (1.1 GB gguf), AMD Radeon 8060S / Strix Halo (RADV GFX1151). Both backends live in ONE image (`ghcr.io/hal0ai/hal0-rocmfpx:c077206` — `rocmfpx` AND `vulkanfpx` resolve to it; `--list-devices` exposes `ROCm0` + `Vulkan0`). Method: throwaway container per backend, identical flags (`-ngl 999 -fa on -c 4096`, only `-dev` differs), fixed prompt, `n_predict=256 ignore_eos=true`, 3 runs; metrics from llama-server `timings`. Containers torn down; live agent/brain slots + model.defaults untouched (verified).

| Metric (steady-state, runs 2-3) | ROCm0 | Vulkan0 | Winner |
|---|---|---|---|
| Prefill (prompt) tok/s | ~1277 | **~1790** | Vulkan **+40%** |
| Generation (decode) tok/s | ~162 | **~189** | Vulkan **+16%** |
| TTFT (prompt_ms) | ~14 ms | **~10 ms** | Vulkan **−30%** |
| Reported free VRAM | 36 GB | 67 GB | Vulkan (RADV) |
| Stability | ok, GPU-offloaded, no errors | ok, GPU-offloaded, no errors | tie |

(run1 each is cold-cache and slower — prefill ~725 tok/s both; steady-state above is the fair read.)

**RECOMMENDATION: prioritize VULKAN for `hal0-brain-fpx8-agent` on 150.** RADV/Vulkan beats ROCm/HIP on every axis here — prefill +40%, decode +16%, TTFT −30% — the known Strix Halo pattern. Note the current live config already has **agent=vulkan (fast)** and **brain=rocm (slow)**; moving brain to vulkan (or standardizing on vulkan for this model) would be the throughput-optimal choice. Caveats: single small model, short prompt (18 tok), batch=1, ctx 4096, this fork build — larger models / long context / batching could shift the ratio; re-bench if those change.

Benchmark transcript: `/tmp/hal0-deploy/bench-probe-150.log`.

---

## 150 VULKAN STANDARDIZE + migrator ctx analysis — stamp r5v3

### 150 write (authorized; EXECUTED + verified; brain slot not restarted)
**DB rows written (2):**
1. `hal0.db` model **`hal0-brain-fpx8-agent`.defaults** → `{extra_args: "-fa on", n_gpu_layers: 999, context_size: None}` (canonical vulkan tune both slots inherit; ctx omitted so each slot's 64000 override wins).
2. `/etc/hal0/slots/brain.toml` **`device`: gpu-rocm → gpu-vulkan** (profile left `rocm-dense` — see note).

**Before/after rendered argv:**
- agent BEFORE: `… --ctx-size 64000 --jinja` (bare) → AFTER: `… --ctx-size 64000 -fa on --jinja -ngl 999`
- brain BEFORE: `… --ctx-size 64000 --jinja --chat-template-file …toolfix.jinja` (bare) → AFTER: `… --ctx-size 64000 -fa on --jinja -ngl 999 --chat-template-file …toolfix.jinja`
- Both now carry `-fa on` + `-ngl 999` + ctx 64000, **no `-dev ROCm0`** (checks fa/ngl/ctx/no-rocm-pin all True). Only diff: brain keeps its chat-template (legit).

**Brain DROPPED vs old rocm tune** (for the keep/drop confirm — matches expected):
`-dev ROCm0`, `-b 512`, `-ub 512`, `--parallel 1`, `--threads 16`, `--no-mmap`, `--metrics`, `--no-webui`, `--ctx-checkpoints 0`, `--checkpoint-every-n-tokens -1`. (Kept: `-fa on`, `-ngl 999`, `--ctx-size 64000`.)

**⚠ Notes for decision:**
- **No explicit `-dev` in either argv** → llama-server AUTO-SELECTS the GPU device at launch (the canonical `extra_args='-fa on'` carries no device pin). `--list-devices` lists `ROCm0` first, so an un-pinned launch could land on ROCm, not Vulkan. To GUARANTEE Vulkan, set `defaults.extra_args = "-fa on -dev Vulkan0"`. Current spec relies on auto-select — flag for the keep/drop decision.
- **brain.profile still `rocm-dense`** (device now gpu-vulkan) — cosmetically inconsistent but harmless at launch (§2 never consults profiles). It DOES affect the migrator (below).

**Migrator recheck (150):** `folds=0 refusals=1 ok=False` — STILL refuses `hal0-brain-fpx8-agent` (agent vs brain). Cause: the migrator computes the fold from **profiles**, and brain's profile is still `rocm-dense` (`-ngl 999 -fa on -dev ROCm0 -b 512 …`) vs agent's `vulkan` (`-ngl 999 -fa on`). The **launch** divergence IS resolved (both render the identical vulkan tune — verified), but the migrator, an old-world (profile→defaults) tool, sees the stale profile. Re-running the migrator on an already-manually-canonicalized model is misleading. To also silence the migrator: set brain.profile → `vulkan`.

### Migrator context_size analysis (read-only; gates the full apply)
Read of `slot_flags_fold.py`:
- **(a) Does it FOLD context_size into model.defaults? YES.** `compute_folded_tune` pulls slot `[model].context_size` into `FoldedTune.context_size` (L217-237); `FoldedTune.as_defaults_updates()` writes `context_size` into the defaults dict when non-None (L~155). So ctx is a folded field, not merely slot-preserved by the migrator.
- **(b) Is refusing on ctx-divergence spurious? Effectively YES (over-conservative).** The divergence key is full `FoldedTune` value-equality (frozen dataclass; `distinct = {r.folded for r in refs}`, L305), which includes `context_size` — so two slots differing only in ctx are "divergent" and refused. BUT at **launch**, slot `[model].context_size` **overrides** `model.defaults.context_size` (`_resolve_context` returns the explicit slot ctx first — proven: every slot renders its own `--ctx-size`). So whatever ctx the migrator would fold into shared defaults is **shadowed at launch**; the fold value is launch-irrelevant. Net: the migrator blocks a fold over a field that doesn't change launch behavior. The only *real* divergence is `extra_args`/`n_gpu_layers`.
- **(c) Abort-whole-run on ANY refusal? YES.** `apply_fold_plan` raises `RuntimeError` if `plan.refusals` is non-empty, BEFORE applying any fold (L369-379; "fold-what-you-can is unsafe" by design). So a full `deploy_window=True` apply **aborts entirely** if ANY model refuses. To run one clean full apply, EVERY refusal must first be resolved.

**Consequences for the full apply (to fix the broader bare-launch regression):**
- A blanket full apply is currently **blocked on BOTH boxes**: 143 `qwen3.5-0.8b` (qtest ctx4096 vs smoke ctx8192) and 150 `hal0-brain-fpx8-agent` (agent vs brain profile) each abort the whole run.
- Ways forward: (i) **one-line migrator fix** — drop `context_size` from `FoldedTune`'s fold + divergence key (since ctx is slot-preserved at launch) → clears the 143 qtest/smoke refusal outright; (ii) for genuine backend-divergent shares (150 agent/brain) — per-backend canonicalize manually (done) and exclude that model from the migrator (don't re-run it post-canonicalization); (iii) or align/split the divergent slots' config. Recommended: (i) + (ii) — after that, the migrator would cleanly fold every remaining single-slot profile-tuned model, restoring them, without aborting. A blind full apply without one of these will just abort.

---

## 150 VULKAN PIN + REGRESSION SCOPE / CLOBBER — stamp r5v3

### 150 pin fix (EXECUTED + verified)
- `hal0.db` `hal0-brain-fpx8-agent.defaults.extra_args`: `-fa on` → **`-fa on -dev Vulkan0`** (ngl 999, ctx None).
- `brain.toml` `profile`: `rocm-dense` → **`vulkan`**.
- Verify: agent argv = `… -fa on -dev Vulkan0 --jinja -ngl 999` (ctx 64000); brain = same + chat-template; both pinned Vulkan0, no ROCm0.
- **Migrator recheck 150: `folds=1 refusals=0 ok=True`** — `hal0-brain-fpx8-agent` refusal CLEARED (profiles aligned to vulkan).

### Broader regression scope (read-only, both boxes)
Enumerated every slot's profile / bound model / model.defaults / rendered `-ngl`:

**143:** only **brain** had a bound model + profile-tune → already FIXED (renders `-ngl 999` + ROCm tune). `qtest`/`smoke-test` have a model (`qwen3.5-0.8b`) but **no profile** → never tuned (not regressed). The other 8 "profile set / no -ngl" slots (`agent, embed, flm, img, rerank, tts, utility, vision`) all have **model='' (no bound model)** — offline/capability slots, not `§2`-regressed (nothing to launch/tune).

**150:** **agent + brain** had bound model + tune → both already FIXED. The other 7 profile slots (`embed, flm, img, rerank, tts, utility, vision`) all have **model=''** — same non-regressed capability-slot case.

**⇒ On these two boxes, the ONLY §2-regressed (bound-model + profile-tune → launched bare) slots were 143-brain, 150-agent, 150-brain — ALL already manually restored. There are ZERO additional bare model-bound slots to restore.** (The "profile set but no -ngl" slots are model-less capability slots, out of the model.defaults regression class.)

### Clobber check — CONFIRMED (both boxes)
The migrator's fold plan contains exactly ONE fold per box, and it is **my manual model** — and the fold's `new_defaults` DIFFERS from what I set, so `apply_fold_plan(deploy_window=True)` **would overwrite my manual defaults**:
- 143 `qwen3.5-0.8b-brain`: existing `context_size=None` → new **`context_size=32000`** (apply would ADD ctx; extra_args unchanged).
- 150 `hal0-brain-fpx8-agent`: existing `context_size=None` → new **`context_size=64000`** (apply would ADD ctx; extra_args unchanged).
Mechanism: `apply_fold_plan` folds `existing ⊕ profile/slot fold` and **writes any model whose merged defaults differ from current** — it does NOT skip non-empty-defaults models (only exact no-ops are skipped). So a full apply re-reads profiles+slot ctx and **re-writes my manual models, adding the ctx I deliberately omitted**. (Benign at launch — slot ctx override wins — but an unwanted mutation.) Note 143's apply would also **abort entirely** first (qtest/smoke `qwen3.5-0.8b` refusal); 150's apply is `ok=True` and WOULD proceed to add ctx 64000.

### Recommendation (safest restore path)
**Do NOT run the full migrator apply on 143/150.** The §2 regression on these boxes was limited to the 3 slots already manually restored; the scope shows **no remaining bare model-bound slots**. A full apply would only (a) clobber my manual models (add ctx), and (b) abort on 143's benign qtest/smoke refusal. Keep the targeted manual per-model restore already done.
- If a full apply is ever wanted as policy (other deployments): first land the **one-line migrator fix** (drop `context_size` from `FoldedTune`'s fold + divergence key — ctx is slot-preserved at launch) to stop both the ctx-clobber and the spurious ctx refusal; and **exclude already-canonicalized models** (or accept the idempotent ctx add). For 143/150 specifically it remains unnecessary.
- 143 qtest/smoke `qwen3.5-0.8b` refusal is harmless (bare test slots, no tune intended) — leave as-is, or align their ctx for a clean migrator state.

---

## Overall verdict
- **471c365a is SHIP-READY on the podman-4.9.3 / privileged substrate (150):** clean 141s install, all exercised core phases green — O12 rootful seam, uniform `PodmanArgs=` render, hermes convergence markers + non-rotated key, `/api/health` 200, services enabled for autostart, prior failed slot healed.
- **NOT validated on the podman-5.7 / unprivileged substrate (143):** blocked by a **box-environment** keyring-quota exhaustion (B1) — not a hal0 code defect. The installer correctly refused, but exposed two installer gaps: **M2** (misleading keyctl remedy; no keyring-quota diagnosis) and **M3** (GPU gid gate false-passes on a gid/name collision).
- **No hal0 code blocker found.** Recommended fix-forward lanes: M2 (keyring-quota detection in the container-runtime preflight), M3 (GPU gate should require the render group specifically), m1 (hermes ownership-drift-after-install), m2 (slot quadlet `StartLimitIntervalSec` section), m4 (`agent status --json`).
- **Action to unblock 143:** free root's kernel keyring (reboot CT / clear leaked keys) or raise `kernel.keys.maxbytes`, then re-run the installer — expected to pass as 150 did (identical code).
