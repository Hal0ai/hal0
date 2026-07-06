# Handoff: model-pipeline plan — finish-out pass

**Status:** RESOLVED (2026-07-06). All four items closed — see the RESOLUTION
block below. Supersedes the "implement Tier 1 (WS-1…WS-6)" prompt that
accompanied `handoffs/model-pipeline-fix-plan-2026-07-04.md`. That prompt is
**stale** — do not run it as written.

## RESOLUTION (2026-07-06)

- **FO-1 — DONE.** `cli/slot_commands.py` now imports `SLOT_PORT_RANGE_START/END`
  from `hardware.stats` (port scan + help string). Ruff clean, slot tests pass.
- **FO-2 — ALREADY DONE + doc fix.** The `render_systemd_override` default was
  already deleted in WS-15 (`base.py:249` is a NOTE). Only a stale docstring in
  `container.py:33-36` still claimed the inherited default existed — corrected.
- **FO-3 — KILLED (per plan).** Removed the `POST /api/slots/{name}/backend`
  endpoint + its two endpoint-only helpers (`slots.py`), the `useSlotBackend`
  hook + `SlotBackendSwitchResponse` type + `slotBackend` endpoint const (UI),
  and the switch UX in `slots.jsx` / `inference-pane.jsx`. The amber
  `backend_mismatch` chip is KEPT (detection stays, sourced from the slot_view
  aggregator) but now opens the slot editor's profile picker (`onEdit`) instead
  of firing a switch. Deleted `tests/api/test_slot_backend_control.py`, removed
  `test_backend_flip_reconciles_profile_via_route` + the two typed-error params.
  Backend suites + UI typecheck/build/.mjs green; 1527 tests collect clean.
- **FO-4 — KEEP INERT (no code change).** `rope_freq_base` is already marked
  DEPRECATED in the schema (`model.py:43`), the Recipe editor already says it
  was removed / use extra_args (`slot-modals.jsx:1291`), and the extra_args
  placeholder demonstrates `--rope-freq-base 10000`. The honesty hint the
  handoff asked for already exists; wiring it would resurrect a deprecated knob.

---

## Why the original prompt is wrong

The plan was researched against `4f1d41b` (v0.8.4b1). Between then and now
(main at v0.9.1) the bulk of **all three tiers** was implemented and merged
via three PRs off `claude/new-session-09hvwe`:

- **#1033** — single argv assembler, taxonomy cleanup, backend completeness
  (WS-2, WS-3, WS-7)
- **#1039** — GPU generalization, dead-path retirement groundwork, catalog
  UX, multi-file pulls (WS-11, WS-13, WS-17, WS-18)
- **#1042** — catalog sort/tag/quant + chat-template pick at pull (WS-6, WS-13)

Also: **PR #1035 is MERGED, not a draft.** The original prompt's central
"rebase over draft #1035 before touching models.jsx / slot-modals.jsx /
inference-pane.jsx / normalizeApiModel.ts" instruction is moot — that work is
in main. Verified-done workstreams (against live `src/hal0/…`): WS-1, WS-2,
WS-3, WS-6, WS-7, WS-8, WS-9, WS-10, WS-11, WS-12, WS-13, WS-14, WS-16, WS-17,
WS-18, WS-19. Do **not** re-open any of these.

## Scope for this pass — the actual remaining backlog

Two concrete code remnants and two divergence decisions. Small; likely one PR
plus one gated PR.

### FO-1 · WS-4 straggler: last hardcoded slot-port range (code)
- **State:** the shared constant landed — `SLOT_PORT_RANGE_START` /
  `SLOT_PORT_RANGE_END` in `src/hal0/hardware/stats.py:37-38`, and
  `_next_free_slot_port(start, end)` (`src/hal0/api/routes/slots.py:252`)
  takes them. But `src/hal0/cli/slot_commands.py:470` **still hardcodes
  `range(8081, 8100)`**, so the CLI's free-port scan diverges from the API's.
- **Fix:** import `SLOT_PORT_RANGE_START/END` from `hardware.stats` and scan
  `range(SLOT_PORT_RANGE_START, SLOT_PORT_RANGE_END + 1)` in
  `slot_commands.py`. Grep for any other bare `808x`/`range(8081` literals
  while in there and fold them too.
- **Test:** none strictly needed; if adding one, assert the CLI port scan and
  `_next_free_slot_port` agree on the pool bounds.

### FO-2 · WS-15: delete the dead LlamaServerProvider launch path (code, GATED)
- **State:** the plan's Tier-3 "design sign-off first" item. Groundwork is in
  place — WS-8's model-defaults merge lives in the live path, WS-18's device
  filter was ported into `container.py`, and `base.py:249` carries a WS-15
  deprecation NOTE. But `src/hal0/providers/llama_server.py` **still exists**.
- **Precondition:** get the sign-off the plan asked for (this is the only
  behavior-bearing deletion in the backlog). Confirm nothing outside tests
  imports `LlamaServerProvider`, `merge_flags`, `_HAL0_TOOLBOX_IMAGES`, or the
  legacy `render_systemd_override` before deleting.
- **Fix:** delete `llama_server.py`'s launch machinery + the legacy
  `render_systemd_override` default in `base.py`. Repoint any surviving
  argv-shape test fixtures at the WS-2 segment assembler
  (`_llama_argv_segments`, `container.py:475`).
- **Grep-before-delete:** list every remaining consumer in the PR body (plan
  house rule for "delete" workstreams).

### FO-3 · Decision: WS-5 backend-switch surface diverged from the plan
- **What happened:** the plan **recommended killing** the legacy
  backend-switch endpoint (backend identity now lives in profiles). The code
  went the **other way — it finished it**: `useSlotBackend` now has live
  callers (`ui/src/dash/slots.jsx:157`, `ui/src/dash/inference-pane.jsx:195`)
  and `actual_backend` is populated (`src/hal0/api/routes/slots.py:1223,1263`).
- **Why it matters:** this re-introduces exactly the "second way to express
  what profiles already own" the plan warned against.
- **Action:** confirm this is intentional. If yes → no code change; just note
  it so a future reader doesn't "fix" it back toward the plan. If no → kill
  path per original WS-5 (remove endpoint response fields + the hook + the two
  UI callers, make the `backend_mismatch` chip open the profile picker).

### FO-4 · Decision: WS-8 `rope_freq_base` is intentionally inert
- **What happened:** the model-defaults launch segment emits `extra_args` and
  `-ngl`, but **`rope_freq_base` is deliberately NOT emitted**
  (`src/hal0/providers/container.py:522`, explicit comment). The plan's WS-8
  said emit all three.
- **Why it matters:** a model default that sets `rope_freq_base` is silently
  ignored at launch — surprising to anyone who sets it in the Recipe editor.
- **Action:** confirm intentional. If keeping it inert → add a UI hint /
  tooltip in the Recipe editor (and/or hide the field) so it doesn't read as
  wired. If it should be honored → add the `--rope-freq-base` emission to the
  `model_defaults` segment with a golden-argv test.

## Constraints / environment

- Backend argv changes get a golden-argv test against `_llama_argv_segments`
  (`container.py:475`); run existing suites for anything you touch
  (`tests/api/test_models_routes.py` patterns; `tests/slots/` argv goldens).
- UI changes: `ui npm run typecheck && build`; dependency-free `.mjs` tests
  under `ui/src/dash/__tests__/`; Playwright specs use the forced-mock
  `HAL0_DATA` init-script injection pattern
  (`ui/tests/e2e/specs/models-upstream-v3.spec.ts`). If pinned Playwright
  wants a missing browser revision, symlink the installed
  `/opt/pw-browsers/chromium_headless_shell-*` dir to the expected revision
  name and bridge the inner `chrome-headless-shell-linux64` path.
- No rebase-over-#1035 dance — it's merged into main.

## Report back with

- PRs opened (expect: one for FO-1, one gated for FO-2, plus whatever FO-3/FO-4
  resolve to).
- The FO-3 and FO-4 decisions and their rationale.
- Anything else in the plan doc that reads as open but is actually landed, so
  the plan doc can be marked DONE.
