# hal0 Memory Sync Batch — from thinMint Claude Code → Hindsight bank

> Generated 2026-07-22. Each section below is a self-contained memory to add to the **Hindsight**
> dynamic bank **`pi-coder::hal0`** (agent=pi-coder, project=hal0, endpoint `10.0.1.142:9177`).
> Both Hindsight (9177) and Cognee/hal0-memory (8080) backends on CT105 are unreachable from
> thinMint at sync time. This file is the batch payload for `hindsight_retain` import when
> connectivity is restored.

---

<!-- memory: hal0-api-deploy-layout -->
## hal0-api-deploy-layout

hal0-api (dashboard backend) runs from an INSTALLED venv copy, NOT current/src — edit both + restart.

The hal0 dashboard API (`hal0-api.service` on hal0/CT105 10.0.1.142, binds 0.0.0.0:8080) runs the Python
package installed at `/usr/lib/hal0/venv/lib/python3.12/site-packages/hal0`. `WorkingDirectory=/usr/lib/hal0/current`
(symlink → `/usr/lib/hal0/hal0-0.8.2b2`) holds the source but is NOT an editable install — the venv
has an independent copy. Editing only `current/src/hal0/...` has zero runtime effect.

**How to apply:** Edit the file in `current/src/hal0/...` (source of truth) AND copy it to the matching
path under `venv/.../site-packages/hal0/...`, then `systemctl restart hal0-api.service` and confirm.

**Full source-of-truth:** local git checkout on thinMint: `/home/mint/projects/hal0` (branch main).
The deployed `current` tarball ships only a prebuilt `ui/dist`, NO `ui/src`, so the dashboard UI can
only be rebuilt from the local checkout.

**Frontend (dashboard SPA, React+Vite under `ui/`):** served from `/usr/lib/hal0/current/ui/dist` via
`HAL0_UI_DIST=/usr/lib/hal0/current/ui/dist` in `/etc/hal0/api.env`. To ship a UI change: edit
`ui/src/...` locally, `cd ui && npm run build`, `rsync -az --delete ui/dist/ hal0:/usr/lib/hal0/current/ui/dist/`.
No restart needed.

<!-- memory: hal0-rework-deploy-halo-lxc -->
## hal0-rework-deploy-halo-lxc

The hal0 rework deploys to a NEW LXC named "halo" (letter o), not by replacing hal0 on lxc105.

The de-scar/rework of hal0 (tracked in `/home/mint/hal0-rework-plan.md`, working clone at
`/home/mint/hal0` on branch `rework/descar`) must NOT be deployed by replacing the live hal0
install on lxc105 in place.

**Why:** the rework is large (SQLite migration, model-store re-layout, Podman/Quadlet + perms
changes) — an in-place swap on the running box is high-risk with no clean rollback.

**How to apply:** stand up a new LXC named `halo` (letter o, distinct from the zero in `hal0`),
run it side-by-side with the existing `hal0`/lxc105, migrate data (Hindsight memory, model
registry/store, config), validate end-to-end, then cut over and only then decommission the old box.

<!-- memory: hal0-rework-working-setup -->
## hal0-rework-working-setup

The hal0 de-scar/rework (started 2026-07-17) works out of a local clone to dodge an NFS permissions
wall, not the shared checkout.

- **Working repo:** `/home/mint/hal0` (mint-owned, local disk), branch `rework/descar` (off
  origin/main `402a7724` = v0.9.8). Remotes: `origin` → github.com/Hal0ai/hal0 · `nfs` →
  `/mnt/dev/repos/hal0-mono/hal0` (the harvest source for ~51 unmerged branches).
- **Why the clone:** the shared repo at `/mnt/dev/repos/hal0-mono/hal0` is on NFS (10.0.1.110/prx
  `/devpool/dev/repos`) with ~797/1076 files root-owned; NFS root-squash makes `sudo chown` fail.
- **Planning docs (in `/home/mint`, not the repo):** `hal0-rework-plan.md` (master plan),
  `hal0-rework-ways-of-working.md` (git/worktree/agent-team/caveman + verification).
- **Verification discipline:** capped local checks (ruff + import-smoke + `make check-sunset` +
  targeted tests <90s) — NEVER full pytest locally (hangs on podman/systemd tests). GitHub CI full
  pytest is the real gate. On ANY signature change, `grep -rn <name> tests/` and run caller tests.

<!-- memory: hal0-memory-shared-bank -->
## hal0-memory-default-bank

When writing memories to the hal0-memory MCP server, use `dataset: "default"` — that IS the shared/common bank where all cross-machine memories live. Do NOT use null (default is NOT "default").

hal0-memory is a shared memory store accessible from any machine on the thinmint.dev network.

**Reaching the server:** It is NOT reachable at `http://10.0.1.142:8080/...` — Cognee's DNS-rebinding
protection returns `421 Invalid Host header`. Use an SSH tunnel and localhost URL:
`ssh -f -N -L 18080:127.0.0.1:8080 hal0`, then `http://localhost:18080/mcp/memory/mcp`.

<!-- memory: hal0-brain-toolcall-leak -->
## hal0-brain-toolcall-leak

The dashboard hal0-brain steward leaked raw tool-call text (e.g. `=name="get_board"`) instead of
running tools. Root cause: format incompatibility on the brain slot's custom FPX runtime. The brain
slot runs `ghcr.io/hal0ai/hal0-rocmfpx` with `--jinja`. Its "peg-native" tool parser ONLY accepts
the qwen form `<tool_call><function=NAME><parameter=X>value</parameter></function></tool_call>`.
No 1B model emits that format natively.

**Fix (the "split", shipped in PR #1265):** keep a 1B for chat, route the steward's TOOL turns to a
capable model via `[brain_chat] tool_model`. Set `[brain_chat] tool_model = "hal0/code"` (27B coder;
falls back to the always-on `hal0/agent` 35B). Only capable models matching the format tool-call:
the **chadrock family** (`chadrock-35b-ace-saber` on `hal0/agent`, `chadrock3-6-27b-coder` on `hal0/code`).

**Native-1B path (not done):** fine-tune a `hal0-braintrain-1b` to emit the runtime format.

<!-- memory: hal0-honcho-local -->
## hal0-honcho-local

hal0 is deliberately configured to use a local self-hosted Honcho, not the Honcho cloud. Do not
"fix" this by repointing it at the cloud. `/root/.honcho/config.json` on hal0: apiKey
`hal0-local-noauth`, baseUrl `http://127.0.0.1:8000`, peerName `alexander`.

As of 2026-07-11, thinMint was repointed from cloud to hal0's local Honcho: endpoint.baseUrl =
`http://10.0.1.142:8000`, peer renamed `mint` → `alexander`, workspace `claude_code`.

**NOTE (2026-07-17):** Rework decision = Hindsight only, remove Honcho. This memory documents the
live state pre-migration for reference during P2-memory migration.

**Exposing Honcho on the LAN (2 gotchas):** (1) Compose port bind must be plain `"8000:8000"` (no
explicit IP — netavark DNATs break). (2) Docker's FORWARD DROP policy must be opened for the
honcho bridge subnet via `iptables -I FORWARD -d <subnet> -j ACCEPT` + `-s <subnet> -j ACCEPT`,
made durable via `/usr/local/bin/hal0-honcho-lanfw.sh` as `ExecStartPost`.

<!-- memory: rocmfp4-quant-procedure -->
## rocmfp4-quant-procedure

ROCmFP4 = charlie12345/rocmfp4-llama fork (custom `Q4_0_ROCMFP4*` GGUF types). Built on hal0
(CT105, 10.0.1.142), the Strix Halo / gfx1151 box.

Pipeline: download → `convert_hf_to_gguf.py` (host venv, Python 3.12) → `llama-quantize Q4_0_ROCMFP4_STRIX_LEAN`
(inside the rocmfp4 toolbox container).

**Gotchas:**
- `hal0-slot-agent` mounts `/mnt/ai-models` read-only → spin a writable toolbox with `--entrypoint sleep`
- Server image Python 3.14 has no torch wheels → do HF→BF16 convert in host venv (Python 3.12)
- Quantize with the SAME image's `llama-quantize` so format matches server
- Reusable toolbox: `hal0-quant-fc` (parked on 105, `podman run -d --name hal0-quant-fc --entrypoint sleep ...`)
- Verified: FastContext-4B BF16 7.49 GiB → ROCmFP4 2.05 GiB, decode ~2.9x faster, tool-call quality 4/5

<!-- memory: ai-models-access-model -->
## ai-models-access-model

The `ai-models` store is the ZFS dataset `devpool/ai-models` on PVE (10.0.1.110), exported over
NFS to 10.0.1.0/24.

- CT105/hal0 is a privileged LXC that bind-mounts the dataset directly (`mp0`) and writes as root.
- dev-server (thinMint) mounts it over NFSv3. The tree is owned `1000:aimodels` (gid 2000) with
  setgid dirs (`2775`); export is `rw` with `root_squash`.

**Historical gotcha (RESOLVED):** `manage-gids=y` in `/etc/nfs.conf` made the NFS server ignore
client's supplementary GIDs → mint's aimodels membership was discarded → group-write denied on
root-created files. Fix: `manage-gids=n` on pve.

<!-- memory: hal0-backup-fuse-hangs -->
## hal0-backup-fuse-hangs

lxc105/hal0 vzdump backups can deadlock on 2 known causes:
1. **podman overlay writes hang `fsfreeze`** — fixed by hookscript `/var/lib/vz/snippets/vzdump-105-quiesce.sh`
2. **stuck FUSE mount from Pane AppImage** — Pane removed from CT105 entirely on 2026-07-17

**Diagnose:** on prx — `cat /proc/<vzdump-worker>/stack` (look for `request_wait_answer`/`fuse_`),
`for d in /sys/fs/fuse/connections/*; do echo $d $(cat $d/waiting); done`.

**Unstick:** `echo 1 > /sys/fs/fuse/connections/<N>/abort` — fails the stuck request, worker unblocks,
backup resumes. If Pane is ever reinstalled, it will reintroduce this hang.

<!-- memory: pbs-datastore-truenas-tank -->
## pbs-datastore-truenas-tank

PBS backups depend on TrueNAS `tank` pool (10.0.1.215). The pool has a FAILING non-redundant 10TB
Seagate (`ata-ST10000NM0016...ZA26RSXS`, 6.8yr pwr-on) that logged 3 read/23 write errors.

**Root cause = failing disk + fragile topology.** `tank` = 2×4TB mirror striped with a SINGLE
non-redundant 10TB disk. The 10TB faulted → suspended the WHOLE pool. 12 data errors recorded.

`zpool clear tank` does NOT recover — I/O re-stalls instantly. Real fix = reboot TrueNAS to clear
wedged kernel state, then REPLACE the 10TB drive AND add redundancy (mirror/raidz).

<!-- memory: pve-gtt-hidden-memory -->
## pve-gtt-hidden-memory

High PVE host memory not shown in `free`/`ps` is amdgpu GTT (Strix Halo unified memory) from
`llama-server` inference.

**Diagnose:** `cat /sys/class/drm/card*/device/mem_info_gtt_used`, map to processes via
`/proc/*/fdinfo/*` (`drm-memory-gtt:` in KB), resolve owner via `/proc/PID/cgroup`.

**Gotcha:** CT107 hal0-next showed ~3.8 GB in Proxmox UI but held ~43 GB via GTT across two
llama-server instances. GTT is NOT charged to the container cgroup `memory.current`.

<!-- memory: hal0-runner-images-provenance -->
## hal0-runner-images-provenance

All runner-image build sources are already hal0-owned on GitHub:
- `vulkan`/`rocm` toolboxes → `Hal0ai/amd-strix-halo-toolboxes`
- `rocmfpx`/`vulkanfpx` → `Hal0ai/Hal0_ROCmFPX` (llama.cpp FPX fork; tag c077206)
- `kokoro`/`moonshine` Dockerfiles reconstructed from published-image layer history.

**Hy3 FPX runner rescued 2026-07-19:** `ghcr.io/hal0ai/hal0-rocmfpx:ifp2-hyv3-{mtp,d7e1f26}`
was local-only on CT105's podman store — pushed off-box. Pinned in images.json as `rocmfpx-hy3`.

**"ciru" = `ciru-ai/ROCmFPX`** (llama FPX upstream), NOT a ComfyUI fork.

**ComfyUI decision:** single ROCm image `ghcr.io/hal0ai/hal0-comfyui`, no rocm/vulkan split.

<!-- memory: hal0-box-uid-mismatch -->
## hal0-box-uid-mismatch

The `hal0` service user has DIFFERENT uids across deploy boxes — always chown by NAME not number:
- **halo150** (10.0.1.150): `hal0` = uid 996, gid 989. uid 999 = dnsmasq.
- **halo143** (10.0.1.143): `hal0` = uid 999, gid 988.

O13b perms-recurse fix (on descar) heals nested `state.json` ownership automatically via PermRows.

<!-- memory: hal0-langfuse-podman -->
## hal0-langfuse-podman

The langfuse observability stack in hal0 (CT105) runs as a podman-compose project
(`/root/langfuse`, project name `langfuse`, 6 containers).

Two gotchas fixed 2026-07-17:
1. **AppArmor:** podman in this unprivileged LXC can't load apparmor profiles. Fix:
   `/etc/containers/containers.conf` with `apparmor_profile = "unconfined"`.
2. **Port 5432 conflict:** langfuse postgres collides with hindsight's embedded postgres.
   hindsight legitimately owns 5432. Fix: remapped langfuse postgres to host `127.0.0.1:5433:5432`.

**Restart:** `pct exec 105 -- bash -lc "cd /root/langfuse && podman-compose up -d"`

<!-- memory: media-qbit-nfs-rootsquash -->
## media-qbit-nfs-rootsquash

media stack = Proxmox CT 250 "media" on prx (10.0.1.110), actual CT IP 10.0.1.252.
qbit "not downloading" = TrueNAS NFS root_squash denying writes.

**Fix:** on TrueNAS (`truenas_admin`@10.0.1.215), set maproot=root on the NFS exports qbit writes to:
`midclt call sharing.nfs.update <id> '{"maproot_user":"root","maproot_group":"root"}'`
Exports: downloads (ids 7/8/9), tv (id 5), movies (id 6).

**Jellyfin auto-import:** wired via MediaBrowser notification in sonarr/radarr → Jellyfin API key.

**SYSTEMIC ROOT CAUSE (found 2026-07-19):** TrueNAS VM 103 had only 4096MB RAM → OOM-killed
`nfs-mountd`/`nfsd`/`rpc-statd` under load → all NFS mounts hang. Fix: `qm set 103 --memory 6144`
+ reboot.

<!-- memory: thinmint-remote-desktop-krdp -->
## thinmint-remote-desktop-krdp

thinMint (CachyOS, Plasma 6.7 on Wayland, Strix Halo GPU) uses KRDP for remote desktop — NOT xrdp.

Two gotchas that break RDP login (both fixed 2026-06-30):
1. **Auth MUST be KRDP's own KWallet credential**, NOT system/PAM. `SystemUserEnabled=false` +
   `Users=thinmint`, password in KWallet `kdewallet` folder `KRDP` key `thinmint`.
2. **Force software H.264 encoding:** `Environment=KPIPEWIRE_FORCE_ENCODER=libx264` in
   `~/.config/systemd/user/app-org.kde.krdpserver.service.d/override.conf`. VAAPI H264 fails
   PipeWire format negotiation on this box.

Access: RDP on :3389 via SSH tunnel or Tailscale only; ufw blocks LAN 3389.

<!-- memory: work-scope-hal0-only -->
## work-scope-hal0-only

Work scope is the hal0 repo only (`Hal0ai/hal0`, cloned at `/home/mint/projects/hal0`). Do NOT
act on other repos: `hal0-web`, `amd-strix-halo-toolboxes`, `halo-claude`, `hermes-agent`, etc.

<!-- memory: hindsight-hermes-claude-integration -->
## hindsight-hermes-claude-integration

Canonical Hindsight integration instructions:
- Hermes ↔ Hindsight: https://hindsight.vectorize.io/sdks/integrations/hermes
- Claude Code ↔ Hindsight: https://hindsight.vectorize.io/sdks/integrations/claude-code

Use these as the reference for hal0 rework §7.4 (keep Hermes, fix its memory with proper Hindsight
plugins). Hindsight is the chosen/only memory engine for hal0 going forward (Honcho is being removed).

<!-- memory: minimax-config-single-point-of-failure -->
## minimax-config-single-point-of-failure

The whole MiniMax path (mm-worker.sh swarm + mmx CLI) depends on `~/.mmx/config.json`. The
`MINIMAX_API_KEY` env var alone is NOT enough.

If the file is missing: `mkdir -p ~/.mmx && node -e "require('fs').writeFileSync(process.env.HOME+'/.mmx/config.json', JSON.stringify({api_key:process.env.MINIMAX_API_KEY})+'\n',{mode:0o600})"`

Endpoint: `https://api.minimax.io/anthropic` (Anthropic-compatible).

<!-- memory: minimax-api-rate-limit -->
## minimax-api-rate-limit

The MiniMax API triggers a sustained 429 quota lockout if burst at high concurrency — not a brief
per-second spike. Keep concurrency ≤3 for bulk jobs. Always use exponential backoff on 429/5xx.
If you hit the wall, stop ALL workers and wait for reset. M2.1 fastest (13s/call).

<!-- memory: minimax-swarm-write-sandbox -->
## minimax-swarm-write-sandbox

mm-worker.sh swarm workers with `--perm acceptEdits` can only write inside their `--dir`. Set
`--dir` to the OUTPUT directory and have workers write `worker-N.md` relative to cwd. After a
swarm, always verify every `worker-N.md` exists.

<!-- memory: openwhispr-hal0-config -->
## openwhispr-hal0-config

OpenWhispr 1.7.5 on thinMint uses hal0 local inference at `http://10.0.1.142:8080/v1` (no trailing
slash). No API key required (LAN unauthenticated). STT model: `whisper-v3:turbo`. Chat model:
`flm` → `qwen3:4b` (reasoning model; switch to `utility`/gemma4 if cleanup is chatty).

<!-- memory: hal0ai-hf-publishing -->
## hal0ai-hf-publishing

Publishing models to Hugging Face under `Hal0ai` org.

HF CLI: installed in venv `~/.local/share/hf-cli-venv`; `hf` symlinked to `~/.local/bin/hf`.
Logged in as user `thinmint`, member of org `Hal0ai`. 403 gotcha: personal-only token gets 403 on
create_repo under org — needs token with org write/create rights.

Publish flow: upload runs ON CT105 (models live at /mnt/ai-models), NOT locally. Script:
`/root/rocmfp4-build/hf-publish-fastcontext.sh`.

A model card needs `hal0-banner.png` referenced at top, format disclaimer (ROCmFP4 needs fork),
benchmark table, MIT/Apache attribution. Org card = README.md in PUBLIC Space `Hal0ai/README`.
