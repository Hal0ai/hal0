root@halo:/tmp# cd hal0
root@halo:/tmp/hal0# ls
AGENTS.md        CLAUDE.md           LICENSE   README.md               dashboard-preview.png  installer      packaging       src    uv.lock
ARCHITECTURE.md  CODE_OF_CONDUCT.md  Makefile  SECURITY.md             docs                   manifest.json  pyproject.toml  tests
CHANGELOG.md     CONTRIBUTING.md     PLAN.md   dashboard-overview.png  graphify-out           oom            scripts         ui
root@halo:/tmp/hal0# installer/install.sh

   ██╗  ██╗ █████╗ ██╗      ██████╗
   ██║  ██║██╔══██╗██║     ██╔═████╗
   ███████║███████║██║     ██║██╔██║
   ██╔══██║██╔══██║██║     ████╔╝██║
   ██║  ██║██║  ██║███████╗╚██████╔╝   v1.0.0-alpha.1
   ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝ ╚═════╝
   Local AI inference, native to your hardware.

✔  FHS layout — code /usr/lib/hal0/hal0-1.0.0-alpha.1, current → /usr/lib/hal0/current, venv /usr/lib/hal0/venv
✔  Pull destination: /var/lib/hal0/models

── (1/13) Pre-flight checks ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
✔  system: Linux 7.0.6-2-pve x86_64
✔  bootstrap prereqs: curl, tar, sha256sum present (Linux)
✔  arch: x86_64
✔  network: reachable (<https://github.com>)
✔  systemd: systemd 255 (255.4-1ubuntu8.16)
✔  python: python3 (3.12)
✔  python venv: available
✔  podman: 4.9.3
✔  gpu: /dev/dri/renderD128 present
✔  gpu: /dev/kfd present (ROCm compute)
✔  npu: /dev/accel/accel0 present (AMD XDNA)
✔  gpu: /dev/dri/renderD128 → group render (gid 993)
✔  gpu: hal0 is a member of render
✔  hindsight python: python3 (3.12)
✔  writable paths: ok
✔  disk: 402 GB free on /var/lib/hal0 (need 20)
✔  disk: 402 GB free on /var/lib/hal0/models (need 20)
✔  disk: 402 GB free on /var/lib/containers/storage (need 20)
✔  port 8080: free
✔  port 3001: free

── (2/13) System user ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
✔  added hal0 to groups: render,video

── (3/13) Filesystem layout ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
✔  directories under /usr/lib/hal0/hal0-1.0.0-alpha.1, /etc/hal0, /var/lib/hal0 (pulls → /var/lib/hal0/models)
✔  Copying source to /usr/lib/hal0/hal0-1.0.0-alpha.1  (0s)
✔  current → /usr/lib/hal0/hal0-1.0.0-alpha.1

── (4/13) Python environment ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
✔  created venv at /usr/lib/hal0/venv
✔  Upgrading pip / setuptools / wheel  (1s)
✔  Installing hal0 from /usr/lib/hal0/hal0-1.0.0-alpha.1  (4s)
✔  Refreshing hal0 code in venv  (2s)
✔  hal0 cli: /usr/lib/hal0/venv/bin/hal0
✔  linked /usr/local/bin/hal0 → /usr/lib/hal0/venv/bin/hal0
✔  linked /usr/local/bin/hal0-agent → /usr/lib/hal0/venv/bin/hal0-agent

── (5/13) Node.js toolchain ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
✔  node: v22.22.2 (>= 20 LTS)

── (6/13) Dashboard UI ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
✔  Installing dashboard npm packages  (1s)
✔  Building dashboard (npm run build)  (2s)
✔  wrote /usr/lib/hal0/hal0-1.0.0-alpha.1/ui/dist

── (7/13) Configuration ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
✔  /etc/hal0/hal0.toml exists — left alone
✔  no HF_TOKEN / HUGGING_FACE_HUB_TOKEN in env — skipping (gated model pulls will need one later via the dashboard Settings -> Secrets tab, or rerun install.sh with HF_TOKEN set)
wrote /etc/hal0/openwebui.env
✔  wrote /etc/hal0/openwebui.env

── (8/13) Systemd units ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
✔  wrote /etc/systemd/system/hal0-api.service
✔  wrote /etc/systemd/system/hal0.target
✔  wrote /etc/systemd/system/hal0-openwebui.service
✔  wrote /etc/systemd/system/hal0-podman-forward.service
✔  wrote /etc/systemd/system/hal0-agent@.service
✔  wrote /etc/systemd/system/hal0-agent@hermes.service.d/override.conf
✔  wrote /usr/lib/hal0/hermes-hooks/inject-system-state.sh
✔  wrote /usr/lib/hal0/guards/run-as-hal0.sh
✔  wrote /usr/lib/hal0/bin/hal0-agentenv
✔  wrote /etc/sudoers.d/hal0-agentenv
✔  wrote /usr/lib/hal0/bin/hal0-benchctl
✔  wrote /usr/lib/hal0/bench + /var/lib/hal0/benchmarks
✔  wrote /etc/hal0/bench + /var/lib/hal0-bench + bench units
✔  wrote /etc/sudoers.d/hal0-benchctl
✔  wrote /usr/lib/hal0/bin/hal0-systemctl
✔  wrote /etc/sudoers.d/hal0-systemctl
✔  wrote /usr/lib/hal0/bin/hal0-podman-ro
✔  wrote /etc/sudoers.d/hal0-podman-ro
✔  systemctl daemon-reload
apparmor-preflight: unrelated wrote=False
apparmor-preflight-detail: Error: runc: runc create failed: unable to start container process: error during container init: exec: "true": executable file not found in $PATH: OCI runtime attempted to invoke a command that was no
✔  Pulling ghcr.io/open-webui/open-webui@sha256:7f1b0a1a50cfbac23da3b16f96bc968fd757b26dc9e54e93813d61768ea9184e in background  (3s)

── (9/13) Hardware probe ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
✔  Running first-run setup (sentinel + wiring + empty capability slots; no model picks, no downloads)
2026-07-20 17:28:55 [debug    ] hardware.probe.nvidia_smi_unavailable err='binary not found: nvidia-smi'
model store /var/lib/hal0/models is on the root filesystem — model + FLM/NPU weights will consume root-FS space; consider a dedicated mount
Seeded 0 slot(s); run `hal0 setup` or `hal0 model pull` to download models.

── (10/13) NPU prerequisites (FastFlowLM) ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
✔  apt-get update (refresh package index)  (3s)
✔  libxrt-npu2 installed (AMDXDNA NPU runtime for the host flm probe)
✔  fastflowlm 0.9.44 already installed — skipping download
✔  flm slot: /etc/hal0/slots/flm.toml exists — left alone
✔  tts slot: /etc/hal0/slots/tts.toml exists — left alone
✔  rerank slot: /etc/hal0/slots/rerank.toml exists — left alone
✔  utility slot: /etc/hal0/slots/utility.toml exists — left alone
✔  img slot: /etc/hal0/slots/img.toml exists — left alone
✔  agent slot: /etc/hal0/slots/agent.toml exists — left alone
✔  brain slot: /etc/hal0/slots/brain.toml exists — left alone
✔  profiles.toml absent — seeds served in-memory on first request
  no stale mtp=true slot overrides found
  all slot units already match the new code

── (11/13) ComfyUI model share ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
✔  ensured /mnt/ai-models/comfyui/{models,output,input,user,custom_nodes}
✔  wrote ComfyUI custom nodes → /mnt/ai-models/comfyui/custom_nodes/
✔  /mnt/ai-models/comfyui/extra_model_paths.yaml exists — left alone
✔  FLM model cache: /var/lib/hal0/.config/flm/models (container-uid writable, setgid hal0)

── (12/13) Bundle picker manifests ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
✔  installed bundle manifests → /var/lib/hal0/models/collections/omni

── (13/13) Bundled agent skills ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
✔  shipped 5 hal0 skill(s) → /usr/share/hal0/skills
✔  skill drop-in: /var/lib/hal0/skills (drop a folder here to add a skill; editable)

── (14/13) Service start ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
                           Hermes ownership audit (#843)
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Check                      ┃ Status ┃ Detail                                    ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ HERMES_HOME tree           │ ok     │ owned by hal0                             │
│ config.yaml                │ ok     │ owned by hal0                             │
│ runtime.json (embed token) │ ok     │ owned by hal0                             │
│ hermes venv                │ absent │ not present                               │
│ split-brain /root/.hermes  │ DRIFT  │ root ran Hermes; remove after reconciling │
└────────────────────────────┴────────┴───────────────────────────────────────────┘
                       Editable checkout group-share (#843)
┏━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Check             ┃ Status ┃ Detail                                             ┃
┡━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ editable checkout │ absent │ no .git (immutable FHS install) — nothing to share │
└───────────────────┴────────┴────────────────────────────────────────────────────┘
                                                     Path ownership table (P3-perms)
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Check                                                                               ┃ Status ┃ Detail                                 ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ /usr/lib/hal0 (shipped, read-only)                                                  │ ok     │ root:root 0755                         │
│ /etc/hal0 (config root)                                                             │ DRIFT  │ is hal0:hal0 2755, want hal0:hal0 2775 │
│ hal0.toml                                                                           │ DRIFT  │ is root:hal0 0600, want hal0:hal0 0600 │
│ profiles.toml                                                                       │ absent │ not present                            │
│ api.env                                                                             │ ok     │ hal0:hal0 0644                         │
│ capabilities.toml                                                                   │ ok     │ hal0:hal0 0600                         │
│ upstreams.toml                                                                      │ ok     │ hal0:hal0 0644                         │
│ hardware.json                                                                       │ DRIFT  │ is root:hal0 0644, want hal0:hal0 0644 │
│ openwebui.env                                                                       │ DRIFT  │ is root:hal0 0600, want hal0:hal0 0600 │
│ slots/ (+ *.toml)                                                                   │ ok     │ hal0:hal0 2775                         │
│ slots/ (+*.toml) :: agent.toml                                                     │ ok     │ hal0:hal0 0600                         │
│ slots/ (+ *.toml) :: brain.toml                                                     │ ok     │ hal0:hal0 0600                         │
│ slots/ (+*.toml) :: embed.toml                                                     │ ok     │ hal0:hal0 0600                         │
│ slots/ (+ *.toml) :: flm.toml                                                       │ ok     │ hal0:hal0 0600                         │
│ slots/ (+*.toml) :: img.toml                                                       │ ok     │ hal0:hal0 0600                         │
│ slots/ (+ *.toml) :: rerank.toml                                                    │ ok     │ hal0:hal0 0600                         │
│ slots/ (+*.toml) :: tts.toml                                                       │ ok     │ hal0:hal0 0600                         │
│ slots/ (+ *.toml) :: utility.toml                                                   │ ok     │ hal0:hal0 0600                         │
│ slots/ (+*.toml) :: vision.toml                                                    │ ok     │ hal0:hal0 0600                         │
│ /etc/hal0 *.lock (advisory RMW locks)                                               │ DRIFT  │ is hal0:hal0 2755, want hal0:hal0 2775 │
│ /etc/hal0*.lock (advisory RMW locks) :: capabilities.toml.lock                     │ DRIFT  │ is hal0:hal0 0644, want hal0:hal0 0664 │
│ /etc/hal0 *.lock (advisory RMW locks) :: slots.lock                                 │ ok     │ hal0:hal0 0664                         │
│ agents/                                                                             │ DRIFT  │ is root:hal0 2755, want root:root 0755 │
│ /var/lib/hal0 (state root)                                                          │ ok     │ hal0:hal0 2775                         │
│ /var/lib/hal0 *.lock (advisory RMW locks)                                           │ ok     │ hal0:hal0 2775                         │
│ .first-run.lock                                                                     │ absent │ not present                            │
│ HERMES_HOME                                                                         │ ok     │ hal0:hal0 0700                         │
│ slots/ (runtime slot state, recursive)                                              │ ok     │ hal0:hal0 2775                         │
│ slots/ (runtime slot state, recursive) :: agent                                     │ ok     │ hal0:hal0 2775                         │
│ slots/ (runtime slot state, recursive) :: agent/state.json                          │ ok     │ hal0:hal0 0600                         │
│ slots/ (runtime slot state, recursive) :: brain                                     │ ok     │ hal0:hal0 2775                         │
│ slots/ (runtime slot state, recursive) :: brain/state.json                          │ ok     │ hal0:hal0 0600                         │
│ slots/ (runtime slot state, recursive) :: embed                                     │ ok     │ hal0:hal0 2775                         │
│ slots/ (runtime slot state, recursive) :: embed/state.json                          │ ok     │ hal0:hal0 0600                         │
│ slots/ (runtime slot state, recursive) :: flm                                       │ DRIFT  │ is hal0:hal0 2755, want hal0:hal0 2775 │
│ slots/ (runtime slot state, recursive) :: flm/state.json                            │ ok     │ hal0:hal0 0600                         │
│ slots/ (runtime slot state, recursive) :: rerank                                    │ ok     │ hal0:hal0 2775                         │
│ slots/ (runtime slot state, recursive) :: rerank/state.json                         │ ok     │ hal0:hal0 0600                         │
│ slots/ (runtime slot state, recursive) :: tts                                       │ ok     │ hal0:hal0 2775                         │
│ slots/ (runtime slot state, recursive) :: tts/state.json                            │ ok     │ hal0:hal0 0600                         │
│ slots/ (runtime slot state, recursive) :: utility                                   │ DRIFT  │ is hal0:hal0 2755, want hal0:hal0 2775 │
│ slots/ (runtime slot state, recursive) :: utility/state.json                        │ ok     │ hal0:hal0 0600                         │
│ slots/ (runtime slot state, recursive) :: vision                                    │ ok     │ hal0:hal0 2775                         │
│ slots/ (runtime slot state, recursive) :: vision/state.json                         │ ok     │ hal0:hal0 0600                         │
│ registry/ (model registry)                                                          │ ok     │ hal0:hal0 2775                         │
│ registry/registry.toml                                                              │ absent │ not present                            │
│ registry/registry.toml.lock                                                         │ absent │ not present                            │
│ registry/hal0.db                                                                    │ absent │ not present                            │
│ models/ (default model store, recursive)                                            │ ok     │ hal0:hal0 2775                         │
│ models/ (default model store, recursive) :: chat-templates                          │ DRIFT  │ is hal0:hal0 2755, want hal0:hal0 2775 │
│ models/ (default model store, recursive) :: chat-templates/chatml.jinja             │ ok     │ hal0:hal0 0644                         │
│ models/ (default model store, recursive) :: chat-templates/llama3.jinja             │ ok     │ hal0:hal0 0644                         │
│ models/ (default model store, recursive) :: chat-templates/qwen3.6-27b-mtp.jinja    │ ok     │ hal0:hal0 0644                         │
│ models/ (default model store, recursive) :: collections                             │ DRIFT  │ is root:hal0 2755, want hal0:hal0 2775 │
│ models/ (default model store, recursive) :: collections/omni                        │ DRIFT  │ is root:hal0 2755, want hal0:hal0 2775 │
│ models/ (default model store, recursive) :: collections/omni/LMX-Omni-52B-Halo.json │ DRIFT  │ is root:hal0 0644, want hal0:hal0 0644 │
│ models/ (default model store, recursive) :: collections/omni/hal0-default.json      │ DRIFT  │ is root:hal0 0644, want hal0:hal0 0644 │
│ models/ (default model store, recursive) :: collections/omni/hal0-lite.json         │ DRIFT  │ is root:hal0 0644, want hal0:hal0 0644 │
│ models/ (default model store, recursive) :: collections/omni/hal0-max.json          │ DRIFT  │ is root:hal0 0644, want hal0:hal0 0644 │
│ models/ (default model store, recursive) :: collections/omni/hal0-pro.json          │ DRIFT  │ is root:hal0 0644, want hal0:hal0 0644 │
│ agents/ (per-agent sub-homes)                                                       │ absent │ not present                            │
│ secrets/                                                                            │ DRIFT  │ is root:root 0700, want root:root 0755 │
│ secrets/agents/ (+ <id>.env)                                                        │ DRIFT  │ is root:root 0700, want root:root 0755 │
│ secrets/agents/ (+ <id>.env) :: hermes.env                                          │ ok     │ root:root 0600                         │
│ benchmarks/ (+ subdirs)                                                             │ DRIFT  │ is hal0:hal0 0755, want hal0:hal0 2775 │
│ benchmarks/ (+ subdirs) :: logs                                                     │ DRIFT  │ is hal0:hal0 0755, want hal0:hal0 2775 │
│ benchmarks/ (+ subdirs) :: runs                                                     │ DRIFT  │ is hal0:hal0 0755, want hal0:hal0 2775 │
│ benchmarks/ (+ subdirs) :: server-ab                                                │ DRIFT  │ is hal0:hal0 0755, want hal0:hal0 2775 │
│ skills/ (drop-in agent skills)                                                      │ ok     │ hal0:hal0 2775                         │
│ STATE.md (session-state snapshot)                                                   │ DRIFT  │ is hal0:hal0 0644, want hal0:hal0 0664 │
│ /var/log/hal0                                                                       │ ok     │ hal0:hal0 0755                         │
└─────────────────────────────────────────────────────────────────────────────────────┴────────┴────────────────────────────────────────┘
nothing to fix — not an editable checkout.
✓  ownership table applied (24 path(s) reconciled).
✗  Hermes ownership drift — run `sudo hal0 agent bootstrap hermes --repair` to reconcile.
!  '/usr/lib/hal0/venv/bin/hal0 doctor perms --fix' reported drift/errors — re-run 'sudo /usr/lib/hal0/venv/bin/hal0 doctor perms --fix' after install
Created symlink /etc/systemd/system/multi-user.target.wants/hal0-api.service -> /etc/systemd/system/hal0-api.service.
✔  hal0-api is running
Created symlink /etc/systemd/system/multi-user.target.wants/hal0.target -> /etc/systemd/system/hal0.target.
✔  hal0.target enabled — slots will autostart after reboot
✔  setting up Hindsight memory engine (venv + daemon) — this can take a few minutes…
Created symlink /etc/systemd/system/multi-user.target.wants/hindsight-api.service -> /etc/systemd/system/hindsight-api.service.
✔  hindsight-api is running (memory engine on 127.0.0.1:9177)
!  memory bank seeding incomplete — banks also lazy-create on first write
Created symlink /etc/systemd/system/multi-user.target.wants/hal0-openwebui.service -> /etc/systemd/system/hal0-openwebui.service.
✔  hal0-openwebui is running (chat at :3001)
✔  hal0-podman-forward enabled (keeps podman ports reachable alongside Docker)
✔  wrote /usr/local/bin/hermes (+ hal0-hermes symlink) — §7.4 root prelude
✔  provisioning Hermes agent (toolchain + bootstrap) — this can take a few minutes…
Ensuring toolchain (python · venv · pip · pipx)…
[hermes-prereqs] toolchain already present (python venv + pip + pipx + interpreter path) — nothing to do
Provisioning Hermes → /var/lib/hal0/venvs/hermes …
Requirement already satisfied: pip in /var/lib/hal0/venvs/hermes/lib/python3.12/site-packages (24.0)
Collecting pip
  Using cached pip-26.1.2-py3-none-any.whl.metadata (4.6 kB)
Using cached pip-26.1.2-py3-none-any.whl (1.8 MB)
Installing collected packages: pip
  Attempting uninstall: pip
    Found existing installation: pip 24.0
    Uninstalling pip-24.0:
      Successfully uninstalled pip-24.0
Successfully installed pip-26.1.2
Collecting hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596> (from hermes-agent[web] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached hermes_agent-0.18.2-py3-none-any.whl
Collecting openai==2.24.0 (from hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached openai-2.24.0-py3-none-any.whl.metadata (29 kB)
Collecting certifi==2026.5.20 (from hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached certifi-2026.5.20-py3-none-any.whl.metadata (2.5 kB)
Collecting python-dotenv==1.2.2 (from hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached python_dotenv-1.2.2-py3-none-any.whl.metadata (27 kB)
Collecting fire==0.7.1 (from hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached fire-0.7.1-py3-none-any.whl.metadata (5.8 kB)
Collecting httpx==0.28.1 (from httpx[socks]==0.28.1->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached httpx-0.28.1-py3-none-any.whl.metadata (7.1 kB)
Collecting rich==14.3.3 (from hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached rich-14.3.3-py3-none-any.whl.metadata (18 kB)
Collecting tenacity==9.1.4 (from hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached tenacity-9.1.4-py3-none-any.whl.metadata (1.2 kB)
Collecting pyyaml==6.0.3 (from hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached pyyaml-6.0.3-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl.metadata (2.4 kB)
Collecting ruamel.yaml==0.18.17 (from hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached ruamel_yaml-0.18.17-py3-none-any.whl.metadata (27 kB)
Collecting requests==2.33.0 (from hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached requests-2.33.0-py3-none-any.whl.metadata (5.1 kB)
Collecting jinja2==3.1.6 (from hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached jinja2-3.1.6-py3-none-any.whl.metadata (2.9 kB)
Collecting pydantic==2.13.4 (from hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached pydantic-2.13.4-py3-none-any.whl.metadata (109 kB)
Collecting prompt_toolkit==3.0.52 (from hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached prompt_toolkit-3.0.52-py3-none-any.whl.metadata (6.4 kB)
Collecting croniter==6.0.0 (from hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached croniter-6.0.0-py2.py3-none-any.whl.metadata (32 kB)
Collecting packaging==26.0 (from hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached packaging-26.0-py3-none-any.whl.metadata (3.3 kB)
Collecting Markdown==3.10.2 (from hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached markdown-3.10.2-py3-none-any.whl.metadata (5.1 kB)
Collecting PyJWT==2.13.0 (from PyJWT[crypto]==2.13.0->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached pyjwt-2.13.0-py3-none-any.whl.metadata (3.4 kB)
Collecting urllib3<3,>=2.7.0 (from hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached urllib3-2.7.0-py3-none-any.whl.metadata (6.9 kB)
Collecting cryptography==46.0.7 (from hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached cryptography-46.0.7-cp311-abi3-manylinux_2_34_x86_64.whl.metadata (5.7 kB)
Collecting psutil==7.2.2 (from hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached psutil-7.2.2-cp36-abi3-manylinux2010_x86_64.manylinux_2_12_x86_64.manylinux_2_28_x86_64.whl.metadata (22 kB)
Collecting websockets==15.0.1 (from hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached websockets-15.0.1-cp312-cp312-manylinux_2_5_x86_64.manylinux1_x86_64.manylinux_2_17_x86_64.manylinux2014_x86_64.whl.metadata (6.8 kB)
Collecting pathspec==1.1.1 (from hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached pathspec-1.1.1-py3-none-any.whl.metadata (14 kB)
Collecting fastapi<1,>=0.104.0 (from hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached fastapi-0.139.2-py3-none-any.whl.metadata (26 kB)
Collecting uvicorn<1,>=0.24.0 (from uvicorn[standard]<1,>=0.24.0->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached uvicorn-0.51.0-py3-none-any.whl.metadata (6.6 kB)
Collecting python-multipart<1,>=0.0.9 (from hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached python_multipart-0.0.32-py3-none-any.whl.metadata (2.1 kB)
Collecting ptyprocess<1,>=0.7.0 (from hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached ptyprocess-0.7.0-py2.py3-none-any.whl.metadata (1.3 kB)
Collecting Pillow==12.2.0 (from hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached pillow-12.2.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl.metadata (8.8 kB)
Collecting fastapi<1,>=0.104.0 (from hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached fastapi-0.133.1-py3-none-any.whl.metadata (30 kB)
Collecting uvicorn<1,>=0.24.0 (from uvicorn[standard]<1,>=0.24.0->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached uvicorn-0.41.0-py3-none-any.whl.metadata (6.7 kB)
Collecting starlette==1.0.1 (from hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached starlette-1.0.1-py3-none-any.whl.metadata (6.3 kB)
Collecting python-multipart<1,>=0.0.9 (from hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached python_multipart-0.0.27-py3-none-any.whl.metadata (2.1 kB)
Collecting typing-extensions>=4.8.0 (from fastapi<1,>=0.104.0->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached typing_extensions-4.16.0-py3-none-any.whl.metadata (3.3 kB)
Collecting typing-inspection>=0.4.2 (from fastapi<1,>=0.104.0->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached typing_inspection-0.4.2-py3-none-any.whl.metadata (2.6 kB)
Collecting annotated-doc>=0.0.2 (from fastapi<1,>=0.104.0->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached annotated_doc-0.0.4-py3-none-any.whl.metadata (6.6 kB)
Collecting click>=7.0 (from uvicorn<1,>=0.24.0->uvicorn[standard]<1,>=0.24.0->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached click-8.4.2-py3-none-any.whl.metadata (2.6 kB)
Collecting h11>=0.8 (from uvicorn<1,>=0.24.0->uvicorn[standard]<1,>=0.24.0->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached h11-0.16.0-py3-none-any.whl.metadata (8.3 kB)
Collecting httptools>=0.6.3 (from uvicorn[standard]<1,>=0.24.0->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached httptools-0.8.0-cp312-cp312-manylinux1_x86_64.manylinux_2_28_x86_64.manylinux_2_5_x86_64.whl.metadata (3.5 kB)
Collecting uvloop>=0.15.1 (from uvicorn[standard]<1,>=0.24.0->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached uvloop-0.22.1-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl.metadata (4.9 kB)
Collecting watchfiles>=0.20 (from uvicorn[standard]<1,>=0.24.0->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached watchfiles-1.2.0-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl.metadata (4.9 kB)
Collecting python-dateutil (from croniter==6.0.0->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached python_dateutil-2.9.0.post0-py2.py3-none-any.whl.metadata (8.4 kB)
Collecting pytz>2021.1 (from croniter==6.0.0->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached pytz-2026.2-py2.py3-none-any.whl.metadata (22 kB)
Collecting cffi>=2.0.0 (from cryptography==46.0.7->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached cffi-2.1.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl.metadata (2.5 kB)
Collecting termcolor (from fire==0.7.1->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached termcolor-3.3.0-py3-none-any.whl.metadata (6.5 kB)
Collecting anyio (from httpx==0.28.1->httpx[socks]==0.28.1->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached anyio-4.14.2-py3-none-any.whl.metadata (4.6 kB)
Collecting httpcore==1.* (from httpx==0.28.1->httpx[socks]==0.28.1->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached httpcore-1.0.9-py3-none-any.whl.metadata (21 kB)
Collecting idna (from httpx==0.28.1->httpx[socks]==0.28.1->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached idna-3.18-py3-none-any.whl.metadata (6.1 kB)
Collecting socksio==1.* (from httpx[socks]==0.28.1->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached socksio-1.0.0-py3-none-any.whl.metadata (6.1 kB)
Collecting MarkupSafe>=2.0 (from jinja2==3.1.6->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached markupsafe-3.0.3-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl.metadata (2.7 kB)
Collecting distro<2,>=1.7.0 (from openai==2.24.0->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached distro-1.9.0-py3-none-any.whl.metadata (6.8 kB)
Collecting jiter<1,>=0.10.0 (from openai==2.24.0->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached jiter-0.16.0-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl.metadata (5.2 kB)
Collecting sniffio (from openai==2.24.0->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached sniffio-1.3.1-py3-none-any.whl.metadata (3.9 kB)
Collecting tqdm>4 (from openai==2.24.0->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached tqdm-4.69.0-py3-none-any.whl.metadata (57 kB)
Collecting annotated-types>=0.6.0 (from pydantic==2.13.4->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached annotated_types-0.7.0-py3-none-any.whl.metadata (15 kB)
Collecting pydantic-core==2.46.4 (from pydantic==2.13.4->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached pydantic_core-2.46.4-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl.metadata (6.6 kB)
Collecting wcwidth (from prompt_toolkit==3.0.52->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached wcwidth-0.8.2-py3-none-any.whl.metadata (43 kB)
Collecting charset_normalizer<4,>=2 (from requests==2.33.0->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached charset_normalizer-3.4.9-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl.metadata (41 kB)
Collecting markdown-it-py>=2.2.0 (from rich==14.3.3->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached markdown_it_py-4.2.0-py3-none-any.whl.metadata (7.4 kB)
Collecting pygments<3.0.0,>=2.13.0 (from rich==14.3.3->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached pygments-2.20.0-py3-none-any.whl.metadata (2.5 kB)
Collecting ruamel.yaml.clib>=0.2.15 (from ruamel.yaml==0.18.17->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached ruamel_yaml_clib-0.2.15-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl.metadata (3.5 kB)
Collecting pycparser (from cffi>=2.0.0->cryptography==46.0.7->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached pycparser-3.0-py3-none-any.whl.metadata (8.2 kB)
Collecting mdurl~=0.1 (from markdown-it-py>=2.2.0->rich==14.3.3->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached mdurl-0.1.2-py3-none-any.whl.metadata (1.6 kB)
Collecting six>=1.5 (from python-dateutil->croniter==6.0.0->hermes-agent @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->hermes-agent[web>] @ git+<https://github.com/NousResearch/hermes-agent.git@9de9c25f620ff7f1ce0fd5457d596052d5159596->-r> /usr/lib/hal0/current/installer/agents/hermes/requirements.txt (line 16))
  Using cached six-1.17.0-py2.py3-none-any.whl.metadata (1.7 kB)
Using cached fastapi-0.133.1-py3-none-any.whl (109 kB)
Using cached python_multipart-0.0.27-py3-none-any.whl (29 kB)
Using cached uvicorn-0.41.0-py3-none-any.whl (68 kB)
Using cached certifi-2026.5.20-py3-none-any.whl (134 kB)
Using cached croniter-6.0.0-py2.py3-none-any.whl (25 kB)
Using cached cryptography-46.0.7-cp311-abi3-manylinux_2_34_x86_64.whl (4.5 MB)
Using cached fire-0.7.1-py3-none-any.whl (115 kB)
Using cached httpx-0.28.1-py3-none-any.whl (73 kB)
Using cached jinja2-3.1.6-py3-none-any.whl (134 kB)
Using cached markdown-3.10.2-py3-none-any.whl (108 kB)
Using cached openai-2.24.0-py3-none-any.whl (1.1 MB)
Using cached pydantic-2.13.4-py3-none-any.whl (472 kB)
Using cached packaging-26.0-py3-none-any.whl (74 kB)
Using cached pathspec-1.1.1-py3-none-any.whl (57 kB)
Using cached pillow-12.2.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl (7.1 MB)
Using cached prompt_toolkit-3.0.52-py3-none-any.whl (391 kB)
Using cached psutil-7.2.2-cp36-abi3-manylinux2010_x86_64.manylinux_2_12_x86_64.manylinux_2_28_x86_64.whl (155 kB)
Using cached pydantic_core-2.46.4-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl (2.1 MB)
Using cached pyjwt-2.13.0-py3-none-any.whl (31 kB)
Using cached python_dotenv-1.2.2-py3-none-any.whl (22 kB)
Using cached pyyaml-6.0.3-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl (807 kB)
Using cached requests-2.33.0-py3-none-any.whl (65 kB)
Using cached rich-14.3.3-py3-none-any.whl (310 kB)
Using cached ruamel_yaml-0.18.17-py3-none-any.whl (121 kB)
Using cached starlette-1.0.1-py3-none-any.whl (72 kB)
Using cached tenacity-9.1.4-py3-none-any.whl (28 kB)
Using cached websockets-15.0.1-cp312-cp312-manylinux_2_5_x86_64.manylinux1_x86_64.manylinux_2_17_x86_64.manylinux2014_x86_64.whl (182 kB)
Using cached anyio-4.14.2-py3-none-any.whl (125 kB)
Using cached charset_normalizer-3.4.9-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl (224 kB)
Using cached distro-1.9.0-py3-none-any.whl (20 kB)
Using cached httpcore-1.0.9-py3-none-any.whl (78 kB)
Using cached idna-3.18-py3-none-any.whl (65 kB)
Using cached jiter-0.16.0-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl (343 kB)
Using cached ptyprocess-0.7.0-py2.py3-none-any.whl (13 kB)
Using cached pygments-2.20.0-py3-none-any.whl (1.2 MB)
Using cached socksio-1.0.0-py3-none-any.whl (12 kB)
Using cached typing_extensions-4.16.0-py3-none-any.whl (45 kB)
Using cached urllib3-2.7.0-py3-none-any.whl (131 kB)
Using cached annotated_doc-0.0.4-py3-none-any.whl (5.3 kB)
Using cached annotated_types-0.7.0-py3-none-any.whl (13 kB)
Using cached cffi-2.1.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.whl (221 kB)
Using cached click-8.4.2-py3-none-any.whl (119 kB)
Using cached h11-0.16.0-py3-none-any.whl (37 kB)
Using cached httptools-0.8.0-cp312-cp312-manylinux1_x86_64.manylinux_2_28_x86_64.manylinux_2_5_x86_64.whl (523 kB)
Using cached markdown_it_py-4.2.0-py3-none-any.whl (91 kB)
Using cached mdurl-0.1.2-py3-none-any.whl (10.0 kB)
Using cached markupsafe-3.0.3-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl (22 kB)
Using cached pytz-2026.2-py2.py3-none-any.whl (510 kB)
Using cached ruamel_yaml_clib-0.2.15-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl (788 kB)
Using cached tqdm-4.69.0-py3-none-any.whl (676 kB)
Using cached typing_inspection-0.4.2-py3-none-any.whl (14 kB)
Using cached uvloop-0.22.1-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl (4.4 MB)
Using cached watchfiles-1.2.0-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl (456 kB)
Using cached pycparser-3.0-py3-none-any.whl (48 kB)
Using cached python_dateutil-2.9.0.post0-py2.py3-none-any.whl (229 kB)
Using cached six-1.17.0-py2.py3-none-any.whl (11 kB)
Using cached sniffio-1.3.1-py3-none-any.whl (10 kB)
Using cached termcolor-3.3.0-py3-none-any.whl (7.7 kB)
Using cached wcwidth-0.8.2-py3-none-any.whl (323 kB)
Installing collected packages: pytz, ptyprocess, websockets, wcwidth, uvloop, urllib3, typing-extensions, tqdm, termcolor, tenacity, socksio, sniffio, six, ruamel.yaml.clib, pyyaml, python-multipart, python-dotenv, PyJWT, pygments, pycparser, psutil, Pillow, pathspec, packaging, mdurl, MarkupSafe, Markdown, jiter, idna, httptools, h11, distro, click, charset_normalizer, certifi, annotated-types, annotated-doc, uvicorn, typing-inspection, ruamel.yaml, requests, python-dateutil, pydantic-core, prompt_toolkit, markdown-it-py, jinja2, httpcore, fire, cffi, anyio, watchfiles, starlette, rich, pydantic, httpx, cryptography, croniter, openai, fastapi, hermes-agent
Successfully installed Markdown-3.10.2 MarkupSafe-3.0.3 Pillow-12.2.0 PyJWT-2.13.0 annotated-doc-0.0.4 annotated-types-0.7.0 anyio-4.14.2 certifi-2026.5.20 cffi-2.1.0 charset_normalizer-3.4.9 click-8.4.2 croniter-6.0.0 cryptography-46.0.7 distro-1.9.0 fastapi-0.133.1 fire-0.7.1 h11-0.16.0 hermes-agent-0.18.2 httpcore-1.0.9 httptools-0.8.0 httpx-0.28.1 idna-3.18 jinja2-3.1.6 jiter-0.16.0 markdown-it-py-4.2.0 mdurl-0.1.2 openai-2.24.0 packaging-26.0 pathspec-1.1.1 prompt_toolkit-3.0.52 psutil-7.2.2 ptyprocess-0.7.0 pycparser-3.0 pydantic-2.13.4 pydantic-core-2.46.4 pygments-2.20.0 python-dateutil-2.9.0.post0 python-dotenv-1.2.2 python-multipart-0.0.27 pytz-2026.2 pyyaml-6.0.3 requests-2.33.0 rich-14.3.3 ruamel.yaml-0.18.17 ruamel.yaml.clib-0.2.15 six-1.17.0 sniffio-1.3.1 socksio-1.0.0 starlette-1.0.1 tenacity-9.1.4 termcolor-3.3.0 tqdm-4.69.0 typing-extensions-4.16.0 typing-inspection-0.4.2 urllib3-2.7.0 uvicorn-0.41.0 uvloop-0.22.1 watchfiles-1.2.0 wcwidth-0.8.2 websockets-15.0.1
Created symlink /etc/systemd/system/multi-user.target.wants/hal0-agent@hermes.service → /etc/systemd/system/hal0-agent@.service.
Installing hermes gateway (Telegram/Discord bridge)…
System gateway install requires root. Re-run with sudo.
hermes gateway install failed — Telegram/Discord bridge unavailable; continuing.
hermes-gateway unit not installed (/etc/systemd/system/hermes-gateway.service missing) — Telegram/Discord bridge unavailable.
retry with: sudo -u hal0 env -u HERMES_HOME /var/lib/hal0/venvs/hermes/bin/hermes gateway install --system --run-as-user hal0 </dev/null
╭──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ Installed hermes  (managed venv: /var/lib/hal0/venvs/hermes)                                                                                                         │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
✔  hermes provisioned — config.yaml + MCP servers + skills wired
✔  hal0-agent@hermes is running (chat at 127.0.0.1:9119, proxied by hal0-api)
✔  installing system-scope hermes gateway (User=hal0)
System gateway install requires root. Re-run with sudo.
!  hermes gateway install failed — Telegram/Discord bridge unavailable; continuing
!  hermes-gateway unit not installed (/etc/systemd/system/hermes-gateway.service missing) — Telegram/Discord bridge unavailable
!    retry with 'sudo -u hal0 env -u HERMES_HOME /var/lib/hal0/venvs/hermes/bin/hermes gateway install --system --run-as-user hal0 </dev/null'

┌─ hal0 is ready ─────────────────────────────────────────────────────────────┐
│ CLI         /usr/lib/hal0/venv/bin/hal0                                      │
│ Config      /etc/hal0                                                        │
│ Data        /var/lib/hal0                                                    │
│ Dashboard   <http://10.0.1.150:8080>                                           │
│ Chat        <http://10.0.1.150:3001>                                           │
│ TLS         upstream-only (front with Traefik / nginx / Cloudflare Tunnel)   │
│ Auth        open on the trusted LAN — front with a reverse proxy if exposed │
│ Logs        journalctl -fu hal0-api                                          │
│                                                                              │
│ Reach hal0 at:                                                               │
│   LAN          <http://10.0.1.150:8080/>                                       │
│                                                                              │
│ Next steps:                                                                  │
│   hal0 setup          guided first-run: provision slots + choose models      │
│   hal0 model pull <id> download a model (browse with hal0 model list)        │
│   hal0 status         system + slot + memory summary                         │
│   hal0 slot list      inspect configured slots                               │
│   hal0 update         check for + apply updates                              │
│                                                                              │
│ Docs <https://github.com/Hal0ai/hal0>  ·  Logs journalctl -fu hal0-api        │
└──────────────────────────────────────────────────────────────────────────────┘

Launch the guided hal0 setup now? [Y/n] y
2026-07-21 01:14:23 [debug    ] hardware.probe.nvidia_smi_unavailable err='binary not found: nvidia-smi'
hal0 setup — guided install

Model storage directory (/var/lib/hal0/models):
Enable NPU inference (STT offload)? [y/n] (n): y
Install the default extension set (Apps + Agents)? [y/n] (y):
Scaffold capability slots now (choose models later)? [y/n] (y):
Show the verification report when setup completes? [y/n] (y):
╭───────────────────────────────────────────────────────────────── Traceback (most recent call last) ──────────────────────────────────────────────────────────────────╮
│ /usr/lib/hal0/venv/lib/python3.12/site-packages/httpx/_transports/default.py:101 in map_httpcore_exceptions                                                          │
│                                                                                                                                                                      │
│    98 │   if len(HTTPCORE_EXC_MAP) == 0:                                                                                                                             │
│    99 │   │   HTTPCORE_EXC_MAP =_load_httpcore_exceptions()                                                                                                         │
│   100 │   try:                                                                                                                                                       │
│ ❱ 101 │   │   yield                                                                                                                                                  │
│   102 │   except Exception as exc:                                                                                                                                   │
│   103 │   │   mapped_exc = None                                                                                                                                      │
│   104                                                                                                                                                                │
│                                                                                                                                                                      │
│ /usr/lib/hal0/venv/lib/python3.12/site-packages/httpx/_transports/default.py:394 in handle_async_request                                                             │
│                                                                                                                                                                      │
│   391 │   │   │   extensions=request.extensions,                                                                                                                     │
│   392 │   │   )                                                                                                                                                      │
│   393 │   │   with map_httpcore_exceptions():                                                                                                                        │
│ ❱ 394 │   │   │   resp = await self._pool.handle_async_request(req)                                                                                                  │
│   395 │   │                                                                                                                                                          │
│   396 │   │   assert isinstance(resp.stream, typing.AsyncIterable)                                                                                                   │
│   397                                                                                                                                                                │
│                                                                                                                                                                      │
│ /usr/lib/hal0/venv/lib/python3.12/site-packages/httpcore/_async/connection_pool.py:256 in handle_async_request                                                       │
│                                                                                                                                                                      │
│   253 │   │   │   │   closing = self._assign_requests_to_connections()                                                                                               │
│   254 │   │   │                                                                                                                                                      │
│   255 │   │   │   await self._close_connections(closing)                                                                                                             │
│ ❱ 256 │   │   │   raise exc from None                                                                                                                                │
│   257 │   │                                                                                                                                                          │
│   258 │   │   # Return the response. Note that in this case we still have to manage                                                                                  │
│   259 │   │   # the point at which the response is closed.                                                                                                           │
│                                                                                                                                                                      │
│ /usr/lib/hal0/venv/lib/python3.12/site-packages/httpcore/_async/connection_pool.py:236 in handle_async_request                                                       │
│                                                                                                                                                                      │
│   233 │   │   │   │                                                                                                                                                  │
│   234 │   │   │   │   try:                                                                                                                                           │
│   235 │   │   │   │   │   # Send the request on the assigned connection.                                                                                             │
│ ❱ 236 │   │   │   │   │   response = await connection.handle_async_request(                                                                                          │
│   237 │   │   │   │   │   │   pool_request.request                                                                                                                   │
│   238 │   │   │   │   │   )                                                                                                                                          │
│   239 │   │   │   │   except ConnectionNotAvailable:                                                                                                                 │
│                                                                                                                                                                      │
│ /usr/lib/hal0/venv/lib/python3.12/site-packages/httpcore/_async/connection.py:103 in handle_async_request                                                            │
│                                                                                                                                                                      │
│   100 │   │   │   self._connect_failed = True                                                                                                                        │
│   101 │   │   │   raise exc                                                                                                                                          │
│   102 │   │                                                                                                                                                          │
│ ❱ 103 │   │   return await self._connection.handle_async_request(request)                                                                                            │
│   104 │                                                                                                                                                              │
│   105 │   async def _connect(self, request: Request) -> AsyncNetworkStream:                                                                                          │
│   106 │   │   timeouts = request.extensions.get("timeout", {})                                                                                                       │
│                                                                                                                                                                      │
│ /usr/lib/hal0/venv/lib/python3.12/site-packages/httpcore/_async/http11.py:136 in handle_async_request                                                                │
│                                                                                                                                                                      │
│   133 │   │   │   with AsyncShieldCancellation():                                                                                                                    │
│   134 │   │   │   │   async with Trace("response_closed", logger, request) as trace:                                                                                 │
│   135 │   │   │   │   │   await self._response_closed()                                                                                                              │
│ ❱ 136 │   │   │   raise exc                                                                                                                                          │
│   137 │                                                                                                                                                              │
│   138 │   # Sending the request...                                                                                                                                   │
│   139                                                                                                                                                                │
│                                                                                                                                                                      │
│ /usr/lib/hal0/venv/lib/python3.12/site-packages/httpcore/_async/http11.py:106 in handle_async_request                                                                │
│                                                                                                                                                                      │
│   103 │   │   │   │   │   reason_phrase,                                                                                                                             │
│   104 │   │   │   │   │   headers,                                                                                                                                   │
│   105 │   │   │   │   │   trailing_data,                                                                                                                             │
│ ❱ 106 │   │   │   │   ) = await self._receive_response_headers(**kwargs)                                                                                             │
│   107 │   │   │   │   trace.return_value = (                                                                                                                         │
│   108 │   │   │   │   │   http_version,                                                                                                                              │
│   109 │   │   │   │   │   status,                                                                                                                                    │
│                                                                                                                                                                      │
│ /usr/lib/hal0/venv/lib/python3.12/site-packages/httpcore/_async/http11.py:177 in _receive_response_headers                                                           │
│                                                                                                                                                                      │
│   174 │   │   timeout = timeouts.get("read", None)                                                                                                                   │
│   175 │   │                                                                                                                                                          │
│   176 │   │   while True:                                                                                                                                            │
│ ❱ 177 │   │   │   event = await self._receive_event(timeout=timeout)                                                                                                 │
│   178 │   │   │   if isinstance(event, h11.Response):                                                                                                                │
│   179 │   │   │   │   break                                                                                                                                          │
│   180 │   │   │   if (                                                                                                                                               │
│                                                                                                                                                                      │
│ /usr/lib/hal0/venv/lib/python3.12/site-packages/httpcore/_async/http11.py:217 in _receive_event                                                                      │
│                                                                                                                                                                      │
│   214 │   │   │   │   event = self._h11_state.next_event()                                                                                                           │
│   215 │   │   │                                                                                                                                                      │
│   216 │   │   │   if event is h11.NEED_DATA:                                                                                                                         │
│ ❱ 217 │   │   │   │   data = await self._network_stream.read(                                                                                                        │
│   218 │   │   │   │   │   self.READ_NUM_BYTES, timeout=timeout                                                                                                       │
│   219 │   │   │   │   )                                                                                                                                              │
│   220                                                                                                                                                                │
│                                                                                                                                                                      │
│ /usr/lib/hal0/venv/lib/python3.12/site-packages/httpcore/_backends/anyio.py:32 in read                                                                               │
│                                                                                                                                                                      │
│    29 │   │   │   anyio.ClosedResourceError: ReadError,                                                                                                              │
│    30 │   │   │   anyio.EndOfStream: ReadError,                                                                                                                      │
│    31 │   │   }                                                                                                                                                      │
│ ❱  32 │   │   with map_exceptions(exc_map):                                                                                                                          │
│    33 │   │   │   with anyio.fail_after(timeout):                                                                                                                    │
│    34 │   │   │   │   try:                                                                                                                                           │
│    35 │   │   │   │   │   return await self._stream.receive(max_bytes=max_bytes)                                                                                     │
│                                                                                                                                                                      │
│ /usr/lib/python3.12/contextlib.py:158 in __exit__                                                                                                                    │
│                                                                                                                                                                      │
│   155 │   │   │   │   # tell if we get the same exception back                                                                                                       │
│   156 │   │   │   │   value = typ()                                                                                                                                  │
│   157 │   │   │   try:                                                                                                                                               │
│ ❱ 158 │   │   │   │   self.gen.throw(value)                                                                                                                          │
│   159 │   │   │   except StopIteration as exc:                                                                                                                       │
│   160 │   │   │   │   # Suppress StopIteration *unless* it's the same exception that                                                                                 │
│   161 │   │   │   │   # was passed to throw().  This prevents a StopIteration                                                                                        │
│                                                                                                                                                                      │
│ /usr/lib/hal0/venv/lib/python3.12/site-packages/httpcore/_exceptions.py:14 in map_exceptions                                                                         │
│                                                                                                                                                                      │
│   11 │   except Exception as exc:  # noqa: PIE786                                                                                                                    │
│   12 │   │   for from_exc, to_exc in map.items():                                                                                                                    │
│   13 │   │   │   if isinstance(exc, from_exc):                                                                                                                       │
│ ❱ 14 │   │   │   │   raise to_exc(exc) from exc                                                                                                                      │
│   15 │   │   raise  # pragma: nocover                                                                                                                                │
│   16                                                                                                                                                                 │
│   17                                                                                                                                                                 │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
ReadTimeout

The above exception was the direct cause of the following exception:

╭───────────────────────────────────────────────────────────────── Traceback (most recent call last) ──────────────────────────────────────────────────────────────────╮
│ /usr/lib/hal0/venv/lib/python3.12/site-packages/hal0/cli/setup_command.py:300 in setup                                                                               │
│                                                                                                                                                                      │
│   297 │   │   )                                                                                                                                                      │
│   298 │   │   asyncio.run(_run_auto(sel, hw, no_pull=no_pull))                                                                                                       │
│   299 │   │   return                                                                                                                                                 │
│ ❱ 300 │   run_interactive(hw, storage_dir=storage_dir)                                                                                                               │
│   301                                                                                                                                                                │
│   302                                                                                                                                                                │
│   303 async def _run_auto(sel: Selections, hw: HardwareInfo, *, no_pull: bool = False) -> None:                                                                      │
│                                                                                                                                                                      │
│ /usr/lib/hal0/venv/lib/python3.12/site-packages/hal0/cli/setup_command.py:420 in run_interactive                                                                     │
│                                                                                                                                                                      │
│   417 │   │   existing_slots=_existing_slot_names(),                                                                                                                 │
│   418 │   │   npu_opt_in=npu_opt_in,                                                                                                                                 │
│   419 │   )                                                                                                                                                          │
│ ❱ 420 │   asyncio.run(_run_auto(sel, hw, no_pull=False))                                                                                                             │
│   421 │                                                                                                                                                              │
│   422 │   if launch_on_completion:                                                                                                                                   │
│   423 │   │   try:                                                                                                                                                   │
│                                                                                                                                                                      │
│ /usr/lib/python3.12/asyncio/runners.py:194 in run                                                                                                                    │
│                                                                                                                                                                      │
│   191 │   │   │   "asyncio.run() cannot be called from a running event loop")                                                                                        │
│   192 │                                                                                                                                                              │
│   193 │   with Runner(debug=debug, loop_factory=loop_factory) as runner:                                                                                             │
│ ❱ 194 │   │   return runner.run(main)                                                                                                                                │
│   195                                                                                                                                                                │
│   196                                                                                                                                                                │
│   197 def _cancel_all_tasks(loop):                                                                                                                                   │
│                                                                                                                                                                      │
│ /usr/lib/python3.12/asyncio/runners.py:118 in run                                                                                                                    │
│                                                                                                                                                                      │
│   115 │   │                                                                                                                                                          │
│   116 │   │   self._interrupt_count = 0                                                                                                                              │
│   117 │   │   try:                                                                                                                                                   │
│ ❱ 118 │   │   │   return self._loop.run_until_complete(task)                                                                                                         │
│   119 │   │   except exceptions.CancelledError:                                                                                                                      │
│   120 │   │   │   if self._interrupt_count > 0:                                                                                                                      │
│   121 │   │   │   │   uncancel = getattr(task, "uncancel", None)                                                                                                     │
│                                                                                                                                                                      │
│ /usr/lib/python3.12/asyncio/base_events.py:687 in run_until_complete                                                                                                 │
│                                                                                                                                                                      │
│    684 │   │   if not future.done():                                                                                                                                 │
│    685 │   │   │   raise RuntimeError('Event loop stopped before Future completed.')                                                                                 │
│    686 │   │                                                                                                                                                         │
│ ❱  687 │   │   return future.result()                                                                                                                                │
│    688 │                                                                                                                                                             │
│    689 │   def stop(self):                                                                                                                                           │
│    690 │   │   """Stop running the event loop.                                                                                                                       │
│                                                                                                                                                                      │
│ /usr/lib/hal0/venv/lib/python3.12/site-packages/hal0/cli/setup_command.py:309 in _run_auto                                                                           │
│                                                                                                                                                                      │
│   306 │   `hal0 setup --auto` on a live service doesn't drift the roster)."""                                                                                        │
│   307 │   from hal0.cli.setup_install import run_install                                                                                                             │
│   308 │                                                                                                                                                              │
│ ❱ 309 │   await run_install(sel, hw, no_pull=no_pull)                                                                                                                │
│   310                                                                                                                                                                │
│   311                                                                                                                                                                │
│   312 # ── Minimal interactive wizard (§17.8) ──────────────────────────────────────                                                                                 │
│                                                                                                                                                                      │
│ /usr/lib/hal0/venv/lib/python3.12/site-packages/hal0/cli/setup_install.py:72 in run_install                                                                          │
│                                                                                                                                                                      │
│    69 │   always defers pulls to BackgroundTasks and ignores this flag.                                                                                              │
│    70 │   """                                                                                                                                                        │
│    71 │   if choose_apply_mode() == "api":                                                                                                                           │
│ ❱  72 │   │   await_apply_via_api(sel)                                                                                                                              │
│    73 │   else:                                                                                                                                                      │
│    74 │   │   await _apply_in_process(sel, hw, no_pull=no_pull)                                                                                                      │
│    75                                                                                                                                                                │
│                                                                                                                                                                      │
│ /usr/lib/hal0/venv/lib/python3.12/site-packages/hal0/cli/setup_install.py:235 in _apply_via_api                                                                      │
│                                                                                                                                                                      │
│   232 │   payload = dataclasses.asdict(sel)                                                                                                                          │
│   233 │   url = f"{_api_base()}/api/install/apply-selections"                                                                                                        │
│   234 │   async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), headers=_auth_headers()) as client:                                                              │
│ ❱ 235 │   │   resp = await client.post(url, json=payload)                                                                                                            │
│   236 │   if resp.status_code in (401, 403):                                                                                                                         │
│   237 │   │   typer.echo(                                                                                                                                            │
│   238 │   │   │   f"hal0 setup: not authorized ({resp.status_code}) to apply via the live API "                                                                      │
│                                                                                                                                                                      │
│ /usr/lib/hal0/venv/lib/python3.12/site-packages/httpx/_client.py:1859 in post                                                                                        │
│                                                                                                                                                                      │
│   1856 │   │                                                                                                                                                         │
│   1857 │   │   __Parameters__: See `httpx.request`.                                                                                                                  │
│   1858 │   │   """                                                                                                                                                   │
│ ❱ 1859 │   │   return await self.request(                                                                                                                            │
│   1860 │   │   │   "POST",                                                                                                                                           │
│   1861 │   │   │   url,                                                                                                                                              │
│   1862 │   │   │   content=content,                                                                                                                                  │
│                                                                                                                                                                      │
│ /usr/lib/hal0/venv/lib/python3.12/site-packages/httpx/_client.py:1540 in request                                                                                     │
│                                                                                                                                                                      │
│   1537 │   │   │   timeout=timeout,                                                                                                                                  │
│   1538 │   │   │   extensions=extensions,                                                                                                                            │
│   1539 │   │   )                                                                                                                                                     │
│ ❱ 1540 │   │   return await self.send(request, auth=auth, follow_redirects=follow_redirects)                                                                         │
│   1541 │                                                                                                                                                             │
│   1542 │   @asynccontextmanager                                                                                                                                      │
│   1543 │   async def stream(                                                                                                                                         │
│                                                                                                                                                                      │
│ /usr/lib/hal0/venv/lib/python3.12/site-packages/httpx/_client.py:1629 in send                                                                                        │
│                                                                                                                                                                      │
│   1626 │   │                                                                                                                                                         │
│   1627 │   │   auth = self._build_request_auth(request, auth)                                                                                                        │
│   1628 │   │                                                                                                                                                         │
│ ❱ 1629 │   │   response = await self._send_handling_auth(                                                                                                            │
│   1630 │   │   │   request,                                                                                                                                          │
│   1631 │   │   │   auth=auth,                                                                                                                                        │
│   1632 │   │   │   follow_redirects=follow_redirects,                                                                                                                │
│                                                                                                                                                                      │
│ /usr/lib/hal0/venv/lib/python3.12/site-packages/httpx/_client.py:1657 in_send_handling_auth                                                                         │
│                                                                                                                                                                      │
│   1654 │   │   │   request = await auth_flow.__anext__()                                                                                                             │
│   1655 │   │   │                                                                                                                                                     │
│   1656 │   │   │   while True:                                                                                                                                       │
│ ❱ 1657 │   │   │   │   response = await self._send_handling_redirects(                                                                                               │
│   1658 │   │   │   │   │   request,                                                                                                                                  │
│   1659 │   │   │   │   │   follow_redirects=follow_redirects,                                                                                                        │
│   1660 │   │   │   │   │   history=history,                                                                                                                          │
│                                                                                                                                                                      │
│ /usr/lib/hal0/venv/lib/python3.12/site-packages/httpx/_client.py:1694 in_send_handling_redirects                                                                    │
│                                                                                                                                                                      │
│   1691 │   │   │   for hook in self._event_hooks["request"]:                                                                                                         │
│   1692 │   │   │   │   await hook(request)                                                                                                                           │
│   1693 │   │   │                                                                                                                                                     │
│ ❱ 1694 │   │   │   response = await self._send_single_request(request)                                                                                               │
│   1695 │   │   │   try:                                                                                                                                              │
│   1696 │   │   │   │   for hook in self._event_hooks["response"]:                                                                                                    │
│   1697 │   │   │   │   │   await hook(response)                                                                                                                      │
│                                                                                                                                                                      │
│ /usr/lib/hal0/venv/lib/python3.12/site-packages/httpx/_client.py:1730 in _send_single_request                                                                        │
│                                                                                                                                                                      │
│   1727 │   │   │   )                                                                                                                                                 │
│   1728 │   │                                                                                                                                                         │
│   1729 │   │   with request_context(request=request):                                                                                                                │
│ ❱ 1730 │   │   │   response = await transport.handle_async_request(request)                                                                                          │
│   1731 │   │                                                                                                                                                         │
│   1732 │   │   assert isinstance(response.stream, AsyncByteStream)                                                                                                   │
│   1733 │   │   response.request = request                                                                                                                            │
│                                                                                                                                                                      │
│ /usr/lib/hal0/venv/lib/python3.12/site-packages/httpx/_transports/default.py:393 in handle_async_request                                                             │
│                                                                                                                                                                      │
│   390 │   │   │   content=request.stream,                                                                                                                            │
│   391 │   │   │   extensions=request.extensions,                                                                                                                     │
│   392 │   │   )                                                                                                                                                      │
│ ❱ 393 │   │   with map_httpcore_exceptions():                                                                                                                        │
│   394 │   │   │   resp = await self._pool.handle_async_request(req)                                                                                                  │
│   395 │   │                                                                                                                                                          │
│   396 │   │   assert isinstance(resp.stream, typing.AsyncIterable)                                                                                                   │
│                                                                                                                                                                      │
│ /usr/lib/python3.12/contextlib.py:158 in __exit__                                                                                                                    │
│                                                                                                                                                                      │
│   155 │   │   │   │   # tell if we get the same exception back                                                                                                       │
│   156 │   │   │   │   value = typ()                                                                                                                                  │
│   157 │   │   │   try:                                                                                                                                               │
│ ❱ 158 │   │   │   │   self.gen.throw(value)                                                                                                                          │
│   159 │   │   │   except StopIteration as exc:                                                                                                                       │
│   160 │   │   │   │   # Suppress StopIteration *unless* it's the same exception that                                                                                 │
│   161 │   │   │   │   # was passed to throw().  This prevents a StopIteration                                                                                        │
│                                                                                                                                                                      │
│ /usr/lib/hal0/venv/lib/python3.12/site-packages/httpx/_transports/default.py:118 in map_httpcore_exceptions                                                          │
│                                                                                                                                                                      │
│   115 │   │   │   raise                                                                                                                                              │
│   116 │   │                                                                                                                                                          │
│   117 │   │   message = str(exc)                                                                                                                                     │
│ ❱ 118 │   │   raise mapped_exc(message) from exc                                                                                                                     │
│   119                                                                                                                                                                │
│   120                                                                                                                                                                │
│   121 class ResponseStream(SyncByteStream):                                                                                                                          │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
ReadTimeout
!  guided setup exited non-zero — re-run 'hal0 setup' anytime

## Diagnosis

### Verdict

__The installer itself succeeded.__ The failure is in the post-install guided `hal0 setup` wizard (step after the banner).

### Root cause

`ReadTimeout` (30s) on `POST /api/install/apply-selections` to the local hal0 API at `127.0.0.1:8080`.

The call chain:

1. User answers "Y" to "Launch the guided hal0 setup now?"
2. `run_interactive()` → `_run_auto()` → `run_install()` → `choose_apply_mode() == "api"` → `_apply_via_api()`
3. `_apply_via_api()` (setup_install.py:235) POSTs the `Selections` dataclass to the hal0 API with `httpx.Timeout(30.0)`
4. The API server didn't respond within 30 seconds → `ReadTimeout`

The 30-second timeout is too tight for the apply-selections endpoint, which may need to wire capability slots, touch config files, and reconcile ownership — especially on an LXC container sharing host IO.

### Non-fatal warnings (no action needed for install success)

| Issue | Detail |
| ------- | -------- |
| `apparmor-preflight` | `runc` can't exec `true` inside a container — unrelated to hal0, just a probe that logged `wrote=False` |
| Model store on root FS | No dedicated mount — models consume root partition; advisory only |
| Ownership DRIFTs | Ownership table applied 24 paths but ~15 items show `DRIFT` after reconciliation — group-perms like 2775 vs 2755, owner hal0 vs root. The `P3-perms` table is informational; the install says `re-run 'hal0 doctor perms --fix'` |
| Hermes ownership drift | `split-brain /root/.hermes` — root previously ran Hermes. `hal0 agent bootstrap hermes --repair` can clean it |
| Memory bank seeding | `! memory bank seeding incomplete` — lazy-created on first write; harmless |
| Hermes gateway | `hermes gateway install failed` — needs `sudo -u hal0` to install the systemd unit; Telegram/Discord bridge unavailable |

### Hermes provisioning (completed pip install but note)

Hermes pip install succeeded (62 packages, all from cache). The interesting version pin: __fastapi-0.133.1__ was installed in the Hermes venv (resolved from `fastapi<1,>=0.104.0`) while the main hal0 venv may be on a different fastapi version. Not a problem as they are independent venvs.

### Action items

1. __Fix the timeout__: Increase `httpx.Timeout(30.0)` in `_apply_via_api()` (`src/hal0/cli/setup_install.py:234`) to at least 120s, or make it configurable
   - ✅ Applied: `httpx.Timeout(30.0)` → `httpx.Timeout(120.0)` on line 234. Verified module imports cleanly; existing tests in `tests/cli/test_setup_install.py` use a fake client and are unaffected.
2. __Re-run__: `hal0 setup` on the LXC — it should work with a longer timeout
3. __Cleanup__: `sudo hal0 agent bootstrap hermes --repair` to fix Hermes split-brain
4. __Optional__: `sudo /usr/lib/hal0/venv/bin/hal0 doctor perms --fix` to reconcile ownership DRIFTs
5. __Optional__: Re-run hermes gateway install with proper sudo

### Follow-up: public-release timeout tuning (PR #1332, merged)

The 120s ceiling was enough for 150lxc but leaves no headroom for slower public-release hardware. After the alpha runs on more boxes, the timeout was bumped to __300s (5 min)__ as the default, with a `HAL0_SETUP_TIMEOUT_SECS` env var override for operators on known-slow or known-fast hardware.

- __PR__: <https://github.com/Hal0ai/hal0/pull/1332> — merged `b6dc1247`
- __Files__: `src/hal0/cli/setup_install.py`, `tests/cli/test_setup_install.py` (+93 / -1)
- __Verified on 150lxc__: cold-start apply completes in ~110s with 300s budget (vs ReadTimeout at 30s)
- __Tests added__: `test_setup_http_timeout_default_is_300`, `test_setup_http_timeout_env_var_override`, `test_setup_http_timeout_bad_env_falls_back_to_default`, `test_apply_via_api_uses_configured_timeout`
- __Telemetry__: `log.info("hal0 setup apply-selections: %.1fs (status=%s)", ...)` emits wall-clock time so we collect real-world numbers from alpha testers
- __Long-term__: real fix is async-with-job-polling (`202 Accepted` + `job_id`, client polls). Beyond alpha scope — file follow-up issue so it doesn't get lost.
