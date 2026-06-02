# Brain Redesign — 04: Consumer Surface

> The complete map of every "consumer of the brain" in hal0: every agent,
> app, MCP surface, slot/capability, and dashboard view that could **produce**
> or **consume** memory / wiki / context. Feeds the memory-system redesign
> (Hindsight engine + Obsidian wiki) whose goal is *deep, non-bolted-on*
> integration.
>
> Scope verified against the repo at `/home/halo/dev/hal0` on 2026-06-02.
> Citations are `file:line`. Where a thing is aspirational vs shipped it is
> flagged explicitly.

---

## 0. TL;DR — the brain has exactly one front door today

Everything that touches durable memory in hal0 today funnels through **one
in-process Cognee store**, reachable via two equivalent transports:

- **MCP**: `hal0-memory` FastMCP server mounted at `/mcp/memory`
  (`src/hal0/api/mcp_mount.py:161`), tools `memory_add` / `memory_search` /
  `memory_list` / `memory_delete` (`src/hal0/mcp/memory.py:331-336`).
- **REST**: `/api/memory/*` (`src/hal0/api/routes/memory.py`, mounted at
  `prefix="/api/memory"` per `src/hal0/api/__init__.py:878`).

Both resolve the **per-caller namespace server-side** from the `X-hal0-Agent`
header (REST: `src/hal0/api/routes/memory.py:48`; MCP: Bearer/client-id
resolver) — clients never send a `dataset` field (issue #317;
`src/hal0/agents/hermes/plugins/memory_cognee/README.md:21`). Namespaces:
`shared` (default), `private:<agent_id>` (per-agent), and `agents` (identity
cards, ADR-0011).

The store itself is `CogneeWrapper` (`src/hal0/memory/cognee_wrapper.py`):
SQLite + LanceDB (vectors) + Kuzu (graph), all embedded, file-based
(`cognee_wrapper.py:90-92`). Embeddings default to **fastembed CPU /
bge-small-en-v1.5 384-dim** (`cognee_wrapper.py:167-169`); graph extraction is
**default-OFF** behind ADR-0014's model gate (`cognee_wrapper.py:170`,
`PLAN.md:347-351`). Reranking optionally posts to **hal0's bundled embed-rerank
slot on port 8086** (`cognee_wrapper.py:209-214`).

**Implication for the redesign:** if the new brain (Hindsight + Obsidian)
preserves the `/mcp/memory` + `/api/memory/*` contract and the
`X-hal0-Agent` namespace rule, *every consumer below inherits it for free*.
The hook points are the wrapper internals and a small number of
context-injection seams, not the consumer surface.

---

## 1. Agents

`BUNDLED_AGENTS = ("pi-coder", "hermes")` (`src/hal0/agents/manager.py:87`).
Single-pick at install (`manager.py:223-265`); swap is atomic
uninstall-then-install. The manager delegates to per-agent driver modules
looked up by `_driver_for` (`manager.py:142-156`).

### 1.1 Hermes-Agent (`hermes`) — the deep case

Hermes is the service-shape bundled agent, installed via the hal0-owned
`hal0-hermes` wrapper around upstream `hermes`
(`src/hal0/agents/hermes/driver.py:1-23`). hal0 cannot PR upstream, so all
hal0-awareness lives in hal0-owned artifacts: the wrapper, the env file, a
vendored MemoryProvider plugin, and rendered context files.

**How Hermes gets context today — four distinct channels:**

1. **`MemoryProvider` plugin (`hal0-cognee`)** — the deepest hook. Vendored at
   `src/hal0/agents/hermes/plugins/memory_cognee/` and copied into
   `$HERMES_HOME/plugins/memory/hal0-cognee/` at provision time
   (`hermes_provision.py:_phase_install`). It subclasses upstream
   `agent.memory_provider.MemoryProvider` (`provider.py:36`) and implements:
   - `system_prompt_block()` — injects a "you have a durable memory store"
     preamble naming the `private:<agent_id>` namespace (`provider.py:128-135`).
   - `prefetch(query)` — **READ**: calls `/api/memory/search` with a 5-item
     budget on every turn, formats hits as a `## hal0-cognee memory` block
     prepended to context (`provider.py:137-161`). *This is the live
     context-injection seam.*
   - `sync_turn(user, assistant)` — **WRITE**: fire-and-forget
     `/api/memory/add` of each turn, tagged `["chat","hermes"]`, skipped for
     `cron`/`flush`/`subagent` contexts (`provider.py:163-182`, `:66`).
   - `on_memory_write(...)` — mirrors Hermes's *built-in* memory tool writes
     into hal0-cognee (`provider.py:207-224`).
   - `get_tool_schemas()` returns `[]` deliberately — memory is surfaced via
     prompt + prefetch, not model-visible tools, to avoid double-registering
     against the MCP path (`provider.py:186-195`).
   - Identity flows via `X-hal0-Agent` from `HAL0_AGENT_ID`
     (`provider.py:110-112`); never sends `dataset`.

2. **MCP servers** — the wrapper's env file
   (`src/hal0/agents/hermes/driver.py:332-346`) wires
   `HAL0_MCP_ADMIN_URL=.../mcp/admin` and `HAL0_MCP_MEMORY_URL=.../mcp/memory`.
   `hermes_provision._phase_config_write` writes these into Hermes
   `config.yaml` `mcp_servers` (`hermes_provision.py:646-647`), and
   `_phase_mcp_wire` verifies they answer `tools/list`
   (`hermes_provision.py:1022`). So Hermes has **operator-grade CRUD** on
   memory via `hal0_memory` MCP *in addition to* the implicit provider path.

3. **Rendered context files** — `_phase_context_link`
   (`hermes_provision.py:1230`) renders three Jinja2 templates from the *live
   host snapshot* and writes them to disk:
   - `HERMES.md` → `/etc/hal0/HERMES.md` (`hermes_provision.py:1317-1319`),
     auto-injected every session because Hermes's terminal cwd is `/etc/hal0`.
     Advertises Memory MCP, Admin MCP, active capability slots, peer agents,
     skills (`src/hal0/agents/hermes_templates/HERMES.md.j2`).
   - `AGENTS.md` → `/etc/hal0/AGENTS.md` (`hermes_provision.py:1331-1333`) —
     the **tool-agnostic** context file (see 1.2).
   - `SOUL.md` → under `$HERMES_HOME` (identity/persona prelude).
   - `$HERMES_HOME/memories/HOST.md` is a symlink to `/etc/hal0/HERMES.md`
     (`hermes_provision.py:1237`) — folds the host snapshot into Hermes's own
     memory tier.

4. **Personas** (`src/hal0/agents/personas.py`, `persona.py`) — hal0's
   user-facing layer over Hermes's `system_prompt_prelude` + tool gating +
   **memory namespacing** (`personas.py:1-8`). TOML at
   `/var/lib/hal0/agents/hermes/personas/<id>.toml`; the active persona is read
   during system-prompt injection. A persona can therefore scope which memory
   namespace a turn reads/writes.

5. **Identity cards** — `_phase_identity_card` (`hermes_provision.py:1386-1415`)
   writes a self-describing card (`roles: [..., "memory-curator"]`,
   `accepts_tasks_from: ["claude-code","pi-coder","user"]`) into the `agents`
   Cognee dataset via `_mcp_memory_call` (`hermes_provision.py:1415-1434`).
   This is memory **produced about the agent itself**.

**Where a wiki/memory hook attaches for Hermes:** the `prefetch()` seam in
`provider.py:137` is the single richest insertion point — today it returns a
flat list of memory hits; a Hindsight retrieval + an Obsidian-wiki context
block would slot in here verbatim. Secondary: the `HERMES.md.j2` /
`AGENTS.md.j2` templates (add a "wiki index" section), and `SOUL.md` for
durable identity.

### 1.2 Claude Code — support reality: **aspirational, partial scaffolding only**

There is **no `claude-code` driver, install path, or runtime integration** in
hal0. It is not in `BUNDLED_AGENTS`. What exists:

- **Client *identity detection*** (cosmetic): `/api/mcp/clients` heuristically
  renders any MCP client whose `client_id` contains `claude-code` as
  "Claude Code" with role "CLI" (`src/hal0/api/routes/mcp.py:503-504`,
  `:516-523`). This only labels a client that *already connected on its own*.
- **The tool-agnostic context file** `AGENTS.md.j2` is explicitly written
  "Read by any agent that lands in `/etc/hal0/` — Claude Code, Cursor, Codex,
  Hermes" (`src/hal0/agents/hermes_templates/AGENTS.md.j2:1-3`). So if a user
  runs Claude Code with cwd `/etc/hal0`, it gets host context — but hal0 does
  nothing to *make* that happen.
- **The intended path is MCP**: ADR-0004 §122 states "MCP is the cross-app
  contract. Claude Code … integrate without hal0-specific glue. The admin MCP
  server is the public surface; bundled agents are just the first consumers."
  A user's Claude Code points itself at `/mcp/memory` + `/mcp/admin` and gets
  the four memory tools (`docs/internal/adr/0005-memory-engine-cognee.md:11`).
- **A second, planned path**: the Cognee `claude-code` integration — a plugin
  using six lifecycle hooks (SessionStart, UserPromptSubmit, PostToolUse, Stop,
  PreCompact, SessionEnd) + `node_set` tagging — "Gives Claude Code users a
  second path into the same Cognee store, complementary to MCP"
  (`PLAN.md:1044`, `docs/internal/adr/0005-memory-engine-cognee.md:88`).
  **Not implemented in this repo.**

**Verdict:** Claude Code support = (a) it can connect as an MCP client today
and is *recognized* in the dashboard, (b) it can read `AGENTS.md` if pointed at
`/etc/hal0`, (c) deeper lifecycle-hook ingestion is roadmap-only. For the
redesign, Claude Code is a **first-class external MCP consumer** — the brain
should treat "a user's Claude Code / Cursor" as a primary memory client, and
the Cognee-style six-hook plugin is the obvious "deep" upgrade for the wiki.

### 1.3 pi-coder — present but de-emphasized (NOT removed)

`pi-coder` is still in `BUNDLED_AGENTS` and has a full driver
(`src/hal0/agents/pi_coder.py`). Status per MEMORY: "narrowed to Hermes-only"
for v0.2 *promo*, parked in-repo for v0.3 reactivation — the code is live, not
deleted. How it gets context:

- **MCP via pi-mcp-adapter**: the driver writes
  `pi-mcp-adapter.json` pointing at `hal0-admin` (`/mcp/admin`) + `hal0-memory`
  (`/mcp/memory`) with the Bearer token (`pi_coder.py:123-146`). A
  proxy-tool routing layer (~200 tokens/dispatch vs dumping the full catalog).
- **`pi-memory-md` left in place** — project-scoped markdown memory, an
  *upstream* extension. CONTEXT.md is explicit this is a **different scope**
  from hal0's cross-app memory MCP; they coexist, hal0 does not displace it
  (`pi_coder.py:10-12`, `PLAN.md:1041-1042`). *This is a notable precedent: a
  per-project markdown memory living alongside the central store — directly
  analogous to an Obsidian wiki.*

**Hook point:** same as Hermes — pi-coder consumes memory only through MCP, so
the wiki tools would appear automatically. The `pi-memory-md` ↔ Obsidian
analogy is worth mining in the design.

### 1.4 Agent infrastructure (shared)

- `src/hal0/agents/mcp_client.py` — the client side of hal0 reaching *external*
  MCP servers (v0.3 stream #5, ADR-0013 allow-list; `PLAN.md:353-360`).
- `src/hal0/agents/budget.py` — per-agent/persona token budgets.
- `src/hal0/api/agents/` + `src/hal0/api/routes/agents.py` — REST surface
  (`/api/agents`) for list/install/uninstall/switch + bootstrap/repair.

---

## 2. Apps / integrations advertised in README

Grouped from `README.md`:

**Bundled MCP servers (the cross-app contract):**
- `hal0-admin` — slot / model / capability / config / hardware / log admin
  (`README.md:165-168`).
- `hal0-memory` — Cognee-backed long-term memory (`README.md:165-168`).
- Both "reachable by any MCP-speaking client — Claude Code, future RAG
  services, external scripts" (`README.md:166-168`).

**Bundled agent app (single-pick):** `pi-coder` *or* `Hermes-Agent`
(`README.md:162-178`).

**Chat / UI apps:**
- **Dashboard** — React 18 + Vite, with built-in chat page
  (`README.md:126-131`).
- **OpenWebUI** — prewired at `:3001`, installer writes `openwebui.env`
  pointing at the local hal0 API (`README.md:132-133`;
  `src/hal0/openwebui/`).

**Inference / API integrations:**
- **OpenAI-compatible `/v1/*`** — drop-in for any OpenAI SDK
  (`README.md:97-100`; `src/hal0/api/routes/v1.py`). This is how *arbitrary
  external apps* (Continue, Cursor's model backend, scripts) consume hal0.
- **OmniRouter (8 client-side tools)** — `generate_image`, `edit_image`,
  `text_to_speech`, `transcribe_audio`, `analyze_image`, `embed_text`,
  `rerank_documents`, `route_to_chat` (`README.md:134-137`;
  `src/hal0/omni_router/`). `embed_text` + `rerank_documents` are the
  memory-adjacent ones — they expose the embed/rerank slots as tools any
  chat slot can call.
- **Dispatcher** with upstream fallback: OpenRouter, Anthropic, OpenAI, custom
  OpenAI-shaped endpoints (`README.md:123-125`; `src/hal0/dispatcher/`,
  `src/hal0/upstreams/`).

**Roadmap integrations (not shipped):** ChatOps adapters (Slack, Matrix) as
extensions (`README.md:350-351`); multi-host federation (`README.md:341-344`).

---

## 3. MCP host

### 3.1 Bundled servers (baked in)

Mounted at app start by `mount_mcp_servers` (`src/hal0/api/mcp_mount.py:105`):
- `/mcp/admin` → `hal0-admin` (`mcp_mount.py:142`; impl `src/hal0/mcp/admin.py`).
- `/mcp/memory` → `hal0-memory` (`mcp_mount.py:161`; impl `src/hal0/mcp/memory.py`).
  Skipped silently if Cognee isn't installed (`mcp_mount.py:113-117`).

`BUNDLED_SERVER_IDS = {"hal0-admin","hal0-memory"}` are uninstall-protected
(`src/hal0/mcp/installed.py:49`); the route returns `409 mcp.bundled`.

**Destructive-call gating:** capital-D MCP calls (`model_pull`, `slot_delete`,
`config_write`, bulk `memory_delete`, …) route through an approval queue
(`src/hal0/mcp/approval_queue.py`) surfaced as a header bell + inbox modal,
with CLI parity (`README.md:174-177`). `memory_delete` carries
`destructiveHint=True` (`src/hal0/mcp/memory.py:410-412`); bulk deletes gate
at the admin layer (`memory.py:299-312`).

### 3.2 Aftermarket MCP host platform (v0.3-alpha, partially shipped)

The dashboard's `/agents/mcp` page hosts arbitrary user-installed MCP servers:
- **`src/hal0/mcp/installed.py`** — registry of installed servers, one TOML per
  server under `/etc/hal0/mcp-servers/<id>.toml` (`installed.py:1-25`),
  list/install/uninstall/patch. Perms hardened 0600/0700 because env blocks
  hold API keys (`installed.py:113-131`).
- **`src/hal0/mcp/manifest.py`** — resolve-from-URL/spec: `oci://`, `npm:`,
  `uvx:`, `git+https://`, or a live manifest URL, with an **SSRF guard**
  (`manifest.py:68-165`) since the resolver is unauthenticated on the LAN.
- **`src/hal0/mcp/probes.py`** — tool-surface introspection.
- **`src/hal0/api/routes/mcp.py`** — the route layer. Note: **no process
  supervision yet** — installed servers report `state="stopped"` until the
  supervisor lands (`installed.py:16-20`); `POST /{id}/{action}` stubs 501
  (`mcp.py:54-64`). A static `_CATALOG` of installable servers is hardcoded
  (`mcp.py:75+`).

### 3.3 What an external agent sees when it connects

A user's Claude Code / Cursor pointed at hal0's MCP gets:
- From `/mcp/memory`: `memory_add`, `memory_search`, `memory_list`,
  `memory_delete` (annotated read-only/destructive per
  `src/hal0/mcp/memory.py:400-413`).
- From `/mcp/admin`: the full slot/model/capability/hardware/log admin surface,
  with destructive ops gated through the approval queue.
- It is **recognized by name** in the dashboard ClientsRibbon
  (`mcp.py:495-523`) and its activity shows in the MCP audit stream
  (`/api/mcp/stream`).

Identity it presents (`X-hal0-Agent` or Bearer-derived `client_id`) drives its
memory namespace. **This is the single most important external consumer for the
redesign:** the wiki should expose read/search tools here so any IDE agent sees
the Obsidian vault as first-class MCP resources/tools.

---

## 4. Slots / capabilities — embeddings are already a native slot

"Memory" is **not** itself a capability slot. But the substrate a vector/graph
memory needs — embeddings + rerank — **is** native:

- Capability → (slot, type) map: `"embed": ("embed","embed")`,
  `"rerank": ("embed","rerank")` (`src/hal0/capabilities/catalog.py:36-37`).
  The `embed` capability card rolls up two children: `embed` + `rerank`
  (`catalog.py:655-669`).
- Seeded slots include `embed` and `rerank` (`README.md:101-106`). The rerank
  slot lives on **port 8086**, model `bge-reranker-v2-m3-q4_k_m` (MEMORY
  `hal0_rerank_slot_wiring`; confirmed `cognee_wrapper.py:209-214`).
- **The memory store already consumes the rerank slot**: `CogneeWrapper`'s
  optional rerank pass POSTs vector top-N candidates to the bundled embed-rerank
  slot at port 8086 and reorders by relevance before clipping
  (`cognee_wrapper.py:205-225`). Default rerank model matches the slot
  (`cognee_wrapper.py:212-214`).
- Cognee's **embedder** is currently fastembed-CPU (bge-small, 384-dim,
  `cognee_wrapper.py:167-169`); PLAN flags switching to **bge-on-iGPU** as a
  perf-only follow-up (`PLAN.md:1088`). That is the embed slot.
- **NPU FLM trio** also fans out an `embed` capability
  (`catalog.py:243`, `:256`), and exposes `embed-npu` as a slot
  (`README.md:74-75`).

**Implication:** Hindsight's embedding + rerank needs map onto *existing* hal0
slots. The redesign should make the brain a **first-class consumer of the embed
and rerank capabilities** (route Hindsight embeddings through the embed slot,
reranking through port 8086), rather than bundling its own embedder. Consider
whether "memory" deserves its own capability card in `capabilities.toml` so the
dashboard can show its embed/rerank wiring and health.

**Adjacent infra:** `src/hal0/journal/` (lemond log ring + fan-out for the
Journal panel) and `src/hal0/events/` (in-process EventBus, 500-entry ring,
footer status). Both are *operational* event streams, not memory — but they are
candidate **producers** of memory ("what happened on this box") if the brain
ingests events.

---

## 5. Dashboard surfaces

Hash-routed SPA; routes:
`["dashboard","chat","firstrun","slots","models","backends","logs","agent","settings","mcp"]`
(`ui/src/dash/main.jsx:17`). Surfaces that touch memory / context / agents:

| View / file | Touches |
|---|---|
| **Agent → Memory tab** (`ui/src/dash/agents/memory-tab.jsx`) | **The Cognee explorer.** GraphExtractionPanel (ADR-0014 route picker), Cognee stats (records/DB), recent records, namespaces side card, and a "Peer memory" subsection reading `/api/memory/search?dataset=agents&tag=agent-identity` (read-only, ADR-0011). Note: stats card currently shows mock numbers (`2,847 records`). |
| **Agent → Personas tab** (`agents/personas-tab.jsx`, `persona-budget-panel.jsx`) | Persona = prompt + tool gating + **memory namespacing** layer. |
| **Agent → Plugins tab** (`agents/plugins-tab.jsx`, `plugin-host.jsx`) | Hosts Hermes plugin UIs (incl. the memory plugin) in shadow-DOM via `/api/dashboard/plugins` proxy. |
| **Agent → Skills tab** (`agents/skills-tab.jsx`) | Static skill catalog (v0.3). |
| **Agent → Chat tab** (`agents/hermes-chat-tab.jsx`) | Hermes conversation — the live producer of `sync_turn` memory writes. |
| **Agent view shell** (`agents/agent-view.jsx`, `sidebar-agent-block.jsx`) | Identity cards, reachability, bootstrap/repair/uninstall. |
| **MCP page** (`mcp.jsx`, `mcp-main.jsx`, `mcp-modals.jsx`, `mcp-data.jsx`) | Lists bundled + installed MCP servers (incl. `hal0-memory`), per-server tool introspection, install drawer, ClientsRibbon (shows connected Claude Code/Cursor), identity-card reader. |
| **Chat** (`chat.jsx`) | OmniRouter tool chips (incl. `embed_text`/`rerank_documents`), persona dropdown — built-in chat producer/consumer. |
| **Dashboard / Memory map** (`dashboard.jsx`, `memory-map.jsx`) | **NOT the brain.** This is GTT/RAM *hardware* attribution (unified-memory bar, Proxmox host segment) — distinct from Cognee. Easy to confuse by name. |
| **Settings** (`settings.jsx`) | Lemonade admin panel, Proxmox token, graph-extraction model route lives logically here / in Memory tab. |

PLAN's v3 dashboard spec for these (`PLAN.md:293-316`): MCP page = list servers
+ tool introspection + identity-card reader; **Memory** = "Cognee dataset
explorer (shared / private / `agents`); search + delete; per-agent namespace
surfaced"; Agents = identity cards.

---

## 6. The integration map — where the new brain must hook in to feel native

Prioritized. "Native" = a consumer gets memory/wiki without bespoke glue.

### Tier A — the load-bearing seams (do these or it's bolted-on)

1. **Preserve the dual transport contract.** Keep `/mcp/memory` (4 tools) +
   `/api/memory/*` with the **`X-hal0-Agent` server-side namespace rule**
   intact (`mcp/memory.py`, `routes/memory.py:48`, issue #317). Every agent +
   external client inherits the brain through these. Add wiki read/search as
   *new tools on the same MCP server* and *new routes under `/api/memory` or a
   sibling `/api/wiki`* — not a separate service.

2. **Hermes `prefetch()` injection seam** (`provider.py:137-161`). This is the
   one place that already injects memory into every Hermes turn. Hindsight
   retrieval + an Obsidian context block must land here. Likewise keep
   `sync_turn()` (`provider.py:163`) and `on_memory_write()` (`:207`) as the
   write seams.

3. **Route embeddings + rerank through existing slots.** Make the brain a
   consumer of the `embed` capability (embed slot; consider bge-on-iGPU,
   `PLAN.md:1088`) and the `rerank` slot on port 8086 (already wired,
   `cognee_wrapper.py:205-225`). Do not bundle a second embedder.

4. **CogneeWrapper boundary** (`src/hal0/memory/cognee_wrapper.py`). If
   Hindsight replaces/augments Cognee, this is the swap point — it already
   isolates SQLite/LanceDB/Kuzu, graph gate, and the rerank client behind one
   class. Keep its async `add/search/list/delete/list_items` contract so the
   MCP + REST shims don't change.

### Tier B — make it visible + operable (native UX)

5. **Dashboard Agent → Memory tab** (`agents/memory-tab.jsx`) — wire the real
   store stats (today mock `2,847`), add an **Obsidian wiki browser** + graph
   view alongside the Cognee explorer. The GraphExtractionPanel already exists
   for ADR-0014 routing.

6. **MCP page ClientsRibbon + Memory MCP card** (`mcp.jsx`, `routes/mcp.py`) —
   surface that Claude Code / Cursor are *reading the wiki/memory*, and show
   the wiki tools on the `hal0-memory` server's introspection card.

7. **Consider a `memory` capability card** in `capabilities.toml` /
   `capabilities/catalog.py` so the brain's embed+rerank wiring + health show
   up in the same UX language as voice/img/embed cards.

### Tier C — deepen the external + roadmap surfaces

8. **Claude Code / external agents** — ship the Cognee-style six-lifecycle-hook
   ingestion path (`PLAN.md:1044`) so a user's Claude Code *produces* memory
   into the wiki, not just reads it via MCP. This is the headline "every agent
   feeds one brain" story.

9. **Context files** (`HERMES.md.j2`, `AGENTS.md.j2`,
   `hermes_provision._phase_context_link`) — add a "wiki index / how to query
   memory" section so *any* agent landing in `/etc/hal0` (Claude Code, Cursor,
   Codex) learns the brain exists.

10. **Identity cards + `agents` dataset** (`hermes_provision.py:1386-1434`,
    ADR-0011) — memory the brain holds *about its agents*. The wiki should
    render these as people/agent notes.

11. **pi-coder `pi-memory-md` precedent** (`pi_coder.py:10-12`) — a per-project
    markdown memory already coexists with the central store. Mine this as the
    design analogy for the Obsidian vault, and decide the federation story
    (PLAN's "memory federation", `PLAN.md:361-364`).

12. **Event/journal producers** (`src/hal0/events/`, `src/hal0/journal/`) —
    optional: ingest operational events ("model X loaded", "slot evicted") as
    machine-authored memory so the brain knows the box's history.

---

## 7. Consumers × {produces / consumes memory / consumes wiki}

Legend: ✅ today · ◐ partial/aspirational · ✗ not yet · n/a not applicable.
"Wiki" = the planned Obsidian vault (does not exist yet) — column shows where it
*would* attach.

| Consumer | Produces memory? | Consumes memory? | Consumes wiki? (planned) | How / seam |
|---|---|---|---|---|
| **Hermes-Agent** | ✅ `sync_turn` + `on_memory_write` → `/api/memory/add` (`provider.py:163,207`) | ✅ `prefetch()` per turn + `hal0_memory` MCP CRUD (`provider.py:137`) | ◐ via `prefetch` seam + HERMES.md | MemoryProvider plugin `hal0-cognee` |
| **Hermes personas** | ◐ scopes write namespace | ✅ scopes read namespace + prompt | ◐ | `personas.py` |
| **pi-coder** | ✅ via `hal0-memory` MCP | ✅ via `hal0-memory` MCP | ◐ MCP tools | `pi-mcp-adapter.json` (`pi_coder.py:123`) |
| **pi-coder `pi-memory-md`** | ✅ project-scoped markdown (upstream, separate store) | ✅ same | n/a (design analogy for Obsidian) | upstream ext, coexists |
| **Claude Code (external)** | ◐ only if it calls `memory_add`; six-hook plugin ✗ not impl | ✅ as MCP client of `/mcp/memory` | ◐ would see wiki MCP tools | `/mcp/memory` + `AGENTS.md`; recognized in ClientsRibbon |
| **Cursor / Codex / other IDE agents** | ◐ MCP `memory_add` | ✅ MCP client | ◐ | `/mcp/memory`; `AGENTS.md` |
| **`hal0-memory` MCP server** | n/a (transport) | n/a | n/a | `mcp/memory.py`, mount `/mcp/memory` |
| **`hal0-admin` MCP server** | ✗ | ✗ (admin only) | ✗ | `mcp/admin.py` |
| **Aftermarket MCP servers** | depends on server | depends | ✗ | `mcp/installed.py` registry |
| **OmniRouter `embed_text` / `rerank_documents`** | ✗ | ✗ (but power memory's embed/rerank) | ✗ | `omni_router/`, slots embed + 8086 |
| **`/v1/*` OpenAI API consumers (OpenWebUI, scripts, Continue)** | ✗ | ✗ (inference only) | ✗ | `routes/v1.py` |
| **Dispatcher / upstreams** | ✗ | ✗ | ✗ | inference routing only |
| **embed slot** | n/a | n/a | n/a | substrate: Cognee embeddings (`cognee_wrapper.py:167`) |
| **rerank slot (8086)** | n/a | n/a | n/a | substrate: memory rerank pass (`cognee_wrapper.py:209`) |
| **Identity cards (`agents` dataset)** | ✅ written by Hermes bootstrap | ✅ read by Memory/Agents views | ◐ render as agent notes | `hermes_provision.py:1386` |
| **Dashboard Agent→Memory tab** | ◐ delete only | ✅ explorer/search | ◐ host wiki browser here | `agents/memory-tab.jsx` |
| **Dashboard Chat / Agent→Chat** | ✅ drives Hermes `sync_turn` | ✅ shows prefetched context | ◐ | `chat.jsx`, `hermes-chat-tab.jsx` |
| **Dashboard MCP page** | ✗ | ✅ shows memory server + clients | ◐ show wiki tools | `mcp.jsx` |
| **Dashboard "Memory map"** | ✗ (hardware GTT, NOT brain) | ✗ | ✗ | `memory-map.jsx` — name collision only |
| **events / journal** | ◐ candidate event-ingest producer | ✗ | ✗ | `events/`, `journal/` |

---

## 8. Assumptions & caveats (flagged)

- **Cognee may be absent at runtime.** `mount_mcp_servers` skips `/mcp/memory`
  if `cognee` isn't installed (`mcp_mount.py:113-117`). The whole brain surface
  is therefore conditionally mounted — the redesign must handle the "no engine"
  state.
- **UI is `.jsx` (React-via-globals), not `.tsx`.** Views publish onto
  `window.*` and read a `HAL0_DATA` seed; several memory numbers in
  `memory-tab.jsx` are still mock placeholders. Inventory above is from headers
  + structure, not a deep TS read (as briefed).
- **pi-coder "parked" is a *promo* decision, not code removal** — the driver +
  manager entry are live. I did not find a feature flag disabling it at
  runtime; treat it as installable.
- **Claude Code lifecycle-hook ingestion is roadmap text only** (`PLAN.md:1044`,
  ADR-0005) — no code in this repo. The only real Claude Code touchpoint is
  MCP-client recognition (`mcp.py:503`) + the shared `AGENTS.md`.
- **`X-hal0-Agent` vs Bearer**: post-ADR-0012 hal0-api is auth-free on
  `0.0.0.0:8080`; identity flows on `X-hal0-Agent` (REST) /
  client-id-resolver (MCP). Older pi-coder code still writes a `Bearer` header
  into the adapter config (`pi_coder.py:135-139`) — a known transitional
  inconsistency, not a blocker.
- **Graph + Memify are default-OFF** (ADR-0014). The brain's graph story is a
  gate + introspection surface in v0.3; real LLM-routed extraction lands v0.4
  (`cognee_wrapper.py:201-204`, `PLAN.md:347-351`).
- The **rerank port (8086)** and **embed model** are config-driven defaults;
  treat them as wiring to verify on a given host, not constants.
