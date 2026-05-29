---
title: Hermes config surfaces
description: The eight config surfaces hal0's bundled Hermes-Agent reads from, who writes which, precedence order, and restart-vs-hot-reload semantics.
sidebar:
  order: 3
---

Hermes-Agent is configurable through eight distinct surfaces, six of
them owned by hal0 and two owned by the operator. This page is the
canonical map: where each surface lives, who writes it, what the agent
reads from it, and whether changes take effect on the next agent
restart, on the next provisioning run, or live via hot-reload.

Why bother enumerating: Hermes was not designed as a managed agent.
Upstream's defaults assume an interactive operator hand-editing
`~/.hermes/config.yaml`. hal0 wraps that flow with `hal0 agent
bootstrap hermes` (the [12-phase pipeline](./hermes-bootstrap.md)) +
a small constellation of override files. If you don't know which file
owns which knob, you'll edit `config.yaml` only to have the next
bootstrap pass blow your changes away. This page prevents that.

## The eight surfaces

| # | Surface | Path | Written by | Read by | Change effect |
|---|---|---|---|---|---|
| 1 | Persona TOML | `/var/lib/hal0/agents/hermes/personas/<id>.toml` | Operator or `hal0 agent reprovision` | `_phase_config_write` → `system_prompt_prelude` | Next bootstrap render OR `hal0 agent personas activate` |
| 2 | Personas active pointer | `/var/lib/hal0/agents/hermes/personas/active.txt` | `hal0 agent personas activate` | `_phase_config_write` | Best-effort hot-reload + next render |
| 3 | Operator overrides | `/etc/hal0/agents/hermes/overrides.yaml` | Operator | `_phase_config_write` post-render deep-merge | Next bootstrap |
| 4 | Hermes config | `$HERMES_HOME/config.yaml` | `_phase_config_write` (managed; do not hand-edit) | Hermes agent loop on startup | Hermes restart |
| 5 | MCP allowlist | `/etc/hal0/agents/hermes.toml` | Operator (installer-seeded) | `_phase_mcp_wire` | Next bootstrap |
| 6 | Secrets env | `/var/lib/hal0/secrets/agents/hermes.env` | `_phase_voice_wire` (auto) + operator (manual) | systemd `EnvironmentFile=` (PR-5) | Hermes restart |
| 7 | Provision checkpoint | `/var/lib/hal0/state/agents/hermes/provision.json` | `_phase_*` (managed; never hand-edit) | Bootstrap orchestrator | Per-phase |
| 8 | Plugin manifests | `$HERMES_HOME/plugins/<name>/plugin.yaml` + `dashboard/manifest.json` | `_phase_install` (managed) or operator (custom) | Hermes plugin discovery + hal0 plugin host (PR-7) | Hermes restart |

Precedence at config render time, top wins:

1. `overrides.yaml` (operator overlay)
2. `personas/<active>.toml` system prompt + tool gating
3. `mcp_wire` probe results (which MCP servers were reachable)
4. Live `/api/slots` payload (chat slot aliases + primary model)
5. Template defaults baked into `config.yaml.j2`

## 1. Persona TOML

**Path:** `/var/lib/hal0/agents/hermes/personas/<id>.toml`
**Written by:** Operator (free-form) or `hal0 agent reprovision hermes`
(seeds the `hermes` + `coder` defaults; operator edits stand).
**Read by:** `_phase_config_write` to compose `agent.system_prompt_prelude`.

Schema (every section optional except `[persona].id`):

```toml
[persona]
id = "hermes"                                # filename stem; matches active.txt
display_name = "Hermes"                      # sidebar / dropdown label
summary = "Default helpful assistant."       # 1-liner for the chooser

[persona.prompt]
system = """
You are Hermes, a resident agent inside the hal0 home-AI platform...
"""

[persona.tools]
allowed = ["*"]                              # glob list; "*" = unrestricted

[persona.memory]
namespace = "private:hermes-agent"           # passed via X-hal0-Agent header

[persona.approval]
default_policy = "ask"                       # ask | auto-approve | never
auto_approve = ["memory.read.*", "search.*"] # glob list
require_approval = ["files.*", "shell.*"]    # glob list

[persona.model]
preferred_upstream = "hal0"
preferred_model = ""                         # empty = first available
```

**Change effect:** The next bootstrap render (or `hal0 agent
reprovision hermes`) picks up the new prompt. `hal0 agent personas
activate <id>` switches the active persona AND sends a best-effort
hot-reload nudge to a running Hermes; the file write is the durable
part — the nudge is opportunistic.

**Default seeds:**

- `hermes` — general assistant, `*` tools, memory ns `private:hermes-agent`, approval `ask`
- `coder` — software focus, `*` tools, memory ns `private:hermes-agent-coder` so context doesn't bleed, approval `ask` but auto-approves all `*.read.*` tool patterns

## 2. Personas active pointer

**Path:** `/var/lib/hal0/agents/hermes/personas/active.txt`
**Written by:** `hal0 agent personas activate <id>` (atomic tmp+rename)
or `_phase_persona_seed` (on first install only).
**Read by:** `_phase_config_write` to know which persona TOML to load.

One line, a persona id, optional trailing newline. Pointing at a missing
file is illegal — `set_active` checks first.

**Change effect:** Hot-reload nudge to Hermes JSON-RPC; if Hermes is
running it switches mid-conversation (next assistant turn picks up the
new system prompt). If Hermes isn't running, the next service start
re-reads via the render path.

## 3. Operator overrides

**Path:** `/etc/hal0/agents/hermes/overrides.yaml`
**Written by:** Operator only. hal0 never touches this file.
**Read by:** `_phase_config_write` after rendering the template; the
overlay deep-merges (overlay wins on key collision, nested dicts merge).

This is the escape hatch for any Hermes config knob hal0 doesn't model
explicitly (a new STT provider, an experimental MCP server, a
`compression.*` tweak). Anything you put here survives every
`hal0 agent reprovision` — that's the whole point.

**Change effect:** Next bootstrap render. Hand-running `hal0 agent
reprovision hermes` is the canonical "I edited overrides.yaml" verb.

## 4. Hermes config

**Path:** `$HERMES_HOME/config.yaml` (default `/var/lib/hal0/agents/hermes/config.yaml`)
**Written by:** `_phase_config_write` (managed file — hash-tracked +
atomic-swapped). Hand-edits are silently overwritten on the next
bootstrap pass.
**Read by:** Hermes agent loop at process startup; some sections
(`mcp_servers`, model defaults) re-read on JSON-RPC `reload.env`.

Sections rendered:

- `model.{default, provider, base_url, context_length}` — primary chat slot
- `providers.custom.{name, base_url, request_timeout_seconds}` — the OpenAI-compatible LAN-endpoint profile that hal0 occupies
- `model_aliases` — one entry per ready chat slot (chat-slot routing)
- `memory.{provider, memory_enabled, ...}` — provider is always `hal0-memory`
- `mcp_servers.*` — one block per probe-OK MCP server (PR-3 Phase 6 sources this from the live probe, not a hard-coded list)
- `agent.system_prompt_prelude` — persona-rendered prelude (PR-3 Phase 7)
- `display.personality` — persona display name (PR-3 Phase 8 cosmetic)
- `stt`/`tts` — only when both slots are ready
- `hooks.on_session_start` — drops to `/usr/lib/hal0/hermes-hooks/inject-system-state.sh`

**Change effect:** Hermes restart for top-level changes. Persona-only
changes can hot-reload on the next assistant turn.

## 5. MCP allowlist

**Path:** `/etc/hal0/agents/hermes.toml`
**Written by:** `installer/install.sh` (drops the default allowlist
naming `hal0-admin` + `hal0-memory`). Operator can edit to add or
deny.
**Read by:** `_phase_mcp_wire` — only servers listed under
`[mcp.servers.<name>]` are probed; missing ones are skipped with a
warning per ADR-0013.

Format:

```toml
[mcp.servers.hal0-admin]
url = "http://127.0.0.1:8080/mcp/admin"

[mcp.servers.hal0-memory]
url = "http://127.0.0.1:8080/mcp/memory"
private = true
```

A missing allowlist file means "allow every default builtin" — opt-out,
not opt-in for the first-install case. Once the file exists, additions
are explicit.

**Change effect:** Next bootstrap (re-runs `_phase_mcp_wire` which
re-probes + re-renders the `mcp_servers:` block).

## 6. Secrets env

**Path:** `/var/lib/hal0/secrets/agents/hermes.env`
**Mode:** `0600`
**Written by:** `_phase_voice_wire` (auto, for STT/TTS endpoints) +
operator (manual, for outbound credentials like HF tokens).
**Read by:** systemd `EnvironmentFile=-/var/lib/hal0/secrets/agents/hermes.env`
in PR-5's `hal0-agent-hermes.service` unit; Hermes itself reads via
`os.environ`.

Outbound-only by convention (ADR-0012): inbound auth is hal0-side
(X-hal0-Agent header set by the wrapper). Anything that ships out to
HuggingFace / external MCP servers / OpenAI-compatible providers lives
here.

**Change effect:** Hermes restart (systemd doesn't watch env files).

## 7. Provision checkpoint

**Path:** `/var/lib/hal0/state/agents/hermes/provision.json`
**Written by:** Bootstrap orchestrator after every phase.
**Read by:** Bootstrap orchestrator (skip already-`ok` phases) and
`hal0 agent status` (pretty-prints to operator).

This is internal state, not config — hand-editing it is unsupported
and lies about phase outcomes. To force a re-run use
`hal0 agent bootstrap hermes --repair` or `hal0 agent reprovision
hermes --repair`.

## 8. Plugin manifests

**Paths:**

- `$HERMES_HOME/plugins/<name>/plugin.yaml` — agent-loop plugins (R3 §Plugin registration)
- `$HERMES_HOME/plugins/<name>/dashboard/manifest.json` — dashboard UI plugins
- `/var/lib/hal0/agents/hermes/plugins/memory/<name>/__init__.py` — memory providers (special-case discovery)

**Written by:** `_phase_install` (for the hal0-bundled set) or
operator drops (for custom plugins).
**Read by:** Hermes plugin discovery (`hermes_cli/plugins.py`) at
process startup; hal0's plugin host (PR-7) at dashboard load.

The `plugins.enabled` allowlist in `config.yaml` gates which plugins
actually load — having a `plugin.yaml` on disk isn't enough.

**Change effect:** Hermes restart picks up new plugins; new dashboard
plugins load on dashboard refresh.

## How a change flows

When the operator edits a persona TOML:

1. `vim /var/lib/hal0/agents/hermes/personas/hermes.toml`
2. `hal0 agent personas activate hermes` — atomically swaps active.txt
   + nudges running Hermes via JSON-RPC `reload.env`. (Or do nothing;
   the next reprovision picks it up.)
3. Optional: `hal0 agent reprovision hermes` if you want the config.yaml
   regenerated NOW (e.g. to inspect the prelude that landed).

When the operator edits `overrides.yaml`:

1. `vim /etc/hal0/agents/hermes/overrides.yaml`
2. `hal0 agent reprovision hermes` — re-renders config.yaml; overlay
   deep-merges on top.
3. `systemctl restart hal0-agent-hermes` (PR-5) — Hermes picks up the
   new config on startup.

When `_phase_mcp_wire`'s probe surfaces a newly-reachable server:

1. Phase 6 captures the live probe result in `provision.json[mcp_wire].details.rendered_servers`.
2. Phase 9 (`model_automap`) re-renders `config.yaml` with the new
   `mcp_servers:` block; hash mismatch triggers the atomic swap.
3. Hermes JSON-RPC `reload.env` (next agent turn) picks up the
   addition — no Hermes process restart needed.

## Chat surface (v0.3 PR-9 + PR-10)

The dashboard chat composer talks to Hermes through hal0-api's chat
proxy, not directly. The proxy is the only browser-facing seam — Hermes
itself binds 127.0.0.1:9119 inside the `hal0-agent@hermes.service`
sandbox.

### WebSocket endpoints (browser → hal0-api → hermes)

| Endpoint | Direction | Purpose |
|---|---|---|
| `WS /api/agents/{id}/events` | server → client | mirror of `hermes /api/events` JSON-RPC bus (`message.delta`, `message.complete`, `tool.progress`, `tool.complete`, `persona.switched`, …). `tool.progress` is coalesced server-side at 100ms; non-progress frames pass through unchanged so the progress-before-complete ordering invariant holds. |
| `WS /api/agents/{id}/submit` | bidi | browser sends JSON-RPC `prompt.submit`, `approval.respond`, `clarify.respond`; proxy forwards verbatim, returns the hermes ack frame. |

### REST shims (browser → hal0-api → hermes)

| Endpoint | Maps to |
|---|---|
| `GET  /api/agents/{id}/session/handshake` | mints the HMAC session cookie + returns the embed token shape the browser needs to render the composer |
| `POST /api/agents/{id}/session/create`    | hermes `session.create` |
| `POST /api/agents/{id}/session/resume`    | hermes `session.resume` |
| `GET  /api/agents/{id}/session/history`   | hermes `session.history` |

### Composer keybindings

* **Enter** — submits the current text as a `prompt.submit` JSON-RPC
  frame. The text input clears on send; the transcript appends the
  user message immediately and renders streaming deltas as they arrive.
* **Shift+Enter** — inserts a newline in the textarea without
  submitting.
* **Ctrl/Cmd+K** — focuses the composer (any pane).

### Security baseline

* WS upgrades require both a permitted `Origin` header AND a valid
  HMAC session cookie (see ADR-0019 §2).
* Embed token in `Authorization: Bearer …` on the outbound hop to
  hermes; never the query string.
* uvicorn access log scrubs query strings.

### Hot-reload semantics

* **Persona swap** — `POST /api/agents/{id}/personas/{pid}/activate`
  writes `active.txt` AND POSTs a JSON-RPC `reload.env` to hermes.
  System-prompt scope swaps on the next user turn; in-flight turn
  continues with the old persona.
* **Service restart** — `POST /api/agents/{id}/restart` (PR-11)
  invokes `systemctl restart hal0-agent@{id}.service`. Used by the
  SidebarAgentBlock service chip when a hot reload isn't enough
  (e.g. the persona changes the tool allowlist and hermes needs a
  fresh plugin load).

## OpenRouter OAuth (deferred to V1)

Wiring the bundled Hermes agent to use OpenRouter as a registered
upstream is gated behind the V1 (Phase 1) OpenRouter integration PR.
Phase 0 ships only the architectural scaffold:

- **ADR-0020** (`docs/internal/adr/0020-localhost-callback-only-oauth-pkce.md`)
  documents why the OAuth PKCE callback URL is constrained to
  `http://127.0.0.1:<port>/api/openrouter/auth/callback`. ADR-0012
  removed every other auth surface; the callback is the one credential
  surface we re-introduce, and we keep it off the LAN so the
  trust-the-LAN posture still holds.
- **Operator note** — when V1 lands, completing the OAuth handshake
  requires either a browser tab running on the hal0 host itself or an
  SSH tunnel forwarding the laptop's `127.0.0.1:8080` to hal0's
  `127.0.0.1:8080`. Plan for this in onboarding flows that assume a
  remote browser (e.g. `hal0.thinmint.dev`).
- **Storage shape** — V1 will persist the OR refresh token + access
  token to
  `/var/lib/hal0/agents/{id}/personas/{pid}/openrouter.toml` (chmod
  `0600`), matching the protections on the existing `runtime.json`.

The route skeleton at `/api/openrouter/auth/callback` is registered as
of Phase 0 and returns HTTP 501 with a pointer to ADR-0020 so V1's PR
can fill in the exchange flow against a baseline that already enforces
the loopback guard.

## See also

- [Hermes-Agent bootstrap](./hermes-bootstrap.md) — the 12-phase pipeline that touches surfaces #1-#7
- [Identity model](./identity.md) — how `X-hal0-Agent` flows through `mcp_servers.*.headers`
- [MCP client](./mcp-client.md) — what `mcp_wire` validates
- [`SERVICE.md`](./SERVICE.md) — `hal0-agent@.service` unit + restart endpoint
- ADR-0013 — agent-installer-managed MCP allowlist contract
- ADR-0019 — v0.3 integration roll-up
- ADR-0020 — localhost-callback-only OAuth PKCE (OpenRouter prereq)
