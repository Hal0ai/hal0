# Hermes-Agent bootstrap plan (hal0 v0.3)

**Status:** Draft 2 — post-grilling, decisions resolved.
**Date:** 2026-05-23.
**Companion docs:**
- `docs/internal/hermes-upstream-map-2026-05-23.md` — upstream surface catalogue.
- `docs/internal/hermes-env-probe-recipes-2026-05-23.md` — env-detection recipes.
- `docs/internal/adr/0011-agent-identity-cards.md` — ADR for the identity-card convention (stub).

---

## Draft 2 changelog (2026-05-23, post-grilling)

| Was (Draft 1) | Is (Draft 2) | Source |
|---|---|---|
| MCP-server path for memory; promote to plugin in v0.4 | **`Hal0MemoryProvider` plugin from day one** (MCP server kept in parallel for operator overrides) | Q1 |
| `HERMES_HOME=/var/lib/hal0/agents/hermes/home/` | `HERMES_HOME=/var/lib/hal0/agents/hermes/` (drop `/home/` suffix); bootstrap state files relocated to `/var/lib/hal0/state/agents/hermes/` (outside HERMES_HOME) | Q2 |
| Bearer token at `/etc/hal0/agents/hermes/secrets.env` | Bearer token at `/var/lib/hal0/secrets/agents/hermes.env` (0600, hal0:hal0), wrapper-sourced; + `hal0 agent rotate-token hermes` CLI stub | Q3 |
| `model.provider: custom` (upstream profile) | `model.provider: hal0` (**`Hal0Profile` plugin**, hal0-owned) | Q4 |
| Identity cards in `shared` dataset, YAML in `text` | Identity cards in **dedicated `agents` dataset**, summary in `text` + structured fields in `metadata`, immutable + cleaned on uninstall | Q5 |
| Embed/rerank possibly wired in v0.4 | **Not wired in v0.3**; fastembed CPU stays for Cognee; both reserved as v0.4 follow-ups in ADR-0011 | Q6 |
| `pip install --user hermes-agent` into `/var/lib/hal0/agents/hermes/venv/` | hal0-managed venv at **`/var/lib/hal0/venvs/hermes/`** with explicit Python 3.11; **hard pin** in `installer/agents/hermes/requirements.txt`; `--to=<version>` flag for opt-in upgrades; plugins packaged inside hal0 wheel + copied at bootstrap | Q7 |
| `on_session_start` hook with 5s timeout | Tightened to **2s timeout**, light JSON dump only | Q8c |
| Module: `src/hal0/agents/hermes_bootstrap.py` | Module renamed to **`src/hal0/agents/hermes_provision.py`** (avoids soft collision with upstream's Windows-UTF8 `hermes_bootstrap.py`); CLI verb stays `hal0 agent bootstrap hermes` | Q8f |
| 14 open decision points in §21 | Status markers added; all 14 + bonus naming resolved | grilling |

---

## 0. TL;DR

When a hal0 user runs `hal0 agent install hermes`, today we install the
upstream package and write a 4-line env file. That's it. The agent boots
into a generic Hermes session with no idea it lives on hal0, no
knowledge of local models, no memory connection, no homelab context.

This plan replaces that 4-line shim with an **idempotent, checkpointed
bootstrap state machine** that turns the freshly-installed Hermes into a
hal0-native homelab admin: it probes its environment, enumerates every
live model, claims a memory namespace, learns the persona of "right-hand
admin for this specific box," and registers itself as discoverable to
peer agents (Claude Code, pi-coder, future agents).

The bootstrap is:
- one new Python module — `src/hal0/agents/hermes_provision.py` (~400 LOC).
- two hal0-owned Hermes plugins coupled to upstream ABCs:
  `Hal0MemoryProvider` (`agent/memory_provider.py` ABC) and `Hal0Profile`
  (`providers/base.py` ABC). Packaged inside the hal0 wheel, copied
  into `$HERMES_HOME/plugins/` by bootstrap. ~200 LOC.
- one new shell stage (`installer/agents/hermes-bootstrap.sh`).
- one new CLI subcommand (`hal0 agent bootstrap hermes`).
- a state file at `/var/lib/hal0/state/agents/hermes/provision.json`
  (outside HERMES_HOME — Hermes owns its own tree).

Total new code: ~700 LOC.

We add zero new MCP transports, **zero forks of upstream**, zero new
daemons. Every wiring decision uses surfaces that already exist in
hal0 v0.2 (`hal0-admin` MCP, `hal0-memory` MCP, `/api/models`,
`/api/capabilities`, `/api/hardware`).

---

## 1. Goals & non-goals

### Goals

1. **Idempotent.** Re-runnable. Detects partial state. Repair flag.
2. **Hardware-aware.** Knows it's on Strix Halo iGPU + XDNA2 NPU + UMA;
   knows it's in an LXC with apparmor-unconfined; knows it has ~96 GiB
   unified pool.
3. **Model-aware.** Every active slot in `/etc/hal0/capabilities.toml`
   becomes a `model_aliases` entry. Embed/rerank/STT/TTS slots wired
   into their respective Hermes subsystems.
4. **Memory-connected.** `hal0-memory` MCP registered as an MCP server.
   Bootstrap claims `private:hermes-agent` namespace; writes an identity
   card; reads/queues peer agents.
5. **Context-aware.** `/etc/hal0/HERMES.md` and `/etc/hal0/agent-skills/`
   exist; `terminal.cwd = /etc/hal0` so Hermes auto-injects them every
   session.
6. **Discoverable.** Other agents (Claude Code, pi-coder) can find
   Hermes via the memory namespace registry and via
   `hermes mcp serve` if exposed.
7. **Re-derivable.** No bootstrap output is "the source of truth." Every
   line of config.yaml can be regenerated from hal0 state at any time.

### Non-goals (v0.3)

- ❌ Federating hal0-memory across mem0 / Supermemory / etc. (Phase 9 / ADR-0006).
- ❌ Wiring `hermes-agent-self-evolution` into the loop. (Out of scope;
  separate repo, opens PRs to upstream — not a bootstrap concern.)
- ❌ Exposing `embed()` / `rerank()` as agent-callable tools (deferred to v0.4 per ADR-0011).
- ❌ Switching Cognee's embedder from fastembed CPU → bge-on-iGPU
  (deferred to v0.4; v0.3 alpha memory volumes don't justify the runtime
  slot dependency).
- ❌ Modifying upstream `hermes-agent`. No fork. No patches. Everything
  ships outside `$HERMES_HOME`'s upstream-owned files — but we DO ship
  two hal0-owned plugins coupled to stable upstream ABCs.
- ❌ Multi-user. Single hal0 host owner = single Hermes identity.

---

## 2. Upstream anchors (the don't-fight list)

From the upstream map, these are the surfaces we MUST use as-is. Fighting
them is what makes our current install fragile.

| Anchor | What we honor |
|---|---|
| `HERMES_HOME` is the single root of truth | We pin it to `/var/lib/hal0/agents/hermes/` and propagate it via the wrapper + systemd `Environment=`. |
| `config.yaml` is the persistent store, NOT `.env` | All non-secret config goes in `config.yaml`. `.env` only for token secrets. |
| `hermes setup` overwrites `model.*` + `terminal.*` | We **never** call `hermes setup` after our bootstrap. We invoke `_run_first_time_quick_setup()` once at the start (or skip entirely) and own `config.yaml` write order ourselves. |
| `mcp add` writes to `config.yaml` | MCP registration happens AFTER all `config.yaml` writes. Bootstrap order is strict. |
| `skills.external_dirs` is read-only | Hal0-managed skills land in `/etc/hal0/agent-skills/`; the agent's own learned skills stay in `$HERMES_HOME/skills/`. |
| `custom` provider profile is the local-server escape hatch | Use `provider: custom` + `base_url: http://127.0.0.1:8000/api/v1`. We do not need a custom plugin in v0.3. |
| Context files auto-inject from cwd | Set `terminal.cwd = /etc/hal0`; drop `HERMES.md` there. |
| `delegate_task` is Hermes's subagent surface | We don't try to make Hermes call Claude Code. We let Hermes use its own delegation primitive when it needs subagents. |

---

## 3. State machine

The bootstrap is a deterministic sequence of named phases. Each phase
writes a checkpoint into `/var/lib/hal0/state/agents/hermes/provision.json`.
On re-run, the bootstrap reads the checkpoint and skips any phase already
marked `ok` (unless `--repair` or the phase's inputs have changed).

```
preflight → install → env_probe → home_init → config_write
   → mcp_wire → context_link → namespace_register → model_automap
   → voice_wire → smoke_tests → self_report
```

Each phase is a function in `hermes_provision.py`:

```python
def _phase_preflight(state: BootstrapState) -> PhaseResult: ...
def _phase_install(state: BootstrapState) -> PhaseResult: ...
def _phase_env_probe(state: BootstrapState) -> PhaseResult: ...
...
PHASES = [
    ("preflight",         _phase_preflight),
    ("install",           _phase_install),
    ("env_probe",         _phase_env_probe),
    ("home_init",         _phase_home_init),
    ("config_write",      _phase_config_write),
    ("mcp_wire",          _phase_mcp_wire),
    ("context_link",      _phase_context_link),
    ("namespace_register",_phase_namespace_register),
    ("model_automap",     _phase_model_automap),
    ("voice_wire",        _phase_voice_wire),
    ("smoke_tests",       _phase_smoke_tests),
    ("self_report",       _phase_self_report),
]
```

Phase contract:
- **Input:** `state` (in-memory dataclass mirroring provision.json).
- **Output:** `PhaseResult(status=ok|skip|fail|repair_needed, details=...)`.
- **Side effects:** writes to filesystem + hal0-memory; never to network
  outside of localhost / 10.0.1.x LAN.
- **Idempotency:** the phase is responsible for detecting "already done"
  via content hash, not just a checkpoint flag.

### State file shape

State lives at `/var/lib/hal0/state/agents/hermes/provision.json` —
**outside** `$HERMES_HOME` so Hermes doesn't trample it.

```json
{
  "version": 1,
  "started_at": "2026-05-23T14:00:00Z",
  "completed_at": null,
  "hal0_version": "0.2.0-alpha.3",
  "hermes_version": "0.14.0",
  "hermes_home": "/var/lib/hal0/agents/hermes",
  "venv": "/var/lib/hal0/venvs/hermes",
  "secrets_file": "/var/lib/hal0/secrets/agents/hermes.env",
  "phases": {
    "preflight":          {"status":"ok","at":"...","hash":"abc..."},
    "install":            {"status":"ok","at":"...","hash":"..."},
    "env_probe":          {"status":"ok","at":"...","snapshot_path":"env-2026-05-23T14:00:01Z.json"},
    "home_init":          {"status":"ok","at":"..."},
    "config_write":       {"status":"ok","at":"...","yaml_hash":"..."},
    "mcp_wire":           {"status":"ok","at":"...","servers":["hal0-admin","hal0-memory"]},
    "context_link":       {"status":"ok","at":"...","links":[...]},
    "namespace_register": {"status":"ok","at":"...","memory_id":"mem_..."},
    "model_automap":      {"status":"ok","at":"...","aliases_written":["primary","embed","rerank","stt","tts","img"]},
    "voice_wire":         {"status":"skip","reason":"no stt/tts slot configured"},
    "smoke_tests":        {"status":"ok","at":"...","results":{...}},
    "self_report":        {"status":"ok","at":"...","summary_id":"mem_..."}
  },
  "errors": []
}
```

---

## 4. Phase A: Preflight (`_phase_preflight`)

**What it checks:**
- Python 3.11+ available (`sys.version_info`).
- `hal0` daemon reachable at `http://127.0.0.1:8080/api/status` (200 OK).
- `HAL0_BEARER_TOKEN` available (from systemd creds or `/etc/hal0/auth.env`).
- `/etc/hal0/` writable by the daemon user.
- `/var/lib/hal0/agents/` writable.
- Disk: ≥ 4 GiB free under `/var/lib/hal0/`.
- Network: can reach `pypi.org` (for `pip install hermes-agent`) — or
  detect an `--offline` flag with a pre-downloaded wheel cache.

**Failure mode:** hard fail. Surface to the dashboard's first-run wizard
with a concrete remediation hint per failure (`hal0 daemon not running:
sudo systemctl start hal0`).

**Why first:** preflight failures are 90% of "install succeeded but
agent broken" tickets. Detect early.

---

## 5. Phase B: Install (`_phase_install`)

**What it does:**
1. Detects if a hal0-managed Hermes venv already exists at
   `/var/lib/hal0/venvs/hermes/` AND `hermes version` matches the pin.
2. If absent or stale: `python3.11 -m venv /var/lib/hal0/venvs/hermes/`
   (explicit Python 3.11), then `pip install -r installer/agents/hermes/requirements.txt`
   (hard-pinned `hermes-agent==<pinned>`).
3. Symlinks `/var/lib/hal0/venvs/hermes/bin/hermes` to `/usr/local/bin/hermes`.
4. Copies the existing `installer/wrappers/hal0-hermes` wrapper to
   `/usr/local/bin/hal0-hermes`. Wrapper sources
   `/var/lib/hal0/secrets/agents/hermes.env` and exports
   `HERMES_HOME=/var/lib/hal0/agents/hermes` before exec.
5. Copies the two hal0-owned plugins from the hal0 wheel's package data
   into `$HERMES_HOME/plugins/`:
   - `plugins/memory/hal0-memory/__init__.py` → `Hal0MemoryProvider`
   - `plugins/model-providers/hal0/__init__.py` → `Hal0Profile`
6. Drops a systemd service template at
   `/etc/systemd/system/hal0-hermes.service` (Type=simple, runs
   `hal0-hermes` headless for `mcp serve` mode — see §11).

**Pinning policy:** hard pin in `installer/agents/hermes/requirements.txt`
(`hermes-agent==0.14.0` exact). Bumping requires a hal0 release; upgrade
gate is `hal0 agent upgrade hermes [--to=<version>]`. The `--to` flag
exists for power users + our own compat testing.

---

## 6. Phase C: Env probe (`_phase_env_probe`)

**Purpose:** capture a snapshot of the hal0 host environment so every
subsequent phase can reference it.

**Source of truth:** `hal0-admin` MCP, NOT direct probing. We don't
re-implement env discovery in Hermes-land — we reuse what hal0 already
exposes.

**Calls made (via `hal0-admin` MCP, autonomous-read tools):**
- `hardware_probe` → CPU, RAM, GPU, NPU, platform, is_uma.
- `version_info` → hal0 version, build hash.
- `capability_list` → which capability slots are configured.
- `slot_list` → which slots are currently running and on what backend.
- `provider_list` → upstream providers (if any are wired).

**Additionally probed locally** (recipes from env-probe doc):
- Container layer via `systemd-detect-virt --container`.
- `/dev/accel/accel0` presence (NPU device node, the LXC-correct check —
  `modinfo amdxdna` lies in containers).
- KFD `gfx_target_version` → confirm `gfx1151` (Strix Halo).
- Default route + DNS reachability of hal0 services.
- Docker socket presence (so Hermes knows if it can use `terminal.backend = docker`).

**Output:** writes `/var/lib/hal0/agents/hermes/env-<timestamp>.json` AND
keeps the latest summary in `provision.json["phases"]["env_probe"]`.

**Why NOT in Hermes:** Hermes shouldn't be the one running `xrt-smi`
because it might be running outside an LXC with NPU passthrough. The
authoritative answer is hal0's, captured at hal0's vantage point.

**Reusable hal0-admin MCP tools to add (from env-probe agent's recos):**
- `gpu_target_version()`
- `npu_status()`
- `model_store_probe(path)`
- `env_report()` — full dataclass dump.

These belong in `src/hal0/mcp/admin.py` so the dashboard, the
Lemonade onboarding wizard, and Hermes bootstrap all consume one
implementation.

---

## 7. Phase D: Home init (`_phase_home_init`)

**What it does:**
1. Creates `/var/lib/hal0/agents/hermes/` if absent (this is
   `$HERMES_HOME`).
2. Creates standard subdirs: `memories/`, `skills/`, `plugins/`,
   `plugins/memory/`, `plugins/model-providers/`, `logs/`,
   `sessions/`, `profiles/`, `mcp-tokens/`.
3. Sets ownership/mode (`hal0:hal0`, 0750).
4. Drops a marker file `.hal0-managed` so we don't accidentally clobber a
   user's pre-existing `~/.hermes`.
5. Writes `$HERMES_HOME/SOUL.md` from a hal0-bundled persona template
   (see §7.1).
6. Copies plugin sources from the hal0 wheel's package data:
   - `installer/agents/hermes/plugins/hal0-memory/` →
     `$HERMES_HOME/plugins/memory/hal0-memory/`
   - `installer/agents/hermes/plugins/hal0/` →
     `$HERMES_HOME/plugins/model-providers/hal0/`

### 7.1 SOUL.md template

We ship `installer/agents/hermes/SOUL.md.j2` as a Jinja2 template
parameterized by env-probe output. Rendered example:

```markdown
# Identity

You are the hal0 admin agent — the right-hand assistant for this
specific homelab inference platform. You live on a {{ platform }} host
({{ hostname }}) with:

- {{ cpu_model }} ({{ cpu_threads }} threads)
- {{ ram_total_gib }} GiB of {{ memory_topology }} RAM
{% if npu_present %}- AMD XDNA2 NPU ({{ npu_name }}){% endif %}
{% if gpu_name %}- {{ gpu_name }} ({{ gpu_arch }}){% endif %}
- Container layer: {{ container_type }}{% if container_type == "lxc" %} (privileged + apparmor-unconfined){% endif %}

# Operating principles

1. Probe before you change. Use `hal0_admin:slot_status` before
   touching anything; prefer `--dry-run` when offered.
2. Cite exact paths, ports, and slot names. "Lemonade primary at
   127.0.0.1:13305" beats "the chat model."
3. You have a memory MCP at `hal0_memory`. Use it for durable facts
   about this host. Don't ask the user the same question twice.
4. Other agents may share this memory (Claude Code, pi-coder).
   Respect their namespaces; never overwrite their identity cards.
5. Default to the smallest model that will work for a task. The utility
   slot on the NPU exists for cheap work; the primary on the iGPU is
   for reasoning-heavy turns.

# Boundaries

- Never reach outside the LAN without explicit user instruction.
- Never edit `/etc/hal0/capabilities.toml` directly; go through
  `hal0_admin:capability_set` so the orchestrator reconciles.
- Never run `hermes setup` — it will undo your config.
```

(Full template parameterized; falls back to upstream `DEFAULT_SOUL_MD`
on render failure.)

---

## 8. Phase E: Config write (`_phase_config_write`)

**What it does:** atomic write of `$HERMES_HOME/config.yaml` from a
hal0-rendered template + env-probe data.

**Order is critical** (per upstream gotcha §9): all config keys go in
ONE write. We never call `hermes config set` for the initial wiring —
that's slow and racy. We render the whole YAML, write to a tmpfile,
fsync, rename.

### Rendered config.yaml (rough shape)

```yaml
# Managed by hal0. Edits MAY be overwritten by `hal0 agent bootstrap hermes`.
# Manual overrides: drop a hal0-override.yaml beside this; bootstrap merges.

model:
  default: "{{ primary.model_id }}"          # from /etc/hal0/slots/primary.toml runtime
  provider: "hal0"                            # the Hal0Profile plugin
  base_url: "{{ primary.backend_url }}"       # from /v1/health.loaded[0]
  context_length: {{ primary.context_length }}

providers:
  hal0:
    request_timeout_seconds: 300
    stale_timeout_seconds: 900

model_aliases:
{% for slot in slots if slot.capability == "chat" %}
  {{ slot.alias }}:
    model: "{{ slot.model_id }}"
    provider: hal0
    base_url: "{{ slot.backend_url }}"
{% endfor %}

memory:
  provider: "hal0-memory"   # Hal0MemoryProvider plugin (Path B — native injection)
  memory_enabled: true
  user_profile_enabled: true
  nudge_interval: 10

mcp_servers:
  # MCP servers stay registered as a parallel surface for operator
  # overrides (e.g., the user manually invoking memory_delete). The
  # agent loop goes through the Hal0MemoryProvider plugin.
  hal0-admin:
    url: "http://127.0.0.1:8080/mcp/admin"
    headers:
      Authorization: "Bearer ${HAL0_BEARER_TOKEN}"
    timeout: 60
  hal0-memory:
    url: "http://127.0.0.1:8080/mcp/memory"
    headers:
      Authorization: "Bearer ${HAL0_BEARER_TOKEN}"
      X-hal0-Private: "1"           # opt into private:hermes-agent namespace
    timeout: 30

skills:
  external_dirs:
    - "/etc/hal0/agent-skills"
    - "/var/lib/hal0/skills"
  creation_nudge_interval: 15

terminal:
  backend: "local"
  cwd: "/etc/hal0"                  # auto-injects HERMES.md and AGENTS.md

agent:
  max_turns: 60
  reasoning_effort: "medium"

display:
  bell_on_complete: false
  show_reasoning: false

# Auxiliary model routing — reuse primary by default
auxiliary:
  vision:    { provider: "main", model: "" }
  web_extract: { provider: "main", model: "" }
  session_search: { provider: "main", model: "" }

# Voice — only emitted if /etc/hal0/slots/{stt,tts}.toml are enabled
{% if stt %}
stt:
  provider: "openai"
{% endif %}
{% if tts %}
tts:
  provider: "openai"
{% endif %}

# Hooks — wire hal0 events into Hermes
hooks:
  on_session_start:
    - command: "/usr/lib/hal0/hermes-hooks/inject-system-state.sh"
      timeout: 5
```

`.env` (secrets only):

```
HAL0_BEARER_TOKEN={{ token }}                 # required for MCP auth
{% if stt %}STT_OPENAI_BASE_URL={{ stt.backend_url }}
VOICE_TOOLS_OPENAI_KEY=dummy{% endif %}
GITHUB_TOKEN=                                  # optional; user-supplied
```

**Override file:** if `/etc/hal0/agents/hermes/overrides.yaml` exists,
deep-merge it INTO our rendered config on top. This is the user escape
hatch — anything they put there survives re-bootstrap.

---

## 9. Phase F: MCP wire (`_phase_mcp_wire`)

**What it does:**
- Verifies `hal0-admin` and `hal0-memory` MCP servers respond to a
  `tools/list` call.
- Verifies `Authorization: Bearer` + (for memory) `X-hal0-Private: 1`
  produce a `private:hermes-agent` namespace on first write.
- Records the discovered tool surface (the live list of `memory_*`,
  `slot_*`, `model_*` tool names) into `provision.json` for the
  `namespace_register` and `model_automap` phases to consume.

**No `hermes mcp add` call** — we already wrote `mcp_servers` directly
into `config.yaml`. Calling `add` would just edit YAML we already own.

**Failure mode:** if Hermes can't reach the MCP servers but they ARE up
(verified via curl from hal0 side), we record the failure and continue.
Smoke tests will surface it.

---

## 10. Phase G: Context link (`_phase_context_link`)

**What it does:**
1. Creates `/etc/hal0/agent-skills/` if absent (the shared skills root).
2. Symlinks every hal0-bundled skill from `/usr/share/hal0/skills/` into
   `/etc/hal0/agent-skills/`. (Bundled with the hal0 wheel under
   `package-data`.)
3. Renders `/etc/hal0/HERMES.md` from `installer/agents/hermes/HERMES.md.j2`
   — this is the cwd-injected context file (per upstream `prompt_builder.py`
   auto-injection rules).
4. Renders `/etc/hal0/AGENTS.md` — a generic agent-context file readable
   by Claude Code, Cursor, Codex, AND Hermes from the same directory.
5. Symlinks `/etc/hal0/HERMES.md` into `$HERMES_HOME/memories/HOST.md` so
   it ALSO appears in Hermes's memory tier (belt + braces).

### HERMES.md content (rendered)

Static enough to live in a template, parameterized by env-probe:

```markdown
# Where you are

- Host: {{ hostname }} ({{ container_type }})
- Platform: {{ platform }}
- hal0 version: {{ hal0_version }}
- Memory MCP: hal0_memory (auto-loaded; you have full read/write on private:hermes-agent)
- Admin MCP: hal0_admin (auto-loaded; you have autonomous read on all status tools)

# Active capability slots

{% for slot in slots %}
- **{{ slot.name }}** ({{ slot.capability }}/{{ slot.kind }}): {{ slot.model_id }} on {{ slot.device }}
  - {{ slot.backend_url }} → call via `model_aliases.{{ slot.alias }}`
{% endfor %}

# Peer agents

{{ peer_agents_summary }}    # filled in by namespace_register phase

# Skills

- Hal0-managed skills mirror at `/etc/hal0/agent-skills/`.
- Your own skills land in `$HERMES_HOME/skills/`.

# Conventions

- The dashboard at https://hal0.thinmint.dev shows live slot state.
- Never edit slot TOMLs directly — use `hal0_admin:capability_set`.
- Never run `hermes setup` — it will overwrite your model wiring.
```

---

## 11. Phase H: Namespace register (`_phase_namespace_register`)

**What it does:**
1. Calls `memory_add` on `hal0-memory` MCP with the agent identity
   card, targeting the **dedicated `agents` dataset** (not `shared`,
   not `private:*`). Tag = `agent-identity`. Card is **immutable** —
   written once at bootstrap, cleaned by `hal0 agent uninstall hermes`.
2. Calls `memory_search` to enumerate ANY other agent identity cards
   already in the `agents` dataset (Claude Code, pi-coder, future).
3. Builds a peer-agents summary, writes it into `provision.json` AND
   substitutes it into the `HERMES.md` `{{ peer_agents_summary }}`
   block (re-render `HERMES.md` post-discovery).

### Agent identity card schema (canonical — see ADR-0011)

Stored in the `agents` dataset, tag `agent-identity`. The `text`
field is a human-readable summary (surfaces well in `memory_search`
ranking). The structured payload lives in `metadata` so programmatic
readers don't have to YAML-parse a text blob.

```json
{
  "text": "I am Hermes, the hal0 admin agent. I have read/write access to the slot lifecycle and the memory store on this host. I can do generalist chat and code review on the LAN.",
  "tags": ["agent-identity", "hermes"],
  "dataset": "agents",
  "metadata": {
    "agent_id": "hermes-agent",
    "display_name": "Hermes (hal0 admin)",
    "namespace": "private:hermes-agent",
    "roles": ["homelab-admin", "generalist-chat", "memory-curator"],
    "endpoint": {
      "type": "mcp-serve",
      "url": "http://127.0.0.1:8081/mcp",
      "transport": "streamable-http"
    },
    "delegation": {
      "accepts_tasks_from": ["claude-code", "pi-coder", "user"],
      "max_concurrent": 3
    },
    "hal0_state": {
      "registered_at": "2026-05-23T14:00:00Z",
      "bootstrap_version": 1,
      "hal0_version": "0.2.0-alpha.3",
      "hermes_version": "0.14.0"
    }
  }
}
```

Discovery:

```json
memory_search({
  "query": "agent identity",
  "tags": ["agent-identity"],
  "dataset": "agents",
  "limit": 50
})
```

**Why `agents` not `shared` or `private`?** Service registry and
episodic memory are different concerns. Cognee will embed each card
but the dataset is small (5-10 cards forever) so the embed cost is
irrelevant. Search-by-similarity is a free side-benefit ("find an
agent that does X" works naturally). `private:*` is wrong because
identity is **public-by-design** — anything that wants to delegate to
Hermes needs to know it's there. `shared` is also wrong because that's
where episodic facts live; conflating concerns invites schema rot.

**Why immutable?** Cards are write-once. Liveness ("is the endpoint
reachable right now?") is **not** a stored field; the dashboard pings
the endpoint to check. Stale-card-on-uninstall is solved by the
uninstall CLI: `memory_delete(ids=[card_id])` is a one-line cleanup
step. Re-bootstrap on Hermes-version upgrade rewrites the card —
that's the only legitimate write besides install.

---

## 12. Phase I: Model automap (`_phase_model_automap`)

**What it does:** walks the output of `slot_list` + `model_list` and
writes `model_aliases` entries to `config.yaml`. Already partially done
in `_phase_config_write`, but this phase handles dynamic post-install
updates (e.g., user added a new chat slot — bootstrap re-run picks it up
without clobbering anything else).

### Mapping rule

| hal0 capability slot | Hermes config destination |
|---|---|
| `primary` (chat) | `model.default` + `model_aliases.primary` |
| Additional chat slots | `model_aliases.<slot-name>` |
| `embed` | **Not wired** — Hermes doesn't call `/v1/embeddings` directly. Memory MCP handles embedding internally via Cognee → fastembed. Recorded but unused. |
| `embed-rerank` | Same — no direct surface in Hermes. |
| `stt` | `stt.provider: openai` + `STT_OPENAI_BASE_URL` env. |
| `tts` | `tts.provider: openai` + analogous env. |
| `img` | `auxiliary.vision` if the model supports vision; otherwise unwired (image generation is not a Hermes core feature). |

**Why embed/rerank stay unwired:** per upstream map §3.8 — Hermes has no
top-level embeddings provider abstraction. Embeddings live inside
specific memory providers. Our memory path is MCP-based (hal0-memory
does Cognee → fastembed internally), so Hermes never needs to know.

**Decision point for grilling:** is that right, or do we WANT Hermes to
have a direct embed surface for RAG-style skills? If yes, we'd need to
ship a custom memory provider plugin and route embeds through it.
Currently leaning no — keep it simple.

---

## 13. Phase J: Voice wire (`_phase_voice_wire`)

Conditionally emits STT/TTS config IF the respective slots are
configured AND running:

```yaml
stt:
  provider: "openai"
  openai: { model: "moonshine-base" }   # or whatever the slot reports

tts:
  provider: "openai"
  openai: { model: "kokoro-default" }
```

Writes `STT_OPENAI_BASE_URL` and a placeholder `VOICE_TOOLS_OPENAI_KEY=dummy`
to `.env`.

Per memory `hal0_moonshine_toolbox_bug`: moonshine requires both
`models_dir` and `model_name` — but that's Lemonade's problem, not ours.
Hermes just sees an OpenAI-compatible endpoint.

---

## 14. Phase K: Smoke tests (`_phase_smoke_tests`)

Each test is a single call against a wired surface; failure is
**non-fatal** but recorded.

1. `hermes status` — process running.
2. `hermes doctor` — provider reachable, models endpoint returns the
   pinned model.
3. One `chat/completions` call against `model.default` ("Reply with the
   word 'ready'.") — assert "ready" in response.
4. `memory_add` + `memory_search` roundtrip on `hal0-memory` — assert
   the just-written doc comes back in search.
5. MCP `tools/list` against `hal0-admin` — assert ≥ 5 tools.
6. Read `/etc/hal0/HERMES.md` — assert it contains the active primary
   model id (proves rendering succeeded).

Failures land in `provision.json["errors"]` with a remediation hint
(`smoke_3_chat_completions_failed: check 'hal0 status primary'`).

---

## 15. Phase L: Self-report (`_phase_self_report`)

**What it does:** writes a bootstrap-completion summary into
`hal0-memory` under the `private:hermes-agent` namespace, then pings
the hal0 dashboard's agent-status endpoint so the UI surfaces "Hermes
bootstrapped ✓" in real time.

Memory write:

```
memory_add({
  "text": "Hermes-Agent bootstrap completed. Pinned to hermes 0.14.0 on hal0 0.2.0-alpha.3. Primary chat slot = Qwen3-30B-A3B-Instruct-2507 on gpu-vulkan (lemonade @ 127.0.0.1:13305). Memory MCP + admin MCP wired. 5 model aliases mapped. 0 smoke failures.",
  "tags": ["bootstrap", "self-report", "agent-identity"],
  "metadata": { "bootstrap_version": 1, "phase_results": {...} }
})
```

This memory becomes the first thing the agent recalls on next session
start (via the prefetch hook), giving it durable knowledge of when and
how it was provisioned.

---

## 16. CLI surface

New subcommands under `hal0 agent`:

```
hal0 agent bootstrap hermes [--repair] [--dry-run] [--skip-phase NAME] [--offline] [--verbose]
hal0 agent status hermes
hal0 agent log hermes [--phase NAME]
hal0 agent upgrade hermes [--to=<version>]
hal0 agent rotate-token hermes
hal0 agent uninstall hermes [--keep-memory]
```

- `bootstrap hermes` — runs phases A–L. Resumes from last successful
  phase unless `--repair`.
- `--dry-run` — renders config.yaml + HERMES.md to /tmp, prints diffs
  against current state, does not write.
- `--skip-phase` — runtime override for stuck phases (with warning).
- `status hermes` — pretty-print `provision.json`.
- `log hermes --phase env_probe` — dump per-phase logs from
  `/var/lib/hal0/state/agents/hermes/provision-logs/<phase>.log`.
- `upgrade hermes` — bumps the Hermes pin in the venv, re-runs bootstrap
  in `--repair` mode against the new version. `--to=<version>` is the
  power-user escape hatch (and what we use for compat testing).
- `rotate-token hermes` — mints a new bearer, writes
  `/var/lib/hal0/secrets/agents/hermes.env`, restarts the wrapper. The
  long-lived token policy in v0.3 makes this a manual operation; v1.0
  hardening will add scheduled rotation.
- `uninstall hermes` — removes venv, plugins, HERMES_HOME (unless
  `--keep-memory`), the identity card in the `agents` dataset, the
  systemd unit, and the bearer token file.

`hal0 agent install hermes` (existing) becomes a **thin wrapper** that
calls `bootstrap hermes` after the existing install. No behavior split
between install and bootstrap — bootstrap IS install.

---

## 17. Code map

### New files

| Path | Purpose | LOC |
|---|---|---|
| `src/hal0/agents/hermes_provision.py` | Phase orchestrator + each phase impl | ~400 |
| `src/hal0/agents/hermes_templates/SOUL.md.j2` | Persona template | ~80 |
| `src/hal0/agents/hermes_templates/HERMES.md.j2` | Cwd-injected context | ~60 |
| `src/hal0/agents/hermes_templates/AGENTS.md.j2` | Generic agent-context (shared with Claude Code etc.) | ~40 |
| `src/hal0/agents/hermes_templates/config.yaml.j2` | The full Hermes config render | ~100 |
| `installer/agents/hermes/requirements.txt` | Hard-pinned `hermes-agent==<v>` for the venv | ~5 |
| `installer/agents/hermes/plugins/hal0-memory/__init__.py` | `Hal0MemoryProvider(MemoryProvider)` plugin | ~150 |
| `installer/agents/hermes/plugins/hal0-memory/plugin.yaml` | Plugin manifest | ~10 |
| `installer/agents/hermes/plugins/hal0/__init__.py` | `Hal0Profile(ProviderProfile)` plugin | ~80 |
| `installer/agents/hermes/plugins/hal0/plugin.yaml` | Plugin manifest | ~10 |
| `installer/agents/hermes-bootstrap.sh` | Shell hook for the existing installer to call into Python | ~30 |
| `installer/agents/hermes/hooks/inject-system-state.sh` | Shell hook fired on session start; calls hal0-admin for fresh state (≤2s) | ~25 |
| `docs/internal/adr/0011-agent-identity-cards.md` | ADR for the identity card convention | ~120 |
| `tests/agents/test_hermes_provision.py` | Per-phase unit tests | ~250 |
| `tests/agents/test_hal0_memory_provider.py` | Plugin tests (ABC contract + HTTP roundtrip) | ~120 |
| `tests/agents/test_hal0_profile.py` | Provider plugin tests | ~80 |
| `tests/harness/scenarios/hermes_bootstrap.yaml` | δ-tier harness scenario | ~30 |

**Total new code:** ~1,580 LOC (was ~1,090 in Draft 1; Path B plugin +
provider plugin + tests added the delta).

### Modified files

| Path | Change |
|---|---|
| `src/hal0/agents/hermes.py` | Existing `HermesDriver.install()` calls into `hermes_provision.run()` after the venv install. The 4-line env file moves to `/var/lib/hal0/secrets/agents/hermes.env`; non-secret config moves to `config.yaml` rendered by Phase E. |
| `installer/agents/hermes-agent.sh` | After upstream venv install, exec the new bootstrap shell hook. |
| `installer/wrappers/hal0-hermes` | Source `/var/lib/hal0/secrets/agents/hermes.env`; export `HERMES_HOME=/var/lib/hal0/agents/hermes`; exec `/var/lib/hal0/venvs/hermes/bin/hermes`. |
| `src/hal0/mcp/admin.py` | Add reusable tools: `gpu_target_version`, `npu_status`, `model_store_probe`, `env_report` (per env-probe agent's recos). |
| `src/hal0/api/routes/installer.py` | New `POST /api/agents/hermes/bootstrap` route (so dashboard's first-run wizard can trigger it). |
| `docs/internal/adr/0004-agents.md` | Append §11 referencing this plan + the two hal0-owned plugin commitments. |
| `pyproject.toml` (hal0) | Add `installer/agents/hermes/plugins/**` to `package-data` so plugins ship inside the wheel. |

### Deleted/replaced

Nothing yet. The existing wrapper survives. ADR-0004 grows; doesn't get
rewritten.

---

## 18. Failure modes & recovery

| Failure | Detection | Recovery |
|---|---|---|
| `pip install hermes-agent` fails | `subprocess.run` non-zero | Retry with `--no-build-isolation`; surface PyPI outage via `hal0 agent log`. |
| `HERMES_HOME` already exists with a non-hal0 marker | absence of `.hal0-managed` file | Prompt user (dashboard) to choose: backup-and-overwrite vs use a sub-profile. |
| MCP servers unreachable | `tools/list` times out | Mark `mcp_wire` as `degraded`, continue. Next phase logs the warning. Smoke tests surface to user. |
| Lemonade primary slot not loaded | `slot_list` returns no `ready` chat slot | `config_write` falls back to a placeholder; bootstrap completes but `model.default` is unwired. Surface in self-report. |
| `hermes setup` accidentally re-run by user | Detect via `config.yaml` content hash mismatch | `bootstrap --repair` re-renders and writes; preserves overrides.yaml. |
| Bootstrap killed mid-phase | Checkpoint file inconsistency | Re-run resumes from last `ok` phase; the killed phase re-runs (must be idempotent). |
| User uninstalled and reinstalled hal0 | `hal0_version` mismatch in provision.json | Bootstrap detects, re-runs all phases. Memory namespace survives via Cognee's persistence. |

---

## 19. Idempotency rules

For each phase to be safely re-runnable:

1. **`config_write`** — write to tmpfile + atomic rename. Hash the
   rendered YAML; if hash matches current `config.yaml`, skip the write
   entirely (still mark phase `ok`).
2. **`context_link`** — symlinks use `os.symlink(target, link)` only
   when `os.readlink(link) != target`. Otherwise pass.
3. **`namespace_register`** — `memory_search` first by `agent_id` tag;
   if our identity card exists, `memory_delete` + `memory_add` so we
   refresh metadata instead of accumulating cards.
4. **`model_automap`** — diff the rendered `model_aliases` against the
   current; only `hermes config set` (or direct YAML rewrite) on
   changed entries.
5. **`smoke_tests`** — always re-run. They're cheap and verify the
   wiring still works.

---

## 20. Self-improvement loop integration (deferred to v0.4)

`NousResearch/hermes-agent-self-evolution` is a separate repo that uses
DSPy + GEPA to mutate prompts/skills and open PRs against
`hermes-agent`. It costs ~$2-10 per run and requires a configured
optimizer LLM.

**v0.3 stance:** mention it in HERMES.md (as a capability the user can
opt into), but do not wire automatically. The right time to do this is
after we ship v0.3 stable, see how the bootstrap holds up across a few
hundred real installs, and then have evolution target the hal0-specific
skills directory (`/etc/hal0/agent-skills/`).

**Forward-looking sketch (v0.4):**
- Fork to `Hal0ai/hermes-agent-self-evolution`.
- Wire to evolve the `agent-skills` corpus, not upstream skills.
- Schedule via `hal0 agent evolve hermes --weekly`.
- Gate evolution proposals on the test harness (`make harness`) before
  any auto-merge.

---

## 21. Open decision points — RESOLVED 2026-05-23 (post-grilling)

> All 14 + bonus naming question resolved. **See the changelog table at
> the top of this doc** for the final answers. This section is kept as
> historical record showing the trade-off space we walked through.

Original list (numbered for `/grill-me` to attack one at a time):

1. **`pipx` vs hal0-managed venv vs system pip.** Plan = venv. Reasoning:
   isolation + Python-version control. Cost: one more thing to maintain.

2. **MCP path vs MemoryProvider plugin path for memory.** Plan = MCP
   (Path A). Reasoning: zero new code on Hermes side; promote to a
   plugin later if MCP-tool-discoverability proves clunky. Risk: the
   model has to "remember" to call memory tools; a plugin would inject
   memory into every system prompt automatically.

3. **Use upstream `custom` profile vs ship `Hal0Profile` plugin.** Plan =
   `custom`. Reasoning: zero new code, works today. Cost: setup wizard
   won't show "hal0" as a first-class provider option (relevant only if
   we expect users to re-run `hermes setup`, which we don't).

4. **`hermes setup` — never call, or call with `--quick`?** Plan =
   never. Reasoning: it overwrites our work. Cost: skip the upstream's
   "is your provider reachable?" check; we re-implement that in
   smoke_tests.

5. **Bootstrap as separate CLI (`hal0 agent bootstrap hermes`) vs
   inside install.** Plan = SAME command; install wraps bootstrap.
   Reasoning: removes "I installed but didn't bootstrap" footgun.
   Cost: bootstrap failures fail install.

6. **`HERMES_HOME` location — `/var/lib/hal0/agents/hermes/home/` vs
   user-scoped (`~/.hermes` of the daemon user).** Plan = system path.
   Reasoning: survives user changes, single-host single-Hermes is the
   default model. Cost: doesn't support multi-user hal0 (out of scope).

7. **Identity card storage — `shared` dataset vs dedicated.** Plan =
   `shared` with `tag=agent-identity`. Reasoning: agent discovery is
   public-by-design. Cost: clutters the shared dataset; future
   federation may need a dedicated source.

8. **Hooks — opt-in or default-on?** Plan = `on_session_start` hook
   default-on (injects fresh state). Cost: every session pays a ~5s
   shell-script tax.

9. **Embed/rerank wiring — leave unwired, or build a skill that calls
   them via hal0-admin?** Plan = leave unwired in Hermes; the memory
   MCP handles embeddings internally. Risk: a skill author might want
   raw embed access; they'd have to call `/v1/embeddings` themselves via
   the terminal tool.

10. **Voice on by default, or opt-in?** Plan = conditional — only emit
    voice config if slots are configured. Reasoning: most users won't
    have STT/TTS in v0.3.

11. **Self-evolution opt-in path in v0.3, or fully deferred?** Plan =
    mention in HERMES.md only; no wiring. Cost: nothing.

12. **Should we also auto-bootstrap a Claude Code AGENTS.md at
    `/etc/hal0/AGENTS.md`?** Plan = yes, Phase G writes both HERMES.md
    AND AGENTS.md from a shared template. Reasoning: free win; any agent
    landing in `/etc/hal0/` gets the same context. Cost: ~40 LOC.

13. **Cognee namespace migration when v0.4 federates.** Plan = ignore
    for now; ADR-0006 (pending) will handle.

14. **Where does the bearer token come from on first install?** Plan =
    hal0 daemon mints a long-lived bearer for the Hermes agent on first
    install, stores it in `/etc/hal0/agents/hermes/secrets.env` (mode
    0600). Lifecycle managed by hal0, not the user.

---

## 22. What grilling will probably attack

Anticipated angles:

- **"What if Lemonade isn't started when bootstrap runs?"** → preflight
  catches it; phase fails with "start Lemonade first" hint.
- **"What if the user runs `hermes setup` after our bootstrap?"** → we
  detect via config.yaml hash diff in `bootstrap --repair`; we don't
  PREVENT it (would require monkey-patching upstream).
- **"What if two hal0 hosts share a memory store and Hermes is on
  both?"** → identity card by `agent_id: hermes-agent` collides. Plan
  punts; ADR-0006 will need a `host_id` qualifier when federation lands.
- **"Why not just write a Hermes plugin and skip all this?"** → because
  Hermes plugins are user-managed at `$HERMES_HOME/plugins/`; we want
  hal0 to OWN the bootstrap from outside, so upgrading Hermes doesn't
  blow it away.
- **"What about pi-coder, when it ships?"** → identity card convention
  is agent-agnostic. pi-coder gets its own bootstrap; uses the same
  `agent-identity` memory tag.

---

## 23. Timeline / ordering

If accepted post-grilling, sequence:

1. **PR-1:** ADR-0011 (agent identity cards) + the new MCP admin tools (`gpu_target_version`, `npu_status`, `env_report`, `model_store_probe`)
   (`gpu_target_version`, `npu_status`, `env_report`, `model_store_probe`).
2. **PR-2:** `hermes_provision.py` scaffold + provision.json schema +
   `preflight` + `install` + `home_init` phases.
3. **PR-3:** `env_probe` + `config_write` + `mcp_wire`.
4. **PR-4:** `context_link` + `namespace_register` + identity card
   convention.
5. **PR-5:** `model_automap` + `voice_wire`.
6. **PR-6:** `smoke_tests` + `self_report` + CLI subcommands.
7. **PR-7:** Test harness scenario + δ-tier coverage.
8. **PR-8:** Dashboard panel for agent status (consumes identity cards
   from `hal0-memory`).

Each PR ships behind a feature flag (`HAL0_HERMES_BOOTSTRAP_V1=1`) until
PR-8 lands and we flip the default.

---

## 24. Success criteria

The bootstrap is "done" when:

- ✅ `hal0 agent install hermes` on a fresh LXC produces a working,
  hal0-aware Hermes session with zero user prompts.
- ✅ `hermes` (bare CLI, no args) opens chat and the model's first
  utterance demonstrates knowledge of the host (e.g., "I'm running on
  your Strix Halo LXC; the primary slot is Qwen3-30B...").
- ✅ Hermes calls `hal0_admin:slot_status` autonomously when asked
  "is anything broken?" — and gets a real answer.
- ✅ `memory_search(tags=["agent-identity"])` returns Hermes's card and
  every peer agent's card.
- ✅ Re-running `hal0 agent bootstrap hermes` is a no-op (all phases
  skip).
- ✅ `hal0 agent bootstrap hermes --repair` after a manual config edit
  restores the canonical config.
- ✅ The harness scenario passes on every CI run.

---

## 25. Companions / follow-ups (not part of v0.3)

- pi-coder bootstrap (parallel design, reuses identity card + env
  probe).
- Claude Code agent integration (different — Claude Code doesn't
  install on the hal0 host typically, but we can drop an `AGENTS.md` at
  `/etc/hal0/` that travels with mount-bound sessions).
- Hermes `mcp serve` as a hal0 systemd unit, exposing Hermes itself as
  a callable MCP server for OTHER agents to delegate to. Currently in
  the install phase but unwired into anything; Phase 9 will use it.

---

**END DRAFT 1.** Ready for `/grill-me`.
