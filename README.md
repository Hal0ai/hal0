# hal0

**Open-source home AI inference platform.**

hal0 is a polished, reliable inference platform for running LLMs at home.
It manages model slots, exposes an OpenAI-compatible API, and ships with
a built-in dashboard and a prewired chat UI — all installable in one
command on any modern Linux box.

> **Status:** pre-alpha. Active development. Not yet a thing you should
> install. See [`PLAN.md`](./PLAN.md) for the v1 roadmap.

## What hal0 does

- **Slots** — each inference workload (chat, embed, STT, TTS) runs in
  its own systemd-managed container with a known port, lifecycle, and
  health
- **Dispatcher** — registry-aware routing between slots, with cold-cache
  prefetch and external upstream fallback (OpenRouter, Anthropic, etc.)
- **Dashboard** — Vue 3 + Tailwind 4 UI for slot/model management,
  hardware-aware configuration, live logs, and system health
- **OpenWebUI bundled** — prewired chat interface at `:3001`, no setup
- **Image generation** — bundled ComfyUI provider with curated SDXL /
  SD 1.5 / Flux models; OpenAI-compatible `/v1/images/generations`
- **One-line install** — `curl -fsSL hal0.dev/install | bash`

## Backends (v1)

| Backend     | Hardware             | Use case                          |
|-------------|----------------------|-----------------------------------|
| llama.cpp   | Vulkan (default) / ROCm | chat, embed, rerank, vision    |
| FLM         | AMD XDNA NPU (opt-in)| chat / embed / ASR multiplex     |
| Moonshine   | CPU / Vulkan         | STT (`/v1/audio/transcriptions`) |
| Kokoro      | CPU / Vulkan         | TTS                              |
| ComfyUI     | ROCm                 | image gen (`/v1/images/generations`) |

## Project layout

```
hal0/
├── src/hal0/         # Python package (FastAPI API + slot manager + CLI)
├── ui/               # Vue 3 + Tailwind 4 dashboard
├── installer/        # install.sh (systemd unit templates live in packaging/systemd/)
├── tests/            # pytest suite
├── docs/             # architecture, install, slot docs
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
docker / disk / ports).

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

This is pre-alpha. External contributions aren't being accepted yet;
that opens up around v0.2. See [`CONTRIBUTING.md`](./CONTRIBUTING.md) for
the eventual flow.
