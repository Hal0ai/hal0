<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="./ui/public/brand/logo-halo-dark.svg">
  <img src="./ui/public/brand/logo-halo-light.svg" alt="hal0" width="220">
</picture>

### Open-source home AI inference platform

[hal0.dev](https://hal0.dev) · [Install](https://hal0.dev/docs/install/) · [Docs](https://hal0.dev/docs/)

</div>

---

hal0 is a polished, reliable inference platform for running LLMs at home.
It manages model slots, exposes an OpenAI-compatible API, and ships with
a built-in dashboard and a prewired chat UI — all installable in one
command on any modern Linux box.

> **Status:** **v0.1.0-alpha** — shipping. The cosign-keyless-OIDC
> release pipeline is wired end-to-end (signed tarball + `.crt` + manifest,
> self-verified before publish); the install command actually installs.
> Expect rough edges: APIs may shift, slot lifecycle bugs are still being
> shaken out, and we don't promise upgrade compatibility across `0.1.x`
> alpha tags. See [`PLAN.md`](./PLAN.md) §1 for what ships now and the
> path to v1.0.

## What hal0 does

- **Slots** — each inference workload (chat, embed, STT, TTS) runs in
  its own systemd-managed container with a known port, lifecycle, and
  health
- **Capability slots layer** — thin UX overlay grouping flat slots into
  user-facing capabilities (Embed / Voice / Image / NPU backend
  rollup). Selections persist in `/etc/hal0/capabilities.toml`;
  `CapabilityOrchestrator` reconciles selections against
  `slots/*.toml` on every apply
- **Dispatcher** — registry-aware routing between slots, with cold-cache
  prefetch and external upstream fallback (OpenRouter, Anthropic, etc.)
- **Dashboard** — Vue 3 + Tailwind 4 UI for slot/model management,
  hardware-aware configuration, live logs, and system health
- **OpenWebUI bundled** — prewired chat interface at `:3001`, no setup
- **Image generation** — bundled ComfyUI provider with curated SDXL /
  SD 1.5 / Flux models; OpenAI-compatible `/v1/images/generations`
- **One-line install** — `curl -fsSL https://hal0.dev/install.sh | bash`
  (`--models-dir=PATH` or `HAL0_MODELS_DIR=PATH` redirects HuggingFace
  pulls off `/var/lib/hal0/models`). The bootstrap fetches the release
  manifest, sha256-verifies the tarball, cosign-verifies the signature
  against the workflow OIDC identity, then hands off to
  [`installer/install.sh`](./installer/install.sh).

## Backends

| Backend     | Hardware             | Use case                          |
|-------------|----------------------|-----------------------------------|
| llama.cpp   | Vulkan (default) / ROCm | chat, embed, rerank, vision    |
| FLM         | AMD XDNA NPU (opt-in)| chat + embed (ASR multiplex available via `defaults.load_asr`; Moonshine is the default STT) |
| Moonshine   | CPU                  | STT (`/v1/audio/transcriptions`) |
| Kokoro      | CPU / Vulkan         | TTS                              |
| ComfyUI     | ROCm                 | image gen (`/v1/images/generations`) |

The NPU/FLM column is populated at runtime from `flm list -j` inside
the FLM toolbox image — hal0 doesn't pretend a GGUF runs on the NPU,
and the dashboard's model picker narrows the backend dropdown to the
backends a given model can actually serve. (`hal0 capabilities
migrate` cleans up persisted selections that pre-date this check.)

## Project layout

```
hal0/
├── src/hal0/         # Python package (FastAPI API + slot manager + CLI)
├── ui/               # Vue 3 + Tailwind 4 dashboard
├── installer/        # install.sh (systemd unit templates live in packaging/systemd/)
├── tests/            # pytest suite
├── docs/             # user docs (mirror of hal0.dev/docs); dev docs under docs/internal/
└── PLAN.md           # roadmap (current cut: v0.1.0-alpha; path to v1.0)
```

## Quick start (development)

```sh
# backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
hal0 serve --reload

# frontend
cd ui
npm install
npm run dev
```

The API lives at `http://127.0.0.1:8080`, the dashboard at the Vite dev
server URL (usually `http://127.0.0.1:5173`).

Run `hal0 doctor` any time to re-check pre-flight (systemd / python /
docker / disk / ports). `hal0 model pull <ref>` streams models from
Hugging Face into the registry, and `hal0 uninstall [--keep-data]` tears
down a running install (thin wrapper over `installer/uninstall.sh`).

### Auth posture

Per [ADR-0001](./docs/internal/adr/0001-collapse-edge-auth-into-fastapi.md), all
auth lives in FastAPI. As of v0.1.0-alpha, a fresh install **starts locked** —
the dashboard, `/v1/*`, and every admin route reject anonymous requests
with `401 auth.required`. Only the first-run wizard claim paths
(`/api/install/*` and `POST /api/auth/password`) stay reachable, and
only while the installer's `.first-run.lock` file is present on disk.

The installer prints a one-time OTP in the post-install summary; the
wizard's "Set a password" step asks for it before minting the owner
credential. Once the password is set the lockfile is deleted and the
claim window closes — from then on every request needs a session
cookie (browser) or Bearer token (programmatic client).

To opt back into the trusted-LAN open posture (single-user dev
boxes only), set `HAL0_AUTH_DISABLED=1` in `/etc/hal0/api.env` and
restart `hal0-api`. The legacy `HAL0_AUTH_ENABLED=0` falsy form is
still honoured for compatibility.

The default install runs Caddy in front for TLS termination (Caddy's
internal CA on `.local` hosts, Let's Encrypt for real DNS-resolvable
hostnames). Use `--no-tls` to skip Caddy and front hal0 with your own
reverse proxy. See [`installer/README.md`](./installer/README.md) for
the full flow.

### Proxmox integration (optional)

If hal0 runs inside a Proxmox LXC, the container only sees its own
cgroup slice of memory — other tenants, ZFS ARC, and the host kernel
draw from the same physical DIMMs as GPU GTT but are invisible from
inside. To surface that, drop a read-only `PVEAuditor` API token into
the dashboard's Settings → "Proxmox integration" panel. Once saved,
the unified-memory bar swaps to the physical host's DIMM total and
adds a muted "Proxmox host" segment for other-tenant + kernel
pressure. Token is sensitive and stored 0600 at
`/etc/hal0/proxmox.json`; the API never echoes it back. Bare-metal and
VM installs leave the panel off and the dashboard stays quiet.

## License

Apache 2.0. See [`LICENSE`](./LICENSE).

## Contributing

The contribution model is still being decided
([`PLAN.md`](./PLAN.md) §16). File issues for discussion; PRs aren't
being accepted from outside contributors yet. See
[`CONTRIBUTING.md`](./CONTRIBUTING.md) for the test tiers and the
eventual flow.
