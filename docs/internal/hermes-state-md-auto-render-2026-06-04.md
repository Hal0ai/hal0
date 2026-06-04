# Hermes live-state auto-render (`STATE.md`) — design

**Date:** 2026-06-04
**Branch:** `feat/hermes-state-md-autorender`
**Status:** Approved design — ready for implementation plan
**Origin:** DreamServer mesh parity item #2 ("auto-render Hermes SOUL from
live slot/capability state every restart"). hal0 has richer state to
inject than DreamServer (NPU + iGPU + capability rollup), so this goes
beyond their `build-installation-context.py`.

## Goal

Hermes always sees the **current** hal0 live state — fresh on every
service restart **and** on every runtime model/capability change —
without:

- bloating the cacheable persona prefix (`SOUL.md`), and
- stalling Hermes session start with a synchronous daemon probe.

## Background — what already exists

- `_phase_context_link` (`src/hal0/agents/hermes_provision.py`) already
  renders `SOUL.md`, `HERMES.md`, `AGENTS.md`, `MCP-CLIENTS.md` from
  Jinja2 templates in `hermes_templates/`, pulling live slot state
  (`/api/slots`), per-model context (`/v1/models`), and a cached
  hardware env snapshot.
- **It only runs at `hal0 agent bootstrap hermes` / `--repair`**, and is
  checkpoint-gated so it won't even re-run unless an input hash drifts.
- The service path is `hal0-agent@hermes.service` → `hal0-agent hermes
  serve` (`cmd_serve` in `src/hal0/cli/agent_shim.py`) → spawns `hermes
  dashboard`. **No render happens at restart.**
- `config.yaml.j2` (line ~213) already declares an `on_session_start`
  hook pointing at `/usr/lib/hal0/hermes-hooks/inject-system-state.sh`
  — **but that script was never written.** It is a dangling reference
  from the 2026-05-23 bootstrap plan; the hook fires every session and
  silently no-ops today.

### Key mechanism fact

Hermes reads cwd-context files (`SOUL.md`, `HERMES.md`) at **process /
session start** — a file rewritten mid-run is not seen until the next
session. The **only** mechanism that re-evaluates per session without a
restart is the `on_session_start` hook, which injects its stdout into
the new session via Hermes's JSON wire protocol (hooks have a per-event
allowlist at `$HERMES_HOME/shell-hooks-allowlist.json`; non-interactive
runs need `--accept-hooks` / `HERMES_ACCEPT_HOOKS=1`). The hook here is
configured with a **2s timeout**.

## Architecture — buffer-file + reader-hook with dual writers

```
                 ┌── ExecStartPre: `hal0-agent <id> render-context`   (on RESTART)
   writers ──────┤
                 └── post-change call in manager.swap() /             (on MODEL/SLOT/
                     orchestrator.apply()                              CAPABILITY CHANGE)
                          │
                          ▼
                  /var/lib/hal0/STATE.md   ← thin volatile snapshot (~12 lines)
                          │
   reader ────────────────┤
                  on_session_start hook (inject-system-state.sh)
                    → cats STATE.md to stdout
                    → if file older than TTL: also spawns DETACHED render-context
                          │
                          ▼
                  injected into EVERY new Hermes session — even if the
                  process has run for hours and the model changed under it
```

The expensive probe runs **only on actual change events** (restart,
swap, apply), never on the latency-sensitive session-start path. The
session-start hook just `cat`s a file → instant, never risks the 2s
timeout.

## Components

### 1. `STATE.md` — volatile live snapshot

- Path: `/var/lib/hal0/STATE.md` (same dir as `HERMES.md`, which is the
  Hermes `terminal.cwd`).
- New template: `src/hal0/agents/hermes_templates/STATE.md.j2`.
- Target size: ~12 lines. Holds **only** volatile state. Fields:
  1. **Loaded chat model + ctx window** — the actually-loaded primary
     model id + its `context_length` (`/api/slots` ∩ `/v1/models`).
  2. **Active capabilities rollup** — one line each for embed / voice
     (stt+tts) / img / rerank that are loaded, and on which backend
     (iGPU vs NPU).
  3. **GPU/NPU runtime backend** — inference backend (Vulkan vs ROCm),
     iGPU clock, NPU XDNA presence + loaded model.
  4. **Live URLs + daemon-health marker** — dashboard URL, lemond base,
     and a one-word `reachable` / `degraded` marker.
  5. **`as_of` timestamp** — when the snapshot content was last
     rendered.

### 2. `render-context` subcommand (`agent_shim.py`)

- New lightweight, render-only entrypoint: `hal0-agent <id>
  render-context`. Added to the `_DISPATCH` table alongside
  `serve`/`stop`/`status`/`reprovision`.
- Re-probes `/api/slots` + `/v1/models` + the cached env snapshot and
  rewrites `STATE.md` (and re-renders `HERMES.md`, since a swap may
  add/remove a slot and change the structural map).
- Reuses the existing render helpers from `hermes_provision`
  (`_fetch_slots`, `_collect_chat_slots`, `_fetch_model_contexts`,
  `_resolve_primary_slot`, `_render_template`, `_atomic_write`). Does
  **not** run the full bootstrap or touch checkpoints.
- **Content-hash gated:** compute a hash over the substantive fields
  (excluding `as_of`); only rewrite the file + bump `as_of` when the
  hash changes. A regen that finds nothing changed does not churn the
  file.
- Degraded handling: if the daemon is unreachable, leave the existing
  `STATE.md` intact and emit a non-zero-but-non-fatal warning; the
  `degraded` marker is only written when we have a partial-but-current
  read.

### 3. ExecStartPre on the unit

- Add to `installer/systemd/hal0-agent@hermes.service.d/override.conf`:
  `ExecStartPre=-/usr/local/bin/hal0-agent %i render-context`.
- Leading `-` → render failure is **non-fatal**: leaves last-good files
  and never blocks the service from starting.

### 4. Runtime writer hook (the second trigger)

- After a **successful** `slots/manager.py:swap()` and
  `capabilities/orchestrator.py:apply()`, fire the same render
  best-effort (logged, swallowed on error, never blocks the API
  response or the swap/apply result).
- Implementation note: factor the render into a small importable
  function so both the CLI subcommand and these call sites use one code
  path (no `subprocess` round-trip from inside the daemon).

### 5. `inject-system-state.sh` — finally write the dangling hook

- Path: `/usr/lib/hal0/hermes-hooks/inject-system-state.sh` (shipped by
  the installer; referenced by `config.yaml.j2` `on_session_start`).
- Behavior:
  1. `cat` `/var/lib/hal0/STATE.md` to stdout in the hook's JSON wire
     format (inject as session context).
  2. If `STATE.md` mtime is older than **TTL = 5 minutes**, additionally
     spawn a **detached** `hal0-agent hermes render-context` (background,
     `&` / `setsid`, output discarded) so the *next* session is fresh.
     **Never** block on the probe — the current session always gets the
     existing (possibly slightly stale) file immediately.
  3. Stay well inside the 2s hook timeout (pure `cat` + a non-blocking
     spawn).
- If `STATE.md` is missing entirely (first boot before any render),
  emit nothing and exit 0 — never error the session.

### 6. `SOUL.md` and `HERMES.md` boundary cleanup

- **`SOUL.md`**: unchanged in spirit — stable persona (identity +
  operating principles + boundaries). Stays byte-stable across restarts
  → keeps the large cacheable prefix warm.
- **`HERMES.md`**: trimmed to **structural / rarely-changing** content
  (slot layout intent, peer agents, skills paths, conventions). Its
  current *live* "primary / chat slot" lines (template lines ~16–28)
  **move into `STATE.md`** to avoid duplication and to stop the live
  data from churning the structural file.

## Freshness model (resolved decisions)

- **Primary freshness = the two event writers** (restart, swap/apply).
  These carry the real load and keep `STATE.md` current almost always.
- **TTL (5 min) = defense-in-depth** for *missed* events (an unobserved
  daemon restart, a mutation path not hooked, a writer that failed).
  Implemented as a **non-blocking background kick** in the hook — serve
  stale instantly, refresh out of band (stale-while-revalidate).
- **`as_of` timestamp + `degraded` marker** make staleness *visible* to
  the agent instead of silent — directly countering the
  "silent-fallback" failure class that bit the original bootstrap
  (PR #316).

## Non-goals / boundaries

- No new daemon, no systemd timer unit (TTL lives in the hook).
- No SIGHUP config-reload path.
- No change to bootstrap phase ordering or checkpoint logic.
- Approach 2 (restart-only re-render) and Approach 3 (hook-only live
  probe, no file) were considered and **rejected** — 2 fails the
  runtime-change trigger; 3 puts the probe on the session path.

## Testing strategy

- **Unit:** `STATE.md.j2` renders correctly for (a) full live state,
  (b) no chat slot loaded, (c) daemon degraded. Content-hash gating:
  identical substantive state → no rewrite / no `as_of` bump; changed
  field → rewrite + bump.
- **Unit:** `render-context` subcommand dispatches, writes atomically,
  and is non-fatal when the daemon is unreachable.
- **Integration (LXC):** restart `hal0-agent@hermes` → `STATE.md`
  refreshes (ExecStartPre); `hal0 slot swap` / `capability_set` →
  `STATE.md` refreshes (runtime writer); start a new Hermes session →
  hook injects current `STATE.md`; touch `STATE.md` mtime back >5 min →
  new session still instant + a background regen fires.
- **Cache check:** confirm `SOUL.md` + `HERMES.md` are byte-identical
  across a restart that doesn't change slots (only `STATE.md` differs).

## Affected files (anticipated)

- `src/hal0/agents/hermes_templates/STATE.md.j2` (new)
- `src/hal0/agents/hermes_templates/HERMES.md.j2` (trim live lines)
- `src/hal0/agents/hermes_provision.py` (extract shared render fn;
  render `STATE.md` in `_phase_context_link` too)
- `src/hal0/cli/agent_shim.py` (`render-context` subcommand)
- `src/hal0/slots/manager.py` (`swap()` post-change render call)
- `src/hal0/capabilities/orchestrator.py` (`apply()` post-change render call)
- `installer/systemd/hal0-agent@hermes.service.d/override.conf` (ExecStartPre)
- `installer/agents/hermes/hooks/inject-system-state.sh` (new) — source
  asset, installed to `/usr/lib/hal0/hermes-hooks/` (`LIB_DIR` in
  `install.sh`); add the copy+chmod step to `install.sh` and the matching
  removal to `uninstall.sh`
- Tests under `tests/` mirroring the above.
