# ODS install journal (sandbox run, 2026-09-05)

Environment: Claude Code remote sandbox (Ubuntu 24.04, 4 vCPU, 15 GB RAM, ~30 GB writable disk, no GPU,
Docker 29.3.1 daemon started by hand with overlay2, egress only via an HTTPS proxy on the host).
This is NOT the requested privileged LXC with the AI-model directories mounted — that must be done on the
operator's Strix Halo box. Commands to reproduce there are at the end.

## 0. Pre-install decisions forced by the sandbox

| Constraint | ODS behaviour observed | Notes for hal0 |
|---|---|---|
| Installer refuses root (`installers/phases/01-preflight.sh:22`) | Hard error with a one-line reason | hal0's installer is `sudo bash`; ODS is rootless-first and escalates only for "extras" |
| No passwordless sudo for the install user | `ods_prepare_sudo` (installers/lib/sudo.sh) detects it up front, prints "continuing rootless", every later `ods_sudo` call is skipped and logged instead of hanging or failing under `set -e` | Clean pattern: decide privilege posture once, early, then make every privileged step optional and visibly skipped |
| `data/hermes` must be `uid:gid:700` on a rootful runtime or the phase errors (phase 06) | Pre-created it with mode 700 before running | Upstream Hermes image demands 0700 on HERMES_HOME |
| Containers have no direct internet | Image pulls are done by the daemon (which has the proxy), model download is done by the host (`curl`/`huggingface_hub`), so this mostly works | Same split hal0 already has (host downloads, container serves) |

Flags used: `--non-interactive --skip-docker --tier 0 --no-voice --no-workflows --no-rag --no-comfyui --summary-json …`

## 1. Phase-by-phase observations

### Boot / arg parsing (`install.sh` → `installers/dispatch.sh` → `install-core.sh`)
- `install.sh` is a 40-line dispatcher: resolves OS → Linux/macOS/Windows target and `exec`s it.
- `install-core.sh` parses ~40 flags; defaults live in one block (voice/workflows/RAG/recommended/Hermes on; OpenClaw/Langfuse/proxy/Tailscale off) with a comment on every non-obvious default.
- Distro detection prints `Detected distro: ubuntu (like: debian, pkg: apt)`; PyYAML is auto-provisioned for the system python because the service registry reads YAML manifests.

### Phase 1/6 "Pre-flight Checks" (internal phases 01)
- A themed banner + narrator voice: `Signal acquired. I will guide the installation. Stay with me.`; every phase header carries an **estimated time** (`EST. TIME: ~30 seconds`).
- The UI shows **6 user-facing phases** while the code has **13 internal phases** — a deliberate compression of internal structure into a human storyline.
- Checks: OS, curl, jq (auto-installs), optional tools (rsync → prints the exact apt/dnf/pacman command), active UFW/firewalld warning, filesystem POSIX-permission check (exFAT/NTFS/9p refuse `chmod 600 .env`), *related-install detection* (scans `$HOME` for other compose stacks with open-webui + dashboard-api + llama-server/litellm, and running compose projects) so two stacks never fight over ports silently.

### "System detection" (internal phase 02 + 02b)
- Writes a **capability profile JSON** (`/tmp/ods-capabilities.json`: platform, gpu vendor/name/memory_type/count/vram, runtime backend + health URL + port, compose overlays, recommended tier, hardware class). This one file is what later phases and the dashboard read — hardware is probed once and serialised.
- No GPU → `apply_cpu_gpu_fallback` → `GPU_BACKEND=cpu`, backend contract loaded from `config/backends/cpu.json`.
- Prints the **full compose selection** (base + cpu overlay + every enabled extension's compose fragment + `docker-compose.tier0.yml` memory-limit overlay), then prints one line per disabled feature saying *why* it is disabled ("Qdrant compose disabled (RAG not enabled or unsupported on this host)").
- **Catalog model selector output is a sentence a human can act on**: `Qwen 3.5 2B needs about 3GB including context/KV, fits 5.2GB system RAM on cpu, and gives 64K context. Throughput requires a local benchmark after first launch. Bounded by --tier 0's model size ceiling (1221MB); use ODS_DISABLE_CATALOG_MODEL_SELECTOR=true to bypass.`

### "Requirements check" (internal phase 04)
- Delegates to a **fixture-driven preflight engine** that emits `/tmp/ods-preflight-report.json`: `{summary:{checks,blockers,warnings,can_proceed}, checks:[{id,status,message,action}]}`. Each check has an `action` string — the remediation is data, not prose in a log.
- Disk requirement is computed from the *selected model size + 15 GB images*, not a static table.

### "Directories / config" (internal phase 06)
- Every sub-step is **named and logged** (`Phase 06 step: prune-retired-services`, `copy-extensions-library`, `configure-legacy-openclaw`, `prepare-service-permissions`, `generate-env`, `render-model-router-config`, `validate-env`, `generate-searxng-config`). A failure names the step, not just the phase.
- Docker rootless state is detected once (`lib/rootless-ownership.sh`) and drives every ownership decision; `ODS_UID/ODS_GID` are persisted into `.env` so re-runs and containers agree on who owns `data/`.
- A hard-coded `chown … 1000:1000` for token-spy failed under uid 1001 and was reported as **non-fatal with the consequence spelled out** ("container may crash if installer ran as a different uid").
- `.env` is generated with secrets (`chmod 600`), then **validated against `.env.schema.json`** before anything starts. The extension *library* (optional catalog) is copied into `data/extensions-library/` so the dashboard can browse it offline.

### "Dev tools" (internal phase 07)
- Installs Claude Code, Codex CLI and OpenCode into `~/.npm-global` (no sudo needed); OpenCode failed and the installer printed the exact one-liner to retry later. Non-fatal by design.
- Starts the **ODS host agent** (`bin/ods-host-agent.py`): a host-side helper for model downloads and mDNS. No systemd in the sandbox → "starting the host agent without a system service … Run `ods agent start` after reboot". Graceful degradation, with the recovery command printed.
- Installs `zeroconf` via `pip --user` when the distro package cannot be installed: the **mDNS announcer is a Python script (`bin/ods-mdns.py`)**, not an avahi service file. It publishes `<device>.local` plus `chat/dashboard/auth/api/hermes/talk.<device>.local` A-records and re-reads `.env` every 30 s so renames need no restart. A static AST test (`tests/test_mdns_subdomains.py`) pins the subdomain set to the Caddyfile routes and the magic-link redirect targets.

### Phase 4/6 "Downloading Modules" (internal phase 08)
- **Validates that each pinned image tag exists in the registry before pulling** (llama-server, Hermes), so a bad pin fails in seconds, not after a 4 GB download.
- Pull order is *largest first*, with a spinner, elapsed time, `[n/N]` counter and a human label per image ("LLAMA-SERVER — downloading the brain (CPU)", "HERMES PROXY — magic-link auth gate (Caddy)"). Rotating one-line taglines fill the wait. Images pulled: llama.cpp server-b8248 (113 MB), open-webui v0.7.2 (4.39 GB), perplexica, hermes-agent v2026.6.5 (3.13 GB), caddy 2.11.3-alpine (63 MB).

### Phase 5/6 "Starting Services" (internal phase 11)
- Compose flags are recomputed from the *install directory* and saved to `.compose-flags` (the CLI reuses them; no re-detection at every `ods start`).
- GGUF download (host-side, `huggingface_hub`), then **SHA-256 verification** against the pinned digest in `installers/lib/bootstrap-model.sh`.
- Generates `models.ini` for llama-server, then **patches the Hermes template with the actually-served model id** (`model.default=Qwen3.5-2B-Q4_K_M.gguf, context=65536`) and verifies the substitution landed.
- Validates service dependencies (manifest `depends_on`) and runs `docker compose config` before `up` — a broken fragment fails before any container starts.
- Local builds are skipped per disabled service ("Skipping local image build for disabled service: brave-search").

### Run 1 outcome: local image builds failed, install aborted (by design)
- Every locally built service (`dashboard`, `dashboard-api`, `model-router`, `ape`, `privacy-shield`, `token-spy`, …) failed with `certificate verify failed` inside BuildKit: the sandbox re-terminates outbound TLS and build containers do not trust its CA. Not an ODS defect.
- ODS behaviour worth copying: each build gets **three attempts with a 5 s delay**, a per-service build log (`/tmp/ods-install.log.<svc>.build.log`), a cross-check that the build actually produced the tagged image, and then a **refusal to start an image left by an earlier install** ("Fix the build error and rerun the installer"). It never silently ran stale code.
- Sandbox fix: the sandbox CA was appended to the eight locally built Dockerfiles in the install user's private copy only.

### A real ODS bug found while re-running: the host agent inherits the installer's lock
- Run 2 blocked at *"Waiting for another model lifecycle operation before Linux installer model configuration…"*.
- Cause: in the no-systemd fallback, phase 07 starts `bin/ods-host-agent.py` as a background child **after** the installer has taken its model-lifecycle `flock`. The child inherits the descriptor (`/proc/<agent-pid>/fd/10 → /tmp/ods-model-lifecycle-<uid>/ods-model-lifecycle-<cksum>.lock`) and keeps the lock for its whole lifetime, so any later installer run or `ods model swap` waits forever. ODS closes inherited FDs for the bootstrap upgrader (`_phase11_close_inherited_fds_for_daemon`) but not for the host agent. Under systemd the unit starts fresh and nobody notices. Killing the agent released the lock; run 2 then started its own agent, which inherited the lock again.
- Lesson for hal0's planned `systemd-run` background pulls: never let a daemon inherit a lock descriptor; take locks inside the daemon.

### Run 2 outcome: stack up in 4 min 44 s (images and model already cached)
| Phase | Wall clock | Notes |
|---|---|---|
| Run 1 start → killed at builds | 07:25:36 → 07:33:59 (8 m 23 s) | ~7.7 GB of images pulled in ~3 min; 1.2 GB model downloaded and SHA-verified |
| Run 2 start → "YOUR ODS IS LIVE" | 07:36:23 → 07:41:07 (4 m 44 s) | includes 6 local image builds; ~1 min was the inherited-lock wait |
| Containers healthy at the end of run 2 | 13 of 15 | `ods-dashboard` and `ods-searxng` restart-looping |

- **Readiness summary**: `Ready now: 5/6` with a per-URL table, a "Needs attention" block, and a "Next:" block naming the exact compose logs command. This is the shape hal0's install summary should have.
- **`--summary-json`** wrote a versioned payload: installer version, tier id and name, `runtime.{gpu_backend, backend_service, llm_model, compose_flags, dry_run}`, hardware class, preflight report path.
- **Extension manifest validation** ran against the installed ODS version: 27 manifests, 0 incompatible, then an **extension runtime check** probing each enabled non-core service's health URL from the manifest.
- The generated `.env` has ~150 keys; `.compose-flags` caches the resolved stack; `config/llama-server/models.ini` is four lines; `data/` has one directory per service.

### Two IPv6 findings (matter for Proxmox LXCs with IPv6 disabled)
- `ods-dashboard`: nginx config has `listen [::]:3001;` unconditionally ("IPv6 support for Docker healthcheck"); on a kernel without IPv6, nginx fails with `socket() [::]:3001 failed (97: Address family not supported by protocol)` and the container crash-loops. Sandbox fix: comment out the IPv6 listen line in the image.
- `ods-searxng`: the upstream image defaults `GRANIAN_HOST=::`; same failure. Sandbox fix: `GRANIAN_HOST=0.0.0.0` in the service env.
- Both are one-line hardenings ODS could ship (`listen [::]:3001 ipv6only=on;` guarded, or IPv4 default plus an env toggle). hal0 already binds IPv4 explicitly in its unit templates; keep it that way.

### Live surfaces after the fixes
- `llama-server /v1/models` → `Qwen3.5-2B-Q4_K_M.gguf`; a direct chat completion answers in ~20 tokens on 4 vCPU.
- LiteLLM exposes only the aliases `default` and `*`; a completion to `ods/current` is answered by the router and reports the real model id in the response.
- `hermes-proxy /` → `303 → /auth/required` ("Owner card required") with no cookie: the forward_auth gate is real.
- `dashboard-api /api/status` returns per-service `status/state/severity/countsAsIssue` plus the manifest's `llm` contract (Hermes: `route: gateway`, `min_context: 65536`, `swap_safe: true`, with a one-sentence `swap_safe_reason`).
- `/api/features` → 4 feature cards (`hermes-agent: services_needed`, `hermes-sso: services_needed`, `coding: insufficient_vram`, `agent-governance: enabled`) and two plain-English recommendations ("Limited GPU memory — chat will work with small models", "Consider cloud hybrid mode for better quality"). `/api/features/hermes-agent/enable` returns numbered steps plus an "Open Hermes" link.
- `/api/extensions/catalog` → 33 catalog entries; 24 `incompatible` on the CPU backend, 9 `not_installed`. `/api/external-links` lists 7 sidebar quicklinks derived from manifests.
- `ods list` prints 27 services with category (core/recommended/optional) and state (always-on/enabled/disabled); `ods status` prints the container table, then per-service health checks.
- The rendered persona (`data/persona/SOUL.md`) opens with **"About this ODS install — read this BEFORE answering questions about your environment"**: host, backend, serving model and context window, the dashboard URL, the running-services list, and a "you can call these / you can only point the operator at these" split. Two inaccuracies in the generated block: it advertises `http://vm.local` and `chat.vm.local` although `ods-proxy` is disabled in this install, and it expands APE as "agentic prompt engineering surface" (it is the Agent Policy Engine). A generated context is only as good as its description table.

## 2. What hal0 should take from the install experience (short list)
1. One capability-profile JSON written once per run, read by everything after it.
2. Preflight and doctor verdicts as JSON rows with an `action` string; fixture-tested.
3. A tee'd install log plus a redacted failure report written from the ERR trap.
4. Per-step time estimates, named sub-steps, and one line per disabled feature saying why.
5. Validate image tags before pulling; pull largest first; retry with backoff; validate after pull.
6. A readiness summary with Ready / Needs attention / Next, and `--summary-json`.
7. Bind IPv4 explicitly (hal0 already does); never inherit a lock into a daemon (hal0 must, when it adds background pulls).
