# Handoff — hal0 R5 endgame (paste-in brief for a new session)

> **SUPERSEDED (2026-07-19, after Phase 0 landed @ `278b32a8`):** the active paste-in
> prompt is now **`docs/rework/handoff-r5-drive2.md`** — Phase 0 below is done; Phases 1–4
> are refined there with the drive-2 orchestration model. This file remains as the
> phase-rationale record; the assessment stays the evidence base.

> **You are picking up the final phase of the hal0 rework (R5 — surface + launch).**
> R1–R4 are merged. This brief turns the sync assessment into an executable, phased plan
> with verify gates, board deltas, and the decisions still owed to the user. Read in this
> order: (1) this handoff, (2) `docs/rework/r5-sync-assessment-2026-07-19.md` (the evidence —
> every claim is `path:line`-cited and adversarially verified), (3)
> `docs/rework/REWORK_BOARD_PROTOCOL.md` (single-writer rule, lane lifecycle, verify/merge
> discipline), (4) `docs/rework/REWORK_BOARD.md` (live status), (5) `docs/rework/REWORK.md`
> (finish line + per-lane DoD). Specs per lane are under `docs/rework/hal0-specs/`.

---

## 1. Where things stand (verify before editing — tips move)

- **Merged:** R1 (`rework-R1`), R2/R2.1, R3 (`rework-R3`), R4 (`c91d0cf5`, `rework-R4-stage`).
  The finish line's hard parts (auth, slot-ids/ports, SQLite substrate, convergent installer,
  Hermes integration) are landed. R5 is **surface + launch**: wire the shipped surfaces back
  into sync, finish the mega-router/flags decomposition, run the migration windows, validate
  on a real box.
- **`main` advanced past descar's base:** `462b28f` + #1315 (HF update-check) + **#1316
  (UI-API-1 managed-arg gap closed — launch + create + `POST /api/models/{id}/validate`)**.
  Any descar→main landing rebases onto **current** `origin/main`.
- **Already ✔ on `rework/descar`** (the assessment/digest may still call these open — they are
  NOT): P4-docs (`ecbdc6f6`, ARCHITECTURE.md is now the single authoritative internal doc,
  ADR-inline), P4-rules (`fa9085d0`, CONTRIBUTING anti-scar rules 1–11), §21.11 golden-paths
  CI subset + map (`fa9085d0`), §21.4 doctor/system-info, P3-routers inc 1+2, UI-D1–D6,
  HP-realtime inc-1, OBS metrics substrate (migration 002).
- **The assessment + graph live on PR #1317** (branch `claude/rework-sync-assessment-bdrwn9`,
  draft, docs-only, CI-green). **First action:** land #1317's docs onto `rework/descar` (or
  cherry-pick `docs/rework/r5-sync-assessment-2026-07-19.md` + this handoff) so you work from
  one tree. The graph artifacts (`graphify-out/GRAPH_REPORT.md`, `wiki/`, `manifest.json`) are
  committed; raw `graph.json` is local-only — run `graphify update .` to regenerate, and use
  `graphify query "..."` before grepping (but read §7 methodology caveats).
- **Board is single-writer** (the orchestrator session). If you don't hold the token, deliver
  board changes as the **row deltas in §5**, don't edit the board directly.

## 2. The finish line (quoted, `REWORK.md`)

Complete when hal0 has: one authoritative model/config path · one Hindsight memory path · one
tool-calling loop · one slot-config apply engine · one settings apply engine · one model-store
resolver · one runner-image registry · SQLite-backed machine state + model metadata · stable
slot IDs + centrally managed ports · deny-by-default auth + exposure classification · a
convergent installer with a clear privilege seam · a small optional Hermes integration ·
**deployment validated on the new `halo` LXC.** Operating rule: *finish the simplification,
validate it on `halo`, merge it — don't add adjacent features.*

## 3. Do these first — three launch-blocking correctness items (all S/M, no design)

1. **`hal0.target` is missing.** Every rendered slot quadlet declares
   `WantedBy=hal0.target` (`src/hal0/providers/container.py:763`), but no `.target` unit
   exists in either repo and the installer never writes one → **slots do not autostart after
   reboot.** Ship + enable the target (or flip the renderer to `multi-user.target` + rerender);
   add a doctor check + uninstall entry. (Assessment §6.1.)
2. **MCP stamps the raw admin key into journald.** `mcp_mount.bearer_resolver` returns the
   bearer as `client_id` (`api/mcp_mount.py:147`); post key-rotation that's the live admin key,
   replayable from `/api/logs`. Derive `client_id` from the principal or hash it; fix the test
   that pins the leak. (§4.2.)
3. **Prod mock fallback fakes data + non-GET success.** `mockFetch` substitutes fixtures on
   404/network-error in prod, ignoring HTTP method (`ui/src/api/mock.ts:1204`) → a
   network-erroring POST returns a 200 fixture. Gate behind DEV/FORCED + `method==='GET'` only.
   (§1.1.)

## 4. Phased execution plan

Each phase is independently shippable; later phases assume earlier ones. Per lane: **§-ref**
into the assessment is the spec unless a `hal0-specs/` file is named. **Verify** = the capped
gate (`ruff check` + `format --check` + import smoke + sunset + named pytest targets; UI:
`tsc` + eslint + build + targeted γ) on every code touch; docs-only = the dangling-link grep.

### Phase 0 — Correctness & security (do now; unblocks trust in the surface)
Goal: no shipped surface lies or leaks. All S/M, no design work.
- The three launch-blockers (§3).
- **Uninstaller sync** (one lane, §6.3): remove `/etc/containers/systemd/hal0-slot@*` quadlet
  sources, the `hal0-podman-ro` sudoers grant, `--purge` bench store, honcho units, dangling
  hermes shims/backup, PPA + containers.conf apparmor edit.
- **Model-store PermRow** (§6.2, O13 class): default `${VAR_DIR}/models` born `root:root` →
  `User=hal0` daemon can't pull; add the PermRow + chown; verify a fresh default-store pull.
- **MCP `upstream_update` hard-broken** (§4.1): PATCH unsupported in `_call_rest` → the gated
  tool always crashes; add PATCH + regression + supported-method guard.
- **Hermes/brain MCP wiring sends no credential** (§4.2): thread service-identity bearer into
  provisioned MCP headers or every call 401s with auth on; golden-path test.
- **CLI auth bypass** (§5.1): 4 sites (`slot logs -f`, `doctor logs -f`, `hal0 chat`, `setup`
  apply) skip `_auth_headers` → 401/traceback with auth on; add an authed stream helper + an
  auth-on smoke tier. Also fix the two help texts that point AT deprecated verbs.
- Verify each with the capped gate; deploy-affecting items (target, perms, uninstaller) are
  **both-boxes** (150 privileged / 143 unprivileged), recorded per box.

### Phase 1 — Sync wiring (mostly S/M; lights up already-shipped surfaces)
Goal: close the backend↔frontend↔CLI↔MCP contract gaps.
- **MCP admin catalog buildout** (§4.3): interim route-sync test (~30 lines, immediate); then
  hand-add the R3/R4 tools agents need (rename gated, by-id reads, model default/duplicate,
  system-info, bench queue-item delete); add `memory_recall` + fix read/write classification;
  record an EXCLUDED set. (Its own board row `MCP-sync`.)
- **`hal0 auth` sub-app + missing CLI verbs** (§5.2): `auth status|rotate|require`, then
  `slot rename`, `model default`, `model update [--check]`, `model pull --cancel`,
  `hal0 ports`, `hal0 board …`, `hal0 chat --brain`; fix `model import-backup` (chains into
  SQLite). Board row `CLI-auth+verbs`.
- **UI wiring** (§1.2/§2): switch DuplicateModelDialog to the real duplicate route; mock-layer
  realignment (capabilities envelope, status/update shapes, dead auth rows); one contract
  doc-sweep (endpoints.ts self-contradictions, CONTRACTS.md WS params); `UI-API-1` re-scope to
  item 4 only; new `UI-API-2` (auth affordances the SecurityPage already stubs). ui-sweep-b
  ENDPOINTS consolidation (8 TODOs). `mock-prod-gate` overlaps Phase 0 #3.
- **Four declared-but-missing routes**: `GET /api/stats/requests` (OBS lane), `/api/doctor`,
  `/api/auth/exposure`; **`/api/migrations/flag-report` → fold into the FLAGS-own DoD** or the
  shipped MigrationBanner stays dead.

### Phase 2 — Structural decomposition (existing board rows; corrected scope)
Goal: finish "routers only parse→call→render" and "one authoritative model/config path".
- **P3-routers inc 3** (`spec-p3-routers.final.md` §5): pull/update-pull orchestration out of
  `models.py` (highest-risk, monkeypatch-heavy) → typed request bodies (dashboard-key
  status-code audit first; **new routes born Pydantic**) → **MCP route-map autogen** (step 20,
  its own lane; settle the 3-gap addendum in §4.4 first: deny-by-default for unclassified,
  stream/WS exclusion, route-keyed redaction). Open a `typed-bodies-rest` row for the 24
  request.json() sites no lane owns.
- **Importer flips + sunset stamps** (§3): flip 5 external importers off route-module
  internals, extract `_probe_power`, shrink shims to monkeypatch minimum; stamp `HAL0-SUNSET`
  markers on the 4 one-release surfaces (the sunset guard checks nothing today) and retire
  `GET /api/slots/{name}`, lowering the scar baseline.
- **`api/__init__.py` god-file** (§3 + §11): target **`lifespan()`** (`:862`, ~540 lines, 44
  `app.state.` touches) — phase-split + type `app.state` + BootReport. **Do NOT** refactor
  `create_app()` (`:1400`) — its centrality is test-fixture noise. Give `v1.py` an
  accepted-as-is-or-split row.
- **FLAGS-own** (`spec-flags-ownership.md`): flags/device/chat_template → models; slots reduce
  to id/name/model/port/state; profiles become copy-on-stamp; add the flag-report endpoint to
  the DoD; CLI tranche (§5.3 deprecate `--provider/--hardware/--backend` + delete the
  client-side hardware probe); model-modals shrink follows. Rides the P2-config window.
- **Settings data-seam completion** (`spec-settings.md`): migrate the remaining settings pages
  onto the typed `settingsClient`/`useSettingsForm` seam; wire or demote the two placeholder
  pages. UI continuation: ESM conversion (47 window-global modules), CSS-era consolidation
  (4 eras / 6,247 lines), god-module round 2 (slot-modals/chrome).

### Phase 3 — Memory & Hermes finish
Goal: "one Hindsight memory path" + a done, small Hermes integration.
- **Memory MCP hindsight rename** (§4.5, plan §7.4/§18.1/§23.1): rename `hal0_memory_*` →
  `hindsight_recall/retain`, **implement `reflect`** (route exists), move config to
  `~/.hermes/hindsight/config.json` `local_external`, fix the two stale docstrings, update the
  provisioner system-prompt — **both parity-locked copies** + parity test. Board `MCP-mem-hindsight`.
- **Brain-lane relocation** (§7, 5 `RELOCATE(brain-lane)` markers): move the 5 steps into the
  hal0-api lifespan, drop the `_BRAIN_LANE_STEPS` convergence exemption; the marker-count test
  is the deletion tripwire. (Correction: these already don't pollute the install-convergence
  surface — the gap is *where they run*.)
- **Drift-watch blind spot** (§7): add `hermes_cli/kanban_db.py` + the kanban runs API + the
  token-injection seam to `pyproject` `tracked_files`; vendor a kanban_runs fixture after the
  Phase-6 live pass.
- **`hermes-bump` runbook** (§7): write the full procedure (bump → requirements.txt +
  HERMES_COMMIT → re-vendor fixtures → contract+parity → both-boxes Phases 3/5/6 →
  `hal0 agent upgrade hermes`); optionally teach `--bump` the other two rewrites.

### Phase 4 — Migration windows + launch (orchestrator-run LIVE steps, not agents)
Goal: cut over on the new `halo` LXC; lxc105 stays as rollback.
- **P2-memory** (`spec-honcho-memory.final.md`, critical for upgrade boxes): rehearse with
  deterministic Honcho fixtures on fresh halo143 (**never mutate lxc105**), per-workspace
  `hal0 memory migrate`, verify `[honcho]` config tolerance, then the ordered deletion
  (UI→CLI→API→provision→registry→modules→schema→units) — includes §5.4 CLI tranche + §6.3 units.
- **P2-config** (`spec-p2-config.final.md`): capabilities.toml → derived view over slots/*.toml;
  3-release window with create-on-select; delete `capabilities migrate` CLI. Sequence FLAGS-own
  migration in this same window.
- **P2-updater-b** (§9 — **re-scope, don't build**): the cosign+swap+rollback pipeline is
  already implemented (`src/hal0/updater/updater.py`, 1,918 lines). Work = verify install.sh
  lays down `/usr/lib/hal0/current`, confirm the release pipeline publishes the tarball+bundle,
  then **delete** the extra mechanisms (nightly channel, detached-sig fallback, `_update_via_git`
  prod path, prepare/commit two-phase); resolve the CLI `--channel nightly`/API mismatch; fix
  the stale `PLAN.md §9` docstring.
- **P3-runtime-db** (plan §8.4): state.json → slot_state (migration 006 pre-allocated),
  pull-jobs, events, one table at a time. **Coordinate with SLOT-B's M5** (it renames
  state.json this lane later moves — decide scope at dispatch).
- **SLOT-B live flip** (`spec-p3-slot-identity-ports.md`): live unit `@name→@id` + podman
  rename + M5 on real state + runtime path/unit flip — must land atomically; quadlet `@`-name
  verify on 143.
- **Cross-repo**: ComfyUI repin `docker.io/kyuz0` → `ghcr.io/hal0ai/hal0-comfyui@fd8c8930…`
  (§6.2); resolve cpu-runner lineage disagreement.
- **R5 cutover program**: redeploy halo143 from descar (clears hot-patches), `hal0 doctor all`,
  podman-5 quadlet template refresh, side-by-side validation, cutover plan. Re-run the two
  deferred Hermes validations here (Phase 5 plugin liveness, Phase 6 HP-executor first contact —
  both unblocked now).

## 5. Board-row deltas (hand to the single writer)

- `UI-API-1`: re-scope to **item 4 only**; item 1 done → #1316; items 2–3 done in code.
- `P4-docs`: sub-note — **root `AGENTS.md` resurrected** (present on descar though the row
  claims it deleted; `CONTEXT.md` correctly gone). Decision {re-delete | pointer stub to
  `ARCHITECTURE.md#bundled-agents-v03` — never a content copy}. The "deleted AGENTS.md" claim
  is currently falsified by the tree.
- Clear stale cells: `R1` "what remains" (its tails are closed), `R4` open-tails "host-net
  renderer" (merged `bf05310d`), `UI-D1-D3` "D4-D6 follow-up" (D4-D6 shipped).
- `P2-updater-b`: re-scope "build" → **verify + scope-trim + delete** (pipeline implemented).
- **New rows** (this handoff + assessment §-refs are their spec): `INSTALL-target` (critical),
  `UNINSTALL-sync`, `MCP-sync`, `MCP-mem-hindsight`, `CLI-auth+verbs`, `UI-API-2`,
  `typed-bodies-rest`, `mock-prod-gate`, `hermes-bump-runbook`, `comfyui-repin`.

## 6. Decisions still owed to the user (surface these; don't guess)

- **Root `AGENTS.md`**: re-delete to finish the P4-docs collapse, or keep as a thin pointer stub?
- **ComfyUI under host-net**: the hostnet-render lane made the ComfyUI web UI loopback-only
  (was LAN :8188) — the user's veto window is still open (§7 tails).
- **Updater channel**: drop `nightly` from the API (CLI is stable-only) or add it to the CLI —
  they disagree today (§5.5, resolve in P2-updater-b).
- **cpu-runner lineage**: wire `hal0-toolbox-cpu:v1` + manifest_key, or ratify the vulkan-reuse
  with a note (§6.2).
- **HP-voice / HP-automation / HP-context**: stay ⏸ post-core, or promote any into R5?
  (Contracts are pre-frozen; not launch-blockers.)
- **god-module LOC burn-down tracking** per checkpoint (proposed; needs a yes/no).

## 7. Conventions — do not violate (from `REWORK_BOARD_PROTOCOL.md` + the merge handoff)

1. **Board single-writer** — deltas, not direct edits, unless you hold the token.
2. **One owner per fact** (rule 1) — grep for an existing test/row/section before adding one.
3. **No ghost-doc citations** (rule 9) — every path/PR/file must exist in the tree or on the
   remote; the docs-reference ratchet fails on a dangling link. **No ADR tree** — decisions
   live inline in `ARCHITECTURE.md` next to the code.
4. **Status legend** — a row is ✔ only with a merge SHA + verify evidence.
5. **Deploy-affecting = both boxes** — 150 (4.9.3/privileged) + 143 (5.7/unprivileged),
   recorded per box.
6. **Capped gate on every code/test touch**; docs-only = dangling-link grep. Ride board updates
   on merge pushes.
7. **Land on `rework/descar`** (one integration branch); it merges to main at phase boundaries.

## 8. Ways of working

- **Agent dispatch: use Sonnet** for lane build/research subagents (per the user's standing
  instruction), Opus/Fable reserved for the hardest verify/judge steps. Follow the board's
  Opus-built → Fable-reviewed → independently-re-run discipline for anything non-trivial.
- **Use the graph** (`graphify query "<question>"`) before grepping, but **filter its metrics**
  (§11): raw degree/betweenness are inflated by test-edge / cross-language / same-file noise —
  don't treat "high degree = refactor". SlotManager is the real coupling waist; `create_app` is
  not. To keep doc→code edges live for the graph MCP, re-run the semantic pass (not just
  `update`) after doc changes.
- **Verify claims against source** — this handoff's parent assessment was adversarially
  re-verified, but tips move; re-check `path:line` before acting on a specific finding.

---

_Source of record: `docs/rework/r5-sync-assessment-2026-07-19.md` (§1–§11, all cited +
verified). This handoff organizes it into phases; where they differ, the assessment's
`path:line` evidence wins. Prepared 2026-07-19._
