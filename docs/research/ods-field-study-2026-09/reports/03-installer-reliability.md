# 03 — Installation, Upgrade, and Reliability Engineering: ODS vs hal0

Source-level comparative study. All paths are absolute. ODS product runtime is
`/home/user/ods/ods/`; hal0 is `/home/user/hal0/`.

The owner's premise — "ODS has me beat on installation and reliability" — is
**half right**. ODS beats hal0 decisively on *installer structure, install-time
observability, and failure forensics*. hal0 beats ODS decisively on *update
integrity, diagnosis modelling, and preflight depth*. The gaps are asymmetric and
mostly complementary, which is good news: almost everything worth porting is
additive.

---

## A. How ODS does it

### A.1 The install pipeline is a sourced-phase state machine

`/home/user/ods/ods/install-core.sh` is a **363-line orchestrator** and nothing
else. It does five things in order:

1. Sets `INSTALL_PHASE` and installs an `ERR` trap (`install-core.sh:23-38`) that
   reports *which phase* died, where the log is, and what the partial state is:
   ```bash
   export INSTALL_PHASE="init"
   cleanup_on_error() {
       local exit_code=$?
       echo -e "\033[0;31m[ERROR] Installation failed during phase: ${INSTALL_PHASE}\033[0m"
       echo -e "\033[0;33m        Log file: ${LOG_FILE:-/tmp/ods-install.log}\033[0m"
       ...
       echo "To retry, run the installer again. It will resume safely."
   }
   trap cleanup_on_error ERR
   ```
2. Installs a **double-tap SIGINT** handler (`:45-64`) — one Ctrl+C warns, a
   second within 3s aborts and calls `cancel_active_download`; `SIGTSTP` is
   trapped to nothing so Ctrl+Z can't background a live install.
3. Sources 15 pure libraries in dependency order (`:74-93`).
4. Parses ~40 flags (`:220-273`): `--dry-run`, `--non-interactive`,
   `--summary-json PATH`, `--no-bootstrap`, `--offline`, `--lan`, `--tier N`, …
5. Sources the 14 phase files, assigning `INSTALL_PHASE` before each
   (`:338-363`). Phase 13 runs under `set +e` because it is cosmetic and "must
   never fail the install".

The lib/phase split is a **hard architectural rule**
(`docs/INSTALLER-ARCHITECTURE.md`): *libraries define functions only and are safe
to source; phases execute on source*. Everything shares one bash namespace.
`docs/INSTALLER_PHASE_CONTRACTS.md` then gives each phase a contract row — **Owns
/ Inputs / Outputs / Idempotency expectation / Common failure modes** — plus a
"Required Validation By Phase" table and a written **Reinstall Contract** ("keep
user secrets; keep downloaded models when valid; keep runtime data under `data/`;
exit non-zero only when the installed product is not recoverable").

### A.2 The standardized module header

Every file under `installers/lib/` and `installers/phases/` carries the same
header. This is the highest-leverage, lowest-cost thing in the whole repo:

```bash
#!/bin/bash
# ============================================================================
# ODS Installer — <Module Name>
# ============================================================================
# Part of: installers/lib/   (or installers/phases/)
# Purpose: <one-line description>
#
# Expects: <globals/functions this file reads>
# Provides: <globals/functions this file defines>
#
# Modder notes:
#   <when and why you'd edit this file>
# ============================================================================
```

The `Expects`/`Provides` chain is how a reader traces dataflow through a global
namespace without reading every line
(`docs/INSTALLER-ARCHITECTURE.md`, "File Header Convention"). Real examples:
`installers/lib/detection.sh:1-22`, `installers/phases/02-detection.sh:1-22`,
`installers/lib/ui.sh:1-24`.

The same doc carries a **Mod Recipes** table ("Add a hardware tier → edit
`lib/tier-map.sh` + `lib/detection.sh`") and a **Generated Config Writers** table
naming, per config surface, the Linux/macOS/Windows/upgrade writers that must
stay in sync — because "only one platform writer was updated" is their most
common regression class.

### A.3 Bootstrap: `get-ods.sh`

`/home/user/ods/ods/get-ods.sh` (507 lines) is the `curl | bash` entry point:

- **CWD anchoring** (`:14-21`): re-runs from a deleted directory (post-uninstall)
  otherwise fail with a misleading "check your internet connection".
- **Error classification** (`:52-65`): `format_git_clone_error` maps git stderr to
  a specific human cause (getcwd, DNS, permissions).
- **`ODS_REF` pinning** (`:90-108, 414-438`): a 40-hex SHA gets `--depth 1` +
  `git fetch origin <sha>` + `git checkout --detach`; a branch/tag gets
  `--branch`. Sparse checkout of only `ods/`, with a full-clone fallback.
- **Coexistence refusal** (`:110-215`): before cloning it scans sibling directories
  for an install signature (`.env` + a compose file naming `open-webui`,
  `dashboard-api`, and `llama-server|litellm`) *and* queries Docker for compose
  projects with the same service tuple, refusing with an explicit
  `ODS_ALLOW_LEGACY_PARALLEL=1` escape hatch.
- **Runtime/dev split** (`:443-468`): rsync excludes `tests/`, `docs/`, `*.md`,
  `.github/` — the installed tree is smaller than the repo tree.
- `docs/INSTALLER_TRUST.md` documents the provenance story honestly, including
  what is *not* yet true ("ODS does not yet publish a complete signed-release or
  checksum/SBOM chain"), with a numbered roadmap.

### A.4 The pure libraries

Highlights, all under `/home/user/ods/ods/installers/lib/`:

- **`detection.sh`** (842 lines). `ods_in_container()` (`:150-163`) checks
  `/.dockerenv`, `/run/.containerenv`, `systemd-detect-virt --container`,
  `/proc/1/cgroup` regex, and `/proc/1/environ`; `ods_container_label()`
  (`:165-185`) names it (`lxc`, `docker`, …). This feeds
  `show_amd_gpu_device_guidance()` (`:219-233`), which prints *LXD-specific*
  remedies (`lxc config device add <container> kfd unix-char path=/dev/kfd`) when
  inside a container and modprobe remedies when not. `apply_cpu_gpu_fallback()`
  (`:235-257`) then degrades the whole install to CPU coherently — it rewrites
  `GPU_BACKEND`, `CAP_*`, and clears compose overlays in one place, rather than
  letting a half-set backend leak downstream. `select_cpu_fallback_tier()`
  (`:259-274`) picks a tier from RAM alone.
- **CPU budget** (`detection.sh:92-147`): `get_docker_available_cpus()` prefers
  `docker info --format '{{.NCPU}}'` over `nproc`, then
  `calculate_llama_cpu_budget()` clamps per-backend limit/reservation to what's
  actually available. `llama-memory-budget.sh` does the same for RAM, taking
  `min(host, docker)` so Docker Desktop VMs don't get over-committed.
- **`progress.sh`** (25 lines, quoted in full in D.2) — a GUI progress protocol
  that is a **complete no-op** unless `ODS_INSTALLER_GUI=1`.
- **`docker-images.sh` / `compose-images.sh`** — validate an image tag exists
  before compose runs (`validate_docker_image_or_fallback`), and discover the
  external image set from the *resolved compose stack* rather than a hardcoded list.
- **`readiness-summary.sh`** (128 lines) — takes `name|health_url|container|open_url`
  lines on stdin, probes each, prints `Ready now: N/M` with per-service state
  (`ready`/`starting`/`not detected`/`needs attention`) plus a "Next:" block.
- **`model-lifecycle-lock.sh`** (83 lines) — `flock` mutual exclusion between the
  installer and the detached background upgrader, keyed by a `cksum` of the
  *resolved* install dir so an SSH session and a systemd unit with different
  `XDG_RUNTIME_DIR` still contend on the same lock.
- **`sudo.sh`** — `ods_prepare_sudo()` sets `ODS_SUDO_AVAILABLE`; `ods_sudo()`
  then *skips* root-only extras rather than hanging on a password prompt under
  `--non-interactive`.
- **`podman-registries.sh`** (234) repairs Podman short-name search config
  idempotently (Bash-3.2-safe empty-array guard); **`packaging.sh`** (312) is the
  apt/dnf/yum/pacman/zypper abstraction; `lib/rootless-ownership.sh` (repo root)
  provides `ods_fix_rootless_ownership()` for rootless-Docker bind-mount UID
  mapping, called from `phases/06-directories.sh:1161` and from `ods-cli`.

### A.5 Installer UX: the narrator

`installers/lib/ui.sh:97-103` is the entire narrator API:

```bash
ai()       { echo -e "  ${GRN}▸${NC} $1" | tee -a "$LOG_FILE"; }
ai_ok()    { echo -e "  ${BGRN}✓${NC} $1" | tee -a "$LOG_FILE"; }
ai_warn()  { echo -e "  ${AMB}⚠${NC} $1" | tee -a "$LOG_FILE"; }
ai_bad()   { echo -e "  ${RED}✗${NC} $1" | tee -a "$LOG_FILE"; }
signal()   { echo -e "  ${GRN}░▒▓█▓▒░${NC} $1" | tee -a "$LOG_FILE"; }
```

Every one of them **tees to `$LOG_FILE`**. That single detail is why ODS can hand
a user a log after a failure and why `ods-doctor` can mine `install-report-*.txt`
later.

`show_phase` (`ui.sh:115-125`) prints `PHASE n/6 — NAME` with a wall-clock
timestamp and an **`EST. TIME:`** line supplied by the caller; call sites give
honest ranges (`phases/08-images.sh:21`: `"~5-10 min + ~30 min ComfyUI build"`).
`spin_task` (`ui.sh:236-266`) drives a braille spinner with an `[mm:ss]` counter,
optionally rendering live progress from a `.part` file; `format_download_progress`
(`ui.sh:187-208`) is deliberately pure so it is BATS-testable.

`pull_with_progress` (`ui.sh:305-370`) is the retry-protected image pull:
`timeout 3600 docker pull`, up to `ODS_DOCKER_PULL_MAX_ATTEMPTS` (default 4)
attempts with backoff `5/15/30s` then doubling (`_docker_pull_retry_delay`,
`ui.sh:269-302`), a post-pull `docker inspect` **validation** step, and a
**non-retryable classifier** that bails immediately on
`unauthorized|denied|not found|404|no space left on device|cannot connect to the
docker daemon`. `check_service` (`ui.sh:375-460`) is the health-check twin:
exponential backoff capped at 8s, distinguishing curl exit 124 (timeout), 7
(refused), 56/52 ("starting up"), while simultaneously inspecting the container —
if it is `exited|dead|missing` it stops retrying instead of burning 30 attempts.

Other surfaces: `show_hardware_summary`/`show_tier_recommendation`
(`ui.sh:462-503`); `show_install_menu` (`ui.sh:507-597`, Full/Core/Custom, which
auto-disables ComfyUI on tier 0/1 *with an explanation*); `show_success_card`
(`:600-632`); a `.desktop` file + GNOME favourites pin
(`phases/13-summary.sh:229-254`); the readiness summary (`:365-396`); and the
`--summary-json` writer (`:435-502`), a versioned payload (`version`,
`installer_version`, `tier`, `runtime.{gpu_backend,backend_service,llm_model,compose_flags,dry_run}`,
`hardware_class`, `preflight_report`) written with `mkstemp` + `fsync` +
`os.replace`. `docs/SETUP-CARD.md` covers a printable 4×6 QR card generator;
`docs/POST-INSTALL-CHECKLIST.md` is a six-step "did it work" ritual.

### A.6 Bootstrap mode: tiny model first, hot-swap later

ODS's signature reliability trick; hal0 has no equivalent.

`installers/lib/bootstrap-model.sh` (57 lines) pins a ~1.22 GiB Qwen3.5-2B (file,
URL, **SHA256**, model id, 64K context floor) and defines `bootstrap_needed()`:
true only when tier > 0, the full GGUF is *not* already on disk, `--no-bootstrap`
was not passed, and the install is not offline / cloud / external-Lemonade.

`phases/11-services.sh:673-690` stashes the full-model config into `FULL_*` vars
and swaps `GGUF_FILE/GGUF_URL/LLM_MODEL/MAX_CONTEXT` to the bootstrap values, so
the *entire rest of the phase* (download, `models.ini`, `.env` patch, compose up)
runs unmodified against the tiny model — the user is chatting in ~2 minutes.
`:1329-1372` then persists retry metadata to `data/bootstrap-upgrade.args` (mode
600) and launches the upgrader detached, inside a subshell that **closes
inherited non-stdio FDs first** so the parent's `flock` FDs aren't held for the
duration of a multi-GB download:

```bash
(
    _phase11_close_inherited_fds_for_daemon
    exec nohup bash "$SCRIPT_DIR/scripts/bootstrap-upgrade.sh" \
        "$INSTALL_DIR" "$FULL_GGUF_FILE" "$FULL_GGUF_URL" \
        "$FULL_GGUF_SHA256" "$FULL_LLM_MODEL" "$FULL_MAX_CONTEXT" \
        "$BOOTSTRAP_GGUF_FILE" \
        > "$INSTALL_DIR/logs/model-upgrade.log" 2>&1
) &
```

`scripts/bootstrap-upgrade.sh` (3412 lines) runs `set -uo pipefail` **without
`-e`** on purpose ("we handle errors explicitly to avoid killing the background
process on transient failures"). Its shape:

- **Status file**: `write_status()` writes `data/bootstrap-status.json` atomically
  (`status`, `percent`, `bytesDownloaded/Total`, `speedBytesPerSec`, `eta`); a
  `monitor_download` loop refreshes it every 2s from the `.part` size — this is
  what drives the dashboard progress bar.
- **Download** (`:2189-2325`): script-owned resume, *not* curl's internal retry
  (a curl retry after a long reset restarts at byte zero and truncates a good
  multi-GB `.part`). Bounded wall-clock budget with rounds; over/undersized
  `.part` detection; SHA256 verify; failure preserves the `.part` for resume.
- **Transaction snapshot** (`:305-380`): `snapshot_active_model_config()` copies
  `.env`, `config/llama-server/models.ini`, `config/litellm/lemonade.yaml` into a
  `mktemp -d` under `data/`, recording *absence* as a `.missing` sentinel so
  restore can delete a file that didn't exist. `restore_active_model_config()` is
  the exact inverse.
- **Swap**: `.env` promotion, `models.ini` rewrite, then `docker compose … up -d
  --force-recreate --no-deps llama-server` with `env -u GGUF_FILE -u LLM_MODEL -u
  MAX_CONTEXT -u CTX_SIZE` so the freshly written `.env` wins compose
  interpolation over inherited shell vars.
- **Post-swap assertion** (`:2712-2745`): inspects the recreated container's
  `.Config.Cmd` and *fails loudly* if `--model` still points at the old GGUF —
  catching the "compose started rather than recreated" bug that would otherwise
  surface hours later as a 502.
- **Rollback** (`restore_docker_llama_server_after_swap_failure`, `:386-452`):
  restore snapshot → recreate with retry → poll health 60×5s → on AMD also restart
  LiteLLM and **prove a completion through the restored route** before declaring
  rollback successful. Called from four failure sites (`:2826, :2845, :2865,
  :3046`), each writing a human-readable `write_status "failed" … "<what
  happened>. <rollback state>. Re-run to retry."`
- **Cleanup** (`:3200-3202`): the bootstrap GGUF is deleted **only** when
  `HOT_SWAP_VERIFIED=true`.

Net effect: a failed upgrade leaves the user on a working small model with a
resumable partial download and an explanatory status string. That is the single
best reliability idea in the repo.

### A.7 Reliability tooling

| Tool | File | What it is |
|---|---|---|
| Preflight engine | `scripts/preflight-engine.sh` (362) | Takes `--tier/--ram-gb/--disk-gb/--gpu-backend/--gpu-vram-mb/--platform-id/--compose-overlays`, emits **blockers/warnings + JSON report** (`/tmp/ods-preflight-report.json`) and an `--env` mode for shell integration. Pure inputs → pure verdict, so it is fixture-testable. |
| Preflight fixtures | `tests/contracts/test-preflight-fixtures.sh` (132) | Six named scenarios (`linux-nvidia-good`, `windows-mvp-good`, `macos-mvp-good`, `disk-blocker`, …) asserting `.summary.blockers`. Runs in `make test`. |
| Runtime preflight | `ods-preflight.sh` (332) | Post-install service probe with bounded curl (`--connect-timeout 3 --max-time 10`); `--install-env` delegates to `scripts/linux-install-preflight.sh` (456) for a JSON environment report. |
| Doctor | `scripts/ods-doctor.sh` (1730), `docs/ODS-DOCTOR.md` | `--json` report with `capability_profile`, `preflight`, `install_artifacts`, **`diagnoses[]`** (stable IDs like `ODS-COMPOSE-CWD-MISMATCH`, `ODS-DOCKER-IMAGE-UNRESOLVED`, plus a whole `ODS-RUNTIME-*` inference-contract family) each carrying `severity/confidence/evidence/impact/next_steps`, `runtime`, `summary`, `autofix_hints`. Exit 0/1. |
| Support bundle | `scripts/ods-support-bundle.sh` (792), `docs/SUPPORT-BUNDLE.md` | Redacted tarball: doctor output, extension audit, compose resolution + validation, docker info, container log tails, `config/env.redacted`. Best-effort — a failing probe is *recorded in the bundle*, not fatal. |
| Compose failure report | `installers/lib/compose-failure-report.sh` (187) | On compose failure writes `install-report-<ts>.txt`: privacy note, phase, compose command, cached flags, model/runtime env, **likely failed image(s) grepped out of the installer log**, per-port occupancy with the owning process, `docker version`/`info`, **redacted** `compose config` tail, `compose ps -a`, and 160 lines of installer log. The redactor (`_ods_report_redact_stream`) reads `.env` and masks both key-name matches and the literal secret values. |
| Update | `ods-update.sh` | `check/status/backup/update/rollback/changelog/health`. `snapshot_pre_update()` (`:235-320`) copies `.env*`, all `docker-compose*.yml`, `.compose-flags`, `config/{litellm,n8n,openclaw,searxng}/`, `.version` into `data/backups/pre-update-<ts>/` with a `snapshot.json` integrity marker and `MAX_BACKUPS` pruning. `cmd_update` = snapshot → `git pull` → run `migrations/migrate-v*.sh` in order → restart → `wait_for_healthy` (`:374-407`). Any non-zero step calls `_update_rollback()` (`:409-450`), which restores + restarts and, on restore failure, prints literal manual recovery commands. |
| Migrations | `migrations/` + `scripts/migrate-config.sh` | Versioned `migrate-vX.Y.Z.sh` with a `.migration-state` ledger; `check`/`diff`/`migrate`/`backup`. |
| Backup / uninstall / stack test | `ods-backup.sh`, `ods-restore.sh`, `ods-uninstall.sh`, `test-stack.sh` | Retention count + free-space precheck; `--keep-models/--keep-data/--force`; `test-stack.sh` runs `--quick/--stress/--voice` with no `set -e` and exits 0/1/2. |
| Policy docs | `docs/KNOWN-GOOD-VERSIONS.md`, `docs/ADR-IMAGE-TAG-PINNING.md`, `docs/RELEASE_VALIDATION.md`, `docs/VALIDATION-MATRIX.md`, `docs/HIGH_RISK_CHANGE_MAP.md` | Tested-version baselines per platform; a written, reasoned decision to *not* pin three `:latest` tags; the "User Green" gate (Zero-prereq bootstrap / Install / Product / Capability / Model Switchboard / Lifecycle Green); the four-layer validation matrix; and a risk table mapping changed area → required validation. |
| Gate + tests | `Makefile`, `tests/` | `make gate = lint + test + bats + smoke + simulate`; `test` alone runs ~60 named contract scripts. 33 BATS files, 32 `contracts/`, 6 `smoke/`, plus `scripts/simulate-installers.sh` (292) which runs `install-core.sh --dry-run --non-interactive --skip-docker --force --summary-json`, the macOS installer in MVP mode, the preflight engine as a Windows stand-in, and doctor — merging all of it into `artifacts/installer-sim/SUMMARY.{json,md}`. |

### A.8 Tauri desktop installer

`/home/user/ods/installer/` is a Vite + React + Tauri v2 app (7 pages: Welcome →
Prerequisites → SystemCheck → GpuDetected → Features → Installing → Complete;
`src-tauri/src/` is 1246 lines of Rust). `installer.rs::run_install` (`:33-172`)
is thin by design: `ensure_checkout()` does `git clone --depth 1 --branch`, then
it maps GUI feature toggles to installer flags and spawns `install.sh` (or
`install.ps1`) with `ODS_INSTALLER_GUI=1`, piping stdout through
`parse_progress_line()`. stderr is collected on a separate thread and the **last
10 lines are surfaced in the error message**. `PROGRESS_INTEGRATION.md` documents
the one-line-per-phase contract. It is a wrapper, not a second installer — the
shell path stays the single source of truth.

### A.9 Platform breadth

The phase model is *replicated*, not shared. **Windows**:
`installers/windows/install-windows.ps1` dot-sources `phases/01-preflight.ps1 …
07-devtools.ps1` plus 15 `lib/*.ps1` modules — including a PowerShell
`tier-map.ps1`, `readiness-summary.ps1`, `install-report.ps1`,
`compose-diagnostics.ps1`. **macOS**: `installers/macos/install-macos.sh` (3327
lines) is one file but speaks the same `show_phase 1 6 … "30 seconds"` vocabulary
(`:1132, :1337, :1451, :1567, :1929`) with its own
`lib/{ui,detection,tier-map,env-generator,preflight-fs,bridge-manager}.sh`;
llama-server runs **natively with Metal** on the host and Docker services reach it
via `host.docker.internal` through a loopback Colima bridge. **WSL**:
`installers/dispatch.sh` routes `linux|wsl → install-core.sh`, with divergences
isolated in detection and covered by `tests/smoke/wsl-logic.sh`. The cost of
replication is the "Generated Config Writers" table in
`docs/INSTALLER-ARCHITECTURE.md` — an explicit admission that four writers must be
edited together.

---

## B. hal0 today (verified)

Everything below was grepped, not assumed.

**Install flow.** `curl -fsSL https://hal0.dev/install.sh | sudo bash` →
`installer/bootstrap.sh` (638) → `installer/install.sh` (4002). Bootstrap's
provenance chain is materially stronger than ODS's: a **digest-pinned cosign**
(per-arch sha256 constants, kept fresh by `scripts/update-cosign-pin.sh` and a
weekly `.github/workflows/cosign-pin.yml`), keyless-OIDC verification of the
*channel manifest bytes* before any JSON is parsed, then sha256 + Sigstore
verification of the release tarball. Channel (`stable|preview|nightly`) is
validated before admission (`bootstrap.sh:241-261`).

**Installer structure.** One 4002-line file with 16 `ui_step` calls
(`UI_STEP_TOTAL=16`, `install.sh:246`, with `tests/install/test_ui_step_total.py`
asserting the constant matches the call count). No step files, no lib/step
separation beyond `lib/{ui,distro,preflight,run-as-hal0}.sh`, no
`Expects/Provides` headers. The ERR trap (`install.sh:248-269`) is genuinely good
— it names `CURRENT_STEP` and prints **step-specific recovery advice** via a
`case`, including the exact `ss -ltnp sport = :PORT` invocation — but that advice
is hardcoded in the trap rather than owned by the step.

**UI.** `installer/lib/ui.sh` (326) is a competent narrator: `ui_banner` (`:122`),
`ui_step` (`:149`, `── (n/16) Title ──`), `ui_spinner_run` (`:178` — background
command + braille spinner + **live tail of the command's last line**, replaying
the last 50 captured lines on failure), `ui_box` (`:270`, ANSI-width-aware), and
`info/warn/err/die` (`:116-119`) with `UI_WARN_COUNT`/`UI_ERR_COUNT` so
`preflight_all` can say "passed with N warning(s)". `HAL0_PLAIN=1`/`NO_COLOR`/
non-TTY all degrade cleanly with ASCII glyph fallbacks — better than ODS.

**Preflight.** `installer/lib/preflight.sh` (2127) is *deeper* than ODS's engine
on substance: `hal0_lxc_kind()` (`:155`) classifies
`none|lxc-privileged|lxc-unprivileged` and decides which remedies are even
possible from inside; `preflight_gpu()` (`:994`) prints the exact Proxmox LXC
`dev0`/gid fix and has a soft/hard mode (`HAL0_GPU_GATE=1`) with distinct return
codes (`HAL0_GPU_RC_BROKEN_GID`, `HAL0_GPU_RC_NO_DEVICE`) so the installer can
*smart-block* a broken passthrough rather than "succeed" into CPU-only;
`preflight_container_runtime()` (`:682`) auto-installs podman under
`HAL0_CONTAINER_REQUIRED=1`; `preflight_ports()` (`:1536`) exempts hal0's own
units from "port in use". Dual-mode — sourced by `install.sh`, executed directly
by `hal0 doctor` (`:1900-1907`). **It emits no JSON and has no fixture corpus.**

**Doctor.** Stronger modelling than ODS. `src/hal0/diagnostics.py` defines frozen
dataclasses `Evidence(kind, summary, data)`, `NextStep(kind, label, target)`,
`Diagnosis(id, severity, confidence, summary, detail, evidence, next_steps,
fixable)`, a **frozen `DIAGNOSIS_IDS` taxonomy** (~30 stable IDs, snapshot-tested)
and `overall_verdict()`. `src/hal0/health_report.py` (298) holds the pure
classifiers shared by `hal0 doctor verify` and `GET /api/doctor`, with a layering
test (`tests/diagnostics/test_layering.py`) forbidding `hal0.api → hal0.cli`.
`cli/doctor_all.py` (1285) is one read-only evidence pass; `doctor_commands.py`
(2272) holds `perms/models/migrations/profiles/ports` with `--fix` autofixes.

**Support bundle.** `src/hal0/cli/doctor_bundle.py:452` (`build_bundle`) —
`hal0 doctor bundle` writes `manifest.json`, `commands.tsv`, `system/`, `config/`
(redacted via the canonical `hal0.api._redact.redact_config`), `diagnostics/`
(doctor JSONs + API payloads), `logs/` (journalctl), `doctor-summary.txt`.
**hal0 already has this.**

**Updater.** `src/hal0/updater/updater.py` (5006 lines): manifest → 0700
root-only staging → sha256 re-derived from the same open file object that gets
extracted → `cosign verify-blob` (`:870`) against a GitHub-Actions OIDC identity
derived from the release kind → extract to `/usr/lib/hal0-<version>/` → config
migrations → `_atomic_symlink_swap()` (`:735`, `os.symlink(tmp)` + `os.replace`)
→ re-pip into the venv, **rolling the symlink back if the re-pip fails** → record
`/var/lib/hal0/hal0.previous`. `rollback()` (`:4851`) swaps back and warns on
schema downgrade. Typed error codes (`system.update_cosign_failed`,
`system.update_swap_failed`, `system.update_rollback_unavailable`).

**Components converge.** `src/hal0/components/registry.py` — one `ComponentDef`
per updatable component (`id, kind, service_id, pinned(), installed(),
converge()`), with `status.py` merging catalog × recorded state × live probes. A
declarative reconcile model ODS has no analogue for.

**Setup.** `hal0 setup` is `hidden=True` and internal-only
(`cli/setup_command.py:1-20`); `install.sh` drives it as
`hal0 setup --auto --no-pull --no-extensions`. The Stage-2 handoff prompt was
deliberately deleted (`install.sh:3985-3997`) with a comment forbidding its
return. README:42 states the contract: "The installer is the whole setup."

**Post-install output.** A `qrencode` QR of the dashboard URL
(`install.sh:3907-3917`, soft-skips when absent), then `ui_box "hal0 is ready"`
with CLI/config/data paths, dashboard/chat URLs, an IPv4+IPv6 **reachability
list**, a repeated `Verify FAILED:` block for failed post-install smoke probes,
and next steps.

**Tests.** `CONTRIBUTING.md:123-215` defines three tiers: **α unit** (`make test`,
pure pytest, ~3s, 425+ tests), **β integration** (documented, but the Makefile
notes it was retired in v0.2), **γ release-gate** (`make release-test`, SSH into
an `hal0-test` LXC, seven-row matrix, `tests/release-gate-report.json`, statuses
`pass|fail|skip|deferred`). Plus `make harness` (`scripts/harness.sh`: installer →
cli → runtime → agents → cleanup, merged JSON), `make harness-install`
(`scripts/fresh-test-ct.sh`, clones a Proxmox CT template for a full
install→smoke→uninstall→destroy cycle, appending JSONL), and
`scripts/release-check.sh` (pre-tag ritual). `tests/installer/` has 30 files;
`tests/install/` 18; `tests/golden_paths/` 5. `tests/release-validation/` adds an
agent-driven RC kit — `kit.toml`, `boxes.toml`, `known-issues.yaml`
(do-not-re-report), `regressions.yaml` (every prior finding re-probed each
release), `lanes/{readonly,stateful,update}/*.md`, committed `reports/*.md` —
which is *ahead of* ODS's `VALIDATION-MATRIX.md` on carry-forward learning.

**Verified absent in hal0:** installer summary JSON; any persisted install log
file (no `tee`, no `LOG_FILE` — output is terminal-only); a failure-report
artifact written to disk; per-step time estimates; a GUI/machine-readable
progress protocol; image-pull retry/backoff (the only pull is a fire-and-forget
`(podman pull … || true) &` at `install.sh:2177`); a preflight JSON report or
fixture corpus; a background model download with hot-swap and rollback; an
installer `--dry-run`; a documented per-step contract table; a high-risk change
map; a known-good-versions baseline doc.

---

## C. Better / worse / equivalent

**ODS is better:**

1. **Installer decomposition.** 14 phase files with written contracts + 19 pure
   libs vs one 4002-line script. This is the biggest gap and it compounds: it is
   why ODS can BATS-test `tier-map`, `compose-select`, `detection`, `progress`,
   and `ui` as units, and hal0 cannot.
2. **Install-time forensics.** `tee "$LOG_FILE"` on every narrator call +
   `write_compose_failure_report()` + an `install-report-*.txt` that doctor later
   mines. hal0 loses the install transcript the moment the terminal scrolls.
3. **Bootstrap-then-upgrade model flow.** ~2 min to first token vs a 15–31 GB
   blocking decision at install time, plus snapshot/rollback around the swap.
4. **Retry discipline on network operations.** `pull_with_progress` (bounded
   attempts, backoff table, post-pull validation, non-retryable classifier) and
   `check_service` (backoff + curl-exit-code classification + container-state
   short-circuit). hal0 has `wait_active` — a flat 0.5s poll — and no pull retry.
5. **Machine-readable install output.** `--summary-json` + preflight JSON +
   doctor JSON, all merged by `simulate-installers.sh` into one artifact set.
6. **Installer dry-run** as a CI lane.
7. **Written per-phase contracts, a reinstall contract, and a high-risk change map.**
8. **Time estimates and a progress percentage** in the UX.

**hal0 is better:**

1. **Update integrity.** cosign keyless-OIDC + sha256 re-derived from the same fd
   + atomic symlink swap + venv re-pip rollback, vs `git pull origin main`. Not close.
2. **Diagnosis modelling.** Typed `Diagnosis/Evidence/NextStep`, a frozen ID
   taxonomy with a snapshot test, one shared classifier for CLI and API. ODS's
   doctor has the same *idea* but implemented as 1730 lines of bash with the
   shape enforced only by convention.
3. **Preflight substance.** LXC privilege classification, GPU device-node +
   gid-mapping remediation, soft/hard gating with distinct return codes,
   auto-install of missing runtimes. ODS's `preflight-engine.sh` is a
   threshold-checker by comparison — more *testable*, not more *capable*.
4. **Degradation quality.** `HAL0_PLAIN`/`NO_COLOR`/non-TTY with ASCII glyph
   fallbacks throughout `ui.sh`; ODS's `type_line` merely skips the animation.
5. **Declarative component convergence** (`components/registry.py`).
6. **Release-validation carry-forward** (`regressions.yaml` + `known-issues.yaml`).
7. **Single-entry-point discipline** — no first-run wizard, enforced by
   `tests/installer/test_install_single_entry_point.py`. ODS still ships a
   dashboard setup wizard alongside its installer.

**Equivalent:** support bundle (both redact, both best-effort); QR code; final
summary card; distro/package-manager abstraction; interactive-vs-piped detection;
idempotent re-run as a stated contract; ADR practice; install-time secret generation.

---

## D. Port candidates, ranked

### D.1 — Module header convention (impact: high, effort: trivial, risk: none)

**ODS:** `docs/INSTALLER-ARCHITECTURE.md` "File Header Convention".
**hal0 target:** `installer/lib/*.sh`, and every extracted step file from D.4.
**Size:** ~12 lines per file. **Deps:** none.

Adopt verbatim, renamed:

```bash
#!/usr/bin/env bash
# ============================================================================
# hal0 installer — <Module Name>
# ============================================================================
# Part of: installer/lib/   (or installer/steps/)
# Purpose: <one line>
#
# Expects: <globals/functions read>
# Provides: <globals/functions defined>
#
# Modder notes:
#   <when and why you'd edit this file>
# ============================================================================
```

Do this **first** — it is the prerequisite that makes D.4 reviewable.

### D.2 — Progress protocol (impact: medium-high, effort: trivial, risk: none)

**ODS:** `installers/lib/progress.sh` (25 lines) — copy essentially verbatim.
**hal0 target:** `installer/lib/ui.sh` (add to the existing lib).

```bash
# Emit structured progress events for a GUI/CI consumer.
# Format: HAL0_PROGRESS:<percent>:<phase_id>:<human message>
# Complete no-op unless HAL0_INSTALLER_GUI=1.
hal0_progress() {
  local percent="$1" phase="$2" message="$3"
  if [[ "${HAL0_INSTALLER_GUI:-0}" == "1" ]]; then
    echo "HAL0_PROGRESS:${percent}:${phase}:${message}"
  fi
}
```

Then one call at the top of each `ui_step`. Immediately buys: a parseable
install transcript for `scripts/fresh-test-ct.sh`, a progress bar for any future
GUI, and a way for the harness to assert *which step* a failed install reached.

### D.3 — Persisted install log + failure report (impact: high, effort: low, risk: low)

**ODS:** `installers/lib/logging.sh` (the `| tee -a "$LOG_FILE"` idiom) and
`installers/lib/compose-failure-report.sh:91-187`.
**hal0 target:** `installer/lib/ui.sh` (`info/warn/err`) plus a new
`installer/lib/failure-report.sh`. **Size:** ~30 + ~150 lines. **Deps:** a
writable log dir. **Risk:** low — must not break `--dev` (no root) or non-TTY.

Two parts. First, tee the narrator — every `info/warn/err` also appends its
ANSI-stripped form to `$HAL0_INSTALL_LOG`. Second, a
`hal0_write_failure_report` invoked from the existing ERR trap, copying ODS's
report *shape*: privacy note → step name → environment (`systemctl --failed`,
`podman info`, `podman images`) → port occupancy with owning process →
**redacted** `api.env`/`hal0.toml` → last 160 lines of the install log →
`journalctl -u hal0-api -n 100`. Reuse `hal0.api._redact` rather than ODS's awk
redactor (hal0's is better) and print the path in the trap so the user has one
file to attach. Payoff: `hal0 doctor` gains an `install_artifacts` surface exactly
as ODS's does, and a `HAL0-INSTALL-*` diagnosis family can key off it.

### D.4 — Decompose `install.sh` into steps (impact: very high, effort: high, risk: medium)

**ODS:** `install-core.sh` + `installers/phases/01..13`.
**hal0 target:** `installer/install.sh` as a ~300-line orchestrator +
`installer/steps/01-preflight.sh … 16-summary.sh`. **Size:** 4002 lines
redistributed, ~0 net new logic. **Deps:** D.1. **Risk:** medium — one global
bash namespace, and any missed `set -u` interaction bites at install time.
Mitigate by moving one step per PR, keeping `UI_STEP_TOTAL` and its test green
throughout, and running `make harness-install` per move.

Keep ODS's exact discipline: `installer/lib/*` defines functions only and is safe
to source; `installer/steps/*` executes on source; the orchestrator sets
`CURRENT_STEP` (already exists) before each `source`. Then write
`docs/internal/install-step-contracts.md` modelled on
`docs/INSTALLER_PHASE_CONTRACTS.md` — Owns / Inputs / Outputs / Idempotency /
Failure modes per step — plus hal0's own **Reinstall Contract** (hal0 claims
idempotency in the README; nothing states what that guarantees). This is the
change that converts hal0's installer from "a script we're careful with" into "a
system with per-unit tests".

### D.5 — Retry-protected image pulls (impact: high, effort: low, risk: low)

**ODS:** `installers/lib/ui.sh:269-370`. **hal0 target:** a `ui_pull_with_retry`
alongside `ui_spinner_run`, plus the Python pulls in
`src/hal0/components/runner_images_arm.py` / `cli/runner_image_commands.py`.
**Size:** ~70 lines bash, ~40 Python.

Copy three ideas, not the code: (1) a **backoff table** with env override
(`5 15 30`, then doubling); (2) a **post-pull validation** step
(`podman image inspect`) — a "successful" pull that produced nothing is a real
failure mode; (3) a **non-retryable classifier** that bails immediately on
`unauthorized|denied|not found|404|no space left on device|cannot connect`.
hal0's current `(podman pull … || true) &` at `install.sh:2177` silently accepts
every one of those.

### D.6 — Preflight JSON report + fixture corpus (impact: high, effort: medium, risk: low)

**ODS:** `scripts/preflight-engine.sh` + `tests/contracts/test-preflight-fixtures.sh`.
**hal0 target:** `installer/lib/preflight.sh` gains a `--report PATH` /
`HAL0_PREFLIGHT_REPORT` writer; `tests/installer/test_preflight_fixtures.py` gains
the corpus. **Size:** ~80 lines in preflight.sh, ~120 test lines. **Deps:** none.

Do **not** port ODS's engine — hal0's checks are better. Port the *contract*:
every `preflight_*` appends `{id, severity, summary, detail, next_steps}` to an
array; `preflight_all` serialises it, reusing the `Diagnosis` field names from
`src/hal0/diagnostics.py` so the JSON is already doctor-shaped, with
`HAL0-PREFLIGHT-*` IDs reserved in `DIAGNOSIS_IDS`. Then add the fixture table —
named scenarios with injected inputs asserting blocker counts:

```
linux-cpu-good           → 0 blockers
lxc-unprivileged-no-gpu  → 0 blockers, ≥1 warning
lxc-broken-gid           → 1 blocker  (HAL0-GPU-BROKEN-GID)
disk-below-floor         → 1 blocker
port-8080-foreign        → 1 blocker
port-8080-own-unit       → 0 blockers  (the exemption path)
```

hal0's preflight already has the hardest part (the detection); it just has no way
to prove the *verdicts* stay stable across refactors.

### D.7 — Bootstrap-then-upgrade model flow (impact: very high, effort: high, risk: medium-high)

**ODS:** `installers/lib/bootstrap-model.sh` (57), `phases/11-services.sh:673-690`
and `:1329-1372`, `scripts/bootstrap-upgrade.sh` (3412),
`installers/lib/model-lifecycle-lock.sh` (83), `installers/lib/background-tasks.sh` (194).
**hal0 target:** `src/hal0/install/agent_model.py` (which already computes a
curated plan and a size string) + a new `src/hal0/install/background_pull.py` +
a `hal0-model-pull@.service` transient unit. **Size:** ~400 lines Python if
written natively. **Deps:** the slot-config writer, the registry, `systemd-run`.
**Risk:** medium-high — this is the one item where copying the *implementation*
would be a mistake.

hal0's shape is already better suited: it has slots, a registry, and a brain model
that *is* the "tiny model first". The port is conceptual:

1. Keep the unconditional `lfm2.5-2.6b` brain pull (that is the bootstrap model).
2. Replace the blocking 15–31 GB agent-model prompt with: ask, then hand the pull
   to a **transient systemd unit** (`systemd-run --unit=hal0-model-pull@…`)
   rather than `nohup` — hal0 is a systemd product and gets journald logging,
   restart policy, and `systemctl status` for free.
3. Copy ODS's **status-file contract** (`data/bootstrap-status.json`:
   `status/percent/bytesDownloaded/bytesTotal/speedBytesPerSec/eta`, written
   atomically) so the dashboard can render progress.
4. Copy the **transaction snapshot** shape verbatim
   (`bootstrap-upgrade.sh:305-380`) — snapshot the slot TOML + registry entry
   before binding, restore on failure, and use a `.missing` sentinel so restore
   can delete a file that did not previously exist:
   ```bash
   snapshot_file_state() {  # writes <snap> or <snap>.missing
       if [[ -f "$src" ]]; then cp -p "$src" "$snap"; else : > "$snap.missing"; fi
   }
   restore_file_state() {
       if   [[ -f "$snap"          ]]; then cp -p "$snap" "$dst"
       elif [[ -f "$snap.missing"  ]]; then rm -f "$dst"; fi
   }
   ```
5. Copy the **verified-before-cleanup** rule: only delete the old model after the
   new slot has answered a real completion (`HOT_SWAP_VERIFIED=true`,
   `bootstrap-upgrade.sh:3200-3202`).
6. Copy the **lock**: `model-lifecycle-lock.sh` is 83 lines and directly reusable —
   the installer and a background pull must not both rewrite a slot config.
   hal0 already has `hal0_config_file_lock` in `config/loader.py`; extend that
   rather than adding a second mechanism.

### D.8 — `--summary-json` (impact: medium, effort: low, risk: none)

**ODS:** `installers/phases/13-summary.sh:435-502`, including the
`mkstemp`+`fsync`+`os.replace` atomic write. **hal0 target:** the `install.sh`
summary block, writing `${VAR_DIR}/install-summary.json` by default. **Size:** ~40
lines. Payload: `version`, `channel`, `install_root`, `models_dir`, `slots[]`,
`services[]` with start verdicts, `smoke_failed[]`, `preflight_report` path.
`scripts/fresh-test-ct.sh` currently parses terminal output; this replaces that
with a contract.

### D.9 — Time estimates on steps (impact: medium, effort: trivial, risk: none)

**ODS:** `show_phase 4 6 "Downloading Modules" "~5-10 min + ~30 min ComfyUI build"`.
**hal0 target:** `ui_step "Python environment" "~2 min"` — one optional second
arg, rendered dim at the right of the rule. Honest ranges matter more than
precision; ODS's ComfyUI line is the model.

### D.10 — Installer `--dry-run` + simulate harness (impact: medium, effort: medium, risk: low)

**ODS:** every phase guards mutations with `if $DRY_RUN`;
`scripts/simulate-installers.sh` runs it in CI and merges artifacts. Feasible for
hal0 only *after* D.4 — a 4002-line script cannot be retrofitted with dry-run
guards safely. Add `HAL0_DRY_RUN=1` per step, then a `scripts/simulate-install.sh`
emitting `artifacts/install-sim/SUMMARY.json` from D.8 + D.6.

### D.11 — Governance docs (impact: medium, effort: low, risk: none)

Port three, adapted: `docs/HIGH_RISK_CHANGE_MAP.md` → a `CONTRIBUTING.md` section
mapping area → risk level → required validation (hal0 has the tiers α/γ/harness/
rc-validate but no table saying *which* to run for a given diff — cheapest
reliability win available); `docs/KNOWN-GOOD-VERSIONS.md` →
`docs/reference/known-good-versions.mdx` with tested kernel/podman/ROCm/driver
baselines per hardware class, as the support-triage companion to the existing
`hardware-matrix.mdx`; and `INSTALLER_PHASE_CONTRACTS.md`'s **Reinstall Contract**
→ `installer/README.md`, because hal0 asserts idempotency without defining it.

---

## E. Do NOT copy

1. **`ods-update.sh`'s update mechanism.** `git fetch && git pull origin main`
   with no signature verification, into the live install directory. hal0's
   cosign-verified, staged, atomic-symlink updater is a different class of thing;
   copying here would be a downgrade. (The pre-update *snapshot* discipline at
   `:235-320` is worth reading, but `hal0.previous` + config migrations already
   cover that ground.)
2. **The CRT/"lore" theme** — `LORE_MESSAGES[]`, `show_stranger_boot()`,
   `type_line_dramatic()`, "THE GATEWAY IS OPEN", the terminal bell. The
   mechanism underneath (print *something* every N seconds so a long pull doesn't
   look hung) is worth having, and `ui_spinner_run` already does it better by
   tailing the command's real output. The ideology is not hal0's voice and adds
   100+ lines of untestable string data.
3. **`type_line()` character-by-character animation** (`ui.sh:28-49`) — a 0.035s
   sleep per character is dead wall-clock on every install and mangles log capture.
4. **`scripts/bootstrap-upgrade.sh` as a file** — 3412 lines with Windows
   Lemonade, macOS native-PID, and Linux Docker paths interleaved under
   `set -uo pipefail` without `-e`. Port the five ideas in D.7; not the artifact.
5. **The Tauri desktop installer.** hal0 installs `curl | sudo bash` onto a
   headless box; a GUI wrapper solves a problem hal0 does not have. The one
   transferable piece is the 25-line progress protocol (D.2), useful without a GUI.
6. **Duplicating the phase model per platform** (`installers/windows/phases/*.ps1`,
   `installers/macos/lib/tier-map.sh`). ODS pays for this with the "Generated
   Config Writers" sync table. hal0 is Linux+systemd only — do not acquire that
   cost speculatively.
7. **`ADR-IMAGE-TAG-PINNING.md`'s conclusion** (retain `:latest` for three
   third-party images). hal0 pins by digest with lockstep tests
   (`tests/installer/test_owui_pin_lockstep.py`, `test_hindsight_pin_lockstep.py`)
   and a weekly cosign-pin workflow. The ADR's *format* is worth emulating; its
   decision is not.
8. **`ods-preflight.sh` (the runtime one)** — `hal0 doctor verify` +
   `health_report.py` already cover that surface with typed results.

---

## F. Decisions for the owner

1. **Decompose `install.sh` — yes or no?** The load-bearing decision. 4002 lines
   in one file is why hal0 has no per-step tests, no dry-run, no per-step
   contracts, and why the ERR trap hardcodes recovery advice for four named steps.
   Cost: several weeks of one-step-at-a-time PRs with `make harness-install`
   between each. Recommendation: **yes**, sequenced D.1 → D.3 → D.4, so the
   headers and the install log land first and make the decomposition reviewable.

2. **Background model pull: systemd unit or nohup?** ODS uses `nohup` + a PID
   registry because it has no service-manager guarantee; hal0 does. Recommend
   `systemd-run --unit=hal0-model-pull@<slot>` — journald logging, `systemctl
   status`, and restart policy for free, with the status JSON reduced to a
   dashboard-progress concern rather than the only observability.

3. **Should preflight emit `Diagnosis` rows?** Reusing `diagnostics.py`'s field
   names from bash means preflight output drops straight into `hal0 doctor --json`
   and the support bundle with no translation layer, and preflight IDs join the
   frozen taxonomy. The cost is a contract `installer/lib/preflight.sh` must not
   drift from. Recommend yes, reserving `HAL0-PREFLIGHT-*` IDs up front (ODS
   reserves IDs before the emitter lands; same pattern).

4. **Where does the install log live?** ODS uses `/tmp/ods-install.log`, lost on
   reboot. Options: `${VAR_DIR}/logs/install-<ts>.log` (survives; needs the dir
   before the filesystem step) or journald via `systemd-cat` (survives, no
   ordering problem, harder to attach to an issue). Recommend the file, created in
   step 1, path echoed by the ERR trap — and have `hal0 doctor bundle` glob it and
   any failure report into `diagnostics/`, so a support case gets evidence the
   user never thought to send.

5. **Is "the installer is the whole setup" negotiable?** Currently enforced by a
   test and a do-not-reintroduce comment. Every port above respects it — D.7
   *strengthens* it by removing the one install-time question big enough to make
   people defer. Worth re-affirming before D.4 starts, since decomposition is
   exactly when a "quick post-install wizard step" gets proposed.

6. **What hal0 does *not* need from ODS:** multi-platform installers, compose-stack
   resolution, tier→model mapping (slots + the curated ladder already generalise
   it), and the desktop GUI. Do not let the breadth of ODS's installer tree imply
   hal0 is missing surface area — hal0 is missing *structure and instrumentation
   on the surface it already has*.
