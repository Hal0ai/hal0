# hal0 Bifrost gateway

A normalization shim between hal0's agents (Hermes first) and lemond, built on
[Bifrost](https://github.com/maximhq/bifrost). It collapses a recurring class of
local-inference bugs into one chokepoint, the way DreamServer uses LiteLLM —
but as a single fast Go binary instead of a Python proxy.

## Why

hal0 currently points each agent straight at `lemond:13305`. That means:

- **Strict model names.** lemond 404s if the request's `model` isn't exactly a
  loaded slot id. Agents loop (reason → tool → reason), so one stale name poisons
  the whole turn with `APIConnectionError`.
- **Reasoning tokens time out.** Local reasoning models emit `<think>` blocks
  that blow the request budget; today this is patched per-client (the FLM
  wrap-patch).

The gateway fixes both for every caller at once.

## What the plugin does (`plugin/main.go` + `normalize/`)

On every chat request (`PreLLMHook`):

1. **Rewrites the model** to lemond's currently-loaded LLM slot, resolved live
   from `/api/v1/health` (`all_models_loaded[]`, `type=="llm"`), cached for
   `ttl_ms`. Callers use one stable virtual name — `lemonade/primary` — and never
   track slot swaps. This is the key difference from DreamServer, which hardwires
   one fixed GGUF; hal0 swaps its primary at runtime, so the target is resolved
   dynamically.
2. **Injects `chat_template_kwargs.enable_thinking=false`** unless the caller
   already set `chat_template_kwargs`. The openai/custom provider merges
   `ExtraParams` into the wire body (`MergeExtraParamsIntoJSON`), so it reaches
   lemond. Verified against bifrost/core v1.5.16.

Unsupported OpenAI params are dropped by Bifrost's compat layer (the LiteLLM
`drop_params` equivalent), so a too-modern client request won't 400.

The pure transform (`normalize/`) is unit-tested and Bifrost-free; `plugin/` is
the thin adapter + the cached lemond resolver.

```
Hermes  --(model: lemonade/primary)-->  Bifrost :8079  --(real slot id + thinking off)-->  lemond :13305
```

## Layout

| Path | What |
|------|------|
| `normalize/` | pure transform + lemond-health parse (unit-tested) |
| `plugin/main.go` | Bifrost native (.so) plugin: `Init`/`GetName`/`PreLLMHook`/`Cleanup` |
| `config/config.json` | Bifrost config: custom `lemonade` provider + plugin ref |
| `systemd/hal0-bifrost.service` | loopback service on `127.0.0.1:8079`, `User=hal0` |
| `Makefile` | ABI-matched build + (held) deploy |

## Build

```sh
make test          # pure logic — no Bifrost needed
make plugin-check  # quick: compile the .so against pinned core
make build         # ABI-matched: bifrost-http + plugin .so from one pinned checkout
```

`-buildmode=plugin` demands the `.so` and `bifrost-http` share an identical
module graph. `make build` guarantees this by joining this module to a pinned
Bifrost checkout (`BIFROST_REF`, default `v1.5.16`) via a `go.work` workspace and
building both from it. **Never** load a locally-built `.so` into a prebuilt
`bifrost-http` — it panics on version skew.

## Deploy (HELD — not yet run)

`make deploy` pushes `bifrost-http`, the `.so`, `config.json`, and the unit to
CT105 under `/opt/hal0/bin` + `/etc/hal0/bifrost`. It does **not** touch Hermes.

### Hermes cutover (separate, manual, Tier-2)

Today hal0's Hermes points at **OpenRouter** (`deepseek/deepseek-v4-pro`), not
local lemond. Switching it to the gateway is a real behavior change (cloud →
local primary slot). After the gateway is up and smoke-tested, edit
`/var/lib/hal0/.hermes/config.yaml`:

```yaml
model:
  default: lemonade/primary
  provider: custom
  base_url: http://127.0.0.1:8079/v1   # config.yaml base_url wins over env
```

then `hermes restart` (as the `hal0` user — never root). Re-read the file first;
it may have changed in an in-flight Hermes update.

## Open items before production

- [ ] Confirm the exact `plugins[]` schema the running `bifrost-http` expects
      (path vs plugins-dir auto-load) — the transport module wasn't inspected
      offline; verify at deploy.
- [ ] Pin `BIFROST_REF` to the release whose transport uses core v1.5.16 and
      smoke-test `make build` end-to-end.
- [ ] Live smoke: `curl :8079/v1/chat/completions` with `model: lemonade/primary`
      → confirm it hits the loaded slot and `enable_thinking=false` is on the wire.
- [ ] Then the Hermes cutover above. Hermes-only first; widen to pi-coder / other
      agents after it's proven.
```
