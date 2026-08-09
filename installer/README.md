# hal0 installer

Non-interactive installer for hal0 — the open-source home AI inference platform.

## Quick start

```sh
# From a clone of this repo:
sudo bash installer/install.sh

# Or point model pulls at a larger disk:
sudo bash installer/install.sh --models-dir=/mnt/ai-models
```

> The one-liner `curl -fsSL https://hal0.dev/install.sh | sudo bash` is the
> primary install path as of v0.1.0-alpha — it fetches the signed
> release tarball, cosign-verifies against the workflow OIDC identity,
> and hands off to this `install.sh`. `git clone` + `sudo bash` still
> works for development against a checkout. The one-liner is served by
> `installer/bootstrap.sh`, which reads `HAL0_CHANNEL` (`stable` —
> default —, `preview`, or `nightly`) to pick which signed release
> manifest to fetch; `install.sh` itself has no channel concept.

hal0's inference runtime is **container-based**: every inference slot
runs as its own podman container supervised by a per-slot systemd unit
(`hal0-slot@<name>.service`). The installer seeds the slot definitions
(`/etc/hal0/slots/*.toml`) and the backend profile catalog
(`/etc/hal0/profiles.toml`), installs `hal0-api.service` (the control
plane + dashboard on `:8080`), and installs the FastFlowLM host `.deb`
on AMDXDNA NPU hosts for device sanity probes. The model catalog is
`/var/lib/hal0/registry/registry.toml` — there is no separate runtime
catalog to sync.

### Supported distributions

The installer runs on any systemd Linux on x86_64. Package-manager-specific
steps (Docker install hints, the Python/venv hint, NPU prereqs) route through
`lib/distro.sh`, which detects apt / dnf / yum / zypper / pacman / apk and emits
the right command for the host — so install-time messages are correct on
**Debian/Ubuntu, Fedora/RHEL, Arch, openSUSE, and Alpine** rather than assuming
apt. The one genuine distro limit is the NPU path: FastFlowLM publishes
Debian/Ubuntu `.deb`s only (per-release builds for Ubuntu 24.04/25.10/26.04
and Debian 13), so on non-apt distros (Fedora, Arch, openSUSE…) the host FLM
probe waits on a manual install (GPU/Vulkan/ROCm and CPU paths are fully
supported everywhere).

## What the installer does

1. **Pre-flight checks** — confirms a Python 3.12+ interpreter (auto-installed via `HAL0_PY_AUTOINSTALL=1`), systemd (skipped in `--dev`), x86_64 arch, a container runtime (podman, auto-installed — every inference slot needs one), GPU/NPU device visibility (hard-stops on broken passthrough; a CPU-only box needs explicit opt-in), disk space, and free ports.
2. **System user** — creates the dedicated `hal0` system user/group (skipped in `--dev`) and adds it to the host's `render`/`video` groups for GPU device access. `hal0-api` itself runs as this unprivileged user (`User=hal0` in the unit); the few genuinely-root operations it still needs — systemd unit writes, `daemon-reload`, slot start/stop/restart, self-update — route through narrow sudo seams (`hal0-systemctl`, `hal0-update`) installed later in "Systemd units". The slot inference containers (podman) are the sandbox boundary for models.
3. **Filesystem layout** — code under `/usr/lib/hal0/hal0-<version>` with a `current` symlink and a shared venv (`hal0 update` swaps the symlink atomically); config in `/etc/hal0/`; state in `/var/lib/hal0/{models,registry,slots,openwebui,cache}`. In `--dev` everything lands under `$PWD/.hal0ai/` instead.
4. **Python environment** — creates the shared venv and `pip install`s the release tree into it (editable in `--dev`), then links `/usr/local/bin/hal0` + `/usr/local/bin/hal0-agent`.
5. **Node.js toolchain** — auto-provisions a Node.js LTS (`HAL0_NODE_AUTOINSTALL=1`) when the tree ships the dashboard's npm project (`ui/package.json`). Skipped entirely on a signed release tree that ships a prebuilt `ui/dist` (nothing to rebuild) and in `--dev` (install Node manually to exercise the build there).
6. **Dashboard UI** — runs `npm install && npm run build` in `ui/` when the built assets are missing or stale (tree-hash stamped), or reuses a release's prebuilt `ui/dist` as-is. Skipped (with a warning) when Node/npm is absent or too old — the API still serves; the dashboard just 404s until built.
7. **Configuration** — writes `/etc/hal0/hal0.toml`, `api.env` (0600), `upstreams.toml`, and `openwebui.env`; validates and persists a supplied HuggingFace token to the root-only `/var/lib/hal0/secrets/hal0-api.env`. `capabilities.toml` ships empty by design — the first-run dashboard renders the bundle picker. Existing files are **never clobbered** on re-run.
8. **Systemd units** — writes `hal0-api.service`, `hal0.target`, `hal0-openwebui.service`, the podman↔Docker forward-reconciliation unit, and the `hal0-agent@.service` template (+ hermes drop-in, session-state hook, and the `hal0-systemctl` / `hal0-agentenv` / `hal0-benchctl` sudo seams with their sudoers grants), then `systemctl daemon-reload`. Units are written here; `hal0-api` and `hal0-openwebui` aren't enabled/started until the "Service start" step below. Per-slot `hal0-slot@<name>.service` units are managed by hal0 itself when slots are loaded.
9. **Container slot seeds** — copies `installer/etc-hal0/slots/{flm,tts,rerank,utility,img,agent,brain,qwen3tts,coder,embed}.toml` into `/etc/hal0/slots/` (never overwriting operator edits, and honoring a `hal0 slot delete` tombstone). Each slot gates on its own runtime validation at load time.
10. **Hardware probe** — runs `hal0 setup --auto --no-pull --no-extensions` (first-run seeding): scaffolds capability-slot structure with no model picks, writes `/etc/hal0/hardware.json`, and prints the detected backends. Skipped by `HAL0_SKIP_SETUP=1` or `HAL0_NO_PROBE=1`.
11. **Steward + agent models** — unconditionally pulls a hardware-matched brain model (~1-2 GB) and binds it to the `brain` slot, so the dashboard's steward chat works out of the box — the brain models are native tool-callers on the shipped runner, so tool calls work too. `HAL0_SKIP_BRAIN_MODEL=1` skips the pull; `HAL0_BRAIN_MODEL=<id>` forces a specific curated quant. Then, only on an interactive terminal, an **opt-in** offer for a larger agent model (15-31 GB, exact size printed first, default **No**, skipped entirely on non-interactive installs) — it becomes the bigger tool-caller behind the `[brain_chat] tool_model` fallback (default `hal0/agent`); `HAL0_PULL_AGENT_MODEL=1` opts in unattended, `=0` force-skips even on a TTY, `HAL0_AGENT_MODEL=<id>` picks a specific rung. Both pulls are fail-soft — a decline or failure never fails the install.
12. **NPU prerequisites (FastFlowLM)** — on apt hosts, installs `libxrt-npu2` (best-effort, from the lemonade-team PPA) and the per-distro, SHA-256-pinned FastFlowLM `.deb` (`ubuntu24.04`/`ubuntu25.10`/`ubuntu26.04`/`debian13`); `apt-get install ./deb` then resolves that release's own ffmpeg/boost/fftw dependencies from the host's own repos — no hardcoded library list. FastFlowLM ships Debian/Ubuntu `.deb`s only upstream, so on non-apt distros (Fedora, Arch, openSUSE…) this step is skipped with an honest message naming the distro — GPU/CPU paths are unaffected; the NPU/FLM trio waits on a manual FastFlowLM install. All fail-soft: a GPU-only host still installs fine.
13. **ComfyUI model share** — creates the `models`/`output`/`input`/`user`/`custom_nodes` share directories the `img` slot container bind-mounts, and seeds custom nodes + `extra_model_paths.yaml` (never overwriting an existing copy). Skip with `HAL0_SKIP_COMFYUI=1`; a squashed/read-only mount here warns rather than aborts.
14. **Bundle picker manifests** — copies the five first-run bundle manifests (hal0-Lite / hal0-Default / hal0-Pro / hal0-Max + LMX-Omni-52B-Halo) into `/var/lib/hal0/models/collections/omni/`, so the dashboard's bundle picker works from a packaged install and not just a source checkout.
15. **Bundled agent skills** — ships hal0's own agent skills to `/usr/share/hal0/skills` (read-only source) and creates a writable drop-in at `/var/lib/hal0/skills`; the Hermes provisioning step below mirrors both into `/etc/hal0/agent-skills` so a fresh agent starts with the bundled skills already loaded.
16. **Service start** — applies the model-layout symlink migration and the `doctor perms --fix` ownership backstop, then (unless `--dev` or `--no-start`) enables and starts `hal0-api` + `hal0.target`, stands up the Hindsight memory engine (`HAL0_SKIP_HINDSIGHT=1` to skip), starts OpenWebUI (`HAL0_SKIP_OPENWEBUI=1`) and the podman-forward unit when Docker is co-installed, and provisions the bundled Hermes agent (`HAL0_SKIP_HERMES=1`). `--no-start` sets up everything but leaves services stopped — start manually with `hal0 serve`.

The installer is **idempotent** — safe to re-run after a partial failure or to update configuration defaults.

### Container runtime

Each enabled slot runs one podman container built from a **profile** —
a named (image, flags, mtp) template in `/etc/hal0/profiles.toml`
(seeded from `installer/etc-hal0/profiles.toml`; hal0-api falls back to
the built-in seed profiles when the file is absent). The slot TOML
picks a profile via `profile = "<name>"`; per-slot state lives at
`/var/lib/hal0/slots/<name>/state.json`. Logs go to journald:

```sh
journalctl -fu hal0-api
journalctl -fu 'hal0-slot@*'          # all slot containers
journalctl -fu hal0-slot@chat         # one slot
```

The image-generation slot (`img`, ComfyUI) runs in **exclusive GPU
mode**: the GPU arbiter (`/var/lib/hal0/gpu_arbiter.json`) stops LLM
GPU slots while image mode is active and restores them when it goes
idle. See `docs/operate/container-runtime.md` for the full ops guide.

Pinned FLM and container image versions are tracked per release; run
`hal0 doctor` to verify API health and (on NPU hosts) FLM install
state.

## Environment variables

These are the variables `installer/install.sh` actually reads:

| Variable | Default | Description |
|---|---|---|
| `HAL0_PREFIX` | `/usr/lib/hal0` (or `$PWD/.hal0ai` in `--dev`) | Installation root (versioned code + shared venv) |
| `HAL0_PORT` | `8080` | hal0 API port |
| `HAL0_PYTHON` | `python3` | Python interpreter used to build the venv |
| `HAL0_MODELS_DIR` | _(unset)_ | Absolute path where model pulls land; same as `--models-dir=PATH`. When unset, models live at `/var/lib/hal0/models` (or `$PWD/.hal0ai/var/lib/hal0/models` under `--dev`). |
| `HAL0_NO_PROBE` | _(unset)_ | Set to `1` to skip the hardware probe at the end |
| `HAL0_SKIP_FLM_SHA` | _(unset)_ | Set to `1` to accept an unpinned FastFlowLM `.deb` checksum (placeholder pin only — a real mismatch always refuses) |
| `HAL0_NONINTERACTIVE` | _(unset)_ | Set to `1` to force flag/env defaults everywhere, skipping the interactive models-dir / HuggingFace-token prompts even on a TTY |
| `HAL0_PY_AUTOINSTALL` | _(unset)_ | Set to `1` to let the installer auto-install a compatible `python3.12` when the default `python3` is below hal0's floor |
| `HAL0_SKIP_HINDSIGHT` | _(unset)_ | Set to `1` to skip installing/starting the `hindsight-api` memory service |
| `HF_TOKEN` | _(unset)_ | HuggingFace read token for gated/large model pulls (also accepted as `HUGGING_FACE_HUB_TOKEN`). Pre-fills the interactive prompt (Enter keeps it) and is used directly on a non-interactive run; validated with `hf auth whoami` and persisted 0600 to `/var/lib/hal0/secrets/hal0-api.env` |
| `HAL0_SKIP_BRAIN_MODEL` | _(unset)_ | Set to `1` to skip the unconditional brain (steward) model pull — the `brain` slot stays model-less |
| `HAL0_BRAIN_MODEL` | _(unset)_ | Force a specific curated brain quant instead of the hardware-derived pick |
| `HAL0_PULL_AGENT_MODEL` | _(unset)_ | Set to `1` to opt in to the (15-31 GB) agent model pull unattended; set to `0` to force-skip the offer even on an interactive terminal |
| `HAL0_AGENT_MODEL` | _(unset)_ | Force a specific rung of the agent model ladder instead of the hardware-derived pick |
| `HAL0_SKIP_SETUP` | _(unset)_ | Set to `1` to skip first-run seeding (`hal0 setup --auto --no-pull`) **and** both the brain and agent model steps |
| `HAL0_OPENWEBUI_PORT` † | `3001` | OpenWebUI host port — **dev mode only** |

† `HAL0_OPENWEBUI_PORT` is honored by `scripts/dev-bootstrap.sh` (the dev-mode launcher). The installed `hal0-openwebui.service` hardcodes `:3001`; to change it post-install, edit `/etc/systemd/system/hal0-openwebui.service` and reload.

Example:

```sh
HAL0_PORT=9090 sudo bash installer/install.sh
```

## Authentication & TLS

As of **v0.3.0-alpha.1** (ADR-0012), authentication is no longer
hal0's concern. The installer ships no Caddy, no Bearer-token store,
no first-run OTP, no password claim wizard, no `--no-tls` flag, and
no `HAL0_AUTH_*` env vars. `hal0-api` binds `0.0.0.0:8080` open; if
you need a gate, put a reverse proxy (Caddy, Traefik, nginx,
Cloudflare Tunnel — whatever you already run) in front of it and
own auth + TLS at the edge. A recipe lives at
[`docs/operate/auth.mdx`](../docs/operate/auth.mdx).

Identity at the application layer is the `X-hal0-Agent` request
header; see [`docs/.devdocs/agents/identity.md`](../docs/.devdocs/agents/identity.md)
for the agent-identity model and how to set the header from a
programmatic client.

## Dev mode (`--dev`)

Runs the full installer logic but lays everything under `$PWD/.hal0ai/` instead of FHS paths. systemd units are **not** installed or enabled — they are written to `$PWD/.hal0ai/etc/systemd/system/` for inspection only.

```sh
bash installer/install.sh --dev
```

Use `scripts/dev-bootstrap.sh` to actually start services during development.

### `--dev` mode limitations

`--dev` is a contributor convenience, not a runtime path. The installer writes the same systemd units (`hal0-api.service`, `hal0-openwebui.service`) into the dev tree, but it does **not** register them with the host's systemd. Concretely:

- Units land in `$PWD/.hal0ai/etc/systemd/system/`.
- The host's `systemctl` only searches `/etc/systemd/system/` and `/usr/lib/systemd/system/`, so it cannot see them.
- Slot loads that end in `systemctl start hal0-slot@<name>` will fail because the per-slot units aren't registered — the dispatcher has no container supervisor to call.

Two ways to resolve this, depending on what you're trying to do:

1. **Just do a real install.** This is the supported runtime path:

   ```sh
   sudo bash installer/install.sh
   ```

   Real install puts units under `/etc/systemd/system/`, runs `systemctl daemon-reload`, and the full container slot pipeline works end-to-end.

2. **Or link the dev units into the system search path.** Keeps the dev tree as the source of truth, but tells the host systemd where to find the units:

   ```sh
   sudo systemctl link "$PWD/.hal0ai/etc/systemd/system/hal0-api.service"
   sudo systemctl link "$PWD/.hal0ai/etc/systemd/system/hal0-openwebui.service"
   sudo systemctl daemon-reload
   ```

   After that, service operations work against the dev tree. Edits to the linked unit files take effect after another `systemctl daemon-reload`.

The installer prints the same warning block at the end of every `--dev` run as a reminder.

## ROCmFP4 + MTP (container profiles)

FP4 GGUFs with a baked-in multi-token-prediction (MTP) head are served
by the `rocm-7.2.4-rocmfp4-server` toolbox image — the fork
`llama-server` that loads the FP4 quant types is **inside the
container**, no host-side build or binary wiring required. Two seed
profiles use it (see `/etc/hal0/profiles.toml`):

- `rocm` — A3B MoE models (~52.8 tok/s gen, 131k ctx).
- `rocm-mtp` — dense chat with MTP (`mtp = true`, ~2× non-MTP).

Point a slot at one of them (`profile = "rocm-mtp"`) or let
the device default pick it (`device = "gpu-rocm"` resolves to
`rocm`). gfx1151 (Strix Halo) + ROCm hosts only; non-eligible
hosts should stay on `vulkan`. The old `--rocmfp4` installer flag
and host-side fork binary are gone.

## Uninstall

```sh
# Prompts before deleting config + model data
sudo bash installer/uninstall.sh

# Keep /etc/hal0 and /var/lib/hal0 (models, registry, OpenWebUI state)
sudo bash installer/uninstall.sh --keep-data

# No confirmation prompt (CI / scripted teardown)
HAL0_FORCE=1 sudo bash installer/uninstall.sh
```

## Troubleshooting

### Port already in use

```
✗ pre-flight failed: port 8080 is already in use.
```

Find the process: `lsof -i :8080`  
Then either stop it or re-run with a different port:

```sh
HAL0_PORT=8090 sudo bash installer/install.sh
```

After changing the port, update `/etc/hal0/api.env` and `/etc/hal0/openwebui.env` to match, then `systemctl restart hal0-api hal0-openwebui`.

### A slot won't load

Check the slot unit and the API's view of it:

```sh
systemctl status hal0-slot@<name>
journalctl -u hal0-slot@<name> -n 60
curl -s http://127.0.0.1:8080/api/slots | python3 -m json.tool
```

Common causes: the container image hasn't been pulled yet (first load
blocks on a multi-GB pull — watch the journal), the model file named in
`/etc/hal0/slots/<name>.toml` isn't in the registry
(`hal0 model list`), or the GPU is held by image mode (the dispatcher
returns 503 while the `img` slot owns the GPU; stop image mode or wait
for idle-restore).

### FLM .deb missing (NPU host only)

```
hal0 doctor: AMDXDNA NPU detected but FastFlowLM not installed
```

The npu slot's host sanity probe needs the FastFlowLM `.deb` package.
The installer handles this automatically on AMDXDNA hosts, but if you
installed on a non-NPU host and later added the hardware, re-run
`installer/install.sh` to pick up the FLM prerequisites. If
`flm validate` fails because `libxrt-npu2` is unavailable from your
apt sources, the npu **container** slot still works — it bundles its
own XRT runtime.

### Not enough disk space

```
✗ pre-flight failed: less than 20GB free in /var/lib
```

Free up space, or redirect HuggingFace pulls to a larger disk:

```sh
sudo bash installer/install.sh --models-dir=/mnt/large-disk/hal0-models
```

The installer records this in `/etc/hal0/hal0.toml` under
`[models].pull_root` so subsequent `hal0 model pull` calls honor it too.

### Services won't start

Check logs:

```sh
journalctl -fu hal0-api
journalctl -fu hal0-openwebui
journalctl -fu 'hal0-slot@*'
systemctl status hal0-api hal0-openwebui
```

### OpenWebUI can't reach the API

OpenWebUI is configured to talk to `http://127.0.0.1:8080/v1`. If you changed `HAL0_PORT`, update `/etc/hal0/openwebui.env`:

```
OPENAI_API_BASE_URLS=http://127.0.0.1:<new-port>/v1
```

Then `systemctl restart hal0-openwebui`.
