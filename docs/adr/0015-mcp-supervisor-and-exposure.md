# ADR-0015: MCP process supervisor and Hermes exposure join

## Status

Accepted (http/sse scope). stdio supervision is designed but deferred —
see "Deferred: stdio supervisor" below.

## Context

`src/hal0/mcp/installed.py` (issue #305) already gives operators a
per-server TOML registry under `/etc/hal0/mcp-servers/<id>.toml` —
atomic-written, 0600, lock-protected (`installed.py:211-314`). What it
does not do:

- An installed server never reaches Hermes. `_default_mcp_servers()`
  (`src/hal0/agents/hermes_provision.py:1462-1497`) is the sole source of
  `mcp_servers` entries written into Hermes's `config.yaml`
  (`hermes_provision.py:2012-2021`) and the sole seed for
  `/etc/hal0/agents/hermes.toml [mcp.servers.*]`
  (`hermes_provision.py:6130-6165`) — the file
  `hal0.agents.mcp_client.AgentMCPClient.classify()` reads
  (`mcp_client.py:149-180`). Installing a server through the dashboard or
  `hal0 mcp install` has never given an agent the ability to call it.
- `InstalledServer` has no `[secrets]`, `[tools]`, or `[exposure]` block,
  so there is nowhere to record per-tool policy or which consumer should
  see a server.
- `POST /api/mcp/{id}/{action}` is a 501 stub (`routes/mcp.py:934-945`)
  that names this ADR by number; `list_servers` hard-codes
  `state="stopped"` for every installed record (`routes/mcp.py:565`)
  regardless of whether an http/sse server is actually reachable.
- There is no `test` verb — installing a server currently gives an
  operator no way to see what tools it advertises before deciding what
  to allow.

This ADR was commissioned by the hal0 × ODS parity field study
(`06-mcp-memory.md`, section E). The study's conclusion, verified against
both trees, is that **ODS contributes no code to this feature** — its
`ape` policy service is unreachable dead infrastructure (see "Rejected"
below) and its MCP story is thinner than hal0's own `ToolPolicy` +
`AgentMCPClient.classify()`. The one thing worth porting is a *shape*:
`ods/scripts/patch-hermes-config.py` patches Hermes's YAML by locating a
named top-level block via regex over `list[str]` lines and rewriting only
that span (`_top_level_block`/`_set_key`,
`patch-hermes-config.py:17-59`), which is how it avoids clobbering an
operator's hand-edited config on migration. Section "Decision 3" below
explains why hal0 achieves the same operator-wins-and-converges property
through a *different, already-precedented* mechanism instead of a literal
port of that text patcher.

## Decision

### 1. Schema: extend `InstalledServer`, five new fields, all defaulted

Added to `src/hal0/mcp/installed.py`'s `InstalledServer`:

- `command: str = ""`, `args: list[str] = []` — stdio launch, unused
  until the supervisor (below) ships.
- `url: str = ""` — the streamable-http/sse connect URL. `transport`
  gains `"sse"` as a third accepted value (`stdio | streamable-http |
  sse`).
- `secrets: dict[str, str] = {}` — maps an env-var name the server
  expects to the **name of a key** in `/etc/hal0/api.env`, never a
  literal value. Resolved at call time via `os.environ` (hal0-api keeps
  `os.environ` in lockstep with `api.env` on every write —
  `routes/secrets.py:1-10`); this is why the resolution never needs its
  own read path. Reuses `/api/secrets`'s existing
  `^[A-Z][A-Z0-9_]{0,63}$` name gate (`routes/secrets.py:63`) rather than
  inventing a second secrets store, per the field study's recommendation
  (§G.5) — hal0 already refuses to hold bearer tokens in TOML
  (`AgentAuthConfig`, `schema.py:2682-2689`); this is the same rule
  applied to third-party servers.
- `tools: ToolPolicy` — **reused verbatim** from
  `src/hal0/config/schema.py:2726` (allow/gated/blocked, disjoint,
  empty-by-default). A freshly installed server therefore has zero
  callable tools until an operator promotes some, matching
  `ToolPolicy`'s own documented intent (`schema.py:2737-2740`). This
  shadows the existing top-level `tools: int` counter field name — kept
  as `tool_policy` on the model (`tools` stays the int count for
  backward-compat with every existing on-disk record and dashboard
  reader) to avoid a breaking rename; `to_toml_dict()` writes it under
  the `[tools]` table.
- `exposure: ExposureConfig` — new small model:
  `hermes: bool = False`, `brain: bool = False`, `openwebui: bool =
  False`, `opencode: bool = False`. Only `hermes` is honoured today
  (Decision 3); `brain` is honoured for http/sse only (Decision 4);
  setting `openwebui` or `opencode` `true` raises `501
  mcp.exposure_unsupported` at the route layer — following the
  `McpNotImplemented` precedent (`routes/mcp.py:57-69`) rather than
  silently ignoring the field.

Every new field defaults such that `InstalledServer.model_validate()` on
an existing pre-#305 on-disk record (no `[secrets]`/`[tools]`/`[exposure]`
table at all) still validates and now carries an empty, zero-tool,
zero-exposure policy — i.e. install-time behaviour for old records is
unchanged (still nothing reaches Hermes) until an operator explicitly
opts in through the new CLI/REST surface.

### 2. Exposure join: recompute, don't hand-patch

On every registry mutation that can change the desired Hermes set
(install, uninstall, `PATCH /tools`, `PATCH /exposure`, `PATCH /config`
enabled toggle), `src/hal0/mcp/hermes_join.py` (new) runs
`sync_hermes_mcp_servers()`:

1. **Desired set** = `_default_mcp_servers()`'s two builtin entries
   (unchanged, still the sole source for those) **plus** every
   `InstalledServer` with `enabled=True`, `transport in
   {"streamable-http", "sse"}`, and `exposure.hermes=True`. `stdio`
   servers are excluded from the desired set — see "Deferred" below —
   and a `hal0 mcp expose <id> --hermes` on a stdio server returns `409
   mcp.exposure_needs_supervisor` rather than silently doing nothing.
2. **Additive apply**: for each desired user-installed entry, call
   `hermes config set` for `mcp_servers.<id>.{type,url,timeout}` +
   `headers.X-hal0-Agent` (+ `Authorization` when a secret named
   `AUTHORIZATION` or a service bearer is available), reusing the exact
   pattern already at `hermes_provision.py:2012-2021` (same subprocess
   invocation via `_hermes_bin`/`_fmt_config_value`, promoted from
   private helpers in `hermes_provision.py` to a small public
   `hermes_provision.apply_mcp_server_entries()` this ADR adds — one
   owner for "how to talk to hermes config set" stays
   `hermes_provision.py`, `hermes_join.py` only decides *what* the
   desired user-server entries are).
3. **Removal — the one thing `hermes config set` cannot express.**
   `config.yaml` is Hermes-owned and machine-migrated
   (`hermes config migrate`); hal0 already accepts losing hand
   formatting/comments on it for exactly this reason —
   `_merge_config_yaml_layers` (`hermes_provision.py:2131-2176`) and
   `_phase_brain_profile_mcp_wire` (`hermes_provision.py:4012-4067`)
   both `yaml.safe_load` → `_deep_merge` → `yaml.safe_dump` the whole
   file today. Given that precedent, `hermes_join.py` uses the **same**
   load/merge/dump primitive rather than ODS's line-based text patcher
   (`patch-hermes-config.py`'s `_top_level_block`/`_set_key` operate on
   a Jinja-rendered file with no independent owner of "what's inside a
   block", and depend on comment-line survival hal0's own tooling
   already doesn't guarantee): it loads `config.yaml`, computes
   `desired_ids = {installed servers with exposure.hermes}`, drops any
   `mcp_servers.<id>` key that (a) is not a builtin name, (b) is not in
   `desired_ids`, and (c) **is present in a small ownership manifest**
   at `/var/lib/hal0/mcp/hermes-managed.json` — the list of ids
   `hermes_join` itself wrote on the previous run. Condition (c) is the
   sentinel-comment idea's functional equivalent for a format that can't
   carry inline markers through a full re-dump: an operator's hand-added
   `mcp_servers` block was never in that manifest, so it is never a
   candidate for removal, matching the field study's "never touch an
   operator hand-added block" requirement (§E.3 step 3) without
   depending on YAML comment preservation.
4. **Mirror `[tools]` into the seed TOML.** `hermes_join.py` merges
   `{"mcp": {"servers": {id: {"builtin": False, "enabled": record.enabled,
   "tools": record.tools.model_dump()}}}}` into
   `/etc/hal0/agents/hermes.toml` using the same deep-merge-preserving
   write `_write_seed_toml` already uses for the two builtin blocks
   (`hermes_provision.py:6178-6210`) — never a wholesale rewrite, and it
   removes a previously-mirrored `[mcp.servers.<id>]` block under the
   same ownership-manifest rule as step 3 when a server is uninstalled.
   This is what makes `AgentMCPClient.classify()` (`mcp_client.py:149`)
   govern user-installed servers on the same two axes (allow-listed at
   all, then per-tool) as the two builtins — no change needed in
   `mcp_client.py` itself.
5. **Targeted re-probe.** After steps 2–4, `hermes_join.py` calls the
   existing `hermes_provision._probe_mcp_server` **only for the servers
   whose desired set changed**, so a bad URL surfaces at
   install/expose-toggle time (`hal0 mcp test` reuses the identical call
   — Decision 5) rather than at the agent's first turn.

Failure mode: any step 2–5 exception is caught, logged, and returned to
the caller as a warning field on the install/patch response (`"hermes_sync":
{"ok": false, "error": ...}`) — never a 500. The registry write (the
operator-visible source of truth) always succeeds or fails on its own;
the Hermes join is a best-effort side effect that self-heals on the next
mutation or the next `hal0 agent reprovision hermes --repair` (which
still runs the full `_build_config_overlay` and will reconcile any drift).

### 3. Why `hermes` only, unconditionally reachable

`hermes` is the only consumer wired today because it is the only one with
an existing, working config-write mechanism (`_build_config_overlay`) to
extend. `brain` reuses `_build_brain_profile_mcp_servers` +
`_phase_brain_profile_mcp_wire` (`hermes_provision.py:3974-4067`) the same
way — `hermes_join.py` calls both syncs when a record's `exposure.brain`
is set, since the brain profile join is a pure deep-merge on its own
`config.yaml` copy and costs no new mechanism (field study §E.3/§G.4
option: "ship hermes+brain in v1"). `openwebui`/`opencode` have no
equivalent write path in this codebase today; wiring either is new
infrastructure, not a join, and is explicitly out of this ADR's scope —
setting either flag `true` returns `501 mcp.exposure_unsupported`.

### 4. CLI verbs

`src/hal0/cli/mcp_commands.py` gains:

- `hal0 mcp test <id>` — calls `POST /api/mcp/{id}/test`: probes the
  server (`_probe_mcp_server` for http/sse; stdio always reports
  `mcp.supervisor_unavailable`) and prints each advertised tool's
  `allow`/`gated`/`blocked`/`unknown_tool` verdict via
  `mcp_client.classify_many` (`mcp_client.py:264-273`) against the
  seed-TOML mirror written by Decision 2 step 4.
- `hal0 mcp allow|gate|block <id> <tool>` — `PATCH /{id}/tools`,
  wrapping `installed.patch_config`'s lock (`installed.py:281-314`,
  extended to accept a `tools` argument).
- `hal0 mcp expose <id> --hermes/--no-hermes [--brain/--no-brain]` —
  `PATCH /{id}/exposure`, triggers Decision 2.
- `hal0 mcp add`/`hal0 mcp remove` ship as `install`/`uninstall`
  aliases (same handler, same route) — existing verb names are kept,
  not renamed.

### 5. REST

`src/hal0/api/routes/mcp.py` gains `POST /{id}/test`, `PATCH
/{id}/tools`, `PATCH /{id}/exposure`. All three classify as `ADMIN` in
`src/hal0/security/exposure.py` (new `_Rule` rows next to the existing
`mcp introspection` rule at `exposure.py:294` — unclassified defaults to
ADMIN already, so this is belt-and-suspenders, verified by
`tests/security/test_exposure.py`'s ratchet). `list_servers` replaces the
hard-coded `state="stopped"` for `transport in {"streamable-http",
"sse"}` records with a live reachability probe (cheap TCP-connect-class
check, not the full tools/list handshake `test` does) — `stdio` records
keep reporting `"stopped"` since there is still no process behind them.

### 6. UI

`ui/src/dash/connections.jsx`'s `McpServerRow` (`:727`) gets a per-tool
three-state control (allow/gated/blocked) writing `PATCH /{id}/tools`,
and an exposure row (Hermes toggle; Brain toggle; OpenWebUI/OpenCode
rendered disabled with the 501 copy) writing `PATCH /{id}/exposure`. A
new `InstallDrawer` (referenced but never built — `manifest.py:1-6`'s
docstring has described it since #224) drives paste → `GET
/api/mcp/resolve` preview → secrets/env rows → exposure toggles →
`POST /api/mcp/install` → `POST /{id}/test` shown inline as the first
tool-verdict list the operator sees.

## Deferred: stdio supervisor

`stdio` servers (the majority of the npm/uvx catalog — `filesystem`,
`playwright`, `sequential-thinking`, …) need a process. The field study
(§E.2) and hal0's own bundled-agent precedent
(`ARCHITECTURE.md:395-397` — sandboxed sibling systemd units) both point
at the same shape: one transient `hal0-mcp@<id>.service` per enabled
stdio server, `User=hal0`, `ProtectSystem=strict`,
`LoadCredential=` per `[secrets]` entry (mirroring how
`AgentAuthConfig`'s `bearer-from-env` tokens already arrive,
`mcp_client.py:199-231`), `ReadWritePaths=` scoped to a per-server
scratch dir. `POST /{id}/{start,stop,restart}` would replace the current
`McpNotImplemented` 501 with `systemctl`-equivalent calls through the
existing `installer/wrappers/hal0-systemctl` allow-list seam.

This is deferred out of this PR rather than designed further here
because it needs the `hal0-systemctl` allow-list widened — an
infrastructure change the field study and `COMMON.md` both flag as
something to coordinate with the lead rather than land unilaterally in a
vertical feature PR. Until it ships: `stdio` transport records install
and configure normally (schema, tools, secrets all work), but `enabled`
has no runtime effect, `exposure.hermes=true` on a stdio record is
rejected with `409 mcp.exposure_needs_supervisor`, and
`POST /{id}/{start,stop,restart}` keeps returning `mcp.supervisor_unavailable`
pointing at this ADR.

## Rejected

- **ODS's `ape` policy service as a deployed component.** It is a
  running, port-bound policy engine nothing calls — `config/ape/policy.yaml`
  was updated for Hermes paths that never route through it (field study
  §F.1). hal0's policy already lives in the call path
  (`AgentMCPClient.classify()`); adding an out-of-path service would be
  strictly worse than what exists.
- **Literal port of `patch-hermes-config.py`'s line-based text patcher.**
  Covered in Decision 2 — hal0 already committed to full-parse-merge-dump
  for `config.yaml` in two existing call sites; a second, different
  patching strategy for the same file would be a second owner of "how
  `config.yaml` gets edited," which is exactly what `CLAUDE.md`'s
  one-owner-per-fact rule forbids.
- **Sentinel HTML/YAML comments as the ownership marker.** Comments
  don't survive `yaml.safe_dump`; the ownership manifest (Decision 2
  step 3) gets the identical "hal0 knows exactly what it can safely
  remove" property without depending on that survival.

## Consequences

- Installing a server and flipping `exposure.hermes` now actually gives
  an agent the ability to call it, closing the gap the field study
  identified as the owner's original ask ("a custom way to add MCP
  servers rather than just be stuck with the originals").
- `classify()` governs user-installed tools identically to the two
  builtins — no new policy code path, no new attack surface on the
  tool-gating axis.
- The Hermes/brain join is eventually-consistent and self-healing (best
  effort now, full reconciliation on the next reprovision) rather than
  transactional with the registry write — an operator who installs a
  server and immediately checks Hermes before the join completes may see
  a one-mutation lag; `hal0 mcp test` and `hal0 mcp status` both report
  the join's last-known state so this is observable, not silent.
- `stdio` servers remain registry-only (install/configure but not run)
  until the supervisor ADR follow-up ships; this PR does not regress
  anything — `stdio` already had no runtime before it.
