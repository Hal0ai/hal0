# hal0

**Open-source home AI inference platform.**

hal0 is a polished, reliable inference platform for running LLMs at home.
It manages model slots, exposes an OpenAI-compatible API, and ships with
a built-in dashboard and a prewired chat UI — all installable in one
command on any modern Linux box.

> **Status:** v1.0 release candidate. The release pipeline (cosign
> keyless OIDC, signed tarball + `releases.hal0.dev` manifest) is wired
> end-to-end; the first real tag-push is still pending. See
> [`PLAN.md`](./PLAN.md) §15 for the milestone state and §18 for the
> v1.0 definition of done.

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
- **One-line install** — `curl -fsSL https://hal0.dev/install | bash`
  (`--models-dir=PATH` or `HAL0_MODELS_DIR=PATH` redirects HuggingFace
  pulls off `/var/lib/hal0/models`). Until the `hal0.dev/install`
  endpoint is wired, the supported entry point is
  `git clone` + `sudo bash installer/install.sh` — see
  [`installer/README.md`](./installer/README.md).

## Backends (v1)

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
├── docs/             # api-errors, release-manifest, migration, ADRs
└── PLAN.md           # v1 roadmap
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

Per [ADR-0001](./docs/adr/0001-collapse-edge-auth-into-fastapi.md), all
auth lives in FastAPI. A fresh install is **open on the LAN** — no
password, no Bearer required for the dashboard or `/v1/*`. The
dashboard wizard's password-setup step (`POST /api/auth/password`,
public on first run) opts in to login. Programmatic clients use Bearer
tokens unchanged.

The default install runs Caddy in front for TLS termination (Caddy's
internal CA on `.local` hosts, Let's Encrypt for real DNS-resolvable
hostnames). Use `--no-tls` to skip Caddy and front hal0 with your own
reverse proxy. See [`installer/README.md`](./installer/README.md) for
the full flow.

## License

Apache 2.0. See [`LICENSE`](./LICENSE).

## Contributing

The contribution model for v1.0 is still being decided
([`PLAN.md`](./PLAN.md) §16). File issues for discussion; PRs aren't
being accepted from outside contributors yet. See
[`CONTRIBUTING.md`](./CONTRIBUTING.md) for the test tiers and the
eventual flow.
