# hal0-wiki Research Dossier

> Research target: **`Hal0ai/hal0-wiki`** — a fork of **`ar9av/obsidian-wiki`**, an
> implementation of Andrej Karpathy's "LLM Wiki" pattern as a set of agent skill files.
> Studied via the GitHub API at HEAD commit `ba60beda` ("fix(cli): warn when installed
> skills are stale after upgrade (#77)"). Written as input to the hal0 brain/memory redesign.

---

## 0. TL;DR

`obsidian-wiki` (the pip package; the fork is `hal0-wiki`) is **not an application or a daemon**.
It is a **distribution of markdown "skill" files** plus a thin, pure-stdlib Python CLI whose only
job is to *install* those skills into the skill-discovery directories of a long list of AI coding
agents (Claude Code, Cursor, Windsurf, Codex, Gemini, **Hermes**, **Pi**, OpenClaw, Kiro, Copilot,
and more). Once installed, you point an agent at an Obsidian vault and say **"set up my wiki"**, and
from then on the agent *is* the runtime: it reads source material, distills it into interconnected
Obsidian-flavored markdown pages, keeps an index/log/manifest current, and answers questions out of
the compiled pages instead of re-doing retrieval every time.

The pattern, per Karpathy: **the wiki is a compiled, compounding artifact; the LLM is the maintainer;
Obsidian is the viewer/IDE; the human curates sources and asks questions.** "Compile, don't retrieve."

**Fork status (important):** as of this writing the `Hal0ai/hal0-wiki` fork is **byte-for-byte
identical to upstream** — `git compare` reports `status: identical, ahead_by: 0, behind_by: 0`, the
README is identical, and HEAD is the same upstream commit authored by "Arnav". The only "Hal0'd"
change so far is the GitHub repo *description* ("Hal0'd - Framework for AI agents…") and the repo
rename. No code or skill divergence exists yet; the fork is a staging point for hal0 integration.

---

## 1. What it actually is

### 1.1 The pip package `obsidian-wiki`

- **Name / entrypoint:** `obsidian-wiki` (PyPI), console script `obsidian-wiki = obsidian_wiki.cli:main`.
- **Dependencies:** **none.** `pyproject.toml` declares `dependencies = []` — a *pure-stdlib CLI*.
  "Installing skills needs no third-party runtime dependencies." Python `>=3.9`. License MIT.
- **Versioning:** CalVer derived from the git tag via `hatch-vcs` (e.g. `v2026.05.2 → 2026.5.2`).
  Tagging a release *is* the version bump. `__init__.py` reads the version via
  `importlib.metadata.version("obsidian-wiki")`, falling back to `0.0.0+dev` in a source tree.
- **What ships in the wheel:** The Python package (`obsidian_wiki/`) is *just the installer*. The
  actual product — the `.skills/` markdown tree plus the agent bootstrap files (`AGENTS.md`, Cursor
  rules, Windsurf rules, Kiro steering, Antigravity rules/workflows, Copilot instructions) — is
  **force-included** into the wheel under `obsidian_wiki/_data/` (`_data/skills/`,
  `_data/bootstrap/…`). So the installed package is self-contained and can install skills with no
  cloned repo. Source paths stay at the repo root as the single source of truth.

The Python module docstring states the design plainly: *"The product is the markdown skill content
under `.skills/` (bundled into this package as data). This module is just the installer CLI."*

### 1.2 The CLI (`setup`, `list`, `info`)

`obsidian_wiki/cli.py` (the Python port of `setup.sh`) exposes three subcommands; with no subcommand
it defaults to `setup`:

| Command | What it does |
|---|---|
| `obsidian-wiki setup` | Writes `~/.obsidian-wiki/config` (vault path + bundled-data root + version stamp) and installs every bundled skill into **every supported agent's** global skills directory under `$HOME`. Flags: `--vault PATH`, `--project [DIR]` (also drop project-local skills + bootstrap/`AGENTS.md` into a repo), `--project-only` (skip the global install), `--copy` (copy instead of symlink). |
| `obsidian-wiki list` | Prints the bundled skill names (one per line). |
| `obsidian-wiki info` | Prints version, resolved skills/bootstrap/config paths, vault path, "setup ran" version, bundled skill count, and a **per-agent install status** line (`N/38`) so you can see at a glance whether `setup` has been run for each agent. |

There is **no server, no daemon, no API**. The CLI runs once to wire up skill discovery and exits.
The only "runtime" is the AI agent reading the markdown.

A staleness guard (`_check_stale()`, added in HEAD commit #77) stamps `OBSIDIAN_WIKI_VERSION` into
`~/.obsidian-wiki/config` on each `setup`; later CLI invocations warn if the installed version
drifted from the stamped one, or if `~/.claude/skills/` is missing bundled skills.

### 1.3 How it installs skill files into agents (symlink-vs-copy)

The core install primitive (`install_skills(target_dir, …, mode="symlink"|"copy")`) iterates the
bundled `.skills/<name>/` folders and, for each, creates `target_dir/<name>`:

- **symlink mode (default):** `target_dir/<name>` → the installed package's skill folder. The payoff:
  *"Skills are symlinked to the installed package, so `pip install -U obsidian-wiki` upgrades them
  everywhere — just re-run `obsidian-wiki setup` to pick up new skills."* (The shell `setup.sh`
  variant emits *relative* `../`-prefixed symlinks for project-local dirs so they match the committed
  symlink mirrors; the pip CLI uses absolute symlinks since there's no repo.)
- **copy mode (`--copy`):** `shutil.copytree` the skill folder. For symlink-hostile filesystems.
- **Safety:** an existing symlink/file at the target is replaced; a *real* directory is replaced only
  if it contains a `SKILL.md` (i.e. it's a managed skill) — otherwise it's left alone ("not a managed
  skill, skipping"). Every install is sanity-checked by confirming `…/<name>/SKILL.md` resolves.

**Per-agent global targets** (`GLOBAL_AGENT_DIRS` in the CLI): `~/.claude/skills`, `~/.gemini/skills`,
`~/.gemini/antigravity/skills`, `~/.codex/skills`, **`~/.hermes/skills`**, `~/.openclaw/skills`,
`~/.copilot/skills`, `~/.trae/skills`, `~/.trae-cn/skills`, `~/.kiro/skills`, **`~/.pi/agent/skills`**,
and `~/.agents/skills` (the shared AGENTS.md discovery path for OpenCode/Aider/Droid/generic).
For Hermes it additionally walks `$HERMES_HOME` and every profile under `~/.hermes/profiles/*/skills/`.
(The shell `setup.sh` notably installs **only the two portable skills** — `wiki-update`, `wiki-query`
— into `~/.claude/skills`, whereas the pip CLI installs *all* skills there, since pip users have no
cloned repo to host project-scoped skills.)

**Agent bootstrap / context files** (one per agent's convention), copied by `install_project()`:
`AGENTS.md` (Codex, OpenCode, Aider, Droid, Trae, Hermes, OpenClaw, Pi, Kilocode), Cursor
`.cursor/rules/obsidian-wiki.mdc` (`alwaysApply: true`), Windsurf `.windsurf/rules/…`, Kiro
`.kiro/steering/…` (`inclusion: always`), Antigravity `.agent/rules/…` + `.agent/workflows/…`,
GitHub `.github/copilot-instructions.md`. Then **`CLAUDE.md`, `GEMINI.md`, and `.hermes.md` are
created as symlinks to `AGENTS.md`** (single source of truth; copy fallback on symlink-hostile FS).

So the model is: **write each skill once in `.skills/`, and `setup` fans it out (by symlink) to every
agent's idiosyncratic discovery path.** Slash commands (`/wiki-ingest`, `/wiki-status`, …) work in
agents that auto-register skills as commands; elsewhere the user just describes intent and the agent's
skill-router matches it.

---

## 2. The LLM Wiki pattern (Karpathy)

The bundled `llm-wiki/SKILL.md` is the "theory" skill, and `references/karpathy-pattern.md` distills
the gist. Karpathy's framing (from the original gist) and how this framework operationalizes it:

### 2.1 Core insight

> *"The wiki is a persistent, compounding artifact. The knowledge is compiled once and then kept
> current, not re-derived on every query."*

The roles invert relative to a chatbot: **the human curates sources and asks questions; the LLM does
the bookkeeping** (summarizing, cross-referencing, updating pages when new sources arrive, touching
10–15 pages per ingest). Karpathy's analogy: **"Obsidian becomes the IDE, the LLM becomes the
programmer/grunt-worker, and the wiki becomes the codebase."** He grounds it in Vannevar Bush's 1945
Memex — a private associative knowledge store whose unsolved problem was *who maintains it*; the LLM
is the answer. The failure mode of human wikis is that *"the maintenance burden grows faster than the
value"* — LLMs flatten that cost toward zero ("doesn't get bored, doesn't forget to update a
cross-reference, can touch 15 files in one pass").

### 2.2 Why it beats RAG

Traditional RAG **rediscovers** knowledge on every query (search raw sources → pull chunks →
synthesize from scratch). The LLM Wiki **compiles** knowledge once into maintained, cross-referenced
pages, so queries hit pre-synthesized content. Karpathy notes a magnitude threshold: under ~100k
tokens (~150–200 dense pages), context-based wikis beat RAG on retrieval reliability (no chunking
artifacts), infrastructure (plain markdown, no vector DB / embedding pipeline), and **global
reasoning** (the model reasons over the whole synthesis, not stitched snippets). Modern 200k–1M
context windows push that ceiling up yearly. (This framework adds optional QMD semantic search to
extend past the threshold — §4.4.)

### 2.3 Three-layer architecture

1. **Layer 1 — Raw Sources (immutable):** the user's original docs, papers, notes, PDFs, conversation
   logs, bookmarks, **and images** (screenshots/whiteboards/diagrams — first-class, vision-model
   required). Never modified. The "source code". Configured via `OBSIDIAN_SOURCES_DIR`.
2. **Layer 2 — The Wiki (LLM-maintained):** interconnected Obsidian markdown organized by category;
   the *compiled* knowledge — synthesized, cross-referenced, navigable. Lives at `OBSIDIAN_VAULT_PATH`.
3. **Layer 3 — The Schema (skill + config):** the rules governing structure (categories, conventions,
   page templates, workflows). Tells the LLM *how* to maintain the wiki. In Karpathy's setup this was
   `CLAUDE.md`; here it's the skill files + `.env`/global config + the vault's own `AGENTS.md`.

### 2.4 Vault structure & note types

```
$OBSIDIAN_VAULT_PATH/
├── index.md          # Master content-catalog by category; rebuilt after every ingest
├── log.md            # Append-only, parseable chronological op log (INGEST/QUERY/LINT/ARCHIVE…)
├── hot.md            # ~500-word semantic "hot cache" of recent activity (warm-start next session)
├── .manifest.json    # Ledger of every ingested source → pages produced (delta backbone)
├── _meta/            # taxonomy.md (controlled tag vocab) + *.base (Obsidian Bases dashboards)
├── _insights.md      # Graph analysis output (hubs, bridges, dead ends)
├── _raw/             # Staging area — drop rough notes; next ingest promotes + deletes them
├── _staging/         # Review queue when WIKI_STAGED_WRITES=true (invisible in graph until promoted)
├── _archives/        # Timestamped wiki snapshots (rebuild/restore)
├── concepts/         # Abstract ideas, patterns, mental models
├── entities/         # Concrete things — people, tools, libraries, companies
├── skills/           # How-to knowledge, techniques, procedures
├── references/       # Factual lookups / per-source summaries
├── synthesis/        # Cross-cutting analysis connecting multiple concepts
├── journal/          # Time-bound entries — daily logs, session notes
└── projects/<name>/  # Per-project knowledge (overview page named <name>.md, not _project.md)
```

**Two axes of organization:** *categories* (what kind of knowledge) × *projects* (where it came from).
Project-specific knowledge goes under `projects/<name>/<category>/`; general knowledge goes in the
global category dirs; both cross-link with `[[wikilinks]]`.

### 2.5 Page template, wikilinks/backlinks, provenance, confidence

Every page carries **required frontmatter**: `title`, `category`, `tags`, `sources`, `created`,
`updated`. The richer template adds:
- `summary:` — 1–2 sentences ≤200 chars, written at ingest so queries can preview without opening.
- `aliases:` and a typed **`relationships:`** block (`extends`, `implements`, `contradicts`,
  `derived_from`, `uses`, `replaces`, `related_to`) — directional, semantic edges beyond plain links.
- **`provenance:`** mix (`extracted` / `inferred` / `ambiguous` fractions). Inline claim markers:
  default = extracted (no marker), `^[inferred]` for LLM synthesis, `^[ambiguous]` for source conflict.
  *"A wiki that hides its guessing rots silently; one that marks it stays trustworthy."*
- **`base_confidence`** (0–1, from source count × source-quality buckets: paper 1.0 → llm_generated
  0.3), **`lifecycle`** (`draft|reviewed|verified|disputed|archived` — only ingest sets `draft`; all
  other transitions are human-only; `stale` is a *computed overlay*, `updated > 90d`), and **`tier`**
  (`core|supporting|peripheral`) controlling which pages get touched per ingest and query priority.

**Index notes / MOCs:** `index.md` is the master Map-of-Content, regenerated after each ingest; each
project gets an overview MOC page that links out to relevant concept/entity/skill pages. Wikilinks
are bidirectional in Obsidian (backlinks panel + graph view), which is what makes it a knowledge
*graph* rather than a folder of files. Link format is configurable (`OBSIDIAN_LINK_FORMAT=wikilink`
default, or `markdown`).

### 2.6 The maintainer/viewer split, in practice

The framework's mantra (from SETUP.md): **"The wiki is the artifact. The agent is the maintainer.
Obsidian is the viewer."** No scripts, no API keys — *the agent **is** the LLM*. It reads `.env`/config,
reads `.manifest.json` to know what's done, reads the relevant `SKILL.md`, uses its built-in
read/write/search tools, and updates the manifest. Output is plain Obsidian-compatible markdown.

### 2.7 The four-stage ingest loop

Every ingest runs: **(1) Ingest** the source directly (md/PDF/JSONL/text/images, no preprocessing) →
**(2) Pull Information** (concepts, entities, claims, relationships, open questions; drop noise; write
a `summary:`) → **(3) Merge** against existing pages (update, don't duplicate; note contradictions;
strengthen cross-refs; track sources) → **(4) Schema** (the schema *emerges* and evolves from sources;
keep categories consistent, links valid, index accurate). `.manifest.json` enables **delta** ingest:
on the next run only new/changed sources are processed.

---

## 3. The bundled skills (full enumeration)

Skills live as `.skills/<name>/SKILL.md` (each with YAML frontmatter `name` + `description`, the
`description` doubling as the trigger/router text). The canonical set is mirrored by `setup` into
every agent dir. `obsidian-wiki info` at HEAD reports **38** bundled skills. Below is the full set,
what each does, and its trigger phrases / slash command (drawn from the `AGENTS.md` skill-routing
table and each skill's own description).

### 3.1 Setup / theory

| Skill | What it does | Triggers |
|---|---|---|
| `llm-wiki` | The core pattern + architecture reference (theory skill). Three-layer arch, page templates, project org, config-resolution protocol. | "/llm-wiki", understanding the pattern, structure decisions |
| `wiki-setup` | Initialize a vault: create dir structure, `index.md`/`log.md`/`hot.md`, `.obsidian/` config, `_raw/`/`_staging/`/`_archives/`, recommend plugins. | "set up my wiki", "initialize", "create a new vault", "get started" |
| `skill-creator` | Scaffold/refine new skills (ships its own eval harness: analyzer/comparator/grader agents + scripts). | "create a new skill" |

### 3.2 Ingest (sources & history)

| Skill | What it does | Triggers |
|---|---|---|
| `wiki-ingest` | Distill documents (md/PDF/text/images) into pages, append or full mode. | "ingest", "add this to the wiki", "process these docs" |
| `wiki-history-ingest` | Thin **router** for agent-history sources; dispatches to the per-agent skill. | "/wiki-history-ingest \<claude\|codex\|hermes\|openclaw\|copilot\|pi\>" |
| `claude-history-ingest` | Mine `~/.claude` conversations/memories/sessions. | "import my Claude history", "mine my conversations" |
| `codex-history-ingest` | Mine `~/.codex` sessions/rollout logs. | "import my Codex history" |
| `hermes-history-ingest` | Mine `~/.hermes` memories + session JSONL (see §3.6). | "import my Hermes history", "ingest ~/.hermes" |
| `openclaw-history-ingest` | Mine `~/.openclaw` `MEMORY.md`/daily notes/sessions/dreams. | "import my OpenClaw history", "ingest ~/.openclaw" |
| `copilot-history-ingest` | Mine `~/.copilot` CLI session history. | "import my Copilot history", "ingest ~/.copilot" |
| `pi-history-ingest` | Mine `~/.pi/agent/sessions` tree-structured JSONL (see §3.6). | "import my Pi history", "ingest ~/.pi" |
| `data-ingest` | Ingest any raw text — ChatGPT/Slack/Discord exports, logs, transcripts. | "process this export", "ingest this data" |
| `ingest-url` | Fetch + ingest a URL directly. | "/ingest-url \<url\>", "add this URL", "save this page" |
| `obsidian-wiki-ingest` | Project-scoped automation wrapper around wiki-ingest (has an `ingest-wiki.sh` script). | "ingest this obsidian wiki", "ingest the obsidian-wiki project" |
| `wiki-import` | Import a vault from a prior export (`graph.json` etc.). | "import wiki", "load graph.json", "/wiki-import" |
| `wiki-quick-chat-capture` | Zero-friction mid-session finding capture to `_raw/` (no subagents/manifest writes; <60s). | "/wiki-quick-chat-capture", "quick capture", "save this gotcha", "drop to raw" |
| `wiki-capture` | Save the current conversation as a wiki note (base_confidence 0.42). | "save this", "/wiki-capture", "file this conversation" |

### 3.3 Cross-project read/write (the two portable skills)

| Skill | What it does | Triggers |
|---|---|---|
| `wiki-update` | From *any* project: scan README/source/git-log/package metadata, distill architecture decisions/patterns/trade-offs (not code dumps), write `projects/<name>.md`, cross-link, delta via `last_commit_synced`. | "update wiki", "sync to wiki", "save this to my wiki" |
| `wiki-query` | Answer questions from the wiki. **Tiered retrieval:** read titles/tags/`summary:` first, open bodies only when needed; "quick answer"/"just scan" forces index-only. Returns `[[wikilink]]` citations. | "what do I know about X", "find info on Y", any question |

### 3.4 Maintenance / health / graph

| Skill | What it does | Triggers |
|---|---|---|
| `wiki-status` | What's ingested/pending/the delta; **insights mode** computes hubs, bridge pages, tag-cluster cohesion, surprising connections, graph delta, suggested questions, token-footprint warning → `_insights.md`. | "what's the status", "show the delta", "wiki insights", "hubs" |
| `wiki-lint` | Find orphans, broken links, stale content, contradictions, missing frontmatter; recompute provenance mix and flag speculation-heavy pages. | "audit", "lint", "find broken links", "wiki health" |
| `wiki-dedup` | Find/merge duplicate pages, identity resolution, consolidation. | "dedup my wiki", "find duplicate pages", "consolidate my wiki" |
| `wiki-rebuild` | Archive (timestamped snapshot) → rebuild from scratch → or restore a prior archive. | "rebuild", "start over", "archive", "restore" |
| `cross-linker` | Scan vault for unlinked mentions and weave `[[wikilinks]]`/typed relationships in. | "link my pages", "cross-reference", "connect my wiki" |
| `tag-taxonomy` | Enforce a controlled tag vocabulary (`_meta/taxonomy.md`) across the vault. | "fix my tags", "normalize tags", "tag audit" |
| `graph-colorize` | Rewrite `<vault>/.obsidian/graph.json` `colorGroups` to tint nodes by tag/category/visibility (backs up first, colorblind-friendly palette). | "color my graph", "color code by tag/category/visibility" |
| `wiki-export` | Export the wikilink graph to `graph.json`, `graph.graphml` (Gephi/yEd), `cypher.txt` (Neo4j), and self-contained interactive `graph.html`. | "export wiki", "export graph", "graphml", "neo4j" |
| `wiki-dashboard` | Create dynamic Obsidian **Bases** dashboard views (`_meta/*.base`). | "create a dashboard", "vault dashboard", "show all X as a table" |

### 3.5 Synthesis / digest / validation / automation

| Skill | What it does | Triggers |
|---|---|---|
| `wiki-synthesize` | Discover and fill synthesis gaps across concepts (base_confidence = min of inputs). | "synthesize my wiki", "find connections", "what concepts keep coming up together" |
| `wiki-research` | Autonomous multi-round web research, self-filed into the vault (often base_confidence 0.85+). | "/wiki-research [topic]", "research X", "find everything about Y" |
| `wiki-digest` | Periodic knowledge summary. | "/wiki-digest", "what did I learn this week", "weekly digest", "monthly review" |
| `impl-validator` | Validate an implementation against its stated goal. | "/impl-validator", "check this implementation", "is this correct?" |
| `daily-update` | Daily maintenance cycle (freshness, index, hot cache); can install a cron/launchd job + terminal notification (see `scripts/`). | "/daily-update", "morning sync", "set up the daily cron" |
| `wiki-switch` | Manage multiple vaults / vault configs. | "/wiki-switch NAME", "switch to my work wiki", "list my wikis" |
| `wiki-context-pack` | (Pi-tree only) build a context pack. | (Pi-specific) |
| `wiki-stage-commit` | (Pi-tree) review + promote `_staging/` pages when `WIKI_STAGED_WRITES=true`. | "/wiki-stage-commit" |

### 3.6 Cross-tool memory (the hal0-relevant differentiators)

| Skill | What it does | Triggers |
|---|---|---|
| `wiki-agent` | **Query-driven, topic-first** ingest from a *specific* agent's raw history — finds the relevant blobs, distills them, and returns a synthesized answer immediately. | "/wiki-claude [topic]", "/wiki-codex [topic]", "/wiki-hermes [topic]", "/wiki-openclaw [topic]", "/wiki-copilot [topic]", "/wiki-pi [topic]" |
| `memory-bridge` | Browse/diff wiki knowledge by **which AI tool produced it** (reads `.manifest.json` `source_type` + page `sources:`). Modes: browse / search / **diff** / map. Diff surfaces *blind spots between tools* ("what does hermes know that claude doesn't") — described as "the killer feature". | "/memory-bridge", "what did codex know about X", "compare my AI tool memories", "gaps between tools" |

**Hermes & Pi history ingest specifics** (relevant because hal0 bundles Hermes-Agent and forked Pi):
- `hermes-history-ingest` reads `~/.hermes/` (`$HERMES_HOME` for non-default profiles): `memories/*.md|json`
  (highest signal — curated persistent memories), `sessions/**/*.jsonl` (turn-by-turn transcripts),
  and ignores `.hub/` internals and the `skills/` dir. Source types written to the manifest:
  `hermes_memory`, `hermes_session`. base_confidence 0.42, lifecycle draft. Hard privacy filter:
  strip secrets, redact identifiers, summarize — never dump raw transcripts.
- `pi-history-ingest` reads `~/.pi/agent/sessions/--<cwd>--/<ts>_<uuid>.jsonl` (or
  `$PI_HISTORY_PATH`/`PI_CODING_AGENT_SESSION_DIR`). Sessions are a **tree** of `id`/`parentId`
  entries (first line a `session` header). It rebuilds the active branch (walk `parentId` from leaf to
  root, reverse), extracts `message`/`compaction`/`branch_summary`/`bashExecution`, skips
  `thinking`/`model_change`/`custom`/`label`. `compaction` and `branch_summary` are treated as
  pre-distilled gold. Source type `pi_session`.

> **Note on the `info`-reported count vs the tree:** `obsidian-wiki info` reports 38 bundled skills.
> The repo tree shows a few skills present only under the Pi mirror or `.skills/` and not yet in the
> generic `.agents/skills` mirror at HEAD (`wiki-context-pack`, `wiki-dedup`, `wiki-stage-commit`,
> `wiki-import`, `wiki-quick-chat-capture`) — this is mirror drift in the committed symlink trees, not
> a functional gap, since `setup` regenerates all mirrors from `.skills/` on install. **Ambiguity
> flagged:** exact installed-skill count may differ slightly from the README's table depending on
> which mirror an agent reads; trust `.skills/` as the source of truth.

---

## 4. Python package internals

### 4.1 Module layout

`obsidian_wiki/` has three files, all thin:
- `__init__.py` — exposes `__version__` via `importlib.metadata`.
- `__main__.py` — `python -m obsidian_wiki` → `cli.main()`.
- `cli.py` — the whole installer (~360 LOC, stdlib only: `argparse`, `os`, `shutil`, `sys`, `pathlib`).

Key functions: `skills_dir()` / `bootstrap_dir()` (resolve data in both a built wheel `_data/…` and an
editable source checkout at the repo root), `install_skills()` (the symlink/copy primitive),
`install_global_skills()` + `_install_hermes_profiles()`, `install_project()` (project-local skills +
bootstrap files + `AGENTS.md` alias symlinks), `resolve_vault_path()` / `write_config()`,
`_check_stale()`, and `cmd_setup`/`cmd_list`/`cmd_info`.

### 4.2 Config — `~/.obsidian-wiki/config`

Written by `write_config()`:
```
OBSIDIAN_VAULT_PATH="<vault>"
OBSIDIAN_WIKI_REPO="<bundled-data-root>"   # so skills can find framework assets post-install
OBSIDIAN_WIKI_VERSION="<version>"          # staleness stamp
```
**Config Resolution Protocol** (defined in `llm-wiki/SKILL.md`, obeyed by every skill): walk up from
CWD looking for a `.env` containing `OBSIDIAN_VAULT_PATH`; else fall back to `~/.obsidian-wiki/config`;
else prompt the user to run `wiki-setup`. After resolving, **always read
`$OBSIDIAN_VAULT_PATH/AGENTS.md` if present** — that's the per-vault owner-conventions override.
Vault-scoped runtime state goes to `~/.obsidian-wiki/state/<md5(vault)[:8]>/`.

### 4.3 `.env.example` variables

Required: `OBSIDIAN_VAULT_PATH`. Optional: `OBSIDIAN_SOURCES_DIR`, `OBSIDIAN_CATEGORIES`
(default `concepts,entities,skills,references,synthesis,journal`), `OBSIDIAN_MAX_PAGES_PER_INGEST`
(15), `CLAUDE_HISTORY_PATH`, `CODEX_HISTORY_PATH`, `PI_HISTORY_PATH` (+ `HERMES_HOME`, `OPENCLAW_HOME`,
`COPILOT_HISTORY_PATH` referenced in skills), `LINT_SCHEDULE` (weekly), `OBSIDIAN_LINK_FORMAT`
(`wikilink`/`markdown`), `OBSIDIAN_RAW_DIR` (`_raw`), `WIKI_TOKEN_WARN_THRESHOLD` (100000),
`WIKI_STAGED_WRITES` (false). QMD block: `QMD_WIKI_COLLECTION`, `QMD_PAPERS_COLLECTION`,
`QMD_TRANSPORT` (`mcp`/`cli`), `QMD_CLI_SEARCH_MODE` (`quality`/`balanced`/`fast`), `QMD_CLI` (binary).

### 4.4 Server/daemon vs pure-CLI

**Pure CLI, no daemon, no network service.** The only optional external moving part is **QMD**
(`github.com/tobi/qmd`) — a local BM25+vector hybrid search index, addressed either via MCP or the
local `qmd` CLI. When `QMD_WIKI_COLLECTION` is set, `wiki-query` runs a semantic pass before falling
back to Grep; when `QMD_PAPERS_COLLECTION` is set, `wiki-ingest` checks sources before writing.
**Without QMD everything degrades gracefully to Grep/Glob** and remains fully functional. The
`scripts/` dir ships a macOS launchd plist + `daily-update.sh` + `wiki-notify.sh` for the optional
daily-maintenance cron — these are the closest thing to a "background" component, and they're opt-in.

---

## 5. The AGENTS.md contract

`AGENTS.md` (root) is the behavioral contract every AGENTS.md-aware agent loads (and `CLAUDE.md`,
`GEMINI.md`, `.hermes.md` are symlinks to it). It imposes:

1. **Self-description:** "A skill-based framework… No scripts or dependencies — everything is markdown
   instructions that you execute directly."
2. **Config resolution** (the protocol above) + "**always read `$VAULT/AGENTS.md` if it exists**" for
   owner-specific conventions that override framework defaults for the session.
3. **The vault structure** (§2.4) and **required frontmatter** (`title, category, tags, sources,
   created, updated`).
4. **A skill-routing table** mapping natural-language intents → skill name (the same table as the
   `.hermes.md`/README) — this is how non-slash-command agents pick a skill.
5. **Cross-project usage** semantics for `wiki-update` (write) and `wiki-query` (read).
6. **Optional visibility tags** (`visibility/public|internal|pii`) — single-vault, single source of
   truth; filtered mode is opt-in via query phrasing ("public only", "as a user would see it").
   PII/internal pages are excluded in filtered mode. These are *system tags* (don't count toward the
   5-tag limit).
7. **Core principles:** *Compile, don't retrieve* · *Track everything* (manifest after ingest;
   index/log/hot after any write) · *Connect with `[[wikilinks]]`* · *Frontmatter is required* ·
   *Single source of truth* · *Keep context warm* (`hot.md` ≈500-word snapshot every write skill
   updates, so the next session warm-starts without crawling the vault).

In short, the contract makes the agent a **disciplined, provenance-tracking librarian**: read config →
resolve owner conventions → route intent to a skill → execute → update the ledgers → never duplicate,
always cross-link, always mark inferences.

---

## 6. What "Hal0'd" changed vs upstream

**Currently: nothing functional.** Hard evidence from the GitHub API:

- `gh api repos/Hal0ai/hal0-wiki/compare/ar9av:main...Hal0ai:main` → `{"ahead_by":0, "behind_by":0,
  "total_commits":0, "status":"identical"}`.
- HEAD commit on both is `ba60beda` ("fix(cli): warn when installed skills are stale…", #77), authored
  by "Arnav" — i.e. an upstream commit, not a hal0 commit.
- `README.md` is byte-identical between fork and upstream; both `pushed_at` timestamps match
  (`2026-05-30T14:28:51Z`).
- The fork's `pyproject.toml` still names the package `obsidian-wiki`, author `Ar9av`, homepage
  `github.com/Ar9av/obsidian-wiki`.

The **only** "Hal0'd" delta is metadata: the GitHub repo *name* (`hal0-wiki`) and *description*
("**Hal0'd** - Framework for AI agents to build and maintain a digital brain…"). So the fork is a
clean, up-to-date mirror staged for hal0 work — a blank canvas, not a diverged codebase. Any
hal0-specific changes (Hermes/Pi wiring to the in-repo agents, vault location defaults, package
rename, MCP exposure) are still **to be made**. This is the right moment to plan that divergence
deliberately rather than discover drift later.

---

## 7. hal0 integration relevance

This section is the point of the dossier: how `hal0-wiki` could become the **human-legible,
compiled-knowledge layer** of the hal0 "brain", and how it sits next to a vector/structured memory
engine.

### 7.1 The fit with hal0's memory story

hal0 already has an MCP memory surface (`hal0-memory`, datasets, `X-hal0-Agent` identity) and bundles
**Hermes-Agent** plus a forked **Pi** (`Hal0ai/pi-mono`). `obsidian-wiki` is the missing *legible*
layer: where the existing memory engine answers "what's the nearest vector to this query", the LLM
Wiki answers "what does the system *understand* about this topic, as prose a human can read, edit, and
audit". The two are complementary, not competing — see §7.4. Crucially, this framework already has
**first-class Hermes and Pi ingesters** and a **`memory-bridge` diff** that surfaces cross-tool blind
spots — exactly the multi-agent topology hal0 runs.

### 7.2 Who maintains the vault (which agent)

Natural owner: **Hermes-Agent on the hal0 LXC (CT 105)**, since (a) it's the bundled long-running
agent with its own memory store, (b) `hermes-history-ingest` + `~/.hermes/skills/` install is already
a supported path, and (c) `.hermes.md`→`AGENTS.md` is wired. A `daily-update` cron (the `scripts/`
launchd plist would become a systemd timer on the LXC) could run the freshness/index/hot-cache cycle
nightly. `wiki-update`/`wiki-query` (the two portable skills) let *any* hal0 agent push to / read from
the same vault from any working directory, with the agent-of-origin tracked in `.manifest.json` for
`memory-bridge` attribution.

### 7.3 Where the vault would live

hal0 runs on the **LXC at CT 105 (`/opt/hal0`, 10.0.1.142)**. The vault is *just a directory of
markdown* — no service to host — so options:
- **Canonical store on the LXC** under e.g. `/var/lib/hal0/wiki` (alongside `registry/`,
  `lemonade/`), set as `OBSIDIAN_VAULT_PATH` in `~/.obsidian-wiki/config` for the agent user. This
  keeps the brain co-located with the runtime that maintains it. Back it with `Obsidian Git`
  (the framework recommends it) or a periodic snapshot.
- **Human viewing:** Obsidian is a desktop app; the LXC is headless. Either (a) sync the vault to the
  `hal0-dev` desktop VM / a user machine over the existing NFS/`devpool` or a git remote and open it in
  Obsidian there, or (b) surface the vault read-only through the hal0 dashboard (it already serves a
  SPA on `:8080` / `hal0.thinmint.dev`) by rendering the markdown + graph. The framework's
  `wiki-export` → `graph.html`/`graph.json` is a ready-made, server-free way to ship the graph view
  into the dashboard.
- Note the **local `obsidian-vault` skill** already present on hal0-dev points at
  `/mnt/d/Obsidian Vault/AI Research/` (a WSL/Windows path, flat layout, no projects/provenance) — it
  is a *different, simpler* skill and should not be confused with this framework. If hal0 adopts
  `hal0-wiki`, decide whether to retire or reconcile that local skill to avoid two competing "Obsidian"
  conventions.

### 7.4 Relationship to a vector/structured memory engine (e.g. Hindsight)

The clean architecture is **two layers with one source of truth**:

- **Markdown vault = source of truth + human-legible compiled knowledge.** Pages carry provenance,
  confidence, lifecycle, typed relationships — already a structured graph in frontmatter.
- **Vector/structured engine = the index over that vault.** This framework's *own* answer is QMD
  ("the markdown vault is the source of truth; QMD is a search index, not the source of truth";
  every write skill refreshes QMD after the vault write). **A hal0 integration would swap QMD for the
  hal0 memory engine / Hindsight**: on every vault write, (re)embed the changed pages into the engine;
  on query, do a semantic pass against the engine first, then fall back to Grep over the vault. The
  `wiki-export` Neo4j/GraphML output and the typed `relationships:` block map directly onto a
  structured/graph memory store if hal0 wants typed-ontology retrieval rather than (or alongside)
  vectors.
- **`memory-bridge` ↔ hal0's per-agent datasets:** the framework already attributes every page to the
  tool that produced it; this lines up with hal0-memory's per-agent dataset namespacing and would let
  the dashboard show "what each agent contributed" and "blind spots between agents".
- **Inference policy fit:** the framework needs *no* extra inference daemon — the maintaining agent
  uses its own LLM access. On hal0 that means the vault is maintained by whichever agent is talking to
  the Lemonade-served models on the iGPU/NPU; CPU-only embedding for the index can run locally or via
  the hal0 embed slot. No new always-on service is required beyond the optional nightly cron.

### 7.5 Integration risks / open questions to resolve before adopting

- **Headless Obsidian:** the "viewer" half assumes a desktop. hal0 needs a viewing story (dashboard
  render or vault sync to a desktop) — decide early.
- **QMD → Hindsight/hal0-memory swap** is a real code change: the skills hard-reference `qmd`
  commands. Either keep QMD as the index, or fork the skills to call the hal0 memory API. This is the
  single biggest "Hal0'd" change to plan.
- **Vault location & backup** on the LXC (CoW/btrfs snapshot vs Obsidian Git) — pick one.
- **Package identity:** if hal0 wants `pip install hal0-wiki` and its own version cadence, the
  `pyproject.toml` name/author/version-source must be changed (currently still upstream's).
- **Two Obsidian skills:** reconcile or retire the existing local `obsidian-vault` skill.
- **Skill-mirror drift** (§3.6 note): rely on `.skills/` as source of truth; always re-run
  `obsidian-wiki setup` after upgrades so each agent dir has the full set.

---

## Appendix — primary sources consulted

- `Hal0ai/hal0-wiki` @ `ba60beda`: `README.md`, `AGENTS.md`, `SETUP.md`, `.hermes.md`, `pyproject.toml`,
  `setup.sh`, `.env.example`, `obsidian_wiki/{cli,__init__,__main__}.py`, and skill files
  `.skills/{llm-wiki, llm-wiki/references/karpathy-pattern, wiki-setup, wiki-history-ingest,
  hermes-history-ingest, pi-history-ingest, memory-bridge, wiki-update}/SKILL.md`; full git tree.
- Upstream comparison: `gh api …/compare/ar9av:main...Hal0ai:main` (status: identical), upstream
  `README.md` (identical), repo metadata for both.
- Karpathy gist: https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f
- Local context: `~/.claude/skills/obsidian-vault/SKILL.md` (the pre-existing, unrelated local skill).
