# ComfyUI Platform Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ComfyUI a first-class, deterministically-provisioned hal0 platform component (image/video gen) — own image, installer wiring, capability-driven model picker, fully-wired operator pane.

**Architecture:** Build `ghcr.io/hal0ai/comfyui` (port kyuz0 gfx1151 recipe; bake nodes+workflows+gpu_gate). Keep ComfyUI as slot/arbiter-managed `img` runtime (implicit GPU-yield switchover) — NOT a standalone always-on service. Promote to "official" at the **provisioning layer**: ship control scripts + sudoers in-repo, Extensions-registry entry, services/health/repair, capability→model picker. Wire the V2 "Render hero" pane to live ComfyUI APIs + real hal0 telemetry.

**Tech Stack:** Python 3.12 (FastAPI), podman, systemd, ROCm-7 TheRock (gfx1151), ComfyUI, JS/JSX dashboard SPA (ui/), pytest, Playwright.

## Global Constraints

- Base hardware: Strix Halo gfx1151, iGPU + unified memory. ComfyUI = ROCm only.
- Image identity: `ghcr.io/hal0ai/comfyui`, **digest-pinned** in `manifest.json` `toolbox_images.comfyui`.
- Launch flags (mandatory): `--disable-mmap --gpu-only --disable-smart-memory --cache-none --bf16-vae`. Port `8188` (hal0 convention; kyuz0 default is 8000 — override).
- Env (fast path): `TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1`, `TORCH_BLAS_PREFER_HIPBLASLT=1`, `COMFYUI_ENABLE_MIOPEN=1`.
- Model store: `/mnt/ai-models/comfyui/models/<clean-subdir>/` (= `model_store_root()/comfyui`). Writable ZFS bind on CT105. NO `/var/lib/hal0/comfyui/models`.
- ComfyUI is a seeded, non-deletable `img`-family slot. LLM registry untouched by ComfyUI models.
- iGPU exclusivity: render enqueue -> arbiter gives ComfyUI GPU -> inference slots yield. Surfaced as header note, never a user toggle.
- Third-party-official-fix-first: port kyuz0's solved recipe; flag any deviation w/ upstream link.
- TDD throughout. Frequent commits. Deploy via `scripts/deploy.sh` (rebuilds ui/dist).

---

## Decisions locked (grill 2026-06-16)

1. **B2** — build `ghcr.io/hal0ai/comfyui`, port kyuz0 recipe, bake nodes/workflows/gpu_gate.
2. Model store `/mnt/ai-models/comfyui/models`, clean names, fix `_comfyui_models_dir`.
3. **3A** — keep slot/arbiter/gpu-gate lifecycle; "official" = deterministic provisioning.
4. Capability matrix: txt2img=Qwen-Image-2512+4step; edit=Qwen-Edit-2511+4step; txt2vid/img2vid=LTX-2 default (Wan2.2/Hunyuan quality alts); video-upscale embedded; **+ESRGAN 4× image upscale**; **+SDXL-Lightning fast tier**; defer FLUX.
5. Model fetch = wrap vendored `get_*.sh`, deferred async pulls, picker records selections.
6. Pane = ops surface (monitor+control+quick-launch), authoring via `Open ComfyUI ↗`; implicit switchover; live controls.
7. Test = TDD unit/contract + e2e mock UI + image smoke + CT105 live cheapest-render.

---

## File Structure

**New (repo):**
- `packaging/toolbox/comfyui.Dockerfile` — hal0 ComfyUI image (FROM rocm-7 base or kyuz0-recipe build).
- `installer/comfyui/scripts/{enter_imagegen.sh,exit_imagegen.sh,cancel.sh,restart.sh,logs.sh,status.sh}` — the 6 control scripts (currently hand-placed on CT105 only).
- `installer/comfyui/scripts/{set_extra_paths.sh, get_qwen_image.sh, get_wan22.sh, get_hunyuan15.sh, get_ltx2.sh, get_sdxl.sh, get_esrgan.sh}` — vendored kyuz0 fetchers + 2 new.
- `installer/comfyui/workflows/*.json` — curated API-format workflows (10 kyuz0 + esrgan + sdxl).
- `installer/comfyui/extra_model_paths.yaml.tmpl` — 8-key layout, base `/mnt/ai-models/comfyui/models`.
- `src/hal0/comfyui/__init__.py`, `src/hal0/comfyui/fetch.py` — get_*.sh wrapper -> hal0 job.
- `src/hal0/comfyui/capabilities.py` — capability→family→variant matrix (the picker source of truth).
- `tests/comfyui/*` — unit/contract tests.
- `ui/src/.../ImageGenCard.*` — ported design pane (from Design/design_handoff_comfyui_imagegen/design/).

**Modify:**
- `src/hal0/install/extensions.py:24-58` — add `comfyui` Extension + install branch.
- `src/hal0/api/routes/installer.py:419-486` — add comfyui to `_REPAIRABLE_UNITS` + services step.
- `src/hal0/api/routes/comfyui.py` — point at shipped `/opt/comfyui/*.sh`; un-gate switchover (was 501); add control routes.
- `src/hal0/registry/pull.py:174` — `_comfyui_models_dir` -> `model_store_root()/comfyui/models`.
- `src/hal0/registry/curated.py` — add SDXL + ESRGAN curated entries (`comfyui_subdir`,`model_class`).
- `installer/install.sh` — place `/opt/comfyui/*` scripts + sudoers; comfyui in services start.
- `packaging/sudoers/hal0-comfyui` — verify cmds match shipped scripts.
- `manifest.json` — `toolbox_images.comfyui` -> ghcr image (digest via `scripts/update-toolbox-digests.sh`).
- `installer/etc-hal0/slots/img.toml`, `SEED_PROFILES` (`schema.py:763-770`), `profiles.toml` — profile image -> ghcr.
- `.github/workflows/` — build+push comfyui image (new job) or document external build.

---

# PHASE 1 — Image (`ghcr.io/hal0ai/comfyui`)

Produces a digest-pinned, reproducible ComfyUI server image w/ baked recipe.

### Task 1.1: Dockerfile — base + ComfyUI + torch wheels

**Files:** Create `packaging/toolbox/comfyui.Dockerfile`; Test `tests/comfyui/test_image_smoke.sh`.

**Interfaces:** Produces image tag `hal0/comfyui:dev`; exposes `:8188`; entrypoint runs ComfyUI w/ Global-Constraints flags+env.

- [ ] **Step 1: Write failing smoke test** `tests/comfyui/test_image_smoke.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
IMG="${1:-hal0/comfyui:dev}"
cid=$(podman run -d --device /dev/dri --device /dev/kfd --group-add video --group-add render \
  --security-opt seccomp=unconfined -p 18188:8188 "$IMG")
trap 'podman rm -f "$cid" >/dev/null' EXIT
for i in $(seq 1 60); do curl -sf localhost:18188/system_stats && break; sleep 2; done
curl -sf localhost:18188/system_stats | grep -q comfyui_version
# gpu_gate node loaded:
podman logs "$cid" 2>&1 | grep -qi "hal0_gpu_gate"
echo SMOKE_OK
```
- [ ] **Step 2: Run -> FAIL** (no image). `bash tests/comfyui/test_image_smoke.sh` -> error pull/run.
- [ ] **Step 3: Write Dockerfile.** Recipe (port kyuz0 `01-rocm-envs.sh`+`99-toolbox-banner.sh`):
```dockerfile
# pin a real digest in CI; rocm-7 gfx1151 base
FROM rocm/dev-ubuntu-24.04:7.0-complete AS base
ENV TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1 \
    TORCH_BLAS_PREFER_HIPBLASLT=1 \
    COMFYUI_ENABLE_MIOPEN=1 \
    HSA_OVERRIDE_GFX_VERSION=11.5.1
RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH VIRTUAL_ENV=/opt/venv
# TheRock gfx1151 nightlies (pin versions in CI, not :latest)
RUN pip install --index-url https://rocm.nightlies.amd.com/v2-staging/gfx1151 \
      torch torchvision torchaudio
RUN git clone --depth 1 https://github.com/comfyanonymous/ComfyUI /opt/ComfyUI
RUN pip install -r /opt/ComfyUI/requirements.txt transformers==4.56.2 gguf
# custom nodes (the 11 + gpu_gate baked, Task 1.2)
WORKDIR /opt/ComfyUI
EXPOSE 8188
COPY packaging/toolbox/comfyui-entrypoint.sh /usr/local/bin/comfyui-entrypoint
ENTRYPOINT ["/usr/local/bin/comfyui-entrypoint"]
```
Entrypoint `comfyui-entrypoint.sh`:
```bash
#!/usr/bin/env bash
set -e
[ -f /opt/comfyui/set_extra_paths.sh ] && /opt/comfyui/set_extra_paths.sh || true
exec python /opt/ComfyUI/main.py --listen 0.0.0.0 --port 8188 \
  --disable-mmap --gpu-only --disable-smart-memory --cache-none --bf16-vae
```
- [ ] **Step 4: Build + run smoke** `podman build -t hal0/comfyui:dev -f packaging/toolbox/comfyui.Dockerfile . && bash tests/comfyui/test_image_smoke.sh` -> SMOKE_OK (run on CT105, GPU box).
- [ ] **Step 5: Commit** `feat(comfyui): hal0 ComfyUI image — gfx1151 recipe + flags`.

### Task 1.2: Bake custom nodes (11 + gpu_gate)

**Files:** Modify `comfyui.Dockerfile`; Create `installer/comfyui/custom_nodes/` manifest (reuse existing `hal0_gpu_gate.py`).

- [ ] **Step 1:** Extend smoke test: assert each node dir present via `/object_info` containing gpu-gate + WanVideo + LTXV node classes.
- [ ] **Step 2:** Run -> FAIL.
- [ ] **Step 3:** Add to Dockerfile `RUN` block cloning the 11 nodes (pin commits): ComfyUI_essentials, ComfyUI-AMDGPUMonitor, ComfyUI-GGUF, ComfyUI-Manager, ComfyUI-WanVideoWrapper, ComfyUI-VideoHelperSuite, rgthree-comfy, ComfyUI-Model-Manager, ComfyUI-LTXVideo, ComfyUI-Crystools, ComfyUI-Custom-Scripts; `COPY installer/comfyui/custom_nodes/hal0_gpu_gate.py /opt/ComfyUI/custom_nodes/`. Pip-install each node's requirements.txt.
- [ ] **Step 4:** Rebuild + smoke -> PASS.
- [ ] **Step 5: Commit** `feat(comfyui): bake 11 custom nodes + gpu_gate`.

### Task 1.3: CI build/push + manifest digest

**Files:** Create `.github/workflows/comfyui-image.yml`; Modify `manifest.json`, `scripts/update-toolbox-digests.sh`.

- [ ] **Step 1:** Test `tests/comfyui/test_manifest.py`: assert `manifest.json toolbox_images.comfyui.image` startswith `ghcr.io/hal0ai/comfyui@sha256:` and digest non-null.
- [ ] **Step 2:** Run -> FAIL (currently kyuz0 image).
- [ ] **Step 3:** Add CI job: build comfyui.Dockerfile, push `ghcr.io/hal0ai/comfyui`, capture digest; update `update-toolbox-digests.sh` to include comfyui; set manifest entry.
- [ ] **Step 4:** Run -> PASS.
- [ ] **Step 5: Commit** `ci(comfyui): build+push ghcr image, pin digest`.

---

# PHASE 2 — Model store + fetch

### Task 2.1: Reconcile model path

**Files:** Modify `src/hal0/registry/pull.py:174`; Test `tests/registry/test_comfyui_path.py`.

**Interfaces:** Produces `_comfyui_models_dir(subdir) -> Path` = `model_store_root()/comfyui/models/<subdir>`.

- [ ] **Step 1:** Test:
```python
def test_comfyui_models_dir_uses_store_root(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "model_store_root", lambda: tmp_path)
    assert pull._comfyui_models_dir("loras") == tmp_path/"comfyui"/"models"/"loras"
```
- [ ] **Step 2:** Run -> FAIL (returns `/var/lib/hal0/comfyui/models`).
- [ ] **Step 3:** Change `_comfyui_models_dir` to derive from `model_store_root()`.
- [ ] **Step 4:** Run -> PASS.
- [ ] **Step 5: Commit** `fix(comfyui): model pulls target /mnt/ai-models/comfyui/models`.

### Task 2.2: Capability matrix (picker source of truth)

**Files:** Create `src/hal0/comfyui/capabilities.py`; Test `tests/comfyui/test_capabilities.py`.

**Interfaces:** Produces `CAPABILITIES: dict[str, Capability]`; `Capability(id, label, default_family, alternatives:list[ModelVariant])`; `ModelVariant(family, precision, lora, est_seconds, fetch_script, workflow)`.

- [ ] **Step 1:** Test: `CAPABILITIES["txt2img"].default_family=="qwen-image"`; default variant `est_seconds<=80`; every capability of {txt2img,img2img,txt2video,img2video,image_upscale} present; each variant has a `fetch_script` that exists in `installer/comfyui/scripts/`.
- [ ] **Step 2:** Run -> FAIL.
- [ ] **Step 3:** Write matrix per Decision 4 (defaults = 4-step; LTX-2 video default; +sdxl +esrgan).
- [ ] **Step 4:** Run -> PASS.
- [ ] **Step 5: Commit** `feat(comfyui): capability→model matrix`.

### Task 2.3: Vendor fetch scripts + 2 new

**Files:** Create `installer/comfyui/scripts/get_*.sh` (vendor 4 kyuz0 + write `get_sdxl.sh`, `get_esrgan.sh`, `set_extra_paths.sh`); Test `tests/comfyui/test_fetch_scripts.py`.

- [ ] **Step 1:** Test: each script `bash -n` clean; `set_extra_paths.sh` emits yaml w/ 8 keys + base `/mnt/ai-models/comfyui/models`; new scripts download to correct subdir (dry-run flag).
- [ ] **Step 2:** Run -> FAIL.
- [ ] **Step 3:** Vendor kyuz0 scripts verbatim (cite upstream commit in header); write `get_sdxl.sh` (SDXL base + 8-step Lightning LoRA + sdxl-vae) and `get_esrgan.sh` (4x-UltraSharp + RealESRGAN_x4 -> upscale_models/). Adapt `MODEL_DIR=/mnt/ai-models/comfyui/models`.
- [ ] **Step 4:** Run -> PASS.
- [ ] **Step 5: Commit** `feat(comfyui): vendor fetch scripts + sdxl/esrgan`.

### Task 2.4: Fetch wrapper -> hal0 job

**Files:** Create `src/hal0/comfyui/fetch.py`; Test `tests/comfyui/test_fetch.py`.

**Interfaces:** Produces `fetch_model(variant: ModelVariant) -> JobId`; async, progress, cancellable; shells `get_<family>.sh --precision <p>`.

- [ ] **Step 1:** Test (mock subprocess): `fetch_model(variant)` invokes correct script+args, registers a job, streams progress, lands files under store root.
- [ ] **Step 2:** Run -> FAIL.
- [ ] **Step 3:** Implement wrapper (reuse hal0 job/registry infra; do NOT route through LLM PullPlan).
- [ ] **Step 4:** Run -> PASS.
- [ ] **Step 5: Commit** `feat(comfyui): async model fetch wrapper`.

### Task 2.5: Curated entries (SDXL, ESRGAN)

**Files:** Modify `src/hal0/registry/curated.py`; Test `tests/registry/test_curated_comfyui.py`.

- [ ] **Step 1:** Test: curated catalog includes `sdxl-lightning`, `esrgan-4x` with `model_class=="image"` and correct `comfyui_subdir`.
- [ ] **Step 2:** Run -> FAIL.
- [ ] **Step 3:** Add entries.
- [ ] **Step 4:** Run -> PASS.
- [ ] **Step 5: Commit** `feat(comfyui): curated sdxl + esrgan`.

---

# PHASE 3 — Installer provisioning (the reliability gap)

### Task 3.1: Ship control scripts

**Files:** Create `installer/comfyui/scripts/{enter_imagegen,exit_imagegen,cancel,restart,logs,status}.sh`; Modify `installer/install.sh`; Test `tests/install/test_comfyui_scripts_shipped.py`.

**Interfaces:** Produces `/opt/comfyui/<name>.sh` placed by install.sh; consumed by `api/routes/comfyui.py` + sudoers.

- [ ] **Step 1:** Test: every script path referenced in `api/routes/comfyui.py` + `packaging/sudoers/hal0-comfyui` exists in `installer/comfyui/scripts/`; `bash -n` clean.
- [ ] **Step 2:** Run -> FAIL (scripts only on CT105).
- [ ] **Step 3:** Author the 6 scripts (extract current CT105 copies via `ssh hal0 'cat /opt/comfyui/*.sh'`, sanitize, commit). `enter_imagegen.sh`=arbiter claim GPU + start resident container; `exit_imagegen.sh`=release; `cancel.sh`=`curl :8188/queue -d '{"clear":true}'` + interrupt; etc. Add install.sh block: `install -d /opt/comfyui; install -m0755 installer/comfyui/scripts/*.sh /opt/comfyui/`.
- [ ] **Step 4:** Run -> PASS.
- [ ] **Step 5: Commit** `feat(comfyui): ship switchover control scripts in-repo`.

### Task 3.2: Sudoers + extra_model_paths placement

**Files:** Modify `installer/install.sh`, `packaging/sudoers/hal0-comfyui`; Test `tests/install/test_comfyui_sudoers.py`.

- [ ] **Step 1:** Test: install.sh contains `install -m0440 packaging/sudoers/hal0-comfyui /etc/sudoers.d/`; sudoers cmd paths == shipped script paths; `visudo -cf` clean.
- [ ] **Step 2:** Run -> FAIL.
- [ ] **Step 3:** Add install.sh lines (sudoers + render extra_model_paths.yaml from tmpl to `/mnt/ai-models/comfyui/extra_model_paths.yaml`).
- [ ] **Step 4:** Run -> PASS.
- [ ] **Step 5: Commit** `feat(comfyui): install sudoers + extra_model_paths`.

### Task 3.3: Extensions registry entry

**Files:** Modify `src/hal0/install/extensions.py:24-58`; Test `tests/install/test_extensions_comfyui.py`.

**Interfaces:** Consumes `Extension`; Produces `comfyui` extension (kind `app`, default on) + install branch (`hal0 capability apply image` / slot ensure, NOT systemctl-only).

- [ ] **Step 1:** Test: `comfyui` in `EXTENSIONS`; `install_extension("comfyui")` ensures img slot + container present (mock).
- [ ] **Step 2:** Run -> FAIL.
- [ ] **Step 3:** Add Extension + branch (image slot is seeded; branch ensures resident container + extra_paths). 
- [ ] **Step 4:** Run -> PASS.
- [ ] **Step 5: Commit** `feat(comfyui): extensions-registry entry`.

### Task 3.4: Services step + repair allowlist

**Files:** Modify `src/hal0/api/routes/installer.py:419-486`, `services_health.py`; Test `tests/api/test_services_comfyui.py`.

- [ ] **Step 1:** Test: `GET /api/install/services` includes comfyui dot; comfyui in `_REPAIRABLE_UNITS`; repair restarts resident container (mock).
- [ ] **Step 2:** Run -> FAIL.
- [ ] **Step 3:** Add comfyui to services step + repair (repair = `/opt/comfyui/restart.sh`, not systemctl).
- [ ] **Step 4:** Run -> PASS.
- [ ] **Step 5: Commit** `feat(comfyui): installer services step + repair`.

### Task 3.5: Picker integration (capability matrix in setup)

**Files:** Modify `src/hal0/cli/setup_ui.py`, `setup_command.py`, `api/routes/installer.py` (curated-models), `install/orchestrate.py`; Test `tests/install/test_picker_comfyui.py`.

**Interfaces:** Consumes `CAPABILITIES` (2.2); Produces selections recorded (no pull at install, `--no-pull`); auto-mode picks best 4-step per enabled capability.

- [ ] **Step 1:** Test: auto selections cover all 5 capabilities w/ default variants; interactive `_choose_model` lists alternatives w/ est_seconds; selections persisted, NOT pulled.
- [ ] **Step 2:** Run -> FAIL.
- [ ] **Step 3:** Wire matrix into picker (TUI + `/curated-models` + auto build). Post-install: expose `POST /api/comfyui/models/fetch` to trigger deferred pulls (uses 2.4).
- [ ] **Step 4:** Run -> PASS.
- [ ] **Step 5: Commit** `feat(comfyui): install-time capability model picker`.

---

# PHASE 4 — API: control + monitoring

### Task 4.1: Un-gate switchover + control routes

**Files:** Modify `src/hal0/api/routes/comfyui.py`; Test `tests/api/test_comfyui_routes.py`.

**Interfaces:** Produces `POST /api/comfyui/render/cancel`, `/restart`, `GET /logs`, existing `/status`; switchover implicit (enqueue triggers arbiter; no manual toggle route).

- [ ] **Step 1:** Test: cancel -> calls `/opt/comfyui/cancel.sh` (mock); restart -> restart.sh; status aggregates ComfyUI `/queue`+`/system_stats`; no 501.
- [ ] **Step 2:** Run -> FAIL (switchover 501).
- [ ] **Step 3:** Implement routes against shipped scripts; status reads ComfyUI `/queue`,`/history`,`/system_stats`.
- [ ] **Step 4:** Run -> PASS.
- [ ] **Step 5: Commit** `feat(comfyui): live control routes, un-gate switchover`.

### Task 4.2: Telemetry contract (real signals)

**Files:** Modify `src/hal0/api/routes/comfyui.py` (status payload); Test same.

**Interfaces:** Produces `status.telemetry = {gtt_used,gtt_total,ram_used,ram_total,util,temp,clock,it_s,eta}`. GTT/RAM/temp/clock from hal0 real telemetry; util NOT raw `gpu_busy_percent` (forced-high artifact) — derive from active-job + duty signals; it_s/eta from ComfyUI ws progress.

- [ ] **Step 1:** Test: payload has all keys; util not hardcoded 100 when idle (use job-presence gate).
- [ ] **Step 2:** Run -> FAIL.
- [ ] **Step 3:** Implement; reuse existing hal0 GPU telemetry helpers; gate util on render-active.
- [ ] **Step 4:** Run -> PASS.
- [ ] **Step 5: Commit** `feat(comfyui): real-telemetry status payload`.

### Task 4.3: Quick-launch + preview proxy

**Files:** Modify `comfyui.py`; Test same.

**Interfaces:** Produces `POST /api/comfyui/workflows/{id}/launch` (fire curated workflow w/ defaults -> ComfyUI `/prompt`); `GET /api/comfyui/preview` (proxy ComfyUI `/view` latest output).

- [ ] **Step 1:** Test: launch posts workflow json to `/prompt` (mock); preview proxies latest `/history` image bytes.
- [ ] **Step 2:** Run -> FAIL.
- [ ] **Step 3:** Implement (workflows from baked `/opt/comfy-workflows/`).
- [ ] **Step 4:** Run -> PASS.
- [ ] **Step 5: Commit** `feat(comfyui): quick-launch + preview proxy`.

---

# PHASE 5 — Pane UI (V2 Render hero)

Port `Design/design_handoff_comfyui_imagegen/design/` into ui/. Component reads `RUN/QUEUE/GTT/RAM/STATS` objects (handoff §Integration).

### Task 5.1: Port ImageGenCard + comfy.css (mock data)

**Files:** Create `ui/src/pages/slots/ImageGenCard.jsx`, `comfy.css`, `comfy-core.jsx`; Test `ui/tests/e2e/imagegen.spec.ts` (forced-mock).

- [ ] **Step 1:** e2e test (mock): card renders render-hero + queue + telemetry + workflows strip + footer; reduced-motion freezes pulse; empty-queue state has no overlay (recall #845 lockup).
- [ ] **Step 2:** Run -> FAIL.
- [ ] **Step 3:** Port jsx/css; scope under `.comfy-page`; mock fixture matches handoff demo data.
- [ ] **Step 4:** Run -> PASS (`npx playwright test imagegen`).
- [ ] **Step 5: Commit** `feat(ui): ImageGen V2 render-hero pane (mock)`.

### Task 5.2: Bind to live API

**Files:** Modify `comfy-core.jsx` (data hooks), slots page mount; Test e2e live-shaped mock.

**Interfaces:** Consumes 4.1/4.2/4.3 routes. RUN<-status.active; QUEUE<-status.queue; GTT/RAM/STATS<-status.telemetry; preview<-/preview.

- [ ] **Step 1:** Test: controls fire correct endpoints (route intercept asserts); progress/queue/telemetry bind; cancel disables while pending.
- [ ] **Step 2:** Run -> FAIL.
- [ ] **Step 3:** Wire fetch hooks (900ms tick), control handlers, preview img.
- [ ] **Step 4:** Run -> PASS.
- [ ] **Step 5: Commit** `feat(ui): wire ImageGen pane to live ComfyUI API`.

---

# PHASE 6 — Integration, deploy, docs

### Task 6.1: Profile/manifest -> ghcr image

**Files:** Modify `schema.py:763-770` SEED_PROFILES, `installer/etc-hal0/profiles.toml`, `slots/img.toml`, `manifest.json`; Test `tests/config/test_comfyui_profile.py`.

- [ ] **Step 1:** Test: comfyui profile image == ghcr digest; img.toml profile==comfyui, port 8188, device gpu-rocm.
- [ ] **Step 2:** Run -> FAIL.
- [ ] **Step 3:** Update all 4 (+ live CT105 `/etc/hal0/profiles.toml` per memory note — deploy.sh won't sync it).
- [ ] **Step 4:** Run -> PASS.
- [ ] **Step 5: Commit** `feat(comfyui): point profile/manifest at ghcr image`.

### Task 6.2: Full pytest + e2e + lint

- [ ] Run `PYTHONPATH=src pytest tests/comfyui tests/install tests/api tests/registry tests/config -v` -> all pass (CT132 box or CI; whole-suite hangs locally per memory).
- [ ] Run `cd ui && npx playwright test imagegen` -> pass.
- [ ] `ruff format --check` (NOT black). Commit fixes.

### Task 6.3: CT105 live validation (cheapest render)

**Tier-3 (verify-then-act): another session may hold /opt/hal0 — run `wip hal0 status` first; deploy to PREVIEW ref, not main.**

- [ ] `wip hal0 status` clean; `wip hal0 claim "comfyui integration deploy" /opt/hal0`.
- [ ] Build+push ghcr comfyui image; refresh digest.
- [ ] `ssh hal0 'cd /opt/hal0 && sudo bash scripts/deploy.sh --ref origin/<branch>'`.
- [ ] Fetch ONE cheapest model set (ESRGAN or SDXL-Lightning) via `/api/comfyui/models/fetch`.
- [ ] Real-browser pass (Playwright MCP, not just spec — recall durability lesson): enqueue render -> verify (a) inference slots yield (header note), (b) progress/preview update, (c) cancel works, (d) GPU released after idle.
- [ ] Capture results; `wip hal0 release`.

### Task 6.4: Docs + memory

- [ ] `hal0-docs` skill: add ComfyUI image-gen page (capabilities, picker, flags, model store).
- [ ] Update README/PLAN + hal0-web CONTENT_BRIEF (recall docs-after-shipping rule).
- [ ] `hal0-memory` `memory_add`: integration decisions + gotchas (image recipe, path reconcile, switchover) dataset `shared`, `document_id=comfyui-platform-integration`.

---

## Self-Review

- **Spec coverage:** installer-official ✓(P3); kyuz0 recipe/image ✓(P1); extensions config ✓(3.3); templates baked ✓(1.2,2.3); model picker every capability ✓(2.2,3.5); set_extra_paths ✓(2.3); model_manager→wrapped fetch ✓(2.4); benchmark model choices+adds ✓(Decision 4); launch flags ✓(GC,1.1); pane wired+controls+monitoring ✓(P4,P5); testing ✓(P6).
- **Placeholders:** none — fetch script bodies vendored verbatim at exec time (cite upstream); CT105 script extraction is a real step (6.3/3.1).
- **Type consistency:** `ModelVariant`/`Capability` (2.2) consumed by fetch (2.4) + picker (3.5); `status.telemetry` keys (4.2) consumed by UI (5.2); control script paths single-sourced `installer/comfyui/scripts/` (3.1) consumed by sudoers (3.2)+routes (4.1).
- **Open risk:** TheRock nightly wheel pinning — must pin exact versions in CI (1.1) or builds drift; flagged upstream WIP.
