# Merge pi-observational-memory into Hindsight Design

**Date:** 2026-07-21

**Status:** Approved

**Scope:** pi-side only. The hal0-api and Hindsight services are unchanged.

## Purpose

Replace the `pi-observational-memory` extension (`/home/mint/.pi/agent/npm/node_modules/pi-observational-memory/`) with a thin bridge inside the existing `hal0-memory` pi extension. The bridge keeps observational memory's auto-capture behavior but routes captured session chunks into Hindsight via the same `hal0_memory_add` REST surface the hal0-memory LLM tools already use. Hindsight's existing extraction pass then structures the raw text into `world` / `experience` / `observation` records — replacing pi-OM's three sub-agents (observer, reflector, dropper) with a single server-side extraction model.

This is a strict simplification. The local session-ledger, the in-process sub-agent runs, the `recall_observation` tool, and the `/om-status` / `/om-view` commands all disappear. Auto-capture moves into hal0-memory and writes through Hindsight.

## Evidence and constraints

Three sources shape this design:

1. The current `pi-observational-memory` source tree (`/home/mint/.pi/agent/npm/node_modules/pi-observational-memory/src/`).
2. The current `hal0-memory` extension (`/home/mint/.pi/agent/extensions/hal0-memory/index.ts`) and its REST contract.
3. The Hindsight extraction model already wired through `HINDSIGHT_API_LLM_MODEL` (configured on the hal0 host, not in pi).

Hard constraints:

- **No hal0-api or Hindsight API changes.** The bridge uses the existing `/api/memory/add` endpoint with the documented `X-hal0-Agent` and `X-hal0-Private` headers.
- **Auto-capture must not block the agent loop.** Pushes happen on `turn_end` after the LLM has already responded, so latency is tolerable but not free; the push itself must be bounded (10s timeout) and best-effort with a small in-memory buffer for retry.
- **One writer per package manager directory.** No parallel writers touching `~/.pi/agent/npm/package.json` or `~/.pi/agent/settings.json`.
- **The existing `hal0-memory` LLM tool surface (`hal0_memory_add` / `hal0_memory_search` / `hal0_memory_recall` / `hal0_memory_list` / `hal0_memory_delete` / `hal0_memory_whoami`) stays as-is.** The bridge calls the same REST endpoint via direct `fetch`, not via the LLM tool (which would round-trip through the model).

## Product outcomes

After delivery:

- pi-OM is uninstalled and removed from `packages`. The `observational-memory` config block is gone from `~/.pi/agent/settings.json`.
- Every meaningful turn (`turn_end`) pushes a serialized chunk of new source entries to Hindsight via `hal0_memory_add` once the chunk crosses `minTokensBetweenPushes` (default 25,000 tokens).
- The bridge chooses the destination bank per chunk: `shared` when cwd has a project marker and is not under a scratch root; `private:pi-coder` otherwise.
- The bridge buffers failed pushes per bank (4 chunks cap) and retries on the next `turn_end`. Buffer overflow drops oldest with a single warning.
- A small `autoMemory.pushed` session coverage entry marks each successful push, so reloads resume from the right point.
- The existing hal0-memory LLM tools and slash commands continue to work; nothing user-visible changes there.

## Non-goals

- Migrating historical `om.observations.recorded` / `om.reflections.recorded` / `om.observations.dropped` entries out of old session jsonl files. They stay as cold history (per user decision). Pruning happens via `/compact` on a fresh branch.
- Replacing Hindsight's extraction model with a pi-side sub-agent. Hindsight owns structuring.
- Touching pi's native compaction (`compaction.enabled` / `reserveTokens` / `keepRecentTokens`). Native compaction still handles context-window pressure.
- Touching the hal0-api or Hindsight codebase.
- Replacing the hal0-memory LLM tool surface (`hal0_memory_*` tools stay).
- A new bridge configuration UI. All knobs live in `~/.pi/agent/settings.json` under `hal0-memory.autoCapture`.

## Architecture

The bridge is a single new module inside the existing `hal0-memory` extension. No new npm package.

```
/home/mint/.pi/agent/extensions/hal0-memory/
  index.ts            — existing REST client + LLM tools + slash commands
  auto-capture.ts     — NEW: turn_end hook, threshold gate, bank selector,
                        buffer/retry, hal0_memory_add wrapper, coverage marker
  config.ts           — NEW: typed AutoCaptureConfig with defaults
  bank-selector.ts    — NEW: pure function — cwd → "shared" | "private"
  auto-capture.test.ts   — NEW: bank-selector + threshold + buffer unit tests
```

`auto-capture.ts` registers two pi event listeners:

- `session_start` — re-hydrates `lastPushedCoverageId` from the most recent `autoMemory.pushed` entry in `ctx.sessionManager.getBranch()`, runs the reachability probe, and resets the buffer-warning latch.
- `turn_end` — the only listener that does real work; see the data flow below.

State owned in memory:

- `lastPushedCoverageId: string | undefined` — id of the most recent `autoMemory.pushed` session entry. Persisted across reloads because the coverage entry itself is in the jsonl; the in-memory pointer is rebuilt on `session_start` by walking the branch.
- `buffer: Map<"shared" | "private", Chunk[]>` — per-bank retry buffer, FIFO, 4 chunks cap each.
- `bufferOverflowWarned: Set<"shared" | "private">` — single-warning latch per bank.
- `endpointReachable: boolean` — set by the `session_start` reachability probe. False disables all subsequent `turn_end` work for the lifetime of the session.

The bridge imports the existing REST helpers from `index.ts` (`headers`, `request`) and uses the same `BASE_URL` / `AGENT_ID` constants — single source of truth for the hal0 endpoint.

## Data flow

```
turn_end fires
  └─ pi.on("turn_end", (event, ctx) => …)
       ├─ load AutoCaptureConfig (cwd-aware via ctx.cwd)
       ├─ if !cfg.enabled → return
       │
       ├─ flushBufferIfAny()                       ── (a) retry
       │     for each bank in buffer:
       │       while buffer[bank].length > 0:
       │         chunk = buffer[bank].shift()
       │         try push; on failure, unshift back, break inner
       │
       ├─ compute newSourceTokens since lastPushedCoverageId
       │     (linear walk of source entries after the marker; tool results
       │      truncated to 2000 chars before measuring; metric is
       │      text.length / 4 — heuristic, matches pi-OM's intent)
       │
       ├─ if newSourceTokens < cfg.minTokensBetweenPushes → return
       │
       ├─ serialize the same entries into a text chunk
       │     shape (from pi-OM's serializeSourceAddressedBranchEntries):
       │       [User]: <text>
       │       [Assistant]: <text>
       │       [Assistant tool calls]: tool(args); tool(args)
       │       [Tool result]: <output truncated to 2000 chars>
       │
       ├─ prepend a 4-line header:
       │     ## Auto-captured session chunk
       │     cwd: <ctx.cwd>
       │     timestamp: <ISO>
       │     source entries: <id1> <id2> … <idN>
       │
       ├─ bank = selectBank(ctx.cwd, cfg)
       ├─ tags = [...cfg.tags, "session:<sid>", "project:<git-remote-or-none>"]
       │
       ├─ POST /api/memory/add { text, tags }
       │     headers X-hal0-Agent: pi-coder
       │             X-hal0-Private: <0 if shared else 1>
       │     AbortSignal.timeout(10_000)
       │
       ├─ on success:
       │     append custom entry `autoMemory.pushed`
       │       { pushId: <operation_id>, bank, tokenCount,
       │         sourceEntryIds, timestamp, firstSourceId, lastSourceId }
       │     lastPushedCoverageId = new entry id
       │
       └─ on failure (network error, !res.ok, timeout):
             buffer[bank].push({text, tags, sourceEntryIds, attemptedAt: now})
             if buffer[bank].length > 4:
               drop oldest
               if !bufferOverflowWarned.has(bank):
                 ctx.ui.notify("hal0-memory autoCapture: buffer overflow on <bank>, dropped oldest chunk", "warning")
                 bufferOverflowWarned.add(bank)
             return (do NOT append coverage marker)
```

Order matters: **(a) flush-first then (b) compute-and-push**. This way a transient hal0 outage drains before fresh pushes accumulate, and the next turn never skips a retry that could have succeeded.

## Bank selector (`bank-selector.ts`)

Pure function, zero side effects:

```ts
export function selectBank(
  cwd: string,
  cfg: AutoCaptureConfig,
): "shared" | "private" {
  const home = os.homedir();
  const expanded = (p: string) => p.replace(/^~/, home);

  // Rule R3 — scratch roots deny shared
  for (const r of cfg.scratchRoots) {
    if (isUnder(cwd, expanded(r))) return "private";
  }
  // Project markers promote shared
  for (const m of cfg.projectMarkers) {
    if (fs.existsSync(path.join(cwd, m))) return "shared";
  }
  return cfg.defaultBank;
}

function isUnder(child: string, parent: string): boolean {
  const rel = path.relative(parent, child);
  return !!rel && !rel.startsWith("..") && !path.isAbsolute(rel);
}
```

Defaults:

- `scratchRoots: ["~/.pi", "~/scratch", "~/tmp"]`
- `projectMarkers: ["pyproject.toml", "package.json", "Cargo.toml", "go.mod"]`
- `defaultBank: "private"`

All three overridable in settings. The `defaultBank` is the third fallback so users who only want "shared in projects, private everywhere else" can leave defaults; users who want "shared everywhere outside `~/.pi`" can set `scratchRoots: ["~/.pi"]`.

## Error handling

Failure modes for `POST /api/memory/add`:

1. **Network error / DNS / TCP reset** → `fetch` rejects with `TypeError`. Caught, treated as transient.
2. **hal0-api 5xx** → `!res.ok`, body captured for logs.
3. **hal0-api 4xx** (e.g., malformed payload) → `!res.ok`. Treated as transient too, because we don't have a clean way to distinguish "fixable" from "permanent" without inspecting the body.
4. **Timeout** (`AbortSignal.timeout(10_000)`) → treated as transient.
5. **JSON parse failure on success body** → caught, logged, treated as success-but-warning because the `operation_id` could not be parsed (we still append the coverage marker with `pushId: undefined`).

Buffer policy:

- 4 chunks per bank. Each chunk is one `turn_end` push worth of text.
- The threshold gate (`minTokensBetweenPushes = 25000` by default) is a lower bound — pushes only happen at or above 25k new tokens. There is no upper bound on a single chunk; a dense turn can produce one chunk much larger than 25k tokens.
- Buffer overflow drops oldest, single `ui.notify` warning per bank per session. The latch is reset on every `session_start`.
- A `hal0_memory_whoami`-style endpoint reachability check happens once per session in `session_start`; if the endpoint is unreachable, the bridge sets `endpointReachable = false`, fires a single `ui.notify`, and skips all `turn_end` work for the rest of the session. This avoids spamming the buffer on a fully-down hal0-api. Re-enabling requires the user to start a new pi session (no mid-session re-probe).

## Token-threshold coverage tracking

The "new-source tokens since last push" metric is local and cheap:

- Walk source entries after the last `autoMemory.pushed` marker (or since session start if none).
- For each entry: serialize to text using the same shape as the push payload, but only for measurement. Use `serializeConversation`-style markers. Tool results truncated to 2000 chars before counting.
- Total `text.length / 4` rounded to integer.
- If the result is `< cfg.minTokensBetweenPushes`, skip.

This is a heuristic — the pi-OM package used the same shape. We don't need exact token counts; we need a stable, predictable trigger.

On the very first `turn_end` of a session, `lastPushedCoverageId` is undefined, so the walk covers the entire historical session since the start. If that exceeds the threshold, we push. This is intentional — cold-start sessions get caught up.

## Configuration shape

`~/.pi/agent/settings.json` diff:

```diff
   "packages": [
     "extensions/hal0-provider",
-    "npm:pi-observational-memory",
     "npm:@juicesharp/rpiv-todo",
     "npm:pi-lens",
     …
   ],
-  "observational-memory": {
-    "observeAfterTokens": 25000,
-    "reflectAfterTokens": 50000,
-    "compactAfterTokens": 70000,
-    "compactAfterTokensMode": "calibrated",
-    "compactAfterTokensRatio": 0.68,
-    "observationsPoolMaxTokens": 25000,
-    "observationsPoolTargetTokens": 15000,
-    "agentMaxTurns": 16,
-    "model": {
-      "provider": "hal0",
-      "id": "utility",
-      "thinking": "low"
-    },
-    "passive": false,
-    "debugLog": false
-  },
+  "hal0-memory": {
+    "autoCapture": {
+      "enabled": true,
+      "minTokensBetweenPushes": 25000,
+      "scratchRoots": ["~/.pi", "~/scratch", "~/tmp"],
+      "projectMarkers": ["pyproject.toml", "package.json", "Cargo.toml", "go.mod"],
+      "defaultBank": "private",
+      "tags": ["obs:auto", "agent:pi-coder"],
+      "pushTimeoutMs": 10000,
+      "bufferMaxChunksPerBank": 4
+    }
+  },
```

`~/.pi/agent/npm/package.json` diff:

```diff
   "dependencies": {
-    "pi-observational-memory": "^3.0.3"
   }
```

After applying: `cd ~/.pi/agent/npm && npm uninstall pi-observational-memory` to drop the directory and prune `node_modules`.

The hal0-memory extension is loaded via `extensions/hal0-provider` in `packages`, which already wires the directory into pi. No package list change is needed for the extension itself — only the new module file (`auto-capture.ts`) inside the extension directory.

## Settings migration safety

The old `observational-memory` block has 12 keys. After removal, the only safe removal is the whole block — partial migration could leave dangling keys that nothing reads. So:

1. Backup `~/.pi/agent/settings.json` to `~/.pi/agent/settings.json.bak-pre-om-merge-<timestamp>` before any edit.
2. Remove the entire `observational-memory` block atomically (one edit).
3. Add the new `hal0-memory.autoCapture` block atomically (one edit).
4. Verify with `cat ~/.pi/agent/settings.json | python3 -m json.tool` before saving.

If anything fails partway, restore from the timestamped backup.

## Testing

Unit tests live next to the source (no test runner other than `node --test` to avoid adding dependencies):

- `bank-selector.test.ts`
  - cwd under `~/.pi/sessions/...` → private
  - cwd under `~/scratch/...` → private
  - cwd under `~/hal0/src/foo` (has `pyproject.toml`) → shared
  - cwd under `~/projects/foo` (has `package.json`) → shared
  - cwd under an empty dir with no markers and not in scratch → default ("private")
  - custom `scratchRoots: ["~/.pi"]`, cwd under `~/scratch` → default ("private") confirms scratch list override works
  - custom `defaultBank: "shared"`, cwd outside projects → "shared"
  - path-boundary edge case: cwd exactly equal to a scratch root → private (boundary inclusive)
  - path-boundary edge case: cwd that starts with `~/.pi` as a string prefix but isn't actually under it (e.g., `~/.piXYZ`) → not under

- `auto-capture.test.ts`
  - threshold gate: 24,999 new tokens → no push; 25,001 → push
  - first-of-session: no prior marker → push when total exceeds threshold
  - bank isolation: failed push to shared does not block push to private
  - buffer cap: 5 failures → 4 buffered, oldest dropped, warning fired
  - buffer warning latch: second overflow on same bank → no second warning
  - flush-on-next-turn: after a failure, the next turn_end successfully drains the buffer before computing its own chunk
  - coverage marker: `autoMemory.pushed` entry is appended on success, not on failure
  - session_start: `whoami` unreachable → `cfg.enabled = false`, single notify, no subsequent pushes
  - tool result truncation in measurement (a 10,000-char tool result counts as 2000 chars toward threshold)

Manual smoke test on a real pi session:

1. Apply settings changes; restart pi.
2. Confirm `/mem-doctor` shows hal0-api reachable.
3. From `~/hal0/src/foo` (a project marker cwd), run a 3-turn conversation. Verify 1 push lands in `shared` via `hal0_memory_list`.
4. From `~/.pi/sessions/...` (a scratch cwd), run a 3-turn conversation. Verify 1 push lands in `private:pi-coder` via `hal0_memory_list`.
5. Inspect `hal0_memory_recall "what did I work on today"` — should surface both pushes' extracted facts.

## Risks

1. **Buffer cap may be too small for long hal0 outages.** If hal0-api is down for a full work session, the bridge drops oldest chunks. Mitigation: the LLM tool `hal0_memory_add` (still callable explicitly) lets the agent catch up at any time. Acceptable for an observability feature.

2. **Cold-start push may be large.** First `turn_end` of a session with no prior marker measures from session start; for a long-resumed session this could push tens of thousands of tokens in one go. Hindsight's extraction handles large inputs (the existing `hal0_memory_add` tool has been used for similar payloads). Acceptable.

3. **Coverage marker never gets pruned.** Each successful push appends a `custom` entry to the jsonl. For a long session with many pushes, this adds a small constant per push to the session file size. Mitigation: markers are tiny (`<200` chars each), and `pi.compact` drops them along with the rest of the source when the session is compacted.

4. **`/om-status` and `/om-view` muscle memory.** Users who learned these commands will get "command not found" until they re-learn `/mem-doctor` / `/mem` / `hal0_memory_recall`. Acceptable; commands are listed by `/help`.

5. **No migration of historical pi-OM entries.** Old session files contain `om.observations.recorded` entries that are now ignored. The next time those sessions are loaded, pi renders them as opaque custom entries. Acceptable per user decision.

## What the user sees after delivery

- `settings.json` is shorter (one block removed, one small block added).
- `~/.pi/agent/npm/node_modules/pi-observational-memory/` is gone.
- The LLM tool list in pi is unchanged (`hal0_memory_*` tools still present; `recall_observation` is gone).
- The slash command list loses `/om-status` and `/om-view`. `pi help` shows `/mem`, `/mem-recall`, `/mem-forget`, `/mem-doctor`.
- During a session, every heavy `turn_end` produces a status toast: `hal0-memory autoCapture: pushed ~28k tokens → bank=shared`.
- `hal0_memory_search` and `hal0_memory_recall` return auto-captured session chunks along with anything the agent explicitly added.

## Implementation outline (for the writing-plans handoff)

1. Create `auto-capture.ts`, `config.ts`, `bank-selector.ts` inside `~/.pi/agent/extensions/hal0-memory/`.
2. Wire `index.ts` to call the auto-capture module from `session_start` and `turn_end`.
3. Add unit tests in the same directory.
4. Back up and update `~/.pi/agent/settings.json` (drop `observational-memory` block, add `hal0-memory.autoCapture`).
5. Update `~/.pi/agent/npm/package.json` (drop `pi-observational-memory` dependency).
6. Run `npm uninstall pi-observational-memory` in `~/.pi/agent/npm/`.
7. Smoke test from a project cwd and a scratch cwd.
8. Verify `/mem-doctor` and `hal0_memory_recall` surface the auto-captured chunks.
