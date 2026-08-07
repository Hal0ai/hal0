## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:

- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

## hindsight

This project has long-term memory via Hindsight at <http://10.0.1.142:9177>. Memories persist across sessions and agents.

Rules:

- **Before answering "what did we do", "what's configured", "do you remember" — call `hindsight_recall` first.** Don't guess; check.
- **When the user states a durable preference, decision, or fact — call `hindsight_retain`.** Be specific: include who, what, when, why. Format: "User prefers X because Y" / "Decided to use Z for W (2026-07-22)".
- **Use `hindsight_reflect` for synthesis.** Unlike recall (raw results), reflect generates a coherent answer from memory. Use it to answer "what do you know about X".
- **Auto-recall runs on session_start; auto-retain runs every 3 turns.** These handle transient session context. Explicit retain/recall calls are for durable long-term facts.
- **One unified bank: `shared`.** All agents (Claude, Pi, Hermes) read and write the `shared` bank, scoped by tags (`agent:<id>`, `project:<slug>`) — not by per-agent or per-project banks. Do NOT enable `dynamicBankId` and do not add `directoryBankMap` entries; fragmenting into many banks makes memories invisible across agents (consolidated 14 banks back to 2 on 2026-08-07). The only other bank is `agents` — hal0's peer-registry dataset (agent identity cards); never write ordinary memories there.
- **Check status** with `hindsight_status` before assuming memories are gone — it shows reachability, resolved bank, and bank count.

## Agent skills

### Issue tracker

Issues live in GitHub Issues (`gh` CLI); Linear is a read-only mirror — migrate any Linear-only item into GitHub and work from git. See `docs/.devdocs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage labels, unmodified (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/.devdocs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` + `docs/adr/` at the repo root (created lazily). See `docs/.devdocs/agents/domain.md`.

### Superset.sh

Local dev-agent workspaces (git worktrees + Claude/Codex/etc. sessions), wired to the Hal0 Linear mirror and GitHub Issues. See `docs/.devdocs/agents/superset-integration.md`.
