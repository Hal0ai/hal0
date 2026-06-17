# ComfyUI integration — follow-up handoffs (for a Codex agent new to hal0)

Context: the ComfyUI platform-integration work shipped as **PR #878** (branch `feat/comfyui-platform`),
merged "partial" with known gaps filed as issues **#872–#877**. #872 is already fixed on the branch.
These handoffs cover the rest. **Read "0. Shared onboarding" first — every task depends on it.**

---

## 0. Shared onboarding (READ FIRST)

### 0.1 Where things are
- **Main checkout** (other sessions use it; do NOT edit here): `/home/halo/dev/hal0` (branch varies).
- **Your worktree** (do your work here): `/home/halo/dev/wt/comfyui-integration`, on branch `feat/comfyui-platform`.
  A git *worktree* is a second working dir sharing one `.git`. Edits here are isolated from the main checkout.
- **The Python venv lives in the main checkout**, not the worktree: use `/home/halo/dev/hal0/.venv/bin/python`
  (and `/home/halo/dev/hal0/.venv/bin/ruff`). There is no venv inside the worktree.
- ComfyUI code: `src/hal0/comfyui/` (capabilities.py, fetch.py, selection.py).
- ComfyUI API routes: `src/hal0/api/routes/comfyui.py`.
- Installer assets: `installer/comfyui/` (scripts/, extra_model_paths.yaml, custom_nodes/).
- Plan + this doc: `docs/superpowers/plans/2026-06-16-comfyui-platform-integration.md`, `docs/superpowers/handoffs/`.

### 0.2 Running tests + lint (IMPORTANT)
- **Never run the whole suite** (`pytest tests/`) — it hangs (a health probe blocks). Always target files:
  ```bash
  cd /home/halo/dev/wt/comfyui-integration
  PYTHONPATH=src /home/halo/dev/hal0/.venv/bin/python -m pytest tests/comfyui -q
  ```
- Lint is **ruff, not black**:
  ```bash
  /home/halo/dev/hal0/.venv/bin/ruff check <files>      # must report "All checks passed!"
  /home/halo/dev/hal0/.venv/bin/ruff format <files>     # auto-format before committing
  ```
  Project selects rules `E,F,I,B,UP,SIM,RUF` (so `RUF006` unstored-task, `RUF012` mutable-classvar,
  `B905` zip-strict all fail CI — handle them).
- Frontend (only for UI tasks): `cd ui && npm run build` (tsc+vite) and
  `cd ui && npx playwright test <spec-substring>`.

### 0.3 Git flow — pick ONE of these two situations
**A) PR #878 is still OPEN (check: `gh pr view 878 -R Hal0ai/hal0 --json state -q .state`).**
Do the fix directly on `feat/comfyui-platform` so it joins that PR:
```bash
cd /home/halo/dev/wt/comfyui-integration
git pull --rebase origin feat/comfyui-platform   # pick up anything pushed since
# ... make changes, TDD ...
git add -A
git commit -m "fix(comfyui): <what> (#<issue>)"
git push                                          # updates PR #878
```

**B) PR #878 is already MERGED.** Branch fresh from `main`, one branch + PR per issue:
```bash
cd /home/halo/dev/hal0
git fetch origin
git worktree add -B fix/comfyui-<issue> /home/halo/dev/wt/comfyui-<issue> origin/main
cd /home/halo/dev/wt/comfyui-<issue>
# ... TDD, commit ...
git push -u origin fix/comfyui-<issue>
gh pr create -R Hal0ai/hal0 --base main --head fix/comfyui-<issue> --title "..." --body "...Fixes #<issue>..."
```

**Commit/PR conventions** (required):
- Conventional-commit prefix: `fix(comfyui): ...`, `feat(comfyui): ...`, `style(comfyui): ...`.
- End commit messages with:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- End PR bodies with: `🤖 Generated with [Claude Code](https://claude.com/claude-code)`
- Put `Fixes #<n>` in the PR body so the issue auto-closes on merge.
- The repo remote is `origin` = `https://github.com/Hal0ai/hal0.git`. `gh` is authed (account `thinmintdev`).

**Before merging PR #878 specifically:** it is BEHIND `main`. Update it first:
`gh pr update-branch 878 -R Hal0ai/hal0` (or `git merge origin/main` in the worktree, resolve, push).
Only merge when all CI checks are green (`gh pr checks 878 -R Hal0ai/hal0`).

### 0.4 CT105 deploy flow (ONLY tasks #874, Phase 1, 6.3 need this — the rest are code-only)
CT105 (a.k.a. `hal0`, `10.0.1.142`) is the **GPU runtime box** and a **SHARED host** — other sessions use it.
- SSH: `ssh hal0` (alias for `root@10.0.1.142`).
- **Coordinate before touching `/opt/hal0`:**
  ```bash
  ssh hal0 'cd /opt/hal0 && git branch --show-current && git status --porcelain'
  ```
  If branch ≠ `main` OR there are uncommitted tracked edits → **another session is working there. Stop and
  coordinate; do not `git checkout`/`reset`/deploy over it.**
- **Deploy a branch (preview):** `deploy.sh` rebuilds `ui/dist` (gitignored — a bare `git reset` leaves the
  dashboard stale, so always use the script) + restarts + healthchecks:
  ```bash
  ssh hal0 'cd /opt/hal0 && sudo bash scripts/deploy.sh --ref origin/feat/comfyui-platform'
  ```
- **After PR merges, reconcile CT105 back to main:**
  ```bash
  ssh hal0 'cd /opt/hal0 && sudo bash scripts/deploy.sh --ref origin/main'
  ```
- Model store on CT105: `/mnt/ai-models/comfyui/` (writable ZFS bind). ComfyUI container = `comfyui`, port `8188`,
  brought up by `/opt/comfyui/comfy-up.sh`. iGPU is gfx1151; only one renderer can hold it at a time.

### 0.5 Record your work (project rule)
After creating/merging a PR or finishing a task, record a one-line memory via the hal0 memory engine
(skill `hal0-memory` → `memory_add`, dataset `shared`, `document_id` like `pr-<n>` / `issue-<n>`, with the *why*).

---

## 1. #876 — remove stale Lemonade inference scripts  *(easiest; code-only)*

**Goal:** `installer/comfyui/scripts/start-inference.sh` and `stop-inference.sh` call the **removed**
`hal0-lemonade.service` (Lemonade was deleted from hal0 months ago) and bypass the GPU arbiter. They are a
footgun shipped to `/opt/comfyui`. Remove them.

**Why they exist:** they were copied verbatim from CT105 (pre-Lemonade-removal era) during PR #878.

**Steps:**
1. Confirm the dead refs: `grep -n lemonade installer/comfyui/scripts/start-inference.sh installer/comfyui/scripts/stop-inference.sh`
   (you'll see `systemctl start/stop hal0-lemonade.service`).
2. `git rm installer/comfyui/scripts/start-inference.sh installer/comfyui/scripts/stop-inference.sh`
3. Grep for anything that referenced them so you don't leave a dangling caller:
   `grep -rn "start-inference\|stop-inference" src/ installer/ tests/ packaging/`
   - `installer/install.sh` copies `installer/comfyui/scripts/*.sh` with a glob, so removing the files is enough
     (no edit needed) — but **verify** the install.sh block uses `*.sh` and not an explicit list; if explicit, drop them there too.
   - If `tests/install/test_comfyui_scripts_shipped.py` asserts these two exist, update it to not expect them.
4. Run: `PYTHONPATH=src /home/halo/dev/hal0/.venv/bin/python -m pytest tests/install -q` → green.
5. Commit `fix(comfyui): drop stale Lemonade start/stop-inference scripts (#876)`.

**Done when:** the two scripts are gone, no caller references them, `tests/install` green.

---

## 2. #877 — review follow-ups (do the two real ones; rest are optional polish)

### 2a. I4 — exception leak + unguarded workflow lookup *(code-only, do this)*
**Files:** `src/hal0/api/routes/comfyui.py` — the workflow-launch route (~line 590–625) and `_find_workflow` (~line 625).
**Problem:** the launch route catches `KeyError as exc` and returns `{"message": str(exc)}` (~line 599–605),
echoing internal exception text to the client; `_find_workflow(name)` builds a path from `name` without validating it.
**Note:** FastAPI's `{name}` path param won't match a `/`, so classic path traversal is already blocked — but
harden anyway and stop leaking exception strings.
**Steps (TDD):**
1. Add tests in `tests/api/test_comfyui_phase4.py`: launching an unknown workflow returns 404 with a generic
   message (no raw exception text); a `name` containing `..` or `/` is rejected (422/404), never reads outside the
   workflows dir.
2. In `_find_workflow`, validate `name` matches `^[A-Za-z0-9._-]+$` (reject otherwise → return None); in the route,
   replace `str(exc)` with a generic message and log the detail server-side.
3. `PYTHONPATH=src /home/halo/dev/hal0/.venv/bin/python -m pytest tests/api/test_comfyui_phase4.py -q` → green; ruff clean.
4. Commit `fix(comfyui): validate workflow name + stop leaking exception text (#877)`.

### 2b. I5 — image identity divergence *(small; mostly documentation)*
**Problem:** the ComfyUI image is referenced inconsistently:
- `manifest.json:34` → `docker.io/kyuz0/amd-strix-halo-comfyui:latest` (mutable **tag**)
- `src/hal0/config/schema.py:769` (SEED_PROFILES) → same `:latest` tag
- `installer/etc-hal0/profiles.toml:76` → same `:latest` tag
- but `installer/comfyui/scripts/comfy-up.sh` pins a **digest** `@sha256:0066678a…`
**Decision already made:** we keep consuming kyuz0's image this week (Phase 1 build deferred). So the fix is to make
all four agree on the **same digest** (reproducible), not the mutable `:latest`.
**Steps:**
1. Get the digest comfy-up.sh uses (it's in the file): `grep sha256 installer/comfyui/scripts/comfy-up.sh`.
2. Replace the `:latest` tag with `@sha256:<that digest>` in `manifest.json`, `schema.py:769`, `profiles.toml:76`.
3. If `tests/config/` asserts the comfyui profile image, update the expectation.
4. Run `tests/config` + `tests/comfyui` targeted → green; ruff clean.
5. Commit `fix(comfyui): pin comfyui image to one digest across manifest/profile/seed (#877)`.

### 2c. Optional polish (only if time; otherwise leave in #877)
- `Selections.comfyui_defaults` sidecar → a dedicated `ComfyUISelection` type (refactor).
- installer repair `if unit == "comfyui"` → a `_REPAIR_HANDLERS` dispatch table.
- UI: replace `alert()` log display with a drawer; preview staleness during render.
Skip unless asked; they aren't correctness bugs.

---

## 3. #873 — wire real GPU telemetry into `/status` *(code-only; you'll feel this in the UI)*

**Goal:** the V2 pane's device grid (util / temp / clock) renders **0** because `/api/comfyui/status` only returns
memory (gtt/ram) + queue + inventory. Add `util`, `temp`, `clock` from hal0's existing GPU telemetry.
`it_s`/`eta`/`step` need a ComfyUI websocket subscription — leave those as a documented follow-up (emit the keys as
`null`, don't fake them).

**Where hal0 reads the iGPU** (reuse, don't reinvent): `src/hal0/hardware/gpu_view.py`, `src/hal0/hardware/stats.py`,
`src/hal0/providers/_gpu.py`, `src/hal0/api/routes/power.py`, `src/hal0/api/routes/slots.py`. Find the function that
returns temperature / clock (sclk) / utilization and call it from comfyui.py's status builder.

**Critical gotcha (from prior incident):** raw `gpu_busy_percent` is a **forced-high artifact** on this box — the
GPU is pinned to perf-level "high" for inference, so it reads ~100% even when idle. **Do not report
`gpu_busy_percent` as `util` unconditionally.** Gate it: report util only when a render is actually active
(there's a running job in the ComfyUI queue); otherwise `util = 0` (or null). Temp/clock are real — report them directly.

**Steps (TDD):**
1. In `src/hal0/api/routes/comfyui.py` find the `/status` handler (`comfyui_status`, ~line 321) and the helper that
   assembles the payload. Add a `telemetry`/device block: `{util, temp, clock}` (keep existing `memory` gtt/ram).
2. Add tests to `tests/api/test_comfyui_phase4.py` (or a new `tests/api/test_comfyui_telemetry.py`): mock the
   hardware helper; assert temp/clock pass through; assert util is 0/None when queue idle and the real value when a
   job is running; assert `it_s`/`eta`/`step` keys exist and are null. Tests must be **fail-soft** — if the hardware
   probe raises, `/status` still returns 200 (never 500), matching the existing fail-soft pattern in this module.
3. Update `ui/src/api/hooks/useComfyui.ts` `transformComfyuiStatus()` to map the new fields into the pane's STATS
   object, and adjust `ui/tests/e2e/specs/comfyui-arbiter-v3.spec.ts` (or imagegen-v2) mock payloads to include them.
4. `pytest tests/api -q` (targeted) + `cd ui && npm run build && npx playwright test comfyui imagegen` → green; ruff clean.
5. Commit `feat(comfyui): real util/temp/clock telemetry in /status (#873)`.

**Done when:** pane device grid shows live temp/clock, util only during renders; `it_s`/`eta`/`step` null; all green.

---

## 4. #875 — wire the hardened (non-root) control path (sudoers) *(code-only; needed before hardened ship)*

**Goal:** on a hardened install, `hal0-api` runs **non-root**, so the container-control routes (`/restart`, repair,
`install_extension`) that shell `comfy-up.sh`/`podman`/`docker` will 403. There's an orphaned
`packaging/sudoers/hal0-comfyui` referencing a `comfyui.py:_script_argv` + `sudo -n` that don't exist, the file is
never installed, and the planned `tests/install/test_comfyui_sudoers.py` is missing.

**Decision context:** on the dev box (CT105) hal0-api runs as root, so this isn't blocking *today* — but it must work
for the shipped hardened product. Scope: make the control paths run privileged via a single, audited sudoers entry.

**Steps:**
1. Read `packaging/sudoers/hal0-comfyui` (current orphan) and `src/hal0/api/routes/comfyui.py` `/restart` +
   `installer.py` repair to see exactly which commands need privilege (`/opt/comfyui/comfy-up.sh`, `comfy-down.sh`,
   `podman`/`docker` inspect/restart).
2. Make the sudoers file grant `hal0` NOPASSWD for *exactly* those command paths (no wildcards). `visudo -cf
   packaging/sudoers/hal0-comfyui` must pass.
3. Have the routes invoke via `sudo -n <script>` when not root (detect EUID); run directly when root.
4. Add `install -m0440 packaging/sudoers/hal0-comfyui /etc/sudoers.d/hal0-comfyui` to `installer/install.sh`
   (near the other `/etc` asset installs).
5. Add `tests/install/test_comfyui_sudoers.py`: file installed by install.sh (grep the install.sh line), command
   paths in sudoers == the paths the routes actually call, `visudo -cf` clean.
6. `pytest tests/install tests/api -q` (targeted) → green; ruff clean.
7. Commit `feat(comfyui): hardened sudoers for container control (#875)`.

**Done when:** non-root control path is wired + tested; sudoers installed by the installer; no wildcard grants.

---

## 5. #874 — reconcile `comfy-up.sh` (standalone `docker run`) vs the arbiter/podman slot *(needs CT105)*

**This is the subtle one — do NOT blind-edit; verify against the live box first.**

**Problem (from review):** `installer/comfyui/scripts/comfy-up.sh` does `docker run` a *standalone* container named
`comfyui` on `:8188`. But hal0's slot system has a `GpuArbiter` (`src/hal0/slots/arbiter.py`) that manages a podman
slot `hal0-slot@img` — also on `:8188`. The control routes (`/restart`), installer repair, and
`install_extension("comfyui")` all call `comfy-up.sh`. If both paths run, you get a port clash / an unmanaged
container that won't yield the iGPU to inference.

**Why "verify first":** the *live CT105 today* actually uses `comfy-up.sh` and works — so it's unclear whether the
arbiter/podman-slot path for ComfyUI is the real runtime or aspirational. You must establish ground truth before
choosing a fix.

**Steps:**
1. **Inspect live CT105 (read-only):**
   ```bash
   ssh hal0 'cd /opt/hal0 && git branch --show-current && git status --porcelain'   # coordinate first (0.4)
   ssh hal0 'docker ps -a --filter name=comfyui; podman ps -a | grep -i "img\|comfy"; ss -ltnp | grep 8188'
   ```
   Determine: is the running ComfyUI a `docker` standalone (comfy-up.sh) or a podman `hal0-slot@img`? Who owns :8188?
2. Read `src/hal0/slots/arbiter.py` (`ensure_img` / `restore_llm`) and how `/api/comfyui/switchover` drives it.
   Decide the single owner of the ComfyUI container:
   - **Option A (preferred if arbiter is real):** route `/restart` + repair + `install_extension` through the
     arbiter (like `/switchover` does), and demote `comfy-up.sh` to a **manual operator** script only (clearly
     commented, not called by the API). 
   - **Option B (if arbiter path is not actually wired for comfyui):** keep `comfy-up.sh` as the one true path and
     make the arbiter use it / not double-bind :8188.
   Pick based on step 1's evidence; write the rationale in the PR + a hal0-memory note.
3. Implement the chosen reconciliation (code-only changes in `comfyui.py` / `installer.py` / `arbiter.py`), with
   unit tests mocking the chosen mechanism.
4. **Validate on CT105** (see 0.4 deploy flow): deploy the branch, then exercise `/restart` and a switchover; confirm
   exactly **one** ComfyUI container exists on :8188 and inference yields/resumes correctly.
5. Commit `fix(comfyui): single-owner container lifecycle via arbiter (#874)`; record memory.

**Done when:** only one mechanism manages the ComfyUI container; no :8188 clash; verified live on CT105.

---

## 6. Phase 1 — build `ghcr.io/hal0ai/comfyui` (deferred; needs CT105/GPU) 

Only do this when explicitly asked — it was intentionally deferred. Full spec is **Phase 1** of
`docs/superpowers/plans/2026-06-16-comfyui-platform-integration.md` (Tasks 1.1–1.3). Summary: build a hal0-owned
ComfyUI server image porting kyuz0's gfx1151 recipe (TheRock ROCm-7 torch wheels pinned by version, launch flags
`--disable-mmap --bf16-vae --cache-none`, env `TORCH_BLAS_PREFER_HIPBLASLT=1 COMFYUI_ENABLE_MIOPEN=1
TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1`), bake the 11 custom nodes + `hal0_gpu_gate.py`, push to GHCR, pin the
digest in `manifest.json`. **Build + smoke only run on CT105** (GPU); the smoke test is `tests/comfyui/test_image_smoke.sh`.
After this lands, #877-I5 (image identity) collapses into "point everything at the new ghcr digest."

---

## 7. Task 6.3 — CT105 live render validation (deferred; needs CT105/GPU)

After the branch is deployed to CT105 (0.4): fetch ONE cheapest model set (`get_esrgan.sh` or
`get_sdxl.sh --precision fp16`, seconds not hours) via `POST /api/comfyui/models/fetch`, then drive a real render and
confirm end-to-end: (a) inference slots yield (pane header note appears), (b) progress + preview thumbnail update,
(c) Cancel works, (d) iGPU returns to inference after idle. Do a **real-browser** pass (Playwright MCP against the
live dashboard), not just the mocked e2e — a prior lesson: spec-green ≠ works-in-a-real-browser.

---

## Suggested order
#876 → #877 (I4, I5) → #873 → #875 → #874 → Phase 1 → 6.3.
(First four are pure code, fast, no CT105. #874/Phase1/6.3 need the GPU box and coordination.)
