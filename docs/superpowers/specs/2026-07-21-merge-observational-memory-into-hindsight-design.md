# Hindsight pi Extension Design

**Date:** 2026-07-22

**Status:** Implemented (Claude Code plugin port).

**Scope:** pi-side only. The hal0-api and Hindsight services are unchanged.

## Purpose

Replace the `pi-observational-memory` npm package and its thin-bridge hal0-memory approach with a full TypeScript port of the Claude Code Hindsight plugin (`vectorize-io/hindsight/hindsight-integrations/claude-code`) adapted for pi's event API. The extension connects directly to Hindsight at `http://10.0.1.142:9177` (LAN-exposed, no auth) and provides:

- **LLM tools:** `hindsight_retain`, `hindsight_recall`, `hindsight_reflect`, `hindsight_status`
- **Auto-recall** on `session_start` (deduped per session)
- **Auto-retain** on `turn_end` (throttled by `retainEveryNTurns`)
- **Pre-compaction retain** on `session_before_compact`
- **Dynamic bank IDs** (`static` or `dynamic` with granularity: agent, project, session, channel, user, gitProject)
- **Bank missions** set on first use, cached in plugin state
- **Slash commands:** `/hindsight`, `/hindsight-recall`, `/hindsight-doctor`

## Architecture

```
~/.pi/agent/extensions/hindsight/
  index.ts      — entry point, registers tools + hooks
  client.ts     — HindsightClient (REST wrapper for /v1/default/banks/{id}/...)
  bank.ts       — deriveBankId (static/dynamic/directoryBankMap + git worktree),
                  ensureBankMission
  content.ts    — stripMemoryTags, composeRecallQuery, truncateRecallQuery,
                  sliceLastTurnsByUserBoundary, prepareRetentionTranscript,
                  formatMemories, formatCurrentTime
  config.ts     — typed HindsightConfig + DEFAULT_HINDSIGHT_CONFIG
  loader.ts     — 4-layer config merge: defaults → ~/.pi/agent/settings.json
                  hindight block → ~/.hindsight/pi.json → HINDSIGHT_* env vars
  state.ts      — PluginState (missionsSet, recalledSessions, turn counting)
  hooks.ts      — session_start (recall), turn_end (retain), session_before_compact
  *.test.ts     — unit tests (116 passing)
```

All 116 unit tests pass via `node --experimental-strip-types --test *.test.ts`.

## Dynamic Bank IDs

Supports three modes, ordered by priority:

1. **directoryBankMap** — explicit directory → bank overrides (highest)
2. **dynamicBankId** — composed from `dynamicBankGranularity` fields
3. **static** — single fixed `bankId` or default `"pi-coder"`

Dynamic granularity fields: `agent`, `project`, `session`, `channel`, `user`.

Git-worktree-aware project resolution enabled by default (`resolveWorktrees: true`) so linked worktrees share one bank.

Channel and user come from `HINDSIGHT_CHANNEL_ID` / `HINDSIGHT_USER_ID` env vars.

## What was replaced

| Old | New |
| --- | --- |
| `npm:pi-observational-memory` | Removed (uninstalled) |
| `~/.pi/agent/extensions/hal0-memory/` | Deleted (auto-discovery removed) |
| `observational-memory` settings block | Removed |
| `hal0_memory_add/search/recall/list/delete/whoami` tools | `hindsight_retain/recall/reflect/status` tools |
| Thin-bridge auto-capture (auto-capture.ts, bank-selector.ts, etc.) | Claude Code plugin port (hooks.ts, client.ts, bank.ts, content.ts) |
| hal0-api proxy (<http://10.0.1.142:8080>) | Hindsight direct (<http://10.0.1.142:9177>) |

## Verification

Smoke-tested live against Hindsight 0.8.4 at `http://10.0.1.142:9177`:

- ✅ Retain → server extracts within ~5s
- ✅ Recall → returns ranked facts with scores
- ✅ Reflect → synthesizes coherent markdown response
- ✅ listBanks → 7 banks visible
- ✅ healthCheck → `/health` returns 200

## Settings (`~/.pi/agent/settings.json`)

```json
{
  "hindsight": {
    "hindsightApiUrl": "http://10.0.1.142:9177",
    "bankId": null,
    "dynamicBankId": false,
    "dynamicBankGranularity": ["agent", "project"],
    "resolveWorktrees": true,
    "autoRecall": true,
    "autoRetain": true,
    "retainMode": "full-session",
    "retainEveryNTurns": 3,
    "retainOverlapTurns": 2,
    "recallBudget": "mid",
    "recallMaxTokens": 1024,
    "recallTypes": ["world", "experience", "observation"],
    "debug": false
  }
}
```

## Implementation done

1. Ported Claude Code plugin → TypeScript pi extension
2. 116 unit tests passing
3. Settings.json updated (dropped OM block, dropped pi-OM from packages, added hindsight block)
4. `~/.pi/agent/extensions/hal0-memory/` deleted (auto-discovery would race)
5. `pi-observational-memory` uninstalled from `~/.pi/agent/npm/`
6. Live Hindsight smoke tested (retain → recall → reflect)
