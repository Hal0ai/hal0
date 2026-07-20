# Shared brief — graphify output analysis (hal0)

You are one worker in a swarm analyzing the **graphify knowledge graph** of the hal0 repo.
The graph is already built. Do NOT rebuild it. Your job is READ-ONLY analysis + a written report.

## Inputs available to you (in this repo, cwd = /home/mint/hal0)
- `graphify-out/GRAPH_REPORT.md` — 376KB audit report. Sections:
  - `## God Nodes` (line ~1146) — most-connected nodes
  - `## Surprising Connections` (~1158), `## Import Cycles` (~1170)
  - `## Communities (1179 total)` starting line ~1173 — one `### Community N - "name"` block each
  - `## Community Hubs` (~17) — navigation list
- `graphify-out/graph.json` — 26,181 nodes / 49,958 edges (built from commit 270a35ae)
- `graphify-out/wiki/` — per-community articles (if present)
- The actual source tree: `src/hal0/`, `ui/`, `tests/`, `docs/`

## Tools you should use (graphify CLI is installed + allowed)
- `graphify query "<question>"` — BFS traversal, broad context. USE THIS FIRST to orient.
- `graphify explain "<NodeName>"` — plain-language explanation of one node + neighbors
- `graphify path "A" "B"` — shortest path between two nodes
Then Read/Grep specific report sections or source files to confirm.

## Output contract
Write your findings to the exact file path given in your task (under graphify-out/analysis/).
Format: markdown, tight, evidence-backed. Every claim cites a node name, file:line, edge count,
or community number. No fluff. Sections: **Findings** (bulleted, ranked by importance),
**Risks/Smells** (coupling, god nodes, thin tests), **Recommendations** (concrete).
Cap ~250 lines. When done, reply exactly: DONE <your-file-path>

## Facts already known (don't re-derive)
- Top god node: `SlotManager` = 277 edges (next: ENDPOINTS 136, BoardStore 114, connect() 114, ContainerProvider 113).
- No import cycles detected.
- 88% edges EXTRACTED / 12% INFERRED.
- Rework is in-flight: specs live as doc nodes (P3-routers, P3-slots, §17 installer, §21.4 doctor, ML-2/3).
