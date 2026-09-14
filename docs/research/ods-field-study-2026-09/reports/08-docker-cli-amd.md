# ODS vs hal0 — Container Runtime, Config Contracts, CLI, and AMD/Strix Halo Tuning

Scope: container runtime (Docker/Podman), compose/config contracts, the operator
CLI, and AMD Ryzen AI Max ("Strix Halo") host tuning. All citations are
`file:line` against the two repos on disk: ODS at `/home/user/ods/ods/` and
hal0 at `/home/user/hal0/`.

## A. ODS mechanism

### A.1 Compose layering & contracts

ODS assembles its runtime from Docker Compose file merging, documented as a
formal contract rather than convention. `docker-compose.base.yml` (475 lines)
defines the always-on core: `llama-server` as a "GPU-agnostic stub"
(`docker-compose.base.yml:24-25`, image defaults to
`ghcr.io/ggml-org/llama.cpp:server-b9014`), `open-webui`
(`:82-186`, pinned `ghcr.io/open-webui/open-webui:v0.7.2`), `dashboard-api`,
`model-router`, two remote-provider-egress services, and `dashboard`. Every
service in the base file carries a `deploy.resources.limits/reservations`
block (e.g. `:173-180`, `:281-288`), a `healthcheck` (7 of them in the base
file), `restart: unless-stopped` (24 occurrences across base + extensions),
and `security_opt: [no-new-privileges:true]`. `dashboard` explicitly gates on
`dashboard-api`'s health (`depends_on.dashboard-api.condition:
service_healthy`, `:463-465`).

Hardware overlays (`docker-compose.{nvidia,amd,apple,arc,intel,cpu}.yml`) only
override `llama-server`, `dashboard-api`, and `open-webui` — never redefine
the whole stack. `docker-compose.amd.yml` rebuilds `llama-server` from a
`Dockerfile.amd` into image `ods-lemonade-server:latest` (`:6-17`), wraps it
with a custom entrypoint that syncs `LEMONADE_CTX_SIZE` into Lemonade's
cached `config.json` on every start (`:18-24`, because Lemonade "only reads
the env on first-start"), and passes `/dev/dri` + `/dev/kfd` plus
`VIDEO_GID`/`RENDER_GID` group_add (`:48-53`). Mode overlays
(`docker-compose.{cloud,external-llm,lemonade-external,tier0}.yml`) use
Compose `profiles: [local-inference]` plus `restart: "no"` to stub out
`llama-server` without deleting the service reference other files'
healthchecks depend on (`docker-compose.cloud.yml:8-16`) — the "dependency
placeholder" pattern `docs/COMPOSE_RESOLVER_CONTRACTS.md:75-87` calls out as
first-class. Multi-GPU overlays (`docker-compose.multigpu-{amd,nvidia}.yml`)
layer again on top for `ROCR_VISIBLE_DEVICES`/tensor-split flags.

`docs/BACKEND-CONTRACT.md:1-39` and `config/backends/{amd,nvidia,cpu,apple}.json`
define one JSON contract per backend (LLM engine, public port/health URL,
provider name/URL — e.g. `config/backends/amd.json:1-18` pins
`ghcr.io/lemonade-sdk/lemonade-server:v10.2.0` and records `linux_backend:
rocm` vs `windows_backend: vulkan`), loaded by
`scripts/load-backend-contract.sh` and consumed in
`installers/lib/detection.sh`. `config/ports.json` is the single canonical
port map (`env_var`, `external_default`, `internal_port` per service).

Config-contract enforcement: `.env.example` (592 lines) documents every
variable; `.env.schema.json` (1522 lines, JSON Schema draft 2020-12) requires
six secrets (`WEBUI_SECRET`, `SEARXNG_SECRET`, `N8N_USER`, `N8N_PASS`,
`LITELLM_KEY`, `OPENCLAW_TOKEN`) and types/enums/ranges for the rest.
`scripts/validate-env.sh` (611 lines) is a from-scratch Bash `.env` parser
(never sources the file, "for security reasons",
`scripts/validate-env.sh:90-92`) that checks required keys, unknown keys,
type/enum/range/minLength, and hand-written cross-field "runtime contracts" —
e.g. remote-LLM transport requires HTTPS and rejects loopback/link-local hosts
(`:450-461`), `EMBEDDING_MODEL` must be a HF TEI repo id not a GGUF
(`:475-485`). `.github/workflows/validate-env.yml` runs this as a 5-way matrix
(tiers 0-4), each generating a synthetic `.env` via a stubbed phase-06
environment before validating (`/home/user/ods/.github/workflows/validate-env.yml:13-118`).

`lib/safe-env.sh` is the shared safe loader (`load_env_file`,
`load_env_from_output`, `load_env_from_output_allowlist`) — explicitly "no
eval, no word-splitting" (`lib/safe-env.sh:1-9`), and it special-cases `UID`
because Bash's own readonly `UID` would otherwise abort every lifecycle
command under `set -e` (`:33-36`). `lib/dotenv-quote.sh:1-25` serializes a
value into the one-line `.env` grammar shared by Bash/Compose. `lib/rootless-
ownership.sh` (250+ lines) detects Docker-rootless vs Podman-rootless via two
different `docker info` fields (`.SecurityOptions` vs
`.Host.Security.Rootless`, `:8-29`) and repairs per-service bind-mount
ownership with a disposable `busybox:1.36.1` helper container rather than
chowning from the host (`:4`, `:130-150`). `ODS_UID`/`ODS_GID`
(`.env.example:487-491`) are read by three extension composes directly
(`extensions/services/hermes/compose.yaml:18-19`, `n8n/compose.yaml:6`,
`privacy-shield/compose.yaml:10`) and by that repair script's per-service
UID/GID table (`lib/rootless-ownership.sh:220-260`).

Image tag pinning is a deliberate, written trade-off: `docs/ADR-IMAGE-TAG-
PINNING.md` accepts (2026-03-04) that OpenClaw, SearXNG, and Whisper stay on
`:latest` because SearXNG has no stable release channel and pinning risks
silently missing patches on an optional, already-hardened service — but
`llama-server`, `open-webui`, and the AMD build args are all pinned to
specific tags. `docs/KNOWN-GOOD-VERSIONS.md` records tested Docker/Compose/
driver baselines per platform. `docs/DOCKER-DESKTOP-OPTIMIZATION.md` is a
Windows/WSL2-specific tuning guide (CPU/memory sliders, disk-image
relocation to NVMe) — there is no equivalent for native Linux or AMD.

### A.2 Podman

ODS does not run on Podman as a first-class, documented target — there is no
Podman row in `docs/PLATFORM-TRUTH-TABLE.md` or `docs/SUPPORT-MATRIX.md`.
What exists is defensive handling for the case where the `docker` binary
itself **is** the Podman Docker-compatibility shim, called out as "common on
AMD Ryzen AI / Fedora / RHEL hosts" (`installers/phases/05-docker.sh:390-401`).
`_runtime_is_podman()` greps `docker version` for "podman" (`:398-401`); when
true, `installers/lib/podman-registries.sh:4-234` rewrites a user-level
`~/.config/containers/registries.conf.d/99-ods-dockerhub.conf` so bare image
names resolve against Docker Hub, "because podman has no implicit `docker.io`
like Docker does" (`:390-393`). This is careful, tested code (idempotent TOML
editing in Python, `tests/test-podman-rootless-contracts.sh`) but a
compatibility patch, not a native path: ODS still emits and resolves Docker
Compose YAML, never `podman-compose` or Podman Quadlets. Rootless is handled
generically for whichever runtime answers "rootless": true"
(`lib/rootless-ownership.sh:8-29`), and Docker Desktop is its own branch
(`docs/DOCKER-DESKTOP-OPTIMIZATION.md`). Net: ODS treats "docker" as the
contract and tolerates Podman when it wears a Docker mask; it is not
validated against native Podman networking, Quadlets, or `podman machine`.

### A.3 AMD / Strix Halo host tuning

`installers/phases/10-amd-tuning.sh` (304 lines) is the concrete, host-level
tuning ODS applies only when `GPU_BACKEND=amd`:

- **Group membership & device presence** — adds the installing user to
  `render`/`video` (`:34-38`), modprobes `amdkfd` if `/dev/kfd` is missing
  (`:41-51`), and verifies `/dev/dri/renderD128` exists (`:53-61`).
- **sysctl** — installs `config/system-tuning/99-ods.conf`
  (`vm.swappiness=10`, `vm.vfs_cache_pressure=50`) to `/etc/sysctl.d/`
  (`:102-111`).
- **amdgpu modprobe options** — installs `config/system-tuning/amdgpu.conf`
  (`options amdgpu ppfeaturemask=0xffffffff` and `gpu_recovery=1`)
  (`:113-121`).
- **GTT/TTM sizing, scaled by RAM** — computes a GTT percentage of total RAM
  (90% at ≥96 GB, 80% at ≥64 GB, else 65%; `:140-152`) and writes
  `/etc/modprobe.d/amdgpu_llm_optimized.conf` with
  `options amdgpu gttsize=<MB>`, `options ttm pages_limit=<pages>`, `options
  ttm page_pool_size=<pages/2>` (`:163-170`), then rebuilds initramfs
  (`:172-174`). A 128 GB Strix Halo box lands at ~115 GB GTT.
- **GRUB kernel cmdline** — single-GPU APUs get `amd_iommu=off` appended for
  "~6% memory bandwidth improvement" (`:186-229`); multi-GPU boxes instead
  require `iommu=pt` for device passthrough and are only warned, not
  auto-edited (`:192-201`).
- **tuned profile** — installs `tuned` if absent and sets
  `accelerator-performance` for "5-8% pp improvement" (`:245-284`).
- A BIOS banner recommends setting UMA Frame Buffer Size to 512 MB minimum
  (`:123-136`), and a reboot-required banner fires whenever GRUB or modprobe
  files changed (`:289-302`).

`installers/lib/amd-topo.sh` provides vendor-neutral multi-GPU topology
detection (`amd_gpu_id`, `amd_gfx_version`, `detect_amd_topo`), with a
three-tier fallback chain per fact (`rocm-smi` → `amd-smi` → sysfs
`ip_discovery` → `rocminfo`). `docs/LEMONADE-SDK-COMPAT.md` documents a
managed-vs-external Lemonade split (own container vs. wrap a pre-existing
host Lemonade via `--use-existing-lemonade`, with `host.docker.internal`
networking notes and a `runtimeMode`/`managedByODS` diagnostic surface).
`docs/AMD-ODS2-GAMMA-BRIEF.md` is a product brief for a Strix-Halo
"drop-ship appliance" flow — engineering-adjacent, not a runtime mechanism.
The Docker 29.x bug workaround (`installers/phases/05-docker.sh:194-244`):
Docker 29.3.x regresses AMD `/dev/dri` custom-device passthrough; ODS
detects the server version and, on AMD, auto-downgrades to 29.2.1 via the OS
package manager, restarting the daemon afterward (`:240-243`).

**hal0 has none of this tuning.** A repo-wide grep for `gttsize`,
`ttm.pages_limit`/`page_pool_size`, `amd_iommu`, `GRUB_CMDLINE`, `tuned-adm`/
`accelerator-performance`, and `vm.swappiness` across `installer/`,
`packaging/`, `src/hal0/hardware`, and `src/hal0/install` returns zero
matches. What hal0 does have is device-presence *diagnosis*, not host
*tuning*: `installer/lib/preflight.sh` (~950-1050) runs rich `/dev/dri` /
`/dev/kfd` / `/dev/accel` gid-mapping checks with named error codes
(`HAL0_GPU_RC_NO_KFD`, `HAL0_GPU_RC_KFD_GID`, etc., tailored to LXC device
passthrough where a render node's gid can map to an unrelated host group like
`clock`), and `src/hal0/install/gpu_perms.py:1-56` + 
`installer/systemd/hal0-gpu-perms.service:1-29` re-converge `/dev/kfd`'s
group **once per boot** (since udev recreates the node fresh every boot) —
which is a materially more robust mechanism than ODS's one-shot,
install-time-only `usermod -aG render,video` (`10-amd-tuning.sh:34-38`, which
also requires a fresh login shell to take effect and is never re-checked
after that). `src/hal0/providers/_gpu.py:70-71` hard-codes a last-resort GID
fallback table explicitly labelled "Strix Halo LXC values" (`render: 993,
video: 44`). `HSA_OVERRIDE_GFX_VERSION` exists in hal0 only as a manual,
per-slot `[server].env` escape hatch documented in
`src/hal0/config/schema.py:298-306` — nothing auto-detects gfx1151 and sets
`11.5.1` the way ODS's phase 06 does (referenced from `.env.example:477`).
`ROCBLAS_USE_HIPBLASLT`, sysctl swappiness, GTT/TTM sizing, GRUB
`amd_iommu=off`, and `tuned-adm accelerator-performance` are entirely absent.
This gap is corroborated by hal0's own upstream image source:
`/home/user/amd-strix-halo-toolboxes/README.md:179-198` (the fork hal0's ROCm
runner images are built from) explicitly documents
`amd_iommu=off amdgpu.gttsize=126976 ttm.pages_limit=32505856` as required
GRUB parameters for Strix Halo, citing a 5-12% performance delta — almost the
exact tuning ODS automates — yet nothing in hal0's own installer applies it.

### A.4 CLI

`ods-cli` is a single Bash file, 6,595 lines / 269,480 bytes
(`ods-cli:1-6595`, confirmed via `wc -l`/`ls -la`), dispatched from one
`case "${1:-help}"` at the bottom (`ods-cli:6562-6595`). The verb tree (with
aliases) is:

```
gpu|g          status topology assignment validate reassign
status|s       (table); status-json (separate top-level cmd, not `status --json`)
list|ls        enable/disable-aware service listing
enable/disable/purge   extension lifecycle
preset|p       save|load|list|delete|export|import  (.env + extension state + meta.txt)
mode|m         local|cloud|hybrid  (rewrites LLM_API_URL/ODS_MODE)
model          current|list|swap
remote-provider  list|status|plan
stt            current|status|download
backup/restore/rollback
logs|log|l <service> [lines]   (hardcodes `-f`/follow, no way to opt out)
restart|r / start / stop       (special-cases "hermes" and "llama-server" inline)
update|u, shell|sh, config|cfg (show|edit|validate), chat|c, benchmark|bench|b
doctor|diag|d [--json]
audit, template|tmpl, agent (controls the ODS **host agent** daemon, not Hermes)
```

32 `cmd_*` functions total (grep count). `cmd_config show`
(`ods-cli:2260-2317`) masks secrets by schema lookup before printing `.env`,
and `cmd_config validate` shells out to the same `scripts/validate-env.sh`
used in CI (`:2289-2312`). `cmd_status_json` (`:1402-1517`) builds one JSON
document via `jq -n`/`jq -s` and per-service HTTP health probes — but it is
its own top-level command (`ods status-json`), not `ods status --json`;
`ods doctor --json` uses the opposite convention
(`completions/ods-cli.bash:44-46`), so JSON output is inconsistent across the
CLI's own surface. `cmd_preset` snapshots `.env` + per-extension state + a
`meta.txt` into `presets/<name>/` and validates compatibility before `load`.
Notably, "hermes" is **not** a subcommand tree — it is a magic string
special-cased inside the generic `cmd_restart`/`cmd_stop` bodies
(`ods-cli:1583`, `:1787`); `cmd_agent` (`:4756-4900+`) is unrelated — it
controls `ods-host-agent.py`, a local Wi-Fi/setup helper daemon.

ODS's own `docs/ODS_CLI_DECOMPOSITION.md` acknowledges the monolith as a
liability — "the file is large enough that future changes need a staged
decomposition plan" (`:1-8`) — with a six-stage extraction order (read-only
helpers → status/logs → diagnostics → model/mode → extensions →
lifecycle/backup last, `:26-92`). The plan is written but unexecuted; `ods-cli`
remains one file. `docs/MODE-SWITCH.md` documents the one-env-var
(`LLM_API_URL`) mode design cleanly. `docs/PROFILES.md` is misleadingly named:
it documents that Docker Compose *profiles* (not CLI presets) were removed —
"removed in favor of the current all-core architecture for simplicity"
(`:39-41`) — so all 16 core services always start today, and disabling one
means hand-editing a `docker-compose.override.yml`. `completions/ods-cli.bash`
is a single static Bash completion file mirroring the verb/alias list by hand.

### A.5 Runtime images

ODS builds `llama-server` per backend: NVIDIA and CPU pull upstream
`ghcr.io/ggml-org/llama.cpp` build tags directly
(`docker-compose.nvidia.yml:8`, `docker-compose.cpu.yml:8`); AMD builds a
custom image (`ods-lemonade-server:latest`) from `Dockerfile.amd` with build
args `AMDGPU_TARGET`/`LLAMA_CPP_REF`/`LEMONADE_SERVER_IMAGE`
(`docker-compose.amd.yml:10-17`); Intel Arc builds SYCL from source
(`docker-compose.arc.yml:29-33`). None of these are digest-pinned — every
image reference in every compose file is a tag (`:server-b9014`, `:v0.7.2`,
`:v10.2.0`), never `@sha256:...`.

## B. hal0 today (verified)

hal0 has no Docker Compose file anywhere in the repository (confirmed:
`find /home/user/hal0 -maxdepth 1 -iname "docker-compose*"` is empty, and
`packaging/` holds only `systemd/toolbox/runner/proxmox/sudoers`). The entire
runtime is Podman, orchestrated by systemd, one container per slot under
`hal0-slot@<name>.service` (`ARCHITECTURE.md:27-51`). `ContainerProvider`
(`src/hal0/providers/container.py:1-40`) is "the sole slot-lifecycle
backend" for every provider type — GPU llama-server, FLM NPU, Kokoro/Qwen3-TTS,
and ComfyUI all render through one function,
`_render_quadlet_from_plan` (`:803-990+`), which emits a Podman **Quadlet**
`.container` unit rather than a hand-built `podman run` string: typed
`Image=`/`AddDevice=`/`Volume=`/`PublishPort=`/`Environment=` keys, with
`GroupAdd=`/`SecurityOpt=` deliberately routed through `PodmanArgs=` instead
of native Quadlet keys because "4.x generators HARD-FAIL the conversion on
those keys" on real hardware (`:899-909`, citing live evidence from two named
test boxes, halo150/halo143). Restart policy is systemd-native and far more
expressive than Compose's `restart: unless-stopped`: `Restart=always`,
`RestartSec=5`, `RestartSteps=6`, `RestartMaxDelaySec=300` (a 5s→10→20→39→
77→152→300s geometric ramp), capped by `StartLimitIntervalSec=1800`/
`StartLimitBurst=10` at the `[Unit]` level (`:854-891`) — and the code
comments record a real regression (#1424) from an earlier, too-tight
300s/5-burst pairing that "LATCHED the unit in failed" for hours. There is,
however, **no resource ceiling**: a repo-wide grep for `MemoryMax=`,
`CPUQuota=`, or any memory/CPU limit key in `container.py`, `base.py`, or
`config/schema.py` returns nothing — every slot container runs unconstrained
on host resources, unlike every ODS Compose service's `deploy.resources.limits`.

Podman images are always fully-qualified (`ghcr.io/hal0ai/...`), so hal0 never
hits the "podman has no implicit docker.io" short-name problem ODS works
around in `podman-registries.sh`. Every runner image is pinned by **both**
tag and sha256 digest in `manifest.json`'s `toolbox_images` block, each with
a dated provenance note (e.g. the `cpu` entry records the exact
`llama-server --version` output used to verify the pinned digest).
`/home/user/hal0-runner-images` is the dedicated aggregator repo: "owned"
Dockerfiles (cpu/flm/kokoro/moonshine/comfyui) plus "referenced" images
pinned by digest but built upstream (`vulkan`/`rocm` →
`Hal0ai/amd-strix-halo-toolboxes`, a hal0-maintained fork of
`kyuz0/amd-strix-halo-toolboxes` adding `*-server` entrypoint variants for
systemd) — CI resolves published ghcr digests and opens an automatic
manifest-bump PR against hal0 (`hal0-runner-images/README.md:8-21`). The
OpenWebUI companion is likewise a hand-rendered, digest-pinned systemd/Podman
unit (`packaging/systemd/hal0-openwebui.service:34,68`, same `Restart=always`
backoff pattern, `ExecStartPre=-podman pull ...@sha256:...`).

Config validation runs through Pydantic v2 models
(`src/hal0/config/schema.py:1-20`): "All TOML files under /etc/hal0/ are
validated against these models at startup. Typos like `backend = "vukan"`
raise a ValidationError with the field path." This is a stronger mechanism
than ODS's bash/jq validator (native typed enums/unions, validated on every
process start rather than only when an operator remembers to run `ods config
validate`), though hal0 has no equivalent of ODS's CI matrix that
materializes and validates a synthetic config per hardware tier.

hal0's CLI (`src/hal0/cli/`) is Typer-based and modular: 35 files, 18,245
lines total, the largest single file (`doctor_commands.py`, 2,272 lines)
still well under half of `ods-cli`'s size. `main.py:61-127` builds one root
Typer `app` and mounts ~17 sub-apps via `app.add_typer(...)` — `slot`,
`model`, `memory`, `config`, `doctor`, `upstream`, `capabilities`, `comfyui`,
`agent`, `app`, `migrate`, `registry`, `runner-images`, `profile`, `mcp`,
`auth`, `board` — plus flat commands (`bench`, `chat`, `system-info`,
`ports`, `status`, `update`, `serve`, `uninstall`). Every mount point carries
an inline comment naming the GitHub issue that motivated it (e.g. `:88-91`
for `runner-images`), and a deprecated command (`probe`, `:234-244`) is kept
as a hidden, redirecting shim rather than silently removed. The CLI calls the
same `/api/*` HTTP surface the dashboard uses (`status()` at `:198-230` hits
`/api/status`, `/api/slots`, `/api/upstreams`) rather than shelling out to
`systemctl`/`podman` directly — one behavior surface for both consumers.

Two concrete, source-confirmed CLI defects match the owner's characterization:

- **Long-blocking lifecycle verbs with no progress feedback.**
  `src/hal0/slot_lifecycle_budget.py:1-192` (written in response to #1832, a
  prior timeout mismatch) derives client-side HTTP timeouts from the
  server's own worst case: `HEALTH_TIMEOUT_S=180`, `TERMINATE_TIMEOUT_S=30`,
  `EVICTION_UNLOAD_ALLOWANCE=3`, plus an output-sanity gate up to 360s on a
  CPU-backed slot (`:29-136`). The derived timeouts — `SLOT_LOAD_TIMEOUT_S` ≈
  1587s, `SLOT_UNLOAD_TIMEOUT_S` ≈ 828s, `SLOT_LIFECYCLE_TIMEOUT_S`
  (restart/swap) ≈ 2415s, via `slot_lifecycle_timeout_s()` (`:165-192`) — are
  consumed directly by `slot_load`/`slot_unload`/`slot_restart` in
  `src/hal0/cli/slot_commands.py:311-362`, each **one blocking `api_post(...)`
  call**: no spinner, no incremental state (the server does transition
  `starting`→`warming`→`ready` internally per `ARCHITECTURE.md:231-266`, but
  the CLI never polls or streams it) — just the final state once the HTTP
  response returns or the timeout fires. The module itself flags the
  remaining gap as open: "bounding it server-side is the real fix (#1869)"
  (`:107`). This is the mechanism behind a lifecycle verb sitting silent for
  minutes; the owner-cited 966s figure falls inside this documented envelope.
- **`hal0 update` does not refresh `/usr/local/bin/hal0` (#1844).**
  `installer/install.sh:1074-1099` creates the PATH symlink exactly once, at
  install time. The self-update path (`src/hal0/updater/updater.py`,
  `activate_release()`) atomically swaps `/usr/lib/hal0/current` and
  `pip install --force-reinstall`s into the **same** venv
  (`_reinstall_into_venv`, `:2338-2341`: "apply() swaps the current symlink
  but the venv imports hal0 from its own site-packages... until the code is
  reinstalled") — but nothing re-runs the `ln -sfn` step, so a box whose
  wrapper predates a venv/path change keeps resolving a stale binary. This
  caused a real, separately-numbered regression (#2092, fixed) where `hal0
  agent reprovision`'s privilege-drop re-exec resolved via
  `shutil.which("hal0")` and silently re-exec'd an old release as root
  (`src/hal0/cli/agent_commands.py:610-625`). CHANGELOG documents the
  operator-facing symptom and workaround across three releases: "check with
  `hal0 --version` and re-run the installer if it disagrees"
  (`CHANGELOG.md:2299-2306`, echoed at `:1085-1088`, `:2397-2399`).

hal0 has no WSL2 path at all — `WSL` appears only in host-classification code
(`src/hal0/hardware/probe.py:644,664`) used to label a detected Microsoft
kernel, never in an install flow or doc.

## C. Better / worse / equivalent

**ODS is clearly better at:** (1) documented AMD/Strix Halo *host tuning* —
sysctl, GTT/TTM sizing, GRUB cmdline, tuned profile — all automated,
RAM-scaled, and independently corroborated by the same upstream toolbox
project hal0 forks from; (2) breadth of documented, tested platform
combinations (NVIDIA, AMD, Apple, Intel Arc, CPU, WSL2, Docker Desktop)
against a named compatibility contract; (3) a written, CI-enforced
env-schema contract with cross-service checks run as a 5-tier matrix on
every PR; (4) a real Docker-Engine-version regression workaround (29.3.x
`/dev/dri`) reflecting production field experience.

**hal0 is clearly better at:** (1) the runtime mechanism itself — Podman
Quadlets + native systemd restart/backoff/rate-limiting is a cleaner, more
debuggable primitive than Compose's coarser `restart: unless-stopped`, and
needs no daemon; (2) boot-persistent GPU device-node convergence
(`hal0-gpu-perms.service`) versus ODS's one-shot, login-shell-dependent
`usermod`; (3) supply-chain pinning — **every** hal0 runner/companion image
is digest-pinned with provenance notes, whereas ODS pins by tag only and has
a written ADR *accepting* `:latest` for three services; (4) config
validation as a load-time, typed contract (Pydantic) rather than an
optional, separately-invoked script; (5) CLI architecture — a modular,
issue-annotated Typer tree is a healthier long-term surface than one 270 KB
Bash file, even though `ods-cli` is more feature-complete in places (masked
`config show`, preset export/import).

**Roughly equivalent:** rootless-ownership handling (ODS's busybox-helper
chown dance vs. hal0's boot-converge service — both exist because container
runtimes and host device nodes disagree on ownership); mode/provider routing
(`ods mode {local,cloud,hybrid}` vs. hal0's upstream/dispatcher model).

Podman support specifically is **not equivalent**: ODS runs on Podman only
incidentally and never documents it as supported; hal0 is Podman-native by
design. ODS's edge is specifically the *tuning and platform-breadth* axes
named above — its actual container-runtime mechanism (Compose + a real
Docker Engine dependency, needing its own version-regression workaround) is
the less robust of the two once Podman/rootless edge cases are in play.

## D. Port candidates

1. **AMD/Strix Halo host tuning phase** (highest value, most direct port).
   Source: `installers/phases/10-amd-tuning.sh` (304 lines, logic reusable
   near-verbatim, e.g. the GTT sizing at `:146-157` — `gtt_pct` 90/80/65 by
   RAM tier, `pages_limit = gtt_size_MB*1024*1024/4096`,
   `page_pool_size = pages_limit/2`). Target: a new `installer/lib/amd-
   tuning.sh` sourced once from `installer/install.sh` (hal0's installer is
   one 4,002-line script with no phase directory, so this is a function
   block called after GPU detection, not a new phase file). Size: ~150-180
   lines of new Bash. Risk: **medium** — edits `/etc/default/grub` and
   `/etc/modprobe.d/`, needs a reboot, and should follow hal0's existing
   "never fail the boot" posture (`gpu_perms.py:1-27`,
   `hal0-gpu-perms.service`'s `SuccessExitStatus=0 1`). Low-controversy on
   the merits: hal0's own upstream image source already publishes the
   identical `amd_iommu=off amdgpu.gttsize=... ttm.pages_limit=...`
   recommendation (`amd-strix-halo-toolboxes/README.md:186`); open questions
   are packaging (default-on vs. flag) and reboot messaging.

2. **HSA_OVERRIDE_GFX_VERSION auto-detection for gfx1151.** Source:
   `.env.example:477` + ODS's gfx1151-conditional phase-06 logic. Target:
   `src/hal0/install/profile_derive.py` or the ROCm slot default
   `[server].env` near `src/hal0/config/schema.py:298-306`. Size: ~15-20
   lines. Risk: **low** — one conditional env default into an injection
   point that already exists; only care needed is not clobbering an
   operator-set value.

3. **Kernel/firmware known-bad-version checks in `hal0 doctor`.** Source:
   the same upstream README's warnings (kernel <6.18.4 unstable on gfx1151;
   `linux-firmware-20251125` "breaks ROCm support," `amd-strix-halo-
   toolboxes/README.md:53`) plus ODS's version-gated remediation pattern
   (`installers/phases/05-docker.sh:194-244`). Target: a new check in
   `src/hal0/cli/doctor_commands.py` reading `uname -r` and `rpm -q`/`dpkg
   -l` for `linux-firmware`. Size: ~40-60 lines. Risk: **low** — read-only.

4. **Resource limits on slot Quadlet units.** Source: ODS's
   `deploy.resources.limits.{cpus,memory}` convention
   (`docker-compose.base.yml:173-180`). Target: `_render_quadlet_from_plan`
   in `src/hal0/providers/container.py:803-990+` — Quadlet has native
   `[Service]` keys (`MemoryMax=`, `CPUQuota=`) that drop in next to the
   existing `Restart=`/`RestartSec=` block, sourced from a new optional
   profile/slot field. Size: ~25-40 lines plus one schema field. Risk:
   **low-medium** — additive when unset, but should default off since hal0
   currently assumes an unconstrained, single-tenant appliance box.

5. **CI matrix that validates a materialized config per bundle tier.**
   Source: `.github/workflows/validate-env.yml`'s 5-tier matrix. Target: a
   GitHub Action rendering each `installer/manifests/omni/<tier>.json`
   bundle's default `slots.toml`/`capabilities.toml` and asserting it loads
   under `hal0.config.schema.Hal0Config` without a `ValidationError` — closes
   the CI-coverage gap ODS's matrix has and hal0's (already-stronger)
   Pydantic validation does not. Size: ~50 lines. Risk: **low**.

6. **AMD multi-GPU topology detection** (`installers/lib/amd-topo.sh`) is
   lower priority: Strix Halo is single-iGPU, so this only matters if hal0
   ever supports discrete multi-GPU AMD boxes.

## E. Do-not-copy

- **The 270 KB / 6,595-line `ods-cli` monolith.** ODS's own decomposition doc
  concedes the problem; hal0's modular Typer tree (35 files, per-group
  ownership, issue-annotated mount points) is already the healthier
  architecture. Do not regress toward one giant dispatch file.
- **Compose "profiles removed, everything is core" as a configurability
  model** (`docs/PROFILES.md:39-41`). ODS's own history is a cautionary
  tale: it *had* opt-in profiles and removed them, ending up with "disable a
  service by hand-editing an override file." hal0's per-slot,
  per-capability TOML enable/disable is strictly more granular already.
- **Silently downgrading a system package as an installer side effect**
  (Docker 29.3.x→29.2.1, `installers/phases/05-docker.sh:205-223`).
  Reasonable for ODS's Docker-Engine-dependent world, but "installer
  reaches into apt/dnf and force-downgrades a system daemon" is not a
  pattern for hal0's podman/systemd model — detect-and-warn is the safer
  analog.
- **The written acceptance of unpinned `:latest` tags**
  (`docs/ADR-IMAGE-TAG-PINNING.md`) is strictly looser than hal0's current
  digest-pin-everything discipline; don't use it as license to relax hal0's
  bar.
- **Multi-platform installer sprawl** (`installers/macos/`,
  `installers/windows/`, `docs/WINDOWS-*`, `docs/WSL2-*`) is out of scope
  unless hal0 commits to non-Linux support.

## F. Owner decisions

1. **Adopt AMD host tuning (D.1) as default-on or opt-in?** It touches GRUB
   and needs a reboot; hal0 would want a "reboot recommended" banner (ODS's
   pattern, `installers/phases/10-amd-tuning.sh:289-302`) and a call on
   whether a single-target-hardware product should just always apply it.
2. **Close the #1870-class long-blocking lifecycle calls** via the event
   bus hal0 already has (`src/hal0/events/`, `/api/journal`) instead of a
   bare blocking `api_post` + multi-thousand-second timeout — is streaming
   slot-state transitions into the CLI (and eventually bounding the
   server-side pieces named open at `slot_lifecycle_budget.py:107`, #1869)
   worth prioritizing now, or does it wait on #1869 first?
3. **Fix #1844 properly**: should `activate_release()`
   (`src/hal0/updater/updater.py`) re-run the `/usr/local/bin/hal0` symlink
   step idempotently on every update, or is `/usr/local/bin/*` deliberately
   outside the updater's managed tree, better enforced by re-invoking
   `install.sh` unattended at the end of `hal0 update`?
4. **Extend hal0's digest-pinning discipline into a published "known-good
   kernel/firmware" doc**, mirroring `docs/KNOWN-GOOD-VERSIONS.md` and the
   amd-strix-halo-toolboxes warnings — worth wiring into `hal0 doctor` (D.3)
   rather than documentation alone.
5. **Resource limits on slot containers (D.4)**: is "unconstrained,
   single-tenant appliance" intentional for a Strix-Halo-only product, or a
   gap worth closing before a multi-slot box (chat + code + embedding
   resident at once, `slot_lifecycle_budget.py:92-94`) can starve itself?
