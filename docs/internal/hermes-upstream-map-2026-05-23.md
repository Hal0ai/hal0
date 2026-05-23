# Hermes-Agent upstream technical map (2026-05-23)

Reference for embedding **NousResearch/hermes-agent** as hal0's bundled
"right-hand admin agent." Goal: every surface area we need to wire to
hal0 (model providers, memory, MCP, skills, persona, bootstrap) is
catalogued with concrete file paths and config keys.

> **Disambiguation.** "Hermes" at NousResearch refers to two distinct
> things. The **Hermes LLM family** (Hermes 3, Hermes 4 etc.) is a series
> of fine-tuned open-weight chat models. The **Hermes Agent** is the
> Python-based "self-improving" agent framework — `pip install hermes-agent`,
> CLI `hermes`. The agent framework is what this doc maps. It is model-
> agnostic and does NOT require Nous's own models.

## 0. Canonical sources

| Repo | URL | What's in it | Latest |
|------|-----|--------------|--------|
| **`NousResearch/hermes-agent`** | https://github.com/NousResearch/hermes-agent | The agent framework itself (Python 3.11+, MIT). | **v0.14.0 / `v2026.5.16`** released 2026-05-16. Default branch `main`, pushed 2026-05-23. |
| `NousResearch/hermes-agent-self-evolution` | https://github.com/NousResearch/hermes-agent-self-evolution | DSPy + GEPA prompt/skill evolution pipeline. PRs against the main repo. NOT bundled with `pip install hermes-agent`. | active. |
| `NousResearch/hermes-example-plugins` | https://github.com/NousResearch/hermes-example-plugins | Example plugin scaffolding (companion to the in-tree `plugins/` dir). | active. |
| `NousResearch/hermes-paperclip-adapter` | n/a for us | Hermes-as-Paperclip-employee adapter. | irrelevant. |

Working clones for this audit:

- `/tmp/hermes-research/hermes-agent/`
- `/tmp/hermes-research/hermes-agent-self-evolution/`
- `/tmp/hermes-research/hermes-example-plugins/`

There is currently **no `Hal0ai/hermes-agent` fork**. The local clone is
read-only research only.

PyPI install: `pip install hermes-agent` (latest 0.14.0). The repo ships
`pyproject.toml` with one pinned base dep set + many `[extras]` (anthropic,
exa, firecrawl, fal, edge-tts, modal, daytona, vercel, voice, mcp, …) plus
a runtime lazy-installer (`tools/lazy_deps.py`) that pulls extras on first
use of the corresponding backend.

Entry points (`pyproject.toml` `[project.scripts]`):

- `hermes` → `hermes_cli.main:main`  (the agent CLI; argparse, ~530K lines)
- `hermes-agent` → `run_agent:main`  (lower-level direct loop runner)
- `hermes-acp` → `acp_adapter.entry:main`  (Agent Client Protocol bridge)

---

## 1. Config surface

Hermes has **three** layered config locations + **one** persistent home:

| Surface | Path | Format | Scope |
|--------|------|--------|-------|
| **Home dir** | `$HERMES_HOME` (default `~/.hermes`) | dir | Everything Hermes owns. Single source of truth. |
| **`config.yaml`** | `$HERMES_HOME/config.yaml` | YAML | Persistent settings — model, providers, terminal, memory, MCP, skills, hooks, display. |
| **`.env`** | `$HERMES_HOME/.env` | dotenv | Secrets. API keys, OAuth tokens. Loaded into `os.environ`. |
| **`SOUL.md`** | `$HERMES_HOME/SOUL.md` | Markdown | Agent persona/identity (free-form prose). |
| **`MEMORY.md` / `USER.md`** | `$HERMES_HOME/memories/{MEMORY,USER}.md` | Markdown | Built-in curated memory + user profile. |
| **`skills/`** | `$HERMES_HOME/skills/` | dir of `<name>/SKILL.md` | User skills (read/write, where new skills are saved). |
| **Bundled `skills/`** | `<install>/skills/` and `<install>/optional-skills/` | dir | Read-only, shipped with the wheel. |
| **`plugins/`** | `$HERMES_HOME/plugins/<kind>/<name>/` | dir | User-installed plugins (model-providers, memory, platforms, …). |

Sole source-of-truth for path resolution:
`/tmp/hermes-research/hermes-agent/hermes_constants.py` — `get_hermes_home()`,
`get_config_path()`, `get_skills_dir()`, `get_bundled_skills_dir()`,
`get_optional_skills_dir()`. Honors `HERMES_HOME`, `HERMES_BUNDLED_SKILLS`,
`HERMES_OPTIONAL_SKILLS` env vars.

Repo-internal example files:

- `/tmp/hermes-research/hermes-agent/.env.example`            (470 lines — every env var)
- `/tmp/hermes-research/hermes-agent/cli-config.yaml.example` (1100 lines — every YAML key)

### 1.1 Subsystem: Model provider

`config.yaml` keys (under `model:`):

| Key | Type | Default | Notes |
|-----|------|---------|------|
| `model.default` (alias `model.model`) | string | `"anthropic/claude-opus-4.6"` | Model ID. Provider-prefix optional. |
| `model.provider` | enum or `"auto"` | `"auto"` | One of: `openrouter`, `nous`, `nous-api`, `anthropic`, `openai-codex`, `copilot`, `gemini`, `zai`, `kimi-coding`, `minimax`, `minimax-cn`, `huggingface`, `nvidia`, `xiaomi`, `arcee`, `ollama-cloud`, `kilocode`, `ai-gateway`, `azure-foundry`, `lmstudio`, `custom` (aliases: `ollama`, `vllm`, `llamacpp`, `llama.cpp`, `llama-cpp`, `local`). |
| `model.base_url` | string | provider default | OpenAI-compatible endpoint root. Required for `custom`. |
| `model.api_key` | string | "" | Inline only; prefer `.env`. |
| `model.context_length` | int | auto-detected | Total context (input+output). |
| `model.max_tokens` | int | model default | Output cap. |
| `model.auth_mode` | string | `"api_key"` | `"entra_id"` for Azure-Foundry keyless. |
| `model.entra.scope` | string | `"https://ai.azure.com/.default"` | Entra-ID scope. |
| `providers.<name>.request_timeout_seconds` | int | `1800` | Per-provider override (legacy env: `HERMES_API_TIMEOUT`). |
| `providers.<name>.stale_timeout_seconds` | int | `300` | Per-provider non-stream stale detector. |
| `providers.<name>.models.<model>.timeout_seconds` | int | inherits | Per-model timeout. |
| `provider_routing.sort` | enum | `"price"` | OpenRouter only: `price|throughput|latency`. |
| `provider_routing.only` / `ignore` / `order` | list[str] | — | OpenRouter routing filters. |
| `provider_routing.require_parameters` | bool | false | OpenRouter strictness. |
| `provider_routing.data_collection` | enum | `"allow"` | `allow|deny`. |
| `openrouter.response_cache` | bool | true | OR edge response cache. |
| `openrouter.response_cache_ttl` | int (1–86400) | 300 | seconds. |
| `model_aliases.<alias>.{model,provider,base_url}` | map | — | Short aliases for `/model`. |

Provider env vars (`.env`):

```
OPENROUTER_API_KEY           # default LLM aggregator
ANTHROPIC_API_KEY            # native Anthropic
NOVITA_API_KEY / NOVITA_BASE_URL
GOOGLE_API_KEY / GEMINI_API_KEY / GEMINI_BASE_URL
OLLAMA_API_KEY / OLLAMA_BASE_URL          # Ollama Cloud (not local)
GLM_API_KEY / GLM_BASE_URL                # z.ai
KIMI_API_KEY / KIMI_BASE_URL / KIMI_CN_API_KEY
ARCEEAI_API_KEY / ARCEE_BASE_URL
MINIMAX_API_KEY / MINIMAX_BASE_URL
MINIMAX_CN_API_KEY / MINIMAX_CN_BASE_URL
OPENCODE_ZEN_API_KEY / OPENCODE_ZEN_BASE_URL
OPENCODE_GO_API_KEY / OPENCODE_GO_BASE_URL
HF_TOKEN                      # Hugging Face Inference Providers
HERMES_QWEN_BASE_URL          # Qwen OAuth (no api_key)
XIAOMI_API_KEY / XIAOMI_BASE_URL
NVIDIA_API_KEY
KILOCODE_API_KEY
AI_GATEWAY_API_KEY            # Vercel AI Gateway
AZURE_FOUNDRY_API_KEY / AZURE_FOUNDRY_BASE_URL
LM_API_KEY                    # LM Studio (default http://127.0.0.1:1234/v1)
HERMES_INFERENCE_PROVIDER     # forces provider regardless of model.provider
HERMES_API_TIMEOUT            # legacy global request timeout (s)
HERMES_API_CALL_STALE_TIMEOUT # legacy stale-detector timeout (s)
```

Custom / local-server env (`provider: custom`): no fixed env vars — set
`model.base_url` in `config.yaml` directly.

### 1.2 Subsystem: Memory

`config.yaml` keys (under `memory:`):

| Key | Type | Default | Notes |
|-----|------|---------|------|
| `memory.provider` | string | `""` (built-in only) | One of `honcho`, `openviking`, `mem0`, `hindsight`, `holographic`, `retaindb`, `supermemory`, `byterover` — or empty for built-in MEMORY.md/USER.md only. |
| `memory.memory_enabled` | bool | true | MEMORY.md (agent notes). |
| `memory.user_profile_enabled` | bool | true | USER.md (user profile). |
| `memory.memory_char_limit` | int | 2200 | ~800 tokens. |
| `memory.user_char_limit` | int | 1375 | ~500 tokens. |
| `memory.nudge_interval` | int | 10 | Remind agent to save memories every N user turns. |
| `memory.flush_min_turns` | int | 6 | Min user turns to trigger flush on `/reset`/exit. |

Per-provider config either via env or `$HERMES_HOME/<provider>.json` (see
`plugins/memory/<provider>/README.md`). Example for mem0:

```
MEM0_API_KEY
MEM0_USER_ID                  # default "hermes-user"
MEM0_AGENT_ID                 # default "hermes"
```

### 1.3 Subsystem: MCP

`config.yaml` keys (under `mcp_servers:`):

```yaml
mcp_servers:
  <name>:
    # Stdio transport:
    command: <executable>
    args: [<...>]
    env: { KEY: VALUE }
    # OR HTTP/SSE transport:
    url: https://mcp.example.com/mcp
    headers: { Authorization: "Bearer ..." }
    # Common:
    timeout: 120              # tool call timeout (s)
    connect_timeout: 60       # initial connect (s)
    # Sampling (server-initiated LLM):
    sampling:
      enabled: true
      model: "gemini-3-flash"
      max_tokens_cap: 4096
      timeout: 30
      max_rpm: 10
      allowed_models: []
      max_tool_rounds: 5
      log_level: info
```

`${VAR}` placeholders in any string get resolved from `os.environ` at
load time (see `_interpolate_env_vars()` in `tools/mcp_tool.py:2224`).

### 1.4 Subsystem: Skills

| Key | Type | Default |
|-----|------|---------|
| `skills.creation_nudge_interval` | int | 15 |
| `skills.external_dirs` | list[str] | `[]` |

`skills.external_dirs` is the **key extension point for hal0** —
read-only directories the agent will scan in addition to
`$HERMES_HOME/skills/`. Expansion supports `~` and `${VAR}`. Local skills
take precedence on name collision. New skill creation always writes to
`$HERMES_HOME/skills/`.

### 1.5 Subsystem: Persona

| File | Format |
|------|--------|
| `$HERMES_HOME/SOUL.md` | free-form Markdown (the persona) |
| `hermes_cli/default_soul.py` | `DEFAULT_SOUL_MD` constant — fallback identity when SOUL.md absent |

The "personalities" in `config.yaml` `agent.personalities.<name>` are
**not** SOUL.md — they're one-line prompts swapped via the `/personality`
slash command. SOUL.md is the durable identity; personalities are short
per-session overlays.

### 1.6 Subsystem: Subagents / delegation

| Key | Default | Notes |
|-----|---------|------|
| `delegation.max_iterations` | 50 | per child |
| `delegation.max_concurrent_children` | 3 | per batch; floor 1, no ceiling |
| `delegation.max_spawn_depth` | 1 | 1=flat, raise to 2 for orchestrator children |
| `delegation.orchestrator_enabled` | true | kill switch |
| `delegation.subagent_auto_approve` | false | for cron/batch only |
| `delegation.inherit_mcp_toolsets` | true | child inherits parent MCP toolsets |
| `delegation.model` / `delegation.provider` | inherit | per-child model override |

### 1.7 Subsystem: Telemetry / Display

`agent.verbose`, `display.tool_progress`, `display.streaming`, `agent.reasoning_effort`
(values: `xhigh|high|medium|low|minimal|none`), `agent.max_turns` (default 60),
`agent.api_max_retries` (default 3), `agent.gateway_timeout` (s), etc. See lines
560–980 of `cli-config.yaml.example`.

### 1.8 Subsystem: Hooks

Shell-script hooks under `config.yaml hooks:` — events: `pre_tool_call`,
`post_tool_call`, `pre_llm_call`, `post_llm_call`, `pre_api_request`,
`post_api_request`, `on_session_start`, `on_session_end`,
`on_session_finalize`, `on_session_reset`, `subagent_stop`. JSON wire
protocol over stdin/stdout. Per-`(event, command)` consent file at
`$HERMES_HOME/shell-hooks-allowlist.json`. Non-interactive runs need
`--accept-hooks` or `HERMES_ACCEPT_HOOKS=1`. **Useful for hal0** — wire
hal0 admin actions to `on_session_start`.

### 1.9 Subsystem: Compression

`compression.enabled` (true), `compression.threshold` (0.50 = 50% of
context window), `compression.target_ratio` (0.20 of threshold preserved
as recent tail), `compression.protect_last_n` (20 messages), `compression.protect_first_n` (3).
Auxiliary models for summarization configured under
`auxiliary.{vision,web_extract,session_search}.{provider,model,timeout,...}`.

### 1.10 Subsystem: Terminal

`terminal.backend`: `local | ssh | docker | singularity | modal | daytona | vercel-sandbox`.
Plus `terminal.cwd`, `terminal.timeout`, `terminal.docker_image`,
`terminal.ssh_host` etc., and `sudo_password` (plaintext — interactive
prompt fallback when omitted). Container resource caps:
`container_cpu`, `container_memory`, `container_disk`, `container_persistent`.

---

## 2. CLI surface

Top-level subcommands wired in `hermes_cli/main.py::build_top_level_parser()`.
Confirmed by grepping `subparsers.add_parser(...)` calls:

| Subcommand | What it does | Key file |
|-----------|--------------|---------|
| (bare) `hermes` | Interactive chat TUI | `hermes_cli/main.py` |
| `hermes setup` | Full first-run/setup wizard | `hermes_cli/setup.py::run_setup_wizard` |
| `hermes postinstall` | Post-install hook (pip users) | `hermes_cli/main.py:11392` |
| `hermes model` | Switch model + provider | `hermes_cli/model_switch.py` |
| `hermes config show \| edit \| set <k> <v> \| path \| env-path \| check \| migrate` | YAML config CRUD | `hermes_cli/config.py` |
| `hermes secrets bw …` | Bitwarden bridge for `.env` secrets | `hermes_cli/secrets_cli.py` |
| `hermes auth add \| list \| remove \| reset \| status \| logout \| spotify` | Pooled credentials | `hermes_cli/auth.py`, `auth_commands.py` |
| `hermes login` / `hermes logout` | Nous Portal OAuth | `hermes_cli/main.py:11467` |
| `hermes gateway run \| start \| stop \| restart \| status \| install \| uninstall \| list \| setup \| migrate-legacy` | Messaging gateway lifecycle (systemd templates included) | `hermes_cli/gateway.py` |
| `hermes proxy start \| stop \| status` | Local credential proxy | `hermes_cli/proxy/` |
| `hermes whatsapp` | Baileys-based WhatsApp pairing | `hermes_cli/main.py:11403` |
| `hermes slack manifest …` | Slack app manifest helper | `hermes_cli/slack_cli.py` |
| `hermes send …` | One-shot send-to-platform | `hermes_cli/send_cmd.py` |
| `hermes cron list \| create \| edit \| pause \| resume \| run \| remove \| status \| tick` | Built-in scheduler (croniter) | `hermes_cli/cron.py`, `cron/` |
| `hermes webhook subscribe \| list \| remove \| test` | GitHub / generic webhook handlers | `hermes_cli/webhook.py` |
| `hermes kanban …` | Built-in kanban DB + LLM workflows | `hermes_cli/kanban*.py` |
| `hermes hooks list \| test \| revoke \| accept-all` | Manage shell hooks | `hermes_cli/hooks.py` |
| `hermes doctor` | Diagnostics (provider health, models endpoint, dep check, …) | `hermes_cli/doctor.py` (88909 bytes) |
| `hermes dump` | Dump session/state for debugging | `hermes_cli/dump.py` |
| `hermes debug share \| delete` | Share/delete debug bundle | `hermes_cli/debug.py` |
| `hermes backup` | Backup `$HERMES_HOME` | `hermes_cli/backup.py` |
| `hermes checkpoints` | Conversation checkpoint mgmt | `hermes_cli/checkpoints.py` |
| `hermes import` | Import sessions | `hermes_cli/main.py:12083` |
| `hermes pairing` | DM-pairing flow for messaging platforms | `hermes_cli/pairing.py` |
| `hermes skills browse \| search \| install \| inspect \| list \| check \| update \| audit \| uninstall \| reset \| publish \| snapshot \| tap` | Skills Hub | `hermes_cli/skills_hub.py` (61717 bytes) |
| `hermes bundles` | Curated skill bundles | `hermes_cli/bundles.py` |
| `hermes plugins install \| update \| <plugin-cli>` | Plugin registry CRUD | `hermes_cli/plugins_cmd.py` |
| `hermes curator` | Auto-curator background tasks | `hermes_cli/curator.py`, `agent/curator.py` |
| `hermes memory setup \| status \| off \| reset` | Configure memory provider | `hermes_cli/memory_setup.py` |
| `hermes tools list \| --summary \| <interactive>` | Per-platform tool config | `hermes_cli/tools_config.py` (137133 bytes) |
| `hermes computer-use …` | Setup macOS cua-driver MCP | `hermes_cli/main.py:12699` |
| `hermes mcp serve \| add \| remove \| list \| test \| configure \| login` | MCP server lifecycle (and `mcp serve` = expose Hermes as MCP server) | `hermes_cli/mcp_config.py`, `mcp_serve.py` |
| `hermes sessions list \| export \| delete \| prune \| stats \| rename \| browse` | Session store CRUD | `hermes_cli/main.py:12849` |
| `hermes insights [--days N]` | Cross-session insights / usage | `agent/insights.py` |
| `hermes claw migrate \| cleanup` | OpenClaw migration | `hermes_cli/claw.py` |
| `hermes version` | Print version | `hermes_cli/main.py:13208` |
| `hermes update` | Self-update | `hermes_cli/main.py:13214` |
| `hermes uninstall` | Clean uninstall | `hermes_cli/uninstall.py` |
| `hermes acp` | Run as Agent Client Protocol server (Zed integration) | `acp_adapter/` |
| `hermes profile list \| use \| create \| delete \| describe \| show \| alias \| rename \| export \| import \| install \| update \| info` | Multi-profile management (`~/.hermes/profiles/<name>/`) | `hermes_cli/profiles.py` |
| `hermes completion` | Shell completion | `hermes_cli/completion.py` |
| `hermes dashboard` | Localhost SPA + API | `hermes_cli/web_server.py` (178133 bytes) |
| `hermes logs` | Tail / show logs | `hermes_cli/logs.py` |
| `hermes status` | Compact runtime status | `hermes_cli/status.py` |
| `hermes fallback show \| add \| remove \| edit` | Fallback model chain | `hermes_cli/fallback_cmd.py` |

First-run / install flow (from `scripts/install.sh` + `hermes_cli/setup.py`):

1. `curl -fsSL .../scripts/install.sh | bash` clones repo, installs `uv`,
   Python 3.11, deps (`.[all]`).
2. Symlinks `$HOME/.local/bin/hermes` → repo `hermes` shim.
3. Optionally runs `hermes setup` (interactive wizard) at the end —
   `_run_first_time_quick_setup(config, hermes_home, is_existing)` at
   `hermes_cli/setup.py:3283`. Streamlined flow: provider → model →
   terminal → messaging.
4. `hermes postinstall` is the pip-only equivalent: installs optional
   deps + runs `hermes setup`.
5. `_offer_openclaw_migration()` (`hermes_cli/setup.py:2916`) detects
   `~/.openclaw/` and offers `hermes claw migrate` before configuration.

---

## 3. Model provider abstraction

### 3.1 Architecture

`/tmp/hermes-research/hermes-agent/providers/base.py` — declares the
`ProviderProfile` dataclass. **Every** provider — local or remote — is a
profile object.

```python
@dataclass
class ProviderProfile:
    # Identity
    name: str
    api_mode: str = "chat_completions"   # or "responses", "native_anthropic", "bedrock_converse"
    aliases: tuple = ()
    # Human metadata
    display_name: str = ""
    description: str = ""
    signup_url: str = ""
    # Auth + endpoints
    env_vars: tuple = ()
    base_url: str = ""
    models_url: str = ""                 # default {base_url}/models
    auth_type: str = "api_key"           # api_key|oauth_device_code|oauth_external|copilot|aws_sdk
    supports_health_check: bool = True
    # Catalog
    fallback_models: tuple = ()
    hostname: str = ""
    # Client quirks
    default_headers: dict = …
    fixed_temperature: Any = None        # use OMIT_TEMPERATURE sentinel to suppress
    default_max_tokens: int | None = None
    default_aux_model: str = ""
```

Overridable hooks: `get_hostname()`, `prepare_messages()`,
`build_extra_body()`, `build_api_kwargs_extras()`, `fetch_models()`.

### 3.2 Registry

`/tmp/hermes-research/hermes-agent/providers/__init__.py`:

- `register_provider(profile)` — call at plugin import time.
- `get_provider_profile(name)` — by name OR alias.
- `list_providers()` — all registered.
- Discovery is lazy. Three sources, in order:
  1. **Bundled plugins**: `plugins/model-providers/<name>/__init__.py`.
  2. **User plugins**: `$HERMES_HOME/plugins/model-providers/<name>/__init__.py`.
     User wins on name collision (last-writer-wins).
  3. **Legacy** `providers/<name>.py` single-file (back-compat).

### 3.3 Bundled providers (28 ship in-tree)

`/tmp/hermes-research/hermes-agent/plugins/model-providers/`:

```
ai-gateway, alibaba, alibaba-coding-plan, anthropic, arcee,
azure-foundry, bedrock, copilot, copilot-acp, custom, deepseek,
gemini, gmi, huggingface, kilocode, kimi-coding, minimax, nous,
novita, nvidia, ollama-cloud, openai-codex, opencode-zen, openrouter,
qwen-oauth, stepfun, xai, xiaomi, zai
```

**There is NO `lmstudio/` or `ollama/` (local) plugin dir** — both are
collapsed into the `custom` profile. `lmstudio` is documented as a
first-class provider in `cli-config.yaml.example` but is wired via
`hermes_cli/runtime_provider.py`, not as a model-providers plugin.

### 3.4 The `custom` profile = what hal0 wants

`/tmp/hermes-research/hermes-agent/plugins/model-providers/custom/__init__.py`:

```python
custom = CustomProfile(
    name="custom",
    aliases=("ollama", "local", "vllm", "llamacpp", "llama.cpp", "llama-cpp"),
    env_vars=(),
    base_url="",                          # user-configured
)
```

`CustomProfile.fetch_models()` does the standard `GET {base_url}/models`
Bearer-auth probe when `base_url` is set. `build_api_kwargs_extras()`
adds `extra_body.options.num_ctx` from `ollama_num_ctx` and
`extra_body.think = False` when reasoning is disabled.

### 3.5 Smallest viable config for a local OpenAI-compatible server

```yaml
# config.yaml
model:
  default: "Qwen3-30B-A3B-Instruct-2507"   # any name your server reports
  provider: "custom"                        # alias for ollama/vllm/llamacpp
  base_url: "http://10.0.1.142:8000/v1"
```

That's it. No env var needed because `env_vars=()` on the custom profile.
For Lemonade specifically, Lemonade exposes `/api/v1/...` — set
`base_url: "http://10.0.1.142:8000/api/v1"` so `{base_url}/models` and
`{base_url}/chat/completions` resolve correctly.

### 3.6 Most complete reference config (hal0-targeted)

```yaml
model:
  default: "Qwen3-30B-A3B-Instruct-2507"
  provider: "custom"
  base_url: "http://10.0.1.142:8000/api/v1"
  context_length: 32768
  # max_tokens unset — let server decide

providers:
  custom:
    request_timeout_seconds: 300      # local cold-start tolerance
    stale_timeout_seconds: 900
    models:
      Qwen3-30B-A3B-Instruct-2507:
        timeout_seconds: 600

model_aliases:
  qwen-coder:
    model: "Qwen3-Coder-30B"
    provider: custom
    base_url: "http://10.0.1.142:8000/api/v1"
  glm-air:
    model: "GLM-4.6-Air"
    provider: custom
    base_url: "http://10.0.1.142:8000/api/v1"

# Auxiliary tasks (vision, web_extract, compression summaries)
# Point at hal0 too if you have a vision-capable slot:
auxiliary:
  vision:
    provider: "main"               # uses model.base_url + (empty) api_key
    model: ""                       # or specific multimodal slot
  web_extract:
    provider: "main"
    model: ""
  session_search:
    provider: "main"
    model: ""
```

### 3.7 Custom provider plugin shape (if hal0 wants its own profile)

Instead of using `custom`, hal0 can ship a private profile that hardcodes
the hal0 base URL and emits a vendor User-Agent. Drop at
`$HERMES_HOME/plugins/model-providers/hal0/`:

```python
# __init__.py
from providers import register_provider
from providers.base import ProviderProfile

class Hal0Profile(ProviderProfile):
    pass

hal0 = Hal0Profile(
    name="hal0",
    aliases=("hal0-local",),
    display_name="hal0 (local)",
    description="hal0 Lemonade-backed slots on the LAN",
    signup_url="https://hal0.dev",
    env_vars=("HAL0_API_KEY", "HAL0_BASE_URL"),
    base_url="http://10.0.1.142:8000/api/v1",
    default_aux_model="",
    default_headers={"User-Agent": "hermes-on-hal0/1.0"},
)
register_provider(hal0)
```

```yaml
# plugin.yaml
name: hal0-provider
kind: model-provider
version: 1.0.0
description: hal0 LAN inference platform
author: hal0
```

### 3.8 Separate provider per modality?

There is **no separate top-level "embeddings provider" or "reranker
provider" abstraction** in Hermes. Reranking happens inside specific
memory providers (mem0 uses Mem0's reranker; honcho/holographic do their
own). Hermes itself does not call `/v1/embeddings` from the agent loop —
that's not part of its architecture.

STT/TTS providers DO have their own pluggable provider abstraction:

- **STT**: `agent/transcription_tools.py` (config: `stt.provider` =
  `local | groq | openai | mistral`, `stt.<provider>.model`, optional
  `GROQ_BASE_URL` / `STT_OPENAI_BASE_URL`).
- **TTS**: `tools/tts_tool.py` (`tts.provider` = `edge | elevenlabs | openai | minimax | mistral | neutts | kittentts`).
- **Vision**: routed through `auxiliary.vision.{provider,model}`. Any
  OpenAI-compatible vision model on a custom endpoint works.

To wire hal0's voice/STT/TTS slots into Hermes:
- Set `stt.provider: openai` with `STT_OPENAI_BASE_URL=http://10.0.1.142:9000/v1`
  pointing at hal0's STT slot.
- Set `tts.provider: openai` and pin to hal0's TTS slot the same way.

---

## 4. Memory system

### 4.1 Two tiers

**Built-in (always on):** `$HERMES_HOME/memories/MEMORY.md` (agent's notes)
and `$HERMES_HOME/memories/USER.md` (user profile). Bounded character
limits (default 2200 / 1375). Injected into every system prompt under
the "volatile" tier. Managed via the built-in `memory` toolset.

**External provider (one at a time):** Selected via `memory.provider` in
`config.yaml`. Discovery in
`/tmp/hermes-research/hermes-agent/plugins/memory/__init__.py`:

- Bundled: `plugins/memory/<name>/`.
- User: `$HERMES_HOME/plugins/<name>/`. Bundled wins on collision.

Bundled providers shipping in-tree:

```
byterover, hindsight, holographic, honcho, mem0, openviking,
retaindb, supermemory
```

The provider ABC is `/tmp/hermes-research/hermes-agent/agent/memory_provider.py::MemoryProvider`.
Lifecycle:

```python
initialize(session_id, hermes_home, platform, agent_context,
           agent_identity, agent_workspace, parent_session_id, user_id)
system_prompt_block() -> str
prefetch(query, session_id=) -> str          # before each turn
queue_prefetch(query, session_id=)            # after each turn (background)
sync_turn(user, assistant, session_id=)       # after each turn
get_tool_schemas() -> list[dict]              # tools the provider adds
handle_tool_call(name, args, **kwargs) -> str # tool dispatch
shutdown()
# Optional:
on_turn_start, on_session_end, on_session_switch, on_pre_compress,
on_delegation, on_memory_write
```

### 4.2 Does Hermes use Cognee?

**No.** No Cognee provider ships in-tree. Cognee is referenced nowhere in
the canonical repo. The closest "knowledge graph" provider is
`holographic` (plugins/memory/holographic). hal0's plan to use Cognee
(noted in MEMORY: "hal0 Agents v0.3 design") would either need a custom
memory provider plugin OR be exposed via MCP and used as a tool.

### 4.3 Wiring hal0's memory MCP into Hermes

Two integration paths:

**Path A (simplest, recommended for v0.3):** expose hal0-memory as an
MCP server, register under `mcp_servers.hal0-memory`. Memory tools
appear in the model's tool list. No new code on Hermes side.

**Path B (deeper):** write a `Hal0MemoryProvider(MemoryProvider)` plugin
that calls hal0's HTTP API directly. Drop at
`$HERMES_HOME/plugins/hal0-memory/`. Hermes will inject a
`system_prompt_block()`, prefetch context before each turn, and sync
turns automatically. Use this when you want hal0 memory to feel
"native," not as a tool the model has to remember to call.

### 4.4 Namespace / scoping

Built-in: per-`HERMES_HOME` (profile-scoped via
`~/.hermes/profiles/<name>/`).

Provider plugins receive in `initialize()` kwargs:
`user_id` (platform user identifier), `agent_identity` (profile name),
`agent_workspace` (shared workspace), `session_id`, `parent_session_id`,
`platform`. Each provider decides how to scope. mem0 scopes
read-filters to `user_id` only (cross-session recall), write-filters
to `(user_id, agent_id)` (attribution).

---

## 5. MCP integration

### 5.1 Loading

`tools/mcp_tool.py::_load_mcp_config()` at line 2237:

1. Reads `config.yaml mcp_servers`.
2. Loads `$HERMES_HOME/.env` into `os.environ`.
3. Resolves `${VAR}` placeholders in every string value.

`hermes mcp add` (in `hermes_cli/mcp_config.py`) supports interactive
add, named presets (currently only `codex`), `--url`, `--command`,
`--args`, `--env KEY=VALUE`, `--auth oauth|header`.

### 5.2 Transports

- **Stdio:** `command` + `args` + `env`. Subprocess sandbox. Hermes adds
  safe-default env vars on top of what's supplied
  (`tools/mcp_tool.py::_build_safe_env`).
- **HTTP / SSE:** `url` + `headers`. Both supported (SSE/streamable HTTP
  detected from server response).

### 5.3 OAuth

`tools/mcp_oauth.py` + `tools/mcp_oauth_manager.py` — Hermes handles
PKCE OAuth for HTTP MCP servers. Auth tokens land in
`$HERMES_HOME/mcp-tokens/<server>.json`. `hermes mcp login <name>`
forces re-auth.

### 5.4 Per-agent or global?

**Global to the profile.** Every `mcp_servers` entry is loaded for every
agent run within that `HERMES_HOME`. The agent picks WHICH tools to
expose via the per-platform toolset config
(`platform_toolsets.<platform>: [server:tool, ...]`). MCP tools use
`server:tool` notation in toolset lists.

### 5.5 Hermes as MCP server

`mcp_serve.py` (31690 bytes) — `hermes mcp serve` exposes Hermes
conversations as an MCP server (stdio). Tools mirror OpenClaw's bridge:
`conversations_list, conversation_get, messages_read, attachments_fetch,
events_poll, events_wait, messages_send, permissions_list_open,
permissions_respond, channels_list`. Use this to wire Hermes INTO
Claude Code / Cursor / Codex as a sub-agent.

---

## 6. Skills / personas / subagents

### 6.1 Skills filesystem layout

A skill is a directory with a `SKILL.md` (the procedure) and arbitrary
support files. Discovery via `agent/skill_utils.py::get_all_skills_dirs()`:

1. `$HERMES_HOME/skills/` (read-write; new skills land here).
2. Every path in `config.yaml skills.external_dirs` (read-only; expanded,
   resolved, dedup'd; missing dirs silently skipped).
3. Bundled (`<install>/skills/` + `<install>/optional-skills/`) — these
   appear in the model's "available skills" listing but are not in
   `external_dirs`.

`get_all_skills_dirs()` walks each dir for `SKILL.md` files. Excluded
sub-dirs:
`{.git, .github, .hub, .archive, .venv, venv, node_modules,
site-packages, __pycache__, .tox, .nox, .pytest_cache, .mypy_cache,
.ruff_cache}`.

`SKILL.md` frontmatter (parsed by `extract_skill_conditions`):

```yaml
---
name: my-skill
description: One-line summary.
metadata:
  hermes:
    fallback_for_toolsets: [web]      # activate when this toolset is enabled
    requires_toolsets: [terminal]      # only if these toolsets enabled
    fallback_for_tools: []
    requires_tools: []
---
# Skill body (Markdown)
```

Example bundled skill: `skills/dogfood/` → `SKILL.md`,
`templates/dogfood-report-template.md`, `references/issue-taxonomy.md`.
Subdirs `templates/` and `references/` are convention, not enforced —
the skill body references them by relative path.

### 6.2 Skills Hub

`hermes_cli/skills_hub.py` (61717 bytes) — full GitHub-backed skill
registry (`agentskills.io`). `hermes skills search/install/tap/publish`
manage external sources. Installed skills land in
`$HERMES_HOME/skills/<source>/<skill>/`.

### 6.3 Personas

`SOUL.md` at `$HERMES_HOME/SOUL.md` — free-form persona, loaded as the
"identity" block at the very top of the system prompt
(`agent/system_prompt.py:90`). When absent, falls back to
`hermes_cli/default_soul.py::DEFAULT_SOUL_MD`. Per-profile: each profile
under `~/.hermes/profiles/<name>/` has its own `SOUL.md`.

`agent.personalities.<name>` in `config.yaml` — short one-line prompts
swappable at runtime via `/personality <name>`. Not the same as SOUL.md.

### 6.4 Subagents

The `delegate_task` tool (`tools/delegate_tool.py`, 119593 bytes) is
Hermes's equivalent of Claude Code's Agent tool. Single tasks or batch
mode (default 3 parallel). Children get isolated context, can spawn
their own subagents up to `delegation.max_spawn_depth` (default 1).
Per-child `model`/`provider` overrides supported. Subagent system prompt
is the parent's stable tier + a slimmed-down memory snapshot.

Two related tools:
- `mixture_of_agents` (`tools/mixture_of_agents_tool.py`) — OpenRouter-
  required MoA fan-out, not a true delegation primitive.
- `execute_code` (`tools/code_execution_tool.py`) — programmatic tool
  calling via Python+RPC; lets the agent compose tool calls in code
  without growing the context window.

---

## 7. First-run / bootstrap

### 7.1 Bootstrap sequence

1. `scripts/install.sh` clones repo, installs uv/Python 3.11/`.[all]`,
   symlinks `~/.local/bin/hermes`, ensures `node`, `ripgrep`, `ffmpeg`
   (via `--ensure`), installs Playwright/Chromium (unless `--skip-browser`).
2. Calls `tools/skills_sync.py` to seed/sync bundled skills into
   `$HERMES_HOME/skills/`.
3. Runs `hermes setup` interactive wizard (unless `--skip-setup`):
   `_run_first_time_quick_setup()` at `hermes_cli/setup.py:3283`.
   Streamlined order: provider → model → terminal backend → messaging.
4. Detects `~/.openclaw/` and offers `hermes claw migrate` first.
5. Writes `$HERMES_HOME/{config.yaml, .env, SOUL.md}`, ensures
   `memories/`, `skills/`, `plugins/`, `logs/`, `sessions/` dirs.

`hermes postinstall` is the pip-only equivalent (`hermes_cli/main.py:11392`,
`scripts/install.sh:2011`).

### 7.2 Self-improvement loop

**Inside the agent loop (every session, automatic):**

- **Memory nudge** (`memory.nudge_interval`, default 10 user turns) —
  injected reminder to save memories.
- **Skill creation nudge** (`skills.creation_nudge_interval`, default 15
  tool iterations) — reminder to create a skill after a complex task.
- **Skill self-improvement** — `tools/skill_usage.py` tracks per-skill
  invocation outcomes; `agent/background_review.py` (29465 bytes) runs
  post-session reviews proposing edits to skills the agent used.
- **Curator** (`agent/curator.py`, 74849 bytes) — long-running
  background process that consolidates memory and prunes stale entries.

**External, opt-in:** `NousResearch/hermes-agent-self-evolution`. DSPy +
GEPA prompt evolution. Reads session traces, proposes targeted
mutations, gates by tests + size limits + caching compatibility, opens
PRs against `hermes-agent`. **Not bundled** with `pip install hermes-agent` —
separate repo (`pip install -e .[dev]`) that you point at
`HERMES_AGENT_REPO=~/.hermes/hermes-agent`. ~$2-10 per optimization run.

### 7.3 Environment probing

`hermes doctor` (`hermes_cli/doctor.py`, 88909 bytes) — comprehensive
diagnostic suite. Hits `/models` on every `auth_type=api_key` provider
profile, checks the terminal backend, validates skills/plugins/MCP
configs, surfaces missing deps. Run on first start by the install
script. `hermes_constants.py` separately detects WSL, Termux, container
runtime, and applies the Windows UTF-8 bootstrap (`hermes_bootstrap.py`).

### 7.4 Onboarding hints

`agent/onboarding.py` — first-touch contextual hints (busy-input mode,
tool-progress mode, openclaw residue). Each hint fires once per install,
tracked in `config.yaml onboarding.seen.<flag>`. **Useful hal0 pattern**
— mimic this for hal0-specific tips on first agent use.

---

## 8. Extension points (where to hook hal0's bootstrap)

Ordered by ease of integration (low risk → deep wiring):

### A. Env discovery on first run

- **Hook**: `config.yaml hooks.on_session_start: [{ command: "/usr/lib/hal0/hermes-hooks/discover-hal0.sh" }]`.
- Script reads `/etc/hal0/capabilities.toml`, posts to hal0's discovery
  endpoint, returns JSON injecting fresh context to the model.
- **Path**: hal0 ships the hook script + flips the consent file
  (`$HERMES_HOME/shell-hooks-allowlist.json`) at install time so we
  don't need `--accept-hooks`.

### B. Auto-register every model on a hal0 instance

- **Hook**: post-install script run from hal0's installer. Two surfaces:
  1. Write `$HERMES_HOME/config.yaml` with `model.provider: custom`,
     `model.base_url: http://10.0.1.142:8000/api/v1`, and
     `model_aliases.<slot>` entries — one per slot in
     `/etc/hal0/capabilities.toml`. The `custom` profile's
     `fetch_models()` will then auto-populate `/model` picker via
     `{base_url}/models`.
  2. OR ship a bundled `Hal0Profile` plugin under
     `$HERMES_HOME/plugins/model-providers/hal0/` so users see "hal0"
     as a first-class provider option in the setup wizard.
- **Decision**: option 1 is zero-code; option 2 is what we want long
  term (recognizable in the wizard, distinct hostname → provider
  detection in `agent/model_metadata.py`).

### C. Auto-wire hal0's memory MCP

- **Quick path**: append `mcp_servers.hal0-memory: { url: "http://10.0.1.142:8095/mcp", headers: { ... } }`
  to `$HERMES_HOME/config.yaml`. Reload Hermes.
- **Deep path**: ship `Hal0MemoryProvider` under
  `$HERMES_HOME/plugins/hal0-memory/__init__.py` extending
  `agent.memory_provider.MemoryProvider`. Wire `system_prompt_block()`,
  `prefetch()`, `sync_turn()` to hal0's HTTP memory API. Flip
  `memory.provider: hal0-memory` in `config.yaml`. Provider becomes the
  ONE active external memory, gets injected into every system prompt.

### D. Post-install hooks (from hal0's installer)

- hal0 installer should call (after `pip install hermes-agent`):
  1. `hermes config set model.provider custom`
  2. `hermes config set model.base_url http://10.0.1.142:8000/api/v1`
  3. `hermes config set memory.provider hal0-memory`
  4. `hermes mcp add hal0-admin --url http://10.0.1.142:8095/mcp`
  5. `hermes skills tap add Hal0ai/hal0-skills` (if we publish skills)
  6. Symlink hal0 context dir → `skills.external_dirs` entry:
     `hermes config set skills.external_dirs '["/etc/hal0/agent-skills"]'`
  7. Drop `SOUL.md` template at `$HERMES_HOME/SOUL.md` (hal0 persona).
  8. Drop a hal0 hook script at `$HERMES_HOME/hal0-hooks/` + register
     under `hooks.on_session_start`.

### E. Self-improvement integration

- **Out of scope for v0.3**. `hermes-agent-self-evolution` is a separate
  repo that opens PRs against `hermes-agent` upstream. For hal0 we'd
  fork to `Hal0ai/hermes-agent-self-evolution` only if we want to
  evolve hal0-specific skills.

### F. Per-profile installs

- Use `hermes profile create hal0` to give the bundled hal0 install its
  own `~/.hermes/profiles/hal0/{config.yaml, .env, SOUL.md, skills/, plugins/, …}`
  isolated from any user's existing hermes install. Activate with
  `hermes profile use hal0` or `HERMES_HOME=~/.hermes/profiles/hal0 hermes`.

### G. CLI subcommand injection (plugin CLI)

- `hermes_cli/plugins.py` discovers `cli.py::register_cli(subparser)`
  inside any plugin under `plugins/<name>/`. Drop hal0-specific
  subcommands (`hermes hal0 status`, `hermes hal0 slot ...`) via a
  plugin shipped under `$HERMES_HOME/plugins/hal0/`. The plugin can
  also `register_hook`, `register_tool`.

### H. Skill context-aware dir

- The MEMORY says "skills/context not symlinked into context-aware dir."
  The right home is `config.yaml skills.external_dirs: ["/etc/hal0/skills",
  "/var/lib/hal0/agent-skills"]`. These are read-only; new skills the
  agent creates land in `$HERMES_HOME/skills/`. If hal0 ships skills via
  a debian-style FHS, point external_dirs there.

### I. Project context files

- Hermes auto-injects `AGENTS.md`, `CLAUDE.md`, `.cursorrules`,
  `.cursor/rules/*.mdc`, and `HERMES.md` / `.hermes.md` from the cwd
  (and `.hermes.md` walks to git root). See `agent/prompt_builder.py`
  lines 1298–1410. Drop a `HERMES.md` at `/etc/hal0/HERMES.md` and
  always start hermes with cwd `/etc/hal0/` to seed context every session.

### J. Skill conditions for hal0 toolsets

- If hal0 ships an MCP server, the agent can be made to auto-load
  hal0-specific skills only when its MCP tools are enabled by adding
  `metadata.hermes.requires_tools: ["hal0_admin:status"]` to the
  skill's frontmatter.

---

## 9. Gaps and gotchas

### Brittle bits

- **`HERMES_HOME` fallback warning, not error.** `get_hermes_home()` in
  `hermes_constants.py` writes a warning to stderr if `HERMES_HOME` is
  unset while `active_profile` is not "default", then falls back to
  `~/.hermes`. Subprocess spawners must propagate `HERMES_HOME`
  explicitly. **Bake into hal0's systemd unit**: `Environment=HERMES_HOME=...`.

- **`config.yaml` is the persistent store, NOT `.env`.** The wizard
  routinely overwrites `model.default`, `model.provider`, `terminal.*`.
  Don't expect manual edits to `config.yaml` to survive `hermes setup`
  unless you skip the wizard.

- **Skills `external_dirs` is read-only.** New skill creation always
  writes to `$HERMES_HOME/skills/`. Don't expect the agent to push
  improvements back to a hal0-managed dir.

- **`hermes setup` is interactive-by-default.** Need `--quick`, env-var
  pre-population, or `--skip-setup` to install non-interactively. The
  wizard at `setup.py:3063` checks `is_interactive_stdin()` and gates
  questions on it, so `bash -c 'echo y | hermes setup'` is fragile.

- **mistralai dependency removed 2026-05-12** due to a PyPI worm. Don't
  reference Mistral STT/TTS provider in hal0 docs until upstream
  re-adds.

- **No model-load-state notification surface.** Hermes polls
  `{base_url}/models` for catalog freshness but otherwise treats
  inference endpoints as fire-and-forget. The Lemonade "evict-all on
  load failure" behavior (from MEMORY: `hal0_lemonade_gotchas`) will
  surface to Hermes as 503/timeout on the next `chat/completions` —
  Hermes will retry per `agent.api_max_retries` (3) and either succeed
  on warm-up or surface a clear failure.

### Linux vs Windows specifics

- **`hermes_bootstrap.py`** explicitly applies Windows UTF-8 mode +
  reconfigures stdio. POSIX is untouched. Safe for hal0 (Linux-only).
- **`pty` extra** uses `ptyprocess` on POSIX, `pywinpty` on Windows.
- **`matrix` extra (python-olm)** has no Windows native wheel. hal0
  Linux-only so irrelevant.
- **`docker_extra_args`** + `docker_run_as_host_user` only sensible on
  Linux (Docker Desktop on Win/macOS rootless differs).
- **Termux** has its own dep set (`.[termux]`, `constraints-termux.txt`).
- **WSL detection** in `hermes_constants.is_wsl()`. Hermes's browser
  dashboard chat pane uses POSIX PTY — needs WSL on Windows.

### CLI-only vs API-only

- **CLI only**: `hermes setup`, `hermes claw migrate`, `hermes mcp add`
  (interactive), `hermes tools` (curses UI), `hermes gateway install`
  (writes systemd units), `hermes doctor`.
- **API only (no CLI)**: programmatic conversation loop via
  `from run_agent import AIAgent` (used by `batch_runner.py`,
  `mini_swe_runner.py`); MCP server mode via `hermes mcp serve` (stdio,
  no HTTP).
- **Both**: `hermes mcp list/test/remove`, `hermes config set`,
  `hermes model`, `hermes cron`.
- The `hermes dashboard` SPA exposes a localhost HTTP API but is for
  user-facing interaction, not orchestration — don't try to drive
  hal0 admin actions through it.

### Gotchas specific to embedding Hermes in hal0

- **`hermes` writes to `~/.hermes/`** by default. hal0's daemon user
  must have a writable `$HOME` OR we set `HERMES_HOME=/var/lib/hal0/hermes`
  in the systemd unit.
- **Hermes pulls Python 3.11 via uv** when not present. hal0's LXC must
  either ship Python 3.11+ OR allow uv to manage it (`UV_PYTHON_INSTALL_DIR`).
- **`pip install hermes-agent` ≠ `git clone`** for skills. The pip wheel
  ships bundled skills via setuptools `package-data`. `get_bundled_skills_dir()`
  prefers `sysconfig.get_path("data")/skills` for wheel installs;
  override with `HERMES_BUNDLED_SKILLS=/usr/share/hal0/skills` if hal0
  installs Hermes via apt/dnf with non-standard data dirs.
- **MCP server registration writes to `config.yaml`**. The hal0
  installer must `hermes mcp add` AFTER `hermes setup` and AFTER any
  config-overwriting step, or the add gets clobbered.
- **`hermes mcp add` requires `--auth` for HTTP servers behind OAuth.**
  hal0's MCP at `10.0.1.220:8095/mcp` is auth-free on LAN — use plain
  `--url` and no `--auth`.
- **No graphql/REST API for runtime introspection.** If hal0 needs to
  know "is hermes alive and what's it doing right now?" use
  `hermes status` (CLI) or read `$HERMES_HOME/sessions/` directly.

---

## 10. Reference: minimal vs reference configs

### Minimal viable `config.yaml` (point Hermes at hal0, nothing else)

```yaml
model:
  default: "Qwen3-30B-A3B-Instruct-2507"
  provider: "custom"
  base_url: "http://10.0.1.142:8000/api/v1"

terminal:
  backend: "local"

memory:
  memory_enabled: true
  user_profile_enabled: true
```

That's it. The custom profile's `fetch_models()` populates the model
picker automatically. SOUL.md will fall back to `DEFAULT_SOUL_MD`.

### Reference `config.yaml` (full hal0 wiring)

```yaml
model:
  default: "Qwen3-30B-A3B-Instruct-2507"
  provider: "custom"
  base_url: "http://10.0.1.142:8000/api/v1"

providers:
  custom:
    request_timeout_seconds: 300
    stale_timeout_seconds: 900

model_aliases:
  qwen:
    model: "Qwen3-30B-A3B-Instruct-2507"
    provider: custom
    base_url: "http://10.0.1.142:8000/api/v1"
  coder:
    model: "Qwen3-Coder-30B"
    provider: custom
    base_url: "http://10.0.1.142:8000/api/v1"

memory:
  provider: "hal0-memory"           # custom MemoryProvider plugin at $HERMES_HOME/plugins/hal0-memory/
  memory_enabled: true
  user_profile_enabled: true
  nudge_interval: 10
  flush_min_turns: 6

mcp_servers:
  hal0-admin:
    url: "http://10.0.1.142:8095/mcp"
    timeout: 60
  hal0-memory:
    url: "http://10.0.1.220:8095/mcp"
    timeout: 30

skills:
  external_dirs:
    - "/etc/hal0/agent-skills"
    - "/var/lib/hal0/skills"
  creation_nudge_interval: 15

terminal:
  backend: "local"
  cwd: "/etc/hal0"                 # so AGENTS.md/HERMES.md auto-inject

agent:
  max_turns: 60
  reasoning_effort: "medium"
  personalities:
    helpful: "You are the hal0 admin agent. Be precise and direct."

auxiliary:
  vision:
    provider: "main"               # reuse model.base_url
    model: ""
  web_extract:
    provider: "main"
    model: ""

hooks:
  on_session_start:
    - command: "/usr/lib/hal0/hermes-hooks/inject-system-state.sh"
      timeout: 10
  post_tool_call:
    - matcher: "memory_save|memory_replace"
      command: "/usr/lib/hal0/hermes-hooks/mirror-to-cognee.sh"

display:
  skin: default
  bell_on_complete: false
  show_reasoning: false

privacy:
  redact_pii: false
```

`$HERMES_HOME/.env`:

```
# No model keys needed — custom provider with no env_vars
# Optional: GitHub token for skills hub rate limits
GITHUB_TOKEN=ghp_xxx
# Optional: voice STT/TTS pointing at hal0 slots
STT_OPENAI_BASE_URL=http://10.0.1.142:9000/v1
VOICE_TOOLS_OPENAI_KEY=dummy
```

`$HERMES_HOME/SOUL.md`:

```markdown
You are the hal0 admin agent, the right-hand assistant for a self-hosted
home-AI inference platform. You have direct access to slot lifecycle,
model registry, capability catalog, and memory MCP servers. You favor
concrete, verifiable actions: probe before you change, prefer dry-runs,
quote exact paths and ports. ...
```

---

## Appendix: file-path cheat sheet

| What | Where |
|------|------|
| Provider ABC | `providers/base.py` |
| Provider registry | `providers/__init__.py` |
| Bundled providers | `plugins/model-providers/<name>/` |
| User providers | `$HERMES_HOME/plugins/model-providers/<name>/` |
| Memory ABC | `agent/memory_provider.py` |
| Memory plugin discovery | `plugins/memory/__init__.py` |
| MCP config loader | `tools/mcp_tool.py::_load_mcp_config` (line 2237) |
| MCP CLI commands | `hermes_cli/mcp_config.py` |
| MCP server mode (hermes-as-MCP) | `mcp_serve.py` |
| Skill scanning | `agent/skill_utils.py::get_all_skills_dirs` |
| System prompt assembly | `agent/system_prompt.py::build_system_prompt_parts` |
| Context-file injection (AGENTS.md, etc.) | `agent/prompt_builder.py::_load_agents_md, _load_claude_md, _load_cursorrules, _load_hermes_md` |
| First-run wizard | `hermes_cli/setup.py::_run_first_time_quick_setup` |
| Install script | `scripts/install.sh` |
| Postinstall hook | `scripts/install.sh::postinstall_mode` |
| Doctor | `hermes_cli/doctor.py` |
| Env vars (env vars docs) | `.env.example` |
| Config YAML reference | `cli-config.yaml.example` |
| HERMES_HOME / paths | `hermes_constants.py` |
| Default SOUL.md | `hermes_cli/default_soul.py::DEFAULT_SOUL_MD` |
| Delegation (subagent) tool | `tools/delegate_tool.py` |
| Curator (background memory) | `agent/curator.py` |
| Background review | `agent/background_review.py` |
| Self-evolution (separate repo) | `NousResearch/hermes-agent-self-evolution` |

