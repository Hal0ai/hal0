# Hal0 Upstream Controls — Epic Plan (2026-07-06)

## Epic

A coherent set of controls to make remote upstreams first-class routable
targets without polluting the model catalog or the agent UIs.

## Issues & dependency graph

```
        ┌── #1147  runtime advertise_models toggle (API + live cache-punch)  [foundation]
        │             ready-for-agent
        │             branch: feat/1147-upstream-advertise-toggle
        │
        ├── #1150  hal0 upstream CLI (list, advertise on/off)                 [blocker → #1147]
        │             ready-for-agent
        │
        ├── #1151  Settings panel UI for upstreams                            [blocker → #1147]
        │             ready-for-human  ← UX review before merge
        │
        ├── #1148  Hermes hal0-provider filter owned_by==hal0                 [independent]
        │             ready-for-agent
        │
        └── #1149  Namespace hal0 slots as hal0/<slot> in Pi + Hermes         [independent]
                      ready-for-human  ← design call: bare-name back-compat
```

## Execution order

1. **#1147** — foundation. Land first; everything else either builds on it
   or runs in parallel with it.
2. **#1148 + #1149** — independent of #1147, can ship any time after their
   respective design questions are settled (#1149 needs the bare-name
   back-compat decision).
3. **#1150** — once #1147 lands. CLI is a thin layer over the new API.
4. **#1151** — once #1147 lands. UI work; HITL UX review before merge.

## #1147 — design notes

**Existing pieces that this PR reuses (don't duplicate):**
- `UpstreamRegistry.update(name, **patch)` — in-memory merge
- `save_upstreams_config(cfg)` + `write_toml_atomic()` — already atomic
- `GET /api/upstreams` and `GET /api/upstreams/{name}` — already serialize
  `advertise_models` + model count
- `v1.list_models` at `routes/v1.py:660` — already filters live by
  `advertise_models`, so toggling the flag automatically excludes rows

**What #1147 actually adds:**
1. `PATCH /api/upstreams/{name}` endpoint
   - Body: `{"advertise_models": bool}` (only this field for now — others
     deferred per the "Write paths are intentionally deferred" docstring
     in `routes/providers.py`)
   - Persists atomically via `save_upstreams_config`
   - Punches `app.state.upstream_models[name]` so the next fetch is fresh
   - Audit-logs the change
2. `UpstreamRegistry.set_advertise(name, value)` (or equivalent) that:
   - Calls `update(name, advertise_models=value)`
   - Reads `UpstreamsConfig` from disk, updates the matching entry, writes back
   - Returns the new Upstream
3. Tests covering:
   - Toggle off → row absent from /v1/models within one request
   - Toggle back on → row reappears
   - Atomic write survives a crash mid-write (no half-written file)
   - 404 on unknown upstream
   - Dispatch by explicit id unchanged when advertise is off
   - Empty model count when advertise is off (upstream row shows count=0 / cleared)

## #1148 — design notes

Hermes' hal0 provider must filter `/v1/models` to `owned_by == "hal0"`,
mirroring Pi's `hal0-provider` extension (already does this). Single-line
filter change; tests cover the remote-passthrough exclusion case.

## #1149 — design notes

**Open question: bare-name back-compat.**
- Standardize emitted form on `hal0/<slot>` (e.g. `hal0/agent`, `hal0/code`)
- Dispatch must resolve BOTH `hal0/<slot>` and bare `<slot>`
- ADR-0023 fallback contract preserved (e.g. `hal0/utility`/`hal0/npu`)

**Need operator decision before coding:**
- How long do we keep bare-name dispatch? Forever? Until next major?
- Log a deprecation warning when a bare name is used?

## #1150 — design notes

CLI surface over the #1147 API:
```
hal0 upstream list                      # table: name, kind, url, advertise, count, auth
hal0 upstream advertise <name> on|off   # calls PATCH endpoint
```
Mirrors existing `hal0 model` / `hal0 slot` CLI style in `cli/config_commands.py`.

## #1151 — design notes

Settings panel section. Backend = #1147 API. UX is the question — operator
review before merge.

## Acceptance sequencing

Each issue becomes its own PR. Sequence:
- PR #1147 → merged
- PR #1148 → merged (parallel to #1149)
- PR #1149 → merged (after bare-name decision)
- PR #1150 → merged (after #1147)
- PR #1151 → ready-for-review (UX HITL)