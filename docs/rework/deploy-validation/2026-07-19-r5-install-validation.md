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

## Overall verdict
- **471c365a is SHIP-READY on the podman-4.9.3 / privileged substrate (150):** clean 141s install, all exercised core phases green — O12 rootful seam, uniform `PodmanArgs=` render, hermes convergence markers + non-rotated key, `/api/health` 200, services enabled for autostart, prior failed slot healed.
- **NOT validated on the podman-5.7 / unprivileged substrate (143):** blocked by a **box-environment** keyring-quota exhaustion (B1) — not a hal0 code defect. The installer correctly refused, but exposed two installer gaps: **M2** (misleading keyctl remedy; no keyring-quota diagnosis) and **M3** (GPU gid gate false-passes on a gid/name collision).
- **No hal0 code blocker found.** Recommended fix-forward lanes: M2 (keyring-quota detection in the container-runtime preflight), M3 (GPU gate should require the render group specifically), m1 (hermes ownership-drift-after-install), m2 (slot quadlet `StartLimitIntervalSec` section), m4 (`agent status --json`).
- **Action to unblock 143:** free root's kernel keyring (reboot CT / clear leaked keys) or raise `kernel.keys.maxbytes`, then re-run the installer — expected to pass as 150 did (identical code).
