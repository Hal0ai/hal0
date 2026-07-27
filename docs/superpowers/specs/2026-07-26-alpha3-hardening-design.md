# hal0 v1.0.0-alpha.3 Hardening — Design

**Date:** 2026-07-26
**Base:** `origin/main` @ `3707cc9c`
**Target release:** `v1.0.0-alpha.3` (prerelease, `preview` channel)
**Supersedes for this drive:** `docs/rework/handoff-r5-phase4.md` §2 scope (written 2026-07-19, stale — see §1)

---

## 1. Why this document exists

The Phase-4 handoff (`docs/rework/handoff-r5-phase4.md`) scoped the live migration windows,
halo143 rehearsal, cutover and a `v1.0.0` GA cut. Seven days of landings have invalidated
its premises:

- `rework/descar` was collapsed into `main` (#1353). `main` is now the integration branch;
  the handoff's "branch off `origin/rework/descar`" gitops no longer applies.
- Version moved to `1.0.0-alpha.2`. An official prerelease/release pipeline landed
  (`scripts/set-version.py`, `scripts/release-check.sh`, `.github/workflows/release.yml`).
- Five of the board's fourteen open rows are **already shipped** and merely unmarked:
  hw-slot-ownership Lanes B, C, D and the MCP route-map autogen (#1323).
- One row is **obsolete**: FLAGS-own §7 slot field-removal. The `hw-slot-ownership` pivot
  reversed that axis — hardware stays on the slot. Building §7 would be actively wrong.

**This drive is scope-2 hardening, not the GA cut.** Its goal: everything currently broken
or half-landed is fixed, a brand-new LXC installs cleanly on both supported substrates, and
`v1.0.0-alpha.3` ships. The deferred live migration windows and the GA tag are a later drive.

### 1.1 Explicitly out of scope

Deferred to the GA drive, deliberately and with reason:

| Deferred | Reason |
|---|---|
| P2-config live window (FLAGS-own migrator run) | Orchestrator-run live step against real boxes; code is complete and deploy-window-gated |
| P2-memory (Honcho→Hindsight live rehearsal) | Same; engine ships, no live workspace migrated |
| P2-updater-b (cosign tarball rehearsal) | Same; pipeline ships, needs a real signed artifact |
| P3-runtime-db (`state.json` → SQLite, migration `006`) | Zero code written; board itself defers post-1.0 |
| SLOT-B live id-flip (`migrate_id_keying`) | Blocked on runtime-version sequencing; board defers post-1.0 |
| Removal of the `cognee` engine alias (`HAL0-SUNSET: v1.0.0`) | `scripts/check_sunset.py` passes today at alpha.2 and only fails at a non-prerelease `1.0.0`. Removing it early is GA work |
| FLAGS-own §7 slot field-removal | Obsolete — axis reversed by the hw-slot-ownership pivot |

---

## 2. The core defect: guard at the wrong layer

### 2.1 Symptom history

The same bug has now surfaced three times:

1. Slot drawer carried model-owned MTP/Reasoning/Vision (fixed in the drawer-ownership merge).
2. Stack-*created* slots carried model-owned `vision`/`mtp` (fixed `d69131a2`, #1356).
3. **Still live:** stack-*reconciled* slots carry model-owned `vision`/`mtp`/`enable_thinking`.

### 2.2 Root cause

`reject_model_owned_slot_keys` is enforced at the HTTP boundary
(`src/hal0/api/routes/slots.py:465-477`), not at the write seam. Every non-HTTP caller
bypasses it:

- `src/hal0/stacks/apply.py:243` writes `updates["vision"]`
- `src/hal0/stacks/apply.py:249` writes `updates["mtp"]` unconditionally, including `None`
- `src/hal0/stacks/apply.py:251` writes `updates["enable_thinking"]`
- all three flow to `reconcile_and_guard_slot_config`
  (`src/hal0/slots/config_write.py:282-300`), which performs merge + NPU-exclusivity +
  default-uniqueness **only** — no model-owned-key rejection
- `SlotManager.create` (`src/hal0/slots/manager.py:1894-1922`) serializes input with no guard
- `SlotManager.update_config` (`src/hal0/slots/manager.py:2113-2157`) passes arbitrary updates
  to the same unguarded pipeline

Patching `apply.py` alone would produce a third symptom fix and a fourth recurrence.

### 2.3 Design: one guard, at the seam

`reject_model_owned_slot_keys` moves **into** `reconcile_and_guard_slot_config` in
`src/hal0/slots/config_write.py` — the single choke point every slot write already traverses.

- The route-level guard **stays** as defense-in-depth: it fails earlier and returns a
  better-shaped HTTP error. Removing it would degrade the API error surface.
- After this change, a new slot construction path cannot reintroduce the bug class without
  deliberately bypassing the seam. That is the deletion-test justification for the change:
  the guard's new home concentrates the invariant instead of scattering it.

### 2.4 Design: stack caps are DROPPED, not routed  *(revised during implementation)*

> **This section was reversed once the code was read.** The original text
> (kept below as §2.4-superseded) proposed routing the stack's caps to the bound
> model. Reading `api/routes/stacks.py:191-193` showed #1356 had already
> established the opposite convention for the *create* path: the fields stay on
> the stack schema for back-compat and simply **do not project** onto the slot.
> Routing in the reconcile path would have made apply diverge from create and
> invented a shared-model write-conflict problem for no user-visible gain.
> Dropping is smaller, consistent with what shipped, and makes #1356's own
> comment true. **Implemented as: drop.**

`_reconciled_stack_slot` stops writing `vision`/`mtp`/`enable_thinking`
entirely. A pre-migration slot whose TOML still carries one keeps it — cleanup
belongs to `hal0 slot migrate-caps` and `updater._strip_ineligible_slot_mtp`,
not to a stack write.

### 2.4-superseded: stack caps route to the model

Under the ownership pivot the model is sole authority for `vision`/`mtp`/`enable_thinking`.
A stack row expressing a capability is therefore a statement about the **bound model**, not
the slot.

`_reconciled_stack_slot` stops projecting those three keys onto slot updates. Instead the
stack apply engine writes them to the bound model's defaults through the model-owned write
path.

**Accepted consequence, stated explicitly:** a model may be bound by more than one stack. Two
stacks declaring conflicting caps for one model now conflict on a *shared* object. This is
resolved the way the FLAGS-own migrator already resolves the analogous case: a divergent
write **refuses** rather than partial-writing, and the plan records the refusal per-slot.
`StackChangePlan` already carries per-slot guard violations, so the refusal path has a home.

**Rejected alternative:** dropping the three fields from the stack schema entirely and letting
the model drawer own them exclusively. Cleaner ownership, but it silently removes working UI
affordances and breaks portable stack exports
(`src/hal0/stacks/portable.py:384-396`). Not worth it in a hardening drive.

### 2.5 Tests encode the wrong behavior

`tests/stacks/test_apply_plan.py:54-125` currently asserts that stack apply *persists* the
model-owned caps onto the slot. These tests are rewritten to assert the new contract, not
patched to keep passing. A test that pins a defect is a defect.

### 2.6 Legacy compatibility path

`src/hal0/updater/updater.py:1380-1450` searches slot TOMLs for `mtp=true` and deletes it only
for ineligible models; comments at `:1383-1392` state forced slot MTP is still honored. This
is a delete-only cleanup path, not a writer, so it does not violate the one-owner rule. It
does keep slot-owned MTP a live compatibility behavior. It gets a `HAL0-SUNSET: v1.0.0` stamp
so the GA drive removes it; behavior is unchanged in alpha.3.

---

## 3. Workstreams

### WS1 — Ownership integrity (Opus)

Correctness-critical; not delegated.

- Move `reject_model_owned_slot_keys` into `src/hal0/slots/config_write.py`'s guarded pipeline.
- Keep the route guard as defense-in-depth.
- Rewrite `src/hal0/stacks/apply.py` `_reconciled_stack_slot` to route caps to the bound model
  with a divergent-write refusal.
- Guard `SlotManager.create` and `SlotManager.update_config`.
- Rewrite `tests/stacks/test_apply_plan.py` to the new contract; add a regression test per
  bypass path (stack apply, manager create, manager update).
- Stamp `src/hal0/updater/updater.py:1380-1450`.

**Acceptance:** every slot write path refuses model-owned keys; a test exists per path; no
production caller regresses.

### WS2 — Fresh-LXC validation (Opus, orchestrator-run)

Live-box steps are never dispatched to build agents — `handoff-r5-phase4.md` §6 rule, retained.

Two new unprivileged CTs on `pve` (`10.0.1.110`, `pve-manager/9.2.3`), cloning CT150's
passthrough shape:

```
features: nesting=1,fuse=1,keyctl=1,mknod=1
dev0: /dev/dri/renderD128,gid=993
dev1: /dev/dri/amdgpu,gid=44
dev2: /dev/kfd
dev3: /dev/accel/accel0,gid=993
```

| CT | Template | Substrate under test |
|---|---|---|
| new id A | `ubuntu-24.04-standard_24.04-2_amd64.tar.zst` | podman 4.9.x, py3.12 — the proven substrate; regression check |
| new id B | `ubuntu-26.04-standard_26.04-1_amd64.tar.zst` | podman 5.7, py3.14 — the substrate that blocked on 2026-07-19 |

Never touch CT105 (live reference), CT150, or CT120 (a PVE template, `template: 1`).

Live-verify the five install-validation defects, all currently fixed in-tree but never proven
on a box:

| id | fix location |
|---|---|
| M2 keyring-EDQUOT diagnosis | `installer/lib/preflight.sh:507-514` |
| M3 render-gid name check + hard stop | `installer/lib/preflight.sh:739-754`, `installer/install.sh:361-364` |
| m1 no `/root/.hermes` on fresh install | `installer/install.sh:2235-2250` |
| m2 `StartLimitIntervalSec` under `[Unit]` | `src/hal0/providers/container.py:621-631` |
| m4 `hal0 agent status hermes --json` | `src/hal0/cli/agent_commands.py:1263-1306` |

M3 is expected to *fire* on at least one CT: the in-container `render` gid must equal 993 for
the passthrough to grant access, and Ubuntu assigns that gid inconsistently across releases —
the exact collision that false-passed on halo143. A firing gate is a pass, not a failure.

**Acceptance:** `INSTALL_EXIT=0` on both CTs, `/api/health` returns 200, services
active+enabled, `hal0 doctor perms` clean, all five defect checks confirmed. Transcripts land
in `docs/rework/deploy-validation/`. Anything that breaks is fixed and re-run.

### WS3 — Test-suite truth (MiniMax swarm, Opus reviews)

Full-suite results pending; lane sized on arrival. Known work regardless:

- **Real bug:** `installer/bench/generate_results_json.py:318` — ruff `F823`, `datetime`
  referenced before assignment; the shadowing import sits at `:342`. This raises
  `UnboundLocalError` at runtime. Outside CI's ruff scope, so CI is green while the script is
  broken.
- `test_open_allowlist_is_exact` (`tests/security/test_exposure.py:154`) flips on `ui/dist`
  presence — green in CI (no dist), red locally (dist). Make it deterministic. No security
  impact; routes are correctly OPEN.
- 7 files fail `ruff format --check` outside CI scope (`installer/bench/`,
  `packaging/toolbox/*/`, `scripts/`).
- Widen the CI lint/format scope so these directories stop drifting.

**Acceptance:** full suite green; CI scope covers every tracked Python file.

### WS4 — Dropped and mismarked surface (Opus + swarm)

- **HP-role-api.** `resolve_role_slots()` ships at `src/hal0/agents/role_slots.py:152` and is
  called by `hermes_provision`, but no `GET /api/agents/{agent_id}/role-slots` route exists in
  `src/hal0/api/routes/agents.py`. The board claims it merged on descar with a CI run. Either
  the collapse dropped it or it never landed. Determine which, then wire the route or close the
  row — do not leave a board row asserting a route that does not exist.
- **Board sync.** Mark hw-slot-ownership Lanes B/C/D and MCP route-map autogen shipped. Mark
  FLAGS-own §7 obsolete with the pivot as the reason.
- **`typed-bodies-rest`.** Open the row at its real size: **51 `request.json()` sites across 15
  files**, not the handoff's 24/12. Do not build it in this drive — the row's absence is the
  defect, not the debt.
- **Lane F sunset stamps.** Add `HAL0-SUNSET: v1.0.0` at the sites recon identified as
  unstamped: `src/hal0/db/migrations/001_registry.sql:18,33` (`preferred_runner`,
  `n_gpu_layers`), the profile-image drop at `src/hal0/config/loader.py:529-540`, the profile
  rationale at `src/hal0/config/schema.py:1083-1089`, and the slot legacy-image compatibility
  at `src/hal0/config/schema.py:444-447`.

**Acceptance:** board asserts nothing false; every deferred item has a row; sunset stamps
cover the compatibility surface the GA drive must remove.

### WS5 — Hygiene, security follow-ups, alpha.3 cut (swarm, Opus reviews)

- **Security:** `src/hal0/install/perms.py:175` — `api.env` is `0644` world-readable and may
  carry tokens (`HF_TOKEN`). Tighten to `0640` root:hal0 and verify the API service still
  reads it. This is the one finding with real exposure.
- Quote `rm -rf $TMP` at `scripts/release-test.sh:239,264`; quote the unit paths in
  `installer/wrappers/hal0-systemctl:105,107`.
- Make `scripts/check_sunset.py` executable or confirm every caller invokes it via `python`.
- **Branch/worktree reaping:** delete the two provably-merged branches
  (`fix/reinstall-cleanup`, `prerelease-channel`); unlock the two locked agent worktrees
  (`agent-a7a4a66f7656c7113`, `agent-ab58edb05eb5593c3`); resolve the
  `chore/release-v1.0.0` / `fix/installer-hermes-heredoc` duplicate; delete
  `fix/stacks-model-owned-bypass` (landed as `d69131a2`). Every other branch needs human
  review before deletion — the audit found unmerged commits on 47 of 49.
- **Release cut:** `python scripts/set-version.py 1.0.0-alpha.3`, CHANGELOG entry covering
  `v1.0.0-alpha.2..HEAD`, then `scripts/release-check.sh`, then tag.

**Acceptance:** `scripts/release-check.sh` green; `manifest.json` says `1.0.0-alpha.3` /
`preview`; CHANGELOG covers every commit since alpha.2.

---

## 3.6 Defects found by actually running the installer (added 2026-07-26)

None of these were predictable from the tree; all four came from building fresh
CTs and watching real output. Recorded here because the spec above predates them.

| id | defect | status |
|---|---|---|
| **F0** | **MCP admin surface silently does not mount.** A fresh install resolves fastapi 0.140.0 / starlette 1.3.1 (`pyproject.toml` pins only `fastapi>=0.115`, no upper bound). Starlette 1.x hides `include_router`'d routes behind a wrapper, so `build_admin_route_map`'s flat walk returned an empty map, `_apply_route_map` raised, and `mount_mcp_servers` swallowed it as `hal0.mcp.mount_failed`. All ~82 admin tools gone. **Release blocker.** | fixed `66a78cb7`, verified live on CT160 |
| **F1** | Stock Ubuntu 24.04/26.04 LXC templates have no `curl`, so `preflight_bootstrap_prereqs` killed the documented direct-`install.sh` path at step 1/13. Unreachable via the `curl \| bash` one-liner, hit by every fresh container. | fixed `93c410f9` |
| **F2** | The container-runtime gate swallowed podman's own error and guessed. On a CT that already had `nesting=1,keyctl=1,fuse=1,mknod=1`, podman failed with `socket: permission denied` (LXC AppArmor confining its network setup) and the gate told the operator to set the flags that were already set. | fixed `93c410f9` |
| **F3** | Every fresh install ended with `hal0 doctor perms` reporting STATE.md drift. `_atomic_write`'s `os.replace` swaps in the tmp inode, so the file inherited the umask's 0644 and discarded the 0664 the installer's `doctor perms --fix` backstop had just set — Hermes provisioning re-renders STATE.md *after* that backstop. A box that always reports drift teaches operators to ignore the check. | fixed, this drive |

**Fresh-LXC prerequisites confirmed empirically** (beyond what the docs stated):
`features: nesting=1,fuse=1,keyctl=1,mknod=1` **plus** `lxc.apparmor.profile: unconfined`
**plus** a `/dev/net/tun` bind mount. The in-container `render` gid differs by
release — 993 on 24.04, **991 on 26.04** — so the `dev0` gid must match the
container's, not the host's. The M3 gate catches exactly this and prints the
correct remedy.

## 4. Execution model

**Branching.** One lane = one worktree = one branch off freshly-fetched `origin/main`.
Small typed commits (`feat|fix|docs|refactor(scope): …`). Tear down the worktree on merge.

**Gating.** Every lane gets an independently re-run capped gate before merge — `ruff check` +
`ruff format --check` + import smoke + `scripts/check_sunset.py` + targeted pytest for Python;
`tsc` + `eslint` + `build` for UI. A worker's self-report is never the gate. Trial-merge
against current `origin/main` before merging for real. CI red on `main` is stop-the-line: fix
forward, never rewrite history.

**Delegation.** MiniMax workers own WS3 and WS5 mechanical work; Opus owns WS1 and WS2 and all
merges, and reviews every worker diff before it lands. Nothing security-, migration-, or
ownership-critical is delegated.

**File ownership.** No two concurrent workers touch the same file. WS1 owns
`src/hal0/slots/`, `src/hal0/stacks/`, `tests/stacks/`. WS3 owns `tests/security/`,
`installer/bench/`, `packaging/toolbox/`. WS5 owns `scripts/`, `src/hal0/install/perms.py`.
WS4's board edits are docs-only and serialized behind everything else so the board reflects
final state.

**Live-box rule.** WS2 is orchestrator-run. No build agent gets credentials to `pve`, CT105,
CT150, or the new CTs.

---

## 5. Acceptance bar for the drive

1. Every slot write path refuses model-owned keys, with a regression test per path.
2. Full pytest suite green; CI lint/format scope covers every tracked Python file.
3. A brand-new Ubuntu 24.04 LXC and a brand-new Ubuntu 26.04 LXC each reach
   `INSTALL_EXIT=0`, `/api/health` 200, and clean `hal0 doctor perms`.
4. All five install-validation defects confirmed fixed on live boxes.
5. The board asserts nothing false; every deferred item has a row.
6. `api.env` no longer world-readable.
7. `scripts/release-check.sh` green and `v1.0.0-alpha.3` tagged.

Item 3 is the bar the drive is named for. Tests passing is not the acceptance criterion for a
product that installs itself onto other people's machines.

---

## 6. Open questions

None blocking. Two decisions recorded above rather than asked, both reversible:

- Stack caps route to the bound model with divergent-write refusal (§2.4), rather than being
  dropped from the stack schema.
- The `cognee` sunset alias stays for alpha.3 (§1.1) because the gate is green at a
  prerelease version and its removal is GA work.
