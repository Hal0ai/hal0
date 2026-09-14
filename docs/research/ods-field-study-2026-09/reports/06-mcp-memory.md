# 06 — MCP & Memory: ODS vs hal0

Source-level comparative study. All paths absolute. ODS = `/home/user/ods`, hal0 = `/home/user/hal0`.

> **Headline correction to the brief.** The task premise — "how Hermes's `cli-config.yaml.template`
> declares `mcp_servers` (stdio vs http, env passing, per-server toolsets)" — is **false against the
> tree on disk**. ODS ships **no MCP anywhere in its product runtime**. There is no `mcp_servers`
> block, no MCP client, no MCP server, no MCP UI, no MCP CLI verb. Conversely, hal0 already ships
> ~70% of the "user-added MCP servers" feature the owner asked for (registry, REST API, CLI,
> manifest resolver, SSRF guard) — it is **unfinished, not absent**. Both findings reframe the
> deliverable: there is nothing to port from ODS on the MCP axis, and the hal0 work is *closing a
> loop*, not *starting a feature*.

---

## A. ODS mechanism

### A.1 MCP in ODS: it does not exist

An exhaustive case-insensitive sweep over the ODS tree returns **seven** files, and every one of
them is either a Claude Code developer-tooling artifact or a passing English mention:

- `/home/user/ods/.claude/commands/{code-review,deep-research,team-plan}.md` — Claude Code slash
  commands that call `mcp__pal__*` / `mcp__rube__*` tools **on the maintainer's workstation**. Not
  shipped, not installed, not part of `ods/`.
- `/home/user/ods/ods/docs/MDNS.md:43` — "These exist for MCP clients, service-discovery tools, and
  the eventual ODS mobile app". Aspirational prose about mDNS `_http._tcp` records.
- `/home/user/ods/ods/bin/ods-mdns.py:179` — same, in a comment.
- `/home/user/ods/ods/extensions/library/services/gaia/README.md:6` — describes a **third-party**
  service (GAIA) that has MCP support; ODS does not wire it.
- `/home/user/ods/ods/extensions/services/dashboard/package-lock.json` — an npm transitive
  substring match.

Point-by-point against the brief's specific claims:

| Claim | Verdict | Evidence |
|---|---|---|
| Hermes `cli-config.yaml.template` declares `mcp_servers` | **False** | `/home/user/ods/ods/extensions/services/hermes/cli-config.yaml.template` is 186 lines: `model`, `providers`, `auxiliary`, `agent.disabled_toolsets`, `terminal`, `platforms`, `compression`. No `mcp_servers`. |
| OpenCode is given MCP servers | **False** | `/home/user/ods/ods/installers/phases/07-devtools.sh:196-274` writes `opencode.json` with `$schema`/`model`/`small_model`/`provider` only. `/home/user/ods/ods/extensions/services/opencode/manifest.yaml` and `README.md` never mention MCP. |
| Open WebUI gets MCP/tool servers | **False** | `/home/user/ods/ods/docker-compose.base.yml:100-168` is the full `open-webui` env block: web search, RAG embeddings, ComfyUI, STT, TTS. No `TOOL_SERVER_CONNECTIONS`, no `mcpo`. |
| n8n MCP nodes wired | **False** | `/home/user/ods/ods/config/n8n/` holds 19 workflow JSONs; the three RAG ones (`06-rag-demo.json`, `rag-pipeline-trigger.json`, `document-qa.json`) contain only `manualTrigger` + `stickyNote` nodes — they are documentation stubs, not pipelines. |
| Dashboard has MCP UI | **False** | No `mcp` string in `/home/user/ods/ods/extensions/services/dashboard/src/`. |
| APE mediates MCP tool calls | **False** (and worse — see A.3) | APE is a standalone HTTP service nothing calls. |
| Users can add their own MCP servers | **N/A** | No mechanism, no docs, no preservation story. `grep -c mcp /home/user/ods/ods/ods-cli` → `0`. |

The nearest thing ODS has to "user extends the agent's tool surface" is **subtractive**:
`cli-config.yaml.template:101-104` sets `agent.disabled_toolsets: [terminal, browser]`, relying on
Hermes's upstream built-in toolsets. Widening the surface means hand-editing
`data/hermes/config.yaml` per the upstream docs (`docs/HERMES.md:121-130`).

### A.2 Memory in ODS

**memory-shepherd** (`/home/user/ods/ods/memory-shepherd/`) is *not* pruning, compaction, or backup
of a vector store. It is a 338-line bash script implementing a **baseline-reset contract on a
markdown file**:

- The agent's `MEMORY.md` is split by a `---` separator (`memory-shepherd.sh:112`). Above the line
  is operator-owned identity/rules/pointers; below is agent scratch.
- Every 3 hours a systemd timer archives everything below the separator to
  `archives/<agent>/<TIMESTAMP>.md` and atomically restores the baseline
  (`memory-shepherd.sh:151-180`).
- Safety rails: refuses to reset from a baseline under `min_baseline_size` (default 500 bytes,
  `memory-shepherd.sh:134`), full-file backup when the separator is missing (`:172-174`), lock file,
  30-day archive retention.
- Rationale in `README.md:7-13`: agents drift from role, bloat context, and *"sometimes rewrite
  their own instructions"*.

Install wiring is odd: it lives in `/home/user/ods/ods/installers/phases/10-amd-tuning.sh:63-95`
(the AMD tuning phase), enabling `memory-shepherd-workspace.timer` and
`memory-shepherd-memory.timer`. Its shipped baselines still target
`/home/deploy/.../.openclaw/workspace/MEMORY.md` — OpenClaw, the **deprecated** agent
(`ARCHITECTURE.md:63`). So the mechanism is real and well-built but points at a retired surface.

**Hermes memory** is upstream Hermes's own: `HERMES_HOME=/opt/data` mounted from `data/hermes/`
with `sessions/`, `memories/`, `skills/` (`docs/HERMES.md:68-77`). ODS contributes only compression
tuning (`cli-config.yaml.template:171-186`: `threshold: 0.75`, `target_ratio: 0.50`,
`protect_last_n: 40`, each with a paragraph of live-testing rationale). There is no ODS-side memory
API, no cross-agent memory, no namespace model.

**Qdrant + TEI: deployed, unwired.** This is the sharpest memory finding. `qdrant` runs
(`/home/user/ods/ods/extensions/services/qdrant/compose.yaml`), `embeddings` runs TEI
(`extensions/services/embeddings/compose.yaml:3`, `BAAI/bge-base-en-v1.5`), and the README asserts
*"These vectors are stored in Qdrant"* (`extensions/services/embeddings/README.md:7`). But **nothing
writes to Qdrant.** Open WebUI is pointed at TEI for embeddings only
(`docker-compose.base.yml:123-129`: `RAG_EMBEDDING_ENGINE=openai`,
`RAG_OPENAI_API_BASE_URL=http://embeddings:80/v1`) and keeps its own default vector DB — there is no
`VECTOR_DB=qdrant` / `QDRANT_URI` env anywhere in the compose tree. Perplexica, n8n, and Hermes
never reference it. Qdrant is a running, port-bound, API-keyed service with zero consumers.

**Open WebUI local-service wiring** (the part that *does* work, `docker-compose.base.yml:115-167`):
SearXNG search, TEI embeddings, ComfyUI image-gen with an inline SDXL-Lightning 4-step workflow JSON
(`:141-156`), Whisper STT at `http://whisper:8000/v1`, Kokoro TTS at `http://tts:8880/v1`. Every
integration is expressed as OpenAI-compatible base URLs — a clean, copyable pattern.

**Langfuse** is opt-in and reaches LLM traffic only through LiteLLM: the container entrypoint
appends `'langfuse'` to `litellm_settings.success_callback` when `LANGFUSE_ENABLED=true`
(`/home/user/ods/ods/extensions/services/litellm/compose.yaml:49-54`). **token-spy** rides the same
seam as a `callbacks` entry (`:46-47`). Traffic that bypasses LiteLLM (Open WebUI → llama-server
direct, Hermes → llama-server direct) is untraced.

### A.3 Agent tool governance: APE

APE (`/home/user/ods/ods/extensions/services/ape/main.py`, 1078 lines) is a genuinely sophisticated
policy engine — and **nothing calls it**.

The design is five layers evaluated in order in `POST /verify` (`main.py:793-951`):

1. **Circuit breaker** (`main.py:574-612`) — trips to deny-all when the deny ratio over a rolling
   300s window exceeds 0.5 with ≥20 samples; tripped state persists across restarts.
2. **Legacy per-minute rate limit** (`main.py:437`).
3. **Intent classification** (`main.py:635-654`) — verb-token matching on the tool name, falling
   back to arg-shape inference (`command`/`cmd` → ExecuteCommand, `url` → NetworkFetch).
4. **Policy evaluation** (`main.py:659-695`) — four modes: `allow`, `deny`, `allowlist` (base-command
   allowlist + regex deny patterns), `path_guard` (realpath prefix check).
5. **Windowed multi-tier caps** (`main.py:482`) — per-intent 5min/hour/day limits, each tier either
   `deny` or `require_approval`.

The `require_approval` tier is the best-engineered piece. `/verify` mints an `approval_token` bound
to an **args fingerprint** (`main.py:891-908`); `/approve` (`main.py:954-1010`) consumes the token
and mints a **one-shot grant** keyed to `{session, tool, intent, args_hash}`. The next matching
`/verify` consumes the grant (strictly one-shot, deleted on use), counts the retry *against* the
window so the bypass costs a sample, and re-escalates on a second retry (`main.py:857-889`). This is
notably tighter than a plain "approve tool X" model.

Everything lands in an append-only `audit.jsonl` (`main.py:700-706`) including
`grant_consumed`/`approver` provenance. State is persisted under a process lock plus advisory
`flock` with bounded sample lists (`main.py:255-262`).

**But**: `APE_STRICT_MODE` defaults to `false` (`compose.yaml:14`), which logs without blocking
(`main.py:107-108` warns about exactly this). The manifest calls it a "Drop-in governance layer for
OpenClaw" (`manifest.yaml:26`) — OpenClaw is deprecated. A tree-wide grep for callers of `:7890`
or `/verify` outside `extensions/services/ape/` finds only dashboard health/restart plumbing
(`dashboard-api/config.py:544`, `settings.py:37`). Hermes has no APE hook. The `path_guard`
allowed_paths were even updated for Hermes's `/opt/data/{workspace,skills,memories,sessions,...}`
(`config/ape/policy.yaml:53-60`) — someone maintained the policy for an integration that was never
built.

### A.4 The one genuinely excellent ODS pattern: generated-config discipline

`/home/user/ods/ods/docs/INSTALLER-ARCHITECTURE.md:118-131` carries a **Generated Config Writers**
table: every config surface × {Linux writer, macOS writer, Windows writer, upgrade/runtime writer}.
The framing line is *"When a bug involves generated config, check every writer before calling the
fix done. This is the most common way install-time surprises survive a patch."*

Two implementations of that discipline are directly reusable:

**`scripts/patch-hermes-config.py`** (324 lines) — a line-oriented, block-scoped YAML patcher that
never round-trips through a YAML parser (so comments survive). `_top_level_block` / `_child_block` /
`_set_key` (`:17-59`) locate a block by indentation and mutate only inside it. Crucially it encodes
**two distinct merge policies**:

```python
# operator wins — only fill a missing key (patch-hermes-config.py:114-115)
if max_tokens and not _has_key(lines, block, "max_tokens", 2):
    _set_key(lines, block, "max_tokens", str(max_tokens), 2)

# ODS converges — unconditionally re-assert (patch-hermes-config.py:215-218)
block = _set_key(lines, block, "enabled",      "true", 2)
block = _set_key(lines, block, "threshold",    "0.75", 2)
block = _set_key(lines, block, "target_ratio", "0.50", 2)
_set_key(lines, block, "protect_last_n",       "40",   2)
```

It returns `changed: bool` and writes only on diff (`:291-294`) — idempotent by construction. It is
invoked against *both* the shipped template and the live config (`scripts/bootstrap-upgrade.sh:1491-1506`),
plus an in-container variant (`:1508-1518`).

**The OpenCode jq merge** (`installers/phases/07-devtools.sh:225-274`) does the same for JSON with a
three-way fallback: fresh-write if absent → surgical `jq` merge owning only `.provider["llama-server"]`
→ regenerate-from-template if the existing file is unparseable (issue #332).

---

## B. hal0 today

hal0's MCP surface is substantially larger than the brief describes.

**Two hosted servers.** `hal0-admin` at `/mcp/admin` and `hal0-memory` at `/mcp/memory`, mounted as
FastMCP sub-ASGI apps by `/home/user/hal0/src/hal0/api/mcp_mount.py`. That file solves a real
problem well: FastMCP runs tool handlers in a lifespan-scoped anyio task group, so a Starlette
middleware contextvar never reaches the handler — every write silently collapsed to the `shared`
namespace (`mcp_mount.py:15-27`, issue #413). The fix reads headers off the MCP SDK's own
`request_ctx` at call time. It also exposes `HAL0_MCP_ALLOWED_HOSTS` / `HAL0_MCP_ALLOWED_ORIGINS` to
widen FastMCP's automatic localhost-only DNS-rebinding lockdown (`:71-121`).

**Three-tier tool gating.** `AUTONOMOUS_READ_TOOLS` / `AUTONOMOUS_WRITE_TOOLS` / `GATED_TOOLS` in
`src/hal0/mcp/admin.py`; gated calls enqueue into an in-memory `ApprovalQueue`
(`src/hal0/mcp/approval_queue.py`) and return `{"status": "pending_approval", "approval_id": ...}`.
The queue dedups on `(tool_name, primary_target)` with a `hit_count`
(`approval_queue.py:16-22, 59-69`) so a retrying agent doesn't flood the inbox. The owner resolves
via `/api/agent/approvals/{id}/{approve,deny}` with an SSE tail that replays the pending set on
subscribe (`src/hal0/api/routes/approvals.py:13-21`). **`approve` executes the bound executor**
(`approval_queue.py:30-33`).

**Default-deny MCP client policy.** `src/hal0/agents/mcp_client.py` — pure, synchronous, no wire IO.
`enabled_servers()` (`:137-145`) is the server axis; `classify()` (`:149-180`) is the tool axis,
checking `blocked` → `gated` → `allow` → `unknown_tool` (default-deny). `guard()` raises
`ToolNotPermittedError` on hard-reject. `rewrite_path()` (`:235-258`) resolves filesystem-MCP args
against the workspace and raises `WorkspaceEscapeError` on `../` or out-of-tree absolutes. The
schema (`src/hal0/config/schema.py:2726-2838`) enforces `allow`/`gated`/`blocked` **disjointness at
load time** with the offending tool named (`ToolPolicy.lists_are_disjoint`, `:2766-2793`) — no
silent "which check wins" behaviour. `MCPServerConfig` requires a URL for non-builtin servers
(`:2833-2838`); auth is `bearer-from-env` only, so tokens never live in TOML (`:2682-2723`).

**A user-installed server registry that already exists.** `src/hal0/mcp/installed.py` (issue #305)
persists one TOML per server at `/etc/hal0/mcp-servers/<id>.toml` with `install` / `uninstall` /
`patch_config` / `list_installed`, `fcntl` advisory locking on read-modify-write (`:261-278`, issue
#382), 0700/0600 perm hardening because the `env` block holds API keys (`:111-133`), a tight id
charset, and bundled-id reservation (`:139-161`).

**A REST API and a CLI.** `src/hal0/api/routes/mcp.py` (945 lines) serves
`GET /servers|clients|catalog|resolve|stream|{id}/logs`, `POST /install`, `DELETE /{id}`,
`PATCH /{id}/config`. `src/hal0/cli/mcp_commands.py` (376 lines) gives
`hal0 mcp list|status|install|uninstall|restart` and `hal0 mcp catalog list|refresh`.
`src/hal0/mcp/manifest.py` resolves `oci://`, `npm:`/`npx:`, `uvx:`/`uv:`, `git+https://`, and
generic manifest URLs behind an SSRF guard that blocks loopback/private/link-local/CGNAT and refuses
redirects (`:69-79`), with a 256 KiB body cap (`:65`).

**Hermes wiring.** `_phase_mcp_wire` (`src/hal0/agents/hermes_provision.py:2840`) reads the per-agent
allow-list at `/etc/hal0/agents/hermes.toml`, probes each server both raw *and through the Hermes
venv's own MCP client*, and hard-fails when the client can't connect to a provably live server
(issue #2021 — "server-side reachability alone is NOT wiring"). The rendered set is written via
`hermes config set` key-value pairs (`:2012-2021`):
`mcp_servers.<n>.{type,url,headers.X-hal0-Agent,timeout}` plus a bearer. The seed write **merges,
never clobbers, operator `[mcp.servers.*]` blocks** (`:248-250`).

**Memory.** An explicit `MemoryProvider` ABC (`src/hal0/memory/provider.py:1-14`) as the anti-lock-in
seam, Hindsight as the only engine (`ARCHITECTURE.md:404`), a closed two-valued namespace grammar
(`src/hal0/memory/namespace.py`, ADR-0005) that fails **closed** when a read resolves to no
addressable bank (`:37-43`, issue #1451), and a Hermes-side plugin
(`installer/agents/hermes/plugins/hal0-memory/`) that treats recalled material as *untrusted data,
never instruction* and never interpolates it into a system/tool position (`provider.py:14-18`).

**Known memory gaps** (per the brief; #1833/#1834 corroborated in `CHANGELOG.md:1869-1872, 2320-2324`):
no document-ingest endpoint (#2016), a fresh uuid4 doc per turn (#2017), an uncapped extraction
queue that fails to drain on CPU-only boxes (#1834), `memory_add` with no preflight (#1930), and no
guide for pointing external coding agents at hal0 memory (#2153).

**The three real gaps in MCP:**

1. **No supervisor.** `POST /api/mcp/{id}/{action}` raises `McpNotImplemented` with code
   `mcp.supervisor_unavailable` "pending ADR-0015" (`routes/mcp.py:934-945`). Installed servers are
   hard-coded `state="stopped"` (`:565`). A `stdio` server therefore never runs.
2. **No exposure wiring.** `/etc/hal0/mcp-servers/*.toml` and `/etc/hal0/agents/*.toml`
   `[mcp.servers.*]` are **two unjoined registries**. Installing a server never reaches Hermes's
   `mcp_servers`, never reaches the allow-list, never reaches brain/OpenWebUI/OpenCode. The install
   route sets `env={k: "" for k in resolved.env_required}` (`:871`) and stops.
3. **No install UI.** `manifest.py:3-6` references "the dashboard's InstallDrawer paste-box";
   `grep -rn InstallDrawer ui/` returns nothing. `ui/src/dash/connections.jsx` renders a **read-only**
   MCP panel plus config-builders that export hal0's servers *outbound* to Claude Desktop / Codex /
   Cursor (`:688-704`).

---

## C. Better / worse / equivalent

| Axis | Verdict | Why |
|---|---|---|
| MCP support | **hal0 vastly better** | ODS: zero. hal0: 2 hosted servers, registry, REST, CLI, resolver, SSRF guard, per-agent policy. Nothing to port. |
| Tool-call policy model | **hal0 better** | hal0's server-axis + tool-axis default-deny with load-time disjointness beats APE's verb-heuristic intent classifier. APE's `classify_intent` guesses from the tool *name* (`main.py:636`); hal0 matches exact `(server, tool)`. |
| Policy *enforcement reach* | **ODS worse (nil), hal0 partial** | APE is wired to nothing. hal0's `AgentMCPClient` is only consulted by the two bundled servers' own gating plus Hermes's allow-list gate; a third-party MCP server would be un-mediated because it never runs. |
| Rate limiting / circuit breaking | **ODS better** | hal0 has no equivalent to APE's sliding-window per-intent caps or its deny-ratio circuit breaker. |
| Approval model | **Roughly equivalent, different strengths** | hal0 dedups + executes on approve + SSE inbox, but is **in-memory only** (`approval_queue.py:11-14`) so a restart drops the queue. APE persists approvals *and* mints args-fingerprinted one-shot grants that count against the window — strictly tighter, but it can't execute anything. |
| Audit | **hal0 better** | Structured `hal0.mcp.audit` events through structlog→journald, consumed by `GET /api/mcp/stream` and `/{id}/logs`. APE's `audit.jsonl` is append-only text with no reader other than `GET /audit`. |
| Memory architecture | **hal0 vastly better** | ODS memory is upstream Hermes files + a markdown-reset timer + an orphaned Qdrant. hal0 has a provider ABC, a closed namespace grammar with fail-closed reads, private/shared banks, curation/mental-models/directives, and prompt-injection-aware recall framing. |
| Memory *reliability* | **ODS arguably better within its scope** | memory-shepherd's contract is trivially correct and observable. hal0's extraction queue is documented as unreliable on CPU-only boxes (#1834). Simple and working beats sophisticated and flaky. |
| Config-generation discipline | **ODS better** | The Generated Config Writers table + `patch-hermes-config.py`'s explicit operator-wins/ODS-converges split is a discipline hal0 lacks a named equivalent for. |
| RAG plumbing | **ODS worse than it looks** | Qdrant + TEI deployed, only TEI consumed, README claims otherwise. A documentation-vs-source divergence of exactly the kind hal0's CLAUDE.md warns about. |

---

## D. Port candidates

Ranked by value/risk. **Nothing on the MCP axis ports from ODS** — these are memory/governance/
process-discipline items.

### D1. Block-scoped idempotent config patcher — **HIGH value, LOW risk, ~150 LOC**

- **From:** `/home/user/ods/ods/scripts/patch-hermes-config.py:17-59, 114-115, 215-218, 269-294`
- **To:** new `src/hal0/agents/hermes_config_patch.py`, called from `hermes_provision._phase_config_write`
- **Why:** hal0 writes Hermes config through `hermes config set` key-value pairs
  (`hermes_provision.py:2012-2021`). That is fine for scalars but has no way to *remove* a stale
  `mcp_servers.<name>` block when a user uninstalls a server, and no way to express "operator wins"
  vs "hal0 converges" per key. The ODS patcher gives both, plus comment preservation.
- **Borrow specifically:** the `_has_key` guard (operator wins) vs unconditional `_set_key` (hal0
  converges) distinction, and the `changed: bool` / write-only-on-diff contract.
- **Risk:** low — pure function over lines, trivially unit-testable.

### D2. Generated Config Writers table — **HIGH value, ZERO risk, docs only**

- **From:** `/home/user/ods/ods/docs/INSTALLER-ARCHITECTURE.md:118-131`
- **To:** a new section in `/home/user/hal0/ARCHITECTURE.md`, or `docs/reference/paths-and-files.mdx`
- **Why:** hal0 has more generated-config surfaces than ODS (`/etc/hal0/agents/*.toml`,
  `/etc/hal0/mcp-servers/*.toml`, `hermes.env`, `api.env`, `runtime.json`, the Hermes `config.yaml`
  overlay) written from at least three places (installer, `hermes_provision`, REST routes). One table
  naming every writer per surface is the cheapest high-value doc in this report — and it becomes
  load-bearing the moment the MCP registry starts writing into Hermes config (§E).

### D3. Windowed rate caps + circuit breaker for gated tools — **MEDIUM value, MEDIUM risk, ~250 LOC**

- **From:** `/home/user/ods/ods/extensions/services/ape/main.py:482-536` (windowed limits),
  `:574-612` (breaker), `:255-262` (bounded sample lists)
- **To:** `src/hal0/mcp/approval_queue.py` or a sibling `src/hal0/mcp/limits.py`
- **Why:** hal0's approval queue dedups but does not *cap*. An agent in a retry loop against a
  distinct-args tool generates unbounded approval rows. Per-tool-class sliding windows that escalate
  to approval before hard-denying map cleanly onto hal0's existing three tiers.
- **Adapt, don't copy:** key on hal0's `(server, tool)` pair, not APE's guessed intent class.

### D4. Persisted, args-fingerprinted one-shot approval grants — **MEDIUM value, LOW risk, ~80 LOC**

```python
# ODS: extensions/services/ape/main.py:973-989 — the grant is bound to the exact call
gkey = _grant_key(rec["session"], rec["tool_name"], rec["intent"], rec["args_hash"])
_state.setdefault("grants", {})[gkey] = {..., "approver": req.approver, ...}
```

- **To:** `src/hal0/mcp/approval_queue.py`
- **Why:** hal0's queue is explicitly in-memory (`approval_queue.py:11-14`) — an `hal0-api` restart
  drops pending approvals silently. Persisting the pending set (and binding an approval to an args
  hash so an approved `model_pull qwen3:0.6b` cannot authorise `model_pull something-else`) is a
  small, self-contained hardening. The module's own docstring already anticipates it: *"A future ADR
  can promote this to a persisted table."*

### D5. Baseline/scratch separator for self-edited agent files — **LOW value, note only**

`memory-shepherd`'s `---` contract (`README.md:133-148`, `memory-shepherd.sh:151-180`) only applies
if hal0 ever lets an agent self-edit a persona/SOUL file. It currently does not (`personas.py` is
hal0-authored). Record, don't port.

---

## E. Design: user-added MCP servers for hal0

The owner's ask — *"a custom way to add MCP servers rather than just be stuck with the originals
that we ship"* — is **80% built**. The work is finishing three things: a **supervisor**, an
**exposure join**, and a **`test` verb**. Do not build a new registry file format; `/etc/hal0/mcp-servers/<id>.toml`
already exists, is atomic-written, perm-hardened, and lock-protected.

### E.1 Schema: extend `InstalledServer`, don't replace it

Current fields (`src/hal0/mcp/installed.py:57-91`): `id, name, description, spec, transport, tools,
resources, prompts, env, enabled, installed_at, source_url, author, verified`.

Add five, all defaulted so existing records keep validating:

```toml
# /etc/hal0/mcp-servers/github.toml
id           = "github"
name         = "GitHub MCP"
spec         = "npm:@modelcontextprotocol/server-github"
transport    = "stdio"                 # stdio | streamable-http | sse   (NEW: sse)
command      = "npx"                   # NEW — stdio only
args         = ["-y", "@modelcontextprotocol/server-github"]   # NEW
url          = ""                      # http/sse only (already implied by transport)
enabled      = true

[env]                                  # existing — literal values, 0600
GITHUB_HOST = "github.com"

[secrets]                              # NEW — indirection, never a literal
GITHUB_TOKEN = "GITHUB_MCP_TOKEN"      # name of a key in /etc/hal0/api.env

[tools]                                # NEW — mirrors ToolPolicy exactly
allow   = ["search_repositories", "get_file_contents"]
gated   = ["create_pull_request"]
blocked = ["delete_repository"]

[exposure]                             # NEW — which consumers see this server
hermes    = true
brain     = false
openwebui = false
opencode  = false
```

Design notes, each tied to an existing hal0 decision:

- **`[secrets]` is a *reference*, not a value.** hal0 already has a secrets store at
  `/etc/hal0/api.env` behind `/api/settings/secrets` with an `^[A-Z][A-Z0-9_]{0,63}$` name gate
  (`src/hal0/api/routes/secrets.py:129-143`), and the agent-config schema already refuses to hold
  tokens in TOML (`AgentAuthConfig`, `config/schema.py:2682-2689`). Keeping literal `env` for
  non-secret settings and `secrets` for indirection preserves both rules. **New vs both repos.**
- **`[tools]` reuses `ToolPolicy` verbatim** (`config/schema.py:2726`), inheriting the
  disjointness validator and the empty-by-default posture. A freshly installed server therefore has
  **zero callable tools** until the operator promotes some — which is exactly the documented intent
  of `ToolPolicy`'s docstring (`:2737-2740`). This is the "default-deny + approvals for dangerous
  tools" requirement, satisfied by *reuse*, not new code.
- **`[exposure]` is new.** It is the join that currently doesn't exist. `hermes` and `brain` are the
  two hal0-side consumers that can be honoured immediately; `openwebui` / `opencode` should ship
  **disabled and unimplemented** rather than silently ignored — return `501 mcp.exposure_unsupported`
  if set, following the precedent of `McpNotImplemented` (`routes/mcp.py:57-69`), which is a pattern
  worth keeping because the dashboard can key on the code.

### E.2 Supervisor (the ADR-0015 gap)

`stdio` servers need a process. Follow hal0's own bundled-agent precedent — sandboxed sibling
systemd units (`ARCHITECTURE.md:395-397`) — rather than inventing a supervisor:

- One transient unit per enabled `stdio` server: `hal0-mcp@<id>.service`, `User=hal0`,
  `ProtectSystem=strict`, `LoadCredential=` for each `[secrets]` entry (which is how
  `AgentAuthConfig`'s `bearer-from-env` tokens already arrive, `mcp_client.py:199-231`),
  `ReadWritePaths=` limited to the server's own scratch dir.
- `POST /api/mcp/{id}/{start,stop,restart}` replaces the 501 with `systemctl --user`-equivalent
  calls; `list_servers` replaces the hard-coded `state="stopped"` (`routes/mcp.py:565`) with the
  real unit state.
- `http`/`sse` servers need no supervision — they are already reachable; only the exposure join and
  the tool policy apply.

**Write ADR-0015 first.** `routes/mcp.py:943` already promises it, hal0's `CLAUDE.md` requires an ADR
before changing behaviour it covers, and `docs/adr/` currently holds only 0001, 0002, 0003, 0005,
0006 while source cites 0004, 0008, 0012, 0013, 0015, 0020 and **0023 sixty times**. (See §G.)

### E.3 Exposure wiring — the Hermes join, done the ODS way

This is where the ODS port earns its place. On any registry mutation (install / uninstall /
patch / enable / disable), regenerate the Hermes `mcp_servers` block **idempotently and
surgically**, exactly as `patch-hermes-config.py` does:

1. Build the desired set = `_default_mcp_servers()` (`hermes_provision.py:1462-1497`) **plus** every
   installed record with `enabled = true` and `exposure.hermes = true`.
2. For each, write `mcp_servers.<id>.{type,url,timeout}` + `headers.X-hal0-Agent` + bearer, reusing
   the existing loop at `hermes_provision.py:2012-2021`.
3. **Remove** `mcp_servers.<id>` blocks that hal0 previously wrote and no longer wants — the
   capability `hermes config set` cannot express, and the reason D1 is a prerequisite rather than a
   nice-to-have.
4. Mirror the record's `[tools]` into `/etc/hal0/agents/hermes.toml` `[mcp.servers.<id>]` so
   `AgentMCPClient.classify()` (`mcp_client.py:149`) governs the new server on the same two axes as
   the bundled ones. Preserve the existing merge-never-clobber contract
   (`hermes_provision.py:248-250`).
5. Re-run the `mcp_wire` probe for the new server only, so a bad URL surfaces at install time rather
   than at first agent turn.

**Ownership marker.** Write hal0-managed blocks between sentinel comments
(`# >>> hal0 mcp registry — do not edit <<<` … `# >>> end <<<`) so step 3 can delete precisely what
hal0 owns and never touch an operator's hand-added block. This is a small addition to the ODS
block-scoping approach, which locates blocks by key name only.

### E.4 CLI

`hal0 mcp list|status|install|uninstall|restart` already exist (`cli/mcp_commands.py:72,119,204,239,271`).
Additions:

- `hal0 mcp test <id>` — **new**. Runs the existing probe path
  (`hermes_provision._probe_mcp_server`, already reused by `cli/doctor_all.py:806-828`) against one
  server and prints the advertised tool list with each tool's current `allow`/`gated`/`blocked`/
  `unknown_tool` verdict from `classify_many` (`mcp_client.py:264-273`). This is the single highest-
  value new verb: it turns "I added a server" into "here is exactly what it can do and what I have
  permitted".
- `hal0 mcp allow|gate|block <id> <tool>` — **new**. Edits `[tools]` through `patch_config`, which
  already holds the lock (`installed.py:281-314`).
- `hal0 mcp expose <id> --hermes/--no-hermes` — **new**. Flips `[exposure]` and triggers §E.3.
- Keep `add` as an alias of `install` and `remove` as an alias of `uninstall` if the owner prefers
  that vocabulary; do not rename the existing verbs.

### E.5 API and dashboard

REST needs three additions to `src/hal0/api/routes/mcp.py`:
`POST /{id}/test` (probe + classify), `PATCH /{id}/tools`, `PATCH /{id}/exposure`. The existing
`PATCH /{id}/config` already covers env/enabled.

Dashboard: build the missing `InstallDrawer` that `manifest.py:3-6` already assumes — paste a spec,
`GET /api/mcp/resolve` for the preview (SSRF-guarded, `manifest.py:69-79`), edit env/secrets,
install. Then extend the existing `McpServerRow` (`ui/src/dash/connections.jsx:727`) — which already
renders a per-tool drawer — with a three-state control per tool writing to `PATCH /{id}/tools`, and
an exposure toggle row. Reuse `useMcpServers` (`ui/src/api/hooks/useMcp.ts:126`).

### E.6 Provenance

| Element | Source |
|---|---|
| Per-server TOML registry, atomic write, 0600, lock | **hal0, existing** — `src/hal0/mcp/installed.py` |
| Three-tier tool policy + disjointness | **hal0, existing** — `src/hal0/config/schema.py:2726` |
| Approval inbox for gated tools | **hal0, existing** — `src/hal0/mcp/approval_queue.py` |
| Manifest resolve + SSRF guard | **hal0, existing** — `src/hal0/mcp/manifest.py` |
| Idempotent block-scoped config regeneration, operator-wins vs converges | **ODS, ported** — `scripts/patch-hermes-config.py:114-115, 215-218` |
| Three-way fallback on unparseable generated config | **ODS, ported** — `installers/phases/07-devtools.sh:225-274` |
| Writers-per-surface documentation table | **ODS, ported** — `docs/INSTALLER-ARCHITECTURE.md:118-131` |
| Windowed caps / circuit breaker (optional, D3) | **ODS, adapted** — `extensions/services/ape/main.py:482-612` |
| `[secrets]` indirection block | **New** |
| `[exposure]` targets block | **New** |
| Sentinel-delimited hal0-owned config region | **New** (extends ODS block-scoping) |
| `hal0 mcp test` | **New** |
| systemd-unit-per-stdio-server supervisor | **New** (follows hal0's bundled-agent precedent) |

---

## F. Do not copy

1. **APE as a deployed service.** A policy engine nothing calls is worse than no policy engine: it
   consumes a port, a container, and a maintained policy file (`config/ape/policy.yaml` was updated
   for Hermes paths that never route through it) while providing zero enforcement. hal0's policy
   belongs *in the call path* (`AgentMCPClient`), where it already is. Port APE's *algorithms* (D3,
   D4), never its topology.
2. **`APE_STRICT_MODE=false` as a default** (`extensions/services/ape/compose.yaml:14`). Advisory-by-
   default governance trains operators to ignore it. hal0's default-deny posture is correct;
   preserve it.
3. **Intent classification by tool-name verb tokens** (`ape/main.py:635-654`). Guessing that a tool
   named `create_pull_request` is a `WriteFile` (it contains "create") is a category error. hal0's
   exact `(server, tool)` matching is strictly better.
4. **Deploying a vector DB with no consumer**, and **documenting it as if it had one.** Qdrant in
   ODS is a running, API-keyed, port-bound service with zero writers, while
   `extensions/services/embeddings/README.md:7` states "These vectors are stored in Qdrant". hal0's
   own `CLAUDE.md` names this failure mode; ODS is a live example. Do not add infrastructure ahead
   of a consumer.
5. **memory-shepherd's periodic destructive reset** as a model for hal0 memory. Hindsight-backed
   memory is the durable record; a timer that truncates it on a schedule would be a data-loss
   feature. The *separator contract* is portable (D5); the *reset* is not.
6. **Wiring memory into the AMD tuning phase** (`installers/phases/10-amd-tuning.sh:63-95` installs
   the memory-shepherd timers) — unrelated concerns in one phase, and presumably no timers at all on
   non-AMD hardware.

---

## G. Owner decisions

1. **Confirm the ODS-MCP finding.** ODS contributes **nothing** to hal0's MCP story. If the owner
   expected otherwise, the expectation may come from a newer ODS branch than the one on disk
   (`ARCHITECTURE.md` header: *Version 2.6.0*). Worth a one-line confirmation before any further
   ODS-MCP archaeology is commissioned.
2. **Write ADR-0015 (MCP process supervisor) — blocking.** `routes/mcp.py:943` promises it by name.
   Decide: systemd-unit-per-server (recommended, matches `ARCHITECTURE.md:395-397`) vs an in-process
   subprocess pool vs http/sse-only (drop `stdio` support entirely, which halves the work and covers
   most hosted MCP servers).
3. **Resolve the ADR bookkeeping gap.** Source cites ADR-0004, 0008, 0012, 0013, 0015, 0020, and
   **0023 (60 times)**; `docs/adr/` holds only 0001, 0002, 0003, 0005, 0006. Separately,
   `ARCHITECTURE.md:392-393` states *"hal0 keeps no ADR tree"* while `CLAUDE.md` says
   *"`docs/adr/` holds the accepted decision records"*. One of those is stale. This matters here
   because the MCP supervisor decision is supposed to land as an ADR.
4. **Scope of `[exposure]`.** Ship `hermes` + `brain` only in v1, with `openwebui`/`opencode`
   returning `501 mcp.exposure_unsupported`? Or defer the whole exposure block and wire Hermes
   unconditionally (simpler, but re-creates the "one hard-coded consumer" shape the owner is trying
   to escape)?
5. **`[secrets]` vs `[env]`.** Confirm that MCP server credentials should live in `/etc/hal0/api.env`
   alongside hal0's own secrets, rather than in a separate `/etc/hal0/mcp-secrets.env`. The former
   reuses `/api/settings/secrets` and its name validation for free; the latter isolates third-party
   credentials from hal0's own. Recommend the former.
6. **Persist the approval queue now or later?** D4 is ~80 LOC and closes a real hole (restart drops
   pending approvals). It becomes more urgent once third-party servers can enqueue gated calls,
   because the blast radius of a silently-dropped approval widens.
7. **Adopt the Generated Config Writers table (D2)?** Zero-risk docs, but it only pays off if it is
   *maintained*. It should probably become a `CONTRIBUTING.md` checklist item ("did you update the
   writers table?") rather than a standalone doc that rots.
8. **Rate-limit gated MCP tools (D3)?** Genuine hardening, ~250 LOC, and the only axis where ODS is
   ahead. Reasonable to defer until a third-party server actually runs.
9. **Memory issues are out of scope for MCP-adds but interact with it.** #1930 (`memory_add` no
   preflight) and #1834 (uncapped extraction queue) both get worse when more MCP clients can write.
   Consider sequencing at least #1930 before third-party servers gain `memory_*` access.
