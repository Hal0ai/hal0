# hal0 installer

Non-interactive installer for hal0 — the open-source home AI inference platform.

## Quick start

```sh
# From a clone of this repo:
sudo bash installer/install.sh

# Or point model pulls at a larger disk:
sudo bash installer/install.sh --models-dir=/mnt/ai-models
```

> The one-liner `curl -fsSL https://hal0.dev/install.sh | bash` is the
> primary install path as of v0.1.0-alpha — it fetches the signed
> release tarball, cosign-verifies against the workflow OIDC identity,
> and hands off to this `install.sh`. `git clone` + `sudo bash` still
> works for development against a checkout.

hal0's inference runtime is **container-based**: every inference slot
runs as its own podman container supervised by a per-slot systemd unit
(`hal0-slot@<name>.service`). The installer seeds the slot definitions
(`/etc/hal0/slots/*.toml`) and the backend profile catalog
(`/etc/hal0/profiles.toml`), installs `hal0-api.service` (the control
plane + dashboard on `:8080`), and installs the FastFlowLM host `.deb`
on AMDXDNA NPU hosts for device sanity probes. The model catalog is
`/var/lib/hal0/registry/registry.toml` — there is no separate runtime
catalog to sync.

## What the installer does

1. **Pre-flight checks** — confirms Python 3.11–3.14 is on `$PATH`, systemd is present (skipped in `--dev`), x86_64 arch, disk space, and free ports.
2. **Privilege model** — runs as `root` (re-execs under `sudo` if needed). `hal0-api` runs as `HAL0_USER` (default `root`; the podman container is the sandbox boundary for slots). A dedicated `hal0` system user runs the non-root services (agents, hermes-gateway, hindsight-api).
3. **Layout** — code under `/usr/lib/hal0/hal0-<version>` with a `current` symlink and a shared venv (`hal0 update` swaps the symlink atomically); config in `/etc/hal0/`; state in `/var/lib/hal0/{models,registry,slots,openwebui,cache}`. In `--dev` everything lands under `$PWD/.hal0ai/` instead.
4. **Installs hal0** — creates the shared venv and `pip install`s the release tree into it (editable in `--dev`), then links `/usr/local/bin/hal0` + `/usr/local/bin/hal0-agent`.
5. **Builds the dashboard UI** — runs `npm install && npm run build` in `ui/` if `ui/dist/` is missing and `npm` is available. Skipped (with a warning) when `npm` is absent.
6. **Config defaults** — writes `/etc/hal0/hal0.toml`, `api.env`, `upstreams.toml`, and `openwebui.env`. `capabilities.toml` ships empty by design — the first-run dashboard renders the bundle picker. Existing files are **never clobbered** on re-run.
7. **systemd units** — writes `hal0-api.service`, copies `hal0-openwebui.service` and the `hal0-agent@.service` template (+ hermes drop-in), reloads the daemon, enables and starts `hal0-api` + `hal0-openwebui` (unless `--no-start`). Per-slot `hal0-slot@<name>.service` units are managed by hal0 itself when slots are loaded.
8. **Hardware probe** — writes `/etc/hal0/hardware.json`, prints detected backends, and seeds a recommended `slots/chat.toml` (disabled until you pull a model). Skip with `HAL0_NO_PROBE=1`.
9. **NPU prerequisites** — installs the FLM runtime libs (ffmpeg6, boost1.83, fftw3), `libxrt-npu2` when the host's apt sources provide it, and the pinned FastFlowLM `.deb` (SHA-256 verified). All fail-soft: a GPU-only host still installs fine.
10. **Container slot seeds** — copies `installer/etc-hal0/slots/{npu,tts,rerank,utility,img}.toml` into `/etc/hal0/slots/` (never overwriting operator edits). Each slot gates on its own runtime validation at load time.

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
| `HAL0_USER` | `root` | systemd unit user for hal0-api (see §What the installer does) |
| `HAL0_PYTHON` | `python3` | Python interpreter used to build the venv |
| `HAL0_MODELS_DIR` | _(unset)_ | Absolute path where model pulls land; same as `--models-dir=PATH`. When unset, models live at `/var/lib/hal0/models` (or `$PWD/.hal0ai/var/lib/hal0/models` under `--dev`). |
| `HAL0_NO_PROBE` | _(unset)_ | Set to `1` to skip the hardware probe at the end |
| `HAL0_SKIP_FLM_SHA` | _(unset)_ | Set to `1` to accept an unpinned FastFlowLM `.deb` checksum (placeholder pin only — a real mismatch always refuses) |
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
header; see [`docs/agents/identity.md`](../docs/agents/identity.md)
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

- `moe-rocmfp4` — A3B MoE models (~52.8 tok/s gen, 131k ctx).
- `dense-mtp-rocmfp4` — dense chat with MTP (`mtp = true`, ~2× non-MTP).

Point a slot at one of them (`profile = "dense-mtp-rocmfp4"`) or let
the device default pick it (`device = "gpu-rocm"` resolves to
`moe-rocmfp4`). gfx1151 (Strix Halo) + ROCm hosts only; non-eligible
hosts should stay on `vulkan-std`. The old `--rocmfp4` installer flag
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
