# Hal0 Installer Setup — Redesign Plan (2026-07-05)

Companion to `handoffs/installer-setup-research-2026-07-05.md` (the 11-reader research map).
This doc records the 17 decisions resolved in the grilling session and the workstream plan.

## Decision ledger

| # | Decision | Resolution |
|---|----------|-----------|
| Q1 | Privilege model | **Stay root, own it explicitly.** hal0-api stays root; guided setup requires root. Create the `hal0` user EARLY (before mutation), not at install.sh:1237. Stop pretending the non-root flip is coming (perms.py:101). |
| Q2 | GPU/NPU preflight | **Smart block.** Run `preflight_gpu` during install. Device nodes present but wrong gid (LXC miswire) → HARD STOP + exact `dev0` remedy + retry. No nodes + inside LXC → loud warn, opt-in continue. Bare-metal no-GPU → proceed silently. |
| Q3 | Network bind | **Ask up front, default LAN.** One `HAL0_BIND_HOST` read by BOTH unit and `hal0 serve`. Derive+seed `HAL0_ALLOWED_ORIGINS` + `HAL0_HOSTNAME`/mDNS from the same answer. Close `/api/install/*` after first-run. |
| Q4 | Model store | **Mandatory prompt.** Validate writable + free-space on the chosen mount, warn on root-FS/small. Seed BOTH `[models].store` AND `[models].flm_store` (co-located). Hard-gate ALL downloads until set. `--models-dir` honored for `--auto`. |
| Q5 | HF_TOKEN | **Gather up front, optional-but-recommended.** Optional `whoami` validation (warn not fail). Persist to root:root `secrets/` EnvironmentFile (NOT 0644 api.env). Thread into `apply_setup`/in-process pulls (kills the 401). Pre-fill from env. |
| Q6 | Seeded slots | **Clean seed.** No `[model].default` pin (boot grey). Derive device/profile from the up-front preflight. Delete the ghost id. Reconcile `qwen3tts` into/out of the seed loop. |
| Q7 | Slot activation | **Enable-on-pull-success + clamp context.** Create disabled, queue pull, flip enabled on pull completion. Clamp `context_size` to a hardware budget (fixes the `oom` artifact). |
| Q8 | ComfyUI/gen | **Scaffold-only default; per-variant download opt-in AFTER fetch fix.** Fetch fix is a hard prereq: podman-exec `get_*.sh` in the img container (or host-resolvable `hf`) + forward `HF_TOKEN`/`HF_HOME` + ship workflow JSON per variant + assign model_meta/profile. Picker shows size+time. |
| Q9 | Hermes/OWUI skip | **Both skippable; build the missing verbs.** Add `hal0 app install openwebui` + `HAL0_SKIP_OPENWEBUI`. Fold the Telegram/Discord gateway enable into the deferred `hal0 agent install hermes` path. Same models/memory/perms wiring now-or-later. |
| Q10 | Install/update divergence | **Extract ONE reconcile seam** shared by first-install and update, so a fresh box ≡ an updated box for the same slot. Its own workstream. |
| Q11 | Post-update restart | **Surface drift loudly + opt-in `--restart-slots`.** Detect stale units, banner (CLI+dashboard), never auto-bounce mid-inference. |
| Q12 | Verify suite | **Report card via reusable `hal0 doctor --verify`,** auto-run at setup end. Pass/warn/fail + live URLs + links (hal0.dev/first-run-guide, docs, Discord, website). Non-blocking; criticals flagged red. |
| Q13 | Execution model | **Two-stage, strengthened.** Stage 1 (install.sh/one-liner): non-interactive system prep + full platform gate (auto-fix; hard-block exits with remedy; `--no-pull`). Stage 2 (`hal0 setup`): interactive guided flow in a real TTY. If Stage 1 sees a TTY, offer to launch it inline; else print the command. |
| Q14 | hardware.json | **Persist at install,** including NPU functional (`flm validate`) result. Slots/FLM read real facts, not Strix-Halo constants. |
| Q15 | NPU opt-in | **One `npu_opt_in` threaded everywhere.** NPU intro shown only when present AND passthrough healthy; present-but-broken shows the fix. |
| Q16 | Apply endpoints | **Converge on `/apply-selections` core;** `/apply` is a tier→selections adapter. The apply endpoint writes the first-run sentinel (not just the CLI path). |
| Q17 | Canonical URL | **`GET /api/config/urls` is the one source.** Banner, QR, dashboard, and seeded `HAL0_ALLOWED_ORIGINS` all derive from it. Delete the thinmint constant. |

## Guided setup flow (Stage 2 target)

```
[Stage 1 done → Platform Report printed: LXC/bare-metal, GPU/NPU visibility+gid, podman, distro/FLM, disk]
   ↓  (auto-fix actions previewed before the long tail)
Q1 Network shape   → HAL0_BIND_HOST + seed origins/mDNS + (optional) reverse-proxy public URLs
Q2 Model store     → validate + free-space; write [models].store AND [models].flm_store
Q3 HF token        → optional whoami; persist to secrets/; thread into pulls
Q4 LLM slots       → main + coder (suggest_models, hw-budgeted ctx); seed disabled → enable on pull
Q5 NPU intro       → iff present+healthy; enable FLM trio; broken → show dev0 fix + retry
Q6 ComfyUI/gen     → off | scaffold-only [default] | scaffold+download (per-variant picker)
Q7 Apps            → OpenWebUI (now|later), Hermes (now|later, +gateway), Pi coder
REVIEW             → "will create" table (slots, ports checked, pulls, bind, store, disk) → Build?
   ↓
apply_setup → dispatch pulls → write sentinel → `hal0 doctor --verify` report card + live URLs + links
```

## Workstream plan (4 waves)

**Wave 1 — foundation (low-risk, unblocks the rest)**
- WS-A Quick-kill bugs: delete ghost id (`utility.toml:11`); thread `hf_token` into `_apply_in_process`→`apply_setup` (Q5/Q16); thread or drop `storage_dir` (`orchestrate.py`); delete stale `packaging/avahi/hal0.service`.
- WS-B Up-front platform gate (Stage 1): fold `preflight_gpu` + LXC smart-block/remedy/retry into `install.sh:219`; fold bootstrap prereqs into install.sh (direct-path parity); create `hal0` user early; disk measured on chosen store; persist authoritative `hardware.json` incl. NPU `flm validate` (Q2/Q13/Q14).
- WS-C Network coherence: `GET /api/config/urls` source of truth; unify `HAL0_BIND_HOST` (unit + serve); seed `HAL0_ALLOWED_ORIGINS`+`HAL0_HOSTNAME`; **delete thinmint constant** (`setup_install.py:36,89,180`); close `/api/install/*` after first-run (Q3/Q17).

**Wave 2 — downloads + seeds**
- WS-D Download gating: mandatory models-dir prompt + flm_store co-locate + free-space; HF_TOKEN gather→secrets→thread (Q4/Q5).
- WS-E Clean seeds + safe activation: strip `[model].default`; derive device from preflight; enable-on-pull-success; context clamp; reconcile qwen3tts (Q6/Q7).
- WS-I Apply convergence: `/apply`→adapter over `/apply-selections`; endpoint writes sentinel (Q16).

**Wave 3 — guided flow + apps + gen**
- WS-F Guided interactive setup (Stage 2 TUI): the decision tree above; thread one `npu_opt_in` (Q13/Q15).
- WS-G ComfyUI fetch fix + branch: podman-exec fetch + forward token + ship workflow JSONs + per-variant picker + model_meta/profile (Q8).
- WS-H Apps parity: `hal0 app install openwebui` + `HAL0_SKIP_OPENWEBUI`; gateway enable in deferred hermes path (Q9).

**Wave 4 — convergence + verification**
- WS-J Reconcile seam: one render routine for install+update; post-update drift surfacing + `hal0 update --restart-slots` (Q10/Q11).
- WS-K Verify suite: `hal0 doctor --verify` report card, auto-run at setup end, live URLs + doc/Discord links (Q12).

## Suggestions (beyond the ask)
- **`hal0 setup --plan`/`--dry-run`**: print the "will create" table without writing (safe preview; also a great test surface).
- **Headless contract**: a `hal0-setup.yaml` answer file so `--auto` is fully reproducible/CI-able and encodes every Stage-2 choice — the non-interactive twin of the guided flow.
- **Resumable setup**: on a mid-run failure, `hal0 setup` resumes from the last completed step (the sentinel becomes a step ledger, not a single bit).
- **Idempotent re-run**: re-running `hal0 setup` on a live box reconciles (uses WS-J's seam) rather than duplicating.
- **First-run telemetry link + QR** already exists (`install.sh:1628`); point it at the computed URL from WS-C and add the first-run-guide/Discord links from WS-K.
