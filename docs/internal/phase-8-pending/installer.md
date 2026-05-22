# Wave 1 — Agent-installer pending coupling notes

Coupling notes left for the orchestrator + sibling wave-1 teammates.
Items still open after the orchestrator-wave merge are flagged
**OPEN**; resolved ones are crossed out for the historical record.

## 1. ~~Orchestrator wires (NOT done in this worktree)~~ — RESOLVED

~~Per the brief, this teammate does NOT edit `src/hal0/api/__init__.py`,
`src/hal0/cli/main.py`, or `pyproject.toml`. The orchestrator needs to:~~

~~- **API mount**: `include_router(routes.agents.router, prefix="/api/agents", tags=["agents"])` in `hal0.api.create_app` (or wherever the other routers are wired). Auth posture: GET is read-only-friendly; the mutations carry their own `require_writer` dep.~~
~~- **CLI mount**: in `src/hal0/cli/main.py`, add `app.add_typer(agent_app, name="agent")`.~~
~~- **pyproject.toml**: no new top-level dependencies required.~~

Landed in the orchestrator wave (`108d1fb feat(phase-8): orchestrator
wires MCP + agents + approvals into the app`). Phase 8 v0.2 is shipped.

## 2. ~~MCP-backend coupling — approval route shape ASSUMED~~ — RESOLVED

`src/hal0/cli/agent_commands.py::approvals_*` calls these routes:

- `GET    /api/agent/approvals`              → `{"approvals": [...]}`
- `POST   /api/agent/approvals/{id}/approve` → `{"approval": {...}}`
- `POST   /api/agent/approvals/{id}/deny`    → `{"approval": {...}}`

Landed in `src/hal0/api/routes/approvals.py` with the envelope key
`approvals` matching the CLI's expectation. Each approval row carries
`id`, `tool`, `args`, `client_id`, `enqueued_at`. The CLI rendering
still expects `agent` / `requested_at` / `summary` keys that the live
shape does not produce 1:1 — the CLI degrades to "—" placeholders
which is acceptable for v0.2 but a follow-up could align the keys.

The Bearer-token plumbing for these calls flows through the shared
`hal0.cli._shared.api_*` helpers, which today do NOT inject an
`Authorization` header automatically. If the MCP-backend route requires
a writer-scope token (it should — these are gated destructives), the
CLI's `_shared.api_*` helpers need an auth-injection pass. Out of
scope for this wave.

## 3. ~~Memory-engine coupling — `/mcp/memory` endpoint assumed~~ — RESOLVED

Landed at `/mcp/memory` per `src/hal0/mcp/memory.py`; the
`_MCP_MEMORY_PATH` constant in `pi_coder.py` matches the live mount
point.

## 4. Token storage — best-effort read from `tokens.toml`

`installer/agents/pi-coder.sh` and `installer/agents/hermes-agent.sh`
both grep `/etc/hal0/tokens.toml` with awk:

```sh
awk '/^wire_token *= */ {gsub(/"/,"",$0); print $3; exit}' /etc/hal0/tokens.toml
```

This is heuristic, not authoritative. If `tokens.toml`'s on-disk
format changes (the file is owned by `src/hal0/auth/tokens.py`),
the grep needs to update too. Alternative path the auth team may want
to land: a `hal0 auth token mint --for=agent-pi-coder` CLI command that
prints just the wire token, which the shell script could call.

## 5. Hermes hal0-awareness probe — placeholder

`hal0.agents.hermes._probe_hal0_awareness` looks for `--hal0-config` in
`hermes-agent --help` output OR `HERMES_HAL0_READY=1` env. This is the
testable probe shape promised in ADR-0004 §6. If the Hermes upstream
ships a different signal (e.g. a `hermes-agent version --capabilities`
JSON surface), update the probe + the shell-script mirror in
`installer/agents/hermes-agent.sh::probe_hermes_hal0_aware`.

## 6. ~~Nightly CI smoke test — NOT landed here~~ — RESOLVED

Landed in `976c985 ci(phase-8): nightly pi-coder shim smoke test
(ADR-0004 §3 mitigation)`. The shim scripts retain their informative
error messages.

## 7. ~~First-run wizard picker — NOT landed here~~ — RESOLVED

Step 7 of the first-run wizard (Agent picker) landed in
`2083c05 feat(phase-8): dashboard /agent page + approval inbox +
first-run picker`. The wizard fires `POST /api/agents/install` with
the operator's pick. Hermes option is disabled when the
hal0-awareness probe returns false.

## 8. Hermes service template — NOT landed here

ADR-0004 §3 says Hermes runs as `hal0-agent-hermes.service`, instance
of `hal0-agent@.service` template that mirrors `hal0-slot@.service`.
The template file is owned by the systemd-templates teammate (or, if
nobody, it's a future wave). The `installer/agents/hermes-agent.sh`
script delegates start-on-install to `hermes-agent install` itself,
which is consistent with ADR-0004 §6 "hal0's Hermes shim is a
one-liner calling Hermes's own install command."

---

## Done in this worktree (for the orchestrator's PR description)

- `src/hal0/agents/__init__.py` (33 lines) — re-export surface
- `src/hal0/agents/manager.py` (~310 lines) — single-pick, atomic
  switch, seed-TOML I/O
- `src/hal0/agents/pi_coder.py` (~140 lines) — shim driver +
  adapter-config writer
- `src/hal0/agents/hermes.py` (~165 lines) — shim driver +
  hal0-awareness probe + env-file writer
- `src/hal0/api/routes/agents.py` (~125 lines) — REST routes
- `src/hal0/cli/agent_commands.py` (~175 lines) — CLI subcommands
- `installer/agents/pi-coder.sh` (~125 lines) — POSIX installer
- `installer/agents/hermes-agent.sh` (~95 lines) — POSIX installer
- `tests/agents/__init__.py` (empty)
- `tests/agents/test_manager.py` (~215 lines) — 12 tests
- `tests/agents/test_pi_coder_shim.py` (~165 lines) — 7 tests
- `installer/uninstall.sh` — `uninstall_agents()` hook + invocation
  (additive, +43 lines)

Test suite: 19/19 passing.
