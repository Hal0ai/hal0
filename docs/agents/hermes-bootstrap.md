---
title: Hermes-Agent bootstrap
description: The 12-phase, checkpointed, idempotent state machine that turns a freshly-installed Hermes-Agent into a hal0-native homelab admin.
sidebar:
  order: 2
---

`hal0 agent install hermes` lays down the binary + wrapper. That's the
shim. **`hal0 agent bootstrap hermes`** is what turns the freshly-installed
Hermes into a hal0-native homelab admin — probing hardware, enumerating
models, claiming a memory namespace, writing its identity card, and
wiring every relevant slot into the right Hermes subsystem.

Full design doc: [`docs/internal/hermes-bootstrap-plan-2026-05-23.md`](../internal/hermes-bootstrap-plan-2026-05-23.md).

## What it does, in one paragraph

A 12-phase pipeline runs in deterministic order. Each phase is a pure
function `(BootstrapState) -> PhaseResult` and either `ok`, `skip`, or
`fail`. State persists to `provision.json` after every phase, so a
crash or `^C` re-runs from the first non-`ok` phase. No phase is
allowed to depend on bootstrap-time output for its inputs — every
config line can be re-derived from live hal0 state at any time.

Source: `src/hal0/agents/hermes_provision.py:1984` (the `PHASES` list).

## The 12 phases

| # | Phase                | What it does | Source |
|---|----------------------|--------------|--------|
| 1 | `preflight`          | Python ≥ 3.11, free disk ≥ 4 GiB, hal0 API health, upstream `hermes` on PATH (unless `--offline`). | `hermes_provision.py:225` |
| 2 | `install`            | Create hal0-managed venv at `/var/lib/hal0/venvs/hermes/`, install pinned `hermes-agent` wheel, copy hal0 plugin tree (`Hal0Profile`, `Hal0MemoryProvider`) into `$HERMES_HOME/plugins/`. | `:350` |
| 3 | `env_probe`          | Snapshot hardware (iGPU, NPU, UMA size), container (LXC + apparmor), tooling — feeds downstream phases. | `:524` |
| 4 | `home_init`          | Claim `HERMES_HOME=/var/lib/hal0/agents/hermes/` with a marker file; idempotent re-runs validate the claim. | `:465` |
| 5 | `config_write`       | Render `config.yaml` from the env probe + the live `/api/capabilities` snapshot; apply operator overrides from `/etc/hal0/agents/hermes/overrides.yaml`. | `:678` |
| 6 | `mcp_wire`           | Register `hal0-admin` + `hal0-memory` MCPs in Hermes's `mcp_servers` map; round-trip each one via JSON-RPC `tools/list`. Reads the per-agent allow-list at `/etc/hal0/agents/hermes.toml` (ADR-0013). | `:874` |
| 7 | `context_link`       | Symlink `/etc/hal0/HERMES.md` + `/etc/hal0/agent-skills/` into HERMES_HOME so Hermes injects them on every session start. | `:1031` |
| 8 | `namespace_register` | Write the immutable identity card into the `agents` Cognee dataset per [ADR-0011](../../internal/adr/0011-agent-identity-cards.md). Idempotent — re-bootstrap rewrites in-place. | `:1294` |
| 9 | `model_automap`      | Walk active slots, turn each into a `model_aliases` entry, point Hermes at the right backend URL per slot capability (primary / agent / embed / rerank / stt / tts). | `:1466` |
| 10 | `voice_wire`        | Wire STT / TTS slots into Hermes's voice subsystem if installed; skip otherwise. | `:1609` |
| 11 | `smoke_tests`       | Sanity round-trips: chat one token through `primary`, `memory_search` against `private:hermes-agent`, MCP `tools/list` against `hal0-admin`. | `:1896` |
| 12 | `self_report`       | Dump a summary to `provision-logs/self_report.json`; mark `completed_at` in `provision.json`. | `:1929` |

## The plugin model

hal0 ships two hal0-owned plugins coupled to upstream Hermes ABCs:

- **`Hal0Profile`** — implements Hermes's `providers/base.py` ABC.
  Exposes the hal0 API (`POST /v1/chat/completions`, `/v1/embeddings`,
  …) as Hermes's `model.provider: hal0`. No fork; the ABC is an
  upstream contract.
- **`Hal0MemoryProvider`** — implements Hermes's
  `agent/memory_provider.py` ABC. Bridges Hermes's memory API straight
  into `hal0-memory` via in-process calls. The MCP server is *also*
  kept registered as an operator override.

Both plugins are packaged inside the hal0 wheel and copied into
`$HERMES_HOME/plugins/` during the `install` phase. No upstream
modifications, no patches.

## Where state lives

| Path | Owner | Purpose |
|------|-------|---------|
| `/var/lib/hal0/venvs/hermes/` | hal0 installer | Pinned hermes-agent Python venv. |
| `/var/lib/hal0/agents/hermes/` (= `HERMES_HOME`) | Hermes upstream | Hermes's own tree — config.yaml, plugins, session state. |
| `/var/lib/hal0/state/agents/hermes/provision.json` | hal0 bootstrap | Checkpoint state machine; idempotency anchor. |
| `/var/lib/hal0/state/agents/hermes/provision-logs/` | hal0 bootstrap | Per-phase logs (`{phase}.log`, plus `self_report.json`). |
| `/etc/hal0/agents/hermes.toml` | Installer (editable) | Per-agent MCP allow-list (ADR-0013). |
| `/etc/hal0/agents/hermes/overrides.yaml` | Operator | Optional deep-merge over rendered `config.yaml`. |
| `/etc/hal0/HERMES.md` | Operator | Persona / homelab context (Markdown). |
| `/etc/hal0/agent-skills/` | Operator | Hermes skill directory (per-skill markdown). |

The strict separation between `/var/lib/hal0/state/agents/hermes/` and
`HERMES_HOME` keeps the upstream-owned tree clean — Hermes owns
everything under HERMES_HOME, hal0 owns everything under
`/var/lib/hal0/state/`. Operators can `rm -rf` either tree
independently for surgery.

## Running the bootstrap

```sh
# First-time bootstrap (after install).
hal0 agent bootstrap hermes

# Verbose phase-by-phase log to stdout.
hal0 agent bootstrap hermes --verbose

# Force a full re-run — every phase regardless of checkpoint.
hal0 agent bootstrap hermes --repair

# Skip a phase (repeatable). Use sparingly — downstream phases may
# assume the skipped one ran.
hal0 agent bootstrap hermes --skip-phase voice_wire

# Run phases but don't persist provision.json — useful for dry-run
# debugging without disturbing real state.
hal0 agent bootstrap hermes --dry-run

# Skip the PyPI preflight (used when the wheel is pre-staged on
# air-gapped hosts).
hal0 agent bootstrap hermes --offline
```

Source: `src/hal0/cli/agent_commands.py:514`.

## Reading the checkpoint

```sh
hal0 agent status hermes
```

Pretty-prints every phase's status, timestamp, and detail dict to a
table. Source: `src/hal0/cli/agent_commands.py:559`.

```sh
hal0 agent log hermes --phase mcp_wire
```

Dumps the matching `provision-logs/<phase>.log`. Source: `:588`.

## Debugging a failed phase

`PhaseResult` ships a free-form `details` dict — the failing phase
stashes its diagnostics there. The status table prints the first 60
chars; the per-phase log carries the rest.

Common patterns:

| Symptom | Likely phase | First place to look |
|---------|--------------|---------------------|
| `upstream hermes not found on PATH` | `preflight` | `command -v hermes`; install upstream Hermes first (`pip install --user hermes-agent`). |
| `Permission denied` on venv create | `install` | Re-run as the hal0 service user, or check `/var/lib/hal0/venvs/` perms. |
| MCP round-trip 404 | `mcp_wire` | hal0 API up? `curl http://127.0.0.1:8080/api/status`. |
| Identity card not visible to peers | `namespace_register` | See [issue #317](https://github.com/Hal0ai/hal0/issues/317) — `private:*` namespaces are currently flattened to `shared`; identity cards still write to `agents` and work. |
| `memory_roundtrip` smoke red | `smoke_tests` | Same as above — issue #317 is the standing cause for this smoke. |

## Idempotency contract

Re-running `hal0 agent bootstrap hermes` is **always safe**. The state
machine:

1. Loads `provision.json` if it exists.
2. For each phase in order, skips if already `ok` (or `skip`) and
   `--repair` was not passed.
3. Persists after every phase.

A `^C` between phases leaves you at a consistent boundary — the next
run picks up at the first non-`ok` phase. There is no "in-flight"
half-state.

## See also

- [`docs/internal/hermes-bootstrap-plan-2026-05-23.md`](../internal/hermes-bootstrap-plan-2026-05-23.md) — full design doc.
- [`docs/internal/hermes-upstream-map-2026-05-23.md`](../internal/hermes-upstream-map-2026-05-23.md) — upstream Hermes surface catalogue.
- [ADR-0011 — Agent identity cards](../../internal/adr/0011-agent-identity-cards.md) — what `namespace_register` writes.
- [ADR-0013 — MCP-client allow-list](../../internal/adr/0013-mcp-client-allow-list.md) — what `mcp_wire` reads.
